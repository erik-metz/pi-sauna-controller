"""Prepare first-install bundle. Only allowlisted runtime settings go to the Pi."""
import argparse
import os
import sys
import tarfile
import tempfile
from pathlib import Path


def collect_files(root):
    paths = []
    for folder, suffixes in (("src", {".py"}), ("tests", {".py"}),
                             ("tools", {".py"}), ("config", {".toml"}),
                             ("deploy", {".py", ".sh", ".toml", ".service", ".timer"})):
        paths.extend(p for p in (root / folder).rglob("*")
                     if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts)
    paths.append(root / "deploy/release-public.pem")  # Never glob arbitrary PEM/key files.
    return sorted(paths)


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from sauna_controller.environment import write_runtime_env, read_env_file
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/sauna-bootstrap.tar.gz")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = collect_files(root)
    with tempfile.TemporaryDirectory() as folder:
        runtime = Path(folder) / ".env"
        write_runtime_env(root / ".env", runtime)
        with tarfile.open(output, "w:gz") as archive:
            for path in sorted(paths):
                archive.add(path, arcname=path.relative_to(root))
            archive.add(runtime, arcname=".env")
    os.chmod(output, 0o600)
    # Check the actual private value, without ever printing it.
    secret = read_env_file(root / ".env").get("RELEASE_SIGNING_KEY_B64", "")
    with tarfile.open(output) as archive:
        for member in archive.getmembers():
            if member.isfile() and secret and secret.encode() in archive.extractfile(member).read():
                raise RuntimeError("Private signing key found in bootstrap bundle")
    print(f"Bootstrap bundle ready: {output}; private release key excluded")


if __name__ == "__main__":
    main()
