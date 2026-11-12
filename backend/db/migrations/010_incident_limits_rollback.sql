-- 010 回滾：刪掉事件的門檻欄位（事件回到用現行門檻判斷）
-- 執行：Get-Content backend/db/migrations/010_incident_limits_rollback.sql | docker exec -i timescaledb psql -U postgres -d iotscada

BEGIN;
ALTER TABLE anomaly_incidents DROP COLUMN IF EXISTS limits_changed;
ALTER TABLE anomaly_incidents DROP COLUMN IF EXISTS limits;
COMMIT;
