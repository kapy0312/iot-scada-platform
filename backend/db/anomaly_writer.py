import asyncpg
import os
from datetime import datetime, timezone

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

async def write_anomaly_event(device_id: str, data: dict, anomaly: dict):
    """異常觸發時寫入 anomaly_events 表"""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("""
            INSERT INTO anomaly_events
                (time, device_id, anomaly_score, motor_speed, temperature, pressure, ai_analysis)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
            datetime.now(timezone.utc),
            device_id,
            anomaly.get("score"),
            data.get("motor_speed"),
            data.get("temperature"),
            data.get("pressure"),
            anomaly.get("ai_analysis"),
        )
        await conn.close()
        print(f"[ANOMALY] 事件寫入 DB：score={anomaly.get('score'):.4f}")
    except Exception as e:
        print(f"[ANOMALY] 寫入失敗：{e}")