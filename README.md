# IIoT SCADA Platform — feature/fx5u-slmp

> 端對端工業物聯網監控系統 — Mitsubishi FX5U × SLMP 整合分支

![Platform](https://img.shields.io/badge/Platform-IIoT%20%7C%20SCADA-00c8b4?style=flat-square)
![Backend](https://img.shields.io/badge/Backend-FastAPI%20%2B%20asyncio-009688?style=flat-square)
![Frontend](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-3178c6?style=flat-square)
![Database](https://img.shields.io/badge/Database-TimescaleDB-orange?style=flat-square)
![ML](https://img.shields.io/badge/ML-Isolation%20Forest-8e44ad?style=flat-square)
![AI](https://img.shields.io/badge/AI-Ollama%20qwen3%3A14b-ff6b35?style=flat-square)
![PLC](https://img.shields.io/badge/PLC-Mitsubishi%20FX5U-e60012?style=flat-square)

---

## 分支說明

| 分支 | 設備 | 協議 |
|---|---|---|
| `main` | Siemens S7-1511T | OPC UA |
| **`feature/fx5u-slmp`（本分支）** | **Mitsubishi FX5U** | **SLMP（MC Protocol 3E）** |

本分支基於 `main` 分支的完整功能，將 PLC 通訊層替換為 Mitsubishi FX5U 的 SLMP 協議，其餘系統架構完全相同。

---

## 系統架構

```
┌─────────────────────────────────────────────────────────────────┐
│                        OT 設備層                                  │
│   Mitsubishi FX5U                                                │
│   SLMP（MC Protocol 3E Type）TCP Port 5011                       │
│   IP：192.168.0.20（實體硬體）                                    │
│   或 127.0.0.1（Python Mock Server）                              │
└────────────────────────┬────────────────────────────────────────┘
                         │ pymcprotocol TCP
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Python 後端（FastAPI + asyncio）                 │
│                                                                   │
│  每秒讀值（D100/D101/D102）                                       │
│      → Isolation Forest 推論                                      │
│      → 異常時呼叫 Ollama（60秒冷卻）                              │
│      → WebSocket 廣播                                            │
│      → TimescaleDB 寫入（背景非同步）                            │
│      → anomaly_events 寫入（異常時）                             │
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

## FX5U 暫存器映射

| 暫存器 | 對應變數 | 說明 |
|---|---|---|
| D100 | `motor_speed` | 馬達轉速（RPM，整數） |
| D101 | `temperature` | 溫度 × 10（700 = 70.0°C） |
| D102 | `pressure` | 壓力 × 10（50 = 5.0 bar） |
| M100 | `motor_enable` | 馬達啟停（1=ON / 0=OFF） |
| D110 | `speed_setpoint` | 轉速設定值（RPM） |

---

## 技術棧

| 層級 | 技術 | 說明 |
|---|---|---|
| **PLC 通訊** | SLMP (`pymcprotocol`) | FX5U 原生 MC Protocol，Type3E |
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
│   ├── main.py                        # FastAPI 進入點
│   ├── core/
│   │   ├── ws_manager.py              # WebSocket 連線管理
│   │   └── plc_simulator.py           # FX5U SLMP 輪詢（全域共用連線）
│   ├── routers/
│   │   ├── realtime.py                # /ws/realtime
│   │   ├── control.py                 # /api/control/write（M100/D110）
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
│   ├── fx5u_mock_server.py            # FX5U SLMP 模擬器（無硬體測試用）
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
- Mitsubishi FX5U + GX Works3（或使用 Mock Server）
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
# 終端機一：FX5U Mock Server（無實體硬體時）
conda activate iot-scada
cd backend
python fx5u_mock_server.py

# 終端機二：後端
uvicorn main:app --reload --port 8000

# 終端機三：前端
cd frontend
npm run dev
```

---

## GX Works3 設定

### SLMP 連線設定

```
Navigation → Parameter → FX5UCPU → Module Parameter → Ethernet Port
→ IP Address：192.168.0.20
→ Communication Data Code：Binary（二進制）
→ Ethernet Configuration → 新增 SLMP Connection Module
  Protocol：TCP
  Port No.：5011
```

### Python 連線

```python
import pymcprotocol

plc = pymcprotocol.Type3E(plctype="iQ-L")
plc.connect("192.168.0.20", 5011)

# 批次讀取 D100~D102
values = plc.batchread_wordunits(headdevice="D100", readsize=3)
motor_speed = values[0]          # D100
temperature = values[1] / 10.0  # D101 ÷ 10
pressure    = values[2] / 10.0  # D102 ÷ 10
```

### 重要：SLMP 連線衝突解法

FX5U 同時只允許少數 TCP 連線，輪詢與寫入必須共用同一條連線：

```python
# plc_simulator.py 維護全域連線
plc_instance = None

# control.py 直接引用，不新建連線
from core.plc_simulator import plc_instance
plc_instance.batchwrite_bitunits(headdevice="M100", values=[1])
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
| `POST` | `/api/control/write` | 反向控制（M100/D110）|
| `GET` | `/api/history` | 歷史資料查詢 |
| `GET` | `/api/anomaly-events` | 異常事件記錄查詢 |
| `GET` | `/health` | 健康檢查 |
| `GET` | `/docs` | Swagger API 文件 |

### 控制指令

```json
POST /api/control/write

// Motor ON
{"tag": "motor_enable", "value": 1}

// Motor OFF
{"tag": "motor_enable", "value": 0}

// Set Speed
{"tag": "motor_speed_setpoint", "value": 1500}
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

## ML 自動再訓練

```
系統啟動
    ↓ 每小時檢查
資料量 >= 5000 筆 且 模型非今日
    ↓
用最近 30 天資料重新訓練
    ↓
儲存 models/FX5U-MOCK_anomaly_YYYYMMDD_HHmmss.pkl
    ↓
detector.reload_model() 熱更新（不需重啟）
```

---

## 技術難點

### 1. SLMP 連線衝突

FX5U 的 SLMP TCP 連線數有限，輪詢連線佔用後，寫入連線會被靜默拒絕（不報錯但值不變）。透過全域 `plc_instance` 讓讀寫共用同一條連線解決。

### 2. pymcprotocol 封包格式逆向

GX Simulator3 網路層在模擬模式下不開放 TCP，無法用官方工具測試。自行實作 SLMP 3E Binary 封包解析（Python Mock Server），透過 hex dump 逐 byte 分析確認正確的 offset 位置。

### 3. 本地 LLM + TimescaleDB 趨勢整合

Ollama 診斷不只看當下數值，還查詢 TimescaleDB 最近 5 分鐘的 MIN/MAX/AVG/RANGE，讓診斷結果更準確，同時資料完全不外傳。

### 4. 非同步架構中的同步 SLMP

`pymcprotocol` 是同步套件，在 FastAPI asyncio 環境中直接呼叫會阻塞事件迴圈。透過 `run_in_executor` 包裝到執行緒池執行。

---

## 履歷技能關鍵字

`SLMP` `MC Protocol` `IIoT` `SCADA` `FastAPI` `WebSocket` `asyncio` `React` `TypeScript` `TimescaleDB` `PostgreSQL` `Docker` `Scikit-Learn` `Isolation Forest` `Ollama` `LLM` `Tailscale` `Mitsubishi FX5U` `Python` `時序資料庫` `異常偵測` `邊緣運算`

---

## License

MIT