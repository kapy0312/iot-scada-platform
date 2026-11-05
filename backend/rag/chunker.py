"""
手冊 PDF → 依章節切塊

流程：逐頁抽文字 → 修正部首字元 → 去掉頁碼行 → 依「2.1 標題」這類編號切成章節
     → 章節太長再切小 → 每塊附上章節名稱與頁碼
"""
import io
import re
import unicodedata

from pypdf import PdfReader

MAX_CHARS = 800      # 單一 chunk 上限（字元）
OVERLAP   = 100      # 長章節再切小時的重疊字數

# 由瀏覽器印成的 PDF 常把「馬」「手」等字存成外觀相同的「康熙部首」字元，
# 肉眼看不出差別，但搜尋「馬達」會對不到「⾺達」。U+2F00~2FDF 可用 NFKC 轉回正常字；
# U+2E80~2EFF（部首補充）大多沒有對應，只能手動補常見的幾個。
_RADICAL_SUPPLEMENT_MAP = {
    "⻑": "長", "⻄": "西", "⻝": "食", "⻘": "青", "⻤": "鬼",
    "⻛": "風", "⻢": "馬", "⻨": "麥", "⻩": "黃", "⻮": "齒",
}

_PAGE_HEADER = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")           # 例：「1 / 4」
_SUB_HEADING = re.compile(r"^(\d+)\.(\d+)\s*(\S.*)$")         # 例：「2.1 連線逾時」
_TOP_HEADING = re.compile(r"^(\d+)\.\s*(\S.*)$")              # 例：「2. OPC UA 通訊異常診斷」
_STARTS_WITH_NUMBER = re.compile(r"^\d+\.")


def normalize_text(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x2F00 <= code <= 0x2FDF:
            ch = unicodedata.normalize("NFKC", ch)
        elif 0x2E80 <= code <= 0x2EFF:
            ch = _RADICAL_SUPPLEMENT_MAP.get(ch, ch)
        out.append(ch)
    text = "".join(out)
    text = re.sub(r"[ \t　]+", " ", text)   # 連續空白收成一個
    return text


def extract_lines(content: bytes) -> list[tuple[int, str]]:
    """回傳 [(頁碼, 一行文字), ...]，已正規化、去掉頁碼行與空行"""
    reader = PdfReader(io.BytesIO(content))
    lines: list[tuple[int, str]] = []
    for page_no, page in enumerate(reader.pages, start=1):
        raw = normalize_text(page.extract_text() or "")
        for line in raw.splitlines():
            line = line.strip()
            if not line or _PAGE_HEADER.match(line):
                continue
            lines.append((page_no, line))
    return lines


def _is_top_heading(line: str, next_line: str | None, expected: int) -> bool:
    """
    「3. PLC 開機自檢異常」與清單項目「3. 檢查後端寫入邏輯」長得一樣，用三個條件區分：
    1. 編號必須是下一個大章節號
    2. 標題短、不是句子（不含逗號、句號）
    3. 下一行是它的第一個小節（3.1），或是一般內文（不是另一個編號項目）
    """
    m = _TOP_HEADING.match(line)
    if not m or int(m.group(1)) != expected:
        return False
    title = m.group(2)
    if len(title) > 30 or any(p in title for p in "，。,;；"):
        return False
    if next_line is None:
        return True
    if next_line.startswith(f"{expected}.1"):
        return True
    return not _STARTS_WITH_NUMBER.match(next_line)


def split_sections(lines: list[tuple[int, str]]) -> tuple[str, list[dict]]:
    """回傳 (文件標題, [{"section": 章節名, "page": 起始頁, "text": 內文}, ...])"""
    doc_title = lines[0][1] if lines else ""
    sections: list[dict] = []
    current = {"section": "前言", "page": lines[0][0] if lines else 1, "lines": []}
    top_no, top_title, sub_no = 0, "", 0

    for i, (page, line) in enumerate(lines):
        if i == 0:
            continue  # 第一行是文件標題
        next_line = lines[i + 1][1] if i + 1 < len(lines) else None

        sub = _SUB_HEADING.match(line)
        if sub and int(sub.group(1)) == top_no and int(sub.group(2)) > sub_no:
            sections.append(current)
            sub_no = int(sub.group(2))
            current = {"section": f"{top_title} > {line}", "page": page, "lines": []}
            continue

        if _is_top_heading(line, next_line, top_no + 1):
            sections.append(current)
            top_no, sub_no, top_title = top_no + 1, 0, line
            current = {"section": line, "page": page, "lines": []}
            continue

        current["lines"].append(line)

    sections.append(current)

    result = []
    for s in sections:
        text = "\n".join(s["lines"]).strip()
        if text:
            result.append({"section": s["section"], "page": s["page"], "text": text})
    return doc_title, result


def _split_long(text: str, size: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, start = [], 0
    while start < len(text):
        parts.append(text[start:start + size])
        start += size - overlap
    return [p.strip() for p in parts if p.strip()]


def build_chunks(content: bytes) -> list[dict]:
    """
    PDF bytes → [{"text": 給向量化與 LLM 看的文字（含章節標題）, "section", "page", "doc_title"}]
    """
    lines = extract_lines(content)
    if not lines:
        return []
    doc_title, sections = split_sections(lines)
    chunks = []
    for s in sections:
        for part in _split_long(s["text"]):
            chunks.append({
                "text": f"【{s['section']}】\n{part}",
                "section": s["section"],
                "page": s["page"],
                "doc_title": doc_title,
            })
    return chunks
