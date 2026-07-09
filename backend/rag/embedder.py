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