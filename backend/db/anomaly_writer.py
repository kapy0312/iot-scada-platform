import asyncio
import json
import time
from datetime import datetime

from db.pool import acquire
from db.trend_writer import link_incident

# 同一事件的新增與更新必須依序執行：第一筆還沒拿到 id 前，第二筆不能又新增一次
_lock = asyncio.Lock()


def _json(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


# migration 010 的 limits、limits_changed 欄位在不在：沒跑 010 時照舊寫入，跑完後最多 5 分鐘自動開始寫
_limit_cols: bool | None = None
_limit_cols_checked = 0.0
LIMIT_COLS_RECHECK = 300


async def has_limit_columns(conn) -> bool:
    global _limit_cols, _limit_cols_checked
    if _limit_cols is None or (not _limit_cols and time.monotonic() - _limit_cols_checked > LIMIT_COLS_RECHECK):
        _limit_cols = await conn.fetchval("""
            SELECT count(*) = 2 FROM information_schema.columns
            WHERE table_name = 'anomaly_incidents' AND column_name IN ('limits', 'limits_changed')
        """)
        _limit_cols_checked = time.monotonic()
    return bool(_limit_cols)


async def _upsert_incident(conn, inc: dict) -> None:
    """事件第一次寫入時新增並取得 id，之後都是更新"""
    was_new = inc["id"] is None
    try:
        async with conn.transaction():
            await _upsert_incident_body(conn, inc)
    except BaseException:
        if was_new:
            inc["id"] = None    # 交易沒成功，新增的那筆也被撤銷了；下一秒重新新增
        raise


async def _upsert_incident_body(conn, inc: dict) -> None:
    with_limits = await has_limit_columns(conn)
    if inc["id"] is None:
        inc["id"] = await conn.fetchval("""
            INSERT INTO anomaly_incidents
                (device_id, started_at, ended_at, records, level, severity,
                 peak, min_score, ai_analysis, ai_source)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb)
            RETURNING id
        """, inc["device_id"], inc["started_at"], inc["ended_at"], inc["records"],
            inc["level"], inc["severity"], _json(inc["peak"]), inc["min_score"],
            inc["ai_analysis"], _json(inc["ai_source"]))
        # 事件剛建立：把剛「到了」的趨勢預警連過來（事前預警；沒有 007 時不做事）
        await link_incident(conn, inc["device_id"], inc["id"], inc["started_at"])
    else:
        await conn.execute("""
            UPDATE anomaly_incidents
            SET ended_at = $2, records = $3, level = $4, severity = $5,
                peak = $6::jsonb, min_score = $7, ai_analysis = $8, ai_source = $9::jsonb,
                updated_at = now()
            WHERE id = $1
        """, inc["id"], inc["ended_at"], inc["records"], inc["level"], inc["severity"],
            _json(inc["peak"]), inc["min_score"], inc["ai_analysis"], _json(inc["ai_source"]))
    if with_limits:
        # 當時門檻（migration 010）：跟上面在同一個交易裡，雲端同步不會讀到只寫一半的列
        await conn.execute(
            "UPDATE anomaly_incidents SET limits = $2::jsonb, limits_changed = $3 WHERE id = $1",
            inc["id"], _json(inc.get("limits")), bool(inc.get("limits_changed")))


async def record_anomaly(device_id: str, values: dict, anomaly: dict, incident: dict, at: datetime):
    """
    每個異常秒呼叫一次：先新增／更新所屬事件，再寫入這一秒的紀錄並連到事件
    - readings（JSONB）：所有訊號的讀值
    - motor_speed / temperature / pressure：舊欄位照常寫入，相容舊的查詢
    需要先執行 db/migrations/001、002
    """
    async with _lock:
        try:
            async with acquire() as conn:
                await _upsert_incident(conn, incident)
                await conn.execute("""
                    INSERT INTO anomaly_events
                        (time, device_id, anomaly_score, motor_speed, temperature, pressure,
                         ai_analysis, readings, ai_source, incident_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10)
                """,
                    at,
                    device_id,
                    anomaly.get("score"),
                    values.get("motor_speed"),
                    values.get("temperature"),
                    values.get("pressure"),
                    anomaly.get("ai_analysis"),
                    json.dumps(values),
                    _json(anomaly.get("ai_source")),
                    incident["id"],
                )
        except Exception as e:
            print(f"[ANOMALY] 寫入失敗：{e}")


async def save_incident(incident: dict):
    """診斷完成時呼叫：事件可能已經沒有新的異常秒了，要主動寫回診斷"""
    async with _lock:
        try:
            async with acquire() as conn:
                await _upsert_incident(conn, incident)
        except Exception as e:
            print(f"[ANOMALY] 事件更新失敗：{e}")
