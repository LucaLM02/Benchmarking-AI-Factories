# src/Core/monitors/prometheus_monitor.py
import requests
import time
import json
import os
from Core.abstracts import Monitor


class PrometheusMonitor(Monitor):
    """Prometheus-based monitor for collecting runtime metrics from services."""

    def __init__(self, scrape_targets, scrape_interval=5, collect_interval=10, save_path="metrics_snapshot.json"):
        self.scrape_targets = scrape_targets
        self.scrape_interval = scrape_interval
        self.collect_interval = collect_interval
        self.save_path = os.path.join(["global"],["workspace"], save_as)
        self._active = False
        self.metrics_data = []

    def start(self) -> None:
        """Start the monitoring process (non-blocking)."""
        self._active = True
        print(f"[PrometheusMonitor] Started monitoring {len(self.scrape_targets)} targets.")

    def collect(self):
        """Collect metrics snapshot from all targets."""
        if not self._active:
            print("[PrometheusMonitor] Not active, skipping collection.")
            return {}

        collected = {}
        for target in self.scrape_targets:
            try:
                response = requests.get(f"http://{target}/metrics", timeout=3)
                collected[target] = response.text
            except requests.RequestException as e:
                collected[target] = f"Error: {e}"
        self.metrics_data.append(collected)
        print(f"[PrometheusMonitor] Collected metrics from {len(collected)} targets.")
        return collected

    def stop(self) -> None:
        """Stop monitoring and save collected data."""
        self._active = False
        with open(self.save_path, "w") as f:
            json.dump(self.metrics_data, f, indent=2)
        print(f"[PrometheusMonitor] Metrics saved to {self.save_path}")
