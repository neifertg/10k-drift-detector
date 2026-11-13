"""Static HTML report. Every finding is a quoted filing sentence plus a link."""

from __future__ import annotations

import difflib
import html
from pathlib import Path

from drift_detector import __version__
from drift_detector.bundle import sector_slug
from drift_detector.flags import SIGNAL_TITLES
from drift_detector.models import AlignedRow, CompanyResult, Flag, PeerPassage, PeerResult, Quote
from drift_detector.verify import BriefSet, Theme
from drift_detector.textutil import (
    ALIGN_THRESHOLD,
    UNCERTAIN_THRESHOLD,
    excerpt,
    norm,
    sentences,
)

_CSS = Path(__file__).with_name("report.css").read_text()


def e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _page(title: str, body: str, depth: int) -> str:
    prefix = "../" * depth
    crumb = ""
    if depth:
        crumb = f'<p class="crumb"><a href="{prefix}index.html">Audit plan</a></p>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="{prefix}style.css">
</head>
<body>
{crumb}
{body}
</body>
</html>
"""


def _quote(q: Quote) -> str:
    note = " The quote is an exact prefix; the link opens the filing." if q.truncated else ""
    return (
        "<blockquote>"
        f"<p>{e(q.text)}</p>"
        f'<p class="src">Period ended {e(q.report_date)}, Item {e(q.item)}. '
        f'Accession <span class="acc">{e(q.accession)}</span>. '
        f'<a href="{e(q.document_url)}">Filing</a>. '
        f'<a href="{e(q.passage_url)}">Passage in the filing</a>.{e(note)}</p>'
        "</blockquote>"
    )


def _word_diff(prior: str, current: str) -> tuple[str, str]:
    left_words = (prior or "").split()
    right_words = (current or "").split()
    matcher = difflib.SequenceMatcher(a=left_words, b=right_words)
    left: list[str] = []
    right: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            chunk = e(" ".join(left_words[i1:i2]))
            left.append(chunk)
            right.append(e(" ".join(right_words[j1:j2])))
        elif tag == "delete":
            left.append(f"<del>{e(' '.join(left_words[i1:i2]))}</del>")
        elif tag == "insert":
            right.append(f"<ins>{e(' '.join(right_words[j1:j2]))}</ins>")
        else:
            left.append(f"<del>{e(' '.join(left_words[i1:i2]))}</del>")
            right.append(f"<ins>{e(' '.join(right_words[j1:j2]))}</ins>")
    return " ".join(left), " ".join(right)


def _focus(prior: str | None, current: str | None) -> tuple[str, str, bool]:
    """Return display text for each side, and whether unchanged sentences were omitted."""
    if prior and current:
        prior_s = sentences(prior)
        current_s = sentences(current)
        kept_prior = [s for s in prior_s if not any(norm(s) == norm(o) for o in current_s)]
        kept_current = [s for s in current_s if not any(norm(s) == norm(o) for o in prior_s)]
        if kept_prior or kept_current:
            left, left_cut = excerpt(" ".join(kept_prior), 900)
            right, right_cut = excerpt(" ".join(kept_current), 900)
            return left, right, True or left_cut or right_cut
    left, _ = excerpt(prior or "", 900)
    right, _ = excerpt(current or "", 900)
    return left, right, False


def _kind_label(kind: str) -> str:
    return {
        "already_paired": "already paired",
        "uncertain": "unclassified",
        "changed": "changed",
        "added": "added",
        "removed": "removed",
    }.get(kind, kind)


def _closest_explanation(similarity: float) -> str:
    """Say why a leftover passage was not called added, removed, or changed.

    A score at or above the alignment threshold is still a leftover when the
    closest passage was already used in a one-to-one pair.
    """
    if similarity >= ALIGN_THRESHOLD:
        return (
            f"Closest passage in the other year overlaps at {similarity:.2f}, "
            f"which is at or above {ALIGN_THRESHOLD:.2f}. "
            "That passage was already paired under one-to-one matching, "
            "so this leftover is not called added, removed, or changed."
        )
    return (
        f"Closest passage in the other year overlaps at {similarity:.2f}, "
        f"which is below {ALIGN_THRESHOLD:.2f} and at or above {UNCERTAIN_THRESHOLD:.2f}, "
        "so this is not called added, removed, or changed."
    )


def _side_by_side(row: AlignedRow) -> str:
    left_text, right_text, omitted = _focus(row.prior_text, row.current_text)
    if row.kind == "changed" and left_text and right_text:
        left_html, right_html = _word_diff(left_text, right_text)
    else:
        left_html = e(left_text) if left_text else "<p>No aligned passage in this filing.</p>"
        right_html = e(right_text) if right_text else "<p>No aligned passage in this filing.</p>"
        if left_text:
            left_html = f"<p>{left_html}</p>"
        if right_text:
            right_html = f"<p>{right_html}</p>"
    if row.kind == "changed" and left_text and right_text:
        left_html = f"<p>{left_html}</p>"
        right_html = f"<p>{right_html}</p>"
    note = ""
    if omitted and row.kind == "changed":
        note = '<p class="muted">Unchanged sentences in the passage are omitted. Struck words are only in the earlier filing. Underlined words are only in the later filing.</p>'
    elif row.kind == "changed":
        note = '<p class="muted">Struck words are only in the earlier filing. Underlined words are only in the later filing.</p>'
    sim = ""
    if row.similarity is not None:
        sim = f'<p class="src">Content-token overlap {row.similarity:.2f}. Alignment threshold {ALIGN_THRESHOLD:.2f}.</p>'
    left_link = ""
    right_link = ""
    if row.prior_url:
        left_link = f'<p class="src"><a href="{e(row.prior_url)}">Passage in the {e(row.prior_report_date)} filing</a></p>'
    if row.current_url:
        right_link = f'<p class="src"><a href="{e(row.current_url)}">Passage in the {e(row.current_report_date)} filing</a></p>'
    extra = ""
    if row.kind in {"uncertain", "already_paired"} and row.closest_similarity is not None:
        extra = f"<p class=\"src\">{e(_closest_explanation(row.closest_similarity))}</p>"
        if row.closest_text:
            shown, cut = excerpt(row.closest_text, 400)
            extra += f"<blockquote><p>{e(shown)}</p></blockquote>"
            if cut:
                extra += '<p class="src">Closest-passage quote truncated to an exact prefix.</p>'
    label = row.label or "Passage"
    return (
        f"<h4>{e(_kind_label(row.kind))} — {e(label)}</h4>"
        f"{note}{sim}"
        '<div class="pair">'
        f'<div class="col"><p class="tag">Period ended {e(row.prior_report_date or "—")}</p>{left_html}{left_link}</div>'
        f'<div class="col"><p class="tag">Period ended {e(row.current_report_date or "—")}</p>{right_html}{right_link}</div>'
        "</div>"
        f"{extra}"
    )


def _flag_block(flag: Flag, suffix: str) -> str:
    quotes = "\n".join(_quote(q) for q in flag.quotes)
    title = SIGNAL_TITLES.get(flag.signal, flag.signal)
    return (
        f'<section id="{e(flag.signal)}-{e(suffix)}">'
        f"<h3>{e(title)}</h3>"
        f"<p>{e(flag.statement)}</p>"
        f"{quotes}"
        "</section>"
    )


def _filing_table(company: CompanyResult) -> str:
    rows = []
    safe_notes = []
    for filing in company.filings:
        rows.append(
            "<tr>"
            f"<td>{e(filing.report_date)}</td>"
            f"<td>{e(filing.filing_date)}</td>"
            f'<td class="acc"><a href="{e(filing.index_url)}">{e(filing.accession)}</a></td>'
            f'<td><a href="{e(filing.document_url)}">{e(filing.primary_document)}</a></td>'
            f'<td class="acc">{e(filing.sha256[:16])}…</td>'
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='5'>No 10-K was retrieved.</td></tr>"
    for filing in company.filings:
        for amend in filing.amendments:
            safe_notes.append(
                "<p>A 10-K/A for the period ended "
                f"{e(filing.report_date)} was filed on {e(amend.filing_date)} "
                f'(accession <span class="acc">{e(amend.accession)}</span>). '
                "It is listed and is not included in the comparison. "
                f'<a href="{e(amend.document_url)}">Amendment filing</a>.</p>'
            )
        for other in filing.other_10k:
            safe_notes.append(
                "<p>Another 10-K for the period ended "
                f"{e(filing.report_date)} was filed on {e(other.filing_date)} "
                f'(accession <span class="acc">{e(other.accession)}</span>). '
                "The comparison uses the earliest 10-K for that period end.</p>"
            )
    if company.filings and not any(f.amendments for f in company.filings):
        safe_notes.append("<p>No 10-K/A for these period ends was in the EDGAR submissions retrieved for this run.</p>")
    return (
        "<table>"
        "<caption>10-Ks used, newest first. The short hash is the first 16 characters of the SHA-256 of the downloaded file.</caption>"
        "<thead><tr><th>Period ended</th><th>Filed</th><th>Accession</th><th>Document</th><th>SHA-256 prefix</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
        + "\n".join(safe_notes)
    )


def _comparison_section(cmp, company: CompanyResult) -> str:
    suffix = cmp.current_report_date
    header = (
        f"<h2>Item {e(cmp.item)} — {e(cmp.prior_report_date)} to {e(cmp.current_report_date)}</h2>"
    )
    bounds = (
        f'<p class="src">Earlier extract: status {e(cmp.prior_status)}, '
        f"heading {e(cmp.prior_header or 'not found')}, ends at {e(cmp.prior_end or 'not found')}, "
        f"split as {e(cmp.segmentation_prior)}. "
        f"Later extract: status {e(cmp.current_status)}, "
        f"heading {e(cmp.current_header or 'not found')}, ends at {e(cmp.current_end or 'not found')}, "
        f"split as {e(cmp.segmentation_current)}."
    )
    if cmp.full_overlap is not None:
        bounds += f" Content-token overlap of the full extracts: {cmp.full_overlap:.2f}."
    bounds += "</p>"
    if not cmp.supported:
        return header + bounds + f"<p>{e(cmp.reason)}</p>" + "\n".join(
            _flag_block(f, suffix) for f in cmp.flags
        )
    counts = cmp.counts
    summary = (
        f"<p>{counts.get('added', 0)} added, {counts.get('removed', 0)} removed, "
        f"{counts.get('changed', 0)} wording changes, {counts.get('cosmetic', 0)} date-only edits, "
        f"{counts.get('unchanged', 0)} unchanged, {counts.get('uncertain', 0)} unclassified "
        f"(closest overlap from {UNCERTAIN_THRESHOLD:.2f} up to, but not including, {ALIGN_THRESHOLD:.2f}), "
        f"{counts.get('already_paired', 0)} already paired "
        f"(closest overlap at least {ALIGN_THRESHOLD:.2f}, but that passage was already matched one-to-one). "
        "Date-only edits are not listed. A wording change means the passage matched and the edit was not only a year, a month, or a day next to a month.</p>"
    )
    flags = "\n".join(_flag_block(f, suffix) for f in cmp.flags)
    blocks = []
    for kind, title in (
        ("changed", "Wording changes"),
        ("added", "Added passages"),
        ("removed", "Removed passages"),
        ("uncertain", "Unclassified passages"),
        ("already_paired", "Already paired"),
    ):
        rows = [r for r in cmp.rows if r.kind == kind]
        if not rows:
            blocks.append(f"<h3>{e(title)}</h3><p>None under the rules above.</p>")
            continue
        inner = "\n".join(_side_by_side(r) for r in rows)
        blocks.append(
            f"<details><summary>{e(title)} ({len(rows)})</summary>{inner}</details>"
        )
    return header + bounds + summary + flags + "\n".join(blocks)


def company_page(company: CompanyResult, generated_at: str) -> str:
    if company.error and not company.filings:
        body = (
            f"<h1>{e(company.display_name)} ({e(company.ticker)})</h1>"
            f"<p>{e(company.sector)}. CIK {e(company.cik)}.</p>"
            f"<p>{e(company.error)} No comparison is drawn.</p>"
        )
        return _page(company.display_name, body, 1)
    note = f"<p>{e(company.cik_note)}</p>" if company.cik_note else ""
    intro = (
        f"<h1>{e(company.display_name)} ({e(company.ticker)})</h1>"
        f"<p>{e(company.sector)}. EDGAR registrant: {e(company.edgar_name)}. "
        f'CIK {e(company.cik)}. <a href="{e(company.edgar_browse_url)}">Company filings on EDGAR</a>.</p>'
        f"{note}"
        "<p>Comparisons are this company's own consecutive 10-Ks. "
        "Item 9A flags are rules over the extracted sentences. "
        "They are not a judgment that controls are effective, and they are not a legal conclusion.</p>"
        f'<p class="src"><a href="../peers/{e(sector_slug(company.sector))}.html">{e(company.sector)} brief</a>. '
        "This page is the passage-level comparison.</p>"
    )
    parts = [intro, "<h2>Filings</h2>", _filing_table(company)]
    nines = [c for c in company.comparisons if c.item == "9A"]
    ones = [c for c in company.comparisons if c.item == "1A"]
    nines.sort(key=lambda c: c.current_report_date, reverse=True)
    ones.sort(key=lambda c: c.current_report_date, reverse=True)
    for cmp in nines + ones:
        parts.append(_comparison_section(cmp, company))
    parts.append(
        f'<footer><p>Generated {e(generated_at)} by 10k-drift-detector {e(__version__)}. '
        "Source filings are on EDGAR. This page quotes them and does not restate figures that are not in the quote. "
        "This is a wording comparison of public filings. It is not investment advice.</p></footer>"
    )
    return _page(f"{company.display_name} 10-K drift", "\n".join(parts), 1)


def _peer_quote_list(passages: list[PeerPassage], empty: str) -> str:
    if not passages:
        return f"<p>{e(empty)}</p>"
    blocks = []
    for passage in passages:
        trunc = " Quote truncated to an exact prefix." if passage.truncated else ""
        closest = ""
        if passage.closest_text:
            closest = (
                f'<p class="src">Closest passage in the peer extract '
                f"(period ended {e(passage.closest_report_date)}) "
                f"overlaps at {passage.closest_similarity:.2f}, below {ALIGN_THRESHOLD:.2f}. "
                "It is quoted so the gap can be checked. It is not treated as the same risk.</p>"
                f"<blockquote><p>{e(passage.closest_text)}</p>"
                f'<p class="src"><a href="{e(passage.closest_url or passage.closest_document_url or "")}">Peer passage</a></p>'
                "</blockquote>"
            )
        elif passage.closest_similarity is not None:
            closest = (
                f'<p class="src">Closest content-token overlap with the peer extract is '
                f"{passage.closest_similarity:.2f}. No peer sentence is quoted at that overlap "
                f"because it is under {UNCERTAIN_THRESHOLD:.2f} and is a different passage.</p>"
            )
        blocks.append(
            "<section>"
            f"<h4>{e(passage.label or 'Passage')}</h4>"
            f"<blockquote><p>{e(passage.text)}</p>"
            f'<p class="src">Period ended {e(passage.report_date)}. '
            f'Accession <span class="acc">{e(passage.accession)}</span>. '
            f'<a href="{e(passage.document_url)}">Filing</a>. '
            f'<a href="{e(passage.passage_url)}">Passage in the filing</a>.{e(trunc)}</p>'
            "</blockquote>"
            f"{closest}"
            "</section>"
        )
    return "\n".join(blocks)


def peer_page(
    peer: PeerResult,
    companies: dict[str, CompanyResult],
    generated_at: str,
    *,
    briefs: BriefSet | None = None,
    brief_errors: list[str] | None = None,
) -> str:
    left = companies[peer.left_ticker]
    right = companies[peer.right_ticker]
    sector_brief = briefs.sectors.get(peer.sector) if briefs else None
    body = [
        '<div class="brief">',
        f"<h1>{e(peer.sector)}</h1>",
        f"<p><a href=\"../company/{e(left.ticker.lower())}.html\">{e(left.display_name)}</a> "
        f"and <a href=\"../company/{e(right.ticker.lower())}.html\">{e(right.display_name)}</a>.</p>",
        f"<p>{e(peer.period_statement)}</p>",
        _sector_status(sector_brief, brief_errors, depth=1),
        "</div>",
        "<details><summary>Passage-level comparison</summary>",
    ]
    if not peer.supported:
        body.append(f"<p>Peer comparison of risk-factor passages is not supported. {e(peer.reason)}</p>")
    else:
        body.append(
            "<p>A passage is listed as distinct only when its content-token overlap with every passage "
            f"in the peer's two extracted Item 1A sections is below {UNCERTAIN_THRESHOLD:.2f}. "
            f"Overlap from {UNCERTAIN_THRESHOLD:.2f} to {ALIGN_THRESHOLD:.2f} is unclassified. "
            "This does not mean the peer has no such risk. It means the extracted text does not contain an aligned passage.</p>"
        )
        body.append(
            '<div class="pair">'
            f'<div class="col"><h2>Added by {e(left.display_name)}, not aligned in {e(right.ticker)}</h2>'
            + _peer_quote_list(peer.left_only_added, "No added passage was below the alignment floor.")
            + "</div>"
            f'<div class="col"><h2>Added by {e(right.display_name)}, not aligned in {e(left.ticker)}</h2>'
            + _peer_quote_list(peer.right_only_added, "No added passage was below the alignment floor.")
            + "</div></div>"
        )
        body.append(
            "<details><summary>Removed passages with no aligned passage in the peer's extract</summary>"
            '<div class="pair">'
            f"<div class=\"col\"><h3>{e(left.ticker)}</h3>"
            + _peer_quote_list(peer.left_only_removed, "None.")
            + f"</div><div class=\"col\"><h3>{e(right.ticker)}</h3>"
            + _peer_quote_list(peer.right_only_removed, "None.")
            + "</div></div></details>"
        )
        if peer.uncertain:
            body.append(
                f"<details><summary>Unclassified ({len(peer.uncertain)})</summary>"
                + _peer_quote_list(peer.uncertain, "None.")
                + "</details>"
            )
        else:
            body.append("<p>Unclassified peer passages: none.</p>")
        body.append(
            f"<p>{peer.shared_added} added passage(s) aligned with the peer's extract and are not listed as distinct.</p>"
        )
    if peer.signals:
        body.append("<h2>Item 9A, side by side</h2>")
        body.append(
            "<p>Each cell is that company's own latest year-over-year flag. "
            "The quotes are the filing sentences the flag used.</p>"
        )
        rows = []
        for signal in peer.signals:
            left_q = "\n".join(_quote(q) for q in signal.left_quotes[:4])
            right_q = "\n".join(_quote(q) for q in signal.right_quotes[:4])
            more_left = ""
            more_right = ""
            if len(signal.left_quotes) > 4:
                more_left = f'<p class="src">{len(signal.left_quotes) - 4} more quotes are on the company page.</p>'
            if len(signal.right_quotes) > 4:
                more_right = f'<p class="src">{len(signal.right_quotes) - 4} more quotes are on the company page.</p>'
            rows.append(
                "<tr>"
                f"<th>{e(signal.title)}</th>"
                f"<td><p>{e(signal.left_statement)}</p>{left_q}{more_left}</td>"
                f"<td><p>{e(signal.right_statement)}</p>{right_q}{more_right}</td>"
                "</tr>"
            )
        body.append(
            "<table><thead><tr><th>Signal</th>"
            f"<th>{e(left.display_name)}</th><th>{e(right.display_name)}</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    body.append("</details>")
    body.append(
        f'<footer><p>Generated {e(generated_at)} by 10k-drift-detector {e(__version__)}. '
        "This is a wording comparison of public filings. It is not investment advice.</p></footer>"
    )
    return _page(f"{peer.sector} brief", "\n".join(body), 1)


def index_page(
    companies: list[CompanyResult],
    peers: list[PeerResult],
    generated_at: str,
    *,
    model_summary: str | None,
    briefs: BriefSet | None = None,
    brief_errors: list[str] | None = None,
) -> str:
    peer_by_sector = {p.sector: p for p in peers}
    by_ticker = {company.ticker: company for company in companies}
    parts = [
        '<div class="brief">',
        "<h1>Emerging risks for the audit plan</h1>",
        "<p>Year-over-year changes in Item 1A (Risk Factors) and Item 9A (Controls and Procedures) "
        "for the six peer pairs. This page is the short list. Each company page is the passage-level evidence, "
        "behind the company name.</p>",
        "<p>A theme is a reading of what the filings added or expanded. It is shown only when every supporting quote "
        "is in the stored extract it cites. It is not a risk rating, not a conclusion that a control failed, "
        "and not investment advice.</p>",
        f'<p class="src">Filing extracts retrieved {e(generated_at)} from SEC EDGAR. '
        "Three latest 10-Ks per company where EDGAR has them. "
        "10-K/A amendments are named and not diffed.</p>",
    ]
    if brief_errors:
        parts.append(_failure_notice(brief_errors))
    elif briefs is None or (not briefs.overview and not briefs.sectors):
        parts.append(_missing_notice())
    else:
        if briefs.overview:
            parts.append("<h2>Across the peer set</h2>")
            parts.append(_theme_list(briefs.overview, by_ticker, depth=0))
        else:
            parts.append("<p>No cross-sector theme was supported by the verified drafts.</p>")
    if model_summary:
        parts.append(
            "<details><summary>Optional model summary</summary>"
            "<p>This paragraph was produced by the optional model hook. It is not evidence. "
            "The quoted passages are the evidence. The hook is off unless DRIFT_LLM_SUMMARY is set.</p>"
            f"<blockquote><p>{e(model_summary)}</p></blockquote></details>"
        )
    parts.append("<h2>Sectors</h2>")
    for peer in peers:
        members = [company for company in companies if company.sector == peer.sector]
        links = " and ".join(
            f'<a href="company/{e(company.ticker.lower())}.html">{e(company.display_name)}</a>'
            for company in members
        )
        parts.append(f"<h3><a href=\"peers/{e(sector_slug(peer.sector))}.html\">{e(peer.sector)}</a></h3>")
        if links:
            parts.append(f"<p>{links}.</p>")
        parts.append(f"<p>{e(peer.period_statement)}</p>")
    parts.append("</div>")
    parts.append("<details><summary>How a passage is compared, and what this does not do</summary>")
    parts.append(_methodology())
    parts.append("</details>")
    parts.append(
        f'<footer><p>Generated {e(generated_at)} by 10k-drift-detector {e(__version__)}. '
        "Requests used the SEC_USER_AGENT environment variable and a minimum interval of 0.2 seconds. "
        "The user agent is not printed here. "
        '<a href="https://www.sec.gov/os/accessing-edgar-data">SEC fair-access guidance</a>. '
        "This is a wording comparison of public filings. It is not investment advice.</p></footer>"
    )
    return _page("Emerging risks for the audit plan", "\n".join(parts), 0)


def _missing_notice() -> str:
    return (
        '<section class="notice">'
        "<h2>Briefs not yet generated</h2>"
        "<p>The overview and the sector pages will list emerging or growing risks here after drafts "
        "are written from the extracted changes and every quote has been checked against the stored filing extract. "
        "Nothing on this page is filled in ahead of that check.</p>"
        '<p class="src">From a checkout: <code>python -m drift_detector draft-briefs --input briefs/input --out briefs/drafts</code>, '
        "then <code>python -m drift_detector render --input briefs/input --drafts briefs/drafts --out docs</code>. "
        "The environment variable is ANTHROPIC_API_KEY. The model defaults to claude-sonnet-5-5.</p>"
        "</section>"
    )


def _failure_notice(errors: list[str]) -> str:
    items = "".join(f"<li>{e(error)}</li>" for error in errors[:30])
    more = ""
    if len(errors) > 30:
        more = f"<p class=\"src\">{len(errors) - 30} more checks failed.</p>"
    return (
        '<section class="notice">'
        "<h2>Briefs did not pass quote verification</h2>"
        "<p>Drafts are present, and they are not shown. A quote did not match the stored filing extract, "
        "or the draft did not match the required shape. The passage comparison on the company pages is unchanged.</p>"
        f"<ul>{items}</ul>{more}"
        "</section>"
    )


def _sector_status(brief, brief_errors: list[str] | None, *, depth: int) -> str:
    if brief_errors:
        return _failure_notice(brief_errors)
    if brief is None:
        return (
            '<section class="notice">'
            "<h2>Briefs not yet generated</h2>"
            "<p>The sector brief will appear here after the drafts are written and every quote has been "
            "checked against the stored filing extract. The passage-level comparison is below.</p>"
            "</section>"
        )
    parts = []
    if brief.divergence:
        parts.append("<h2>Where the peers diverge</h2>")
        parts.append(f"<p>{e(brief.divergence)}</p>")
        parts.append(_evidence_details(brief.divergence_evidence, depth))
    if brief.themes:
        parts.append("<h2>Emerging or growing risks</h2>")
        parts.append(_theme_list(brief.themes, {}, depth=depth))
    elif not brief.divergence:
        parts.append("<p>The verified draft has no theme and no divergence note.</p>")
    else:
        parts.append("<p>The verified draft did not support a separate theme for this sector.</p>")
    return "\n".join(parts)


def _theme_list(themes: list[Theme], by_ticker: dict[str, CompanyResult], *, depth: int) -> str:
    blocks = []
    seen_slugs: dict[str, int] = {}
    for theme in themes:
        slug = sector_slug(theme.title) or "theme"
        seen_slugs[slug] = seen_slugs.get(slug, 0) + 1
        if seen_slugs[slug] > 1:
            slug = f"{slug}-{seen_slugs[slug]}"
        who = _company_line(theme, by_ticker, depth)
        blocks.append(
            f'<article class="theme" id="theme-{e(slug)}">'
            f"<h3>{e(theme.title)}</h3>"
            f"{who}"
            f"<p>{e(theme.what_changed)}</p>"
            f'<p class="audit"><span class="tag">Audit plan</span> {e(theme.audit_consideration)}</p>'
            f"{_evidence_details(theme.evidence, depth)}"
            "</article>"
        )
    return "\n".join(blocks)


def _company_line(theme: Theme, by_ticker: dict[str, CompanyResult], depth: int) -> str:
    order: list[str] = []
    for evidence in theme.evidence:
        if evidence.ticker not in order:
            order.append(evidence.ticker)
    if not order:
        return ""
    links = []
    prefix = "../" * depth
    for ticker in order:
        company = by_ticker.get(ticker)
        name = company.display_name if company else theme.evidence[0].company_name
        for evidence in theme.evidence:
            if evidence.ticker == ticker:
                name = evidence.company_name
                break
        links.append(f'<a href="{prefix}company/{e(ticker.lower())}.html">{e(name)}</a>')
    return f'<p class="who">Companies: {", ".join(links)}.</p>'


def _evidence_details(evidence, depth: int) -> str:
    if not evidence:
        return ""
    count = len(evidence)
    label = "Supporting quote" if count == 1 else f"Supporting quotes ({count})"
    blocks = []
    for item in evidence:
        blocks.append(
            "<blockquote>"
            f"<p>{e(item.quote)}</p>"
            f'<p class="src">{e(item.company_name)} ({e(item.ticker)}). '
            f"Period ended {e(item.report_date)}, Item {e(item.item)}. "
            f'Accession <span class="acc">{e(item.accession)}</span>. '
            f"Stored comparison: {e(item.change)}. "
            f'<a href="{e(item.document_url)}">Filing</a>. '
            f'<a href="{e(item.passage_url)}">Passage in the filing</a>.</p>'
            "</blockquote>"
        )
    return f"<details><summary>{label}</summary>{''.join(blocks)}</details>"


def _methodology() -> str:
    return f"""
