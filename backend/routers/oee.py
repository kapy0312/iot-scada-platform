"""
OEE API（需要先執行 005、006）
  讀取（不用登入，跟其他讀取 API 一致）：
    GET /api/oee/current     目前狀態＋本班 OEE（含每小時）
    GET /api/oee/shifts      最近幾天各班 OEE
    GET /api/oee/timeline    狀態時間軸（預設本班）
    GET /api/oee/config      品號標準週期、班別設定
  修改（要登入、帶 X-SCADA-Request header，寫操作紀錄）：
    PUT /api/oee/products    新增或修改品號標準週期
    PUT /api/oee/shifts      設定一套班別（從 effective_from 那天起生效）
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

import core.plc_simulator as plc
from core.auth import client_ip, require_operator
from core.oee import SHIFT_TZ, Shift, is_effective, locate_shift, shift_summary, shift_window, validate_shifts
from db import oee_repo
from db.audit import write_audit
from db.pool import acquire

router = APIRouter()

MAX_TIMELINE_HOURS = 24


def _device(device_id: str | None) -> str:
    return device_id or plc.get_active_device_id()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _locate(versions, at: datetime):
    return locate_shift(lambda day: oee_repo.shifts_for_day(versions, day), at)


async def _run(fn):
    """統一處理：006 沒執行時回 503 並說明"""
    try:
        async with acquire() as conn:
            return await fn(conn)
    except oee_repo.OeeNotReady as e:
        raise HTTPException(503, str(e))


# ---------- 讀取 ----------

@router.get("/api/oee/current")
async def oee_current(device_id: str | None = None):
    dev = _device(device_id)
    now = _now()

    async def work(conn):
        state = await oee_repo.current_state(conn, dev)
        versions = await oee_repo.load_shift_versions(conn)
        cycles = await oee_repo.load_cycles(conn)
        found = _locate(versions, now)
        shift = None                 # 現在不在任何班別（例如只設了早班、現在是晚上）
        if found:
            s, day = found
            start, end = shift_window(s, day)
            rows = await oee_repo.load_hourly(conn, dev, start, end)
            shift = shift_summary(s, day, rows, cycles, now=now, hourly=True)
        if state and state["since"]:
            state["duration_s"] = round((now - state["since"]).total_seconds())
        return {"device_id": dev, "now": now, "state": state, "shift": shift}

    return await _run(work)


@router.get("/api/oee/shifts")
async def oee_shifts(device_id: str | None = None, days: int = Query(7, ge=1, le=31)):
    """最近 days 天的各班 OEE，新的在前；還沒開始的班不列"""
    dev = _device(device_id)
    now = _now()

    async def work(conn):
        versions = await oee_repo.load_shift_versions(conn)
        cycles = await oee_repo.load_cycles(conn)
        today = now.astimezone(SHIFT_TZ).date()
        pick = lambda d: oee_repo.shifts_for_day(versions, d)  # noqa: E731
        plan = []
        for i in range(days):
            day = today - timedelta(days=i)
            for s in oee_repo.shifts_for_day(versions, day):
                start, _ = shift_window(s, day)
                if start <= now and is_effective(pick, s, day):
                    plan.append((s, day))
        if not plan:
            return {"device_id": dev, "shifts": []}
        first = min(shift_window(s, d)[0] for s, d in plan)
        rows = await oee_repo.load_hourly(conn, dev, first, now + timedelta(hours=1))
        result = [shift_summary(s, d, rows, cycles, now=now) for s, d in plan]
        result.sort(key=lambda r: r["start"], reverse=True)
        return {"device_id": dev, "shifts": result}

    return await _run(work)


@router.get("/api/oee/timeline")
async def oee_timeline(device_id: str | None = None, start: datetime | None = None, end: datetime | None = None):
    """狀態時間軸；沒給時間就用本班（不在任何班別時用最近 12 小時），最長 24 小時"""
    dev = _device(device_id)
    now = _now()

    async def work(conn):
        s, e = start, end
        if s is None or e is None:
            versions = await oee_repo.load_shift_versions(conn)
            found = _locate(versions, now)
            if found:
                s, e = shift_window(*found)
            else:
                s, e = now - timedelta(hours=12), now
        for t in (s, e):
            if t.tzinfo is None:
                raise HTTPException(422, "start、end 要帶時區，例如 2026-11-11T08:00:00+08:00")
        if e <= s:
            raise HTTPException(422, "end 要晚於 start")
        if e - s > timedelta(hours=MAX_TIMELINE_HOURS):
            raise HTTPException(422, f"時間範圍最多 {MAX_TIMELINE_HOURS} 小時")
        segments = await oee_repo.timeline(conn, dev, s, min(e, now))
        return {"device_id": dev, "start": s, "end": e, "segments": segments}

    return await _run(work)


@router.get("/api/oee/config")
async def oee_config():
    async def work(conn):
        versions = await oee_repo.load_shift_versions(conn)
        today = _now().astimezone(SHIFT_TZ).date()
        current = None
        upcoming = []
        for eff, shifts in versions:
            item = {"effective_from": eff, "shifts": [s.__dict__ for s in shifts]}
            if eff <= today:
                current = item
            else:
                upcoming.append(item)
        return {
            "timezone": str(SHIFT_TZ),
            "products": await oee_repo.load_products(conn),
            "shifts": current,            # 今天適用的那一套
            "upcoming_shifts": upcoming,  # 已設定、還沒生效的
        }

    return await _run(work)


# ---------- 修改 ----------

class ProductIn(BaseModel):
    product_id: int = Field(gt=0, le=32767)          # PLC 的 product_id 是 Int
    name: str = Field(min_length=1, max_length=50)
    ideal_cycle_s: float = Field(gt=0, le=86400)


class ShiftIn(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)


class ShiftSetIn(BaseModel):
    effective_from: date | None = None   # 沒給就是今天（台北時間）
    shifts: list[ShiftIn] = Field(min_length=1, max_length=6)


@router.put("/api/oee/products")
async def put_products(body: list[ProductIn], request: Request, user: dict = Depends(require_operator)):
    if not body:
        raise HTTPException(422, "至少要一個品號")
    ids = [p.product_id for p in body]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "品號不能重複")
    params = {"products": [p.model_dump() for p in body]}

    async def work(conn):
        before = [p for p in await oee_repo.load_products(conn) if p["product_id"] in ids]
        try:
            await oee_repo.save_products(conn, params["products"], user["username"])
        except Exception as e:
            await write_audit("oee.products", "error", username=user["username"], client_ip=client_ip(request),
                              params=params, before={"products": before}, message=str(e))
            raise
        await write_audit("oee.products", "ok", username=user["username"], client_ip=client_ip(request),
                          params=params, before={"products": before})
        return {"status": "ok", "products": await oee_repo.load_products(conn)}

    return await _run(work)


@router.put("/api/oee/shifts")
async def put_shifts(body: ShiftSetIn, request: Request, user: dict = Depends(require_operator)):
    shifts = [Shift(s.name.strip(), s.start_hour, s.end_hour) for s in body.shifts]
    err = validate_shifts(shifts)
    if err:
        raise HTTPException(422, err)
    eff = body.effective_from or _now().astimezone(SHIFT_TZ).date()
    params = {"effective_from": eff.isoformat(), "shifts": [s.__dict__ for s in shifts]}

    async def work(conn):
        versions = await oee_repo.load_shift_versions(conn)
        before = oee_repo.shifts_for_day(versions, eff)
        before_info = {"shifts": [s.__dict__ for s in before]}
        try:
            await oee_repo.save_shifts(conn, eff, shifts, user["username"])
        except Exception as e:
            await write_audit("oee.shifts", "error", username=user["username"], client_ip=client_ip(request),
                              params=params, before=before_info, message=str(e))
            raise
        await write_audit("oee.shifts", "ok", username=user["username"], client_ip=client_ip(request),
                          params=params, before=before_info)
        return {"status": "ok", "effective_from": eff, "shifts": params["shifts"]}

    return await _run(work)
