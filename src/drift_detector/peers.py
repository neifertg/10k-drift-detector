"""Peer comparison within a sector.

Each column is that company's own year-over-year change. Period-end
dates are stated. A passage is "not aligned" only against the peer's
extracted Item 1A for the two years in the peer's column. If either
extract is incomplete, the comparison says so and stops.
"""

from __future__ import annotations

from datetime import datetime

from drift_detector.flags import SIGNAL_TITLES
from drift_detector.models import (
    CompanyResult,
    PeerPassage,
    PeerResult,
    PeerSignal,
    Section,
)
from drift_detector.textutil import (
    ALIGN_THRESHOLD,
    CLOSEST_QUOTE_THRESHOLD,
    UNCERTAIN_THRESHOLD,
    content_overlap,
    excerpt,
)


def _days(left: str, right: str) -> int:
    a = datetime.strptime(left, "%Y-%m-%d")
    b = datetime.strptime(right, "%Y-%m-%d")
    return abs((a - b).days)


def period_statement(
    left_name: str,
    left_current: str,
    left_prior: str | None,
    right_name: str,
    right_current: str,
    right_prior: str | None,
) -> tuple[str, str, int | None]:
    if not left_prior or not right_prior:
        missing = []
        if not left_prior:
            missing.append(left_name)
        if not right_prior:
            missing.append(right_name)
        who = " and ".join(missing)
        return (
            "unavailable",
            (
                f"{who} does not have two consecutive 10-Ks in this run, "
                "so a year-over-year peer comparison is not supported."
            ),
            None,
        )
    gap = _days(left_current, right_current)
    transition = (
        f"{left_name} is the change from {left_prior} to {left_current}. "
        f"{right_name} is the change from {right_prior} to {right_current}."
    )
    if left_current == right_current:
        return (
            "same",
            (
                f"Both latest 10-Ks have a period end of {left_current}. "
                f"Each column is that company's own prior-year change. {transition}"
            ),
            0,
        )
    if gap <= 120:
        kind = "close"
        lead = (
            f"The latest period ends are {gap} days apart "
            f"({left_name} {left_current}; {right_name} {right_current}). "
            "They are not the same fiscal period."
        )
    else:
        kind = "different"
        lead = (
            f"The latest period ends are {gap} days apart "
            f"({left_name} {left_current}; {right_name} {right_current}). "
            "This is not a same-fiscal-period comparison."
        )
    return kind, f"{lead} {transition}", gap


def _latest_pair(company: CompanyResult, item: str):
    pairs = [
        c
        for c in company.comparisons
        if c.item == item
    ]
    if not pairs:
        return None
    return max(pairs, key=lambda c: c.current_report_date)


def _section(company: CompanyResult, report_date: str, item: str) -> Section | None:
    for filing in company.filings:
        if filing.report_date == report_date:
            return filing.sections.get(item)
    return None


def _passages_for_pair(company: CompanyResult, comparison) -> list[tuple[str, str]]:
    """Return (report_date, passage text) for both years when both extracts are ok."""
    out = []
    for report_date in (comparison.prior_report_date, comparison.current_report_date):
        section = _section(company, report_date, comparison.item)
        if section is None or section.status != "ok":
            return []
        for passage in section.passages:
            out.append((report_date, passage.text))
    return out


def _closest(text: str, pool: list[tuple[str, str]]):
    best = 0.0
    best_text = None
    best_date = None
    for report_date, other in pool:
        score = content_overlap(text, other)
        if score > best:
            best = score
            best_text = other
            best_date = report_date
    return best, best_text, best_date


def _peer_passage(row, company: CompanyResult, pool, document_for) -> PeerPassage | None:
    text = row.current_text if row.kind == "added" else row.prior_text
    if not text:
        return None
    report_date = row.current_report_date if row.kind == "added" else row.prior_report_date
    url = row.current_document_url if row.kind == "added" else row.prior_document_url
    passage_url = row.current_url if row.kind == "added" else row.prior_url
    accession = row.current_accession if row.kind == "added" else row.prior_accession
    if not text or not url or not report_date:
        return None
    # Confirm the quote is still in the extract.
    section = _section(company, report_date, "1A")
    if section is None or text not in section.text:
        return None
    score, closest_text, closest_date = _closest(text, pool) if pool else (0.0, None, None)
    shown, truncated = excerpt(text, 900)
    closest_shown = None
    closest_url = None
    closest_doc = None
    if closest_text and score >= CLOSEST_QUOTE_THRESHOLD and closest_date:
        closest_shown, _ = excerpt(closest_text, 500)
        closest_doc = document_for(closest_date)
        if closest_doc:
            from drift_detector.textutil import passage_fragment

            closest_url = passage_fragment(closest_doc, closest_shown)
    return PeerPassage(
        label=row.label,
        text=shown,
        report_date=report_date,
        accession=accession or "",
        document_url=url,
        passage_url=passage_url or url,
        truncated=truncated,
        closest_text=closest_shown,
        closest_similarity=score if pool else None,
        closest_url=closest_url,
        closest_report_date=closest_date,
        closest_document_url=closest_doc,
    )


def _bucket(rows, company, pool, document_for, kind: str):
    distinct = []
    shared = 0
    uncertain = []
    for row in rows:
        if row.kind != kind:
            continue
        passage = _peer_passage(row, company, pool, document_for)
        if passage is None:
            continue
        score = passage.closest_similarity or 0.0
        if score >= ALIGN_THRESHOLD:
            shared += 1
            continue
        if score >= UNCERTAIN_THRESHOLD:
            uncertain.append(passage)
            continue
        distinct.append(passage)
    return distinct, shared, uncertain


