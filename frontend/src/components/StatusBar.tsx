import type { AuthUser } from "../api";
import type { Alarm } from "../hooks/useAlarms";
import type { Theme } from "../hooks/useTheme";
import { DangerIcon, WarningIcon } from "./Icons";

interface Props {
  deviceName: string;
  source?: string;
  isConnected: boolean;
  lastUpdate?: number;
  alarms: Alarm[];
  theme: Theme;
  onToggleTheme: () => void;
  user: AuthUser | null;
  onLogin: () => void;
  onLogout: () => void;
}

export default function StatusBar({ deviceName, source, isConnected, lastUpdate, alarms, theme, onToggleTheme, user, onLogin, onLogout }: Props) {
  const top = alarms[0]?.level;
  const chipClass = top === "danger" ? "chip danger" : top === "warning" ? "chip warning" : "chip";
  const time = lastUpdate
    ? new Date(lastUpdate * 1000).toLocaleString("zh-TW", { hour12: false })
    : "—";

  return (
    <header className="statusbar">
      <div className="title">
        <span className="small muted">IIoT SCADA Platform · 單元畫面</span>
        <strong>{deviceName}</strong>
      </div>
      <div className="meta">
        <span>
          資料源 <strong className="mono">{source ? source.toUpperCase() : "—"}</strong>
        </span>
        <span>
          <span className={isConnected ? "conn-dot" : "conn-dot off"} aria-hidden="true" />
          {isConnected ? "已連線" : "連線中斷，3 秒後重試"}
        </span>
        <span className="mono">{time}</span>
      </div>
      <div className="spacer" />
      <div className={chipClass} role="status" aria-live="polite">
        {top === "danger" && <DangerIcon size={16} />}
        {top === "warning" && <WarningIcon size={16} />}
        {alarms.length > 0 ? `作用中告警 ${alarms.length}` : "無告警"}
      </div>
      {user ? (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span className="small">操作員 <strong>{user.username}</strong></span>
          <button type="button" className="btn ghost" onClick={onLogout}>登出</button>
        </span>
      ) : (
        <button type="button" className="btn" onClick={onLogin}>登入</button>
      )}
      <button type="button" className="btn ghost" onClick={onToggleTheme} aria-label="切換淺色或深色主題">
        {theme === "light" ? "深色模式" : "淺色模式"}
      </button>
    </header>
  );
}
