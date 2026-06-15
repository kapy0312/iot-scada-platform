from fastapi import APIRouter
from pydantic import BaseModel, field_validator
import asyncio
from asyncua import Client
from asyncua.ua import DataValue, Variant, VariantType

PLC_URL = "opc.tcp://192.168.0.10:4840"
NS      = 3

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

async def _write_opcua(tag: str, value: float):
    async with Client(url=PLC_URL) as client:
        db1_path = [
            f"0:Objects", f"{NS}:PLC_1",
            f"{NS}:DataBlocksGlobal", f"{NS}:DB1"
        ]

        if tag == "motor_enable":
            node = await client.nodes.root.get_child(
                db1_path + [f"{NS}:motor_enable"]
            )
            await node.write_value(
                DataValue(Variant(bool(value), VariantType.Boolean))
            )
            print(f"[CONTROL] motor_enable = {bool(value)}")

        elif tag == "motor_speed_setpoint":
            node = await client.nodes.root.get_child(
                db1_path + [f"{NS}:speed_setpoint"]
            )
            await node.write_value(
                DataValue(Variant(float(value), VariantType.Float))
            )
            print(f"[CONTROL] speed_setpoint = {float(value)}")

@router.post("/api/control/write")
async def write_to_plc(cmd: ControlCommand):
    try:
        await _write_opcua(cmd.tag, cmd.value)
        return {"status": "ok", "tag": cmd.tag, "value": cmd.value}
    except Exception as e:
        print(f"[CONTROL] 寫入失敗：{e}")
        return {"status": "error", "message": str(e)}