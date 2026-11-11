-- 雲端 001 回滾：刪除兩張表（雲端資料全部消失，本地資料不受影響）
-- 要先執行 002_roles_rollback.sql，否則帳號還掛著這兩張表的權限
-- 重建後要重送：在本地執行 python -m tools.sync_admin rewind --all

DROP TABLE IF EXISTS measurements_10s;
DROP TABLE IF EXISTS anomaly_incidents;
