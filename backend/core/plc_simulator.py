import asyncio
import os
import random
import time
from datetime import datetime, timezone

from asyncua import Client
from core.device_profile import evaluate, get_production, get_profile, get_tags
from core.incidents import INCIDENT_GAP_SECONDS, absorb, new_incident, severity_and_level
from core.production import RUN, STATE_TEXT, CounterTracker, RunGate, is_running
from core.trend import TrendTracker
from core.ws_manager import manager
from db.writer import write_to_db
from db.anomaly_writer import record_anomaly, save_incident
from db.status_writer import write_status
from ml.inferencer import AnomalyDetector
from ml.ollama_analyzer import analyze_anomaly

detector = AnomalyDetector()
counters = CounterTracker()   # PLC 累計計數 → 每秒增量
run_gate = RunGate()          # 停機時暫停異常偵測
trend = TrendTracker()        # 趨勢預警：推估多久後達警戒值（停機、恢復期跟著 RunGate 暫停）
OLLAMA_COOLDOWN = 60.0      # 同一次事件內，兩次 AI 診斷至少間隔 60 秒
_last_ollama_call = 0.0
_last_anomaly_at = 0.0
_latest_ai_analysis: str = ""
_latest_ai_source: dict | None = None
_incident: dict | None = None   # 目前進行中的事件（記憶體版本，每個異常秒同步寫入資料庫）

PLC_URL = os.getenv("PLC_URL", "opc.tcp://192.168.0.10:4840")
NS = 3
DB1_PATH = ["0:Objects", f"{NS}:PLC_1", f"{NS}:DataBlocksGlobal", f"{NS}:DB1"]

VALID_SOURCES = ("opcua", "mock")
# OPC UA 來源寫入用的 device_id：接實機用 S7-1511T（預設）；接 PLCSIM 時設成 S7-1511T-SIM，避免模擬資料進入訓練集
OPCUA_DEVICE_ID: str = os.getenv("OPCUA_DEVICE_ID", "S7-1511T")
current_source: str = os.getenv("PLC_SOURCE", "opcua")
if current_source not in VALID_SOURCES:
    current_source = "opcua"

_polling_task: asyncio.Task | None = None

# 內建模擬（mock）：每個訊號的基準值、雜訊、模擬異常時的目標值
# 沒列在這裡的訊號，mock 模式不會產生
MOCK_SIGNALS = {
    "motor_speed": {"base": 1480.0, "noise": 8.0,  "anomaly": 1780.0},
    "temperature": {"base": 70.0,   "noise": 0.4,  "anomaly": 105.0},
    "pressure":    {"base": 5.0,    "noise": 0.1,  "anomaly": 9.5},
    "vibration":   {"base": 1.2,    "noise": 0.05, "anomaly": 5.0},
    "current":     {"base": 24.0,   "noise": 0.3,  "anomaly": 32.0},
}

_anomaly_override: bool = False
_anomaly_deadline: float = 0.0

# 內建模擬的生產計數：一直運轉，額定轉速 6 秒一件；模擬異常期間 NG 率提高
MOCK_CYCLE_SECONDS = 6.0
MOCK_NG_RATE = {False: 0.01, True: 0.10}
_mock_prod = {"acc": 0.0, "good": 0, "ng": 0}


def get_active_device_id() -> str:
    """依目前資料源回傳寫入用的 device_id；mock 與 PLCSIM 的資料獨立標記，避免污染訓練集"""
    return "S7-1511T-MOCK" if current_source == "mock" else OPCUA_DEVICE_ID


def trigger_mock_anomaly(duration_seconds: float = 20.0):
    """讓 mock 資料源在指定秒數內朝異常值靠攏，僅限 mock 來源可用"""
    global _anomaly_override, _anomaly_deadline
    if current_source != "mock":
        raise ValueError("僅 mock 資料源可觸發模擬異常；PLCSIM 請用 tools/plcsim_ctl.py fault")
    _anomaly_override = True
    _anomaly_deadline = time.time() + duration_seconds
    print(f"[PLC] 模擬異常已觸發，持續 {duration_seconds} 秒")


