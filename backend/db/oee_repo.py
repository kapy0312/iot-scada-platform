"""
OEE 的資料庫存取（需要先執行 005、006）：品號標準週期、班別設定、每小時彙整、目前狀態、狀態時間軸
"""
from datetime import date, datetime

from core.oee import HourRow, Shift
from core.production import STATE_TEXT


class OeeNotReady(Exception):
    """006 還沒執行（表或彙整不存在）"""


def _not_ready(e: Exception) -> bool:
    msg = str(e)
    return "does not exist" in msg and any(t in msg for t in ("oee_hourly", "product_specs", "shift_definitions",
                                                               "machine_status"))


async def _guard(coro):
    try:
        return await coro
    except Exception as e:
        if _not_ready(e):
            raise OeeNotReady("OEE 資料表不存在，請先執行 backend/db/migrations/005、006") from e
        raise


# ---------- 設定 ----------

async def load_cycles(conn) -> dict[int, float]:
    rows = await _guard(conn.fetch("SELECT product_id, ideal_cycle_s FROM product_specs"))
    return {r["product_id"]: float(r["ideal_cycle_s"]) for r in rows}


async def load_products(conn) -> list[dict]:
    rows = await _guard(conn.fetch(
        "SELECT product_id, name, ideal_cycle_s, updated_at, updated_by FROM product_specs ORDER BY product_id"))
    return [{**dict(r), "ideal_cycle_s": float(r["ideal_cycle_s"])} for r in rows]


async def load_shift_versions(conn) -> list[tuple[date, list[Shift]]]:
    """每一套班別（同一個 effective_from），由舊到新"""
    rows = await _guard(conn.fetch(
        "SELECT effective_from, name, start_hour, end_hour FROM shift_definitions ORDER BY effective_from, start_hour"))
    versions: dict[date, list[Shift]] = {}
    for r in rows:
        versions.setdefault(r["effective_from"], []).append(Shift(r["name"], r["start_hour"], r["end_hour"]))
    return sorted(versions.items())


def shifts_for_day(versions: list[tuple[date, list[Shift]]], day: date) -> list[Shift]:
    """那一天適用的班別：effective_from <= day 的最新一套；比第一套還早的日期沒有班別"""
    chosen: list[Shift] = []
    for eff, shifts in versions:
        if eff <= day:
            chosen = shifts
    return chosen


async def save_products(conn, products: list[dict], username: str) -> None:
    async with conn.transaction():
        for p in products:
            await conn.execute("""
                INSERT INTO product_specs (product_id, name, ideal_cycle_s, updated_at, updated_by)
                VALUES ($1, $2, $3, now(), $4)
                ON CONFLICT (product_id) DO UPDATE
                SET name = EXCLUDED.name, ideal_cycle_s = EXCLUDED.ideal_cycle_s,
                    updated_at = now(), updated_by = EXCLUDED.updated_by
            """, p["product_id"], p["name"], p["ideal_cycle_s"], username)


async def save_shifts(conn, effective_from: date, shifts: list[Shift], username: str) -> None:
    """整套取代：同一個 effective_from 的舊班別先刪掉再新增"""
    async with conn.transaction():
        await conn.execute("DELETE FROM shift_definitions WHERE effective_from = $1", effective_from)
        for s in shifts:
            await conn.execute("""
                INSERT INTO shift_definitions (effective_from, name, start_hour, end_hour, updated_by)
                VALUES ($1, $2, $3, $4, $5)
            """, effective_from, s.name.strip(), s.start_hour, s.end_hour, username)


# ---------- 資料 ----------

async def load_hourly(conn, device_id: str, start: datetime, end: datetime) -> list[HourRow]:
    rows = await _guard(conn.fetch("""
        SELECT bucket, product_id, run_s, idle_s, fault_s, changeover_s, planned_stop_s,
               data_s, unknown_s, good, ng
        FROM oee_hourly
        WHERE device_id = $1 AND bucket >= $2 AND bucket < $3
        ORDER BY bucket
    """, device_id, start, end))
    return [HourRow(
        bucket=r["bucket"], product_id=r["product_id"],
        run_s=float(r["run_s"] or 0), idle_s=float(r["idle_s"] or 0), fault_s=float(r["fault_s"] or 0),
        changeover_s=float(r["changeover_s"] or 0), planned_stop_s=float(r["planned_stop_s"] or 0),
        data_s=float(r["data_s"] or 0), unknown_s=float(r["unknown_s"] or 0),
        good=int(r["good"] or 0), ng=int(r["ng"] or 0),
    ) for r in rows]


async def current_state(conn, device_id: str) -> dict | None:
    """最新一筆機台狀態，以及這個狀態持續多久（往回找第一筆不同狀態，最多找 1 天）"""
    last = await _guard(conn.fetchrow("""
        SELECT time, state, product_id, good_total, ng_total
        FROM machine_status WHERE device_id = $1
        ORDER BY time DESC LIMIT 1
    """, device_id))
    if last is None:
        return None
    changed = await conn.fetchval("""
        SELECT max(time) FROM machine_status
        WHERE device_id = $1 AND time > $2::timestamptz - INTERVAL '1 day' AND state IS DISTINCT FROM $3::smallint
    """, device_id, last["time"], last["state"])
    # 從狀態改變之後開始算；中間斷過資料（關機、斷線超過 10 秒）就從恢復那一刻算
    since = await conn.fetchval("""
        SELECT max(time) FROM (
            SELECT time, time - lag(time) OVER (ORDER BY time) AS gap,
                   row_number() OVER (ORDER BY time) AS n
            FROM machine_status
            WHERE device_id = $1 AND time > COALESCE($2::timestamptz, $3::timestamptz - INTERVAL '1 day')
        ) x
        WHERE n = 1 OR gap > INTERVAL '10 seconds'
    """, device_id, changed, last["time"])
    return {
        "state": last["state"],
        "state_text": STATE_TEXT.get(last["state"]) if last["state"] is not None else None,
        "since": since,
        "updated_at": last["time"],
        "product_id": last["product_id"],
        "good_total": last["good_total"],
        "ng_total": last["ng_total"],
    }


async def timeline(conn, device_id: str, start: datetime, end: datetime, gap_seconds: int = 10) -> list[dict]:
    """
    狀態時間軸：把連續相同狀態的每秒紀錄合併成一段
    兩筆隔超過 gap_seconds 就斷開（中間那段前端畫成「無資料」）
    """
    rows = await _guard(conn.fetch("""
        WITH s AS (
            SELECT time, state, dt,
                   CASE WHEN state IS DISTINCT FROM lag(state) OVER w
                          OR time - lag(time) OVER w > make_interval(secs => $4)
                        THEN 1 ELSE 0 END AS brk
            FROM machine_status
            WHERE device_id = $1 AND time >= $2 AND time < $3
            WINDOW w AS (ORDER BY time)
        ), g AS (
            SELECT *, sum(brk) OVER (ORDER BY time) AS grp FROM s
        )
        SELECT state, min(time) AS start, max(time) AS last, sum(dt) AS secs
        FROM g GROUP BY grp, state ORDER BY start
    """, device_id, start, end, gap_seconds))
    return [{
        "state": r["state"],
        "state_text": STATE_TEXT.get(r["state"]) if r["state"] is not None else None,
        "start": r["start"],
        "end": r["last"],
        "seconds": round(float(r["secs"] or 0), 1),
    } for r in rows]
