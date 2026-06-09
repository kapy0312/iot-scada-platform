import joblib
import numpy as np
import pandas as pd
from collections import deque

class AnomalyDetector:
    def __init__(self, model_path: str, window: int = 30):
        self.pipeline = joblib.load(model_path)
        self.window = window
        # 維持一個滑動視窗，存最近 N 筆資料
        self.buffer = deque(maxlen=window)
        self.is_ready = False

    def update(self, data: dict) -> dict:
        """
        每次收到新的 PLC 資料時呼叫
        data 格式：{"motor_speed": 1480.2, "temperature": 70.1, "pressure": 5.0}
        """
        self.buffer.append({
            "motor_speed": data.get("motor_speed", 0),
            "temperature": data.get("temperature", 0),
            "pressure":    data.get("pressure", 0),
        })

        # buffer 還沒填滿，暖機中
        if len(self.buffer) < self.window:
            return {
                "is_anomaly": False,
                "score": 0.0,
                "status": "warming_up",
                "remaining": self.window - len(self.buffer)
            }

        # 計算特徵
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

        score = float(self.pipeline.decision_function(X)[0])
        is_anomaly = score < 0.05  # 閾值，可調整

        return {
            "is_anomaly": is_anomaly,
            "score": round(score, 4),
            "status": "anomaly" if is_anomaly else "normal",
        }