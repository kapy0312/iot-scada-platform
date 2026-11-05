from fastapi import APIRouter

from core.device_profile import ANOMALY_THRESHOLD, base_device_id, get_profile

router = APIRouter()


@router.get("/api/device-profile/{device_id}")
async def device_profile(device_id: str):
    """前端畫類比指示條、判斷告警用；與 AI 診斷、MCP 共用同一份規格"""
    return {
        "device_id": base_device_id(device_id),
        "profile": get_profile(device_id),
        "anomaly_threshold": ANOMALY_THRESHOLD,
    }
