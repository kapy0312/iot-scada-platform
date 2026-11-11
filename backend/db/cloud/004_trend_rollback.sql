-- 雲端 004 回滾：刪除趨勢預警紀錄
-- 用 tsdbadmin 執行：python -m tools.cloud_admin apply db/cloud/004_trend_rollback.sql
-- 雲端沒有這張表時，本地同步會自動略過這個目標（不影響其他三個）；之後重建要重送時：
--   python -m tools.sync_admin rewind --all --target trend_warnings

DROP TABLE IF EXISTS trend_warnings;

-- 確認：應該回傳 0
SELECT count(*) FROM information_schema.tables WHERE table_name = 'trend_warnings';
