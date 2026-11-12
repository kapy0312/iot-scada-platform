"""
訊號門檻覆寫：在畫面上改正常範圍、警戒值、危險值、刻度（需要 migration 008）

做法：直接改 DEVICE_PROFILES 裡那份 dict 的數值。所有地方都是透過 get_profile() 拿同一份 dict，
所以告警、趨勢預警、恢復期判斷、AI 診斷、前端都會跟著變，呼叫端不用改。
- 預設值在 import 時另外存一份；載入覆寫時先全部回到預設，再套上資料庫的值
- 表裡沒有的訊號＝用預設值；表不存在（還沒跑 008）也照常運作，只是不能改
- version：目前門檻的指紋，前端看到它變了就重新載入規格（其他開著的網頁也會跟上）
- 後端啟動後每 60 秒重新載入一次（有人直接改資料庫、或另一個程式改過時也會跟上）
"""
import asyncio
import copy
import hashlib
import json
import math
import sys

import asyncpg

from core.device_profile import DEVICE_PROFILES, base_device_id
from db.pool import acquire

RELOAD_SECONDS = 60
_DEFAULTS = {
    model: {tag: {"normal": list(s["normal"]), "warning": s["warning"], "danger": s["danger"], "scale": list(s["scale"])}
            for tag, s in p["tags"].items()}
    for model, p in DEVICE_PROFILES.items()
}
_meta: dict[tuple[str, str], dict] = {}     # (型號, 訊號) → {"updated_by", "updated_at"}
_warned_missing = False
version = ""


class LimitsError(ValueError):
    """門檻不合法（訊息直接給畫面顯示）"""


class LimitsUnavailable(RuntimeError):
    """還沒跑 migration 008"""


def _fingerprint() -> str:
    cur = {m: {t: {k: s[k] for k in ("normal", "warning", "danger", "scale")} for t, s in p["tags"].items()}
           for m, p in DEVICE_PROFILES.items()}
    return hashlib.sha1(json.dumps(cur, sort_keys=True).encode()).hexdigest()[:12]


version = _fingerprint()


def defaults(model: str) -> dict:
    return copy.deepcopy(_DEFAULTS.get(base_device_id(model), {}))


def meta(model: str, tag: str) -> dict | None:
    return _meta.get((base_device_id(model), tag))


def validate(limits: dict) -> dict:
    """檢查並整理成 {"normal": [lo, hi], "warning", "danger", "scale": [lo, hi]}；不合法丟 LimitsError"""
    try:
        n_lo, n_hi = (float(v) for v in limits["normal"])
        s_lo, s_hi = (float(v) for v in limits["scale"])
        warn, dang = float(limits["warning"]), float(limits["danger"])
    except (KeyError, TypeError, ValueError):
        raise LimitsError("欄位不完整：需要正常範圍（下限、上限）、警戒值、危險值、刻度（最小、最大）")
    nums = (n_lo, n_hi, warn, dang, s_lo, s_hi)
    if not all(math.isfinite(v) for v in nums):
        raise LimitsError("數值不能是空白或無限大")
    rules = [
        (s_lo <= n_lo, "正常下限不能小於刻度最小值"),
        (n_lo < n_hi, "正常下限要小於正常上限"),
        (n_hi < warn, "警戒值要大於正常上限"),
        (warn < dang, "危險值要大於警戒值"),
        (dang <= s_hi, "刻度最大值不能小於危險值（指示條會畫不到）"),
    ]
    for ok, msg in rules:
        if not ok:
            raise LimitsError(msg)
    return {"normal": [n_lo, n_hi], "warning": warn, "danger": dang, "scale": [s_lo, s_hi]}


