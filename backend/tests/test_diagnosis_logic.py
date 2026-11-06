"""
AI 診斷與事件規則的單元測試：不需要資料庫、Ollama、Qdrant
執行（在 backend 資料夾）：python -m pytest tests -q

章節標題取自手冊測試版 1～3 的實際章節；讀值取自 PLCSIM fault 4 / fault 5 的實測值
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.incidents import absorb, diagnosis_type, new_incident, severity_and_level
from ml.ollama_analyzer import (
    compose_text, condition_states, condition_summary, make_source, parse_answer,
    rank_candidates, select_primary,
)

DEV = "S7-1511T-SIM"

FAULT4 = {"motor_speed": 1480, "temperature": 78, "pressure": 5.1, "vibration": 4.2, "current": 25.4}
FAULT5 = {"motor_speed": 1258, "temperature": 88, "pressure": 3.77, "vibration": 1.95, "current": 29.6}
FAULT5_EARLY = {"motor_speed": 1440, "temperature": 74, "pressure": 5.0, "vibration": 1.3, "current": 30.6}

V3 = "S71511T設備維護手冊測試版3.pdf"
V1 = "S71511T設備維護手冊測試版1.pdf"
SECTIONS = [
    # (檔名, 章節, 頁碼, 向量分數) —— 向量分數刻意讓錯的章節比較高，模擬實測的排序問題
    (V3, "4. 綜合異常情境（振動與電流） > 4.4 轉速高 + 電流低 + 壓力正常", 3, 0.775),
    (V1, "5. 綜合異常情境（多參數同時異常） > 5.3 轉速低 + 溫度正常 + 壓力低", 3, 0.752),
    (V3, "4. 綜合異常情境（振動與電流） > 4.1 振動高 + 溫度偏高 + 電流正常", 3, 0.737),
    (V3, "4. 綜合異常情境（振動與電流） > 4.2 電流高 + 轉速低 + 溫度高 + 壓力低", 3, 0.722),
    (V3, "3. 電流異常診斷 > 3.1 電流超過 28 A", 2, 0.700),
    (V3, "1. 系統參數規格（振動與電流）", 1, 0.710),
    (V3, "2. 振動異常診斷 > 2.1 振動持續上升超過 2.8 mm/s", 2, 0.622),
    (V1, "5. 綜合異常情境（多參數同時異常） > 5.1 轉速高 + 溫度高 + 壓力正常", 3, 0.739),
]


def _results():
    return [
        SimpleNamespace(score=vec, payload={"source_filename": f, "section": sec, "page": page,
                                            "chunk_index": i, "text": f"【{sec}】內文"})
        for i, (f, sec, page, vec) in enumerate(SECTIONS)
    ]


def _pick(values):
    ranked = rank_candidates(_results(), condition_states(values, DEV))
    primary, match_type = select_primary(ranked)
    return make_source(primary, match_type)


# ---------- 選章節 ----------

def test_fault5_picks_4_2_even_though_vector_ranks_it_lower():
    src = _pick(FAULT5)
    assert src["label"] == "測試版3 §4.2"
    assert src["match_type"] == "full"
    assert (src["match"], src["contra"]) == (4, 0)
    assert src["page"] == 3


def test_fault4_picks_4_1():
    src = _pick(FAULT4)
    assert src["label"] == "測試版3 §4.1"
    assert src["match_type"] == "full"


def test_fault5_early_single_signal_picks_3_1_not_spec_section():
    src = _pick(FAULT5_EARLY)
    assert src["label"] == "測試版3 §3.1"
    assert src["match_type"] == "full"


def test_partial_when_no_full_match():
    # 只剩手冊1 §5.3（溫度正常的條件與現況矛盾）
    results = [r for r in _results() if "5.3" in r.payload["section"]]
    ranked = rank_candidates(results, condition_states(FAULT5, DEV))
    primary, match_type = select_primary(ranked)
    assert match_type == "partial"
    assert make_source(primary, match_type)["label"] == "測試版1 §5.3"


def test_reference_when_only_descriptive_sections():
    results = [r for r in _results() if r.payload["section"].startswith("1.")]
    ranked = rank_candidates(results, condition_states(FAULT5, DEV))
    primary, match_type = select_primary(ranked)
    assert match_type == "reference"
    assert make_source(primary, match_type)["label"] == "測試版3 §1"


def test_no_results():
    assert select_primary([]) == (None, None)


def test_condition_summary():
    assert condition_summary(FAULT5, DEV) == "轉速低 + 溫度高 + 壓力低 + 電流高（振動正常）"


# ---------- 組合文字與解析 ----------

def test_compose_text_adds_basis_by_backend():
    src = _pick(FAULT5)
    text = compose_text("機械卡滯", "檢查負載端是否有異物卡入", src)
    assert text == "原因：機械卡滯（依據：測試版3 §4.2）\n處置：檢查負載端是否有異物卡入"


def test_compose_text_partial_and_none():
    results = [r for r in _results() if "5.3" in r.payload["section"]]
    ranked = rank_candidates(results, condition_states(FAULT5, DEV))
    src = make_source(*select_primary(ranked))
    assert "（依據：測試版1 §5.3，部分符合）" in compose_text("推論：x", "y", src)
    assert "（依據：無對應章節）" in compose_text("推論：x", "", None)
    assert compose_text("x", "", None).endswith("處置：請依手冊章節逐項檢查")


def test_parse_answer_strips_model_written_basis():
    cause, action = parse_answer('{"cause": "軸承磨損（依據：測試版3 §4.1）", "action": "檢查潤滑"}')
    assert (cause, action) == ("軸承磨損", "檢查潤滑")


def test_parse_answer_fallback_on_plain_text():
    assert parse_answer("不是 JSON 的回答") == ("不是 JSON 的回答", "")


# ---------- 事件規則 ----------

def test_severity_and_level():
    # 轉速偏低 1 + 溫度警戒 3 + 壓力偏低 1 + 振動正常 0 + 電流警戒 3
    assert severity_and_level(DEV, FAULT5) == (8, "warning")
    sev, lv = severity_and_level(DEV, {"motor_speed": 1480, "temperature": 70, "pressure": 5.1,
                                       "vibration": 1.2, "current": 24.0})
    assert (sev, lv) == (0, "normal")


def test_absorb_tracks_peaks_both_directions():
    t0 = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)
    inc = new_incident(DEV, t0)
    for i, v in enumerate([FAULT5_EARLY, FAULT5]):
        sev, lv = severity_and_level(DEV, v)
        absorb(inc, {**v, "source": "opcua"}, sev, lv, -0.05 - i * 0.1, t0 + timedelta(seconds=i))
    assert inc["records"] == 2
    assert inc["peak"]["max"]["current"] == 30.6
    assert inc["peak"]["min"]["motor_speed"] == 1258       # 偏低的異常要看最小值
    assert "source" not in inc["peak"]["max"]               # 非規格訊號不進峰值
    assert inc["min_score"] == pytest.approx(-0.15)
    assert inc["ended_at"] == t0 + timedelta(seconds=1)
    assert inc["level"] == "warning"


# ---------- 診斷來源分類 ----------

def test_診斷來源分類():
    new_text = compose_text("軸承磨損", "更換軸承", {"label": "測試版3 §4.1", "match_type": "full"})
    assert diagnosis_type(new_text, {"label": "測試版3 §4.1"}) == "verified"
    assert diagnosis_type(compose_text("推論：x", "", None), None) == "no_manual"
    # 實際舊資料（事件 22）：模型自己寫了依據，但沒有 ai_source
    old = "原因：轉速低、溫度高（依據：測試版3 §4.2）\n處置：檢查負載端"
    assert diagnosis_type(old, None) == "legacy"
    assert diagnosis_type(None, None) == "none"
    assert diagnosis_type("", None) == "none"
