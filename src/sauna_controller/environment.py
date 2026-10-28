"""Small non-executing .env reader; never run shell commands from configuration."""
import os
import re
import shlex
from pathlib import Path

RUNTIME_NAMES = {"MQTT_HOST", "MQTT_PORT", "MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_TLS",
                 "MQTT_CA_FILE", "GITHUB_REPOSITORY"}


def read_env_file(path):
    result = {}
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid environment variable name on line {number}")
        if key in result:
            raise ValueError(f"Duplicate environment variable on line {number}")
        try:
            fields = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid environment quoting on line {number}") from None
        if len(fields) > 1:
            raise ValueError(f"Quote values containing spaces on line {number}")
        result[key] = fields[0] if fields else ""
    return result


def load_env_file(path):
    if Path(path).is_file():
        for key, value in read_env_file(path).items():
            os.environ.setdefault(key, value)


def write_runtime_env(source, destination):
    # Keep the private release signing key on the development machine / CI only.
    values = read_env_file(source)
    lines = []
    for key in sorted(RUNTIME_NAMES & values.keys()):
        # systemd EnvironmentFile supports quoted strings without shell evaluation.
        value = values[key].replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{value}"')
    destination = Path(destination)
    destination.write_text("\n".join(lines) + "\n")
    destination.chmod(0o600)
