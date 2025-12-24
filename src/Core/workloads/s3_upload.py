import time
import os
import random
import uuid
import io
import statistics
import concurrent.futures
from typing import Dict, Any, Optional, List
from threading import Event, Lock
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def _log(logger, message, level="INFO"):
    if logger:
        logger.log(message, level)
    else:
        print(f"[s3-upload] {level}: {message}")


# ----------------------------------------------------------------------------
# STATIC DATA GENERATION (Global Buffer)
# ----------------------------------------------------------------------------
STATIC_BUFFER_SIZE = 50 * 1024 * 1024  # 50 MB
STATIC_BUFFER = b""

def init_buffer(logger=None):
    global STATIC_BUFFER
    try:
        if len(STATIC_BUFFER) < STATIC_BUFFER_SIZE:
             STATIC_BUFFER = os.urandom(STATIC_BUFFER_SIZE)
    except Exception as e:
        if logger: _log(logger, f"Failed to allocate static buffer: {e}", "ERROR")

def get_random_slice(min_k, max_k):
    if not STATIC_BUFFER:
        return os.urandom(min_k * 1024)
    size = random.randint(min_k, max_k) * 1024
    if size > STATIC_BUFFER_SIZE:
            size = STATIC_BUFFER_SIZE
    return STATIC_BUFFER[:size]


# ----------------------------------------------------------------------------
# WORKER FUNCTION
# ----------------------------------------------------------------------------
def worker_task(
    thread_id: int,
    config: Dict[str, Any],
    stop_event: Optional[Event],
    shared_stats: Dict[str, Any],
    stats_lock: Lock
):
    """
    Worker function to be executed by each thread.
    Performs the upload/download loop.
    """
    
    # Unpack config
    endpoint = config.get("endpoint", "http://127.0.0.1:9000")
    bucket_name = config.get("bucket", "ai-factory")
    min_kb = config.get("min_kb", 1024)      # Increased default: 1MB
    max_kb = config.get("max_kb", 10240)     # Increased default: 10MB
    objects_per_thread = int(config.get("objects", 200)) # Target per thread
    duration_sec = int(config.get("duration_sec", 0))    # 0 means limit by objects
    preprocess_ms = int(config.get("preprocess_ms", 5))
    access_key = config.get("access_key", "minioadmin")
    secret_key = config.get("secret_key", "minioadmin")
    
    # Thread-local Boto3 Client
    # We optimize config for high concurrency
    s3_config = Config(
        signature_version='s3v4',
        retries = {'max_attempts': 3, 'mode': 'standard'},
        connect_timeout=10, 
        read_timeout=30,
        max_pool_connections=10  # Ensure pool is large enough per thread if sharing, but here we have 1 client per thread usually
    )
    
    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=s3_config,
        verify=False
    )
    
    # Local stats tracking
    local_uploaded_objects = []
    local_upload_latencies = []
    local_download_latencies = []
    local_uploaded_bytes = 0
    
    start_time = time.time()
    i = 0
    
    while True:
        # 1. Check stop conditions
        elapsed = time.time() - start_time
        if duration_sec > 0:
            if elapsed >= duration_sec:
                break
        elif i >= objects_per_thread:
             break

        if stop_event and stop_event.is_set():
            break
            
        i += 1

        # 2. Simulated preprocessing
        time.sleep(preprocess_ms / 1000.0)

        # 3. PUT Operation
        payload = get_random_slice(min_kb, max_kb)
        obj_name = f"worker_{thread_id}_Sample_{uuid.uuid4().hex}.bin"
        
        t0 = time.time()
        try:
            s3.put_object(Bucket=bucket_name, Key=obj_name, Body=payload)
            lat = time.time() - t0
            
            local_upload_latencies.append(lat)
            local_uploaded_objects.append(obj_name)
            local_uploaded_bytes += len(payload)
            
        except Exception:
            # For high-throughput tests, we might ignore individual failures or log them sparsely
            pass

        # 4. GET Operation (Probabilistic)
        if local_uploaded_objects and random.random() < 0.30:
            sample = random.choice(local_uploaded_objects)
            t1 = time.time()
            try:
                resp = s3.get_object(Bucket=bucket_name, Key=sample)
                
                # Optimized consumption: Read in chunks
                # Avoiding 'for line in body' which is slow for binary
                stream = resp['Body']
                chunk_size = 64 * 1024 # 64KB chunks
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                
                dl_lat = time.time() - t1
                local_download_latencies.append(dl_lat)
            except Exception:
                pass
                
    # Merge local stats into shared global stats
    with stats_lock:
        shared_stats['upload_latencies'].extend(local_upload_latencies)
        shared_stats['download_latencies'].extend(local_download_latencies)
        shared_stats['total_bytes'] += local_uploaded_bytes
        shared_stats['total_objects'] += len(local_uploaded_objects)


