class Service:
    """Composable benchmark service (server or client)."""

    def __init__(self, id, role, executor, monitor=None, logger=None):
        self.id = id
        self.role = role
        self.executor = executor
        self.monitor = monitor
        self.logger = logger

    def start(self, command):
        if self.logger:
            self.logger.log(f"Starting service {self.id}", "INFO")

        if self.monitor:
            self.monitor.start()

        self.executor.run(command)

        if self.logger:
            self.logger.log(f"Service {self.id} started.", "INFO")

    def stop(self):
        if self.logger:
            self.logger.log(f"Stopping service {self.id}", "INFO")

        try:
            self.executor.stop()
        except Exception as e:
            print(f"[WARN] Executor stop failed for {self.id}: {e}")

        if self.monitor:
            self.monitor.stop()

    def status(self):
        st = self.executor.status()
        if self.logger:
            self.logger.log(f"Service {self.id} status: {st}", "DEBUG")
        return st

    def collect_metrics(self):
        if self.monitor:
            metrics = self.monitor.collect()
            if self.logger:
                self.logger.log(f"Metrics collected for {self.id}", "DEBUG")
            return metrics
        return None
