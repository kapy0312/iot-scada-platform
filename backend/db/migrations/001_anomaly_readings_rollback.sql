-- 還原 001：移除 readings 欄位（舊的三個欄位一直有寫入，資料不會遺失）
-- 執行前先把後端換回舊版程式，否則寫入異常事件會失敗
-- 執行：docker exec -i timescaledb psql -U postgres -d iotscada < backend/db/migrations/001_anomaly_readings_rollback.sql

ALTER TABLE anomaly_events DROP COLUMN IF EXISTS readings;
