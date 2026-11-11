"""
生產計數、停機暫停偵測的測試
- 前半不需要資料庫：計數換算、暫停規則、訓練過濾、每秒處理流程
- 最後一段要一個「空的、沒裝 TimescaleDB 的」PostgreSQL 測試資料庫（TEST_PROD_DB_URL）才會執行：
  執行 005 的建表語句（略過 TimescaleDB 專屬的語句），確認後端寫得進去、檢查條件有效
  ⚠ 會刪掉並重建 machine_status：千萬不要指向正式資料庫；偵測到 timescaledb 會直接拒絕執行
執行（在 backend 資料夾）：python -m pytest tests -q
"""
import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from core import plc_simulator as ps
from core.device_profile import get_production, get_tags
from core.production import (
    CHANGEOVER, FAULT, IDLE, PLANNED_STOP, RUN, CounterTracker, RunGate, is_running,
)
from ml.train import running_mask

DEV = "S7-1511T-SIM"
NORMAL = {"motor_speed": 1480, "temperature": 70, "pressure": 5.0, "vibration": 1.2, "current": 24}
STOPPED = {"motor_speed": 0, "temperature": 40, "pressure": 0.3, "vibration": 0, "current": 0}
BACKEND = Path(__file__).resolve().parents[1]


# ---------- 訊號分類 ----------

def test_生產訊號不在製程訊號清單裡():
    tags = get_tags(DEV)
    prod = get_production(DEV)
    assert set(prod) == {"machine_state", "good_count", "ng_count", "product_id"}
    assert not set(tags) & set(prod)
    assert tags == ["motor_speed", "temperature", "pressure", "vibration", "current"]


def test_沒有規格的設備沒有生產訊號():
    assert get_production("UNKNOWN") == {}


# ---------- 是否運轉 ----------

def test_有狀態就看狀態():
    assert is_running(RUN, 0) is True       # 狀態優先
    for st in (PLANNED_STOP, IDLE, FAULT, CHANGEOVER):
        assert is_running(st, 1) is False   # 故障、換模時 motor_enable 還是 1，也算停機


def test_舊版PLC沒有狀態就看motor_enable():
    assert is_running(None, 1) is True
    assert is_running(None, 0) is False


# ---------- 計數換算 ----------

def test_第一筆不算增量也不算有資料():
    c = CounterTracker()
    s = c.step(DEV, 100.0, 50, 2)
    assert (s.good_delta, s.ng_delta, s.dt) == (0, 0, 0.0)


def test_正常累加():
    c = CounterTracker()
    c.step(DEV, 100.0, 50, 2)
    s = c.step(DEV, 101.0, 51, 2)
    assert (s.good_delta, s.ng_delta, s.dt, s.reset, s.gap) == (1, 0, 1.0, False, False)


def test_計數歸零不會變負數():
    c = CounterTracker()
    c.step(DEV, 100.0, 500, 20)
    s = c.step(DEV, 101.0, 1, 0)       # 歸零後馬上做了 1 件
    assert (s.good_delta, s.ng_delta, s.reset) == (1, 0, True)


def test_斷線太久那段不算進OEE():
    c = CounterTracker()
    c.step(DEV, 100.0, 50, 2)
    s = c.step(DEV, 400.0, 100, 3)     # 後端停了 5 分鐘
    assert (s.good_delta, s.ng_delta, s.gap) == (0, 0, True)
    assert s.dt == 5.0                 # 最多算 5 秒，其餘是「無資料」
    s = c.step(DEV, 401.0, 101, 3)     # 下一秒恢復正常
    assert (s.good_delta, s.gap) == (1, False)


def test_短暫延遲照算但最多5秒():
    c = CounterTracker()
    c.step(DEV, 100.0, 50, 2)
    s = c.step(DEV, 108.0, 51, 2)      # 8 秒：還沒超過 10 秒門檻
    assert (s.good_delta, s.dt, s.gap) == (1, 5.0, False)


