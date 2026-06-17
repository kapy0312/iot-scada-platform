import asyncio
import asyncpg
import os
import joblib
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

MODEL_DIR    = "models"
DEVICE_ID    = "S7-1511T"
MIN_ROWS     = 5000
RETRAIN_DAYS = 1

async def count_rows() -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM plc_measurements WHERE device_id = $1",
        DEVICE_ID
    )
    await conn.close()
    return count

async def load_training_data() -> pd.DataFrame:
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT time, tag_name, value
        FROM plc_measurements
        WHERE device_id = $1
          AND time > NOW() - INTERVAL '30 days'
        ORDER BY time
    """, DEVICE_ID)
    await conn.close()

    df = pd.DataFrame(rows, columns=["time", "tag_name", "value"])
    df_wide = df.pivot_table(
        index="time", columns="tag_name", values="value"
    ).reset_index()
    df_wide.columns.name = None
    return df_wide

def build_features(df: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    features = pd.DataFrame()
    for tag in ["motor_speed", "temperature", "pressure"]:
        if tag not in df.columns:
            continue
        s = df[tag]
        features[f"{tag}_mean"] = s.rolling(window).mean()
        features[f"{tag}_std"]  = s.rolling(window).std()
        features[f"{tag}_max"]  = s.rolling(window).max()
        features[f"{tag}_min"]  = s.rolling(window).min()
        features[f"{tag}_diff"] = s.diff()
    return features.dropna()

async def train_model() -> str:
    print(f"[AutoTrainer] 開始訓練...")
    df = await load_training_data()
    X  = build_features(df)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("model",  IsolationForest(
            n_estimators=100,
            contamination=0.02,
            random_state=42
        ))
    ])
    pipeline.fit(X)

    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(MODEL_DIR, f"{DEVICE_ID}_anomaly_{timestamp}.pkl")
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(pipeline, model_path)

    print(f"[AutoTrainer] ✅ 模型儲存：{model_path}（{len(X)} 筆）")
    return model_path

async def should_retrain() -> tuple[bool, str]:
    count = await count_rows()
    if count < MIN_ROWS:
        return False, f"資料不足（{count}/{MIN_ROWS} 筆）"

    models = sorted([
        f for f in os.listdir(MODEL_DIR)
        if f.endswith(".pkl")
    ]) if os.path.exists(MODEL_DIR) else []

    if not models:
        return True, "沒有模型，首次訓練"

    try:
        latest   = models[-1]
        date_str = latest.split("_anomaly_")[1][:8]
        model_date = datetime.strptime(date_str, "%Y%m%d").date()
        days_old   = (datetime.now().date() - model_date).days
        if days_old >= RETRAIN_DAYS:
            return True, f"模型已 {days_old} 天未更新"
        return False, "模型是今天訓練的"
    except Exception:
        return True, "無法解析模型日期，重新訓練"

async def auto_retrain_loop(detector):
    print("[AutoTrainer] 自動訓練排程啟動")
    while True:
        try:
            need, reason = await should_retrain()
            print(f"[AutoTrainer] 檢查：{reason}")
            if need:
                await train_model()
                detector.reload_model()
                print("[AutoTrainer] 推論器已更新為最新模型")
        except Exception as e:
            print(f"[AutoTrainer] 錯誤：{e}")

        await asyncio.sleep(3600)  # 每小時檢查一次