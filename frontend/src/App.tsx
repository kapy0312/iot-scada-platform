// App.tsx

import { useWebSocket } from "./hooks/useWebSocket";
import GaugeCard from "./components/GaugeCard";
import RealtimeChart from "./components/RealtimeChart";
import ControlPanel from './components/ControlPanel';
import AnomalyPanel from './components/AnomalyPanel';

export default function App() {
  const { data, history, isConnected, sendCommand } = useWebSocket(
    "ws://localhost:8000/ws/realtime"
  );

  return (
    <>
      <div className="scanline" />
      <div style={{ minHeight: "100vh", padding: "24px 32px" }}>
        {/* Header */}
        <header
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 32,
            paddingBottom: 16,
            borderBottom: "1px solid var(--border-dim)",
          }}
        >
          <div>
            <div
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 11,
                letterSpacing: 6,
                color: "var(--text-secondary)",
                marginBottom: 6,
              }}
            >
              INDUSTRIAL MONITOR
            </div>
            <h1
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 22,
                fontWeight: 700,
                color: "var(--accent-cyan)",
                letterSpacing: 2,
              }}
            >
              IIoT SCADA PLATFORM
            </h1>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: isConnected
                  ? "var(--accent-green)"
                  : "var(--accent-red)",
                boxShadow: isConnected
                  ? "0 0 12px var(--accent-green)"
                  : "0 0 12px var(--accent-red)",
                animation: isConnected ? "pulse 2s infinite" : "none",
              }}
            />
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: isConnected
                  ? "var(--accent-green)"
                  : "var(--accent-red)",
              }}
            >
              {isConnected ? "CONNECTED" : "DISCONNECTED"}
            </span>
          </div>
        </header>

        {/* Gauge row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <GaugeCard
            label="Motor Speed"
            value={data?.motor_speed ?? 0}
            unit="RPM"
            min={0}
            max={1800}
            warn={1600}
            danger={1750}
            decimals={1}
          />
          <GaugeCard
            label="Temperature"
            value={data?.temperature ?? 0}
            unit="°C"
            min={0}
            max={120}
            warn={80}
            danger={100}
            decimals={2}
          />
          <GaugeCard
            label="Pressure"
            value={data?.pressure ?? 0}
            unit="bar"
            min={0}
            max={10}
            warn={7}
            danger={9}
            decimals={3}
          />
        </div>

        {/* Charts */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <RealtimeChart
            data={history}
            dataKey="motor_speed"
            color="var(--accent-cyan)"
            label="Speed Trend"
            unit="RPM"
            warnValue={1600}
          />
          <RealtimeChart
            data={history}
            dataKey="temperature"
            color="var(--accent-amber)"
            label="Temp Trend"
            unit="°C"
            warnValue={80}
          />
          <RealtimeChart
            data={history}
            dataKey="pressure"
            color="var(--accent-green)"
            label="Pressure Trend"
            unit="bar"
            warnValue={7}
          />
        </div>

        {/* Control + Status row */}
        <div
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}
        >
          <ControlPanel
            onCommand={sendCommand}
            motorEnabled={!!data?.motor_enable}
          />
          <AnomalyPanel anomaly={data?.anomaly ?? null} />

          {/* System log */}
          <div
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border-dim)",
              borderRadius: 2,
              padding: "20px 24px",
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 11,
                letterSpacing: 3,
                color: "var(--text-secondary)",
                textTransform: "uppercase",
                marginBottom: 16,
              }}
            >
              System Status
            </div>
            {data ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {[
                  {
                    k: "Last Update",
                    v: new Date(data.timestamp * 1000).toLocaleTimeString(),
                  },
                  {
                    k: "Motor Enable",
                    v: data.motor_enable ? "RUNNING" : "STOPPED",
                  },
                  { k: "Data Points", v: `${history.length} / 120` },
                  { k: "WS Endpoint", v: "ws://localhost:8000" },
                ].map(({ k, v }) => (
                  <div
                    key={k}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      borderBottom: "1px solid var(--border-dim)",
                      paddingBottom: 6,
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-ui)",
                        fontSize: 12,
                        color: "var(--text-secondary)",
                        letterSpacing: 1,
                      }}
                    >
                      {k}
                    </span>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                        color: "var(--accent-cyan)",
                      }}
                    >
                      {v}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  color: "var(--text-dim)",
                }}
              >
                Awaiting connection...
              </div>
            )}
          </div>
        </div>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </>
  );
}
