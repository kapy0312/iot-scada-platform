import json
import os
import re

import asyncpg
import httpx

from rag.embedder import embed_text
from rag.qdrant_client import search as qdrant_search
from core.device_profile import LEVEL_TEXT, base_device_id, evaluate, get_profile

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://100.89.23.28:11434")
OLLAMA_MODEL = "qwen3:14b"
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

RAG_CANDIDATES = 12    # 向量檢索先多撈一些，再用章節條件重新排序

# 章節由程式選定，模型只負責把章節內容寫成兩句話
SYSTEM_PROMPT = """你是設備維護助理。系統已依訊號條件選好一段手冊章節，你只要根據這段章節和目前讀值，輸出 JSON：
{"cause": "…", "action": "…"}

- cause：一句繁體中文，60 字以內，寫出章節內文所說的典型情境或故障原因，不要照抄章節標題，也不要只重複訊號狀態。
  例如章節內文寫「典型情境為皮帶打滑」時，cause 寫「皮帶打滑，傳動效率下降使轉速不穩」。
- action：一句繁體中文，60 字以內，優先引用章節中的具體檢查項目或術語，不要給籠統建議。
- 不要寫「依據」、章節編號或手冊名稱，系統會自己附上。
- 系統註明「章節只部分符合」、「參考章節」或沒有提供章節時，cause 以「推論：」開頭，依讀值推論。
- 訊號狀態一律以「依手冊規格判定」為準，5 分鐘均值只代表趨勢。

只輸出 JSON，不要任何其他文字。"""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"cause": {"type": "string"}, "action": {"type": "string"}},
    "required": ["cause", "action"],
}

MATCH_NOTE = {
    "full": "",
    "partial": "（章節只部分符合，請依讀值推論）",
    "reference": "（參考章節，不是條件完全對應的診斷章節，請依讀值推論）",
}
SOURCE_SUFFIX = {"full": "", "partial": "，部分符合", "reference": "，參考"}


# ---------- 訊號狀態 ----------

def describe_status(values: dict, device_id: str) -> list[str]:
    """把數值對照設備規格，翻成「溫度超過危險值」這類文字；向量模型看不懂數字大小，看得懂文字"""
    profile = get_profile(device_id)
    if not profile:
        return []
    phrases = []
    for tag, spec in profile["tags"].items():
        v = values.get(tag)
        if v is None:
            continue
        phrases.append(f"{spec['label']}{LEVEL_TEXT[evaluate(spec, v)]}")
    return phrases


def _short_label(spec: dict) -> str:
    """手冊章節標題用短名稱（轉速、電流），規格裡可用 short 欄位覆寫"""
    return spec.get("short") or spec["label"].removeprefix("馬達")


def condition_states(values: dict, device_id: str) -> dict[str, str]:
    """每個訊號的狀態，用手冊標題的說法：{"電流": "高", "轉速": "低", "振動": "正常", ...}"""
    profile = get_profile(device_id)
    if not profile:
        return {}
    states: dict[str, str] = {}
    for tag, spec in profile["tags"].items():
        v = values.get(tag)
        if v is None:
            continue
        lv = evaluate(spec, v)
        states[_short_label(spec)] = "高" if lv in ("high", "warning", "danger") else "低" if lv == "low" else "正常"
    return states


def condition_terms(values: dict, device_id: str) -> tuple[list[str], list[str]]:
    """異常的（電流高、轉速低）與正常的（振動正常）分開回傳"""
    states = condition_states(values, device_id)
    abnormal = [f"{n}{s}" for n, s in states.items() if s != "正常"]
    normal = [f"{n}{s}" for n, s in states.items() if s == "正常"]
    return abnormal, normal


def condition_summary(values: dict, device_id: str) -> str:
    """例：轉速低 + 溫度高 + 壓力低 + 電流高（振動正常）"""
    abnormal, normal = condition_terms(values, device_id)
    text = " + ".join(abnormal) if abnormal else "全部正常"
    if normal:
        text += "（" + "、".join(normal) + "）"
    return text


# ---------- 章節條件比對 ----------

def _state_word(term: str) -> str | None:
    """章節標題裡一個條件的狀態；順序重要：「過低」要先判成低，「超過 28 A」判成高"""
    if "正常" in term:
        return "正常"
    if "低" in term:
        return "低"
    if "高" in term or "超過" in term:
        return "高"
    return None


def parse_title_conditions(section: str, names: list[str]) -> dict[str, str]:
    """「4. 綜合異常情境 > 4.2 電流高 + 轉速低 + …」→ {"電流": "高", "轉速": "低", …}；非條件寫法回傳空 dict"""
    title = section.split(">")[-1].strip()
    title = re.sub(r"^\d+(\.\d+)*\s*", "", title)
    conds: dict[str, str] = {}
    for term in re.split(r"[+＋]", title):
        name = next((n for n in names if n in term), None)
        if not name or name in conds:
            continue
        word = _state_word(term)
        if word:
            conds[name] = word
    return conds


