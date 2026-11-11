-- 雲端 002 回滾：收回權限、刪除兩個帳號
-- 先在本地 .env 設 CLOUD_SYNC_ENABLED=false 並重啟 backend，否則同步會一直連線失敗

REVOKE ALL ON measurements_10s, anomaly_incidents FROM edge_writer, mcp_reader;
REVOKE USAGE ON SCHEMA public FROM edge_writer, mcp_reader;
DROP ROLE IF EXISTS edge_writer;
DROP ROLE IF EXISTS mcp_reader;
