-- 002：事件表 anomaly_incidents（一次故障一列），以及每秒事件列的手冊來源
-- 回滾：002_incidents_rollback.sql

-- 每秒的事件列：當下診斷所用的手冊來源，以及所屬事件
ALTER TABLE anomaly_events ADD COLUMN IF NOT EXISTS ai_source   jsonb;
ALTER TABLE anomaly_events ADD COLUMN IF NOT EXISTS incident_id bigint;

-- 事件表：資料量小，用一般表即可
CREATE TABLE IF NOT EXISTS anomaly_incidents (
    id           bigserial PRIMARY KEY,
    device_id    text        NOT NULL,
    started_at   timestamptz NOT NULL,
    ended_at     timestamptz NOT NULL,
    records      integer     NOT NULL DEFAULT 1,
    level        text        NOT NULL,   -- normal（只有 ML 異常）/ low / high / warning / danger
    severity     integer     NOT NULL,   -- 各訊號等級加總的最大值，用來判斷「最嚴重時刻」
    peak         jsonb       NOT NULL,   -- {"max": {訊號: 值}, "min": {訊號: 值}}
    min_score    double precision,       -- 最低的 ML 異常分數
    ai_analysis  text,                   -- 最嚴重時刻的診斷
    ai_source    jsonb,                  -- 診斷依據的手冊章節
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS anomaly_incidents_device_started_idx
    ON anomaly_incidents (device_id, started_at DESC);