from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config
from . import __version__


@dataclass(frozen=True)
class Reading:
    temperature: float | None
    timestamp: float
    error: str = ""
    humidity: float | None = None
    resistance: float | None = None

    def valid(self, now, max_age):
        return (not self.error and self.temperature is not None
                and math.isfinite(self.temperature) and -50 <= self.temperature <= 250
                and 0 <= now - self.timestamp <= max_age)


@dataclass(frozen=True)
class Inputs:
    pt: Reading
    sht: Reading
    feedback1: bool | None = True  # True = NC mirror contact closed (released)
    feedback2: bool | None = True
    safety: bool | None = True   # Independent STB/STOP loop healthy diagnostic
    door: bool | None = True


class Controller:
    """All mutable control state belongs to the main loop. No network/GPIO here."""

    def __init__(self, cfg: Config, now: float, saved_fault="", save_fault=lambda _: None):
        self.cfg = cfg
        self.save_fault = save_fault
        self.fault = saved_fault
        self.phase = "FAULT" if saved_fault else "OFF"
        self.target = cfg.target
        self.heating = False
        self.light = False
        self.output_changed = now
        self.last_off = now
        self.arm_until = 0.0
        self.started = None
        self.hold_started = None
        self.band_since = None
        self.profile = "manual"
        self.last_rejection = ""
        self.maintenance = False
        self.update_pending = False

    def stop(self, now):
        self.set_heating(False, now)
        self.started = self.hold_started = self.band_since = None
        self.arm_until = 0.0
        self.phase = "FAULT" if self.fault else "MAINTENANCE" if self.maintenance else "OFF"

    def enter_maintenance(self, inputs, now):
        # This decision runs atomically in the same loop that owns every heating command.
        if self.fault or self.started is not None or self.phase not in {"OFF", "MAINTENANCE"} or self.heating:
            return self.reject("update_deferred_controller_busy")
        if self.cfg.mode == "hardware" and (
                inputs.feedback1 is not True or inputs.feedback2 is not True
                or now - self.output_changed < self.cfg.feedback_timeout):
            return self.reject("update_deferred_contactors_not_released")
        self.maintenance = True
        self.stop(now)
        self.light = False
        return True

    def leave_maintenance(self, now):
        self.maintenance = False
        self.update_pending = False
        self.stop(now)  # Never re-arm or resume a session after an update.

    def trip(self, reason, now):
        if not self.fault:
            self.fault = reason
            # Remove the demand even if persistence fails. Caller then exits fail-off.
            self.stop(now)
            self.save_fault(reason)
        self.stop(now)

    def set_heating(self, value, now):
        if value != self.heating:
            self.heating = value
            self.output_changed = now
            if not value:
                self.last_off = now

    def healthy(self, inputs, now):
        return (inputs.pt.valid(now, self.cfg.sensor_max_age)
                and inputs.pt.temperature < self.cfg.high_limit
                and inputs.safety and inputs.door)

    def ready(self, inputs, now):
        return (self.healthy(inputs, now) and inputs.feedback1 and inputs.feedback2
                and now - self.output_changed >= self.cfg.feedback_timeout)

    def arm(self, inputs, now):
        if self.maintenance or self.update_pending:
            return self.reject("update_maintenance")
        if self.cfg.mode == "monitor":
            return self.reject("heating_disabled_monitor_mode")
        if self.phase != "OFF" or self.fault or not self.ready(inputs, now):
            return self.reject("not_ready_for_local_arm")
        self.arm_until = now + self.cfg.arm_seconds
        self.phase = "ARMED"
        return True

    def reset(self, inputs, now):
        if not self.fault or not self.ready(inputs, now):
            return self.reject("fault_reset_not_ready")
        self.save_fault("")
        self.fault = ""
        self.stop(now)
        return True

    def reject(self, why):
        self.last_rejection = why
        return False

    def command(self, action, value, inputs, now):
        if action == "stop" or (action == "mode" and value == "off"):
            self.stop(now)
            return True
        if action == "target":
            try:
                if isinstance(value, bool):
                    raise ValueError()
                target = float(value)
            except (TypeError, ValueError):
                return self.reject("invalid_target")
            if not math.isfinite(target) or not self.cfg.min_target <= target <= self.cfg.max_target:
                return self.reject("target_out_of_range")
            if self.started is not None and self.profile != "manual":
                return self.reject("profile_running_stop_before_edit")
            self.target = target
            # No minimum-on time may delay a requested temperature reduction.
            if inputs.pt.valid(now, self.cfg.sensor_max_age) and inputs.pt.temperature >= target:
                self.set_heating(False, now)
            return True
        if action == "profile":
            if self.started is not None:
                return self.reject("profile_running_stop_before_edit")
            if not isinstance(value, str) or (value != "manual" and value not in self.cfg.profiles):
                return self.reject("unknown_profile")
            self.profile = value
            if value != "manual":
                self.target = self.cfg.profiles[value]["target"]
            return True
        if action == "light":
            if self.maintenance and value != "OFF":
                return self.reject("update_maintenance")
            if value not in ("ON", "OFF") or self.cfg.mode == "monitor":
                return self.reject("light_unavailable_or_invalid")
            self.light = value == "ON"
            return True
        if action == "mode" and value == "heat":
            if self.maintenance or (self.update_pending and self.started is None):
                return self.reject("update_maintenance")
            if self.cfg.mode == "monitor":
                return self.reject("heating_disabled_monitor_mode")
            if self.started is not None:
                return True  # QoS duplicates must not renew the session deadline.
            if (self.phase != "ARMED" or now >= self.arm_until or self.fault
                    or not self.ready(inputs, now)):
                return self.reject("local_arm_required_or_not_ready")
            self.started = now
            self.arm_until = 0.0
            self.phase = "PREHEAT"
            return True
        return self.reject("unknown_command")

    def tick(self, inputs, now):
        if self.cfg.mode == "monitor":
            self.stop(now)
            return
        if self.fault:
            self.stop(now)
            return
        if not inputs.pt.valid(now, self.cfg.sensor_max_age):
            self.trip("pt100_invalid_or_stale", now)
            return
        if inputs.pt.temperature >= self.cfg.high_limit:
            self.trip("pt100_overtemperature", now)
            return
        if not inputs.safety:
            self.trip("hardware_safety_open", now)
            return
        if now - self.output_changed >= self.cfg.feedback_timeout:
            expected = not self.heating
            if inputs.feedback1 != expected or inputs.feedback2 != expected:
                self.trip("contactor_feedback_mismatch", now)
                return
        if not inputs.door and self.phase == "ARMED":
            self.stop(now)  # Door opening invalidates a pre-start inspection.
        if self.phase == "ARMED" and now >= self.arm_until:
            self.stop(now)
        if self.started is None:
            return
        # V1 also stops an active session on door opening; no automatic restart.
        if not inputs.door:
            self.stop(now)
            return
        if now - self.started >= self.cfg.max_session_seconds:
            self.stop(now)
            return
        if self.hold_started is None and now - self.started >= self.cfg.max_preheat_seconds:
            self.trip("preheat_timeout", now)
            return
        if abs(inputs.pt.temperature - self.target) <= self.cfg.hysteresis / 2:
            if self.band_since is None:
                self.band_since = now
            if self.hold_started is None and now - self.band_since >= 10:
                self.hold_started = now
                self.phase = "HOLD"
        else:
            self.band_since = None
        if self.profile != "manual" and self.hold_started is not None:
            if now - self.hold_started >= self.cfg.profiles[self.profile]["hold_seconds"]:
                self.stop(now)
                return
        if inputs.pt.temperature >= self.target + self.cfg.hysteresis / 2:
            self.set_heating(False, now)
        elif (inputs.pt.temperature <= self.target - self.cfg.hysteresis / 2
              and now - self.last_off >= self.cfg.min_off_seconds):
            self.set_heating(True, now)

    def state(self, inputs, now):
        pt_ok = inputs.pt.valid(now, self.cfg.sensor_max_age)
        sht_ok = inputs.sht.valid(now, self.cfg.sensor_max_age + 2)
        active = self.started is not None
        confirmed = self.heating and not inputs.feedback1 and not inputs.feedback2
        return {
            "version": __version__, "maintenance": self.maintenance,
            "update_pending": self.update_pending,
            "mode": "heat" if active else "off",
            "action": "heating" if confirmed else "idle" if active else "off",
            "phase": self.phase, "target": self.target,
            "pt100": round(inputs.pt.temperature, 2) if pt_ok else None,
            "bank_temperature": round(inputs.sht.temperature, 2) if sht_ok else None,
            "humidity": round(inputs.sht.humidity, 2) if sht_ok and inputs.sht.humidity is not None else None,
            "pt100_valid": pt_ok, "sht41_valid": sht_ok,
            "pt100_error": inputs.pt.error, "sht41_error": inputs.sht.error,
            "pt100_age": round(max(0, now - inputs.pt.timestamp), 2),
            "remaining_seconds": max(0, round(self.cfg.max_session_seconds - (now - self.started))) if active else 0,
            "armed": self.phase == "ARMED", "fault": bool(self.fault), "fault_code": self.fault,
            "heating_demand": self.heating, "k1_released": inputs.feedback1, "k2_released": inputs.feedback2,
            "safety_ok": inputs.safety, "door_closed": inputs.door,
            "light": "ON" if self.light else "OFF", "profile": self.profile,
            "operating_mode": self.cfg.mode, "heating_enabled": self.cfg.mode != "monitor",
            "last_rejection": self.last_rejection,
            "warning": "SIMULATION" if self.cfg.mode == "simulation" else
                       "SHT41_UNAVAILABLE" if not sht_ok else "",
        }
