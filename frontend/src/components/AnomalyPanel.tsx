import type { AnomalyResult } from "../hooks/useWebSocket";

interface Props {
  anomaly: AnomalyResult | null;
}

export default function AnomalyPanel({ anomaly }: Props) {
  const isWarming = anomaly?.status === "warming_up";
  const isAnomaly = anomaly?.is_anomaly === true;

  const borderColor = isAnomaly
    ? "rgba(255,61,90,0.6)"
    : isWarming
    ? "var(--border-dim)"
    : "rgba(57,255,138,0.35)";

  const statusColor = isAnomaly
    ? "var(--accent-red)"
    : isWarming
    ? "var(--text-secondary)"
    : "var(--accent-green)";

  const statusText = isAnomaly
    ? "⚠ ANOMALY DETECTED"
    : isWarming
    ? `WARMING UP (${anomaly?.remaining ?? "?"} remaining)`
    : "✓ NORMAL";

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: `1px solid ${borderColor}`,
        borderRadius: 2,
        padding: "20px 24px",
        transition: "border-color 0.4s",
        boxShadow: isAnomaly ? "0 0 24px rgba(255,61,90,0.2)" : "none",
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
        ML Anomaly Detection
      </div>

      {/* 狀態燈 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 16,
        }}
      >
        <div
          style={{
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: statusColor,
            boxShadow: `0 0 10px ${statusColor}`,
            animation: isAnomaly ? "pulse 0.8s infinite" : "none",
          }}
        />
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 14,
            color: statusColor,
            letterSpacing: 2,
          }}
        >
          {statusText}
        </span>
      </div>

      {/* 分數顯示 */}
      {!isWarming && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div
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
              Anomaly Score
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: statusColor,
              }}
            >
              {anomaly?.score?.toFixed(4) ?? "—"}
            </span>
          </div>
          <div
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
              Threshold
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--text-dim)",
              }}
            >
              -0.1000
            </span>
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
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
              Model
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--text-dim)",
              }}
            >
              Isolation Forest v1
            </span>
          </div>

          {/* AI 分析結果 */}
          {anomaly?.ai_analysis && (
            <div
              style={{
                marginTop: 12,
                padding: "10px 12px",
                background: "var(--bg-deep)",
                border: "1px solid var(--border-dim)",
                borderRadius: 2,
              }}
            >
              <div
                style={{
                  fontFamily: "var(--font-ui)",
                  fontSize: 10,
                  letterSpacing: 2,
                  color: "var(--text-secondary)",
                  marginBottom: 6,
                  textTransform: "uppercase",
                }}
              >
                AI 診斷說明
              </div>
              <div
                style={{
                  fontFamily: "var(--font-ui)",
                  fontSize: 13,
                  color: "var(--text-primary)",
                  lineHeight: 1.8,
                  whiteSpace: "pre-line",
                }}
              >
                {anomaly.ai_analysis}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
