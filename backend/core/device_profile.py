"""
設備基本資料：正常範圍、警戒值、危險值（來源：S7-1511T 設備維護手冊 第 1 節）

device_id 有兩種用途要分開：
- 資料來源：S7-1511T（真實）、S7-1511T-MOCK（內建模擬）、S7-1511T-SIM（PLCSIM）分開存，避免模擬值污染訓練集
- 手冊歸屬：手冊只跟「設備型號」有關，模擬資料也要查得到同一份手冊
"""

# 模擬來源的後綴：-MOCK（後端內建模擬）、-SIM（PLCSIM Advanced）
SIM_SUFFIXES = ("-MOCK", "-SIM")
ANOMALY_THRESHOLD = 0.05  # 與 ml/inferencer.py 判定門檻一致

DEVICE_PROFILES: dict[str, dict] = {
    "S7-1511T": {
        "name": "S7-1511T 馬達驅動系統",
        "tags": {
            "motor_speed": {"label": "馬達轉速", "unit": "RPM", "normal": [1400, 1600], "warning": 1650, "danger": 1750},
            "temperature": {"label": "溫度",     "unit": "°C",  "normal": [65, 75],     "warning": 85,   "danger": 100},
            "pressure":    {"label": "壓力",     "unit": "bar", "normal": [4.5, 5.5],   "warning": 7.0,  "danger": 9.0},
        },
    },
}


def base_device_id(device_id: str) -> str:
    """S7-1511T-MOCK、S7-1511T-SIM → S7-1511T；查手冊、查設備規格都用這個"""
    for suffix in SIM_SUFFIXES:
        if device_id.endswith(suffix):
            return device_id[: -len(suffix)]
    return device_id


def get_profile(device_id: str) -> dict | None:
    return DEVICE_PROFILES.get(base_device_id(device_id))
