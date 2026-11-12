import os
import httpx

OLLAMA_URL  = os.getenv("OLLAMA_URL", "http://100.89.23.28:11434")
EMBED_MODEL = "bge-m3"


async def embed_text(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        response.raise_for_status()
        return response.json()["embedding"]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    # Ollama /api/embeddings 不支援批次輸入，逐筆呼叫
    return [await embed_text(t) for t in texts]

# ---------- DeepInfra 代管的 bge-m3（同一個模型，給雲端用；Phase 3 的 Lambda 連不到桌機 Ollama） ----------
# 跟 rag-platform 同一套呼叫方式：OpenAI 相容端點、Bearer token；金鑰放 .env 的 DEEPINFRA_API_KEY
DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/embeddings"
DEEPINFRA_MODEL = "BAAI/bge-m3"
DEEPINFRA_BATCH = 16


async def embed_deepinfra(texts: list[str]) -> list[list[float]]:
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("找不到 DEEPINFRA_API_KEY：請加在 repo 根目錄的 .env")
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), DEEPINFRA_BATCH):
            r = await client.post(
                DEEPINFRA_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"input": texts[i:i + DEEPINFRA_BATCH], "model": DEEPINFRA_MODEL, "encoding_format": "float"},
            )
            if r.status_code in (401, 403):
                raise RuntimeError(f"DeepInfra 拒絕金鑰（HTTP {r.status_code}）：確認 .env 的 DEEPINFRA_API_KEY")
            if r.status_code == 402:
                raise RuntimeError("DeepInfra 回 402：帳號付款資訊或額度有問題，請到 DeepInfra 後台 Billing 確認")
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])   # 依輸入順序排好
            out.extend(d["embedding"] for d in data)
    return out
