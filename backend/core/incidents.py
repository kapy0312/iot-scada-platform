"""
事件（一次故障）的共用規則：合併間隔、嚴重度、等級、峰值累加
後端即時維護（plc_simulator）與回填腳本（db/backfill_incidents）都用這份，避免規則分岔
"""
from datetime import datetime

from core.device_profile import evaluate, get_profile

INCIDENT_GAP_SECONDS = 60   # 正常超過 60 秒後再異常，算新事件
LEVEL_ORDER = ["normal", "low", "high", "warning", "danger"]


# 診斷來源分類：讓讀資料的人（或 AI）分得出哪些診斷可信
# - 新版（Phase 0 起）由程式依訊號條件選章節，章節記在 ai_source；找不到章節時文字固定寫「無對應章節」
# - 舊版由模型自己寫「依據」，章節沒經過程式驗證，可能挑錯或編造
DIAGNOSIS_TYPES = {
    "verified": "程式依訊號條件選定手冊章節",
    "no_manual": "新版診斷，手冊中沒有符合條件的章節",
    "legacy": "舊版診斷：章節由模型自行引用，未經程式驗證，僅供參考",
    "none": "沒有 AI 診斷（事件太短、或當時 AI 服務沒有回應）",
}


def diagnosis_type(ai_analysis, ai_source) -> str:
    if not ai_analysis:
        return "none"
    if ai_source:
        return "verified"
    if "（依據：無對應章節）" in ai_analysis:
        return "no_manual"
    return "legacy"


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def severity_and_level(device_id: str, readings: dict) -> tuple[int, str]:
    """嚴重度＝各訊號等級加總（偏離的訊號越多、越嚴重分數越高）；等級＝最嚴重那個訊號的等級"""
    profile = get_profile(device_id)
    if not profile:
        return 0, "normal"
    total, worst = 0, "normal"
    for tag, spec in profile["tags"].items():
        v = readings.get(tag)
        if not _is_number(v):
            continue
        lv = evaluate(spec, float(v))
        total += LEVEL_ORDER.index(lv)
        if LEVEL_ORDER.index(lv) > LEVEL_ORDER.index(worst):
            worst = lv
    return total, worst


def new_incident(device_id: str, at: datetime) -> dict:
    return {
        "id": None,
        "device_id": device_id,
        "started_at": at,
        "ended_at": at,
        "records": 0,
        "level": "normal",
        "severity": 0,
        "peak": {"max": {}, "min": {}},
        "min_score": None,
        "ai_analysis": None,
        "ai_source": None,
        "diag_severity": -1,   # 目前這份診斷產生時的嚴重度；只在記憶體裡用，不寫入資料庫
    }


def absorb(incident: dict, readings: dict, severity: int, level: str, score, at: datetime) -> None:
    """把這一秒的讀值併入事件：結束時間、筆數、峰值、等級、最低分數"""
    profile = get_profile(incident["device_id"])
    tags = set(profile["tags"]) if profile else None
    incident["ended_at"] = at
    incident["records"] += 1
    for k, v in readings.items():
        if tags is not None and k not in tags:
            continue
        if not _is_number(v):
            continue
        v = float(v)
        incident["peak"]["max"][k] = max(incident["peak"]["max"].get(k, v), v)
        incident["peak"]["min"][k] = min(incident["peak"]["min"].get(k, v), v)
    incident["severity"] = max(incident["severity"], severity)
    if LEVEL_ORDER.index(level) > LEVEL_ORDER.index(incident["level"]):
        incident["level"] = level
    if _is_number(score):
        incident["min_score"] = score if incident["min_score"] is None else min(incident["min_score"], score)
