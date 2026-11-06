-- 回滾 002：刪除事件表與新增的兩個欄位；anomaly_events 原有的資料不受影響
DROP TABLE IF EXISTS anomaly_incidents;
ALTER TABLE anomaly_events DROP COLUMN IF EXISTS incident_id;
ALTER TABLE anomaly_events DROP COLUMN IF EXISTS ai_source;