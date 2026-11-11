"""
OEE 計算與 API 的測試
- 前半不需要資料庫：班別、可用率／表現率／良率、各種提醒
- 後半要一個「空的、沒裝 TimescaleDB 的」PostgreSQL 測試資料庫（TEST_PROD_DB_URL）才會執行：
  每小時彙整改用一般 VIEW 代替（同一段 SELECT，只把 time_bucket 換成 date_trunc），測 API 的計算與存取
  ⚠ 會刪掉並重建 machine_status、product_specs、shift_definitions：千萬不要指向正式資料庫
  TimescaleDB 專屬的部分（continuous aggregate、自動整理）要在本機驗證
執行（在 backend 資料夾）：python -m pytest tests -q
"""
import asyncio
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.oee import SHIFT_TZ, HourRow, Shift, locate_shift, shift_summary, shift_window, summarize, validate_shifts
from core.production import FAULT, PLANNED_STOP, RUN

BACKEND = Path(__file__).resolve().parents[1]
DAY = Shift("早班", 8, 20)
NIGHT = Shift("晚班", 20, 8)
TW = SHIFT_TZ


def tw(*args) -> datetime:
    return datetime(*args, tzinfo=TW)


# ---------- 班別 ----------

def test_跨午夜的班屬於開始那天():
    start, end = shift_window(NIGHT, date(2026, 11, 11))
    assert start == tw(2026, 11, 11, 20) and end == tw(2026, 11, 12, 8)


def test_凌晨兩點算前一天的晚班():
    assert locate_shift(lambda d: [DAY, NIGHT], tw(2026, 11, 12, 2)) == (NIGHT, date(2026, 11, 11))
    assert locate_shift(lambda d: [DAY, NIGHT], tw(2026, 11, 12, 8)) == (DAY, date(2026, 11, 12))


def test_不在任何班別():
    assert locate_shift(lambda d: [DAY], tw(2026, 11, 11, 22)) is None


def test_不同日期適用不同套班別():
    """11/12 起改三班制；11/12 01:00 仍屬於 11/11 舊制的晚班"""
    three = [Shift("A", 8, 16), Shift("B", 16, 0), Shift("C", 0, 8)]
    pick = lambda d: three if d >= date(2026, 11, 12) else [DAY, NIGHT]
    assert locate_shift(pick, tw(2026, 11, 12, 1)) == (NIGHT, date(2026, 11, 11))
    assert locate_shift(pick, tw(2026, 11, 12, 9)) == (three[0], date(2026, 11, 12))


def test_班別檢查():
    assert validate_shifts([DAY, NIGHT]) is None
    assert validate_shifts([Shift("全天", 8, 8)]) is None                       # 24 小時一班
    assert "重疊" in validate_shifts([DAY, Shift("加班", 19, 22)])
    assert "重複" in validate_shifts([DAY, Shift("早班", 20, 8)])
    assert validate_shifts([]) is not None


# ---------- OEE 計算 ----------

def _row(hour, **kw):
    return HourRow(bucket=tw(2026, 11, 11, hour), product_id=kw.pop("product_id", 1), **kw)


# 早班到 10:30：8 點整小時運轉、9 點故障半小時、10 點計畫停機 15 分鐘後斷線
ROWS = [
    _row(8, run_s=3600, data_s=3600, good=590, ng=10),
    _row(9, run_s=1800, fault_s=1800, data_s=3600, good=280),
    _row(10, planned_stop_s=900, data_s=900),
]


def test_一班的OEE():
    r = summarize(ROWS, {1: 6.0}, window_s=9000)
    assert r["availability"] == pytest.approx(5400 / 7200)          # 運轉 ÷（有資料 − 計畫停機）
    assert r["performance"] == pytest.approx(880 * 6 / 5400)        # 880 件 × 6 秒 ÷ 運轉秒
    assert r["quality"] == pytest.approx(870 / 880)
    assert r["oee"] == pytest.approx(0.75 * (5280 / 5400) * (870 / 880))
    assert r["coverage"] == pytest.approx(0.9)
    assert r["losses"] == {"fault_s": 1800, "changeover_s": 0, "idle_s": 0}
    assert r["flags"] == []


