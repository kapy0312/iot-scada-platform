# IIoT SCADA Platform 完整教學手冊 v2.0

> 本手冊目標：讓你從「照著做」進化到「真正理解為什麼這樣設計」

---

## 目錄

1. [專案全貌與目標](#1-專案全貌與目標)
2. [系統架構圖](#2-系統架構圖)
3. [技術選型說明](#3-技術選型說明)
4. [完整資料流解析](#4-完整資料流解析)
5. [後端每個檔案的作用](#5-後端每個檔案的作用)
6. [前端每個檔案的作用](#6-前端每個檔案的作用)
7. [資料庫設計說明](#7-資料庫設計說明)
8. [ML 模型說明](#8-ml-模型說明)
9. [Ollama AI 診斷說明](#9-ollama-ai-診斷說明)
10. [Git 分支說明](#10-git-分支說明)
11. [套件總覽](#11-套件總覽)
12. [常見問題與解法](#12-常見問題與解法)
13. [未來可擴充方向](#13-未來可擴充方向)

---

## 1. 專案全貌與目標

### 這個專案在做什麼

用一句話說：**把工廠裡的 PLC 設備資料，透過網路即時顯示在網頁儀表板上，並用 AI 自動偵測異常、生成診斷說明。**

傳統工廠的問題：
- 設備資料只能在 HMI 現場看，無法遠端監控
- 異常發生後才知道，沒有預警
- 資料沒有保存，無法回溯分析
- 異常原因需要有經驗的工程師判斷

這個系統解決了什麼：
- 任何有瀏覽器的裝置都能即時看到設備狀態
- ML 模型持續分析數據，自動發出異常警報
- 所有資料存進時序資料庫，可以查詢任意時間段
- 本地 LLM 根據趨勢資料自動生成診斷說明文字

### 系統分為四個階段

| 階段 | 內容 | 狀態 |
|---|---|---|
| Phase 1 | PLC 連線 + 即時儀表板 | ✅ 完成 |
| Phase 2 | 資料庫持久化 | ✅ 完成 |
| Phase 3 | ML 異常偵測 | ✅ 完成 |
| Phase 4 | Ollama AI 診斷 | ✅ 完成 |

### Git 分支對應設備

| 分支 | 設備 | 協議 |
|---|---|---|
| `main` | Siemens S7-1511T | OPC UA |
| `feature/fx5u-slmp` | Mitsubishi FX5U（Mock Server）| SLMP |

---

## 2. 系統架構圖

```
┌─────────────────────────────────────────────────────────────────┐
│                        OT 設備層                                  │
│                                                                   │
│  ┌─────────────────┐          ┌─────────────────┐               │
│  │ Siemens S7-1511T│          │ Mitsubishi FX5U │               │
│  │  OPC UA Server  │          │  SLMP Protocol  │               │
│  │ 192.168.0.10    │          │ Mock:127.0.0.1  │               │
│  └────────┬────────┘          └────────┬────────┘               │
└───────────┼─────────────────────────────┼───────────────────────┘
            │ opc.tcp://192.168.0.10:4840  │ TCP:5011
            ▼                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Python 後端（FastAPI）                         │
│                                                                   │
│  main.py ──────── 應用進入點，lifespan 管理背景任務               │
│      │                                                            │
│      ├── core/plc_simulator.py ── PLC 輪詢（OPC UA / SLMP）     │
│      │         │                                                  │
│      │         ├── core/ws_manager.py ── WebSocket 連線管理      │
│      │         ├── db/writer.py ── 寫入 TimescaleDB             │
│      │         ├── ml/inferencer.py ── Isolation Forest 推論    │
│      │         └── ml/ollama_analyzer.py ── AI 診斷（Tailscale）│
│      │                                                            │
│      ├── routers/realtime.py ── /ws/realtime 端點               │
│      └── routers/control.py ── /api/control/write 端點          │
│                                                                   │
└──────────┬──────────────────────────────────────────────────────┘
           │ WebSocket          │ asyncpg          │ httpx
           ▼                    ▼                  ▼
┌──────────────┐  ┌─────────────────────┐  ┌──────────────────────┐
│ React 前端   │  │ TimescaleDB（Docker）│  │ Ollama（桌電）        │
│              │  │                     │  │ 100.89.23.28:11434   │
│ App.tsx      │  │ plc_measurements    │  │ qwen3:14b            │
│ GaugeCard    │  │ time|device|tag|val │  │ 透過 Tailscale VPN   │
│ RealtimeChart│  │                     │  └──────────────────────┘
│ ControlPanel │  │ 每秒寫入 N 筆       │
│ AnomalyPanel │  │（每個 tag 一筆）    │
└──────────────┘  └─────────────────────┘
```

### 資料的三條路徑

```
路徑一（即時顯示）：
PLC → 每秒讀值 → ML推論 → Ollama診斷（異常時）→ WebSocket廣播 → 前端

路徑二（資料儲存）：
PLC → 每秒讀值 → asyncpg → TimescaleDB

路徑三（AI趨勢分析）：
異常觸發 → 查詢TimescaleDB 5分鐘趨勢 → 組裝prompt → Ollama → 診斷文字
```

---

## 3. 技術選型說明

### 為什麼用 FastAPI 而不是 Flask？

| 項目 | Flask | FastAPI |
|---|---|---|
| 非同步支援 | 需要額外套件 | 原生 async/await |
| WebSocket | 需要 Flask-SocketIO | 原生支援 |
| 資料驗證 | 需要手動寫 | Pydantic 自動驗證 |
| API 文件 | 要另外裝 | 自動生成（/docs）|

### 為什麼用 WebSocket 而不是 HTTP 輪詢？

```
HTTP 輪詢（舊方式）：
前端每秒問：「有新資料嗎？」→ 後端每秒答：「有」
→ 每秒一個 HTTP 請求，浪費資源

WebSocket（現代方式）：
連線一次建立，保持開著
後端有新資料就主動推
→ 低延遲，低資源消耗
```

### 為什麼用 TimescaleDB？

每秒寫 N 筆資料，一天就是幾十萬筆。TimescaleDB 的超表（Hypertable）自動按時間分區，查詢「最近一小時」只掃那一個分區，不會因資料量增加而變慢。

### 為什麼用 Isolation Forest？

無監督學習，不需要標記「這筆是異常」的訓練資料，只需要正常運行時的資料就能訓練。工業設備的真實異常很少，標記資料難以取得，Isolation Forest 是工業場景的標準選擇。

### 為什麼用 Ollama 而不是 ChatGPT API？

- 完全本地運行，不需要網路，資料不外傳
- 無 API 費用，無流量限制
- 隱私安全，工廠資料不上傳第三方服務
- 透過 Tailscale VPN 連接桌電，外出筆電也能用

---

## 4. 完整資料流解析

### 4.1 從 PLC 到前端（即時路徑）

**Step 1：uvicorn 啟動**
```
uvicorn main:app --reload --port 8000
    ↓
lifespan 執行
    ↓
asyncio.create_task(poll_plc_forever())
→ 背景啟動永遠跑不停的協程
```

**Step 2：PLC 輪詢（OPC UA 版本）**
```python
async with Client(url="opc.tcp://192.168.0.10:4840") as client:
    while True:
        spd = await motor_speed_node.read_value()
        # ... 讀取所有變數
        await asyncio.sleep(1)
```

**Step 2（SLMP 版本，fx5u 分支）**
```python
plc = pymcprotocol.Type3E(plctype="iQ-L")
plc.connect("127.0.0.1", 5011)
values = await asyncio.get_event_loop().run_in_executor(
    None, lambda: plc.batchread_wordunits(headdevice="D100", readsize=3)
)
```

**Step 3：ML 推論**
```python
anomaly = detector.update(data)
# 維護最近 30 筆滑動視窗
# 計算 15 個統計特徵（mean/std/max/min/diff × N個tag）
# Isolation Forest 判斷
# → {"is_anomaly": bool, "score": float, "status": str}
```

**Step 4：Ollama 診斷（僅異常時，60秒冷卻）**
```python
if is_anomaly and now - _last_ollama_call > 60:
    _last_ollama_call = now
    asyncio.create_task(_fetch_ollama_analysis(data, anomaly))
    # 背景呼叫，不阻塞廣播
```

**Step 5：廣播 + 寫入資料庫（並行）**
```python
await manager.broadcast(data)              # 推送前端
asyncio.create_task(write_to_db(...))      # 背景寫入DB，不等
```

**Step 6：前端接收並渲染**
```typescript
ws.current.onmessage = (e) => {
    const parsed = JSON.parse(e.data)
    setData(parsed)       // 觸發重新渲染
    setHistory(prev => [...prev.slice(-120), parsed])
}
```

### 4.2 Ollama AI 診斷流程

```
異常觸發
    ↓
查詢 TimescaleDB 最近 5 分鐘趨勢
SELECT tag_name, MIN/MAX/AVG/RANGE FROM plc_measurements
WHERE time > NOW() - INTERVAL '5 minutes'
    ↓
組裝 prompt（包含當前值 + 5分鐘趨勢）
    ↓
httpx POST → http://100.89.23.28:11434/api/generate
（透過 Tailscale VPN，連接桌電的 Ollama）
    ↓
qwen3:14b 生成繁體中文診斷
    ↓
ai_analysis 加進 anomaly dict
    ↓
_latest_ai_analysis 持久化（避免每秒覆蓋）
    ↓
前端 AnomalyPanel 顯示
```

### 4.3 ML 訓練路徑（離線）

```
手動執行：python ml/train.py
    ↓
從 TimescaleDB 讀取歷史資料
    ↓
長表轉寬表（pivot_table）
    ↓
滑動視窗特徵工程（15個特徵）
    ↓
IsolationForest.fit(X)
    ↓
joblib.dump() → models/S7-1511T_anomaly_YYYYMMDD.pkl
```

---

## 5. 後端每個檔案的作用

### 專案結構

```
backend/
├── main.py                         # 總指揮
├── core/
│   ├── ws_manager.py               # WebSocket 連線管理
│   └── plc_simulator.py            # PLC 輪詢 + ML推論 + Ollama整合
├── routers/
│   ├── realtime.py                 # /ws/realtime WebSocket 端點
│   └── control.py                  # /api/control/write REST 端點
├── db/
│   └── writer.py                   # 非同步寫入 TimescaleDB
├── ml/
│   ├── train.py                    # Isolation Forest 訓練（離線）
│   ├── inferencer.py               # 即時推論，滑動視窗
│   └── ollama_analyzer.py          # Ollama AI 診斷
├── models/
│   └── S7-1511T_anomaly_v1.pkl     # 訓練好的模型
├── fx5u_mock_server.py             # FX5U SLMP 模擬器（fx5u分支）
├── generate_training_data.py       # 產生訓練資料
└── test_ws.py / test_fx5u.py       # 開發測試
```

---

### `main.py` — 總指揮

把所有模組組合起來，定義應用的啟動和關閉行為。

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_plc_forever())  # 啟動時開始輪詢
    yield                                            # 正常運作中
    task.cancel()                                    # 關閉時停止
```

`yield` 上面 = 開機做的事，`yield` 下面 = 關機做的事。

CORS Middleware 告訴瀏覽器允許 `localhost:5173`（前端）呼叫 `localhost:8000`（後端），沒有這個瀏覽器會擋掉所有跨來源請求。

---

### `core/ws_manager.py` — WebSocket 連線管理員

維護 `active_connections` 清單，記錄所有連線的瀏覽器。

```python
async def broadcast(self, data: dict):
    disconnected = []
    for ws in self.active_connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)   # 先記下，不能邊跑邊刪
    for ws in disconnected:
        self.disconnect(ws)           # 跑完再統一清理
```

為什麼不能邊跑邊刪：Python 不允許在迭代清單的同時修改它，先記後刪是標準做法。

---

### `core/plc_simulator.py` — 核心資料處理模組

這是整個系統最複雜的檔案，整合了四件事：

```
1. 連接 PLC 讀值（OPC UA 或 SLMP）
2. 呼叫 inferencer.py 做 ML 推論
3. 異常時觸發 Ollama 診斷（冷卻機制）
4. 廣播資料給前端 + 寫入資料庫
```

**重要設計：冷卻機制**
```python
_last_ollama_call = 0.0
OLLAMA_COOLDOWN   = 60.0  # 60秒內只呼叫一次

if is_anomaly and now - _last_ollama_call > OLLAMA_COOLDOWN:
    _last_ollama_call = now
    asyncio.create_task(_fetch_ollama_analysis(data, anomaly))
```

**重要設計：AI 分析結果持久化**
```python
_latest_ai_analysis: str = ""  # 全域變數，記住最新診斷

# 廣播時帶入最新診斷，避免每秒新資料覆蓋掉診斷文字
if anomaly.get("is_anomaly") and _latest_ai_analysis:
    data["anomaly"]["ai_analysis"] = _latest_ai_analysis
```

---

### `ml/inferencer.py` — 即時推論

維護滑動視窗，每秒對最新 30 筆資料做 ML 推論。

**三種狀態：**
```
collecting_data → 沒有模型檔案，純資料收集模式
warming_up      → 模型存在但視窗未滿 30 筆
normal / anomaly → 正常推論
```

**特徵工程（每個 tag × 5 種統計）：**
```python
features[f"{tag}_mean"] = s.mean()   # 水平
features[f"{tag}_std"]  = s.std()    # 穩定性
features[f"{tag}_max"]  = s.max()    # 峰值
features[f"{tag}_min"]  = s.min()    # 谷值
features[f"{tag}_diff"] = s.diff().iloc[-1]  # 變化率
```

**異常判斷：**
```python
score = pipeline.decision_function(X)[0]
# score > 0  → 正常
# score < 0.05 → 異常（閾值可調整）
```

---

### `ml/ollama_analyzer.py` — AI 診斷

整合 TimescaleDB 趨勢查詢 + Ollama LLM，生成繁體中文診斷。

**連線設定（Tailscale）：**
```python
OLLAMA_URL   = "http://100.89.23.28:11434"  # 桌電 Tailscale IP
OLLAMA_MODEL = "qwen3:14b"
```

**流程：**
```
1. 查詢 TimescaleDB 最近 5 分鐘趨勢（MIN/MAX/AVG/RANGE）
2. 組裝 prompt（當前值 + 趨勢 + 正常範圍）
3. POST → Ollama API（think: False，關閉思考模式加快速度）
4. 回傳兩行格式：
   原因：[一句話]
   處置：[一句話]
```

**Prompt 設計關鍵：**
```python
SYSTEM_PROMPT = """你只能輸出剛好兩行繁體中文，格式如下：
原因：（一句話，40字以內）
處置：（一句話，40字以內）
輸出完兩行後立即停止，不得有第三行。"""
```

---

### `db/writer.py` — 資料庫寫入

非同步寫入，不阻塞廣播。

```python
asyncio.create_task(write_to_db("S7-1511T", data))
# create_task 讓寫入在背景執行
# 不會等寫完才廣播，兩件事並行進行
```

排除不需要寫入資料庫的欄位：
```python
if tag not in ("timestamp", "motor_enable", "anomaly")
# anomaly 是 dict，pressure/temp/speed 才是數值
```

---

### `fx5u_mock_server.py` — FX5U SLMP 模擬器（fx5u 分支）

用 Python socket 模擬一台 FX5U PLC 的 SLMP 回應，讓沒有實體硬體也能測試完整流程。

```python
registers = {
    100: 1480,   # D100 motor_speed（RPM）
    101: 700,    # D101 temperature × 10
    102: 50,     # D102 pressure × 10
}
```

透過解析 SLMP 3E Binary 封包格式，正確回應 `batchread_wordunits` 請求。

---

## 6. 前端每個檔案的作用

### 專案結構

```
frontend/src/
├── App.tsx                    # 總指揮，組合所有元件
├── index.css                  # 工業風設計系統（CSS 變數、字型）
├── hooks/
│   └── useWebSocket.ts        # WS 連線 + 自動重連 + 狀態管理
└── components/
    ├── GaugeCard.tsx           # 數值卡片，警戒值自動變色
    ├── RealtimeChart.tsx       # Recharts 即時趨勢圖
    ├── ControlPanel.tsx        # 反向控制面板
    └── AnomalyPanel.tsx        # ML 異常 + AI 診斷面板
```

---

### `hooks/useWebSocket.ts` — 前端資料入口

管理 WebSocket 連線，把後端資料轉成 React 狀態。

```typescript
// 回傳四個東西，性質不同：
data        → 值（最新一筆），給 GaugeCard
history     → 值（最近 120 筆），給 RealtimeChart
isConnected → 值（boolean），給 Header 狀態燈
sendCommand → 函式，給 ControlPanel 按鈕呼叫
```

**自動重連機制：**
```typescript
ws.current.onclose = () => {
    setIsConnected(false)
    reconnectTimer.current = setTimeout(connect, 3000)  // 3秒後重連
}
```

**TypeScript 型別（含 AI 診斷欄位）：**
```typescript
export interface AnomalyResult {
  is_anomaly: boolean
  score: number
  status: string
  remaining?: number
  ai_analysis?: string  // Ollama 診斷結果（異常時才有）
}
```

---

### `components/GaugeCard.tsx` — 數值卡片

根據 warn/danger 閾值自動切換顏色：
```
正常  → 青色（--accent-cyan）
警告  → 橘黃色（--accent-amber）
危險  → 紅色（--accent-red）
```

進度條計算：`pct = (value - min) / (max - min) * 100`

---

### `components/RealtimeChart.tsx` — 趨勢圖

`isAnimationActive={false}` 關掉動畫，避免每秒更新時折線閃爍。

`warnValue` 用 `ReferenceLine` 畫水平警戒虛線。

---

### `components/AnomalyPanel.tsx` — 異常診斷面板

顯示四種狀態：
```
collecting_data → 資料收集中（無模型）
warming_up      → 暖機中（顯示剩餘秒數）
normal          → 綠燈 ✓ NORMAL
anomaly         → 紅燈閃爍 ⚠ ANOMALY DETECTED + AI 診斷文字
```

AI 診斷顯示使用 `whiteSpace: 'pre-line'` 讓換行符號正確渲染：
```tsx
<div style={{ whiteSpace: 'pre-line' }}>
    {anomaly.ai_analysis}
</div>
```

---

## 7. 資料庫設計說明

### Schema

```sql
CREATE TABLE plc_measurements (
    time        TIMESTAMPTZ NOT NULL,   -- 帶時區的時間戳
    device_id   TEXT NOT NULL,          -- 設備識別碼，如 'S7-1511T'
    tag_name    TEXT NOT NULL,          -- 變數名稱，如 'motor_speed'
    value       DOUBLE PRECISION,       -- 量測值
    quality     SMALLINT DEFAULT 192    -- OPC UA 品質碼，192 = Good
);

SELECT create_hypertable('plc_measurements', 'time');
CREATE INDEX ON plc_measurements (device_id, tag_name, time DESC);
```

### 長表格 vs 寬表格

選用長表格（每筆資料一個變數）的原因：

```
寬表格：每個變數一欄
→ 新增第 N+1 個變數需要 ALTER TABLE，影響所有舊資料

長表格：每筆資料一個變數
→ 新增變數只需寫新的 tag_name，完全不改表結構
→ 20 個參數和 3 個參數用同一張表，零修改成本
```

### 超表（Hypertable）

TimescaleDB 自動按時間分區管理資料：
```
沒有超表：查詢「最近一小時」要掃整張表
有超表：只掃最新的時間分塊，資料量再大速度也穩定
```

---

## 8. ML 模型說明

### Isolation Forest 原理

核心思想：**異常資料比較容易被「隔離」出來。**

```
隨機切割資料空間
→ 正常資料：附近有很多同類，要切很多刀才能隔離（分數高）
→ 異常資料：附近沒什麼同類，幾刀就隔離了（分數低）
```

`decision_function` 回傳的 score：
```
score > 0    → 正常（越正越正常）
score < 0.05 → 異常（閾值，可調整）
```

### 訓練 vs 推論的分離

```
訓練（離線，只做一次）：
    TimescaleDB 歷史資料
        ↓ 讀取 5000 筆
        ↓ 計算特徵
        ↓ IsolationForest.fit()
        ↓ joblib.dump()
    models/S7-1511T_anomaly_v1.pkl

推論（線上，每秒執行）：
    inferencer.py 記憶體中的 deque（30筆）
        ↓ 計算特徵
        ↓ pipeline.decision_function()  ← 用 .pkl 模型
    is_anomaly + score
```

**推論時完全不碰 TimescaleDB**，模型已從訓練時學到正常範圍。

### 特徵工程（N個tag × 5種統計 = N×5 個特徵）

| 特徵 | 意義 |
|---|---|
| `_mean` | 水平（這段時間的平均值） |
| `_std` | 穩定性（波動大不大） |
| `_max` | 峰值 |
| `_min` | 谷值 |
| `_diff` | 變化率（趨勢） |

### 訓練資料的設計

```python
# 正常資料：用 sin 波 + 高斯雜訊模擬真實設備波動
motor_speed = 1480 + 15 * sin(i/50) + gauss(0, 5)

# 異常樣本：每 500 筆注入一個異常點
if i % 500 == 0:
    motor_speed += uniform(200, 400)
```

讓模型同時學到「正常的樣子」和「異常的邊界」。

---

## 9. Ollama AI 診斷說明

### 架構概述

```
Isolation Forest 判斷「有沒有異常」（數值層面）
    ↓ is_anomaly = True
Ollama qwen3:14b 解釋「這個異常是什麼原因」（語言層面）
    ↓
前端 AnomalyPanel 顯示診斷文字
```

### Tailscale 連線設定

```
桌電（KevinLai-PC）：Tailscale IP 100.89.23.28，跑 Ollama + qwen3:14b
筆電（Kevin-NB）：    Tailscale IP 100.102.1.58，跑開發環境

兩台在同一個 Tailscale tailnet → 直接互連
筆電的 FastAPI 呼叫 http://100.89.23.28:11434
→ 透過 Tailscale WireGuard 加密隧道傳輸
→ 到達桌電的 Ollama
```

**桌電設定 Ollama 對區域網路開放：**
```
系統環境變數：OLLAMA_HOST = 0.0.0.0
```

### Prompt 工程

**System Prompt（控制輸出格式）：**
```
你只能輸出剛好兩行繁體中文：
原因：（一句話，40字以內）
處置：（一句話，40字以內）
輸出完兩行後立即停止，不得有第三行。
```

**User Prompt（包含趨勢資訊）：**
```
設備異常，請依格式輸出診斷：
馬達轉速 9965 RPM（正常1400~1600），5分鐘均值 9551.30
溫度 36.6 °C（正常65~75），5分鐘均值 44.96
壓力 51.7 bar（正常4.5~5.5），5分鐘均值 57.59
```

**Ollama API 參數：**
```python
{
    "model": "qwen3:14b",
    "think": False,          # 關閉思考模式，加快速度（約20秒→10秒）
    "options": {
        "temperature": 0.3,  # 低溫度，輸出更穩定
        "num_predict": 150,  # 限制輸出長度
    }
}
```

### 冷卻機制

```python
OLLAMA_COOLDOWN = 60.0  # 60秒內只呼叫一次

# 避免每秒異常都觸發 Ollama，節省資源
if now - _last_ollama_call > OLLAMA_COOLDOWN:
    _last_ollama_call = now
    asyncio.create_task(_fetch_ollama_analysis(...))
```

### AI 診斷結果持久化

```python
_latest_ai_analysis: str = ""  # 全域變數

# 問題：每秒新資料廣播會覆蓋掉 ai_analysis 欄位
# 解法：把最新診斷存在全域變數，每次廣播時帶入

if anomaly.get("is_anomaly") and _latest_ai_analysis:
    data["anomaly"]["ai_analysis"] = _latest_ai_analysis
```

---

## 10. Git 分支說明

### 分支結構

```
main                    → Siemens S7-1511T × OPC UA（真實硬體）
feature/fx5u-slmp       → Mitsubishi FX5U × SLMP（含 Mock Server）
```

### 兩個分支的差異

| 檔案 | main | feature/fx5u-slmp |
|---|---|---|
| `plc_simulator.py` | asyncua OPC UA，連 192.168.0.10 | pymcprotocol SLMP，連 127.0.0.1:5011 |
| `fx5u_mock_server.py` | 無 | ✅ FX5U 模擬器 |
| `test_fx5u.py` | 無 | ✅ 連線測試 |
| 其他所有檔案 | 相同 | 相同 |

### 切換指令

```bash
git checkout main                # 切到西門子版本
git checkout feature/fx5u-slmp  # 切到三菱版本
```

切換後記得：
- `main`：確認 S7-1511T 有連線，直接跑 uvicorn
- `feature/fx5u-slmp`：先跑 `python fx5u_mock_server.py`，再跑 uvicorn

---

## 11. 套件總覽

### 後端 Python 套件

| 套件 | 用途 |
|---|---|
| `fastapi` | Web 框架，REST + WebSocket |
| `uvicorn` | ASGI 伺服器 |
| `asyncua` | OPC UA Client（Siemens）|
| `pymcprotocol` | SLMP Client（Mitsubishi）|
| `asyncpg` | PostgreSQL 非同步驅動 |
| `scikit-learn` | Isolation Forest |
| `pandas` | 資料處理，長表轉寬表 |
| `joblib` | 儲存/載入 ML 模型 |
| `httpx` | 非同步 HTTP，呼叫 Ollama |
| `python-dotenv` | 讀取環境變數 |

### 前端 npm 套件

| 套件 | 用途 |
|---|---|
| `react` | UI 框架 |
| `typescript` | 型別安全 |
| `vite` | 開發伺服器，HMR 熱更新 |
| `recharts` | 即時折線圖 |

### 基礎設施

| 工具 | 用途 |
|---|---|
| `Docker` | 跑 TimescaleDB |
| `TimescaleDB` | 時序資料庫（PostgreSQL 延伸）|
| `Ollama` | 本地 LLM 服務 |
| `Tailscale` | 點對點 VPN，連接桌電 Ollama |
| `conda` | Python 環境管理 |
| `Git` | 版本控制 |

---

## 12. 常見問題與解法

### OPC UA 連不上

1. TIA Portal DB 沒有取消「優化塊存取」
2. 變數沒有勾選「可從 HMI/OPC UA 存取」
3. OPC UA 安全模式不是 None
4. 先 `ping 192.168.0.10` 確認網路通

### TimescaleDB 密碼認證失敗

本機已有 PostgreSQL 佔用 5432 port，換 port：
```bash
docker run -d --name timescaledb -p 5435:5432 ...
```

### ML 偵測不到異常

1. 訓練資料和真實資料範圍差太多 → 重新訓練
2. 閾值設太嚴 → 調高 threshold（`inferencer.py`）
3. 視窗還在暖機 → 等 30 秒

### Ollama 逾時

1. 桌電 Ollama 沒有跑 → 確認 `ollama serve`
2. `OLLAMA_HOST` 沒有設 `0.0.0.0` → 重設並重啟 Ollama
3. Tailscale 沒有連線 → 確認兩台都有 Connected 狀態
4. qwen3:14b 冷啟動需要 20~30 秒 → timeout 設 60 秒

### SLMP Mock Server 崩潰

暫存器值超過 65535（ushort 上限）：
```python
registers[100] = max(0, min(65535, int(registers[100] + gauss(0, 10))))
```

---

## 13. 未來可擴充方向

### 待新增功能

**自動化再訓練流程（Auto Retraining Pipeline）：**
- 累積到 N 筆資料後自動觸發訓練
- 每天定時用最新 30 天資料重新訓練
- 歷史模型全部保留，可回溯比較
- 訓練用滑動視窗 + 降採樣，控制資料量

**資料生命週期管理：**
```sql
-- TimescaleDB 自動刪除 90 天以前的資料
SELECT add_retention_policy('plc_measurements', INTERVAL '90 days');

-- 壓縮 30 天以前的資料（節省儲存空間）
SELECT add_compression_policy('plc_measurements', INTERVAL '30 days');
```

**Docker Compose 整合：**
```yaml
# 一鍵啟動整個系統
services:
  timescaledb: ...
  backend: ...
  frontend: ...
```

**歷史資料查詢 API：**
```
GET /api/history?tag=motor_speed&from=2026-06-01&to=2026-06-12
```

**邊緣運算部署（Edge Computing）：**
- 針對有機台分散各地的場景
- 樹莓派 / 工業 PC 部署在機台旁
- 本地執行 TimescaleDB + Isolation Forest
- 正常時零流量，異常時才透過 4G SIM 上傳摘要
- Ollama 保留在雲端或公司伺服器，按需呼叫

---

## 附錄：快速啟動指令

### 西門子版本（main 分支）

```bash
# 確認在 main 分支
git checkout main

# 1. 啟動 TimescaleDB
docker start timescaledb

# 2. 啟動後端
conda activate iot-scada
cd backend
uvicorn main:app --reload --port 8000

# 3. 啟動前端
cd frontend
npm run dev

# 4. 瀏覽器開啟 http://localhost:5173
```

### 三菱 FX5U 版本（fx5u 分支）

```bash
# 切換分支
git checkout feature/fx5u-slmp

# 1. 啟動 TimescaleDB
docker start timescaledb

# 2. 啟動 FX5U Mock Server（終端機一）
conda activate iot-scada
cd backend
python fx5u_mock_server.py

# 3. 啟動後端（終端機二）
uvicorn main:app --reload --port 8000

# 4. 啟動前端（終端機三）
cd frontend
npm run dev
```

---

*文件版本：v2.0 | 對應專案：iot-scada-platform | 最後更新：2026-06-12*