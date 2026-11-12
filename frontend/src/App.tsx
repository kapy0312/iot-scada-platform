// App.tsx — ISA-101 風格的單元監控畫面
import { useEffect, useState } from "react";
import { useWebSocket } from "./hooks/useWebSocket";
import { useTheme } from "./hooks/useTheme";
import { useAlarms } from "./hooks/useAlarms";
import { useAlarmAttention } from "./hooks/useAlarmAttention";
import { useAlarmSound } from "./hooks/useAlarmSound";
import { useAuth } from "./hooks/useAuth";
import { useDeviceProfile, type TagKey } from "./deviceProfile";
import { WS_URL } from "./config";
import StatusBar from "./components/StatusBar";
import AlarmBanner from "./components/AlarmBanner";
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
import LoginDialog from "./components/LoginDialog";
import OeeCard from "./components/OeeCard";
import ProductionPanel from "./components/ProductionPanel";
import OeeSettings from "./components/OeeSettings";
import TrendLog from "./components/TrendLog";
import { useOeeCurrent } from "./hooks/useOee";

type Tab = "monitor" | "production" | "manuals" | "settings";
const TABS: Array<{ id: Tab; label: string }> = [
  { id: "monitor", label: "監控" },
  { id: "production", label: "生產" },
  { id: "manuals", label: "手冊管理" },
  { id: "settings", label: "系統設定" },
];

export default function App() {
  const { data, history, isConnected, sendCommand } = useWebSocket(WS_URL);
  const { theme, toggle } = useTheme();
  const auth = useAuth();
  const [tab, setTab] = useState<Tab>("monitor");
  // 目前開啟詳細趨勢的訊號；null 代表彈出視窗關閉
  const [trendTag, setTrendTag] = useState<TagKey | null>(null);

  const deviceId =
    data?.device_id ?? (data?.source === "mock" ? "S7-1511T-MOCK" : "S7-1511T");
  const { profile, threshold, fromServer, limitsInfo, reload: reloadProfile } = useDeviceProfile(
    deviceId,
    data?.limits_version,
  );
  const alarms = useAlarms(data, profile, threshold);
  const { attention, acknowledge, flashingDanger, anyFlashing } = useAlarmAttention(alarms, data?.timestamp);
  const sound = useAlarmSound(flashingDanger);

  // 分頁標題加上告警等級：切到別的瀏覽器分頁也看得到
  const topLevel = alarms[0]?.level;
  useEffect(() => {
    const base = "IIoT SCADA 監控";
    document.title = topLevel === "danger" ? `⚠ 危險｜${base}` : topLevel === "warning" ? `⚠ 警戒｜${base}` : base;
  }, [topLevel]);

  // 橫幅點文字：切回監控頁並捲到那張卡片
  const locateTag = (tag: string) => {
    setTab("monitor");
    window.setTimeout(() => {
      const el = document.getElementById(`ind-${tag}`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.focus({ preventScroll: true });
    }, 0);
  };
  const forecastCount = Object.values(data?.trend?.tags ?? {}).filter((t) => t.state === "forecast").length;
  const liveState = data?.production?.state;
  const oee = useOeeCurrent(deviceId, liveState);

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
        forecastCount={forecastCount}
        theme={theme}
        onToggleTheme={toggle}
        user={auth.user}
        onLogin={auth.openDialog}
        onLogout={auth.logout}
        soundOn={sound.enabled}
        soundBlocked={sound.blocked}
        onToggleSound={sound.toggle}
      />

      <AlarmBanner
        alarms={alarms}
        flashing={anyFlashing}
        canAcknowledge={flashingDanger}
        onAcknowledge={acknowledge}
        onLocate={locateTag}
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
            <div className="columns monitor-cols">
              <div className="col-wide">
                <ProcessOverview
                  data={data}
                  profile={profile}
                  history={history}
                  attention={attention}
                  onOpenTrend={setTrendTag}
                />
                {/* 事件紀錄放在左欄卡片下方，跟右欄高度接近，不再留下一大片空白 */}
                <EventLog
                  deviceId={deviceId}
                  isAnomaly={Boolean(data?.anomaly?.is_anomaly || data?.anomaly?.rule_alarm)}
                  profile={profile}
                />
              </div>
              <div className="col-side">
                <OeeCard
                  data={oee.data}
                  error={oee.error}
                  liveState={liveState}
                  onOpenDetail={() => setTab("production")}
                />
                <AlarmPanel
                  data={data}
                  alarms={alarms}
                  threshold={threshold}
                  diagnosis={diagnosis}
                  profile={profile}
                />
                <ControlPanel
                  onCommand={sendCommand}
                  motorEnabled={data ? Boolean(data.motor_enable) : null}
                  source={data?.source}
                  canOperate={auth.user !== null}
                  onRequireLogin={auth.openDialog}
                  profile={profile}
                  drift={data?.mock_drift ?? null}
                />
              </div>
            </div>
          </>
        )}

        {tab === "production" && (
          <>
            <ProductionPanel deviceId={deviceId} current={oee.data} error={oee.error} />
            <TrendLog deviceId={deviceId} profile={profile} />
          </>
        )}

        {tab === "manuals" && <RagPanel />}

        {tab === "settings" && (
          <>
            <SystemSettings
              historyCount={history.length}
              threshold={threshold}
              profileFromServer={fromServer}
              lastUpdate={data?.timestamp}
            />
            <OeeSettings canOperate={auth.user !== null} onRequireLogin={auth.openDialog} />
          </>
        )}
      </main>

      <TrendDialog
        tag={trendTag}
        history={history}
        profile={profile}
        onClose={() => setTrendTag(null)}
        deviceId={deviceId}
        limitsInfo={limitsInfo}
        canOperate={auth.user !== null}
        onRequireLogin={auth.openDialog}
        onLimitsSaved={reloadProfile}
      />

      <LoginDialog open={auth.dialogOpen} onLogin={auth.login} onClose={auth.closeDialog} />
    </>
  );
}
