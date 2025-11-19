import requests
import time
import json
from Core.abstracts import Monitor


class PrometheusMonitor(Monitor):
    """
    Pull-based Prometheus monitor.
    Scrapes /metrics endpoints from the configured targets.
    """

    def __init__(self,
                 scrape_targets,
                 scrape_interval=5,
                 collect_interval=10,
                 save_path="metrics_snapshot.json",
                 metrics_path="/metrics"):
        self.scrape_targets = scrape_targets              # list of host:port
        self.scrape_interval = scrape_interval           # how often to scrape
        self.collect_interval = collect_interval         # how often to save buffer
        self.save_path = save_path
        self.metrics_path = metrics_path or "/metrics"
        self._active = False
        self._buffer = []
        self._last_saved = time.time()

    def start(self):
        self._active = True
        print(f"[PrometheusMonitor] Started pull-mode monitoring: {self.scrape_targets}")

    def collect(self):
        if not self._active:
            return {}

        snapshot = {}
        for target in self.scrape_targets:
            if target.startswith("http://") or target.startswith("https://"):
                url = target
            else:
                path = self.metrics_path
                if path and not path.startswith("/"):
                    path = f"/{path}"
                url = f"http://{target}{path}"
            try:
                r = requests.get(url, timeout=3)
                snapshot[target] = r.text
            except Exception as e:
                snapshot[target] = f"ERROR: {e}"

        entry = {"timestamp": time.time(), "data": snapshot}
        self._buffer.append(entry)

        # periodic save
        if time.time() - self._last_saved >= self.collect_interval:
            self._save()
            self._last_saved = time.time()

        print(f"[PrometheusMonitor] Polled {len(self.scrape_targets)} targets.")
        return snapshot

    def _save(self):
        try:
            with open(self.save_path, "w") as f:
                json.dump(self._buffer, f, indent=2)
        except Exception as e:
            print(f"[PrometheusMonitor] Save error: {e}")

    def stop(self):
        self._active = False
        self._save()
        print(f"[PrometheusMonitor] Saved metrics to {self.save_path}")
