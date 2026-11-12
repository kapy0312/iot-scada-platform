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


def limits_snapshot(device_id: str, version: str = "") -> dict | None:
    """事件開始時的門檻（只存判斷用的正常範圍、警戒、危險；刻度只影響畫面，不存）"""
    profile = get_profile(device_id)
    if not profile:
        return None
    return {
        "version": version,
        "tags": {tag: {"normal": list(s["normal"]), "warning": s["warning"], "danger": s["danger"]}
                 for tag, s in profile["tags"].items()},
    }


def spec_at_time(spec: dict, snapshot: dict | None, tag: str) -> dict:
    """事件有存當時門檻就用當時的，沒有（010 之前的舊事件）就用現行的"""
    old = (snapshot or {}).get("tags", {}).get(tag)
    return {**spec, **old} if old else spec


def limit_diffs(profile: dict, snapshot: dict | None) -> list[dict]:
    """當時門檻跟現行不同的地方，例如 [{"tag": "temperature", "label": "溫度", "field": "warning", "then": 69, "now": 85}]"""
    out = []
    for tag, spec in (profile or {}).get("tags", {}).items():
        old = (snapshot or {}).get("tags", {}).get(tag)
        if not old:
            continue
        for field in ("normal", "warning", "danger"):
            then, now = old.get(field), spec.get(field)
            if then is not None and list(then if isinstance(then, list) else [then]) != list(now if isinstance(now, list) else [now]):
                out.append({"tag": tag, "label": spec.get("label", tag), "field": field, "then": then, "now": now})
    return out


FIELD_TEXT = {"normal": "正常範圍", "warning": "警戒值", "danger": "危險值"}


def diff_text(d: dict) -> str:
    """「溫度警戒值 69（現行 85）」"""
    fmt = lambda v: "–".join(f"{x:g}" for x in v) if isinstance(v, list) else f"{v:g}"
    return f"{d['label']}{FIELD_TEXT[d['field']]} {fmt(d['then'])}（現行 {fmt(d['now'])}）"


def new_incident(device_id: str, at: datetime, version: str = "") -> dict:
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
        "limits": limits_snapshot(device_id, version),   # 事件開始時的門檻（migration 010）
        "limits_changed": False,                          # 事件進行中門檻被改過
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
