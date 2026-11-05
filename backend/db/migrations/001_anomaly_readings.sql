-- 001：anomaly_events 新增 readings（JSONB），存放所有訊號的讀值
-- 之後新增訊號只要改 core/device_profile.py，不必再改資料表
-- 執行：docker exec -i timescaledb psql -U postgres -d iotscada < backend/db/migrations/001_anomaly_readings.sql

BEGIN;

ALTER TABLE anomaly_events ADD COLUMN IF NOT EXISTS readings JSONB;

-- 把舊資料的三個欄位補進 readings，讓新舊紀錄用同一種方式查詢
UPDATE anomaly_events
SET readings = jsonb_strip_nulls(jsonb_build_object(
        'motor_speed', motor_speed,
        'temperature', temperature,
        'pressure',    pressure))
WHERE readings IS NULL;

COMMIT;

-- 確認：應該看到 readings 欄位，且 missing 為 0
SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE readings IS NULL) AS missing FROM anomaly_events;
