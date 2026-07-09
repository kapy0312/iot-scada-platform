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
    return {"source": plc.current_source}


@router.post("/api/system/data-source")
async def set_data_source(cmd: DataSourceCommand):
    try:
        await plc.switch_source(cmd.source)
        return {"status": "ok", "source": plc.current_source}
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    
@router.post("/api/system/simulate-anomaly")
async def simulate_anomaly(cmd: AnomalySimRequest):
    try:
        plc.trigger_mock_anomaly(cmd.duration_seconds)
        return {"status": "ok", "duration_seconds": cmd.duration_seconds}
    except ValueError as e:
        return {"status": "error", "message": str(e)}