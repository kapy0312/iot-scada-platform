"""
操作紀錄：誰、從哪裡、做了什麼、改之前是多少、結果如何

寫入失敗只印錯誤、不拋例外：操作已經執行了，不能因為紀錄寫不進去就回報失敗，
否則畫面顯示「失敗」但 PLC 其實已經動作，反而更危險
"""
import json

from db.pool import acquire


def _json(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


async def write_audit(
    action: str,
    result: str,
    *,
    username: str | None = None,
    client_ip: str | None = None,
    params: dict | None = None,
    before: dict | None = None,
    message: str | None = None,
) -> None:
    try:
        async with acquire() as conn:
            await conn.execute("""
                INSERT INTO audit_log (username, client_ip, action, params, before, result, message)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7)
            """, username, client_ip, action, _json(params), _json(before), result, message)
    except Exception as e:
        print(f"[AUDIT] 寫入操作紀錄失敗（{action} {result}）：{type(e).__name__}: {e}")
