// ControlPanel.tsx

import { useState } from "react";

interface Props {
  onCommand: (tag: string, value: number) => void;
  motorEnabled: boolean;
}

function CmdButton({
  label,
  onClick,
  variant = "default",
}: {
  label: string;
  onClick: () => void;
  variant?: "danger" | "success" | "default";
}) {
  const colors = {
    danger: {
      border: "rgba(255,61,90,0.4)",
      color: "var(--accent-red)",
      glow: "rgba(255,61,90,0.3)",
    },
    success: {
      border: "rgba(57,255,138,0.35)",
      color: "var(--accent-green)",
      glow: "rgba(57,255,138,0.25)",
    },
    default: {
      border: "var(--border-mid)",
      color: "var(--accent-cyan)",
      glow: "rgba(0,200,180,0.2)",
    },
  }[variant];

  return (
    <button
      onClick={onClick}
      style={{
        background: "transparent",
        border: `1px solid ${colors.border}`,
        color: colors.color,
        fontFamily: "var(--font-ui)",
        fontSize: 12,
        fontWeight: 600,
        letterSpacing: 2,
        textTransform: "uppercase",
        padding: "10px 20px",
        cursor: "pointer",
        transition: "all 0.2s",
        borderRadius: 1,
      }}
      onMouseEnter={(e) => (
        (e.currentTarget.style.boxShadow = `0 0 16px ${colors.glow}`),
        (e.currentTarget.style.background = `${colors.glow}`)
      )}
      onMouseLeave={(e) => (
        (e.currentTarget.style.boxShadow = "none"),
        (e.currentTarget.style.background = "transparent")
      )}
    >
      {label}
    </button>
  );
}

// 改成這樣，用底線前綴
export default function ControlPanel({ onCommand, motorEnabled }: Props) {
  const [speedInput, setSpeedInput] = useState("1480");

  return (
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
          marginBottom: 20,
        }}
      >
        Control Interface
      </div>

      <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
        <CmdButton
          label="Motor ON"
          variant={motorEnabled ? "default" : "success"}
          onClick={() => onCommand("motor_enable", 1)}
        />
        <CmdButton
          label="Motor OFF"
          variant={motorEnabled ? "danger" : "default"}
          onClick={() => onCommand("motor_enable", 0)}
        />
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          type="number"
          value={speedInput}
          onChange={(e) => setSpeedInput(e.target.value)}
          style={{
            background: "var(--bg-deep)",
            border: "1px solid var(--border-mid)",
            color: "var(--accent-cyan)",
            fontFamily: "var(--font-mono)",
            fontSize: 14,
            padding: "8px 12px",
            width: 100,
            borderRadius: 1,
            outline: "none",
          }}
        />
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-dim)",
          }}
        >
          RPM
        </span>
        <CmdButton
          label="Set Speed"
          onClick={() => onCommand("motor_speed_setpoint", Number(speedInput))}
        />
      </div>
    </div>
  );
}
