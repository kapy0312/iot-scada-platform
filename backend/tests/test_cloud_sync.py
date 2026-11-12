"""
雲端同步的測試
- 前半不需要資料庫：區間計算
- 後半要兩個「空的、沒裝 TimescaleDB 的」PostgreSQL 測試資料庫：
  TEST_SYNC_LOCAL_URL（當本地）與 TEST_SYNC_CLOUD_URL（當雲端），都設定才會執行
  TimescaleDB 的彙整與系統資訊表用同名的普通表模擬，雲端表直接執行 db/cloud/001_schema.sql
  （略過 TimescaleDB 專屬的語句）
  ⚠ 測試會刪掉並重建 anomaly_incidents 等表：千萬不要指向正式資料庫；
    偵測到資料庫裝了 timescaledb 會直接拒絕執行
執行（在 backend 資料夾）：python -m pytest tests -q
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from sync import cloud_sync as cs

T0 = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)
S = timedelta(seconds=1)
BACKEND = Path(__file__).resolve().parents[1]


# ---------- 不需要資料庫 ----------

def test_取整到10秒():
    assert cs.floor_bucket(T0 + 15 * S) == T0 + 10 * S
    assert cs.floor_bucket(T0 + 10 * S) == T0 + 10 * S
    assert cs.floor_bucket(T0 - 1 * S) == T0 - 10 * S


def test_區間_往回重疊且不超過上限():
    wm, upper = T0, T0 + timedelta(minutes=5)
    assert cs.plan_window(wm, upper, timedelta(minutes=15)) == (T0 - timedelta(minutes=15), upper)


def test_區間_斷線很久時分批追():
    wm, upper = T0, T0 + timedelta(days=2)
    start, end = cs.plan_window(wm, upper, timedelta(minutes=15), timedelta(hours=6))
    assert end == T0 + timedelta(hours=6)


def test_區間_沒有新資料或整理沒跑過就不送():
    assert cs.plan_window(T0, T0, timedelta(minutes=15)) is None
    assert cs.plan_window(T0, T0 - S, timedelta(minutes=15)) is None
    assert cs.plan_window(T0, None, timedelta(minutes=15)) is None


def test_排除模擬設備的條件(monkeypatch):
    monkeypatch.setenv("SYNC_EXCLUDE_SUFFIXES", "-MOCK, -TEST")
    assert cs.exclude_patterns() == ["%-MOCK", "%-TEST"]


# ---------- 需要兩個資料庫 ----------

LOCAL_URL = os.getenv("TEST_SYNC_LOCAL_URL")
CLOUD_URL = os.getenv("TEST_SYNC_CLOUD_URL")
needs_db = pytest.mark.skipif(not (LOCAL_URL and CLOUD_URL),
                              reason="未設定 TEST_SYNC_LOCAL_URL 與 TEST_SYNC_CLOUD_URL")

LOCAL_DDL = """
DROP SCHEMA IF EXISTS timescaledb_information CASCADE;
CREATE SCHEMA timescaledb_information;
CREATE TABLE timescaledb_information.continuous_aggregates (view_name text, materialization_hypertable_name text);
CREATE TABLE timescaledb_information.jobs (job_id int, proc_name text, hypertable_name text, config jsonb);
CREATE TABLE timescaledb_information.job_stats (job_id int, last_successful_finish timestamptz);
INSERT INTO timescaledb_information.continuous_aggregates VALUES ('plc_measurements_10s', '_materialized_hypertable_9');
INSERT INTO timescaledb_information.jobs VALUES
    (1000, 'policy_refresh_continuous_aggregate', '_materialized_hypertable_9', '{"end_offset": "00:00:20", "start_offset": "02:00:00"}'),
    (1001, 'policy_retention', 'plc_measurements', '{}');
INSERT INTO timescaledb_information.job_stats VALUES (1000, NULL), (1001, now());

