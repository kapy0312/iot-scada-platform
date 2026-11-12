// 頁面最上方的告警橫幅：有警戒或危險時才出現，切到其他分頁也看得到
import type { Alarm } from "../hooks/useAlarms";
import { alarmTag } from "../hooks/useAlarmAttention";
import { DangerIcon, WarningIcon } from "./Icons";

interface Props {
  alarms: Alarm[];              // 已排序：危險優先
  flashing: boolean;            // 有沒有還在閃的告警
  canAcknowledge: boolean;      // 有沒有還沒按「知道了」的危險
  onAcknowledge: () => void;
  onLocate: (tag: string) => void;
}

export default function AlarmBanner({ alarms, flashing, canAcknowledge, onAcknowledge, onLocate }: Props) {
  if (alarms.length === 0) return null;
  const top = alarms[0];
  const danger = top.level === "danger";
  const tag = alarmTag(top);
  const locatable = tag !== "ml";
  const others = alarms.length - 1;

  return (
    <div
      className={`alarm-banner ${danger ? "danger" : "warning"}${flashing ? " flash" : ""}`}
      role="alert"
      aria-live="assertive"
    >
      <span className="ab-level">
        {danger ? <DangerIcon size={20} /> : <WarningIcon size={20} />}
        {danger ? "危險" : "警戒"}
      </span>
      {locatable ? (
        <button type="button" className="ab-text" onClick={() => onLocate(tag)} title="捲到這個訊號的卡片">
          {top.text}
        </button>
      ) : (
        <span className="ab-text">{top.text}</span>
      )}
      {others > 0 && <span className="ab-more">另有 {others} 項</span>}
      {canAcknowledge && (
        <button type="button" className="btn ab-ack" onClick={onAcknowledge}>
          知道了
        </button>
      )}
    </div>
  );
}
