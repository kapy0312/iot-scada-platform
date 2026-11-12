"""
比對 Qdrant 與 pgvector 的手冊搜尋結果（Phase 2 切換前的驗收）

同一個問題只算一次向量，兩邊各查一次，逐題比：
  1. 前 5 名章節是否相同、順序是否一樣
  2. 相似度分數差多少（同樣的向量，預期差不到 0.001）
  3. AI 診斷實際用的：依章節條件重排後選出的依據章節與配對類型（full／partial／reference）是否相同

題目兩組：
  A. 手冊每個章節標題本身（MCP search_manual 這類自由提問）
  B. 單一訊號偏高／偏低、兩兩組合偏高，用 AI 診斷的 build_query 產生查詢句

用法（在 backend 資料夾，Qdrant、TimescaleDB、桌機 Ollama 都要開著）：
    python -m tools.rag_compare                只印摘要與不一致的題目
    python -m tools.rag_compare --verbose      每一題都印
    python -m tools.rag_compare --device S7-1511T --top 5
結束碼：第 3 項全部一致為 0，否則為 1
"""
import argparse
import asyncio
import itertools
import sys

from core.device_profile import get_profile
from db.pool import close_pool
from ml.ollama_analyzer import RAG_CANDIDATES, build_query, condition_states, rank_candidates, select_primary
from rag import pgvector_store, qdrant_client
from rag.embedder import embed_text
from rag.pgvector_store import SearchHit

SCORE_TOL = 1e-3


def hit_key(h: SearchHit) -> tuple:
    p = h.payload or {}
    return (p.get("file_hash"), p.get("chunk_index"))


def compare_hits(a: list[SearchHit], b: list[SearchHit], k: int = 5) -> dict:
    """a＝Qdrant、b＝pgvector；回傳前 k 名是否同一批、順序是否相同、共同段落的最大分數差"""
    ka, kb = [hit_key(h) for h in a[:k]], [hit_key(h) for h in b[:k]]
    sa, sb = {hit_key(h): h.score for h in a}, {hit_key(h): h.score for h in b}
    common = set(sa) & set(sb)
    return {
        "same_set": set(ka) == set(kb),
        "same_order": ka == kb,
        "overlap": len(set(ka) & set(kb)),
        "max_score_diff": max((abs(sa[x] - sb[x]) for x in common), default=0.0),
    }


def primary_of(hits: list[SearchHit], states: dict[str, str]) -> tuple[str | None, str | None]:
    ranked = rank_candidates(hits, states)
    chosen, match = select_primary(ranked)
    return (chosen["payload"].get("section") if chosen else None), match


def condition_cases(device_id: str) -> list[tuple[str, dict]]:
    """B 組題目：每個訊號偏高、偏低，以及兩兩一起偏高；其餘訊號放在正常範圍中間"""
    profile = get_profile(device_id)
    if not profile:
        return []
    tags = profile["tags"]
    base = {t: (s["normal"][0] + s["normal"][1]) / 2 for t, s in tags.items()}
    high = {t: (s["warning"] + s["danger"]) / 2 for t, s in tags.items()}
    low = {t: s["normal"][0] - (s["normal"][1] - s["normal"][0]) * 0.5 for t, s in tags.items()}
    cases = []
    for t in tags:
        cases.append((f"{tags[t]['label']}高", {**base, t: high[t]}))
        cases.append((f"{tags[t]['label']}低", {**base, t: low[t]}))
    for t1, t2 in itertools.combinations(tags, 2):
        cases.append((f"{tags[t1]['label']}＋{tags[t2]['label']}高", {**base, t1: high[t1], t2: high[t2]}))
    return cases


async def run(device_id: str, top: int, verbose: bool) -> int:
    qdrant_search = qdrant_client.search
    sections = sorted({
        str(p.payload.get("section"))
        for p in await qdrant_client.scroll_all(with_vectors=False)
        if (p.payload or {}).get("device_id") == device_id and (p.payload or {}).get("enabled", True) is not False
        and p.payload.get("section")
    })
    questions: list[tuple[str, str, dict]] = [("A", s, {}) for s in sections]
    for name, values in condition_cases(device_id):
        questions.append((f"B {name}", build_query(values, device_id), condition_states(values, device_id)))
    print(f"題目：A 組（章節標題）{len(sections)} 題、B 組（訊號條件）{len(questions) - len(sections)} 題，"
          f"設備 {device_id}，比前 {top} 名\n")

    stats = {"same_set": 0, "same_order": 0, "same_primary": 0}
    worst = 0.0
    fails = []
    for group, query, states in questions:
        vec = await embed_text(query)
        qa = [SearchHit(payload=h.payload or {}, score=h.score or 0.0, id=str(h.id))
              for h in await qdrant_search(vec, device_id=device_id, top_k=RAG_CANDIDATES)]
        pb = await pgvector_store.search(vec, device_id=device_id, top_k=RAG_CANDIDATES)
        c = compare_hits(qa, pb, top)
        prim_q, prim_p = primary_of(qa, states), primary_of(pb, states)
        same_primary = prim_q == prim_p
        stats["same_set"] += c["same_set"]
        stats["same_order"] += c["same_order"]
        stats["same_primary"] += same_primary
        worst = max(worst, c["max_score_diff"])
        # 前幾名有同分時，邊界上的那一段可能換人，所以「同一批」只列出來，不算失敗
        ok = same_primary and c["max_score_diff"] < SCORE_TOL
        if not ok:
            fails.append(query)
        if verbose or not ok:
            mark = "OK " if ok else "NG "
            print(f"{mark}[{group}] {query[:60]}")
            print(f"     前{top}名 重疊 {c['overlap']}/{top}、順序{'相同' if c['same_order'] else '不同'}、"
                  f"分數最大差 {c['max_score_diff']:.5f}")
            print(f"     依據章節 Qdrant：{prim_q[0]}（{prim_q[1]}）")
            if not same_primary:
                print(f"              pgvector：{prim_p[0]}（{prim_p[1]}）")

    n = len(questions)
    print(f"\n摘要（{n} 題）：前{top}名同一批 {stats['same_set']}/{n}、順序相同 {stats['same_order']}/{n}、"
          f"依據章節相同 {stats['same_primary']}/{n}、分數最大差 {worst:.5f}")
    if stats["same_primary"] == n:
        print("結論：AI 診斷選到的章節完全一致，可以把 RAG_BACKEND 切成 pgvector")
        return 0
    print(f"結論：有 {n - stats['same_primary']} 題依據章節不同，先別切換，把上面 NG 的題目貼給我看")
    return 1


async def main() -> int:
    ap = argparse.ArgumentParser(description="比對 Qdrant 與 pgvector 的手冊搜尋結果")
    ap.add_argument("--device", default="S7-1511T")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    try:
        return await run(args.device, args.top, args.verbose)
    finally:
        await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
