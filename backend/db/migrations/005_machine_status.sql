-- 005：機台狀態與生產計數（每秒一筆），OEE 的原始資料
-- 執行：Get-Content backend/db/migrations/005_machine_status.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：005_machine_status_rollback.sql（只刪這張表，製程資料 plc_measurements 不受影響）
--
-- 跟 plc_measurements 分開存：生產計數不會混進 10 秒彙整、雲端同步或 ML 訓練資料
-- 每小時彙整、品號與班別設定在 006（B 包）

BEGIN;

CREATE TABLE IF NOT EXISTS machine_status (
    time         timestamptz NOT NULL,
    device_id    text        NOT NULL,
    state        smallint,              -- 0=計畫停機 1=運轉 2=待機 3=故障 4=換模；舊版 PLC 程式沒有這個變數時為 NULL
    motor_enable boolean,
    product_id   integer,
    good_total   bigint,                -- PLC 的累計原值（除錯、對帳用）
    ng_total     bigint,
    good_delta   integer     NOT NULL DEFAULT 0,   -- 這一筆新增的件數（後端換算，已處理歸零）
    ng_delta     integer     NOT NULL DEFAULT 0,
    dt           real        NOT NULL,             -- 這一筆代表幾秒（0～5）；一小時加總不到 3600 的部分就是「無資料」
    CONSTRAINT machine_status_state_chk CHECK (state IS NULL OR state BETWEEN 0 AND 4),
    CONSTRAINT machine_status_delta_chk CHECK (good_delta >= 0 AND ng_delta >= 0),
    CONSTRAINT machine_status_dt_chk    CHECK (dt >= 0 AND dt <= 5)
);

SELECT create_hypertable('machine_status', by_range('time', INTERVAL '1 day'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS machine_status_device_time_idx ON machine_status (device_id, time DESC);

-- 跟原始量測一樣保存 90 天（每小時彙整在 006 另外保存 2 年）
SELECT add_retention_policy('machine_status', INTERVAL '90 days', if_not_exists => true);

COMMIT;

-- 確認：應該看到 machine_status 是 hypertable，且有 1 個保存期限工作
SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'machine_status';
SELECT job_id, proc_name, config FROM timescaledb_information.jobs
WHERE hypertable_name = 'machine_status';
