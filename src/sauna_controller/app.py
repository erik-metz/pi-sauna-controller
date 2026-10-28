from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import signal
import socket
import threading
import time
from pathlib import Path
from queue import Empty, Queue

from .config import load
from .core import Controller, Inputs, Reading
from .hardware import GPIO, Simulation, sensor_worker, dew_point_c
from .mqtt import MQTT
from .storage import FaultStore
from .environment import load_env_file

log = logging.getLogger(__name__)


def add_aht21_state(state, channels, samples, now, max_age):
    for index, _channel in enumerate(channels, start=1):
        key = f"aht21_{index}"
        sample = samples.get(key, Reading(None, now, "not_yet_measured"))
        valid = sample.valid(now, max_age + 2) and sample.humidity is not None
        state[key + "_valid"] = valid
        state[key + "_temperature"] = round(sample.temperature, 2) if valid else None
        state[key + "_humidity"] = round(sample.humidity, 2) if valid else None
        state[key + "_dew_point"] = round(dew_point_c(sample.temperature, sample.humidity), 2) if valid and sample.humidity > 0 else None
        state[key + "_error"] = sample.error
    return state


def notify(message):
    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        sock.sendall(message.encode())


def serve_local(server, controller, inputs, now, state):
    try:
        conn, _ = server.accept()
    except BlockingIOError:
        return
    with conn:
        # A slow/malicious local client may block at most 50 ms, not the control loop.
        conn.settimeout(0.05)
        try:
            packet = conn.recv(2049)
            if len(packet) > 2048:
                raise ValueError("Request too large")
            command = json.loads(packet)
            action = command.get("action")
            if action == "status":
                result = {"ok": True, "state": state}
            elif action in {"arm", "reset"}:
                if controller.cfg.mode != "simulation":
                    result = {"ok": False, "error": "Use physical GPIO5 button; CLI arm/reset is simulation-only"}
                else:
                    result = {"ok": getattr(controller, action)(inputs, now)}
            elif action == "maintenance":
                result = {"ok": controller.enter_maintenance(inputs, now),
                          "error": controller.last_rejection}
            else:
                ok = controller.command(action, command.get("value"), inputs, now)
                result = {"ok": ok, "error": "" if ok else controller.last_rejection}
            conn.sendall(json.dumps(result, allow_nan=False).encode())
        except (ValueError, OSError, AttributeError) as exc:
            log.warning("Local command failed: %s", type(exc).__name__)


