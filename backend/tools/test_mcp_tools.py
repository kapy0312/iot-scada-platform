"""
不經過 Claude，直接呼叫 MCP 的 4 個工具，確認資料庫、Qdrant、Ollama 都接得上。

用法（在 backend 資料夾）：
    python tools/test_mcp_tools.py
    python tools/test_mcp_tools.py S7-1511T-MOCK 48     # 指定 device_id 與往前查幾小時
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server import LOCAL_TZ, get_device_info, get_trend, list_anomalies, search_manual  # noqa: E402


def show(title: str, data) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])


async def main():
    device_id = sys.argv[1] if len(sys.argv) > 1 else "S7-1511T-MOCK"
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24

    show("get_device_info", await get_device_info(device_id))

    anomalies = await list_anomalies(device_id=device_id, hours=hours)
    show("list_anomalies", anomalies)

    if anomalies["incidents"]:
        first = anomalies["incidents"][0]
        t0 = datetime.fromisoformat(first["start"]).replace(tzinfo=LOCAL_TZ)
        start = (t0 - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
        end = (t0 + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
        show(f"get_trend（最近一次事件前後 10 分鐘：{start} ~ {end}）", await get_trend(device_id, start, end))
    else:
        print("\n（這段時間沒有異常事件，略過 get_trend；可先在前端按「模擬異常」再測）")

    show("search_manual", await search_manual("溫度持續上升 壓力偏高 可能原因與處置", device_id, top_k=3))


if __name__ == "__main__":
    asyncio.run(main())
