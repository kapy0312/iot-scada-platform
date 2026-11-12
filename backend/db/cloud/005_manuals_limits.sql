-- 雲端 005：手冊向量（給 Phase 3 雲端 MCP 查手冊）＋事件的當時門檻
-- 用 tsdbadmin 執行：python -m tools.cloud_admin apply db/cloud/005_manuals_limits.sql
-- 回滾：005_manuals_limits_rollback.sql
--
-- 手冊：對應本地 009 的 manual_documents／manual_chunks，向量 bge-m3 1024 維
-- - 本地刪掉的手冊，雲端不刪，只標 deleted_at（文件表 edge_writer 維持「只能新增、更新」）
--   雲端查手冊時要加 WHERE enabled AND deleted_at IS NULL
-- - 唯一例外：manual_chunks 給 edge_writer DELETE，同一份 PDF 重新切塊時清掉舊段落（段落是由文件算出來的資料）
-- - 同一份 PDF（file_hash）內容不會變，段落只在第一次送；之後只更新勾選與 deleted_at
-- 事件：對應本地 010 的 limits、limits_changed

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS manual_documents (
    file_hash       text        PRIMARY KEY,
    filename        text        NOT NULL,
    device_id       text        NOT NULL,
    doc_title       text,
    enabled         boolean     NOT NULL,
    chunk_count     integer     NOT NULL,
    chunker_version integer,
    embed_model     text        NOT NULL,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,                -- 本地最後更新時間
    deleted_at      timestamptz,                         -- 本地已刪除（雲端保留，查詢時排除）
    synced_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manual_chunks (
    id          uuid         PRIMARY KEY,                -- 沿用本地的 id
    file_hash   text         NOT NULL REFERENCES manual_documents (file_hash),
    chunk_index integer      NOT NULL,
    section     text,
    page        integer,
    text        text         NOT NULL,
    embedding   vector(1024) NOT NULL
);
CREATE INDEX IF NOT EXISTS manual_chunks_file_idx ON manual_chunks (file_hash, chunk_index);
CREATE INDEX IF NOT EXISTS manual_chunks_embedding_hnsw ON manual_chunks USING hnsw (embedding vector_cosine_ops);

ALTER TABLE anomaly_incidents ADD COLUMN IF NOT EXISTS limits jsonb;
ALTER TABLE anomaly_incidents ADD COLUMN IF NOT EXISTS limits_changed boolean NOT NULL DEFAULT false;

-- 權限：跟 002 一樣，edge_writer 只能新增、更新（manual_chunks 另外可刪舊段落），mcp_reader 只能讀
GRANT SELECT, INSERT, UPDATE ON manual_documents, manual_chunks TO edge_writer;
GRANT DELETE ON manual_chunks TO edge_writer;
GRANT SELECT ON manual_documents, manual_chunks TO mcp_reader;

-- 確認 1：edge_writer 的 manual_documents 是 INSERT,SELECT,UPDATE、manual_chunks 多一個 DELETE；mcp_reader 只有 SELECT
SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_name IN ('manual_documents', 'manual_chunks') AND grantee IN ('edge_writer', 'mcp_reader')
GROUP BY table_name, grantee ORDER BY table_name, grantee;
-- 確認 2：事件表多了 limits、limits_changed
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'anomaly_incidents' AND column_name IN ('limits', 'limits_changed') ORDER BY column_name;
