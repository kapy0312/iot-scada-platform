// RealtimeChart.tsx

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import type { PlcData } from '../hooks/useWebSocket';

interface Props {
  data: PlcData[];
  dataKey: keyof PlcData;
  color: string;
  label: string;
  unit: string;
  warnValue?: number;
}

const CustomTooltip = ({ active, payload, label: _l, unit }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border-mid)",
        padding: "8px 12px",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
      }}
    >
      <div style={{ color: "var(--text-secondary)", marginBottom: 2 }}>
        {new Date(payload[0]?.payload?.timestamp * 1000).toLocaleTimeString()}
      </div>
      <div style={{ color: payload[0]?.color }}>
        {payload[0]?.value?.toFixed(2)} {unit}
      </div>
    </div>
  );
};

export default function RealtimeChart({
  data,
  dataKey,
  color,
  label,
  unit,
  warnValue,
}: Props) {
  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-dim)",
        borderRadius: 2,
        padding: "16px 8px 8px 8px",
      }}
    >
      <div
        style={{
          paddingLeft: 16,
          marginBottom: 12,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <div
          style={{
            width: 3,
            height: 14,
            background: color,
            boxShadow: `0 0 6px ${color}`,
          }}
        />
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 11,
            letterSpacing: 3,
            color: "var(--text-secondary)",
            textTransform: "uppercase",
          }}
        >
          {label}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={120}>
        <LineChart
          data={data}
          margin={{ top: 4, right: 16, left: -20, bottom: 0 }}
        >
          <XAxis dataKey="timestamp" hide />
          <YAxis
            tick={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              fill: "var(--text-dim)",
            }}
          />
          {warnValue && (
            <ReferenceLine
              y={warnValue}
              stroke="var(--accent-amber)"
              strokeDasharray="4 4"
              strokeOpacity={0.5}
            />
          )}
          <Tooltip content={<CustomTooltip unit={unit} />} />
          <Line
            type="monotone"
            dataKey={dataKey as string}
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
