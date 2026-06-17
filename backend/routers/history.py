from fastapi import APIRouter, Query
from datetime import datetime, timezone
import asyncpg
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

router = APIRouter()

@router.get("/api/history")
async def get_history(
    device_id: str = Query(default="S7-1511T"),
    tag: str = Query(default="motor_speed"),
    hours: int = Query(default=1, ge=1, le=168),  # 最多查 7 天
):
    """查詢指定 tag 的歷史資料"""
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT time, value
        FROM plc_measurements
        WHERE device_id = $1
          AND tag_name = $2
          AND time > NOW() - ($3 || ' hours')::INTERVAL
        ORDER BY time ASC
    """, device_id, tag, str(hours))
    await conn.close()

    return {
        "device_id": device_id,
        "tag": tag,
        "hours": hours,
        "count": len(rows),
        "data": [{"time": row["time"].isoformat(), "value": row["value"]} for row in rows]
    }

@router.get("/api/anomaly-events")
async def get_anomaly_events(
    device_id: str = Query(default="S7-1511T"),
    hours: int = Query(default=24, ge=1, le=720),  # 最多查 30 天
):
    """查詢異常事件記錄"""
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT time, anomaly_score, motor_speed, temperature, pressure, ai_analysis
        FROM anomaly_events
        WHERE device_id = $1
          AND time > NOW() - ($2 || ' hours')::INTERVAL
        ORDER BY time DESC
    """, device_id, str(hours))
    await conn.close()

    return {
        "device_id": device_id,
        "hours": hours,
        "count": len(rows),
        "events": [{
            "time":          row["time"].isoformat(),
            "anomaly_score": row["anomaly_score"],
            "motor_speed":   row["motor_speed"],
            "temperature":   row["temperature"],
            "pressure":      row["pressure"],
            "ai_analysis":   row["ai_analysis"],
        } for row in rows]
    }