"""
門檻覆寫的測試（migration 008、檢查規則、套用到規格、API）
- 檢查規則與套用不需要資料庫
- 寫入與 API 需要 TEST_PROD_DB_URL（會刪掉並重建 tag_limits；偵測到 timescaledb 會拒絕執行）
執行（在 backend 資料夾）：python -m pytest tests/test_limits.py -q
"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import auth, limits
from core.device_profile import evaluate, get_profile
from core.trend import TrendTracker

BACKEND = Path(__file__).resolve().parents[1]
DEV = "S7-1511T-SIM"
TEMP_OK = {"normal": [60, 72], "warning": 80, "danger": 95, "scale": [30, 110]}


@pytest.fixture(autouse=True)
def restore_defaults():
    limits.apply_rows([])
    yield
    limits.apply_rows([])


def row(tag="temperature", **kw):
    base = {"device_model": "S7-1511T", "tag_name": tag, "normal_lo": 60.0, "normal_hi": 72.0, "warning": 80.0,
            "danger": 95.0, "scale_lo": 30.0, "scale_hi": 110.0, "updated_by": "kevin",
            "updated_at": datetime(2026, 11, 12, 2, 40, tzinfo=timezone.utc)}
    base.update(kw)
    return base


# ---------- 不需要資料庫 ----------

@pytest.mark.parametrize("bad, msg", [
    ({**TEMP_OK, "normal": [72, 60]}, "正常下限要小於正常上限"),
    ({**TEMP_OK, "warning": 70}, "警戒值要大於正常上限"),
    ({**TEMP_OK, "danger": 80}, "危險值要大於警戒值"),
    ({**TEMP_OK, "scale": [65, 110]}, "正常下限不能小於刻度最小值"),
    ({**TEMP_OK, "scale": [30, 90]}, "刻度最大值不能小於危險值"),
    ({**TEMP_OK, "warning": float("nan")}, "數值不能是空白"),
    ({"normal": [60], "warning": 80, "danger": 95, "scale": [30, 110]}, "欄位不完整"),
])
def test_檢查規則(bad, msg):
    with pytest.raises(limits.LimitsError, match=msg):
        limits.validate(bad)


def test_合法的門檻整理成浮點數():
    assert limits.validate({**TEMP_OK, "normal": ["60", 72]})["normal"] == [60.0, 72.0]


def test_套用覆寫後所有地方都看到新值_清掉後回到預設():
    v0 = limits.version
    spec = get_profile(DEV)["tags"]["temperature"]
    assert spec["warning"] == 85
    assert limits.apply_rows([row()]) is True
    assert spec["warning"] == 80 and spec["normal"] == [60.0, 72.0] and spec["scale"] == [30.0, 110.0]
    assert evaluate(spec, 82) == "warning"                     # 用新的警戒值判斷
    assert limits.version != v0
    assert limits.meta("S7-1511T-MOCK", "temperature")["updated_by"] == "kevin"
    assert get_profile("S7-1511T-MOCK")["tags"]["pressure"]["warning"] == 7.0   # 沒改的訊號不受影響
    assert limits.apply_rows([row()]) is False                 # 同樣的內容不算變更
    limits.apply_rows([])
    assert spec["warning"] == 85 and limits.version == v0
    assert limits.defaults(DEV)["temperature"]["warning"] == 85


def test_程式裡已經沒有的訊號_舊覆寫忽略():
    limits.apply_rows([row(tag="no_such_tag")])
    assert get_profile(DEV)["tags"]["temperature"]["warning"] == 85


def test_預警中改了門檻_結束並用新的線重新判斷():
    tr = TrendTracker()
    specs = get_profile(DEV)["tags"]
    events = []
    for i in range(500):   # 每分鐘 +1：第 300 多秒開始預警
        vals = {"motor_speed": 1480, "temperature": 70 + i / 60, "pressure": 5, "vibration": 1.2, "current": 24}
        events += tr.update(DEV, vals, specs, float(i), True)
    assert [e["type"] for e in events if e["type"] != "update"] == ["start"]
    limits.apply_rows([row(normal_lo=60.0, normal_hi=75.0, warning=90.0, danger=100.0, scale_lo=40.0, scale_hi=120.0)])
    later = []
    for i in range(500, 700):
        vals = {"motor_speed": 1480, "temperature": 70 + i / 60, "pressure": 5, "vibration": 1.2, "current": 24}
        later += tr.update(DEV, vals, specs, float(i), True)
    ends = [e["episode"] for e in later if e["type"] == "end"]
    starts = [e["episode"] for e in later if e["type"] == "start"]
    assert ends[0]["outcome"] == "interrupted" and ends[0]["threshold"] == 85
    assert starts and starts[0]["threshold"] == 90


# ---------- 資料庫（TEST_PROD_DB_URL） ----------

DB_URL = os.getenv("TEST_PROD_DB_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未設定 TEST_PROD_DB_URL")


def run_db(body, monkeypatch, with_table=True):
    import asyncpg
    from db import pool

    async def go():
        conn = await asyncpg.connect(DB_URL)
        try:
            if await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"):
                pytest.fail("測試資料庫裝了 timescaledb，可能是正式資料庫，拒絕執行")
            await conn.execute("DROP TABLE IF EXISTS tag_limits")
            if with_table:
                sql = (BACKEND / "db/migrations/008_tag_limits.sql").read_text(encoding="utf-8")
                await conn.execute(sql)
                await conn.execute(sql)     # 重跑不會出錯
        finally:
            await conn.close()
        monkeypatch.setattr(pool, "get_database_url", lambda: DB_URL)
        conn = await asyncpg.connect(DB_URL)
        try:
            await body(conn)
        finally:
            await conn.execute("DROP TABLE IF EXISTS tag_limits")
            await conn.close()
            await pool.close_pool()
    asyncio.run(go())


@needs_db
def test_寫入_載入_恢復預設(monkeypatch):
    async def body(conn):
        before, after = await limits.save(DEV, "temperature", TEMP_OK, "kevin")
        assert before["warning"] == 85 and after["warning"] == 80
        r = await conn.fetchrow("SELECT * FROM tag_limits")
        assert (r["device_model"], r["updated_by"], r["danger"]) == ("S7-1511T", "kevin", 95)
        # 另一個程式（例如 MCP）重新載入也看到一樣的值
        limits.apply_rows([])
        await limits.load(conn)
        assert get_profile(DEV)["tags"]["temperature"]["warning"] == 80
        before, after = await limits.reset(DEV, "temperature")
        assert (before["warning"], after["warning"]) == (80, 85)
        assert await conn.fetchval("SELECT count(*) FROM tag_limits") == 0
    run_db(body, monkeypatch)


@needs_db
def test_資料庫最後一道檢查(monkeypatch):
    import asyncpg

    async def body(conn):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("""INSERT INTO tag_limits (device_model, tag_name, normal_lo, normal_hi, warning, danger,
                                  scale_lo, scale_hi) VALUES ('S7-1511T', 'temperature', 60, 72, 80, 79, 30, 110)""")
    run_db(body, monkeypatch)


@needs_db
def test_沒有008時用預設值_改門檻回報要先建表(monkeypatch):
    async def body(conn):
        await limits.load(conn)
        assert get_profile(DEV)["tags"]["temperature"]["warning"] == 85
        with pytest.raises(limits.LimitsUnavailable, match="008"):
            await limits.save(DEV, "temperature", TEMP_OK, "kevin")
    run_db(body, monkeypatch, with_table=False)


def make_client(monkeypatch, audits):
    from routers import device

    async def fake_audit(action, result, **kw):
        audits.append((action, result, kw.get("params")))
    monkeypatch.setattr(device, "write_audit", fake_audit)
    app = FastAPI()
    app.include_router(device.router)
    app.dependency_overrides[auth.require_operator] = lambda: {"username": "kevin", "role": "operator"}
    return TestClient(app)


@needs_db
def test_API_修改_檢查_恢復預設_都有操作紀錄(monkeypatch):
    audits = []

    async def body(conn):
        pass
    run_db(body, monkeypatch)     # 只用來建表後清掉；下面另外建一次給 TestClient 用

    import asyncpg
    from db import pool

    async def setup():
        c = await asyncpg.connect(DB_URL)
        await c.execute((BACKEND / "db/migrations/008_tag_limits.sql").read_text(encoding="utf-8"))
        await c.close()
    asyncio.run(setup())
    monkeypatch.setattr(pool, "get_database_url", lambda: DB_URL)
    try:
        with make_client(monkeypatch, audits) as client:
            r = client.get(f"/api/device-profile/{DEV}")
            v0 = r.json()["limits_version"]
            assert r.json()["limits"]["temperature"]["default"]["warning"] == 85

            r = client.put(f"/api/device-profile/{DEV}/limits/temperature", json={**TEMP_OK, "danger": 79})
            assert r.status_code == 400 and "危險值要大於警戒值" in r.json()["detail"]

            r = client.put(f"/api/device-profile/{DEV}/limits/temperature", json=TEMP_OK)
            assert r.status_code == 200 and r.json()["limits"]["warning"] == 80
            p = client.get(f"/api/device-profile/{DEV}").json()
            assert p["profile"]["tags"]["temperature"]["warning"] == 80 and p["limits_version"] != v0
            assert p["limits"]["temperature"]["modified"]["updated_by"] == "kevin"

            r = client.put(f"/api/device-profile/{DEV}/limits/no_such", json=TEMP_OK)
            assert r.status_code == 400

            r = client.delete(f"/api/device-profile/{DEV}/limits/temperature")
            assert r.status_code == 200 and r.json()["limits"]["warning"] == 85
            assert client.get(f"/api/device-profile/{DEV}").json()["limits_version"] == v0
    finally:
        async def cleanup():
            c = await asyncpg.connect(DB_URL)
            await c.execute("DROP TABLE IF EXISTS tag_limits")
            await c.close()
            await pool.close_pool()
        asyncio.run(cleanup())
    actions = [(a, res) for a, res, _ in audits]
    assert actions == [("limits.update", "error"), ("limits.update", "ok"), ("limits.update", "error"), ("limits.reset", "ok")]
    ok = audits[1][2]
    assert ok["before"]["warning"] == 85 and ok["after"]["warning"] == 80
