import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class RuntimeTests(unittest.TestCase):
    def test_simulation_daemon_start_stop_restart(self):
        with tempfile.TemporaryDirectory(prefix="sauna-") as folder:
            # AF_UNIX paths on macOS are limited to 104 bytes, including long TMPDIRs.
            socket_path = f"/tmp/sauna-test-{os.getpid()}.sock"
            marker = Path(folder) / "maintenance.json"
            args = [sys.executable, "-m", "sauna_controller.app", "--config", "config/simulation.toml",
                    "--state-dir", folder, "--socket", socket_path,
                    "--maintenance-file", str(marker)]

            def request(action, value=None):
                with socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(2)
                    conn.connect(socket_path)
                    conn.sendall(json.dumps({"action": action, "value": value}).encode())
                    return json.loads(conn.recv(16384))

            def spawn():
                proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                for _ in range(100):
                    if proc.poll() is not None:
                        self.fail(proc.stderr.read())
                    if Path(socket_path).exists():
                        return proc
                    time.sleep(0.05)
                proc.terminate()
                self.fail("daemon failed to start")

            proc = spawn()
            try:
                time.sleep(1.1)  # Startup feedback settling; no external hardware involved.
                self.assertEqual(request("status")["state"]["phase"], "OFF")
                self.assertTrue(request("arm")["ok"])
                self.assertTrue(request("mode", "heat")["ok"])
                state = request("status")["state"]
                self.assertEqual(state["phase"], "PREHEAT")
                self.assertTrue(state["heating_demand"])
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                proc.stderr.close()

            # Reboot during an update must preserve the durable maintenance lock.
            marker.write_text('{}')
            proc = spawn()
            try:
                self.assertEqual(request("status")["state"]["phase"], "MAINTENANCE")
                self.assertFalse(request("arm")["ok"])
                self.assertFalse(request("mode", "heat")["ok"])
                marker.unlink()
                time.sleep(0.2)
                self.assertEqual(request("status")["state"]["phase"], "OFF")
                self.assertFalse(request("mode", "heat")["ok"])
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                proc.stderr.close()
            self.assertFalse(Path(socket_path).exists())
            proc = spawn()
            try:
                self.assertEqual(request("status")["state"]["phase"], "OFF")
                self.assertFalse(request("mode", "heat")["ok"])
                self.assertTrue(request("stop")["ok"])
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
