import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.realtime import router as ws_router
from routers.control import router as ctrl_router
from routers.history import router as history_router
from routers.rag import router as rag_router
from routers.system import router as system_router
from routers.device import router as device_router
from core.config import get_database_url, masked
from core.plc_simulator import start_polling, stop_polling, detector
from db.pool import close_pool
from ml.auto_trainer import auto_retrain_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 設定缺漏直接讓啟動失敗並說明原因；資料庫沒開則不影響啟動（連線池第一次用到才建立）
    print(f"[DB] 連線目標：{masked(get_database_url())}")
    start_polling()
    task2 = asyncio.create_task(auto_retrain_loop(detector))
    yield
    stop_polling()
    task2.cancel()
    await asyncio.sleep(0.5)    # 讓最後幾筆背景寫入有機會完成
    await close_pool()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws_router)
app.include_router(ctrl_router)
app.include_router(history_router)
app.include_router(rag_router)
app.include_router(system_router)
app.include_router(device_router)

@app.get("/health")
async def health():
    return {"status": "ok"}