import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from sauna_controller.config import Config, load
from sauna_controller.core import Controller, Inputs, Reading
from sauna_controller.hardware import crc8, pt100_temperature
from sauna_controller.mqtt import discoveries, MQTT
from sauna_controller.storage import FaultStore


def inputs(now, temperature=20, **changes):
    base = Inputs(Reading(temperature, now), Reading(25, now, humidity=30))
    return dataclasses.replace(base, **changes)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(mode="simulation", device_id="test_sim", min_off_seconds=1).validate()
        self.saved = []
        self.core = Controller(self.cfg, 0, save_fault=self.saved.append)

    def start(self, now=2):
        self.assertTrue(self.core.arm(inputs(now), now))
        self.assertTrue(self.core.command("mode", "heat", inputs(now), now))
        self.core.tick(inputs(now), now)
        self.assertTrue(self.core.heating)

    def test_no_start_without_local_arm(self):
        self.assertFalse(self.core.command("mode", "heat", inputs(2), 2))
        self.assertFalse(self.core.heating)

    def test_start_and_hysteresis(self):
        self.start()
        self.core.tick(inputs(4, 69, feedback1=False, feedback2=False), 4)
        self.assertTrue(self.core.heating)
        self.core.tick(inputs(5, 73, feedback1=False, feedback2=False), 5)
        self.assertFalse(self.core.heating)
        self.core.tick(inputs(7, 70), 7)
        self.assertFalse(self.core.heating)
        self.core.tick(inputs(8, 67), 8)
        self.assertTrue(self.core.heating)

    def test_stop_is_immediate(self):
        self.start()
        self.core.command("stop", "STOP", inputs(2.1), 2.1)
        self.assertFalse(self.core.heating)
        self.assertEqual(self.core.phase, "OFF")

    def test_lower_target_stops_immediately(self):
        self.start()
        self.assertTrue(self.core.command("target", "50", inputs(2.1, 55), 2.1))
        self.assertFalse(self.core.heating)

    def test_stale_sensor_shutdown(self):
        self.start()
        self.core.tick(inputs(2, feedback1=False, feedback2=False), 6)
        self.assertEqual(self.core.fault, "pt100_invalid_or_stale")
        self.assertFalse(self.core.heating)
        self.assertEqual(self.saved, ["pt100_invalid_or_stale"])

    def test_invalid_sensor_shutdown(self):
        self.start()
        self.core.tick(inputs(3, pt=Reading(None, 3, "open_wire")), 3)
        self.assertEqual(self.core.phase, "FAULT")

    def test_overtemperature_stops_before_minimum_time(self):
        self.start()
        self.core.tick(inputs(2.1, 110), 2.1)
        self.assertEqual(self.core.fault, "pt100_overtemperature")
        self.assertFalse(self.core.heating)

    def test_failure_to_pick_up(self):
        self.start()
        self.core.tick(inputs(3.1), 3.1)
        self.assertEqual(self.core.fault, "contactor_feedback_mismatch")

    def test_welded_contactor_after_stop(self):
        self.start()
        self.core.stop(3)
        self.core.tick(inputs(4.1, feedback1=False), 4.1)
        self.assertEqual(self.core.fault, "contactor_feedback_mismatch")
        self.assertFalse(self.core.heating)

    def test_hardware_stop(self):
        self.start()
        self.core.tick(inputs(2.1, safety=False), 2.1)
        self.assertEqual(self.core.fault, "hardware_safety_open")

    def test_door_invalidates_arm(self):
        self.core.arm(inputs(2), 2)
        self.core.tick(inputs(2.1, door=False), 2.1)
        self.assertEqual(self.core.phase, "OFF")
        self.assertFalse(self.core.command("mode", "heat", inputs(3), 3))

    def test_door_stops_active_session(self):
        self.start()
        self.core.tick(inputs(2.1, door=False), 2.1)
        self.assertEqual(self.core.phase, "OFF")
        self.assertFalse(self.core.heating)

    def test_arm_expiry(self):
        self.core.arm(inputs(2), 2)
        self.assertFalse(self.core.command("mode", "heat", inputs(63), 63))

    def test_arm_requires_released_contactors(self):
        self.assertFalse(self.core.arm(inputs(2, feedback2=False), 2))

    def test_repeated_heat_does_not_extend_session(self):
        self.start()
        self.core.command("mode", "heat", inputs(10), 10)
        self.assertEqual(self.core.started, 2)

    def test_session_timeout(self):
        self.cfg.max_session_seconds = 20
        self.cfg.max_preheat_seconds = 20
        self.start()
        self.core.tick(inputs(22, feedback1=False, feedback2=False), 22)
        self.assertEqual(self.core.phase, "OFF")
        self.assertFalse(self.core.heating)

    def test_preheat_timeout(self):
        self.cfg.max_preheat_seconds = 10
        self.start()
        self.core.tick(inputs(12, feedback1=False, feedback2=False), 12)
        self.assertEqual(self.core.fault, "preheat_timeout")

    def test_sht_failure_does_not_stop_pt100_control(self):
        self.start()
        self.core.tick(inputs(3, feedback1=False, feedback2=False, sht=Reading(None, 3, "CRC")), 3)
        self.assertFalse(self.core.fault)
        self.assertTrue(self.core.heating)

    def test_nan_and_bad_setpoints_rejected(self):
        for value in ("NaN", "inf", "-inf", True, None, "hello", "500", "-1"):
            with self.subTest(value=value):
                self.assertFalse(self.core.command("target", value, inputs(2), 2))
                self.assertEqual(self.core.target, 70)

    def test_fault_does_not_auto_reset(self):
        self.core.trip("test_fault", 2)
        self.core.tick(inputs(4), 4)
        self.assertEqual(self.core.phase, "FAULT")
        self.assertFalse(self.core.arm(inputs(4), 4))

    def test_reset_does_not_restart_or_arm(self):
        self.core.trip("test_fault", 2)
        self.assertTrue(self.core.reset(inputs(4), 4))
        self.assertEqual(self.core.phase, "OFF")
        self.assertFalse(self.core.command("mode", "heat", inputs(4), 4))

    def test_reset_requires_good_inputs(self):
        self.core.trip("test_fault", 2)
        self.assertFalse(self.core.reset(inputs(4, safety=False), 4))

    def test_restart_remembers_fault_but_not_session(self):
        self.core = Controller(self.cfg, 0, saved_fault="weld")
        self.assertEqual(self.core.phase, "FAULT")
        self.assertIsNone(self.core.started)
        self.assertFalse(self.core.heating)

    def test_monitor_cannot_arm_or_heat(self):
        self.core = Controller(Config(), 0)
        self.assertFalse(self.core.arm(inputs(2), 2))
        self.assertFalse(self.core.command("mode", "heat", inputs(2), 2))
        self.assertFalse(self.core.command("light", "ON", inputs(2), 2))
        self.core.tick(inputs(4, temperature=None), 4)
        self.assertFalse(self.core.heating)
        self.assertFalse(self.core.fault)

    def test_profile_has_local_hold_timer(self):
        self.cfg.profiles["standard"]["hold_seconds"] = 5
        self.core.command("profile", "standard", inputs(2), 2)
        self.start()
        self.core.tick(inputs(3, 70, feedback1=False, feedback2=False), 3)
        self.core.tick(inputs(13, 70, feedback1=False, feedback2=False), 13)
        self.assertEqual(self.core.phase, "HOLD")
        self.core.tick(inputs(18, 70, feedback1=False, feedback2=False), 18)
        self.assertEqual(self.core.phase, "OFF")

    def test_profile_edits_rejected_during_session(self):
        self.core.command("profile", "standard", inputs(2), 2)
        self.start()
        self.assertFalse(self.core.command("profile", "sanft", inputs(3), 3))
        self.assertFalse(self.core.command("target", "60", inputs(3), 3))

    def test_fault_persistence_failure_still_removes_heat_demand(self):
        self.start()
        self.core.save_fault = Mock(side_effect=OSError("disk_full"))
        with self.assertRaises(OSError):
            self.core.trip("fault", 3)
        self.assertFalse(self.core.heating)


