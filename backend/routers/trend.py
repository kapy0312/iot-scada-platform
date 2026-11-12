"""
趨勢預警 API（唯讀、不用登入，跟其他查詢 API 一樣）
- GET /api/trend/current：目前每個訊號的預警狀態（跟 WebSocket 每秒推送的 trend 欄位相同）
- GET /api/trend/warnings：預警紀錄與統計（命中率、平均提前量、預估誤差），需要 migration 007
"""
import asyncpg
from fastapi import APIRouter, Query

import core.plc_simulator as plc
from core.warning_stats import row_view as _row, summarize  # noqa: F401（history 也從這裡 import）
from db.pool import acquire

router = APIRouter()


@router.get("/api/trend/current")
async def trend_current():
    if plc.latest_trend is None:
        return {"status": "no_data", "tags": {}, "message": "後端剛啟動，還沒收到 PLC 讀值"}
    return plc.latest_trend


@router.get("/api/trend/warnings")
async def trend_warnings(
    device_id: str = Query(default="S7-1511T"),
    hours: int = Query(default=24, ge=1, le=720),   # 最多查 30 天
    limit: int = Query(default=200, ge=1, le=1000),
):
    try:
        async with acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM trend_warnings
                WHERE device_id = $1 AND ended_at > now() - make_interval(hours => $2)
                ORDER BY started_at DESC
                LIMIT $3
            """, device_id, hours, limit)
    except asyncpg.UndefinedTableError:
        return {"available": False, "message": "找不到 trend_warnings 表：請先執行 db/migrations/007_trend_warnings.sql",
                "device_id": device_id, "hours": hours, "warnings": [], "summary": summarize([])}
    items = [_row(r) for r in rows]
    return {"available": True, "device_id": device_id, "hours": hours, "count": len(items),
            "summary": summarize(items), "warnings": items}


async def pre_warnings_of(conn, incident_ids: list[int]) -> dict[int, list[dict]]:
    """事件 id → 這次事件的事前預警（給 /api/incidents 用）；沒有 007 時回空 dict"""
    if not incident_ids:
        return {}
    try:
        rows = await conn.fetch("""
            SELECT * FROM trend_warnings WHERE incident_id = ANY($1::bigint[]) ORDER BY started_at
        """, incident_ids)
    except asyncpg.UndefinedTableError:
        return {}
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["incident_id"], []).append(_row(r))
    return out
