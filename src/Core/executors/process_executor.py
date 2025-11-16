# src/Core/executors/process_executor.py

import subprocess
import threading
import os
import signal
from Core.abstracts import Executor


class ProcessExecutor(Executor):
    """
    Executor used to run commands directly on the local system.
    Works inside the container without Slurm or Apptainer.
    """

    def __init__(self):
        super().__init__()
        self.process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self.logger = None

    # ----------------------------------------------------
    # Attach logger (important!)
    # ----------------------------------------------------
    def attach_logger(self, logger):
        self.logger = logger

    # ----------------------------------------------------
    # Internal stream reader
    # ----------------------------------------------------
    def _stream_reader(self, stream, logger=None, prefix=""):
        for line in iter(stream.readline, b''):
            decoded = line.decode(errors="replace").rstrip()
            if logger:
                logger.log(f"{prefix}{decoded}", "INFO")
        stream.close()

    # ----------------------------------------------------
    # Run subprocess
    # ----------------------------------------------------
    def run(self, command: str):
        if not isinstance(command, str):
            raise ValueError("ProcessExecutor.run expects a string command.")

        # Kill a previous process if still running
        if self.process and self.process.poll() is None:
            self.stop()

        print(f"[ProcessExecutor] Running command: {command}")

        self.process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid   # start new process group for clean kill
        )

        # Logging threads
        if self.logger:
            self._stdout_thread = threading.Thread(
                target=self._stream_reader,
                args=(self.process.stdout, self.logger, "[STDOUT] "),
                daemon=True
            )
            self._stderr_thread = threading.Thread(
                target=self._stream_reader,
                args=(self.process.stderr, self.logger, "[STDERR] "),
                daemon=True
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

        return self.process.pid

    # ----------------------------------------------------
    # Status
    # ----------------------------------------------------
    def status(self):
        if self.process is None:
            return "not_started"
        ret = self.process.poll()
        if ret is None:
            return "running"
        return f"finished (code={ret})"

    # ----------------------------------------------------
    # Stop
    # ----------------------------------------------------
    def stop(self):
        if not self.process:
            return

        print("[ProcessExecutor] Stopping process...")
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except Exception:
            pass

        try:
            self.process.wait(timeout=5)
        except Exception:
            pass

        self.process = None
        self._stdout_thread = None
        self._stderr_thread = None
