"""
手冊向量庫的統一入口（Phase 2 過渡期：Qdrant 與 pgvector 並存）

- 查詢（search、list_documents、file_exists）：只查 RAG_BACKEND 指定的那一邊（預設 qdrant）
- 寫入（上傳、刪除、勾選）：兩邊都寫。指定的那一邊（主要）失敗就報錯；
  另一邊（次要）失敗只印警告、不擋操作，之後用 python -m tools.rag_migrate 補齊
- 回傳的搜尋結果都有 .payload、.score，呼叫端不用管是哪一邊

像 PLC 的雙通道：主通道出錯要停下來，備援通道出錯先記下來，不影響生產。
"""
import uuid

from core.config import rag_backend
from rag import pgvector_store
from rag import qdrant_client as qdrant_store
from rag.pgvector_store import SearchHit

_STORES = {"qdrant": qdrant_store, "pgvector": pgvector_store}


def backend_name() -> str:
    return rag_backend()


def _primary():
    return _STORES[rag_backend()]


def _secondary_name() -> str:
    return "pgvector" if rag_backend() == "qdrant" else "qdrant"


async def _both(op: str, *args) -> None:
    """主要的那一邊先做（失敗就往外丟），再做次要的那一邊（失敗只警告）"""
    await getattr(_primary(), op)(*args)
    name = _secondary_name()
    try:
        await getattr(_STORES[name], op)(*args)
    except Exception as e:  # 例如還沒跑 migration 009、Qdrant 容器沒開
        fix = ("之後可用 python -m tools.rag_migrate 補齊" if name == "pgvector"
               else "Qdrant 之後會拿掉，不需要補")
        print(f"[RAG] 次要向量庫 {name} 的 {op} 失敗（不影響操作，{fix}）：{type(e).__name__}: {e}")


# ---------- 查詢 ----------

async def search(query_vector: list[float], device_id: str | None = None, top_k: int = 3) -> list[SearchHit]:
    hits = await _primary().search(query_vector, device_id=device_id, top_k=top_k)
    # Qdrant 回傳 ScoredPoint，包成一樣的 SearchHit
    return [h if isinstance(h, SearchHit) else SearchHit(payload=h.payload or {}, score=h.score or 0.0, id=str(h.id))
            for h in hits]


async def list_documents() -> list[dict]:
    return await _primary().list_documents()


async def file_exists(file_hash: str) -> bool:
    return await _primary().file_exists(file_hash)


# ---------- 寫入 ----------

def build_chunks(doc: dict, chunks: list[dict], vectors: list[list[float]]) -> list[dict]:
    """每段配上向量與 id；兩邊用同一個 id，方便對照"""
    return [
        {"id": str(uuid.uuid4()), "chunk_index": i, "section": c.get("section"), "page": c.get("page"),
         "text": c["text"], "vector": v}
        for i, (c, v) in enumerate(zip(chunks, vectors))
    ]


async def add_document(doc: dict, chunks: list[dict]) -> None:
    await _both("add_document", doc, chunks)


async def delete_document(file_hash: str) -> None:
    await _both("delete_document", file_hash)


async def set_document_enabled(file_hash: str, enabled: bool) -> None:
    await _both("set_document_enabled", file_hash, enabled)
