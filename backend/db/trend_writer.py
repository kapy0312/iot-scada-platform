"""
趨勢預警寫入 trend_warnings（需要先執行 db/migrations/007_trend_warnings.sql）

一次預警一列：開始時新增、預警中每 10 秒更新、結束時寫結果
- 寫入照順序執行（同一把鎖）：開始那筆還沒拿到 id 前，更新不能先跑
- 表還沒建時只提醒一次，不影響即時推估與畫面
- 真的到了（reached）要連到事件，兩邊都在資料庫裡找對方，先寫的找不到、後寫的一定找得到：
  預警先到 → 事件建立時回頭連（link_incident）；事件先開 → 預警寫 reached 時找進行中的事件
"""
import asyncio
from datetime import datetime, timezone

from db.pool import acquire

_lock = asyncio.Lock()
_ids: dict[tuple, int] = {}   # (device_id, tag_name, target_level, started_at) → id
_warned = False
LINK_WINDOW_SECONDS = 120     # 事件開始前後 2 分鐘內「到了」的預警，算是這次事件的事前預警

COLS = ("device_id", "tag_name", "target_level", "threshold", "started_at", "ended_at", "first_eta_s",
        "min_eta_s", "slope_per_min", "value_at_start", "value_at_end", "outcome", "reached_at", "incident_id")


def _ts(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromtimestamp(float(v), tz=timezone.utc)


def _key(ep: dict) -> tuple:
    return ep["device_id"], ep["tag_name"], ep["target_level"], float(ep["started_at"])


def _row(ep: dict) -> list:
    vals = []
    for c in COLS:
        v = ep.get(c)
        if c in ("started_at", "ended_at", "reached_at"):
            v = _ts(v)
        elif c in ("first_eta_s", "min_eta_s") and v is not None:
            v = int(v)
        vals.append(v)
    return vals


def _missing_table(e: Exception) -> bool:
    global _warned
    if "trend_warnings" in str(e) and "does not exist" in str(e):
        if not _warned:
            print("[DB] trend_warnings 表不存在，請先執行 db/migrations/007_trend_warnings.sql（預警照常顯示，只是不留紀錄）")
            _warned = True
        return True
    return False


async def save_trend(ep: dict) -> int | None:
    """新增或更新一筆預警；回傳 id（寫不進去時回 None）"""
    async with _lock:
        key = _key(ep)
        try:
            async with acquire() as conn:
                tid = _ids.get(key)
                if tid is None:
                    tid = await conn.fetchval(f"""
                        INSERT INTO trend_warnings ({", ".join(COLS)})
                        VALUES ({", ".join(f"${i + 1}" for i in range(len(COLS)))})
                        RETURNING id
                    """, *_row(ep))
                    _ids[key] = tid
                else:
                    # $1＝id，$2 起依序是 COLS[5:]；incident_id 用 COALESCE：事件那邊先連好的，不要被這邊的 NULL 蓋掉
                    sets = ", ".join(
                        f"{c} = COALESCE(${i + 2}, {c})" if c == "incident_id" else f"{c} = ${i + 2}"
                        for i, c in enumerate(COLS[5:]))
                    await conn.execute(f"UPDATE trend_warnings SET {sets}, updated_at = now() WHERE id = $1",
                                       tid, *_row(ep)[5:])
                if ep.get("outcome") == "reached" and ep.get("incident_id") is None:
                    await conn.execute("""
                        UPDATE trend_warnings t SET incident_id = i.id, updated_at = now()
                        FROM (SELECT id FROM anomaly_incidents
                              WHERE device_id = $2
                                AND started_at <= $3::timestamptz + make_interval(secs => $4::float8)
                                AND ended_at   >= $3::timestamptz - make_interval(secs => $4::float8)
                              ORDER BY started_at DESC LIMIT 1) i
                        WHERE t.id = $1 AND t.incident_id IS NULL
                    """, tid, ep["device_id"], _ts(ep["reached_at"]), float(LINK_WINDOW_SECONDS))
            if ep.get("outcome", "ongoing") != "ongoing":
                _ids.pop(key, None)
            return tid
        except Exception as e:
            if not _missing_table(e):
                print(f"[DB] 趨勢預警寫入失敗：{type(e).__name__}: {e}")
            return None


async def link_incident(conn, device_id: str, incident_id: int, started_at: datetime) -> None:
    """事件剛建立時呼叫：把前後 2 分鐘內「到了」但還沒連到事件的預警連過來（表不存在時不做事）"""
    try:
        await conn.execute("""
            UPDATE trend_warnings SET incident_id = $1, updated_at = now()
            WHERE device_id = $2 AND outcome = 'reached' AND incident_id IS NULL
              AND reached_at BETWEEN $3::timestamptz - make_interval(secs => $4::float8) AND $3::timestamptz + make_interval(secs => $4::float8)
        """, incident_id, device_id, started_at, float(LINK_WINDOW_SECONDS))
    except Exception as e:
        if not _missing_table(e):
            print(f"[DB] 預警連結事件失敗：{type(e).__name__}: {e}")


async def close_stale() -> None:
    """後端啟動時呼叫：上次沒結束的預警（後端停掉、當機）一律記為 interrupted"""
    try:
        async with acquire() as conn:
            n = await conn.fetchval("""
                WITH u AS (
                    UPDATE trend_warnings SET outcome = 'interrupted', updated_at = now()
                    WHERE outcome = 'ongoing' RETURNING 1
                ) SELECT count(*) FROM u
            """)
        if n:
            print(f"[TREND] 上次沒結束的預警 {n} 筆，記為 interrupted")
    except Exception as e:
        if not _missing_table(e):
            print(f"[DB] 整理未結束預警失敗：{type(e).__name__}: {e}")
