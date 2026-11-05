"""
上傳前先預覽手冊會被切成哪些章節（不會寫入 Qdrant）。

用法（在 backend 資料夾）：
    python tools/preview_chunks.py "C:\\路徑\\S7-1511T設備維護手冊測試版2.pdf"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.chunker import build_chunks  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("用法：python tools/preview_chunks.py <PDF 路徑>")
        sys.exit(1)
    chunks = build_chunks(Path(sys.argv[1]).read_bytes())
    if not chunks:
        print("抽不到文字（可能是掃描檔）")
        return
    print(f"文件標題：{chunks[0]['doc_title']}")
    print(f"共 {len(chunks)} 段\n")
    for c in chunks:
        print(f"p{c['page']:<3}{len(c['text']):>5} 字  {c['section']}")
    print("\n----- 範例段落 -----")
    print(chunks[min(2, len(chunks) - 1)]["text"])


if __name__ == "__main__":
    main()
