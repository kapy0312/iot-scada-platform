"""
自動再訓練：每小時檢查一次，只用「真實設備」的資料（S7-1511T），模擬資料不會自動拿來訓練。
模擬資料要訓練請手動執行 python -m ml.train --device S7-1511T-SIM
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg

from core.device_profile import get_tags
from ml.model_store import list_models
from ml.train import train

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:iotscada123@localhost:5435/iotscada")
DEVICE_ID = os.getenv("AUTO_TRAIN_DEVICE_ID", "S7-1511T")
MIN_ROWS = 5000            # 最近 30 天每個訊號至少要有這麼多筆
TRAIN_DAYS = 30
RETRAIN_DAYS = 1


async def count_rows_per_tag(tags: list[str]) -> dict[str, int]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("""
            SELECT tag_name, COUNT(*) AS n FROM plc_measurements
            WHERE device_id = $1 AND time > NOW() - INTERVAL '30 days' AND tag_name = ANY($2::text[])
            GROUP BY tag_name
        """, DEVICE_ID, tags)
    finally:
        await conn.close()
    counts = {r["tag_name"]: r["n"] for r in rows}
    return {t: counts.get(t, 0) for t in tags}


async def should_retrain() -> tuple[bool, str]:
    tags = get_tags(DEVICE_ID)
    counts = await count_rows_per_tag(tags)
    lacking = {t: n for t, n in counts.items() if n < MIN_ROWS}
    if lacking:
        detail = "、".join(f"{t} {n}/{MIN_ROWS}" for t, n in lacking.items())
        return False, f"{DEVICE_ID} 資料不足（{detail}）"

    own = [m for m in list_models() if m["trained_on"] == DEVICE_ID]
    if not own:
        return True, f"還沒有 {DEVICE_ID} 的模型，首次訓練"
    days_old = (datetime.now().date() - own[-1]["created_at"].date()).days
    if days_old >= RETRAIN_DAYS:
        return True, f"{DEVICE_ID} 模型已 {days_old} 天未更新"
    return False, "模型是今天訓練的"


async def auto_retrain_loop(detector):
    print(f"[AutoTrainer] 自動訓練排程啟動（資料來源 {DEVICE_ID}）")
    while True:
        try:
            need, reason = await should_retrain()
            print(f"[AutoTrainer] 檢查：{reason}")
            if need:
                since = datetime.now(timezone.utc) - timedelta(days=TRAIN_DAYS)
                if await train(DEVICE_ID, since):
                    detector.reload_model()
                    print("[AutoTrainer] 推論器已更新為最新模型")
        except Exception as e:
            print(f"[AutoTrainer] 錯誤：{e}")
        await asyncio.sleep(3600)
