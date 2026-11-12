// 告警要不要「閃」：剛跨過門檻時閃，讓人注意到；看過（按知道了）或過了一段時間就改成恆亮
//
// 規則（類似 PLC 告警的「未確認閃爍、已確認恆亮」）：
// - 警戒：剛進入時閃 WARNING_FLASH_S 秒，之後恆亮
// - 危險：一直閃到有人按「知道了」
// - 數值在門檻附近跳來跳去時，解除不到 REARM_S 秒又回來，算同一次，不重新閃（避免一直閃、看久就沒人理）
// - 從警戒升到危險：算新的一次，重新閃
// - 「知道了」只記在這個網頁裡，重新整理就沒了；正式的確認／擱置紀錄之後另做
import { useCallback, useState } from "react";
import type { Alarm, AlarmLevel } from "./useAlarms";

export const WARNING_FLASH_S = 10;
export const REARM_S = 30;

export interface Attention {
  level: AlarmLevel;
  flashing: boolean;
}

interface Episode {
  level: AlarmLevel;
  start: number;      // 這一次開始的時間（unix 秒）
  acked: boolean;
  clearedAt: number | null; // 解除的時間；null＝仍在告警
}

/** alarm.key 是「訊號:等級」，取前半當訊號代號 */
export const alarmTag = (a: Alarm) => a.key.split(":")[0];

/** 純函式：依這一秒的告警更新每個訊號的狀態（方便單獨測試） */
export function stepEpisodes(
  prev: Record<string, Episode>,
  active: Array<{ tag: string; level: AlarmLevel }>,
  now: number,
): Record<string, Episode> {
  const next: Record<string, Episode> = {};
  const seen = new Set<string>();
  for (const { tag, level } of active) {
    seen.add(tag);
    const old = prev[tag];
    const resumable = old && (old.clearedAt === null || now - old.clearedAt < REARM_S);
    if (!old || !resumable) {
      next[tag] = { level, start: now, acked: false, clearedAt: null };
    } else if (level === "danger" && old.level === "warning") {
      next[tag] = { level, start: now, acked: false, clearedAt: null }; // 升級：重新閃
    } else {
      // 同一次（含危險降回警戒）：等級跟著目前值，開始時間與確認狀態保留
      next[tag] = { ...old, level, clearedAt: null };
    }
  }
  // 已解除的先留著 REARM_S 秒，期間回來算同一次
  for (const [tag, old] of Object.entries(prev)) {
    if (seen.has(tag)) continue;
    const clearedAt = old.clearedAt ?? now;
    if (now - clearedAt < REARM_S) next[tag] = { ...old, clearedAt };
  }
  return next;
}

export function isFlashing(ep: Episode, now: number): boolean {
  if (ep.clearedAt !== null || ep.acked) return false;
  if (ep.level === "danger") return true;
  return now - ep.start < WARNING_FLASH_S;
}

export function useAlarmAttention(alarms: Alarm[], now: number | undefined) {
  // 每收到新的一秒資料更新一次；用「render 中依新值調整 state」的寫法，不需要 ref 或 effect
  const [st, setSt] = useState<{ now: number | undefined; episodes: Record<string, Episode> }>({
    now: undefined,
    episodes: {},
  });
  let episodes = st.episodes;
  if (now !== undefined && st.now !== now) {
    episodes = stepEpisodes(st.episodes, alarms.map((a) => ({ tag: alarmTag(a), level: a.level })), now);
    setSt({ now, episodes });
  }

  const t = now ?? 0;
  const attention: Record<string, Attention> = {};
  for (const [tag, ep] of Object.entries(episodes)) {
    if (ep.clearedAt !== null) continue;
    attention[tag] = { level: ep.level, flashing: isFlashing(ep, t) };
  }

  /** 按「知道了」：目前所有告警都改成恆亮 */
  const acknowledge = useCallback(() => {
    setSt((s) => ({
      ...s,
      episodes: Object.fromEntries(Object.entries(s.episodes).map(([k, ep]) => [k, { ...ep, acked: true }])),
    }));
  }, []);

  const flashingDanger = Object.values(attention).some((a) => a.flashing && a.level === "danger");
  const anyFlashing = Object.values(attention).some((a) => a.flashing);
  return { attention, acknowledge, flashingDanger, anyFlashing };
}