DROP TABLE IF EXISTS plc_measurements_10s, sync_state, anomaly_incidents, trend_warnings, manual_chunks, manual_documents;
CREATE TABLE plc_measurements_10s (
    bucket timestamptz, device_id text, tag_name text,
    avg_v double precision, min_v double precision, max_v double precision, samples bigint);
CREATE TABLE anomaly_incidents (
    id bigserial PRIMARY KEY, device_id text NOT NULL, started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL, records integer NOT NULL DEFAULT 1, level text NOT NULL,
    severity integer NOT NULL, peak jsonb NOT NULL, min_score double precision,
    ai_analysis text, ai_source jsonb, updated_at timestamptz NOT NULL DEFAULT now());
"""


def _sync_state_ddl() -> str:
    """直接取 migration 003 裡建 sync_state 的那一句，確保測的是真正的表結構"""
    sql = (BACKEND / "db/migrations/003_cagg_10s.sql").read_text(encoding="utf-8")
    start = sql.index("CREATE TABLE IF NOT EXISTS sync_state")
    return sql[start: sql.index(");", start) + 2]


def _cloud_ddl() -> list[str]:
    """雲端 001 的語句，略過 TimescaleDB 專屬的（建 hypertable、壓縮、保存期限、確認查詢）"""
    sql = (BACKEND / "db/cloud/001_schema.sql").read_text(encoding="utf-8")
    stmts = []
    for raw in sql.split(";"):
        body = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--")).strip()
        if body and not any(k in body for k in ("create_hypertable", "timescaledb", "add_")):
            stmts.append(body)
    return stmts


class Env:
    """一個測試用的本地＋雲端連線組"""

    def __init__(self, local, cloud):
        self.local, self.cloud = local, cloud

    async def refreshed_until(self, t: datetime | None):
        """模擬自動整理最後一次成功：t 是整理完成的位置（內部會加回 end_offset 20 秒）"""
        finish = None if t is None else t + 20 * S
        await self.local.execute("UPDATE timescaledb_information.job_stats SET last_successful_finish = $1 "
                                 "WHERE job_id = 1000", finish)

    async def add_buckets(self, device: str, start: datetime, n: int, value: float = 1.0, tags=("t1", "t2")):
        rows = [(start + i * 10 * S, device, tag, value, value, value, 10) for i in range(n) for tag in tags]
        await self.local.executemany("INSERT INTO plc_measurements_10s VALUES ($1,$2,$3,$4,$5,$6,$7)", rows)

    async def cloud_rows(self, device: str | None = None) -> list:
        return await self.cloud.fetch(
            "SELECT bucket, device_id, tag_name, avg_v, samples FROM measurements_10s "
            "WHERE $1::text IS NULL OR device_id = $1 ORDER BY bucket, tag_name", device)

    async def wm(self, target=cs.T_MEAS):
        return await cs.get_watermark(self.local, target)


def run(coro_fn):
    """每個測試建新的兩個資料庫狀態，跑完關連線"""
    async def go():
        local = await asyncpg.connect(LOCAL_URL)
        cloud = await asyncpg.connect(CLOUD_URL)
        try:
            for conn in (local, cloud):
                if await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"):
                    pytest.fail("測試資料庫裝了 timescaledb，看起來是正式資料庫：拒絕執行（會刪表）")
            await local.execute(LOCAL_DDL + _sync_state_ddl())
            await cloud.execute("DROP TABLE IF EXISTS measurements_10s, anomaly_incidents, trend_warnings")
            for stmt in _cloud_ddl():
                await cloud.execute(stmt)
            await coro_fn(Env(local, cloud))
        finally:
            await local.close()
            await cloud.close()
    asyncio.run(go())


@needs_db
def test_雲端表結構可以建立且有主鍵():
    async def body(env):
        pk = await env.cloud.fetch("""
            SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'measurements_10s'::regclass AND i.indisprimary ORDER BY a.attname""")
        assert [r["attname"] for r in pk] == ["bucket", "device_id", "tag_name"]
    run(body)


@needs_db
def test_自動整理沒跑過時不送也不推指標():
    async def body(env):
        await env.add_buckets("S7-1511T-SIM", T0, 6)
        await env.refreshed_until(None)
        assert await cs.sync_cycle(env.local, env.cloud) == {cs.T_MEAS: 0, cs.T_INC: 0}
        assert await env.cloud_rows() == [] and await env.wm() is None
    run(body)


@needs_db
def test_正常同步_只送已整理完的區間_排除MOCK():
    async def body(env):
        await env.add_buckets("S7-1511T-SIM", T0, 12)            # 00:00:00 ~ 00:01:50
        await env.add_buckets("S7-1511T-MOCK", T0, 12)
        await env.refreshed_until(T0 + 60 * S + 5 * S)           # 整理到 00:01:05 → 只有前 6 個區間完整
        sent = await cs.sync_cycle(env.local, env.cloud)
        rows = await env.cloud_rows()
        assert sent[cs.T_MEAS] == 12 and len(rows) == 12         # 6 個區間 × 2 個訊號
        assert {r["device_id"] for r in rows} == {"S7-1511T-SIM"}
        assert max(r["bucket"] for r in rows) == T0 + 50 * S
        assert await env.wm() == T0 + 60 * S
    run(body)


@needs_db
def test_雲端斷線_指標不動_恢復後補齊且不重複():
    async def body(env):
        await env.add_buckets("S7-1511T-SIM", T0, 30)
        await env.refreshed_until(T0 + 100 * S)
        await cs.sync_cycle(env.local, env.cloud)
        wm_before = await env.wm()

        # 雲端斷線時，新資料照樣進本地
        await env.refreshed_until(T0 + 300 * S)
        dead = await asyncpg.connect(CLOUD_URL)
        await dead.close()
        with pytest.raises(asyncpg.InterfaceError):
            await cs.sync_cycle(env.local, dead)
        assert await env.wm() == wm_before                         # 指標停在原地

        await cs.sync_cycle(env.local, env.cloud)                   # 恢復
        rows = await env.cloud_rows()
        assert len(rows) == 60                                      # 30 個區間 × 2 個訊號，沒漏
        keys = [(r["bucket"], r["tag_name"]) for r in rows]
        assert len(keys) == len(set(keys))                          # 沒重複
        assert await env.wm() == T0 + 300 * S
    run(body)


@needs_db
def test_雲端已commit但本地指標沒寫成_重送不會變兩筆(monkeypatch):
    async def body(env):
        await env.add_buckets("S7-1511T-SIM", T0, 6)
        await env.refreshed_until(T0 + 60 * S)
        real = cs.set_watermark

        async def crash(*a, **k):
            raise ConnectionError("本地資料庫剛好斷線")
        monkeypatch.setattr(cs, "set_watermark", crash)
        with pytest.raises(ConnectionError):
            await cs.sync_cycle(env.local, env.cloud)
        assert len(await env.cloud_rows()) == 12 and await env.wm() is None   # 雲端有了，指標沒推

        monkeypatch.setattr(cs, "set_watermark", real)
        await cs.sync_cycle(env.local, env.cloud)
        assert len(await env.cloud_rows()) == 12                    # 重送後還是 12 筆
        assert await env.wm() == T0 + 60 * S
    run(body)


@needs_db
def test_晚到資料讓彙整重算_重疊區間會把修正值送上去():
    async def body(env):
        await env.add_buckets("S7-1511T-SIM", T0, 6, value=1.0)
        await env.refreshed_until(T0 + 60 * S)
        await cs.sync_cycle(env.local, env.cloud)
        # 晚到資料讓 00:00:20 這格重算成 9.9
        await env.local.execute("UPDATE plc_measurements_10s SET avg_v = 9.9 WHERE bucket = $1", T0 + 20 * S)
        await env.add_buckets("S7-1511T-SIM", T0 + 60 * S, 3)
        await env.refreshed_until(T0 + 90 * S)
        await cs.sync_cycle(env.local, env.cloud)
        fixed = [r["avg_v"] for r in await env.cloud_rows() if r["bucket"] == T0 + 20 * S]
        assert fixed == [9.9, 9.9]
    run(body)


@needs_db
def test_斷線很久_分批追上(monkeypatch):
    monkeypatch.setattr(cs, "MAX_WINDOW", timedelta(minutes=10))
    async def body(env):
        await env.add_buckets("S7-1511T-SIM", T0, 6 * 60, tags=("t1",))  # 1 小時，每 10 秒一格
        await env.refreshed_until(T0 + timedelta(hours=1))
        # 預設參數在函式定義時就綁好了，所以用包一層的方式套用 10 分鐘的批次
        orig = cs.sync_measurements_once
        monkeypatch.setattr(cs, "sync_measurements_once",
                            lambda l, c: orig(l, c, max_window=timedelta(minutes=10)))
        sent = await cs.sync_cycle(env.local, env.cloud)
        assert len(await env.cloud_rows()) == 360                   # 一輪內分 6 批追完
        assert sent[cs.T_MEAS] >= 360                               # 每批往回重送 15 分鐘，總送出數會多於 360
        assert await env.wm() == T0 + timedelta(hours=1)
    run(body)


@needs_db
def test_事件表_新增與更新都會同步_舊版本不會蓋掉新版本():
    async def body(env):
        await env.refreshed_until(None)
        inc_id = await env.local.fetchval("""
            INSERT INTO anomaly_incidents (device_id, started_at, ended_at, level, severity, peak, updated_at)
            VALUES ('S7-1511T-SIM', now(), now(), 'warning', 2, '{"max": {"temperature": 88.1}}', now() - interval '1 minute')
            RETURNING id""")
        await env.local.execute("""
            INSERT INTO anomaly_incidents (device_id, started_at, ended_at, level, severity, peak, updated_at)
            VALUES ('S7-1511T-MOCK', now(), now(), 'warning', 2, '{}', now() - interval '1 minute')""")
        await cs.sync_cycle(env.local, env.cloud)
        rows = await env.cloud.fetch("SELECT id, level, peak FROM anomaly_incidents")
        assert [r["id"] for r in rows] == [inc_id]                  # MOCK 沒送
        assert json.loads(rows[0]["peak"])["max"]["temperature"] == 88.1

        # 事件持續中：本地更新 → 下一輪雲端跟著更新
        await env.local.execute("UPDATE anomaly_incidents SET level = 'danger', updated_at = now() - interval '30 seconds' "
                                "WHERE id = $1", inc_id)
        await cs.sync_cycle(env.local, env.cloud)
        assert await env.cloud.fetchval("SELECT level FROM anomaly_incidents WHERE id = $1", inc_id) == "danger"

        # 重送舊版本（例如指標被調回）不會把雲端改回去
        old = await env.local.fetchrow(f"SELECT {', '.join(cs.INC_COLS)} FROM anomaly_incidents WHERE id = $1", inc_id)
        stale = list(old)
        stale[cs.INC_COLS.index("level")] = "warning"
        stale[cs.INC_COLS.index("updated_at")] = old["updated_at"] - timedelta(minutes=5)
        await cs.upsert_incidents(env.cloud, [tuple(stale)])
        assert await env.cloud.fetchval("SELECT level FROM anomaly_incidents WHERE id = $1", inc_id) == "danger"
    run(body)


@needs_db
def test_另一個backend正在同步時這一輪略過():
    async def body(env):
        await env.add_buckets("S7-1511T-SIM", T0, 6)
        await env.refreshed_until(T0 + 60 * S)
        other = await asyncpg.connect(LOCAL_URL)
        try:
            assert await other.fetchval("SELECT pg_try_advisory_lock($1)", cs.LOCK_KEY)
            assert await cs.sync_cycle(env.local, env.cloud) is None
            assert await env.cloud_rows() == []
        finally:
            await other.close()                                      # 連線關掉鎖就釋放
        assert (await cs.sync_cycle(env.local, env.cloud))[cs.T_MEAS] == 12
    run(body)


@needs_db
def test_空白時段直接跳過_一輪就追上_早期只有MOCK的資料不翻():
    async def body(env):
        await env.add_buckets("S7-1511T-MOCK", T0 - timedelta(days=20), 6)          # 很早以前只有 MOCK
        await env.add_buckets("S7-1511T-SIM", T0, 6)                                # 第一段資料
        await env.add_buckets("S7-1511T-SIM", T0 + timedelta(days=30), 6)           # 30 天空白後的第二段
        await env.refreshed_until(T0 + timedelta(days=30, minutes=1))
        calls = 0
        orig = cs.sync_measurements_once

        async def counted(l, c):
            nonlocal calls
            calls += 1
            return await orig(l, c)
        cs_once, cs.sync_measurements_once = cs.sync_measurements_once, counted
        try:
            await cs.sync_cycle(env.local, env.cloud)
        finally:
            cs.sync_measurements_once = cs_once
        assert len(await env.cloud_rows()) == 24                    # 兩段都到了，MOCK 沒送
        assert await env.wm() == T0 + timedelta(days=30, minutes=1)
        assert calls <= 4                                           # 沒有空白跳躍時要 120 批以上
    run(body)


def test_錯誤訊息看得懂():
    assert "逾時" in cs.describe_error(TimeoutError())
    assert "密碼" in cs.describe_error(asyncpg.InvalidPasswordError("x"))
    assert cs.describe_error(ValueError("壞掉")) == "ValueError: 壞掉"


# ---------- 每小時 OEE ----------

H = timedelta(hours=1)

OEE_LOCAL_DDL = """
DROP TABLE IF EXISTS oee_hourly, product_specs;
CREATE TABLE oee_hourly (
    bucket timestamptz, device_id text, product_id integer,
    run_s real, idle_s real, fault_s real, changeover_s real, planned_stop_s real,
    data_s real, unknown_s real, good bigint, ng bigint, samples bigint);
