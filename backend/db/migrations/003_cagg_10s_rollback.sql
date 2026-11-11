-- 003 回滾：刪除 10 秒彙整（自動整理、保存期限的排程會一起刪除）與同步進度
-- 原始資料 plc_measurements 不受影響；回滾前請先在 .env 設 CLOUD_SYNC_ENABLED=false 並重啟 backend
-- 雲端已同步的資料不會被刪，要清雲端請另外執行 backend/db/cloud/001_schema_rollback.sql

DROP MATERIALIZED VIEW IF EXISTS plc_measurements_10s;
DROP TABLE IF EXISTS sync_state;