class ConfigAndHardwareTests(unittest.TestCase):
    def test_example_configs(self):
        for path in Path("config").glob("*.toml"):
            load(path)

    def test_hardware_requires_explicit_commissioning(self):
        with self.assertRaises(ValueError):
            Config(mode="hardware").validate()

    def test_duplicate_pins_rejected(self):
        cfg = Config()
        cfg.pins["k2"] = cfg.pins["k1"]
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_bad_config_numbers_and_types(self):
        for key, value in (("target", float("nan")), ("heating_enabled", "false"),
                           ("max_session_seconds", 0), ("high_limit", 60),
                           ("feedback_timeout", 100), ("device_id", "bad/topic")):
            cfg = Config()
            setattr(cfg, key, value)
            with self.assertRaises(ValueError, msg=key):
                cfg.validate()

    def test_simulation_ids_separate(self):
        with self.assertRaises(ValueError):
            Config(mode="simulation").validate()

    def test_crc_known_vector(self):
        self.assertEqual(crc8([0xBE, 0xEF]), 0x92)

    def test_pt100_reference_points(self):
        for resistance, temp in ((100, 0), (138.5055, 100), (80.3063, -50)):
            self.assertAlmostEqual(pt100_temperature(resistance), temp, places=2)
        with self.assertRaises(ValueError):
            pt100_temperature(0)

    def test_corrupt_fault_store_locks_out(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FaultStore(folder)
            self.assertEqual(store.read(), "")
            store.write("weld")
            self.assertEqual(store.read(), "weld")
            store.path.write_text("bad json")
            self.assertEqual(store.read(), "fault_store_unreadable")
            store.write("")
            self.assertEqual(store.read(), "")


class MQTTTests(unittest.TestCase):
    def test_discovery_consistency(self):
        cfg = Config()
        found = discoveries(cfg)
        self.assertGreater(len(found), 20)
        ids = [payload["unique_id"] for _, payload in found]
        self.assertEqual(len(ids), len(set(ids)))
        climate = next(payload for topic, payload in found if "/climate/" in topic)
        self.assertEqual(climate["min_temp"], 30)
        self.assertEqual(climate["max_temp"], 100)
        self.assertFalse(climate["retain"])
        self.assertIn("pt100", climate["current_temperature_template"])

    def make_client(self):
        # Exercise callback logic without importing or connecting the optional paho library.
        from queue import Queue
        from threading import Event
        mqtt = MQTT.__new__(MQTT)
        mqtt.cfg = Config()
        mqtt.commands = Queue(maxsize=1)
        mqtt.stop_event = Event()
        mqtt.rediscover = Event()
        mqtt.epoch = 1
        return mqtt

    def message(self, action, value, retain=False):
        return Mock(topic="sauna/saunapi/command/" + action, payload=value, retain=retain)

    def test_retained_heat_is_rejected(self):
        mqtt = self.make_client()
        mqtt._message(None, None, self.message("mode", b"heat", retain=True))
        self.assertTrue(mqtt.commands.empty())

    def test_stop_bypasses_full_queue(self):
        mqtt = self.make_client()
        mqtt.commands.put("filled")
        mqtt._message(None, None, self.message("stop", b"STOP"))
        self.assertTrue(mqtt.stop_event.is_set())

    def test_bad_utf8_and_oversized_rejected(self):
        mqtt = self.make_client()
        for value in (b"\xff", b"a"*129):
            mqtt._message(None, None, self.message("mode", value))
        self.assertTrue(mqtt.commands.empty())

    def test_valid_command_enqueued(self):
        mqtt = self.make_client()
        mqtt._message(None, None, self.message("target", b"75"))
        _, epoch, action, value = mqtt.commands.get_nowait()
        self.assertEqual((epoch, action, value), (1, "target", "75"))


if __name__ == "__main__":
    unittest.main()
