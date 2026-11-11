"""
雲端同步：把本地的 10 秒彙整、事件表、每小時 OEE、趨勢預警紀錄送到 Tiger Cloud

類比：像 PLC 用「已傳送到第幾筆」的指標做資料轉送——
- watermark 是指標：它之前的資料都已確認送到雲端
- 每一輪從「指標往回一小段」送到「目前確定整理好的位置」，雲端 commit 成功後才把指標往前推
- 雲端用 upsert（同一筆就覆蓋），所以重送不會變成兩筆

不漏資料：
- 斷線、雲端暫停、backend 重開 → 指標停在原地，恢復後從那裡繼續補（每輪最多 6 小時，追上為止）
- 上限取「本地自動整理（refresh job）最後成功的位置」，不是現在時間：整理落後時不會跳過還沒整理的區間
不重複：
- 雲端已 commit、本地指標還沒更新就斷電 → 下一輪重送同一段，upsert 只會覆蓋成相同的值
晚到的資料：
- 每輪往回多送 15 分鐘（事件表 2 分鐘、每小時 OEE 2 小時），晚到資料讓彙整重算時，修正後的值會再送一次
每小時 OEE（需要本地 migration 006 與雲端 003_oee.sql，任一邊沒有就略過、不影響其他兩個）：
- 只送「整小時已整理完」的資料；送的時候把當時的品號標準週期一起帶上，雲端自己就能算表現率
趨勢預警紀錄（需要本地 migration 007 與雲端 004_trend.sql，任一邊沒有就略過）：
- 跟事件表同一套：依 updated_at 送，往回重疊 2 分鐘，雲端只用較新的版本覆蓋

設定（.env）：
- CLOUD_SYNC_ENABLED=true 才會啟動
- TIGER_EDGE_URL：edge_writer 帳號的連線字串
- SYNC_EXCLUDE_SUFFIXES：不同步的設備後綴，預設 -MOCK（內建亂數模擬）
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg

from core.config import ConfigError, cloud_sync_enabled, get_cloud_url, masked
from db.pool import acquire

BUCKET = timedelta(seconds=10)
CAGG_NAME = "plc_measurements_10s"

T_MEAS = "measurements_10s"
T_INC = "anomaly_incidents"
T_OEE = "oee_hourly"
T_TREND = "trend_warnings"
TARGETS = (T_MEAS, T_INC, T_OEE, T_TREND)

OEE_CAGG = "oee_hourly"
HOUR = timedelta(hours=1)
OEE_MAX_WINDOW = timedelta(days=7)    # 每小時一筆，一批可以送比較長的時間

OVERLAP = {T_MEAS: timedelta(minutes=15), T_INC: timedelta(minutes=2), T_OEE: timedelta(hours=2),
           T_TREND: timedelta(minutes=2)}
MAX_WINDOW = timedelta(hours=6)       # 彙整每輪最多送幾小時（斷線很久時分批追）
INC_BATCH = 2000                      # 事件表每輪最多幾筆
INC_SETTLE = timedelta(seconds=5)     # 事件表只送 5 秒前更新的列，避免還在寫入中的交易被跳過
MAX_BATCHES_PER_CYCLE = 20            # 一輪最多追幾批，避免剛接上時一次佔住太久

INTERVAL = float(os.getenv("SYNC_INTERVAL_SECONDS", "60"))
BACKOFF_MIN, BACKOFF_MAX = 10.0, 300.0
CONNECT_TIMEOUT = 15

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

MEAS_COLS = ["bucket", "device_id", "tag_name", "avg_v", "min_v", "max_v", "samples"]
OEE_COLS = ["bucket", "device_id", "product_id", "run_s", "idle_s", "fault_s", "changeover_s",
            "planned_stop_s", "data_s", "unknown_s", "good", "ng", "samples", "ideal_cycle_s", "ideal_s"]
INC_COLS = ["id", "device_id", "started_at", "ended_at", "records", "level", "severity",
            "peak", "min_score", "ai_analysis", "ai_source", "updated_at"]
TREND_COLS = ["id", "device_id", "tag_name", "target_level", "threshold", "started_at", "ended_at",
              "first_eta_s", "min_eta_s", "slope_per_min", "value_at_start", "value_at_end",
              "outcome", "reached_at", "incident_id", "updated_at"]


def exclude_patterns() -> list[str]:
    """SQL LIKE 用的排除條件：-MOCK → %-MOCK"""
    raw = os.getenv("SYNC_EXCLUDE_SUFFIXES", "-MOCK")
    return [f"%{s.strip()}" for s in raw.split(",") if s.strip()]


# ---------- 純計算（不碰資料庫，方便測試） ----------

def floor_bucket(t: datetime, step: timedelta = BUCKET) -> datetime:
    """往前取整：預設 10 秒（12:00:15 → 12:00:10）；每小時 OEE 用 1 小時（12:35 → 12:00）"""
    secs = (t - EPOCH).total_seconds()
    return EPOCH + timedelta(seconds=(secs // step.total_seconds()) * step.total_seconds())


def plan_window(watermark: datetime, upper: datetime | None, overlap: timedelta,
                max_window: timedelta = MAX_WINDOW) -> tuple[datetime, datetime] | None:
    """
    這一輪要送的區間 [start, end)：
    - start：watermark 往回 overlap（重送最近一段，接住晚到資料造成的修正）
    - end：不超過 upper，也不超過 watermark + max_window（分批追）
    upper 不比 watermark 新 → 沒有新資料，回 None
    """
    if upper is None or upper <= watermark:
        return None
    end = min(upper, watermark + max_window)
    return watermark - overlap, end


# ---------- 本地端 ----------

async def refresh_upper(local, cagg: str = CAGG_NAME, step: timedelta = BUCKET) -> datetime | None:
    """
    本地彙整「確定整理好」到哪裡：自動整理工作最後一次成功的時間 − end_offset，再取整到 bucket 大小
    還沒成功跑過（剛建好、TimescaleDB 背景工作沒開）→ None，這一輪不送彙整
    TimescaleDB 2.31 起 jobs.hypertable_name 改成顯示彙整名稱，舊版是內部表名稱，兩種都比對
    """
    row = await local.fetchrow("""
        SELECT js.last_successful_finish, (j.config->>'end_offset')::interval AS end_offset
        FROM timescaledb_information.continuous_aggregates ca
        JOIN timescaledb_information.jobs j
          ON j.proc_name = 'policy_refresh_continuous_aggregate'
         AND j.hypertable_name IN (ca.view_name, ca.materialization_hypertable_name)
        JOIN timescaledb_information.job_stats js ON js.job_id = j.job_id
        WHERE ca.view_name = $1
        ORDER BY js.last_successful_finish DESC NULLS LAST
        LIMIT 1
    """, cagg)
    if row is None or row["last_successful_finish"] is None:
        return None
    return floor_bucket(row["last_successful_finish"] - row["end_offset"], step)


async def get_watermark(local, target: str) -> datetime | None:
    return await local.fetchval("SELECT watermark FROM sync_state WHERE target = $1", target)


async def set_watermark(local, target: str, watermark: datetime, rows: int) -> None:
    await local.execute("""
        INSERT INTO sync_state (target, watermark, last_ok_at, last_rows, last_error, updated_at)
        VALUES ($1, $2, now(), $3, NULL, now())
        ON CONFLICT (target) DO UPDATE
        SET watermark = EXCLUDED.watermark, last_ok_at = now(), last_rows = EXCLUDED.last_rows,
            last_error = NULL, updated_at = now()
    """, target, watermark, rows)


async def record_error(local, message: str) -> None:
    await local.execute(
        "UPDATE sync_state SET last_error = $1, updated_at = now()", message[:500])


# ---------- 雲端寫入 ----------

async def upsert_measurements(cloud, rows: list) -> None:
    """先整批 COPY 進暫存表，再一次 upsert：跨海連線只要來回幾次，不是一筆一次"""
    async with cloud.transaction():
        await cloud.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _in_meas (
                bucket timestamptz, device_id text, tag_name text,
                avg_v double precision, min_v double precision, max_v double precision, samples integer
            ) ON COMMIT DELETE ROWS
        """)
        await cloud.copy_records_to_table("_in_meas", records=rows, columns=MEAS_COLS)
        await cloud.execute(f"""
            INSERT INTO measurements_10s ({", ".join(MEAS_COLS)})
            SELECT {", ".join(MEAS_COLS)} FROM _in_meas
            ON CONFLICT (device_id, tag_name, bucket) DO UPDATE
            SET avg_v = EXCLUDED.avg_v, min_v = EXCLUDED.min_v, max_v = EXCLUDED.max_v,
                samples = EXCLUDED.samples, synced_at = now()
        """)


