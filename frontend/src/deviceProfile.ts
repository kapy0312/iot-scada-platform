// 設備規格：正常範圍、警戒值、危險值
// 以後端 /api/device-profile 為準（來源 backend/core/device_profile.py），連不上時用下面的預設值
import { useEffect, useState } from "react";
import { API_BASE } from "./config";

export type TagKey = "motor_speed" | "temperature" | "pressure";
export const TAG_KEYS: TagKey[] = ["motor_speed", "temperature", "pressure"];

export interface TagSpec {
  label: string;
  unit: string;
  normal: [number, number];
  warning: number;
  danger: number;
}

export interface DeviceProfile {
  name: string;
  tags: Record<TagKey, TagSpec>;
}

// 畫面用的資訊：儀表編號、刻度範圍、小數位數（只跟顯示有關，不放後端）
export const TAG_META: Record<TagKey, { tagNo: string; scaleMin: number; scaleMax: number; decimals: number }> = {
  motor_speed: { tagNo: "ST-101", scaleMin: 1200, scaleMax: 1900, decimals: 1 },
  temperature: { tagNo: "TT-102", scaleMin: 40, scaleMax: 120, decimals: 1 },
  pressure: { tagNo: "PT-103", scaleMin: 2, scaleMax: 11, decimals: 2 },
};

export const DEFAULT_PROFILE: DeviceProfile = {
  name: "S7-1511T 馬達驅動系統",
  tags: {
    motor_speed: { label: "馬達轉速", unit: "RPM", normal: [1400, 1600], warning: 1650, danger: 1750 },
    temperature: { label: "溫度", unit: "°C", normal: [65, 75], warning: 85, danger: 100 },
    pressure: { label: "壓力", unit: "bar", normal: [4.5, 5.5], warning: 7.0, danger: 9.0 },
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

/** S7-1511T-MOCK、S7-1511T-SIM → S7-1511T（規格與手冊只跟設備型號有關） */
export function baseDeviceId(deviceId: string): string {
  return deviceId.replace(/-(MOCK|SIM)$/, "");
}

export function useDeviceProfile(deviceId: string) {
  const [profile, setProfile] = useState<DeviceProfile>(DEFAULT_PROFILE);
  const [threshold, setThreshold] = useState<number>(DEFAULT_THRESHOLD);
  const [fromServer, setFromServer] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/device-profile/${encodeURIComponent(baseDeviceId(deviceId))}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((data) => {
        if (cancelled || !data?.profile?.tags) return;
        setProfile(data.profile as DeviceProfile);
        if (typeof data.anomaly_threshold === "number") setThreshold(data.anomaly_threshold);
        setFromServer(true);
      })
      .catch(() => {
        if (!cancelled) setFromServer(false);
      });
    return () => {
      cancelled = true;
    };
  }, [deviceId]);

  return { profile, threshold, fromServer };
}