def match_conditions(conds: dict[str, str], states: dict[str, str]) -> tuple[int, int]:
    """回傳（符合項數, 矛盾項數）；標題沒提到的訊號不算"""
    match = sum(1 for n, w in conds.items() if states.get(n) == w)
    contra = sum(1 for n, w in conds.items() if n in states and states[n] != w)
    return match, contra


# ---------- 手冊來源 ----------

def _doc_short(payload: dict) -> str:
    """S71511T設備維護手冊測試版3.pdf → 測試版3；認不出版本就用檔名"""
    name = str(payload.get("source_filename") or payload.get("doc_title") or "手冊")
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", name)
    m = re.search(r"(測試版\s*\d+|第\s*\d+\s*版|[Vv]\d+(?:\.\d+)*)", stem)
    return re.sub(r"\s+", "", m.group(1)) if m else stem


def _section_no(section: str) -> tuple[str, bool]:
    """「4. 綜合異常 > 4.2 電流高 + …」→ ("4.2", True)；沒有編號就回傳標題前 30 字"""
    last = section.split(">")[-1].strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", last)
    return (m.group(1), True) if m else (last[:30], False)


def make_source(cand: dict, match_type: str) -> dict:
    payload = cand["payload"]
    section = str(payload.get("section") or "")
    no, numbered = _section_no(section)
    doc = _doc_short(payload)
    return {
        "label": f"{doc} §{no}" if numbered else f"{doc}「{no}」",
        "file": payload.get("source_filename"),
        "doc_title": payload.get("doc_title"),
        "section": section,
        "section_no": no,
        "page": payload.get("page"),
        "chunk_index": payload.get("chunk_index"),
        "match_type": match_type,          # full / partial / reference
        "match": cand["match"],
        "contra": cand["contra"],
        "vector_score": round(cand["vec"], 4),
    }


# ---------- 檢索與選章節 ----------

def build_query(values: dict, device_id: str) -> str:
    """兩個以上訊號異常用章節標題寫法查；單一訊號異常沿用「馬達電流超過警戒值」寫法"""
    abnormal, normal = condition_terms(values, device_id)
    if len(abnormal) >= 2:
        return "綜合異常情境：" + " + ".join(abnormal + normal)
    status = describe_status(values, device_id)
    flagged = [s for s in status if not s.endswith("正常")]
    return "、".join(flagged or status) + "，可能原因與處置方式"


def rank_candidates(results, states: dict[str, str]) -> list[dict]:
    """依章節條件重新排序：矛盾扣 2、符合加 1，同分比向量相似度"""
    names = list(states)
    ranked = []
    for r in results or []:
        payload = r.payload or {}
        conds = parse_title_conditions(str(payload.get("section", "")), names)
        match, contra = match_conditions(conds, states)
        ranked.append({
            "payload": payload,
            "text": payload.get("text", ""),
            "match": match,
            "contra": contra,
            "parsed": bool(conds),
            "cond_score": match - 2 * contra,
            "vec": getattr(r, "score", 0.0) or 0.0,
        })
    ranked.sort(key=lambda c: (-c["cond_score"], -c["vec"]))
    return ranked


async def retrieve_manual(values: dict, device_id: str) -> list[dict]:
    """向量檢索候選段落，再依章節條件重新排序；失敗回傳空清單，不擋整個診斷流程"""
    query_text = build_query(values, device_id)
    states = condition_states(values, device_id)
    try:
        vector = await embed_text(query_text)
        # 手冊依設備型號存放：模擬資料（-MOCK、-SIM）也要查 S7-1511T 的手冊
        results = await qdrant_search(vector, device_id=base_device_id(device_id), top_k=RAG_CANDIDATES)
    except Exception as e:
        print(f"[RAG] 手冊檢索失敗：{e}")
        return []

    print(f"[RAG] 查詢：{query_text}")
    print(f"[RAG] 條件：{condition_summary(values, device_id)}")
    ranked = rank_candidates(results, states)
    for i, c in enumerate(ranked[:6], 1):
        head = c["text"].strip().splitlines()[0][:40] if c["text"].strip() else ""
        check = f"符合 {c['match']} 項、矛盾 {c['contra']} 項" if c["parsed"] else "標題非條件寫法"
        print(f"[RAG]  #{i} cond={c['cond_score']:+d}（{check}） vec={c['vec']:.3f} ▶ {head}")
    return ranked


