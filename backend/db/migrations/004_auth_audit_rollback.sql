-- 004 回滾：刪除帳號、登入狀態、操作紀錄三張表
-- 注意：操作紀錄會永久消失。回滾前先匯出：
--   docker exec timescaledb psql -U postgres -d iotscada -c "\copy audit_log TO STDOUT WITH CSV HEADER" > audit_log_backup.csv
-- 回滾後舊版程式碼（沒有登入功能）照常可用

BEGIN;
DROP TABLE IF EXISTS app_sessions;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS app_users;
COMMIT;
