"""
事件的當時門檻（migration 010）與手冊同步上雲（雲端 005）
- 門檻快照、差異文字、補舊事件的推算、手冊同步的比對規則：不需要資料庫
- 實際同步：需要 TEST_SYNC_LOCAL_URL 與 TEST_SYNC_CLOUD_URL（兩邊都要有 vector extension，
  雲端要有 edge_writer、mcp_reader 兩個角色）
  ⚠ 會刪掉並重建 anomaly_incidents、manual_* 等表：千萬不要指向正式資料庫；偵測到 timescaledb 會拒絕執行
執行（在 backend 資料夾）：python -m pytest tests/test_phase2c.py -q
"""
import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from core.device_profile import get_profile
from core.incidents import diff_text, limit_diffs, limits_snapshot, new_incident, spec_at_time
from sync import cloud_sync as cs
from tools.backfill_incident_limits import build_history, changed_between, limits_at, snapshot_for

BACKEND = Path(__file__).resolve().parents[1]
DEV = "S7-1511T-SIM"
T0 = datetime(2026, 11, 12, 2, 55, tzinfo=timezone.utc)
M = timedelta(minutes=1)


# ---------- 門檻快照 ----------

def test_新事件記下開始時的門檻():
    inc = new_incident(DEV, T0, "v1")
    assert inc["limits"]["version"] == "v1" and inc["limits_changed"] is False
    assert inc["limits"]["tags"]["temperature"] == {"normal": [65, 75], "warning": 85, "danger": 100}
    assert "scale" not in inc["limits"]["tags"]["temperature"]       # 刻度只影響畫面，不存


def test_用當時門檻判斷_沒有快照就用現行():
    spec = get_profile(DEV)["tags"]["temperature"]
    snap = {"tags": {"temperature": {"normal": [65, 68], "warning": 69, "danger": 100}}}
    assert spec_at_time(spec, snap, "temperature")["warning"] == 69
    assert spec_at_time(spec, None, "temperature")["warning"] == 85
    assert spec_at_time(spec, snap, "current") is spec


def test_差異文字_事件39的情況():
    profile = get_profile(DEV)
    snap = limits_snapshot(DEV)
    snap["tags"]["temperature"]["warning"] = 69.0
    diffs = limit_diffs(profile, snap)
    assert [diff_text(d) for d in diffs] == ["溫度警戒值 69（現行 85）"]
    assert limit_diffs(profile, limits_snapshot(DEV)) == []
    assert limit_diffs(profile, None) == []


# ---------- 補舊事件：從 audit_log 推回當時門檻 ----------

def audit(t, before_w, after_w, tag="temperature"):
    lim = lambda w: {"normal": [65, 75] if w > 75 else [65, 68], "warning": w, "danger": 100, "scale": [40, 120]}
    return {"time": t, "params": json.dumps({"device_id": "S7-1511T", "tag": tag,
                                             "before": lim(before_w), "after": lim(after_w)})}


def test_補門檻_事件39():
    hist = build_history([audit(T0, 85, 69), audit(T0 + 3 * M, 69, 85)])
    h = hist[("S7-1511T", "temperature")]
    cur = {"normal": [65, 75], "warning": 85, "danger": 100}
    assert limits_at(h, T0 + 5 * timedelta(seconds=1), cur)["warning"] == 69   # 改成 69 之後開始
    assert limits_at(h, T0 - M, cur)["warning"] == 85                          # 改之前：用第一次的修改前
    assert limits_at(h, T0 + 10 * M, cur)["warning"] == 85                     # 改回來之後
    assert limits_at([], T0, cur) is cur                                       # 從沒改過：現行
    snap, changed = snapshot_for(DEV, T0 + timedelta(seconds=1), T0 + 3 * M - timedelta(seconds=1), hist)
    assert snap["tags"]["temperature"]["warning"] == 69 and not changed
    _, changed2 = snapshot_for(DEV, T0 + timedelta(seconds=1), T0 + 4 * M, hist)
    assert changed2                                                            # 進行中被改回 85
    assert not changed_between(h, T0 + 4 * M, T0 + 5 * M)


def test_補門檻_格式不對的紀錄略過():
    bad = {"time": T0, "params": json.dumps({"tag": "temperature", "after": {"warning": 1}})}
    assert build_history([bad]) == {}


# ---------- 手冊同步的比對規則 ----------

def doc(fh, updated, n=3):
    return {"file_hash": fh, "filename": f"{fh}.pdf", "updated_at": updated, "n_chunks": n}


def cdoc(updated, n=3, deleted=None):
    return {"updated_at": updated, "n_chunks": n, "deleted_at": deleted}


