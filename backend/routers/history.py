import json

from fastapi import APIRouter, Query

from db.pool import acquire

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
    async with acquire() as conn:
        rows = await conn.fetch("""
            SELECT time, value
            FROM plc_measurements
            WHERE device_id = $1
              AND tag_name = $2
              AND time > NOW() - ($3 || ' hours')::INTERVAL
            ORDER BY time ASC
        """, device_id, tag, str(hours))

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
    async with acquire() as conn:
        rows = await conn.fetch("""
            SELECT time, anomaly_score, motor_speed, temperature, pressure, ai_analysis, readings
            FROM anomaly_events
            WHERE device_id = $1
              AND time > NOW() - ($2 || ' hours')::INTERVAL
            ORDER BY time DESC
        """, device_id, str(hours))

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


def _json_field(v):
    """asyncpg 預設把 jsonb 讀成字串"""
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else v


@router.get("/api/incidents")
async def get_incidents(
    device_id: str = Query(default="S7-1511T"),
    hours: int = Query(default=24, ge=1, le=720),   # 最多查 30 天
    limit: int = Query(default=200, ge=1, le=1000),
):
    """查詢事件（一次故障一筆）：起訖時間、等級、峰值、最嚴重時刻的診斷與手冊來源"""
    async with acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, device_id, started_at, ended_at, records, level, severity,
                   peak, min_score, ai_analysis, ai_source
            FROM anomaly_incidents
            WHERE device_id = $1
              AND ended_at > NOW() - ($2 || ' hours')::INTERVAL
            ORDER BY started_at DESC
            LIMIT $3
        """, device_id, str(hours), limit)

    incidents = [{
        "id": r["id"],
        "device_id": r["device_id"],
        "started_at": r["started_at"].isoformat(),
        "ended_at": r["ended_at"].isoformat(),
        "duration_seconds": int((r["ended_at"] - r["started_at"]).total_seconds()) + 1,
        "records": r["records"],
        "level": r["level"],
        "severity": r["severity"],
        "peak": _json_field(r["peak"]),
        "min_score": r["min_score"],
        "ai_analysis": r["ai_analysis"],
        "ai_source": _json_field(r["ai_source"]),
    } for r in rows]
    return {"device_id": device_id, "hours": hours, "count": len(incidents), "incidents": incidents}
