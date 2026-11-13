"""Check drafted briefs against the stored filing extracts.

A quote is accepted only when, after the same whitespace cleanup the extractor
uses, it is a contiguous substring of the Item 1A or Item 9A extract for the
accession it cites, and of the stored year-over-year passage or Item 9A quote
for that accession. The second check keeps a theme from citing text that did
not change. Any failure raises BriefVerificationError.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from drift_detector.bundle import sector_slug
from drift_detector.models import CompanyResult, ItemComparison
from drift_detector.textutil import clean_text, passage_fragment, sentences

MIN_QUOTE = 40
MAX_QUOTE = 1200
MAX_THEMES_OVERVIEW = 4
MAX_AI_THEMES = 2
MAX_THEMES_SECTOR = 4
MAX_TAKEAWAYS = 2
MAX_EVIDENCE = 4
MAX_EVIDENCE_OVERVIEW = 6
CHANGE_LABELS = {"added", "removed", "changed", "unclassified", "already_paired", "9a"}
SCHEMA_VERSION = 1
OVERVIEW_SCHEMA_VERSION = 2
_LEGAL_SUFFIXES = tuple(
    sorted(
        (
            " incorporated",
            " corporation",
            " company",
            " corp.",
            " corp",
            " inc.",
            " inc",
            " & co.",
            " & co",
            " co.",
            " group",
            " plc",
            " l.l.c.",
            " llc",
            " ltd.",
            " ltd",
        ),
        key=len,
        reverse=True,
    )
)
_SKIP_FIRST = {"bank", "the", "new", "group"}


class BriefVerificationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass
class Evidence:
    ticker: str
    company_name: str
    accession: str
    item: str
    report_date: str
    change: str
    quote: str
    document_url: str
    passage_url: str


@dataclass
class Theme:
    title: str
    what_changed: str
    audit_consideration: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class SectorBrief:
    sector: str
    divergence: str
    divergence_evidence: list[Evidence] = field(default_factory=list)
    themes: list[Theme] = field(default_factory=list)


@dataclass
class OverviewDraft:
    themes: list[Theme]
    takeaways: dict[str, list[Theme]]


@dataclass
class BriefSet:
    overview: list[Theme]
    sectors: dict[str, SectorBrief]
    takeaways: dict[str, list[Theme]] = field(default_factory=dict)


def drafts_present(draft_dir: Path) -> bool:
    overview, sector_files = _draft_paths(draft_dir)
    return overview is not None or bool(sector_files)


def verify_drafts(draft_dir: Path, companies: list[CompanyResult]) -> BriefSet:
    """Load drafts and check every quote. Raise if any check fails.

    The caller decides what an empty draft folder means. This function
    requires a complete set: an overview and one sector file per pair.
    """
    overview_path, sector_files = _draft_paths(draft_dir)
    errors: list[str] = []
    expected = [sector for sector, members in _pairs(companies) if len(members) == 2]
    if overview_path is None:
        errors.append(f"Missing {draft_dir / 'overview.json'}.")
    found_sectors: dict[str, tuple[Path, dict]] = {}
    for path in sector_files:
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: not valid JSON ({exc}).")
            continue
        sector_name = payload.get("sector")
        if not isinstance(sector_name, str) or not sector_name.strip():
            errors.append(f"{path.name}: sector is missing.")
            continue
        if sector_name in found_sectors:
            errors.append(f"Two drafts for sector {sector_name}.")
            continue
        found_sectors[sector_name] = (path, payload)
    for sector_name in expected:
        if sector_name not in found_sectors:
            errors.append(f"Missing sector draft for {sector_name} (sectors/{sector_slug(sector_name)}.json).")
    for sector_name in found_sectors:
        if sector_name not in expected:
            errors.append(f"Sector draft {sector_name} is not a pair in the input bundle.")
    if errors:
        raise BriefVerificationError(errors)

    sectors: dict[str, SectorBrief] = {}
    allowed: set[tuple[str, str, str, str, str]] = set()
    for sector_name in expected:
        path, payload = found_sectors[sector_name]
        brief, problems = validate_document(payload, companies, kind="sector", sector=sector_name)
        if problems:
            errors.extend(f"{path.name}: {problem}" for problem in problems)
            continue
        assert isinstance(brief, SectorBrief)
        sectors[sector_name] = brief
        for evidence in brief.divergence_evidence:
            allowed.add(_quote_key(evidence))
        for theme in brief.themes:
            for evidence in theme.evidence:
                allowed.add(_quote_key(evidence))
    overview_payload = None
    if overview_path is not None:
        try:
            overview_payload = json.loads(overview_path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"overview.json: not valid JSON ({exc}).")
    overview: list[Theme] = []
    takeaways: dict[str, list[Theme]] = {}
    if overview_payload is not None and not errors:
        parsed, problems = validate_document(
            overview_payload,
            companies,
            kind="overview",
            allowed_quotes=allowed,
        )
        if problems:
            errors.extend(f"overview.json: {problem}" for problem in problems)
        elif isinstance(parsed, OverviewDraft):
            overview = parsed.themes
            takeaways = parsed.takeaways
    if errors:
        raise BriefVerificationError(errors)
    if not overview and any(brief.themes for brief in sectors.values()):
        raise BriefVerificationError(
            ["overview.json: no cross-sector themes, but at least one sector brief has a theme."]
        )
    return BriefSet(overview=overview, sectors=sectors, takeaways=takeaways)


def validate_document(
    payload: dict,
    companies: list[CompanyResult],
    *,
    kind: str,
    sector: str | None = None,
    allowed_quotes: set[tuple[str, str, str, str, str]] | None = None,
) -> tuple[SectorBrief | OverviewDraft | None, list[str]]:
    """Check one draft object. Return the structured brief and a list of errors."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return None, ["draft is not a JSON object."]
    expected_version = OVERVIEW_SCHEMA_VERSION if kind == "overview" else SCHEMA_VERSION
    if payload.get("schema_version") != expected_version:
        errors.append(f"schema_version must be {expected_version}.")
    if payload.get("kind") != kind:
        errors.append(f"kind must be {kind!r}.")
    by_ticker = {company.ticker: company for company in companies}
    if kind == "sector":
        if payload.get("sector") != sector:
            errors.append(f"sector must be {sector!r}.")
        peers = [company for company in companies if company.sector == sector]
        allowed_tickers = {company.ticker for company in peers}
        if len(peers) != 2:
            errors.append(f"{sector} does not have two companies in the bundle.")
        divergence, divergence_errors = _divergence(
            payload.get("divergence"), by_ticker, allowed_tickers, allowed_quotes, companies
        )
        errors.extend(divergence_errors)
        themes, theme_errors = _themes(
            payload.get("themes"),
            by_ticker,
            allowed_tickers,
            allowed_quotes,
            companies,
            limit=MAX_THEMES_SECTOR,
        )
        errors.extend(theme_errors)
        if errors:
            return None, errors
        return (
            SectorBrief(
                sector=sector or "",
                divergence=divergence[0],
                divergence_evidence=divergence[1],
                themes=themes,
            ),
            [],
        )
    themes, theme_errors = _themes(
        payload.get("themes"),
        by_ticker,
        set(by_ticker),
        allowed_quotes,
        companies,
        limit=MAX_THEMES_OVERVIEW,
        evidence_limit=MAX_EVIDENCE_OVERVIEW,
    )
    errors.extend(theme_errors)
    takeaways, takeaway_errors = _takeaways(payload.get("sectors"), by_ticker, allowed_quotes, companies)
    errors.extend(takeaway_errors)
    if not theme_errors:
        ai_themes = _ai_theme_count(themes)
        if ai_themes > MAX_AI_THEMES:
            errors.append(
                f"overview has {ai_themes} cross-sector themes about artificial intelligence; "
                f"merge them so at most {MAX_AI_THEMES} remain."
            )
    if errors:
        return None, errors
    return OverviewDraft(themes=themes, takeaways=takeaways), []


