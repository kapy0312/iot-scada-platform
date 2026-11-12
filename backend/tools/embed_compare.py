"""
比對桌機 Ollama 與 DeepInfra 的 bge-m3 向量（Phase 3 前的驗收；只讀，不寫入任何資料）

為什麼要比：雲端的手冊向量是本機 Ollama 算好同步上去的，Phase 3 的 Lambda 會改用 DeepInfra 算「問題」的向量。
同一個模型、不同執行環境，數字會有細微差異，要確認差異不會讓 AI 診斷選到不同章節。

比兩件事：
  1. 手冊每一段：Ollama 存的向量 vs DeepInfra 重算的向量，相似度多少（預期 0.99 以上）
  2. 同一組問題（跟 tools.rag_compare 一樣：章節標題＋訊號條件），分別用 Ollama、DeepInfra 算問題向量，
     去查本機 pgvector 的手冊：前 5 名、依據章節是否一樣（這才是 AI 診斷真正用到的）

用法（在 backend 資料夾，TimescaleDB、桌機 Ollama 要開著，.env 要有 DEEPINFRA_API_KEY）：
    python -m tools.embed_compare
    python -m tools.embed_compare --verbose
費用：手冊約 40 段＋問題約 60 句，全部不到 1 萬 tokens（DeepInfra 每百萬 tokens 0.01 美元）
結束碼：依據章節全部一致為 0，否則為 1
"""
import argparse
import asyncio
import json
import math
import sys

from db.pool import acquire, close_pool
from ml.ollama_analyzer import RAG_CANDIDATES, build_query, condition_states
from rag import pgvector_store
from rag.embedder import embed_deepinfra, embed_text
from tools.rag_compare import compare_hits, condition_cases, primary_of


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def stats(values: list[float]) -> str:
    return f"最低 {min(values):.4f}、平均 {sum(values) / len(values):.4f}、最高 {max(values):.4f}" if values else "（沒有資料）"


async def run(device_id: str, top: int, verbose: bool) -> int:
    async with acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.text, c.section, c.embedding::text AS emb
            FROM manual_chunks c JOIN manual_documents d USING (file_hash)
            WHERE d.enabled AND d.device_id = $1
            ORDER BY d.filename, c.chunk_index
        """, device_id)
    if not rows:
        print(f"pgvector 裡沒有 {device_id} 的啟用手冊：先確認 migration 009 與 tools.rag_migrate 做過")
        return 1

    # 1. 手冊段落
    print(f"1. 手冊段落 {len(rows)} 段：Ollama（已存）vs DeepInfra（重算）")
    deep = await embed_deepinfra([r["text"] for r in rows])
    chunk_sims = [cosine(json.loads(r["emb"]), v) for r, v in zip(rows, deep)]
    print(f"   相似度 {stats(chunk_sims)}")
    worst = sorted(zip(chunk_sims, rows), key=lambda x: x[0])[:3]
    for s, r in worst if verbose else worst[:1]:
        print(f"   最低的一段 {s:.4f}：{(r['section'] or '')[:50]}")

    # 2. 問題
    sections = sorted({r["section"] for r in rows if r["section"]})
    questions = [(f"A {s[:30]}", s, {}) for s in sections]
    questions += [(f"B {name}", build_query(v, device_id), condition_states(v, device_id))
                  for name, v in condition_cases(device_id)]
    print(f"\n2. 問題 {len(questions)} 題（A 章節標題 {len(sections)}、B 訊號條件 {len(questions) - len(sections)}），"
          f"各查本機 pgvector 前 {RAG_CANDIDATES} 名")
    q_deep = await embed_deepinfra([q for _, q, _ in questions])
    q_sims, same_set, same_order, same_primary, fails = [], 0, 0, 0, []
    for (label, query, states), vd in zip(questions, q_deep):
        vo = await embed_text(query)
        q_sims.append(cosine(vo, vd))
        ho = await pgvector_store.search(vo, device_id=device_id, top_k=RAG_CANDIDATES)
        hd = await pgvector_store.search(vd, device_id=device_id, top_k=RAG_CANDIDATES)
        c = compare_hits(ho, hd, top)
        po, pd = primary_of(ho, states), primary_of(hd, states)
        same_set += c["same_set"]
        same_order += c["same_order"]
        same_primary += po == pd
        if po != pd:
            fails.append(label)
        if verbose or po != pd:
            print(f"   {'OK' if po == pd else 'NG'} [{label}] 問題向量相似度 {q_sims[-1]:.4f}、"
                  f"前{top}名重疊 {c['overlap']}/{top}、順序{'相同' if c['same_order'] else '不同'}")
            print(f"      依據章節 Ollama：{po[0]}（{po[1]}）")
            if po != pd:
                print(f"            DeepInfra：{pd[0]}（{pd[1]}）")
    n = len(questions)
    print(f"   問題向量相似度 {stats(q_sims)}")
    print(f"\n摘要：手冊段落相似度最低 {min(chunk_sims):.4f}；問題 {n} 題中，前{top}名同一批 {same_set}/{n}、"
          f"順序相同 {same_order}/{n}、依據章節相同 {same_primary}/{n}")
    if same_primary == n:
        print("結論：DeepInfra 算的問題向量可以直接查雲端（Ollama 算的）手冊，Phase 3 不用重算手冊向量")
        return 0
    print("結論：有題目依據章節不同。改用「同步上雲時用 DeepInfra 重算手冊向量」，把上面 NG 的題目貼給我")
    return 1


async def main() -> int:
    ap = argparse.ArgumentParser(description="比對 Ollama 與 DeepInfra 的 bge-m3 向量")
    ap.add_argument("--device", default="S7-1511T")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    try:
        return await run(args.device, args.top, args.verbose)
    except RuntimeError as e:
        print(f"錯誤：{e}")
        return 1
    finally:
        await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
