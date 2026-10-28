import argparse
import json
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description="Control local sauna daemon; arm/reset simulation-only")
    parser.add_argument("--socket", default="/run/sauna/control.sock")
    parser.add_argument("action", choices=["status", "stop", "target", "profile", "light", "mode", "arm", "reset"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(3)
            sock.connect(args.socket)
            sock.sendall(json.dumps({"action": args.action, "value": args.value}).encode())
            result = json.loads(sock.recv(16384))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot contact sauna daemon: {exc}\n")


if __name__ == "__main__":
    main()
