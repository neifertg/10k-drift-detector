"""Align passages from two consecutive filings.

A passage is matched to at most one passage in the other year.
Matching uses content-token overlap. Date-only edits are labeled
cosmetic and are not listed as wording changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from drift_detector.models import Passage
from drift_detector.textutil import (
    ALIGN_THRESHOLD,
    UNCERTAIN_THRESHOLD,
    content_overlap,
    is_cosmetic,
    norm,
)


@dataclass
class Match:
    kind: str
    similarity: float | None
    prior: Passage | None = None
    current: Passage | None = None
    closest_similarity: float | None = None
    closest: Passage | None = None


def _best(passage: Passage, others: list[Passage]) -> tuple[float, Passage | None]:
    best = 0.0
    chosen: Passage | None = None
    for other in others:
        score = content_overlap(passage.text, other.text)
        if score > best:
            best = score
            chosen = other
    return best, chosen


def align_passages(prior: list[Passage], current: list[Passage]) -> list[Match]:
    pairs: list[tuple[float, int, int]] = []
    for i, left in enumerate(prior):
        for j, right in enumerate(current):
            score = content_overlap(left.text, right.text)
            if score >= ALIGN_THRESHOLD:
                pairs.append((score, i, j))
    pairs.sort(key=lambda row: row[0], reverse=True)
    used_prior: set[int] = set()
    used_current: set[int] = set()
    matched: dict[int, tuple[int, float]] = {}
    for score, i, j in pairs:
        if i in used_prior or j in used_current:
            continue
        used_prior.add(i)
        used_current.add(j)
        matched[i] = (j, score)

    rows: list[Match] = []
    for i, left in enumerate(prior):
        if i in matched:
            j, score = matched[i]
            right = current[j]
            if norm(left.text) == norm(right.text):
                kind = "unchanged"
            elif is_cosmetic(left.text, right.text):
                kind = "cosmetic"
            else:
                kind = "changed"
            rows.append(Match(kind=kind, similarity=score, prior=left, current=right))
            continue
        rows.append(_leftover(left, current, "removed"))
    for j, right in enumerate(current):
        if j in used_current:
            continue
        rows.append(_leftover(right, prior, "added"))
    return rows


def _leftover(passage: Passage, others: list[Passage], absent_kind: str) -> Match:
    """Classify a passage that one-to-one matching did not pair.

    The closest passage can still score at or above the alignment threshold
    when that passage was already paired with something else. That leftover
    is not added or removed, and it is not in the 0.30-to-0.45 band.
    """
    best, other = _best(passage, others)
    prior = passage if absent_kind == "removed" else None
    current = passage if absent_kind == "added" else None
    if best >= ALIGN_THRESHOLD:
        kind = "already_paired"
    elif best >= UNCERTAIN_THRESHOLD:
        kind = "uncertain"
    else:
        kind = absent_kind
    return Match(
        kind=kind,
        similarity=best if other else None,
        prior=prior,
        current=current,
        closest_similarity=best if other else None,
        closest=other,
    )


def count_kinds(rows: list[Match]) -> dict[str, int]:
    counts = {
        k: 0
        for k in (
            "unchanged",
            "cosmetic",
            "changed",
            "added",
            "removed",
            "uncertain",
            "already_paired",
        )
    }
    for row in rows:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return counts
