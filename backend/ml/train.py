"""
手動訓練異常偵測模型

用法（在 backend 資料夾，conda 環境 iot-scada）：
    python -m ml.train --device S7-1511T-SIM --minutes 60
    python -m ml.train --device S7-1511T --days 30

    --device   用哪個資料來源訓練（S7-1511T＝真實、S7-1511T-SIM＝PLCSIM、S7-1511T-MOCK＝內建模擬）
    --minutes  取最近幾分鐘（與 --days 擇一，預設最近 30 天）
    --keep-anomalies  預設會排除異常事件前後 2 分鐘的資料，只用「正常」資料訓練；加上這個參數則不排除

停機與剛恢復運轉的資料一律不用（跟即時偵測的暫停規則相同，見 core/production.RunGate）：
依 machine_status 判斷；那段時間沒有 machine_status 紀錄（005 之前的舊資料）也不用。
整個區間都沒有 machine_status 時退回舊做法（全部使用）並提醒。

訓練完會存到 models/，後端可呼叫 POST /api/system/reload-model 立即換用，不必重啟。
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from core.device_profile import evaluate, get_profile, get_tags
from core.production import RunGate, is_running
from db.pool import acquire, close_pool
from ml.features import WINDOW, build_training_features
from ml.model_store import save_model

MIN_SAMPLES = 600          # 至少 10 分鐘的有效特徵列（建議 60 分鐘以上，誤報較少）
THRESHOLD_QUANTILE = 0.001 # 門檻＝訓練資料中最低的 0.1% 分數；正常資料幾乎不會低於它
EXCLUDE_MARGIN = timedelta(minutes=2)


async def load_wide(device_id: str, since: datetime, tags: list[str]) -> pd.DataFrame:
    """長表格 → 寬表格；時間取到秒，避免同一秒的訊號因毫秒差異被拆成兩列"""
    async with acquire() as conn:
        rows = await conn.fetch("""
            SELECT date_trunc('second', time) AS t, tag_name, AVG(value) AS value
            FROM plc_measurements
            WHERE device_id = $1 AND time >= $2 AND tag_name = ANY($3::text[])
            GROUP BY t, tag_name
            ORDER BY t
        """, device_id, since, tags)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["time", "tag_name", "value"])
    wide = df.pivot_table(index="time", columns="tag_name", values="value").sort_index()
    wide.columns.name = None
    return wide


async def load_anomaly_windows(device_id: str, since: datetime) -> list[tuple[datetime, datetime]]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT time FROM anomaly_events WHERE device_id = $1 AND time >= $2 ORDER BY time",
            device_id, since)
    return [(r["time"] - EXCLUDE_MARGIN, r["time"] + EXCLUDE_MARGIN) for r in rows]


def drop_windows(df: pd.DataFrame, windows: list[tuple[datetime, datetime]]) -> pd.DataFrame:
    if not windows or df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for start, end in windows:
        mask |= (df.index >= start) & (df.index <= end)
    return df[~mask]


async def load_status(device_id: str, since: datetime) -> pd.DataFrame:
    """每秒的機台狀態；表不存在（還沒跑 005）時回空表"""
    try:
        async with acquire() as conn:
            rows = await conn.fetch("""
                SELECT date_trunc('second', time) AS t, max(state) AS state, bool_or(motor_enable) AS motor_enable
                FROM machine_status
                WHERE device_id = $1 AND time >= $2
                GROUP BY t ORDER BY t
            """, device_id, since)
    except Exception as e:
        if "machine_status" in str(e):
            return pd.DataFrame()
        raise
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=["time", "state", "motor_enable"]).set_index("time")


def running_mask(df: pd.DataFrame, status: pd.DataFrame, device_id: str, tags: list[str]) -> pd.Series:
    """
    逐秒重播即時偵測的暫停規則，回傳「這一秒可以拿來訓練」的布林值
    沒有狀態紀錄的秒數當成停機（保守：接下來也要等恢復期）
    「所有訊號正常」跟 device_profile.evaluate 回傳 normal 的條件相同，先整欄算好再逐秒重播
    """
    specs = (get_profile(device_id) or {"tags": {}})["tags"]
    st = status.reindex(df.index)

    all_normal = np.ones(len(df), dtype=bool)
    for tag in tags:
        spec = specs.get(tag)
        if spec is None:
            continue
        v = df[tag].to_numpy(dtype=float)
        lo, hi = spec["normal"]
        all_normal &= (v >= lo) & (v <= hi) & (v < spec["warning"]) & (v < spec["danger"])

    states = st["state"].to_numpy(dtype=float)
    enables = st["motor_enable"].astype("float").to_numpy()
    times = [t.timestamp() for t in df.index]

    gate = RunGate()
    keep = np.zeros(len(df), dtype=bool)
    for i in range(len(df)):
        has_status = not (np.isnan(states[i]) and np.isnan(enables[i]))
        state = None if np.isnan(states[i]) else int(states[i])
        enable = None if np.isnan(enables[i]) else int(enables[i])
        running = has_status and is_running(state, enable)
        keep[i] = gate.update(running, bool(all_normal[i]), times[i])["status"] == "active"
    return pd.Series(keep, index=df.index)


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

    status = await load_status(device_id, since)
    if status.empty:
        print("[Train] ⚠ 這段時間沒有 machine_status 紀錄，無法排除停機資料（模型可能把停機當成正常）；"
              "建議 005 上線、累積 60 分鐘以上運轉資料後再訓練")
    elif not df.empty:
        before = len(df)
        df = df[running_mask(df, status, device_id, tags)]
        print(f"[Train] 排除停機、恢復期與沒有狀態紀錄的時段：{before} → {len(df)} 筆")

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
    asyncio.run(_run_cli(args.device, since, args.keep_anomalies))


async def _run_cli(device_id: str, since: datetime, keep_anomalies: bool):
    try:
        await train(device_id, since, keep_anomalies)
    finally:
        await close_pool()


if __name__ == "__main__":
    main()