def _takeaways(raw, by_ticker, allowed_quotes, companies):
    expected = [sector for sector, members in _pairs(companies) if len(members) == 2]
    if not isinstance(raw, list):
        return {}, ["sectors must be a list of sector takeaways."]
    found: dict[str, list] = {}
    errors: list[str] = []
    for index, item in enumerate(raw, start=1):
        label = f"sectors[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object.")
            continue
        sector_name = item.get("sector")
        if not isinstance(sector_name, str) or sector_name not in expected:
            errors.append(f"{label}: sector {sector_name!r} is not a pair in the input bundle.")
            continue
        if sector_name in found:
            errors.append(f"Two takeaway lists for {sector_name}.")
            continue
        peers = {company.ticker for company in by_ticker.values() if company.sector == sector_name}
        themes, theme_errors = _themes(
            item.get("takeaways"),
            by_ticker,
            peers,
            allowed_quotes,
            companies,
            limit=MAX_TAKEAWAYS,
            label_prefix="takeaways",
        )
        errors.extend(f"{sector_name} {problem}" for problem in theme_errors)
        if not theme_errors and not themes:
            errors.append(f"{sector_name} needs one or two takeaways.")
        found[sector_name] = themes
    for sector_name in expected:
        if sector_name not in found:
            errors.append(f"Missing takeaways for {sector_name}.")
    if errors:
        return {}, errors
    return {sector_name: found[sector_name] for sector_name in expected}, []


