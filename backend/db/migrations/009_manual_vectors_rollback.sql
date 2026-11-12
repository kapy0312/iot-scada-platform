-- 009 回滾：刪掉 pgvector 的手冊表（先把 .env 的 RAG_BACKEND 改回 qdrant 或刪掉，再執行）
-- 執行：Get-Content backend/db/migrations/009_manual_vectors_rollback.sql | docker exec -i timescaledb psql -U postgres -d iotscada
-- vector extension 保留（刪掉沒有好處，重跑 009 時也會再用到）

BEGIN;
DROP TABLE IF EXISTS manual_chunks;
DROP TABLE IF EXISTS manual_documents;
COMMIT;
