"""
趨勢預警紀錄的資料庫測試（migration 007、寫入、事件連結、API、雲端同步）
- 統計（命中率、提前量）不需要資料庫
- 寫入與 API 需要 TEST_PROD_DB_URL；雲端同步需要 TEST_SYNC_LOCAL_URL 與 TEST_SYNC_CLOUD_URL
  ⚠ 會刪掉並重建 trend_warnings、anomaly_incidents：千萬不要指向正式資料庫；偵測到 timescaledb 會直接拒絕執行
執行（在 backend 資料夾）：python -m pytest tests/test_trend_db.py -q
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from routers import trend as trend_api

BACKEND = Path(__file__).resolve().parents[1]
DEV = "S7-1511T-SIM"
T0 = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


def ep(started=T0, **kw) -> dict:
    """一筆預警（時間用 unix 秒，跟 TrendTracker 送出來的一樣）"""
    base = {
        "device_id": DEV, "tag_name": "temperature", "target_level": "warning", "threshold": 85.0,
        "started_at": started.timestamp(), "ended_at": started.timestamp(), "first_eta_s": 600,
        "min_eta_s": 600, "slope_per_min": 1.0, "value_at_start": 75.0, "value_at_end": 75.0,
        "outcome": "ongoing", "reached_at": None,
    }
    base.update(kw)
    return base


# ---------- 統計（不需要資料庫） ----------

def test_統計_命中率不算中斷與進行中():
    rows = [
        {"outcome": "reached", "lead_s": 600, "first_eta_s": 540},
        {"outcome": "reached", "lead_s": 300, "first_eta_s": 320},
        {"outcome": "cleared", "lead_s": None, "first_eta_s": 900},
        {"outcome": "interrupted", "lead_s": None, "first_eta_s": 900},
        {"outcome": "ongoing", "lead_s": None, "first_eta_s": 900},
    ]
    s = trend_api.summarize(rows)
    assert s["count"] == {"ongoing": 1, "reached": 2, "cleared": 1, "interrupted": 1}
    assert s["hit_rate"] == round(2 / 3, 3)
    assert s["avg_lead_s"] == 450
    assert s["avg_eta_error_s"] == 20          # (60 + -20) / 2：平均實際比預估晚 20 秒


def test_統計_沒有資料():
    s = trend_api.summarize([])
    assert s["hit_rate"] is None and s["avg_lead_s"] is None


# ---------- 寫入與 API（TEST_PROD_DB_URL） ----------

DB_URL = os.getenv("TEST_PROD_DB_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未設定 TEST_PROD_DB_URL")

INCIDENTS_DDL = """
CREATE TABLE anomaly_incidents (
    id bigserial PRIMARY KEY, device_id text NOT NULL, started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL, records integer NOT NULL DEFAULT 1, level text NOT NULL,
    severity integer NOT NULL, peak jsonb NOT NULL, min_score double precision,
    ai_analysis text, ai_source jsonb, updated_at timestamptz NOT NULL DEFAULT now());
