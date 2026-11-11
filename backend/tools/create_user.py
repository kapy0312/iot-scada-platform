"""
管理登入帳號（密碼用互動輸入，不會出現在指令紀錄或螢幕上）

用法（在 backend 資料夾）：
    python -m tools.create_user kevin             建立帳號
    python -m tools.create_user kevin --reset     重設密碼，並讓這個帳號所有已登入的瀏覽器登出
    python -m tools.create_user kevin --disable   停用帳號（立即失效，紀錄保留）
    python -m tools.create_user kevin --enable    重新啟用
    python -m tools.create_user --list            列出所有帳號

需要先執行 db/migrations/004_auth_audit.sql
"""
import argparse
import asyncio
import getpass
import re
import sys

import asyncpg

from core.auth import MIN_PASSWORD_LENGTH, hash_password
from core.config import ConfigError, get_database_url

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def ask_password() -> str:
    while True:
        pw = getpass.getpass(f"輸入密碼（至少 {MIN_PASSWORD_LENGTH} 個字元，輸入時不會顯示）：")
        if len(pw) < MIN_PASSWORD_LENGTH:
            print(f"太短，至少 {MIN_PASSWORD_LENGTH} 個字元")
            continue
        if pw != getpass.getpass("再輸入一次："):
            print("兩次輸入不一樣，請重來")
            continue
        return pw


async def run(args) -> int:
    try:
        conn = await asyncpg.connect(get_database_url(), timeout=5)
    except ConfigError as e:
        print(f"[失敗] {e}")
        return 1
    except Exception as e:
        print(f"[失敗] 連不上資料庫（{type(e).__name__}）：確認 timescaledb 容器已啟動")
        return 1
    try:
        if args.list:
            rows = await conn.fetch(
                "SELECT username, role, is_active, created_at FROM app_users ORDER BY id")
            if not rows:
                print("目前沒有任何帳號")
            for r in rows:
                state = "啟用" if r["is_active"] else "停用"
                print(f"{r['username']:<20} {r['role']:<10} {state}  建立於 {r['created_at'].astimezone():%Y-%m-%d %H:%M}")
            return 0

        name = args.username
        exists = await conn.fetchval("SELECT id FROM app_users WHERE username = $1", name)

        if args.disable or args.enable:
            if not exists:
                print(f"[失敗] 帳號 {name} 不存在")
                return 1
            await conn.execute("UPDATE app_users SET is_active = $2, updated_at = now() WHERE id = $1",
                               exists, bool(args.enable))
            if args.disable:
                await conn.execute(
                    "UPDATE app_sessions SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL", exists)
            print(f"[成功] {name} 已{'啟用' if args.enable else '停用'}")
            return 0

        if args.reset:
            if not exists:
                print(f"[失敗] 帳號 {name} 不存在，要建立請拿掉 --reset")
                return 1
            pw = ask_password()
            async with conn.transaction():
                await conn.execute("UPDATE app_users SET password_hash = $2, updated_at = now() WHERE id = $1",
                                   exists, hash_password(pw))
                n = await conn.execute(
                    "UPDATE app_sessions SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL", exists)
            print(f"[成功] {name} 密碼已更新，已登出 {n.split()[-1]} 個登入中的瀏覽器")
            return 0

        if exists:
            print(f"[失敗] 帳號 {name} 已存在；要改密碼請加 --reset")
            return 1
        if not USERNAME_RE.match(name):
            print("[失敗] 帳號只能用英文、數字、底線、點、減號，長度 3～32")
            return 1
        pw = ask_password()
        await conn.execute(
            "INSERT INTO app_users (username, password_hash, role) VALUES ($1, $2, 'operator')",
            name, hash_password(pw))
        print(f"[成功] 已建立帳號 {name}（operator）")
        return 0
    except asyncpg.UndefinedTableError:
        print("[失敗] 找不到 app_users 表：請先執行 db/migrations/004_auth_audit.sql")
        return 1
    finally:
        await conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="管理 SCADA 登入帳號")
    p.add_argument("username", nargs="?")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--reset", action="store_true", help="重設密碼")
    g.add_argument("--disable", action="store_true", help="停用帳號")
    g.add_argument("--enable", action="store_true", help="重新啟用帳號")
    g.add_argument("--list", action="store_true", help="列出所有帳號")
    args = p.parse_args()
    if not args.list and not args.username:
        p.error("請指定帳號名稱，或用 --list")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
