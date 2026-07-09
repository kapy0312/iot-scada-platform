import hashlib
import io
import uuid
from fastapi import APIRouter, UploadFile, File, Form
from pydantic import BaseModel
from pypdf import PdfReader
from qdrant_client.models import PointStruct

from rag.embedder import embed_batch
from rag.qdrant_client import upsert_chunks, file_exists, list_documents, delete_document, set_document_enabled

router = APIRouter()

CHUNK_SIZE    = 500
CHUNK_OVERLAP = 50


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return [c.strip() for c in chunks if c.strip()]


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
        reader = PdfReader(io.BytesIO(content))
        full_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        return {"status": "error", "reason": f"PDF 解析失敗：{e}"}

    if not full_text.strip():
        return {"status": "error", "reason": "PDF 無法擷取文字（可能是掃描檔，需先 OCR）"}

    chunks = chunk_text(full_text)
    vectors = await embed_batch(chunks)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "device_id": device_id,
                "source_filename": file.filename,
                "chunk_index": i,
                "text": chunk,
                "file_hash": file_hash,
                "enabled": True,
            },
        )
        for i, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]
    await upsert_chunks(points)

    return {
        "status": "ok",
        "filename": file.filename,
        "device_id": device_id,
        "chunks_embedded": len(points),
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