def start_polling():
    """依 current_source 建立輪詢任務，main.py lifespan 啟動時呼叫一次"""
    global _polling_task
    _polling_task = asyncio.create_task(_run_source_loop())


def stop_polling():
    if _polling_task:
        _polling_task.cancel()


async def switch_source(new_source: str):
    """切換資料源：取消目前輪詢任務、清空 ML 滑動視窗、重建任務"""
    global current_source, _polling_task, _anomaly_override
    if new_source not in VALID_SOURCES:
        raise ValueError(f"不支援的資料源：{new_source}，僅支援 {VALID_SOURCES}")

    if new_source == current_source and _polling_task and not _polling_task.done():
        return

    if _polling_task:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass

    current_source = new_source
    detector.pause()
    counters.forget()
    run_gate.reset()
    _handle_trend_events(trend.reset(time.time()))
    _anomaly_override = False
    print(f"[PLC] 資料源切換為：{current_source}，ML 滑動視窗已清空")
    _polling_task = asyncio.create_task(_run_source_loop())


async def _run_source_loop():
    if current_source == "mock":
        print("[PLC] 使用模擬資料源")
        await _mock_poll_loop()
    else:
        await _opcua_poll_loop()


async def _opcua_poll_loop():
    print(f"[PLC] 啟動 OPC UA 連線：{PLC_URL}")
    while True:
        try:
            async with Client(url=PLC_URL) as client:
                profile = get_profile(get_active_device_id()) or {"tags": {}}
                nodes = {}
                for tag, spec in profile["tags"].items():
                    try:
                        nodes[tag] = await client.nodes.root.get_child(DB1_PATH + [f"{NS}:{spec['node']}"])
                    except Exception:
                        # PLC 程式還沒有這個變數：略過它，其餘訊號照常讀
                        print(f"[PLC] ⚠ DB1 找不到變數 {spec['node']}，略過訊號 {tag}")
                if not nodes:
                    raise RuntimeError("DB1 裡找不到任何設備規格列出的變數")
                try:
                    enable_node = await client.nodes.root.get_child(DB1_PATH + [f"{NS}:motor_enable"])
                except Exception:
                    enable_node = None
                # 生產訊號：舊版 PLC 程式沒有這些變數時略過，停機判斷退回看 motor_enable
                prod_nodes = {}
                for key, spec in get_production(get_active_device_id()).items():
                    try:
                        prod_nodes[key] = await client.nodes.root.get_child(DB1_PATH + [f"{NS}:{spec['node']}"])
                    except Exception:
                        pass
                print(f"[PLC] ✅ 連線成功，讀取 {len(nodes)} 個訊號：{', '.join(nodes)}")
                if prod_nodes:
                    print(f"[PLC] 生產訊號 {len(prod_nodes)} 個：{', '.join(prod_nodes)}")
                else:
                    print("[PLC] ⚠ DB1 沒有生產訊號（machine_state 等），停機判斷改看 motor_enable，不計算 OEE")

                while True:
                    values = {}
                    for tag, node in nodes.items():
                        values[tag] = round(float(await node.read_value()), 3)
                    enabled = int(bool(await enable_node.read_value())) if enable_node else 1
                    production = {key: int(await node.read_value()) for key, node in prod_nodes.items()}
                    await _process_reading(values, enabled, production)
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[PLC] ❌ 連線失敗：{e}")
            print("[PLC] 5 秒後重試...")
            await asyncio.sleep(5)


async def _mock_poll_loop():
    global _anomaly_override
    tags = [t for t in get_tags(get_active_device_id()) if t in MOCK_SIGNALS]
    values = {t: MOCK_SIGNALS[t]["base"] for t in tags}
    while True:
        anomalous = _anomaly_override and time.time() < _anomaly_deadline
        if _anomaly_override and not anomalous:
            _anomaly_override = False
            print("[PLC] 模擬異常結束，回復正常波動")
        for t in tags:
            sig = MOCK_SIGNALS[t]
            if anomalous:
                # 朝異常目標值靠攏（每秒推進 40%），而非瞬間跳變，較貼近真實故障發展過程
                values[t] += (sig["anomaly"] - values[t]) * 0.4 + random.gauss(0, sig["noise"] * 0.6)
            else:
                values[t] += random.gauss(0, sig["noise"]) + 0.02 * (sig["base"] - values[t])
            values[t] = max(0.0, values[t])
        await _process_reading({t: round(v, 3) for t, v in values.items()}, 1, _mock_production(values, anomalous))
        await asyncio.sleep(1)


