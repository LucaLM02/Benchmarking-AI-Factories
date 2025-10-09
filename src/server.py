import asyncio
from monitor import Monitor

class AsyncServer:
    def __init__(self, monitor: Monitor, host: str = "0.0.0.0", port: int = 8888):
        self.monitor = monitor
        self.host = host
        self.port = port

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        await self.monitor.log("Server", f"Connected to {addr}")

        while True:
            data = await reader.readline()
            if not data:
                break
            message = data.decode().strip()
            await self.monitor.log("Server", f"Received from {addr}: {message}")

            writer.write(f"Server received: {message}\n".encode())
            await writer.drain()

        await self.monitor.log("Server", f"Connection closed {addr}")
        writer.close()
        await writer.wait_closed()

    async def start(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        addr = server.sockets[0].getsockname()
        await self.monitor.log("Server", f"Serving on {addr}")

        async with server:
            await server.serve_forever()


