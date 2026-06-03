import asyncio
import websockets

async def test():
    async with websockets.connect('ws://localhost:8000/ws/realtime') as ws:
        for _ in range(3):
            msg = await ws.recv()
            print(msg)

asyncio.run(test())