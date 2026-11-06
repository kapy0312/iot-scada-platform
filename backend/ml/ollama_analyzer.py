import os
import re
import httpx
import asyncpg

from rag.embedder import embed_text
from rag.qdrant_client import search as qdrant_search
from core.device_profile import LEVEL_TEXT, base_device_id, evaluate, get_profile

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://100.89.23.28:11434")
OLLAMA_MODEL = "qwen3:14b"
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

RAG_TOP_K = 6          # 最後交給 LLM 的段數
RAG_CANDIDATES = 12    # 向量檢索先多撈一些，再用章節條件重新排序

SYSTEM_PROMPT = """你只能輸出剛好兩行繁體中文，格式如下：
原因：（一句話，70字以內，結尾用括號註明依據，例如「（依據：測試版3 §4.2）」；沒有可用章節時寫「（依據：無對應章節）」）
處置：（一句話，60字以內，優先引用所依據章節中的具體檢查項目或術語，不要只給籠統建議；這一行不要寫依據）

選擇手冊章節的規則：
1. 訊號狀態一律以「條件摘要」與「依手冊規格判定」為準，不可用 5 分鐘均值判斷某訊號正常或異常。
2. 「偏高、超過警戒值、超過危險值」都視為「高」；「偏低、低於警戒值、低於危險值」都視為「低」。
3. 章節標題列出的每一個條件都必須與條件摘要一致，才能採用該章節。只要有一項矛盾（例如章節寫「溫度正常」但摘要是溫度高），就不能採用。
4. 章節標題沒有提到的訊號，不影響是否符合。
5. 每段資料標頭的「條件比對」是系統逐條比對的結果，可直接參考：矛盾 0 項者優先，符合項數多者優先。
6. 若沒有完全符合的章節，選矛盾最少的章節，並在依據寫「部分符合」，例如「（依據：測試版3 §4.2，部分符合）」。

輸出完兩行後立即停止，不得有第三行。"""


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
        states[_short_label(spec)] = "高" if lv in (
            "high", "warning", "danger") else "低" if lv == "low" else "正常"
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
    """
    從章節路徑取出條件，例：
    「4. 綜合異常情境（振動與電流） > 4.2 電流高 + 轉速低 + 溫度高 + 壓力低」
    → {"電流": "高", "轉速": "低", "溫度": "高", "壓力": "低"}
    標題不是條件寫法（例如「系統參數規格」）時回傳空 dict
    """
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


async def get_recent_trend(device_id: str) -> dict:
    """從 TimescaleDB 查詢最近 5 分鐘的趨勢統計"""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
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
        await conn.close()
        return {row["tag_name"]: dict(row) for row in rows}
    except Exception:
        return {}


def _payload_meta(payload: dict) -> str:
    """把 payload 中 text 以外的欄位（例如檔名、章節）串成一行，讓 LLM 知道片段出自哪份手冊"""
    parts = []
    for k, v in payload.items():
        if k == "text":
            continue
        s = str(v)
        parts.append(f"{k}={s[:60]}")
    return "｜".join(parts)


def build_query(values: dict, device_id: str) -> str:
    """
    兩個以上訊號異常：用手冊章節標題的寫法查（綜合異常情境：電流高 + 轉速低 + …）。
    單一訊號異常：沿用「馬達電流超過警戒值」這種寫法，對應單一訊號的章節。
    向量檢索只負責撈候選，高／低是否相符由 match_conditions 重新排序決定。
    """
    abnormal, normal = condition_terms(values, device_id)
    if len(abnormal) >= 2:
        return "綜合異常情境：" + " + ".join(abnormal + normal)
    status = describe_status(values, device_id)
    flagged = [s for s in status if not s.endswith("正常")]
    return "、".join(flagged or status) + "，可能原因與處置方式"


async def get_manual_context(values: dict, device_id: str) -> str:
    """查 Qdrant 拿相關手冊片段並依章節條件重新排序；失敗就回空字串，不擋整個診斷流程"""
    query_text = build_query(values, device_id)
    states = condition_states(values, device_id)
    names = list(states)
    try:
        vector = await embed_text(query_text)
        # 手冊依設備型號存放：模擬資料（-MOCK、-SIM）也要查 S7-1511T 的手冊
        results = await qdrant_search(vector, device_id=base_device_id(device_id), top_k=RAG_CANDIDATES)
        print(f"[RAG] 查詢：{query_text}")
        print(f"[RAG] 條件：{condition_summary(values, device_id)}")
        if not results:
            print("[RAG] 查無手冊片段")
            return ""

        ranked = []
        for r in results:
            payload = r.payload or {}
            conds = parse_title_conditions(
                str(payload.get("section", "")), names)
            match, contra = match_conditions(conds, states)
            vec = getattr(r, "score", 0.0) or 0.0
            ranked.append((match - 2 * contra, vec,
                          match, contra, bool(conds), r))
        # 先比條件分數（矛盾扣 2、符合加 1），同分再比向量相似度
        ranked.sort(key=lambda x: (-x[0], -x[1]))

        blocks = []
        for i, (cond_score, vec, match, contra, parsed, r) in enumerate(ranked[:RAG_TOP_K], 1):
            payload = r.payload or {}
            text = payload.get("text", "")
            meta = _payload_meta(payload)
            stripped = text.strip()
            head = stripped.splitlines()[0][:40] if stripped else ""
            check = f"符合 {match} 項、矛盾 {contra} 項" if parsed else "標題非條件寫法"
            print(
                f"[RAG]  #{i} cond={cond_score:+d}（{check}） vec={vec:.3f} ▶ {head}")
            blocks.append(f"[資料{i}｜條件比對：{check}｜{meta}]\n{text}")
        return "\n---\n".join(blocks)
    except Exception as e:
        print(f"[RAG] 手冊檢索失敗：{e}")
        return ""


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
        lines.append(
            f"{spec['label']} {values[tag]} {spec['unit']}（正常{low}~{high}），5分鐘均值 {avg}")
    return lines


async def analyze_anomaly(values: dict, device_id: str = "S7-1511T") -> str:
    # 查詢 5 分鐘趨勢（趨勢要看實際資料來源，所以這裡保留原始 device_id）
    trend = await get_recent_trend(device_id)
    manual_context = await get_manual_context(values, device_id)

    prompt = "設備異常，請依格式輸出診斷：\n" + \
        "\n".join(build_reading_lines(values, device_id, trend))

    status = describe_status(values, device_id)
    if status:
        prompt += (
            f"\n依手冊規格判定：{'、'.join(status)}"
            "（這是目前狀態的唯一依據；5 分鐘均值只代表趨勢，不能拿來判斷狀態）"
        )
    prompt += f"\n條件摘要（用來逐條比對章節標題）：{condition_summary(values, device_id)}"

    if manual_context:
        prompt += f"\n\n設備手冊參考資料：\n{manual_context}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model":   OLLAMA_MODEL,
                    "prompt":  prompt,
                    "system":  SYSTEM_PROMPT,
                    "stream":  False,
                    "think":   False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 200,
                    }
                }
            )
            result = response.json()
            return result.get("response", "無法取得分析結果").strip()

    except httpx.TimeoutException:
        return "AI 分析逾時，請確認桌電 Ollama 服務正常運作"
    except Exception as e:
        return f"AI 分析失敗：{str(e)}"
