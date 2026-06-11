import asyncio
import time
import pymcprotocol
from core.ws_manager import manager
from db.writer import write_to_db
from ml.inferencer import AnomalyDetector

detector = AnomalyDetector("models/S7-1511T_anomaly_v1.pkl")

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
                data["anomaly"] = anomaly

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