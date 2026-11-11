"""
OEE 計算（純計算，不碰資料庫，方便單元測試）

資料來源：oee_hourly（每小時、每品號：各狀態秒數、良品、NG）＋ 班別設定 ＋ 品號標準週期

  可用率 = 運轉秒 ÷（有資料秒 − 計畫停機秒）      → 該開工的時間裡，真的在動的比例
  表現率 = Σ(件數 × 標準週期) ÷ 運轉秒，最多 100%  → 動的時候跑得夠不夠快
  良率   = 良品 ÷ 總數                             → 做出來的有幾成是好的
  OEE    = 可用率 × 表現率 × 良率
  資料覆蓋率 = 有資料秒 ÷ 這段時間的總秒數         → 斷線、關機的時段不算進分母，另外用這個數字提醒

班別：台北時間整點；end_hour <= start_hour 代表跨午夜（例如 20～8 是晚上 8 點到隔天早上 8 點），
相等代表 24 小時一班。班別屬於「開始那一天」：11/11 的晚班是 11/11 20:00～11/12 08:00
"""
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SHIFT_TZ = ZoneInfo(os.getenv("SHIFT_TZ", "Asia/Taipei"))

LOW_COVERAGE = 0.8        # 資料覆蓋率低於這個值，標示「數據不完整」
PERF_OVER = 1.05          # 表現率算出來超過 105%，標示「標準週期可能設錯」


@dataclass(frozen=True)
class Shift:
    name: str
    start_hour: int
    end_hour: int

    @property
    def hours(self) -> int:
        h = (self.end_hour - self.start_hour) % 24
        return h or 24


def validate_shifts(shifts: list[Shift]) -> str | None:
    """回傳錯誤訊息；沒問題回 None。檢查：名稱不重複、整點範圍、各班不重疊、總長不超過 24 小時"""
    if not shifts:
        return "至少要有一個班別"
    names = [s.name.strip() for s in shifts]
    if any(not n for n in names):
        return "班別名稱不能空白"
    if len(set(names)) != len(names):
        return "班別名稱不能重複"
    used: dict[int, str] = {}
    for s in shifts:
        if not (0 <= s.start_hour <= 23 and 0 <= s.end_hour <= 23):
            return f"{s.name}：時間要是 0～23 的整點"
        for i in range(s.hours):
            h = (s.start_hour + i) % 24
            if h in used:
                return f"{s.name} 和 {used[h]} 在 {h}:00 重疊"
            used[h] = s.name
    return None


def is_effective(shifts_for: Callable[[date], list[Shift]], shift: Shift, day: date) -> bool:
    """這一班真的有上：開始時間沒有被前一天還沒結束的班蓋掉（只有換班別那天會發生）"""
    return locate_shift(shifts_for, shift_window(shift, day)[0]) == (shift, day)


def shift_window(shift: Shift, day: date) -> tuple[datetime, datetime]:
    """某一天這個班的起訖時間（台北時間，有時區）"""
    start = datetime(day.year, day.month, day.day, shift.start_hour, tzinfo=SHIFT_TZ)
    return start, start + timedelta(hours=shift.hours)


def locate_shift(shifts_for: Callable[[date], list[Shift]], at: datetime) -> tuple[Shift, date] | None:
    """
    at 這個時間落在哪一班；shifts_for(day) 回傳那一天適用的班別（不同日期可能適用不同套）
    先看昨天開始、還沒結束的班（跨午夜的晚班），再看今天的班：
    換新一套班別的那天，新班別跟前一天還沒結束的班重疊時，重疊的時間算舊的那一班
    """
    local = at.astimezone(SHIFT_TZ).date()
    for day in (local - timedelta(days=1), local):
        for s in shifts_for(day):
            start, end = shift_window(s, day)
            if start <= at < end:
                return s, day
    return None


@dataclass
class HourRow:
    """oee_hourly 的一列"""
    bucket: datetime
    product_id: int | None
    run_s: float = 0.0
    idle_s: float = 0.0
    fault_s: float = 0.0
    changeover_s: float = 0.0
    planned_stop_s: float = 0.0
    data_s: float = 0.0
    unknown_s: float = 0.0
    good: int = 0
    ng: int = 0


def _ratio(a: float, b: float) -> float | None:
    return a / b if b > 0 else None


