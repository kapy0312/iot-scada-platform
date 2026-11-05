"""
特徵工程：訓練與即時推論共用同一份，避免兩邊算法不一致

每個訊號在 30 筆滑動視窗內算 5 個統計量：平均、標準差、最大、最小、最後一筆的變化量。
訊號清單由 device_profile 決定，3 個訊號＝15 個特徵，5 個訊號＝25 個特徵。
"""
import pandas as pd

WINDOW = 30
STATS = ("mean", "std", "max", "min", "diff")


def feature_names(tags: list[str]) -> list[str]:
    return [f"{tag}_{stat}" for tag in tags for stat in STATS]


def build_training_features(df: pd.DataFrame, tags: list[str], window: int = WINDOW) -> pd.DataFrame:
    """df：寬表格（每列一個時間點、每個訊號一欄），回傳每個時間點的特徵"""
    cols = {}
    for tag in tags:
        s = df[tag]
        cols[f"{tag}_mean"] = s.rolling(window).mean()
        cols[f"{tag}_std"] = s.rolling(window).std()
        cols[f"{tag}_max"] = s.rolling(window).max()
        cols[f"{tag}_min"] = s.rolling(window).min()
        cols[f"{tag}_diff"] = s.diff()
    return pd.DataFrame(cols)[feature_names(tags)].dropna()


def build_window_features(rows: list[dict], tags: list[str]) -> pd.DataFrame:
    """rows：滑動視窗內的讀值（舊到新），回傳 1 列特徵，欄位順序與訓練時相同"""
    df = pd.DataFrame(rows)
    values = {}
    for tag in tags:
        s = df[tag]
        values[f"{tag}_mean"] = s.mean()
        values[f"{tag}_std"] = s.std()
        values[f"{tag}_max"] = s.max()
        values[f"{tag}_min"] = s.min()
        values[f"{tag}_diff"] = s.diff().iloc[-1]
    return pd.DataFrame([values])[feature_names(tags)]