def _divergence(raw, by_ticker, allowed_tickers, allowed_quotes, companies):
    if not isinstance(raw, dict):
        return ("", []), ["divergence must be an object with text and evidence."]
    text = raw.get("text")
    prose_errors = _prose(text, "divergence.text", sentences_expected=2, max_len=1000)
    evidence, evidence_errors = _evidence_list(
        raw.get("evidence"),
        by_ticker,
        allowed_tickers,
        allowed_quotes,
        field_name="divergence.evidence",
    )
    if not evidence_errors and not evidence:
        evidence_errors.append("divergence.evidence needs at least one quote.")
    cited = {item.ticker for item in evidence}
    name_errors = _names_need_quotes(text if isinstance(text, str) else "", cited, by_ticker, companies, "divergence.text")
    return (text if isinstance(text, str) else "", evidence), prose_errors + evidence_errors + name_errors


def _themes(
    raw,
    by_ticker,
    allowed_tickers,
    allowed_quotes,
    companies,
    *,
    limit: int,
    evidence_limit: int = MAX_EVIDENCE,
    label_prefix: str = "themes",
):
    if not isinstance(raw, list):
        return [], [f"{label_prefix} must be a list."]
    if len(raw) > limit:
        return [], [f"{label_prefix} has {len(raw)} items; the maximum is {limit}."]
    themes: list[Theme] = []
    errors: list[str] = []
    titles: set[str] = set()
    for index, item in enumerate(raw, start=1):
        label = f"{label_prefix}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object.")
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{label}.title is missing.")
            title_text = ""
        else:
            title_text = " ".join(title.split())
            if len(title_text) > 120:
                errors.append(f"{label}.title is longer than 120 characters.")
            if title_text in titles:
                errors.append(f"{label}.title repeats {title_text!r}.")
            titles.add(title_text)
            errors.extend(_no_emoji(title_text, f"{label}.title"))
        errors.extend(_prose(item.get("what_changed"), f"{label}.what_changed", sentences_expected=2, max_len=1000))
        errors.extend(
            _prose(item.get("audit_consideration"), f"{label}.audit_consideration", sentences_expected=1, max_len=400)
        )
        evidence, evidence_errors = _evidence_list(
            item.get("evidence"),
            by_ticker,
            allowed_tickers,
            allowed_quotes,
            field_name=f"{label}.evidence",
            evidence_limit=evidence_limit,
        )
        errors.extend(evidence_errors)
        if not evidence_errors and not evidence:
            errors.append(f"{label}.evidence needs at least one quote.")
        cited = {quote.ticker for quote in evidence}
        for field_name, field_value in (
            ("title", title_text),
            ("what_changed", item.get("what_changed")),
            ("audit_consideration", item.get("audit_consideration")),
        ):
            errors.extend(
                _names_need_quotes(
                    field_value if isinstance(field_value, str) else "",
                    cited,
                    by_ticker,
                    companies,
                    f"{label}.{field_name}",
                )
            )
        if not errors:
            themes.append(
                Theme(
                    title=title_text,
                    what_changed=item["what_changed"].strip(),
                    audit_consideration=item["audit_consideration"].strip(),
                    evidence=evidence,
                )
            )
    if errors:
        return [], errors
    return themes, []


def _evidence_list(raw, by_ticker, allowed_tickers, allowed_quotes, *, field_name: str, evidence_limit: int = MAX_EVIDENCE):
    if not isinstance(raw, list):
        return [], [f"{field_name} must be a list."]
    if len(raw) > evidence_limit:
        return [], [f"{field_name} has {len(raw)} quotes; the maximum is {evidence_limit}."]
    evidence: list[Evidence] = []
    errors: list[str] = []
    for index, item in enumerate(raw, start=1):
        label = f"{field_name}[{index}]"
        checked, problem = _one_evidence(item, by_ticker, allowed_tickers, allowed_quotes, label)
        if problem:
            errors.append(problem)
        elif checked is not None:
            evidence.append(checked)
    return evidence, errors


