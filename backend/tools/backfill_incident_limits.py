"""
幫 migration 010 之前的舊事件補上「當時的門檻」

門檻每次在畫面上修改都記在 audit_log（limits.update／limits.reset，含修改前後的值），
所以可以推回任何時間點的門檻：
  - 事件開始前最後一次修改的「修改後」值
  - 事件開始前沒有修改過，就用之後第一次修改的「修改前」值
  - 從來沒改過，就是現行門檻
事件進行中有修改紀錄的，limits_changed 設成 true。
補完會更新 updated_at，雲端同步下一輪會把門檻一起送上去。

用法（在 backend 資料夾，先執行 migration 010）：
    python -m tools.backfill_incident_limits --dry-run   只列出會補什麼（只列跟現行不同的）
    python -m tools.backfill_incident_limits             寫入
"""
import argparse
import asyncio
import json
import sys
from bisect import bisect_right
from collections import defaultdict
from zoneinfo import ZoneInfo

from core import limits
from core.device_profile import base_device_id, get_profile
from core.incidents import diff_text, limit_diffs
from db.anomaly_writer import has_limit_columns
from db.pool import acquire, close_pool

FIELDS = ("normal", "warning", "danger")
TW = ZoneInfo("Asia/Taipei")


def _pick(d: dict | None) -> dict | None:
    if not isinstance(d, dict) or not all(k in d for k in FIELDS):
        return None
    return {"normal": list(d["normal"]), "warning": d["warning"], "danger": d["danger"]}


def build_history(rows) -> dict[tuple[str, str], list[tuple]]:
    """audit_log → {(型號, 訊號): [(時間, 修改前, 修改後), …]}（依時間排序）"""
    hist: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for r in rows:
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else (p or {})
        before, after = _pick(p.get("before")), _pick(p.get("after"))
        if not p.get("tag") or before is None or after is None:
            continue
        hist[(base_device_id(p.get("device_id", "")), p["tag"])].append((r["time"], before, after))
    for v in hist.values():
        v.sort(key=lambda x: x[0])
    return hist


def limits_at(hist: list[tuple], at, current: dict) -> dict:
    """某個時間點的門檻"""
    if not hist:
        return current
    times = [h[0] for h in hist]
    i = bisect_right(times, at)
    return hist[i - 1][2] if i > 0 else hist[0][1]


def changed_between(hist: list[tuple], start, end) -> bool:
    return any(start < h[0] <= end for h in hist)


def snapshot_for(device_id: str, started_at, ended_at, hist_all: dict) -> tuple[dict, bool]:
    profile = get_profile(device_id) or {"tags": {}}
    model = base_device_id(device_id)
    tags, changed = {}, False
    for tag, spec in profile["tags"].items():
        cur = {"normal": list(spec["normal"]), "warning": spec["warning"], "danger": spec["danger"]}
        h = hist_all.get((model, tag), [])
        tags[tag] = limits_at(h, started_at, cur)
        changed |= changed_between(h, started_at, ended_at)
    return {"version": "backfill", "tags": tags}, changed


async def main() -> int:
    ap = argparse.ArgumentParser(description="舊事件補上當時的門檻")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    async with acquire() as conn:
        if not await has_limit_columns(conn):
            print("anomaly_incidents 還沒有 limits 欄位：請先執行 db/migrations/010_incident_limits.sql")
            return 1
        await limits.load(conn)      # 現行門檻（含畫面上改過的）
        audits = await conn.fetch("""
            SELECT time, params FROM audit_log
            WHERE action IN ('limits.update', 'limits.reset') AND result = 'ok' ORDER BY time
        """)
        incidents = await conn.fetch("""
            SELECT id, device_id, started_at, ended_at FROM anomaly_incidents
            WHERE limits IS NULL ORDER BY id
        """)
    hist = build_history(audits)
    print(f"門檻修改紀錄 {len(audits)} 筆；沒有當時門檻的事件 {len(incidents)} 筆")

    updates, shown = [], 0
    for r in incidents:
        snap, changed = snapshot_for(r["device_id"], r["started_at"], r["ended_at"], hist)
        updates.append((r["id"], json.dumps(snap, ensure_ascii=False), changed))
        diffs = limit_diffs(get_profile(r["device_id"]) or {}, snap)
        if diffs or changed:
            shown += 1
            note = "、".join(diff_text(d) for d in diffs) or "（開始時跟現行相同）"
            print(f"  事件 {r['id']}（{r['started_at'].astimezone(TW):%m-%d %H:%M}）：{note}{'；進行中被修改' if changed else ''}")
    print(f"其中 {shown} 筆當時門檻跟現行不同或進行中被修改，其餘 {len(updates) - shown} 筆跟現行相同")

    if args.dry_run:
        print("（試跑，沒有寫入）")
        return 0
    async with acquire() as conn:
        await conn.executemany("""
            UPDATE anomaly_incidents SET limits = $2::jsonb, limits_changed = $3, updated_at = now()
            WHERE id = $1 AND limits IS NULL
        """, updates)
    print(f"已補上 {len(updates)} 筆（雲端同步下一輪會一起送上去）")
    return 0


async def _run() -> int:
    try:
        return await main()
    finally:
        await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
