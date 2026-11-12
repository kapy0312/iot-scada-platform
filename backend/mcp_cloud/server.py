"""
IIoT SCADA 雲端 MCP Server（Phase 3，部署在 AWS Lambda）

claude.ai（或 Claude Code）→ Lambda Function URL /mcp → 這支程式 → Tiger Cloud（mcp_reader 唯讀）＋ DeepInfra（問題轉向量）
工具跟本機 Claude Desktop 用的是同一份（mcp_tools/），只是資料來源換成雲端。

驗證：每個請求都要帶 Authorization: Bearer <MCP_AUTH_TOKEN>，沒帶或不對回 401；沒設定密碼時一律拒絕（寧可不能用，也不要變成公開）
傳輸：Streamable HTTP、無狀態（stateless），每個請求獨立處理，適合 Lambda

設定（三個值）：
  Lambda 上：環境變數 SECRET_ID 指向 Secrets Manager 的 secret（JSON，含下面三個 key），啟動時讀一次
  本機測試：直接放環境變數或 repo 根目錄 .env
    TIGER_READER_URL   Tiger Cloud 的 mcp_reader 連線字串（唯讀）
    DEEPINFRA_API_KEY  DeepInfra 金鑰
    MCP_AUTH_TOKEN     連這個 MCP 用的密碼（自己產生一串夠長的亂碼）

本機啟動（在 backend 資料夾）：python -m mcp_cloud.server   → http://127.0.0.1:8080/mcp
"""
import hmac
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import core.config  # noqa: E402,F401（本機測試時載入 repo 根目錄的 .env；容器裡沒有 .env，不影響）

KEYS = ("TIGER_READER_URL", "DEEPINFRA_API_KEY", "MCP_AUTH_TOKEN")


def load_secrets() -> None:
    """Lambda 上從 Secrets Manager 讀三個值放進環境變數；已經有的不覆蓋（本機測試用環境變數）"""
    secret_id = os.getenv("SECRET_ID")
    if not secret_id or all(os.getenv(k) for k in KEYS):
        return
    import boto3
    value = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"]
    for k, v in json.loads(value).items():
        if k in KEYS and v and not os.getenv(k):
            os.environ[k] = v


load_secrets()

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from mcp_tools.sources import CloudSource  # noqa: E402
from mcp_tools.tools import INSTRUCTIONS, register  # noqa: E402

server = MCPServer(name="iot-scada-cloud", instructions=INSTRUCTIONS)
TOOLS = register(server, CloudSource())

# Function URL 的網址每個人不同，不做 Host 檢查（DNS rebinding 防護是給本機瀏覽器情境用的；這裡靠 Bearer 密碼）
_mcp_app = server.streamable_http_app(
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


class BearerAuth:
    """ASGI 中介層：/health 不用密碼（給 Lambda Web Adapter 檢查有沒有啟動好），其他路徑都要 Bearer 密碼"""

    def __init__(self, app, token: str | None):
        self.app = app
        self.token = (token or "").encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/health":
            return await JSONResponse({"status": "ok"})(scope, receive, send)
        auth = dict(scope.get("headers") or []).get(b"authorization", b"")
        ok = bool(self.token) and auth.startswith(b"Bearer ") and hmac.compare_digest(auth[7:].strip(), self.token)
        if not ok:
            resp = JSONResponse({"error": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="iot-scada"'})
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


app = BearerAuth(_mcp_app, os.getenv("MCP_AUTH_TOKEN"))

if __name__ == "__main__":
    import uvicorn
    if not os.getenv("MCP_AUTH_TOKEN"):
        print("警告：沒有設定 MCP_AUTH_TOKEN，所有請求都會被拒絕（401）", file=sys.stderr)
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8080")))