INSERT INTO timescaledb_information.continuous_aggregates VALUES ('oee_hourly', '_materialized_hypertable_12');
INSERT INTO timescaledb_information.jobs VALUES
    (1002, 'policy_refresh_continuous_aggregate', 'oee_hourly', '{"end_offset": "00:01:00", "start_offset": "04:00:00"}');
INSERT INTO timescaledb_information.job_stats VALUES (1002, NULL);
"""


def _product_specs_ddl() -> list[str]:
    """migration 006 裡建品號表、放預設品號的兩句"""
    sql = (BACKEND / "db/migrations/006_oee.sql").read_text(encoding="utf-8")
    a = sql.index("CREATE TABLE IF NOT EXISTS product_specs")
    b = sql.index("ON CONFLICT (product_id) DO NOTHING;") + len("ON CONFLICT (product_id) DO NOTHING;")
    return [s for s in sql[a:b].split(";") if s.strip()]


def _cloud_oee_ddl() -> list[str]:
    """雲端 003 的建表語句（測試資料庫沒有 edge_writer 帳號，略過 GRANT 與確認查詢）"""
    sql = (BACKEND / "db/cloud/003_oee.sql").read_text(encoding="utf-8")
    out = []
    for raw in sql.split(";"):
        body = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--")).strip()
        if body and not body.upper().startswith(("GRANT", "SELECT")):
            out.append(body)
    return out


async def _oee_setup(env, cloud_table=True):
    await env.local.execute(OEE_LOCAL_DDL)
    for s in _product_specs_ddl():
        await env.local.execute(s)
    await env.cloud.execute("DROP TABLE IF EXISTS oee_hourly")
    if cloud_table:
        for s in _cloud_oee_ddl():
            await env.cloud.execute(s)


async def _oee_refreshed_until(env, t):
    await env.local.execute("UPDATE timescaledb_information.job_stats SET last_successful_finish = $1 "
                            "WHERE job_id = 1002", t + timedelta(minutes=1))


async def _add_hours(env, device, start, n, product=1, good=500, ng=5):
    rows = [(start + i * H, device, product, 3000.0, 600.0, 0.0, 0.0, 0.0, 3600.0, 0.0, good, ng, 3600)
            for i in range(n)]
    await env.local.executemany("INSERT INTO oee_hourly VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)", rows)


@needs_db
def test_OEE_只送整小時已整理完的_帶上標準週期_排除MOCK():
    async def body(env):
        await _oee_setup(env)
        await _add_hours(env, "S7-1511T-SIM", T0, 5)
        await _add_hours(env, "S7-1511T-MOCK", T0, 5)
        await _add_hours(env, "S7-1511T-SIM", T0 + 2 * H, 1, product=9)      # 沒設定標準週期的品號
        await _oee_refreshed_until(env, T0 + 3 * H + 30 * S * 6)             # 整理到 03:03 → 00～02 點完整
        sent = await cs.sync_cycle(env.local, env.cloud)
        assert sent[cs.T_OEE] == 4
        rows = await env.cloud.fetch("SELECT * FROM oee_hourly ORDER BY bucket, product_id")
        assert {r["device_id"] for r in rows} == {"S7-1511T-SIM"}
        assert max(r["bucket"] for r in rows) == T0 + 2 * H
        r0 = rows[0]
        assert (r0["ideal_cycle_s"], r0["ideal_s"], r0["good"], r0["run_s"]) == (6.0, 505 * 6.0, 500, 3000.0)
        r9 = [r for r in rows if r["product_id"] == 9][0]
        assert r9["ideal_cycle_s"] is None and r9["ideal_s"] is None
        assert await env.wm(cs.T_OEE) == T0 + 3 * H
    run(body)


@needs_db
def test_OEE_重送不重複_改標準週期後重送會更新():
    async def body(env):
        await _oee_setup(env)
        await _add_hours(env, "S7-1511T-SIM", T0, 3)
        await _oee_refreshed_until(env, T0 + 3 * H)
        await cs.sync_cycle(env.local, env.cloud)
        await env.local.execute("UPDATE product_specs SET ideal_cycle_s = 5 WHERE product_id = 1")
        await env.local.execute("UPDATE oee_hourly SET good = 510 WHERE bucket = $1", T0 + 2 * H)  # 晚到資料重算
        await cs.sync_cycle(env.local, env.cloud)                            # 沒有新的整小時：不送
        assert await env.cloud.fetchval("SELECT good FROM oee_hourly WHERE bucket = $1", T0 + 2 * H) == 500
        await _add_hours(env, "S7-1511T-SIM", T0 + 3 * H, 1)
        await _oee_refreshed_until(env, T0 + 4 * H)
        await cs.sync_cycle(env.local, env.cloud)                            # 新的一小時好了，往回重疊 2 小時重送
        rows = await env.cloud.fetch("SELECT bucket, good, ideal_cycle_s FROM oee_hourly ORDER BY bucket")
        assert len(rows) == 4                                                # 重送不會變兩筆
        assert [(r["good"], r["ideal_cycle_s"]) for r in rows] == [(500, 6.0), (500, 5.0), (510, 5.0), (500, 5.0)]
    run(body)


@needs_db
def test_OEE_雲端沒有表就略過_其他目標照常():
    async def body(env):
        await _oee_setup(env, cloud_table=False)
        await _add_hours(env, "S7-1511T-SIM", T0, 3)
        await _oee_refreshed_until(env, T0 + 3 * H)
        await env.add_buckets("S7-1511T-SIM", T0, 6)
        await env.refreshed_until(T0 + 60 * S)
        sent = await cs.sync_cycle(env.local, env.cloud)
        assert cs.T_OEE not in sent and sent[cs.T_MEAS] == 12
        assert await env.wm(cs.T_OEE) is None
    run(body)


def test_取整到1小時():
    assert cs.floor_bucket(T0 + 95 * 60 * S, cs.HOUR) == T0 + H
