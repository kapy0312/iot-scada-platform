import { useEffect, useMemo, useRef, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceArea,
  ReferenceLine,
} from "recharts";
import type { PlcData } from "../hooks/useWebSocket";
import {
  LEVEL_TEXT,
  evaluate,
  getValue,
  tagKeys,
  type DeviceProfile,
  type LimitsInfo,
  type TagKey,
  type TagSpec,
} from "../deviceProfile";
import LimitsEditor from "./LimitsEditor";
import { autoRange, fixedWindow, levelKind, numbersOf } from "./hmi";
import "./hmi.css";

interface Props {
  /** 要看的訊號；null 代表視窗關閉 */
  tag: TagKey | null;
  history: PlcData[];
  profile: DeviceProfile;
  onClose: () => void;
  /** 門檻設定（沒傳就不顯示編輯區） */
  deviceId?: string;
  limitsInfo?: Record<TagKey, LimitsInfo>;
  canOperate?: boolean;
  onRequireLogin?: () => void;
  onLimitsSaved?: () => void;
}

type Scale = "fixed" | "auto";

const RANGES = [
  { minutes: 2, label: "2 分" },
  { minutes: 5, label: "5 分" },
  { minutes: 10, label: "10 分" },
];

// 縱軸數字寬度、右側門檻標籤寬度；ML 異常條用同樣的左右留白，時間才會對齊
const Y_AXIS_W = 56;
const RIGHT_GUTTER = 76;
const X_AXIS_H = 30; // Recharts XAxis 預設高度
const MIN_LABEL_GAP = 14; // 兩條門檻線距離小於這個像素就把標籤上下錯開

