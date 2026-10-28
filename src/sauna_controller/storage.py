import json
import os
from pathlib import Path


class FaultStore:
    def __init__(self, directory):
        self.path = Path(directory) / "fault.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self):
        if not self.path.exists():
            return ""
        try:
            result = json.loads(self.path.read_text())
            if set(result) != {"fault"} or not isinstance(result["fault"], str):
                raise ValueError()
            return result["fault"]
        except (ValueError, OSError):
            return "fault_store_unreadable"

    def write(self, fault):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump({"fault": fault}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
