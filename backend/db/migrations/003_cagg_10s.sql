-- 003：每 10 秒彙整（continuous aggregate）與雲端同步進度表
-- 執行：Get-Content backend/db/migrations/003_cagg_10s.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：003_cagg_10s_rollback.sql（只刪彙整與同步進度，原始資料 plc_measurements 不受影響）
--
-- 注意：建立 continuous aggregate 不能放在 BEGIN/COMMIT 裡，所以這個檔案不包交易；
--       每一句都用 IF NOT EXISTS / if_not_exists，重跑不會出錯

-- 1. 每 10 秒一筆：平均、最小、最大、筆數（筆數不足 10 代表那 10 秒有斷資料）
CREATE MATERIALIZED VIEW IF NOT EXISTS plc_measurements_10s
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket(INTERVAL '10 seconds', time) AS bucket,
       device_id,
       tag_name,
       avg(value)   AS avg_v,
       min(value)   AS min_v,
       max(value)   AS max_v,
       count(value) AS samples
FROM plc_measurements
GROUP BY bucket, device_id, tag_name
WITH NO DATA;

-- 2. 自動整理：每 30 秒把「2 小時前到 20 秒前」重新整理一次
--    20 秒：讓最新的 10 秒區間收齊資料再整理；2 小時：晚到的資料也會被補進彙整
SELECT add_continuous_aggregate_policy('plc_measurements_10s',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '20 seconds',
    schedule_interval => INTERVAL '30 seconds',
    if_not_exists     => true);

-- 3. 彙整也跟原始資料一樣只留 90 天
SELECT add_retention_policy('plc_measurements_10s', INTERVAL '90 days', if_not_exists => true);

-- 4. 同步進度：每個同步目標一列，watermark 之前的資料都已確認送上雲端
CREATE TABLE IF NOT EXISTS sync_state (
    target      text        PRIMARY KEY,         -- measurements_10s / anomaly_incidents
    watermark   timestamptz NOT NULL,
    last_ok_at  timestamptz,
    last_rows   integer,
    last_error  text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 5. 第一次回填：把現有 90 天的原始資料整理進彙整（資料多時要跑幾分鐘，請等它跑完）
CALL refresh_continuous_aggregate('plc_measurements_10s', now() - INTERVAL '90 days', now() - INTERVAL '20 seconds');

-- 確認：彙整筆數、時間範圍，以及自動整理工作已排程
SELECT count(*) AS rows, min(bucket) AS first_bucket, max(bucket) AS last_bucket FROM plc_measurements_10s;
SELECT job_id, proc_name, schedule_interval, config FROM timescaledb_information.jobs
WHERE proc_name = 'policy_refresh_continuous_aggregate';
