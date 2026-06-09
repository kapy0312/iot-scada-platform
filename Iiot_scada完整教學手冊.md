# IIoT SCADA Platform 完整教學手冊

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
9. [套件總覽](#9-套件總覽)
10. [常見問題與解法](#10-常見問題與解法)
11. [未來可擴充方向](#11-未來可擴充方向)

---

## 1. 專案全貌與目標

### 這個專案在做什麼

用一句話說：**把工廠裡的 PLC 設備資料，透過網路即時顯示在網頁儀表板上，並用 AI 自動偵測異常。**

傳統工廠的問題：
- 設備資料只能在 HMI 現場看，無法遠端監控
- 異常發生後才知道，沒有預警
- 資料沒有保存，無法回溯分析

這個系統解決了什麼：
- 任何有瀏覽器的裝置都能即時看到設備狀態
- ML 模型持續分析數據，自動發出異常警報
- 所有資料存進時序資料庫，可以查詢任意時間段

### 系統分為四個階段

| 階段 | 內容 | 狀態 |
|---|---|---|
| Phase 1 | PLC 連線 + 即時儀表板 | ✅ 完成 |
| Phase 2 | 資料庫持久化 | ✅ 完成 |
| Phase 3 | ML 異常偵測 | ✅ 完成 |
| Phase 4 | Ollama AI 解讀（待做）| 🔜 |

---

## 2. 系統架構圖

```
┌─────────────────────────────────────────────────────────────────┐
│                        OT 設備層                                  │
│                                                                   │
│  ┌─────────────────┐          ┌─────────────────┐               │
│  │ Siemens S7-1511T│          │ Mitsubishi FX5U │               │
│  │  OPC UA Server  │          │  SLMP Protocol  │               │
│  │ 192.168.0.10    │          │ (未來擴充)       │               │
│  └────────┬────────┘          └────────┬────────┘               │
└───────────┼─────────────────────────────┼───────────────────────┘
            │ opc.tcp://192.168.0.10:4840  │ TCP:5007
            ▼                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Python 後端（FastAPI）                         │
│                                                                   │
│  main.py ──────── 應用進入點，lifespan 管理背景任務               │
│      │                                                            │
│      ├── core/plc_simulator.py ── OPC UA 輪詢，每秒讀值          │
│      │         │                                                  │
│      │         ├── core/ws_manager.py ── 管理 WebSocket 連線     │
│      │         │         │ broadcast()                           │
│      │         │         ▼                                        │
│      │         │   所有連線的瀏覽器                               │
│      │         │                                                  │
│      │         ├── db/writer.py ── 寫入 TimescaleDB              │
│      │         │                                                  │
│      │         └── ml/inferencer.py ── Isolation Forest 推論     │
│      │                                                            │
│      ├── routers/realtime.py ── /ws/realtime 端點               │
│      └── routers/control.py ── /api/control/write 端點          │
│                                                                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │ WebSocket               │ asyncpg (TCP)
              ▼                         ▼
┌─────────────────────┐    ┌───────────────────────────┐
│   React 前端         │    │  TimescaleDB（Docker）     │
│                     │    │                           │
│  App.tsx            │    │  plc_measurements 表       │
│  ├── GaugeCard      │    │  time | device | tag | val │
│  ├── RealtimeChart  │    │                           │
│  ├── ControlPanel   │    │  每秒寫入 3 筆             │
│  └── AnomalyPanel   │    │  （motor/temp/pressure）  │
│                     │    └───────────────────────────┘
│  useWebSocket hook  │                 ▲
│  (自動重連)          │    ┌────────────┴──────────────┐
└─────────────────────┘    │  ML 訓練（離線）            │
                           │  ml/train.py               │
                           │  → Isolation Forest        │
                           │  → models/*.pkl            │
                           └───────────────────────────┘
```

### 資料的兩條路徑

```
路徑一（即時顯示）：
PLC → poll每秒 → ML推論 → WebSocket廣播 → 前端畫面更新

路徑二（資料儲存）：
PLC → poll每秒 → asyncpg → TimescaleDB → 歷史查詢
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

你的系統需要同時跑「PLC 輪詢迴圈」和「WebSocket 伺服器」，這需要非同步架構，FastAPI 是最自然的選擇。

### 為什麼用 WebSocket 而不是 HTTP 輪詢？

```
HTTP 輪詢（舊方式）：
前端每秒問：「有新資料嗎？」
後端每秒答：「有，這是資料」
→ 每秒一個 HTTP 請求，浪費資源

WebSocket（現代方式）：
連線一次建立，保持開著
後端有新資料就主動推
→ 低延遲，低資源消耗
```

### 為什麼用 TimescaleDB 而不是 SQLite？

SQLite 適合小型應用，但你的系統每秒寫 3 筆，一天就是 259,200 筆。TimescaleDB 的超表（Hypertable）自動按時間分區，讓查詢「最近一小時的資料」只掃那一個分區，不會因為資料量增加而變慢。

### 為什麼用 Isolation Forest？

Isolation Forest 是無監督學習，意思是**不需要標記「這筆是異常」的訓練資料**，只需要正常運行時的資料就能訓練。對工業設備來說非常實用，因為真實異常資料很少。

---

## 4. 完整資料流解析

### 4.1 從 PLC 到前端（即時路徑）

**Step 1：uvicorn 啟動 FastAPI**

```
你執行：uvicorn main:app --reload --port 8000
         ↓
FastAPI 應用啟動
         ↓
lifespan 函式執行
         ↓
asyncio.create_task(poll_plc_forever())
→ 在背景啟動一個「永遠跑不停的協程」
```

**Step 2：PLC 輪詢**

```python
# plc_simulator.py 每秒執行一次這段：
async with Client(url="opc.tcp://192.168.0.10:4840") as client:
    while True:
        spd = await motor_speed_node.read_value()  # 透過 OPC UA 讀 DB1.motor_speed
        tmp = await temperature_node.read_value()
        prs = await pressure_node.read_value()
        
        data = {
            "timestamp": time.time(),
            "motor_speed": round(float(spd), 2),
            "temperature": round(float(tmp), 3),
            "pressure":    round(float(prs), 3),
        }
        await asyncio.sleep(1)
```

**Step 3：ML 推論**

```python
# inferencer.py
anomaly = detector.update(data)
# detector 維護最近 30 筆資料的滑動視窗
# 計算 15 個統計特徵（mean/std/max/min/diff × 3個tag）
# 用訓練好的 Isolation Forest 判斷是否異常
# 回傳：{"is_anomaly": True/False, "score": float, "status": "normal"/"anomaly"}

data["anomaly"] = anomaly  # 把判斷結果加進資料
```

**Step 4：廣播給前端**

```python
# ws_manager.py
await manager.broadcast(data)
# 走訪 active_connections 清單
# 把 data 轉成 JSON 字串
# 用 WebSocket 送給每一個連線的瀏覽器
```

**Step 5：寫入資料庫（並行，不阻塞廣播）**

```python
asyncio.create_task(write_to_db("S7-1511T", data))
# create_task 讓寫入在背景執行
# 不會等寫完才廣播，兩件事並行進行
```

**Step 6：前端接收**

```typescript
// useWebSocket.ts
ws.current.onmessage = (e) => {
    const parsed = JSON.parse(e.data)   // 字串 → JavaScript 物件
    setData(parsed)                      // 觸發 React 重新渲染
    setHistory(prev => [...prev.slice(-120), parsed])  // 加進歷史陣列
}
```

**Step 7：畫面更新**

```
setData() 呼叫後，React 偵測到狀態改變
→ App.tsx 重新渲染
→ 把新的 data 傳給子元件
→ GaugeCard 顯示新數值
→ RealtimeChart 延伸折線
→ AnomalyPanel 更新狀態燈
```

### 4.2 控制指令路徑（反向控制）

```
使用者點擊 Motor OFF 按鈕
    ↓
ControlPanel.tsx
onClick={() => onCommand('motor_enable', 0)}
    ↓
onCommand 實際上是 App.tsx 傳進來的 sendCommand
    ↓
sendCommand('motor_enable', 0) in useWebSocket.ts
    ↓
fetch('http://localhost:8000/api/control/write', {
    method: 'POST',
    body: JSON.stringify({ tag: 'motor_enable', value: 0 })
})
    ↓
control.py 接收
→ Pydantic 自動驗證格式
→ range check（安全範圍確認）
→ print（現在）/ snap7 寫值（之後接真實 PLC）
→ return {"status": "ok"}
    ↓
fetch 收到回應，結束
```

### 4.3 ML 訓練路徑（離線）

```
手動執行：python ml/train.py
    ↓
從 TimescaleDB 讀取歷史資料（5000 筆）
    ↓
pivot_table：長表轉寬表
（time | tag | value） → （time | motor_speed | temperature | pressure）
    ↓
build_features()：滑動視窗特徵工程
每個 tag 計算 5 種統計 → 共 15 個特徵
    ↓
IsolationForest.fit(X)
（無監督學習，不需要標籤）
    ↓
joblib.dump() 儲存模型到 models/*.pkl
    ↓
下次 uvicorn 啟動時，inferencer.py 載入這個模型
用於即時推論
```

---

## 5. 後端每個檔案的作用

### 專案結構

```
backend/
├── main.py                    # 總指揮
├── core/
│   ├── ws_manager.py          # WebSocket 連線管理
│   └── plc_simulator.py       # PLC 輪詢（OPC UA）
├── routers/
│   ├── realtime.py            # WebSocket 端點
│   └── control.py             # REST 控制端點
├── db/
│   └── writer.py              # 資料庫寫入
├── ml/
│   ├── train.py               # 模型訓練（離線執行）
│   └── inferencer.py          # 即時推論
├── models/
│   └── S7-1511T_anomaly_v1.pkl  # 訓練好的模型
└── test_ws.py                 # 開發測試用
```

### `main.py` — 總指揮

**職責：** 把所有模組組合起來，定義應用的啟動和關閉行為

**關鍵機制：lifespan**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_plc_forever())  # 啟動時開始輪詢
    yield                                            # 應用正常運作中
    task.cancel()                                    # 關閉時停止輪詢
```

`yield` 上面 = 開機做的事
`yield` 下面 = 關機做的事

**CORS Middleware：**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
)
```

瀏覽器的同源政策會擋掉 5173（前端）→ 8000（後端）的請求，CORS 告訴瀏覽器「這個來源被允許」。

### `core/ws_manager.py` — 連線管理員

**職責：** 記錄所有連線的瀏覽器，廣播資料

```python
class ConnectionManager:
    def __init__(self):
        self.active_connections = []  # 這是「所有連線的清單」
```

`self` 是什麼：Python 的類別裡，`self` 代表「這個物件本身」，讓每個方法都能存取同一份資料（active_connections）。

**broadcast 的設計細節：**

```python
async def broadcast(self, data: dict):
    disconnected = []
    for ws in self.active_connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)  # 先記下來，不能邊跑邊刪
    for ws in disconnected:
        self.disconnect(ws)           # 跑完再統一清理
```

為什麼不能邊跑邊刪？因為 Python 不允許在迭代一個清單的同時修改它。

### `core/plc_simulator.py` — 資料來源

**職責：** 連接 PLC 讀值，現在用 OPC UA，未來可換成任何協議

這個檔案是整個系統唯一需要在換 PLC 時修改的地方，其他所有檔案完全不用動。

**OPC UA 連線架構：**

```python
async with Client(url=PLC_URL) as client:
    # 取得節點（只取一次，不用每秒重取）
    motor_speed_node = await client.nodes.root.get_child([
        "0:Objects", "3:PLC_1", "3:DataBlocksGlobal", "3:DB1", "3:motor_speed"
    ])
    
    while True:
        spd = await motor_speed_node.read_value()  # 每秒讀值
```

**節點路徑怎麼解讀：**

```
"0:Objects"           → Namespace 0，OPC UA 標準節點
"3:PLC_1"             → Namespace 3（Siemens），PLC 名稱
"3:DataBlocksGlobal"  → 全域資料塊
"3:DB1"               → 你在 TIA Portal 建的 DB1
"3:motor_speed"       → DB1 裡的變數
```

**斷線重連機制：**

```python
while True:
    try:
        async with Client(...) as client:
            while True:
                # 正常輪詢
    except Exception as e:
        print(f"連線失敗：{e}")
        await asyncio.sleep(5)  # 5秒後重試
```

外層 `while True` 確保 PLC 斷線後自動重連，不會讓整個系統停掉。

### `routers/realtime.py` — WebSocket 大門

**職責：** 開一扇門讓前端連進來

```python
@router.websocket("/ws/realtime")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)   # 加進清單
    try:
        while True:
            await websocket.receive_text()  # 保持連線活著，等前端傳訊息
    except WebSocketDisconnect:
        manager.disconnect(websocket)  # 斷線時從清單移除
```

`receive_text()` 在這裡的作用不是真的要接收資料，而是「等待」——讓這個協程保持存活，不會提前結束。一旦前端關閉連線，`WebSocketDisconnect` 例外被觸發，執行清理。

### `routers/control.py` — 控制指令接收器

**職責：** 接收前端的控制指令，驗證安全範圍

```python
class ControlCommand(BaseModel):
    tag: str
    value: float
    
    @field_validator("value")
    def check_range(cls, v, info):
        limits = {
            "motor_speed_setpoint": (0, 1800),
            "motor_enable": (0, 1),
        }
        # 超出範圍直接拒絕，不讓危險指令到達 PLC
```

這個 `BaseModel` 是 Pydantic 的功能：前端傳來 JSON，Pydantic 自動對應到這個類別的欄位，格式不對（例如 value 傳了文字）直接回 422 錯誤，不需要自己寫驗證邏輯。

### `db/writer.py` — 資料庫寫入

**職責：** 把 PLC 資料非同步寫入 TimescaleDB

```python
async def write_to_db(device_id: str, data: dict):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = [
            (now, device_id, tag, float(value), 192)
            for tag, value in data.items()
            if tag not in ("timestamp", "motor_enable", "anomaly")
            # anomaly 是 dict，不能存進 value 欄位
        ]
        await conn.executemany("INSERT INTO plc_measurements ...", rows)
    finally:
        conn.close()  # 無論成功失敗都關閉連線
```

**為什麼用 `asyncio.create_task(write_to_db(...))` 而不是直接 `await`？**

```python
# 直接 await（錯誤方式）：
await manager.broadcast(data)
await write_to_db(...)     # 要等寫完才能繼續輪詢
await asyncio.sleep(1)

# create_task（正確方式）：
await manager.broadcast(data)
asyncio.create_task(write_to_db(...))  # 丟到背景，不等
await asyncio.sleep(1)
```

資料庫寫入可能需要幾十毫秒，如果用 `await` 等它，每秒的輪詢就會被拖慢。`create_task` 讓寫入在背景進行，廣播和輪詢不受影響。

---

## 6. 前端每個檔案的作用

### 專案結構

```
frontend/src/
├── App.tsx                    # 總指揮，組合所有元件
├── index.css                  # 設計系統（CSS 變數、字型）
├── hooks/
│   └── useWebSocket.ts        # WebSocket 連線 hook
└── components/
    ├── GaugeCard.tsx           # 數值卡片
    ├── RealtimeChart.tsx       # 即時趨勢圖
    ├── ControlPanel.tsx        # 控制面板
    └── AnomalyPanel.tsx        # 異常偵測面板
```

### `hooks/useWebSocket.ts` — 前端資料入口

**職責：** 管理 WebSocket 連線，把資料轉成 React 狀態

React 的「Hook」是什麼：一個以 `use` 開頭的函式，可以在裡面使用 React 的狀態管理功能（useState、useEffect 等）。

```typescript
export function useWebSocket(url: string) {
    // useState：宣告一個狀態，React 會在狀態改變時重新渲染
    const [data, setData] = useState<PlcData | null>(null)
    
    // useRef：存放不需要觸發重新渲染的東西
    const ws = useRef<WebSocket | null>(null)
    
    // useEffect：元件載入後執行一次（建立連線）
    useEffect(() => {
        connect()
        return () => ws.current?.close()  // 元件卸載時關閉連線
    }, [connect])
    
    // useCallback：把函式記憶起來，避免每次渲染都重新建立
    const connect = useCallback(() => {
        ws.current = new WebSocket(url)
        ws.current.onmessage = (e) => {
            const parsed = JSON.parse(e.data)
            setData(parsed)              // 觸發重新渲染
            setHistory(prev => [...prev.slice(-120), parsed])
        }
        ws.current.onclose = () => {
            setTimeout(connect, 3000)   // 3秒後自動重連
        }
    }, [url])
    
    return { data, history, isConnected, sendCommand }
}
```

**回傳值的分類：**

| 名稱 | 類型 | 用途 |
|---|---|---|
| `data` | 值（最新一筆）| GaugeCard 顯示即時數值 |
| `history` | 值（最近120筆）| RealtimeChart 畫趨勢圖 |
| `isConnected` | 值（boolean）| Header 狀態燈 |
| `sendCommand` | 函式 | ControlPanel 按鈕呼叫 |

### `components/GaugeCard.tsx` — 數值卡片

**職責：** 顯示單一量測值，根據閾值自動變色

```typescript
const isWarn = warn !== undefined && value >= warn
const isDanger = danger !== undefined && value >= danger

const accentColor = isDanger ? 'var(--accent-red)'
                  : isWarn   ? 'var(--accent-amber)'
                  :            'var(--accent-cyan)'
```

**進度條計算：**

```typescript
const pct = ((value - min) / (max - min)) * 100
// 溫度 70，min=0，max=120
// (70 - 0) / (120 - 0) * 100 = 58.3%
```

### `components/RealtimeChart.tsx` — 趨勢圖

**職責：** 把最近 120 筆資料畫成折線圖

重要設定：`isAnimationActive={false}` — 關掉動畫。每秒更新一次如果有動畫，折線會一直閃，使用者體驗很差。

`warnValue` 用 Recharts 的 `ReferenceLine` 畫一條水平虛線，讓操作者一眼看出有沒有接近警戒值。

### `components/AnomalyPanel.tsx` — 異常偵測面板

**職責：** 顯示 ML 模型的即時判斷結果

三種狀態：

```
warming_up → 滑動視窗還在累積（前 30 秒）
normal     → 綠色燈，score > threshold
anomaly    → 紅色燈閃爍，score < threshold
```

### `App.tsx` — 組合所有元件

**職責：** 資料的統一出口，把 useWebSocket 的資料分發給各元件

```typescript
const { data, history, isConnected, sendCommand } = useWebSocket(...)

// 然後分發給各元件：
<GaugeCard value={data?.motor_speed ?? 0} ... />
<RealtimeChart data={history} ... />
<AnomalyPanel anomaly={data?.anomaly ?? null} />
<ControlPanel onCommand={sendCommand} ... />
```

`data?.motor_speed ?? 0` 的意思：
- `?.` = 如果 data 是 null，不要報錯，直接回傳 undefined
- `?? 0` = 如果前面是 undefined 或 null，使用預設值 0

---

## 7. 資料庫設計說明

### Schema 設計

```sql
CREATE TABLE plc_measurements (
    time        TIMESTAMPTZ NOT NULL,   -- 帶時區的時間戳
    device_id   TEXT NOT NULL,          -- 設備識別碼，例如 'S7-1511T'
    tag_name    TEXT NOT NULL,          -- 變數名稱，例如 'motor_speed'
    value       DOUBLE PRECISION,       -- 量測值
    quality     SMALLINT DEFAULT 192    -- OPC UA 品質碼，192 = Good
);
```

### 為什麼用「長表格」而不是「寬表格」？

**寬表格（每個變數一欄）：**

```
time                  | motor_speed | temperature | pressure
2024-01-01 10:00:01   | 1480.2      | 70.1        | 5.0
```

問題：之後新增第四個變數（例如 vibration），要 `ALTER TABLE` 加欄位，影響所有舊資料。

**長表格（每筆資料一個變數）：**

```
time                  | device_id | tag_name     | value
2024-01-01 10:00:01   | S7-1511T  | motor_speed  | 1480.2
2024-01-01 10:00:01   | S7-1511T  | temperature  | 70.1
2024-01-01 10:00:01   | S7-1511T  | pressure     | 5.0
```

好處：新增變數完全不需要改表結構，直接寫新的 tag_name 就好。

### 超表（Hypertable）是什麼

```sql
SELECT create_hypertable('plc_measurements', 'time');
```

這行把普通表變成 TimescaleDB 的超表，效果是：

```
沒有超表：
一張大表，所有時間的資料混在一起
查詢「最近一小時」要掃整張表

有超表：
TimescaleDB 自動按時間切成多個「分塊（chunk）」
每個分塊只存一段時間的資料
查詢「最近一小時」只掃最新的分塊
```

---

## 8. ML 模型說明

### Isolation Forest 原理

Isolation Forest 的核心思想：**異常資料比較容易被「隔離」出來**。

想像把資料丟進一個隨機切割的箱子：
- 正常資料：附近有很多其他資料，要切很多刀才能隔離它
- 異常資料：附近沒什麼資料，很少幾刀就被隔離了

「需要幾刀才能隔離」就是異常分數的基礎。

### 為什麼是無監督學習

```
有監督學習：需要標記資料
"這筆是正常"  "這筆是異常"
→ 問題：工業設備的真實異常資料很少，很難收集

無監督學習（Isolation Forest）：只需要正常資料
→ 只用正常運行時的資料訓練
→ 偵測到跟「正常」差異大的資料就是異常
```

### 特徵工程的作用

原始資料是每秒一個數值，直接用來訓練效果差。特徵工程把原始數值轉換成更有意義的特徵：

```python
# 對每個 tag 計算 5 種統計特徵：
features[f"{tag}_mean"] = 滑動平均（代表水平）
features[f"{tag}_std"]  = 滑動標準差（代表穩定性）
features[f"{tag}_max"]  = 滑動最大值（代表峰值）
features[f"{tag}_min"]  = 滑動最小值（代表谷值）
features[f"{tag}_diff"] = 變化率（代表趨勢）
```

3 個 tag × 5 種統計 = 15 個特徵，讓模型能從「穩定性」和「趨勢」的角度判斷異常。

### 閾值（Threshold）的意義

```
Isolation Forest 的 decision_function 回傳 score：
score > 0：正常（越正越正常）
score < 0：可能異常（越負越異常）

我們設定 threshold = 0.05：
score < 0.05 → 判定為異常
```

閾值越高，越容易觸發警報（敏感）；越低，越不容易觸發（保守）。需要根據實際運行情況調整。

### 滑動視窗的作用

```
不用滑動視窗：每秒只看一個點
→ 一個瞬間的異常值就觸發警報（假警報多）

用滑動視窗（30秒）：看最近 30 秒的趨勢
→ 需要持續的異常才會觸發
→ 暖機期間（前 30 秒）顯示 warming_up
```

---

## 9. 套件總覽

### 後端 Python 套件

| 套件 | 用途 | 官方文件 |
|---|---|---|
| `fastapi` | Web 框架，REST + WebSocket | https://fastapi.tiangolo.com |
| `uvicorn` | ASGI 伺服器，跑 FastAPI | https://www.uvicorn.org |
| `asyncua` | OPC UA Client，連西門子 PLC | https://github.com/FreeOpcUa/opcua-asyncio |
| `asyncpg` | PostgreSQL 非同步驅動 | https://magicstack.github.io/asyncpg |
| `scikit-learn` | ML 套件，Isolation Forest | https://scikit-learn.org |
| `pandas` | 資料處理，長表轉寬表 | https://pandas.pydata.org |
| `joblib` | 儲存/載入 ML 模型 | https://joblib.readthedocs.io |
| `python-dotenv` | 讀取 .env 環境變數 | https://pypi.org/project/python-dotenv |
| `httpx` | 非同步 HTTP（之後呼叫 Ollama 用）| https://www.python-httpx.org |

### 前端 npm 套件

| 套件 | 用途 |
|---|---|
| `react` | UI 框架 |
| `typescript` | 型別安全，避免執行期錯誤 |
| `vite` | 開發伺服器，支援 HMR 熱更新 |
| `recharts` | React 圖表套件 |

### 基礎設施

| 工具 | 用途 |
|---|---|
| `Docker` | 容器化，跑 TimescaleDB |
| `TimescaleDB` | 時序資料庫（PostgreSQL 延伸）|
| `conda` | Python 環境管理 |
| `Git` | 版本控制 |

---

## 10. 常見問題與解法

### OPC UA 連不上

可能原因：
1. TIA Portal 的 DB 沒有取消「優化塊存取」
2. 變數沒有勾選「可從 HMI/OPC UA 存取」
3. OPC UA Server 的安全模式不是 None
4. 網路不通（先 `ping 192.168.0.10`）

### TimescaleDB 密碼認證失敗

原因：本機已有 PostgreSQL 佔用 5432 port，連線被攔截。

解法：TimescaleDB 換用其他 port（例如 5435）：
```bash
docker run -d --name timescaledb -p 5435:5432 ...
```

### ML 偵測不到異常

可能原因：
1. 訓練資料和真實資料範圍差太多（重新訓練）
2. 閾值設太嚴（調高 threshold）
3. 滑動視窗還在暖機（等 30 秒）

### uvicorn 自動 reload 沒觸發

確認有加 `--reload` 參數，且修改的是 `backend/` 目錄下的 `.py` 檔案。

---

## 11. 未來可擴充方向

### 近期（Phase 4）

**整合 Ollama 本地 LLM：**
- Isolation Forest 偵測到異常後，呼叫 Ollama 的 API
- 讓語言模型根據異常數值生成人類可讀的說明文字
- 例如：「溫度均值超過正常範圍 15°C，且轉速不穩定，建議檢查冷卻系統」

### 中期

**接入三菱 FX5U（feature/fx5u-slmp 分支）：**
- `pymcprotocol` 替換 OPC UA 讀值部分
- 同一套後端架構支援多廠牌 PLC

**Docker Compose 整合：**
- 把 FastAPI、TimescaleDB、React 全部打包
- 一個指令跑起整個系統

**歷史資料查詢 API：**
- 新增 `GET /api/history?tag=motor_speed&from=...&to=...`
- 前端加上時間選擇器

### 長期

**雲端部署：**
- FastAPI 打包成 Docker image
- 部署到 GCP Cloud Run 或 AWS EC2
- 設定 nginx + HTTPS

**PyTorch LSTM 模型：**
- 比 Isolation Forest 更精準的時序異常偵測
- 可以預測「未來幾分鐘的趨勢」

**CI/CD：**
- GitHub Actions 自動測試和部署

---

## 附錄：快速啟動指令

```bash
# 1. 啟動 TimescaleDB
docker start timescaledb

# 2. 啟動後端
conda activate iot-scada
cd iot-scada-platform/backend
uvicorn main:app --reload --port 8000

# 3. 啟動前端（另一個終端機）
cd iot-scada-platform/frontend
npm run dev

# 4. 開啟瀏覽器
# http://localhost:5173
```

---

*文件版本：v1.0 | 對應專案：iot-scada-platform | 最後更新：2026-06*