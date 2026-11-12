-- 雲端 006：設定表（門檻、品號標準週期、班別）＋量測資料保留 30 天
-- 用 tsdbadmin 執行：python -m tools.cloud_admin apply db/cloud/006_settings.sql
-- 回滾：006_settings_rollback.sql
--
-- 為什麼：Phase 3 的雲端 MCP 要能回答「目前門檻」與 OEE，OEE 要用品號標準週期與班別
-- - 三張表是本地 006、008 的副本，欄位相同（雲端多一欄 synced_at）
-- - 同步方式：每輪整張比對（只有幾十列），本地刪掉的列雲端也刪
--   （門檻「恢復預設值」在本地是刪掉那一列；改班別是刪掉同一天那套再新增）
-- - 所以 edge_writer 對這三張設定表有 DELETE；事件、量測等歷史資料維持不能刪
-- - 雲端不加 UNIQUE／CHECK（只留主鍵），本地已檢查過，避免同步時因順序卡住
--
-- 保留期限：免費服務只有 750 MiB。001 原本設「保留 365 天、30 天後壓縮」，24 小時運轉約 10 MB／天，
-- 一年會超過上限（用滿會變唯讀）。改成「保留 30 天、7 天後壓縮」：就算壓縮沒作用，最多約 300 MB
-- 要改天數：照下面 DO 區塊的寫法，先 remove 再用新天數 add

CREATE TABLE IF NOT EXISTS tag_limits (
    device_model text             NOT NULL,
    tag_name     text             NOT NULL,
    normal_lo    double precision NOT NULL,
    normal_hi    double precision NOT NULL,
    warning      double precision NOT NULL,
    danger       double precision NOT NULL,
    scale_lo     double precision NOT NULL,
    scale_hi     double precision NOT NULL,
    updated_at   timestamptz      NOT NULL,
    updated_by   text,
    synced_at    timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (device_model, tag_name)
);

CREATE TABLE IF NOT EXISTS product_specs (
    product_id    integer       PRIMARY KEY,
    name          text          NOT NULL,
    ideal_cycle_s numeric(10,3) NOT NULL,
    updated_at    timestamptz   NOT NULL,
    updated_by    text,
    synced_at     timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shift_definitions (
    id             integer     PRIMARY KEY,                 -- 沿用本地的 id
    effective_from date        NOT NULL,
    name           text        NOT NULL,
    start_hour     smallint    NOT NULL,
    end_hour       smallint    NOT NULL,
    updated_at     timestamptz NOT NULL,
    updated_by     text,
    synced_at      timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON tag_limits, product_specs, shift_definitions TO edge_writer;
GRANT SELECT ON tag_limits, product_specs, shift_definitions TO mcp_reader;

-- 量測資料保留 30 天、7 天後壓縮（TimescaleDB 背景工作自動處理）
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM remove_retention_policy('measurements_10s', if_exists => true);
        PERFORM add_retention_policy('measurements_10s', INTERVAL '30 days');
        PERFORM remove_compression_policy('measurements_10s', if_exists => true);
        PERFORM add_compression_policy('measurements_10s', INTERVAL '7 days');
    END IF;
END $$;
