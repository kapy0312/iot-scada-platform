-- 雲端 004：趨勢預警紀錄（對應本地 007 的 trend_warnings，id 沿用本地的 id）
-- 用 tsdbadmin 執行：python -m tools.cloud_admin apply db/cloud/004_trend.sql
-- 回滾：004_trend_rollback.sql
--
-- 一次預警一列，本地更新時（預警中每 10 秒、結束時）用 updated_at 判斷新舊，只用較新的版本覆蓋
-- 資料量很小（一天頂多幾十筆），不用 hypertable

CREATE TABLE IF NOT EXISTS trend_warnings (
    id             bigint           PRIMARY KEY,
    device_id      text             NOT NULL,
    tag_name       text             NOT NULL,
    target_level   text             NOT NULL,          -- warning / danger
    threshold      double precision NOT NULL,
    started_at     timestamptz      NOT NULL,
    ended_at       timestamptz      NOT NULL,
    first_eta_s    integer          NOT NULL,
    min_eta_s      integer          NOT NULL,
    slope_per_min  double precision NOT NULL,
    value_at_start double precision NOT NULL,
    value_at_end   double precision NOT NULL,
    outcome        text             NOT NULL,          -- ongoing / reached / cleared / interrupted
    reached_at     timestamptz,
    incident_id    bigint,                             -- 對應雲端 anomaly_incidents.id
    updated_at     timestamptz      NOT NULL,          -- 本地最後更新時間
    synced_at      timestamptz      NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trend_warnings_device_started_idx ON trend_warnings (device_id, started_at DESC);

-- 權限：跟 002 一樣，edge_writer 只能新增、更新，mcp_reader 只能讀
GRANT SELECT, INSERT, UPDATE ON trend_warnings TO edge_writer;
GRANT SELECT ON trend_warnings TO mcp_reader;

-- 確認：edge_writer 有 INSERT/SELECT/UPDATE，mcp_reader 只有 SELECT
SELECT grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_name = 'trend_warnings' AND grantee IN ('edge_writer', 'mcp_reader')
GROUP BY grantee ORDER BY grantee;
