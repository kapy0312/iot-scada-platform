import asyncio
import asyncpg
import random
import math
from datetime import datetime, timezone, timedelta

DATABASE_URL = "postgresql://postgres:iotscada123@localhost:5435/iotscada"

async def generate():
    conn = await asyncpg.connect(DATABASE_URL)

    print("開始產生訓練資料...")

    rows = []
    # 從 7 天前開始，每秒一筆，共 5000 筆（約 83 分鐘的資料）
    start_time = datetime.now(timezone.utc) - timedelta(days=7)

    for i in range(5000):
        t = start_time + timedelta(seconds=i)

        # 模擬正常運行的波動（用 sin 加上隨機雜訊，更像真實設備）
        motor_speed = 1480.0 + 15 * math.sin(i / 50) + random.gauss(0, 5)
        temperature = 70.0   + 2  * math.sin(i / 80) + random.gauss(0, 0.5)
        pressure    = 5.0    + 0.3 * math.sin(i / 30) + random.gauss(0, 0.1)

        # 每 500 筆加入一些異常樣本（給模型學習用）
        if i % 500 == 0 and i > 0:
            motor_speed += random.uniform(200, 400)  # 突然飆高
            temperature += random.uniform(20, 40)
            pressure    += random.uniform(2, 4)

        rows.extend([
            (t, "S7-1511T", "motor_speed", round(motor_speed, 2), 192),
            (t, "S7-1511T", "temperature", round(temperature,  3), 192),
            (t, "S7-1511T", "pressure",    round(pressure,     3), 192),
        ])

    # 批次寫入，一次全部塞進去
    await conn.executemany(
        """
        INSERT INTO plc_measurements (time, device_id, tag_name, value, quality)
        VALUES ($1, $2, $3, $4, $5)
        """,
        rows
    )

    await conn.close()
    print(f"✅ 完成，共寫入 {len(rows)} 筆資料（{len(rows)//3} 個時間點）")
    print(f"   時間範圍：{start_time.strftime('%Y-%m-%d %H:%M')} ~ 現在")
    print(f"   異常樣本：{5000//500 - 1} 個時間點")

asyncio.run(generate())