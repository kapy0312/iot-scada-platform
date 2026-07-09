import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.realtime import router as ws_router
from routers.control import router as ctrl_router
from routers.history import router as history_router
from routers.rag import router as rag_router
from routers.system import router as system_router
from core.plc_simulator import start_polling, stop_polling, detector
from ml.auto_trainer import auto_retrain_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_polling()
    task2 = asyncio.create_task(auto_retrain_loop(detector))
    yield
    stop_polling()
    task2.cancel()

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

@app.get("/health")
async def health():
    return {"status": "ok"}