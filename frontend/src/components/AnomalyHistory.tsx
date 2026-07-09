import { useEffect, useRef, useState } from "react";

interface AnomalyEvent {
  time: string;
  anomaly_score: number;
  motor_speed: number;
  temperature: number;
  pressure: number;
  ai_analysis: string | null;
}

interface Props {
  isAnomaly: boolean;
  dataSource?: string;
}

export default function AnomalyHistory({ isAnomaly, dataSource }: Props) {
  const [events, setEvents] = useState<AnomalyEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [hours, setHours] = useState(24);

  const fetchEvents = async () => {
    setLoading(true);
    const deviceId = dataSource === "mock" ? "S7-1511T-MOCK" : "S7-1511T";
    try {
      const res = await fetch(
        `http://localhost:8000/api/anomaly-events?device_id=${deviceId}&hours=${hours}`
      );
      const data = await res.json();
      setEvents(data.events);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEvents();
  }, [hours, dataSource]);

  // 監聽 is_anomaly 從 false 變 true 的瞬間，延遲觸發刷新讓後端 DB 寫入完成
  const wasAnomaly = useRef(false);
  useEffect(() => {
    if (isAnomaly && !wasAnomaly.current) {
      const timer = setTimeout(() => {
        fetchEvents();
      }, 1500);
      return () => clearTimeout(timer);
    }
    wasAnomaly.current = isAnomaly;
  }, [isAnomaly]);

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleTimeString("zh-TW", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-dim)",
        borderRadius: 2,
        padding: "20px 24px",
      }}
    >
      {/* 標題列 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <div
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 11,
            letterSpacing: 3,
            color: "var(--text-secondary)",
            textTransform: "uppercase",
          }}
        >
          Anomaly History
        </div>

        {/* 時間範圍選擇 */}
        <div style={{ display: "flex", gap: 6 }}>
          {[1, 6, 24, 72].map((h) => (
            <button
              key={h}
              onClick={() => setHours(h)}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                padding: "3px 8px",
                background: hours === h ? "var(--accent-cyan)" : "transparent",
                color: hours === h ? "var(--bg-deep)" : "var(--text-secondary)",
                border: `1px solid ${
                  hours === h ? "var(--accent-cyan)" : "var(--border-dim)"
                }`,
                borderRadius: 2,
                cursor: "pointer",
              }}
            >
              {h}h
            </button>
          ))}
          <button
            onClick={fetchEvents}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              padding: "3px 8px",
              background: "transparent",
              color: "var(--text-secondary)",
              border: "1px solid var(--border-dim)",
              borderRadius: 2,
              cursor: "pointer",
            }}
          >
            ↻
          </button>
        </div>
      </div>

      {/* 統計列 */}
      <div
        style={{
          display: "flex",
          gap: 24,
          marginBottom: 12,
          paddingBottom: 12,
          borderBottom: "1px solid var(--border-dim)",
        }}
      >
        <div>
          <span
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 11,
              color: "var(--text-secondary)",
            }}
          >
            事件數
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 18,
              color:
                events.length > 0 ? "var(--accent-red)" : "var(--accent-green)",
              marginLeft: 8,
            }}
          >
            {events.length}
          </span>
        </div>
      </div>

      {/* 事件列表 */}
      {loading ? (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          載入中...
        </div>
      ) : events.length === 0 ? (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          過去 {hours} 小時無異常事件
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            maxHeight: 300,
            overflowY: "auto",
          }}
        >
          {events.map((e, i) => (
            <div
              key={i}
              style={{
                padding: "8px 10px",
                background: "var(--bg-deep)",
                border: "1px solid rgba(255,61,90,0.25)",
                borderRadius: 2,
                display: "grid",
                gridTemplateColumns: "140px 1fr 1fr 1fr 1fr",
                gap: 8,
                alignItems: "center",
              }}
            >
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-dim)",
                }}
              >
                {formatTime(e.time)}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--accent-red)",
                }}
              >
                {e.anomaly_score?.toFixed(4)}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-primary)",
                }}
              >
                {e.motor_speed?.toFixed(1)} RPM
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-primary)",
                }}
              >
                {e.temperature?.toFixed(1)}°C
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-primary)",
                }}
              >
                {e.pressure?.toFixed(2)} bar
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
