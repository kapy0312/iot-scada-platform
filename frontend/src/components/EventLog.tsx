import { useCallback, useEffect, useRef, useState } from "react";
import { eventReadings, extractManualRefs, getAnomalyEvents, type AnomalyEvent } from "../api";
import { evaluate, tagKeys, type DeviceProfile, type Level } from "../deviceProfile";

interface Props {
  deviceId: string;
  isAnomaly: boolean;
  profile: DeviceProfile;
}

interface Incident {
  start: Date;
  end: Date;
  records: number;
  peak: Record<string, number | null>;
  level: Level;
  analysis: string | null;
}

const GAP_SECONDS = 60; // 與 MCP list_anomalies 相同：間隔超過 60 秒算不同次事件
const ORDER: Level[] = ["normal", "low", "high", "warning", "danger"];
const LEVEL_LABEL: Record<Level, string> = { normal: "ML 異常", low: "偏低", high: "偏高", warning: "警戒", danger: "危險" };

function groupIncidents(events: AnomalyEvent[], profile: DeviceProfile): Incident[] {
  const sorted = [...events].sort((a, b) => a.time.localeCompare(b.time));
  const groups: AnomalyEvent[][] = [];
  for (const e of sorted) {
    const last = groups[groups.length - 1];
    const prev = last?.[last.length - 1];
    if (prev && (new Date(e.time).getTime() - new Date(prev.time).getTime()) / 1000 <= GAP_SECONDS) last.push(e);
    else groups.push([e]);
  }

  return groups
    .map((g) => {
      const peak: Record<string, number | null> = {};
      let level: Level = "normal";
      const readings = g.map(eventReadings);
      for (const k of tagKeys(profile)) {
        const vals = readings.map((r) => r[k]).filter((v): v is number => typeof v === "number");
        peak[k] = vals.length ? Math.max(...vals) : null;
        if (peak[k] !== null) {
          const lv = evaluate(profile.tags[k], peak[k] as number);
          if (ORDER.indexOf(lv) > ORDER.indexOf(level)) level = lv;
        }
      }
      // 事件開頭幾秒可能掛著上一次的診斷，取最後一筆有診斷的
      const analyses = g.map((e) => e.ai_analysis).filter((a): a is string => Boolean(a));
      return {
        start: new Date(g[0].time),
        end: new Date(g[g.length - 1].time),
        records: g.length,
        peak,
        level,
        analysis: analyses.length ? analyses[analyses.length - 1] : null,
      };
    })
    .reverse();
}

const fmt = (d: Date) =>
  d.toLocaleString("zh-TW", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });

export default function EventLog({ deviceId, isAnomaly, profile }: Props) {
  const [hours, setHours] = useState(24);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setIncidents(groupIncidents(await getAnomalyEvents(deviceId, hours), profile));
    } catch {
      setError("無法取得事件紀錄，請確認後端服務");
    } finally {
      setLoading(false);
    }
  }, [deviceId, hours, profile]);

  useEffect(() => {
    load();
  }, [load]);

  // 異常開始與結束時各刷新一次（延遲讓後端寫完資料庫），結束時才拿得到完整峰值與診斷
  const was = useRef(isAnomaly);
  useEffect(() => {
    if (isAnomaly === was.current) return;
    was.current = isAnomaly;
    const timer = setTimeout(load, isAnomaly ? 1500 : 3000);
    return () => clearTimeout(timer);
  }, [isAnomaly, load]);

  return (
    <section className="panel" aria-labelledby="log-h">
      <div className="panel-head">
        <h2 id="log-h" className="panel-title">事件紀錄（最近 {hours} 小時 · {deviceId}）</h2>
        <div className="seg" role="group" aria-label="時間範圍">
          {[1, 6, 24, 72].map((h) => (
            <button key={h} type="button" className="btn" aria-pressed={hours === h} onClick={() => setHours(h)}>
              {h} 時
            </button>
          ))}
          <button type="button" className="btn" onClick={load} aria-label="重新整理事件紀錄">重新整理</button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && incidents.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>載入中…</p>
      ) : incidents.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>過去 {hours} 小時沒有異常事件。</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>開始時間</th>
                <th>持續</th>
                <th>嚴重度</th>
                <th>峰值（{tagKeys(profile).map((k) => profile.tags[k].label).join("／")}）</th>
                <th>AI 診斷</th>
                <th>引用</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((inc) => {
                const refs = extractManualRefs(inc.analysis);
                const sec = Math.round((inc.end.getTime() - inc.start.getTime()) / 1000) + 1;
                return (
                  <tr key={inc.start.toISOString()}>
                    <td className="mono nowrap">{fmt(inc.start)}</td>
                    <td className="nowrap">{sec} 秒</td>
                    <td className={`nowrap ${inc.level === "danger" ? "lv-danger" : inc.level === "warning" || inc.level === "high" ? "lv-warning" : ""}`}>
                      {LEVEL_LABEL[inc.level]}
                    </td>
                    <td className="mono nowrap">
                      {tagKeys(profile)
                        .map((k) => {
                          const v = inc.peak[k];
                          return typeof v === "number" ? v.toFixed(profile.tags[k].decimals) : "—";
                        })
                        .join(" / ")}
                    </td>
                    <td style={{ whiteSpace: "pre-line" }}>{inc.analysis ?? <span className="muted">（無診斷）</span>}</td>
                    <td className="nowrap">{refs.length ? refs.join("、") : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
