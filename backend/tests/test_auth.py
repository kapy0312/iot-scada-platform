"""
登入與操作紀錄的測試
- 前半不需要資料庫
- 後半要真的資料庫：設定 TEST_DATABASE_URL 才會執行（會自動建立 004 的三張表，測完刪掉測試帳號）
執行（在 backend 資料夾）：python -m pytest tests -q
"""
import asyncio
import os

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import auth
from routers import auth as auth_router
from routers import control, rag, system

HEADER = {auth.CSRF_HEADER: "1"}


def make_app() -> FastAPI:
    app = FastAPI()
    for r in (auth_router.router, control.router, system.router, rag.router):
        app.include_router(r)
    return app


# ---------- 不需要資料庫 ----------

def test_密碼雜湊可驗證且每次不同():
    h1, h2 = auth.hash_password("correct-horse"), auth.hash_password("correct-horse")
    assert h1 != h2
    assert auth.verify_password(h1, "correct-horse")
    assert not auth.verify_password(h1, "wrong-horse")
    assert not auth.verify_password("不是雜湊", "correct-horse")


def test_token_只存雜湊():
    t = auth.new_token()
    assert len(t) >= 40 and auth.hash_token(t) != t and len(auth.hash_token(t)) == 64


def test_登入失敗5次鎖定_時間過後解鎖():
    lim = auth.LoginLimiter(max_fails=5, window=300)
    k = auth.limiter_key("1.2.3.4", "Kevin")
    for i in range(4):
        lim.fail(k, now=100 + i)
    assert lim.retry_after(k, now=105) == 0
    lim.fail(k, now=105)
    assert lim.retry_after(k, now=106) > 0
    assert lim.retry_after(k, now=100 + 301) == 0      # 最舊那次超過 5 分鐘
    assert auth.limiter_key("1.2.3.4", " kevin ") == k  # 大小寫、空白不能拿來繞過


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/control/write", {"tag": "motor_enable", "value": 1}),
    ("post", "/api/system/data-source", {"source": "mock"}),
    ("post", "/api/system/simulate-anomaly", {"duration_seconds": 5}),
    ("post", "/api/system/reload-model", None),
    ("delete", "/api/rag/documents/abc", None),
    ("patch", "/api/rag/documents/abc", {"enabled": False}),
])
def test_沒帶header的寫入請求一律拒絕(method, path, body):
    with TestClient(make_app()) as c:
        r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
    assert r.status_code == 403


def test_上傳手冊沒帶header也拒絕():
    with TestClient(make_app()) as c:
        r = c.post("/api/rag/upload", files={"file": ("a.pdf", b"%PDF")})
    assert r.status_code == 403


def test_登入沒帶header拒絕():
    with TestClient(make_app()) as c:
        assert c.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 403


# ---------- 需要真的資料庫 ----------

