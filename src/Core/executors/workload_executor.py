import threading
from Core.abstracts import Executor
from Core.workloads import get_workload_runner


class WorkloadExecutor(Executor):
    """Executor that runs in-process Python workloads on background threads."""

    def __init__(self):
        super().__init__()
        self._thread = None
        self._status = "not_started"
        self._stop_event = threading.Event()
        self.logger = None

    def attach_logger(self, logger):
        self.logger = logger

    def _log(self, message, level="INFO"):
        if self.logger:
            self.logger.log(message, level)
        else:
            print(f"[WorkloadExecutor] {level}: {message}")

    def run(self, workload_spec):
        if not isinstance(workload_spec, dict):
            raise ValueError("WorkloadExecutor expects a workload specification dictionary")

        runner = get_workload_runner(workload_spec.get("type"))

        if self._thread and self._thread.is_alive():
            self.stop()

        self._status = "running"
        self._stop_event.clear()

        def _runner_wrapper():
            try:
                runner(workload_spec, logger=self.logger, stop_event=self._stop_event)
                if self._status == "running":
                    self._status = "finished"
            except Exception as exc:
                self._status = "failed"
                self._log(f"Workload failed: {exc}", "ERROR")
            finally:
                self._stop_event.set()

        self._thread = threading.Thread(target=_runner_wrapper, daemon=True)
        self._thread.start()
        return self._thread.name

    def status(self):
        return self._status

    def stop(self):
        if not self._thread:
            return

        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        if self._status == "running":
            self._status = "stopped"
