import { API_BASE } from "./config";

export type Source = "mock" | "opcua";

async function readMessage(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.message === "string") return data.message;
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map((d: { msg?: string }) => d.msg).join("；");
  } catch {
    /* 回應不是 JSON */
  }
  return `HTTP ${res.status}`;
}

export async function getDataSource(): Promise<Source> {
  const res = await fetch(`${API_BASE}/api/system/data-source`);
  const data = await res.json();
  return data.source as Source;
}

export async function setDataSource(source: Source): Promise<Source> {
  const res = await fetch(`${API_BASE}/api/system/data-source`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });
  const data = await res.json();
  if (data.status !== "ok") throw new Error(data.message ?? "切換失敗");
  return data.source as Source;
}

export async function triggerMockAnomaly(durationSeconds = 40): Promise<void> {
  const res = await fetch(`${API_BASE}/api/system/simulate-anomaly`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ duration_seconds: durationSeconds }),
  });
  const data = await res.json();
  if (data.status !== "ok") throw new Error(data.message ?? "觸發失敗");
}

export async function writeTag(tag: string, value: number): Promise<{ ok: boolean; message: string }> {
  try {
    const res = await fetch(`${API_BASE}/api/control/write`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag, value }),
    });
    if (res.ok) return { ok: true, message: "已寫入" };
    return { ok: false, message: await readMessage(res) };
  } catch {
    return { ok: false, message: "連線失敗，請確認後端服務" };
  }
}

export interface AnomalyEvent {
  time: string;
  anomaly_score: number | null;
  readings?: Record<string, number>; // 所有訊號（新後端）
  motor_speed?: number | null;      // 舊欄位
  temperature?: number | null;
  pressure?: number | null;
  ai_analysis: string | null;
}

/** 事件的讀值：優先用 readings，舊後端沒有 readings 時用三個舊欄位 */
export function eventReadings(e: AnomalyEvent): Record<string, number> {
  if (e.readings && Object.keys(e.readings).length) return e.readings;
  const out: Record<string, number> = {};
  for (const k of ["motor_speed", "temperature", "pressure"] as const) {
    const v = e[k];
    if (typeof v === "number") out[k] = v;
  }
  return out;
}

export interface ModelInfo {
  loaded: boolean;
  file?: string;
  trained_on?: string;
  created_at?: string;
  tags?: string[];
  feature_count?: number;
  samples?: number | null;
  threshold?: number;
}

export async function getModelInfo(): Promise<ModelInfo> {
  const res = await fetch(`${API_BASE}/api/system/model-info`);
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

export async function reloadModel(): Promise<ModelInfo> {
  const res = await fetch(`${API_BASE}/api/system/reload-model`, { method: "POST" });
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

export async function getAnomalyEvents(deviceId: string, hours: number): Promise<AnomalyEvent[]> {
  const res = await fetch(
    `${API_BASE}/api/anomaly-events?device_id=${encodeURIComponent(deviceId)}&hours=${hours}`
  );
  const data = await res.json();
  return (data.events ?? []) as AnomalyEvent[];
}

/** AI 診斷依據的手冊章節（後端選定，不再從診斷文字裡用 regex 猜） */
export interface ManualSource {
  label: string;                      // 例：測試版3 §4.2
  file?: string | null;
  doc_title?: string | null;
  section?: string;                   // 完整章節路徑
  section_no?: string;
  page?: number | null;
  match_type?: "full" | "partial" | "reference";
  match?: number;                     // 章節條件符合項數
  contra?: number;                    // 章節條件矛盾項數
}

const MATCH_TEXT: Record<string, string> = { partial: "部分符合", reference: "參考" };

/** 「測試版3 §4.2 · p.3」，部分符合或參考章節會加註 */
export function sourceText(s: ManualSource): string {
  const page = typeof s.page === "number" ? ` · p.${s.page}` : "";
  const note = s.match_type && MATCH_TEXT[s.match_type] ? `（${MATCH_TEXT[s.match_type]}）` : "";
  return `${s.label}${page}${note}`;
}

/** 滑鼠移上去顯示的完整來源 */
export function sourceTitle(s: ManualSource): string {
  return [s.file, s.section].filter(Boolean).join("\n");
}

/** 事件（一次故障一筆），由後端即時維護 */
export interface Incident {
  id: number;
  device_id: string;
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  records: number;
  level: string;                      // normal（只有 ML 異常）/ low / high / warning / danger
  severity: number;
  peak: { max?: Record<string, number>; min?: Record<string, number> } | null;
  min_score: number | null;
  ai_analysis: string | null;
  ai_source: ManualSource | null;
}

export async function getIncidents(deviceId: string, hours: number): Promise<Incident[]> {
  const res = await fetch(
    `${API_BASE}/api/incidents?device_id=${encodeURIComponent(deviceId)}&hours=${hours}`
  );
  if (!res.ok) throw new Error(String(res.status));
  const data = await res.json();
  return (data.incidents ?? []) as Incident[];
}
