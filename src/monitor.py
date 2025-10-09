import asyncio
from datetime import datetime

class Monitor:
    """
    Collects and synchronizes log messages from multiple async components.
    Allows both the server and clients to report events, which are
    timestamped and stored for later display or export.
    """

    def __init__(self):
        self.messages: list[str] = []
        self.lock = asyncio.Lock()

    async def log(self, source: str, message: str):
        """Thread-safe async log append with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        async with self.lock:
            self.messages.append(f"[{timestamp}] [{source}] {message}")

    def dump(self):
        """Prints all stored log messages in order."""
        print("\n===== MONITOR LOG =====")
        for m in self.messages:
            print(m)
        print("=======================")
