-- 009：手冊向量從 Qdrant 搬到 pgvector（本機 TimescaleDB 同一個資料庫）
-- 執行：Get-Content backend/db/migrations/009_manual_vectors.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- 回滾：009_manual_vectors_rollback.sql（刪掉兩張表；Qdrant 的資料不受影響，RAG_BACKEND 改回 qdrant 即可）
--
-- 需要 pgvector：timescale/timescaledb:latest-pg16 已內建（vector 0.8.1），這裡只是啟用
-- 向量模型 bge-m3、1024 維、cosine，跟 Qdrant 的 device_manuals collection 一樣
-- 啟用勾選放在文件層級（manual_documents.enabled），切換時只改一筆，不用改每一段

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS manual_documents (
    file_hash       text        PRIMARY KEY,                 -- PDF 內容的 sha256，同一份檔案不會重複上傳
    filename        text        NOT NULL,
    device_id       text        NOT NULL,                    -- 設備型號（S7-1511T；-SIM、-MOCK 共用）
    doc_title       text,
    enabled         boolean     NOT NULL DEFAULT true,       -- 手冊管理頁的勾選；false 不參與搜尋
    chunk_count     integer     NOT NULL DEFAULT 0,
    chunker_version integer,
    embed_model     text        NOT NULL DEFAULT 'bge-m3',   -- 之後換模型或雲端另算向量時用來分辨
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()       -- 雲端同步（之後）依這欄判斷哪份文件變了
);
CREATE INDEX IF NOT EXISTS manual_documents_device_idx ON manual_documents (device_id);

CREATE TABLE IF NOT EXISTS manual_chunks (
    id          uuid         PRIMARY KEY,                    -- 沿用 Qdrant 的 point id，方便兩邊對照
    file_hash   text         NOT NULL REFERENCES manual_documents (file_hash) ON DELETE CASCADE,
    chunk_index integer      NOT NULL,
    section     text,
    page        integer,
    text        text         NOT NULL,
    embedding   vector(1024) NOT NULL,
    UNIQUE (file_hash, chunk_index)
);

-- HNSW：空表也能建、新增資料不用重建（IVFFlat 要先有資料才分得好群，不適合手冊這種少量又會增減的資料）
CREATE INDEX IF NOT EXISTS manual_chunks_embedding_hnsw
    ON manual_chunks USING hnsw (embedding vector_cosine_ops);

COMMIT;

-- 確認：應該看到 vector 的版本，以及兩張表各 0 筆（之後用 python -m tools.rag_migrate 從 Qdrant 搬進來）
SELECT extversion AS pgvector FROM pg_extension WHERE extname = 'vector';
SELECT (SELECT count(*) FROM manual_documents) AS documents, (SELECT count(*) FROM manual_chunks) AS chunks;