def _mock_production(values: dict, anomalous: bool) -> dict:
    """內建模擬：一直是運轉狀態，依轉速累加產量（轉速低就做得慢）"""
    speed = values.get("motor_speed", MOCK_SIGNALS["motor_speed"]["base"])
    _mock_prod["acc"] += max(0.0, speed) / MOCK_SIGNALS["motor_speed"]["base"] / MOCK_CYCLE_SECONDS
    while _mock_prod["acc"] >= 1.0 - 1e-9:     # 1e-9：1/6 連加 6 次會是 0.9999…，浮點誤差不該少算一件
        _mock_prod["acc"] = max(0.0, _mock_prod["acc"] - 1.0)
        key = "ng" if random.random() < MOCK_NG_RATE[anomalous] else "good"
        _mock_prod[key] += 1
    return {"machine_state": RUN, "good_count": _mock_prod["good"], "ng_count": _mock_prod["ng"], "product_id": 1}


async def _process_reading(values: dict, motor_enable: int = 1, production: dict | None = None):
    """
    每秒一次：停機判斷 → ML 推論 → 趨勢預警 → 判斷告警 → 維護事件 → 必要時觸發 AI 診斷 → 推送畫面、寫入資料庫

    停機時暫停 ML 異常判斷（core/production.RunGate）：
    - 停機中、或剛恢復運轉數值還沒回穩時，ML 不推論、滑動視窗清空
    - 規格告警（警戒值、危險值）照常判斷：停機後溫度還很高一樣是安全問題

    兩種告警來源分開看：
    - 規格告警（rule_alarm）：任一訊號達警戒值或危險值，依設備規格判斷，反應最快
    - ML 異常（is_anomaly）：Isolation Forest 看多訊號組合，能抓到「各自都還沒超標」的異常
    AI 診斷在「規格告警」或「ML 異常且有訊號超出正常範圍」時觸發；
    ML 異常但所有訊號都正常時，多半是統計雜訊，只記錄不診斷。
    """
    global _last_ollama_call, _last_anomaly_at, _latest_ai_analysis, _latest_ai_source, _incident
    device_id = get_active_device_id()
    now = time.time()
    at = datetime.now(timezone.utc)

    production = production or {}
    machine_state = production.get("machine_state")

    profile = get_profile(device_id) or {"tags": {}}
    levels = {tag: evaluate(spec, values[tag]) for tag, spec in profile["tags"].items() if values.get(tag) is not None}
    rule_alarm = any(lv in ("warning", "danger") for lv in levels.values())
    out_of_range = any(lv != "normal" for lv in levels.values())

    gate = run_gate.update(is_running(machine_state, motor_enable), not out_of_range, now)
    if gate["just_paused"]:
        detector.pause()
    if gate["status"] == "active":
        anomaly = detector.update(values)
    else:
        anomaly = detector.paused_result(gate)
    anomaly["rule_alarm"] = rule_alarm
    _handle_trend_events(trend.update(device_id, values, profile["tags"], now, gate["status"] == "active"))
    abnormal = bool(anomaly.get("is_anomaly")) or rule_alarm

    if abnormal:
        if (
            _incident is None
            or _incident["device_id"] != device_id
            or now - _last_anomaly_at > INCIDENT_GAP_SECONDS
        ):
            # 新的一次事件：清掉上一次的診斷，並允許立刻重新診斷
            _latest_ai_analysis = ""
            _latest_ai_source = None
            _last_ollama_call = 0.0
            _incident = new_incident(device_id, at)
        _last_anomaly_at = now

        severity, worst = severity_and_level(device_id, values)
        absorb(_incident, values, severity, worst, anomaly.get("score"), at)

        should_diagnose = rule_alarm or (anomaly.get("is_anomaly") and out_of_range)
        if not should_diagnose:
            anomaly["note"] = "所有訊號都在正常範圍內，未觸發 AI 診斷"
        elif now - _last_ollama_call > OLLAMA_COOLDOWN:
            _last_ollama_call = now
            asyncio.create_task(_fetch_ollama_analysis(dict(values), device_id, _incident, severity))

        if _latest_ai_analysis:
            anomaly["ai_analysis"] = _latest_ai_analysis
            anomaly["ai_source"] = _latest_ai_source
        anomaly["incident_id"] = _incident["id"]   # 事件第一秒還沒寫進資料庫時會是 None

    step = counters.step(device_id, now, production.get("good_count"), production.get("ng_count"))
    if step.reset:
        print(f"[PLC] {device_id} 生產計數歸零（換班歸零或 PLC 重新下載）")

    data = {
        "timestamp": now,
        **values,              # 頂層保留各訊號欄位，相容舊前端
        "values": values,      # 新前端讀這個
        "motor_enable": motor_enable,
        "anomaly": anomaly,
        "trend": trend.snapshot(gate["status"]),
        "production": {
            "state": machine_state,
            "state_text": STATE_TEXT.get(machine_state) if machine_state is not None else None,
            "good_total": production.get("good_count"),
            "ng_total": production.get("ng_count"),
            "product_id": production.get("product_id"),
        } if production else None,
        "source": current_source,
        "device_id": device_id,
    }

    await manager.broadcast(data)
    asyncio.create_task(write_to_db(device_id, values, at))
    asyncio.create_task(write_status(device_id, at, {
        "state": machine_state,
        "motor_enable": bool(motor_enable),
        "product_id": production.get("product_id"),
        "good_total": production.get("good_count"),
        "ng_total": production.get("ng_count"),
        "good_delta": step.good_delta,
        "ng_delta": step.ng_delta,
        "dt": step.dt,
    }))
    if abnormal:
        asyncio.create_task(record_anomaly(device_id, values, anomaly, _incident, at))


