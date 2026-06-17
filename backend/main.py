import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.realtime import router as ws_router
from routers.control import router as ctrl_router
from routers.history import router as history_router
from core.plc_simulator import poll_plc_forever

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_plc_forever())
    yield
    task.cancel()

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

@app.get("/health")
async def health():
    return {"status": "ok"}