def test_資料覆蓋率太低要提醒():
    r = summarize(ROWS[:1], {1: 6.0}, window_s=43200)
    assert "low_coverage" in r["flags"]


def test_表現率最多100且提醒標準週期可能設錯():
    r = summarize([_row(8, run_s=3600, data_s=3600, good=1000)], {1: 6.0}, window_s=3600)
    assert r["performance"] == 1.0 and "performance_over_100" in r["flags"]


def test_品號沒設標準週期算不出表現率():
    r = summarize([_row(8, run_s=3600, data_s=3600, good=600, product_id=7)], {1: 6.0}, window_s=3600)
    assert r["performance"] is None and r["oee"] is None
    assert r["missing_products"] == [7] and "missing_product_spec" in r["flags"]


def test_多品號各用各的標準週期():
    rows = [_row(8, run_s=1800, data_s=1800, good=300), _row(8, run_s=1800, data_s=1800, good=150, product_id=2)]
    r = summarize(rows, {1: 6.0, 2: 12.0}, window_s=3600)
    assert r["performance"] == pytest.approx((300 * 6 + 150 * 12) / 3600)


def test_該開工卻完全沒動OEE是0():
    r = summarize([_row(8, idle_s=3600, data_s=3600)], {1: 6.0}, window_s=3600)
    assert r["availability"] == 0 and r["oee"] == 0


def test_整段計畫停機不算OEE():
    r = summarize([_row(8, planned_stop_s=3600, data_s=3600)], {1: 6.0}, window_s=3600)
    assert r["availability"] is None and r["oee"] is None


def test_沒有資料():
    r = summarize([], {1: 6.0}, window_s=3600)
    assert r["oee"] is None and r["coverage"] == 0 and "low_coverage" in r["flags"]


def test_舊版PLC沒有狀態要提醒():
    r = summarize([_row(8, unknown_s=3600)], {1: 6.0}, window_s=3600)
    assert "no_state" in r["flags"] and r["coverage"] == 1.0


def test_進行中的班只算到現在並列出每小時():
    now = tw(2026, 11, 11, 10, 30)
    r = shift_summary(DAY, date(2026, 11, 11), ROWS, {1: 6.0}, now=now, hourly=True)
    assert r["in_progress"] is True and r["window_s"] == 9000
    assert [h["hour"][11:16] for h in r["hourly"]] == ["08:00", "09:00", "10:00"]
    assert r["hourly"][0]["oee"] == pytest.approx(1.0 * 1.0 * 590 / 600)
    assert r["hourly"][2]["coverage"] == pytest.approx(0.5)          # 10 點只過了 30 分鐘、有 15 分鐘資料


# ---------- 需要資料庫：API ----------

DB_URL = os.getenv("TEST_PROD_DB_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未設定 TEST_PROD_DB_URL")
DEV = "S7-1511T-SIM"
NOW = tw(2026, 11, 11, 10, 30)


def _statements(name: str) -> list[str]:
    sql = (BACKEND / "db/migrations" / name).read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


def _plain_pg_ddl() -> list[str]:
    """005＋006 的語句改成一般 PostgreSQL 可以跑的版本"""
    out = []
    for s in _statements("005_machine_status.sql") + _statements("006_oee.sql"):
        head = s.upper()
        if head.startswith(("SELECT", "CALL", "BEGIN", "COMMIT")):
            continue
        if "CREATE MATERIALIZED VIEW" in head:
            s = re.sub(r"CREATE MATERIALIZED VIEW IF NOT EXISTS oee_hourly\s+WITH \([^)]*\) AS",
                       "CREATE OR REPLACE VIEW oee_hourly AS", s)
            s = s.replace("time_bucket(INTERVAL '1 hour', time)", "date_trunc('hour', time)")
            s = s.replace("WITH NO DATA", "")
        out.append(s)
    return out


