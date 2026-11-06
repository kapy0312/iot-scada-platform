// App.tsx — ISA-101 風格的單元監控畫面
import { useEffect, useState } from "react";
import { useWebSocket } from "./hooks/useWebSocket";
import { useTheme } from "./hooks/useTheme";
import { useAlarms } from "./hooks/useAlarms";
import { useDeviceProfile, type TagKey } from "./deviceProfile";
import { WS_URL } from "./config";
import StatusBar from "./components/StatusBar";
import ProcessOverview from "./components/ProcessOverview";
import TrendDialog from "./components/TrendDialog";
import AlarmPanel, {
  describeStatus,
  type Diagnosis,
} from "./components/AlarmPanel";
import ControlPanel from "./components/ControlPanel";
import EventLog from "./components/EventLog";
import RagPanel from "./components/RagPanel";
import SystemSettings from "./components/SystemSettings";

type Tab = "monitor" | "manuals" | "settings";
const TABS: Array<{ id: Tab; label: string }> = [
  { id: "monitor", label: "監控" },
  { id: "manuals", label: "手冊管理" },
  { id: "settings", label: "系統設定" },
];

export default function App() {
  const { data, history, isConnected, sendCommand } = useWebSocket(WS_URL);
  const { theme, toggle } = useTheme();
  const [tab, setTab] = useState<Tab>("monitor");
  // 目前開啟詳細趨勢的訊號；null 代表彈出視窗關閉
  const [trendTag, setTrendTag] = useState<TagKey | null>(null);

  const deviceId =
    data?.device_id ?? (data?.source === "mock" ? "S7-1511T-MOCK" : "S7-1511T");
  const { profile, threshold, fromServer } = useDeviceProfile(deviceId);
  const alarms = useAlarms(data, profile, threshold);

  // 保留最近一次 AI 診斷，異常結束後畫面仍看得到
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const aiText = data?.anomaly?.ai_analysis;
  useEffect(() => {
    if (!data || !aiText) return;
    setDiagnosis((prev) =>
      prev?.text === aiText
        ? prev
        : {
            text: aiText,
            at: data.timestamp,
            status: describeStatus(profile, data),
            source: data.anomaly?.ai_source ?? null,
          },
    );
  }, [aiText, data, profile]);

  return (
    <>
      <StatusBar
        deviceName={profile.name}
        source={data?.source}
        isConnected={isConnected}
        lastUpdate={data?.timestamp}
        alarms={alarms}
        theme={theme}
        onToggleTheme={toggle}
      />

      <nav className="tabs" role="tablist" aria-label="畫面分頁">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="main">
        {tab === "monitor" && (
          <>
            <div className="columns">
              <div className="col-wide">
                <ProcessOverview
                  data={data}
                  profile={profile}
                  history={history}
                  onOpenTrend={setTrendTag}
                />
              </div>
              <div className="col-side">
                <AlarmPanel
                  data={data}
                  alarms={alarms}
                  threshold={threshold}
                  diagnosis={diagnosis}
                />
                <ControlPanel
                  onCommand={sendCommand}
                  motorEnabled={data ? Boolean(data.motor_enable) : null}
                  source={data?.source}
                />
              </div>
            </div>
            <EventLog
              deviceId={deviceId}
              isAnomaly={Boolean(
                data?.anomaly?.is_anomaly || data?.anomaly?.rule_alarm,
              )}
              profile={profile}
            />
          </>
        )}

        {tab === "manuals" && <RagPanel />}

        {tab === "settings" && (
          <SystemSettings
            historyCount={history.length}
            threshold={threshold}
            profileFromServer={fromServer}
            lastUpdate={data?.timestamp}
          />
        )}
      </main>

      <TrendDialog
        tag={trendTag}
        history={history}
        profile={profile}
        onClose={() => setTrendTag(null)}
      />
    </>
  );
}
