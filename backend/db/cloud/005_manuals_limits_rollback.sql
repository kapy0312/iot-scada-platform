-- 雲端 005 回滾：刪掉手冊表與事件的門檻欄位（vector extension 保留）
-- 用 tsdbadmin 執行：python -m tools.cloud_admin apply db/cloud/005_manuals_limits_rollback.sql
-- 雲端沒有手冊表時，本地同步會自動略過 manual_documents 這個目標；事件表沒有門檻欄位時，只送原本的欄位
-- 之後重建要重送：手冊會自動整份重送；事件的門檻要 python -m tools.sync_admin rewind --all --target anomaly_incidents

DROP TABLE IF EXISTS manual_chunks;
DROP TABLE IF EXISTS manual_documents;
ALTER TABLE anomaly_incidents DROP COLUMN IF EXISTS limits_changed;
ALTER TABLE anomaly_incidents DROP COLUMN IF EXISTS limits;

-- 確認：應該回傳 0 與 0
SELECT count(*) AS manual_tables FROM information_schema.tables WHERE table_name IN ('manual_documents', 'manual_chunks');
SELECT count(*) AS limit_columns FROM information_schema.columns
WHERE table_name = 'anomaly_incidents' AND column_name IN ('limits', 'limits_changed');
