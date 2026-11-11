import { useEffect, useRef, useState, type FormEvent } from "react";
import "./hmi.css";

interface Props {
  open: boolean;
  onLogin: (username: string, password: string) => Promise<void>;
  onClose: () => void;
}

export default function LoginDialog({ open, onLogin, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setError(null);
      setPassword("");
      d.showModal();
    }
    if (!open && d.open) d.close();
  }, [open]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      await onLogin(username.trim(), password);
      setPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登入失敗");
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="hmi-dialog" style={{ width: "min(380px, 94vw)" }}
      aria-labelledby="login-dlg-h" onClose={onClose}>
      <form className="hmi-dlg-body" onSubmit={submit}>
        <h2 id="login-dlg-h" className="hmi-dlg-title">操作員登入</h2>
        <p className="small muted" style={{ margin: 0 }}>
          寫入 PLC、切換資料源、管理手冊需要登入，每次操作都會留下紀錄。
        </p>
        <label className="muted" style={{ fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
          帳號
          <input className="input" autoComplete="username" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="muted" style={{ fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
          密碼
          <input className="input" type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="error" role="alert" style={{ margin: 0 }}>{error}</p>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" className="btn primary" disabled={busy || !username.trim() || !password}>
            {busy ? "登入中…" : "登入"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
