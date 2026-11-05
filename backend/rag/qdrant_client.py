import os
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)

QDRANT_URL      = os.getenv("QDRANT_URL", "http://localhost:6343")
COLLECTION_NAME = "device_manuals"
VECTOR_SIZE     = 1024  # bge-m3 輸出維度

_client = AsyncQdrantClient(url=QDRANT_URL)


async def ensure_collection():
    collections = await _client.get_collections()
    names = [c.name for c in collections.collections]
    if COLLECTION_NAME not in names:
        await _client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"[Qdrant] 建立 collection：{COLLECTION_NAME}")


async def upsert_chunks(points: list[PointStruct]):
    await ensure_collection()
    await _client.upsert(collection_name=COLLECTION_NAME, points=points)


async def search(query_vector: list[float], device_id: str | None = None, top_k: int = 3):
    await ensure_collection()
    must_conditions = []
    if device_id:
        must_conditions.append(FieldCondition(key="device_id", match=MatchValue(value=device_id)))

    query_filter = Filter(
        must=must_conditions,
        must_not=[FieldCondition(key="enabled", match=MatchValue(value=False))],
    )
    result = await _client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=query_filter,
        limit=top_k,
    )
    return result.points


async def file_exists(file_hash: str) -> bool:
    await ensure_collection()
    results, _ = await _client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="file_hash", match=MatchValue(value=file_hash))]
        ),
        limit=1,
    )
    return len(results) > 0

async def list_documents() -> list[dict]:
    """列出所有已上傳文件（依 file_hash 聚合，回傳文件層級而非 chunk 層級）"""
    await ensure_collection()
    points, _ = await _client.scroll(
        collection_name=COLLECTION_NAME,
        limit=1000,
        with_payload=True,
    )
    seen: dict[str, dict] = {}
    for p in points:
        fh = p.payload.get("file_hash")
        if fh not in seen:
            seen[fh] = {
                "file_hash": fh,
                "filename": p.payload.get("source_filename"),
                "device_id": p.payload.get("device_id"),
                "chunk_count": 0,
                "enabled": p.payload.get("enabled", True),
            }
        seen[fh]["chunk_count"] += 1
    return list(seen.values())


async def delete_document(file_hash: str):
    """刪除指定 file_hash 的所有 chunks"""
    await ensure_collection()
    await _client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="file_hash", match=MatchValue(value=file_hash))]
        ),
    )
    
async def set_document_enabled(file_hash: str, enabled: bool):
    """切換指定文件的啟用狀態，更新該 file_hash 底下所有 chunks 的 payload"""
    await ensure_collection()
    await _client.set_payload(
        collection_name=COLLECTION_NAME,
        payload={"enabled": enabled},
        points=Filter(
            must=[FieldCondition(key="file_hash", match=MatchValue(value=file_hash))]
        ),
    )
