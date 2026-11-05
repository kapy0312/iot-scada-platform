import json
import os

import asyncpg
from fastapi import APIRouter, Query

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

LEGACY_COLUMNS = ("motor_speed", "temperature", "pressure")

router = APIRouter()


def readings_of(row) -> dict:
    """優先用 readings（JSONB，含所有訊號）；舊紀錄沒有 readings 時用三個舊欄位"""
    raw = row["readings"]
    if raw:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    return {c: row[c] for c in LEGACY_COLUMNS if row[c] is not None}


@router.get("/api/history")
async def get_history(
    device_id: str = Query(default="S7-1511T"),
    tag: str = Query(default="motor_speed"),
    hours: int = Query(default=1, ge=1, le=168),  # 最多查 7 天
):
    """查詢指定 tag 的歷史資料"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("""
            SELECT time, value
            FROM plc_measurements
            WHERE device_id = $1
              AND tag_name = $2
              AND time > NOW() - ($3 || ' hours')::INTERVAL
            ORDER BY time ASC
        """, device_id, tag, str(hours))
    finally:
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
    """查詢異常事件記錄；每筆含 readings（所有訊號），並保留舊的三個欄位"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("""
            SELECT time, anomaly_score, motor_speed, temperature, pressure, ai_analysis, readings
            FROM anomaly_events
            WHERE device_id = $1
              AND time > NOW() - ($2 || ' hours')::INTERVAL
            ORDER BY time DESC
        """, device_id, str(hours))
    finally:
        await conn.close()

    events = []
    for row in rows:
        readings = readings_of(row)
        events.append({
            "time": row["time"].isoformat(),
            "anomaly_score": row["anomaly_score"],
            "readings": readings,
            **{c: row[c] for c in LEGACY_COLUMNS},
            "ai_analysis": row["ai_analysis"],
        })
    return {"device_id": device_id, "hours": hours, "count": len(events), "events": events}