def test_手冊比對():
    local = [doc("new", T0), doc("same", T0), doc("toggled", T0 + M), doc("half", T0), doc("revived", T0 + M)]
    cloud = {"same": cdoc(T0), "toggled": cdoc(T0), "half": cdoc(T0, n=1),
             "revived": cdoc(T0, deleted=T0), "removed": cdoc(T0), "already_gone": cdoc(T0, deleted=T0)}
    send, gone = cs.plan_manuals(local, cloud)
    plan = {d["file_hash"]: d["need_chunks"] for d in send}
    assert plan == {"new": True, "toggled": False, "half": True, "revived": False}
    assert gone == ["removed"]


# ---------- 實際同步（兩個資料庫） ----------

LOCAL_URL = os.getenv("TEST_SYNC_LOCAL_URL")
CLOUD_URL = os.getenv("TEST_SYNC_CLOUD_URL")
needs_db = pytest.mark.skipif(not (LOCAL_URL and CLOUD_URL),
                              reason="未設定 TEST_SYNC_LOCAL_URL 與 TEST_SYNC_CLOUD_URL")

INC_DDL = """
DROP TABLE IF EXISTS manual_chunks, manual_documents, anomaly_incidents, sync_state CASCADE;
CREATE TABLE anomaly_incidents (
    id bigserial PRIMARY KEY, device_id text NOT NULL, started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL, records integer NOT NULL DEFAULT 1, level text NOT NULL,
    severity integer NOT NULL, peak jsonb NOT NULL, min_score double precision,
    ai_analysis text, ai_source jsonb, updated_at timestamptz NOT NULL DEFAULT now());
"""


def sql_file(rel: str) -> str:
    return (BACKEND / rel).read_text(encoding="utf-8")


def cloud_001_incidents() -> str:
    sql = sql_file("db/cloud/001_schema.sql")
    start = sql.index("CREATE TABLE IF NOT EXISTS anomaly_incidents")
    return sql[start: sql.index(");", start) + 2]


def sync_state_ddl() -> str:
    sql = sql_file("db/migrations/003_cagg_10s.sql")
    start = sql.index("CREATE TABLE IF NOT EXISTS sync_state")
    return sql[start: sql.index(");", start) + 2]


def run(coro_fn, local_010=True, cloud_005=True):
    async def go():
        local = await asyncpg.connect(LOCAL_URL)
        cloud = await asyncpg.connect(CLOUD_URL)
        try:
            for conn in (local, cloud):
                if await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"):
                    pytest.fail("測試資料庫裝了 timescaledb，看起來是正式資料庫：拒絕執行（會刪表）")
            await local.execute(INC_DDL + sync_state_ddl())
            await local.execute(sql_file("db/migrations/009_manual_vectors.sql"))
            if local_010:
                await local.execute(sql_file("db/migrations/010_incident_limits.sql"))
            await cloud.execute("DROP TABLE IF EXISTS manual_chunks, manual_documents, anomaly_incidents CASCADE")
            await cloud.execute(cloud_001_incidents())
            if cloud_005:
                await cloud.execute(sql_file("db/cloud/005_manuals_limits.sql"))
            cs._skip_reason.clear()
            return await coro_fn(local, cloud)
        finally:
            await local.close()
            await cloud.close()
    return asyncio.run(go())


def vec(seed: int) -> str:
    return "[" + ",".join(str(((i * 7 + seed) % 13) / 13) for i in range(1024)) + "]"


async def add_manual(local, fh: str, n: int = 3, enabled: bool = True):
    await local.execute("""INSERT INTO manual_documents (file_hash, filename, device_id, doc_title, enabled,
                           chunk_count, chunker_version) VALUES ($1, $2, 'S7-1511T', 't', $3, $4, 2)""",
                        fh, f"{fh}.pdf", enabled, n)
    for i in range(n):
        await local.execute("""INSERT INTO manual_chunks (id, file_hash, chunk_index, section, page, text, embedding)
                               VALUES ($1, $2, $3, $4, 1, $5, $6::vector)""",
                            uuid.uuid4(), fh, i, f"{i}. 章節", f"{fh}-{i}", vec(i))


async def add_incident(local, with_limits: bool):
    snap = json.dumps(limits_snapshot(DEV, "v1")) if with_limits else None
    cols = ", limits, limits_changed" if with_limits else ""
    vals = ", $3::jsonb, true" if with_limits else ""
    args = [DEV, json.dumps({"max": {"temperature": 70.3}})] + ([snap] if with_limits else [])
    return await local.fetchval(f"""
        INSERT INTO anomaly_incidents (device_id, started_at, ended_at, level, severity, peak, updated_at{cols})
        VALUES ($1, now() - interval '10 minutes', now() - interval '9 minutes', 'warning', 3, $2::jsonb,
                now() - interval '1 minute'{vals}) RETURNING id""", *args)