async def upsert_incidents(cloud, rows: list) -> None:
    async with cloud.transaction():
        await cloud.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _in_inc (
                id bigint, device_id text, started_at timestamptz, ended_at timestamptz,
                records integer, level text, severity integer, peak jsonb, min_score double precision,
                ai_analysis text, ai_source jsonb, updated_at timestamptz
            ) ON COMMIT DELETE ROWS
        """)
        await cloud.copy_records_to_table("_in_inc", records=rows, columns=INC_COLS)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in INC_COLS if c != "id")
        # 只用較新的版本覆蓋：重送舊的那一版時不會把雲端改回去
        await cloud.execute(f"""
            INSERT INTO anomaly_incidents ({", ".join(INC_COLS)})
            SELECT {", ".join(INC_COLS)} FROM _in_inc
            ON CONFLICT (id) DO UPDATE SET {sets}, synced_at = now()
            WHERE anomaly_incidents.updated_at <= EXCLUDED.updated_at
        """)


async def upsert_oee(cloud, rows: list) -> None:
    async with cloud.transaction():
        await cloud.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _in_oee (
                bucket timestamptz, device_id text, product_id integer,
                run_s double precision, idle_s double precision, fault_s double precision,
                changeover_s double precision, planned_stop_s double precision, data_s double precision,
                unknown_s double precision, good bigint, ng bigint, samples integer,
                ideal_cycle_s double precision, ideal_s double precision
            ) ON COMMIT DELETE ROWS
        """)
        await cloud.copy_records_to_table("_in_oee", records=rows, columns=OEE_COLS)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in OEE_COLS if c not in ("bucket", "device_id", "product_id"))
        await cloud.execute(f"""
            INSERT INTO oee_hourly ({", ".join(OEE_COLS)})
            SELECT {", ".join(OEE_COLS)} FROM _in_oee
            ON CONFLICT (device_id, product_id, bucket) DO UPDATE SET {sets}, synced_at = now()
        """)


