import asyncio
import asyncpg
import pandas as pd
import numpy as np
import joblib
import os
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

DATABASE_URL = "postgresql://postgres:iotscada123@localhost:5435/iotscada"

async def load_data() -> pd.DataFrame:
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT time, tag_name, value
        FROM plc_measurements
        WHERE device_id = 'S7-1511T'
        ORDER BY time
    """)
    await conn.close()

    df = pd.DataFrame(rows, columns=["time", "tag_name", "value"])
    # 長表轉寬表：每個 tag 變成一欄
    df_wide = df.pivot_table(
        index="time", columns="tag_name", values="value"
    ).reset_index()
    df_wide.columns.name = None
    return df_wide

def build_features(df: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """
    從原始數值建構統計特徵
    每個 tag 計算：mean、std、max、min、diff（變化率）
    """
    features = pd.DataFrame()
    tags = ["motor_speed", "temperature", "pressure"]

    for tag in tags:
        if tag not in df.columns:
            continue
        s = df[tag]
        features[f"{tag}_mean"] = s.rolling(window).mean()
        features[f"{tag}_std"]  = s.rolling(window).std()
        features[f"{tag}_max"]  = s.rolling(window).max()
        features[f"{tag}_min"]  = s.rolling(window).min()
        features[f"{tag}_diff"] = s.diff()

    return features.dropna()

async def train():
    print("📥 從資料庫載入資料...")
    df = await load_data()
    print(f"   資料筆數：{len(df)} 個時間點")

    print("🔧 建構特徵...")
    X = build_features(df)
    print(f"   特徵數量：{X.shape[1]}，樣本數：{X.shape[0]}")

    print("🤖 訓練 Isolation Forest...")
    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("model", IsolationForest(
            n_estimators=100,
            contamination=0.02,  # 預期約 2% 為異常
            random_state=42
        ))
    ])
    pipeline.fit(X)

    # 評估訓練結果
    scores = pipeline.decision_function(X)
    predictions = pipeline.predict(X)
    anomaly_count = (predictions == -1).sum()
    print(f"   偵測到異常樣本：{anomaly_count} 筆（{anomaly_count/len(X)*100:.1f}%）")
    print(f"   異常分數範圍：{scores.min():.3f} ~ {scores.max():.3f}")

    # 儲存模型
    os.makedirs("models", exist_ok=True)
    model_path = "models/S7-1511T_anomaly_v1.pkl"
    joblib.dump(pipeline, model_path)
    print(f"✅ 模型已儲存：{model_path}")

asyncio.run(train())