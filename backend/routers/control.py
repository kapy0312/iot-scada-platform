import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from asyncua import Client
from asyncua.ua import DataValue, Variant, VariantType

from core.auth import client_ip, require_operator
from db.audit import write_audit

PLC_URL = os.getenv("PLC_URL", "opc.tcp://192.168.0.10:4840")
NS      = 3

router = APIRouter()

# 可寫入的訊號：API 名稱 → (DB1 裡的變數名稱, OPC UA 型別, 安全範圍)
WRITABLE = {
    "motor_enable":         ("motor_enable",   VariantType.Boolean, (0, 1)),
    "motor_speed_setpoint": ("speed_setpoint", VariantType.Float,   (0, 1800)),
}


class ControlCommand(BaseModel):
    tag: str
    value: float

    @field_validator("tag")
    @classmethod
    def check_tag(cls, v):
        if v not in WRITABLE:
            raise ValueError(f"不允許寫入的訊號：{v}（只能寫 {', '.join(WRITABLE)}）")
        return v

    @field_validator("value")
    @classmethod
    def check_range(cls, v, info):
        tag = info.data.get("tag", "")
        if tag in WRITABLE:
            lo, hi = WRITABLE[tag][2]
            if not (lo <= v <= hi):
                raise ValueError(f"{tag} 值超出安全範圍 {lo}~{hi}")
        return v


async def _write_opcua(tag: str, value: float) -> dict:
    """先讀目前值（留給操作紀錄），再寫入；回傳 {"value": 寫入前的值}，讀不到時為 None"""
    node_name, vtype, _ = WRITABLE[tag]
    async with Client(url=PLC_URL) as client:
        node = await client.nodes.root.get_child([
            "0:Objects", f"{NS}:PLC_1", f"{NS}:DataBlocksGlobal", f"{NS}:DB1", f"{NS}:{node_name}",
        ])
        try:
            before = await node.read_value()
        except Exception:
            before = None
        new = bool(value) if vtype == VariantType.Boolean else float(value)
        await node.write_value(DataValue(Variant(new, vtype)))
        print(f"[CONTROL] {node_name}: {before} → {new}")
        return {"value": before}


@router.post("/api/control/write")
async def write_to_plc(cmd: ControlCommand, request: Request, user: dict = Depends(require_operator)):
    params = {"tag": cmd.tag, "value": cmd.value}
    ip = client_ip(request)
    try:
        before = await _write_opcua(cmd.tag, cmd.value)
    except Exception as e:
        print(f"[CONTROL] 寫入失敗：{e}")
        await write_audit("control.write", "error", username=user["username"], client_ip=ip,
                          params=params, message=str(e))
        # 502：後端正常、但下游的 PLC 沒寫成功；以前回 200 會讓前端誤顯示「已寫入」
        return JSONResponse(status_code=502, content={"status": "error", "message": f"寫入 PLC 失敗：{e}"})
    await write_audit("control.write", "ok", username=user["username"], client_ip=ip,
                      params=params, before=before)
    return {"status": "ok", "tag": cmd.tag, "value": cmd.value}