<h2>How a change is called</h2>
<div class="method">
<p>Passages are split on bold headings where the filing has them, otherwise on paragraphs. Passages are paired one-to-one, greedily, when content-token overlap is at least {ALIGN_THRESHOLD:.2f}. A leftover whose closest passage is still at or above that line was not paired because the closest passage was already matched; it is labeled already paired, not added or removed. Overlap from {UNCERTAIN_THRESHOLD:.2f} up to, but not including, {ALIGN_THRESHOLD:.2f} is left unclassified. Below {UNCERTAIN_THRESHOLD:.2f}, the passage is added or removed.</p>
<p>Content tokens drop function words and keep negations, so removing “not” is not treated as noise. A matched pair is date-only when every added or removed token is a four-digit year, a month name, or an integer from 1 to 31 sitting within three tokens of a month name. Other number changes are kept.</p>
<p>“Material weakness” and “significant deficiency” are classified from the sentence. A sentence that says a weakness was identified is labeled assertion. A sentence that only says one may occur in the future is labeled hypothetical. The auditor’s standard sentence about assessing the risk that a material weakness exists is labeled a procedure description. None of those is reported as an identification. If the rules do not fit, the sentence is quoted and no classification is made.</p>
<p>The auditor's opinion is taken only from words in Item 9A. A sentence that points to Item 8 is quoted as a pointer. Item 8 is not opened.</p>
<p>Peer “not aligned” means no passage in the peer's extracted Item 1A for the two years in that column cleared the overlap floor. It does not mean the peer has no such risk.</p>
</div>
<h2>What this does not do</h2>
<ul>
<li>It does not score the company, and it does not decide whether a control is effective.</li>
<li>It does not read Item 8, exhibits, 10-Q, 8-K, or the 10-K/A.</li>
<li>It does not put peers on one fiscal calendar. The peer page states the period ends.</li>
<li>Passage matching is greedy and one-to-one. A leftover can overlap another passage at or above 0.45 when that passage was already paired. A full rewrite can show up as a removal plus an addition.</li>
<li>An integer from 1 to 31 next to a month name is treated as a date.</li>
<li>The optional model summary is off unless you set it, and it is never the evidence.</li>
<li>The short list is drafted from the extracted changes. It is published only after each quote is found, after whitespace normalization, in the cited Item 1A or Item 9A extract, and in a stored passage or Item 9A quote for that accession. The audit-plan line is a consideration, not a finding.</li>
<li>This is a wording comparison of public filings. It is not investment advice.</li>
</ul>
"""


def write_report(
    companies: list[CompanyResult],
    peers: list[PeerResult],
    out_dir: Path,
    generated_at: str,
    *,
    model_summary: str | None = None,
    briefs: BriefSet | None = None,
    brief_errors: list[str] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "company").mkdir(exist_ok=True)
    (out_dir / "peers").mkdir(exist_ok=True)
    (out_dir / "style.css").write_text(_CSS)
    (out_dir / ".nojekyll").write_text("")
    by_ticker = {c.ticker: c for c in companies}
    (out_dir / "index.html").write_text(
        index_page(
            companies,
            peers,
            generated_at,
            model_summary=model_summary,
            briefs=briefs,
            brief_errors=brief_errors,
        )
    )
    for company in companies:
        (out_dir / "company" / f"{company.ticker.lower()}.html").write_text(
            company_page(company, generated_at)
        )
    for peer in peers:
        (out_dir / "peers" / f"{sector_slug(peer.sector)}.html").write_text(
            peer_page(peer, by_ticker, generated_at, briefs=briefs, brief_errors=brief_errors)
        )
