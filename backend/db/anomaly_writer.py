import json
import os
from datetime import datetime, timezone

import asyncpg

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)


async def write_anomaly_event(device_id: str, values: dict, anomaly: dict):
    """
    異常時寫入 anomaly_events
    - readings（JSONB）：所有訊號的讀值，之後新增訊號不必改資料表
    - motor_speed / temperature / pressure：舊欄位照常寫入，相容舊的查詢
    需要先執行 db/migrations/001_anomaly_readings.sql
    """
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute("""
                INSERT INTO anomaly_events
                    (time, device_id, anomaly_score, motor_speed, temperature, pressure, ai_analysis, readings)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
                datetime.now(timezone.utc),
                device_id,
                anomaly.get("score"),
                values.get("motor_speed"),
                values.get("temperature"),
                values.get("pressure"),
                anomaly.get("ai_analysis"),
                json.dumps(values),
            )
        finally:
            await conn.close()
        print(f"[ANOMALY] 事件寫入 DB：score={anomaly.get('score'):.4f}")
    except Exception as e:
        print(f"[ANOMALY] 寫入失敗：{e}")
