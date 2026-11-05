// ISA-101：告警一律「形狀 + 文字」，不只靠顏色（菱形＝危險，三角形＝警戒）
export function DangerIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 1 L15 8 L8 15 L1 8 Z" fill="currentColor" />
    </svg>
  );
}

export function WarningIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 1 L15 14 L1 14 Z" fill="currentColor" />
    </svg>
  );
}
