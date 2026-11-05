import asyncio
import os
import random
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

PLC_URL = os.getenv("PLC_URL", "opc.tcp://192.168.0.10:4840")
NS      = 3

VALID_SOURCES = ("opcua", "mock")
# OPC UA 來源寫入用的 device_id：接實機用 S7-1511T（預設）；接 PLCSIM 時設成 S7-1511T-SIM，避免模擬資料進入訓練集
OPCUA_DEVICE_ID: str = os.getenv("OPCUA_DEVICE_ID", "S7-1511T")
current_source: str = os.getenv("PLC_SOURCE", "opcua")
if current_source not in VALID_SOURCES:
    current_source = "opcua"

_polling_task: asyncio.Task | None = None

MOCK_BASELINE = {"motor_speed": 1480.0, "temperature": 70.0, "pressure": 5.0}


_anomaly_override: dict | None = None
_anomaly_deadline: float = 0.0

def get_active_device_id() -> str:
    """依目前資料源回傳寫入用的 device_id；mock 與 PLCSIM 的資料獨立標記，避免污染訓練集"""
    return "S7-1511T-MOCK" if current_source == "mock" else OPCUA_DEVICE_ID

def trigger_mock_anomaly(duration_seconds: float = 20.0):
    """讓 mock 資料源在指定秒數內朝異常值靠攏，僅限 mock 來源可用"""
    global _anomaly_override, _anomaly_deadline
    if current_source != "mock":
        raise ValueError("僅 mock 資料源可觸發模擬異常")
    _anomaly_override = {
        "motor_speed": 1780.0,
        "temperature": 105.0,
        "pressure": 9.5,
    }
    _anomaly_deadline = time.time() + duration_seconds
    print(f"[PLC] 模擬異常已觸發，持續 {duration_seconds} 秒")

def start_polling():
    """依 current_source 建立輪詢任務，main.py lifespan 啟動時呼叫一次"""
    global _polling_task
    _polling_task = asyncio.create_task(_run_source_loop())


def stop_polling():
    """main.py lifespan 關閉時呼叫"""
    if _polling_task:
        _polling_task.cancel()


async def switch_source(new_source: str):
    """切換資料源：取消目前輪詢任務、清空 ML 滑動視窗、重建任務"""
    global current_source, _polling_task
    if new_source not in VALID_SOURCES:
        raise ValueError(f"不支援的資料源：{new_source}，僅支援 {VALID_SOURCES}")

    if new_source == current_source and _polling_task and not _polling_task.done():
        return  # 已經是目前來源，不需重建

    if _polling_task:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass

    current_source = new_source
    detector.buffer.clear()  # 避免視窗混雜不同來源的殘留數值
    global _anomaly_override
    _anomaly_override = None
    print(f"[PLC] 資料源切換為：{current_source}，ML 滑動視窗已清空")

    _polling_task = asyncio.create_task(_run_source_loop())


async def _run_source_loop():
    if current_source == "mock":
        print("[PLC] 使用模擬資料源")
        await _mock_poll_loop()
    else:
        await _opcua_poll_loop()


async def _opcua_poll_loop():
    print("[PLC] 啟動 OPC UA 連線...")
    while True:
        try:
            async with Client(url=PLC_URL) as client:
                print("[PLC] ✅ 連線成功，開始輪詢")

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

                while True:
                    spd = await motor_speed_node.read_value()
                    tmp = await temperature_node.read_value()
                    prs = await pressure_node.read_value()
                    await _process_reading(
                        round(float(spd), 2), round(float(tmp), 3), round(float(prs), 3)
                    )
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[PLC] ❌ 連線失敗：{e}")
            print("[PLC] 5 秒後重試...")
            await asyncio.sleep(5)


async def _mock_poll_loop():
    global _anomaly_override, _anomaly_deadline
    spd = MOCK_BASELINE["motor_speed"]
    tmp = MOCK_BASELINE["temperature"]
    prs = MOCK_BASELINE["pressure"]
    while True:
        if _anomaly_override and time.time() < _anomaly_deadline:
            # 朝異常目標值靠攏（每秒推進 40%），而非瞬間跳變，較貼近真實故障發展過程
            target = _anomaly_override
            spd = spd + (target["motor_speed"] - spd) * 0.4 + random.gauss(0, 5)
            tmp = tmp + (target["temperature"] - tmp) * 0.4 + random.gauss(0, 0.5)
            prs = prs + (target["pressure"] - prs) * 0.4 + random.gauss(0, 0.2)
        else:
            if _anomaly_override and time.time() >= _anomaly_deadline:
                _anomaly_override = None
                print("[PLC] 模擬異常結束，回復正常波動")
            spd = max(0.0, spd + random.gauss(0, 8) + 0.02 * (MOCK_BASELINE["motor_speed"] - spd))
            tmp = max(0.0, tmp + random.gauss(0, 0.4) + 0.02 * (MOCK_BASELINE["temperature"] - tmp))
            prs = max(0.0, prs + random.gauss(0, 0.1) + 0.02 * (MOCK_BASELINE["pressure"] - prs))

        await _process_reading(round(spd, 2), round(tmp, 3), round(prs, 3))
        await asyncio.sleep(1)


async def _process_reading(spd: float, tmp: float, prs: float):
    device_id = get_active_device_id()

    data = {
        "timestamp": time.time(),
        "motor_speed": spd,
        "temperature": tmp,
        "pressure": prs,
        "motor_enable": 1,
    }
    anomaly = detector.update(data)

    global _last_ollama_call, _latest_ai_analysis
    now = time.time()
    if (anomaly.get("is_anomaly") and
        anomaly.get("status") == "anomaly" and
        now - _last_ollama_call > OLLAMA_COOLDOWN):
        _last_ollama_call = now
        asyncio.create_task(_fetch_ollama_analysis(data, anomaly, device_id))

    if anomaly.get("is_anomaly") and _latest_ai_analysis:
        anomaly["ai_analysis"] = _latest_ai_analysis

    data["anomaly"] = anomaly
    data["source"] = current_source
    data["device_id"] = device_id

    await manager.broadcast(data)
    asyncio.create_task(write_to_db(device_id, data))

    if anomaly.get("is_anomaly"):
        asyncio.create_task(write_anomaly_event(device_id, data, anomaly))


async def _fetch_ollama_analysis(data: dict, anomaly: dict, device_id: str):
    global _latest_ai_analysis
    analysis = await analyze_anomaly({
        "motor_speed":   data.get("motor_speed"),
        "temperature":   data.get("temperature"),
        "pressure":      data.get("pressure"),
        "anomaly_score": anomaly.get("score", 0),
    }, device_id=device_id)
    _latest_ai_analysis = analysis
    anomaly["ai_analysis"] = analysis
    await manager.broadcast(data)
    print(f"[Ollama] 分析完成：\n{analysis}")
