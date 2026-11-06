"""
連線設定與連線池的測試
- 前半不需要資料庫
- 最後一組要真的資料庫：設定環境變數 TEST_DATABASE_URL 才會執行，否則自動略過
執行（在 backend 資料夾）：python -m pytest tests -q
"""
import asyncio
import os

import pytest

from core import config
from core.config import ConfigError, get_database_url, masked
from db import pool


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_USER", "DB_HOST", "DB_PORT", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_沒有密碼時報錯並指出_env位置(clean_env):
    with pytest.raises(ConfigError) as e:
        get_database_url()
    assert str(config.ENV_FILE) in str(e.value)


def test_用密碼組出本機網址(clean_env):
    clean_env.setenv("POSTGRES_PASSWORD", "abc_123-XYZ")
    assert get_database_url() == "postgresql://postgres:abc_123-XYZ@localhost:5435/iotscada"


def test_密碼含特殊字元會被編碼(clean_env):
    clean_env.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")
    assert get_database_url() == "postgresql://postgres:p%40ss%3Aw%2Frd@localhost:5435/iotscada"


def test_DATABASE_URL_優先(clean_env):
    clean_env.setenv("POSTGRES_PASSWORD", "x")
    clean_env.setenv("DATABASE_URL", "postgresql://u:p@db:5432/d")
    assert get_database_url() == "postgresql://u:p@db:5432/d"


def test_log_不印出密碼():
    assert masked("postgresql://postgres:secret@localhost:5435/iotscada") == \
        "postgresql://postgres:***@localhost:5435/iotscada"


def test_程式碼裡沒有任何寫死的舊密碼():
    backend = config.REPO_ROOT / "backend"
    old = "iotscada" + "123"
    hits = [str(p) for p in backend.rglob("*.py") if old in p.read_text(encoding="utf-8")]
    hits += [str(p) for p in config.REPO_ROOT.glob("docker-compose*.yml") if old in p.read_text(encoding="utf-8")]
    assert hits == []


def test_資料庫連不上時寫入不會拋例外(clean_env, capsys):
    from db.writer import write_to_db
    clean_env.setenv("DATABASE_URL", "postgresql://postgres:x@127.0.0.1:1/none")
    asyncio.run(write_to_db("S7-1511T-SIM", {"motor_speed": 1480.0}))
    assert "[DB] 寫入失敗" in capsys.readouterr().out


def test_連線池關閉後不再重建(clean_env):
    clean_env.setenv("DATABASE_URL", "postgresql://postgres:x@127.0.0.1:1/none")

    async def run():
        await pool.close_pool()
        with pytest.raises(RuntimeError):
            await pool.get_pool()
    asyncio.run(run())


# ---------- 需要真的資料庫 ----------

TEST_URL = os.getenv("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="未設定 TEST_DATABASE_URL")


@needs_db
def test_連線池只建立一次且寫入成功(clean_env):
    from db.writer import write_to_db
    clean_env.setenv("DATABASE_URL", TEST_URL)

    async def run():
        p1 = await pool.get_pool()
        # 同時 20 筆寫入，只會共用同一個連線池，連線數不超過上限
        await asyncio.gather(*(write_to_db("TEST-POOL", {"motor_speed": float(i)}) for i in range(20)))
        p2 = await pool.get_pool()
        assert p1 is p2
        assert p1.get_size() <= pool.MAX_SIZE
        async with pool.acquire() as conn:
            n = await conn.fetchval("SELECT count(*) FROM plc_measurements WHERE device_id = 'TEST-POOL'")
            await conn.execute("DELETE FROM plc_measurements WHERE device_id = 'TEST-POOL'")
        await pool.close_pool()
        return n
    assert asyncio.run(run()) == 20
