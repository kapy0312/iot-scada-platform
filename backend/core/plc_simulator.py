import asyncio
import time
from asyncua import Client
from core.ws_manager import manager
from db.writer import write_to_db
from ml.inferencer import AnomalyDetector

detector = AnomalyDetector("models/S7-1511T_anomaly_v1.pkl")

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
                    data["anomaly"] = anomaly

                    await manager.broadcast(data)
                    asyncio.create_task(
                        write_to_db("S7-1511T", data)
                    )
                    await asyncio.sleep(1)
        except Exception as e:
            print(f"[PLC] ❌ 連線失敗：{e}")
            print("[PLC] 5 秒後重試...")
            await asyncio.sleep(5)