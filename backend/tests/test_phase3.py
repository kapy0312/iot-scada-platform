"""
Phase 3：MCP 工具共用模組、設定表同步上雲（雲端 006）
- 時間範圍、工具註冊：不需要資料庫
- 設定表同步：需要 TEST_SYNC_LOCAL_URL 與 TEST_SYNC_CLOUD_URL
  ⚠ 會刪掉並重建 tag_limits、product_specs、shift_definitions、sync_state：千萬不要指向正式資料庫；偵測到 timescaledb 會拒絕執行
執行（在 backend 資料夾）：python -m pytest tests/test_phase3.py -q
"""
import asyncio
import os
from datetime import date
from pathlib import Path

import asyncpg
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_tools.common import time_range
from mcp_tools.sources import CloudSource, LocalSource
from mcp_tools.tools import INSTRUCTIONS, register
from sync import cloud_sync as cs

BACKEND = Path(__file__).resolve().parents[1]


# ---------- 共用模組 ----------

def test_時間範圍上限():
    s, e = time_range("2026-11-01 00:00", "2026-11-08 00:00", 1, 168)
    assert (e - s).days == 7
    with pytest.raises(ToolError):
        time_range("2026-11-01 00:00", "2026-11-08 00:01", 1, 168)
    with pytest.raises(ToolError):
        time_range("2026-11-02 00:00", "2026-11-01 00:00", 1)


@pytest.mark.parametrize("source", [LocalSource(), CloudSource("postgresql://x", "k")])
def test_兩種來源都註冊六個唯讀工具(source):
    server = MCPServer(name="t", instructions=INSTRUCTIONS)
    tools = register(server, source)
    listed = asyncio.run(server.list_tools())
    assert sorted(t.name for t in listed) == sorted(tools) == sorted(
        ["list_anomalies", "get_trend", "search_manual", "get_device_info", "get_oee", "list_trend_warnings"])
    assert all(t.annotations.read_only_hint for t in listed)


def test_雲端沒設定連線_給看得懂的錯誤():
    with pytest.raises(ToolError, match="TIGER_READER_URL"):
        asyncio.run(CloudSource("", "")._open())


# ---------- 設定表同步 ----------

LOCAL_URL = os.getenv("TEST_SYNC_LOCAL_URL")
CLOUD_URL = os.getenv("TEST_SYNC_CLOUD_URL")
needs_db = pytest.mark.skipif(not (LOCAL_URL and CLOUD_URL), reason="未設定 TEST_SYNC_LOCAL_URL 與 TEST_SYNC_CLOUD_URL")


def local_ddl() -> str:
    sql = (BACKEND / "db/migrations/006_oee.sql").read_text(encoding="utf-8")
    parts = []
    for name in ("product_specs", "shift_definitions"):
        start = sql.index(f"CREATE TABLE IF NOT EXISTS {name}")
        parts.append(sql[start: sql.index(");", start) + 2])
    lim = (BACKEND / "db/migrations/008_tag_limits.sql").read_text(encoding="utf-8")
    start = lim.index("CREATE TABLE IF NOT EXISTS tag_limits")
    parts.append(lim[start: lim.index("\n);", start) + 3])
    st = (BACKEND / "db/migrations/003_cagg_10s.sql").read_text(encoding="utf-8")
    start = st.index("CREATE TABLE IF NOT EXISTS sync_state")
    parts.append(st[start: st.index(");", start) + 2])
    return "DROP TABLE IF EXISTS product_specs, shift_definitions, tag_limits, sync_state;\n" + "\n".join(parts)


def run(fn, cloud_006=True):
    async def go():
        local, cloud = await asyncpg.connect(LOCAL_URL), await asyncpg.connect(CLOUD_URL)
        try:
            for conn in (local, cloud):
                if await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"):
                    pytest.fail("測試資料庫裝了 timescaledb，看起來是正式資料庫：拒絕執行（會刪表）")
            await local.execute(local_ddl())
            await cloud.execute("DROP TABLE IF EXISTS tag_limits, product_specs, shift_definitions")
            if cloud_006:
                await cloud.execute((BACKEND / "db/cloud/006_settings.sql").read_text(encoding="utf-8"))
            cs._skip_reason.clear()
            return await fn(local, cloud)
        finally:
            await local.close()
            await cloud.close()
    return asyncio.run(go())


