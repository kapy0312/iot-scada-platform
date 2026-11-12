from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core import limits
from core.auth import client_ip, require_operator
from core.device_profile import ANOMALY_THRESHOLD, base_device_id, get_profile
from db.audit import write_audit

router = APIRouter()


@router.get("/api/device-profile/{device_id}")
async def device_profile(device_id: str):
    """前端畫類比指示條、判斷告警用；與 AI 診斷、MCP 共用同一份規格（含畫面上改過的門檻）"""
    profile = get_profile(device_id)
    model = base_device_id(device_id)
    return {
        "device_id": model,
        "profile": profile,
        "anomaly_threshold": ANOMALY_THRESHOLD,
        "limits_version": limits.version,
        # 每個訊號的預設值與最後修改紀錄（給「恢復預設值」與「上次修改」顯示）
        "limits": {
            tag: {"default": limits.defaults(model).get(tag), "modified": limits.meta(model, tag)}
            for tag in (profile or {}).get("tags", {})
        },
    }


class LimitsIn(BaseModel):
    normal: list[float]
    warning: float
    danger: float
    scale: list[float]


@router.put("/api/device-profile/{device_id}/limits/{tag}")
async def update_limits(device_id: str, tag: str, body: LimitsIn, request: Request,
                        user: dict = Depends(require_operator)):
    """修改一個訊號的門檻（需登入）；前後值寫進操作紀錄"""
    params = {"device_id": base_device_id(device_id), "tag": tag, "after": body.model_dump()}
    try:
        before, after = await limits.save(device_id, tag, body.model_dump(), user["username"])
    except limits.LimitsError as e:
        await write_audit("limits.update", "error", username=user["username"], client_ip=client_ip(request),
                          params=params, message=str(e))
        raise HTTPException(400, str(e))
    except limits.LimitsUnavailable as e:
        raise HTTPException(503, str(e))
    await write_audit("limits.update", "ok", username=user["username"], client_ip=client_ip(request),
                      params={**params, "before": before, "after": after})
    return {"status": "ok", "tag": tag, "limits": after, "limits_version": limits.version}


@router.delete("/api/device-profile/{device_id}/limits/{tag}")
async def reset_limits(device_id: str, tag: str, request: Request, user: dict = Depends(require_operator)):
    """恢復程式預設值（需登入）"""
    params = {"device_id": base_device_id(device_id), "tag": tag}
    try:
        before, after = await limits.reset(device_id, tag)
    except limits.LimitsError as e:
        raise HTTPException(400, str(e))
    except limits.LimitsUnavailable as e:
        raise HTTPException(503, str(e))
    await write_audit("limits.reset", "ok", username=user["username"], client_ip=client_ip(request),
                      params={**params, "before": before, "after": after})
    return {"status": "ok", "tag": tag, "limits": after, "limits_version": limits.version}
