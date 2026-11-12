// 預警紀錄（生產分頁）：每一次趨勢預警的結果，加上命中率與平均提前量
import { useCallback, useEffect, useState } from "react";
import { getTrendWarnings, type TrendWarning, type TrendWarningsResult } from "../api";
import type { DeviceProfile } from "../deviceProfile";
import { LINE_TEXT, durText, etaText } from "./hmi";

interface Props {
  deviceId: string;
  profile: DeviceProfile;
}

const POLL_MS = 60_000;

const OUTCOME_TEXT: Record<TrendWarning["outcome"], string> = {
  ongoing: "預警中",
  reached: "真的到了",
  cleared: "上升停止，自己退了",
  interrupted: "中途停機",
};

const fmt = (iso: string) =>
  new Date(iso).toLocaleString("zh-TW", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

export default function TrendLog({ deviceId, profile }: Props) {
  const [hours, setHours] = useState(24);
  const [res, setRes] = useState<TrendWarningsResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRes(await getTrendWarnings(deviceId, hours));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "讀取失敗");
    }
  }, [deviceId, hours]);

  useEffect(() => {
    const first = window.setTimeout(load, 0);
    const id = window.setInterval(load, POLL_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [load]);

  const s = res?.summary;
  const err = s?.avg_eta_error_s ?? null;

  return (
    <section className="panel" aria-labelledby="tw-h">
      <div className="panel-head">
        <h2 id="tw-h" className="panel-title">趨勢預警紀錄</h2>
        <div className="seg" role="group" aria-label="時間範圍">
          {[24, 72, 168, 720].map((h) => (
            <button key={h} type="button" className="btn" aria-pressed={hours === h} onClick={() => setHours(h)}>
              {h < 168 ? `${h} 時` : `${h / 24} 天`}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {res && !res.available && (
        <p className="muted" style={{ margin: 0 }}>還沒有預警紀錄表：請先執行 backend/db/migrations/007_trend_warnings.sql。</p>
      )}

      {res?.available && s && (
        <>
          <div className="stat-tiles">
            <div className="stat-tile" title="真的到了 ÷（真的到了＋自己退了）；中途停機、進行中的不算">
              <div className="label">命中率</div>
              <div className="value">{pct(s.hit_rate)}</div>
            </div>
            <div className="stat-tile">
              <div className="label">平均提前發現</div>
              <div className="value">{s.avg_lead_s === null ? "—" : durText(s.avg_lead_s)}</div>
            </div>
            <div className="stat-tile" title="第一次預估的時間跟實際到達的時間差多少">
              <div className="label">預估誤差</div>
              <div className="value">
                {err === null ? "—" : `${err >= 0 ? "晚" : "早"} ${durText(Math.abs(err))}`}
              </div>
            </div>
            <div className="stat-tile">
              <div className="label">預警次數</div>
              <div className="value">
                {Object.values(s.count).reduce((a, b) => a + b, 0)}
              </div>
            </div>
          </div>

          {res.warnings.length === 0 ? (
            <p className="muted" style={{ margin: 0 }}>這段時間沒有預警。</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>開始</th>
                    <th>訊號</th>
                    <th>預估會到</th>
                    <th>當時預估</th>
                    <th>上升速度</th>
                    <th>結果</th>
                    <th>提前發現</th>
                  </tr>
                </thead>
                <tbody>
                  {res.warnings.map((w) => {
                    const spec = profile.tags[w.tag_name];
                    return (
                      <tr key={w.id}>
                        <td className="mono nowrap">{fmt(w.started_at)}</td>
                        <td className="nowrap">{spec?.label ?? w.tag_name}</td>
                        <td className="nowrap">
                          {LINE_TEXT[w.target_level]} {w.threshold}
                          {spec ? ` ${spec.unit}` : ""}
                        </td>
                        <td className="nowrap">{etaText(w.first_eta_s)}後</td>
                        <td className="mono nowrap">
                          +{w.slope_per_min.toFixed(spec?.decimals ?? 2)}／分
                        </td>
                        <td className={`nowrap ${w.outcome === "reached" ? "lv-warning" : w.outcome === "ongoing" ? "lv-forecast" : ""}`}>
                          {OUTCOME_TEXT[w.outcome]}
                        </td>
                        <td className="nowrap">{w.lead_s === null ? "—" : durText(w.lead_s)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <p className="small muted" style={{ margin: "8px 0 0" }}>
        預警＝訊號持續上升、預估 60 分鐘內會到警戒值；停機與剛恢復運轉時暫停
      </p>
    </section>
  );
}
