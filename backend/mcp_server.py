"""
IIoT SCADA MCP Server（本機、唯讀）

由 Claude Desktop 以 stdio 方式啟動，讓 Claude 能查：
  異常事件 → 前後趨勢 → 對應手冊章節 → 設備規格，以及 OEE、趨勢預警紀錄
刻意不提供任何寫入 PLC 或修改資料的工具。

工具本體在 mcp_tools/tools.py（雲端 Lambda 版 mcp_cloud/server.py 共用同一份），這裡只指定資料來源是本機。
手動測試（不經過 Claude）：python tools/test_mcp_tools.py
"""
import sys
from pathlib import Path

# Claude Desktop 啟動時的工作目錄不固定，先把 backend/ 放進 import 路徑
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from mcp_tools.common import LOCAL_TZ  # noqa: E402,F401（test_mcp_tools 會用）
from mcp_tools.sources import LocalSource  # noqa: E402
from mcp_tools.tools import INSTRUCTIONS, register  # noqa: E402

server = MCPServer(name="iot-scada", instructions=INSTRUCTIONS)
TOOLS = register(server, LocalSource())

# 測試腳本直接呼叫用
list_anomalies = TOOLS["list_anomalies"]
get_trend = TOOLS["get_trend"]
search_manual = TOOLS["search_manual"]
get_device_info = TOOLS["get_device_info"]
get_oee = TOOLS["get_oee"]
list_trend_warnings = TOOLS["list_trend_warnings"]

if __name__ == "__main__":
    server.run()  # 預設 stdio，由 Claude Desktop 啟動
