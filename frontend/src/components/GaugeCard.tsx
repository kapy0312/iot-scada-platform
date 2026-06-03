// GaugeCard.tsx

interface GaugeCardProps {
  label: string;
  value: number;
  unit: string;
  min: number;
  max: number;
  warn?: number;
  danger?: number;
  decimals?: number;
}

export default function GaugeCard({
  label,
  value,
  unit,
  min,
  max,
  warn,
  danger,
  decimals = 1,
}: GaugeCardProps) {
  const pct = Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));
  const isWarn = warn !== undefined && value >= warn;
  const isDanger = danger !== undefined && value >= danger;
  const accentColor = isDanger
    ? "var(--accent-red)"
    : isWarn
    ? "var(--accent-amber)"
    : "var(--accent-cyan)";

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: `1px solid ${
          isDanger
            ? "rgba(255,61,90,0.4)"
            : isWarn
            ? "rgba(255,176,32,0.35)"
            : "var(--border-dim)"
        }`,
        borderRadius: 2,
        padding: "20px 24px",
        position: "relative",
        overflow: "hidden",
        transition: "border-color 0.4s",
      }}
    >
      {/* Corner accents */}
      {["0,0", "0,auto", "auto,0", "auto,auto"].map((pos, i) => {
        const [t, b] = pos.split(",");
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              top: t === "0" ? 0 : undefined,
              bottom: b === "0" ? 0 : undefined,
              left: i < 2 ? 0 : undefined,
              right: i >= 2 ? 0 : undefined,
              width: 8,
              height: 8,
              borderTop: t === "0" ? `1px solid ${accentColor}` : undefined,
              borderBottom: b === "0" ? `1px solid ${accentColor}` : undefined,
              borderLeft: i < 2 ? `1px solid ${accentColor}` : undefined,
              borderRight: i >= 2 ? `1px solid ${accentColor}` : undefined,
            }}
          />
        );
      })}

      <div
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 11,
          letterSpacing: 3,
          color: "var(--text-secondary)",
          textTransform: "uppercase",
          marginBottom: 12,
        }}
      >
        {label}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 6,
          marginBottom: 16,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 36,
            color: accentColor,
            transition: "color 0.4s",
            lineHeight: 1,
          }}
        >
          {value.toFixed(decimals)}
        </span>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            color: "var(--text-secondary)",
            letterSpacing: 1,
          }}
        >
          {unit}
        </span>
      </div>

      {/* Progress bar */}
      <div
        style={{
          height: 2,
          background: "var(--border-dim)",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: accentColor,
            transition: "width 0.5s ease, background 0.4s",
            boxShadow: `0 0 8px ${accentColor}`,
          }}
        />
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: 4,
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-dim)",
        }}
      >
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}
