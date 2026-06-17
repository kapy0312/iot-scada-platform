import asyncio
import time
from asyncua import Client
from core.ws_manager import manager
from db.writer import write_to_db
from db.anomaly_writer import write_anomaly_event
from ml.inferencer import AnomalyDetector
from ml.ollama_analyzer import analyze_anomaly

detector = AnomalyDetector(model_dir="models")
_last_ollama_call = 0.0
OLLAMA_COOLDOWN   = 60.0
_latest_ai_analysis: str = ""

PLC_URL = "opc.tcp://192.168.0.10:4840"
NS = 3  # PLC_1 的 Namespace Index

async def poll_plc_forever():
    print("[PLC] 啟動 OPC UA 連線...")

    while True:
        try:
            async with Client(url=PLC_URL) as client:
                print("[PLC] ✅ 連線成功，開始輪詢")

                # 取得節點（連線後只需取一次）
                motor_speed_node = await client.nodes.root.get_child(
                    [f"0:Objects", f"{NS}:PLC_1", f"{NS}:DataBlocksGlobal",
                     f"{NS}:DB1", f"{NS}:motor_speed"]
                )
                temperature_node = await client.nodes.root.get_child(
                    [f"0:Objects", f"{NS}:PLC_1", f"{NS}:DataBlocksGlobal",
                     f"{NS}:DB1", f"{NS}:temperature"]
                )
                pressure_node = await client.nodes.root.get_child(
                    [f"0:Objects", f"{NS}:PLC_1", f"{NS}:DataBlocksGlobal",
                     f"{NS}:DB1", f"{NS}:pressure"]
                )

                # 持續輪詢
                while True:
                    spd = await motor_speed_node.read_value()
                    tmp = await temperature_node.read_value()
                    prs = await pressure_node.read_value()

                    data = {
                        "timestamp": time.time(),
                        "motor_speed": round(float(spd), 2),
                        "temperature": round(float(tmp), 3),
                        "pressure":    round(float(prs), 3),
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
                    asyncio.create_task(write_to_db("S7-1511T", data))

                    # 異常時寫入 anomaly_events
                    if anomaly.get("is_anomaly"):
                        asyncio.create_task(
                            write_anomaly_event("S7-1511T", data, anomaly)
                        )

                    await asyncio.sleep(1)
        except Exception as e:
            print(f"[PLC] ❌ 連線失敗：{e}")
            print("[PLC] 5 秒後重試...")
            await asyncio.sleep(5)
            
async def _fetch_ollama_analysis(data: dict, anomaly: dict):
    global _latest_ai_analysis
    analysis = await analyze_anomaly({
        "motor_speed":   data.get("motor_speed"),
        "temperature":   data.get("temperature"),
        "pressure":      data.get("pressure"),
        "anomaly_score": anomaly.get("score", 0),
    }, device_id="S7-1511T")
    _latest_ai_analysis = analysis
    anomaly["ai_analysis"] = analysis
    await manager.broadcast(data)
    print(f"[Ollama] 分析完成：\n{analysis}")