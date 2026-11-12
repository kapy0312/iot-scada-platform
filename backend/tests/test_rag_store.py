"""
手冊向量庫 Phase 2（Qdrant → pgvector）
- 切換規則、主要／次要寫入、比對工具、搬移分組：不需要資料庫
- pgvector 實際寫入與搜尋：需要 TEST_VECTOR_DB_URL（要有 vector extension）
  ⚠ 會刪掉並重建 manual_documents、manual_chunks：千萬不要指向正式資料庫；偵測到 timescaledb 會直接拒絕執行
執行（在 backend 資料夾）：python -m pytest tests/test_rag_store.py -q
"""
import asyncio
import math
import os
import random
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import ConfigError, rag_backend
from rag import pgvector_store, store
from rag.pgvector_store import EMBED_DIM, SearchHit, to_vector_literal
from tools.rag_compare import compare_hits, condition_cases
from tools.rag_migrate import group_points

BACKEND = Path(__file__).resolve().parents[1]


# ---------- 設定 ----------

def test_RAG_BACKEND_預設qdrant(monkeypatch):
    monkeypatch.delenv("RAG_BACKEND", raising=False)
    assert rag_backend() == "qdrant"


def test_RAG_BACKEND_大小寫與空白(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", " PgVector ")
    assert rag_backend() == "pgvector"


def test_RAG_BACKEND_打錯字直接報錯(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "pgvectr")
    with pytest.raises(ConfigError):
        rag_backend()


def test_向量維度不對就報錯():
    with pytest.raises(ValueError):
        to_vector_literal([0.1] * 768)
    assert to_vector_literal([0.5] * EMBED_DIM).startswith("[0.5,0.5")


# ---------- 主要／次要寫入（假的兩個向量庫） ----------

class FakeStore:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def delete_document(self, fh):
        self.calls.append(("delete", fh))
        if self.fail:
            raise RuntimeError("boom")

    async def search(self, vec, device_id=None, top_k=3):
        return [SimpleNamespace(payload={"section": "1.1"}, score=0.9, id="p1")]


def use_fakes(monkeypatch, backend, q, p):
    monkeypatch.setenv("RAG_BACKEND", backend)
    monkeypatch.setattr(store, "_STORES", {"qdrant": q, "pgvector": p})


def test_寫入兩邊都寫(monkeypatch):
    q, p = FakeStore(), FakeStore()
    use_fakes(monkeypatch, "qdrant", q, p)
    asyncio.run(store.delete_document("h1"))
    assert q.calls == [("delete", "h1")] and p.calls == [("delete", "h1")]


def test_次要失敗只警告不擋(monkeypatch, capsys):
    q, p = FakeStore(), FakeStore(fail=True)
    use_fakes(monkeypatch, "qdrant", q, p)
    asyncio.run(store.delete_document("h1"))
    assert "次要向量庫 pgvector" in capsys.readouterr().out


def test_主要失敗要報錯_次要不做(monkeypatch):
    q, p = FakeStore(), FakeStore(fail=True)
    use_fakes(monkeypatch, "pgvector", q, p)
    with pytest.raises(RuntimeError):
        asyncio.run(store.delete_document("h1"))
    assert q.calls == []


def test_Qdrant結果包成SearchHit(monkeypatch):
    use_fakes(monkeypatch, "qdrant", FakeStore(), FakeStore())
    hits = asyncio.run(store.search([0.0] * EMBED_DIM))
    assert isinstance(hits[0], SearchHit) and hits[0].score == 0.9 and hits[0].payload["section"] == "1.1"


def test_兩邊用同一個段落id():
    chunks = store.build_chunks({}, [{"text": "a", "section": "1", "page": 1}, {"text": "b"}], [[0.1], [0.2]])
    assert [c["chunk_index"] for c in chunks] == [0, 1]
    assert len({c["id"] for c in chunks}) == 2 and uuid.UUID(chunks[0]["id"])


# ---------- 比對工具 ----------

def h(fh, i, score):
    return SearchHit(payload={"file_hash": fh, "chunk_index": i}, score=score)


def test_比對_完全相同():
    a = [h("x", 1, 0.9), h("x", 2, 0.8)]
    c = compare_hits(a, [h("x", 1, 0.9), h("x", 2, 0.8)], 2)
    assert c["same_set"] and c["same_order"] and c["max_score_diff"] == 0


def test_比對_順序不同與分數差():
    c = compare_hits([h("x", 1, 0.9), h("x", 2, 0.8)], [h("x", 2, 0.8), h("x", 1, 0.85)], 2)
    assert c["same_set"] and not c["same_order"] and math.isclose(c["max_score_diff"], 0.05)


def test_比對題目_單一訊號高低與兩兩組合():
    cases = condition_cases("S7-1511T")
    n = 5
    assert len(cases) == n * 2 + n * (n - 1) // 2


# ---------- 搬移分組 ----------

def pt(fh, idx, enabled=True, **extra):
    payload = {"file_hash": fh, "chunk_index": idx, "source_filename": f"{fh}.pdf", "device_id": "S7-1511T",
               "text": f"t{idx}", "enabled": enabled, **extra}
    return SimpleNamespace(id=str(uuid.uuid4()), payload=payload, vector=[0.0] * EMBED_DIM)


def test_搬移_依文件分組並排序():
    docs = group_points([pt("a", 2), pt("a", 0), pt("b", 0, enabled=False), pt("a", 1)])
    assert [c["chunk_index"] for c in docs["a"]["chunks"]] == [0, 1, 2]
    assert docs["b"]["enabled"] is False and docs["a"]["filename"] == "a.pdf"


def test_搬移_chunk_index重複就重新編號():
    docs = group_points([pt("a", 0), pt("a", 0), pt("a", 0)])
    assert [c["chunk_index"] for c in docs["a"]["chunks"]] == [0, 1, 2]


# ---------- pgvector 實際寫入與搜尋（TEST_VECTOR_DB_URL） ----------

DB_URL = os.getenv("TEST_VECTOR_DB_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未設定 TEST_VECTOR_DB_URL")


def rand_vec(rng) -> list[float]:
    return [rng.uniform(-1, 1) for _ in range(EMBED_DIM)]


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@pytest.fixture
def vdb(monkeypatch):
    import asyncpg
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    pgvector_store._iterative_scan = None

    async def setup():
        conn = await asyncpg.connect(DB_URL)
        try:
            if await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"):
                pytest.exit("TEST_VECTOR_DB_URL 指到有 timescaledb 的資料庫，可能是正式資料庫，拒絕執行")
            await conn.execute((BACKEND / "db/migrations/009_manual_vectors_rollback.sql").read_text(encoding="utf-8"))
            await conn.execute((BACKEND / "db/migrations/009_manual_vectors.sql").read_text(encoding="utf-8"))
        finally:
            await conn.close()

    asyncio.run(setup())
    yield


def run_db(coro_fn):
    """每個測試自己的 event loop；結束前關掉連線池"""
    from db.pool import close_pool

    async def wrapper():
        try:
            return await coro_fn()
        finally:
            await close_pool()
    return asyncio.run(wrapper())


def make_doc(fh, device="S7-1511T", n=4, rng=None, enabled=True):
    rng = rng or random.Random(fh)
    doc = {"file_hash": fh, "filename": f"{fh}.pdf", "device_id": device, "doc_title": "手冊",
           "enabled": enabled, "chunker_version": 2}
    chunks = store.build_chunks(doc, [{"text": f"{fh}-{i}", "section": f"{i}. 章節", "page": i + 1}
                                      for i in range(n)], [rand_vec(rng) for _ in range(n)])
    return doc, chunks


@needs_db
def test_pg_搜尋順序與分數跟精確計算一樣(vdb):
    rng = random.Random(7)
    doc_a, ch_a = make_doc("a", n=30, rng=rng)
    doc_b, ch_b = make_doc("b", n=30, rng=rng)
    query = rand_vec(rng)

    async def go():
        await pgvector_store.add_document(doc_a, ch_a)
        await pgvector_store.add_document(doc_b, ch_b)
        return await pgvector_store.search(query, device_id="S7-1511T", top_k=10)

    hits = run_db(go)
    expect = sorted(ch_a + ch_b, key=lambda c: -cosine(query, c["vector"]))[:10]
    assert [x.id for x in hits] == [c["id"] for c in expect]
    for x, c in zip(hits, expect):
        assert math.isclose(x.score, cosine(query, c["vector"]), abs_tol=1e-5)
    # payload 欄位跟 Qdrant 一樣，AI 診斷與 MCP 直接用
    assert {"source_filename", "section", "page", "text", "chunk_index", "file_hash", "device_id"} <= set(hits[0].payload)


@needs_db
def test_pg_篩選設備與停用文件(vdb):
    rng = random.Random(1)
    doc_a, ch_a = make_doc("a", rng=rng)
    doc_b, ch_b = make_doc("b", device="FX5U", rng=rng)
    doc_c, ch_c = make_doc("c", rng=rng, enabled=False)

    async def go():
        for d, c in ((doc_a, ch_a), (doc_b, ch_b), (doc_c, ch_c)):
            await pgvector_store.add_document(d, c)
        only = await pgvector_store.search(ch_c[0]["vector"], device_id="S7-1511T", top_k=20)
        await pgvector_store.set_document_enabled("c", True)
        after = await pgvector_store.search(ch_c[0]["vector"], device_id="S7-1511T", top_k=20)
        return only, after

    only, after = run_db(go)
    assert {x.payload["file_hash"] for x in only} == {"a"}          # FX5U 與停用的 c 都不出現
    assert after[0].payload["file_hash"] == "c" and math.isclose(after[0].score, 1.0, abs_tol=1e-5)


@needs_db
def test_pg_清單刪除與重搬(vdb):
    doc, chunks = make_doc("a", n=5)

    async def go():
        await pgvector_store.add_document(doc, chunks)
        await pgvector_store.add_document(doc, chunks[:3])          # 重搬：整份換掉，不會變 8 段
        listed = await pgvector_store.list_documents()
        exists = await pgvector_store.file_exists("a")
        await pgvector_store.delete_document("a")
        from db.pool import acquire
        async with acquire() as conn:
            left = await conn.fetchval("SELECT count(*) FROM manual_chunks")
        return listed, exists, left, await pgvector_store.file_exists("a")

    listed, exists, left, exists_after = run_db(go)
    assert listed == [{"file_hash": "a", "filename": "a.pdf", "device_id": "S7-1511T", "chunk_count": 3, "enabled": True}]
    assert exists and left == 0 and not exists_after
