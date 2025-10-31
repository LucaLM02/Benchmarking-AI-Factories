# src/Core/service.py
from Core.loggers.file_logger import FileLogger
from Core.monitors.prometheus_monitor import PrometheusMonitor
from Core.executors.slurm_executor import SlurmExecutor

class Service:
    """Composable benchmark service (client or server)"""

    def __init__(self, id, role, executor, monitor, logger):
        self.id = id
        self.role = role
        self.executor = executor
        self.monitor = monitor
        self.logger = logger

    def start(self, command):
        self.logger.log(f"Starting service {self.id}", "INFO")
        self.monitor.start()
        self.executor.run(command)
        self.logger.log(f"Service {self.id} started successfully.", "INFO")

    def stop(self):
        self.logger.log(f"Stopping service {self.id}", "INFO")
        self.executor.stop()
        self.monitor.stop()

    def status(self):
        status = self.executor.status()
        self.logger.log(f"Service {self.id} status: {status}", "DEBUG")
        return status
    
    def collect_metrics(self):
        metrics = self.monitor.collect()
        self.logger.log(f"Collected metrics for service {self.id}: {metrics}", "DEBUG")
        return metrics
