# IIoT SCADA Platform — main

> 端對端工業物聯網監控系統 — Siemens S7-1511T × OPC UA 整合分支

![Platform](https://img.shields.io/badge/Platform-IIoT%20%7C%20SCADA-00c8b4?style=flat-square)
![Backend](https://img.shields.io/badge/Backend-FastAPI%20%2B%20asyncio-009688?style=flat-square)
![Frontend](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-3178c6?style=flat-square)
![Database](https://img.shields.io/badge/Database-TimescaleDB-orange?style=flat-square)
![ML](https://img.shields.io/badge/ML-Isolation%20Forest-8e44ad?style=flat-square)
![AI](https://img.shields.io/badge/AI-Ollama%20qwen3%3A14b-ff6b35?style=flat-square)
![PLC](https://img.shields.io/badge/PLC-Siemens%20S7--1511T-0078d4?style=flat-square)

---

## 分支說明

| 分支 | 設備 | 協議 |
|---|---|---|
| **`main`（本分支）** | **Siemens S7-1511T** | **OPC UA（port 4840）** |
| `feature/fx5u-slmp` | Mitsubishi FX5U | SLMP（MC Protocol 3E）|

---

## 系統架構

```
┌─────────────────────────────────────────────────────────────────┐
│                        OT 設備層                                  │
│   Siemens S7-1511T                                               │
│   OPC UA Server  Port 4840                                       │
│   IP：192.168.0.10                                               │
└────────────────────────┬────────────────────────────────────────┘
                         │ opc.tcp://192.168.0.10:4840
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Python 後端（FastAPI + asyncio）                 │
│                                                                   │
│  每秒讀值（DB1 變數）                                             │
│      → Isolation Forest 推論                                      │
│      → 異常時呼叫 Ollama（60秒冷卻）                              │
│      → WebSocket 廣播                                            │
│      → TimescaleDB 寫入（背景非同步）                            │
│      → anomaly_events 寫入（異常時）                             │
│      → 每小時自動再訓練檢查                                       │
└──────────────┬──────────────────────────────────────────────────┘
               ↓ WebSocket                    ↓ Tailscale VPN
┌──────────────────────────┐   ┌──────────────────────────────────┐
│       React 前端          │   │  桌電本地 Ollama                  │
│  GaugeCard（即時數值）    │   │  100.89.23.28:11434              │
│  RealtimeChart（趨勢圖）  │   │  qwen3:14b                       │
│  AnomalyPanel（AI 診斷）  │   │  RTX 5060 Ti 16GB                │
│  AnomalyHistory（歷史）   │   └──────────────────────────────────┘
│  ControlPanel（反向控制） │
└──────────────────────────┘
```

---

## DB1 變數結構

| 名稱 | 型別 | Offset | 說明 |
|---|---|---|---|
| `motor_enable` | Bool | 0.0 | 馬達啟停控制（OPC UA 寫入）|
| `speed_setpoint` | Real | 2.0 | 轉速設定值（OPC UA 寫入）|
| `motor_speed` | Real | 6.0 | 轉速讀取 |
| `temperature` | Real | 10.0 | 溫度讀取 |
| `pressure` | Real | 14.0 | 壓力讀取 |

---

## 技術棧

| 層級 | 技術 | 說明 |
|---|---|---|
| **PLC 通訊** | OPC UA (`asyncua`) | S7-1511T 原生支援，無需 Gateway |
| **後端框架** | FastAPI + asyncio | 非同步架構，輪詢與 WebSocket 並行 |
| **即時通訊** | WebSocket | 後端主動推送，低延遲 |
| **時序資料庫** | TimescaleDB | PostgreSQL 延伸，自動時間分區 |
| **資料生命週期** | Retention + Compression | 90天刪除，30天壓縮 |
| **ML 模型** | Isolation Forest | 無監督學習，不需標記資料 |
| **自動再訓練** | Auto Retraining Pipeline | 每天用最新30天資料重訓 |
| **本地 LLM** | Ollama qwen3:14b | 結合 TimescaleDB 趨勢診斷 |
| **VPN** | Tailscale | P2P 加密隧道連接桌電 Ollama |
| **前端框架** | React + TypeScript | 元件化設計，型別安全 |
| **容器化** | Docker Compose | 一鍵啟動後端與前端 |

---

## 專案結構

```
iot-scada-platform/
├── backend/
│   ├── main.py                        # FastAPI 進入點，lifespan 管理
│   ├── core/
│   │   ├── ws_manager.py              # WebSocket 連線管理
│   │   └── plc_simulator.py           # OPC UA 輪詢 + Ollama 整合
│   ├── routers/
│   │   ├── realtime.py                # /ws/realtime
│   │   ├── control.py                 # /api/control/write（OPC UA 寫入）
│   │   └── history.py                 # /api/history、/api/anomaly-events
│   ├── db/
│   │   ├── writer.py                  # asyncpg 寫入 TimescaleDB
│   │   └── anomaly_writer.py          # 異常事件寫入 anomaly_events
│   ├── ml/
│   │   ├── train.py                   # Isolation Forest 訓練腳本
│   │   ├── inferencer.py              # 即時推論，滑動視窗特徵工程
│   │   ├── ollama_analyzer.py         # Ollama AI 診斷 + DB 趨勢查詢
│   │   └── auto_trainer.py            # 自動再訓練排程
│   ├── models/                        # 訓練好的模型（按日期命名）
│   ├── generate_training_data.py      # 產生模擬訓練資料
│   ├── test_opcua.py                  # OPC UA 節點掃描與讀寫測試
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.tsx
│       ├── hooks/useWebSocket.ts
│       └── components/
│           ├── GaugeCard.tsx
│           ├── RealtimeChart.tsx
│           ├── ControlPanel.tsx
│           ├── AnomalyPanel.tsx       # ML 異常 + AI 診斷
│           └── AnomalyHistory.tsx     # 異常歷史查詢面板
│   └── Dockerfile
├── docker-compose.yml                 # 本機部署（使用現有 TimescaleDB）
├── docker-compose.full.yml            # 全新部署（含 TimescaleDB）
└── README.md
```

---

## 快速開始

### 環境需求

- Docker Desktop
- Python 3.11（conda，開發用）
- Node.js 20+（開發用）
- Siemens TIA Portal V17+（連接真實 PLC）
- Ollama + qwen3:14b（本地或 Tailscale 連接）

### 方式一：Docker Compose（推薦）

```bash
# 確認 TimescaleDB 已啟動
docker start timescaledb

# 一鍵啟動後端 + 前端
docker compose up --build
```

開啟瀏覽器：
- 前端：`http://localhost:3000`
- API 文件：`http://localhost:8000/docs`

### 方式二：本地開發模式

```bash
# 終端機一：後端
conda activate iot-scada
cd backend
uvicorn main:app --reload --port 8000

# 終端機二：前端
cd frontend
npm run dev
```

### 首次訓練 ML 模型

```bash
# 產生 5000 筆模擬訓練資料寫入 TimescaleDB
python generate_training_data.py

# 訓練 Isolation Forest 模型
python ml/train.py
```

之後系統會自動每天重訓，無需手動執行。

---

## TIA Portal 設定

### OPC UA Server 設定

1. CPU 屬性 → OPC UA → Server → 勾選「啟動 OPC UA Server」
2. Port 4840，安全模式 `None`，啟用匿名存取
3. **不建立 Server Interface**（避免 TIA Portal V17 RAM 不足當機）

### DB1 設定

1. DB1 屬性 → 取消勾選「優化塊存取」
2. 所有變數 → 勾選「可從 HMI/OPC UA 存取」
3. 編譯並下載至 PLC

### SCL 亂數波動程式（OB1）

```scl
// 使用 LGF_RandomRange_Real 產生自然波動
"DB1".motor_speed := LGF_RandomRange_Real(minValue:=1450.0, maxValue:=1510.0);
"DB1".temperature := LGF_RandomRange_Real(minValue:=67.0,   maxValue:=73.0);
"DB1".pressure    := LGF_RandomRange_Real(minValue:=4.6,    maxValue:=5.4);
```

### Python 連線

```python
from asyncua import Client

PLC_URL = "opc.tcp://192.168.0.10:4840"
NS = 3  # Siemens Namespace Index

async with Client(url=PLC_URL) as client:
    motor_speed_node = await client.nodes.root.get_child([
        "0:Objects", f"{NS}:PLC_1",
        f"{NS}:DataBlocksGlobal", f"{NS}:DB1", f"{NS}:motor_speed"
    ])
    spd = await motor_speed_node.read_value()
```

---

## Ollama AI 診斷設定

### 桌電設定

```bash
# 設定系統環境變數
OLLAMA_HOST = 0.0.0.0

# 下載模型
ollama pull qwen3:14b
```

### Tailscale 連線

```
桌電 Tailscale IP：100.89.23.28
筆電 → Tailscale VPN → 桌電 Ollama:11434
```

---

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| `WS` | `/ws/realtime` | 即時 PLC 資料推送 |
| `POST` | `/api/control/write` | 反向控制（motor_enable/speed_setpoint）|
| `GET` | `/api/history` | 歷史資料查詢 |
| `GET` | `/api/anomaly-events` | 異常事件記錄查詢 |
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
    "score": 0.1483,
    "status": "normal",
    "ai_analysis": null
  }
}
```

### 控制指令格式

```json
POST /api/control/write

// Motor ON
{"tag": "motor_enable", "value": 1}

// Set Speed
{"tag": "motor_speed_setpoint", "value": 1500}
```

---

## ML 自動再訓練

```
系統啟動
    ↓ 每小時檢查
資料量 >= 5000 筆 且 模型非今日
    ↓
用最近 30 天資料重新訓練
    ↓
儲存 models/S7-1511T_anomaly_YYYYMMDD_HHmmss.pkl
    ↓
detector.reload_model() 熱更新（不需重啟）
```

---

## TimescaleDB 資料生命週期

```sql
-- 90 天自動刪除（job 1002）
SELECT add_retention_policy('plc_measurements', INTERVAL '90 days');

-- 30 天自動壓縮（job 1001）
SELECT add_compression_policy('plc_measurements', INTERVAL '30 days');
```

---

## 技術難點

### 1. OT/IT 整合的通訊障壁

直接透過 OPC UA 與 Siemens S7-1511T 通訊，不依賴第三方 Gateway。TIA Portal V17 編譯 OPC UA Server Interface 時因 RAM 不足（82%）直接當機，改用 Standard Interface 繞過，僅需在 DB 變數勾選存取權限即可。

### 2. OPC UA 多 Session 架構

OPC UA 原生支援多 Session 同時連線，輪詢（plc_simulator.py）和寫入（control.py）可以使用各自獨立的 Session，不會像 SLMP 發生連線衝突，架構更簡潔。

### 3. 非同步架構設計

PLC 輪詢、WebSocket 廣播、資料庫寫入、Ollama 呼叫四件事並行不互相阻塞。透過 `asyncio.create_task()` 讓資料庫寫入和 AI 診斷在背景執行，確保即時推送低延遲。

### 4. 無標記資料的異常偵測

Isolation Forest 無監督學習，僅需正常運行資料即可訓練。自動再訓練 Pipeline 每天用最新 30 天真實 PLC 資料重訓，模型持續適應設備的個別特性。

### 5. 本地 LLM + TimescaleDB 趨勢整合

Ollama 診斷查詢 TimescaleDB 最近 5 分鐘的 MIN/MAX/AVG/RANGE 趨勢，提供比單點數值更準確的診斷，同時資料完全不外傳。

---

## 履歷技能關鍵字

`OPC UA` `IIoT` `SCADA` `FastAPI` `WebSocket` `asyncio` `React` `TypeScript` `TimescaleDB` `PostgreSQL` `Docker` `Scikit-Learn` `Isolation Forest` `Ollama` `LLM` `Tailscale` `Siemens S7-1500` `TIA Portal` `Python` `時序資料庫` `異常偵測` `自動再訓練`

---

## License

MIT