from typing import Callable, Dict

from Core.workloads import s3_upload, vllm_inference

WORKLOAD_REGISTRY: Dict[str, Callable] = {
    "s3-upload": s3_upload.run,
    "vllm-inference": vllm_inference.run,
}


def get_workload_runner(name: str) -> Callable:
    if not name:
        raise ValueError("Workload type is required")

    runner = WORKLOAD_REGISTRY.get(name)
    if not runner:
        raise ValueError(f"Unknown workload type '{name}'. Available: {list(WORKLOAD_REGISTRY)}")
    return runner