def test_沒有生產變數時增量為0():
    c = CounterTracker()
    c.step(DEV, 100.0, None, None)
    s = c.step(DEV, 101.0, None, None)
    assert (s.good_delta, s.ng_delta, s.dt) == (0, 0, 1.0)


def test_不同設備分開記():
    c = CounterTracker()
    c.step("A", 100.0, 10, 0)
    c.step("B", 100.0, 999, 0)
    assert c.step("A", 101.0, 11, 0).good_delta == 1


def test_切換資料源後重新開始():
    c = CounterTracker()
    c.step(DEV, 100.0, 10, 0)
    c.forget()
    assert c.step(DEV, 101.0, 11, 0).good_delta == 0


# ---------- 停機暫停 ----------

def test_停機時暫停並要求清空視窗():
    g = RunGate(30, 120)
    assert g.update(True, True, 0)["status"] == "active"
    r = g.update(False, False, 1)
    assert r == {"status": "paused_stopped", "just_paused": True}
    assert g.update(False, False, 2)["just_paused"] is False   # 只在剛停的那一秒清一次


def test_恢復運轉至少等30秒():
    g = RunGate(30, 120)
    g.update(False, False, 0)
    assert g.update(True, True, 10)["status"] == "paused_recovering"
    assert g.update(True, True, 39)["status"] == "paused_recovering"
    assert g.update(True, True, 40)["status"] == "active"


def test_數值沒回穩就繼續等():
    g = RunGate(30, 120)
    g.update(False, False, 0)
    g.update(True, False, 1)
    assert g.update(True, False, 60)["status"] == "paused_recovering"   # 溫度還沒回到正常
    assert g.update(True, True, 61)["status"] == "active"


def test_最多等120秒_啟動時就有故障不會被藏起來():
    g = RunGate(30, 120)
    g.update(False, False, 0)
    g.update(True, False, 1)
    assert g.update(True, False, 120)["status"] == "paused_recovering"
    assert g.update(True, False, 121)["status"] == "active"


def test_恢復期中又停機():
    g = RunGate(30, 120)
    g.update(False, False, 0)
    g.update(True, True, 1)
    assert g.update(False, False, 10) == {"status": "paused_stopped", "just_paused": False}
    assert g.update(True, True, 11)["status"] == "paused_recovering"   # 恢復期重新計算


def test_後端剛啟動時機台在運轉直接判斷():
    assert RunGate(30, 120).update(True, True, 0)["status"] == "active"


# ---------- 訓練過濾 ----------

def _wide(rows):
    t0 = datetime(2026, 11, 11, tzinfo=timezone.utc)
    idx = [t0 + timedelta(seconds=i) for i in range(len(rows))]
    return pd.DataFrame(rows, index=idx)


def test_訓練只用運轉且過了恢復期的資料():
    # 0~9 運轉、10~19 停機、20~99 恢復運轉（溫度一開始偏低，第 60 秒回到正常）
    rows = [NORMAL] * 10 + [STOPPED] * 10 + [{**NORMAL, "temperature": 50}] * 40 + [NORMAL] * 40
    df = _wide(rows)
    status = pd.DataFrame({
        "state": [RUN] * 10 + [IDLE] * 10 + [RUN] * 80,
        "motor_enable": [True] * 10 + [False] * 10 + [True] * 80,
    }, index=df.index)
    keep = running_mask(df, status, DEV, get_tags(DEV)).tolist()
    assert keep[:10] == [True] * 10
    assert keep[10:60] == [False] * 50     # 停機＋恢復期（溫度還沒回穩）
    assert keep[60:] == [True] * 40


def test_沒有狀態紀錄的秒數不用():
    df = _wide([NORMAL] * 5)
    status = pd.DataFrame({"state": [RUN, RUN], "motor_enable": [True, True]}, index=df.index[:2])
    keep = running_mask(df, status, DEV, get_tags(DEV)).tolist()
    assert keep == [True, True, False, False, False]


