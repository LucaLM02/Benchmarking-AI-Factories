import os
import json
import threading
from datetime import datetime

class FileLogger:
    def __init__(self, log_dir, file_name, fmt="json"):
        self.log_dir = log_dir
        self.file_name = file_name
        self.format = fmt

        # Full path of the log file
        self.log_path = os.path.join(log_dir, file_name)

        # Ensure directory exists
        os.makedirs(log_dir, exist_ok=True)

        # Critical fix: initialize lock
        self._lock = threading.Lock()

        print(f"[INFO] FileLogger initialized at {self.log_path}")

    def log(self, message, level="INFO"):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message
        }

        with self._lock:
            with open(self.log_path, "a") as f:
                if self.format == "json":
                    f.write(json.dumps(entry) + "\n")
                else:
                    f.write(f"[{entry['timestamp']}] [{level}] {message}\n")

    def export(self):
        """Return the path of the log file."""
        return self.log_path
