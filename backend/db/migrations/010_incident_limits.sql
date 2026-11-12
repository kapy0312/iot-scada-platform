-- 010：事件記下「當時的門檻」
-- 執行：Get-Content backend/db/migrations/010_incident_limits.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：010_incident_limits_rollback.sql
--
-- 為什麼：事件表只存數值（peak），之後在畫面上改門檻，回頭看舊事件時會用「現在的門檻」重新判斷，
--         例如事件 39：當時溫度警戒值暫時改成 69，溫度 70 算警戒；改回 85 後看起來像「全部正常卻有診斷」
-- limits：事件開始時五個訊號的門檻 {"version": "…", "tags": {"temperature": {"normal": [65, 75], "warning": 85, "danger": 100}, …}}
-- limits_changed：事件進行中門檻被改過（判斷以開始時的門檻為準，畫面會提示）
-- 舊事件兩欄是空的／false，照舊用現行門檻判斷
-- 不跑 010 系統也照常運作（寫入時會略過這兩欄），跑完要重開 backend 或等 5 分鐘才會開始寫

BEGIN;
ALTER TABLE anomaly_incidents ADD COLUMN IF NOT EXISTS limits jsonb;
ALTER TABLE anomaly_incidents ADD COLUMN IF NOT EXISTS limits_changed boolean NOT NULL DEFAULT false;
COMMIT;

-- 確認：應該看到 limits（jsonb）與 limits_changed（boolean）
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'anomaly_incidents' AND column_name IN ('limits', 'limits_changed') ORDER BY column_name;
