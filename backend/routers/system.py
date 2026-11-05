from fastapi import APIRouter
from pydantic import BaseModel
import core.plc_simulator as plc

router = APIRouter()


class DataSourceCommand(BaseModel):
    source: str  # "opcua" 或 "mock"


class AnomalySimRequest(BaseModel):
    duration_seconds: float = 20.0


@router.get("/api/system/data-source")
async def get_data_source():
    return {"source": plc.current_source, "device_id": plc.get_active_device_id()}


@router.post("/api/system/data-source")
async def set_data_source(cmd: DataSourceCommand):
    try:
        await plc.switch_source(cmd.source)
        return {"status": "ok", "source": plc.current_source, "device_id": plc.get_active_device_id()}
    except ValueError as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/system/simulate-anomaly")
async def simulate_anomaly(cmd: AnomalySimRequest):
    try:
        plc.trigger_mock_anomaly(cmd.duration_seconds)
        return {"status": "ok", "duration_seconds": cmd.duration_seconds}
    except ValueError as e:
        return {"status": "error", "message": str(e)}


@router.get("/api/system/model-info")
async def model_info():
    """目前推論用的模型：檔名、用哪個資料來源訓練、包含哪些訊號"""
    return {"loaded": plc.detector.pipeline is not None, **plc.detector.info}


@router.post("/api/system/reload-model")
async def reload_model():
    """手動訓練（python -m ml.train）後呼叫，立即換用最新模型，不必重啟後端"""
    plc.detector.reload_model()
    return {"status": "ok", "loaded": plc.detector.pipeline is not None, **plc.detector.info}
