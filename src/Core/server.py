from Core.service import Service


class Server(Service):
    """Specialized Service that knows how to launch persistent servers."""

    def __init__(self, id, role, executor, command, monitor=None, logger=None):
        super().__init__(id=id, role=role, executor=executor, monitor=monitor, logger=logger)
        self.command = command

    def _resolve_command(self):
        if isinstance(self.command, str) and self.command.strip():
            return self.command

        if isinstance(self.command, dict):
            cmd = self.command.get("cmd")
            if cmd:
                return cmd

        raise ValueError(f"Server {self.id} has no valid command configured")

    def start_service(self):
        payload = self._resolve_command()
        descriptor = payload if isinstance(payload, str) else self.role
        print(f"[INFO] Launching server {self.id} ({self.role}) -> {descriptor}")
        super().start(payload)
