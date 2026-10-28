from __future__ import annotations

import math
import time
from queue import Empty, Full

from .core import Inputs, Reading


def crc8(data):
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def pt100_temperature(resistance, nominal=100):
    # IEC 60751 Callendar-Van Dusen, positive quadratic / negative Newton solution.
    a, b, c = 3.9083e-3, -5.775e-7, -4.183e-12
    ratio = resistance / nominal
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("Invalid PT100 resistance")
    discriminant = a*a - 4*b*(1-ratio)
    if discriminant < 0:
        raise ValueError("PT100 outside conversion range")
    t = (-a + math.sqrt(discriminant)) / (2*b)
    if resistance < nominal:
        for _ in range(12):
            f = 1 + a*t + b*t*t + c*(t-100)*t**3 - ratio
            slope = a + 2*b*t + c*(4*t**3 - 300*t*t)
            t -= f/slope
    if not -200 <= t <= 250:
        raise ValueError("PT100 outside application range")
    return t


class MAX31865:
    def __init__(self, cfg):
        import spidev
        self.cfg = cfg
        self.spi = spidev.SpiDev()
        self.spi.open(cfg.spi_bus, cfg.spi_device)
        self.spi.max_speed_hz = 500_000
        self.spi.mode = 1
        # VBIAS, optional three-wire compensation, 50 Hz filter.
        self.control = 0x80 | (0x10 if cfg.rtd_wires == 3 else 0) | 0x01
        self.write(0, self.control | 0x02)  # Clear power-on fault only.

    def write(self, register, value):
        self.spi.xfer2([register | 0x80, value])

    def read(self, register, count=1):
        return self.spi.xfer2([register & 0x7F] + [0]*count)[1:]

    def measure(self):
        self.write(0, self.control | 0x04)  # Automatic fault detection cycle.
        time.sleep(0.003)
        self.write(0, self.control | 0x20)  # One-shot conversion, 50 Hz.
        time.sleep(0.075)
        data = self.read(1, 2)
        fault = self.read(7)[0]
        if fault or data[1] & 1:
            raise OSError(f"MAX31865 fault 0x{fault:02x}")
        resistance = ((data[0] << 8 | data[1]) >> 1) * self.cfg.rtd_reference / 32768
        value = pt100_temperature(resistance, self.cfg.rtd_nominal)
        return Reading(value, time.monotonic(), resistance=resistance)

    def close(self):
        self.spi.close()


class SHT41:
    def __init__(self, cfg):
        from smbus2 import SMBus, i2c_msg
        self.bus = SMBus(cfg.i2c_bus)
        self.address = cfg.i2c_address
        self.message = i2c_msg

    def measure(self):
        self.bus.i2c_rdwr(self.message.write(self.address, [0xFD]))
        time.sleep(0.010)
        msg = self.message.read(self.address, 6)
        self.bus.i2c_rdwr(msg)
        data = list(msg)
        if crc8(data[:2]) != data[2] or crc8(data[3:5]) != data[5]:
            raise OSError("SHT41 CRC mismatch")
        t = -45 + 175 * (data[0]*256 + data[1]) / 65535
        rh = max(0, min(100, -6 + 125 * (data[3]*256 + data[4]) / 65535))
        return Reading(t, time.monotonic(), humidity=rh)

    def close(self):
        self.bus.close()


def dew_point_c(temperature, humidity):
    """Magnus approximation for ordinary indoor air temperatures."""
    if not math.isfinite(temperature) or not math.isfinite(humidity) or not 0 < humidity <= 100:
        raise ValueError("Invalid temperature/humidity for dew point")
    a, b = 17.62, 243.12
    gamma = math.log(humidity / 100) + a * temperature / (b + temperature)
    return b * gamma / (a - gamma)


class AHT21Array:
    """Read fixed-address AHT21 sensors one at a time through TCA9548A channels."""
    ADDRESS = 0x38

    def __init__(self, cfg):
        from smbus2 import SMBus, i2c_msg
        self.bus = SMBus(cfg.i2c_bus)
        self.message = i2c_msg
        self.mux_address = cfg.aht21_mux_address

    def measure(self, channel):
        self.bus.i2c_rdwr(self.message.write(self.mux_address, [1 << channel]))
        time.sleep(0.002)
        status = self.message.read(self.ADDRESS, 1)
        self.bus.i2c_rdwr(status)
        if not (list(status)[0] & 0x08):
            self.bus.i2c_rdwr(self.message.write(self.ADDRESS, [0xBE, 0x08, 0x00]))
            time.sleep(0.010)
        self.bus.i2c_rdwr(self.message.write(self.ADDRESS, [0xAC, 0x33, 0x00]))
        time.sleep(0.085)
        msg = self.message.read(self.ADDRESS, 6)
        self.bus.i2c_rdwr(msg)
        data = list(msg)
        if len(data) != 6 or data[0] & 0x80:
            raise OSError("AHT21 measurement busy or invalid")
        raw_h = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
        raw_t = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]
        humidity = max(0.0, min(100.0, raw_h * 100.0 / (1 << 20)))
        temperature = raw_t * 200.0 / (1 << 20) - 50.0
        if not -40 <= temperature <= 120:
            raise OSError("AHT21 temperature outside sensor limits")
        return Reading(temperature, time.monotonic(), humidity=humidity)

    def close(self):
        self.bus.close()


