import type { PlcData } from "../hooks/useWebSocket";
import {
  LEVEL_TEXT, evaluate, getValue, instrumentCode, tagKeys,
  type DeviceProfile, type Level, type TagKey, type TagSpec,
} from "../deviceProfile";
import { DangerIcon, WarningIcon } from "./Icons";

interface Props {
  data: PlcData | null;
  profile: DeviceProfile;
}

const pct = (min: number, max: number, v: number) =>
  Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));

function AnalogIndicator({ spec, value }: { spec: TagSpec; value: number | null }) {
  const [scaleMin, scaleMax] = spec.scale;
  const level: Level | null = value === null ? null : evaluate(spec, value);
  const p = (v: number) => `${pct(scaleMin, scaleMax, v)}%`;
  const valueText = value === null ? "—" : value.toFixed(spec.decimals);
  const stateClass = level ?? "normal";

  return (
    <div className="ind">
      <div className="ind-head">
        <span className="muted" style={{ fontSize: 13 }}>
          <span className="mono">{spec.tag_no}</span> {spec.label}
        </span>
        <span className={`ind-state ${stateClass}`}>
          {level === "danger" && <DangerIcon size={12} />}
          {(level === "warning" || level === "high") && <WarningIcon size={12} />}
          {level ? LEVEL_TEXT[level] : "等待資料"}
        </span>
      </div>
      <div className="ind-value">
        <span className={`num ${level === "danger" || level === "warning" ? level : ""}`}>{valueText}</span>
        <span className="muted" style={{ fontSize: 13 }}>{spec.unit}</span>
      </div>
      <div
        className="bar"
        role="img"
        aria-label={`${spec.label} ${valueText} ${spec.unit}，${level ? LEVEL_TEXT[level] : "等待資料"}，正常範圍 ${spec.normal[0]} 到 ${spec.normal[1]}，警戒 ${spec.warning}，危險 ${spec.danger}`}
      >
        <div className="band" style={{ left: p(spec.normal[0]), width: `calc(${p(spec.normal[1])} - ${p(spec.normal[0])})` }} />
        <div className="mark warn" style={{ left: p(spec.warning) }} />
        <div className="mark danger" style={{ left: p(spec.danger) }} />
        {value !== null && <div className={`pointer ${stateClass}`} style={{ left: p(value) }} />}
      </div>
      <div className="scale">
        <span>{scaleMin}</span>
        <span>正常 {spec.normal[0]}–{spec.normal[1]}</span>
        <span>警戒 {spec.warning} · 危險 {spec.danger}</span>
        <span>{scaleMax}</span>
      </div>
    </div>
  );
}

function Instrument({ spec, value }: { spec: TagSpec; value: number | null }) {
  const lv = value === null ? "normal" : evaluate(spec, value);
  const cls = lv === "danger" ? "inst danger" : lv === "warning" || lv === "high" ? "inst warning" : "inst";
  return (
    <div className="equip">
      <div className={cls} title={`${spec.tag_no} ${spec.label}`}>{instrumentCode(spec)}</div>
      <span className="cap">{spec.label}</span>
    </div>
  );
}

// 壓力計裝在泵浦出口；其餘儀表（轉速、溫度、振動、電流）都量測馬達本體
const PUMP_SIDE_CODES = new Set(["PT", "FT"]);

export default function ProcessOverview({ data, profile }: Props) {
  const running = data ? Boolean(data.motor_enable) : null;
  const keys = tagKeys(profile);
  const motorSide: TagKey[] = keys.filter((k) => !PUMP_SIDE_CODES.has(instrumentCode(profile.tags[k])));
  const pumpSide: TagKey[] = keys.filter((k) => PUMP_SIDE_CODES.has(instrumentCode(profile.tags[k])));

  return (
    <section className="panel" aria-labelledby="proc-h">
      <div className="panel-head">
        <h2 id="proc-h" className="panel-title">製程簡圖與即時值</h2>
      </div>

      <div className="process" aria-label="製程流向：馬達帶動泵浦，經壓力計到下游閥門">
        <div className="equip">
          <div className="shape round">M</div>
          <span className="cap">馬達 · {running === null ? "—" : running ? "運轉中" : "停止"}</span>
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
          <svg width="56" height="40" viewBox="0 0 56 40" role="img" aria-label="下游閥門">
            <path d="M4 6 L28 20 L4 34 Z M52 6 L28 20 L52 34 Z" fill="var(--equip)" stroke="var(--text)" strokeWidth="2" />
          </svg>
          <span className="cap">下游閥門</span>
        </div>
        <div className="pipe" style={{ width: 32 }} aria-hidden="true" />
        <span className="cap" style={{ marginBottom: 22 }}>出口</span>
      </div>

      <div className="indicators">
        {keys.map((k) => (
          <AnalogIndicator key={k} spec={profile.tags[k]} value={getValue(data, k)} />
        ))}
      </div>
    </section>
  );
}
