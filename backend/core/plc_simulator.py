import asyncio
import time
import pymcprotocol
from core.ws_manager import manager
from db.writer import write_to_db
from db.anomaly_writer import write_anomaly_event
from ml.inferencer import AnomalyDetector
from ml.ollama_analyzer import analyze_anomaly

detector = AnomalyDetector(model_dir="models")
_last_ollama_call = 0.0
OLLAMA_COOLDOWN   = 60.0
_latest_ai_analysis: str = ""

PLC_IP   = "192.168.0.20"
PLC_PORT = 5011
plc_instance = None  # 全域共用連線

async def poll_plc_forever():
    print("[PLC] 啟動 FX5U SLMP 連線...")

    while True:
        try:
            global plc_instance
            plc_instance = pymcprotocol.Type3E(plctype="iQ-L")
            plc_instance.connect(PLC_IP, PLC_PORT)
            print("[PLC] ✅ 連線成功，開始輪詢")

            while True:
                values = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: plc_instance.batchread_wordunits(headdevice="D100", readsize=3)
                )

                data = {
                    "timestamp":    time.time(),
                    "motor_speed":  float(values[0]),
                    "temperature":  round(values[1] / 10.0, 2),
                    "pressure":     round(values[2] / 10.0, 2),
                    "motor_enable": 1,
                }

                # ML 推論
                anomaly = detector.update(data)

                # 異常時觸發 Ollama（60秒冷卻）
                global _last_ollama_call, _latest_ai_analysis
                now = time.time()
                if (anomaly.get("is_anomaly") and
                    anomaly.get("status") == "anomaly" and
                    now - _last_ollama_call > OLLAMA_COOLDOWN):
                    _last_ollama_call = now
                    asyncio.create_task(_fetch_ollama_analysis(data, anomaly))

                # 持續帶入最新 AI 分析
                if anomaly.get("is_anomaly") and _latest_ai_analysis:
                    anomaly["ai_analysis"] = _latest_ai_analysis

                data["anomaly"] = anomaly

                await manager.broadcast(data)
                asyncio.create_task(write_to_db("FX5U-MOCK", data))

                # 異常時寫入 anomaly_events
                if anomaly.get("is_anomaly"):
                    asyncio.create_task(
                        write_anomaly_event("FX5U-MOCK", data, anomaly)
                    )

                await asyncio.sleep(1)

        except Exception as e:
            print(f"[PLC] ❌ 連線失敗：{e}")
            print("[PLC] 5 秒後重試...")
            try:
                plc_instance.close()
            except Exception:
                pass
            plc_instance = None
            await asyncio.sleep(5)

async def _fetch_ollama_analysis(data: dict, anomaly: dict):
    global _latest_ai_analysis
    analysis = await analyze_anomaly({
        "motor_speed":   data.get("motor_speed"),
        "temperature":   data.get("temperature"),
        "pressure":      data.get("pressure"),
        "anomaly_score": anomaly.get("score", 0),
    }, device_id="FX5U-MOCK")
    _latest_ai_analysis = analysis
    anomaly["ai_analysis"] = analysis
    await manager.broadcast(data)
    print(f"[Ollama] 分析完成：\n{analysis}")