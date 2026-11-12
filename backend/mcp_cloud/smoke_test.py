"""
雲端 MCP 冒煙測試（只用 Python 標準函式庫）

用法（在 backend 資料夾）：
  python mcp_cloud/smoke_test.py <MCP 網址>
網址要包含 /mcp，例如 http://127.0.0.1:8080/mcp 或 https://xxxx.lambda-url.us-east-1.on.aws/mcp
密碼從環境變數 MCP_AUTH_TOKEN 讀（不要把密碼寫在指令裡，會留在終端機歷史紀錄）；
沒設定時會讀 repo 根目錄 .env 的 MCP_AUTH_TOKEN
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def env_token() -> str | None:
    if os.getenv("MCP_AUTH_TOKEN"):
        return os.environ["MCP_AUTH_TOKEN"]
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("MCP_AUTH_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def call(url, method, params=None, token=None, rid=1):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "mcp-protocol-version": "2025-06-18"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    url = sys.argv[1].rstrip("/")
    token = env_token()
    if not token:
        sys.exit("找不到 MCP_AUTH_TOKEN（環境變數或 .env）")
    ok = True

    s, _ = call(url, "tools/list")
    print(f"1) 不帶密碼：HTTP {s}（預期 401）")
    ok &= s == 401
    s, _ = call(url, "tools/list", token="wrong-" + token)
    print(f"2) 密碼錯誤：HTTP {s}（預期 401）")
    ok &= s == 401

    s, r = call(url, "tools/list", token=token)
    names = sorted(t["name"] for t in r["result"]["tools"]) if s == 200 else r
    print(f"3) 列出工具：HTTP {s}，{names}")
    ok &= s == 200 and len(names) == 6

    for i, (tool, args) in enumerate([("get_device_info", {"device_id": "S7-1511T-SIM"}),
                                      ("list_anomalies", {"hours": 24}),
                                      ("search_manual", {"query": "溫度持續上升 可能原因", "top_k": 2})], start=4):
        s, r = call(url, "tools/call", {"name": tool, "arguments": args}, token=token, rid=i)
        if s == 200 and not r["result"].get("isError"):
            data = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
            brief = {"get_device_info": lambda d: f"資料中的設備 {[x['device_id'] for x in d['devices_in_db_last_30_days']]}、手冊 {len(d['manuals'])} 份",
                     "list_anomalies": lambda d: f"最近 24 小時事件 {d['incident_count']} 筆",
                     "search_manual": lambda d: f"最相關章節 {[x['section'][:20] for x in d['results']]}"}[tool](data)
            print(f"{i}) {tool}：OK，{brief}")
        else:
            ok = False
            print(f"{i}) {tool}：失敗 HTTP {s} {str(r)[:300]}")

    print("\n全部通過" if ok else "\n有項目失敗，把上面的輸出貼給我")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
