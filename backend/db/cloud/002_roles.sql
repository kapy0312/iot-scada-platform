-- 雲端 002：兩個專用帳號
--   edge_writer：本地 backend 同步用，只能新增、更新（不能刪、不能改表結構）
--   mcp_reader ：之後雲端 MCP 用，只能讀
-- 用 tsdbadmin 執行：psql "$env:TIGER_URL" -f backend/db/cloud/002_roles.sql
-- 檔案裡不放密碼：執行完後在 psql 裡用 \password edge_writer、\password mcp_reader 互動輸入
-- 回滾：002_roles_rollback.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'edge_writer') THEN
        CREATE ROLE edge_writer LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_reader') THEN
        CREATE ROLE mcp_reader LOGIN;
    END IF;
END $$;

-- 連線數上限：免費方案連線數有限，各自只給夠用的量
ALTER ROLE edge_writer CONNECTION LIMIT 2;
ALTER ROLE mcp_reader  CONNECTION LIMIT 3;

-- mcp_reader 就算被下了寫入指令也會被資料庫拒絕；查詢超過 10 秒自動中斷
ALTER ROLE mcp_reader SET default_transaction_read_only = on;
ALTER ROLE mcp_reader SET statement_timeout = '10s';
ALTER ROLE edge_writer SET statement_timeout = '60s';

GRANT USAGE ON SCHEMA public TO edge_writer, mcp_reader;

-- upsert（INSERT ... ON CONFLICT DO UPDATE）需要 SELECT + INSERT + UPDATE；沒有 DELETE、TRUNCATE
GRANT SELECT, INSERT, UPDATE ON measurements_10s, anomaly_incidents TO edge_writer;
GRANT SELECT ON measurements_10s, anomaly_incidents TO mcp_reader;

-- 確認：應該看到 edge_writer 有 INSERT/SELECT/UPDATE，mcp_reader 只有 SELECT
SELECT grantee, table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE grantee IN ('edge_writer', 'mcp_reader')
GROUP BY grantee, table_name ORDER BY grantee, table_name;
