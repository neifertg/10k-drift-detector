"""Fetch 10-Ks, extract the two items, compare consecutive years, write the report."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from drift_detector.align import align_passages, count_kinds
from drift_detector.config import AppConfig, CompanySpec, load_config
from drift_detector.edgar import (
    EdgarClient,
    EdgarError,
    browse_url,
    document_url,
    filing_index_json,
    gather_rows,
    index_url,
    select_10ks,
    ticker_map,
)
from drift_detector.extract import EXTRACTOR_VERSION, extract_sections
from drift_detector.flags import item_9a_flags
from drift_detector.llm import maybe_summarize
from drift_detector.models import (
    AlignedRow,
    Amendment,
    CompanyResult,
    FilingRecord,
    ItemComparison,
    Passage,
    Section,
)
from drift_detector.peers import build_peer
from drift_detector.report import write_report
from drift_detector.textutil import content_overlap, passage_fragment

logger = logging.getLogger(__name__)

_EXHIBIT = re.compile(r"(exhibit|^ex[-_ ]?\d|graphic|image|logo)", re.I)


def _section_from_dict(payload: dict) -> Section:
    return Section(
        item=payload["item"],
        status=payload["status"],
        header=payload.get("header"),
        end_header=payload.get("end_header"),
        text=payload.get("text") or "",
        segmentation=payload.get("segmentation") or "none",
        notes=list(payload.get("notes") or []),
        passages=[
            Passage(text=p["text"], title=p.get("title"), part=p.get("part"))
            for p in payload.get("passages") or []
        ],
    )


def _section_to_dict(section: Section) -> dict:
    return {
        "item": section.item,
        "status": section.status,
        "header": section.header,
        "end_header": section.end_header,
        "text": section.text,
        "segmentation": section.segmentation,
        "notes": section.notes,
        "passages": [
            {"text": p.text, "title": p.title, "part": p.part} for p in section.passages
        ],
    }


def _load_sections(cache_dir: Path, digest: str, html: str) -> dict[str, Section]:
    path = cache_dir / "sections" / f"{digest}-v{EXTRACTOR_VERSION}.json"
    if path.exists():
        payload = json.loads(path.read_text())
        return {key: _section_from_dict(value) for key, value in payload.items()}
    sections = extract_sections(html)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: _section_to_dict(value) for key, value in sections.items()}))
    return sections


def _candidate_names(client: EdgarClient, cik: str, accession: str, primary: str) -> list[str]:
    names: list[str] = []
    if primary and primary.lower().endswith((".htm", ".html", ".txt")):
        names.append(primary)
    try:
        index = client.get_json(filing_index_json(cik, accession))
    except EdgarError as exc:
        logger.warning("Filing index unavailable for %s: %s", accession, exc)
        return names
    ranked = []
    for item in index.get("directory", {}).get("item") or []:
        name = item.get("name") or ""
        if not name.lower().endswith((".htm", ".html")):
            continue
        if _EXHIBIT.search(name):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        ranked.append((size, name))
    for _, name in sorted(ranked, reverse=True):
        if name not in names:
            names.append(name)
    return names


def _fetch_filing(client: EdgarClient, cache_dir: Path, cik: str, selected) -> FilingRecord:
    names = _candidate_names(client, cik, selected.accession, selected.primary_document)
    if not names:
        raise EdgarError(f"No HTML document for accession {selected.accession}")
    chosen_name = names[0]
    body = b""
    html = ""
    sections: dict[str, Section] = {}
    for offset, name in enumerate(names[:3]):
        url = document_url(cik, selected.accession, name)
        body, html = client.get_text(url)
        digest = hashlib.sha256(body).hexdigest()
        sections = _load_sections(cache_dir, digest, html)
        both_missing = sections["1A"].status == "missing" and sections["9A"].status == "missing"
        chosen_name = name
        if not both_missing or offset == len(names[:3]) - 1:
            break
        logger.info("Primary document for %s did not contain either item; trying %s", selected.accession, names[offset + 1] if offset + 1 < len(names) else "")
    digest = hashlib.sha256(body).hexdigest()
    amendments = []
    for row in selected.amendments:
        amendments.append(
            Amendment(
                form=row["form"],
                filing_date=row["filingDate"],
                accession=row["accessionNumber"],
                document_url=document_url(cik, row["accessionNumber"], row["primaryDocument"])
                if row.get("primaryDocument")
                else index_url(cik, row["accessionNumber"]),
            )
        )
    others = [
        Amendment(
            form=row["form"],
            filing_date=row["filingDate"],
            accession=row["accessionNumber"],
            document_url=document_url(cik, row["accessionNumber"], row["primaryDocument"])
            if row.get("primaryDocument")
            else index_url(cik, row["accessionNumber"]),
        )
        for row in selected.other_10k
    ]
    return FilingRecord(
        report_date=selected.report_date,
        filing_date=selected.filing_date,
        accession=selected.accession,
        primary_document=chosen_name,
        document_url=document_url(cik, selected.accession, chosen_name),
        index_url=index_url(cik, selected.accession),
        sha256=digest,
        sections=sections,
        amendments=amendments,
        other_10k=others,
    )


def _quote_ok(text: str | None, section: Section | None) -> bool:
    if text is None:
        return True
    if section is None:
        return False
    return text in section.text


def _rows_for(matches, prior: FilingRecord, current: FilingRecord, item: str) -> list[AlignedRow]:
    rows = []
    prior_section = prior.sections[item]
    current_section = current.sections[item]
    for match in matches:
        if match.kind not in {"added", "removed", "changed", "uncertain", "already_paired"}:
            continue
        prior_text = match.prior.text if match.prior else None
        current_text = match.current.text if match.current else None
        if not _quote_ok(prior_text, prior_section) or not _quote_ok(current_text, current_section):
            logger.warning("Dropping a %s row whose text is not in the extract", match.kind)
            continue
        label = ""
        if match.current and match.current.label:
            label = match.current.label
        elif match.prior and match.prior.label:
            label = match.prior.label
        closest_text = match.closest.text if match.closest else None
        closest_section = current_section if match.kind in {"removed", "uncertain"} and match.prior and not match.current else prior_section
        if match.kind == "added":
            closest_section = prior_section
        elif match.kind == "removed":
            closest_section = current_section
        elif match.kind in {"uncertain", "already_paired"}:
            closest_section = current_section if match.prior and not match.current else prior_section
        if closest_text and not _quote_ok(closest_text, closest_section):
            closest_text = None
        rows.append(
            AlignedRow(
                kind=match.kind,
                similarity=match.similarity,
                label=label,
                prior_text=prior_text,
                current_text=current_text,
                prior_report_date=prior.report_date,
                current_report_date=current.report_date,
                prior_url=passage_fragment(prior.document_url, prior_text) if prior_text else None,
                current_url=passage_fragment(current.document_url, current_text) if current_text else None,
                prior_document_url=prior.document_url,
                current_document_url=current.document_url,
                prior_accession=prior.accession,
                current_accession=current.accession,
                closest_text=closest_text,
                closest_similarity=match.closest_similarity,
                closest_url=passage_fragment(
                    (current.document_url if closest_section is current_section else prior.document_url),
                    closest_text,
                )
                if closest_text
                else None,
                closest_report_date=(
                    current.report_date if closest_section is current_section else prior.report_date
                )
                if closest_text
                else None,
            )
        )
    return rows


def compare_item(prior: FilingRecord, current: FilingRecord, item: str) -> ItemComparison:
    left = prior.sections[item]
    right = current.sections[item]
    overlap = content_overlap(left.text, right.text) if left.text and right.text else None
    base = dict(
        item=item,
        prior_report_date=prior.report_date,
        current_report_date=current.report_date,
        full_overlap=overlap,
        prior_status=left.status,
        current_status=right.status,
        prior_header=left.header,
        current_header=right.header,
        prior_end=left.end_header,
        current_end=right.end_header,
        segmentation_prior=left.segmentation,
        segmentation_current=right.segmentation,
    )
    flags = []
    if item == "9A":
        flags = item_9a_flags(
            left,
            right,
            prior.report_date,
            current.report_date,
            prior_accession=prior.accession,
            current_accession=current.accession,
            prior_url=prior.document_url,
            current_url=current.document_url,
            make_url=passage_fragment,
        )
    if left.status != "ok" or right.status != "ok":
        reason = (
            f"Item {item} extraction status is {left.status} for the period ended {prior.report_date} "
            f"and {right.status} for the period ended {current.report_date}. "
            "Passage comparison is not supported, and absence is not inferred. "
            + " ".join(left.notes + right.notes)
        ).strip()
        return ItemComparison(supported=False, reason=reason, counts={}, rows=[], flags=flags, **base)
    if left.segmentation != right.segmentation:
        reason = (
            f"Item {item} was split as {left.segmentation} for {prior.report_date} and "
            f"{right.segmentation} for {current.report_date}. "
            "Added and removed passages are not listed because the two years were not divided the same way."
        )
        return ItemComparison(supported=False, reason=reason, counts={}, rows=[], flags=flags, **base)
    if not left.passages or not right.passages:
        reason = (
            f"Item {item} did not yield passages for {prior.report_date} "
            f"({len(left.passages)}) or {current.report_date} ({len(right.passages)}). "
            "Passage comparison is not supported."
        )
        return ItemComparison(supported=False, reason=reason, counts={}, rows=[], flags=flags, **base)
    matches = align_passages(left.passages, right.passages)
    return ItemComparison(
        supported=True,
        reason=None,
        counts=count_kinds(matches),
        rows=_rows_for(matches, prior, current, item),
        flags=flags,
        **base,
    )


def _risk_line(cmp: ItemComparison) -> str:
    if not cmp.supported:
        return (
            f"Item 1A ({cmp.prior_report_date} to {cmp.current_report_date}): "
            f"{cmp.reason}"
        )
    counts = cmp.counts
    return (
        f"Item 1A ({cmp.prior_report_date} to {cmp.current_report_date}): "
        f"{counts.get('added', 0)} added, {counts.get('removed', 0)} removed, "
        f"{counts.get('changed', 0)} wording changes, {counts.get('cosmetic', 0)} date-only, "
        f"{counts.get('uncertain', 0)} unclassified, "
        f"{counts.get('already_paired', 0)} already paired."
    )


def index_lines(company: CompanyResult) -> list[str]:
    if company.error and not company.comparisons:
        return [company.error + " No comparison is drawn."]
    lines: list[str] = []
    for item in ("9A", "1A"):
        group = [c for c in company.comparisons if c.item == item]
        if not group:
            lines.append(f"Item {item}: no consecutive pair to compare.")
            continue
        latest = max(group, key=lambda c: c.current_report_date)
        if item == "1A":
            lines.append(_risk_line(latest))
        elif latest.flags:
            lines.extend(flag.index_line for flag in latest.flags)
        else:
            lines.append(latest.reason or "Item 9A comparison was not produced.")
    return lines


def _resolve_cik(spec: CompanySpec, tickers: dict[str, dict]) -> tuple[str, str | None]:
    mapped = tickers.get(spec.ticker)
    mapped_cik = mapped["cik"] if mapped else None
    mapped_title = mapped["title"] if mapped else ""
    if spec.cik:
        note = None
        if mapped_cik and mapped_cik != spec.cik:
            note = (
                f"The SEC ticker file maps {spec.ticker} to CIK {mapped_cik}"
                + (f" ({mapped_title})" if mapped_title else "")
                + f". This run uses CIK {spec.cik}, the registrant on the 10-K."
            )
        elif mapped_cik is None:
            note = (
                f"The SEC ticker file has no row for {spec.ticker}. "
                f"This run uses the configured CIK {spec.cik}."
            )
        return spec.cik, note
    if not mapped_cik:
        raise EdgarError(f"Ticker {spec.ticker} is not in the SEC ticker file and has no configured CIK.")
    return mapped_cik, None


def process_company(
    spec: CompanySpec,
    client: EdgarClient,
    tickers: dict[str, dict],
    years: int,
    cache_dir: Path,
) -> CompanyResult:
    try:
        cik, note = _resolve_cik(spec, tickers)
    except EdgarError as exc:
        return CompanyResult(
            ticker=spec.ticker,
            display_name=spec.name,
            edgar_name="",
            cik="",
            sector=spec.sector,
            error=str(exc),
        )
    try:
        submission, rows = gather_rows(client, cik, years)
        edgar_name = submission.get("name") or spec.name
        selected = select_10ks(rows, years)
        if not selected:
            return CompanyResult(
                ticker=spec.ticker,
                display_name=spec.name,
                edgar_name=edgar_name,
                cik=f"{int(cik):010d}",
                sector=spec.sector,
                error=f"No 10-K was found in the EDGAR submissions retrieved for CIK {int(cik):010d}.",
                cik_note=note,
                edgar_browse_url=browse_url(cik),
            )
        filings = []
        for filing in selected:
            logger.info("Fetching %s %s %s", spec.ticker, filing.report_date, filing.accession)
            filings.append(_fetch_filing(client, cache_dir, cik, filing))
        comparisons = []
        chronological = list(reversed(filings))
        for older, newer in zip(chronological, chronological[1:]):
            for item in ("1A", "9A"):
                comparisons.append(compare_item(older, newer, item))
        result = CompanyResult(
            ticker=spec.ticker,
            display_name=spec.name,
            edgar_name=edgar_name,
            cik=f"{int(cik):010d}",
            sector=spec.sector,
            filings=filings,
            comparisons=comparisons,
            cik_note=note,
            edgar_browse_url=browse_url(cik),
        )
        for filing in filings:
            for item, section in filing.sections.items():
                logger.info(
                    "%s %s Item %s status=%s chars=%d passages=%d seg=%s end=%s",
                    spec.ticker,
                    filing.report_date,
                    item,
                    section.status,
                    section.chars,
                    len(section.passages),
                    section.segmentation,
                    (section.end_header or "")[:90],
                )
                if section.notes:
                    logger.info("  %s", " ".join(section.notes))
        return result
    except EdgarError as exc:
        return CompanyResult(
            ticker=spec.ticker,
            display_name=spec.name,
            edgar_name=spec.name,
            cik=f"{int(cik):010d}" if cik else "",
            sector=spec.sector,
            error=str(exc),
            cik_note=note,
            edgar_browse_url=browse_url(cik) if cik else "",
        )
    except Exception as exc:
        logger.exception("Failed while processing %s", spec.ticker)
        return CompanyResult(
            ticker=spec.ticker,
            display_name=spec.name,
            edgar_name=spec.name,
            cik=f"{int(cik):010d}" if cik else "",
            sector=spec.sector,
            error=f"{type(exc).__name__}: {exc}. No comparison is drawn.",
            cik_note=note,
            edgar_browse_url=browse_url(cik) if cik else "",
        )


def _unsupported_peer(sector: str, members: list[CompanySpec]):
    from drift_detector.models import PeerResult

    names = ", ".join(m.ticker for m in members) or "(none)"
    reason = (
        f"{sector} has {len(members)} companies in the config ({names}). "
        "A peer comparison is produced only for a sector with two companies."
    )
    return PeerResult(
        sector=sector,
        left_ticker=members[0].ticker if members else "",
        right_ticker=members[1].ticker if len(members) > 1 else "",
        supported=False,
        reason=reason,
        period_statement=reason,
        period_kind="unavailable",
        gap_days=None,
        index_line=reason,
    )


def run(
    config_path: Path,
    out_dir: Path,
    cache_dir: Path,
    *,
    only: str = "",
) -> None:
    user_agent = os.environ.get("SEC_USER_AGENT")
    config = load_config(config_path)
    client = EdgarClient(user_agent or "", cache_dir, config.request_interval_seconds)
    tickers = ticker_map(client)
    wanted = {part.strip().upper() for part in only.split(",") if part.strip()}
    specs = [c for c in config.companies if not wanted or c.ticker in wanted]
    companies = [process_company(spec, client, tickers, config.years, cache_dir) for spec in specs]
    for company in companies:
        company.index_lines = index_lines(company)
    by_ticker = {c.ticker: c for c in companies}
    peers = []
    for sector, members in config.pairs():
        if wanted and not any(m.ticker in wanted for m in members):
            continue
        if len(members) != 2 or any(m.ticker not in by_ticker for m in members):
            if wanted and any(m.ticker not in by_ticker for m in members):
                continue
            peers.append(_unsupported_peer(sector, members))
            continue
        peers.append(build_peer(by_ticker[members[0].ticker], by_ticker[members[1].ticker]))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    model_summary = maybe_summarize(
        {
            "generated_at": generated_at,
            "companies": [
                {"ticker": c.ticker, "lines": c.index_lines} for c in companies
            ],
        }
    )
    write_report(companies, peers, out_dir, generated_at, model_summary=model_summary)
    logger.info("Wrote report to %s", out_dir)


def load_app_config(path: Path) -> AppConfig:
    return load_config(path)
