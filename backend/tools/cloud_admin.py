"""
Tiger Cloud 管理工具（筆電沒裝 psql 也能用；連線字串從 .env 讀，不會印出密碼）

用法（在 backend 資料夾）：
    python -m tools.cloud_admin apply db/cloud/001_schema.sql   用 tsdbadmin（TIGER_URL）執行 SQL 檔
    python -m tools.cloud_admin password edge_writer            設定帳號密碼（互動輸入）
    python -m tools.cloud_admin check                           用兩個帳號實際連線，確認權限正確

設定密碼時，密碼在本機先換算成 SCRAM-SHA-256 雜湊才送出（跟 psql 的 \\password 一樣），
雲端紀錄裡不會出現密碼原文
"""
import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg

from core.config import ConfigError, get_cloud_url, masked

ROLES = ("edge_writer", "mcp_reader")
ROLE_ENV = {"edge_writer": "TIGER_EDGE_URL", "mcp_reader": "TIGER_READER_URL"}
MIN_LEN = 16
ITERATIONS = 4096


def scram_verifier(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """PostgreSQL 存密碼的格式：SCRAM-SHA-256$次數:鹽$StoredKey:ServerKey"""
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def ask_password() -> str:
    print("建議用隨機密碼：python -c \"import secrets; print(secrets.token_urlsafe(24))\"")
    while True:
        pw = getpass.getpass(f"輸入密碼（至少 {MIN_LEN} 個字元，只用英文、數字、- 和 _）：")
        if len(pw) < MIN_LEN or not all(c.isascii() and (c.isalnum() or c in "-_") for c in pw):
            print(f"不符合：至少 {MIN_LEN} 個字元，只能用英文、數字、- 和 _（放進連線網址才不會出錯）")
            continue
        if pw != getpass.getpass("再輸入一次："):
            print("兩次輸入不一樣，請重來")
            continue
        return pw


async def connect(kind: str) -> asyncpg.Connection:
    url = get_cloud_url(kind)
    print(f"連線：{masked(url)}")
    return await asyncpg.connect(url, timeout=15)


async def cmd_apply(path: str) -> int:
    sql = Path(path).read_text(encoding="utf-8")
    conn = await connect("admin")
    try:
        await conn.execute(sql)
    finally:
        await conn.close()
    print(f"[成功] 已執行 {path}")
    return 0


async def cmd_password(role: str) -> int:
    pw = ask_password()
    conn = await connect("admin")
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role):
            print(f"[失敗] 雲端沒有 {role} 帳號：請先執行 apply db/cloud/002_roles.sql")
            return 1
        # ALTER ROLE 不能用參數，雜湊只含 base64 字元與 $ : ，放進字串常數是安全的
        await conn.execute(f"ALTER ROLE {role} PASSWORD '{scram_verifier(pw)}'")
        admin = urlsplit(get_cloud_url("admin"))
    finally:
        await conn.close()
    print(f"[成功] {role} 密碼已設定")
    print("請把下面這行加到 .env（<密碼> 換成剛剛輸入的密碼）：")
    host = admin.netloc.rsplit("@", 1)[-1]          # 主機:port，不含 tsdbadmin 的帳密
    print(f"{ROLE_ENV[role]}=postgres://{role}:<密碼>@{host}{admin.path}?sslmode=require")
    return 0


async def _expect_denied(conn, sql: str) -> str:
    try:
        async with conn.transaction():
            await conn.execute(sql)
            raise RuntimeError("rollback")      # 萬一真的成功了也不留下資料
    except RuntimeError:
        return "❌ 竟然可以執行（權限太大）"
    except (asyncpg.InsufficientPrivilegeError, asyncpg.ReadOnlySQLTransactionError) as e:
        return f"✅ 被拒絕（{type(e).__name__}）"


async def cmd_check() -> int:
    ok = True
    for role, kind in (("edge_writer", "writer"), ("mcp_reader", "reader")):
        print(f"\n== {role} ==")
        try:
            conn = await connect(kind)
        except ConfigError as e:
            print(f"略過：{e}")
            continue
        except Exception as e:
            print(f"❌ 連不上：{type(e).__name__}: {e}")
            ok = False
            continue
        try:
            n = await conn.fetchval("SELECT count(*) FROM measurements_10s")
            m = await conn.fetchval("SELECT count(*) FROM anomaly_incidents")
            print(f"✅ 可以讀取：measurements_10s {n} 筆、anomaly_incidents {m} 筆")
            if await conn.fetchval("SELECT to_regclass('oee_hourly') IS NOT NULL"):
                print(f"✅ 可以讀取：oee_hourly {await conn.fetchval('SELECT count(*) FROM oee_hourly')} 筆")
            else:
                print("（雲端還沒有 oee_hourly：執行 db/cloud/003_oee.sql 後才會同步每小時 OEE）")
            for label, sql in (
                ("刪除資料", "DELETE FROM measurements_10s WHERE false"),
                ("改表結構", "ALTER TABLE measurements_10s ADD COLUMN _x int"),
            ) + ((("寫入資料", "INSERT INTO anomaly_incidents (id, device_id, started_at, ended_at, records, "
                   "level, severity, peak, updated_at) VALUES (-1, 'x', now(), now(), 1, 'x', 0, '{}', now())"),)
                 if role == "mcp_reader" else ()):
                result = await _expect_denied(conn, sql)
                ok &= result.startswith("✅")
                print(f"{label}：{result}")
        finally:
            await conn.close()
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Tiger Cloud 管理工具")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="用 tsdbadmin 執行 SQL 檔")
    a.add_argument("path")
    pw = sub.add_parser("password", help="設定帳號密碼")
    pw.add_argument("role", choices=ROLES)
    sub.add_parser("check", help="用兩個帳號連線並確認權限")
    args = p.parse_args()
    try:
        if args.cmd == "apply":
            return asyncio.run(cmd_apply(args.path))
        if args.cmd == "password":
            return asyncio.run(cmd_password(args.role))
        return asyncio.run(cmd_check())
    except ConfigError as e:
        print(f"[失敗] {e}")
        return 1
    except asyncpg.InvalidPasswordError:
        print("[失敗] 密碼錯誤：檢查 .env 裡這個帳號的連線字串")
        return 1
    except (TimeoutError, OSError) as e:
        print(f"[失敗] 連不上 Tiger（{type(e).__name__}）：到 Console 確認服務沒有暫停；"
              f"再用 Test-NetConnection 主機 -Port 埠號 確認網路沒擋")
        return 1


if __name__ == "__main__":
    sys.exit(main())
