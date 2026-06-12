import httpx
import asyncpg

OLLAMA_URL   = "http://100.89.23.28:11434"
OLLAMA_MODEL = "qwen3:14b"
DATABASE_URL = "postgresql://postgres:iotscada123@localhost:5435/iotscada"

SYSTEM_PROMPT = """你只能輸出剛好兩行繁體中文，格式如下：
原因：（一句話，40字以內）
處置：（一句話，40字以內）
輸出完兩行後立即停止，不得有第三行。"""

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

async def analyze_anomaly(anomaly_data: dict, device_id: str = "FX5U-MOCK") -> str:
    # 查詢 5 分鐘趨勢
    trend = await get_recent_trend(device_id)

    def fmt(tag: str, unit: str) -> str:
        if tag not in trend:
            return f"{anomaly_data.get(tag, 'N/A')} {unit}（無趨勢資料）"
        t = trend[tag]
        return (f"現值 {anomaly_data.get(tag, 'N/A')} {unit}，"
                f"5分鐘內均值 {t['avg_val']}／最高 {t['max_val']}／"
                f"變化幅度 {t['range_val']}")

    prompt = f"""設備異常，請依格式輸出診斷：
馬達轉速 {anomaly_data.get('motor_speed','N/A')} RPM（正常1400~1600），5分鐘均值 {trend.get('motor_speed', {}).get('avg_val', 'N/A')}
溫度 {anomaly_data.get('temperature','N/A')} °C（正常65~75），5分鐘均值 {trend.get('temperature', {}).get('avg_val', 'N/A')}
壓力 {anomaly_data.get('pressure','N/A')} bar（正常4.5~5.5），5分鐘均值 {trend.get('pressure', {}).get('avg_val', 'N/A')}"""

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