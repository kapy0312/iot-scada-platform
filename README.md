# IIoT SCADA Platform

> 端對端工業物聯網監控系統 — 從 PLC 設備資料採集到 AI 異常偵測的完整實作

![Platform](https://img.shields.io/badge/Platform-IIoT%20%7C%20SCADA-00c8b4?style=flat-square)
![Backend](https://img.shields.io/badge/Backend-FastAPI%20%2B%20asyncio-009688?style=flat-square)
![Frontend](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-3178c6?style=flat-square)
![Database](https://img.shields.io/badge/Database-TimescaleDB-orange?style=flat-square)
![ML](https://img.shields.io/badge/ML-Isolation%20Forest-8e44ad?style=flat-square)
![PLC](https://img.shields.io/badge/PLC-Siemens%20S7--1511T-0078d4?style=flat-square)

---

## 專案簡介

本專案為一套完整的 **Web-based SCADA 系統**，實現工業設備資料從 OT 端採集到 IT 端分析的完整鏈路，並整合機器學習模組進行設備健康度即時監控。

### 為什麼這個專案有技術含金量

市面上大多數 SCADA 系統不是閉源商業軟體（WinCC、iFIX、Ignition），就是缺乏 AI 整合能力。本專案從零建構，同時具備：

- **OT 端深度整合**：直接透過 OPC UA 與 Siemens S7-1500 PLC 通訊，無需第三方 Gateway
- **現代 IT 架構**：FastAPI + asyncio 非同步設計，WebSocket 即時推送，React TypeScript 前端
- **時序資料工程**：TimescaleDB 超表自動時間分區，支援高頻寫入與快速範圍查詢
- **無監督 ML**：Isolation Forest 異常偵測，不需要標記資料，適合工業場景

---

## 系統架構

```
┌─────────────────────────────────────────────────────────┐
│                     OT 設備層                             │
│   Siemens S7-1511T  ←→  OPC UA (port 4840)             │
└────────────────────────┬────────────────────────────────┘
                         │ opc.tcp://192.168.0.10:4840
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Python 後端  (FastAPI + asyncio)            │
│                                                          │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────┐  │
│  │ OPC UA Poll │──▶│ ML Inference │──▶│  WS Manager │  │
│  │  每秒讀值   │   │ Isolation    │   │  broadcast  │  │
│  └─────────────┘   │ Forest       │   └──────┬──────┘  │
│         │          └──────────────┘          │          │
│         ▼                                    │          │
│  ┌─────────────┐                             │          │
│  │ TimescaleDB │                             │          │
│  │   Writer    │                   WebSocket │          │
│  └─────────────┘                             │          │
└─────────────────────────────────────────────┼──────────┘
                                               ▼
┌─────────────────────────────────────────────────────────┐
│              React 前端  (TypeScript + Vite)             │
│                                                          │
│  GaugeCard × 3  │  RealtimeChart × 3  │  AnomalyPanel  │
│  即時數值顯示    │  Recharts 趨勢圖    │  ML 警報面板   │
│                  ControlPanel                            │
│                  反向控制介面                            │
└─────────────────────────────────────────────────────────┘
```

### 資料流

```
PLC 每秒讀值
    → Isolation Forest 推論（正常 / 異常）
    → WebSocket 廣播至所有前端
    → TimescaleDB 持久化（非同步，不阻塞廣播）
    → React 重新渲染（Gauge / Chart / AnomalyPanel）
```

---

## 技術棧

| 層級 | 技術 | 說明 |
|---|---|---|
| **PLC 通訊** | OPC UA (`asyncua`) | Siemens S7-1511T 原生支援，無需 Gateway |
| **後端框架** | FastAPI + asyncio | 非同步架構，同時處理輪詢與 WebSocket |
| **即時通訊** | WebSocket | 後端主動推送，低延遲 |
| **時序資料庫** | TimescaleDB | PostgreSQL 延伸，自動時間分區 |
| **ML 模型** | Isolation Forest | 無監督學習，不需標記資料 |
| **前端框架** | React + TypeScript | 元件化設計，型別安全 |
| **圖表** | Recharts | 即時折線圖，動畫關閉避免閃爍 |
| **容器化** | Docker | TimescaleDB 容器部署 |
| **開發環境** | conda + Vite | 環境隔離，HMR 熱更新 |

---

## 專案結構

```
iot-scada-platform/
├── backend/
│   ├── main.py                   # FastAPI 應用進入點，lifespan 管理
│   ├── core/
│   │   ├── ws_manager.py         # WebSocket 連線管理，broadcast 機制
│   │   └── plc_simulator.py      # OPC UA 輪詢，斷線自動重連
│   ├── routers/
│   │   ├── realtime.py           # /ws/realtime WebSocket 端點
│   │   └── control.py            # /api/control/write REST 端點
│   ├── db/
│   │   └── writer.py             # asyncpg 非同步寫入 TimescaleDB
│   ├── ml/
│   │   ├── train.py              # Isolation Forest 訓練腳本
│   │   └── inferencer.py         # 即時推論，滑動視窗特徵工程
│   └── models/
│       └── S7-1511T_anomaly_v1.pkl
├── frontend/
│   └── src/
│       ├── App.tsx               # 根元件，資料流協調
│       ├── index.css             # 工業風設計系統（CSS 變數）
│       ├── hooks/
│       │   └── useWebSocket.ts   # WS 連線 Hook，自動重連
│       └── components/
│           ├── GaugeCard.tsx     # 數值卡片，警戒值變色
│           ├── RealtimeChart.tsx # 即時趨勢圖
│           ├── ControlPanel.tsx  # 反向控制面板
│           └── AnomalyPanel.tsx  # ML 異常警報
└── README.md
```

---

## 快速開始

### 環境需求

- Python 3.11（conda 管理）
- Node.js 18+
- Docker Desktop
- Siemens TIA Portal V17+（連接真實 PLC 時需要）

### 1. 建立 Python 環境

```bash
conda create -n iot-scada python=3.11 -y
conda activate iot-scada
cd backend
pip install fastapi uvicorn websockets asyncua asyncpg scikit-learn pandas joblib httpx python-dotenv
```

### 2. 啟動 TimescaleDB

```bash
docker run -d \
  --name timescaledb \
  -p 5435:5432 \
  -e POSTGRES_PASSWORD=iotscada123 \
  -e POSTGRES_DB=iotscada \
  timescale/timescaledb:latest-pg16
```

初始化資料表：

```bash
docker exec -it timescaledb psql -U postgres -d iotscada -c "
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE plc_measurements (
    time        TIMESTAMPTZ NOT NULL,
    device_id   TEXT NOT NULL,
    tag_name    TEXT NOT NULL,
    value       DOUBLE PRECISION,
    quality     SMALLINT DEFAULT 192
);
SELECT create_hypertable('plc_measurements', 'time');
CREATE INDEX ON plc_measurements (device_id, tag_name, time DESC);
"
```

### 3. 訓練 ML 模型（首次需要）

```bash
# 產生訓練資料（約 5000 筆模擬正常資料）
python generate_training_data.py

# 訓練 Isolation Forest 模型
python ml/train.py
```

### 4. 啟動後端

```bash
conda activate iot-scada
cd backend
uvicorn main:app --reload --port 8000
```

### 5. 啟動前端

```bash
cd frontend
npm install
npm run dev
```

### 6. 開啟儀表板

瀏覽器開啟 [http://localhost:5173](http://localhost:5173)

---

## PLC 連線設定

### Siemens S7-1511T（OPC UA）

**TIA Portal 設定步驟：**

1. CPU 屬性 → OPC UA → Server → 勾選「啟動 OPC UA Server」
2. 安全模式選 `None`，啟用匿名存取（開發環境）
3. DB1 屬性 → 取消勾選「優化塊存取」
4. DB1 變數 → 勾選「可從 HMI/OPC UA 存取」
5. 編譯並下載至 PLC

**Python 連線：**

```python
# backend/core/plc_simulator.py
PLC_URL = "opc.tcp://192.168.0.10:4840"

# 節點路徑格式
motor_speed_node = await client.nodes.root.get_child([
    "0:Objects", "3:PLC_1", "3:DataBlocksGlobal", "3:DB1", "3:motor_speed"
])
```

### Mitsubishi FX5U（SLMP） — feature/fx5u-slmp 分支

```python
import pymcprotocol
plc = pymcprotocol.Type3E()
plc.connect("192.168.3.10", 5007)  # FX5U 預設 port 5007
_, values = plc.batchread_wordunits(headdevice="D100", readsize=3)
```

---

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| `WS` | `/ws/realtime` | 即時 PLC 資料推送（每秒） |
| `POST` | `/api/control/write` | 反向控制指令 |
| `GET` | `/health` | 健康檢查 |
| `GET` | `/docs` | Swagger API 文件 |

### WebSocket 資料格式

```json
{
  "timestamp": 1718000001.23,
  "motor_speed": 1481.2,
  "temperature": 70.15,
  "pressure": 5.02,
  "motor_enable": 1,
  "anomaly": {
    "is_anomaly": false,
    "score": 0.0483,
    "status": "normal"
  }
}
```

### 控制指令格式

```json
POST /api/control/write
{
  "tag": "motor_enable",
  "value": 0
}
```

---

## ML 模型說明

### 演算法：Isolation Forest

無監督學習異常偵測，無需標記資料，適合工業設備場景。

**特徵工程（15 個特徵）：**

每個感測器變數（motor_speed、temperature、pressure）計算 5 種滑動統計：
- `mean`：均值（代表水平）
- `std`：標準差（代表穩定性）
- `max`：最大值（代表峰值）
- `min`：最小值（代表谷值）
- `diff`：變化率（代表趨勢）

**推論流程：**

```
新資料進來
    → 加入滑動視窗（30筆）
    → 計算 15 個統計特徵
    → Isolation Forest 判斷
    → score < 0.05 → 異常警報
```

**模型效能（訓練集）：**

| 指標 | 數值 |
|---|---|
| 訓練樣本 | 4,971 筆 |
| 特徵數量 | 15 |
| 異常比例（contamination）| 2% |
| 異常分數範圍 | -0.075 ~ 0.272 |

---

## 技術難點

### 1. OT/IT 整合的通訊障壁

工業 PLC 使用私有通訊協議（OPC UA、SLMP、MC Protocol），不同廠牌有不同的實作細節。本專案直接實作協議層，不依賴第三方 Gateway，覆蓋 Siemens（OPC UA）和 Mitsubishi（SLMP）兩大廠牌。

### 2. 非同步架構設計

PLC 輪詢、WebSocket 廣播、資料庫寫入三件事需要並行進行互不阻塞。透過 `asyncio.create_task()` 讓資料庫寫入在背景執行，確保即時推送的低延遲。

### 3. 時序資料的高頻寫入

每秒 3 筆資料寫入，長期運行下資料量達數百萬筆。TimescaleDB 的 Hypertable 自動時間分區，確保查詢效能不隨資料量線性劣化。

### 4. 無標記資料的異常偵測

工業設備的真實異常事件極少，無法收集足夠的標記資料訓練監督式模型。採用 Isolation Forest 無監督學習，僅需正常運行期間的資料即可訓練，並透過滑動視窗統計特徵降低瞬間噪聲的假警報率。

---

## 分支說明

| 分支 | 說明 |
|---|---|
| `main` | Siemens S7-1511T + OPC UA |
| `feature/fx5u-slmp` | Mitsubishi FX5U + SLMP（開發中）|

---

## 履歷技能關鍵字

`OPC UA` `IIoT` `SCADA` `FastAPI` `WebSocket` `asyncio` `React` `TypeScript` `TimescaleDB` `PostgreSQL` `Docker` `Scikit-Learn` `Isolation Forest` `Siemens S7-1500` `Mitsubishi FX5U` `Python` `時序資料庫` `異常偵測`

---

## License

MIT