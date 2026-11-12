"""
趨勢預警：用最近 10 分鐘的 10 秒平均，推估「再過多久會超過警戒值（或危險值）」

類比：像看油箱指針往下掉的速度，估「還能跑幾公里」——
1. 每秒讀值先在記憶體湊成 10 秒一筆平均（跟 plc_measurements_10s 同樣的切法），雜訊小很多
   不直接查 plc_measurements_10s：那張彙整最多晚 50 秒才整理好，預警等不了
2. 斜率用 Theil–Sen：每兩點算一次斜率取中位數，單一秒突波不會把趨勢拉歪
3. 預估時間＝（下一條線 − 目前值）÷ 斜率；目前值用最近 30 秒平均推到現在

不誤報的四道關卡：
- 資料至少 5 分鐘才推估
- 斜率要明顯：斜率 × 視窗長度 ≥ 3 倍雜訊（雜訊用殘差的 MAD 估計）
- 斜率要兩段都往上：整段 10 分鐘看「是不是真的在升」，最近 3 分鐘決定「現在升多快」
  （劣化常常是平了一陣子才開始爬，整段斜率會被前面平的那段拉低，預估時間會多估一倍）
- 上升要夠大、夠穩（防「慢慢飄」：環境溫度變化這類幾分鐘尺度的起伏，10 秒雜訊看不出來）：
  視窗內的漲幅至少要有正常範圍寬度的一半；把資料切成每分鐘平均，大多數要比前面的高（Kendall τ ≥ 0.6）
- 趨緩檢查：最近 3 分鐘的斜率比前一段小很多時，當成一階延遲（像熱車：越接近終點爬越慢），
  推估最後會停在哪；停的位置到不了警戒值就不預警，到得了就用指數曲線算時間
- 延遲生效：連續 3 次（30 秒）成立才亮，連續 6 次（60 秒）不成立才熄
- 到了就不重複：某條線「到了」之後，要等數值回到正常範圍，才會再預警同一條線
  （數值在警戒值附近上下晃時，不會一直冒出「預估 0 分鐘後到警戒」；那段時間由規格告警負責）

停機、換模、恢復期（RunGate 不是 active）時不收資料、視窗清空，進行中的預警記為 interrupted。

純計算，不碰資料庫與 PLC；預警的開始／結束以 events 回傳，由呼叫端決定要不要寫資料庫。
"""
import math
import os
from collections import deque
from dataclasses import dataclass, field

import numpy as np

BUCKET_SECONDS = 10
WINDOW_MINUTES = float(os.getenv("TREND_WINDOW_MINUTES", "10"))
MIN_MINUTES = float(os.getenv("TREND_MIN_MINUTES", "5"))
HORIZON_MINUTES = float(os.getenv("TREND_HORIZON_MINUTES", "60"))
RECENT_MINUTES = 3.0
ON_COUNT = int(os.getenv("TREND_ON_COUNT", "3"))
OFF_COUNT = int(os.getenv("TREND_OFF_COUNT", "6"))
DECEL_RATIO = 0.8             # 後段斜率 < 前段 × 0.8 才算在趨緩（雜訊造成的小差異不算）
SIGNIFICANCE_K = 3.0
MIN_RISE_OF_BAND = 0.5        # 視窗內漲幅至少是正常範圍寬度的幾倍
MIN_KENDALL = 0.6             # 每分鐘平均的「往上走」一致程度（1＝每一分鐘都比前面高，0＝沒方向）
BLOCK_POINTS = 6              # 6 筆 10 秒平均＝1 分鐘          # 斜率 × 視窗長度至少是雜訊的幾倍
MIN_SAMPLES_PER_BUCKET = 5    # 10 秒內至少要有 5 筆讀值，這 10 秒才算數
LEVELS = ("warning", "danger")

# 每個訊號的狀態
COLLECTING = "collecting"     # 資料還不夠 5 分鐘
STABLE = "stable"             # 沒有明顯上升，或預估超過 60 分鐘
RISING = "rising"             # 條件成立了，還在等連續 3 次
FORECAST = "forecast"         # 預警中
ABOVE = "above"               # 已超過危險值，沒有下一條線可以預估