# ----------------------------------------------------------------------------
# MAIN ORCHESTRATOR
# ----------------------------------------------------------------------------
def run(config: Dict[str, Any], logger=None, stop_event: Optional[Event] = None):

    endpoint = config.get("endpoint", "http://127.0.0.1:9000")
    bucket_name = config.get("bucket", "ai-factory")
    concurrency = int(config.get("concurrency", 4)) # Scaled up default
    duration_sec = int(config.get("duration_sec", 0))
    label = config.get("label", "s3-realistic")
    
    access_key = config.get("access_key", "minioadmin")
    secret_key = config.get("secret_key", "minioadmin")

    _log(logger, f"[{label}] START — endpoint={endpoint}, bucket={bucket_name}, threads={concurrency}")

    # 0. Init Static Buffer
    init_buffer(logger)

    # 1. Create bucket (One-time setup)
    # Using a temporary single-threaded client for setup
    try:
        s3_setup = boto3.client(
            's3', endpoint_url=endpoint,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version='s3v4'), verify=False
        )
        s3_setup.head_bucket(Bucket=bucket_name)
    except ClientError:
        try:
            s3_setup.create_bucket(Bucket=bucket_name)
            _log(logger, f"[{label}] Bucket '{bucket_name}' created")
        except ClientError as exc:
            error_code = exc.response.get('Error', {}).get('Code')
            if error_code in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
                 pass
            else:
                 _log(logger, f"[{label}] Failed to verify/create bucket: {exc}", "ERROR")
    except Exception:
        pass # Ignore setup errors, workers will fail if critical

    # 2. Shared Stats Container
    shared_stats = {
        'upload_latencies': [],
        'download_latencies': [],
        'total_bytes': 0,
        'total_objects': 0
    }
    stats_lock = Lock()

    # 3. Launch Workers
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for i in range(concurrency):
            futures.append(
                executor.submit(worker_task, i, config, stop_event, shared_stats, stats_lock)
            )
        
        # Wait for all workers
        concurrent.futures.wait(futures)

    total_duration = time.time() - start_time
    
    # 4. Final Aggregated Reporting
    # ----------------------------------------------------------------
    upload_latencies = shared_stats['upload_latencies']
    download_latencies = shared_stats['download_latencies']
    total_bytes = shared_stats['total_bytes']
    total_objects = shared_stats['total_objects']

    def calc_stats_metrics(latencies):
        if not latencies:
            return 0.0, 0.0, 0.0
        avg = statistics.mean(latencies)
        try:
            stdev = statistics.stdev(latencies)
        except statistics.StatisticsError:
            stdev = 0.0
        
        latencies.sort()
        idx_p95 = int(len(latencies) * 0.95)
        p95 = latencies[min(idx_p95, len(latencies)-1)]
        return avg, stdev, p95

    avg_put, stdev_put, p95_put = calc_stats_metrics(upload_latencies)
    avg_get, stdev_get, p95_get = calc_stats_metrics(download_latencies)
    
    # Throughput calculation
    # Using total_duration for conservative estimate (wall clock time)
    throughput_mb_s = (total_bytes / 1024 / 1024) / max(total_duration, 0.001)

    _log(logger, f"[{label}] Uploaded Total: {total_objects} objects in {total_duration:.2f}s")
    
    # PUT Report
    _log(logger, f"[{label}] PUT Stats | Avg: {avg_put*1000:.2f} ms | StDev: {stdev_put*1000:.2f} ms | P95: {p95_put*1000:.2f} ms")
    
    # GET Report
    _log(logger, f"[{label}] GET Stats | Avg: {avg_get*1000:.2f} ms | StDev: {stdev_get*1000:.2f} ms | P95: {p95_get*1000:.2f} ms")
    
    # Throughput
    _log(logger, f"[{label}] Aggregated Throughput: {throughput_mb_s:.2f} MB/s")
