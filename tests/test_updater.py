import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from sauna_controller.config import Config
from sauna_controller.core import Controller
from test_controller import inputs

spec = importlib.util.spec_from_file_location("updater", "deploy/updater.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)
build_spec = importlib.util.spec_from_file_location("build_release", "tools/build_release.py")
builder = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(builder)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.core = Controller(Config(mode="simulation", device_id="test_sim"), 0)

    def test_busy_session_is_not_interrupted(self):
        self.core.arm(inputs(2), 2)
        self.core.command("mode", "heat", inputs(2), 2)
        self.assertFalse(self.core.enter_maintenance(inputs(3), 3))
        self.assertIsNotNone(self.core.started)

    def test_armed_controller_defers_update(self):
        self.core.arm(inputs(2), 2)
        self.assertFalse(self.core.enter_maintenance(inputs(3), 3))

    def test_maintenance_blocks_every_start_path(self):
        self.assertTrue(self.core.enter_maintenance(inputs(2), 2))
        self.assertFalse(self.core.arm(inputs(3), 3))
        self.assertFalse(self.core.command("mode", "heat", inputs(3), 3))
        self.assertFalse(self.core.command("light", "ON", inputs(3), 3))
        self.core.stop(4)
        self.assertEqual(self.core.phase, "MAINTENANCE")
        self.core.leave_maintenance(5)
        self.assertEqual(self.core.phase, "OFF")
        self.assertFalse(self.core.command("mode", "heat", inputs(5), 5))

    def test_pending_update_prevents_new_local_arm(self):
        self.core.update_pending = True
        self.assertFalse(self.core.arm(inputs(2), 2))

    def test_hardware_requires_confirmed_release(self):
        cfg = Config(mode="hardware", heating_enabled=True, safety_verified=True).validate()
        core = Controller(cfg, 0)
        self.assertFalse(core.enter_maintenance(inputs(2, feedback1=False), 2))
        self.assertFalse(core.enter_maintenance(inputs(2, feedback1=None), 2))
        self.assertFalse(core.enter_maintenance(inputs(0.1), 0.1))
        self.assertTrue(core.enter_maintenance(inputs(2), 2))


class UpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.key = Path(cls.keys.name) / "private.pem"
        cls.public = Path(cls.keys.name) / "public.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(cls.key)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(["openssl", "pkey", "-in", str(cls.key), "-pubout", "-out", str(cls.public)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.root = self.path / "installed"
        previous = self.root / "releases/0.1.0"
        previous.mkdir(parents=True)
        (self.root / "current").symlink_to(previous)
        self.system = Mock()
        self.system.request.return_value = {"ok": True}
        self.system.healthy.return_value = True
        self.cfg = {"repository": "owner/repo", "root": str(self.root),
                    "state_dir": str(self.path / "state"), "public_key": str(self.public)}
        self.release = self.path / "release"
        self.manifest = builder.build(Path.cwd(), self.release, self.key, "a"*40)
        base = f'https://github.com/owner/repo/releases/download/{self.manifest["tag"]}/'
        self.assets = {path.name: path.read_bytes() for path in self.release.iterdir()}
        release = {"tag_name": self.manifest["tag"], "draft": False, "prerelease": False,
                   "assets": [{"name": name, "browser_download_url": base + name} for name in self.assets]}
        self.download = Mock(side_effect=lambda url, limit: json.dumps(release).encode() if "api.github.com" in url
                             else self.assets[url.rsplit("/", 1)[-1]])
        self.updater = updater.Updater(self.cfg, system=self.system, download=self.download)

    def test_signed_update_exchanges_current_and_clears_lock(self):
        self.assertEqual(self.updater.check(), "updated")
        self.assertEqual(self.updater.current_version(), self.manifest["version"])
        self.assertFalse(self.updater.marker.exists())
        self.system.request.assert_called_once_with("maintenance")
        self.system.stop.assert_called_once()

    def test_active_session_defers_without_stop(self):
        self.system.request.return_value = {"ok": False}
        self.assertEqual(self.updater.check(), "deferred")
        self.assertEqual(self.updater.current_version(), "0.1.0")
        self.system.stop.assert_not_called()
        self.assertFalse(self.updater.marker.exists())

    def test_bad_signature_never_reaches_controller(self):
        self.assets["manifest.sig"] = b"bad signature"
        with self.assertRaises(subprocess.CalledProcessError):
            self.updater.check()
        self.system.request.assert_not_called()
        self.system.stop.assert_not_called()

    def test_altered_archive_never_reaches_controller(self):
        self.assets[updater.ASSET] += b"tampered"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.updater.check()
        self.system.request.assert_not_called()

    def test_failed_candidate_rolls_back_and_is_not_retried(self):
        self.system.healthy.side_effect = [False, True]
        self.assertEqual(self.updater.check(), "rolled_back")
        self.assertEqual(self.updater.current_version(), "0.1.0")
        self.assertFalse(self.updater.marker.exists())
        self.assertEqual(self.updater.check(), "failed_version_skipped")

    def test_failed_rollback_retains_durable_lock(self):
        self.system.healthy.return_value = False
        with self.assertRaisesRegex(RuntimeError, "maintenance lock retained"):
            self.updater.check()
        self.assertTrue(self.updater.marker.exists())

    def test_power_loss_recovers_previous_before_network_request(self):
        updater.atomic_json(self.updater.marker, {"previous": "0.1.0", "candidate": "0.2.0"})
        self.assertEqual(self.updater.check(), "recovered")
        self.download.assert_not_called()
        self.assertFalse(self.updater.marker.exists())

    def test_preflight_failure_does_not_stop_controller(self):
        self.system.preflight.side_effect = RuntimeError("config incompatible")
        with self.assertRaises(RuntimeError):
            self.updater.check()
        self.system.stop.assert_not_called()
        self.assertFalse(self.updater.marker.exists())

    def test_offline_does_not_stop_controller(self):
        self.download.side_effect = OSError("network unavailable")
        with self.assertRaises(OSError):
            self.updater.check()
        self.system.stop.assert_not_called()

    def test_unsafe_archive_paths_and_links_are_rejected(self):
        for name, kind in (("../outside.py", tarfile.REGTYPE),
                           ("src/sauna_controller/link.py", tarfile.SYMTYPE),
                           ("/etc/evil.py", tarfile.REGTYPE)):
            archive = self.path / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.size = 0
                stream.addfile(info, io.BytesIO())
            with self.assertRaises(ValueError):
                updater.extract_source(archive, self.path / "extracted")

    def test_version_validation(self):
        for value in ("../x", "1.2", "v1.2.3", "1.2.3-beta", "01.2.3", None):
            with self.assertRaises(ValueError):
                updater.version_tuple(value)


if __name__ == "__main__":
    unittest.main()
