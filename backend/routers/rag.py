import hashlib
import uuid
from fastapi import APIRouter, UploadFile, File, Form
from pydantic import BaseModel
from qdrant_client.models import PointStruct

from rag.chunker import build_chunks
from rag.embedder import embed_batch
from rag.qdrant_client import upsert_chunks, file_exists, list_documents, delete_document, set_document_enabled
from core.device_profile import base_device_id

router = APIRouter()

CHUNKER_VERSION = 2  # 2 = 依章節切塊，payload 含 section / page


@router.post("/api/rag/upload")
async def upload_manual(
    file: UploadFile = File(...),
    device_id: str = Form(default="general"),
):
    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    if await file_exists(file_hash):
        return {"status": "skipped", "reason": "檔案已存在，未重複嵌入", "file_hash": file_hash}

    try:
        chunks = build_chunks(content)
    except Exception as e:
        return {"status": "error", "reason": f"PDF 解析失敗：{e}"}

    if not chunks:
        return {"status": "error", "reason": "PDF 無法擷取文字（可能是掃描檔，需先 OCR）"}

    # 手冊歸屬設備型號，不分真實或模擬來源
    device_id = base_device_id(device_id.strip())

    vectors = await embed_batch([c["text"] for c in chunks])

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "device_id": device_id,
                "source_filename": file.filename,
                "doc_title": c["doc_title"],
                "section": c["section"],
                "page": c["page"],
                "chunk_index": i,
                "text": c["text"],
                "file_hash": file_hash,
                "enabled": True,
                "chunker_version": CHUNKER_VERSION,
            },
        )
        for i, (c, vector) in enumerate(zip(chunks, vectors))
    ]
    await upsert_chunks(points)

    return {
        "status": "ok",
        "filename": file.filename,
        "device_id": device_id,
        "chunks_embedded": len(points),
        "sections": [c["section"] for c in chunks],
        "file_hash": file_hash,
    }


@router.get("/api/rag/documents")
async def get_documents():
    docs = await list_documents()
    return {"count": len(docs), "documents": docs}


@router.delete("/api/rag/documents/{file_hash}")
async def remove_document(file_hash: str):
    await delete_document(file_hash)
    return {"status": "ok", "file_hash": file_hash}


class ToggleDocumentRequest(BaseModel):
    enabled: bool


@router.patch("/api/rag/documents/{file_hash}")
async def toggle_document(file_hash: str, cmd: ToggleDocumentRequest):
    await set_document_enabled(file_hash, cmd.enabled)
    return {"status": "ok", "file_hash": file_hash, "enabled": cmd.enabled}
