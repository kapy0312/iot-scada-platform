-- 雲端 003 回滾：刪除雲端的每小時 OEE（本地資料不受影響）
-- 本地 backend 偵測到雲端沒有這張表會自動略過 OEE 同步，其他資料照常同步
-- 重建後要重送：python -m tools.sync_admin rewind --all --target oee_hourly

REVOKE ALL ON oee_hourly FROM edge_writer, mcp_reader;
DROP TABLE IF EXISTS oee_hourly;