"""


def migration_007() -> str:
    return (BACKEND / "db/migrations/007_trend_warnings.sql").read_text(encoding="utf-8")


async def _reset(conn, with_table=True):
    import asyncpg  # noqa: F401
    if await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"):
        pytest.fail("測試資料庫裝了 timescaledb，可能是正式資料庫，拒絕執行")
    await conn.execute("DROP TABLE IF EXISTS trend_warnings, anomaly_incidents")
    await conn.execute(INCIDENTS_DDL)
    if with_table:
        await conn.execute(migration_007())
        await conn.execute(migration_007())     # 重跑不會出錯


def run_db(body, monkeypatch, with_table=True):
    import asyncpg
    from db import pool, trend_writer

    async def go():
        conn = await asyncpg.connect(DB_URL)
        try:
            await _reset(conn, with_table)
        finally:
            await conn.close()
        monkeypatch.setattr(pool, "get_database_url", lambda: DB_URL)
        monkeypatch.setattr(trend_writer, "_ids", {})
        monkeypatch.setattr(trend_writer, "_warned", False)
        conn = await asyncpg.connect(DB_URL)
        try:
            await body(conn)
        finally:
            await conn.close()
            await pool.close_pool()
    asyncio.run(go())


@needs_db
def test_開始_更新_到了_同一筆(monkeypatch):
    from db.trend_writer import save_trend

    async def body(conn):
        id1 = await save_trend(ep())
        id2 = await save_trend(ep(ended_at=T0.timestamp() + 60, min_eta_s=540, value_at_end=76.0))
        reached = T0.timestamp() + 600
        id3 = await save_trend(ep(ended_at=reached, min_eta_s=10, value_at_end=85.1,
                                  outcome="reached", reached_at=reached))
        assert id1 == id2 == id3
        r = await conn.fetchrow("SELECT * FROM trend_warnings")
        assert (r["outcome"], r["min_eta_s"], r["value_at_end"]) == ("reached", 10, 85.1)
        assert r["reached_at"] == T0 + timedelta(seconds=600) and r["incident_id"] is None
        # 結束後同一個開始時間再來一筆（例如換成危險值）會是新的一列
        id4 = await save_trend(ep(target_level="danger", threshold=100.0))
        assert id4 != id1
    run_db(body, monkeypatch)


@needs_db
def test_事件先開_預警到了時連過去(monkeypatch):
    from db.trend_writer import save_trend

    async def body(conn):
        inc = await conn.fetchval("""
            INSERT INTO anomaly_incidents (device_id, started_at, ended_at, level, severity, peak)
            VALUES ($1, $2, $3, 'warning', 3, '{}') RETURNING id
        """, DEV, T0 + timedelta(seconds=590), T0 + timedelta(seconds=620))
        # 另一台設備同時間的事件不能被連到
        await conn.execute("""
            INSERT INTO anomaly_incidents (device_id, started_at, ended_at, level, severity, peak)
            VALUES ('OTHER', $1, $1, 'warning', 3, '{}')
        """, T0 + timedelta(seconds=600))
        await save_trend(ep())
        reached = T0.timestamp() + 600
        await save_trend(ep(ended_at=reached, outcome="reached", reached_at=reached))
        assert await conn.fetchval("SELECT incident_id FROM trend_warnings") == inc
    run_db(body, monkeypatch)


@needs_db
def test_預警先到_事件建立時回頭連_清掉的不連(monkeypatch):
    from db import anomaly_writer
    from db.trend_writer import save_trend
    from core.incidents import new_incident

    async def body(conn):
        reached = T0.timestamp() + 600
        await save_trend(ep())
        await save_trend(ep(ended_at=reached, outcome="reached", reached_at=reached))
        await save_trend(ep(started=T0 + timedelta(seconds=10), tag_name="vibration"))
        await save_trend(ep(started=T0 + timedelta(seconds=10), tag_name="vibration", outcome="cleared"))
        inc = new_incident(DEV, T0 + timedelta(seconds=603))
        inc["level"], inc["severity"] = "warning", 3
        await anomaly_writer.save_incident(inc)
        rows = await conn.fetch("SELECT tag_name, incident_id FROM trend_warnings ORDER BY tag_name")
        assert [(r["tag_name"], r["incident_id"]) for r in rows] == [("temperature", inc["id"]), ("vibration", None)]
        # 第二次更新同一筆預警（incident_id 是 None）不會把連好的蓋掉
        await save_trend(ep(ended_at=reached, outcome="reached", reached_at=reached, threshold=85.0))
        assert await conn.fetchval("SELECT incident_id FROM trend_warnings WHERE tag_name = 'temperature'") == inc["id"]
    run_db(body, monkeypatch)


@needs_db
def test_後端重啟_沒結束的記為中斷(monkeypatch):
    from db.trend_writer import close_stale, save_trend

    async def body(conn):
        await save_trend(ep())
        await save_trend(ep(started=T0 + timedelta(seconds=5), tag_name="vibration", outcome="cleared"))
        await close_stale()
        rows = await conn.fetch("SELECT tag_name, outcome FROM trend_warnings ORDER BY tag_name")
        assert [(r["tag_name"], r["outcome"]) for r in rows] == [("temperature", "interrupted"), ("vibration", "cleared")]
    run_db(body, monkeypatch)


@needs_db
def test_檢查條件(monkeypatch):
    import asyncpg

    async def body(conn):
        cols = ("device_id, tag_name, target_level, threshold, started_at, ended_at, first_eta_s, min_eta_s, "
                "slope_per_min, value_at_start, value_at_end, outcome, reached_at")
        base = [DEV, "temperature", "warning", 85.0, T0, T0, 1, 1, 1.0, 70.0, 70.0, "ongoing", None]
        for idx, bad in ((2, "high"), (11, "done")):
            vals = list(base)
            vals[idx] = bad
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(f"INSERT INTO trend_warnings ({cols}) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)", *vals)
        vals = list(base)
        vals[11] = "reached"     # reached 一定要有 reached_at
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(f"INSERT INTO trend_warnings ({cols}) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)", *vals)
    run_db(body, monkeypatch)


@needs_db
def test_沒有007時不出錯只提醒一次(monkeypatch, capsys):
    from db import anomaly_writer
    from db.trend_writer import close_stale, save_trend
    from core.incidents import new_incident

    async def body(conn):
        assert await save_trend(ep()) is None
        assert await save_trend(ep()) is None
        await close_stale()
        inc = new_incident(DEV, T0)
        await anomaly_writer.save_incident(inc)     # 事件照樣寫得進去
        assert inc["id"] is not None
        out = await trend_api.trend_warnings(device_id=DEV, hours=24, limit=10)
        assert out["available"] is False and out["warnings"] == []
    run_db(body, monkeypatch, with_table=False)
    assert capsys.readouterr().out.count("trend_warnings 表不存在") == 1


@needs_db
def test_API_預警紀錄與事件的事前預警(monkeypatch):
    from db.trend_writer import save_trend
    from routers.history import get_incidents

    async def body(conn):
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=20)
        inc = await conn.fetchval("""
            INSERT INTO anomaly_incidents (device_id, started_at, ended_at, level, severity, peak)
            VALUES ($1, $2, $2, 'warning', 3, '{}') RETURNING id
        """, DEV, start + timedelta(seconds=602))
        await save_trend(ep(started=start))
        reached = start.timestamp() + 600
        await save_trend(ep(started=start, ended_at=reached, outcome="reached", reached_at=reached))
        await save_trend(ep(started=start + timedelta(minutes=1), tag_name="vibration"))
        await save_trend(ep(started=start + timedelta(minutes=1), tag_name="vibration", outcome="cleared"))

        out = await trend_api.trend_warnings(device_id=DEV, hours=24, limit=10)
        assert out["available"] is True and out["count"] == 2
        temp = next(w for w in out["warnings"] if w["tag_name"] == "temperature")
        assert temp["lead_s"] == 600 and temp["incident_id"] == inc
        assert out["summary"]["hit_rate"] == 0.5 and out["summary"]["avg_lead_s"] == 600
        # 漏報率從第一筆預警開始算：預警功能上線前的超標事件不算
        await conn.execute("""
            INSERT INTO anomaly_incidents (device_id, started_at, ended_at, level, severity, peak)
            VALUES ($1, $2, $2, 'danger', 4, '{}')
        """, DEV, start - timedelta(minutes=5))
        out = await trend_api.trend_warnings(device_id=DEV, hours=24, limit=10)
        m = out["misses"]
        assert (m["excluded_before_feature"], m["incidents_over_line"], m["warned"], m["miss_rate"]) == (1, 1, 1, 0.0)
        assert m["counted_since"] is not None

        incs = await get_incidents(device_id=DEV, hours=24, limit=10)
        pre = incs["incidents"][0]["pre_warnings"]
        assert [(p["tag_name"], p["lead_s"]) for p in pre] == [("temperature", 600)]
    run_db(body, monkeypatch)


def test_API_目前狀態(monkeypatch):
    import core.plc_simulator as ps
    monkeypatch.setattr(ps, "latest_trend", None)
    assert asyncio.run(trend_api.trend_current())["status"] == "no_data"
    monkeypatch.setattr(ps, "latest_trend", {"status": "active", "tags": {}})
    assert asyncio.run(trend_api.trend_current())["status"] == "active"


# ---------- 雲端同步（TEST_SYNC_LOCAL_URL、TEST_SYNC_CLOUD_URL） ----------

LOCAL_URL = os.getenv("TEST_SYNC_LOCAL_URL")
CLOUD_URL = os.getenv("TEST_SYNC_CLOUD_URL")
needs_sync = pytest.mark.skipif(not (LOCAL_URL and CLOUD_URL), reason="未設定 TEST_SYNC_LOCAL_URL 與 TEST_SYNC_CLOUD_URL")


def _sync_state_ddl() -> str:
    sql = (BACKEND / "db/migrations/003_cagg_10s.sql").read_text(encoding="utf-8")
    start = sql.index("CREATE TABLE IF NOT EXISTS sync_state")
    return sql[start: sql.index(");", start) + 2]


def _cloud_004() -> str:
    """雲端 004 去掉 GRANT（測試資料庫沒有 edge_writer、mcp_reader 帳號）"""
    sql = (BACKEND / "db/cloud/004_trend.sql").read_text(encoding="utf-8")
    return ";".join(s for s in sql.split(";") if "GRANT" not in s and "role_table_grants" not in s)


def run_sync(body, local_table=True, cloud_table=True):
    import asyncpg

    async def go():
        local = await asyncpg.connect(LOCAL_URL)
        cloud = await asyncpg.connect(CLOUD_URL)
        try:
            for conn in (local, cloud):
                if await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"):
                    pytest.fail("測試資料庫裝了 timescaledb，看起來是正式資料庫：拒絕執行（會刪表）")
            await local.execute("DROP TABLE IF EXISTS trend_warnings, sync_state")
            await local.execute(_sync_state_ddl())
            if local_table:
                await local.execute(migration_007())
            await cloud.execute("DROP TABLE IF EXISTS trend_warnings")
            if cloud_table:
                await cloud.execute(_cloud_004())
            await body(local, cloud)
        finally:
            # 清掉，不影響 test_cloud_sync（它預期雲端沒有這張表）
            await local.execute("DROP TABLE IF EXISTS trend_warnings")
            await cloud.execute("DROP TABLE IF EXISTS trend_warnings")
            await local.close()
            await cloud.close()
    asyncio.run(go())


async def _insert_local(local, device=DEV, tag="temperature", outcome="ongoing", updated=None):
    return await local.fetchval("""
        INSERT INTO trend_warnings (device_id, tag_name, target_level, threshold, started_at, ended_at,
            first_eta_s, min_eta_s, slope_per_min, value_at_start, value_at_end, outcome, reached_at, updated_at)
        VALUES ($1, $2, 'warning', 85, now() - interval '5 minutes', now() - interval '1 minute',
            600, 500, 1.0, 75, 80, $3, CASE WHEN $3 = 'reached' THEN now() END, $4)
        RETURNING id
    """, device, tag, outcome, updated or datetime.now(timezone.utc) - timedelta(seconds=30))


@needs_sync
def test_同步_新增更新都送_排除MOCK_舊版本不蓋新版本():
    from sync import cloud_sync as cs

    async def body(local, cloud):
        tid = await _insert_local(local)
        await _insert_local(local, device="S7-1511T-MOCK")
        n, more = await cs.sync_trend_once(local, cloud)
        assert (n, more) == (1, False)
        assert await cloud.fetchval("SELECT outcome FROM trend_warnings WHERE id = $1", tid) == "ongoing"

        # 本地結束（reached），下一輪送更新
        await local.execute("""UPDATE trend_warnings SET outcome = 'reached', reached_at = now(),
                               updated_at = now() - interval '10 seconds' WHERE id = $1""", tid)
        await cs.sync_trend_once(local, cloud)
        assert await cloud.fetchval("SELECT outcome FROM trend_warnings WHERE id = $1", tid) == "reached"

        # 重送舊版本（指標往回調）不會把雲端改回 ongoing
        old = await local.fetchrow("SELECT * FROM trend_warnings WHERE id = $1", tid)
        stale = tuple({**dict(old), "outcome": "ongoing", "reached_at": None,
                       "updated_at": old["updated_at"] - timedelta(minutes=1)}[c] for c in cs.TREND_COLS)
        await cs.upsert_trend(cloud, [stale])
        assert await cloud.fetchval("SELECT outcome FROM trend_warnings WHERE id = $1", tid) == "reached"
        assert await cloud.fetchval("SELECT count(*) FROM trend_warnings") == 1
    run_sync(body)


@needs_sync
def test_同步_任一邊沒有表就略過():
    from sync import cloud_sync as cs

    async def no_local(local, cloud):
        assert await cs.trend_ready(local, cloud) is False

    async def no_cloud(local, cloud):
        assert await cs.trend_ready(local, cloud) is False

    async def both(local, cloud):
        assert await cs.trend_ready(local, cloud) is True
    run_sync(no_local, local_table=False)
    run_sync(no_cloud, cloud_table=False)
    run_sync(both)
