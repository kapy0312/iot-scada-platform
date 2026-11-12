"""
MCP 工具的資料來源：本機（TimescaleDB＋Ollama）或雲端（Tiger Cloud＋DeepInfra）

兩邊的表大多同名同欄位（雲端是本地同步上去的），只有這幾個地方不同：
- 趨勢：本機查原始資料 plc_measurements；雲端只有 10 秒彙整 measurements_10s
- 手冊：本機走 rag.store（依 RAG_BACKEND 查 Qdrant 或 pgvector）；雲端直接查 Tiger 的 pgvector（排除已刪除）
- 問題轉向量：本機呼叫桌機 Ollama；雲端呼叫 DeepInfra（同一個 bge-m3，實測相似度 0.9999）
- 連線失敗時給的提示
"""
import os

import asyncpg
from mcp.server.mcpserver.exceptions import ToolError

from core import limits


class Source:
    name = ""
    # 給 Claude 的資料說明（例如雲端資料有延遲）
    data_note: str | None = None
    # 參數：$1 區間長度、$2 device_id、$3 開始、$4 結束；回傳 bucket、tag_name、avg_v、min_v、max_v
    trend_sql = ""
    # 最近 30 天每個 device_id 的第一筆、最後一筆時間
    devices_sql = ""

    async def _open(self):
        raise NotImplementedError

    async def connect(self):
        conn = await self._open()
        # 畫面上改過的門檻存在 tag_limits：每次查詢前重新載入，正常範圍、警戒值才會跟網頁一致
        await limits.load(conn)
        return conn

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    async def manual_search(self, conn, vector: list[float], device_id: str, top_k: int) -> list[dict]:
        raise NotImplementedError

    async def manual_list(self, conn, device_id: str) -> list[dict]:
        raise NotImplementedError


# ---------- 本機 ----------

class LocalSource(Source):
    name = "local"
    trend_sql = """
        SELECT time_bucket($1::interval, time) AS bucket, tag_name,
               AVG(value) AS avg_v, MIN(value) AS min_v, MAX(value) AS max_v
        FROM plc_measurements
        WHERE device_id = $2 AND time >= $3 AND time < $4
        GROUP BY bucket, tag_name
        ORDER BY bucket
    """
    devices_sql = """
        SELECT device_id, MIN(time) AS first_seen, MAX(time) AS last_seen
        FROM plc_measurements
        WHERE time > NOW() - INTERVAL '30 days'
        GROUP BY device_id ORDER BY device_id
    """

    async def _open(self):
        from core.config import get_database_url
        try:
            return await asyncpg.connect(get_database_url(), timeout=5)
        except Exception as e:
            raise ToolError(f"無法連線 TimescaleDB（{type(e).__name__}）。請確認 timescaledb 容器已啟動："
                            "docker start timescaledb") from e

    async def embed(self, text: str) -> list[float]:
        from rag.embedder import embed_text
        try:
            return await embed_text(text)
        except Exception as e:
            raise ToolError(f"無法呼叫 bge-m3 向量化（{type(e).__name__}）。請確認桌機 Ollama 已開啟、Tailscale 已連線") from e

    async def _manual(self, coro):
        from rag import store
        try:
            return await coro
        except Exception as e:
            if store.backend_name() == "pgvector":
                hint = "請確認 timescaledb 容器已啟動，並已執行 db/migrations/009_manual_vectors.sql"
            else:
                hint = "請確認 scada-qdrant 容器已啟動（port 6343）：docker start scada-qdrant"
            raise ToolError(f"無法查詢手冊向量庫 {store.backend_name()}（{type(e).__name__}）。{hint}") from e

    async def manual_search(self, conn, vector, device_id, top_k):
        from rag import store
        hits = await self._manual(store.search(vector, device_id=device_id, top_k=top_k))
        return [{"score": h.score, **h.payload} for h in hits]

    async def manual_list(self, conn, device_id):
        from rag import store
        docs = await self._manual(store.list_documents())
        return [d for d in docs if d.get("device_id") == device_id]


# ---------- 雲端（Tiger Cloud，mcp_reader 唯讀帳號） ----------

