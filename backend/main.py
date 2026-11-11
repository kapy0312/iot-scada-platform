import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.realtime import router as ws_router
from routers.control import router as ctrl_router
from routers.history import router as history_router
from routers.rag import router as rag_router
from routers.system import router as system_router
from routers.device import router as device_router
from routers.auth import router as auth_router
from routers.oee import router as oee_router
from core.config import get_database_url, masked
from core.plc_simulator import start_polling, stop_polling, detector
from db.pool import close_pool
from ml.auto_trainer import auto_retrain_loop
from sync.cloud_sync import syncer

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 設定缺漏直接讓啟動失敗並說明原因；資料庫沒開則不影響啟動（連線池第一次用到才建立）
    print(f"[DB] 連線目標：{masked(get_database_url())}")
    start_polling()
    task2 = asyncio.create_task(auto_retrain_loop(detector))
    syncer.start()             # .env 沒設 CLOUD_SYNC_ENABLED=true 時只印一行就略過
    yield
    stop_polling()
    task2.cancel()
    await syncer.stop()
    await asyncio.sleep(0.5)    # 讓最後幾筆背景寫入有機會完成
    await close_pool()

app = FastAPI(lifespan=lifespan)

# 允許哪些前端網址呼叫 API：用 Tailscale IP 開前端時，要把那個網址加進 .env 的 CORS_ORIGINS（逗號分隔）
# 登入 cookie 要跨 port 傳送，所以 allow_credentials 必須開，而且來源不能用 "*"
CORS_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    if o.strip() and o.strip() != "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],   # PUT：OEE 品號、班別設定
    allow_headers=["Content-Type", "X-SCADA-Request"],
)

app.include_router(auth_router)
app.include_router(ws_router)
app.include_router(ctrl_router)
app.include_router(history_router)
app.include_router(rag_router)
app.include_router(system_router)
app.include_router(device_router)
app.include_router(oee_router)

@app.get("/health")
async def health():
    return {"status": "ok"}