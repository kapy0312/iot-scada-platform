# IIoT SCADA Platform

> 端對端工業物聯網監控系統 — 從 PLC 設備資料採集到 AI 異常偵測與本地 LLM 診斷的完整實作

![Platform](https://img.shields.io/badge/Platform-IIoT%20%7C%20SCADA-00c8b4?style=flat-square)
![Backend](https://img.shields.io/badge/Backend-FastAPI%20%2B%20asyncio-009688?style=flat-square)
![Frontend](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-3178c6?style=flat-square)
![Database](https://img.shields.io/badge/Database-TimescaleDB-orange?style=flat-square)
![ML](https://img.shields.io/badge/ML-Isolation%20Forest-8e44ad?style=flat-square)
![AI](https://img.shields.io/badge/AI-Ollama%20qwen3%3A14b-ff6b35?style=flat-square)
![PLC](https://img.shields.io/badge/PLC-Siemens%20S7--1511T-0078d4?style=flat-square)
![PLC](https://img.shields.io/badge/PLC-Mitsubishi%20FX5U-e60012?style=flat-square)

---

## 專案簡介

本專案為一套完整的 **Web-based SCADA 系統**，實現工業設備資料從 OT 端採集到 IT 端分析的完整鏈路。整合機器學習模組進行設備健康度即時監控，並透過本地 LLM（Ollama）自動生成異常診斷說明。

### 為什麼這個專案有技術含金量

市面上大多數 SCADA 系統不是閉源商業軟體（WinCC、iFIX、Ignition），就是缺乏 AI 整合能力。本專案從零建構，同時具備：

- **OT 端深度整合**：直接透過 OPC UA（Siemens）和 SLMP（Mitsubishi）與 PLC 通訊，無需第三方 Gateway
- **現代 IT 架構**：FastAPI + asyncio 非同步設計，WebSocket 即時推送，React TypeScript 前端
- **時序資料工程**：TimescaleDB 超表自動時間分區，支援高頻寫入與快速範圍查詢
- **無監督 ML**：Isolation Forest 異常偵測，不需要標記資料，適合工業場景
- **本地 LLM 診斷**：Ollama qwen3:14b 結合 TimescaleDB 趨勢資料生成繁體中文診斷說明，透過 Tailscale VPN 連接本地 GPU 主機，資料不外傳

---

## 系統架構

```
┌─────────────────────────────────────────────────────────────────┐
│                        OT 設備層                                  │
│   Siemens S7-1511T（OPC UA）    Mitsubishi FX5U（SLMP）          │
│   192.168.0.10:4840             127.0.0.1:5011（Mock Server）    │
└────────────────────────┬────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                  Python 後端（FastAPI + asyncio）                 │
│                                                                   │
│  每秒讀值 → Isolation Forest 推論 → 異常時呼叫 Ollama（60秒冷卻）│
│      ↓                                    ↓                      │
│  WebSocket 廣播                    查詢 5分鐘趨勢                 │
│      ↓                             → qwen3:14b 生成診斷文字      │
│  TimescaleDB 寫入（背景非同步）                                   │
└──────────────┬──────────────────────────────────────────────────┘
               ↓ WebSocket                          ↓ Tailscale VPN
┌──────────────────────────┐           ┌────────────────────────────┐
│       React 前端          │           │  桌電本地 Ollama            │
│  GaugeCard（即時數值）    │           │  100.89.23.28:11434         │
│  RealtimeChart（趨勢圖）  │           │  qwen3:14b（14B 參數）      │
│  AnomalyPanel（AI 診斷）  │           │  RTX 5060 Ti 16GB 推論      │
│  ControlPanel（反向控制） │           └────────────────────────────┘
└──────────────────────────┘
```

### 資料的三條路徑

```
路徑一（即時顯示）：
PLC → 每秒讀值 → ML推論 → WebSocket廣播 → 前端畫面更新

路徑二（資料儲存）：
PLC → 每秒讀值 → asyncpg → TimescaleDB

路徑三（AI診斷，異常時觸發）：
異常觸發 → 查詢TimescaleDB 5分鐘趨勢 → Ollama生成診斷 → 廣播到前端
```

---

## 技術棧

| 層級 | 技術 | 說明 |
|---|---|---|
| **PLC 通訊** | OPC UA (`asyncua`) | Siemens S7-1511T 原生支援，無需 Gateway |
| **PLC 通訊** | SLMP (`pymcprotocol`) | Mitsubishi FX5U，含 Python Mock Server |
| **後端框架** | FastAPI + asyncio | 非同步架構，同時處理輪詢、WebSocket、AI 呼叫 |
| **即時通訊** | WebSocket | 後端主動推送，低延遲 |
| **時序資料庫** | TimescaleDB | PostgreSQL 延伸，自動時間分區 |
| **ML 模型** | Isolation Forest | 無監督學習，不需標記資料 |
| **本地 LLM** | Ollama qwen3:14b | 本地推論，資料不外傳，透過 Tailscale 連接 |
| **VPN** | Tailscale | P2P 加密隧道，連接桌電 Ollama 服務 |
| **前端框架** | React + TypeScript | 元件化設計，型別安全 |
| **圖表** | Recharts | 即時折線圖，動畫關閉避免閃爍 |
| **容器化** | Docker | TimescaleDB 容器部署 |
| **開發環境** | conda + Vite | 環境隔離，HMR 熱更新 |

---

## 專案結構

```
iot-scada-platform/
├── backend/
│   ├── main.py                        # FastAPI 進入點，lifespan 管理
│   ├── core/
│   │   ├── ws_manager.py              # WebSocket 連線管理，broadcast 機制
│   │   └── plc_simulator.py           # PLC 輪詢 + ML推論 + Ollama整合
│   ├── routers/
│   │   ├── realtime.py                # /ws/realtime WebSocket 端點
│   │   └── control.py                 # /api/control/write REST 端點
│   ├── db/
│   │   └── writer.py                  # asyncpg 非同步寫入 TimescaleDB
│   ├── ml/
│   │   ├── train.py                   # Isolation Forest 訓練腳本
│   │   ├── inferencer.py              # 即時推論，滑動視窗特徵工程
│   │   └── ollama_analyzer.py         # Ollama AI 診斷，結合 DB 趨勢
│   ├── models/
│   │   └── S7-1511T_anomaly_v1.pkl    # 訓練好的模型
│   ├── fx5u_mock_server.py            # FX5U SLMP 模擬器（fx5u 分支）
│   └── generate_training_data.py      # 產生模擬訓練資料
├── frontend/
│   └── src/
│       ├── App.tsx                    # 根元件，資料流協調
│       ├── index.css                  # 工業風設計系統（CSS 變數）
│       ├── hooks/
│       │   └── useWebSocket.ts        # WS 連線 Hook，自動重連
│       └── components/
│           ├── GaugeCard.tsx          # 數值卡片，警戒值變色
│           ├── RealtimeChart.tsx      # 即時趨勢圖
│           ├── ControlPanel.tsx       # 反向控制面板
│           └── AnomalyPanel.tsx       # ML 異常 + AI 診斷面板
└── README.md
```

---

## 快速開始

### 環境需求

- Python 3.11（conda 管理）
- Node.js 18+
- Docker Desktop
- Ollama（本地 LLM，可選）
- Siemens TIA Portal V17+（連接真實 PLC 時需要）

### 1. 建立 Python 環境

```bash
conda create -n iot-scada python=3.11 -y
conda activate iot-scada
cd backend
pip install fastapi uvicorn websockets asyncua pymcprotocol asyncpg \
            scikit-learn pandas joblib httpx python-dotenv
```

### 2. 啟動 TimescaleDB

```bash
# 注意：若本機有 PostgreSQL 佔用 5432，改用 5435
docker run -d --name timescaledb \
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
# 產生訓練資料（5000 筆模擬正常資料寫入 TimescaleDB）
python generate_training_data.py

# 訓練 Isolation Forest 模型
python ml/train.py
```

### 4. 啟動後端

```bash
# Siemens 版本（main 分支）
uvicorn main:app --reload --port 8000

# FX5U 版本（feature/fx5u-slmp 分支，需先啟動 Mock Server）
python fx5u_mock_server.py         # 終端機一
uvicorn main:app --reload --port 8000  # 終端機二
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

### Siemens S7-1511T（OPC UA）— main 分支

**TIA Portal 設定步驟：**

1. CPU 屬性 → OPC UA → Server → 勾選「啟動 OPC UA Server」
2. 安全模式選 `None`，啟用匿名存取（開發環境）
3. DB1 屬性 → 取消勾選「優化塊存取」
4. DB1 變數 → 勾選「可從 HMI/OPC UA 存取」
5. 編譯並下載至 PLC

**Python 連線：**

```python
PLC_URL = "opc.tcp://192.168.0.10:4840"

motor_speed_node = await client.nodes.root.get_child([
    "0:Objects", "3:PLC_1", "3:DataBlocksGlobal", "3:DB1", "3:motor_speed"
])
```

### Mitsubishi FX5U（SLMP）— feature/fx5u-slmp 分支

GX Works3 設定：Ethernet Configuration → 新增 SLMP Connection Module，Protocol TCP，Port 5011。

**Python 連線：**

```python
import pymcprotocol
plc = pymcprotocol.Type3E(plctype="iQ-L")
plc.connect("192.168.0.20", 5011)
values = plc.batchread_wordunits(headdevice="D100", readsize=3)
motor_speed = values[0]
temperature = values[1] / 10.0  # D101 × 10 還原
pressure    = values[2] / 10.0  # D102 × 10 還原
```

**無硬體測試（Mock Server）：**

```bash
python fx5u_mock_server.py
# 模擬 FX5U 在 127.0.0.1:5011 提供 SLMP 服務
# 支援 D100~D102 讀取，資料自然波動
```

---

## Ollama AI 診斷設定

### 需求

- 本地執行 Ollama 的 GPU 主機（本專案使用 RTX 5060 Ti 16GB）
- Tailscale 連接筆電與桌電（免費，個人用戶永久免費）

### 桌電設定

```bash
# 設定 Ollama 對所有網路介面開放
# 系統環境變數：OLLAMA_HOST = 0.0.0.0

# 下載模型
ollama pull qwen3:14b

# 確認服務運作
ollama serve
```

### Tailscale 設定

1. 兩台電腦都安裝 Tailscale（https://tailscale.com/download）
2. 登入同一個帳號
3. 記錄桌電的 Tailscale IP（如 100.89.23.28）

### 修改連線設定

```python
# backend/ml/ollama_analyzer.py
OLLAMA_URL   = "http://100.89.23.28:11434"  # 桌電 Tailscale IP
OLLAMA_MODEL = "qwen3:14b"
```

### AI 診斷輸出範例

```
AI 診斷說明
原因：馬達轉速持續偏高，5分鐘均值 9551 RPM 遠超正常範圍
處置：立即停機檢查變頻器與馬達負載，確認控制訊號是否正常
```

---

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| `WS` | `/ws/realtime` | 即時 PLC 資料推送（每秒） |
| `POST` | `/api/control/write` | 反向控制指令 |
| `GET` | `/health` | 健康檢查 |
| `GET` | `/docs` | Swagger API 文件 |

### WebSocket 資料格式（含 AI 診斷）

```json
{
  "timestamp": 1718000001.23,
  "motor_speed": 9965.0,
  "temperature": 36.7,
  "pressure": 51.7,
  "motor_enable": 1,
  "anomaly": {
    "is_anomaly": true,
    "score": -0.0176,
    "status": "anomaly",
    "ai_analysis": "原因：馬達轉速過高，溫度與壓力均異常偏離正常範圍\n處置：立即停機檢查馬達、冷卻系統及壓力控制裝置"
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

**特徵工程（N個tag × 5種統計）：**

| 特徵 | 意義 |
|---|---|
| `_mean` | 滑動均值，代表水平 |
| `_std` | 滑動標準差，代表穩定性 |
| `_max` | 滑動最大值，代表峰值 |
| `_min` | 滑動最小值，代表谷值 |
| `_diff` | 變化率，代表趨勢 |

**推論流程：**

```
新資料進來（每秒）
    → 加入滑動視窗（30筆）
    → 計算統計特徵
    → Isolation Forest 判斷
    → score < 0.15 → 異常警報 + 觸發 Ollama（60秒冷卻）
```

**訓練集資訊：**

| 指標 | 數值 |
|---|---|
| 訓練樣本 | 4,971 筆 |
| 特徵數量 | 15（3個tag × 5種統計）|
| 異常比例（contamination）| 2% |
| 滑動視窗 | 30 秒 |

---

## 技術難點

### 1. OT/IT 整合的通訊障壁

工業 PLC 使用私有通訊協議（OPC UA、SLMP），不同廠牌有不同實作細節。本專案直接實作協議層，不依賴第三方 Gateway，覆蓋 Siemens（OPC UA）和 Mitsubishi（SLMP）兩大廠牌。針對 Mitsubishi 無實體硬體的情況，自行實作 SLMP 3E Binary 封包格式的 Python Mock Server。

### 2. 非同步架構設計

PLC 輪詢、WebSocket 廣播、資料庫寫入、Ollama 呼叫四件事需要並行進行互不阻塞。透過 `asyncio.create_task()` 讓資料庫寫入和 AI 診斷在背景執行，確保即時推送的低延遲。

### 3. 時序資料的高頻寫入

每秒多筆資料寫入，長期運行下資料量達數百萬筆。TimescaleDB 的 Hypertable 自動時間分區，確保查詢效能不隨資料量線性劣化。

### 4. 無標記資料的異常偵測

工業設備的真實異常事件極少，無法收集足夠的標記資料訓練監督式模型。採用 Isolation Forest 無監督學習，並透過滑動視窗統計特徵降低瞬間噪聲的假警報率。

### 5. 本地 LLM 整合與資料隱私

工廠資料具有高度機密性，不適合送上第三方 API。採用 Ollama 本地推論，結合 Tailscale P2P VPN 連接配備 GPU 的本地主機，實現資料完全不外傳的 AI 診斷能力。AI 診斷結合 TimescaleDB 5 分鐘趨勢資料，提供比單點數值更準確的分析。

---

## 分支說明

| 分支 | 設備 | 協議 | 狀態 |
|---|---|---|---|
| `main` | Siemens S7-1511T | OPC UA | 已連接真實硬體 |
| `feature/fx5u-slmp` | Mitsubishi FX5U | SLMP | Mock Server 驗證 |

切換後注意事項：
- **main**：確認 S7-1511T 已上線，直接啟動 uvicorn
- **feature/fx5u-slmp**：需先啟動 `fx5u_mock_server.py`，再啟動 uvicorn

---

## 履歷技能關鍵字

`OPC UA` `SLMP` `IIoT` `SCADA` `FastAPI` `WebSocket` `asyncio` `React` `TypeScript` `TimescaleDB` `PostgreSQL` `Docker` `Scikit-Learn` `Isolation Forest` `Ollama` `LLM` `Tailscale` `Siemens S7-1500` `Mitsubishi FX5U` `Python` `時序資料庫` `異常偵測` `邊緣運算`

---

## License

MIT