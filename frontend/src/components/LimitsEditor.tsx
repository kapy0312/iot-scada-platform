// 趨勢視窗裡的「門檻設定」：顯示目前的正常範圍、警戒值、危險值、刻度，登入後可以修改或恢復預設
import { useState } from "react";
import { resetLimits, updateLimits } from "../api";
import { limitsOf, type Limits, type LimitsInfo, type TagSpec } from "../deviceProfile";

interface Props {
  deviceId: string;
  tag: string;
  spec: TagSpec;
  info?: LimitsInfo;
  current: number | null;         // 目前讀值：新的警戒值比它低時，先確認（儲存後會立刻告警）
  canOperate: boolean;
  onRequireLogin: () => void;
  onSaved: () => void;            // 儲存或恢復後重新載入規格
}

type Draft = Record<"normal_lo" | "normal_hi" | "warning" | "danger" | "scale_lo" | "scale_hi", string>;

const FIELDS: { key: keyof Draft; label: string }[] = [
  { key: "normal_lo", label: "正常下限" },
  { key: "normal_hi", label: "正常上限" },
  { key: "warning", label: "警戒值" },
  { key: "danger", label: "危險值" },
  { key: "scale_lo", label: "刻度最小" },
  { key: "scale_hi", label: "刻度最大" },
];

function toDraft(l: Limits): Draft {
  return {
    normal_lo: String(l.normal[0]), normal_hi: String(l.normal[1]),
    warning: String(l.warning), danger: String(l.danger),
    scale_lo: String(l.scale[0]), scale_hi: String(l.scale[1]),
  };
}

/** 跟後端同一套規則；回傳錯誤訊息，沒問題回傳 null */
function checkLimits(d: Draft): { limits: Limits | null; error: string | null } {
  const n = Object.fromEntries(Object.entries(d).map(([k, v]) => [k, v.trim() === "" ? NaN : Number(v)])) as Record<keyof Draft, number>;
  if (Object.values(n).some((v) => !Number.isFinite(v))) return { limits: null, error: "每一格都要填數字" };
  const rules: [boolean, string][] = [
    [n.scale_lo <= n.normal_lo, "正常下限不能小於刻度最小值"],
    [n.normal_lo < n.normal_hi, "正常下限要小於正常上限"],
    [n.normal_hi < n.warning, "警戒值要大於正常上限"],
    [n.warning < n.danger, "危險值要大於警戒值"],
    [n.danger <= n.scale_hi, "刻度最大值不能小於危險值（指示條會畫不到）"],
  ];
  const bad = rules.find(([ok]) => !ok);
  if (bad) return { limits: null, error: bad[1] };
  return {
    limits: { normal: [n.normal_lo, n.normal_hi], warning: n.warning, danger: n.danger, scale: [n.scale_lo, n.scale_hi] },
    error: null,
  };
}

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString("zh-TW", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

const same = (a: Limits, b: Limits) => JSON.stringify(a) === JSON.stringify(b);

export default function LimitsEditor({ deviceId, tag, spec, info, current, canOperate, onRequireLogin, onSaved }: Props) {
  const now = limitsOf(spec);
  const def = info?.default ?? null;
  const [draft, setDraft] = useState<Draft | null>(null);   // null＝檢視模式
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const check = draft ? checkLimits(draft) : null;
  const u = spec.unit;

  const startEdit = () => {
    if (!canOperate) return onRequireLogin();
    setMsg(null);
    setDraft(toDraft(now));
  };

  const save = async () => {
    if (!check?.limits) return;
    const l = check.limits;
    if (current !== null && current >= l.warning) {
      const level = current >= l.danger ? "危險值" : "警戒值";
      if (!window.confirm(`目前讀值 ${current.toFixed(spec.decimals)} ${u} 已經超過新的${level}，儲存後會立刻告警。確定要儲存？`)) return;
    }
    setBusy(true);
    try {
      await updateLimits(deviceId, tag, l);
      setDraft(null);
      setMsg({ ok: true, text: "已儲存，告警、預警與畫面都改用新的門檻" });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "儲存失敗" });
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    if (!canOperate) return onRequireLogin();
    if (!def || !window.confirm(`把${spec.label}的門檻恢復成預設值？`)) return;
    setBusy(true);
    try {
      await resetLimits(deviceId, tag);
      setDraft(null);
      setMsg({ ok: true, text: "已恢復預設值" });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "恢復失敗" });
    } finally {
      setBusy(false);
    }
  };

  const rows: { label: string; value: string; dflt: string | null }[] = [
    { label: "正常範圍", value: `${now.normal[0]}–${now.normal[1]}`, dflt: def ? `${def.normal[0]}–${def.normal[1]}` : null },
    { label: "警戒值", value: String(now.warning), dflt: def ? String(def.warning) : null },
    { label: "危險值", value: String(now.danger), dflt: def ? String(def.danger) : null },
    { label: "刻度", value: `${now.scale[0]}–${now.scale[1]}`, dflt: def ? `${def.scale[0]}–${def.scale[1]}` : null },
  ];
  const modified = info?.modified;
  const isDefault = def ? same(now, def) : true;

  return (
    <section className="limits" aria-labelledby="limits-h">
      <div className="limits-head">
        <h3 id="limits-h" className="section-title" style={{ margin: 0 }}>門檻設定（{u}）</h3>
        {!draft && (
          <div style={{ display: "flex", gap: 8 }}>
            {!isDefault && (
              <button type="button" className="btn ghost" disabled={busy} onClick={restore}>恢復預設值</button>
            )}
            <button type="button" className="btn" disabled={busy} onClick={startEdit}>
              {canOperate ? "編輯" : "登入後編輯"}
            </button>
          </div>
        )}
      </div>

      {!draft ? (
        <table className="limits-table">
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                <td className="mono">{r.value}</td>
                <td className="muted small">{r.dflt !== null && r.dflt !== r.value ? `預設 ${r.dflt}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <div className="limits-grid">
            {FIELDS.map((f) => (
              <label key={f.key} className="limits-field">
                <span className="muted small">{f.label}</span>
                <input
                  className="input mono"
                  inputMode="decimal"
                  value={draft[f.key]}
                  onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                  aria-invalid={Boolean(check?.error)}
                />
              </label>
            ))}
          </div>
          <p className={check?.error ? "error" : "small muted"} role="status" style={{ margin: "8px 0" }}>
            {check?.error ?? "規則：刻度最小 ≤ 正常下限 < 正常上限 < 警戒 < 危險 ≤ 刻度最大"}
          </p>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            {def && (
              <button type="button" className="btn ghost" disabled={busy} onClick={() => setDraft(toDraft(def))}>
                填入預設值
              </button>
            )}
            <button type="button" className="btn ghost" disabled={busy} onClick={() => setDraft(null)}>取消</button>
            <button type="submit" className="btn primary" disabled={busy || Boolean(check?.error)}>儲存</button>
          </div>
        </form>
      )}

      {msg && <p className={msg.ok ? "small" : "error"} role="status" style={{ margin: "8px 0 0" }}>{msg.text}</p>}
      <p className="small muted" style={{ margin: "6px 0 0" }}>
        {modified?.updated_at
          ? `上次修改：${modified.updated_by ?? "—"} ${fmtTime(modified.updated_at)}；每次修改都會記在操作紀錄`
          : "目前使用程式預設值；修改需要登入，每次修改都會記在操作紀錄"}
      </p>
    </section>
  );
}
