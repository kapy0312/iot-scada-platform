from fastapi import APIRouter
from pydantic import BaseModel, field_validator

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

@router.post("/api/control/write")
async def write_to_plc(cmd: ControlCommand):
    print(f"[CONTROL] {cmd.tag} = {cmd.value}")
    return {"status": "ok", "tag": cmd.tag, "value": cmd.value}