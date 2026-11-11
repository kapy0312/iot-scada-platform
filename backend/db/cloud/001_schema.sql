-- 雲端 001：Tiger Cloud 上接收同步資料的兩張表
-- 用 tsdbadmin 執行（TIGER_URL）：
--   psql "$env:TIGER_URL" -f backend/db/cloud/001_schema.sql
-- 回滾：001_schema_rollback.sql
--
-- 這兩張表的資料全部由本地 backend 送上來；雲端資料就算全部刪掉，也能把本地 watermark 調回去重送

-- 1. 每 10 秒彙整（對應本地的 plc_measurements_10s）
CREATE TABLE IF NOT EXISTS measurements_10s (
    bucket     timestamptz      NOT NULL,
    device_id  text             NOT NULL,
    tag_name   text             NOT NULL,
    avg_v      double precision,
    min_v      double precision,
    max_v      double precision,
    samples    integer          NOT NULL,
    synced_at  timestamptz      NOT NULL DEFAULT now(),
    -- 同一個「設備、訊號、10 秒區間」只會有一筆：重送時更新、不會重複
    PRIMARY KEY (device_id, tag_name, bucket)
);
SELECT create_hypertable('measurements_10s', by_range('bucket', INTERVAL '7 days'), if_not_exists => true);

-- 壓縮：30 天以前的資料（斷線補送通常只碰最近的資料，不會頻繁改到壓縮過的區塊）
ALTER TABLE measurements_10s SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, tag_name',
    timescaledb.compress_orderby   = 'bucket'
);
SELECT add_compression_policy('measurements_10s', INTERVAL '30 days', if_not_exists => true);
SELECT add_retention_policy('measurements_10s', INTERVAL '365 days', if_not_exists => true);

-- 2. 事件表（對應本地的 anomaly_incidents，id 沿用本地的 id）
CREATE TABLE IF NOT EXISTS anomaly_incidents (
    id           bigint      PRIMARY KEY,
    device_id    text        NOT NULL,
    started_at   timestamptz NOT NULL,
    ended_at     timestamptz NOT NULL,
    records      integer     NOT NULL,
    level        text        NOT NULL,
    severity     integer     NOT NULL,
    peak         jsonb       NOT NULL,
    min_score    double precision,
    ai_analysis  text,
    ai_source    jsonb,
    updated_at   timestamptz NOT NULL,       -- 本地最後更新時間
    synced_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS anomaly_incidents_device_started_idx
    ON anomaly_incidents (device_id, started_at DESC);

-- 確認
SELECT hypertable_name, compression_enabled FROM timescaledb_information.hypertables
WHERE hypertable_name = 'measurements_10s';
