import os
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

SYSTEM_PROMPT = """你只能輸出剛好兩行繁體中文，格式如下：
原因：（一句話，60字以內）
處置：（一句話，60字以內，若有提供設備手冊參考資料，優先引用資料中的具體檢查項目或術語，不要只給籠統建議）
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


async def get_manual_context(values: dict, device_id: str) -> str:
    """查 Qdrant 拿相關手冊片段，失敗就回空字串，不擋整個診斷流程"""
    status = describe_status(values, device_id)
    abnormal = [s for s in status if not s.endswith("正常")]
    if len(abnormal) >= 2:
        abnormal.append("多參數同時異常")
    query_text = "、".join(abnormal or status) + "，可能原因與處置方式"
    try:
        vector = await embed_text(query_text)
        # 手冊依設備型號存放：模擬資料（-MOCK、-SIM）也要查 S7-1511T 的手冊
        results = await qdrant_search(vector, device_id=base_device_id(device_id), top_k=4)
        if not results:
            return ""
        return "\n---\n".join(r.payload.get("text", "") for r in results)
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
        lines.append(f"{spec['label']} {values[tag]} {spec['unit']}（正常{low}~{high}），5分鐘均值 {avg}")
    return lines


async def analyze_anomaly(values: dict, device_id: str = "S7-1511T") -> str:
    # 查詢 5 分鐘趨勢（趨勢要看實際資料來源，所以這裡保留原始 device_id）
    trend = await get_recent_trend(device_id)
    manual_context = await get_manual_context(values, device_id)

    prompt = "設備異常，請依格式輸出診斷：\n" + "\n".join(build_reading_lines(values, device_id, trend))

    status = describe_status(values, device_id)
    if status:
        prompt += f"\n依手冊規格判定：{'、'.join(status)}（請以此判定為準，不要自行推論某訊號正常）"

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
                        "num_predict": 150,
                    }
                }
            )
            result = response.json()
            return result.get("response", "無法取得分析結果").strip()

    except httpx.TimeoutException:
        return "AI 分析逾時，請確認桌電 Ollama 服務正常運作"
    except Exception as e:
        return f"AI 分析失敗：{str(e)}"
