import time
import os
import random
import uuid
import io
from typing import Dict, Any, Optional
from threading import Event
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


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
    bucket_name = config.get("bucket", "ai-factory")
    min_kb = config.get("min_kb", 64)
    max_kb = config.get("max_kb", 2048)
    objects = int(config.get("objects", 200))
    duration_sec = int(config.get("duration_sec", 0))  # 0 means limit by objects
    preprocess_ms = int(config.get("preprocess_ms", 5))
    # timeout = float(config.get("timeout", 30)) # Boto3 uses botocore config for timeouts
    label = config.get("label", "s3-realistic")
    
    # Credentials (default to minioadmin/minioadmin for local testing)
    access_key = config.get("access_key", "minioadmin")
    secret_key = config.get("secret_key", "minioadmin")

    start_time = time.time()
    
    _log(logger, f"[{label}] START — endpoint={endpoint}, bucket={bucket_name}, duration={duration_sec}s")

    # ------------------------------------------------------------
    # 0. Init Boto3 Client
    # ------------------------------------------------------------
    s3_config = Config(
        signature_version='s3v4',
        retries = {'max_attempts': 3, 'mode': 'standard'},
        connect_timeout=10, 
        read_timeout=30
    )
    
    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=s3_config,
        verify=False # Local minio often creates self-signed cert issues if https
    )

    # ------------------------------------------------------------
    # 1. Create bucket (idempotent-ish)
    # ------------------------------------------------------------
    try:
        s3.head_bucket(Bucket=bucket_name)
    except ClientError:
        try:
            s3.create_bucket(Bucket=bucket_name)
            _log(logger, f"[{label}] Bucket '{bucket_name}' created")
        except Exception as exc:
            _log(logger, f"[{label}] Failed to verify/create bucket: {exc}", "ERROR")

    uploaded_objects = []
    upload_latencies = []
    download_latencies = []
    total_uploaded_bytes = 0

    # ------------------------------------------------------------
    # 2. Loop simulating ingestion + inference workflow
    # ------------------------------------------------------------
    i = 0
    
    while True:
        # Check termination conditions
        elapsed = time.time() - start_time
        if duration_sec > 0:
            if elapsed >= duration_sec:
                _log(logger, f"[{label}] Duration {duration_sec}s reached.", "INFO")
                break
        elif i >= objects:
             _log(logger, f"[{label}] Object limit {objects} reached.", "INFO")
             break

        if stop_event and stop_event.is_set():
            _log(logger, f"[{label}] Stop signal received at iteration {i}", "WARN")
            break
            
        i += 1

        # Simulated preprocessing (AI pipeline)
        time.sleep(preprocess_ms / 1000)

        # --------------------------------------------------------
        # PUT (upload)
        # --------------------------------------------------------
        payload = _generate_payload(min_kb, max_kb)
        obj_name = f"sample_{uuid.uuid4().hex}.bin"
        
        t0 = time.time()
        try:
            s3.put_object(Bucket=bucket_name, Key=obj_name, Body=payload)
            lat = time.time() - t0
            
            upload_latencies.append(lat)
            uploaded_objects.append(obj_name)
            total_uploaded_bytes += len(payload)
            
        except Exception as exc:
            _log(logger, f"[{label}] PUT failed: {exc}", "ERROR")

        # --------------------------------------------------------
        # LIST occasionally (every 25 ops)
        # --------------------------------------------------------
        if i % 25 == 0:
            try:
                # MaxKeys=5 just to Ping the list endpoint
                s3.list_objects_v2(Bucket=bucket_name, MaxKeys=5)
                # _log(logger, f"[{label}] LIST check OK", "DEBUG")
            except Exception:
                pass

        # --------------------------------------------------------
        # GET (download) - random sample
        # --------------------------------------------------------
        if uploaded_objects and random.random() < 0.30:
            sample = random.choice(uploaded_objects)
            t1 = time.time()
            try:
                s3.get_object(Bucket=bucket_name, Key=sample)
                # For benchmark we usually read the body to ensure IO happens
                # resp['Body'].read() 
                # But to save Client CPU just ensuring the request works might be enough depending on goal.
                # Let's read it to be realistic.
                # resp['Body'].read()
                dl_lat = time.time() - t1
                download_latencies.append(dl_lat)
            except Exception as exc:
                _log(logger, f"[{label}] GET failed: {exc}", "WARN")

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