def theil_sen(t: np.ndarray, v: np.ndarray) -> float:
    """每兩點的斜率取中位數（單位：每秒）"""
    i, j = np.triu_indices(len(t), k=1)
    dt = t[j] - t[i]
    ok = dt > 0
    if not ok.any():
        return 0.0
    return float(np.median((v[j] - v[i])[ok] / dt[ok]))


def kendall_tau(v: np.ndarray) -> float:
    """每兩個點比一次大小：後面比較大記 +1、比較小記 −1，平均起來（1＝一路往上）"""
    n = len(v)
    if n < 2:
        return 0.0
    i, j = np.triu_indices(n, k=1)
    return float(np.sign(v[j] - v[i]).sum() / len(i))


def minute_means(v: np.ndarray, size: int = BLOCK_POINTS) -> np.ndarray:
    """從最新的往回切成每分鐘一組取平均（最舊那組不滿 1 分鐘就丟掉）"""
    n = len(v) // size
    return v[len(v) - n * size:].reshape(n, size).mean(axis=1) if n else v[:0]


def plateau(t: np.ndarray, v: np.ndarray, seg: int) -> tuple[float, float] | None:
    """
    一階延遲的終點估計：前後兩段斜率 s1、s2，間隔 Δ 秒
    斜率每 Δ 秒縮成 s2/s1 倍 → 時間常數 τ＝Δ ÷ ln(s1/s2)，離終點還差 s2 × τ
    回傳 (還會再升多少, τ)；沒有在趨緩時回傳 None
    """
    seg = min(seg, len(t) // 2)
    if seg < 3:
        return None
    s1 = theil_sen(t[-2 * seg:-seg], v[-2 * seg:-seg])
    s2 = theil_sen(t[-seg:], v[-seg:])
    if s1 <= 0 or s2 <= 0 or s2 >= s1 * DECEL_RATIO:
        return None
    delta = float(np.median(t[-seg:]) - np.median(t[-2 * seg:-seg]))
    tau = delta / math.log(s1 / s2)
    return s2 * tau, tau


def analyze(points: list[tuple[float, float]], now: float, spec: dict,
            min_points: int, recent_points: int, horizon_s: float, k: float = SIGNIFICANCE_K) -> dict:
    """
    points：[(10 秒區間中點, 平均值)]，由舊到新
    回傳這一次的判斷：candidate（是否符合預警條件）、target、eta_s 等
    """
    n = len(points)
    if n < min_points:
        return {"state": COLLECTING, "candidate": False, "points": n, "need": min_points}

    t = np.array([p[0] for p in points], dtype=float)
    v = np.array([p[1] for p in points], dtype=float)
    slope_full = theil_sen(t, v)
    slope_recent = theil_sen(t[-recent_points:], v[-recent_points:])
    intercept = float(np.median(v - slope_full * t))
    resid = v - (intercept + slope_full * t)
    noise = 1.4826 * float(np.median(np.abs(resid - np.median(resid))))
    span = float(t[-1] - t[0])
    band = float(spec["normal"][1]) - float(spec["normal"][0])
    significant = (slope_full > 0 and slope_recent > 0 and slope_full * span >= k * noise
                   and slope_full * span >= MIN_RISE_OF_BAND * band
                   and kendall_tau(minute_means(v)) >= MIN_KENDALL)
    slope = slope_recent      # 有在趨緩時，下面的 plateau 會改用指數曲線算

    last = v[-3:]
    current = float(last.mean()) + max(slope, 0.0) * (now - float(t[-3:].mean()))

    target = next((lv for lv in LEVELS if current < spec[lv]), None)
    base = {"slope_per_min": round(slope * 60, 4), "current": round(current, 4), "noise": round(noise, 4)}
    if target is None:
        return {**base, "state": ABOVE, "candidate": False}

    pl = plateau(t, v, recent_points) if significant else None
    if pl:
        base["plateau"] = round(current + pl[0], 4)

    def eta_to(line: float) -> float | None:
        need = line - current
        if slope <= 0:
            return None
        if pl is None:
            return need / slope
        rise, tau = pl
        if need >= rise:
            return None            # 停下來的位置到不了這條線
        return -tau * math.log(1 - need / rise)

    threshold = float(spec[target])
    eta = eta_to(threshold)
    eta_danger = eta_to(float(spec["danger"])) if target == "warning" else None
    candidate = bool(significant and eta is not None and eta <= horizon_s)
    return {
        **base,
        "state": RISING if candidate else STABLE,
        "candidate": candidate,
        "target": target,
        "threshold": threshold,
        "eta_s": round(eta) if eta is not None else None,
        "eta_danger_s": round(eta_danger) if eta_danger is not None else None,
    }


@dataclass
class _TagState:
    points: deque
    on_streak: int = 0
    off_streak: int = 0
    episode: dict | None = None     # 進行中的預警
    reached: set = field(default_factory=set)   # 已經到過的線（回到正常範圍才清掉）
    last: dict = field(default_factory=dict)


class TrendTracker:
    """一次只追一台設備（後端同一時間只有一個資料源）；切換資料源時呼叫 reset()"""

    def __init__(self, window_minutes: float = WINDOW_MINUTES, min_minutes: float = MIN_MINUTES,
                 horizon_minutes: float = HORIZON_MINUTES, on_count: int = ON_COUNT, off_count: int = OFF_COUNT,
                 bucket_seconds: int = BUCKET_SECONDS):
        self.bucket_s = bucket_seconds
        self.window_points = max(2, round(window_minutes * 60 / bucket_seconds))
        self.min_points = max(2, round(min_minutes * 60 / bucket_seconds))
        self.recent_points = max(2, round(RECENT_MINUTES * 60 / bucket_seconds))
        self.horizon_s = horizon_minutes * 60
        self.on_count = max(1, on_count)
        self.off_count = max(1, off_count)
        self.device_id: str | None = None
        self.tags: dict[str, _TagState] = {}
        self._bucket: float | None = None
        self._acc: dict[str, list[float]] = {}
        self.updated_at: float | None = None

    # ---------- 清空 ----------

    def reset(self, now: float | None = None) -> list[dict]:
        """切換資料源、停機、恢復期：清空視窗；進行中的預警記為 interrupted"""
        events = []
        for tag, st in self.tags.items():
            if st.episode:
                events.append(self._end(tag, st, "interrupted", now))
        self.tags.clear()
        self._bucket = None
        self._acc = {}
        return events

    # ---------- 每秒 ----------

    def update(self, device_id: str, values: dict, specs: dict, now: float, active: bool) -> list[dict]:
        """
        每秒呼叫一次；active＝RunGate 是 active
        回傳預警事件：{"type": "start"|"update"|"end", "episode": {...}}
        update 只在 10 秒區間結算、且有進行中的預警時才會有
        """
        if device_id != self.device_id:
            events = self.reset(now)
            self.device_id = device_id
        else:
            events = []
        if not active:
            if self.tags or self._acc:
                events += self.reset(now)
            return events

        bucket = (now // self.bucket_s) * self.bucket_s
        if self._bucket is not None and bucket != self._bucket:
            if bucket - self._bucket > self.bucket_s * 3:
                events += self.reset(now)     # 中間斷了 30 秒以上（RunGate 通常會先擋下）
            else:
                events += self._close_bucket(specs, now)
        self._bucket = bucket
        for tag, spec in specs.items():
            v = values.get(tag)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self._acc.setdefault(tag, []).append(float(v))
        return events

    def _close_bucket(self, specs: dict, now: float) -> list[dict]:
        events = []
        mid = self._bucket + self.bucket_s / 2
        for tag, spec in specs.items():
            samples = self._acc.get(tag, [])
            st = self.tags.get(tag)
            if st is None:
                st = self.tags[tag] = _TagState(points=deque(maxlen=self.window_points))
            if len(samples) >= MIN_SAMPLES_PER_BUCKET:
                st.points.append((mid, sum(samples) / len(samples)))
            events += self._evaluate(tag, st, spec, now)
        self._acc = {}
        self.updated_at = now
        return events

    def _evaluate(self, tag: str, st: _TagState, spec: dict, now: float) -> list[dict]:
        r = analyze(list(st.points), now, spec, self.min_points, self.recent_points, self.horizon_s)
        events = []
        ep = st.episode
        if r.get("current") is not None and r["current"] <= float(spec["normal"][1]):
            st.reached.clear()
        if r["candidate"] and r.get("target") in st.reached:
            r["candidate"] = False
            r["state"] = STABLE

        # 預警中，這條線的數值被改了（畫面上改門檻）：結束為 interrupted，用新的線重新判斷
        if ep and r.get("target") == ep["target_level"] and r.get("threshold") != ep["threshold"]:
            events.append(self._end(tag, st, "interrupted", now))
            ep = None
            st.on_streak = 0
            st.reached.clear()

        # 預警中，這條線已經到了：結束為 reached（現有規格告警會接手）
        if ep and (r["state"] == ABOVE or (r.get("target") and r["target"] != ep["target_level"])):
            events.append(self._end(tag, st, "reached", now))
            ep = None
            st.on_streak = 0        # 下一條線（危險值）要重新連續成立 3 次才預警

        if r["candidate"]:
            st.on_streak += 1
            st.off_streak = 0
        else:
            st.off_streak += 1
            st.on_streak = 0

        if ep is None and r["candidate"] and st.on_streak >= self.on_count:
            ep = st.episode = {
                "device_id": self.device_id, "tag_name": tag, "target_level": r["target"],
                "threshold": r["threshold"], "started_at": now, "ended_at": now,
                "first_eta_s": r["eta_s"], "min_eta_s": r["eta_s"], "slope_per_min": r["slope_per_min"],
                "value_at_start": r["current"], "value_at_end": r["current"],
                "outcome": "ongoing", "reached_at": None,
            }
            events.append({"type": "start", "episode": dict(ep)})
        elif ep is not None:
            if st.off_streak >= self.off_count:
                events.append(self._end(tag, st, "cleared", now, r))
                ep = None
            else:
                ep["ended_at"] = now
                ep["value_at_end"] = r.get("current", ep["value_at_end"])
                if r.get("eta_s") is not None:
                    ep["min_eta_s"] = min(ep["min_eta_s"], r["eta_s"])
                    ep["slope_per_min"] = r["slope_per_min"]
                events.append({"type": "update", "episode": dict(ep)})

        if st.episode is not None:
            r["state"] = FORECAST
        st.last = r
        return events

    def _end(self, tag: str, st: _TagState, outcome: str, now: float | None, r: dict | None = None) -> dict:
        ep = st.episode
        st.episode = None
        if now is not None:
            ep["ended_at"] = now
        if r and r.get("current") is not None:
            ep["value_at_end"] = r["current"]
        ep["outcome"] = outcome
        if outcome == "reached":
            ep["reached_at"] = ep["ended_at"]
            st.reached.add(ep["target_level"])
        return {"type": "end", "episode": dict(ep)}

    # ---------- 給畫面 ----------

    def snapshot(self, gate_status: str) -> dict:
        """WebSocket 每秒推送的 trend 欄位"""
        tags = {}
        for tag, st in self.tags.items():
            r = st.last
            if not r:
                continue
            item = {"state": r["state"]}
            if r["state"] == COLLECTING:
                item.update(points=r["points"], need=r["need"])
            else:
                for key in ("target", "threshold", "eta_s", "eta_danger_s", "slope_per_min", "current"):
                    if r.get(key) is not None:
                        item[key] = r[key]
                if st.episode:
                    item["since"] = st.episode["started_at"]
            tags[tag] = item
        return {
            "status": gate_status,
            "window_s": self.window_points * self.bucket_s,
            "min_s": self.min_points * self.bucket_s,
            "horizon_s": self.horizon_s,
            "updated_at": self.updated_at,
            "tags": tags,
        }
