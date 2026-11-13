"""Pull Item 1A and Item 9A out of a 10-K HTML document.

The extractor looks for item headings and keeps the longest span that
ends at the next item. A table-of-contents hit is short, so the body
heading wins. If the span is missing or the wrong length, the section
is marked missing or incomplete and later steps do not treat that as
a conclusion about the company.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from drift_detector.models import Passage, Section
from drift_detector.textutil import clean_text

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

EXTRACTOR_VERSION = 5

DEFAULT_MIN = {"1A": 1500, "9A": 400}
DEFAULT_MAX = {"1A": 250_000, "9A": 80_000}
EXPECTED_NEXT = {
    "1A": {"1B", "1C", "2"},
    "9A": {"9B", "9C", "10"},
}
CANONICAL = {
    "1A": "risk factors",
    "9A": "controls and procedures",
}
HEADING_MAX = 320
MIN_PASSAGE = 80

_ITEM_RE = re.compile(
    r"^item\s+(\d{1,2}[a-c]?)\s*[\.\:\-\u2013\u2014]?\s*(.*)$",
    re.I,
)
_REJECT_IN_HEADER = (" of this", " in this", " on page", " under the heading")
_FURNITURE_PART = re.compile(r"^part\s+[ivx]+$", re.I)
_FURNITURE_NUM = re.compile(r"^\d{1,3}$")


@dataclass
class Block:
    text: str
    bold: bool = False
    lead_title: str | None = None


def item_number(text: str) -> str | None:
    cleaned = clean_text(text)
    if not cleaned or len(cleaned) > 160:
        return None
    match = _ITEM_RE.match(cleaned)
    if not match:
        return None
    low = cleaned.lower()
    if any(piece in low for piece in _REJECT_IN_HEADER):
        return None
    return match.group(1).upper()


def is_furniture(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return True
    if _FURNITURE_NUM.fullmatch(cleaned):
        return True
    if _FURNITURE_PART.fullmatch(cleaned):
        return True
    if re.fullmatch(r"table of contents", cleaned, re.I):
        return True
    if re.search(r"form\s+10-k", cleaned, re.I) and len(cleaned) < 90:
        return True
    return False


def _strip_invisible(html: str) -> str:
    html = re.sub(r"(?is)<ix:header\b.*?</ix:header>", " ", html)
    html = re.sub(r"(?is)<ix:hidden\b.*?</ix:hidden>", " ", html)
    return html


def _in_bold(node) -> bool:
    for parent in getattr(node, "parents", []):
        name = getattr(parent, "name", None) or ""
        if name in {"b", "strong", "h1", "h2", "h3", "h4"}:
            return True
        style = (parent.get("style") or "").replace(" ", "").lower() if hasattr(parent, "get") else ""
        if any(token in style for token in ("font-weight:bold", "font-weight:700", "font-weight:600", "font-weight:800")):
            return True
        if name in {"body", "html", "[document]"}:
            break
    return False


def _bold_coverage(el) -> float:
    total = 0
    bold = 0
    for string in el.strings:
        piece = string.strip()
        if not piece:
            continue
        total += len(piece)
        if _in_bold(string):
            bold += len(piece)
    if total == 0:
        return 0.0
    return bold / total


def _leading_bold(el, full: str) -> str | None:
    chunks: list[str] = []
    started = False
    for string in el.strings:
        piece = string.strip()
        if not piece:
            continue
        if _in_bold(string):
            chunks.append(piece)
            started = True
            continue
        if not started:
            continue
        break
    if not chunks:
        return None
    title = clean_text(" ".join(chunks))
    if title == full:
        return None
    if len(title) < 15 or len(title) > 300:
        return None
    if not full.startswith(title):
        return None
    return title


def _blocks(html: str) -> list[Block]:
    soup = BeautifulSoup(_strip_invisible(html), "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    for el in list(soup.find_all(style=True)):
        # Decompose can detach a later element in this list. Skip those.
        if getattr(el, "attrs", None) is None or el.parent is None:
            continue
        style = el.attrs.get("style") or ""
        if re.search(r"display\s*:\s*none", style, re.I):
            el.decompose()
    blocks: list[Block] = []
    for el in soup.find_all(["div", "p", "h1", "h2", "h3", "h4", "li", "td"]):
        if el.find(["div", "p", "table", "h1", "h2", "h3", "h4", "li"]):
            continue
        for br in el.find_all("br"):
            br.replace_with("\n")
        raw = el.get_text(" ", strip=False)
        coverage = _bold_coverage(el)
        for line in raw.split("\n"):
            text = clean_text(line)
            if not text or is_furniture(text):
                continue
            # Some filings split a capital letter off a heading: "RIS K FACTORS".
            if len(text) <= 160:
                text = re.sub(r"\b([A-Z]{2,})\s+([A-Z])\b", r"\1\2", text)
            lead = _leading_bold(el, text) if coverage < 0.8 else None
            bold = coverage >= 0.8 or el.name in {"h1", "h2", "h3", "h4"}
            blocks.append(Block(text=text, bold=bold, lead_title=lead))
    return _merge_split_headers(blocks)


def _merge_split_headers(blocks: list[Block]) -> list[Block]:
    merged: list[Block] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        number = item_number(block.text)
        if number and len(clean_text(block.text)) <= 12 and i + 1 < len(blocks):
            nxt = blocks[i + 1]
            nxt_text = clean_text(nxt.text)
            if (
                0 < len(nxt_text) <= 80
                and "." not in nxt_text
                and item_number(nxt_text) is None
            ):
                merged.append(
                    Block(
                        text=clean_text(block.text + " " + nxt_text),
                        bold=block.bold or nxt.bold,
                    )
                )
                i += 2
                continue
        merged.append(block)
        i += 1
    return merged


@dataclass
class _Span:
    start: int
    end: int
    header: str
    end_header: str | None
    end_item: str | None
    length: int
    titled: bool


def _spans(blocks: list[Block], item: str) -> list[_Span]:
    headers: list[tuple[int, str, str]] = []
    for i, block in enumerate(blocks):
        number = item_number(block.text)
        if number:
            headers.append((i, number, block.text))
    spans: list[_Span] = []
    canonical = CANONICAL[item]
    for i, number, header in headers:
        if number != item:
            continue
        # A repeated "Item 1A" running header is the same item, not the end.
        end = next((h for h in headers if h[0] > i and h[1] != number), None)
        end_i = end[0] if end else len(blocks)
        length = sum(len(blocks[j].text) + 1 for j in range(i, end_i))
        spans.append(
            _Span(
                start=i,
                end=end_i,
                header=header,
                end_header=end[2] if end else None,
                end_item=end[1] if end else None,
                length=length,
                titled=canonical in header.lower(),
            )
        )
    return spans


def _choose_span(spans: list[_Span], item: str) -> _Span | None:
    """Pick the body span, not the table-of-contents line.

    A short heading that contains the section title must not beat a long span.
    A titled span wins only when it is nearly as long as the longest bounded span.
    """
    if not spans:
        return None
    expected = EXPECTED_NEXT[item]
    bounded = [s for s in spans if s.end_item in expected] or spans
    best_len = max(s.length for s in bounded)
    titled = [s for s in bounded if s.titled and s.length >= best_len * 0.8]
    pool = titled or bounded
    return max(pool, key=lambda s: s.length)


def _is_pure_heading(block: Block) -> bool:
    return (
        block.bold
        and not block.lead_title
        and len(block.text) <= HEADING_MAX
        and item_number(block.text) is None
    )


def _heading_passages(blocks: list[Block]) -> list[Passage]:
    """Group a heading with the blocks that follow it.

    The category label is stored on the passage and included in the text only
    for the first risk under that label, where it actually sits in the filing.
    Later risks in the same category do not have the label copied onto them,
    because that copy would not be a contiguous quote.
    """
    passages: list[Passage] = []
    part: str | None = None
    title: str | None = None
    part_for: str | None = None
    chunks: list[str] = []
    pending_part_text: str | None = None

    def flush() -> None:
        nonlocal title, part_for, chunks
        text = clean_text(" ".join(chunks))
        if len(text) >= MIN_PASSAGE:
            passages.append(Passage(text=text, title=title, part=part_for))
        title = None
        part_for = None
        chunks = []

    def start(block: Block, block_title: str) -> None:
        nonlocal title, part_for, chunks, pending_part_text
        flush()
        title = block_title
        part_for = part
        chunks = []
        if pending_part_text:
            chunks.append(pending_part_text)
            pending_part_text = None
        chunks.append(block.text)

    for idx, block in enumerate(blocks):
        if block.lead_title:
            start(block, block.lead_title)
            continue
        if _is_pure_heading(block):
            nxt = blocks[idx + 1] if idx + 1 < len(blocks) else None
            next_is_heading = bool(nxt and (_is_pure_heading(nxt) or nxt.lead_title))
            if next_is_heading and len(block.text) <= 80 and not block.text.endswith("."):
                flush()
                part = block.text
                pending_part_text = block.text
                continue
            start(block, block.text)
            continue
        if not chunks:
            chunks = [block.text]
            part_for = part
        else:
            chunks.append(block.text)
    flush()
    return passages


def _paragraph_passages(blocks: list[Block]) -> list[Passage]:
    """One passage per HTML block. Short fragments are joined to the previous block.

    This follows the filing's own paragraph breaks, which are more stable
    across years than a fixed character window.
    """
    passages: list[Passage] = []
    for block in blocks:
        text = clean_text(block.text)
        if not text:
            continue
        if len(text) < MIN_PASSAGE and passages:
            prev = passages[-1]
            passages[-1] = Passage(
                text=clean_text(prev.text + " " + text),
                title=prev.title,
                part=prev.part,
            )
            continue
        if len(text) >= MIN_PASSAGE:
            passages.append(Passage(text=text))
    return passages


def _segment(blocks: list[Block]) -> tuple[list[Passage], str]:
    headings = [b for b in blocks if _is_pure_heading(b) or b.lead_title]
    if len(headings) < 3 and len(blocks) > 12:
        return _paragraph_passages(blocks), "paragraphs"
    if len(headings) < 1:
        return _paragraph_passages(blocks), "paragraphs"
    return _heading_passages(blocks), "headings"


def extract_item(
    html: str,
    item: str,
    *,
    min_chars: int | None = None,
    max_chars: int | None = None,
    blocks: list[Block] | None = None,
) -> Section:
    item = item.upper()
    if item not in CANONICAL:
        raise ValueError(item)
    min_chars = DEFAULT_MIN[item] if min_chars is None else min_chars
    max_chars = DEFAULT_MAX[item] if max_chars is None else max_chars
    blocks = blocks if blocks is not None else _blocks(html)
    spans = _spans(blocks, item)
    if not spans:
        return Section(item=item, status="missing", notes=["No item heading was found."])
    chosen = _choose_span(spans, item)
    assert chosen is not None
    span_blocks = blocks[chosen.start : chosen.end]
    kept = []
    opening: list[Block] = []
    opened = False
    for block in span_blocks:
        if is_furniture(block.text):
            continue
        number = item_number(block.text)
        if not opened and number == item:
            opening.append(block)
            continue
        if opening and not opened:
            opened = True
            kept.append(max(opening, key=lambda b: len(b.text)))
        if number == item:
            continue
        kept.append(block)
    if opening and not opened:
        kept.insert(0, max(opening, key=lambda b: len(b.text)))
    text = clean_text(" ".join(b.text for b in kept))
    header_text = kept[0].text if kept else chosen.header
    body = [b for b in kept[1:] if item_number(b.text) is None] if kept else []
    passages, segmentation = _segment(body)
    notes: list[str] = []
    status = "ok"
    if chosen.end_item not in EXPECTED_NEXT[item]:
        status = "incomplete"
        notes.append(
            f"The extract ends at {chosen.end_header or 'the end of the file'}, "
            "which is not the next item heading this tool expects. "
            "No absence conclusion is drawn from this section."
        )
    if len(text) < min_chars:
        status = "incomplete" if chosen.titled or text else "missing"
        if text:
            notes.append(
                f"The extract is {len(text)} characters, under the {min_chars} character minimum. "
                "No absence conclusion is drawn."
            )
        else:
            notes.append("The item heading was found but no section text followed it.")
            status = "missing"
    if len(text) > max_chars:
        status = "incomplete"
        notes.append(
            f"The extract is {len(text)} characters, over the {max_chars} character maximum. "
            "It may include text past the item. No absence conclusion is drawn."
        )
    if header_text and CANONICAL[item] not in header_text.lower():
        notes.append(
            "The heading line does not contain the usual section title "
            f"({CANONICAL[item]})."
        )
    return Section(
        item=item,
        status=status,
        header=header_text,
        end_header=chosen.end_header,
        text=text,
        passages=passages if status == "ok" else passages,
        segmentation=segmentation if text else "none",
        notes=notes,
    )


def extract_sections(
    html: str,
    *,
    min_chars: dict[str, int] | None = None,
    max_chars: dict[str, int] | None = None,
) -> dict[str, Section]:
    blocks = _blocks(html)
    out = {}
    for item in ("1A", "9A"):
        out[item] = extract_item(
            html,
            item,
            min_chars=None if not min_chars else min_chars.get(item),
            max_chars=None if not max_chars else max_chars.get(item),
            blocks=blocks,
        )
    return out
