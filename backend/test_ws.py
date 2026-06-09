import asyncio
import websockets
import json

async def test():
    async with websockets.connect('ws://localhost:8000/ws/realtime') as ws:
        for _ in range(5):
            msg = await ws.recv()
            data = json.loads(msg)
            anomaly = data.get("anomaly", {})
            print(f"motor={data['motor_speed']:.1f} | "
                  f"temp={data['temperature']:.2f} | "
                  f"status={anomaly.get('status','?')} | "
                  f"score={anomaly.get('score','?')}")

asyncio.run(test())