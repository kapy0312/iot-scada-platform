-- 006 回滾：刪除每小時彙整、品號表、班別表（自動整理與保存期限的排程會一起刪除）
-- machine_status 原始紀錄不受影響，重跑 006 會重新整理出每小時彙整
-- 品號標準週期、班別設定會一起刪除，回滾前先記下來：
--   docker exec timescaledb psql -U postgres -d iotscada -c "SELECT * FROM product_specs" -c "SELECT * FROM shift_definitions"
-- 執行：Get-Content backend/db/migrations/006_oee_rollback.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 注意：C 包上線後要先在 .env 停用 OEE 雲端同步再回滾

DROP MATERIALIZED VIEW IF EXISTS oee_hourly;
DROP TABLE IF EXISTS shift_definitions;
DROP TABLE IF EXISTS product_specs;
DELETE FROM sync_state WHERE target = 'oee_hourly';

-- 確認：應該回傳 0
SELECT count(*) FROM information_schema.tables
WHERE table_name IN ('oee_hourly', 'shift_definitions', 'product_specs');
