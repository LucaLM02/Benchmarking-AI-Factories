"""Simple FastAPI-based visualizer for benchmark results.

This server is intentionally lightweight and requires only Python packages
installed inside the project's virtualenv (no system changes). It reads
parsed Prometheus snapshots produced by the repository (files ending with
"_parsed.json") and provides a minimal web UI and plotting endpoints.

Usage:
  export RESULTS_DIR=/path/to/results_<project>_YYYYmmdd_HHMMSS
  python src/Interface/fastapi_server.py

The automation script sets `RESULTS_DIR` before launching this server.
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


app = FastAPI()


def get_results_dir() -> str:
    rd = os.environ.get("RESULTS_DIR")
    if not rd:
        # fallback to results_* in repo parent
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        candidates = [p for p in os.listdir(base) if p.startswith("results_")]
        if candidates:
            rd = os.path.join(base, sorted(candidates)[-1])
        else:
            rd = os.path.join(base, "results")
    return rd


def find_parsed_files(results_dir: str) -> List[str]:
    if not os.path.isdir(results_dir):
        return []
    out = []
    for root, _, files in os.walk(results_dir):
        for f in files:
            if f.endswith("_parsed.json") or f.endswith("prom_snapshot_parsed.json"):
                out.append(os.path.join(root, f))
    return sorted(out)


@app.get("/", response_class=HTMLResponse)
def index():
    rd = get_results_dir()
    files = find_parsed_files(rd)
    html = [f"<h1>Benchmark Results Visualizer</h1>", f"<p>Results dir: {rd}</p>", "<ul>"]
    if not files:
        html.append("<li>No parsed results found (look for *_parsed.json)</li>")
    for f in files:
        rel = os.path.relpath(f, rd)
        html.append(f"<li>{rel} - <a href=\"/view?file={rel}\">view</a> | <a href=\"/metrics?file={rel}\">raw JSON</a></li>")
    html.append("</ul>")
    html.append("<p>To plot a metric: open the view page and choose a metric.</p>")
    return HTMLResponse("\n".join(html))


@app.get("/view", response_class=HTMLResponse)
def view(file: str = Query(..., description="Relative path to parsed JSON file inside RESULTS_DIR")):
    rd = get_results_dir()
    path = os.path.join(rd, file)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {file}")
    with open(path, "r") as f:
        data = json.load(f)

    # Collect metric names
    metrics = set()
    for entry in data:
        for target, payload in entry.get("targets", {}).items():
            mlist = payload.get("metrics") or []
            for m in mlist:
                metrics.add(m.get("name"))

    html = [f"<h1>View: {file}</h1>", f"<p>Entries: {len(data)}</p>", "<ul>"]
    for m in sorted(metrics):
        html.append(f"<li>{m} - <a href=\"/plot?file={file}&metric={m}\">plot</a></li>")
    html.append("</ul>")
    html.append("<p><a href=\"/\">Back</a></p>")
    return HTMLResponse("\n".join(html))


@app.get("/metrics", response_class=JSONResponse)
def metrics(file: str = Query(..., description="Relative path to parsed JSON file inside RESULTS_DIR")):
    rd = get_results_dir()
    path = os.path.join(rd, file)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {file}")
    with open(path, "r") as f:
        data = json.load(f)
    return JSONResponse(content=data)


def extract_time_series(parsed_data, metric_name: str):
    # Return dict: target_with_labels -> {"ts": [], "vals": []}
    # 1. Collect all points
    # structure: [ (ts, val, labels_str), ... ]
    raw_points = []
    
    for entry in parsed_data:
        ts = entry.get("timestamp")
        for target, payload in entry.get("targets", {}).items():
            metrics = payload.get("metrics") or []
            for m in metrics:
                if m.get("name") == metric_name:
                    val = m.get("value")
                    labels = m.get("labels") or {}
                    # Create a unique key for the series based on labels
                    # target is usually just host:port, we want distinctions if labels differ
                    # Format: host {label="val", ...}
                    label_parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
                    if label_parts:
                        series_key = f"{target}{{{','.join(label_parts)}}}"
                    else:
                        series_key = target
                    
                    if isinstance(val, (int, float)):
                        raw_points.append({
                            "key": series_key,
                            "ts": ts,
                            "val": val
                        })

    # 2. Group by series key
    grouped = {}
    for p in raw_points:
        k = p["key"]
        if k not in grouped:
            grouped[k] = {"ts": [], "vals": []}
        # Assuming parsed_data is already sorted by time, but safety first not strictly need if processed sequentially
        grouped[k]["ts"].append(p["ts"])
        grouped[k]["vals"].append(p["val"])
        
    # 3. Apply Rate Calculation Heuristic
    # If metric name implies a counter, we usually want Rate (per second).
    # Common suffixes: _total, _count, _sum, _bytes
    # EXCEPTION: _bytes might be gauge (disk usage), but here minio_node_io_* are counters.
    # minio_cluster_capacity_* is GAUGE.
    is_counter = False
    if metric_name.endswith("_total") or \
       metric_name.endswith("_count") or \
       metric_name.endswith("_sum") or \
       metric_name.endswith("_distribution") or \
       (metric_name.endswith("_bytes") and "capacity" not in metric_name and "memory" not in metric_name):
        is_counter = True

    if is_counter:
        for k, d in grouped.items():
            timestamps = d["ts"]
            values = d["vals"]
            rate_ts = []
            rate_vals = []
            
            # Simple derivative: (v2 - v1) / (t2 - t1)
            # We assign the rate to t2
            for i in range(1, len(timestamps)):
                t1 = timestamps[i-1]
                t2 = timestamps[i]
                v1 = values[i-1]
                v2 = values[i]
                
                dt = t2 - t1
                if dt > 0:
                    rate = (v2 - v1) / dt
                    # Filter negative rates (restarts)
                    if rate >= 0:
                        rate_ts.append(t2)
                        rate_vals.append(rate)
            
            grouped[k]["ts"] = rate_ts
            grouped[k]["vals"] = rate_vals

    return grouped


@app.get("/plot")
def plot(file: str = Query(...), metric: str = Query(...)):
    rd = get_results_dir()
    path = os.path.join(rd, file)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {file}")
    with open(path, "r") as f:
        data = json.load(f)

    series = extract_time_series(data, metric)
    if not series:
        raise HTTPException(status_code=404, detail=f"Metric '{metric}' not found in file")

    fig, ax = plt.subplots(figsize=(8, 4))
    for tgt, d in series.items():
        ax.plot(d["ts"], d["vals"], marker="o", label=tgt)
    ax.set_title(metric)
    ax.set_xlabel("timestamp")
    ax.set_ylabel("value")
    ax.legend(loc="best", fontsize="small")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


# ---------------------------------------------------------------------------
# Grafana SimpleJson Models
# ---------------------------------------------------------------------------
class GrafanaTarget(BaseModel):
    target: str

class GrafanaRange(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime

class GrafanaQueryRequest(BaseModel):
    targets: List[GrafanaTarget]
    range: Optional[GrafanaRange] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_all_metrics(parsed_data) -> List[str]:
    metrics = set()
    for entry in parsed_data:
        for target, payload in entry.get("targets", {}).items():
            mlist = payload.get("metrics") or []
            for m in mlist:
                name = m.get("name")
                if name:
                    metrics.add(name)
    return sorted(metrics)

def load_first_parsed_file():
    rd = get_results_dir()
    files = find_parsed_files(rd)
    if not files:
        return []
    # For now, we load the last one (latest) or first? Code uses lists.
    # Usually we want the *latest* snapshot. logic in find_parsed_files returns sorted list.
    # Assuming the last file is the most relevant or they are split? 
    # The existing code loops over all files in index().
    # Let's pick the last one for "latest" view.
    path = files[-1]
    with open(path, "r") as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Grafana Endpoints
# ---------------------------------------------------------------------------
@app.get("/heartbeat")
def heartbeat():
    return {"status": "ok"}

@app.post("/search")
def grafana_search(body: dict = None):
    print(f"DEBUG: /search called. Body: {body}")
    data = load_first_parsed_file()
    metrics = get_all_metrics(data)
    print(f"DEBUG: Found {len(metrics)} metrics. Sample: {metrics[:5]}")
    return metrics

@app.post("/query")
def grafana_query(body: GrafanaQueryRequest):
    print(f"DEBUG: /query received for targets: {[t.target for t in body.targets]}")
    data = load_first_parsed_file()
    print(f"DEBUG: Loaded {len(data)} entries from file")
    response = []
    
    for target_req in body.targets:
        metric_name = target_req.target
        # Reuse existing extraction logic
        series_dict = extract_time_series(data, metric_name)
        print(f"DEBUG: Series found for '{metric_name}': {list(series_dict.keys())}")
        
        # series_dict is { "127.0.0.1:9000": {"ts": [...], "vals": [...]} }
        # Convert to Grafana format: [{"target": "name", "datapoints": [[val, ts_ms], ...]}]
        for host, d in series_dict.items():
            timestamps = d["ts"]
            values = d["vals"]
            
            datapoints = []
            for i in range(len(timestamps)):
                # Filter by range if needed, strictly speaking not required if we want to show all loaded data
                # but good practice. The loaded data is a snapshot, so it might be sparse.
                ts_sec = timestamps[i]
                val = values[i]
                
                # Grafana expects ms timestamps
                # if body.range:
                #     if body.range.from_.timestamp() <= ts_sec <= body.range.to.timestamp():
                #         datapoints.append([val, int(ts_sec * 1000)])
                # else:
                datapoints.append([val, int(ts_sec * 1000)])
            
            if datapoints:
                print(f"DEBUG: Returning {len(datapoints)} datapoints for {metric_name} ({host})")
                print(f"DEBUG: Sample: {datapoints[0]}")
                response.append({
                    "target": target_req.target, # Return exact requested target name to avoid confusion for single-metric panels
                    "datapoints": datapoints
                })
    
    return response

@app.post("/annotations")
def grafana_annotations():
    return []

if __name__ == "__main__":
    import uvicorn
    rd = get_results_dir()
    print(f"Starting FastAPI visualizer; RESULTS_DIR={rd}")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
