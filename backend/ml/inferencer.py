import os
import warnings
from collections import deque

from core.device_profile import ANOMALY_THRESHOLD
from ml.features import WINDOW, build_window_features
from ml.model_store import MODEL_DIR, latest_model, load_model

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

# 分數連續低於門檻幾秒才判定異常（告警延遲，避免單一秒的雜訊誤報）
ON_DELAY = int(os.getenv("ANOMALY_ON_DELAY", "5"))


class AnomalyDetector:
    """
    即時異常偵測：維持 30 筆滑動視窗，用模型記錄的訊號清單算特徵。
    - 模型用哪幾個訊號訓練，推論就只看那幾個；資料多出來的訊號不影響舊模型
    - 門檻跟著模型走（訓練時算好存在模型檔）；舊模型沿用 0.05
    - 分數要連續 ON_DELAY 秒低於門檻才算異常
    """

    def __init__(self, model_dir: str = MODEL_DIR, window: int = WINDOW, on_delay: int = ON_DELAY):
        self.model_dir = model_dir
        self.window = window
        self.on_delay = max(1, on_delay)
        self.buffer: deque = deque(maxlen=window)
        self.below = 0
        self.pipeline = None
        self.tags: list[str] = []
        self.threshold = ANOMALY_THRESHOLD
        self.info: dict = {}
        self._load_latest_model()

    def _load_latest_model(self):
        latest = latest_model(self.model_dir)
        if not latest:
            self.pipeline, self.tags, self.info = None, [], {}
            print("[ML] 沒有模型，進入資料收集模式")
            return
        model = load_model(latest["path"])
        self.pipeline = model["pipeline"]
        self.tags = model["tags"]
        self.threshold = float(model.get("threshold", ANOMALY_THRESHOLD))
        self.window = model.get("window") or self.window
        self.buffer = deque(self.buffer, maxlen=self.window)
        self.below = 0
        self.info = {
            "file": latest["file"],
            "trained_on": model.get("trained_on") or latest["trained_on"],
            "created_at": latest["created_at"].isoformat(timespec="seconds"),
            "tags": self.tags,
            "feature_count": len(self.tags) * 5,
            "samples": model.get("samples"),
            "window": self.window,
            "threshold": self.threshold,
            "on_delay_seconds": self.on_delay,
        }
        print(f"[ML] 載入模型：{latest['file']}（訊號 {len(self.tags)} 個，門檻 {self.threshold}）")

    def reload_model(self):
        """訓練完後熱更新，不需要重啟"""
        self._load_latest_model()

    def update(self, values: dict) -> dict:
        """values：這一秒所有訊號的讀值 {tag: value}"""
        base = {"threshold": self.threshold}
        if self.pipeline is None:
            self.buffer.append(dict(values))
            return {**base, "is_anomaly": False, "score": 0.0, "status": "collecting_data"}

        missing = [t for t in self.tags if values.get(t) is None]
        if missing:
            # 模型需要的訊號這次沒讀到（例如舊版 PLC 程式沒有這個變數）
            self.below = 0
            return {**base, "is_anomaly": False, "score": 0.0, "status": "model_mismatch", "missing": missing}

        self.buffer.append({t: float(values[t]) for t in self.tags})
        if len(self.buffer) < self.window:
            return {**base, "is_anomaly": False, "score": 0.0, "status": "warming_up",
                    "remaining": self.window - len(self.buffer)}

        X = build_window_features(list(self.buffer), self.tags)
        score = float(self.pipeline.decision_function(X)[0])
        self.below = self.below + 1 if score < self.threshold else 0
        is_anomaly = self.below >= self.on_delay
        status = "anomaly" if is_anomaly else ("suspect" if self.below else "normal")
        return {**base, "is_anomaly": is_anomaly, "score": round(score, 4), "status": status}
