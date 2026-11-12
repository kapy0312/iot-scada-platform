import hashlib
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel
from rag import store
from rag.chunker import build_chunks
from rag.embedder import EMBED_MODEL, embed_batch
from core.auth import client_ip, require_operator
from core.device_profile import base_device_id
from db.audit import write_audit

router = APIRouter()

CHUNKER_VERSION = 2  # 2 = 依章節切塊，payload 含 section / page


@router.post("/api/rag/upload")
async def upload_manual(
    request: Request,
    file: UploadFile = File(...),
    device_id: str = Form(default="general"),
    user: dict = Depends(require_operator),
):
    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    if await store.file_exists(file_hash):
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

    doc = {
        "file_hash": file_hash,
        "filename": file.filename,
        "device_id": device_id,
        "doc_title": chunks[0]["doc_title"],
        "enabled": True,
        "chunker_version": CHUNKER_VERSION,
        "embed_model": EMBED_MODEL,
    }
    points = store.build_chunks(doc, chunks, vectors)
    await store.add_document(doc, points)
    await write_audit("rag.upload", "ok", username=user["username"], client_ip=client_ip(request),
                      params={"filename": file.filename, "device_id": device_id,
                              "file_hash": file_hash, "chunks": len(points)})

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
    docs = await store.list_documents()
    return {"count": len(docs), "documents": docs, "backend": store.backend_name()}


@router.delete("/api/rag/documents/{file_hash}")
async def remove_document(file_hash: str, request: Request, user: dict = Depends(require_operator)):
    await store.delete_document(file_hash)
    await write_audit("rag.delete", "ok", username=user["username"], client_ip=client_ip(request),
                      params={"file_hash": file_hash})
    return {"status": "ok", "file_hash": file_hash}


class ToggleDocumentRequest(BaseModel):
    enabled: bool


@router.patch("/api/rag/documents/{file_hash}")
async def toggle_document(file_hash: str, cmd: ToggleDocumentRequest, request: Request,
                          user: dict = Depends(require_operator)):
    await store.set_document_enabled(file_hash, cmd.enabled)
    await write_audit("rag.toggle", "ok", username=user["username"], client_ip=client_ip(request),
                      params={"file_hash": file_hash, "enabled": cmd.enabled})
    return {"status": "ok", "file_hash": file_hash, "enabled": cmd.enabled}