_trend_logged: dict[str, float] = {}   # 每個訊號上次印預估時間的時間，預警中每分鐘印一次


def _handle_trend_events(events: list[dict]):
    """預警開始／結束印在 log，預警中每分鐘印一次最新預估；寫入 trend_warnings 在 B 包加上"""
    for e in events:
        ep = e["episode"]
        name = f"{ep['device_id']} {ep['tag_name']}"
        line = "警戒" if ep["target_level"] == "warning" else "危險"
        if e["type"] == "start":
            _trend_logged[name] = ep["started_at"]
            print(f"[TREND] {name} 預警：約 {ep['first_eta_s'] // 60} 分鐘後達{line}值 {ep['threshold']}"
                  f"（每分鐘 +{ep['slope_per_min']}）")
        elif e["type"] == "update" and ep["ended_at"] - _trend_logged.get(name, 0) >= 60:
            _trend_logged[name] = ep["ended_at"]
            snap = trend.tags.get(ep["tag_name"])
            eta = snap.last.get("eta_s") if snap else None
            if eta is not None:
                print(f"[TREND] {name} 預估 {eta // 60} 分 {eta % 60} 秒後達{line}值（目前 {ep['value_at_end']}，"
                      f"每分鐘 +{ep['slope_per_min']}）")
        elif e["type"] == "end":
            _trend_logged.pop(name, None)
            print(f"[TREND] {name} {line}值預警結束：{ep['outcome']}")


async def _fetch_ollama_analysis(values: dict, device_id: str, incident: dict, severity: int):
    """
    診斷完成後只寫回「觸發它的那次事件」：
    - 畫面顯示：那次事件還在進行中才更新，避免舊診斷掛到新事件上
    - 事件表：這份診斷產生時的嚴重度不低於目前那份，才取代（保留最嚴重時刻的診斷）
    """
    global _latest_ai_analysis, _latest_ai_source
    result = await analyze_anomaly(values, device_id=device_id)
    print(f"[Ollama] 分析完成：\n{result['text']}")

    if incident is _incident:
        _latest_ai_analysis = result["text"]
        _latest_ai_source = result.get("source")

    if result.get("ok") and severity >= incident.get("diag_severity", -1):
        incident["diag_severity"] = severity
        incident["ai_analysis"] = result["text"]
        incident["ai_source"] = result.get("source")
        asyncio.create_task(save_incident(incident))
