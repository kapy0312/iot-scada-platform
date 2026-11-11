-- 雲端 003：每小時 OEE（對應本地 006 的 oee_hourly）
-- 用 tsdbadmin 執行：python -m tools.cloud_admin apply backend/db/cloud/003_oee.sql
-- 回滾：003_oee_rollback.sql
--
-- 資料全部由本地 backend 送上來；送的時候帶上當時的品號標準週期（ideal_cycle_s）與理想生產秒數（ideal_s），
-- 雲端不用另外存品號表就能算表現率＝ideal_s ÷ run_s
-- 每台設備每小時每品號一筆，一年約 9000 筆，不用 hypertable

CREATE TABLE IF NOT EXISTS oee_hourly (
    bucket         timestamptz      NOT NULL,
    device_id      text             NOT NULL,
    product_id     integer          NOT NULL,         -- 0＝舊版 PLC 程式沒有品號
    run_s          double precision NOT NULL,
    idle_s         double precision NOT NULL,
    fault_s        double precision NOT NULL,
    changeover_s   double precision NOT NULL,
    planned_stop_s double precision NOT NULL,
    data_s         double precision NOT NULL,         -- 有狀態的秒數；3600 − data_s − unknown_s ＝ 無資料
    unknown_s      double precision NOT NULL,
    good           bigint           NOT NULL,
    ng             bigint           NOT NULL,
    samples        integer          NOT NULL,
    ideal_cycle_s  double precision,                  -- 送出當時的標準週期；品號沒設定時為 NULL
    ideal_s        double precision,                  -- (good + ng) × ideal_cycle_s
    synced_at      timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, product_id, bucket)
);
CREATE INDEX IF NOT EXISTS oee_hourly_bucket_idx ON oee_hourly (bucket DESC);

-- 權限：跟 002 一樣，edge_writer 只能新增、更新，mcp_reader 只能讀
GRANT SELECT, INSERT, UPDATE ON oee_hourly TO edge_writer;
GRANT SELECT ON oee_hourly TO mcp_reader;

-- 確認：edge_writer 有 INSERT/SELECT/UPDATE，mcp_reader 只有 SELECT
SELECT grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_name = 'oee_hourly' AND grantee IN ('edge_writer', 'mcp_reader')
GROUP BY grantee ORDER BY grantee;
