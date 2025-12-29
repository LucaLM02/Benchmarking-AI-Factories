import time
import statistics
import concurrent.futures
import requests
from typing import Dict, Any, Optional, List
from threading import Event, Lock, Thread

def _log(logger, message: str, level: str = "INFO"):
    if logger:
        logger.log(message, level)
    else:
        print(f"[vllm-inference] {level}: {message}")

def _build_chat_payload(cfg: Dict[str, Any]) -> Dict[str, Any]:
    prompt = cfg.get("prompt") or "Write a short JSON describing the weather in Rome."
    system = cfg.get("system_prompt") or "You are a fast inference worker."
    max_tokens = int(cfg.get("max_tokens", 128))
    temperature = float(cfg.get("temperature", 0.1))
    return {
        "model": cfg.get("model", "meta-llama/Llama-3-8b-instruct"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

def _build_completion_payload(cfg: Dict[str, Any]) -> Dict[str, Any]:
    prompt = cfg.get("prompt") or "List three European capitals."
    max_tokens = int(cfg.get("max_tokens", 128))
    temperature = float(cfg.get("temperature", 0.1))
    return {
        "model": cfg.get("model", "meta-llama/Llama-3-8b-instruct"),
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def _detect_running_model(endpoint: str, api_key: str = "dummy-key", timeout: float = 10.0) -> Optional[str]:
    """
    Auto-detect the model running on a vLLM server by querying /v1/models.
    
    Args:
        endpoint: The inference endpoint (e.g., http://127.0.0.1:8000/v1/completions)
        api_key: Bearer token for authentication
        timeout: Request timeout in seconds
    
    Returns:
        Model ID string (e.g., "facebook/opt-125m") or None if detection fails
    """
    try:
        # Parse the base URL from the endpoint
        # e.g., "http://127.0.0.1:8000/v1/completions" -> "http://127.0.0.1:8000"
        from urllib.parse import urlparse
        parsed = urlparse(endpoint)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        models_url = f"{base_url}/v1/models"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        resp = requests.get(models_url, headers=headers, timeout=timeout)
        
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("data", [])
            if models and len(models) > 0:
                # Return the first model's ID
                model_id = models[0].get("id")
                return model_id
        
        return None
        
    except requests.exceptions.RequestException:
        # Server unreachable or endpoint error
        return None
    except Exception:
        # JSON parsing or other errors
        return None


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
    Performs the inference loop with proper error handling and backoff.
    
    IMPORTANT: Stats are updated in real-time to shared_stats (not batched at end)
    so the periodic reporter can see live data.
    """
    
    # Unpack config - endpoint MUST come from config, not hardcoded
    endpoint = config.get("endpoint")
    if not endpoint:
        print(f"[vllm-inference][T{thread_id}] FATAL: No endpoint specified in config!")
        return
    
    mode = config.get("mode", "chat")
    api_key = config.get("api_key", "dummy-key")
    timeout = float(config.get("timeout", 60))  # Increased default timeout
    
    total_requests = int(config.get("requests", 0))
    concurrency = int(config.get("concurrency", 1))
    target_requests = total_requests // concurrency if total_requests > 0 else 0

    duration_sec = int(config.get("duration_sec", 0))
    preprocess_ms = int(config.get("preprocess_ms", 5))
    
    # Backoff configuration
    error_backoff_sec = float(config.get("error_backoff_sec", 1.0))
    max_consecutive_errors = int(config.get("max_consecutive_errors", 50))
    
    # Thread-local Session
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    })

    # Local counters for progress logging only
    local_success_count = 0
    local_failure_count = 0
    local_latency_sum = 0.0
    
    # Error tracking - only log first occurrence of each error type
    seen_errors = set()
    consecutive_errors = 0
    
    start_time = time.time()
    i = 0
    
    print(f"[vllm-inference][T{thread_id}] Starting worker -> {endpoint}")
    
    while True:
        # 1. Check stop conditions
        elapsed = time.time() - start_time
        if duration_sec > 0:
            if elapsed >= duration_sec:
                break
        elif target_requests > 0:
            if i >= target_requests:
                break
        # If both are 0, run until stop_event

        if stop_event and stop_event.is_set():
            break
        
        # Safety: abort if too many consecutive errors (server is definitely down)
        if consecutive_errors >= max_consecutive_errors:
            print(f"[vllm-inference][T{thread_id}] Aborting: {consecutive_errors} consecutive errors. Server unreachable.")
            break
            
        i += 1

        # 2. Simulated preprocessing
        if preprocess_ms > 0:
             time.sleep(preprocess_ms / 1000.0)

        # 3. Inference Request
        payload = (
            _build_chat_payload(config) if mode == "chat" else _build_completion_payload(config)
        )
        
        t0 = time.time()
        try:
            resp = session.post(endpoint, json=payload, timeout=timeout)
            req_latency = time.time() - t0
            
            if resp.status_code == 200:
                consecutive_errors = 0  # Reset on success
                local_success_count += 1
                local_latency_sum += req_latency
                
                # Try to count tokens
                tokens_this_req = 0
                try:
                    data = resp.json()
                    usage = data.get("usage", {})
                    tokens_this_req = usage.get("total_tokens", 0)
                except Exception:
                    pass
                
                # *** REAL-TIME UPDATE: Push to shared stats immediately ***
                with stats_lock:
                    shared_stats['latencies'].append(req_latency)
                    shared_stats['total_tokens'] += tokens_this_req
                    shared_stats['total_requests'] += 1
                
            else:
                local_failure_count += 1
                consecutive_errors += 1
                
                # Update shared failure count
                with stats_lock:
                    shared_stats['failures'] += 1
                    shared_stats['total_requests'] += 1
                    error_key = f"HTTP_{resp.status_code}"
                    shared_stats['error_breakdown'][error_key] = shared_stats['error_breakdown'].get(error_key, 0) + 1
                
                # Log HTTP errors only first time per status code
                error_key = f"HTTP_{resp.status_code}"
                if error_key not in seen_errors:
                    seen_errors.add(error_key)
                    print(f"[vllm-inference][T{thread_id}] ERROR: HTTP {resp.status_code} from {endpoint}")
                
                # Backoff on server errors
                time.sleep(error_backoff_sec)
                 
        except requests.exceptions.ConnectionError as e:
            local_failure_count += 1
            consecutive_errors += 1
            
            # Update shared stats
            with stats_lock:
                shared_stats['failures'] += 1
                shared_stats['total_requests'] += 1
                shared_stats['error_breakdown']["ConnectionError"] = shared_stats['error_breakdown'].get("ConnectionError", 0) + 1
            
            # Log connection errors only first time
            error_key = "ConnectionError"
            if error_key not in seen_errors:
                seen_errors.add(error_key)
                print(f"[vllm-inference][T{thread_id}] CONNECTION ERROR: {e} -> {endpoint}")
            
            # CRITICAL: Backoff to prevent tight loop flooding
            time.sleep(error_backoff_sec)
            
        except requests.exceptions.Timeout as e:
            local_failure_count += 1
            consecutive_errors += 1
            
            with stats_lock:
                shared_stats['failures'] += 1
                shared_stats['total_requests'] += 1
                shared_stats['error_breakdown']["Timeout"] = shared_stats['error_breakdown'].get("Timeout", 0) + 1
            
            error_key = "Timeout"
            if error_key not in seen_errors:
                seen_errors.add(error_key)
                print(f"[vllm-inference][T{thread_id}] TIMEOUT: Request took >{timeout}s -> {endpoint}")
            
            time.sleep(error_backoff_sec)
            
        except requests.exceptions.RequestException as e:
            local_failure_count += 1
            consecutive_errors += 1
            error_type = type(e).__name__
            
            with stats_lock:
                shared_stats['failures'] += 1
                shared_stats['total_requests'] += 1
                shared_stats['error_breakdown'][error_type] = shared_stats['error_breakdown'].get(error_type, 0) + 1
            
            # Generic request exception - log with type name
            if error_type not in seen_errors:
                seen_errors.add(error_type)
                print(f"[vllm-inference][T{thread_id}] REQUEST ERROR ({error_type}): {e}")
            
            time.sleep(error_backoff_sec)
        
        # Periodic progress logging (every 30 requests)
        if i > 0 and i % 30 == 0:
            success_rate = (local_success_count / i * 100) if i > 0 else 0
            avg_lat = (local_latency_sum / local_success_count * 1000) if local_success_count > 0 else 0
            print(f"[vllm-inference][T{thread_id}] Progress: {i} reqs, {success_rate:.1f}% success, avg_lat={avg_lat:.1f}ms")

    # Final worker summary (stats already pushed to shared_stats)
    success_rate = (local_success_count / i * 100) if i > 0 else 0
    print(f"[vllm-inference][T{thread_id}] Finished: {local_success_count} ok / {i} attempts ({success_rate:.1f}%)")




# ----------------------------------------------------------------------------
# MAIN ORCHESTRATOR
# ----------------------------------------------------------------------------
def run(config: Dict[str, Any], logger=None, stop_event: Optional[Event] = None):

    endpoint = config.get("endpoint", "http://127.0.0.1:8000/v1/chat/completions")
    mode = config.get("mode", "chat")
    concurrency = int(config.get("concurrency", 4))
    label = config.get("label", "vllm-load")
    stats_interval = int(config.get("stats_interval", 5))  # Periodic stats every N seconds
    api_key = config.get("api_key", "dummy-key")
    
    # 0. Model Auto-Discovery
    # ----------------------------------------------------------------
    # Query the vLLM server to detect which model is actually running
    detected_model = _detect_running_model(endpoint, api_key)
    if detected_model:
        config["model"] = detected_model
        _log(logger, f"[{label}] [Init] Auto-detected server model: '{detected_model}'")
    else:
        configured_model = config.get("model", "meta-llama/Llama-3-8b-instruct")
        _log(logger, f"[{label}] [Init] Model auto-detection failed, using configured: '{configured_model}'", "WARN")
    
    _log(logger, f"[{label}] START — endpoint={endpoint}, mode={mode}, threads={concurrency}, model={config.get('model', 'unknown')}")

    # 1. Shared Stats Container
    shared_stats = {
        'latencies': [],
        'failures': 0,
        'total_tokens': 0,
        'total_requests': 0,
        'error_breakdown': {},  # Error counts by type (Timeout, ConnectionError, etc.)
        # Snapshot tracking for periodic delta calculations
        '_last_snapshot_requests': 0,
        '_last_snapshot_tokens': 0,
        '_last_snapshot_latencies_len': 0,
    }
    stats_lock = Lock()
    reporter_stop = Event()
    
    # 2. Periodic Stats Reporter Thread
    # ----------------------------------------------------------------
    def periodic_reporter():
        """Background thread that logs statistics every stats_interval seconds."""
        last_time = time.time()
        
        while not reporter_stop.is_set():
            reporter_stop.wait(timeout=stats_interval)
            if reporter_stop.is_set():
                break
            
            now = time.time()
            elapsed_interval = now - last_time
            last_time = now
            
            with stats_lock:
                # Current totals
                current_latencies = len(shared_stats['latencies'])
                current_requests = shared_stats['total_requests']
                current_tokens = shared_stats['total_tokens']
                current_failures = shared_stats['failures']
                
                # Delta since last snapshot
                delta_requests = current_latencies - shared_stats['_last_snapshot_latencies_len']
                delta_tokens = current_tokens - shared_stats['_last_snapshot_tokens']
                
                # Calculate stats on recent latencies (last interval)
                recent_latencies = shared_stats['latencies'][shared_stats['_last_snapshot_latencies_len']:]
                
                # Update snapshot markers
                shared_stats['_last_snapshot_requests'] = current_requests
                shared_stats['_last_snapshot_tokens'] = current_tokens
                shared_stats['_last_snapshot_latencies_len'] = current_latencies
            
            # Calculate metrics for this interval
            rps = delta_requests / max(elapsed_interval, 0.001)
            tps = delta_tokens / max(elapsed_interval, 0.001)
            
            if recent_latencies:
                avg_lat = statistics.mean(recent_latencies) * 1000  # Convert to ms
                try:
                    stdev_lat = statistics.stdev(recent_latencies) * 1000
                except statistics.StatisticsError:
                    stdev_lat = 0.0
                sorted_lats = sorted(recent_latencies)
                idx_p95 = int(len(sorted_lats) * 0.95)
                p95_lat = sorted_lats[min(idx_p95, len(sorted_lats)-1)] * 1000
            else:
                avg_lat = stdev_lat = p95_lat = 0.0
            
            # Log periodic stats in S3-consistent format
            _log(logger, f"[{label}] Stats | RPS: {rps:.1f} | Latency Avg: {avg_lat:.2f} ms | P95: {p95_lat:.2f} ms | StDev: {stdev_lat:.2f} ms | Tokens/s: {tps:.1f}")
    
    # Start reporter thread
    reporter_thread = Thread(target=periodic_reporter, daemon=True)
    reporter_thread.start()

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
    
    # Stop the reporter thread
    reporter_stop.set()
    reporter_thread.join(timeout=2)
    
    # 4. Final Aggregated Reporting
    # ----------------------------------------------------------------
    latencies = shared_stats['latencies']
    failures = shared_stats['failures']
    total_tokens = shared_stats['total_tokens']
    total_requests = shared_stats['total_requests']
    error_breakdown = shared_stats['error_breakdown']

    def calc_stats_metrics(lats):
        if not lats:
            return 0.0, 0.0, 0.0, 0.0
        avg = statistics.mean(lats)
        try:
            stdev = statistics.stdev(lats)
        except statistics.StatisticsError:
            stdev = 0.0
        
        lats.sort()
        idx_p95 = int(len(lats) * 0.95)
        idx_p99 = int(len(lats) * 0.99)
        p95 = lats[min(idx_p95, len(lats)-1)]
        p99 = lats[min(idx_p99, len(lats)-1)]
        return avg, stdev, p95, p99

    avg_lat, stdev_lat, p95_lat, p99_lat = calc_stats_metrics(latencies)
    
    # Throughput calculation
    throughput_rps = (len(latencies)) / max(total_duration, 0.001)
    throughput_tps = (total_tokens) / max(total_duration, 0.001)
    success_rate = (len(latencies) / total_requests * 100) if total_requests > 0 else 0

    _log(logger, f"[{label}] FINAL | Completed: {len(latencies)} ok / {total_requests} attempts / {failures} failed in {total_duration:.2f}s")
    
    # Latency Report
    _log(logger, f"[{label}] FINAL Latency | Avg: {avg_lat*1000:.2f} ms | StDev: {stdev_lat*1000:.2f} ms | P95: {p95_lat*1000:.2f} ms | P99: {p99_lat*1000:.2f} ms")
    
    # Throughput Report
    _log(logger, f"[{label}] FINAL Throughput: {throughput_rps:.2f} req/s | {throughput_tps:.2f} tokens/s | Success Rate: {success_rate:.1f}%")
    
    # Error Breakdown
    if error_breakdown:
        _log(logger, f"[{label}] Error Breakdown: {error_breakdown}")