def _one_evidence(item, by_ticker, allowed_tickers, allowed_quotes, label: str) -> tuple[Evidence | None, str | None]:
    if not isinstance(item, dict):
        return None, f"{label} must be an object."
    ticker = item.get("ticker")
    accession = item.get("accession")
    item_no = item.get("item")
    report_date = item.get("report_date")
    change = item.get("change")
    quote = item.get("quote")
    if not isinstance(ticker, str) or ticker not in by_ticker:
        return None, f"{label}: ticker {ticker!r} is not in the input bundle."
    if ticker not in allowed_tickers:
        return None, f"{label}: ticker {ticker} is outside this brief."
    if item_no not in {"1A", "9A"}:
        return None, f"{label}: item must be 1A or 9A."
    if change not in CHANGE_LABELS:
        return None, f"{label}: change must be one of {', '.join(sorted(CHANGE_LABELS))}."
    if not isinstance(accession, str) or not accession.strip():
        return None, f"{label}: accession is missing."
    if not isinstance(report_date, str) or not report_date.strip():
        return None, f"{label}: report_date is missing."
    if not isinstance(quote, str) or not quote.strip():
        return None, f"{label}: quote is missing."
    normalized = clean_text(quote)
    if len(normalized) < MIN_QUOTE:
        return None, f"{label}: quote is shorter than {MIN_QUOTE} characters after whitespace normalization."
    if len(normalized) > MAX_QUOTE:
        return None, f"{label}: quote is longer than {MAX_QUOTE} characters after whitespace normalization."
    company = by_ticker[ticker]
    filing = _filing(company, accession)
    if filing is None:
        return None, f"{label}: accession {accession} is not a stored filing for {ticker}."
    if filing.report_date != report_date:
        return None, (
            f"{label}: accession {accession} is the period ended {filing.report_date}, not {report_date}."
        )
    section = filing.sections.get(item_no)
    if section is None or not section.text:
        return None, f"{label}: no stored Item {item_no} extract for accession {accession}."
    if normalized not in clean_text(section.text):
        return None, (
            f"{label}: quote is not in the Item {item_no} extract for accession {accession} "
            f"after whitespace normalization. Quote starts: {normalized[:80]!r}."
        )
    blob = _change_blob(company, accession, item_no, change)
    if normalized not in clean_text(blob):
        return None, (
            f"{label}: quote is in the Item {item_no} extract but not in a stored {change} passage "
            f"for accession {accession}. Quote starts: {normalized[:80]!r}."
        )
    if not _accession_in_latest(company, accession, item_no):
        return None, f"{label}: accession {accession} is not part of the latest Item {item_no} comparison."
    built = Evidence(
        ticker=ticker,
        company_name=company.display_name,
        accession=accession,
        item=item_no,
        report_date=report_date,
        change=change,
        quote=quote.strip(),
        document_url=filing.document_url,
        passage_url=passage_fragment(filing.document_url, normalized),
    )
    if allowed_quotes is not None and _quote_key(built) not in allowed_quotes:
        return None, (
            f"{label}: overview quote is not one of the sector-brief quotes "
            f"(ticker {ticker}, accession {accession}, item {item_no})."
        )
    return built, None


def _accession_in_latest(company: CompanyResult, accession: str, item: str) -> bool:
    comparison = _latest(company, item)
    if comparison is None:
        return False
    for row in comparison.rows:
        if accession in {row.prior_accession, row.current_accession}:
            return True
    for flag in comparison.flags:
        for quote in flag.quotes:
            if quote.accession == accession and quote.item == item:
                return True
    return False


_ROW_KIND = {
    "added": "added",
    "removed": "removed",
    "changed": "changed",
    "unclassified": "uncertain",
    "already_paired": "already_paired",
}


def _change_blob(company: CompanyResult, accession: str, item: str, change: str) -> str:
    comparison = _latest(company, item)
    if comparison is None:
        return ""
    parts: list[str] = []
    if change == "9a":
        for flag in comparison.flags:
            for quote in flag.quotes:
                if quote.accession == accession and quote.item == item and quote.text:
                    parts.append(quote.text)
        return "\n".join(parts)
    row_kind = _ROW_KIND.get(change)
    if row_kind is None:
        return ""
    for row in comparison.rows:
        if row.kind != row_kind:
            continue
        if row.prior_accession == accession and row.prior_text:
            parts.append(row.prior_text)
        if row.current_accession == accession and row.current_text:
            parts.append(row.current_text)
    return "\n".join(parts)


