-- 008：訊號門檻覆寫 tag_limits（在畫面上改正常範圍、警戒值、危險值、刻度）
-- 執行：Get-Content backend/db/migrations/008_tag_limits.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：008_tag_limits_rollback.sql（刪掉覆寫，全部回到 core/device_profile.py 的預設值）
--
-- 只存「改過的」訊號；表裡沒有的訊號用 device_profile.py 的預設值，所以不跑 008 系統也照常運作
-- 名稱、單位、小數位、儀表編號、PLC 變數屬於結構，不在這裡改
-- 每次修改的前後值另外記在 audit_log（action＝limits.update / limits.reset）

BEGIN;

CREATE TABLE IF NOT EXISTS tag_limits (
    device_model text             NOT NULL,          -- 設備型號（S7-1511T；模擬 -SIM、-MOCK 共用同一份）
    tag_name     text             NOT NULL,
    normal_lo    double precision NOT NULL,
    normal_hi    double precision NOT NULL,
    warning      double precision NOT NULL,
    danger       double precision NOT NULL,
    scale_lo     double precision NOT NULL,          -- 畫面刻度（只影響顯示）
    scale_hi     double precision NOT NULL,
    updated_at   timestamptz      NOT NULL DEFAULT now(),
    updated_by   text,
    PRIMARY KEY (device_model, tag_name),
    -- 刻度最小 ≤ 正常下限 < 正常上限 < 警戒 < 危險 ≤ 刻度最大（API 也會先檢查，這裡是最後一道）
    CONSTRAINT tag_limits_order_chk CHECK (
        scale_lo <= normal_lo AND normal_lo < normal_hi AND normal_hi < warning
        AND warning < danger AND danger <= scale_hi
    )
);

COMMIT;

-- 確認：應該看到 0 筆（還沒改過任何門檻）
SELECT count(*) AS rows FROM tag_limits;
