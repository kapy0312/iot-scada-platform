import { useState } from "react";
import { simulateDrift, triggerMockAnomaly } from "../api";
import { tagKeys, type DeviceProfile } from "../deviceProfile";
import type { MockDrift } from "../hooks/useWebSocket";

/** 模擬劣化的速度：從正常範圍中間爬到警戒值大約 8 分鐘（取兩位有效數字） */
const DRIFT_MINUTES = 8;
function driftRate(profile: DeviceProfile, tag: string): number {
  const s = profile.tags[tag];
  const r = (s.warning - (s.normal[0] + s.normal[1]) / 2) / DRIFT_MINUTES;
  return Number(r.toPrecision(2));
}

interface Props {
  onCommand: (tag: string, value: number) => Promise<{ ok: boolean; message: string }>;
  motorEnabled: boolean | null;
  source?: string;
  canOperate: boolean;          // 已登入才可操作
  onRequireLogin: () => void;
  profile: DeviceProfile;
  drift: MockDrift | null;      // 內建模擬目前的漸進劣化（沒有＝null）
}

const SPEED_MIN = 0;
const SPEED_MAX = 1800;

export default function ControlPanel({ onCommand, motorEnabled, source, canOperate, onRequireLogin, profile, drift }: Props) {
  const [driftTag, setDriftTag] = useState("temperature");
  const [speedInput, setSpeedInput] = useState("1480");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const isMock = source === "mock";
  const locked = busy || !canOperate;

  const send = async (label: string, tag: string, value: number) => {
    const target = isMock ? "（mock 模式，指令不會送到實體 PLC）" : "（會寫入 PLC）";
    if (!window.confirm(`確定要${label}？${target}`)) return;
    setBusy(true);
    setResult(null);
    const r = await onCommand(tag, value);
    setResult({ ok: r.ok, message: r.ok ? `${label}：已送出` : `${label}失敗：${r.message}` });
    setBusy(false);
  };

  const speed = Number(speedInput);
  const speedValid = speedInput.trim() !== "" && Number.isFinite(speed) && speed >= SPEED_MIN && speed <= SPEED_MAX;

  const onTrigger = async () => {
    setBusy(true);
    setResult(null);
    try {
      await triggerMockAnomaly(40);
      setResult({ ok: true, message: "已觸發模擬異常，持續 40 秒" });
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : "觸發失敗" });
    } finally {
      setBusy(false);
    }
  };

  const tags = tagKeys(profile);
  const pickTag = tags.includes(driftTag) ? driftTag : tags[0];
  const onDrift = async (on: boolean) => {
    setBusy(true);
    setResult(null);
    try {
      const rate = on ? driftRate(profile, pickTag) : 0;
      await simulateDrift(on ? pickTag : null, rate);
      const spec = profile.tags[pickTag];
      setResult({
        ok: true,
        message: on
          ? `已開始模擬劣化：${spec.label}每分鐘 +${rate} ${spec.unit}，約 ${DRIFT_MINUTES} 分鐘到警戒值（預警會先收集幾分鐘資料才出現）`
          : "已停止模擬劣化，數值約 1 分鐘內回到正常",
      });
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : "設定失敗" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" aria-labelledby="ctrl-h" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 id="ctrl-h" className="panel-title">控制面板</h2>

      {!canOperate && (
        <div className="kv" role="note">
          <span className="small muted">登入後才能操作，每次寫入都會記錄</span>
          <button type="button" className="btn" onClick={onRequireLogin}>登入</button>
        </div>
      )}

      <div className="kv">
        <span className="muted">運轉狀態</span>
        <strong>{motorEnabled === null ? "—" : motorEnabled ? "RUNNING" : "STOPPED"}</strong>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" className="btn" style={{ flex: "1 1 0" }} disabled={locked}
          aria-pressed={motorEnabled === true} onClick={() => send("啟動馬達", "motor_enable", 1)}>
          啟動
        </button>
        <button type="button" className="btn" style={{ flex: "1 1 0" }} disabled={locked}
          aria-pressed={motorEnabled === false} onClick={() => send("停止馬達", "motor_enable", 0)}>
          停止
        </button>
      </div>

      <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
        <div style={{ flex: "1 1 auto", display: "flex", flexDirection: "column", gap: 4 }}>
          <label htmlFor="speed-sp" className="muted" style={{ fontSize: 15 }}>
            轉速設定值（RPM，{SPEED_MIN}–{SPEED_MAX}）
          </label>
          <input id="speed-sp" className="input mono" type="number" min={SPEED_MIN} max={SPEED_MAX}
            value={speedInput} onChange={(e) => setSpeedInput(e.target.value)} aria-invalid={!speedValid} />
        </div>
        <button type="button" className="btn primary" disabled={locked || !speedValid}
          onClick={() => send(`設定轉速為 ${speed} RPM`, "motor_speed_setpoint", speed)}>
          寫入
        </button>
      </div>

      {result && (
        <p className={result.ok ? "small muted" : "error"} role="status" style={{ margin: 0 }}>{result.message}</p>
      )}

      {isMock && (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="muted" style={{ fontSize: 15 }}>測試工具（只有內建模擬可用）</span>
          <button type="button" className="btn ghost" disabled={locked} onClick={onTrigger}>
            突發異常（40 秒）
          </button>
          {drift ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ flex: "1 1 auto" }} role="status">
                模擬劣化中：{profile.tags[drift.tag]?.label ?? drift.tag}每分鐘 +{drift.rate_per_min}
                <span className="muted small">（已加 {drift.offset.toFixed(2)}）</span>
              </span>
              <button type="button" className="btn ghost" disabled={locked} onClick={() => onDrift(false)}>
                停止劣化
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", gap: 8 }}>
              <select
                className="input" aria-label="模擬劣化的訊號" style={{ flex: "1 1 auto" }}
                value={pickTag} disabled={locked} onChange={(e) => setDriftTag(e.target.value)}
              >
                {tags.map((k) => (
                  <option key={k} value={k}>{profile.tags[k].label}</option>
                ))}
              </select>
              <button type="button" className="btn ghost" disabled={locked} onClick={() => onDrift(true)}>
                慢慢劣化
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
