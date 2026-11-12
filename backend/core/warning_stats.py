"""
趨勢預警紀錄的統計與格式（給 /api/trend/warnings 與 MCP 共用；不依賴 PLC 迴圈，雲端 Lambda 也能用）
"""


def summarize(rows: list[dict]) -> dict:
    """
    命中率＝真的到了 ÷（真的到了＋自己退了）；中途停機（interrupted）與進行中的不算
    提前量＝到了的時間 − 預警開始；預估誤差＝提前量 − 開始時的預估（正數＝實際比預估晚到）
    """
    count = {k: 0 for k in ("ongoing", "reached", "cleared", "interrupted")}
    for r in rows:
        count[r["outcome"]] = count.get(r["outcome"], 0) + 1
    reached = [r for r in rows if r["outcome"] == "reached" and r["lead_s"] is not None]
    judged = count["reached"] + count["cleared"]
    return {
        "count": count,
        "hit_rate": round(count["reached"] / judged, 3) if judged else None,
        "avg_lead_s": round(sum(r["lead_s"] for r in reached) / len(reached)) if reached else None,
        "avg_eta_error_s": round(sum(r["lead_s"] - r["first_eta_s"] for r in reached) / len(reached)) if reached else None,
    }


def row_view(r) -> dict:
    lead = int((r["reached_at"] - r["started_at"]).total_seconds()) if r["reached_at"] else None
    return {
        "id": r["id"],
        "device_id": r["device_id"],
        "tag_name": r["tag_name"],
        "target_level": r["target_level"],
        "threshold": r["threshold"],
        "started_at": r["started_at"].isoformat(),
        "ended_at": r["ended_at"].isoformat(),
        "first_eta_s": r["first_eta_s"],
        "min_eta_s": r["min_eta_s"],
        "slope_per_min": r["slope_per_min"],
        "value_at_start": r["value_at_start"],
        "value_at_end": r["value_at_end"],
        "outcome": r["outcome"],
        "reached_at": r["reached_at"].isoformat() if r["reached_at"] else None,
        "lead_s": lead,
        "incident_id": r["incident_id"],
    }
