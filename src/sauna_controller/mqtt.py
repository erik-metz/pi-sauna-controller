from __future__ import annotations

import json
import logging
import os
import threading
import time
from queue import Full

from . import __version__

log = logging.getLogger(__name__)


def discoveries(cfg):
    """Single-entity discovery with one shared HA device and distinct simulation IDs."""
    base = cfg.base
    device = {"identifiers": [cfg.device_id],
              "name": "Sauna Simulation" if cfg.mode == "simulation" else "Sauna Controller",
              "manufacturer": "Eigenbau", "model": "Raspberry Pi Zero W",
              "sw_version": __version__}
    common = {"device": device, "availability_topic": base + "/availability",
              "payload_available": "online", "payload_not_available": "offline"}
    results = []

    def add(component, key, values):
        payload = {**common, "unique_id": cfg.device_id + "_" + key,
                   "state_topic": base + "/state", **values}
        results.append((f"homeassistant/{component}/{cfg.device_id}/{key}/config", payload))

    add("climate", "climate", {
        "name": "Ofenfühler-Regelung", "modes": ["off", "heat"],
        "mode_command_topic": base + "/command/mode", "mode_state_topic": base + "/state",
        "mode_state_template": "{{ value_json.mode }}",
        "temperature_command_topic": base + "/command/target",
        "temperature_state_topic": base + "/state", "temperature_state_template": "{{ value_json.target }}",
        "current_temperature_topic": base + "/state",
        "current_temperature_template": "{{ value_json.pt100 if value_json.pt100_valid else 'None' }}",
        "action_topic": base + "/state", "action_template": "{{ value_json.action }}",
        "min_temp": cfg.min_target, "max_temp": cfg.max_target, "temp_step": 1,
        "temperature_unit": "C", "precision": 0.1, "retain": False, "qos": 0,
    })
    results[-1][1].pop("state_topic")  # Climate uses its dedicated mode/temperature/action topics.
    for key, name, unit, cls in (
        ("pt100", "Temperatur Ofenfühler", "°C", "temperature"),
        ("bank_temperature", "Temperatur Sitzbank", "°C", "temperature"),
        ("humidity", "Feuchte Sitzbank", "%", "humidity"),
        ("remaining_seconds", "Restlaufzeit", "s", "duration"),
        ("pt100_age", "Alter Ofenmessung", "s", "duration"),
    ):
        validity = "pt100_valid" if key == "pt100" else "sht41_valid" if key in {"bank_temperature", "humidity"} else None
        template = "{{ value_json." + key + " }}"
        if validity:
            template = "{{ value_json." + key + " if value_json." + validity + " else None }}"
        add("sensor", key, {"name": name, "unit_of_measurement": unit,
                           "device_class": cls, "value_template": template, "expire_after": 15})
    for key, name in (("phase", "Sitzungsphase"), ("fault_code", "Fehlercode"),
                      ("last_rejection", "Letzte Befehlsablehnung"), ("operating_mode", "Betriebsart"),
                      ("warning", "Warnung")):
        add("sensor", key, {"name": name, "value_template": "{{ value_json." + key + " }}",
                            "expire_after": 15, "entity_category": "diagnostic"})
    for key, name in (("armed", "Lokal freigegeben"), ("fault", "Störung"),
                      ("k1_released", "Schütz K1 abgefallen"), ("k2_released", "Schütz K2 abgefallen"),
                      ("safety_ok", "STB-Stop-Kreis bereit"), ("door_closed", "Tür geschlossen"),
                      ("heating_enabled", "Heizbetrieb aktiviert"), ("heating_demand", "Heizanforderung"),
                      ("maintenance", "Update-Wartungszustand"),
                      ("pt100_valid", "Ofenfühler gültig"), ("sht41_valid", "Bankfühler gültig")):
        add("binary_sensor", key, {"name": name,
            "value_template": "{{ 'ON' if value_json." + key + " else 'OFF' }}",
            "payload_on": "ON", "payload_off": "OFF", "expire_after": 15})
        if key in {"k1_released", "k2_released", "safety_ok", "door_closed"}:
            # Monitor mode has no GPIO measurements. Do not invent contactor/door states.
            payload = results[-1][1]
            payload.pop("availability_topic")
            payload["availability_mode"] = "all"
            payload["availability"] = [
                {"topic": base + "/availability"},
                {"topic": base + "/state", "value_template":
                 "{{ 'offline' if value_json." + key + " is none else 'online' }}"},
            ]
    add("light", "light", {"name": "Banklicht", "command_topic": base + "/command/light",
        "state_value_template": "{{ value_json.light }}", "retain": False})
    add("select", "profile", {"name": "Lokales Programm", "command_topic": base + "/command/profile",
        "value_template": "{{ value_json.profile }}", "options": ["manual", *cfg.profiles], "retain": False})
    add("button", "stop", {"name": "Sitzung stoppen", "command_topic": base + "/command/stop",
        "payload_press": "STOP", "retain": False})
    # Buttons have no state_topic.
    results[-1][1].pop("state_topic")
    return results


