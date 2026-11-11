-- 006：OEE 用的品號表、班別表、每小時彙整（建在 005 的 machine_status 上）
-- 執行：Get-Content backend/db/migrations/006_oee.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：006_oee_rollback.sql（只刪 OEE 設定與每小時彙整，machine_status 原始紀錄不受影響）
--
-- 注意：建立 continuous aggregate 不能放在 BEGIN/COMMIT 裡，所以這個檔案不包交易；
--       每一句都用 IF NOT EXISTS / ON CONFLICT，重跑不會出錯

-- 1. 品號：標準週期時間（理想狀態下幾秒做一件），表現率用它算
CREATE TABLE IF NOT EXISTS product_specs (
    product_id    integer       PRIMARY KEY,
    name          text          NOT NULL,
    ideal_cycle_s numeric(10,3) NOT NULL,
    updated_at    timestamptz   NOT NULL DEFAULT now(),
    updated_by    text,
    CONSTRAINT product_specs_cycle_chk CHECK (ideal_cycle_s > 0 AND ideal_cycle_s <= 86400)
);
-- PLC 的 FB_Production 額定轉速 6 秒一件，品號 1 對應這個值
INSERT INTO product_specs (product_id, name, ideal_cycle_s, updated_by)
VALUES (1, '預設品號', 6.0, 'migration 006')
ON CONFLICT (product_id) DO NOTHING;

-- 2. 班別：同一個 effective_from 的幾列是「一套班別」，從那天起生效
--    改班別時新增一套（新的 effective_from），舊日期的 OEE 仍照舊的那套算
--    時間用台北時間的整點（每班 OEE 由每小時彙整加總，所以只能整點）；end_hour <= start_hour 代表跨午夜
CREATE TABLE IF NOT EXISTS shift_definitions (
    id             serial      PRIMARY KEY,
    effective_from date        NOT NULL,
    name           text        NOT NULL,
    start_hour     smallint    NOT NULL,
    end_hour       smallint    NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     text,
    CONSTRAINT shift_definitions_hour_chk CHECK (start_hour BETWEEN 0 AND 23 AND end_hour BETWEEN 0 AND 23),
    CONSTRAINT shift_definitions_name_uq  UNIQUE (effective_from, name)
);
CREATE INDEX IF NOT EXISTS shift_definitions_from_idx ON shift_definitions (effective_from DESC);
-- 預設兩班：早班 08～20、晚班 20～隔天 08（只在表是空的時候放）
INSERT INTO shift_definitions (effective_from, name, start_hour, end_hour, updated_by)
SELECT d, n, s, e, 'migration 006'
FROM (VALUES (DATE '2026-01-01', '早班', 8, 20), (DATE '2026-01-01', '晚班', 20, 8)) AS v(d, n, s, e)
WHERE NOT EXISTS (SELECT 1 FROM shift_definitions);

-- 3. 每小時彙整：各狀態幾秒、良品／NG 幾件（依品號分開，表現率要用各自的標準週期）
--    materialized_only = false：還沒整理到的部分（例如正在進行的這一小時）查詢時直接從原始資料補上
--    data_s 只算有狀態的秒數；state 為 NULL（舊版 PLC 程式）另外記在 unknown_s
CREATE MATERIALIZED VIEW IF NOT EXISTS oee_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
       device_id,
       product_id,
       sum(dt) FILTER (WHERE state = 1)        AS run_s,
       sum(dt) FILTER (WHERE state = 2)        AS idle_s,
       sum(dt) FILTER (WHERE state = 3)        AS fault_s,
       sum(dt) FILTER (WHERE state = 4)        AS changeover_s,
       sum(dt) FILTER (WHERE state = 0)        AS planned_stop_s,
       sum(dt) FILTER (WHERE state IS NOT NULL) AS data_s,
       sum(dt) FILTER (WHERE state IS NULL)    AS unknown_s,
       sum(good_delta)                         AS good,
       sum(ng_delta)                           AS ng,
       count(*)                                AS samples
FROM machine_status
GROUP BY bucket, device_id, product_id
WITH NO DATA;

-- 4. 自動整理：每 5 分鐘整理「4 小時前到 1 分鐘前」（整理範圍至少要涵蓋 2 個 bucket）
--    一小時結束後約 1～6 分鐘整理進去；在那之前查詢會自動從原始資料補
SELECT add_continuous_aggregate_policy('oee_hourly',
    start_offset      => INTERVAL '4 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists     => true);

-- 5. 每小時彙整保存 2 年（原始 machine_status 只留 90 天）
SELECT add_retention_policy('oee_hourly', INTERVAL '730 days', if_not_exists => true);

-- 6. 第一次回填：把 005 上線以來的紀錄整理進去
CALL refresh_continuous_aggregate('oee_hourly', now() - INTERVAL '90 days', now() - INTERVAL '1 minute');

-- 確認：品號 1 筆、班別 2 筆、每小時彙整有資料、自動整理與保存期限各 1 個工作
SELECT product_id, name, ideal_cycle_s FROM product_specs ORDER BY product_id;
SELECT effective_from, name, start_hour, end_hour FROM shift_definitions ORDER BY effective_from, start_hour;
SELECT count(*) AS rows, min(bucket) AS first_bucket, max(bucket) AS last_bucket FROM oee_hourly;
SELECT job_id, proc_name, schedule_interval FROM timescaledb_information.jobs
WHERE hypertable_name = (SELECT materialization_hypertable_name FROM timescaledb_information.continuous_aggregates
                         WHERE view_name = 'oee_hourly');