const fmtTime = (t: number) =>
  new Date(t * 1000).toLocaleTimeString("zh-TW", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

/** 把連續判定為異常的時間點合併成區段 */
function anomalySegments(points: PlcData[]): Array<[number, number]> {
  const segs: Array<[number, number]> = [];
  let start: number | null = null;
  let prev: number | null = null;
  for (const p of points) {
    if (p.anomaly?.is_anomaly) {
      if (start === null) start = p.timestamp;
      prev = p.timestamp;
    } else if (start !== null && prev !== null) {
      segs.push([start, prev]);
      start = null;
    }
  }
  if (start !== null && prev !== null) segs.push([start, prev]);
  return segs;
}

/** 門檻線右側的標籤；Recharts 會傳入 viewBox（線段的位置），dy 用來把太近的兩個標籤錯開 */
function EdgeLabel(props: {
  viewBox?: { x: number; y: number; width: number };
  text: string;
  color: string;
  dy?: number;
}) {
  const { viewBox, text, color, dy = 0 } = props;
  if (!viewBox) return null;
  return (
    <text
      x={viewBox.x + viewBox.width + 6}
      y={viewBox.y + dy}
      dominantBaseline="middle"
      fill={color}
      fontSize={11}
    >
      {text}
    </text>
  );
}

function TagLabel({
  spec,
  tag,
  points,
  big,
}: {
  spec: TagSpec;
  tag: TagKey;
  points: PlcData[];
  big: boolean;
}) {
  const values = numbersOf(points, tag);
  const last = values.length ? values[values.length - 1] : null;
  const level = last === null ? null : evaluate(spec, last);
  const kind = levelKind(level);
  return (
    <div className="hmi-tlbl">
      <span className="mono small muted">{spec.tag_no}</span>
      <span style={{ fontSize: big ? 15 : 13 }}>
        {spec.label}（{spec.unit}）
      </span>
      <span className={`hmi-cur ${kind}`} style={{ fontSize: big ? 24 : 15 }}>
        {last === null ? "—" : last.toFixed(spec.decimals)}
      </span>
      {big && level && <span className="small muted">{LEVEL_TEXT[level]}</span>}
    </div>
  );
}

function TagChart({
  spec,
  tag,
  points,
  domain,
  height,
  scale,
  showX,
}: {
  spec: TagSpec;
  tag: TagKey;
  points: PlcData[];
  domain: [number, number];
  height: number;
  scale: Scale;
  showX: boolean;
}) {
  const values = numbersOf(points, tag);
  const { lo, hi } =
    scale === "fixed" ? fixedWindow(spec, values) : autoRange(spec, values);
  const showWarn = spec.warning >= lo && spec.warning <= hi;
  const showDanger = spec.danger >= lo && spec.danger <= hi;
  const axDec = Math.max(0, spec.decimals - 1);

  // 下方留白：有時間軸時由時間軸撐開，沒有時要留 10px，縱軸下限數字才不會被切掉
  const marginTop = 8;
  const marginBottom = showX ? 2 : 10;
  const plotH = height - marginTop - marginBottom - (showX ? X_AXIS_H : 0);
  const gapPx = ((spec.danger - spec.warning) / (hi - lo || 1)) * plotH;
  const tooClose = showWarn && showDanger && gapPx < MIN_LABEL_GAP;

  return (
    <div role="img" aria-label={`${spec.label} 趨勢`}>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart
          data={points}
          syncId="trend-detail"
          margin={{
            top: marginTop,
            right: RIGHT_GUTTER,
            left: 0,
            bottom: marginBottom,
          }}
        >
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={domain}
            hide={!showX}
            tickFormatter={fmtTime}
            tick={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fill: "var(--muted)",
            }}
            stroke="var(--line)"
            minTickGap={64}
          />
          <YAxis
            domain={[lo, hi]}
            ticks={[lo, hi]}
            interval={0}
            allowDataOverflow
            width={Y_AXIS_W}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => v.toFixed(axDec)}
            tick={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fill: "var(--muted)",
            }}
          />
          <ReferenceArea
            y1={spec.normal[0]}
            y2={spec.normal[1]}
            fill="var(--band)"
            fillOpacity={1}
            stroke="none"
            ifOverflow="hidden"
          />
          {showWarn && (
            <ReferenceLine
              y={spec.warning}
              stroke="var(--warn-mark)"
              strokeDasharray="6 4"
              ifOverflow="hidden"
              label={
                <EdgeLabel
                  text={`警戒 ${spec.warning}`}
                  color="var(--warn-mark)"
                  dy={tooClose ? 6 : 0}
                />
              }
            />
          )}
          {showDanger && (
            <ReferenceLine
              y={spec.danger}
              stroke="var(--danger-mark)"
              strokeDasharray="2 3"
              ifOverflow="hidden"
              label={
                <EdgeLabel
                  text={`危險 ${spec.danger}`}
                  color="var(--danger-mark)"
                  dy={tooClose ? -6 : 0}
                />
              }
            />
          )}
          <Tooltip
            labelFormatter={(t) => fmtTime(Number(t))}
            formatter={(v) => [
              `${Number(v).toFixed(spec.decimals)} ${spec.unit}`,
              spec.label,
            ]}
            contentStyle={{
              background: "var(--panel)",
              border: "1px solid var(--line)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--muted)" }}
            itemStyle={{ color: "var(--text)" }}
          />
          <Line
            type="monotone"
            dataKey={(p: PlcData) => getValue(p, tag)}
            name={spec.label}
            stroke="var(--pen)"
            strokeWidth={1.75}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function TrendDialog({
  tag, history, profile, onClose, deviceId, limitsInfo, canOperate = false, onRequireLogin, onLimitsSaved,
}: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [minutes, setMinutes] = useState(5);
  const [scale, setScale] = useState<Scale>("fixed");

  // tag 有值就以 modal 開啟（焦點鎖在視窗內、Esc 可關閉），null 就關閉
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (tag && !d.open) d.showModal();
    if (!tag && d.open) d.close();
  }, [tag]);

  const points = useMemo(() => {
    if (history.length === 0) return [];
    const end = history[history.length - 1].timestamp;
    return history.filter((p) => p.timestamp >= end - minutes * 60);
  }, [history, minutes]);

  const segments = useMemo(() => anomalySegments(points), [points]);
  const domain: [number, number] = points.length
    ? [points[0].timestamp, points[points.length - 1].timestamp]
    : [0, 1];
  const span = domain[1] - domain[0] || 1;
  const covered = points.length > 1 ? Math.round(domain[1] - domain[0]) : 0;
  const coveredText = `${Math.floor(covered / 60)} 分 ${covered % 60} 秒`;
  const mlText = segments.length
    ? `${fmtTime(segments[segments.length - 1][0])} 起`
    : "無";

  const spec = tag ? profile.tags[tag] : undefined;
  const others = tag ? tagKeys(profile).filter((k) => k !== tag) : [];

  return (
    <dialog
      ref={ref}
      className="hmi-dialog"
      aria-labelledby="trend-dlg-h"
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      {tag && spec && (
        <div className="hmi-dlg-body">
          <div className="hmi-dlg-head">
            <div>
              <h2 id="trend-dlg-h" className="hmi-dlg-title">
                <span className="mono">{spec.tag_no}</span> {spec.label}{" "}
                詳細趨勢
              </h2>
              <p className="small muted" style={{ margin: "4px 0 0" }}>
                已累積 {coveredText} 的資料（資料存在瀏覽器，重新整理後會清空）
              </p>
            </div>
            <button type="button" className="btn" onClick={onClose}>
              關閉
            </button>
          </div>

          <div className="hmi-dlg-tools">
            <div className="seg" role="group" aria-label="時間範圍">
              {RANGES.map((r) => (
                <button
                  key={r.minutes}
                  type="button"
                  className="btn"
                  aria-pressed={minutes === r.minutes}
                  onClick={() => setMinutes(r.minutes)}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <div className="seg" role="group" aria-label="縱軸刻度">
              <button
                type="button"
                className="btn"
                aria-pressed={scale === "fixed"}
                onClick={() => setScale("fixed")}
              >
                固定刻度
              </button>
              <button
                type="button"
                className="btn"
                aria-pressed={scale === "auto"}
                onClick={() => setScale("auto")}
              >
                自動縮放
              </button>
            </div>
          </div>

          {points.length < 2 ? (
            <p className="muted" style={{ margin: 0 }}>
              等待資料中（連線後每秒累積一筆）
            </p>
          ) : (
            <>
              <div className="hmi-trow">
                <div className="hmi-tlbl">
                  <span style={{ fontSize: 13 }}>ML 異常判定</span>
                  <span className="mono small muted">{mlText}</span>
                </div>
                <div
                  className="hmi-ml-track"
                  style={{ marginLeft: Y_AXIS_W, marginRight: RIGHT_GUTTER }}
                  role="img"
                  aria-label={
                    segments.length ? `ML 判定異常，${mlText}` : "ML 未判定異常"
                  }
                >
                  {segments.map(([a, b]) => (
                    <div
                      key={a}
                      className="hmi-ml-seg"
                      style={{
                        left: `${((a - domain[0]) / span) * 100}%`,
                        width: `${Math.max(0.4, ((b - a) / span) * 100)}%`,
                      }}
                    />
                  ))}
                </div>
              </div>

              <div className="hmi-trow">
                <TagLabel spec={spec} tag={tag} points={points} big />
                <TagChart
                  spec={spec}
                  tag={tag}
                  points={points}
                  domain={domain}
                  height={260}
                  scale={scale}
                  showX
                />
              </div>

              <p className="hmi-sub">
                對照訊號（時間軸同步，用來判斷哪個訊號先變化）
              </p>
              {others.map((k, i) => (
                <div className="hmi-trow" key={k}>
                  <TagLabel
                    spec={profile.tags[k]}
                    tag={k}
                    points={points}
                    big={false}
                  />
                  <TagChart
                    spec={profile.tags[k]}
                    tag={k}
                    points={points}
                    domain={domain}
                    height={i === others.length - 1 ? 100 : 76}
                    scale={scale}
                    showX={i === others.length - 1}
                  />
                </div>
              ))}
              <p className="small muted" style={{ margin: 0 }}>
                灰色帶＝正常範圍 · 長虛線＝警戒、短虛線＝危險（數值標在右側）·
                斜紋條＝ML 判定異常
              </p>
            </>
          )}
          {deviceId && onRequireLogin && onLimitsSaved && (
            <LimitsEditor
              key={`${tag}-${spec.warning}-${spec.danger}-${spec.normal.join()}-${spec.scale.join()}`}
              deviceId={deviceId}
              tag={tag}
              spec={spec}
              info={limitsInfo?.[tag]}
              current={history.length ? getValue(history[history.length - 1], tag) : null}
              canOperate={canOperate}
              onRequireLogin={onRequireLogin}
              onSaved={onLimitsSaved}
            />
          )}
        </div>
      )}
    </dialog>
  );
}
