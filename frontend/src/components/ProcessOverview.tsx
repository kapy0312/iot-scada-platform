import { useMemo } from "react";
import type { PlcData, TagTrend, TrendInfo } from "../hooks/useWebSocket";
import {
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
import { LINE_TEXT, etaText, fixedWindow, isForecast, levelKind, numbersOf, pct } from "./hmi";
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
/** 5 分鐘變化至少是正常範圍寬度的幾成才顯示（小抖動不顯示，免得每張卡都有一行數字） */
const DELTA_SHOW_RATIO = 0.3;

/** 固定刻度的迷你走勢：灰色帶是正常範圍、虛線是警戒值，位置永遠不變 */
function Sparkline({ spec, values }: { spec: TagSpec; values: number[] }) {
  if (values.length < 2) {
    return <svg className="hmi-spark" aria-hidden="true" />;
  }
  const { lo, hi } = fixedWindow(spec, values);
  const y = (v: number) => ((hi - v) / (hi - lo)) * SPARK_H;
  const pts = values
    .map((v, i) => `${((i / (values.length - 1)) * 200).toFixed(1)},${y(v).toFixed(2)}`)
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
      <rect x={0} y={bandTop} width={200} height={Math.max(0, bandBottom - bandTop)} fill="var(--band)" />
      {showWarn && (
        <line
          x1={0} x2={200} y1={y(spec.warning)} y2={y(spec.warning)}
          stroke="var(--warn-mark)" strokeWidth={1} strokeDasharray="4 3" vectorEffect="non-scaling-stroke"
        />
      )}
      <polyline points={pts} fill="none" stroke="var(--pen)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** 卡片上那一句話：現在正不正常、要不要擔心 */
function statusLine(spec: TagSpec, level: Level | null, trend: TagTrend | undefined): string {
  if (level === null) return "等待資料";
  const fmt = (v: number) => v.toFixed(spec.decimals).replace(/\.0+$/, "");
  if (level === "danger") return `超過危險值 ${fmt(spec.danger)}`;
  if (level === "warning") {
    return isForecast(trend) && trend.target === "danger"
      ? `超過警戒值，${etaText(trend.eta_s)}後到危險 ${fmt(spec.danger)}`
      : `超過警戒值 ${fmt(spec.warning)}`;
  }
  if (isForecast(trend)) {
    return `持續上升，${etaText(trend.eta_s)}後到${LINE_TEXT[trend.target]} ${fmt(trend.threshold ?? spec[trend.target])}`;
  }
  if (level === "high") return `偏高（正常 ${fmt(spec.normal[0])}–${fmt(spec.normal[1])}）`;
  if (level === "low") return `偏低（正常 ${fmt(spec.normal[0])}–${fmt(spec.normal[1])}）`;
  return "正常";
}

function AnalogIndicator({
  spec,
  value,
  history,
  trend,
  onOpen,
}: {
  spec: TagSpec;
  value: number | null;
  history: number[];
  trend?: TagTrend;
  onOpen?: () => void;
}) {
  const [scaleMin, scaleMax] = spec.scale;
  const level: Level | null = value === null ? null : evaluate(spec, value);
  const kind = levelKind(level);
  const forecast = isForecast(trend);
  const p = (v: number) => pct(scaleMin, scaleMax, v);
  const valueText = value === null ? "—" : value.toFixed(spec.decimals);

  const delta = value !== null && history.length > 1 ? value - history[0] : null;
  const band = spec.normal[1] - spec.normal[0];
  const showDelta = delta !== null && Math.abs(delta) >= band * DELTA_SHOW_RATIO;

  const cardClass = [
    "ind",
    onOpen ? "hmi-card" : "",
    kind === "warning" || kind === "danger" ? `hmi-st-${kind}` : forecast ? "hmi-st-forecast" : kind === "dev" ? "hmi-st-dev" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const lineClass =
    kind === "danger" ? "danger" : kind === "warning" || forecast ? "warning" : kind === "dev" ? "dev" : "";

  return (
    <div
      className={cardClass}
      onClick={onOpen}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onKeyDown={onOpen ? (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen()) : undefined}
      aria-label={onOpen ? `${spec.label}，${valueText} ${spec.unit}，${statusLine(spec, level, trend)}。按下看詳細趨勢` : undefined}
      title={`${spec.tag_no} ${spec.label}｜正常 ${spec.normal[0]}–${spec.normal[1]}、警戒 ${spec.warning}、危險 ${spec.danger} ${spec.unit}`}
    >
      <div className="hmi-name">{spec.label}</div>

      <div className="hmi-val">
        <span className={`hmi-num ${kind === "danger" || kind === "warning" ? kind : ""}`}>{valueText}</span>
        <span className="hmi-u">{spec.unit}</span>
      </div>

      <div className={`hmi-status ${lineClass}`}>
        {kind === "danger" && <DangerIcon size={14} />}
        {kind === "warning" && <WarningIcon size={14} />}
        {forecast && kind !== "warning" && kind !== "danger" && <span aria-hidden="true">↗</span>}
        <span>{statusLine(spec, level, trend)}</span>
      </div>
      {/* 固定佔一行：有沒有這行，各卡片的指示條都對齊在同一高度 */}
      <div className="hmi-delta" aria-hidden={!showDelta}>
        {showDelta && delta !== null
          ? `比 5 分鐘前${delta >= 0 ? "高" : "低"} ${Math.abs(delta).toFixed(spec.decimals)} ${spec.unit}`
          : "\u00a0"}
      </div>

      <div className="hmi-bar" aria-hidden="true">
        <div className="hmi-track">
          <div
            className="hmi-zone normal"
            style={{ left: `${p(spec.normal[0])}%`, width: `${p(spec.normal[1]) - p(spec.normal[0])}%` }}
          />
          <div
            className={`hmi-zone warn${kind === "warning" ? " on" : ""}`}
            style={{ left: `${p(spec.warning)}%`, width: `${p(spec.danger) - p(spec.warning)}%` }}
          />
          <div className={`hmi-zone danger${kind === "danger" ? " on" : ""}`} style={{ left: `${p(spec.danger)}%` }} />
        </div>
        {value !== null && <div className={`hmi-ptr ${kind}`} style={{ left: `${p(value)}%` }} />}
      </div>
      {/* 刻度兩端：指示條的最小值與最大值 */}
      <div className="hmi-scale" aria-hidden="true">
        <span>{scaleMin}</span>
        <span>{scaleMax}</span>
      </div>

      <Sparkline spec={spec} values={history} />
      {onOpen && <span className="hmi-more" aria-hidden="true">看趨勢 ›</span>}
    </div>
  );
}

/** 製程簡圖上的儀表：圓圈＋代號，下面直接標名稱與目前值 */
function Instrument({ spec, value, trend }: { spec: TagSpec; value: number | null; trend?: TagTrend }) {
  const lv = value === null ? "normal" : evaluate(spec, value);
  const forecast = isForecast(trend);
  const cls =
    lv === "danger" ? "inst danger"
    : lv === "warning" ? "inst warning"
    : forecast ? "inst forecast"
    : lv === "high" || lv === "low" ? "inst dev"
    : "inst";
  const valCls = lv === "danger" ? "danger" : lv === "warning" || forecast ? "warning" : "";
  return (
    <div className="equip">
      <div className={cls} title={`${spec.tag_no} ${spec.label}`}>{instrumentCode(spec)}</div>
      <span className="cap">{spec.label}</span>
      <span className={`inst-val ${valCls}`}>
        {value === null ? "—" : value.toFixed(spec.decimals)}
        <span className="inst-u"> {spec.unit}</span>
        {forecast && <span aria-label="持續上升"> ↗</span>}
      </span>
    </div>
  );
}

function Pipe({ grow = 1 }: { grow?: number }) {
  return <div className="pipe" style={{ flexGrow: grow }} aria-hidden="true" />;
}

// 壓力計裝在泵浦出口；其餘儀表（轉速、溫度、振動、電流）都量測馬達本體
const PUMP_SIDE_CODES = new Set(["PT", "FT"]);

export default function ProcessOverview({ data, profile, history, onOpenTrend }: Props) {
  const running = data ? Boolean(data.motor_enable) : null;
  const keys = tagKeys(profile);
  const trend: TrendInfo | undefined = data?.trend;
  const motorSide: TagKey[] = keys.filter((k) => !PUMP_SIDE_CODES.has(instrumentCode(profile.tags[k])));
  const pumpSide: TagKey[] = keys.filter((k) => PUMP_SIDE_CODES.has(instrumentCode(profile.tags[k])));

  const recent = useMemo(() => {
    if (!history || history.length === 0) return [];
    const end = history[history.length - 1].timestamp;
    return history.filter((pt) => pt.timestamp >= end - SPARK_SECONDS);
  }, [history]);

  return (
    <section className="panel" aria-labelledby="proc-h">
      <div className="panel-head">
        <h2 id="proc-h" className="panel-title">製程與即時值</h2>
        <span className="muted">點卡片看詳細趨勢</span>
      </div>

      <div className="process" aria-label="製程流向：馬達帶動泵浦，經壓力計到下游閥門">
        <div className="equip">
          <div className={`shape round${running ? " on" : ""}`}>M</div>
          <span className="cap">馬達</span>
          <span className="inst-val">{running === null ? "—" : running ? "運轉中" : "停止"}</span>
        </div>
        {motorSide.map((k) => (
          <div key={k} style={{ display: "contents" }}>
            <Pipe />
            <Instrument spec={profile.tags[k]} value={getValue(data, k)} trend={trend?.tags[k]} />
          </div>
        ))}
        <Pipe />
        <div className="equip">
          <div className="shape square">P</div>
          <span className="cap">泵浦</span>
          <span className="inst-val">&nbsp;</span>
        </div>
        {pumpSide.map((k) => (
          <div key={k} style={{ display: "contents" }}>
            <Pipe grow={2} />
            <Instrument spec={profile.tags[k]} value={getValue(data, k)} trend={trend?.tags[k]} />
          </div>
        ))}
        <Pipe grow={2} />
        <div className="equip">
          <div className="valve">
            <svg width="64" height="46" viewBox="0 0 56 40" role="img" aria-label="下游閥門">
              <path d="M4 6 L28 20 L4 34 Z M52 6 L28 20 L52 34 Z" fill="var(--equip)" stroke="var(--text)" strokeWidth="2" />
            </svg>
          </div>
          <span className="cap">閥門</span>
          <span className="inst-val">&nbsp;</span>
        </div>
        <Pipe />
        <div className="equip">
          <span className="cap outlet">出口 →</span>
        </div>
      </div>

      <div className="indicators">
        {keys.map((k) => (
          <AnalogIndicator
            key={k}
            spec={profile.tags[k]}
            value={getValue(data, k)}
            history={numbersOf(recent, k)}
            trend={trend?.tags[k]}
            onOpen={onOpenTrend ? () => onOpenTrend(k) : undefined}
          />
        ))}
      </div>
    </section>
  );
}
