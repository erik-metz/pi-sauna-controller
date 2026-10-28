"""Root-owned bootstrap updater. No dependencies beyond Python 3.11+ and openssl.

Only signed application source is exchanged. This updater, system services and
system packages intentionally require explicit administrative upgrades.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

log = logging.getLogger("sauna-updater")
ASSET = "sauna-controller.tar.gz"
MAX_ARCHIVE = 2 * 1024 * 1024


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r"0|[1-9]\d*", version.split(".")[0]):
        raise ValueError("Invalid release version")
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("Expected stable major.minor.patch version")
    return tuple(map(int, version.split(".")))


def atomic_json(path, values):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(values, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o644)
    temporary.replace(path)
    fsync_directory(path.parent)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fetch(url, limit):
    if not url.startswith("https://"):
        raise ValueError("HTTPS required")
    request = urllib.request.Request(url, headers={"User-Agent": "sauna-updater/1",
                                                  "Accept": "application/vnd.github+json" if "api.github.com/" in url else "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=15) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Insecure download redirect")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds maximum size")
    return data


def verify_manifest(raw, signature, public_key, folder):
    manifest_file = Path(folder) / "manifest.json"
    signature_file = Path(folder) / "manifest.sig"
    manifest_file.write_bytes(raw)
    signature_file.write_bytes(signature)
    subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public_key),
                    "-signature", str(signature_file), str(manifest_file)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("updater_api") != 1:
        raise ValueError("Unsupported signed release format")
    version_tuple(value.get("version"))
    if value.get("tag") != "v" + value["version"] or value.get("asset") != ASSET:
        raise ValueError("Manifest tag/asset mismatch")
    if not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"]):
        raise ValueError("Invalid archive digest")
    if not isinstance(value.get("source_commit"), str) or not re.fullmatch(r"[a-f0-9]{40}", value["source_commit"]):
        raise ValueError("Invalid source commit")
    return value


def extract_source(archive_path, destination):
    """Reject links, special files, traversal, duplicates and excessive unpacked size."""
    seen = set()
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > 256:
            raise ValueError("Invalid archive member count")
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or ".." in path.parts
                    or len(path.parts) < 3 or path.parts[:2] != ("src", "sauna_controller")
                    or path.suffix != ".py" or member.name in seen
                    or not re.fullmatch(r"src/sauna_controller/[a-zA-Z0-9_/]+\.py", member.name)):
                raise ValueError("Disallowed archive member: " + member.name)
            seen.add(member.name)
            total += member.size
            if member.size > 512 * 1024 or total > 4 * 1024 * 1024:
                raise ValueError("Unpacked release too large")
        if "src/sauna_controller/__init__.py" not in seen or "src/sauna_controller/app.py" not in seen:
            raise ValueError("Missing application entrypoint")
        for member in members:
            target = Path(destination) / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
            os.chmod(target, 0o644)


def request_controller(path, action):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.connect(str(path))
        conn.sendall(json.dumps({"action": action}).encode())
        return json.loads(conn.recv(32768))


class System:
    def __init__(self, cfg):
        self.cfg = cfg

    def request(self, action):
        return request_controller(self.cfg["controller_socket"], action)

    def stop(self):
        subprocess.run(["systemctl", "stop", "sauna-controller.service"], check=True, timeout=15)

    def start(self):
        subprocess.run(["systemctl", "reset-failed", "sauna-controller.service"], check=True, timeout=5)
        subprocess.run(["systemctl", "start", "sauna-controller.service"], check=True, timeout=30)

    def preflight(self, release):
        python = Path(self.cfg["root"]) / "venv/bin/python"
        subprocess.run(["runuser", "-u", self.cfg["user"], "--", "env",
                        "PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH=" + str(Path(release) / "src"),
                        str(python), "-m", "sauna_controller.app", "--check",
                        "--config", self.cfg["controller_config"]], check=True, timeout=20)

    def healthy(self, version):
        deadline = time.monotonic() + 25
        successes = 0
        while time.monotonic() < deadline:
            try:
                result = self.request("status")
                state = result["state"]
                if (result["ok"] and state["version"] == version and state["maintenance"]
                        and not state["fault"] and not state["heating_demand"]
                        and state["mode"] == "off"
                        and (state["operating_mode"] != "hardware" or
                             (state["pt100_valid"] and state["safety_ok"]
                              and state["k1_released"] and state["k2_released"]))):
                    successes += 1
                    if successes >= 3:
                        return True
                else:
                    successes = 0
            except (OSError, ValueError, KeyError):
                successes = 0
            time.sleep(0.5)
        return False


class Updater:
    def __init__(self, cfg, system=None, download=fetch):
        repository = cfg["repository"]
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository):
            raise ValueError("Invalid GitHub repository")
        self.cfg, self.download = cfg, download
        self.root = Path(cfg["root"])
        self.releases = self.root / "releases"
        self.state_dir = Path(cfg["state_dir"])
        self.marker = self.state_dir / "maintenance.json"
        self.state_file = self.state_dir / "state.json"
        self.system = system or System(cfg)
        self.releases.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def current_version(self):
        path = (self.root / "current").resolve(strict=True)
        if path.parent != self.releases.resolve():
            raise ValueError("Current application is outside managed releases")
        version_tuple(path.name)
        return path.name

    def switch(self, version):
        version_tuple(version)
        target = self.releases / version
        if not target.is_dir() or target.is_symlink():
            raise ValueError("Release directory missing or not regular")
        temporary = self.root / "current.next"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        temporary.replace(self.root / "current")
        fsync_directory(self.root)

    def state(self):
        if not self.state_file.exists():
            return {"failed_versions": []}
        result = json.loads(self.state_file.read_text())
        if not isinstance(result.get("failed_versions"), list):
            raise ValueError("Invalid updater state")
        return result

    def clear_marker(self):
        self.marker.unlink(missing_ok=True)
        fsync_directory(self.state_dir)

    def rollback(self, transaction):
        old = transaction["previous"]
        self.system.stop()
        self.switch(old)
        self.system.start()
        if not self.system.healthy(old):
            # Keep durable lock; no repeated blind restart or automatic fault reset.
            raise RuntimeError("Rollback unhealthy; maintenance lock retained, operator required")
        state = self.state()
        if transaction["candidate"] not in state["failed_versions"]:
            state["failed_versions"].append(transaction["candidate"])
        state.update(installed=old, last_result="rolled_back", failed=transaction["candidate"])
        atomic_json(self.state_file, state)
        self.clear_marker()
        log.warning("Rolled back to %s; heating remains OFF", old)

    def recover(self):
        if self.marker.exists():
            transaction = json.loads(self.marker.read_text())
            version_tuple(transaction["previous"])
            version_tuple(transaction["candidate"])
            log.warning("Recovering interrupted update to %s", transaction["candidate"])
            self.rollback(transaction)
            return True
        return False

    def install(self, manifest, stage):
        candidate, previous = manifest["version"], self.current_version()
        self.system.preflight(stage)
        transaction = {"previous": previous, "candidate": candidate}
        atomic_json(self.marker, transaction)
        try:
            result = self.system.request("maintenance")
        except (OSError, ValueError):
            self.clear_marker()
            raise RuntimeError("Controller unavailable; update deferred without stopping service")
        if not result.get("ok"):
            self.clear_marker()
            log.info("Update deferred: active session, armed controller, fault or feedback not ready")
            return "deferred"
        target = self.releases / candidate
        try:
            # No code exchange until controller has atomically inhibited all new starts.
            self.system.stop()
            if target.exists():
                raise RuntimeError("Release directory already exists; operator review required")
            shutil.move(str(stage), target)
            atomic_json(target / "release.json", manifest)
            self.switch(candidate)
            self.system.start()
            if not self.system.healthy(candidate):
                raise RuntimeError("New version failed health check")
            state = self.state()
            state.update(installed=candidate, previous=previous, last_result="updated")
            atomic_json(self.state_file, state)
            self.clear_marker()
            log.info("Installed %s; controller remains OFF", candidate)
            return "updated"
        except Exception:
            log.exception("Update failed; attempting rollback")
            self.rollback(transaction)
            return "rolled_back"

    def check(self):
        if self.recover():
            return "recovered"
        repo = self.cfg["repository"]
        try:
            release = json.loads(self.download(f"https://api.github.com/repos/{repo}/releases/latest", 1024*1024))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                log.info("No stable GitHub release published yet")
                return "no_release"
            raise
        if release.get("draft") is not False or release.get("prerelease") is not False:
            raise ValueError("Only published stable releases allowed")
        tag = release.get("tag_name", "")
        candidate = tag.removeprefix("v")
        if tag != "v" + candidate:
            raise ValueError("Release tag must start with v")
        version_tuple(candidate)
        if version_tuple(candidate) <= version_tuple(self.current_version()):
            return "current"
        if candidate in self.state()["failed_versions"]:
            log.warning("Skipping previously failed version %s", candidate)
            return "failed_version_skipped"
        assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}
        for name in (ASSET, "manifest.json", "manifest.sig"):
            expected = f"https://github.com/{repo}/releases/download/{tag}/{name}"
            if assets.get(name) != expected:
                raise ValueError("Missing release asset or unexpected asset URL: " + name)
        # Parent must be traversable by the unprivileged preflight process.
        with tempfile.TemporaryDirectory(prefix=".download-", dir=self.releases) as folder:
            os.chmod(folder, 0o755)
            manifest = verify_manifest(self.download(assets["manifest.json"], 16384),
                                       self.download(assets["manifest.sig"], 4096),
                                       self.cfg["public_key"], folder)
            if manifest["version"] != candidate:
                raise ValueError("Signed manifest does not match selected GitHub release")
            data = self.download(assets[ASSET], MAX_ARCHIVE)
            if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                raise ValueError("Release archive digest mismatch")
            archive = Path(folder) / ASSET
            archive.write_bytes(data)
            stage = Path(folder) / "application"
            stage.mkdir(mode=0o755)
            extract_source(archive, stage)
            return self.install(manifest, stage)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/sauna/updater.toml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        parser.error("Updater requires root; use systemctl start sauna-updater.service")
    cfg = tomllib.loads(Path(args.config).read_text())
    cfg["repository"] = os.getenv("GITHUB_REPOSITORY", cfg["repository"])
    updater = Updater(cfg)
    # One lock for automatic timer runs, manual requests and interrupted recovery.
    with (updater.state_dir / "update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("Another updater is running")
            return
        log.info("Update result: %s", updater.check())


if __name__ == "__main__":
    main()