async def snapshot(cloud):
    return {
        "limits": [tuple(r) for r in await cloud.fetch("SELECT tag_name, warning FROM tag_limits ORDER BY tag_name")],
        "products": [tuple(r) for r in await cloud.fetch("SELECT product_id, ideal_cycle_s::float FROM product_specs ORDER BY 1")],
        "shifts": [tuple(r) for r in await cloud.fetch("SELECT id, name, start_hour FROM shift_definitions ORDER BY id")],
    }


@needs_db
def test_設定表_新增_修改_刪除_沒變不重寫():
    async def go(local, cloud):
        await local.execute("""
            INSERT INTO product_specs (product_id, name, ideal_cycle_s) VALUES (1, 'A', 6.0), (2, 'B', 8.5);
            INSERT INTO shift_definitions (effective_from, name, start_hour, end_hour)
                VALUES ('2026-01-01', '早班', 8, 20), ('2026-01-01', '晚班', 20, 8);
            INSERT INTO tag_limits VALUES ('S7-1511T', 'temperature', 65, 75, 80, 100, 40, 120, now(), 'kevin');
        """)
        n, _ = await cs.sync_config_once(local, cloud)
        assert n == 5
        first = await snapshot(cloud)
        assert first["limits"] == [("temperature", 80.0)] and len(first["shifts"]) == 2
        assert (await cs.sync_config_once(local, cloud))[0] == 0              # 沒變：不重寫
        # 門檻恢復預設（本地刪列）、品號改週期、班別換一套（刪舊 id、加新 id）
        await local.execute("""
            DELETE FROM tag_limits;
            UPDATE product_specs SET ideal_cycle_s = 7.0, updated_at = now() WHERE product_id = 2;
            DELETE FROM shift_definitions WHERE effective_from = '2026-01-01';
            INSERT INTO shift_definitions (effective_from, name, start_hour, end_hour)
                VALUES ('2026-01-01', '早班', 7, 19), ('2026-01-01', '晚班', 19, 7);
        """)
        await cs.sync_config_once(local, cloud)
        after = await snapshot(cloud)
        assert after["limits"] == []
        assert after["products"] == [(1, 6.0), (2, 7.0)]
        local_ids = [r["id"] for r in await local.fetch("SELECT id FROM shift_definitions ORDER BY id")]
        assert [s[0] for s in after["shifts"]] == local_ids and [s[2] for s in after["shifts"]] == [7, 19]
        # 雲端 MCP 讀得懂：同一個 oee_repo 讀雲端的班別
        from db import oee_repo
        versions = await oee_repo.load_shift_versions(cloud)
        assert [s.start_hour for s in oee_repo.shifts_for_day(versions, date(2026, 11, 12))] == [7, 19]
    run(go)


@needs_db
def test_設定表_雲端還沒跑006_略過():
    async def go(local, cloud):
        assert await cs.config_ready(local, cloud) is False
        assert "006" in cs._skip_reason[cs.T_CFG]
    run(go, cloud_006=False)


# ---------- 雲端 MCP 的密碼檢查 ----------

def _client(token):
    from starlette.testclient import TestClient
    from mcp_cloud.server import BearerAuth

    async def inner(scope, receive, send):   # 代替真正的 MCP：通過檢查就回 200
        from starlette.responses import PlainTextResponse
        await PlainTextResponse("ok")(scope, receive, send)
    return TestClient(BearerAuth(inner, token))


def test_雲端MCP_密碼檢查():
    c = _client("s3cret")
    assert c.post("/mcp").status_code == 401
    assert c.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/mcp", headers={"Authorization": "s3cret"}).status_code == 401          # 少了 Bearer
    assert c.post("/mcp", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/health").status_code == 200                                              # 健康檢查不用密碼


def test_雲端MCP_沒設定密碼一律拒絕():
    c = _client("")
    assert c.post("/mcp", headers={"Authorization": "Bearer "}).status_code == 401
    assert c.post("/mcp", headers={"Authorization": "Bearer anything"}).status_code == 401
