import os
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sauna_controller.config import load
from sauna_controller.environment import load_env_file, read_env_file, write_runtime_env


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = Path(self.temp.name) / ".env"

    def test_secrets_are_read_without_evaluating_shell_code(self):
        self.env.write_text('MQTT_PASSWORD="space # $HOME $(not-a-command)"\nMQTT_USERNAME=test\n')
        values = read_env_file(self.env)
        self.assertEqual(values["MQTT_PASSWORD"], "space # $HOME $(not-a-command)")

    def test_actual_environment_takes_precedence(self):
        self.env.write_text('MQTT_HOST="from-file"\n')
        with patch.dict(os.environ, {"MQTT_HOST": "from-environment"}):
            load_env_file(self.env)
            self.assertEqual(os.environ["MQTT_HOST"], "from-environment")

    def test_missing_env_file_is_optional(self):
        load_env_file(Path(self.temp.name) / "missing")

    def test_invalid_and_duplicate_keys_rejected_without_exposing_value(self):
        for content in ("bad-name=secret", 'MQTT_PASSWORD="unterminated', "MQTT_HOST=a\nMQTT_HOST=b"):
            self.env.write_text(content)
            with self.assertRaises(ValueError) as raised:
                read_env_file(self.env)
            self.assertNotIn("secret", str(raised.exception))

    def test_runtime_export_excludes_private_signing_key_and_unknown_variables(self):
        self.env.write_text('MQTT_PASSWORD="private password"\nMQTT_HOST=broker\n'
                            'RELEASE_SIGNING_KEY_B64="privatekey"\nOTHER_SECRET="not-for-pi"\n')
        destination = Path(self.temp.name) / "runtime.env"
        write_runtime_env(self.env, destination)
        exported = read_env_file(destination)
        self.assertEqual(exported, {"MQTT_PASSWORD": "private password", "MQTT_HOST": "broker"})
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_runtime_export_preserves_quotes_backslashes_and_dollar_signs(self):
        self.env.write_text("MQTT_PASSWORD='a\"b\\c$#'\n")
        destination = Path(self.temp.name) / "runtime.env"
        write_runtime_env(self.env, destination)
        self.assertEqual(read_env_file(destination), read_env_file(self.env))

    def test_mqtt_runtime_settings_override_toml(self):
        with patch.dict(os.environ, {"MQTT_HOST": "broker.test", "MQTT_PORT": "8883", "MQTT_TLS": "true"}):
            cfg = load("config/controller.toml")
        self.assertEqual((cfg.host, cfg.port, cfg.tls), ("broker.test", 8883, True))

    def test_bootstrap_only_includes_the_named_public_key(self):
        spec = importlib.util.spec_from_file_location("make_bootstrap", "tools/make_bootstrap.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = Path(self.temp.name)
        (root / "deploy").mkdir()
        for name in ("release-public.pem", "private.pem", "secret.key", "credentials.env"):
            (root / "deploy" / name).write_text("test material")
        self.assertEqual(module.collect_files(root), [root / "deploy/release-public.pem"])


if __name__ == "__main__":
    unittest.main()
