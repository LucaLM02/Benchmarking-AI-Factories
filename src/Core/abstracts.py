# src/Core/abstracts.py

from abc import ABC, abstractmethod
from typing import Dict, Any

# --- Executor Abstract Class ---
class Executor(ABC):
    """Abstract base class for all executors (Slurm, Apptainer, Local, etc.)"""

    @abstractmethod
    def run(self, command: Any, **kwargs) -> str:
        """Launch a command or job (string shell command or workload payload). Returns an identifier."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop/terminate running job or process."""
        pass

    @abstractmethod
    def status(self) -> str:
        """Return current status of job: running, completed, failed, etc."""
        pass


# --- Monitor Abstract Class ---
class Monitor(ABC):
    """Abstract base for system/service monitors (Prometheus, Node exporter, etc.)"""

    @abstractmethod
    def start(self) -> None:
        """Start monitoring the target."""
        pass

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """Collect and return metrics snapshot."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop monitoring and finalize metrics collection."""
        pass


# --- Logger Abstract Class ---
class Logger(ABC):
    """Abstract base for logging system (FileLogger, PrometheusLogger, etc.)"""

    @abstractmethod
    def log(self, message: str, level: str = "INFO") -> None:
        """Log message at a given level."""
        pass

    @abstractmethod
    def export(self) -> str:
        """Export log data to file or external storage."""
        pass
