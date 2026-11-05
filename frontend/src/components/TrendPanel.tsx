import { useMemo, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceArea, ReferenceLine,
} from "recharts";
import type { PlcData } from "../hooks/useWebSocket";
import { getValue, tagKeys, type DeviceProfile } from "../deviceProfile";

interface Props {
  history: PlcData[];
  profile: DeviceProfile;
}

const RANGES = [
  { minutes: 2, label: "2 分" },
  { minutes: 5, label: "5 分" },
  { minutes: 10, label: "10 分" },
];

const fmtTime = (t: number) =>
  new Date(t * 1000).toLocaleTimeString("zh-TW", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** 把連續判定為異常的時間點合併成區段，畫成淺色底 */
function anomalySegments(points: PlcData[]): Array<[number, number]> {
  const segs: Array<[number, number]> = [];
  let start: number | null = null;
  let prev: number | null = null;
  for (const p of points) {
    if (p.anomaly?.is_anomaly) {
      if (start === null) start = p.timestamp;
      prev = p.timestamp;
    } else if (start !== null && prev !== null) {
      segs.push([start, prev]);
      start = null;
    }
  }
  if (start !== null && prev !== null) segs.push([start, prev]);
  return segs;
}

export default function TrendPanel({ history, profile }: Props) {
  const [minutes, setMinutes] = useState(5);

  const points = useMemo(() => {
    if (history.length === 0) return [];
    const end = history[history.length - 1].timestamp;
    return history.filter((p) => p.timestamp >= end - minutes * 60);
  }, [history, minutes]);

  const segments = useMemo(() => anomalySegments(points), [points]);
  const domain: [number, number] = points.length
    ? [points[0].timestamp, points[points.length - 1].timestamp]
    : [0, 1];

  return (
    <section className="panel" aria-labelledby="trend-h">
      <div className="panel-head">
        <h2 id="trend-h" className="panel-title">趨勢（最近 {minutes} 分鐘）</h2>
        <div className="seg" role="group" aria-label="時間範圍">
          {RANGES.map((r) => (
            <button
              key={r.minutes}
              type="button"
              className="btn"
              aria-pressed={minutes === r.minutes}
              onClick={() => setMinutes(r.minutes)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {points.length < 2 ? (
        <p className="muted" style={{ margin: 0 }}>等待資料中（連線後每秒累積一筆）</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {tagKeys(profile).map((tag, i, keys) => {
            const spec = profile.tags[tag];
            const [scaleMin, scaleMax] = spec.scale;
            const isLast = i === keys.length - 1;
            return (
              <div className="trend-row" key={tag}>
                <div className="trend-label">
                  <span style={{ fontSize: 13 }}>{spec.label}（{spec.unit}）</span>
                  <span className="mono small muted">{scaleMin}–{scaleMax}</span>
                </div>
                <div className="trend-chart" role="img" aria-label={`${spec.label}最近 ${minutes} 分鐘趨勢`}>
                  <ResponsiveContainer width="100%" height={isLast ? 84 : 60}>
                    <LineChart data={points} syncId="trend" margin={{ top: 6, right: 36, left: 0, bottom: 0 }}>
                      <XAxis
                        dataKey="timestamp"
                        type="number"
                        domain={domain}
                        hide={!isLast}
                        tickFormatter={fmtTime}
                        tick={{ fontFamily: "var(--font-mono)", fontSize: 11, fill: "var(--muted)" }}
                        stroke="var(--line)"
                        minTickGap={48}
                      />
                      <YAxis domain={[scaleMin, scaleMax]} hide />
                      <ReferenceArea y1={spec.normal[0]} y2={spec.normal[1]} fill="var(--band)" fillOpacity={1} stroke="none" ifOverflow="hidden" />
                      {segments.map(([x1, x2]) => (
                        <ReferenceArea key={x1} x1={x1} x2={x2} fill="var(--alarm-shade)" fillOpacity={1} stroke="none" ifOverflow="hidden" />
                      ))}
                      <ReferenceLine y={spec.warning} stroke="var(--warn-mark)" strokeDasharray="4 4" ifOverflow="hidden" />
                      <ReferenceLine y={spec.danger} stroke="var(--danger-mark)" strokeDasharray="2 3" ifOverflow="hidden" />
                      <Tooltip
                        labelFormatter={(t) => fmtTime(Number(t))}
                        formatter={(v) => [`${Number(v).toFixed(spec.decimals)} ${spec.unit}`, spec.label]}
                        contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", fontFamily: "var(--font-mono)", fontSize: 12 }}
                        labelStyle={{ color: "var(--muted)" }}
                        itemStyle={{ color: "var(--text)" }}
                      />
                      <Line type="monotone" dataKey={(p: PlcData) => getValue(p, tag)} name={spec.label} stroke="var(--pen)" strokeWidth={1.6} dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            );
          })}
          <p className="small muted" style={{ margin: 0 }}>
            灰色帶＝正常範圍，長虛線＝警戒值，短虛線＝危險值{segments.length > 0 && "，淺色底＝ML 判定異常的時段"}
          </p>
        </div>
      )}
    </section>
  );
}
