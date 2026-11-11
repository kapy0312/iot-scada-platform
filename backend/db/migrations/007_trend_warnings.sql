-- 007：趨勢預警紀錄 trend_warnings（一次預警一列，像 anomaly_incidents 一次故障一列）
-- 執行：Get-Content backend/db/migrations/007_trend_warnings.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：007_trend_warnings_rollback.sql（只刪這張表，其他資料不受影響）
--
-- 不存每 10 秒的推估值（可以隨時重算、量大又雜），只存「一次預警」的頭尾：
-- 預警開始時的預估、最短預估、結果（真的到了／自己退了／中途停機），用來算命中率與平均提前量
-- 一般表即可（一天頂多幾十筆），不用 hypertable，也不設保存期限

BEGIN;

CREATE TABLE IF NOT EXISTS trend_warnings (
    id             bigserial PRIMARY KEY,
    device_id      text             NOT NULL,
    tag_name       text             NOT NULL,
    target_level   text             NOT NULL,          -- warning＝預估達警戒值、danger＝預估達危險值
    threshold      double precision NOT NULL,          -- 當時的警戒值或危險值
    started_at     timestamptz      NOT NULL,
    ended_at       timestamptz      NOT NULL,          -- 進行中時＝最後一次更新的時間
    first_eta_s    integer          NOT NULL,          -- 預警開始時的預估秒數
    min_eta_s      integer          NOT NULL,          -- 預警期間最短的預估秒數
    slope_per_min  double precision NOT NULL,          -- 最後一次推估的上升速度（每分鐘）
    value_at_start double precision NOT NULL,
    value_at_end   double precision NOT NULL,
    outcome        text             NOT NULL DEFAULT 'ongoing',
    reached_at     timestamptz,                        -- 真的到了的時間；提前量＝reached_at − started_at
    incident_id    bigint,                             -- 到了之後連到哪一筆事件（anomaly_incidents.id）
    updated_at     timestamptz      NOT NULL DEFAULT now(),
    CONSTRAINT trend_warnings_level_chk   CHECK (target_level IN ('warning', 'danger')),
    -- ongoing＝進行中、reached＝真的到了、cleared＝上升停止自己退了、interrupted＝停機或後端重啟中斷
    CONSTRAINT trend_warnings_outcome_chk CHECK (outcome IN ('ongoing', 'reached', 'cleared', 'interrupted')),
    CONSTRAINT trend_warnings_reached_chk CHECK ((outcome = 'reached') = (reached_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS trend_warnings_device_started_idx ON trend_warnings (device_id, started_at DESC);
CREATE INDEX IF NOT EXISTS trend_warnings_updated_idx        ON trend_warnings (updated_at);   -- 雲端同步用
CREATE INDEX IF NOT EXISTS trend_warnings_incident_idx       ON trend_warnings (incident_id) WHERE incident_id IS NOT NULL;

COMMIT;

-- 確認：應該看到 trend_warnings，0 筆
SELECT count(*) AS rows FROM trend_warnings;
