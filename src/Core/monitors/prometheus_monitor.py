import json
import os
import re
import time

import requests

from Core.abstracts import Monitor
from Core.analytics.grafana_exporter import GrafanaExporter

LABEL_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')


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
                 metrics_path="/metrics",
                 readable_save_path=None,
                 grafana_export_path=None):
        self.scrape_targets = scrape_targets              # list of host:port
        self.scrape_interval = scrape_interval           # how often to scrape
        self.collect_interval = collect_interval         # how often to save buffer
        self.save_path = save_path
        self.metrics_path = metrics_path or "/metrics"
        self.readable_save_path = readable_save_path or self._derive_readable_path(save_path)
        self.grafana_export_path = grafana_export_path or self._derive_grafana_path(save_path)
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
            readable_snapshot = self._build_readable_snapshot()
            if self.readable_save_path:
                with open(self.readable_save_path, "w") as f:
                    json.dump(readable_snapshot, f, indent=2)
            if self.grafana_export_path:
                exporter = GrafanaExporter()
                exporter.export(readable_snapshot, self.grafana_export_path)
        except Exception as e:
            print(f"[PrometheusMonitor] Save error: {e}")

    def stop(self):
        self._active = False
        self._save()
        msg = f"[PrometheusMonitor] Saved metrics to {self.save_path}"
        if self.readable_save_path:
            msg += f" | parsed: {self.readable_save_path}"
        if self.grafana_export_path:
            msg += f" | grafana: {self.grafana_export_path}"
        print(msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _derive_readable_path(save_path):
        base, ext = os.path.splitext(save_path)
        if not ext:
            ext = ".json"
        return f"{base}_parsed{ext}"

    @staticmethod
    def _derive_grafana_path(save_path):
        base, ext = os.path.splitext(save_path)
        if not ext:
            ext = ".json"
        return f"{base}_grafana{ext}"

    def _build_readable_snapshot(self):
        readable_entries = []
        for entry in self._buffer:
            parsed_targets = {}
            for target, payload in entry.get("data", {}).items():
                if isinstance(payload, str):
                    if payload.startswith("ERROR"):
                        parsed_targets[target] = {"error": payload}
                    else:
                        parsed_targets[target] = {
                            "metrics": self._parse_prometheus_text(payload)
                        }
                else:
                    parsed_targets[target] = {"raw": payload}
            readable_entries.append({
                "timestamp": entry.get("timestamp"),
                "targets": parsed_targets
            })
        return readable_entries

    def _parse_prometheus_text(self, text):
        metrics = []
        if not isinstance(text, str):
            return metrics

        metadata = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                tokens = stripped.split(None, 3)
                if len(tokens) >= 3:
                    directive = tokens[1].upper()
                    metric_name = tokens[2]
                    meta = metadata.setdefault(metric_name, {})
                    if directive == "HELP" and len(tokens) >= 4:
                        meta["help"] = tokens[3]
                    elif directive == "TYPE" and len(tokens) >= 4:
                        meta["type"] = tokens[3]
                continue

            sample = self._parse_metric_sample(stripped)
            if not sample:
                continue
            meta = metadata.get(sample["name"], {})
            if meta:
                sample.update({k: v for k, v in meta.items() if k in ("help", "type")})
            metrics.append(sample)
        return metrics

    def _parse_metric_sample(self, line):
        try:
            metric_and_labels, rest = line.split(None, 1)
        except ValueError:
            return None

        name = metric_and_labels
        labels = {}
        if "{" in metric_and_labels:
            brace_idx = metric_and_labels.index("{")
            name = metric_and_labels[:brace_idx]
            closing_idx = metric_and_labels.rfind("}")
            labels_str = metric_and_labels[brace_idx + 1:closing_idx] if closing_idx > brace_idx else ""
            labels = self._parse_labels(labels_str)

        parts = rest.split()
        if not parts:
            return None
        value_token = parts[0]
        sample_ts = None
        if len(parts) > 1:
            try:
                sample_ts = float(parts[1])
            except ValueError:
                sample_ts = None

        value = self._convert_value(value_token)
        sample = {
            "name": name,
            "labels": labels,
            "value": value
        }
        if sample_ts is not None:
            sample["sample_timestamp"] = sample_ts
        return sample

    def _parse_labels(self, chunk):
        labels = {}
        if not chunk:
            return labels
        for match in LABEL_PATTERN.finditer(chunk):
            key = match.group(1)
            value = match.group(2)
            labels[key] = bytes(value, "utf-8").decode("unicode_escape")
        return labels

    @staticmethod
    def _convert_value(token):
        if token in ("NaN", "+Inf", "-Inf"):
            return token
        try:
            return float(token)
        except ValueError:
            return token
