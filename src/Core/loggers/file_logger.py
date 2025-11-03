# src/Core/loggers/file_logger.py
import json
import os
import threading
from datetime import datetime
from Core.abstracts import Logger


class FileLogger(Logger):
    """File-based logger implementation for benchmark services."""
    def __init__(self, log_dir, file_name="benchmark.log", fmt="json"):
        self.log_dir = os.path.expandvars(os.path.expanduser(log_dir))
        os.makedirs(self.log_dir, exist_ok=True)
        self.file_name = file_name
        self.format = fmt
        self.log_path = os.path.join(self.log_dir, self.file_name)
        print(f"[INFO] FileLogger initialized at {self.log_path}")

    def log(self, message: str, level: str = "INFO") -> None:
        """Write a log entry to file."""
        timestamp = datetime.utcnow().isoformat()
        log_entry = {"timestamp": timestamp, "level": level, "message": message}

        with self._lock:
            with open(self.log_path, "a") as f:
                if self.format == "json":
                    f.write(json.dumps(log_entry) + "\n")
                else:
                    f.write(f"[{timestamp}] [{level}] {message}\n")

    def export(self) -> str:
        """Return path of the log file for external processing."""
        self.log("Exporting log file", "DEBUG")
        return self.log_path
