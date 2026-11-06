"""
IIoT SCADA MCP Server（本機、唯讀）

由 Claude Desktop 以 stdio 方式啟動，讓 Claude 能查：
  異常事件 → 前後趨勢 → 對應手冊章節 → 設備規格
刻意不提供任何寫入 PLC 或修改資料的工具。

手動測試（不經過 Claude）：python tools/test_mcp_tools.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Claude Desktop 啟動時的工作目錄不固定，先把 backend/ 放進 import 路徑
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import asyncpg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from core.device_profile import ANOMALY_THRESHOLD, DEVICE_PROFILES, LEVEL_TEXT, base_device_id, evaluate, get_profile
from rag.embedder import embed_text
from rag.qdrant_client import list_documents, search as qdrant_search

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)
LOCAL_TZ = ZoneInfo("Asia/Taipei")
MAX_TREND_POINTS = 240      # 趨勢最多回傳幾個時間點，避免塞爆對話

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

server = MCPServer(
    name="iot-scada",
    instructions=(
        "這是 IIoT SCADA 平台的唯讀查詢工具，時間一律使用台灣時間（Asia/Taipei）。\n"
        "device_id：S7-1511T 是真實 PLC，S7-1511T-MOCK 是後端內建模擬，S7-1511T-SIM 是 PLCSIM 模擬，三者共用同一份手冊。\n"
        "分析異常的建議順序：\n"
        "1. list_anomalies 找出事件與時間（每個事件附 AI 診斷與它依據的手冊章節、頁碼）\n"
        "2. get_trend 看事件前後的趨勢（例如前後各 30 分鐘），判斷哪個訊號先變化\n"
        "3. get_device_info 對照正常範圍、警戒值、危險值\n"
        "4. search_manual 依現象查手冊，回答時引用手冊的章節與頁碼\n"
        "手冊沒有寫到的內容，要明確說明是推論，不要假裝來自手冊。"
    ),
)


# ---------- 共用小工具 ----------

def _parse_time(value: str | None, default: datetime) -> datetime:
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


def _fmt(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _round(v, n=2):
    return None if v is None else round(float(v), n)


def _time_range(start: str | None, end: str | None, default_hours: int) -> tuple[datetime, datetime]:
    now = datetime.now(LOCAL_TZ)
    end_dt = _parse_time(end, now)
    start_dt = _parse_time(start, end_dt - timedelta(hours=default_hours))
    if start_dt >= end_dt:
        raise ToolError(f"開始時間 {_fmt(start_dt)} 必須早於結束時間 {_fmt(end_dt)}")
    return start_dt, end_dt


async def _connect_db():
    try:
        return await asyncpg.connect(DATABASE_URL, timeout=5)
    except Exception as e:
        raise ToolError(
            f"無法連線 TimescaleDB（{type(e).__name__}）。請確認 timescaledb 容器已啟動："
            "docker start timescaledb"
        ) from e


async def _qdrant_call(coro):
    try:
        return await coro
    except Exception as e:
        raise ToolError(
            f"無法查詢手冊向量庫（{type(e).__name__}）。請確認 scada-qdrant 容器已啟動（port 6343）："
            "docker start scada-qdrant"
        ) from e


async def _embed(text: str) -> list[float]:
    try:
        return await embed_text(text)
    except Exception as e:
        raise ToolError(
            f"無法呼叫 bge-m3 向量化（{type(e).__name__}）。請確認桌機 Ollama 已開啟、Tailscale 已連線"
        ) from e


INCIDENT_LEVEL_TEXT = {
    "danger": "危險", "warning": "警戒", "high": "偏高", "low": "偏低", "normal": "只有 ML 異常（訊號都在正常範圍）",
}


def _json_field(v):
    """asyncpg 預設把 jsonb 讀成字串"""
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else v


def _incident_view(r) -> dict:
    """事件表（anomaly_incidents）的一列 → 給 Claude 看的格式"""
    profile = get_profile(r["device_id"]) or {"tags": {}}
    peak = _json_field(r["peak"]) or {}
    pmax, pmin = peak.get("max", {}), peak.get("min", {})
    tags = list(profile["tags"]) or sorted(set(pmax) | set(pmin))
    signals = {}
    for tag in tags:
        hi, lo = pmax.get(tag), pmin.get(tag)
        if hi is None and lo is None:
            continue
        info = {"max": _round(hi), "min": _round(lo)}
        spec = profile["tags"].get(tag)
        if spec:
            info["label"] = spec["label"]
            info["unit"] = spec["unit"]
            # 偏高的看最大值、偏低的看最小值，取比較嚴重的那一個
            levels = [evaluate(spec, v) for v in (hi, lo) if v is not None]
            order = ["normal", "low", "high", "warning", "danger"]
            info["peak_level"] = LEVEL_TEXT[max(levels, key=order.index)]
        signals[tag] = info

    src = _json_field(r["ai_source"])
    return {
        "incident_id": r["id"],
        "device_id": r["device_id"],
        "start": _fmt(r["started_at"]),
        "end": _fmt(r["ended_at"]),
        "duration_seconds": int((r["ended_at"] - r["started_at"]).total_seconds()) + 1,
        "records": r["records"],
        "level": INCIDENT_LEVEL_TEXT.get(r["level"], r["level"]),
        "lowest_anomaly_score": _round(r["min_score"], 4),
        "signals": signals,
        # 事件中最嚴重時刻的 AI 診斷，以及它依據的手冊章節（由後端依訊號條件選定）
        "ai_analysis": r["ai_analysis"],
        "manual_source": {
            "label": src.get("label"),
            "file": src.get("file"),
            "section": src.get("section"),
            "page": src.get("page"),
            "match": {"full": "條件完全符合", "partial": "部分符合", "reference": "參考章節"}.get(src.get("match_type")),
        } if src else None,
    }


# ---------- MCP 工具 ----------

@server.tool(annotations=READ_ONLY)
async def list_anomalies(
    device_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    hours: int = 24,
) -> dict:
    """
    列出指定時段內的異常事件（一次故障一筆，由後端即時維護）。

    參數：
    - device_id：S7-1511T（真實）、S7-1511T-MOCK（內建模擬）或 S7-1511T-SIM（PLCSIM）；不填則查全部設備
    - start / end：台灣時間，例如 "2026-11-04 08:00"；不填 end 就是現在
    - hours：只填 end 或都不填時，往前查幾小時（預設 24）

    每個事件含：起訖時間、持續秒數、等級、最低異常分數（越低越異常，門檻依模型而定）、
    每個訊號的最高與最低值（含對照手冊規格的判定）、事件中最嚴重時刻的 AI 診斷，
    以及該診斷依據的手冊章節與頁碼（manual_source）。
    """
    start_dt, end_dt = _time_range(start, end, hours)
    sql = """
        SELECT id, device_id, started_at, ended_at, records, level, severity,
               peak, min_score, ai_analysis, ai_source
        FROM anomaly_incidents
        WHERE started_at < $2 AND ended_at >= $1
    """
    args: list = [start_dt, end_dt]
    if device_id:
        sql += " AND device_id = $3"
        args.append(device_id)
    sql += " ORDER BY started_at DESC LIMIT 21"

    conn = await _connect_db()
    try:
        rows = await conn.fetch(sql, *args)
    finally:
        await conn.close()

    incidents = [_incident_view(r) for r in rows[:20]]
    return {
        "range": {"start": _fmt(start_dt), "end": _fmt(end_dt), "timezone": "Asia/Taipei"},
        "incident_count": len(incidents),
        "incidents": incidents,
        "note": "只列出最近 20 次，要看更早的請縮小時間範圍" if len(rows) > 20 else None,
    }


@server.tool(annotations=READ_ONLY)
async def get_trend(
    device_id: str,
    start: str,
    end: str,
    bucket_minutes: int = 1,
) -> dict:
    """
    查詢指定時段的訊號趨勢，以時間區間彙總（每段的平均、最低、最高）。

    參數：
    - device_id：S7-1511T 或 S7-1511T-MOCK
    - start / end：台灣時間，例如 "2026-11-04 13:30"、"2026-11-04 14:30"
    - bucket_minutes：每幾分鐘彙總成一點（預設 1）；時段太長時會自動放大，最多回傳 240 點

    用途：看異常發生前，哪個訊號先開始偏離、偏離得多快。
    """
    start_dt, end_dt = _time_range(start, end, 1)
    total_minutes = (end_dt - start_dt).total_seconds() / 60
    bucket = max(1, bucket_minutes, int(total_minutes // MAX_TREND_POINTS) + 1)

    conn = await _connect_db()
    try:
        rows = await conn.fetch("""
            SELECT time_bucket($1::interval, time) AS bucket,
                   tag_name,
                   AVG(value) AS avg_v, MIN(value) AS min_v, MAX(value) AS max_v
            FROM plc_measurements
            WHERE device_id = $2 AND time >= $3 AND time < $4
            GROUP BY bucket, tag_name
            ORDER BY bucket
        """, timedelta(minutes=bucket), device_id, start_dt, end_dt)
    finally:
        await conn.close()

    points: dict[datetime, dict] = {}
    overall: dict[str, dict] = {}
    for r in rows:
        p = points.setdefault(r["bucket"], {"time": _fmt(r["bucket"])})
        p[r["tag_name"]] = {"avg": _round(r["avg_v"]), "min": _round(r["min_v"]), "max": _round(r["max_v"])}
        o = overall.setdefault(r["tag_name"], {"min": r["min_v"], "max": r["max_v"]})
        o["min"], o["max"] = min(o["min"], r["min_v"]), max(o["max"], r["max_v"])

    return {
        "device_id": device_id,
        "range": {"start": _fmt(start_dt), "end": _fmt(end_dt), "timezone": "Asia/Taipei"},
        "bucket_minutes": bucket,
        "point_count": len(points),
        "overall": {k: {"min": _round(v["min"]), "max": _round(v["max"])} for k, v in overall.items()},
        "points": list(points.values()),
        "note": None if points else "這個時段沒有資料，請確認 device_id 與時間範圍",
    }


@server.tool(annotations=READ_ONLY)
async def search_manual(query: str, device_id: str = "S7-1511T", top_k: int = 5) -> dict:
    """
    用語意搜尋設備維護手冊，回傳最相關的章節內容。

    參數：
    - query：描述現象，例如「溫度持續上升 壓力偏高 可能原因與處置」
    - device_id：S7-1511T 或 S7-1511T-MOCK（兩者查同一份手冊）
    - top_k：回傳幾段（預設 5，最多 10）

    每段含檔名、章節、頁碼、相似度。回答時請引用章節與頁碼。
    只會搜尋前端有勾選啟用的手冊。
    """
    top_k = max(1, min(top_k, 10))
    manual_device = base_device_id(device_id)
    vector = await _embed(query)
    results = await _qdrant_call(qdrant_search(vector, device_id=manual_device, top_k=top_k))
    return {
        "device_id": manual_device,
        "query": query,
        "results": [
            {
                "score": round(r.score, 4),
                "source_filename": r.payload.get("source_filename"),
                "section": r.payload.get("section") or "（舊版切塊，無章節資訊，請重新上傳手冊）",
                "page": r.payload.get("page"),
                "text": r.payload.get("text", ""),
            }
            for r in results
        ],
        "note": None if results else "找不到手冊內容：可能尚未上傳，或已在前端取消勾選",
    }


@server.tool(annotations=READ_ONLY)
async def get_device_info(device_id: str = "S7-1511T") -> dict:
    """
    查詢設備規格（各訊號的正常範圍、警戒值、危險值）、異常判定門檻、
    資料庫內有哪些 device_id 與最新資料時間，以及已上傳的手冊清單。
    不確定要查哪個 device_id 時，先呼叫這個工具。
    """
    profile = get_profile(device_id)

    conn = await _connect_db()
    try:
        rows = await conn.fetch("""
            SELECT device_id, MIN(time) AS first_seen, MAX(time) AS last_seen
            FROM plc_measurements
            WHERE time > NOW() - INTERVAL '30 days'
            GROUP BY device_id
            ORDER BY device_id
        """)
    finally:
        await conn.close()

    manual_device = base_device_id(device_id)
    docs = [d for d in await _qdrant_call(list_documents()) if d.get("device_id") == manual_device]

    return {
        "device_id": device_id,
        "data_source": "後端內建模擬" if device_id.endswith("-MOCK") else "PLCSIM 模擬" if device_id.endswith("-SIM") else "真實 PLC",
        "profile": profile or f"沒有 {manual_device} 的規格資料，已知設備：{list(DEVICE_PROFILES)}",
        "anomaly_rule": "Isolation Forest 分數連續 5 秒低於模型門檻才判定異常（分數越低越異常）；"
                        f"門檻由訓練資料決定，舊模型為 {ANOMALY_THRESHOLD}",
        "devices_in_db_last_30_days": [
            {"device_id": r["device_id"], "first_seen": _fmt(r["first_seen"]), "last_seen": _fmt(r["last_seen"])}
            for r in rows
        ],
        "manuals": [
            {"filename": d["filename"], "chunks": d["chunk_count"], "enabled": d["enabled"]}
            for d in docs
        ],
    }


if __name__ == "__main__":
    server.run()  # 預設 stdio，由 Claude Desktop 啟動
