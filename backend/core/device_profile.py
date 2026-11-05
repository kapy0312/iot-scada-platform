"""
設備規格：整個系統唯一的「訊號清單」

要新增訊號時只改這裡（再加上 PLC 的 DB1 變數與手冊章節），後端輪詢、寫入、推論、AI 診斷、
MCP、前端都會自動跟上。訊號數量改變後，異常偵測模型需要重新訓練（python -m ml.train）。

device_id 有兩種用途要分開：
- 資料來源：S7-1511T（真實）、S7-1511T-MOCK（內建模擬）、S7-1511T-SIM（PLCSIM）分開存，避免模擬值污染訓練集
- 手冊與規格歸屬：只跟「設備型號」有關，模擬資料也查同一份手冊與規格

每個訊號的欄位：
  label / unit       顯示名稱與單位
  normal             正常範圍 [下限, 上限]
  warning / danger   警戒值、危險值（超過即告警）
  node               PLC DB1 裡的變數名稱（OPC UA 讀取用）
  tag_no             儀表編號（畫面顯示用，依 ISA 慣例）
  scale              畫面刻度範圍 [最小, 最大]
  decimals           顯示小數位數
"""

SIM_SUFFIXES = ("-MOCK", "-SIM")  # -MOCK＝後端內建模擬，-SIM＝PLCSIM Advanced
ANOMALY_THRESHOLD = 0.05           # Isolation Forest 分數低於此值判定異常

DEVICE_PROFILES: dict[str, dict] = {
    "S7-1511T": {
        "name": "S7-1511T 馬達驅動系統",
        "tags": {
            # 來源：設備維護手冊（測試版）第 1 節
            "motor_speed": {"label": "馬達轉速", "unit": "RPM", "normal": [1400, 1600], "warning": 1650, "danger": 1750,
                            "node": "motor_speed", "tag_no": "ST-101", "scale": [1200, 1900], "decimals": 1},
            "temperature": {"label": "溫度", "unit": "°C", "normal": [65, 75], "warning": 85, "danger": 100,
                            "node": "temperature", "tag_no": "TT-102", "scale": [40, 120], "decimals": 1},
            "pressure":    {"label": "壓力", "unit": "bar", "normal": [4.5, 5.5], "warning": 7.0, "danger": 9.0,
                            "node": "pressure", "tag_no": "PT-103", "scale": [2, 11], "decimals": 2},
            # 來源：設備維護手冊（測試版3）第 1 節；振動參考 ISO 10816-3 第 2 類機器（剛性基礎）
            "vibration":   {"label": "振動", "unit": "mm/s", "normal": [0.5, 2.0], "warning": 2.8, "danger": 4.5,
                            "node": "vibration", "tag_no": "VT-104", "scale": [0, 6], "decimals": 2},
            # 電流規格為假設值：15 kW、400 V 馬達額定約 28 A，請依實際銘牌調整
            "current":     {"label": "馬達電流", "unit": "A", "normal": [20, 26], "warning": 28, "danger": 31,
                            "node": "current", "tag_no": "CT-105", "scale": [0, 36], "decimals": 1},
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


def get_tags(device_id: str) -> list[str]:
    """這台設備的訊號清單（順序固定，模型特徵也依這個順序）"""
    profile = get_profile(device_id)
    return list(profile["tags"]) if profile else []


def all_tags() -> set[str]:
    """所有設備的訊號聯集；寫入資料庫時用來過濾非數值欄位"""
    return {tag for p in DEVICE_PROFILES.values() for tag in p["tags"]}


def evaluate(spec: dict, value: float) -> str:
    """danger / warning / high / low / normal"""
    if value >= spec["danger"]:
        return "danger"
    if value >= spec["warning"]:
        return "warning"
    if value > spec["normal"][1]:
        return "high"
    if value < spec["normal"][0]:
        return "low"
    return "normal"


LEVEL_TEXT = {"danger": "超過危險值", "warning": "超過警戒值", "high": "偏高", "low": "偏低", "normal": "正常"}
