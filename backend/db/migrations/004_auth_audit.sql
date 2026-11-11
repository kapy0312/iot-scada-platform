-- 004：登入帳號、登入狀態（session）、操作紀錄
-- 執行：Get-Content backend/db/migrations/004_auth_audit.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：004_auth_audit_rollback.sql（操作紀錄會一起刪除，回滾前先匯出）

BEGIN;

-- 帳號：密碼只存 argon2 雜湊，不存原文；沒有預設帳號，用 python -m tools.create_user 建立
CREATE TABLE IF NOT EXISTS app_users (
    id            bigserial PRIMARY KEY,
    username      text        NOT NULL UNIQUE,
    password_hash text        NOT NULL,
    role          text        NOT NULL DEFAULT 'operator',   -- 目前只有 operator，之後可加 viewer
    is_active     boolean     NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT app_users_role_chk CHECK (role IN ('operator', 'viewer'))
);

-- 登入狀態：瀏覽器 cookie 裡是隨機 token，這裡只存它的 sha256，資料庫外洩也拿不到可用的 token
CREATE TABLE IF NOT EXISTS app_sessions (
    token_hash   text        PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    client_ip    text,
    revoked_at   timestamptz
);
CREATE INDEX IF NOT EXISTS app_sessions_user_idx ON app_sessions (user_id);

-- 操作紀錄：每次改動狀態的操作與登入成功／失敗都記一筆，只新增不修改
CREATE TABLE IF NOT EXISTS audit_log (
    id         bigserial   PRIMARY KEY,
    time       timestamptz NOT NULL DEFAULT now(),
    username   text,                      -- 登入失敗時是輸入的帳號名稱，可能不存在
    client_ip  text,
    action     text        NOT NULL,      -- 例：control.write、system.data_source、auth.login
    params     jsonb,                     -- 操作參數，例：{"tag": "motor_enable", "value": 1}
    before     jsonb,                     -- 寫入前的值（讀得到才有）
    result     text        NOT NULL,      -- ok / error / denied
    message    text
);
CREATE INDEX IF NOT EXISTS audit_log_time_idx ON audit_log (time DESC);

COMMIT;

-- 確認：應該看到 3 張表
SELECT table_name FROM information_schema.tables
WHERE table_name IN ('app_users', 'app_sessions', 'audit_log') ORDER BY table_name;
