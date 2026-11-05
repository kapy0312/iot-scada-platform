// 依設備規格判斷目前的告警，並記住每個告警從何時開始
import { useMemo, useRef } from "react";
import type { PlcData } from "./useWebSocket";
import { TAG_KEYS, TAG_META, evaluate, type DeviceProfile, type TagKey } from "../deviceProfile";

export type AlarmLevel = "danger" | "warning";

export interface Alarm {
  key: string;
  level: AlarmLevel;
  since: number; // unix 秒
  text: string;
}

export function useAlarms(data: PlcData | null, profile: DeviceProfile, threshold: number): Alarm[] {
  const sinceRef = useRef<Record<string, number>>({});

  return useMemo(() => {
    if (!data) return [];
    const now = data.timestamp;
    const active: Alarm[] = [];

    for (const key of TAG_KEYS) {
      const spec = profile.tags[key as TagKey];
      const meta = TAG_META[key];
      const value = data[key];
      const level = evaluate(spec, value);
      if (level !== "danger" && level !== "warning") continue;
      const id = `${key}:${level}`;
      const limit = level === "danger" ? spec.danger : spec.warning;
      active.push({
        key: id,
        level,
        since: 0,
        text: `${spec.label} ${meta.tagNo} ${value.toFixed(meta.decimals)} ${spec.unit}，${level === "danger" ? "危險" : "警戒"} ${limit}`,
      });
    }

    if (data.anomaly?.is_anomaly) {
      active.push({
        key: "ml:warning",
        level: "warning",
        since: 0,
        text: `ML 異常分數 ${data.anomaly.score.toFixed(4)}（低於 ${threshold} 判定異常）`,
      });
    }

    // 記錄開始時間；已解除的告警從記錄中移除
    const nextSince: Record<string, number> = {};
    for (const a of active) {
      nextSince[a.key] = sinceRef.current[a.key] ?? now;
      a.since = nextSince[a.key];
    }
    sinceRef.current = nextSince;

    // 危險優先，其次依發生時間由新到舊
    return active.sort((a, b) =>
      a.level === b.level ? b.since - a.since : a.level === "danger" ? -1 : 1
    );
  }, [data, profile, threshold]);
}
