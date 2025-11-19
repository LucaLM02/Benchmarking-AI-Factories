import time
from typing import Dict, Any, Optional

from threading import Event
import requests
from urllib.parse import urljoin


def _log(logger, message: str, level: str = "INFO"):
    if logger:
        logger.log(message, level)
    else:
        print(f"[s3-upload] {level}: {message}")


def run(config: Dict[str, Any], logger=None, stop_event: Optional[Event] = None):
    endpoint = config.get("endpoint", "http://127.0.0.1:9000")
    health_path = config.get("health_path", "/minio/health/ready")
    objects = int(config.get("objects", 100))
    delay = float(config.get("delay", 0.05))
    timeout = float(config.get("timeout", 2))
    label = config.get("label", "s3-upload")

    _log(logger, f"[{label}] Starting workload against {endpoint}")
    session = requests.Session()
    success = 0

    for idx in range(objects):
        if stop_event and stop_event.is_set():
            _log(logger, f"[{label}] Stop requested; exiting loop at iteration {idx}", "WARN")
            break

        try:
            resp = session.get(urljoin(endpoint.rstrip("/") + "/", health_path.lstrip("/")), timeout=timeout)
            if resp.status_code == 200:
                success += 1
            else:
                _log(logger, f"[{label}] request {idx} returned {resp.status_code}", "WARN")
        except Exception as exc:
            _log(logger, f"[{label}] request {idx} failed: {exc}", "ERROR")

        time.sleep(delay)

    _log(logger, f"[{label}] Successful checks: {success}/{objects}")
