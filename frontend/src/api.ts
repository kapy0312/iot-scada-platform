import type { Limits } from "./deviceProfile";
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

/** 需要重新登入時發出的事件：App 收到後跳出登入視窗 */
export const AUTH_REQUIRED_EVENT = "scada:auth-required";

/**
 * 所有 API 呼叫都走這裡：
 * - credentials: "include"：讓瀏覽器帶上登入 cookie（前端 3000 port、後端 8000 port 算跨來源）
 * - 寫入請求加上 X-SCADA-Request header，後端用它擋跨站偽造請求
 * - 收到 401 時通知 App 跳出登入視窗
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET") headers.set("X-SCADA-Request", "1");
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  }
  return res;
}

/** 不成功時丟出後端給的錯誤訊息 */
async function ensureOk(res: Response): Promise<Response> {
  if (!res.ok) throw new Error(await readMessage(res));
  return res;
}

// ---------- 登入 ----------

export interface AuthUser {
  username: string;
  role: string;
  expires_at?: string;
}

export async function getMe(): Promise<AuthUser | null> {
  const res = await ensureOk(await apiFetch("/api/auth/me"));
  return (await res.json()).user ?? null;
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await ensureOk(
    await apiFetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }),
  );
  return (await res.json()).user as AuthUser;
}

export async function logout(): Promise<void> {
  await apiFetch("/api/auth/logout", { method: "POST" });
}

export async function getDataSource(): Promise<Source> {
  const res = await ensureOk(await apiFetch("/api/system/data-source"));
  const data = await res.json();
  return data.source as Source;
}

export async function setDataSource(source: Source): Promise<Source> {
  const res = await ensureOk(await apiFetch("/api/system/data-source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  }));
  const data = await res.json();
  if (data.status !== "ok") throw new Error(data.message ?? "切換失敗");
  return data.source as Source;
}

export async function triggerMockAnomaly(durationSeconds = 40): Promise<void> {
  const res = await ensureOk(await apiFetch("/api/system/simulate-anomaly", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ duration_seconds: durationSeconds }),
  }));
  const data = await res.json();
  if (data.status !== "ok") throw new Error(data.message ?? "觸發失敗");
}

/** 內建模擬的漸進劣化（驗證趨勢預警用）；tag 為 null 代表關閉 */
export async function simulateDrift(tag: string | null, ratePerMin = 0): Promise<void> {
  const res = await ensureOk(await apiFetch("/api/system/simulate-drift", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag, rate_per_min: ratePerMin }),
  }));
  const data = await res.json();
  if (data.status !== "ok") throw new Error(data.message ?? "設定失敗");
}

export async function writeTag(tag: string, value: number): Promise<{ ok: boolean; message: string }> {
  try {
    const res = await apiFetch("/api/control/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag, value }),
    });
    if (res.ok) return { ok: true, message: "已寫入" };
    if (res.status === 401) return { ok: false, message: "請先登入" };
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
  const res = await ensureOk(await apiFetch("/api/system/model-info"));
  return res.json();
}

export async function reloadModel(): Promise<ModelInfo> {
  const res = await ensureOk(await apiFetch("/api/system/reload-model", { method: "POST" }));
  return res.json();
}

