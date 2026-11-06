"""
連線設定：密碼只放在 repo 根目錄的 .env（已被 gitignore 排除），程式碼裡不寫任何密碼

讀取順序：
1. 環境變數 DATABASE_URL（docker compose 會自己組好傳進來）
2. 用 .env 裡的 POSTGRES_PASSWORD 組出本機網址（直接跑 uvicorn、MCP server、訓練腳本時）

Claude Desktop 啟動 MCP server 時工作目錄不固定，所以 .env 依「這個檔案的位置」去找，不依工作目錄
"""
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

# 已經存在的環境變數優先（docker compose、Claude Desktop 設定檔傳進來的）
load_dotenv(ENV_FILE, override=False)


class ConfigError(RuntimeError):
    """設定缺漏：要讓人一看就知道去哪裡補"""


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise ConfigError(
            f"找不到資料庫密碼：請在 {ENV_FILE} 加上 POSTGRES_PASSWORD=...（範本見 .env.example）"
        )
    user = os.getenv("POSTGRES_USER", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5435")
    db = os.getenv("POSTGRES_DB", "iotscada")
    # 密碼裡若有 @ : / 之類的字元，要先編碼，否則網址會被拆錯
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{db}"


def masked(url: str) -> str:
    """印 log 用：把密碼換成 ***"""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    cred, host = rest.rsplit("@", 1)
    user = cred.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