class MQTT:
    def __init__(self, cfg, commands, stop_event):
        import paho.mqtt.client as mqtt
        self.cfg = cfg
        self.commands = commands
        self.stop_event = stop_event
        self.connected = False
        self.epoch = 0
        self.rediscover = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=cfg.device_id, clean_session=True)
        self.client.max_queued_messages_set(128)  # Bounded, but sufficient for all discovery messages.
        self.client.reconnect_delay_set(1, 30)
        self.client.will_set(cfg.base + "/availability", "offline", qos=1, retain=True)
        username, password = os.getenv("MQTT_USERNAME"), os.getenv("MQTT_PASSWORD")
        if username:
            self.client.username_pw_set(username, password)
        if cfg.tls:
            self.client.tls_set(ca_certs=cfg.ca_file or None)
        self.client.on_connect = self._connect
        self.client.on_disconnect = self._disconnect
        self.client.on_message = self._message
        self.client.enable_logger(log)

    def _connect(self, client, userdata, flags, reason, properties):
        if reason != 0:
            log.warning("MQTT connection refused: %s", reason)
            return
        self.epoch += 1
        self.connected = True
        client.subscribe([(self.cfg.base + "/command/+", 0), ("homeassistant/status", 0)])
        self.rediscover.set()
        log.info("MQTT connected to %s:%s", self.cfg.host, self.cfg.port)

    def _disconnect(self, client, userdata, flags, reason, properties):
        self.connected = False
        self.epoch += 1
        log.warning("MQTT disconnected: %s", reason)

    def _message(self, client, userdata, message):
        if message.topic == "homeassistant/status":
            if message.payload == b"online":
                self.rediscover.set()
            return
        if not message.topic.startswith(self.cfg.base + "/command/"):
            return
        action = message.topic.rsplit("/", 1)[-1]
        if message.retain or len(message.payload) > 128:
            log.warning("Rejected retained/oversized MQTT command")
            return
        try:
            value = message.payload.decode("utf-8")
        except UnicodeError:
            return
        if action == "stop" or (action == "mode" and value == "off"):
            self.stop_event.set()  # Never lose STOP because a command queue is full.
            return
        try:
            self.commands.put_nowait((time.monotonic(), self.epoch, action, value))
        except Full:
            log.warning("Command queue full; command dropped")

    def start(self):
        self.client.connect_async(self.cfg.host, self.cfg.port, keepalive=15)
        self.client.loop_start()

    def publish(self, state):
        if not self.connected:
            return
        if self.rediscover.is_set():
            self.rediscover.clear()
            for topic, payload in discoveries(self.cfg):
                self.client.publish(topic, json.dumps(payload, allow_nan=False), qos=1, retain=True)
        # Actual values are not retained; discovery is. Republish every five seconds.
        self.client.publish(self.cfg.base + "/state", json.dumps(state, allow_nan=False), qos=0, retain=False)
        self.client.publish(self.cfg.base + "/availability", "online", qos=0, retain=True)

    def close(self):
        if self.connected:
            try:
                self.client.publish(self.cfg.base + "/availability", "offline", qos=1, retain=True).wait_for_publish(2)
            except (RuntimeError, ValueError):
                pass
        self.client.disconnect()
        self.client.loop_stop()