def summarize(rows: list[HourRow], cycles: dict[int, float], window_s: float) -> dict:
    """
    把一段時間（一班、或一小時）的每小時資料加總成 OEE
    cycles：品號 → 標準週期秒數；window_s：這段時間的總秒數（進行中的班別用「到現在為止」）
    """
    t = {k: 0.0 for k in ("run_s", "idle_s", "fault_s", "changeover_s", "planned_stop_s", "data_s", "unknown_s")}
    good = ng = 0
    ideal_s = 0.0
    missing_products: set = set()
    for r in rows:
        for k in t:
            t[k] += getattr(r, k) or 0.0
        good += r.good or 0
        ng += r.ng or 0
        count = (r.good or 0) + (r.ng or 0)
        if count:
            cycle = cycles.get(r.product_id) if r.product_id is not None else None
            if cycle is None:
                missing_products.add(r.product_id)
            else:
                ideal_s += count * cycle

    total = good + ng
    planned_s = t["data_s"] - t["planned_stop_s"]
    availability = _ratio(t["run_s"], planned_s)
    perf_raw = _ratio(ideal_s, t["run_s"]) if not missing_products else None
    performance = min(perf_raw, 1.0) if perf_raw is not None else None
    quality = _ratio(good, total)
    if availability is not None and t["run_s"] == 0:
        oee = 0.0                     # 該開工卻完全沒動：OEE 就是 0
    elif None in (availability, performance, quality):
        oee = None
    else:
        oee = availability * performance * quality
    coverage = min(_ratio(t["data_s"] + t["unknown_s"], window_s) or 0.0, 1.0) if window_s > 0 else None

    flags = []
    if coverage is not None and coverage < LOW_COVERAGE:
        flags.append("low_coverage")              # 斷線或關機太久，數字僅供參考
    if perf_raw is not None and perf_raw > PERF_OVER:
        flags.append("performance_over_100")      # 實際比標準快：標準週期可能設太長
    if missing_products:
        flags.append("missing_product_spec")      # 有品號沒設定標準週期，算不出表現率
    if t["unknown_s"] > 0:
        flags.append("no_state")                  # 有一段是舊版 PLC 程式（沒有機台狀態）

    return {
        "oee": oee,
        "availability": availability,
        "performance": performance,
        "quality": quality,
        "coverage": coverage,
        "good": good,
        "ng": ng,
        "total": total,
        "ideal_s": round(ideal_s, 1),
        "planned_s": round(planned_s, 1),
        "window_s": round(window_s, 1),
        "losses": {                               # 停機損失：各狀態幾秒
            "fault_s": round(t["fault_s"], 1),
            "changeover_s": round(t["changeover_s"], 1),
            "idle_s": round(t["idle_s"], 1),
        },
        **{k: round(v, 1) for k, v in t.items()},
        "missing_products": sorted(p for p in missing_products if p is not None),
        "flags": flags,
    }


def hourly_breakdown(rows: list[HourRow], cycles: dict[int, float], start: datetime, end: datetime,
                     now: datetime) -> list[dict]:
    """把一班切成每小時，各自算 OEE（每小時長條圖用）；還沒到的小時不列"""
    out = []
    t = start
    while t < end and t < now:
        nxt = t + timedelta(hours=1)
        hour_rows = [r for r in rows if t <= r.bucket < nxt]
        window = (min(nxt, now) - t).total_seconds()
        out.append({"hour": t.astimezone(SHIFT_TZ).isoformat(), **summarize(hour_rows, cycles, window)})
        t = nxt
    return out


def shift_summary(shift: Shift, day: date, rows: list[HourRow], cycles: dict[int, float],
                  now: datetime | None = None, hourly: bool = False) -> dict:
    """一個班別的 OEE；進行中的班只算到現在"""
    now = now or datetime.now(timezone.utc)
    start, end = shift_window(shift, day)
    in_shift = [r for r in rows if start <= r.bucket < end]
    window_s = max(0.0, (min(end, now) - start).total_seconds())
    result = {
        "shift": shift.name,
        "day": day.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "in_progress": start <= now < end,
        **summarize(in_shift, cycles, window_s),
    }
    if hourly:
        result["hourly"] = hourly_breakdown(in_shift, cycles, start, end, now)
    return result
