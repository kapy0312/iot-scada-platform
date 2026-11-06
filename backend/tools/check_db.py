"""
檢查資料庫連線：.env 有沒有讀到、密碼對不對、最近有沒有資料寫進來
用法（在 backend 資料夾）：python -m tools.check_db
"""
import asyncio
import sys

import asyncpg

from core.config import ConfigError, get_database_url, masked


async def main() -> int:
    try:
        url = get_database_url()
    except ConfigError as e:
        print(f"[失敗] {e}")
        return 1
    print(f"連線目標：{masked(url)}")
    try:
        conn = await asyncpg.connect(url, timeout=5)
    except asyncpg.InvalidPasswordError:
        print("[失敗] 密碼錯誤：.env 的 POSTGRES_PASSWORD 和資料庫裡的密碼不一致")
        return 1
    except Exception as e:
        print(f"[失敗] 連不上資料庫（{type(e).__name__}）：確認 timescaledb 容器已啟動")
        return 1
    try:
        latest = await conn.fetchval("SELECT max(time) FROM plc_measurements")
        recent = await conn.fetchval(
            "SELECT count(*) FROM plc_measurements WHERE time > now() - interval '1 minute'")
    finally:
        await conn.close()
    print(f"[成功] 最新一筆資料時間：{latest}，最近 1 分鐘寫入 {recent} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
