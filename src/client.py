import asyncio
import sys
from monitor import Monitor

class AsyncClient:
    def __init__(self, monitor: Monitor, host: str, port: int = 8888):
        self.monitor = monitor
        self.host = host
        self.port = port

    async def send_message(self, message: str):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        await self.monitor.log("Client", f"Sending: {message}")
        writer.write(f"{message}\n".encode())
        await writer.drain()

        data = await reader.readline()
        await self.monitor.log("Client", f"Received: {data.decode().strip()}")

        writer.close()
        await writer.wait_closed()

