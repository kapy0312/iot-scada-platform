import { useMemo } from "react";
import type { PlcData } from "../hooks/useWebSocket";
import {
  LEVEL_TEXT,
  evaluate,
  getValue,
  instrumentCode,
  tagKeys,
  type DeviceProfile,
  type Level,
  type TagKey,
  type TagSpec,
} from "../deviceProfile";
import { DangerIcon, WarningIcon } from "./Icons";
import { fixedWindow, levelKind, numbersOf, pct } from "./hmi";
import "./hmi.css";

interface Props {
  data: PlcData | null;
  profile: DeviceProfile;
  /** 最近的歷史資料，用來畫卡片內 5 分鐘迷你走勢；沒傳就不畫 */
  history?: PlcData[];
  /** 點卡片時開啟該訊號的詳細趨勢 */
  onOpenTrend?: (tag: TagKey) => void;
}

const SPARK_SECONDS = 300;
const SPARK_H = 44;

/** 固定刻度的迷你走勢：灰色帶與警戒線位置永遠不變；危險值交給指示條呈現，小圖不再擠兩條線 */
function Sparkline({ spec, values }: { spec: TagSpec; values: number[] }) {
  if (values.length < 2) {
    return <svg className="hmi-spark" aria-hidden="true" />;
  }
  const { lo, hi } = fixedWindow(spec, values);
  const y = (v: number) => ((hi - v) / (hi - lo)) * SPARK_H;
  const pts = values
    .map(
      (v, i) =>
        `${((i / (values.length - 1)) * 200).toFixed(1)},${y(v).toFixed(2)}`,
    )
    .join(" ");
  const bandTop = y(Math.min(spec.normal[1], hi));
  const bandBottom = y(Math.max(spec.normal[0], lo));
  const showWarn = spec.warning >= lo && spec.warning <= hi;
  return (
    <svg
      className="hmi-spark"
      viewBox={`0 0 200 ${SPARK_H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${spec.label} 最近 5 分鐘走勢`}
    >
      <rect
        x={0}
        y={bandTop}
        width={200}
        height={Math.max(0, bandBottom - bandTop)}
        fill="var(--band)"
      />
      {showWarn && (
        <line
          x1={0}
          x2={200}
          y1={y(spec.warning)}
          y2={y(spec.warning)}
          stroke="var(--warn-mark)"
          strokeWidth={1}
          strokeDasharray="4 3"
          vectorEffect="non-scaling-stroke"
        />
      )}
      <polyline
        points={pts}
        fill="none"
        stroke="var(--pen)"
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function AnalogIndicator({
  spec,
  value,
  trend,
  onOpen,
}: {
  spec: TagSpec;
  value: number | null;
  trend: number[];
  onOpen?: () => void;
}) {
  const [scaleMin, scaleMax] = spec.scale;
  const level: Level | null = value === null ? null : evaluate(spec, value);
  const kind = levelKind(level);
  const p = (v: number) => pct(scaleMin, scaleMax, v);
  const valueText = value === null ? "—" : value.toFixed(spec.decimals);
  const stateClass = kind === "dev" ? "dev" : (level ?? "normal");
  const arrow = value !== null && value > spec.normal[1] ? "▲" : "▼";

  const delta = value !== null && trend.length > 1 ? value - trend[0] : null;
  const deltaText =
    delta === null
      ? "5 分鐘變化 —"
      : `5 分鐘 ${delta >= 0 ? "+" : "−"}${Math.abs(delta).toFixed(spec.decimals)} ${spec.unit}`;

  const cardClass = [
    "ind",
    onOpen ? "hmi-card" : "",
    kind === "dev" || kind === "warning" || kind === "danger"
      ? `hmi-st-${kind}`
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cardClass} onClick={onOpen}>
      <div className="hmi-head">
        <div className="hmi-title">
          <span className="mono hmi-tag">{spec.tag_no}</span>
          <span className="hmi-name">{spec.label}</span>
        </div>
        <span className={`ind-state ${stateClass}`}>
          {kind === "danger" && <DangerIcon size={12} />}
          {kind === "warning" && <WarningIcon size={12} />}
          {kind === "dev" && <span aria-hidden="true">{arrow}</span>}
          {level ? LEVEL_TEXT[level] : "等待資料"}
        </span>
      </div>

      <div className="hmi-val">
        <span
          className={`hmi-num ${kind === "danger" || kind === "warning" ? kind : ""}`}
        >
          {valueText}
        </span>
        <span className="hmi-u">{spec.unit}</span>
      </div>
      <div className="hmi-delta">{deltaText}</div>

      <div
        className="hmi-bar"
        role="img"
        aria-label={`${spec.label} ${valueText} ${spec.unit}，${level ? LEVEL_TEXT[level] : "等待資料"}，正常範圍 ${spec.normal[0]} 到 ${spec.normal[1]}，警戒 ${spec.warning}，危險 ${spec.danger}`}
      >
        <div className="hmi-track">
          <div
            className="hmi-zone normal"
            style={{
              left: `${p(spec.normal[0])}%`,
              width: `${p(spec.normal[1]) - p(spec.normal[0])}%`,
            }}
          />
          <div
            className={`hmi-zone warn${kind === "warning" ? " on" : ""}`}
            style={{
              left: `${p(spec.warning)}%`,
              width: `${p(spec.danger) - p(spec.warning)}%`,
            }}
          />
          <div
            className={`hmi-zone danger${kind === "danger" ? " on" : ""}`}
            style={{ left: `${p(spec.danger)}%` }}
          />
        </div>
        {value !== null && (
          <div className={`hmi-ptr ${kind}`} style={{ left: `${p(value)}%` }} />
        )}
      </div>
      <div className="hmi-scale">
        <span>{scaleMin}</span>
        <span>{scaleMax}</span>
      </div>

      <div className="hmi-limits">
        <span>
          正常{" "}
          <span className="mono">
            {spec.normal[0]}–{spec.normal[1]}
          </span>
        </span>
        <span className="w">
          警戒 <span className="mono">{spec.warning}</span>
        </span>
        <span className="d">
          危險 <span className="mono">{spec.danger}</span>
        </span>
      </div>

      <div className="hmi-spark-box">
        <div className="hmi-spark-head">
          <span className="hmi-spark-cap">最近 5 分鐘</span>
          {onOpen && (
            <button
              type="button"
              className="hmi-link"
              onClick={(e) => {
                e.stopPropagation();
                onOpen();
              }}
              aria-label={`開啟 ${spec.label} 詳細趨勢`}
            >
              趨勢 ›
            </button>
          )}
        </div>
        <Sparkline spec={spec} values={trend} />
      </div>
    </div>
  );
}

function Instrument({ spec, value }: { spec: TagSpec; value: number | null }) {
  const lv = value === null ? "normal" : evaluate(spec, value);
  const cls =
    lv === "danger"
      ? "inst danger"
      : lv === "warning" || lv === "high"
        ? "inst warning"
        : "inst";
  return (
    <div className="equip">
      <div className={cls} title={`${spec.tag_no} ${spec.label}`}>
        {instrumentCode(spec)}
      </div>
      <span className="cap">{spec.label}</span>
    </div>
  );
}

// 壓力計裝在泵浦出口；其餘儀表（轉速、溫度、振動、電流）都量測馬達本體
const PUMP_SIDE_CODES = new Set(["PT", "FT"]);

export default function ProcessOverview({
  data,
  profile,
  history,
  onOpenTrend,
}: Props) {
  const running = data ? Boolean(data.motor_enable) : null;
  const keys = tagKeys(profile);
  const motorSide: TagKey[] = keys.filter(
    (k) => !PUMP_SIDE_CODES.has(instrumentCode(profile.tags[k])),
  );
  const pumpSide: TagKey[] = keys.filter((k) =>
    PUMP_SIDE_CODES.has(instrumentCode(profile.tags[k])),
  );

  const recent = useMemo(() => {
    if (!history || history.length === 0) return [];
    const end = history[history.length - 1].timestamp;
    return history.filter((pt) => pt.timestamp >= end - SPARK_SECONDS);
  }, [history]);

  return (
    <section className="panel" aria-labelledby="proc-h">
      <div className="panel-head">
        <h2 id="proc-h" className="panel-title">
          製程簡圖與即時值
        </h2>
      </div>

      <div
        className="process"
        aria-label="製程流向：馬達帶動泵浦，經壓力計到下游閥門"
      >
        <div className="equip">
          <div className="shape round">M</div>
          <span className="cap">
            馬達 · {running === null ? "—" : running ? "運轉中" : "停止"}
          </span>
        </div>
        {motorSide.map((k) => (
          <div key={k} style={{ display: "contents" }}>
            <div className="pipe" style={{ width: 12 }} aria-hidden="true" />
            <Instrument spec={profile.tags[k]} value={getValue(data, k)} />
          </div>
        ))}
        <div className="pipe" style={{ width: 24 }} aria-hidden="true" />
        <div className="equip">
          <div className="shape square">P</div>
          <span className="cap">泵浦</span>
        </div>
        {pumpSide.map((k) => (
          <div key={k} style={{ display: "contents" }}>
            <div className="pipe" style={{ width: 48 }} aria-hidden="true" />
            <Instrument spec={profile.tags[k]} value={getValue(data, k)} />
          </div>
        ))}
        <div className="pipe" style={{ width: 48 }} aria-hidden="true" />
        <div className="equip">
          <svg
            width="56"
            height="40"
            viewBox="0 0 56 40"
            role="img"
            aria-label="下游閥門"
          >
            <path
              d="M4 6 L28 20 L4 34 Z M52 6 L28 20 L52 34 Z"
              fill="var(--equip)"
              stroke="var(--text)"
              strokeWidth="2"
            />
          </svg>
          <span className="cap">下游閥門</span>
        </div>
        <div className="pipe" style={{ width: 32 }} aria-hidden="true" />
        <span className="cap" style={{ marginBottom: 22 }}>
          出口
        </span>
      </div>

      <div className="indicators">
        {keys.map((k) => (
          <AnalogIndicator
            key={k}
            spec={profile.tags[k]}
            value={getValue(data, k)}
            trend={numbersOf(recent, k)}
            onOpen={onOpenTrend ? () => onOpenTrend(k) : undefined}
          />
        ))}
      </div>
    </section>
  );
}
