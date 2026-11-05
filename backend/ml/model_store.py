"""
模型檔的存取規則（訓練、自動訓練、推論共用）

檔名：<資料來源 device_id>_anomaly_<YYYYMMDD_HHMMSS>.pkl
內容：{"pipeline", "tags", "window", "threshold", "trained_on", "created_at", "samples"}
舊版檔案只有 pipeline 本身，視為 3 個訊號（轉速、溫度、壓力）、門檻 0.05 的模型。

「最新」一律看檔名裡的時間，不看整個檔名排序：
S7-1511T-SIM_anomaly_… 依字母會排在 S7-1511T_anomaly_… 前面，直接排序會挑錯模型。
"""
import os
import re
from datetime import datetime

import joblib

from core.device_profile import ANOMALY_THRESHOLD

MODEL_DIR = os.getenv("MODEL_DIR", "models")
LEGACY_TAGS = ["motor_speed", "temperature", "pressure"]
_PATTERN = re.compile(r"^(?P<device>.+)_anomaly_(?P<ts>\d{8}_\d{6})\.pkl$")


def list_models(model_dir: str = MODEL_DIR) -> list[dict]:
    """依時間由舊到新排列；檔名不符規則的（例如 *_anomaly_v1.pkl）不列入"""
    if not os.path.isdir(model_dir):
        return []
    found = []
    for name in os.listdir(model_dir):
        m = _PATTERN.match(name)
        if m:
            found.append({
                "file": name,
                "path": os.path.join(model_dir, name),
                "trained_on": m.group("device"),
                "created_at": datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S"),
            })
    return sorted(found, key=lambda x: x["created_at"])


def latest_model(model_dir: str = MODEL_DIR) -> dict | None:
    models = list_models(model_dir)
    return models[-1] if models else None


def load_model(path: str) -> dict:
    obj = joblib.load(path)
    if isinstance(obj, dict) and "pipeline" in obj:
        return obj
    # 舊版：檔案裡只有 Pipeline
    return {"pipeline": obj, "tags": list(LEGACY_TAGS), "window": 30, "threshold": ANOMALY_THRESHOLD,
            "trained_on": None, "samples": None}


def save_model(pipeline, tags: list[str], window: int, trained_on: str, samples: int, threshold: float,
               model_dir: str = MODEL_DIR) -> str:
    os.makedirs(model_dir, exist_ok=True)
    now = datetime.now()
    path = os.path.join(model_dir, f"{trained_on}_anomaly_{now.strftime('%Y%m%d_%H%M%S')}.pkl")
    joblib.dump({
        "pipeline": pipeline,
        "tags": list(tags),
        "window": window,
        "threshold": threshold,
        "trained_on": trained_on,
        "created_at": now.isoformat(timespec="seconds"),
        "samples": samples,
    }, path)
    return path