def run(cfg, state_dir, socket_path, maintenance_file="/var/lib/sauna-updater/maintenance.json"):
    finish = threading.Event()
    stop = threading.Event()
    commands = Queue(maxsize=32)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: finish.set())
    outputs = worker = mqtt = server = None
    now = time.monotonic()
    store = FaultStore(state_dir)
    core = Controller(cfg, now, store.read(), store.write)
    marker = Path(maintenance_file)
    # Survives service restarts and power loss in the middle of an update.
    if marker.exists():
        core.maintenance = core.update_pending = True
        core.stop(now)
    pt = sht = Reading(None, now, "not_yet_measured")
    aht = {}
    context = mp.get_context("spawn")
    samples = context.Queue(maxsize=2)
    simulation = Simulation(now) if cfg.mode == "simulation" else None
    path = Path(socket_path)
    try:
        if cfg.mode == "hardware":
            outputs = GPIO(cfg)  # All outputs initialized OFF, before opening networking.
        if cfg.mode != "simulation" and cfg.sensors_enabled:
            worker = context.Process(target=sensor_worker, args=(cfg, samples), daemon=True)
            worker.start()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(4)
        server.setblocking(False)
        if cfg.mqtt_enabled:
            mqtt = MQTT(cfg, commands, stop)
            mqtt.start()
        last_publish = -100.0
        last_serialized = ""
        button_since = None
        button_consumed = False
        button_released = False
        heartbeat = False
        initialized = cfg.mode != "hardware"
        boot_deadline = now + 15
        notify("READY=1")
        log.info("Controller started: mode=%s, device=%s; hardware heating=%s",
                 cfg.mode, cfg.device_id, cfg.mode == "hardware")
        while not finish.is_set():
            cycle_start = now = time.monotonic()
            core.update_pending = marker.exists()
            if core.maintenance and not core.update_pending:
                core.leave_maintenance(now)
            if simulation:
                inputs = simulation.read(now, core.heating)
            else:
                while True:
                    try:
                        reading = samples.get_nowait()
                        pt, sht = reading["pt"], reading["sht"]
                        aht = reading.get("aht", {})
                    except Empty:
                        break
                inputs = outputs.read(pt, sht) if outputs else Inputs(pt, sht, None, None, None, None)
            if not initialized and (inputs.pt.valid(now, cfg.sensor_max_age) or now >= boot_deadline):
                initialized = True
            if stop.is_set():
                stop.clear()
                core.stop(now)
                # Discard commands queued before STOP. No immediate restart from queue.
                while True:
                    try:
                        commands.get_nowait()
                    except Empty:
                        break
            else:
                for _ in range(16):
                    try:
                        received, epoch, action, value = commands.get_nowait()
                    except Empty:
                        break
                    if mqtt and mqtt.connected and epoch == mqtt.epoch and now - received <= 1:
                        core.command(action, value, inputs, now)
            if outputs:
                pressed = bool(outputs.inputs["arm"].value)
                if not pressed:
                    if button_since is not None and not button_consumed and now - button_since >= 0.1:
                        core.arm(inputs, now)
                    button_since = None
                    button_consumed = False
                    button_released = True
                elif button_released:
                    if button_since is None:
                        button_since = now
                    if core.fault and now - button_since >= 3 and not button_consumed:
                        core.reset(inputs, now)
                        button_consumed = True
            state = core.state(inputs, now)
            add_aht21_state(state, cfg.aht21_channels, aht, now, cfg.sensor_max_age)
            serve_local(server, core, inputs, now, state)
            if initialized:
                core.tick(inputs, now)
            if outputs:
                heartbeat = not heartbeat if (initialized and not core.fault and not core.maintenance
                                               and core.healthy(inputs, now)) else False
                outputs.apply(core.heating, core.light, heartbeat)
            state = core.state(inputs, now)
            add_aht21_state(state, cfg.aht21_channels, aht, now, cfg.sensor_max_age)
            serialized = json.dumps(state, sort_keys=True, allow_nan=False)
            # Limit MQTT publication to 1 Hz; publish immediately enough for HA plus every 5s.
            changed = serialized != last_serialized
            if now - last_publish >= 5 or (changed and now - last_publish >= 1):
                if mqtt:
                    mqtt.publish(state)
                last_publish = now
                last_serialized = serialized
            notify("WATCHDOG=1")  # Main-loop health, never generated by network/sensor thread.
            time.sleep(max(0, 0.1 - (time.monotonic() - cycle_start)))
    finally:
        # Actuators off before any potentially blocking network/process cleanup.
        if outputs:
            outputs.close()
        if worker:
            worker.terminate()
            worker.join(timeout=0.5)
            if worker.is_alive():
                worker.kill()
        samples.close()
        samples.cancel_join_thread()
        if mqtt:
            mqtt.close()
        if server:
            server.close()
            path.unlink(missing_ok=True)
        log.info("Controller stopped; no session will be resumed")


def main():
    parser = argparse.ArgumentParser(description="Local sauna regulation with MQTT")
    parser.add_argument("--config", default="/etc/sauna/controller.toml")
    parser.add_argument("--state-dir", default="/var/lib/sauna")
    parser.add_argument("--socket", default="/run/sauna/control.sock")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--maintenance-file", default="/var/lib/sauna-updater/maintenance.json")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(args.env_file)
    cfg = load(args.config)
    if args.check:
        print(f"Configuration OK: {cfg.device_id}, mode={cfg.mode}")
        return
    run(cfg, args.state_dir, args.socket, args.maintenance_file)


if __name__ == "__main__":
    main()