def _seed_rows():
    """每 5 秒一筆，跟前半 ROWS 同一個情境：8 點運轉 600 件（前 10 件 NG）、9 點故障 30 分＋運轉 280 件、10 點計畫停機 15 分"""
    rows = []

    def add(start, n, state, parts=0, ng_first=0):
        made = 0
        for i in range(n):
            t = start + timedelta(seconds=5 * i)
            part = (i + 1) * parts // n - i * parts // n      # parts 件平均分散在 n 筆裡
            made += part
            ng = 1 if part and made <= ng_first else 0
            rows.append((t, DEV, state, state == RUN, 1, None, None, part - ng, ng, 5.0))

    add(tw(2026, 11, 11, 8), 720, RUN, parts=600, ng_first=10)
    add(tw(2026, 11, 11, 9), 360, FAULT)
    add(tw(2026, 11, 11, 9, 30), 360, RUN, parts=280)
    add(tw(2026, 11, 11, 10), 180, PLANNED_STOP)
    return rows


@pytest.fixture
def client(monkeypatch):
    import asyncpg
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.auth import require_operator
    from db import pool
    from routers import oee as oee_router

    async def setup():
        conn = await asyncpg.connect(DB_URL)
        try:
            if await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"):
                pytest.fail("測試資料庫裝了 timescaledb，可能是正式資料庫，拒絕執行")
            await conn.execute("DROP VIEW IF EXISTS oee_hourly; DROP TABLE IF EXISTS machine_status, "
                               "product_specs, shift_definitions; "
                               "CREATE TABLE IF NOT EXISTS sync_state (target text PRIMARY KEY)")
            for s in _plain_pg_ddl():
                await conn.execute(s)
            await conn.copy_records_to_table("machine_status", records=_seed_rows(), columns=[
                "time", "device_id", "state", "motor_enable", "product_id", "good_total", "ng_total",
                "good_delta", "ng_delta", "dt"])
        finally:
            await conn.close()

    asyncio.run(setup())
    monkeypatch.setattr(pool, "get_database_url", lambda: DB_URL)
    monkeypatch.setattr(oee_router, "_now", lambda: NOW.astimezone(timezone.utc))
    audits = []

    async def fake_audit(action, result, **kw):
        audits.append((action, result, kw))

    monkeypatch.setattr(oee_router, "write_audit", fake_audit)
    app = FastAPI()
    app.include_router(oee_router.router)
    app.dependency_overrides[require_operator] = lambda: {"username": "kevin", "role": "operator"}
    with TestClient(app) as c:
        c.audits = audits
        yield c
    asyncio.run(pool.close_pool())


@needs_db
def test_API_本班OEE(client):
    r = client.get("/api/oee/current", params={"device_id": DEV}).json()
    s = r["shift"]
    assert s["shift"] == "早班" and s["in_progress"] is True
    assert (s["good"], s["ng"]) == (870, 10)
    assert s["run_s"] == 5400 and s["fault_s"] == 1800 and s["planned_stop_s"] == 900
    assert s["availability"] == pytest.approx(0.75)
    assert s["oee"] == pytest.approx(0.75 * (5280 / 5400) * (870 / 880))
    assert len(s["hourly"]) == 3
    st = r["state"]
    assert st["state"] == PLANNED_STOP and st["state_text"] == "計畫停機"
    assert st["since"].startswith("2026-11-11T02:00:00")              # 10:00 台北＝02:00 UTC
    assert st["duration_s"] == 1800


@needs_db
def test_API_最近幾天各班(client):
    r = client.get("/api/oee/shifts", params={"device_id": DEV, "days": 2}).json()["shifts"]
    assert [(x["day"], x["shift"]) for x in r] == [("2026-11-11", "早班"), ("2026-11-10", "晚班"), ("2026-11-10", "早班")]
    assert r[1]["oee"] is None and r[1]["coverage"] == 0              # 前一晚沒資料


