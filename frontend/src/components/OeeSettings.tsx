// 系統設定頁的 OEE 設定：品號標準週期、班別（要登入才能修改，修改會寫操作紀錄）
import { useCallback, useEffect, useState } from "react";
import { getOeeConfig, saveProducts, saveShifts, type OeeConfig, type ProductSpec, type ShiftDef } from "../api";
import "./oee.css";

interface Props {
  canOperate: boolean;
  onRequireLogin: () => void;
}

type Msg = { ok: boolean; text: string } | null;

/** 今天（台北時間）的 YYYY-MM-DD */
function todayTw(): string {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" });
}

/** 前端先檢查一次（後端還會再檢查）：重疊、總長超過 24 小時 */
function shiftError(shifts: ShiftDef[]): string | null {
  const used = new Map<number, string>();
  for (const s of shifts) {
    if (!s.name.trim()) return "班別名稱不能空白";
    const hours = (s.end_hour - s.start_hour + 24) % 24 || 24;
    for (let i = 0; i < hours; i++) {
      const h = (s.start_hour + i) % 24;
      if (used.has(h)) return `${s.name} 和 ${used.get(h)} 在 ${h}:00 重疊`;
      used.set(h, s.name);
    }
  }
  return null;
}

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function HourSelect({ value, onChange, disabled, label }: {
  value: number; onChange: (v: number) => void; disabled: boolean; label: string;
}) {
  return (
    <select className="input" aria-label={label} value={value} disabled={disabled}
            onChange={(e) => onChange(Number(e.target.value))}>
      {HOURS.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
    </select>
  );
}

export default function OeeSettings({ canOperate, onRequireLogin }: Props) {
  const [cfg, setCfg] = useState<OeeConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [products, setProducts] = useState<ProductSpec[]>([]);
  const [shifts, setShifts] = useState<ShiftDef[]>([]);
  const [effectiveFrom, setEffectiveFrom] = useState(todayTw());
  const [busy, setBusy] = useState(false);
  const [prodMsg, setProdMsg] = useState<Msg>(null);
  const [shiftMsg, setShiftMsg] = useState<Msg>(null);

  const load = useCallback(async () => {
    try {
      const c = await getOeeConfig();
      setCfg(c);
      setProducts(c.products.map((p) => ({ ...p })));
      setShifts((c.shifts?.shifts ?? []).map((s) => ({ ...s })));
      setLoadError(null);
    } catch (e) {
      const m = e instanceof Error ? e.message : "";
      setLoadError(m.includes("006") ? "OEE 尚未啟用：資料庫還沒執行 migration 006" : `無法取得 OEE 設定：${m}`);
    }
  }, []);

  useEffect(() => {
    const t = window.setTimeout(load, 0);
    return () => window.clearTimeout(t);
  }, [load]);

  const disabled = !canOperate || busy;

  const onSaveProducts = async () => {
    const bad = products.find((p) => !p.name.trim() || !(p.ideal_cycle_s > 0) || !(p.product_id > 0));
    if (bad) return setProdMsg({ ok: false, text: "品號要大於 0、名稱不能空白、標準週期要大於 0" });
    if (new Set(products.map((p) => p.product_id)).size !== products.length)
      return setProdMsg({ ok: false, text: "品號不能重複" });
    setBusy(true);
    try {
      await saveProducts(products);
      setProdMsg({ ok: true, text: "已儲存；之後的表現率用新的標準週期計算" });
      await load();
    } catch (e) {
      setProdMsg({ ok: false, text: e instanceof Error ? e.message : "儲存失敗" });
    } finally {
      setBusy(false);
    }
  };

  const onSaveShifts = async () => {
    const err = shifts.length ? shiftError(shifts) : "至少要一個班別";
    if (err) return setShiftMsg({ ok: false, text: err });
    if (!window.confirm(`新的班別從 ${effectiveFrom} 起生效，之前的日期仍照舊班別計算。確定嗎？`)) return;
    setBusy(true);
    try {
      await saveShifts(effectiveFrom, shifts);
      setShiftMsg({ ok: true, text: `已儲存，${effectiveFrom} 起生效` });
      await load();
    } catch (e) {
      setShiftMsg({ ok: false, text: e instanceof Error ? e.message : "儲存失敗" });
    } finally {
      setBusy(false);
    }
  };

  const setProduct = (i: number, patch: Partial<ProductSpec>) =>
    setProducts((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const setShift = (i: number, patch: Partial<ShiftDef>) =>
    setShifts((ss) => ss.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  const loginHint = !canOperate && (
    <p className="small muted">
      修改要先登入。<button type="button" className="btn ghost" style={{ minHeight: 28 }} onClick={onRequireLogin}>登入</button>
    </p>
  );

  if (loadError) {
    return <section className="panel"><p className="error">{loadError}</p></section>;
  }

  return (
    <div className="columns">
      <section className="panel col-side" aria-labelledby="prod-h">
        <div className="panel-head">
          <h2 id="prod-h" className="panel-title">品號標準週期</h2>
        </div>
        <p className="small muted">理想狀態下幾秒做一件；表現率＝件數 × 標準週期 ÷ 運轉時間。品號要跟 PLC 的 product_id 一致。</p>
        {loginHint}
        <table className="edit-table">
          <thead><tr><th style={{ width: 80 }}>品號</th><th>名稱</th><th style={{ width: 110 }}>秒／件</th></tr></thead>
          <tbody>
            {products.map((p, i) => (
              <tr key={i}>
                <td>
                  <input className="input mono" type="number" min={1} aria-label="品號" value={p.product_id}
                         disabled={disabled || i < (cfg?.products.length ?? 0)} /* 已存在的品號不改編號，只改名稱與週期 */
                         onChange={(e) => setProduct(i, { product_id: Number(e.target.value) })} />
                </td>
                <td>
                  <input className="input" aria-label="名稱" value={p.name} maxLength={50} disabled={disabled}
                         onChange={(e) => setProduct(i, { name: e.target.value })} />
                </td>
                <td>
                  <input className="input mono" type="number" min={0.001} step={0.1} aria-label="標準週期秒數"
                         value={p.ideal_cycle_s} disabled={disabled}
                         onChange={(e) => setProduct(i, { ideal_cycle_s: Number(e.target.value) })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <button type="button" className="btn" disabled={disabled}
                  onClick={() => setProducts((ps) => [...ps, {
                    product_id: Math.max(0, ...ps.map((p) => p.product_id)) + 1, name: "", ideal_cycle_s: 6,
                  }])}>
            新增品號
          </button>
          <button type="button" className="btn primary" disabled={disabled} onClick={onSaveProducts}>儲存品號</button>
        </div>
        {prodMsg && <p className={prodMsg.ok ? "ok-text" : "error"}>{prodMsg.text}</p>}
      </section>

      <section className="panel col-wide" aria-labelledby="shift-h">
        <div className="panel-head">
          <h2 id="shift-h" className="panel-title">班別</h2>
          <span className="small muted">
            目前：{cfg?.shifts ? `${cfg.shifts.effective_from} 起` : "未設定"}・時區 {cfg?.timezone}
          </span>
        </div>
        <p className="small muted">整點起訖；結束時間早於開始時間代表跨午夜（例如 20:00～08:00）。休息時間請在 PLC 設「計畫停機」，不用寫在這裡。</p>
        {loginHint}
        <table className="edit-table">
          <thead><tr><th>名稱</th><th style={{ width: 120 }}>開始</th><th style={{ width: 120 }}>結束</th><th style={{ width: 70 }}>時數</th><th style={{ width: 80 }} /></tr></thead>
          <tbody>
            {shifts.map((s, i) => (
              <tr key={i}>
                <td><input className="input" aria-label="班別名稱" value={s.name} maxLength={20} disabled={disabled}
                           onChange={(e) => setShift(i, { name: e.target.value })} /></td>
                <td><HourSelect label="開始" value={s.start_hour} disabled={disabled} onChange={(v) => setShift(i, { start_hour: v })} /></td>
                <td><HourSelect label="結束" value={s.end_hour} disabled={disabled} onChange={(v) => setShift(i, { end_hour: v })} /></td>
                <td className="mono">{(s.end_hour - s.start_hour + 24) % 24 || 24} 時</td>
                <td>
                  <button type="button" className="btn ghost" style={{ minHeight: 32, whiteSpace: "nowrap" }} disabled={disabled}
                          aria-label={`刪除 ${s.name}`} onClick={() => setShifts((ss) => ss.filter((_, j) => j !== i))}>
                    刪除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
          <button type="button" className="btn" disabled={disabled || shifts.length >= 6}
                  onClick={() => setShifts((ss) => [...ss, { name: `班別${ss.length + 1}`, start_hour: 0, end_hour: 8 }])}>
            新增班別
          </button>
          <label className="small" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            生效日
            <input className="input mono" type="date" value={effectiveFrom} disabled={disabled}
                   onChange={(e) => setEffectiveFrom(e.target.value)} />
          </label>
          <button type="button" className="btn primary" disabled={disabled} onClick={onSaveShifts}>儲存班別</button>
        </div>
        {shiftMsg && <p className={shiftMsg.ok ? "ok-text" : "error"}>{shiftMsg.text}</p>}
        {cfg && cfg.upcoming_shifts.length > 0 && (
          <p className="small muted">
            已排定：{cfg.upcoming_shifts.map((u) => `${u.effective_from} 起 ${u.shifts.map((s) => s.name).join("／")}`).join("；")}
          </p>
        )}
      </section>
    </div>
  );
}
