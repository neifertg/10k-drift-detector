"""Text normalization, token overlap, and date-only change checks.

Overlap is a count of shared content words. It is not a model score.
"""

from __future__ import annotations

import re
from collections import Counter

# Function words. Negations stay out of this set so "not" can move the match
# and can never be dismissed as cosmetic.
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "of", "to",
    "in", "on", "for", "with", "by", "at", "from", "as", "into", "over",
    "under", "after", "before", "between", "about", "than", "that", "this",
    "these", "those", "it", "its", "their", "our", "we", "you", "they",
    "them", "his", "her", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "such", "including", "include",
    "includes", "other", "also", "any", "all", "each", "per", "via", "within",
    "across", "among", "both", "further", "there", "here", "when", "where",
    "which", "who", "whom", "whose", "what", "how", "because", "while",
    "during", "through", "up", "down", "out", "only", "own", "same", "so",
    "too", "very", "just", "more", "most", "some", "those", "these",
}

MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}

# A matched pair at or above this content-token overlap is the same passage.
ALIGN_THRESHOLD = 0.45
# Below this, an unmatched passage is treated as added or removed.
# Between the two thresholds the passage is left unclassified.
UNCERTAIN_THRESHOLD = 0.30
# Peer "no aligned passage" uses the same uncertain floor.
# A closest passage is quoted only when overlap is at least this high;
# below it, the nearest passage is usually a different topic.
CLOSEST_QUOTE_THRESHOLD = 0.15

_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"(19|20)\d{2}")
_ABBREV = re.compile(
    r"(?:U\.S|Inc|Corp|Ltd|Mr|Ms|Mrs|Dr|No|Jr|Sr|vs|e\.g|i\.e|Co|Fig)\.$"
)


def clean_text(text: str) -> str:
    """Collapse whitespace and drop characters that split words in HTML."""
    if not text:
        return ""
    text = (
        text.replace("\u00ad", "")
        .replace("\xa0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    return _WS.sub(" ", text).strip()


def norm(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    text = clean_text(text).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return _WS.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(norm(text))


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 1]


def jaccard(left: list[str], right: list[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def content_overlap(left: str, right: str) -> float:
    return jaccard(content_tokens(left), content_tokens(right))


def _ignorable(tok: str, index: int, toks: list[str]) -> bool:
    if _YEAR.fullmatch(tok):
        return True
    if tok in MONTHS:
        return True
    if re.fullmatch(r"\d{1,2}", tok) and 1 <= int(tok) <= 31:
        window = toks[max(0, index - 3) : index + 4]
        if any(part in MONTHS for part in window):
            return True
    return False


def _extras(source: str, other: str) -> list[tuple[str, int, list[str]]]:
    src = tokens(source)
    need = Counter(src) - Counter(tokens(other))
    found: Counter[str] = Counter()
    extras = []
    for i, tok in enumerate(src):
        if found[tok] < need[tok]:
            extras.append((tok, i, src))
            found[tok] += 1
    return extras


def is_cosmetic(prior: str, current: str) -> bool:
    """True when the only token changes are years, month names, or a day next to a month.

    Integers 1-31 are treated as days only when a month name is within three
    tokens. Other number changes are kept.
    """
    if norm(prior) == norm(current):
        return False
    extras = _extras(prior, current) + _extras(current, prior)
    if not extras:
        return False
    return all(_ignorable(tok, idx, toks) for tok, idx, toks in extras)


def sentences(text: str) -> list[str]:
    """Split on sentence boundaries. Rejoin a split caused by a short abbreviation."""
    raw = clean_text(text)
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", raw)
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if merged and _ABBREV.search(merged[-1]):
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)
    return merged


def conclusion_key(text: str) -> str:
    """Compare sentences with years and other numbers folded together."""
    text = norm(text)
    text = re.sub(r"\d+", "#", text)
    return _WS.sub(" ", text).strip()


def excerpt(text: str, limit: int = 700) -> tuple[str, bool]:
    """Exact prefix, cut on a word boundary when the text is longer than limit."""
    text = clean_text(text)
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut, True


def passage_fragment(document_url: str, quote: str) -> str:
    """Browser text-fragment link. The words are taken from the quote in order."""
    words = re.findall(r"[A-Za-z0-9]+", quote)[:8]
    if len(words) < 4:
        return document_url
    from urllib.parse import quote

    encoded = "%20".join(quote(word, safe="") for word in words)
    return f"{document_url}#:~:text={encoded}"
