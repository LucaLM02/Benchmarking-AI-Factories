import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Optional
from threading import Event

import requests


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


def run(config: Dict[str, Any], logger=None, stop_event: Optional[Event] = None):
    """
    Simple OpenAI-compatible workload for vLLM. Sends chat/completion
    requests and records latency statistics.
    """
    mode = config.get("mode", "chat")
    endpoint = config.get("endpoint", "http://127.0.0.1:8000/v1/chat/completions")
    timeout = float(config.get("timeout", 8))
    requests_count = int(config.get("requests", 40))
    concurrency = int(config.get("concurrency", 4))
    api_key = config.get("api_key", "dummy-key")
    label = config.get("label", "vllm-load")

    if mode not in ("chat", "completion"):
        raise ValueError(f"Unsupported mode '{mode}', expected 'chat' or 'completion'")

    _log(logger, f"[{label}] START — endpoint={endpoint} mode={mode} requests={requests_count}")

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {api_key}"})

    latencies = []
    failures = 0

    def _invoke(idx: int):
        nonlocal failures
        if stop_event and stop_event.is_set():
            return

        payload = (
            _build_chat_payload(config) if mode == "chat" else _build_completion_payload(config)
        )
        t0 = time.time()
        try:
            resp = session.post(endpoint, json=payload, timeout=timeout)
            elapsed = time.time() - t0
            if resp.status_code == 200:
                latencies.append(elapsed)
                if idx % 10 == 0:
                    _log(logger, f"[{label}] req#{idx} {elapsed*1000:.1f} ms")
            else:
                failures += 1
                _log(
                    logger,
                    f"[{label}] req#{idx} failed HTTP {resp.status_code}: {resp.text[:120]}",
                    "WARN",
                )
        except Exception as exc:
            failures += 1
            _log(logger, f"[{label}] req#{idx} exception: {exc}", "ERROR")

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for i in range(requests_count):
            if stop_event and stop_event.is_set():
                _log(logger, f"[{label}] stop requested before enqueue idx={i}", "WARN")
                break
            futures.append(pool.submit(_invoke, i + 1))

        for f in as_completed(futures):
            if stop_event and stop_event.is_set():
                break
            try:
                f.result()
            except Exception as exc:  # pragma: no cover - executor already logs
                failures += 1
                _log(logger, f"[{label}] worker exception: {exc}", "ERROR")

    count = len(latencies)
    avg_ms = (sum(latencies) / count) * 1000 if count else 0
    if count:
        sorted_lat = sorted(latencies)
        idx = min(len(sorted_lat) - 1, int(0.99 * len(sorted_lat)))
        p99_ms = sorted_lat[idx] * 1000
    else:
        p99_ms = 0

    _log(logger, f"[{label}] Completed: {count} ok / {requests_count} planned")
    _log(logger, f"[{label}] Avg latency: {avg_ms:.1f} ms")
    _log(logger, f"[{label}] P99 latency: {p99_ms:.1f} ms")
    _log(logger, f"[{label}] Failures: {failures}")
