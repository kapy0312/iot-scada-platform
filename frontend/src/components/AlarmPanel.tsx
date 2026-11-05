import type { PlcData } from "../hooks/useWebSocket";
import type { Alarm } from "../hooks/useAlarms";
import { LEVEL_TEXT, evaluate, getValue, tagKeys, type DeviceProfile } from "../deviceProfile";
import { extractManualRefs } from "../api";
import { DangerIcon, WarningIcon } from "./Icons";

export interface Diagnosis {
  text: string;
  at: number; // unix 秒
  status: string; // 診斷當下依手冊判定的狀態
}

interface Props {
  data: PlcData | null;
  alarms: Alarm[];
  threshold: number;
  diagnosis: Diagnosis | null;
}

const fmt = (t: number) => new Date(t * 1000).toLocaleTimeString("zh-TW", { hour12: false });

export function describeStatus(profile: DeviceProfile, data: PlcData): string {
  return tagKeys(profile)
    .map((k) => {
      const v = getValue(data, k);
      return v === null ? null : `${profile.tags[k].label}${LEVEL_TEXT[evaluate(profile.tags[k], v)]}`;
    })
    .filter(Boolean)
    .join("、");
}

export default function AlarmPanel({ data, alarms, threshold, diagnosis }: Props) {
  const anomaly = data?.anomaly;
  const top = alarms[0]?.level;
  const panelClass = top === "danger" ? "panel alert-danger" : top === "warning" ? "panel alert" : "panel";
  const refs = extractManualRefs(diagnosis?.text);
  const mlStatus =
    !anomaly ? "等待資料"
    : anomaly.status === "collecting_data" ? "收集資料中（需要 30 筆才開始判斷）"
    : anomaly.status === "warming_up" ? `暖機中（剩 ${anomaly.remaining ?? "?"} 筆）`
    : anomaly.status === "model_mismatch" ? `模型需要的訊號沒讀到：${(anomaly.missing ?? []).join("、")}`
    : anomaly.status === "suspect" ? "疑似異常，觀察中（需連續 5 秒才判定）"
    : anomaly.is_anomaly ? "判定異常" : "正常";

  return (
    <section className={panelClass} aria-labelledby="alarm-h">
      <div className="panel-head">
        <h2 id="alarm-h" className="panel-title">告警與 AI 診斷</h2>
      </div>

      {alarms.length > 0 ? (
        <ul className="alarm-list">
          {alarms.map((a) => (
            <li key={a.key} className={`alarm-row ${a.level}`}>
              <span style={{ color: a.level === "danger" ? "var(--danger)" : "var(--warning)" }}>
                {a.level === "danger" ? <DangerIcon /> : <WarningIcon />}
              </span>
              <strong>{a.level === "danger" ? "危險" : "警戒"}</strong>
              <span className="mono muted">{fmt(a.since)}</span>
              <span>{a.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted" style={{ margin: "0 0 14px" }}>無作用中的告警，所有訊號都在警戒值以內。</p>
      )}

      <div className="kv">
        <span className="muted">ML 異常偵測</span>
        <span>{mlStatus}</span>
      </div>
      <div className="kv">
        <span className="muted">異常分數／門檻</span>
        <span className="mono">
          {typeof anomaly?.score === "number" ? anomaly.score.toFixed(4) : "—"} / {anomaly?.threshold ?? threshold}
        </span>
      </div>

      {anomaly?.note && <p className="small muted" style={{ margin: "6px 0 0" }}>{anomaly.note}</p>}

      <div style={{ borderTop: "1px solid var(--line)", marginTop: 12, paddingTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        {diagnosis ? (
          <>
            <span className="small muted">
              {anomaly?.is_anomaly ? "AI 診斷" : "最近一次 AI 診斷"} · qwen3:14b · {fmt(diagnosis.at)}
            </span>
            <p style={{ margin: 0, whiteSpace: "pre-line" }}>{diagnosis.text}</p>
            <p className="small muted" style={{ margin: 0 }}>診斷當下依手冊判定：{diagnosis.status}</p>
            {refs.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
                {refs.map((r) => (
                  <span key={r} className="ref-tag">{r}</span>
                ))}
              </div>
            )}
          </>
        ) : (
          <span className="small muted">尚無 AI 診斷。異常發生時會自動查手冊並產生兩行診斷。</span>
        )}
      </div>
    </section>
  );
}
