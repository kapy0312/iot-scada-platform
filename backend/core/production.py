"""
生產狀態與計數的共用規則（不碰資料庫、不碰 PLC，方便單元測試）

1. 機台狀態代碼：跟 PLC 的 FB_Production 一致
2. CounterTracker：PLC 的累計計數（像總里程）→ 每秒增量（像單趟里程）
   - 計數變小：PLC 歸零或重新下載，這次的值就是歸零後做的件數
   - 跟上一筆隔太久（後端停掉、斷線）：那段的件數不算進 OEE（增量記 0），累計原值照樣保存
3. RunGate：停機時暫停異常偵測，恢復運轉後等數值回穩再開始判斷（像引擎熱車）
   - 停機中 → paused_stopped
   - 剛恢復運轉 → 至少等 RECOVERY_MIN_SECONDS；之後所有訊號回到正常範圍就恢復判斷，
     最多等 RECOVERY_MAX_SECONDS（啟動時就有真故障時不會一直被藏起來）
"""
import os
from dataclasses import dataclass

# ---------- 機台狀態 ----------
PLANNED_STOP = 0   # 計畫停機（休息、保養）：不算進 OEE 分母
RUN = 1            # 運轉
IDLE = 2           # 待機（馬達沒啟動，也沒有其他狀態）
FAULT = 3          # 故障停機
CHANGEOVER = 4     # 換模

STATE_TEXT = {PLANNED_STOP: "計畫停機", RUN: "運轉", IDLE: "待機", FAULT: "故障", CHANGEOVER: "換模"}

# ---------- 計數 ----------
MAX_DT = 5.0           # 一筆最多代表幾秒；超過的部分算「無資料」
GAP_SECONDS = 10.0     # 跟上一筆隔超過這麼久，這段的件數不算進 OEE

# ---------- 停機暫停 ----------
RECOVERY_MIN_SECONDS = float(os.getenv("RECOVERY_MIN_SECONDS", "30"))
RECOVERY_MAX_SECONDS = float(os.getenv("RECOVERY_MAX_SECONDS", "120"))


def is_running(machine_state: int | None, motor_enable: int | None) -> bool:
    """有 machine_state（新版 PLC 程式）就看它；舊版 PLC 沒有這個變數時改看 motor_enable"""
    if machine_state is not None:
        return int(machine_state) == RUN
    return bool(motor_enable)


@dataclass
class CounterStep:
    good_delta: int
    ng_delta: int
    dt: float          # 這一筆代表的秒數（0～MAX_DT）
    reset: bool        # 這一筆偵測到計數歸零
    gap: bool          # 跟上一筆隔太久，增量沒有算


def _delta(new: int | None, old: int | None) -> tuple[int, bool]:
    if new is None or old is None:
        return 0, False
    if new >= old:
        return new - old, False
    return new, True     # 變小＝歸零過，歸零後做了 new 件


class CounterTracker:
    """每台設備各自記住上一筆的累計值與時間"""

    def __init__(self, gap_seconds: float = GAP_SECONDS, max_dt: float = MAX_DT):
        self.gap_seconds = gap_seconds
        self.max_dt = max_dt
        self._last: dict[str, tuple[float, int | None, int | None]] = {}

    def forget(self, device_id: str | None = None) -> None:
        """切換資料源時呼叫：下一筆當成第一筆"""
        if device_id is None:
            self._last.clear()
        else:
            self._last.pop(device_id, None)

    def step(self, device_id: str, now: float, good_total: int | None, ng_total: int | None) -> CounterStep:
        last = self._last.get(device_id)
        self._last[device_id] = (now, good_total, ng_total)
        if last is None:
            # 第一筆（後端剛啟動）：不知道之前做到幾件，不算增量；這一秒也還不能算有資料
            return CounterStep(0, 0, 0.0, False, False)

        elapsed = max(0.0, now - last[0])
        dt = min(elapsed, self.max_dt)
        if elapsed > self.gap_seconds:
            return CounterStep(0, 0, dt, False, True)

        good, r1 = _delta(good_total, last[1])
        ng, r2 = _delta(ng_total, last[2])
        return CounterStep(good, ng, dt, r1 or r2, False)


class RunGate:
    """決定這一秒要不要做 ML 異常判斷"""

    def __init__(self, min_seconds: float = RECOVERY_MIN_SECONDS, max_seconds: float = RECOVERY_MAX_SECONDS):
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.resumed_at: float | None = None   # 停機後恢復運轉的時間；None＝不在恢復期
        self.stopped = False

    def reset(self) -> None:
        self.resumed_at = None
        self.stopped = False

    def update(self, running: bool, all_normal: bool, now: float) -> dict:
        """
        回傳 {"status": active / paused_stopped / paused_recovering, "just_paused": bool, "elapsed": 秒}
        just_paused：這一秒剛從「判斷中」變成暫停，呼叫端要清空 ML 滑動視窗
        """
        was_active = not self.stopped and self.resumed_at is None
        if not running:
            self.stopped = True
            self.resumed_at = None
            return {"status": "paused_stopped", "just_paused": was_active}

        if self.stopped:                 # 停機後第一秒恢復運轉
            self.stopped = False
            self.resumed_at = now

        if self.resumed_at is not None:
            elapsed = now - self.resumed_at
            if elapsed < self.min_seconds or (elapsed < self.max_seconds and not all_normal):
                return {"status": "paused_recovering", "just_paused": False, "elapsed": round(elapsed)}
            self.resumed_at = None
        return {"status": "active", "just_paused": False}
