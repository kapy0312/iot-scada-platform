"""
把手冊向量從 Qdrant 搬到 pgvector（Phase 2）

直接搬 Qdrant 裡的文字、章節、頁碼和向量，不重新切塊、不重新算向量：
兩邊內容一模一樣，比對結果時差異只會來自搜尋方式。

用法（在 backend 資料夾）：
    python -m tools.rag_migrate --dry-run   只列出要搬什麼，不寫入
    python -m tools.rag_migrate             搬移（pgvector 已有的文件跳過，只同步啟用勾選）
    python -m tools.rag_migrate --force     已有的文件也整份重搬

需要先執行 db/migrations/009_manual_vectors.sql。可以重複執行，不會重複寫入。

RAG_BACKEND=pgvector 時 pgvector 才是正本：Qdrant 可能漏掉了停機期間的刪除或勾選，
這時再從 Qdrant 搬會把舊資料蓋回來，所以只列出差異、不寫入（確定要搬加 --from-qdrant-anyway）。
"""
import argparse
import asyncio
import sys
from collections import defaultdict

from core.config import rag_backend
from db.pool import acquire, close_pool
from rag import pgvector_store, qdrant_client


def group_points(points) -> dict[str, dict]:
    """Qdrant 的 point → 依 file_hash 分成文件，段落依 chunk_index 排好"""
    docs: dict[str, dict] = {}
    chunks: dict[str, list] = defaultdict(list)
    for p in points:
        pl = p.payload or {}
        fh = pl.get("file_hash")
        if not fh:
            print(f"  ! 略過沒有 file_hash 的 point {p.id}")
            continue
        if fh not in docs:
            docs[fh] = {
                "file_hash": fh,
                "filename": pl.get("source_filename") or "(未命名)",
                "device_id": pl.get("device_id") or "general",
                "doc_title": pl.get("doc_title"),
                "enabled": pl.get("enabled", True) is not False,
                "chunker_version": pl.get("chunker_version"),
                "embed_model": "bge-m3",
            }
        vec = p.vector
        if isinstance(vec, dict):           # 具名向量的 collection（我們沒用，保險起見）
            vec = next(iter(vec.values()))
        chunks[fh].append({
            "id": str(p.id),
            "chunk_index": pl.get("chunk_index", 0),
            "section": pl.get("section"),
            "page": pl.get("page"),
            "text": pl.get("text", ""),
            "vector": list(vec),
        })
    for fh, cs in chunks.items():
        cs.sort(key=lambda c: c["chunk_index"])
        # 舊版切塊可能沒有 chunk_index 或重複：依順序重新編號，避免違反 UNIQUE (file_hash, chunk_index)
        if len({c["chunk_index"] for c in cs}) != len(cs):
            for i, c in enumerate(cs):
                c["chunk_index"] = i
        docs[fh]["chunks"] = cs
    return docs


async def main() -> int:
    ap = argparse.ArgumentParser(description="手冊向量 Qdrant → pgvector")
    ap.add_argument("--dry-run", action="store_true", help="只列出，不寫入")
    ap.add_argument("--force", action="store_true", help="pgvector 已有的文件也整份重搬")
    ap.add_argument("--from-qdrant-anyway", action="store_true",
                    help="RAG_BACKEND=pgvector 時仍以 Qdrant 為準寫入（通常不需要）")
    args = ap.parse_args()
    if rag_backend() == "pgvector" and not args.from_qdrant_anyway and not args.dry_run:
        print("目前 RAG_BACKEND=pgvector（pgvector 是正本），改成只列出差異、不寫入")
        args.dry_run = True

    points = await qdrant_client.scroll_all(with_vectors=True)
    docs = group_points(points)
    print(f"Qdrant：{len(docs)} 份文件、{len(points)} 段")

    try:
        existing = {d["file_hash"]: d for d in await pgvector_store.list_documents()}
    except Exception as e:
        print(f"讀不到 pgvector 的表（{type(e).__name__}: {e}）。請先執行 db/migrations/009_manual_vectors.sql")
        return 1

    copied = skipped = toggled = 0
    for fh, doc in docs.items():
        n = len(doc["chunks"])
        label = f"{doc['filename']}（{doc['device_id']}，{n} 段，{'啟用' if doc['enabled'] else '停用'}）"
        old = existing.get(fh)
        if old and not args.force:
            if old["enabled"] != doc["enabled"]:
                print(f"  ~ 同步勾選 {label}")
                if not args.dry_run:
                    await pgvector_store.set_document_enabled(fh, doc["enabled"])
                toggled += 1
            else:
                print(f"  = 已存在 {label}")
            skipped += 1
            continue
        print(f"  + {'重搬' if old else '搬移'} {label}")
        if not args.dry_run:
            chunks = doc.pop("chunks")
            await pgvector_store.add_document(doc, chunks)
            doc["chunks"] = chunks
        copied += 1

    if args.dry_run:
        print(f"\n（試跑，沒有寫入）會搬 {copied} 份、略過 {skipped} 份")
        return 0

    # 逐份核對段數
    print("\n核對段數：")
    bad = 0
    async with acquire() as conn:
        rows = await conn.fetch("SELECT file_hash, count(*) AS n FROM manual_chunks GROUP BY file_hash")
    pg_counts = {r["file_hash"]: r["n"] for r in rows}
    for fh, doc in docs.items():
        q, p = len(doc["chunks"]), pg_counts.get(fh, 0)
        ok = q == p
        bad += not ok
        print(f"  {'OK ' if ok else 'NG '} {doc['filename']}：Qdrant {q} 段、pgvector {p} 段")
    extra = set(pg_counts) - set(docs)
    if extra:
        print(f"  注意：pgvector 有 {len(extra)} 份 Qdrant 沒有的文件（Qdrant 那邊被刪掉了？）：{sorted(extra)}")
    print(f"\n完成：搬移 {copied} 份、略過 {skipped} 份（其中同步勾選 {toggled} 份）、段數不符 {bad} 份")
    return 1 if bad else 0


async def _run() -> int:
    try:
        return await main()
    finally:
        await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
