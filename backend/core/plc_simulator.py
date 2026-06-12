import asyncio
import time
import pymcprotocol
from core.ws_manager import manager
from db.writer import write_to_db
from ml.inferencer import AnomalyDetector
from ml.ollama_analyzer import analyze_anomaly

detector = AnomalyDetector(model_dir="models")
_last_ollama_call = 0.0
OLLAMA_COOLDOWN   = 60.0

PLC_IP   = "127.0.0.1"
PLC_PORT = 5011

async def poll_plc_forever():
    print("[PLC] 啟動 FX5U SLMP 連線...")

    while True:
        try:
            plc = pymcprotocol.Type3E(plctype="iQ-L")
            plc.connect(PLC_IP, PLC_PORT)
            print("[PLC] ✅ 連線成功，開始輪詢")

            while True:
                # 用 asyncio 的執行緒池跑同步的 pymcprotocol
                values = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: plc.batchread_wordunits(headdevice="D100", readsize=3)
                )

                data = {
                    "timestamp":   time.time(),
                    "motor_speed": float(values[0]),
                    "temperature": round(values[1] / 10.0, 2),
                    "pressure":    round(values[2] / 10.0, 2),
                    "motor_enable": 1,
                }

                anomaly = detector.update(data)

                # 偵測到異常時，非同步呼叫 Ollama 分析（冷卻 60 秒）
                global _last_ollama_call
                now = time.time()
                if (anomaly.get("is_anomaly") and
                    anomaly.get("status") == "anomaly" and
                    now - _last_ollama_call > OLLAMA_COOLDOWN):
                    _last_ollama_call = now
                    asyncio.create_task(_fetch_ollama_analysis(data, anomaly))

                data["anomaly"] = anomaly
                # 如果目前是異常狀態，把最新的 AI 分析結果帶進每次廣播
                if anomaly.get("is_anomaly") and _latest_ai_analysis:
                    data["anomaly"]["ai_analysis"] = _latest_ai_analysis
                await manager.broadcast(data)
                asyncio.create_task(write_to_db("FX5U-MOCK", data))
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[PLC] ❌ 連線失敗：{e}")
            print("[PLC] 5 秒後重試...")
            try:
                plc.close()
            except Exception:
                pass
            await asyncio.sleep(5)
            
# 在檔案頂層加這個變數，記住最新的 AI 分析結果
_latest_ai_analysis: str = ""

async def _fetch_ollama_analysis(data: dict, anomaly: dict):
    global _latest_ai_analysis
    analysis = await analyze_anomaly({
        "motor_speed":   data.get("motor_speed"),
        "temperature":   data.get("temperature"),
        "pressure":      data.get("pressure"),
        "anomaly_score": anomaly.get("score", 0),
    }, device_id="FX5U-MOCK")
    _latest_ai_analysis = analysis
    data["anomaly"]["ai_analysis"] = analysis
    await manager.broadcast(data)
    print(f"[Ollama] 分析完成：{analysis[:100]}...")