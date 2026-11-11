// OEE 畫面共用：狀態顏色、數字格式、提醒文字
// 顏色依 ISA-101：正常運轉用深灰、停止用淺灰，只有故障用告警色，避免整個畫面五顏六色
import type { OeeSummary } from "../api";

export const STATE_STYLE: Record<number, { label: string; className: string }> = {
  1: { label: "運轉", className: "st-run" },
  2: { label: "待機", className: "st-idle" },
  3: { label: "故障", className: "st-fault" },
  4: { label: "換模", className: "st-change" },
  0: { label: "計畫停機", className: "st-planned" },
};

export const NO_DATA = { label: "無資料", className: "st-nodata" };

export function stateStyle(state: number | null | undefined) {
  return state == null ? NO_DATA : STATE_STYLE[state] ?? NO_DATA;
}

/** 0.8234 → "82.3%"；算不出來顯示 — */
export function pctText(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** 秒數 → 「1 時 23 分」「12 分」「45 秒」 */
export function durationText(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h} 時 ${m} 分`;
  if (m > 0) return `${m} 分`;
  return `${s} 秒`;
}

export function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString("zh-TW", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

export function flagTexts(s: Pick<OeeSummary, "flags" | "missing_products">): string[] {
  return s.flags.map((f) => {
    switch (f) {
      case "low_coverage":
        return "資料覆蓋率不足 80%（斷線或關機），數字僅供參考";
      case "performance_over_100":
        return "實際比標準週期快，標準週期可能設太長";
      case "missing_product_spec":
        return `品號 ${s.missing_products.join("、")} 沒有設定標準週期，算不出表現率`;
      case "no_state":
        return "有一段時間 PLC 沒有回報機台狀態（舊版程式）";
      default:
        return f;
    }
  });
}
