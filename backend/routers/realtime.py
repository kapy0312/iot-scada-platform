from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.ws_manager import manager

router = APIRouter()

@router.websocket("/ws/realtime")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)