def sensor_worker(cfg, queue):
    """Separate process: a stuck kernel bus operation cannot freeze the controller."""
    drivers = {"pt": None, "sht": None, "aht": None}
    types = {"pt": MAX31865, "sht": SHT41}
    while True:
        readings = {}
        for key, driver_type in types.items():
            try:
                if drivers[key] is None:
                    drivers[key] = driver_type(cfg)
                readings[key] = drivers[key].measure()
            except Exception as exc:
                readings[key] = Reading(None, time.monotonic(), error=f"{type(exc).__name__}: {exc}")
                if drivers[key] is not None:
                    try:
                        drivers[key].close()
                    except Exception:
                        pass
                    drivers[key] = None
        if cfg.aht21_channels:
            try:
                if drivers["aht"] is None:
                    drivers["aht"] = AHT21Array(cfg)
                readings["aht"] = {}
                for index, channel in enumerate(cfg.aht21_channels, start=1):
                    try:
                        readings["aht"][f"aht21_{index}"] = drivers["aht"].measure(channel)
                    except Exception as exc:
                        readings["aht"][f"aht21_{index}"] = Reading(
                            None, time.monotonic(), error=f"{type(exc).__name__}: {exc}")
            except Exception as exc:
                readings["aht"] = {f"aht21_{index}": Reading(
                    None, time.monotonic(), error=f"{type(exc).__name__}: {exc}")
                    for index, _ in enumerate(cfg.aht21_channels, start=1)}
                if drivers["aht"] is not None:
                    try:
                        drivers["aht"].close()
                    except Exception:
                        pass
                    drivers["aht"] = None
        try:
            queue.put_nowait(readings)
        except Full:
            # Keep latest sample, never build an unbounded backlog.
            try:
                queue.get_nowait()
            except Empty:
                pass
            try:
                queue.put_nowait(readings)
            except Full:
                pass
        time.sleep(0.8)


class GPIO:
    def __init__(self, cfg):
        from gpiozero import DigitalInputDevice, OutputDevice
        from gpiozero.pins.lgpio import LGPIOFactory
        self.factory = LGPIOFactory(chip=0)
        self.devices = []
        self.outputs = {}
        self.inputs = {}
        try:
            for name in ("k1", "k2", "light", "aufguss_valve", "evaporator_fill_valve",
                         "reserve_relay", "heartbeat"):
                active_high = cfg.relay_active_high if name in {
                    "k1", "k2", "aufguss_valve", "evaporator_fill_valve", "reserve_relay"
                } else True
                device = OutputDevice(cfg.pins[name], active_high=active_high,
                                      initial_value=False, pin_factory=self.factory)
                self.devices.append(device)
                self.outputs[name] = device
            for name in ("feedback1", "feedback2", "safety", "door", "arm"):
                device = DigitalInputDevice(cfg.pins[name], pull_up=False, pin_factory=self.factory)
                self.devices.append(device)
                self.inputs[name] = device
        except Exception:
            self.close()
            raise

    def read(self, pt, sht):
        return Inputs(pt, sht, **{key: bool(self.inputs[key].value) for key in
                                 ("feedback1", "feedback2", "safety", "door")})

    def apply(self, heating, light, heartbeat):
        # Both commands always receive the same demand; transitions are diagnosed separately.
        self.outputs["k1"].value = heating
        self.outputs["k2"].value = heating
        self.outputs["light"].value = light
        # Unconfigured water and reserve actuators stay physically OFF.
        self.outputs["aufguss_valve"].off()
        self.outputs["evaporator_fill_valve"].off()
        self.outputs["reserve_relay"].off()
        self.outputs["heartbeat"].value = heartbeat

    def close(self):
        for device in self.outputs.values():
            try:
                device.off()
            except Exception:
                pass
        for device in self.devices:
            try:
                device.close()
            except Exception:
                pass
        self.factory.close()


class Simulation:
    def __init__(self, now):
        self.temperature = 20.0
        self.last = now
        self.feedback_heating = False

    def read(self, now, heating):
        dt = min(1, now - self.last)
        self.last = now
        self.temperature += dt * (0.35 if heating else -(self.temperature - 20)/600)
        # Report the last demand: instantaneous ideal contactor response.
        return Inputs(Reading(self.temperature, now),
                      Reading(20 + (self.temperature - 20)*0.75, now, humidity=25),
                      not heating, not heating)
