"""
共用的資料庫連線池

以前每次寫入都「連線 → 驗證密碼 → 寫入 → 斷線」，每秒重複一次；
連線池先開好幾條常駐連線輪流使用，省掉每次重新連線與驗證。

- 第一次用到才建立：資料庫沒開時 backend 照樣能啟動，寫入失敗只印錯誤，資料庫恢復後自動連上
- 用法：
      async with acquire() as conn:
          await conn.fetch(...)
"""
import asyncio
from contextlib import asynccontextmanager

import asyncpg

from core.config import get_database_url

MIN_SIZE = 1
MAX_SIZE = 5
CONNECT_TIMEOUT = 5        # 秒；asyncpg 預設 60 秒，資料庫沒開時會卡太久

_pool: asyncpg.Pool | None = None
_lock: asyncio.Lock | None = None
_loop: asyncio.AbstractEventLoop | None = None
_closed = False            # 關機後不再重建，避免背景寫入任務又開出一個沒人關的連線池


async def get_pool() -> asyncpg.Pool:
    global _pool, _lock, _loop, _closed
    loop = asyncio.get_running_loop()
    # 連線池綁定建立它的 event loop；換了 loop（測試、腳本重跑）就重建
    if _loop is not loop:
        _pool, _lock, _loop, _closed = None, asyncio.Lock(), loop, False
    if _closed:
        raise RuntimeError("連線池已關閉（backend 正在關機）")
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is None:      # 等鎖的期間別人可能已經建好了
            _pool = await asyncpg.create_pool(
                get_database_url(), min_size=MIN_SIZE, max_size=MAX_SIZE, timeout=CONNECT_TIMEOUT
            )
    return _pool


@asynccontextmanager
async def acquire():
    pool = await get_pool()
    async with pool.acquire(timeout=CONNECT_TIMEOUT) as conn:
        yield conn


async def close_pool(timeout: float = 5) -> None:
    """關閉時呼叫：等還在用的連線還回來，超過 timeout 就強制關掉"""
    global _pool, _closed, _loop
    loop = asyncio.get_running_loop()
    if _loop is not loop:          # 從沒在這個 loop 用過：舊 loop 的連線池無法在這裡關，直接丟掉
        _pool, _loop = None, loop
    _closed = True
    pool, _pool = _pool, None
    if pool is None:
        return
    try:
        await asyncio.wait_for(pool.close(), timeout)
    except (asyncio.TimeoutError, Exception):
        pool.terminate()