def test_舊版PLC只有motor_enable也能過濾():
    df = _wide([NORMAL] * 3)
    status = pd.DataFrame({"state": [None, None, None], "motor_enable": [True, False, None]}, index=df.index)
    assert running_mask(df, status, DEV, get_tags(DEV)).tolist() == [True, False, False]


# ---------- 每秒處理流程（不碰資料庫、不碰 Ollama） ----------

class FakeDetector:
    threshold = -0.04

    def __init__(self):
        self.calls = 0
        self.paused = 0

    def update(self, values):
        self.calls += 1
        # 數值偏低就當成 ML 異常（模擬目前「停機被判成偏低異常」的狀況）
        bad = values["motor_speed"] < 1400
        return {"threshold": self.threshold, "is_anomaly": bad, "score": -0.2 if bad else 0.1,
                "status": "anomaly" if bad else "normal"}

    def pause(self):
        self.paused += 1

    def paused_result(self, gate):
        return {"threshold": self.threshold, "is_anomaly": False, "score": 0.0, "status": gate["status"]}


@pytest.fixture
def pipeline(monkeypatch):
    """把每秒流程的外部依賴換成假的，回傳收集到的推送、狀態紀錄、異常紀錄"""
    out = {"broadcast": [], "status": [], "anomaly": [], "diagnose": 0}
    det = FakeDetector()

    async def broadcast(data):
        out["broadcast"].append(data)

    async def write_db(*a):
        pass

    async def write_status(device_id, at, row):
        out["status"].append(row)

    async def record(*a):
        out["anomaly"].append(a)

    async def diagnose(*a):
        out["diagnose"] += 1

    monkeypatch.setattr(ps, "detector", det)
    monkeypatch.setattr(ps, "counters", CounterTracker())
    monkeypatch.setattr(ps, "run_gate", RunGate(30, 120))
    monkeypatch.setattr(ps, "_incident", None)
    monkeypatch.setattr(ps, "_last_ollama_call", 0.0)
    monkeypatch.setattr(ps, "OPCUA_DEVICE_ID", DEV)
    monkeypatch.setattr(ps, "current_source", "opcua")
    monkeypatch.setattr(ps.manager, "broadcast", broadcast)
    monkeypatch.setattr(ps, "write_to_db", write_db)
    monkeypatch.setattr(ps, "write_status", write_status)
    monkeypatch.setattr(ps, "record_anomaly", record)
    monkeypatch.setattr(ps, "_fetch_ollama_analysis", diagnose)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ps.time, "time", lambda: clock["t"])
    out["det"], out["clock"] = det, clock
    return out


async def _run(values, motor_enable, production):
    await ps._process_reading(values, motor_enable, production)
    await asyncio.sleep(0)   # 讓 create_task 的背景工作跑完
    await asyncio.sleep(0)


def _prod(state, good=0, ng=0):
    return {"machine_state": state, "good_count": good, "ng_count": ng, "product_id": 1}


def test_停機不再被判成偏低異常(pipeline):
    async def go():
        await _run(NORMAL, 1, _prod(RUN))
        pipeline["clock"]["t"] += 1
        await _run(STOPPED, 0, _prod(IDLE))
    asyncio.run(go())
    last = pipeline["broadcast"][-1]
    assert last["anomaly"]["status"] == "paused_stopped"
    assert pipeline["det"].calls == 1            # 停機那秒沒有推論
    assert pipeline["det"].paused == 1           # 滑動視窗清空一次
    assert pipeline["anomaly"] == [] and pipeline["diagnose"] == 0


def test_故障停機時motor_enable還是1也暫停(pipeline):
    asyncio.run(_run(STOPPED, 1, _prod(FAULT)))
    assert pipeline["broadcast"][-1]["anomaly"]["status"] == "paused_stopped"
    assert pipeline["det"].calls == 0


def test_停機時危險值照樣告警(pipeline):
    hot = {**STOPPED, "temperature": 105}
    asyncio.run(_run(hot, 0, _prod(IDLE)))
    a = pipeline["broadcast"][-1]["anomaly"]
    assert a["status"] == "paused_stopped" and a["rule_alarm"] is True
    assert len(pipeline["anomaly"]) == 1         # 有記錄成事件
    assert pipeline["diagnose"] == 1             # 規格告警照樣觸發 AI 診斷


