"""
雲端同步檢查與調整

用法（在 backend 資料夾）：
    python -m tools.sync_admin status              同步到哪裡、落後多久、本地自動整理是否正常
    python -m tools.sync_admin compare --hours 24  逐小時比對本地與雲端的筆數（要設 TIGER_EDGE_URL）
    python -m tools.sync_admin rewind --hours 6    把指標往回調 6 小時，下一輪重送這段
    python -m tools.sync_admin rewind --all        從頭重送（雲端表重建後用）

補舊資料（超過 2 小時才寫進原始表的資料，自動整理不會重算）時：
    python -m tools.sync_admin refresh --start "2026-11-01 08:00" --end "2026-11-01 12:00"
這會重算該時段的彙整，並把指標調回那個時間點
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from core.config import ConfigError, get_cloud_url, get_database_url
from sync import cloud_sync as cs

TW = ZoneInfo("Asia/Taipei")


def fmt(t: datetime | None) -> str:
    return t.astimezone(TW).strftime("%Y-%m-%d %H:%M:%S") if t else "—"


def parse_tw(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TW)


async def local_conn() -> asyncpg.Connection:
    return await asyncpg.connect(get_database_url(), timeout=5)


async def cmd_status() -> int:
    conn = await local_conn()
    try:
        upper = await cs.refresh_upper(conn)
        rows = await conn.fetch("SELECT * FROM sync_state ORDER BY target")
        now = await conn.fetchval("SELECT now()")
    finally:
        await conn.close()
    print(f"本地彙整已整理到：{fmt(upper)}" + ("（自動整理還沒成功跑過）" if upper is None else
                                       f"（{int((now - upper).total_seconds())} 秒前）"))
    if not rows:
        print("還沒有同步紀錄（backend 還沒啟用同步，或還沒跑完第一輪）")
    for r in rows:
        lag = int((now - r["watermark"]).total_seconds())
        print(f"\n[{r['target']}]")
        print(f"  已同步到：{fmt(r['watermark'])}（落後 {lag} 秒）")
        print(f"  最後成功：{fmt(r['last_ok_at'])}，該批 {r['last_rows']} 筆")
        if r["last_error"]:
            print(f"  最後錯誤：{r['last_error']}")
    return 0


async def cmd_compare(hours: int) -> int:
    local = await local_conn()
    try:
        cloud = await asyncpg.connect(get_cloud_url("writer"), timeout=15)
    except (TimeoutError, OSError) as e:
        await local.close()
        print(f"[失敗] 連不上 Tiger（{type(e).__name__}）：服務可能暫停，或網路擋了連線")
        return 1
    try:
        wm = await local.fetchval("SELECT watermark FROM sync_state WHERE target = $1", cs.T_MEAS)
        if wm is None:
            print("還沒有同步紀錄")
            return 1
        # 比對到同步指標為止（包含還沒結束的這一小時，只算已同步的部分）
        end = wm
        start = wm.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)
        q = """
            SELECT date_trunc('hour', bucket) AS h, count(*) AS n, sum(samples) AS s
            FROM {t} WHERE bucket >= $1 AND bucket < $2 AND NOT (device_id LIKE ANY($3::text[]))
            GROUP BY 1 ORDER BY 1
        """
        pats = cs.exclude_patterns()
        a = {r["h"]: (r["n"], r["s"]) for r in await local.fetch(q.format(t=cs.CAGG_NAME), start, end, pats)}
        b = {r["h"]: (r["n"], r["s"]) for r in await cloud.fetch(q.format(t="measurements_10s"), start, end, pats)}
    finally:
        await local.close()
        await cloud.close()
    bad = 0
    print(f"{'小時（台灣時間）':<20}{'本地筆數':>10}{'雲端筆數':>10}  結果")
    for h in sorted(set(a) | set(b)):
        la, cl = a.get(h, (0, 0)), b.get(h, (0, 0))
        same = la == cl
        bad += not same
        partial = "（到 " + fmt(end)[11:16] + "）" if h + timedelta(hours=1) > end else ""
        print(f"{fmt(h)[:13] + ':00' + partial:<20}{la[0]:>10}{cl[0]:>10}  {'✅' if same else '❌ 不一致'}")
    print(f"\n比對 {fmt(start)} ～ {fmt(end)}：" + ("全部一致" if bad == 0 else f"{bad} 個小時不一致"))
    return 0 if bad == 0 else 1


async def _rewind_to(conn, target: str | None, to: datetime | None) -> None:
    """拿同步鎖再改指標，避免剛好被正在跑的同步覆蓋"""
    await conn.execute("SELECT pg_advisory_lock($1)", cs.LOCK_KEY)
    try:
        if to is None:
            await conn.execute("DELETE FROM sync_state WHERE $1::text IS NULL OR target = $1", target)
        else:
            await conn.execute("""
                UPDATE sync_state SET watermark = LEAST(watermark, $2), updated_at = now()
                WHERE $1::text IS NULL OR target = $1
            """, target, to)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", cs.LOCK_KEY)


async def cmd_rewind(hours: float | None, all_: bool, target: str | None) -> int:
    conn = await local_conn()
    try:
        if all_:
            await _rewind_to(conn, target, None)
            print("[成功] 已清除同步指標，下一輪會從最早的資料開始重送（資料多時要好幾輪才追上）")
        else:
            to = await conn.fetchval("SELECT now() - $1::interval", timedelta(hours=hours))
            await _rewind_to(conn, target, to)
            print(f"[成功] 指標已調回 {fmt(to)}，下一輪開始重送")
    finally:
        await conn.close()
    return 0


async def cmd_refresh(start: datetime, end: datetime) -> int:
    conn = await local_conn()
    try:
        print(f"重算彙整 {fmt(start)} ～ {fmt(end)} …")
        # 不用參數：refresh_continuous_aggregate 不能在交易裡執行，不帶參數才會走單純的指令模式
        await conn.execute(f"CALL refresh_continuous_aggregate('{cs.CAGG_NAME}', "
                           f"'{start.isoformat()}'::timestamptz, '{end.isoformat()}'::timestamptz)")
        await _rewind_to(conn, cs.T_MEAS, start)
    finally:
        await conn.close()
    print("[成功] 已重算並把指標調回起點，下一輪會重送這段")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="雲端同步檢查與調整")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    c = sub.add_parser("compare")
    c.add_argument("--hours", type=int, default=24)
    r = sub.add_parser("rewind")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--hours", type=float)
    g.add_argument("--all", action="store_true")
    r.add_argument("--target", choices=cs.TARGETS, help="只調其中一個；不指定就兩個都調")
    f = sub.add_parser("refresh")
    f.add_argument("--start", required=True, type=parse_tw, help='台灣時間，例如 "2026-11-01 08:00"')
    f.add_argument("--end", required=True, type=parse_tw)
    args = p.parse_args()
    try:
        if args.cmd == "status":
            return asyncio.run(cmd_status())
        if args.cmd == "compare":
            return asyncio.run(cmd_compare(args.hours))
        if args.cmd == "rewind":
            return asyncio.run(cmd_rewind(args.hours, args.all, args.target))
        return asyncio.run(cmd_refresh(args.start, args.end))
    except ConfigError as e:
        print(f"[失敗] {e}")
        return 1
    except asyncpg.UndefinedTableError as e:
        print(f"[失敗] 找不到資料表（{e}）：請先執行 db/migrations/003_cagg_10s.sql")
        return 1


if __name__ == "__main__":
    sys.exit(main())
