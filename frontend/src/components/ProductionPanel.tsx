// 「生產」分頁：本班狀態時間軸、每小時 OEE、停機損失排行、近 7 天各班 OEE
import { useCallback, useEffect, useState } from "react";
import { getOeeShifts, getOeeTimeline, type OeeCurrent, type OeeShift, type StateSegment } from "../api";
import { useNow } from "../hooks/useOee";
import { NO_DATA, STATE_STYLE, durationText, flagTexts, hhmm, pctText, stateStyle } from "./oee";
import "./oee.css";

interface Props {
  deviceId: string;
  current: OeeCurrent | null;
  error: string | null;
}

const TIMELINE_MS = 60_000;
const SHIFTS_MS = 300_000;

function usePolling<T>(fn: () => Promise<T>, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setData(await fn());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "讀取失敗");
    }
  }, [fn]);
  useEffect(() => {
    const first = window.setTimeout(load, 0);
    const id = window.setInterval(load, ms);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [load, ms]);
  return { data, error };
}

// ---------- 狀態時間軸 ----------

function Timeline({ start, end, segments }: { start: string; end: string; segments: StateSegment[] }) {
  const now = useNow(10_000);
  const t0 = Date.parse(start);
  const t1 = Date.parse(end);
  const span = t1 - t0;
  const pos = (t: number) => `${Math.max(0, Math.min(100, ((t - t0) / span) * 100))}%`;
  const hours = Math.round(span / 3_600_000);
  const step = Math.max(1, Math.ceil(hours / 6));
  const ticks = Array.from({ length: Math.floor(hours / step) + 1 }, (_, i) => t0 + i * step * 3_600_000);

  return (
    <>
      <div className="timeline st-nodata" role="img" aria-label="本班機台狀態時間軸">
        {now < t1 && (
          <div className="seg future" style={{ left: pos(now), right: 0 }} title="還沒到" />
        )}
        {segments.map((s) => {
          const a = Date.parse(s.start);
          const b = Date.parse(s.end) + 1000;      // 每筆代表 1 秒，最後一筆也要佔寬度
          const st = stateStyle(s.state);
          return (
            <div
              key={s.start}
              className={`seg ${st.className}`}
              style={{ left: pos(a), width: `${Math.max(0.15, ((b - a) / span) * 100)}%` }}
              title={`${st.label} ${hhmm(s.start)}–${hhmm(new Date(b).toISOString())}（${durationText(s.seconds)}）`}
            />
          );
        })}
        {now >= t0 && now < t1 && <div className="now" style={{ left: pos(now) }} title="現在" />}
      </div>
      <div className="timeline-axis" aria-hidden="true">
        {ticks.map((t) => (
          <span key={t} style={{ left: pos(t) }}>{hhmm(new Date(t).toISOString())}</span>
        ))}
      </div>
      <div className="legend">
        {[1, 2, 3, 4, 0].map((k) => (
          <span key={k}><span className={`st-swatch ${STATE_STYLE[k].className}`} />{STATE_STYLE[k].label}</span>
        ))}
        <span><span className={`st-swatch ${NO_DATA.className}`} />{NO_DATA.label}</span>
      </div>
    </>
  );
}

// ---------- 每小時 OEE ----------

function HourBars({ shift }: { shift: OeeShift }) {
  const hours = shift.hourly ?? [];
  if (!hours.length) return <p className="small muted">這一班還沒有資料</p>;
  return (
    <>
      <div className="hour-bars" role="img" aria-label="本班每小時 OEE">
        {hours.map((h) => {
          const v = h.oee ?? 0;
          const tip = `${hhmm(h.hour)}  OEE ${pctText(h.oee)}\n可用率 ${pctText(h.availability)}・表現率 ${pctText(h.performance)}・良率 ${pctText(h.quality)}\n良品 ${h.good}・NG ${h.ng}・資料覆蓋 ${pctText(h.coverage, 0)}`;
          return (
            <div className="hour-bar" key={h.hour} title={tip}>
              <span className="val" style={{ bottom: `${v * 100}%` }}>{h.oee == null ? "—" : Math.round(v * 100)}</span>
              <div className={`fill${(h.coverage ?? 0) < 0.8 ? " low-data" : ""}`} style={{ height: `${v * 100}%` }} />
            </div>
          );
        })}
      </div>
      <div className="hour-labels" aria-hidden="true">
        {hours.map((h) => <span key={h.hour}>{hhmm(h.hour).slice(0, 2)}</span>)}
      </div>
      <p className="small muted">單位 %；斜線＝那一小時資料覆蓋率不足 80%。滑鼠移到長條上看明細。</p>
    </>
  );
}

// ---------- 停機損失 ----------

