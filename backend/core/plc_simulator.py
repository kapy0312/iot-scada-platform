import asyncio
import time
import random
from core.ws_manager import manager


async def poll_plc_forever():
    base_speed = 1150.0
    base_temp = 70.0
    base_press = 5.0

    while True:
        data = {
            "timestamp": time.time(),
            "motor_speed": round(base_speed + random.uniform(-15, 15), 1),
            "temperature": round(base_temp + random.uniform(-2, 2),   2),
            "pressure":    round(base_press + random.uniform(-0.3, 0.3), 3),
            "motor_enable": 1,
        }
        await manager.broadcast(data)
        await asyncio.sleep(1)