class CloudSource(Source):
    name = "cloud"
    data_note = ("資料來自雲端（Tiger Cloud）：本機每 60 秒同步一次，所以最新資料約落後 1～3 分鐘；"
                 "趨勢是 10 秒彙整後的資料；後端內建模擬（-MOCK）不會同步上雲。")
    trend_sql = """
        SELECT time_bucket($1::interval, bucket) AS bucket, tag_name,
               SUM(avg_v * samples) / NULLIF(SUM(samples), 0) AS avg_v,
               MIN(min_v) AS min_v, MAX(max_v) AS max_v
        FROM measurements_10s
        WHERE device_id = $2 AND bucket >= $3 AND bucket < $4
        GROUP BY 1, tag_name
        ORDER BY 1
    """
    devices_sql = """
        SELECT device_id, MIN(bucket) AS first_seen, MAX(bucket) AS last_seen
        FROM measurements_10s
        WHERE bucket > NOW() - INTERVAL '30 days'
        GROUP BY device_id ORDER BY device_id
    """

    def __init__(self, url: str | None = None, deepinfra_key: str | None = None):
        # 只有「沒給」（None）才讀環境變數；明確給空字串就是空的（測試與 Lambda 設定錯誤時才不會悄悄改用 .env 的值）
        self.url = os.getenv("TIGER_READER_URL", "") if url is None else url
        self.deepinfra_key = os.getenv("DEEPINFRA_API_KEY", "") if deepinfra_key is None else deepinfra_key
        self._iterative: bool | None = None

    async def _open(self):
        if not self.url:
            raise ToolError("雲端 MCP 沒有設定資料庫連線（TIGER_READER_URL）")
        try:
            return await asyncpg.connect(self.url, timeout=10)
        except Exception as e:
            # 不把連線字串放進錯誤訊息
            raise ToolError(f"無法連線雲端資料庫（{type(e).__name__}），請稍後再試") from e

    async def embed(self, text: str) -> list[float]:
        from rag.embedder import embed_deepinfra
        if self.deepinfra_key:
            os.environ.setdefault("DEEPINFRA_API_KEY", self.deepinfra_key)
        try:
            return (await embed_deepinfra([text]))[0]
        except Exception as e:
            raise ToolError(f"無法呼叫 DeepInfra 向量化（{type(e).__name__}），請稍後再試") from e

    async def manual_search(self, conn, vector, device_id, top_k):
        from rag.pgvector_store import to_vector_literal
        if self._iterative is None:
            ver = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'") or "0"
            major, minor = (int(x) for x in (ver.split(".") + ["0"])[:2])
            self._iterative = (major, minor) >= (0, 8)
        try:
            async with conn.transaction():
                if self._iterative:
                    await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
                rows = await conn.fetch("""
                    SELECT c.section, c.page, c.chunk_index, c.text, d.filename, d.doc_title, d.device_id,
                           1 - (c.embedding <=> $1::vector) AS score
                    FROM manual_chunks c JOIN manual_documents d USING (file_hash)
                    WHERE d.enabled AND d.deleted_at IS NULL AND d.device_id = $2
                    ORDER BY c.embedding <=> $1::vector
                    LIMIT $3
                """, to_vector_literal(vector), device_id, top_k)
        except asyncpg.UndefinedTableError as e:
            raise ToolError("雲端還沒有手冊資料（需要執行 db/cloud/005_manuals_limits.sql 並等同步）") from e
        return [{"score": float(r["score"]), "source_filename": r["filename"], "doc_title": r["doc_title"],
                 "section": r["section"], "page": r["page"], "chunk_index": r["chunk_index"],
                 "text": r["text"], "device_id": r["device_id"]} for r in rows]

    async def manual_list(self, conn, device_id):
        try:
            rows = await conn.fetch("""
                SELECT d.filename, d.enabled, d.device_id, count(c.id) AS chunk_count
                FROM manual_documents d LEFT JOIN manual_chunks c USING (file_hash)
                WHERE d.deleted_at IS NULL AND d.device_id = $1
                GROUP BY d.file_hash ORDER BY d.filename
            """, device_id)
        except asyncpg.UndefinedTableError:
            return []
        return [dict(r) for r in rows]
