import time
from typing import Dict, Any, Optional
from threading import Event
import boto3
import os


def _log(logger, message: str, level: str = "INFO"):
    if logger:
        logger.log(message, level)
    else:
        print(f"[s3-upload] {level}: {message}")


def run(config: Dict[str, Any], logger=None, stop_event: Optional[Event] = None):
    endpoint = config.get("target", "127.0.0.1:9000")
    bucket = config.get("bucket", "bench")
    nobj = int(config.get("objects", 100))
    size_kb = int(config.get("size_kb", 64))
    label = config.get("label", "s3-upload")

    _log(logger, f"[{label}] Connecting to MinIO at http://{endpoint}")

    # ============================
    # 1. Client S3
    # ============================
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{endpoint}",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
        config=boto3.session.Config(signature_version="s3v4")
    )

    # ============================
    # 2. Create bucket
    # ============================
    try:
        s3.create_bucket(Bucket=bucket)
        _log(logger, f"[{label}] Created bucket '{bucket}'")
    except Exception:
        _log(logger, f"[{label}] Bucket '{bucket}' already exists")

    # ============================
    # 3. Payload
    # ============================
    payload = os.urandom(size_kb * 1024)

    # ============================
    # 4. Upload loop
    # ============================
    success = 0

    for i in range(nobj):
        if stop_event and stop_event.is_set():
            _log(logger, f"[{label}] Stop requested at object {i}", "WARN")
            break

        key = f"object_{i}.bin"

        try:
            s3.put_object(Bucket=bucket, Key=key, Body=payload)
            success += 1
            _log(logger, f"[{label}] Uploaded {key}", "DEBUG")

        except Exception as exc:
            _log(logger, f"[{label}] Upload failed for {key}: {exc}", "ERROR")

    _log(logger, f"[{label}] Finished: {success}/{nobj} objects uploaded")
