"""
MCP 工具共用的小工具：時間解析與格式、事件列的整理（本機 stdio 與雲端 Lambda 共用）
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mcp.server.mcpserver.exceptions import ToolError

from core.device_profile import LEVEL_TEXT, evaluate, get_profile
from core.incidents import DIAGNOSIS_TYPES, diagnosis_type, diff_text, limit_diffs, spec_at_time

LOCAL_TZ = ZoneInfo("Asia/Taipei")

INCIDENT_LEVEL_TEXT = {
    "danger": "危險", "warning": "警戒", "high": "偏高", "low": "偏低", "normal": "只有 ML 異常（訊號都在正常範圍）",
}
MATCH_TEXT = {"full": "條件完全符合", "partial": "部分符合", "reference": "參考章節"}
OUTCOME_TEXT = {"ongoing": "進行中", "reached": "真的到了（命中）", "cleared": "自己退了（未到）",
                "interrupted": "中途停機或門檻被改（不計）"}


def parse_time(value: str | None, default: datetime) -> datetime:
    """接受 '2026-11-04 14:00' 或 ISO 8601；沒寫時區就當台灣時間"""
    if not value:
        return default
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError:
        raise ToolError(f"時間格式看不懂：{value!r}，請用 \"2026-11-04 14:00\" 這種格式")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def fmt(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def rnd(v, n=2):
    return None if v is None else round(float(v), n)


def time_range(start: str | None, end: str | None, default_hours: int,
               max_hours: int | None = None) -> tuple[datetime, datetime]:
    now = datetime.now(LOCAL_TZ)
    end_dt = parse_time(end, now)
    start_dt = parse_time(start, end_dt - timedelta(hours=default_hours))
    if start_dt >= end_dt:
        raise ToolError(f"開始時間 {fmt(start_dt)} 必須早於結束時間 {fmt(end_dt)}")
    if max_hours and end_dt - start_dt > timedelta(hours=max_hours):
        raise ToolError(f"時間範圍最多 {max_hours} 小時（{max_hours // 24} 天），請縮小範圍")
    return start_dt, end_dt


def json_field(v):
    """asyncpg 預設把 jsonb 讀成字串"""
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else v


async def has_limit_columns(conn) -> bool:
    """事件表有沒有當時門檻欄位（本機 migration 010、雲端 005）"""
    return bool(await conn.fetchval("""
        SELECT count(*) = 2 FROM information_schema.columns
        WHERE table_name = 'anomaly_incidents' AND column_name IN ('limits', 'limits_changed')
    """))


def incident_view(r) -> dict:
    """事件表（anomaly_incidents）的一列 → 給 Claude 看的格式"""
    profile = get_profile(r["device_id"]) or {"tags": {}}
    snapshot = json_field(r.get("limits"))      # 事件開始時的門檻（migration 010；舊事件沒有）
    peak = json_field(r["peak"]) or {}
    pmax, pmin = peak.get("max", {}), peak.get("min", {})
    tags = list(profile["tags"]) or sorted(set(pmax) | set(pmin))
    signals = {}
    for tag in tags:
        hi, lo = pmax.get(tag), pmin.get(tag)
        if hi is None and lo is None:
            continue
        info = {"max": rnd(hi), "min": rnd(lo)}
        spec = profile["tags"].get(tag)
        if spec:
            spec = spec_at_time(spec, snapshot, tag)
            info["label"] = spec["label"]
            info["unit"] = spec["unit"]
            # 偏高的看最大值、偏低的看最小值，取比較嚴重的那一個
            levels = [evaluate(spec, v) for v in (hi, lo) if v is not None]
            order = ["normal", "low", "high", "warning", "danger"]
            info["peak_level"] = LEVEL_TEXT[max(levels, key=order.index)]
        signals[tag] = info

    src = json_field(r["ai_source"])
    diffs = limit_diffs(profile, snapshot)
    notes = []
    if diffs:
        notes.append("事件當時的門檻跟現在不同，上面的判定用當時的門檻：" + "、".join(diff_text(d) for d in diffs))
    if r.get("limits_changed"):
        notes.append("事件進行中門檻被修改過，判定以事件開始時的門檻為準")
    if snapshot is None:
        notes.append("這筆事件沒有記錄當時的門檻（migration 010 之前），判定用現行門檻")
    return {
        "incident_id": r["id"],
        "device_id": r["device_id"],
        "start": fmt(r["started_at"]),
        "end": fmt(r["ended_at"]),
        "duration_seconds": int((r["ended_at"] - r["started_at"]).total_seconds()) + 1,
        "records": r["records"],
        "level": INCIDENT_LEVEL_TEXT.get(r["level"], r["level"]),
        "lowest_anomaly_score": rnd(r["min_score"], 4),
        "signals": signals,
        # 事件中最嚴重時刻的 AI 診斷，以及它依據的手冊章節（由後端依訊號條件選定）
        "ai_analysis": r["ai_analysis"],
        "diagnosis_type": DIAGNOSIS_TYPES[diagnosis_type(r["ai_analysis"], src)],
        "manual_source": {
            "label": src.get("label"),
            "file": src.get("file"),
            "section": src.get("section"),
            "page": src.get("page"),
            "match": MATCH_TEXT.get(src.get("match_type")),
        } if src else None,
        "limits_note": "；".join(notes) or None,
    }
