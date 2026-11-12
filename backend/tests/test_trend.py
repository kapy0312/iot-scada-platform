"""
趨勢預警的測試（不需要資料庫）：用合成的訊號曲線確認會不會預警、預估時間準不準
執行（在 backend 資料夾）：python -m pytest tests/test_trend.py -q
"""
import asyncio
import math
import random

from core import plc_simulator as ps
from core.device_profile import get_profile
from core.trend import ABOVE, COLLECTING, FORECAST, STABLE, TrendTracker, analyze, theil_sen

DEV = "S7-1511T-SIM"
SPECS = get_profile(DEV)["tags"]
NORMAL = {"motor_speed": 1480, "temperature": 70, "pressure": 5.0, "vibration": 1.2, "current": 24}
NOISE = {"motor_speed": 4, "temperature": 0.3, "pressure": 0.05, "vibration": 0.05, "current": 0.2}


def run(tracker, seconds, temp_fn, start=0.0, active=True, seed=1, values_fn=None):
    """跑 seconds 秒，溫度依 temp_fn(t) 變化，其他訊號平穩加雜訊；回傳所有事件"""
    rng = random.Random(seed)
    events = []
    for i in range(int(seconds)):
        t = start + i
        vals = {k: v + rng.uniform(-1, 1) * NOISE[k] for k, v in NORMAL.items()}
        vals["temperature"] = temp_fn(t) + rng.uniform(-1, 1) * NOISE["temperature"]
        if values_fn:
            values_fn(vals, t)
        events += tracker.update(DEV, vals, SPECS, t, active)
    return events


def starts(events):
    return [e["episode"] for e in events if e["type"] == "start"]


def ends(events):
    return [e["episode"] for e in events if e["type"] == "end"]


def test_theil_sen不受單點突波影響():
    t = list(range(20))
    v = [0.5 * x for x in t]
    v[10] = 1000
    import numpy as np
    assert abs(theil_sen(np.array(t, float), np.array(v, float)) - 0.5) < 1e-9


def test_資料不到5分鐘只顯示收集中():
    tr = TrendTracker()
    events = run(tr, 200, lambda t: 70 + t / 60)
    assert events == []
    snap = tr.snapshot("active")["tags"]["temperature"]
    assert snap["state"] == COLLECTING and snap["need"] == 30


def test_平穩雜訊不預警():
    tr = TrendTracker()
    events = run(tr, 1800, lambda t: 70)
    assert starts(events) == []
    assert tr.snapshot("active")["tags"]["temperature"]["state"] == STABLE


def test_每分鐘升1度_預估時間準():
    tr = TrendTracker()
    events = run(tr, 900, lambda t: 70 + t / 60)       # 15 分鐘到 85
    st = starts(events)
    assert len(st) == 1
    ep = st[0]
    assert ep["tag_name"] == "temperature" and ep["target_level"] == "warning"
    # 實際到達時間＝900 秒；預估值要在 ±10% 內
    actual = 900 - ep["started_at"]
    assert abs(ep["first_eta_s"] - actual) <= 0.1 * actual
    assert 300 <= ep["started_at"] <= 400                # 5 分鐘收資料＋30 秒延遲生效
    assert abs(ep["slope_per_min"] - 1.0) < 0.1


def test_到了警戒值就結束並改預估危險值():
    tr = TrendTracker()
    events = run(tr, 1500, lambda t: 70 + t / 60)
    done = ends(events)
    assert done[0]["outcome"] == "reached" and done[0]["target_level"] == "warning"
    assert 880 <= done[0]["reached_at"] <= 940
    # 85 之後繼續升，到 100（危險）前會再出現一筆危險值預警
    dangers = [e for e in starts(events) if e["target_level"] == "danger"]
    assert len(dangers) == 1


def test_預估超過60分鐘不預警():
    tr = TrendTracker()
    events = run(tr, 1200, lambda t: 70 + t / 600)     # 每分鐘 0.1 度，要 150 分鐘
    assert starts(events) == []


def test_一階延遲快到頂的曲線不誤報():
    # 從 70 往 80 靠（時間常數 3 分鐘），不會超過 85，不該預警
    tr = TrendTracker()
    events = run(tr, 1800, lambda t: 70 + 10 * (1 - math.exp(-t / 180)))
    assert starts(events) == []


