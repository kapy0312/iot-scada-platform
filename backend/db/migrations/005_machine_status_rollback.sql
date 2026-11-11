-- 005 回滾：刪除機台狀態表（生產計數紀錄會一起刪除，回滾前先匯出）
-- 匯出：docker exec timescaledb psql -U postgres -d iotscada -c "\copy machine_status TO '/tmp/machine_status.csv' CSV HEADER"
-- 執行：Get-Content backend/db/migrations/005_machine_status_rollback.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 注意：006 建在這張表上，要先回滾 006 再回滾 005

BEGIN;
SELECT remove_retention_policy('machine_status', if_exists => true);
DROP TABLE IF EXISTS machine_status;
COMMIT;

-- 確認：應該回傳 0
SELECT count(*) FROM information_schema.tables WHERE table_name = 'machine_status';
