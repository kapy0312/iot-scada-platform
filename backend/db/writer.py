import asyncpg
from datetime import datetime, timezone
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

async def write_to_db(device_id: str, data: dict):
    """
    把一筆 PLC 資料寫入 TimescaleDB
    data 格式：{"motor_speed": 123.45, "temperature": 987.65, ...}
    """
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        now = datetime.now(timezone.utc)
        rows = [
            (now, device_id, tag, float(value), 192)
            for tag, value in data.items()
            if tag not in ("timestamp", "motor_enable", "anomaly")
        ]
        await conn.executemany(
            """
            INSERT INTO plc_measurements (time, device_id, tag_name, value, quality)
            VALUES ($1, $2, $3, $4, $5)
            """,
            rows
        )
    finally:
        await conn.close()