import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


class GrafanaExporter:
    """
    Builds lightweight Grafana-friendly time-series files from Prometheus snapshots.
    Designed for offline usage where Grafana must run outside the HPC cluster.
    """

    DEFAULT_PANELS = [
        {
            "name": "ingest_throughput_ops",
            "title": "Ingest Throughput (ops/s)",
            "unit": "ops/s",
            "metric_names": [
                "minio_s3_requests_total",
                "minio_http_requests_total",
                "http_requests_total",
                "s3_upload_objects_total"
            ],
            "transform": "rate"
        },
        {
            "name": "ingest_errors_total",
            "title": "Ingest Errors",
            "unit": "errors/s",
            "metric_names": [
                "minio_s3_errors_total",
                "minio_http_requests_error_total",
                "s3_upload_errors_total"
            ],
            "transform": "rate"
        },
        {
            "name": "ingest_latency_seconds",
            "title": "Request Latency",
            "unit": "seconds",
            "metric_names": [
                "minio_http_request_duration_seconds",
                "http_request_duration_seconds",
                "minio_s3_request_duration_seconds"
            ],
            "transform": None
        },
        {
            "name": "cpu_consumption",
            "title": "CPU Consumption",
            "unit": "seconds",
            "metric_names": [
                "process_cpu_seconds_total",
                "minio_node_cpu_total_seconds"
            ],
            "transform": "rate"
        },
        {
            "name": "memory_usage",
            "title": "Memory Usage",
            "unit": "bytes",
            "metric_names": [
                "process_resident_memory_bytes",
                "go_memstats_heap_inuse_bytes",
                "minio_node_memory_usage_bytes"
            ],
            "transform": None
        },
        {
            "name": "network_io",
            "title": "Network IO",
            "unit": "bytes/s",
            "metric_names": [
                "minio_network_sent_bytes_total",
                "minio_network_received_bytes_total",
                "node_network_transmit_bytes_total",
                "node_network_receive_bytes_total"
            ],
            "transform": "rate"
        },
    ]

    def __init__(self, panels=None):
        self.panels = panels or self.DEFAULT_PANELS

    def export(self, readable_snapshot: List[Dict], destination: str):
        """
        readable_snapshot: output from PrometheusMonitor._build_readable_snapshot
        destination: path to JSON file consumed by Grafana dashboards/offline visualizations.
        """
        panels_output = []
        for panel_cfg in self.panels:
            series = self._build_panel_series(readable_snapshot, panel_cfg)
            if not series:
                continue
            panels_output.append({
                "name": panel_cfg["name"],
                "title": panel_cfg["title"],
                "unit": panel_cfg["unit"],
                "series": series
            })

        payload = {
            "generated_at": time.time(),
            "panels": panels_output,
            "note": (
                "This file aggregates Prometheus snapshots for offline Grafana imports. "
                "Panels list only contains metrics detected in the captured data."
            )
        }
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "w") as fh:
            json.dump(payload, fh, indent=2)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_panel_series(self, readable_snapshot: List[Dict], panel_cfg: Dict):
        samples = defaultdict(list)
        metric_names = set(panel_cfg.get("metric_names", []))
        for entry in readable_snapshot:
            timestamp = entry.get("timestamp")
            for target, payload in entry.get("targets", {}).items():
                metrics = payload.get("metrics", [])
                for metric in metrics:
                    name = metric.get("name")
                    if name not in metric_names:
                        continue
                    value = self._to_float(metric.get("value"))
                    if value is None:
                        continue
                    samples[(target, name)].append((timestamp, value))

        series_list = []
        for (target, metric_name), values in samples.items():
            values.sort(key=lambda item: item[0])
            transformed = self._apply_transform(values, panel_cfg.get("transform"))
            if not transformed:
                continue
            series_list.append({
                "target": target,
                "metric": metric_name,
                "points": transformed
            })
        return series_list

    def _apply_transform(self, values: List[Tuple[float, float]], transform: str):
        if not transform:
            return values
        if transform == "rate":
            return self._to_rate(values)
        return values

    @staticmethod
    def _to_rate(values: List[Tuple[float, float]]):
        if len(values) < 2:
            return []
        rates = []
        for idx in range(1, len(values)):
            ts_prev, val_prev = values[idx - 1]
            ts_curr, val_curr = values[idx]
            delta_v = val_curr - val_prev
            delta_t = ts_curr - ts_prev
            if delta_t <= 0 or delta_v < 0:
                continue
            rate = delta_v / delta_t
            rates.append((ts_curr, rate))
        return rates

    @staticmethod
    def _to_float(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            if math.isnan(value):
                return None
            return float(value)
        try:
            parsed = float(value)
            if math.isnan(parsed):
                return None
            return parsed
        except (TypeError, ValueError):
            return None
