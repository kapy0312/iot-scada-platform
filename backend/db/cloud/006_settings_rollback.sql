-- 雲端 006 回滾：刪掉三張設定表，量測資料改回 001 的「保留 365 天、30 天後壓縮」
-- 用 tsdbadmin 執行：python -m tools.cloud_admin apply db/cloud/006_settings_rollback.sql
-- 雲端沒有設定表時，本地同步會自動略過 settings 這個目標；雲端 MCP 的 OEE 與門檻改用程式預設值

DROP TABLE IF EXISTS tag_limits;
DROP TABLE IF EXISTS product_specs;
DROP TABLE IF EXISTS shift_definitions;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM remove_retention_policy('measurements_10s', if_exists => true);
        PERFORM add_retention_policy('measurements_10s', INTERVAL '365 days');
        PERFORM remove_compression_policy('measurements_10s', if_exists => true);
        PERFORM add_compression_policy('measurements_10s', INTERVAL '30 days');
    END IF;
END $$;