def _signals(left: CompanyResult, right: CompanyResult) -> list[PeerSignal]:
    left_cmp = _latest_pair(left, "9A")
    right_cmp = _latest_pair(right, "9A")
    left_flags = {f.signal: f for f in (left_cmp.flags if left_cmp else [])}
    right_flags = {f.signal: f for f in (right_cmp.flags if right_cmp else [])}
    out = []
    for signal, title in SIGNAL_TITLES.items():
        lf = left_flags.get(signal)
        rf = right_flags.get(signal)
        out.append(
            PeerSignal(
                signal=signal,
                title=title,
                left_statement=lf.statement if lf else "No Item 9A comparison was produced for this company.",
                right_statement=rf.statement if rf else "No Item 9A comparison was produced for this company.",
                left_quotes=list(lf.quotes) if lf else [],
                right_quotes=list(rf.quotes) if rf else [],
            )
        )
    return out


def build_peer(left: CompanyResult, right: CompanyResult) -> PeerResult:
    left_filing = left.filings[0] if left.filings else None
    right_filing = right.filings[0] if right.filings else None
    left_current = left_filing.report_date if left_filing else ""
    right_current = right_filing.report_date if right_filing else ""
    left_cmp = _latest_pair(left, "1A")
    right_cmp = _latest_pair(right, "1A")
    left_prior = left_cmp.prior_report_date if left_cmp else None
    right_prior = right_cmp.prior_report_date if right_cmp else None
    kind, statement, gap = period_statement(
        left.display_name,
        left_current or "unknown",
        left_prior,
        right.display_name,
        right_current or "unknown",
        right_prior,
    )
    if left.error or right.error:
        reason = " ".join(
            bit
            for bit in (
                f"{left.ticker}: {left.error}" if left.error else "",
                f"{right.ticker}: {right.error}" if right.error else "",
            )
            if bit
        )
        return PeerResult(
            sector=left.sector,
            left_ticker=left.ticker,
            right_ticker=right.ticker,
            supported=False,
            reason=reason,
            period_statement=statement,
            period_kind="unavailable",
            gap_days=gap,
            index_line=f"Peer comparison is not supported. {reason}",
        )
    if kind == "unavailable" or left_cmp is None or right_cmp is None:
        reason = statement
        return PeerResult(
            sector=left.sector,
            left_ticker=left.ticker,
            right_ticker=right.ticker,
            supported=False,
            reason=reason,
            period_statement=statement,
            period_kind=kind,
            gap_days=gap,
            signals=_signals(left, right),
            index_line=f"Peer comparison of risk factors is not supported. {reason}",
        )
    if not left_cmp.supported or not right_cmp.supported:
        bits = []
        if not left_cmp.supported:
            bits.append(f"{left.ticker} Item 1A: {left_cmp.reason}")
        if not right_cmp.supported:
            bits.append(f"{right.ticker} Item 1A: {right_cmp.reason}")
        reason = " ".join(bits)
        return PeerResult(
            sector=left.sector,
            left_ticker=left.ticker,
            right_ticker=right.ticker,
            supported=False,
            reason=reason,
            period_statement=statement,
            period_kind=kind,
            gap_days=gap,
            signals=_signals(left, right),
            index_line=f"Peer comparison of risk-factor passages is not supported. {reason}",
        )

    def docs(company: CompanyResult):
        return {f.report_date: f.document_url for f in company.filings}

    left_docs = docs(left)
    right_docs = docs(right)
    right_pool = _passages_for_pair(right, right_cmp)
    left_pool = _passages_for_pair(left, left_cmp)
    left_added, shared_left, unc_left = _bucket(
        left_cmp.rows, left, right_pool, lambda d: right_docs.get(d, ""), "added"
    )
    right_added, shared_right, unc_right = _bucket(
        right_cmp.rows, right, left_pool, lambda d: left_docs.get(d, ""), "added"
    )
    left_removed, _, _ = _bucket(
        left_cmp.rows, left, right_pool, lambda d: right_docs.get(d, ""), "removed"
    )
    right_removed, _, _ = _bucket(
        right_cmp.rows, right, left_pool, lambda d: left_docs.get(d, ""), "removed"
    )
    # Shared counts a passage that aligns with the peer's text, not with the peer's additions.
    shared = shared_left + shared_right
    def _n(count: int) -> str:
        return "1 passage" if count == 1 else f"{count} passages"

    shared_line = (
        "1 added passage aligned with the peer and is not called distinct"
        if shared == 1
        else f"{shared} added passages aligned with the peer and are not called distinct"
    )
    index = (
        f"{left.ticker} added {_n(len(left_added))} in Item 1A with no aligned passage in "
        f"{right.ticker}'s extract; {right.ticker} added {len(right_added)}. "
        f"{shared_line}. "
        f"Latest period ends: {left_current} and {right_current}"
        + (f" ({gap} days apart)." if gap else ".")
    )
    return PeerResult(
        sector=left.sector,
        left_ticker=left.ticker,
        right_ticker=right.ticker,
        supported=True,
        reason=None,
        period_statement=statement,
        period_kind=kind,
        gap_days=gap,
        signals=_signals(left, right),
        left_only_added=left_added,
        right_only_added=right_added,
        left_only_removed=left_removed,
        right_only_removed=right_removed,
        uncertain=unc_left + unc_right,
        shared_added=shared,
        index_line=index,
    )