def test_推送與狀態紀錄帶生產資料(pipeline):
    async def go():
        await _run(NORMAL, 1, _prod(RUN, 10, 1))
        pipeline["clock"]["t"] += 1
        await _run(NORMAL, 1, _prod(RUN, 12, 1))
    asyncio.run(go())
    p = pipeline["broadcast"][-1]["production"]
    assert p == {"state": RUN, "state_text": "運轉", "good_total": 12, "ng_total": 1, "product_id": 1}
    row = pipeline["status"][-1]
    assert (row["good_delta"], row["ng_delta"], row["dt"], row["state"]) == (2, 0, 1.0, RUN)


def test_舊版PLC沒有生產變數(pipeline):
    asyncio.run(_run(NORMAL, 1, {}))
    assert pipeline["broadcast"][-1]["production"] is None
    row = pipeline["status"][-1]
    assert row["state"] is None and row["motor_enable"] is True and row["good_delta"] == 0


def test_內建模擬會產生計數():
    ps._mock_prod.update({"acc": 0.0, "good": 0, "ng": 0})
    for _ in range(60):
        prod = ps._mock_production({"motor_speed": 1480.0}, False)
    assert prod["machine_state"] == RUN
    assert prod["good_count"] + prod["ng_count"] == 10    # 額定轉速 6 秒一件，60 秒 10 件


# ---------- 需要資料庫 ----------

DB_URL = os.getenv("TEST_PROD_DB_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未設定 TEST_PROD_DB_URL")


def _ddl_without_timescale() -> str:
    """005 的建表語句，拿掉 TimescaleDB 專屬的 SELECT（create_hypertable、retention、確認查詢）"""
    sql = (BACKEND / "db/migrations/005_machine_status.sql").read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    keep = [s for s in sql.split(";") if s.strip() and not s.strip().upper().startswith("SELECT")]
    return ";".join(keep) + ";"


@needs_db
def test_005建表與寫入(monkeypatch):
    import asyncpg
    from db import pool, status_writer

    async def go():
        conn = await asyncpg.connect(DB_URL)
        try:
            if await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"):
                pytest.fail("測試資料庫裝了 timescaledb，可能是正式資料庫，拒絕執行")
            await conn.execute("DROP TABLE IF EXISTS machine_status")
            await conn.execute(_ddl_without_timescale())
            await conn.execute(_ddl_without_timescale())   # 重跑不會出錯
        finally:
            await conn.close()

        monkeypatch.setattr(pool, "get_database_url", lambda: DB_URL)
        at = datetime.now(timezone.utc)
        await status_writer.write_status(DEV, at, {
            "state": RUN, "motor_enable": True, "product_id": 1, "good_total": 12, "ng_total": 1,
            "good_delta": 2, "ng_delta": 0, "dt": 1.0})
        await status_writer.write_status(DEV, at, {
            "state": None, "motor_enable": False, "product_id": None, "good_total": None, "ng_total": None,
            "good_delta": 0, "ng_delta": 0, "dt": 0.0})
        await pool.close_pool()

        conn = await asyncpg.connect(DB_URL)
        try:
            rows = await conn.fetch("SELECT state, good_delta, dt FROM machine_status ORDER BY state NULLS LAST")
            assert [(r["state"], r["good_delta"], r["dt"]) for r in rows] == [(RUN, 2, 1.0), (None, 0, 0.0)]
            for bad in ("state", "good_delta", "dt"):
                vals = {"state": 1, "good_delta": 0, "dt": 1.0}
                vals[bad] = {"state": 9, "good_delta": -1, "dt": 6.0}[bad]
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(
                        "INSERT INTO machine_status (time, device_id, state, good_delta, dt) VALUES (now(), $1, $2, $3, $4)",
                        DEV, vals["state"], vals["good_delta"], vals["dt"])
        finally:
            await conn.close()

    asyncio.run(go())
