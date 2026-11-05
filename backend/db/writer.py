import asyncpg
from datetime import datetime, timezone
import os

from core.device_profile import all_tags

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

# 白名單由設備規格決定：只寫入規格裡定義的數值訊號，'source'、'device_id' 這類文字欄位不會進來
NUMERIC_TAGS = all_tags()


async def write_to_db(device_id: str, values: dict):
    """
    把一秒的讀值寫入 TimescaleDB（長表格：一個訊號一列）
    values 格式：{"motor_speed": 1480.2, "temperature": 70.1, ...}
    """
    now = datetime.now(timezone.utc)
    rows = [
        (now, device_id, tag, float(value), 192)
        for tag, value in values.items()
        if tag in NUMERIC_TAGS and value is not None
    ]
    if not rows:
        return
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.executemany(
            """
            INSERT INTO plc_measurements (time, device_id, tag_name, value, quality)
            VALUES ($1, $2, $3, $4, $5)
            """,
            rows
        )
    finally:
        await conn.close()
