import { useCallback, useEffect, useRef, useState } from "react";
import { getIncidents, sourceText, sourceTitle, type Incident } from "../api";
import { durText } from "./hmi";
import {
  evaluate,
  tagKeys,
  type DeviceProfile,
  type Level,
  type TagKey,
} from "../deviceProfile";

interface Props {
  deviceId: string;
  isAnomaly: boolean;
  profile: DeviceProfile;
}

// 事件改由後端即時維護（anomaly_incidents），前端只負責顯示，不再自己合併每秒紀錄
const ORDER: Level[] = ["normal", "low", "high", "warning", "danger"];
const LEVEL_LABEL: Record<Level, string> = {
  normal: "ML 異常",
  low: "偏低",
  high: "偏高",
  warning: "警戒",
  danger: "危險",
};

const asLevel = (s: string): Level =>
  (ORDER as string[]).includes(s) ? (s as Level) : "normal";

const levelClass = (lv: Level) =>
  lv === "danger" ? "lv-danger" : lv === "warning" || lv === "high" || lv === "low" ? "lv-warning" : "";

/**
 * 每個訊號顯示「最偏離正常」的那個值：
 * 偏高的看最大值、偏低的看最小值（例如機械卡滯時轉速掉到 1256，只看最大值會看不出來）
 */
function peakOf(inc: Incident, profile: DeviceProfile, k: TagKey): { text: string; level: Level } {
  const spec = profile.tags[k];
  const max = inc.peak?.max?.[k];
  const min = inc.peak?.min?.[k];
  if (typeof max !== "number" && typeof min !== "number") return { text: "—", level: "normal" };
  const hi = typeof max === "number" ? { v: max, lv: evaluate(spec, max) } : null;
  const lo = typeof min === "number" ? { v: min, lv: evaluate(spec, min) } : null;
  const pick =
    hi && lo ? (ORDER.indexOf(lo.lv) > ORDER.indexOf(hi.lv) ? lo : hi) : (hi ?? lo)!;
  const arrow = pick.lv === "low" ? "▼" : pick.lv === "normal" ? "" : "▲";
  return { text: `${arrow}${pick.v.toFixed(spec.decimals)}`, level: pick.lv };
}

const fmt = (iso: string) =>
  new Date(iso).toLocaleString("zh-TW", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

/** 事件裡不正常的訊號，例如「振動 ▲3.12、溫度 ▲86.0」；只有 ML 異常（每個訊號都在範圍內）時回傳空陣列 */
function abnormalOf(inc: Incident, profile: DeviceProfile) {
  return tagKeys(profile)
    .map((k) => ({ k, label: profile.tags[k].label, ...peakOf(inc, profile, k) }))
    .filter((p) => p.level !== "normal")
    .sort((a, b) => ORDER.indexOf(b.level) - ORDER.indexOf(a.level));
}

/** 事前預警：最早那一次預警比到達早多久 */
function leadOf(inc: Incident): number | null {
  const leads = (inc.pre_warnings ?? []).map((w) => w.lead_s).filter((v): v is number => typeof v === "number");
  return leads.length ? Math.max(...leads) : null;
}

function DiagnosisCell({ text }: { text: string | null }) {
  const [open, setOpen] = useState(false);
  if (!text) return <span className="muted">（無診斷）</span>;
  return (
    <span
      className={`clamp-2${open ? " open" : ""}`}
      style={{ whiteSpace: "pre-line", cursor: "pointer" }}
      title={open ? "按一下收合" : "按一下展開全文"}
      onClick={() => setOpen(!open)}
    >
      {text}
    </span>
  );
}

export default function EventLog({ deviceId, isAnomaly, profile }: Props) {
  const [hours, setHours] = useState(24);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setIncidents(await getIncidents(deviceId, hours));
    } catch {
      setError("無法取得事件紀錄，請確認後端服務");
    } finally {
      setLoading(false);
    }
  }, [deviceId, hours]);

  useEffect(() => {
    load();
  }, [load]);

  // 異常開始與結束時各刷新一次（延遲讓後端寫完資料庫）；異常進行中每 15 秒刷新，診斷完成就看得到
  const was = useRef(isAnomaly);
  useEffect(() => {
    if (isAnomaly === was.current) return;
    was.current = isAnomaly;
    const timer = setTimeout(load, isAnomaly ? 1500 : 3000);
    return () => clearTimeout(timer);
  }, [isAnomaly, load]);
  useEffect(() => {
    if (!isAnomaly) return;
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [isAnomaly, load]);

  return (
    <section className="panel" aria-labelledby="log-h">
      <div className="panel-head">
        <h2 id="log-h" className="panel-title">
          事件紀錄（最近 {hours} 小時）
        </h2>
        <div className="seg" role="group" aria-label="時間範圍">
          {[1, 6, 24, 72].map((h) => (
            <button
              key={h}
              type="button"
              className="btn"
              aria-pressed={hours === h}
              onClick={() => setHours(h)}
            >
              {h} 時
            </button>
          ))}
          <button
            type="button"
            className="btn"
            onClick={load}
            aria-label="重新整理事件紀錄"
          >
            重新整理
          </button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && incidents.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          載入中…
        </p>
      ) : incidents.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          過去 {hours} 小時沒有異常事件。
        </p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>時間</th>
                <th>持續</th>
                <th>嚴重度</th>
                <th>哪個訊號</th>
                <th title="趨勢預警比真正到警戒值早多久發現">事前預警</th>
                <th>AI 診斷（按一下展開）</th>
                <th>依據</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((inc) => {
                const level = asLevel(inc.level);
                const abnormal = abnormalOf(inc, profile);
                const lead = leadOf(inc);
                return (
                  <tr key={inc.id}>
                    <td className="mono nowrap">{fmt(inc.started_at)}</td>
                    <td className="nowrap">{durText(inc.duration_seconds)}</td>
                    <td className={`nowrap ${levelClass(level)}`}>{LEVEL_LABEL[level]}</td>
                    <td className="nowrap">
                      {abnormal.length === 0 ? (
                        <span className="muted">各訊號都在範圍內</span>
                      ) : (
                        abnormal.map((p, i) => (
                          <span key={p.k}>
                            {i > 0 && "、"}
                            {p.label} <span className={`mono ${levelClass(p.level)}`}>{p.text}</span>
                          </span>
                        ))
                      )}
                    </td>
                    <td className="nowrap">
                      {lead === null ? <span className="muted">—</span> : <span className="lv-forecast">提前 {durText(lead)}</span>}
                    </td>
                    <td style={{ minWidth: 280 }}>
                      <DiagnosisCell text={inc.ai_analysis} />
                    </td>
                    <td className="nowrap">
                      {inc.ai_source ? (
                        <span className="ref-tag" title={sourceTitle(inc.ai_source)}>
                          {sourceText(inc.ai_source)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted" style={{ margin: "8px 0 0" }}>
        ▲ 偏高、▼ 偏低，數字是事件中最偏離的值；恢復正常超過 60 秒後再發生算新的一筆
      </p>
    </section>
  );
}
