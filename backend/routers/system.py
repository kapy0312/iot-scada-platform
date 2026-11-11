from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
import core.plc_simulator as plc
from core.auth import client_ip, require_operator
from db.audit import write_audit
from sync import cloud_sync

router = APIRouter()


class DataSourceCommand(BaseModel):
    source: str  # "opcua" 或 "mock"


class AnomalySimRequest(BaseModel):
    duration_seconds: float = 20.0


@router.get("/api/system/data-source")
async def get_data_source():
    return {"source": plc.current_source, "device_id": plc.get_active_device_id()}


@router.post("/api/system/data-source")
async def set_data_source(cmd: DataSourceCommand, request: Request, user: dict = Depends(require_operator)):
    before = {"source": plc.current_source}
    try:
        await plc.switch_source(cmd.source)
    except ValueError as e:
        await write_audit("system.data_source", "error", username=user["username"], client_ip=client_ip(request),
                          params={"source": cmd.source}, before=before, message=str(e))
        return {"status": "error", "message": str(e)}
    await write_audit("system.data_source", "ok", username=user["username"], client_ip=client_ip(request),
                      params={"source": cmd.source}, before=before)
    return {"status": "ok", "source": plc.current_source, "device_id": plc.get_active_device_id()}


@router.post("/api/system/simulate-anomaly")
async def simulate_anomaly(cmd: AnomalySimRequest, request: Request, user: dict = Depends(require_operator)):
    params = {"duration_seconds": cmd.duration_seconds}
    try:
        plc.trigger_mock_anomaly(cmd.duration_seconds)
    except ValueError as e:
        await write_audit("system.simulate_anomaly", "error", username=user["username"],
                          client_ip=client_ip(request), params=params, message=str(e))
        return {"status": "error", "message": str(e)}
    await write_audit("system.simulate_anomaly", "ok", username=user["username"],
                      client_ip=client_ip(request), params=params)
    return {"status": "ok", "duration_seconds": cmd.duration_seconds}


@router.get("/api/system/model-info")
async def model_info():
    """目前推論用的模型：檔名、用哪個資料來源訓練、包含哪些訊號"""
    return {"loaded": plc.detector.pipeline is not None, **plc.detector.info}


@router.post("/api/system/reload-model")
async def reload_model(request: Request, user: dict = Depends(require_operator)):
    """手動訓練（python -m ml.train）後呼叫，立即換用最新模型，不必重啟後端"""
    before = {"file": plc.detector.info.get("file")}
    plc.detector.reload_model()
    await write_audit("system.reload_model", "ok", username=user["username"], client_ip=client_ip(request),
                      before=before, params={"file": plc.detector.info.get("file")})
    return {"status": "ok", "loaded": plc.detector.pipeline is not None, **plc.detector.info}


@router.get("/api/system/sync-status")
async def sync_status():
    """雲端同步狀態：是否啟用、最後成功時間、各目標同步到哪裡、落後幾秒"""
    return await cloud_sync.status()