TEST_URL = os.getenv("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="未設定 TEST_DATABASE_URL")
MIGRATION = os.path.join(os.path.dirname(__file__), "..", "db", "migrations", "004_auth_audit.sql")
USER, PASSWORD = "pytest_operator", "pytest-password-123"


def _sql(query: str, *args):
    async def run():
        conn = await asyncpg.connect(TEST_URL)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()
    return asyncio.run(run())


@pytest.fixture
def db_user(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    for k in ("POSTGRES_PASSWORD",):
        monkeypatch.delenv(k, raising=False)

    async def setup():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.execute(open(MIGRATION, encoding="utf-8").read())
            await conn.execute("DELETE FROM app_users WHERE username = $1", USER)
            await conn.execute("DELETE FROM audit_log WHERE username = $1", USER)
            await conn.execute("INSERT INTO app_users (username, password_hash) VALUES ($1, $2)",
                               USER, auth.hash_password(PASSWORD))
        finally:
            await conn.close()
    asyncio.run(setup())
    auth.limiter._fails.clear()
    yield
    _sql("DELETE FROM app_users WHERE username = $1", USER)   # session 會跟著 CASCADE 刪除
    _sql("DELETE FROM audit_log WHERE username = $1", USER)
    auth.limiter._fails.clear()


def _login(c, password=PASSWORD):
    return c.post("/api/auth/login", json={"username": USER, "password": password}, headers=HEADER)


@needs_db
def test_完整流程_登入寫入登出(db_user, monkeypatch):
    calls = []

    async def fake_write(tag, value):
        calls.append((tag, value))
        return {"value": False}
    monkeypatch.setattr(control, "_write_opcua", fake_write)

    with TestClient(make_app()) as c:
        assert c.get("/api/auth/me").json() == {"user": None}
        assert c.post("/api/control/write", json={"tag": "motor_enable", "value": 1},
                      headers=HEADER).status_code == 401

        r = _login(c)
        assert r.status_code == 200 and r.json()["user"]["username"] == USER
        assert "httponly" in r.headers["set-cookie"].lower()
        assert "samesite=strict" in r.headers["set-cookie"].lower()
        assert c.get("/api/auth/me").json()["user"]["username"] == USER

        r = c.post("/api/control/write", json={"tag": "motor_enable", "value": 1}, headers=HEADER)
        assert r.status_code == 200 and calls == [("motor_enable", 1.0)]

        # 範圍外與不允許的訊號：422，而且不會呼叫 PLC
        assert c.post("/api/control/write", json={"tag": "motor_speed_setpoint", "value": 9999},
                      headers=HEADER).status_code == 422
        assert c.post("/api/control/write", json={"tag": "pressure", "value": 1},
                      headers=HEADER).status_code == 422
        assert len(calls) == 1

        assert c.post("/api/auth/logout", headers=HEADER).status_code == 200
        assert c.get("/api/auth/me").json() == {"user": None}

    rows = _sql("SELECT action, result, params, before FROM audit_log WHERE username = $1 ORDER BY id", USER)
    assert [(r["action"], r["result"]) for r in rows] == [
        ("auth.login", "ok"), ("control.write", "ok"), ("auth.logout", "ok")]
    assert '"motor_enable"' in rows[1]["params"] and rows[1]["before"] == '{"value": false}'


@needs_db
def test_PLC寫入失敗回502並記錄(db_user, monkeypatch):
    async def broken(tag, value):
        raise ConnectionError("PLC 沒回應")
    monkeypatch.setattr(control, "_write_opcua", broken)

    with TestClient(make_app()) as c:
        _login(c)
        r = c.post("/api/control/write", json={"tag": "motor_enable", "value": 0}, headers=HEADER)
    assert r.status_code == 502 and "PLC 沒回應" in r.json()["message"]
    rows = _sql("SELECT result, message FROM audit_log WHERE username = $1 AND action = 'control.write'", USER)
    assert rows[0]["result"] == "error" and "PLC 沒回應" in rows[0]["message"]


@needs_db
def test_密碼錯5次被鎖定_正確密碼也進不去(db_user):
    with TestClient(make_app()) as c:
        for _ in range(5):
            assert _login(c, "wrong-password").status_code == 401
        r = _login(c)
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


@needs_db
def test_停用帳號後原本的登入立即失效(db_user):
    with TestClient(make_app()) as c:
        _login(c)
        assert c.get("/api/auth/me").json()["user"] is not None
        _sql("UPDATE app_users SET is_active = false WHERE username = $1", USER)
        assert c.get("/api/auth/me").json() == {"user": None}
        assert _login(c).status_code == 401


@needs_db
def test_過期的登入無效(db_user):
    with TestClient(make_app()) as c:
        _login(c)
        _sql("UPDATE app_sessions SET expires_at = now() - interval '1 second' "
             "WHERE user_id = (SELECT id FROM app_users WHERE username = $1)", USER)
        assert c.get("/api/auth/me").json() == {"user": None}


@needs_db
def test_偽造cookie無效(db_user):
    with TestClient(make_app()) as c:
        c.cookies.set(auth.COOKIE_NAME, auth.new_token())
        assert c.get("/api/auth/me").json() == {"user": None}
        assert c.post("/api/system/reload-model", headers=HEADER).status_code == 401
