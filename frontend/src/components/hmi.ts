import type { PlcData, TagTrend } from "../hooks/useWebSocket";
import { getValue, type Level, type TagKey, type TagSpec } from "../deviceProfile";

export const pct = (min: number, max: number, v: number) =>
    Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));

/** 把等級歸成四類：正常、偏離正常（未達警戒）、警戒、危險 */
export type Kind = "none" | "normal" | "dev" | "warning" | "danger";
export function levelKind(level: Level | null): Kind {
    if (level === null) return "none";
    const s = level as string;
    if (s === "danger") return "danger";
    if (s === "warning") return "warning";
    if (s === "normal") return "normal";
    return "dev";
}

/** 取出某個訊號的數值序列，略過缺值 */
export function numbersOf(points: PlcData[], tag: TagKey): number[] {
    return points
        .map((p) => getValue(p, tag))
        .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
}

/**
 * 固定刻度（操作視窗）：正常下限再往下一個正常範圍寬度，到危險值再往上一點。
 * 刻度不隨資料變動，灰色帶與門檻線永遠在同一個位置；只有資料超出視窗才往外撐開。
 */
export function fixedWindow(spec: TagSpec, values: number[] = []) {
    const [scaleMin, scaleMax] = spec.scale;
    const width = spec.normal[1] - spec.normal[0];
    let lo = Math.max(scaleMin, spec.normal[0] - width);
    let hi = Math.min(scaleMax, spec.danger + (spec.danger - spec.warning) * 0.25);
    if (values.length) {
        const dMin = Math.min(...values);
        const dMax = Math.max(...values);
        if (dMin < lo) lo = Math.max(scaleMin, dMin - width * 0.2);
        if (dMax > hi) hi = Math.min(scaleMax, dMax + width * 0.2);
    }
    return { lo, hi };
}

/**
 * 自動縮放（只在詳細趨勢手動切換時使用）：
 * 正常時只放資料與正常範圍；超出正常範圍才放警戒值，到達警戒值再放危險值
 */
export function autoRange(spec: TagSpec, values: number[]) {
    const [scaleMin, scaleMax] = spec.scale;
    const dMin = values.length ? Math.min(...values) : spec.normal[0];
    const dMax = values.length ? Math.max(...values) : spec.normal[1];
    let lo = Math.min(dMin, spec.normal[0]);
    let hi = Math.max(dMax, spec.normal[1]);
    if (dMax > spec.normal[1]) hi = Math.max(hi, spec.warning);
    if (dMax >= spec.warning) hi = Math.max(hi, spec.danger);
    const pad = (hi - lo) * 0.1 || 1;
    lo = Math.max(scaleMin, lo - pad);
    hi = Math.min(scaleMax, hi + pad);
    return { lo, hi };
}
/** 預估時間的口語說法：「約 8 分鐘」「1 分鐘內」「約 1 小時 5 分」 */
export function etaText(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
    if (seconds < 60) return "1 分鐘內";
    const m = Math.round(seconds / 60);
    if (m < 60) return `約 ${m} 分鐘`;
    return `約 ${Math.floor(m / 60)} 小時 ${m % 60} 分`;
}

/** 持續時間：「5 分 29 秒」「45 秒」 */
export function durText(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s} 秒`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} 分 ${s % 60} 秒`;
    return `${Math.floor(m / 60)} 時 ${m % 60} 分`;
}

export const LINE_TEXT = { warning: "警戒", danger: "危險" } as const;

/** 這個訊號目前是否在預警中（預估會在 60 分鐘內到警戒或危險值） */
export function isForecast(t: TagTrend | undefined): t is TagTrend & { target: "warning" | "danger" } {
    return t?.state === "forecast" && (t.target === "warning" || t.target === "danger");
}
