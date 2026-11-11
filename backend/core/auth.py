"""
登入驗證：帳號密碼、登入狀態（session）、寫入權限檢查

流程（類比門禁卡）：
1. POST /api/auth/login 驗證帳號密碼 → 發一張「門禁卡」（隨機 token）放在瀏覽器 HttpOnly cookie
   資料庫只存 token 的 sha256，網頁上的 JavaScript 也讀不到 cookie
2. 之後每次改動狀態的請求，require_operator 會：
   - 檢查有沒有帶 X-SCADA-Request header（別的網站沒辦法偷偷帶這個 header，用來擋跨站偽造請求）
   - 用 cookie 查出是誰、卡片有沒有過期或被撤銷
3. 登出或過期後卡片失效，要重新登入

設定（.env，都可以不設）：
- SESSION_HOURS：登入有效時數，預設 12
- COOKIE_SECURE：改成 true 時 cookie 只在 HTTPS 傳送；目前區網是 HTTP，預設 false
"""
import hashlib
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

from db.pool import acquire

COOKIE_NAME = "scada_session"
CSRF_HEADER = "X-SCADA-Request"
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes")
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()
# 帳號不存在時也做一次雜湊比對，讓「帳號不存在」和「密碼錯誤」花的時間差不多，不讓人用回應時間猜帳號
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


# ---------- 密碼 ----------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        # ValueError 涵蓋資料庫裡的雜湊被改成非 ASCII 等損壞的情況（UnicodeEncodeError 是它的子類）
        return False


# ---------- 登入失敗次數限制 ----------

class LoginLimiter:
    """同一個「來源 IP＋帳號」在 window 秒內失敗 max_fails 次就鎖住，直到最舊的失敗紀錄超過 window
    存在記憶體：backend 重啟就歸零（單機使用夠用，多台 backend 時要改存資料庫）"""

    def __init__(self, max_fails: int = 5, window: float = 300):
        self.max_fails = max_fails
        self.window = window
        self._fails: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque:
        q = self._fails[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, key: str, now: float | None = None) -> int:
        """還要等幾秒才能再試；0 代表沒被鎖"""
        now = time.monotonic() if now is None else now
        q = self._trim(key, now)
        if len(q) < self.max_fails:
            return 0
        return max(1, int(self.window - (now - q[0])) + 1)

    def fail(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._trim(key, now).append(now)

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


limiter = LoginLimiter()


def limiter_key(ip: str | None, username: str) -> str:
    return f"{ip or '?'}|{username.strip().lower()}"


# ---------- session ----------

def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def authenticate(username: str, password: str) -> dict | None:
    """帳號密碼正確且帳號啟用中才回傳使用者，其他情況一律回 None（不透露是哪一項錯）"""
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash, role, is_active FROM app_users WHERE username = $1",
            username.strip(),
        )
    if row is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(row["password_hash"], password) or not row["is_active"]:
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


async def create_session(user_id: int, ip: str | None) -> tuple[str, datetime]:
    token = new_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    async with acquire() as conn:
        await conn.execute(
            "INSERT INTO app_sessions (token_hash, user_id, expires_at, client_ip) VALUES ($1, $2, $3, $4)",
            hash_token(token), user_id, expires, ip,
        )
    return token, expires


async def session_user(token: str | None) -> dict | None:
    """cookie 對應的使用者；過期、撤銷、帳號停用都回 None"""
    if not token:
        return None
    async with acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE app_sessions s SET last_seen_at = now()
            FROM app_users u
            WHERE s.token_hash = $1 AND s.user_id = u.id
              AND s.revoked_at IS NULL AND s.expires_at > now() AND u.is_active
            RETURNING u.id, u.username, u.role, s.expires_at
        """, hash_token(token))
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"], "expires_at": row["expires_at"]}


async def revoke_session(token: str | None) -> None:
    if not token:
        return
    async with acquire() as conn:
        await conn.execute(
            "UPDATE app_sessions SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL",
            hash_token(token),
        )


# ---------- FastAPI 相依（Depends） ----------

async def current_user(request: Request) -> dict | None:
    try:
        return await session_user(request.cookies.get(COOKIE_NAME))
    except Exception as e:
        print(f"[AUTH] 查詢登入狀態失敗：{type(e).__name__}: {e}")
        raise HTTPException(503, "無法確認登入狀態：資料庫連不上")


async def require_operator(request: Request) -> dict:
    """會改動狀態的 API 都掛這個；通過後回傳使用者，供操作紀錄使用"""
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, f"缺少 {CSRF_HEADER} header，拒絕執行")
    user = await current_user(request)
    if user is None:
        raise HTTPException(401, "請先登入")
    if user["role"] != "operator":
        raise HTTPException(403, "此帳號沒有操作權限")
    return user
