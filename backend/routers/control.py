from fastapi import APIRouter
from pydantic import BaseModel, field_validator
import asyncio
import pymcprotocol

PLC_IP   = "192.168.0.20"
PLC_PORT = 5011

router = APIRouter()

class ControlCommand(BaseModel):
    tag: str
    value: float

    @field_validator("value")
    @classmethod
    def check_range(cls, v, info):
        limits = {
            "motor_speed_setpoint": (0, 1800),
            "motor_enable": (0, 1),
        }
        tag = info.data.get("tag", "")
        if tag in limits:
            lo, hi = limits[tag]
            if not (lo <= v <= hi):
                raise ValueError(f"{tag} 值超出安全範圍 {lo}~{hi}")
        return v

def _write_plc_sync(tag: str, value: float):
    from core.plc_simulator import plc_instance
    if plc_instance is None:
        raise Exception("PLC 未連線")
    if tag == "motor_enable":
        plc_instance.batchwrite_bitunits(headdevice="M100", values=[int(value)])
        print(f"[CONTROL] M100 = {int(value)}")
    elif tag == "motor_speed_setpoint":
        plc_instance.batchwrite_wordunits(headdevice="D110", values=[int(value)])
        print(f"[CONTROL] D110 = {int(value)}")

@router.post("/api/control/write")
async def write_to_plc(cmd: ControlCommand):
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, _write_plc_sync, cmd.tag, cmd.value
        )
        return {"status": "ok", "tag": cmd.tag, "value": cmd.value}
    except Exception as e:
        print(f"[CONTROL] 寫入失敗：{e}")
        return {"status": "error", "message": str(e)}