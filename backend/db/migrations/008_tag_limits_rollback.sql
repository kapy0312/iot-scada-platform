-- 008 回滾：刪除門檻覆寫表，所有訊號回到 core/device_profile.py 的預設值
-- 執行：Get-Content backend/db/migrations/008_tag_limits_rollback.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 執行後要 docker restart iot-backend（後端記憶體裡還留著改過的值，重啟才會回到預設）

DROP TABLE IF EXISTS tag_limits;

-- 確認：應該回傳 0
SELECT count(*) FROM information_schema.tables WHERE table_name = 'tag_limits';
