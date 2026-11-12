import type { PlcData, TrendInfo } from "../hooks/useWebSocket";
import type { Alarm } from "../hooks/useAlarms";
import { LEVEL_TEXT, evaluate, getValue, tagKeys, type DeviceProfile } from "../deviceProfile";
import { sourceText, sourceTitle, type ManualSource } from "../api";
import { DangerIcon, WarningIcon } from "./Icons";
import { LINE_TEXT, etaText, isForecast } from "./hmi";

export interface Diagnosis {
  text: string;
  at: number; // unix 秒
  status: string; // 診斷當下哪些訊號不正常（空字串＝都正常）
  source: ManualSource | null; // 診斷依據的手冊章節（後端選定）
}

interface Props {
  data: PlcData | null;
  alarms: Alarm[];
  threshold: number;
  diagnosis: Diagnosis | null;
  profile: DeviceProfile;
}

const fmt = (t: number) => new Date(t * 1000).toLocaleTimeString("zh-TW", { hour12: false });

/** 診斷當下「不正常」的訊號，例如「振動超過警戒值、溫度偏高」；全部正常回傳空字串 */
export function describeStatus(profile: DeviceProfile, data: PlcData): string {
  return tagKeys(profile)
    .map((k) => {
      const v = getValue(data, k);
      if (v === null) return null;
      const lv = evaluate(profile.tags[k], v);
      return lv === "normal" ? null : `${profile.tags[k].label}${LEVEL_TEXT[lv]}`;
    })
    .filter(Boolean)
    .join("、");
}

/** 預警暫停或還在收資料時的說明；預警正常運作時回傳 null */
function trendNote(trend: TrendInfo | undefined): string | null {
  if (!trend) return "後端尚未提供趨勢預警";
  if (trend.status === "paused_stopped") return "停機中，預警暫停";
  if (trend.status === "paused_recovering") return "剛恢復運轉，等數值回穩後重新收集";
  if (trend.status !== "active") return "等待資料";
  const collecting = Object.values(trend.tags).filter((t) => t.state === "collecting");
  if (collecting.length > 0 && collecting.length === Object.keys(trend.tags).length) {
    const done = Math.min(...collecting.map((t) => t.points ?? 0));
    const need = collecting[0].need ?? 30;
    return `收集資料中（${Math.floor((done * 10) / 60)}／${Math.round((need * 10) / 60)} 分鐘），收滿才開始推估`;
  }
  return null;
}

export default function AlarmPanel({ data, alarms, threshold, diagnosis, profile }: Props) {
  const anomaly = data?.anomaly;
  const trend = data?.trend;
  const top = alarms[0]?.level;
  const panelClass = top === "danger" ? "panel alert-danger" : top === "warning" ? "panel alert" : "panel";

  const forecasts = tagKeys(profile)
    .map((k) => ({ key: k, spec: profile.tags[k], t: trend?.tags[k] }))
    .filter((f) => isForecast(f.t))
    .sort((a, b) => (a.t?.eta_s ?? Infinity) - (b.t?.eta_s ?? Infinity));
  const note = trendNote(trend);

  // AI 異常偵測：一句話；分數放在滑鼠提示
  const ml: { text: string; strong?: boolean } =
    !anomaly ? { text: "等待資料" }
    : anomaly.status === "paused_stopped" ? { text: "停機中，暫停判斷" }
    : anomaly.status === "paused_recovering" ? { text: `剛恢復運轉，等數值回穩（${anomaly.elapsed ?? 0} 秒）` }
    : anomaly.status === "collecting_data" ? { text: "收集資料中" }
    : anomaly.status === "warming_up" ? { text: `暖機中（剩 ${anomaly.remaining ?? "?"} 筆）` }
    : anomaly.status === "model_mismatch" ? { text: `模型缺訊號：${(anomaly.missing ?? []).join("、")}` }
    : anomaly.status === "suspect" ? { text: "有點不對勁，觀察中", strong: true }
    : anomaly.is_anomaly ? { text: "判定異常", strong: true }
    : { text: "正常" };
  const scoreTitle = `異常分數 ${typeof anomaly?.score === "number" ? anomaly.score.toFixed(4) : "—"}，門檻 ${anomaly?.threshold ?? threshold}（分數低於門檻＝異常）`;

  return (
    <section className={panelClass} aria-labelledby="alarm-h">
      <div className="panel-head">
        <h2 id="alarm-h" className="panel-title">告警 · 預警 · AI 診斷</h2>
      </div>

      <div className="alarm-sec">
        <h3 className="section-title">告警</h3>
        {alarms.length > 0 ? (
          <ul className="alarm-list">
            {alarms.map((a) => (
              <li key={a.key} className={`alarm-row ${a.level}`}>
                <span style={{ color: a.level === "danger" ? "var(--danger)" : "var(--warning)" }}>
                  {a.level === "danger" ? <DangerIcon /> : <WarningIcon />}
                </span>
                <strong>{a.level === "danger" ? "危險" : "警戒"}</strong>
                <span>{a.text}</span>
                <span className="when">{fmt(a.since)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">沒有告警，所有訊號都在警戒值以內。</p>
        )}
      </div>

      <div className="alarm-sec">
        <h3 className="section-title">預警（預估 60 分鐘內會到警戒）</h3>
        {forecasts.length > 0 ? (
          <ul className="alarm-list">
            {forecasts.map(({ key, spec, t }) => (
              <li key={key} className="alarm-row forecast">
                <span aria-hidden="true">↗</span>
                <strong>{spec.label}</strong>
                <span>
                  {etaText(t?.eta_s)}後到{LINE_TEXT[t!.target!]} {t?.threshold}
                  <span className="muted small">（每分鐘 +{t?.slope_per_min?.toFixed(spec.decimals)} {spec.unit}）</span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">{note ?? "沒有預警，沒有訊號在持續往警戒值上升。"}</p>
        )}
      </div>

      <div className="alarm-sec">
        <h3 className="section-title">AI 診斷</h3>
        <div className="kv" title={scoreTitle}>
          <span className="muted">異常偵測</span>
          <span style={{ fontWeight: ml.strong ? 700 : 400 }}>{ml.text}</span>
        </div>
        {anomaly?.note && <p className="small muted" style={{ margin: "6px 0 0" }}>{anomaly.note}</p>}
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
          {diagnosis ? (
            <>
              <span className="small muted">
                {anomaly?.is_anomaly ? "診斷" : "最近一次診斷"} · {fmt(diagnosis.at)}
              </span>
              <p style={{ margin: 0, whiteSpace: "pre-line" }}>{diagnosis.text}</p>
              <p className="small muted" style={{ margin: 0 }}>
                {diagnosis.status ? `當時：${diagnosis.status}` : "當時所有訊號都在正常範圍（AI 偵測到組合異常）"}
              </p>
              {diagnosis.source && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 2 }}>
                  <span className="ref-tag" title={sourceTitle(diagnosis.source)}>
                    依據：{sourceText(diagnosis.source)}
                  </span>
                </div>
              )}
            </>
          ) : (
            <span className="small muted">還沒有診斷。發生異常時會自動查手冊，寫出原因與處置。</span>
          )}
        </div>
      </div>
    </section>
  );
}