function Losses({ shift }: { shift: OeeShift }) {
  const noData = Math.max(0, shift.window_s - shift.data_s);
  const items = [
    { label: "故障", s: shift.losses.fault_s, cls: STATE_STYLE[3].className },
    { label: "換模", s: shift.losses.changeover_s, cls: STATE_STYLE[4].className },
    { label: "待機", s: shift.losses.idle_s, cls: STATE_STYLE[2].className },
  ].sort((a, b) => b.s - a.s);
  const max = Math.max(1, ...items.map((i) => i.s));
  return (
    <>
      {items.map((i) => (
        <div className="loss-row" key={i.label}>
          <span>{i.label}</span>
          <div className="oee-meter"><span className={i.cls} style={{ width: `${(i.s / max) * 100}%` }} /></div>
          <span className="mono" style={{ textAlign: "right" }}>{durationText(i.s)}</span>
        </div>
      ))}
      <p className="small muted">
        計畫停機 {durationText(shift.planned_stop_s)}（不算損失）・無資料 {durationText(noData)}（不算進 OEE 分母）
      </p>
    </>
  );
}

// ---------- 近 7 天 ----------

function ShiftTable({ shifts: all }: { shifts: OeeShift[] }) {
  // 完全沒有資料的班（筆電沒開、系統還沒上線）不列，只在下面註明幾班
  const shifts = all.filter((s) => s.data_s > 0 || s.in_progress);
  const empty = all.length - shifts.length;
  if (!shifts.length) return <p className="small muted">最近 7 天還沒有生產資料</p>;
  return (
    <div className="table-wrap">
      <table className="oee-table">
        <thead>
          <tr>
            <th>日期</th><th>班別</th><th>OEE</th><th>可用率</th><th>表現率</th><th>良率</th>
            <th>良品</th><th>NG</th><th>運轉</th><th>故障</th><th>覆蓋率</th>
          </tr>
        </thead>
        <tbody>
          {shifts.map((s) => {
            const notes = flagTexts(s);
            return (
              <tr key={`${s.day}-${s.shift}`}>
                <td className="num">{s.day.slice(5).replace("-", "/")}</td>
                <td>{s.shift}{s.in_progress && <span className="small muted">（進行中）</span>}</td>
                <td className="num"><b>{pctText(s.oee)}</b></td>
                <td className="num">{pctText(s.availability)}</td>
                <td className="num">{pctText(s.performance)}</td>
                <td className="num">{pctText(s.quality)}</td>
                <td className="num">{s.good}</td>
                <td className="num">{s.ng}</td>
                <td className="num">{durationText(s.run_s)}</td>
                <td className="num">{durationText(s.fault_s)}</td>
                <td className="num" title={notes.join("\n")}>
                  {pctText(s.coverage, 0)}{notes.length > 0 && <span className="lv-warning"> ⚠</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {empty > 0 && <p className="small muted">另有 {empty} 班完全沒有資料，未列出。</p>}
    </div>
  );
}

export default function ProductionPanel({ deviceId, current, error }: Props) {
  const fetchTimeline = useCallback(() => getOeeTimeline(deviceId), [deviceId]);
  const fetchShifts = useCallback(() => getOeeShifts(deviceId, 7), [deviceId]);
  const timeline = usePolling(fetchTimeline, TIMELINE_MS);
  const shifts = usePolling(fetchShifts, SHIFTS_MS);
  const shift = current?.shift ?? null;

  if (error) {
    return (
      <section className="panel">
        <p className="error">{error.includes("006") ? "OEE 尚未啟用：資料庫還沒執行 migration 006" : error}</p>
      </section>
    );
  }

  return (
    <>
      <section className="panel" aria-labelledby="tl-h">
        <div className="panel-head">
          <h2 id="tl-h" className="panel-title">
            狀態時間軸{shift ? ` · ${shift.day.slice(5).replace("-", "/")} ${shift.shift}` : "（最近 12 小時）"}
          </h2>
          <span className="small muted">每分鐘更新</span>
        </div>
        {timeline.error && <p className="error">{timeline.error}</p>}
        {timeline.data && <Timeline {...timeline.data} />}
      </section>

      {shift && (
        <div className="columns">
          <section className="panel col-wide" aria-labelledby="hr-h">
            <div className="panel-head">
              <h2 id="hr-h" className="panel-title">每小時 OEE</h2>
              <span className="small muted">本班 {pctText(shift.oee)}</span>
            </div>
            <HourBars shift={shift} />
          </section>
          <section className="panel col-side" aria-labelledby="loss-h">
            <div className="panel-head">
              <h2 id="loss-h" className="panel-title">停機損失（本班）</h2>
            </div>
            <Losses shift={shift} />
          </section>
        </div>
      )}

      <section className="panel" aria-labelledby="sh-h">
        <div className="panel-head">
          <h2 id="sh-h" className="panel-title">近 7 天各班 OEE</h2>
          <span className="small muted">每 5 分鐘更新</span>
        </div>
        {shifts.error && <p className="error">{shifts.error}</p>}
        {shifts.data && <ShiftTable shifts={shifts.data} />}
      </section>
    </>
  );
}
