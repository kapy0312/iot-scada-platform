"""
每秒寫一筆機台狀態到 machine_status（需要先執行 db/migrations/005_machine_status.sql）

跟 plc_measurements 分開存：生產計數不會混進 10 秒彙整、雲端 measurements_10s 或 ML 訓練資料
"""
from datetime import datetime

from db.pool import acquire

_warned = False   # 表還沒建（還沒跑 005）時只提醒一次，不洗版


async def write_status(device_id: str, at: datetime, row: dict) -> None:
    """
    row：state、motor_enable、product_id、good_total、ng_total、good_delta、ng_delta、dt
    沒有生產變數的舊版 PLC 程式：state、product_id、累計值為 None，照樣記錄 motor_enable
    """
    global _warned
    try:
        async with acquire() as conn:
            await conn.execute("""
                INSERT INTO machine_status
                    (time, device_id, state, motor_enable, product_id,
                     good_total, ng_total, good_delta, ng_delta, dt)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, at, device_id, row.get("state"), row.get("motor_enable"), row.get("product_id"),
                row.get("good_total"), row.get("ng_total"), row["good_delta"], row["ng_delta"], row["dt"])
        _warned = False
    except Exception as e:
        if "machine_status" in str(e) and "does not exist" in str(e):
            if not _warned:
                print("[DB] machine_status 表不存在，請先執行 db/migrations/005_machine_status.sql")
                _warned = True
            return
        print(f"[DB] 機台狀態寫入失敗：{type(e).__name__}: {e}")
