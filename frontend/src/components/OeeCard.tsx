// 監控頁的 OEE 卡片：本班 OEE、可用率／表現率／良率、目前狀態與持續時間
import type { OeeCurrent } from "../api";
import { useNow } from "../hooks/useOee";
import { durationText, flagTexts, hhmm, pctText, stateStyle } from "./oee";
import "./oee.css";

interface Props {
  data: OeeCurrent | null;
  error: string | null;
  /** 即時資料（WebSocket）裡的機台狀態，比 API 新；沒有就用 API 的 */
  liveState?: number | null;
  onOpenDetail?: () => void;
}

function Meter({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="oee-row">
      <span className="muted">{label}</span>
      <div className="oee-meter" role="img" aria-label={`${label} ${pctText(value)}`}>
        <span style={{ width: `${Math.max(0, Math.min(1, value ?? 0)) * 100}%` }} />
      </div>
      <span className="mono" style={{ textAlign: "right" }}>{pctText(value)}</span>
    </div>
  );
}

export default function OeeCard({ data, error, liveState, onOpenDetail }: Props) {
  const now = useNow();
  const shift = data?.shift ?? null;
  const state = data?.state ?? null;
  const shownState = liveState !== undefined && liveState !== null ? liveState : state?.state;
  const style = stateStyle(shownState);
  // 即時狀態跟 API 一致時才顯示持續時間；剛切換、API 還沒更新時先不顯示，避免數字錯
  const since = state?.since && shownState === state.state ? Date.parse(state.since) : null;

  return (
    <section className="panel" aria-labelledby="oee-h">
      <div className="panel-head">
        <h2 id="oee-h" className="panel-title">
          生產 OEE{shift ? ` · ${shift.shift} ${hhmm(shift.start)}–${hhmm(shift.end)}` : ""}
        </h2>
        {onOpenDetail && (
          <button type="button" className="btn ghost" style={{ minHeight: 32 }} onClick={onOpenDetail}>
            詳細 ›
          </button>
        )}
      </div>

      {error && <p className="error">{error.includes("006") ? "OEE 尚未啟用（資料庫還沒執行 migration 006）" : error}</p>}

      <div className="kv">
        <span className="muted">機台狀態</span>
        <span className="st-chip">
          <span className={`st-swatch ${style.className}`} aria-hidden="true" />
          {style.label}
          {since !== null && <span className="muted mono" style={{ fontWeight: 400 }}>· {durationText((now - since) / 1000)}</span>}
        </span>
      </div>

      {!shift && !error && <p className="small muted">現在不在任何班別，班別可在「系統設定」調整。</p>}

      {shift && (
        <>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", margin: "10px 0 6px" }}>
            <span className="oee-big">{pctText(shift.oee)}</span>
            <span className="small muted">資料覆蓋 {pctText(shift.coverage, 0)}</span>
          </div>
          <Meter label="可用率" value={shift.availability} />
          <Meter label="表現率" value={shift.performance} />
          <Meter label="良率" value={shift.quality} />
          <div className="oee-counts">
            <span>良品 <b className="mono">{shift.good}</b></span>
            <span>NG <b className="mono">{shift.ng}</b></span>
            <span className="muted">總數 <span className="mono">{shift.total}</span></span>
          </div>
          {shift.flags.length > 0 && (
            <ul className="oee-notes">
              {flagTexts(shift).map((t) => <li key={t}>{t}</li>)}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
