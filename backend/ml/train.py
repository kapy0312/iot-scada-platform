"""
手動訓練異常偵測模型

用法（在 backend 資料夾，conda 環境 iot-scada）：
    python -m ml.train --device S7-1511T-SIM --minutes 60
    python -m ml.train --device S7-1511T --days 30

    --device   用哪個資料來源訓練（S7-1511T＝真實、S7-1511T-SIM＝PLCSIM、S7-1511T-MOCK＝內建模擬）
    --minutes  取最近幾分鐘（與 --days 擇一，預設最近 30 天）
    --keep-anomalies  預設會排除異常事件前後 2 分鐘的資料，只用「正常」資料訓練；加上這個參數則不排除

訓練完會存到 models/，後端可呼叫 POST /api/system/reload-model 立即換用，不必重啟。
"""
import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from core.device_profile import get_tags
from ml.features import WINDOW, build_training_features
from ml.model_store import save_model

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:iotscada123@localhost:5435/iotscada")
MIN_SAMPLES = 600          # 至少 10 分鐘的有效特徵列（建議 60 分鐘以上，誤報較少）
THRESHOLD_QUANTILE = 0.001 # 門檻＝訓練資料中最低的 0.1% 分數；正常資料幾乎不會低於它
EXCLUDE_MARGIN = timedelta(minutes=2)


async def load_wide(device_id: str, since: datetime, tags: list[str]) -> pd.DataFrame:
    """長表格 → 寬表格；時間取到秒，避免同一秒的訊號因毫秒差異被拆成兩列"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("""
            SELECT date_trunc('second', time) AS t, tag_name, AVG(value) AS value
            FROM plc_measurements
            WHERE device_id = $1 AND time >= $2 AND tag_name = ANY($3::text[])
            GROUP BY t, tag_name
            ORDER BY t
        """, device_id, since, tags)
    finally:
        await conn.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["time", "tag_name", "value"])
    wide = df.pivot_table(index="time", columns="tag_name", values="value").sort_index()
    wide.columns.name = None
    return wide


async def load_anomaly_windows(device_id: str, since: datetime) -> list[tuple[datetime, datetime]]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT time FROM anomaly_events WHERE device_id = $1 AND time >= $2 ORDER BY time",
            device_id, since)
    finally:
        await conn.close()
    return [(r["time"] - EXCLUDE_MARGIN, r["time"] + EXCLUDE_MARGIN) for r in rows]


def drop_windows(df: pd.DataFrame, windows: list[tuple[datetime, datetime]]) -> pd.DataFrame:
    if not windows or df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for start, end in windows:
        mask |= (df.index >= start) & (df.index <= end)
    return df[~mask]


def fit(X: pd.DataFrame) -> Pipeline:
    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("model", IsolationForest(n_estimators=100, contamination=0.02, random_state=42)),
    ])
    pipeline.fit(X)
    return pipeline


async def train(device_id: str, since: datetime, keep_anomalies: bool = False) -> str | None:
    tags = get_tags(device_id)
    print(f"[Train] 資料來源 {device_id}，訊號 {len(tags)} 個：{', '.join(tags)}")
    df = await load_wide(device_id, since, tags)
    if df.empty:
        print("[Train] ❌ 沒有資料")
        return None

    missing = [t for t in tags if t not in df.columns]
    if missing:
        print(f"[Train] ❌ 資料缺少訊號 {missing}，請確認 PLC 已提供這些變數")
        return None

    total = len(df)
    df = df.dropna(subset=tags)
    if not keep_anomalies:
        df = drop_windows(df, await load_anomaly_windows(device_id, since))
    print(f"[Train] 時間點 {total} 筆，排除缺值與異常時段後剩 {len(df)} 筆")

    X = build_training_features(df.reset_index(drop=True), tags, WINDOW)
    if len(X) < MIN_SAMPLES:
        print(f"[Train] ❌ 有效樣本 {len(X)} 筆，至少需要 {MIN_SAMPLES} 筆（約 10 分鐘的正常運轉資料）")
        return None

    pipeline = fit(X)
    scores = pipeline.decision_function(X)
    threshold = round(float(np.quantile(scores, THRESHOLD_QUANTILE)), 4)
    print(f"[Train] 特徵 {X.shape[1]} 個，樣本 {len(X)} 筆，分數範圍 {scores.min():.3f} ~ {scores.max():.3f}")
    print(f"[Train] 異常門檻 {threshold}（訓練資料最低 {THRESHOLD_QUANTILE:.1%} 的分數）")
    if len(X) < 3000:
        print("[Train] 提醒：有效樣本少於約 50 分鐘，正常運轉時可能較常誤報，建議收集 60 分鐘以上再訓練")
    path = save_model(pipeline, tags, WINDOW, device_id, len(X), threshold)
    print(f"[Train] ✅ 模型已儲存：{path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="訓練異常偵測模型")
    parser.add_argument("--device", default="S7-1511T")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--minutes", type=int)
    group.add_argument("--days", type=int, default=30)
    parser.add_argument("--keep-anomalies", action="store_true")
    args = parser.parse_args()

    delta = timedelta(minutes=args.minutes) if args.minutes else timedelta(days=args.days)
    since = datetime.now(timezone.utc) - delta
    asyncio.run(train(args.device, since, args.keep_anomalies))


if __name__ == "__main__":
    main()
