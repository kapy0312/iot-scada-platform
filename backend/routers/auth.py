from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core import auth
from db.audit import write_audit

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _public(user: dict) -> dict:
    out = {"username": user["username"], "role": user["role"]}
    if user.get("expires_at"):
        out["expires_at"] = user["expires_at"].isoformat()
    return out


@router.post("/api/auth/login")
async def login(body: LoginRequest, request: Request, response: Response):
    if request.headers.get(auth.CSRF_HEADER) != "1":
        raise HTTPException(403, f"缺少 {auth.CSRF_HEADER} header")
    ip = auth.client_ip(request)
    key = auth.limiter_key(ip, body.username)

    wait = auth.limiter.retry_after(key)
    if wait:
        await write_audit("auth.login", "denied", username=body.username, client_ip=ip,
                          message=f"失敗次數過多，鎖定中（剩 {wait} 秒）")
        raise HTTPException(429, f"登入失敗次數過多，請 {wait} 秒後再試", headers={"Retry-After": str(wait)})

    try:
        user = await auth.authenticate(body.username, body.password)
    except Exception as e:
        print(f"[AUTH] 登入查詢失敗：{type(e).__name__}: {e}")
        raise HTTPException(503, "資料庫連不上，暫時無法登入")

    if user is None:
        auth.limiter.fail(key)
        await write_audit("auth.login", "error", username=body.username, client_ip=ip, message="帳號或密碼錯誤")
        raise HTTPException(401, "帳號或密碼錯誤")

    auth.limiter.reset(key)
    token, expires = await auth.create_session(user["id"], ip)
    response.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=int(auth.SESSION_HOURS * 3600),
        httponly=True, samesite="strict", secure=auth.COOKIE_SECURE, path="/",
    )
    await write_audit("auth.login", "ok", username=user["username"], client_ip=ip)
    return {"status": "ok", "user": _public({**user, "expires_at": expires})}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    # 登出不需要先確認身分：沒登入時呼叫也只是清掉 cookie
    token = request.cookies.get(auth.COOKIE_NAME)
    user = None
    try:
        user = await auth.session_user(token)
        await auth.revoke_session(token)
    except Exception as e:
        print(f"[AUTH] 登出時撤銷 session 失敗：{type(e).__name__}: {e}")
    response.delete_cookie(auth.COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=auth.COOKIE_SECURE)
    if user:
        await write_audit("auth.logout", "ok", username=user["username"], client_ip=auth.client_ip(request))
    return {"status": "ok"}


@router.get("/api/auth/me")
async def me(user: dict | None = Depends(auth.current_user)):
    """前端開啟時呼叫：沒登入回 {"user": null}（不是錯誤）"""
    return {"user": _public(user) if user else None}
