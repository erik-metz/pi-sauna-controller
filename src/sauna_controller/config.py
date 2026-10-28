from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    device_id: str = "saunapi"
    mode: str = "monitor"
    target: float = 70.0
    min_target: float = 30.0
    max_target: float = 100.0
    high_limit: float = 110.0
    hysteresis: float = 4.0
    min_off_seconds: float = 30.0
    max_session_seconds: float = 7200.0
    max_preheat_seconds: float = 3600.0
    sensor_max_age: float = 3.0
    feedback_timeout: float = 1.0
    arm_seconds: float = 60.0
    heating_enabled: bool = False
    safety_verified: bool = False
    sensors_enabled: bool = True
    rtd_wires: int = 4
    rtd_nominal: float = 100.0
    rtd_reference: float = 430.0
    i2c_address: int = 0x44
    spi_bus: int = 0
    spi_device: int = 0
    i2c_bus: int = 1
    aht21_channels: list = field(default_factory=list)
    aht21_mux_address: int = 0x70
    relay_active_high: bool = True
    host: str = "homeassistant.local"
    port: int = 1883
    tls: bool = False
    ca_file: str = ""
    mqtt_enabled: bool = True
    pins: dict = field(default_factory=lambda: {
        "k1": 17, "k2": 27, "feedback1": 22, "feedback2": 23,
        "light": 18, "aufguss_valve": 16, "evaporator_fill_valve": 20,
        "heartbeat": 24, "safety": 25, "arm": 5, "door": 6,
    })
    profiles: dict = field(default_factory=lambda: {
        "sanft": {"target": 60.0, "hold_seconds": 1800.0},
        "standard": {"target": 70.0, "hold_seconds": 1800.0},
        "heiss": {"target": 85.0, "hold_seconds": 1800.0},
    })

    @property
    def base(self):
        return "sauna/" + self.device_id

    def validate(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", self.device_id):
            raise ValueError("Invalid device_id")
        if self.mode not in {"monitor", "simulation", "hardware"}:
            raise ValueError("mode must be monitor, simulation or hardware")
        if self.mode == "simulation" and not self.device_id.endswith("_sim"):
            raise ValueError("Simulation device_id must end in _sim")
        if self.mode != "simulation" and self.device_id.endswith("_sim"):
            raise ValueError("Real devices must not use simulation IDs")
        for key in ("target", "min_target", "max_target", "high_limit", "hysteresis",
                    "min_off_seconds", "max_session_seconds", "max_preheat_seconds",
                    "sensor_max_age", "feedback_timeout", "arm_seconds", "rtd_nominal", "rtd_reference"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Invalid number: " + key)
        if not (0 <= self.min_target <= self.target <= self.max_target < self.high_limit <= 200):
            raise ValueError("Invalid temperature limits")
        if not (0.5 <= self.hysteresis <= 20 and self.min_off_seconds >= 1):
            raise ValueError("Invalid thermostat timing/band")
        if not (10 <= self.max_preheat_seconds <= self.max_session_seconds <= 14400):
            raise ValueError("Invalid session limits")
        if not (0.2 <= self.feedback_timeout <= 5 and 1 <= self.sensor_max_age <= 10
                and 1 <= self.arm_seconds <= 300):
            raise ValueError("Invalid watchdog/arming timing")
        if self.rtd_wires not in (2, 3, 4) or not (self.rtd_reference > self.rtd_nominal > 0):
            raise ValueError("Invalid RTD configuration")
        if self.rtd_nominal != 100:
            raise ValueError("This configuration supports PT100 only")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Invalid MQTT port")
        for key in ("heating_enabled", "safety_verified", "sensors_enabled", "relay_active_high",
                    "tls", "mqtt_enabled"):
            if type(getattr(self, key)) is not bool:
                raise ValueError("Expected boolean: " + key)
        if self.mode == "hardware" and not (self.heating_enabled and self.safety_verified):
            raise ValueError("Hardware mode requires heating_enabled AND safety_verified")
        if self.mode == "hardware" and not self.sensors_enabled:
            raise ValueError("Hardware mode requires physical sensors")
        if not isinstance(self.host, str) or not self.host or not isinstance(self.ca_file, str):
            raise ValueError("Invalid MQTT hostname/CA path")
        for key in ("spi_bus", "spi_device", "i2c_bus", "i2c_address", "rtd_wires"):
            if type(getattr(self, key)) is not int:
                raise ValueError("Expected integer: " + key)
        if self.spi_bus != 0 or self.spi_device != 0 or self.i2c_bus != 1 or self.i2c_address not in (0x44, 0x45, 0x46):
            raise ValueError("Bus configuration differs from the documented pin assignment")
        if (not isinstance(self.aht21_channels, list) or len(self.aht21_channels) > 8
                or any(type(ch) is not int or not 0 <= ch <= 7 for ch in self.aht21_channels)
                or len(set(self.aht21_channels)) != len(self.aht21_channels)):
            raise ValueError("AHT21 channels must be unique TCA9548A channels from 0 to 7")
        if type(self.aht21_mux_address) is not int or not 0x70 <= self.aht21_mux_address <= 0x77:
            raise ValueError("TCA9548A address must be from 0x70 to 0x77")
        expected = {"k1", "k2", "feedback1", "feedback2", "light", "aufguss_valve",
                    "evaporator_fill_valve", "heartbeat", "safety", "arm", "door"}
        if set(self.pins) != expected or len(set(self.pins.values())) != len(expected):
            raise ValueError("GPIO assignments must be complete and unique")
        if any(type(p) is not int or not 4 <= p <= 27 or p in {7, 8, 9, 10, 11}
               for p in self.pins.values()):
            raise ValueError("GPIO assignment conflicts with SPI/reserved pins")
        if not self.profiles:
            raise ValueError("At least one profile required")
        for name, profile in self.profiles.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", name):
                raise ValueError("Invalid profile name")
            if set(profile) != {"target", "hold_seconds"}:
                raise ValueError("Profile needs target and hold_seconds")
            t, seconds = profile["target"], profile["hold_seconds"]
            if (type(t) not in (int, float) or type(seconds) not in (int, float)
                    or not math.isfinite(t) or not math.isfinite(seconds)
                    or not self.min_target <= t <= self.max_target
                    or not 1 <= seconds <= self.max_session_seconds):
                raise ValueError("Invalid profile parameters")
        return self


def load(path):
    values = tomllib.loads(Path(path).read_text())
    allowed = set(Config.__dataclass_fields__)
    if values.keys() - allowed:
        raise ValueError("Unknown config keys: " + ", ".join(values.keys() - allowed))
    for env, key, convert in (("MQTT_HOST", "host", str), ("MQTT_PORT", "port", int),
                              ("MQTT_CA_FILE", "ca_file", str)):
        if env in os.environ:
            values[key] = convert(os.environ[env])
    if "MQTT_TLS" in os.environ:
        tls = os.environ["MQTT_TLS"].lower()
        if tls not in {"true", "false"}:
            raise ValueError("MQTT_TLS must be true or false")
        values["tls"] = tls == "true"
    return Config(**values).validate()