@needs_db
def test_同步_手冊新增_勾選_刪除_重新上傳():
    async def go(local, cloud):
        await add_manual(local, "a")
        await add_manual(local, "b", n=2)
        n, _ = await cs.sync_manuals_once(local, cloud)
        assert n == 2
        assert await cloud.fetchval("SELECT count(*) FROM manual_chunks") == 5
        # 向量原封不動：跟本地同一段的文字表示一樣
        lv = await local.fetchval("SELECT embedding::text FROM manual_chunks WHERE file_hash='a' AND chunk_index=1")
        cv = await cloud.fetchval("SELECT embedding::text FROM manual_chunks WHERE file_hash='a' AND chunk_index=1")
        assert lv == cv
        # 沒變化：不重送
        assert (await cs.sync_manuals_once(local, cloud))[0] == 0
        # 勾選：只更新文件列
        await local.execute("UPDATE manual_documents SET enabled = false, updated_at = now() WHERE file_hash = 'a'")
        assert (await cs.sync_manuals_once(local, cloud))[0] == 1
        assert await cloud.fetchval("SELECT enabled FROM manual_documents WHERE file_hash = 'a'") is False
        # 本地刪除：雲端標 deleted_at，段落保留
        await local.execute("DELETE FROM manual_documents WHERE file_hash = 'b'")
        assert (await cs.sync_manuals_once(local, cloud))[0] == 1
        row = await cloud.fetchrow("SELECT deleted_at, enabled FROM manual_documents WHERE file_hash = 'b'")
        assert row["deleted_at"] is not None and row["enabled"] is False
        assert (await cs.sync_manuals_once(local, cloud))[0] == 0            # 不會每輪重標
        # 同一份重新上傳、切成不同段數：清掉舊段落，段數一致，deleted_at 清掉
        await add_manual(local, "b", n=4)
        assert (await cs.sync_manuals_once(local, cloud))[0] == 1
        assert await cloud.fetchval("SELECT count(*) FROM manual_chunks WHERE file_hash = 'b'") == 4
        assert await cloud.fetchval("SELECT deleted_at FROM manual_documents WHERE file_hash = 'b'") is None
        assert (await cs.sync_manuals_once(local, cloud))[0] == 0
        # 雲端用 pgvector 查得到（給 Phase 3）
        hit = await cloud.fetchval("""SELECT c.text FROM manual_chunks c JOIN manual_documents d USING (file_hash)
                                      WHERE d.enabled AND d.deleted_at IS NULL
                                      ORDER BY c.embedding <=> $1::vector LIMIT 1""", vec(2))
        assert hit == "b-2"
    run(go)


@needs_db
def test_同步_事件帶門檻():
    async def go(local, cloud):
        inc = await add_incident(local, with_limits=True)
        await cs.sync_incidents_once(local, cloud)
        row = await cloud.fetchrow("SELECT limits, limits_changed FROM anomaly_incidents WHERE id = $1", inc)
        assert json.loads(row["limits"])["tags"]["temperature"]["warning"] == 85 and row["limits_changed"] is True
    run(go)


async def _no_meas(local, cloud):
    return 0, False


@needs_db
def test_同步_雲端還沒跑005_事件照送_手冊略過(monkeypatch):
    monkeypatch.setattr(cs, "sync_measurements_once", _no_meas)   # 這裡只測事件與手冊

    async def go(local, cloud):
        inc = await add_incident(local, with_limits=True)
        await add_manual(local, "a")
        sent = await cs.sync_cycle(local, cloud)
        assert sent[cs.T_INC] == 1 and cs.T_MAN not in sent
        assert await cloud.fetchval("SELECT id FROM anomaly_incidents WHERE id = $1", inc) == inc
    run(go, cloud_005=False)


@needs_db
def test_同步_本地還沒跑010_事件照送(monkeypatch):
    monkeypatch.setattr(cs, "sync_measurements_once", _no_meas)

    async def go(local, cloud):
        inc = await add_incident(local, with_limits=False)
        sent = await cs.sync_cycle(local, cloud)
        assert sent[cs.T_INC] == 1
        assert await cloud.fetchval("SELECT limits FROM anomaly_incidents WHERE id = $1", inc) is None
    run(go, local_010=False)
