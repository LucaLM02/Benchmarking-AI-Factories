import asyncio
from monitor import Monitor
from server import AsyncServer
from client import AsyncClient

async def main():
    monitor = Monitor()

    # Start server
    server = AsyncServer(monitor)
    server_task = asyncio.create_task(server.start())

    await asyncio.sleep(5)  # Allow server to initialize

    # Start clients
    clients = [
        AsyncClient(monitor, "127.0.0.1") for _ in range(4)
    ]
    tasks = [
        asyncio.create_task(c.send_message(f"Hello from client {i+1}"))
        for i, c in enumerate(clients)
    ]
    await asyncio.gather(*tasks)

    await asyncio.sleep(1)
    monitor.dump()

asyncio.run(main())
