"""
把 anomaly_events（每秒一列）整理成 anomaly_incidents（一次事件一列）

規則與前端舊版 groupIncidents 相同：
- 同一台設備，間隔超過 60 秒就算新事件
- 診斷取「最嚴重時刻」那一列：各訊號等級加總最高，同分取較晚的那一列

用法（在 backend 資料夾、conda iot-scada 環境下執行）：
    python -m db.backfill_incidents            # 事件表是空的才會執行
    python -m db.backfill_incidents --force    # 清空事件表後全部重算
"""
import argparse
import asyncio
import json
import os

import asyncpg

from core.device_profile import get_profile
from core.incidents import INCIDENT_GAP_SECONDS as GAP_SECONDS, LEVEL_ORDER, severity_and_level

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:iotscada123@localhost:5435/iotscada"
)

LEGACY_COLUMNS = ("motor_speed", "temperature", "pressure")


def readings_of(row) -> dict:
    """優先用 readings（JSONB）；舊紀錄沒有 readings 時用三個舊欄位"""
    raw = row["readings"]
    if raw:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    else:
        data = {c: row[c] for c in LEGACY_COLUMNS}
    return {
        k: float(v) for k, v in data.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }



def summarize(group: list) -> dict:
    device_id = group[0]["device_id"]
    profile = get_profile(device_id)
    tags = set(profile["tags"]) if profile else None

    peak_max: dict[str, float] = {}
    peak_min: dict[str, float] = {}
    worst = "normal"
    max_sev = 0
    best_sev = -1
    analysis = None
    source = None

    for r in group:
        rd = readings_of(r)
        for k, v in rd.items():
            if tags is not None and k not in tags:
                continue
            peak_max[k] = max(peak_max.get(k, v), v)
            peak_min[k] = min(peak_min.get(k, v), v)
        sev, lv = severity_and_level(device_id, rd)
        max_sev = max(max_sev, sev)
        if LEVEL_ORDER.index(lv) > LEVEL_ORDER.index(worst):
            worst = lv
        if r["ai_analysis"] and sev >= best_sev:
            best_sev = sev
            analysis = r["ai_analysis"]
            source = r["ai_source"]

    scores = [r["anomaly_score"]
              for r in group if r["anomaly_score"] is not None]
    return {
        "device_id": device_id,
        "started_at": group[0]["time"],
        "ended_at": group[-1]["time"],
        "records": len(group),
        "level": worst,
        "severity": max_sev,
        "peak": json.dumps({"max": peak_max, "min": peak_min}),
        "min_score": min(scores) if scores else None,
        "ai_analysis": analysis,
        "ai_source": source if source is None or isinstance(source, str) else json.dumps(source),
    }


async def main(force: bool):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing = await conn.fetchval("SELECT count(*) FROM anomaly_incidents")
        if existing and not force:
            print(f"[Backfill] 事件表已有 {existing} 筆，未執行；要重算請加 --force")
            return

        async with conn.transaction():
            if force:
                await conn.execute("UPDATE anomaly_events SET incident_id = NULL WHERE incident_id IS NOT NULL")
                await conn.execute("TRUNCATE anomaly_incidents RESTART IDENTITY")

            rows = await conn.fetch("""
                SELECT time, device_id, anomaly_score, motor_speed, temperature, pressure,
                       ai_analysis, readings, ai_source
                FROM anomaly_events
                ORDER BY device_id, time
            """)

            groups: list[list] = []
            for r in rows:
                last = groups[-1] if groups else None
                if (
                    last
                    and last[-1]["device_id"] == r["device_id"]
                    and (r["time"] - last[-1]["time"]).total_seconds() <= GAP_SECONDS
                ):
                    last.append(r)
                else:
                    groups.append([r])

            for g in groups:
                inc = summarize(g)
                inc_id = await conn.fetchval("""
                    INSERT INTO anomaly_incidents
                        (device_id, started_at, ended_at, records, level, severity,
                         peak, min_score, ai_analysis, ai_source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb)
                    RETURNING id
                """, inc["device_id"], inc["started_at"], inc["ended_at"], inc["records"],
                    inc["level"], inc["severity"], inc["peak"], inc["min_score"],
                    inc["ai_analysis"], inc["ai_source"])
                await conn.execute("""
                    UPDATE anomaly_events SET incident_id = $1
                    WHERE device_id = $2 AND time BETWEEN $3 AND $4
                """, inc_id, inc["device_id"], inc["started_at"], inc["ended_at"])

            print(f"[Backfill] ✅ 完成：{len(rows)} 筆每秒紀錄 → {len(groups)} 個事件")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="清空事件表後全部重算")
    asyncio.run(main(parser.parse_args().force))