async def upsert_trend(cloud, rows: list) -> None:
    async with cloud.transaction():
        await cloud.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _in_trend (
                id bigint, device_id text, tag_name text, target_level text, threshold double precision,
                started_at timestamptz, ended_at timestamptz, first_eta_s integer, min_eta_s integer,
                slope_per_min double precision, value_at_start double precision, value_at_end double precision,
                outcome text, reached_at timestamptz, incident_id bigint, updated_at timestamptz
            ) ON COMMIT DELETE ROWS
        """)
        await cloud.copy_records_to_table("_in_trend", records=rows, columns=TREND_COLS)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in TREND_COLS if c != "id")
        await cloud.execute(f"""
            INSERT INTO trend_warnings ({", ".join(TREND_COLS)})
            SELECT {", ".join(TREND_COLS)} FROM _in_trend
            ON CONFLICT (id) DO UPDATE SET {sets}, synced_at = now()
            WHERE trend_warnings.updated_at <= EXCLUDED.updated_at
        """)


# ---------- 一批 ----------

async def sync_measurements_once(local, cloud, max_window: timedelta = MAX_WINDOW) -> tuple[int, bool]:
    """送一批彙整；回傳 (筆數, 是否還有沒追上的)"""
    wm = await get_watermark(local, T_MEAS)
    if wm is None:
        # 從最早一筆「要同步的設備」開始（只有 MOCK 的早期資料不必一段段翻過）
        first = await local.fetchval(
            f"SELECT min(bucket) FROM {CAGG_NAME} WHERE NOT (device_id LIKE ANY($1::text[]))",
            exclude_patterns())
        if first is None:
            return 0, False
        wm = first
    upper = await refresh_upper(local)
    window = plan_window(wm, upper, OVERLAP[T_MEAS], max_window)
    if window is None:
        return 0, False
    start, end = window
    rows = await local.fetch(f"""
        SELECT {", ".join(MEAS_COLS)} FROM {CAGG_NAME}
        WHERE bucket >= $1 AND bucket < $2 AND NOT (device_id LIKE ANY($3::text[]))
        ORDER BY bucket
    """, start, end, exclude_patterns())
    records = [tuple(r) for r in rows]
    if records:
        await upsert_measurements(cloud, records)
    else:
        # 這段是空白（機台沒開、backend 沒跑）：直接跳到下一筆有資料的位置，不必每 6 小時翻一次
        # 只在「已整理完」的範圍內跳（< upper），所以不會跳過還沒整理的資料
        nxt = await local.fetchval(f"""
            SELECT min(bucket) FROM {CAGG_NAME}
            WHERE bucket >= $1 AND bucket < $2 AND NOT (device_id LIKE ANY($3::text[]))
        """, end, upper, exclude_patterns())
        end = nxt if nxt is not None else upper
    # 雲端 commit 成功（或這段本來就沒資料）才推進指標
    await set_watermark(local, T_MEAS, end, len(records))
    return len(records), end < upper


async def sync_incidents_once(local, cloud, batch: int = INC_BATCH) -> tuple[int, bool]:
    wm = await get_watermark(local, T_INC) or EPOCH
    upper = await local.fetchval("SELECT now() - $1::interval", INC_SETTLE)
    if upper <= wm:
        return 0, False
    rows = await local.fetch(f"""
        SELECT {", ".join(INC_COLS)} FROM anomaly_incidents
        WHERE updated_at >= $1 AND updated_at < $2 AND NOT (device_id LIKE ANY($3::text[]))
        ORDER BY updated_at, id
        LIMIT $4
    """, wm - OVERLAP[T_INC], upper, exclude_patterns(), batch)
    records = [tuple(r) for r in rows]
    if records:
        await upsert_incidents(cloud, records)
    more = len(records) == batch
    # 一批送滿：指標只推到這批最後一筆（下一輪往回 2 分鐘再接）；沒送滿：已經追到 upper
    new_wm = max(wm, records[-1][INC_COLS.index("updated_at")]) if more else upper
    await set_watermark(local, T_INC, new_wm, len(records))
    return len(records), more


async def sync_trend_once(local, cloud, batch: int = INC_BATCH) -> tuple[int, bool]:
    """跟事件表同一套：依 updated_at 往前推，一批最多 batch 筆"""
    wm = await get_watermark(local, T_TREND) or EPOCH
    upper = await local.fetchval("SELECT now() - $1::interval", INC_SETTLE)
    if upper <= wm:
        return 0, False
    rows = await local.fetch(f"""
        SELECT {", ".join(TREND_COLS)} FROM trend_warnings
        WHERE updated_at >= $1 AND updated_at < $2 AND NOT (device_id LIKE ANY($3::text[]))
        ORDER BY updated_at, id
        LIMIT $4
    """, wm - OVERLAP[T_TREND], upper, exclude_patterns(), batch)
    records = [tuple(r) for r in rows]
    if records:
        await upsert_trend(cloud, records)
    more = len(records) == batch
    new_wm = max(wm, records[-1][TREND_COLS.index("updated_at")]) if more else upper
    await set_watermark(local, T_TREND, new_wm, len(records))
    return len(records), more


_skip_reason: dict[str, str | None] = {}


async def table_ready(local, cloud, target: str, local_tables: tuple[str, ...], local_hint: str,
                      cloud_hint: str) -> bool:
    """本地、雲端都有表才同步這個目標；缺一邊就略過（原因改變時才提醒一次），不影響其他目標"""
    reason = None
    checks = " AND ".join(f"to_regclass('{t}') IS NOT NULL" for t in local_tables)
    if not await local.fetchval(f"SELECT {checks}"):
        reason = local_hint
    elif not await cloud.fetchval(f"SELECT to_regclass('{target}') IS NOT NULL"):
        reason = cloud_hint
    if reason and reason != _skip_reason.get(target):
        print(f"[SYNC] {target} 先不同步：{reason}")
    _skip_reason[target] = reason
    return reason is None


async def trend_ready(local, cloud) -> bool:
    return await table_ready(local, cloud, T_TREND, ("trend_warnings",),
                             "本地還沒執行 migration 007", "雲端還沒執行 db/cloud/004_trend.sql")


_oee_skip_reason: str | None = None


async def oee_ready(local, cloud) -> bool:
    """本地有 006、雲端有 003_oee 才同步每小時 OEE；缺一邊就略過（只提醒一次），不影響其他目標"""
    global _oee_skip_reason
    reason = None
    if not await local.fetchval("SELECT to_regclass('oee_hourly') IS NOT NULL AND to_regclass('product_specs') IS NOT NULL"):
        reason = "本地還沒執行 migration 006"
    elif not await cloud.fetchval("SELECT to_regclass('oee_hourly') IS NOT NULL"):
        reason = "雲端還沒執行 db/cloud/003_oee.sql"
    if reason and reason != _oee_skip_reason:
        print(f"[SYNC] 每小時 OEE 先不同步：{reason}")
    _oee_skip_reason = reason
    return reason is None


async def sync_oee_once(local, cloud, max_window: timedelta = OEE_MAX_WINDOW) -> tuple[int, bool]:
    """送一批每小時 OEE（只送整小時已整理完的）；回傳 (筆數, 是否還有沒追上的)"""
    wm = await get_watermark(local, T_OEE)
    if wm is None:
        first = await local.fetchval(
            f"SELECT min(bucket) FROM {OEE_CAGG} WHERE NOT (device_id LIKE ANY($1::text[]))", exclude_patterns())
        if first is None:
            return 0, False
        wm = first
    upper = await refresh_upper(local, OEE_CAGG, HOUR)
    window = plan_window(wm, upper, OVERLAP[T_OEE], max_window)
    if window is None:
        return 0, False
    start, end = window
    # product_id 為 NULL（舊版 PLC 程式）送成 0：雲端主鍵不能有 NULL
    rows = await local.fetch(f"""
        SELECT h.bucket, h.device_id, COALESCE(h.product_id, 0),
               COALESCE(h.run_s, 0)::float8, COALESCE(h.idle_s, 0)::float8, COALESCE(h.fault_s, 0)::float8,
               COALESCE(h.changeover_s, 0)::float8, COALESCE(h.planned_stop_s, 0)::float8,
               COALESCE(h.data_s, 0)::float8, COALESCE(h.unknown_s, 0)::float8,
               COALESCE(h.good, 0)::bigint, COALESCE(h.ng, 0)::bigint, h.samples::int,
               p.ideal_cycle_s::float8,
               ((COALESCE(h.good, 0) + COALESCE(h.ng, 0)) * p.ideal_cycle_s)::float8
        FROM {OEE_CAGG} h
        LEFT JOIN product_specs p ON p.product_id = h.product_id
        WHERE h.bucket >= $1 AND h.bucket < $2 AND NOT (h.device_id LIKE ANY($3::text[]))
        ORDER BY h.bucket
    """, start, end, exclude_patterns())
    records = [tuple(r) for r in rows]
    if records:
        await upsert_oee(cloud, records)
    else:
        nxt = await local.fetchval(f"""
            SELECT min(bucket) FROM {OEE_CAGG}
            WHERE bucket >= $1 AND bucket < $2 AND NOT (device_id LIKE ANY($3::text[]))
        """, end, upper, exclude_patterns())
        end = nxt if nxt is not None else upper
    await set_watermark(local, T_OEE, end, len(records))
    return len(records), end < upper


# 同一時間只能有一個同步在跑：docker 裡的 backend 和本機 uvicorn 同時開著時，第二個會自動略過；
# tools.sync_admin rewind 也會先拿這把鎖，避免調指標時剛好被同步覆蓋
LOCK_KEY = 0x5CADA_5C  # 任意固定數字


async def sync_cycle(local, cloud) -> dict | None:
    """一輪：各目標各自追到最新（最多 MAX_BATCHES_PER_CYCLE 批）；拿不到鎖回 None"""
    if not await local.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
        return None
    try:
        sent = {}
        plan = [(T_MEAS, sync_measurements_once), (T_INC, sync_incidents_once)]
        if await oee_ready(local, cloud):
            plan.append((T_OEE, sync_oee_once))
        if await trend_ready(local, cloud):
            plan.append((T_TREND, sync_trend_once))
        for target, fn in plan:
            total = 0
            for _ in range(MAX_BATCHES_PER_CYCLE):
                n, more = await fn(local, cloud)
                total += n
                if not more:
                    break
            sent[target] = total
        return sent
    finally:
        try:
            await local.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        except Exception:
            pass    # 本地連線已斷：鎖會隨連線一起釋放；不蓋掉原本的錯誤訊息


# ---------- 背景常駐 ----------

def describe_error(e: BaseException) -> str:
    """錯誤訊息轉成看得懂的一行；有些例外（例如逾時）本身沒有訊息文字"""
    if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
        return f"連不上 Tiger（逾時 {CONNECT_TIMEOUT} 秒）：服務可能暫停，或網路擋了連線"
    if isinstance(e, asyncpg.InvalidPasswordError):
        return "Tiger 帳號或密碼錯誤：檢查 .env 的 TIGER_EDGE_URL"
    if isinstance(e, ConnectionError | OSError) and not isinstance(e, asyncpg.PostgresError):
        return f"網路連線中斷：{type(e).__name__}: {e}"
    text = str(e).strip()
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


class CloudSync:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.cloud: asyncpg.Connection | None = None
        self.last_ok_at: datetime | None = None
        self.last_error: str | None = None
        self.last_sent: dict = {}

    def start(self) -> None:
        if not cloud_sync_enabled():
            print("[SYNC] 雲端同步未啟用（.env 設 CLOUD_SYNC_ENABLED=true 才會啟動）")
            return
        try:
            url = get_cloud_url("writer")
        except ConfigError as e:
            self.last_error = str(e)
            print(f"[SYNC] {e}")
            return
        print(f"[SYNC] 雲端同步啟動：{masked(url)}，每 {INTERVAL:.0f} 秒一輪")
        self.task = asyncio.create_task(self._loop(url))

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_cloud()

    async def _close_cloud(self) -> None:
        if self.cloud is not None:
            try:
                await asyncio.wait_for(self.cloud.close(), 5)
            except Exception:
                self.cloud.terminate()
            self.cloud = None

    async def _loop(self, url: str) -> None:
        backoff = BACKOFF_MIN
        while True:
            try:
                if self.cloud is None or self.cloud.is_closed():
                    self.cloud = await asyncpg.connect(url, timeout=CONNECT_TIMEOUT)
                async with acquire() as local:
                    sent = await sync_cycle(local, self.cloud)
                if sent is None:
                    print("[SYNC] 另一個 backend 正在同步，這一輪略過")
                    await asyncio.sleep(INTERVAL)
                    continue
                self.last_sent = sent
                self.last_ok_at = datetime.now(timezone.utc)
                if self.last_error:
                    print("[SYNC] 已恢復")
                self.last_error = None
                backoff = BACKOFF_MIN
                await asyncio.sleep(INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = describe_error(e)
                if msg != self.last_error:          # 同樣的錯誤不重複洗版
                    print(f"[SYNC] 同步失敗，{backoff:.0f} 秒後重試：{msg}")
                self.last_error = msg
                await self._close_cloud()            # 連線可能已壞，下一輪重新連
                try:
                    async with acquire() as local:
                        await record_error(local, msg)
                except Exception:
                    pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)


syncer = CloudSync()


async def status() -> dict:
    """給 /api/system/sync-status：每個目標同步到哪、落後多久"""
    out = {
        "enabled": cloud_sync_enabled(),
        "running": syncer.task is not None and not syncer.task.done(),
        "last_ok_at": syncer.last_ok_at.isoformat() if syncer.last_ok_at else None,
        "last_error": syncer.last_error,
        "last_sent": syncer.last_sent,
        "targets": [],
    }
    try:
        async with acquire() as local:
            rows = await local.fetch("""
                SELECT target, watermark, last_ok_at, last_rows, last_error,
                       extract(epoch FROM now() - watermark)::int AS lag_seconds
                FROM sync_state ORDER BY target
            """)
        out["targets"] = [
            {**dict(r), "watermark": r["watermark"].isoformat(),
             "last_ok_at": r["last_ok_at"].isoformat() if r["last_ok_at"] else None}
            for r in rows
        ]
    except asyncpg.UndefinedTableError:
        out["targets_error"] = "找不到 sync_state 表：請先執行 db/migrations/003_cagg_10s.sql"
    except Exception as e:
        out["targets_error"] = f"{type(e).__name__}: {e}"
    return out
