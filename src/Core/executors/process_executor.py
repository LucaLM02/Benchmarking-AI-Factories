# src/Core/executors/process_executor.py

import subprocess
import threading
import os
import signal
from Core.abstracts import Executor


class ProcessExecutor(Executor):
    """
    Executor for running processes directly inside the container.
    This executor does NOT use Slurm and does NOT spawn new containers.
    It is used for running MinIO, upload scripts, small tasks etc.
    """

    def __init__(self):
        super().__init__()
        self.process = None
        self._stdout_thread = None
        self._stderr_thread = None

    # -------------------------------
    # Helper: stream reader
    # -------------------------------
    def _stream_reader(self, stream, logger=None, prefix=""):
        for line in iter(stream.readline, b''):
            decoded = line.decode(errors="replace").rstrip()
            if logger:
                logger.log(f"{prefix}{decoded}", level="INFO")
        stream.close()

    # -------------------------------
    # Run command
    # -------------------------------
    def run(self, command: str):
        """
        Run a command as a local subprocess inside the container.
        """
        if not isinstance(command, str):
            raise ValueError("ProcessExecutor.run expects command as a string.")

        print(f"[ProcessExecutor] Running command: {command}")

        # Start process asynchronously
        self.process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid  # allow killing whole process group
        )

        # Start streaming threads
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

    # -------------------------------
    # Check status
    # -------------------------------
    def status(self):
        if self.process is None:
            return "not_started"
        ret = self.process.poll()
        if ret is None:
            return "running"
        return f"finished (code={ret})"

    # -------------------------------
    # Stop process
    # -------------------------------
    def stop(self):
        if not self.process:
            return

        print("[ProcessExecutor] Stopping process...")
        try:
            # Kill the full process group
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except Exception:
            pass

        self.process = None