@needs_db
def test_API_狀態時間軸(client):
    seg = client.get("/api/oee/timeline", params={"device_id": DEV}).json()["segments"]
    assert [x["state"] for x in seg] == [RUN, FAULT, RUN, PLANNED_STOP]
    assert [x["seconds"] for x in seg] == [3600, 1800, 1800, 900]


@needs_db
def test_API_時間軸範圍檢查(client):
    bad = client.get("/api/oee/timeline", params={"start": "2026-11-10T00:00:00+08:00", "end": "2026-11-12T00:00:00+08:00"})
    assert bad.status_code == 422


@needs_db
def test_API_設定品號(client):
    r = client.put("/api/oee/products", json=[{"product_id": 1, "name": "預設品號", "ideal_cycle_s": 5.5},
                                              {"product_id": 2, "name": "B 型", "ideal_cycle_s": 12}])
    assert r.status_code == 200
    cfg = client.get("/api/oee/config").json()
    assert [(p["product_id"], p["ideal_cycle_s"]) for p in cfg["products"]] == [(1, 5.5), (2, 12.0)]
    action, result, kw = client.audits[-1]
    assert (action, result) == ("oee.products", "ok")
    assert kw["before"]["products"][0]["ideal_cycle_s"] == 6.0       # 操作紀錄有改之前的值
    assert client.put("/api/oee/products", json=[{"product_id": 1, "name": "x", "ideal_cycle_s": 0}]).status_code == 422


@needs_db
def test_API_設定班別從明天生效(client):
    body = {"effective_from": "2026-11-12",
            "shifts": [{"name": "A", "start_hour": 8, "end_hour": 16}, {"name": "B", "start_hour": 16, "end_hour": 0},
                       {"name": "C", "start_hour": 0, "end_hour": 8}]}
    assert client.put("/api/oee/shifts", json=body).status_code == 200
    cfg = client.get("/api/oee/config").json()
    assert [s["name"] for s in cfg["shifts"]["shifts"]] == ["早班", "晚班"]     # 今天仍是舊制
    assert cfg["upcoming_shifts"][0]["effective_from"] == "2026-11-12"
    # 今天的 OEE 不受影響
    assert client.get("/api/oee/current", params={"device_id": DEV}).json()["shift"]["shift"] == "早班"


@needs_db
def test_API_班別重疊拒絕(client):
    body = {"shifts": [{"name": "早班", "start_hour": 8, "end_hour": 20}, {"name": "加班", "start_hour": 19, "end_hour": 22}]}
    r = client.put("/api/oee/shifts", json=body)
    assert r.status_code == 422 and "重疊" in r.json()["detail"]
    assert client.audits == []


@needs_db
def test_API_還沒跑006回503(client, monkeypatch):
    async def drop():
        import asyncpg
        conn = await asyncpg.connect(DB_URL)
        await conn.execute("DROP VIEW oee_hourly; DROP TABLE product_specs, shift_definitions")
        await conn.close()
    asyncio.run(drop())
    r = client.get("/api/oee/current")
    assert r.status_code == 503 and "006" in r.json()["detail"]


def test_換班別那天重疊的新班不列():
    three = [Shift("A", 8, 16), Shift("B", 16, 0), Shift("C", 0, 8)]
    pick = lambda d: three if d >= date(2026, 11, 12) else [DAY, NIGHT]
    from core.oee import is_effective
    assert is_effective(pick, NIGHT, date(2026, 11, 11)) is True
    assert is_effective(pick, three[2], date(2026, 11, 12)) is False    # 被舊制晚班蓋掉
    assert is_effective(pick, three[0], date(2026, 11, 12)) is True
    assert is_effective(pick, three[2], date(2026, 11, 13)) is True


def test_跨來源的PUT請求不會被CORS擋下():
    """前端（3000）與後端（8000）不同 port：瀏覽器送 PUT 前會先問後端允不允許，沒列 PUT 就整個被擋"""
    from fastapi.testclient import TestClient

    import main
    r = TestClient(main.app).options("/api/oee/products", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type,x-scada-request",
    })
    assert r.status_code == 200 and "PUT" in r.headers["access-control-allow-methods"]