def test_單一突波不預警():
    tr = TrendTracker()
    events = run(tr, 1200, lambda t: 95 if 600 <= t < 605 else 70)
    assert starts(events) == []


def test_上升停止後預警解除():
    tr = TrendTracker()
    events = run(tr, 1500, lambda t: 70 + min(t, 480) / 60)   # 升 8 分鐘停在 78
    assert len(starts(events)) == 1
    assert ends(events)[0]["outcome"] == "cleared"
    assert tr.snapshot("active")["tags"]["temperature"]["state"] == STABLE


def test_停機時清空並記為中斷():
    tr = TrendTracker()
    events = run(tr, 450, lambda t: 70 + t / 60)
    assert len(starts(events)) == 1
    events = run(tr, 5, lambda t: 40, start=450, active=False)
    assert ends(events)[0]["outcome"] == "interrupted"
    assert tr.snapshot("paused_stopped")["tags"] == {}
    # 恢復後要重新收 5 分鐘
    events = run(tr, 200, lambda t: 70, start=500)
    assert tr.snapshot("active")["tags"]["temperature"]["state"] == COLLECTING


def test_超過危險值就沒有可預估的線():
    r = analyze([(i * 10.0, 105.0) for i in range(40)], 400, SPECS["temperature"], 30, 18, 3600)
    assert r["state"] == ABOVE and r["candidate"] is False


def test_每秒流程帶趨勢欄位(monkeypatch):
    out = []

    async def broadcast(data):
        out.append(data)

    async def noop(*a):
        pass

    from core.production import RunGate
    gate = RunGate(30, 120)
    gate.stopped, gate.last_seen = False, 0.0
    tr = TrendTracker()
    clock = {"t": 1.0}
    monkeypatch.setattr(ps, "run_gate", gate)
    monkeypatch.setattr(ps, "trend", tr)
    monkeypatch.setattr(ps, "OPCUA_DEVICE_ID", DEV)
    monkeypatch.setattr(ps, "current_source", "opcua")
    monkeypatch.setattr(ps.manager, "broadcast", broadcast)
    for name in ("write_to_db", "write_status", "record_anomaly", "_fetch_ollama_analysis"):
        monkeypatch.setattr(ps, name, noop)
    saved = []

    async def save_trend(ep):
        saved.append(ep)
    monkeypatch.setattr(ps, "save_trend", save_trend)
    monkeypatch.setattr(ps.time, "time", lambda: clock["t"])
    prod = {"machine_state": 1, "good_count": 0, "ng_count": 0, "product_id": 1}

    async def go():
        for i in range(420):
            clock["t"] = 1.0 + i
            await ps._process_reading({**NORMAL, "temperature": 70 + i / 60}, 1, prod)
        await asyncio.sleep(0)
    asyncio.run(go())
    t = out[-1]["trend"]
    assert t["status"] == "active" and t["horizon_s"] == 3600
    temp = t["tags"]["temperature"]
    assert temp["state"] == FORECAST and temp["target"] == "warning"
    assert 400 <= temp["eta_s"] <= 560        # 第 7 分鐘：約 77 度，剩 8 分鐘
    assert t["tags"]["pressure"]["state"] == STABLE
    assert t["device_id"] == DEV and ps.latest_trend is t
    # 預警開始那筆＋之後每 10 秒的更新都會送去寫資料庫
    assert saved[0]["outcome"] == "ongoing" and saved[0]["tag_name"] == "temperature"
    assert len(saved) >= 2 and all(e["started_at"] == saved[0]["started_at"] for e in saved)


def test_趨緩但終點超過警戒值_仍會預警且時間合理():
    # 從 70 往 95 靠（時間常數 10 分鐘）：實際在 t = -600·ln(1-15/25) ≈ 550 秒到 85
    tr = TrendTracker()
    events = run(tr, 700, lambda t: 70 + 25 * (1 - math.exp(-t / 600)))
    ep = starts(events)[0]
    actual = 550 - ep["started_at"]
    assert abs(ep["first_eta_s"] - actual) <= max(60, 0.25 * actual)


def test_開機暖機曲線不誤報():
    # 恢復期放行後溫度還在往 70 靠一點點（像 PLCSIM 時間常數 20 秒）
    tr = TrendTracker()
    events = run(tr, 1800, lambda t: 70 - 5 * math.exp(-t / 20))
    assert starts(events) == []


