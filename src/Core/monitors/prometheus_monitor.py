import requests
import time
import json
from Core.abstracts import Monitor


class PrometheusMonitor(Monitor):
    """
    Prometheus Pushgateway-based monitor.
    Metrics are pushed by services/clients to a local Pushgateway
    running inside the same Slurm node.
    """

    def __init__(self,
                 gateway_url="http://localhost:9091/metrics",
                 collect_interval=10,
                 save_path="metrics_snapshot.json"):
        self.gateway_url = gateway_url
        self.collect_interval = collect_interval
        self.save_path = save_path
        self._active = False
        self.metrics_data = []

    def start(self):
        self._active = True
        print(f"[PrometheusMonitor] Listening to Pushgateway at {self.gateway_url}")

    def collect(self):
        """Collect one metrics snapshot from Pushgateway."""
        if not self._active:
            return {}

        try:
            text = requests.get(self.gateway_url, timeout=3).text
            snapshot = {
                "timestamp": time.time(),
                "raw": text
            }
            self.metrics_data.append(snapshot)
            print("[PrometheusMonitor] Metrics snapshot collected")
            return snapshot
        except Exception as e:
            print(f"[PrometheusMonitor] Error while collecting: {e}")
            return {"error": str(e)}

    def stop(self):
        """Stop monitoring and save all collected snapshots to file."""
        self._active = False
        try:
            with open(self.save_path, "w") as f:
                json.dump(self.metrics_data, f, indent=2)
            print(f"[PrometheusMonitor] Saved metrics to {self.save_path}")
        except Exception as e:
            print(f"[PrometheusMonitor] Failed to write metrics: {e}")
