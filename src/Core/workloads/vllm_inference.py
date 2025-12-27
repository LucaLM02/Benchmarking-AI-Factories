import time
import statistics
import concurrent.futures
import requests
from typing import Dict, Any, Optional, List
from threading import Event, Lock

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

    # Local stats
    local_latencies = []
    local_failures = 0
    local_tokens = 0
    
    # Error tracking - only log first occurrence of each error type
    seen_errors = set()
    consecutive_errors = 0
    error_counts = {}  # Track count per error type
    
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
                local_latencies.append(req_latency)
                consecutive_errors = 0  # Reset on success
                
                # Try to count tokens
                try:
                    data = resp.json()
                    usage = data.get("usage", {})
                    local_tokens += usage.get("total_tokens", 0)
                except Exception:
                    pass
            else:
                local_failures += 1
                consecutive_errors += 1
                
                # Log HTTP errors only first time per status code
                error_key = f"HTTP_{resp.status_code}"
                if error_key not in seen_errors:
                    seen_errors.add(error_key)
                    print(f"[vllm-inference][T{thread_id}] ERROR: HTTP {resp.status_code} from {endpoint}")
                
                # Backoff on server errors
                time.sleep(error_backoff_sec)
                 
        except requests.exceptions.ConnectionError as e:
            local_failures += 1
            consecutive_errors += 1
            error_counts["ConnectionError"] = error_counts.get("ConnectionError", 0) + 1
            
            # Log connection errors only first time
            error_key = "ConnectionError"
            if error_key not in seen_errors:
                seen_errors.add(error_key)
                print(f"[vllm-inference][T{thread_id}] CONNECTION ERROR: {e} -> {endpoint}")
            
            # CRITICAL: Backoff to prevent tight loop flooding
            time.sleep(error_backoff_sec)
            
        except requests.exceptions.Timeout as e:
            local_failures += 1
            consecutive_errors += 1
            error_counts["Timeout"] = error_counts.get("Timeout", 0) + 1
            
            error_key = "Timeout"
            if error_key not in seen_errors:
                seen_errors.add(error_key)
                print(f"[vllm-inference][T{thread_id}] TIMEOUT: Request took >{timeout}s -> {endpoint}")
            
            time.sleep(error_backoff_sec)
            
        except requests.exceptions.RequestException as e:
            local_failures += 1
            consecutive_errors += 1
            error_type = type(e).__name__
            error_counts[error_type] = error_counts.get(error_type, 0) + 1
            
            # Generic request exception - log with type name
            if error_type not in seen_errors:
                seen_errors.add(error_type)
                print(f"[vllm-inference][T{thread_id}] REQUEST ERROR ({error_type}): {e}")
            
            time.sleep(error_backoff_sec)
        
        # Periodic progress logging (every 30 requests)
        if i > 0 and i % 30 == 0:
            success_rate = (len(local_latencies) / i * 100) if i > 0 else 0
            avg_lat = (sum(local_latencies) / len(local_latencies) * 1000) if local_latencies else 0
            print(f"[vllm-inference][T{thread_id}] Progress: {i} reqs, {success_rate:.1f}% success, avg_lat={avg_lat:.1f}ms")

    # Merge local stats into shared global stats
    with stats_lock:
        shared_stats['latencies'].extend(local_latencies)
        shared_stats['failures'] += local_failures
        shared_stats['total_tokens'] += local_tokens
        shared_stats['total_requests'] += i
        # Merge error counts
        for err_type, count in error_counts.items():
            shared_stats['error_breakdown'][err_type] = shared_stats['error_breakdown'].get(err_type, 0) + count
    
    success_rate = (len(local_latencies) / i * 100) if i > 0 else 0
    print(f"[vllm-inference][T{thread_id}] Finished: {len(local_latencies)} ok / {i} attempts ({success_rate:.1f}%) / Errors: {error_counts}")


# ----------------------------------------------------------------------------
# MAIN ORCHESTRATOR
# ----------------------------------------------------------------------------
def run(config: Dict[str, Any], logger=None, stop_event: Optional[Event] = None):

    endpoint = config.get("endpoint", "http://127.0.0.1:8000/v1/chat/completions")
    mode = config.get("mode", "chat")
    concurrency = int(config.get("concurrency", 4))
    label = config.get("label", "vllm-load")
    
    _log(logger, f"[{label}] START — endpoint={endpoint}, mode={mode}, threads={concurrency}")

    # 1. Shared Stats Container
    shared_stats = {
        'latencies': [],
        'failures': 0,
        'total_tokens': 0,
        'total_requests': 0,
        'error_breakdown': {}  # Error counts by type (Timeout, ConnectionError, etc.)
    }
    stats_lock = Lock()

    # 2. Launch Workers
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
    
    # 3. Final Aggregated Reporting
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

    _log(logger, f"[{label}] Completed: {len(latencies)} ok / {total_requests} attempts / {failures} failed in {total_duration:.2f}s")
    
    # Latency Report
    _log(logger, f"[{label}] Latency | Avg: {avg_lat*1000:.2f} ms | StDev: {stdev_lat*1000:.2f} ms | P95: {p95_lat*1000:.2f} ms | P99: {p99_lat*1000:.2f} ms")
    
    # Throughput Report
    _log(logger, f"[{label}] Throughput: {throughput_rps:.2f} req/s | {throughput_tps:.2f} tokens/s | Success Rate: {success_rate:.1f}%")
    
    # Error Breakdown
    if error_breakdown:
        _log(logger, f"[{label}] Error Breakdown: {error_breakdown}")
