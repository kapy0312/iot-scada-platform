import { useState } from "react";
import { triggerMockAnomaly } from "../api";

interface Props {
  onCommand: (tag: string, value: number) => Promise<{ ok: boolean; message: string }>;
  motorEnabled: boolean | null;
  source?: string;
}

const SPEED_MIN = 0;
const SPEED_MAX = 1800;

export default function ControlPanel({ onCommand, motorEnabled, source }: Props) {
  const [speedInput, setSpeedInput] = useState("1480");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const isMock = source === "mock";

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

  return (
    <section className="panel" aria-labelledby="ctrl-h" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 id="ctrl-h" className="panel-title">控制面板</h2>

      <div className="kv">
        <span className="muted">運轉狀態</span>
        <strong>{motorEnabled === null ? "—" : motorEnabled ? "RUNNING" : "STOPPED"}</strong>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" className="btn" style={{ flex: "1 1 0" }} disabled={busy}
          aria-pressed={motorEnabled === true} onClick={() => send("啟動馬達", "motor_enable", 1)}>
          啟動
        </button>
        <button type="button" className="btn" style={{ flex: "1 1 0" }} disabled={busy}
          aria-pressed={motorEnabled === false} onClick={() => send("停止馬達", "motor_enable", 0)}>
          停止
        </button>
      </div>

      <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
        <div style={{ flex: "1 1 auto", display: "flex", flexDirection: "column", gap: 4 }}>
          <label htmlFor="speed-sp" className="muted" style={{ fontSize: 13 }}>
            轉速設定值（RPM，{SPEED_MIN}–{SPEED_MAX}）
          </label>
          <input id="speed-sp" className="input mono" type="number" min={SPEED_MIN} max={SPEED_MAX}
            value={speedInput} onChange={(e) => setSpeedInput(e.target.value)} aria-invalid={!speedValid} />
        </div>
        <button type="button" className="btn primary" disabled={busy || !speedValid}
          onClick={() => send(`設定轉速為 ${speed} RPM`, "motor_speed_setpoint", speed)}>
          寫入
        </button>
      </div>

      {result && (
        <p className={result.ok ? "small muted" : "error"} role="status" style={{ margin: 0 }}>{result.message}</p>
      )}

      {isMock && (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 12, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <span className="muted" style={{ fontSize: 13 }}>測試工具（僅 mock）</span>
          <button type="button" className="btn ghost" disabled={busy} onClick={onTrigger}>
            觸發模擬異常（40 秒）
          </button>
        </div>
      )}
    </section>
  );
}