def apply_rows(rows: list) -> bool:
    """全部回到預設，再套上資料庫的覆寫；門檻有變回傳 True"""
    global version
    for model, tags in _DEFAULTS.items():
        for tag, d in tags.items():
            spec = DEVICE_PROFILES[model]["tags"][tag]
            spec["normal"], spec["warning"], spec["danger"], spec["scale"] = list(d["normal"]), d["warning"], d["danger"], list(d["scale"])
    _meta.clear()
    for r in rows:
        spec = DEVICE_PROFILES.get(r["device_model"], {}).get("tags", {}).get(r["tag_name"])
        if spec is None:
            continue    # 程式裡已經沒有這個訊號：忽略舊的覆寫
        spec["normal"] = [r["normal_lo"], r["normal_hi"]]
        spec["warning"], spec["danger"] = r["warning"], r["danger"]
        spec["scale"] = [r["scale_lo"], r["scale_hi"]]
        _meta[(r["device_model"], r["tag_name"])] = {
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
    new = _fingerprint()
    changed = new != version
    version = new
    return changed


async def load(conn=None) -> bool:
    """從資料庫載入覆寫；表不存在時用預設值（只提醒一次）"""
    global _warned_missing
    try:
        if conn is None:
            async with acquire() as c:
                rows = await c.fetch("SELECT * FROM tag_limits")
        else:
            rows = await conn.fetch("SELECT * FROM tag_limits")
    except asyncpg.UndefinedTableError:
        if not _warned_missing:
            # 印到 stderr：MCP server 用 stdout 跟 Claude Desktop 溝通，印到 stdout 會弄壞協定
            print("[LIMITS] tag_limits 表不存在，門檻用程式預設值（要在畫面上修改請先執行 db/migrations/008_tag_limits.sql）", file=sys.stderr)
            _warned_missing = True
        return apply_rows([])
    changed = apply_rows(rows)
    if changed:
        print(f"[LIMITS] 門檻已載入（{len(rows)} 個訊號有覆寫，版本 {version}）", file=sys.stderr)
    return changed


async def reload_loop():
    while True:
        try:
            await load()
        except Exception as e:
            print(f"[LIMITS] 載入門檻失敗（沿用目前的值）：{type(e).__name__}: {e}", file=sys.stderr)
        await asyncio.sleep(RELOAD_SECONDS)


def _check_tag(model: str, tag: str) -> str:
    model = base_device_id(model)
    if tag not in DEVICE_PROFILES.get(model, {}).get("tags", {}):
        raise LimitsError(f"{model} 沒有訊號 {tag}")
    return model


def current(model: str, tag: str) -> dict:
    s = DEVICE_PROFILES[base_device_id(model)]["tags"][tag]
    return {"normal": list(s["normal"]), "warning": s["warning"], "danger": s["danger"], "scale": list(s["scale"])}


async def save(model: str, tag: str, limits: dict, username: str) -> tuple[dict, dict]:
    """寫入覆寫並立刻套用；回傳（修改前, 修改後）"""
    model = _check_tag(model, tag)
    new = validate(limits)
    before = current(model, tag)
    try:
        async with acquire() as conn:
            await conn.execute("""
                INSERT INTO tag_limits (device_model, tag_name, normal_lo, normal_hi, warning, danger,
                                        scale_lo, scale_hi, updated_at, updated_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), $9)
                ON CONFLICT (device_model, tag_name) DO UPDATE SET
                    normal_lo = EXCLUDED.normal_lo, normal_hi = EXCLUDED.normal_hi,
                    warning = EXCLUDED.warning, danger = EXCLUDED.danger,
                    scale_lo = EXCLUDED.scale_lo, scale_hi = EXCLUDED.scale_hi,
                    updated_at = now(), updated_by = EXCLUDED.updated_by
            """, model, tag, *new["normal"], new["warning"], new["danger"], *new["scale"], username)
            await load(conn)
    except asyncpg.UndefinedTableError:
        raise LimitsUnavailable("還沒建立門檻設定表：請先執行 db/migrations/008_tag_limits.sql")
    return before, current(model, tag)


async def reset(model: str, tag: str) -> tuple[dict, dict]:
    """刪掉覆寫，回到程式預設值；回傳（修改前, 修改後）"""
    model = _check_tag(model, tag)
    before = current(model, tag)
    try:
        async with acquire() as conn:
            await conn.execute("DELETE FROM tag_limits WHERE device_model = $1 AND tag_name = $2", model, tag)
            await load(conn)
    except asyncpg.UndefinedTableError:
        raise LimitsUnavailable("還沒建立門檻設定表：請先執行 db/migrations/008_tag_limits.sql")
    return before, current(model, tag)