def select_primary(ranked: list[dict]) -> tuple[dict | None, str | None]:
    """
    由程式決定依據章節，不交給模型挑：
    1. 條件完全符合（矛盾 0 項、符合至少 1 項）→ full
    2. 有符合也有矛盾 → partial（取條件分數最高的）
    3. 標題不是條件寫法的段落 → reference
    """
    for c in ranked:
        if c["parsed"] and c["contra"] == 0 and c["match"] >= 1:
            return c, "full"
    for c in ranked:
        if c["parsed"] and c["match"] >= 1:
            return c, "partial"
    for c in ranked:
        if not c["parsed"]:
            return c, "reference"
    return None, None


# ---------- 讀值與趨勢 ----------

async def get_recent_trend(device_id: str) -> dict:
    """從 TimescaleDB 查詢最近 5 分鐘的趨勢統計"""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            rows = await conn.fetch("""
                SELECT
                    tag_name,
                    ROUND(MIN(value)::numeric, 2)  AS min_val,
                    ROUND(MAX(value)::numeric, 2)  AS max_val,
                    ROUND(AVG(value)::numeric, 2)  AS avg_val,
                    ROUND((MAX(value) - MIN(value))::numeric, 2) AS range_val
                FROM plc_measurements
                WHERE device_id = $1
                  AND time > NOW() - INTERVAL '5 minutes'
                GROUP BY tag_name
            """, device_id)
        finally:
            await conn.close()
        return {row["tag_name"]: dict(row) for row in rows}
    except Exception:
        return {}


def build_reading_lines(values: dict, device_id: str, trend: dict) -> list[str]:
    """每個訊號一行：現值、正常範圍、5 分鐘均值；訊號清單來自設備規格"""
    profile = get_profile(device_id)
    if not profile:
        return [f"{k} {v}" for k, v in values.items()]
    lines = []
    for tag, spec in profile["tags"].items():
        if tag not in values:
            continue
        low, high = spec["normal"]
        avg = trend.get(tag, {}).get("avg_val", "N/A")
        lines.append(f"{spec['label']} {values[tag]} {spec['unit']}（正常{low}~{high}），5分鐘均值 {avg}")
    return lines


def _clean(text) -> str:
    """去掉模型自己加的依據括號與多餘空白"""
    text = re.sub(r"[（(]\s*依據[:：].*?[)）]", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def compose_text(cause: str, action: str, source: dict | None) -> str:
    """依據由後端組出，模型不碰"""
    if source:
        basis = f"{source['label']}{SOURCE_SUFFIX.get(source.get('match_type'), '')}"
    else:
        basis = "無對應章節"
    return f"原因：{cause}（依據：{basis}）\n處置：{action or '請依手冊章節逐項檢查'}"


def parse_answer(raw: str) -> tuple[str, str]:
    """解析模型的 JSON；結構化輸出失敗時保底：原文當原因、處置留空"""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return _clean(data.get("cause")), _clean(data.get("action"))
    except (json.JSONDecodeError, TypeError):
        pass
    return _clean(raw), ""


# ---------- 主流程 ----------

async def analyze_anomaly(values: dict, device_id: str = "S7-1511T") -> dict:
    """
    回傳 {"ok": bool, "text": 兩行診斷, "source": 手冊來源 dict 或 None}
    章節由 select_primary 決定，依據文字由後端組出，模型只填 cause / action
    """
    trend = await get_recent_trend(device_id)   # 趨勢看實際資料來源，保留原始 device_id
    ranked = await retrieve_manual(values, device_id)
    primary, match_type = select_primary(ranked)
    source = make_source(primary, match_type) if primary else None

    prompt = "設備異常，目前讀值：\n" + "\n".join(build_reading_lines(values, device_id, trend))
    status = describe_status(values, device_id)
    if status:
        prompt += f"\n依手冊規格判定：{'、'.join(status)}"
    prompt += f"\n條件摘要：{condition_summary(values, device_id)}"
    if primary:
        prompt += f"\n\n手冊章節（系統已選定）{MATCH_NOTE[match_type]}：\n{primary['text']}"
    else:
        prompt += "\n\n（沒有找到可用的手冊章節）"

    if source:
        print(f"[RAG] 選定依據：{source['label']}（{match_type}）")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "think": False,
                    "format": ANSWER_SCHEMA,
                    "options": {"temperature": 0.3, "num_predict": 200},
                },
            )
            raw = response.json().get("response", "")
    except httpx.TimeoutException:
        return {"ok": False, "text": "AI 分析逾時，請確認桌電 Ollama 服務正常運作", "source": source}
    except Exception as e:
        return {"ok": False, "text": f"AI 分析失敗：{e}", "source": source}

    cause, action = parse_answer(raw)
    if not cause:
        return {"ok": False, "text": "AI 分析失敗：模型沒有回傳內容", "source": source}
    return {"ok": True, "text": compose_text(cause, action, source), "source": source}
