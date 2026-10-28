"""Build a small source-only package and sign its manifest with a local RSA key."""
import argparse
import base64
import hashlib
import json
import re
import subprocess
import os
import sys
import tempfile
import tarfile
from pathlib import Path


def build(root, output, key, commit):
    root, output = Path(root), Path(output)
    init = (root / "src/sauna_controller/__init__.py").read_text()
    version = re.search(r'__version__ = "(\d+\.\d+\.\d+)"', init).group(1)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / "sauna-controller.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted((root / "src/sauna_controller").rglob("*.py")):
            if "__pycache__" not in path.parts:
                info = archive.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with path.open("rb") as source:
                    archive.addfile(info, source)
    manifest = {"schema": 1, "updater_api": 1, "version": version, "tag": "v" + version,
                "source_commit": commit, "asset": archive_path.name,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest()}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key), "-out",
                    str(output / "manifest.sig"), str(manifest_path)], check=True)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", help="Optional PEM path; otherwise use ignored .env RELEASE_SIGNING_KEY_B64")
    parser.add_argument("--output", default="dist")
    parser.add_argument("--commit")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from sauna_controller.environment import load_env_file
    load_env_file(root / ".env")
    commit = args.commit or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        parser.error("Expected a full source commit SHA")
    if args.key:
        manifest = build(root, args.output, args.key, commit)
    else:
        private = base64.b64decode(os.getenv("RELEASE_SIGNING_KEY_B64", ""), validate=True)
        if not private:
            parser.error("Set RELEASE_SIGNING_KEY_B64 in .env or pass --key")
        with tempfile.NamedTemporaryFile() as keyfile:
            keyfile.write(private)
            keyfile.flush()
            manifest = build(root, args.output, keyfile.name, commit)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
