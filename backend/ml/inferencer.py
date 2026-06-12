import os
import warnings
import joblib
import numpy as np
import pandas as pd
from collections import deque

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

class AnomalyDetector:
    def __init__(self, model_dir: str = "models", window: int = 30):
        self.model_dir = model_dir
        self.window    = window
        self.buffer    = deque(maxlen=window)
        self.pipeline  = None
        self._load_latest_model()

    def _load_latest_model(self):
        os.makedirs(self.model_dir, exist_ok=True)
        models = sorted([
            f for f in os.listdir(self.model_dir)
            if f.endswith(".pkl")
        ])
        if models:
            latest = os.path.join(self.model_dir, models[-1])
            self.pipeline = joblib.load(latest)
            print(f"[ML] 載入模型：{latest}")
        else:
            print("[ML] 沒有模型，進入資料收集模式")

    def reload_model(self):
        self._load_latest_model()

    def update(self, data: dict) -> dict:
        self.buffer.append({
            "motor_speed": data.get("motor_speed", 0),
            "temperature": data.get("temperature", 0),
            "pressure":    data.get("pressure", 0),
        })

        if self.pipeline is None:
            return {
                "is_anomaly": False,
                "score": 0.0,
                "status": "collecting_data",
            }

        if len(self.buffer) < self.window:
            return {
                "is_anomaly": False,
                "score": 0.0,
                "status": "warming_up",
                "remaining": self.window - len(self.buffer)
            }

        df = pd.DataFrame(list(self.buffer))
        features = {}
        for tag in ["motor_speed", "temperature", "pressure"]:
            s = df[tag]
            features[f"{tag}_mean"] = s.mean()
            features[f"{tag}_std"]  = s.std()
            features[f"{tag}_max"]  = s.max()
            features[f"{tag}_min"]  = s.min()
            features[f"{tag}_diff"] = s.diff().iloc[-1]

        X = np.array(list(features.values())).reshape(1, -1)
        score      = float(self.pipeline.decision_function(X)[0])
        is_anomaly = score < 0.15

        return {
            "is_anomaly": is_anomaly,
            "score":      round(score, 4),
            "status":     "anomaly" if is_anomaly else "normal",
        }