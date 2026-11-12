// 設備規格：以後端 /api/device-profile 為準（來源 backend/core/device_profile.py）
// 訊號清單、正常範圍、刻度、儀表編號都從這裡來；後端新增訊號，畫面會自動多出來
// 連不上後端時用下面的預設值
import { useEffect, useState } from "react";
import { API_BASE } from "./config";

export type TagKey = string;

export interface TagSpec {
  label: string;
  unit: string;
  normal: [number, number];
  warning: number;
  danger: number;
  tag_no: string;
  scale: [number, number];
  decimals: number;
}

export interface DeviceProfile {
  name: string;
  tags: Record<TagKey, TagSpec>;
}

export const DEFAULT_PROFILE: DeviceProfile = {
  name: "S7-1511T 馬達驅動系統",
  tags: {
    motor_speed: { label: "馬達轉速", unit: "RPM", normal: [1400, 1600], warning: 1650, danger: 1750, tag_no: "ST-101", scale: [1200, 1900], decimals: 1 },
    temperature: { label: "溫度", unit: "°C", normal: [65, 75], warning: 85, danger: 100, tag_no: "TT-102", scale: [40, 120], decimals: 1 },
    pressure: { label: "壓力", unit: "bar", normal: [4.5, 5.5], warning: 7.0, danger: 9.0, tag_no: "PT-103", scale: [2, 11], decimals: 2 },
    vibration: { label: "振動", unit: "mm/s", normal: [0.5, 2.0], warning: 2.8, danger: 4.5, tag_no: "VT-104", scale: [0, 6], decimals: 2 },
    current: { label: "馬達電流", unit: "A", normal: [20, 26], warning: 28, danger: 31, tag_no: "CT-105", scale: [0, 36], decimals: 1 },
  },
};

export const DEFAULT_THRESHOLD = 0.05;

export type Level = "normal" | "low" | "high" | "warning" | "danger";

export const LEVEL_TEXT: Record<Level, string> = {
  normal: "正常",
  low: "偏低",
  high: "偏高",
  warning: "超過警戒值",
  danger: "超過危險值",
};

export function evaluate(spec: TagSpec, value: number): Level {
  if (value >= spec.danger) return "danger";
  if (value >= spec.warning) return "warning";
  if (value > spec.normal[1]) return "high";
  if (value < spec.normal[0]) return "low";
  return "normal";
}

/** 依規格的順序列出訊號 */
export function tagKeys(profile: DeviceProfile): TagKey[] {
  return Object.keys(profile.tags);
}

/** 儀表代號，例如 ST-101 → ST */
export function instrumentCode(spec: TagSpec): string {
  return spec.tag_no.split("-")[0] || spec.tag_no;
}

/** 從一筆即時資料取出某個訊號的值；新後端放在 values，舊後端放在頂層 */
export function getValue(data: { values?: Record<string, number> } | null | undefined, key: TagKey): number | null {
  if (!data) return null;
  const v = data.values?.[key] ?? (data as Record<string, unknown>)[key];
  return typeof v === "number" ? v : null;
}

/** S7-1511T-MOCK、S7-1511T-SIM → S7-1511T（規格與手冊只跟設備型號有關） */
export function baseDeviceId(deviceId: string): string {
  return deviceId.replace(/-(MOCK|SIM)$/, "");
}

/** 後端回傳的規格若缺少畫面欄位（舊版後端），用預設值補上 */
function normalize(profile: DeviceProfile): DeviceProfile {
  const tags: Record<TagKey, TagSpec> = {};
  for (const [key, spec] of Object.entries(profile.tags)) {
    const fallback = DEFAULT_PROFILE.tags[key];
    tags[key] = {
      ...spec,
      tag_no: spec.tag_no ?? fallback?.tag_no ?? key,
      scale: spec.scale ?? fallback?.scale ?? [spec.normal[0] * 0.5, spec.danger * 1.2],
      decimals: spec.decimals ?? fallback?.decimals ?? 2,
    };
  }
  return { name: profile.name, tags };
}

/** 可以在畫面上改的門檻（名稱、單位等結構不在這裡） */
export interface Limits {
  normal: [number, number];
  warning: number;
  danger: number;
  scale: [number, number];
}

/** 每個訊號的預設值與最後修改紀錄（後端 migration 008） */
export interface LimitsInfo {
  default: Limits | null;
  modified: { updated_by: string | null; updated_at: string | null } | null;
}

export function limitsOf(spec: TagSpec): Limits {
  return { normal: [spec.normal[0], spec.normal[1]], warning: spec.warning, danger: spec.danger, scale: [spec.scale[0], spec.scale[1]] };
}

/**
 * 設備規格；liveVersion 是 WebSocket 帶來的門檻版本，跟目前載入的不同時自動重新載入
 * （別的網頁改了門檻，這邊也會跟上）
 */
export function useDeviceProfile(deviceId: string, liveVersion?: string) {
  const [profile, setProfile] = useState<DeviceProfile>(DEFAULT_PROFILE);
  const [threshold, setThreshold] = useState<number>(DEFAULT_THRESHOLD);
  const [fromServer, setFromServer] = useState(false);
  const [limitsInfo, setLimitsInfo] = useState<Record<TagKey, LimitsInfo>>({});
  const [loadedVersion, setLoadedVersion] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // 版本不同就觸發重新載入（只在「已經載入過、而且版本真的變了」時）
  const stale = Boolean(liveVersion && loadedVersion && liveVersion !== loadedVersion);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/device-profile/${encodeURIComponent(baseDeviceId(deviceId))}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((data) => {
        if (cancelled || !data?.profile?.tags) return;
        setProfile(normalize(data.profile as DeviceProfile));
        if (typeof data.anomaly_threshold === "number") setThreshold(data.anomaly_threshold);
        setLimitsInfo(data.limits ?? {});
        setLoadedVersion(typeof data.limits_version === "string" ? data.limits_version : "");
        setFromServer(true);
      })
      .catch(() => {
        if (!cancelled) setFromServer(false);
      });
    return () => {
      cancelled = true;
    };
  }, [deviceId, reloadKey, stale]);

  return { profile, threshold, fromServer, limitsInfo, reload: () => setReloadKey((k) => k + 1) };
}
