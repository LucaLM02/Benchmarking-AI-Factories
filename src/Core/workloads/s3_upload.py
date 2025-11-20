import time
import os
import random
import uuid
import io
from typing import Dict, Any, Optional
from threading import Event
import requests
from urllib.parse import urljoin


def _log(logger, message, level="INFO"):
    if logger:
        logger.log(message, level)
    else:
        print(f"[s3-upload] {level}: {message}")


def _generate_payload(min_kb: int, max_kb: int) -> bytes:
    size = random.randint(min_kb, max_kb) * 1024
    return os.urandom(size)


def run(config: Dict[str, Any], logger=None, stop_event: Optional[Event] = None):

    endpoint = config.get("endpoint", "http://127.0.0.1:9000")
    bucket = config.get("bucket", "ai-factory")
    min_kb = config.get("min_kb", 64)
    max_kb = config.get("max_kb", 2048)
    objects = int(config.get("objects", 200))
    preprocess_ms = int(config.get("preprocess_ms", 5))
    timeout = float(config.get("timeout", 3))
    label = config.get("label", "s3-realistic")

    session = requests.Session()
    base = endpoint.rstrip("/") + "/"
    bucket_url = urljoin(base, f"{bucket}/")

    _log(logger, f"[{label}] START — endpoint={endpoint}, bucket={bucket}")

    # ------------------------------------------------------------
    # 1. Create bucket (idempotent)
    # ------------------------------------------------------------
    try:
        r = session.put(bucket_url, timeout=timeout)
        _log(logger, f"[{label}] Bucket PUT -> {r.status_code}")
    except Exception as exc:
        _log(logger, f"[{label}] Bucket creation failed: {exc}", "ERROR")

    uploaded_objects = []
    upload_latencies = []
    download_latencies = []
    total_uploaded_bytes = 0

    # ------------------------------------------------------------
    # 2. Loop simulating ingestion + inference workflow
    # ------------------------------------------------------------
    for i in range(objects):

        if stop_event and stop_event.is_set():
            _log(logger, f"[{label}] Stop at iteration {i}", "WARN")
            break

        # Simulated preprocessing (AI pipeline)
        time.sleep(preprocess_ms / 1000)

        # --------------------------------------------------------
        # PUT (upload)
        # --------------------------------------------------------
        payload = _generate_payload(min_kb, max_kb)
        obj_name = f"sample_{uuid.uuid4().hex}.bin"
        obj_url = urljoin(bucket_url, obj_name)

        t0 = time.time()
        try:
            r = session.put(obj_url, data=payload, timeout=timeout)
            lat = time.time() - t0

            if r.status_code in (200, 204):
                upload_latencies.append(lat)
                uploaded_objects.append(obj_name)
                total_uploaded_bytes += len(payload)
            else:
                _log(logger, f"[{label}] PUT error {r.status_code}", "WARN")

        except Exception as exc:
            _log(logger, f"[{label}] Exception uploading: {exc}", "ERROR")

        # --------------------------------------------------------
        # LIST occasionally
        # --------------------------------------------------------
        if i % 25 == 0:
            try:
                r = session.get(bucket_url, timeout=timeout)
                _log(logger, f"[{label}] LIST returned {len(r.text)} bytes", "DEBUG")
            except Exception:
                pass

        # --------------------------------------------------------
        # GET (download)
        # --------------------------------------------------------
        if uploaded_objects and random.random() < 0.30:
            sample = random.choice(uploaded_objects)
            dl_url = urljoin(bucket_url, sample)
            t1 = time.time()
            try:
                r = session.get(dl_url, timeout=timeout)
                dl_lat = time.time() - t1
                if r.status_code == 200:
                    download_latencies.append(dl_lat)
                else:
                    _log(logger, f"[{label}] GET error {r.status_code}", "WARN")
            except Exception as exc:
                _log(logger, f"[{label}] GET exception: {exc}", "ERROR")

    # ------------------------------------------------------------
    # Final statistics
    # ------------------------------------------------------------
    avg_put = sum(upload_latencies) / len(upload_latencies) if upload_latencies else 0
    avg_get = sum(download_latencies) / len(download_latencies) if download_latencies else 0
    throughput = total_uploaded_bytes / max(sum(upload_latencies), 1e-5)

    _log(logger, f"[{label}] Uploaded: {len(uploaded_objects)}/{objects}")
    _log(logger, f"[{label}] Avg PUT latency: {avg_put*1000:.2f} ms")
    _log(logger, f"[{label}] Avg GET latency: {avg_get*1000:.2f} ms")
    _log(logger, f"[{label}] Client-side throughput: {throughput/1e6:.2f} MB/s")