export async function getAnomalyEvents(deviceId: string, hours: number): Promise<AnomalyEvent[]> {
  const res = await apiFetch(
    `/api/anomaly-events?device_id=${encodeURIComponent(deviceId)}&hours=${hours}`
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
  pre_warnings?: TrendWarning[];      // 這次事件之前的趨勢預警（沒有 migration 007 時是空的）
  /** 事件開始時的門檻（migration 010）；舊事件是 null，改用現行門檻判斷 */
  limits?: { version?: string; tags: Record<string, { normal: [number, number]; warning: number; danger: number }> } | null;
  limits_changed?: boolean;           // 事件進行中門檻被改過
}

export async function getIncidents(deviceId: string, hours: number): Promise<Incident[]> {
  const res = await apiFetch(
    `/api/incidents?device_id=${encodeURIComponent(deviceId)}&hours=${hours}`
  );
  if (!res.ok) throw new Error(String(res.status));
  const data = await res.json();
  return (data.incidents ?? []) as Incident[];
}

// ---------- 門檻設定 ----------

/** 修改一個訊號的門檻（需登入）；不合法時丟出後端的說明，例如「危險值要大於警戒值」 */
export async function updateLimits(deviceId: string, tag: string, limits: Limits): Promise<void> {
  await ensureOk(await apiFetch(
    `/api/device-profile/${encodeURIComponent(deviceId)}/limits/${encodeURIComponent(tag)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(limits) },
  ));
}

/** 恢復程式預設值（需登入） */
export async function resetLimits(deviceId: string, tag: string): Promise<void> {
  await ensureOk(await apiFetch(
    `/api/device-profile/${encodeURIComponent(deviceId)}/limits/${encodeURIComponent(tag)}`,
    { method: "DELETE" },
  ));
}

// ---------- 趨勢預警 ----------

/** 一次預警（trend_warnings 一列） */
export interface TrendWarning {
  id: number;
  device_id: string;
  tag_name: string;
  target_level: "warning" | "danger";
  threshold: number;
  started_at: string;
  ended_at: string;
  first_eta_s: number;
  min_eta_s: number;
  slope_per_min: number;
  value_at_start: number;
  value_at_end: number;
  outcome: "ongoing" | "reached" | "cleared" | "interrupted";
  reached_at: string | null;
  lead_s: number | null;              // 提前量：到了的時間 − 預警開始
  incident_id: number | null;
}

export interface TrendSummary {
  count: Record<TrendWarning["outcome"], number>;
  hit_rate: number | null;            // 真的到了 ÷（到了＋自己退了）
  avg_lead_s: number | null;
  avg_eta_error_s: number | null;     // 正數＝實際比預估晚到
}

export interface TrendWarningsResult {
  available: boolean;                 // false＝還沒執行 migration 007
  message?: string;
  summary: TrendSummary;
  warnings: TrendWarning[];
}

export async function getTrendWarnings(deviceId: string, hours: number): Promise<TrendWarningsResult> {
  const res = await ensureOk(await apiFetch(
    `/api/trend/warnings?device_id=${encodeURIComponent(deviceId)}&hours=${hours}`
  ));
  return res.json();
}

// ---------- OEE ----------

/** 一段時間（一班或一小時）的 OEE；算不出來的指標是 null（例如沒有產量就沒有良率） */
export interface OeeSummary {
  oee: number | null;
  availability: number | null;
  performance: number | null;
  quality: number | null;
  coverage: number | null;            // 資料覆蓋率：有資料秒 ÷ 這段時間的總秒數
  good: number;
  ng: number;
  total: number;
  run_s: number;
  idle_s: number;
  fault_s: number;
  changeover_s: number;
  planned_stop_s: number;
  data_s: number;
  window_s: number;
  losses: { fault_s: number; changeover_s: number; idle_s: number };
  missing_products: number[];
  flags: string[];                    // low_coverage / performance_over_100 / missing_product_spec / no_state
}

export interface OeeHour extends OeeSummary {
  hour: string;
}

export interface OeeShift extends OeeSummary {
  shift: string;
  day: string;
  start: string;
  end: string;
  in_progress: boolean;
  hourly?: OeeHour[];
}

export interface MachineState {
  state: number | null;               // 0=計畫停機 1=運轉 2=待機 3=故障 4=換模
  state_text: string | null;
  since: string | null;
  duration_s?: number;
  updated_at: string;
  product_id: number | null;
  good_total: number | null;
  ng_total: number | null;
}

export interface OeeCurrent {
  device_id: string;
  now: string;
  state: MachineState | null;
  shift: OeeShift | null;             // null：現在不在任何班別
}

export interface StateSegment {
  state: number | null;
  state_text: string | null;
  start: string;
  end: string;
  seconds: number;
}

export interface ShiftDef {
  name: string;
  start_hour: number;
  end_hour: number;
}

export interface ProductSpec {
  product_id: number;
  name: string;
  ideal_cycle_s: number;
  updated_at?: string;
  updated_by?: string | null;
}

export interface OeeConfig {
  timezone: string;
  products: ProductSpec[];
  shifts: { effective_from: string; shifts: ShiftDef[] } | null;
  upcoming_shifts: Array<{ effective_from: string; shifts: ShiftDef[] }>;
}

const dev = (deviceId: string) => `device_id=${encodeURIComponent(deviceId)}`;

export async function getOeeCurrent(deviceId: string): Promise<OeeCurrent> {
  return (await ensureOk(await apiFetch(`/api/oee/current?${dev(deviceId)}`))).json();
}

export async function getOeeShifts(deviceId: string, days = 7): Promise<OeeShift[]> {
  const res = await ensureOk(await apiFetch(`/api/oee/shifts?${dev(deviceId)}&days=${days}`));
  return (await res.json()).shifts as OeeShift[];
}

export async function getOeeTimeline(deviceId: string): Promise<{ start: string; end: string; segments: StateSegment[] }> {
  return (await ensureOk(await apiFetch(`/api/oee/timeline?${dev(deviceId)}`))).json();
}

export async function getOeeConfig(): Promise<OeeConfig> {
  return (await ensureOk(await apiFetch("/api/oee/config"))).json();
}

export async function saveProducts(products: ProductSpec[]): Promise<void> {
  await ensureOk(await apiFetch("/api/oee/products", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(products.map(({ product_id, name, ideal_cycle_s }) => ({ product_id, name, ideal_cycle_s }))),
  }));
}

export async function saveShifts(effectiveFrom: string | null, shifts: ShiftDef[]): Promise<void> {
  await ensureOk(await apiFetch("/api/oee/shifts", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ effective_from: effectiveFrom || null, shifts }),
  }));
}