def test_多組亂數都不誤報也不漏報():
    for seed in range(20):
        assert starts(run(TrendTracker(), 1200, lambda t: 70, seed=seed)) == [], seed
        st = starts(run(TrendTracker(), 900, lambda t: 70 + t / 60, seed=seed))
        assert len(st) == 1, seed


def test_振動漸進劣化():
    # 每分鐘 +0.1 mm/s：從 1.2 到 2.8 約 16 分鐘
    def vib(vals, t):
        vals["vibration"] = 1.2 + t / 600 + random.Random(t).uniform(-1, 1) * 0.05
    events = run(TrendTracker(), 1100, lambda t: 70, values_fn=vib)
    ep = [e for e in starts(events) if e["tag_name"] == "vibration" and e["target_level"] == "warning"]
    assert len(ep) == 1
    actual = 960 - ep[0]["started_at"]
    assert abs(ep[0]["first_eta_s"] - actual) <= 0.15 * actual


def test_平了一陣子才開始升_預估不會多估():
    # 前 3 分鐘平穩、之後每分鐘 +1：實際在 180 + 900 = 1080 秒到 85（PLCSIM 實測時的操作順序）
    tr = TrendTracker()
    events = run(tr, 1100, lambda t: 70 + max(0.0, t - 180) / 60)
    ep = starts(events)[0]
    actual = 1080 - ep["started_at"]
    assert abs(ep["first_eta_s"] - actual) <= 0.15 * actual


def test_慢慢飄的雜訊不誤報():
    # 溫度跟著環境慢慢飄（時間常數約 50 秒、幅度約 ±1 °C，像 mock 的雜訊），2 小時內不該預警
    for seed in range(3):
        rng = random.Random(seed)
        wander = {"v": 0.0}

        def temp(t):
            wander["v"] += rng.gauss(0, 0.2) - 0.02 * wander["v"]
            return 70 + wander["v"]
        assert starts(run(TrendTracker(), 7200, temp, seed=seed)) == [], seed


def test_內建模擬劣化(monkeypatch):
    monkeypatch.setattr(ps, "current_source", "mock")
    st = ps.set_mock_drift("temperature", 1.5)
    assert st["tag"] == "temperature" and st["rate_per_min"] == 1.5
    try:
        import pytest
        with pytest.raises(ValueError):
            ps.set_mock_drift("no_such_tag", 1.0)
        assert ps.set_mock_drift(None) is None and ps.mock_drift_state() is None
        monkeypatch.setattr(ps, "current_source", "opcua")
        with pytest.raises(ValueError):
            ps.set_mock_drift("temperature", 1.0)
    finally:
        ps._mock_drift.update(tag=None, rate=0.0, offset=0.0, since=None)


def test_到了之後在警戒值附近晃不會重複預警():
    # 升到 86 之後在 84～86 之間晃 10 分鐘：警戒值只預警一次；之後回到正常再升，會重新預警
    def temp(t):
        if t < 960:
            return 70 + t / 60
        if t < 1560:
            return 85 + 1.2 * math.sin(t / 40)
        if t < 1800:
            return 72
        return 72 + (t - 1800) / 60
    events = run(TrendTracker(), 3000, temp)
    warns = [e for e in starts(events) if e["target_level"] == "warning"]
    assert len(warns) == 2, [(e["started_at"], e["first_eta_s"]) for e in warns]
    assert warns[1]["started_at"] > 1800


def test_預警危險值後掉回來_不能算命中():
    # 先升過 85（警戒）再往 100（危險）升，危險預警亮起後降回 80：危險值沒到，結果要是 cleared
    def temp(t):
        if t < 1400:
            return 70 + t / 60                       # 1400 秒時約 93.3
        return max(80.0, 93.3 - (t - 1400) / 10)     # 之後每 10 秒降 1 度，停在 80
    tr = TrendTracker()
    events = run(tr, 2200, temp)
    dangers = [e for e in ends(events) if e["target_level"] == "danger"]
    assert dangers, "危險值預警應該有亮過"
    assert all(e["outcome"] == "cleared" and e["reached_at"] is None for e in dangers)
    warnings = [e for e in ends(events) if e["target_level"] == "warning"]
    assert warnings and warnings[0]["outcome"] == "reached"     # 警戒值真的到了，仍算命中
