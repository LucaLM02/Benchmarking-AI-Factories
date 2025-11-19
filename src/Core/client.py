from Core.service import Service


class Client(Service):
    """Specialized Service that knows how to launch benchmark workloads."""

    def __init__(self, id, role, executor, monitor=None, logger=None, workload=None, executor_type="process"):
        super().__init__(id=id, role=role, executor=executor, monitor=monitor, logger=logger)
        self.workload = workload or {}
        self.executor_type = executor_type

    def _resolve_payload(self):
        if self.executor_type == "workload":
            if not self.workload:
                raise ValueError(f"Client {self.id} has no workload configuration")
            return self.workload

        cmd = self.workload.get("cmd") if isinstance(self.workload, dict) else None
        if not cmd:
            raise ValueError(
                f"Client {self.id} is using executor '{self.executor_type}' but no workload.cmd was provided"
            )
        return cmd

    def start_workload(self):
        payload = self._resolve_payload()
        descriptor = payload if isinstance(payload, str) else self.workload.get("type", self.role)
        print(f"[INFO] Launching client {self.id} ({self.role}) -> {descriptor}")
        super().start(payload)
