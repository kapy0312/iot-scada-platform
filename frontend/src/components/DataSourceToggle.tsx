import { useEffect, useState } from "react";

type Source = "mock" | "opcua";

export default function DataSourceToggle() {
  const [current, setCurrent] = useState<Source | null>(null);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);

  const fetchCurrent = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/system/data-source");
      const data = await res.json();
      setCurrent(data.source);
    } catch {
      setError("無法取得目前資料源");
    }
  };

  useEffect(() => {
    fetchCurrent();
  }, []);

  const switchSource = async (source: Source) => {
    if (source === current || switching) return;
    setSwitching(true);
    setError(null);
    try {
      const res = await fetch("http://localhost:8000/api/system/data-source", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source }),
      });
      const data = await res.json();
      if (data.status === "ok") {
        setCurrent(data.source);
      } else {
        setError(data.message ?? "切換失敗");
      }
    } catch {
      setError("連線失敗，無法切換資料源");
    } finally {
      setSwitching(false);
    }
  };

  const triggerAnomaly = async () => {
    setTriggering(true);
    setError(null);
    try {
      const res = await fetch(
        "http://localhost:8000/api/system/simulate-anomaly",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ duration_seconds: 40 }),
        }
      );
      const data = await res.json();
      if (data.status !== "ok") {
        setError(data.message ?? "觸發失敗");
      }
    } catch {
      setError("連線失敗，無法觸發模擬異常");
    } finally {
      setTriggering(false);
    }
  };

  const btnStyle = (active: boolean) => ({
    fontFamily: "var(--font-mono)",
    fontSize: 11,
    padding: "4px 10px",
    background: active ? "var(--accent-cyan)" : "transparent",
    color: active ? "var(--bg-deep)" : "var(--text-secondary)",
    border: `1px solid ${active ? "var(--accent-cyan)" : "var(--border-dim)"}`,
    borderRadius: 2,
    cursor: switching ? "not-allowed" : "pointer",
    opacity: switching ? 0.5 : 1,
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
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
          Data Source
        </span>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            style={btnStyle(current === "mock")}
            onClick={() => switchSource("mock")}
            disabled={switching}
          >
            MOCK
          </button>
          <button
            style={btnStyle(current === "opcua")}
            onClick={() => switchSource("opcua")}
            disabled={switching}
          >
            OPC UA
          </button>
        </div>
      </div>
      {current === "mock" && (
        <button
          onClick={triggerAnomaly}
          disabled={triggering}
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: 1,
            textTransform: "uppercase",
            padding: "6px 10px",
            background: "transparent",
            color: "var(--accent-amber)",
            border: "1px solid rgba(255,176,32,0.4)",
            borderRadius: 2,
            cursor: triggering ? "not-allowed" : "pointer",
            opacity: triggering ? 0.5 : 1,
          }}
        >
          {triggering ? "觸發中..." : "⚠ 觸發模擬異常（40秒）"}
        </button>
      )}
      {error && (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--accent-red)",
          }}
        >
          {error}
        </span>
      )}
    </div>
  );
}
