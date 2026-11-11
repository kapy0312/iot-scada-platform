-- 007 回滾：刪除趨勢預警紀錄表（預警的即時推估在記憶體裡，不受影響；只是不再留紀錄）
-- 執行：Get-Content backend/db/migrations/007_trend_warnings_rollback.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 注意：有開雲端同步時，先回滾雲端 004（或不管它：本地沒有這張表時同步會自動略過這個目標）

DROP TABLE IF EXISTS trend_warnings;
DELETE FROM sync_state WHERE target = 'trend_warnings';

-- 確認：應該回傳 0
SELECT count(*) FROM information_schema.tables WHERE table_name = 'trend_warnings';
