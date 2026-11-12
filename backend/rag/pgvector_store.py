"""
手冊向量存放在 PostgreSQL（pgvector），表結構見 db/migrations/009_manual_vectors.sql

對外的函式跟 rag/qdrant_client.py 一樣，由 rag/store.py 依 RAG_BACKEND 決定用哪一邊。
搜尋結果統一包成 SearchHit（.payload、.score），payload 欄位名稱沿用 Qdrant 的寫法，
所以 AI 診斷選章節（ml/ollama_analyzer.py）與 MCP search_manual 不用改。

分數：pgvector 的 <=> 是 cosine 距離（越小越像），換成 1 - 距離 = cosine 相似度，跟 Qdrant 的 score 同一個意思。
向量用文字 '[0.1,0.2,…]' 傳給資料庫再轉型，不需要另外裝 pgvector 的 Python 套件。
"""
from dataclasses import dataclass, field

from db.pool import acquire

EMBED_DIM = 1024

_iterative_scan: bool | None = None   # pgvector 0.8 起才有 hnsw.iterative_scan；第一次搜尋時檢查


@dataclass
class SearchHit:
    payload: dict
    score: float
    id: str = field(default="")


def to_vector_literal(vec: list[float]) -> str:
    if len(vec) != EMBED_DIM:
        raise ValueError(f"向量維度 {len(vec)}，預期 {EMBED_DIM}（bge-m3）")
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _payload(r) -> dict:
    """資料庫一列 → 跟 Qdrant payload 一樣的欄位"""
    return {
        "device_id": r["device_id"],
        "source_filename": r["filename"],
        "doc_title": r["doc_title"],
        "section": r["section"],
        "page": r["page"],
        "chunk_index": r["chunk_index"],
        "text": r["text"],
        "file_hash": r["file_hash"],
        "enabled": r["enabled"],
        "chunker_version": r["chunker_version"],
    }


async def _supports_iterative_scan(conn) -> bool:
    global _iterative_scan
    if _iterative_scan is None:
        ver = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'") or "0"
        major, minor = (int(x) for x in (ver.split(".") + ["0"])[:2])
        _iterative_scan = (major, minor) >= (0, 8)
    return _iterative_scan


async def search(query_vector: list[float], device_id: str | None = None, top_k: int = 3) -> list[SearchHit]:
    vec = to_vector_literal(query_vector)
    async with acquire() as conn:
        async with conn.transaction():
            # HNSW 是先找最像的再套篩選（device_id、enabled），篩完可能不滿 top_k；
            # iterative_scan 會繼續往下找直到湊滿，strict_order 保證順序跟精確比對一樣
            if await _supports_iterative_scan(conn):
                await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            rows = await conn.fetch(
                """
                SELECT c.id, c.chunk_index, c.section, c.page, c.text,
                       d.file_hash, d.filename, d.doc_title, d.device_id, d.enabled, d.chunker_version,
                       1 - (c.embedding <=> $1::vector) AS score
                FROM manual_chunks c
                JOIN manual_documents d USING (file_hash)
                WHERE d.enabled AND ($2::text IS NULL OR d.device_id = $2)
                ORDER BY c.embedding <=> $1::vector
                LIMIT $3
                """,
                vec, device_id, top_k,
            )
    return [SearchHit(payload=_payload(r), score=float(r["score"]), id=str(r["id"])) for r in rows]


async def file_exists(file_hash: str) -> bool:
    async with acquire() as conn:
        return bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM manual_documents WHERE file_hash = $1)", file_hash))


async def list_documents() -> list[dict]:
    """文件層級清單，欄位跟 qdrant_client.list_documents 一樣；chunk_count 以實際段數為準"""
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.file_hash, d.filename, d.device_id, d.enabled, count(c.id) AS chunk_count
            FROM manual_documents d
            LEFT JOIN manual_chunks c USING (file_hash)
            GROUP BY d.file_hash
            ORDER BY d.created_at
            """
        )
    return [
        {"file_hash": r["file_hash"], "filename": r["filename"], "device_id": r["device_id"],
         "chunk_count": r["chunk_count"], "enabled": r["enabled"]}
        for r in rows
    ]


async def add_document(doc: dict, chunks: list[dict]) -> None:
    """
    新增一份文件與它的所有段落（同一個交易，不會只寫一半）。
    doc：file_hash、filename、device_id、doc_title、enabled、chunker_version、embed_model
    chunks：id、chunk_index、section、page、text、vector
    已存在的文件整份換掉（搬移工具 --force 時用）
    """
    async with acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM manual_documents WHERE file_hash = $1", doc["file_hash"])
            await conn.execute(
                """
                INSERT INTO manual_documents
                    (file_hash, filename, device_id, doc_title, enabled, chunk_count, chunker_version, embed_model)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                doc["file_hash"], doc["filename"], doc["device_id"], doc.get("doc_title"),
                doc.get("enabled", True), len(chunks), doc.get("chunker_version"), doc.get("embed_model", "bge-m3"),
            )
            await conn.executemany(
                """
                INSERT INTO manual_chunks (id, file_hash, chunk_index, section, page, text, embedding)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::vector)
                """,
                [
                    (str(c["id"]), doc["file_hash"], c["chunk_index"], c.get("section"), c.get("page"),
                     c["text"], to_vector_literal(c["vector"]))
                    for c in chunks
                ],
            )


async def delete_document(file_hash: str) -> None:
    async with acquire() as conn:
        await conn.execute("DELETE FROM manual_documents WHERE file_hash = $1", file_hash)  # 段落跟著刪（CASCADE）


async def set_document_enabled(file_hash: str, enabled: bool) -> None:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE manual_documents SET enabled = $2, updated_at = now() WHERE file_hash = $1",
            file_hash, enabled,
        )