def _latest(company: CompanyResult, item: str) -> ItemComparison | None:
    pairs = [comparison for comparison in company.comparisons if comparison.item == item]
    if not pairs:
        return None
    return max(pairs, key=lambda comparison: comparison.current_report_date)


def _filing(company: CompanyResult, accession: str):
    for filing in company.filings:
        if filing.accession == accession:
            return filing
    return None


def _prose(value, label: str, *, sentences_expected: int, max_len: int) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{label} is missing."]
    text = value.strip()
    errors = []
    if len(text) > max_len:
        errors.append(f"{label} is longer than {max_len} characters.")
    count = len(sentences(text))
    if count != sentences_expected:
        errors.append(f"{label} has {count} sentences; expected {sentences_expected}.")
    errors.extend(_no_emoji(text, label))
    return errors


def company_aliases(company: CompanyResult) -> list[str]:
    """Names a reader would use for this company, longest first."""
    names = [company.display_name.strip()]
    trimmed = company.display_name.strip()
    changed = True
    while changed and trimmed:
        changed = False
        lower = trimmed.lower()
        for suffix in _LEGAL_SUFFIXES:
            if lower.endswith(suffix):
                trimmed = trimmed[: -len(suffix)].strip(" ,")
                if trimmed and trimmed not in names:
                    names.append(trimmed)
                changed = True
                break
    if " & " in trimmed:
        names.append(trimmed.replace(" & ", " and "))
        left, _, right = trimmed.partition(" & ")
        left, right = left.strip(), right.strip()
        if left and left == right and " " not in left:
            names.append(f"{left[0]}&{right[0]}")
    words = trimmed.split()
    if words and len(words[0]) >= 4 and words[0].lower() not in _SKIP_FIRST:
        names.append(words[0])
    if len(words) >= 2 and len(words[0]) >= 4 and len(words[1]) >= 4 and words[1][:1].isupper():
        names.append(words[0] + words[1])
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    unique.sort(key=len, reverse=True)
    return unique


def named_tickers(text: str, companies: list[CompanyResult]) -> list[str]:
    """Covered companies named in prose. Tickers match only in uppercase, so 'cost' is not COST."""
    found: list[str] = []
    for company in companies:
        matched = False
        for alias in company_aliases(company):
            pattern = re.compile(
                r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?:'s|’s)?(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            if pattern.search(text):
                matched = True
                break
        if not matched and len(company.ticker) >= 3:
            ticker = re.compile(r"(?<![A-Za-z0-9])" + re.escape(company.ticker) + r"(?![A-Za-z0-9])")
            matched = ticker.search(text) is not None
        if matched:
            found.append(company.ticker)
    return found


def _names_need_quotes(text: str, cited: set[str], by_ticker, companies, label: str) -> list[str]:
    errors = []
    for ticker in named_tickers(text, companies):
        if ticker not in cited:
            errors.append(
                f"{label} names {by_ticker[ticker].display_name}, but this item cites no quote from that company."
            )
    return errors


def _ai_theme_count(themes: list[Theme]) -> int:
    count = 0
    for theme in themes:
        text = f"{theme.title}\n{theme.what_changed}"
        if re.search(r"\bAI\b", text) or "artificial intelligence" in text.lower():
            count += 1
    return count


def _no_emoji(text: str, label: str) -> list[str]:
    for char in text:
        code = ord(char)
        if code >= 0x1F300 or 0x2600 <= code <= 0x27BF:
            return [f"{label} contains a symbol or emoji."]
    return []


def _quote_key(evidence: Evidence) -> tuple[str, str, str, str, str]:
    return (evidence.ticker, evidence.accession, evidence.item, evidence.report_date, clean_text(evidence.quote))


def _pairs(companies: list[CompanyResult]) -> list[tuple[str, list[CompanyResult]]]:
    order: list[str] = []
    grouped: dict[str, list[CompanyResult]] = {}
    for company in companies:
        grouped.setdefault(company.sector, [])
        if company.sector not in order:
            order.append(company.sector)
        grouped[company.sector].append(company)
    return [(sector, grouped[sector]) for sector in order]


def _draft_paths(draft_dir: Path) -> tuple[Path | None, list[Path]]:
    overview = draft_dir / "overview.json"
    sector_dir = draft_dir / "sectors"
    sectors = sorted(sector_dir.glob("*.json")) if sector_dir.exists() else []
    return (overview if overview.exists() else None), sectors
