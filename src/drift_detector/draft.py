"""Draft CAE briefs from the exported filing bundle.

The script calls the Anthropic Messages API. It does not fetch EDGAR.
Every draft is checked before it is written. A quote that is not in the
stored extract is not saved.

    export ANTHROPIC_API_KEY="..."
    PYTHONPATH=src python -m drift_detector draft-briefs --input briefs/input --out briefs/drafts
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from drift_detector.bundle import load_bundle, peer_groups, sector_slug
from drift_detector.flags import SIGNAL_TITLES
from drift_detector.models import CompanyResult, ItemComparison
from drift_detector.peers import build_peer
from drift_detector.textutil import (
    ALIGN_THRESHOLD,
    UNCERTAIN_THRESHOLD,
    clean_text,
    content_overlap,
    excerpt,
    norm,
    sentences,
)
from drift_detector.verify import (
    Evidence,
    OverviewDraft,
    SectorBrief,
    Theme,
    _quote_key,
    validate_document,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_VERSION = "2026-10-21"
DEFAULT_MAX_TOKENS = 16000
API_URL = "https://api.anthropic.com/v1/messages"
PASSAGE_CAP = 1800
_PROMPTS = Path(__file__).resolve().parent / "prompts"

_PACKET_KIND = {
    "added": "added",
    "removed": "removed",
    "changed": "changed",
    "uncertain": "unclassified",
    "already_paired": "already_paired",
}


class DraftError(Exception):
    pass


def load_prompt(name: str) -> str:
    path = _PROMPTS / name
    if not path.exists():
        raise DraftError(f"Prompt file is missing: {path}")
    return path.read_text().strip()


def draft_briefs(
    input_dir: Path,
    out_dir: Path,
    *,
    transport=None,
    dry_run: bool = False,
    force: bool = False,
    only: str = "",
) -> None:
    """Write briefs/drafts/overview.json and briefs/drafts/sectors/*.json.

    Each sector file is written as soon as that request succeeds. A later run
    skips a sector that already has a valid file, unless ``force`` is set.
    ``only`` limits the sectors that are drafted. The overview is written only
    when every sector file is valid.

    ``transport``, when set, is called with the JSON request body and must
    return a Messages API response dict. Tests use it. The CLI does not.
    """
    manifest, companies = load_bundle(input_dir)
    sectors = [str(sector) for sector in manifest.get("sectors") or []]
    groups = [(sector, members) for sector, members in peer_groups(companies, sectors) if len(members) == 2]
    if not groups:
        raise DraftError("The input bundle has no sector with two companies.")
    packets = [(sector, members, build_sector_packet(members[0], members[1])) for sector, members in groups]
    chosen = _selected_sectors(only, packets)
    if dry_run:
        for sector, members, packet in packets:
            if chosen is not None and sector not in chosen:
                continue
            size = len(json.dumps(packet, ensure_ascii=False))
            names = " and ".join(company.ticker for company in members)
            print(f"{sector} ({names}): packet is {size} characters.")
        print("Dry run: the API was not called and no drafts were written.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if transport is None and not api_key:
        raise DraftError("ANTHROPIC_API_KEY is not set.")
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    version = os.environ.get("ANTHROPIC_VERSION", "").strip() or DEFAULT_VERSION
    max_tokens = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "").strip() or DEFAULT_MAX_TOKENS)
    effort = os.environ.get("ANTHROPIC_EFFORT", "medium").strip()
    if effort.lower() in {"off", "none", "-"}:
        effort = ""
    out_dir.mkdir(parents=True, exist_ok=True)
    sector_dir = out_dir / "sectors"
    sector_dir.mkdir(parents=True, exist_ok=True)
    usage_path = out_dir / "token-usage.json"
    settings = {
        "model": model,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "effort": effort,
        "version": version,
        "transport": transport,
        "input_generated_at": manifest.get("generated_at") or "",
        "usage": _saved_usage(usage_path),
        "usage_path": usage_path,
    }
    sector_system = load_prompt("sector_brief.md")
    written: list[tuple[str, SectorBrief | None, dict | None]] = []
    rewrote = False
    for sector, _members, packet in packets:
        path = sector_dir / f"{sector_slug(sector)}.json"
        selected = chosen is None or sector in chosen
        loaded = _load_valid_sector(path, companies, sector)
        if not selected:
            written.append((sector, None, None) if loaded is None else (sector, loaded[0], loaded[1]))
            continue
        if loaded is not None and not force:
            logger.info("Skipping %s; %s is already a valid draft. Pass --force to redraft it.", sector, path)
            written.append((sector, loaded[0], loaded[1]))
            continue
        logger.info("Drafting %s (%s characters of packet)", sector, len(json.dumps(packet)))
        try:
            payload, brief = _draft_sector(sector, packet, companies, sector_system, settings, label=sector)
        except DraftError as exc:
            raise DraftError(
                f"{sector} was not saved. {exc} "
                f"Sector files that already succeeded were kept in {sector_dir}."
            ) from exc
        _write(path, payload)
        rewrote = True
        written.append((sector, brief, payload))
    missing = [sector for sector, brief, _payload in written if brief is None]
    if missing:
        logger.info("Overview was not drafted. No valid draft yet for: %s.", ", ".join(missing))
        return
    allowed = set()
    overview_packet = []
    for _sector, brief, payload in written:
        assert brief is not None and payload is not None
        for evidence in brief.divergence_evidence:
            allowed.add(_quote_key(evidence))
        for theme in brief.themes:
            for evidence in theme.evidence:
                allowed.add(_quote_key(evidence))
        overview_packet.append(_public_sector(payload))
    overview_path = out_dir / "overview.json"
    if not rewrote and not force and _load_valid_overview(overview_path, companies, allowed) is not None:
        logger.info("Skipping overview; %s is already a valid draft. Pass --force to redraft it.", overview_path)
        return
    logger.info("Drafting overview from %s sector briefs", len(overview_packet))
    try:
        overview = _draft_overview(overview_packet, companies, allowed, settings)
    except DraftError as exc:
        raise DraftError(
            f"The overview was not saved. {exc} "
            f"Sector files that already succeeded were kept in {sector_dir}."
        ) from exc
    _write(overview_path, overview)
    if chosen is None:
        keep = {f"{sector_slug(sector)}.json" for sector, _members, _packet in packets}
        for stale in sector_dir.glob("*.json"):
            if stale.name not in keep:
                stale.unlink()


def _selected_sectors(only: str, packets) -> set[str] | None:
    names = [part.strip() for part in only.split(",") if part.strip()]
    if not names:
        return None
    by_key = {}
    for sector, _members, _packet in packets:
        by_key[sector.lower()] = sector
        by_key[sector_slug(sector)] = sector
    chosen: list[str] = []
    unknown: list[str] = []
    for name in names:
        match = by_key.get(name.lower())
        if match is None:
            unknown.append(name)
        elif match not in chosen:
            chosen.append(match)
    if unknown:
        known = ", ".join(sector for sector, _members, _packet in packets)
        raise DraftError(f"Unknown sector in --only: {', '.join(unknown)}. Known sectors: {known}.")
    return set(chosen)


def _load_valid_sector(path: Path, companies, sector: str) -> tuple[SectorBrief, dict] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    brief, errors = validate_document(payload, companies, kind="sector", sector=sector)
    if errors or not isinstance(brief, SectorBrief):
        return None
    return brief, payload


def _load_valid_overview(path: Path, companies, allowed) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    parsed, errors = validate_document(payload, companies, kind="overview", allowed_quotes=allowed)
    if errors or not isinstance(parsed, OverviewDraft):
        return None
    return payload


def _saved_usage(path: Path) -> list:
    if not path.exists():
        return []
    try:
        saved = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    requests = saved.get("requests") if isinstance(saved, dict) else None
    if not isinstance(requests, list):
        return []
    return list(requests)


def build_sector_packet(left: CompanyResult, right: CompanyResult) -> dict:
    peer = build_peer(left, right)
    return {
        "sector": left.sector,
        "how_to_quote": (
            "Quote only from text fields. comparison_note, computed_passage_counts, "
            "peer_alignment, and content_token_overlap are tool output, not filing sentences."
        ),
        "period": {
            "statement": peer.period_statement,
            "kind": peer.period_kind,
            "gap_days": peer.gap_days,
        },
        "companies": [
            _company_packet(left, right),
            _company_packet(right, left),
        ],
    }


def _company_packet(company: CompanyResult, peer: CompanyResult) -> dict:
    item_1a = _latest(company, "1A")
    item_9a = _latest(company, "9A")
    return {
        "ticker": company.ticker,
        "name": company.display_name,
        "sector": company.sector,
        "item_1a": _item_1a_packet(company, peer, item_1a),
        "item_9a": _item_9a_packet(company, item_9a),
    }


def _item_1a_packet(company: CompanyResult, peer: CompanyResult, comparison: ItemComparison | None) -> dict:
    if comparison is None:
        return {"comparison_supported": False, "unsupported_reason": "No Item 1A comparison is stored.", "passages": []}
    payload = {
        "comparison_supported": comparison.supported,
        "unsupported_reason": comparison.reason,
        "prior_report_date": comparison.prior_report_date,
        "current_report_date": comparison.current_report_date,
        "computed_passage_counts": comparison.counts,
        "passages": [],
    }
    if not comparison.supported:
        return payload
    peer_pool = _passage_pool(peer, "1A")
    passages = []
    for row in comparison.rows:
        kind = _PACKET_KIND.get(row.kind)
        if kind is None:
            continue
        if kind == "changed":
            prior = _side(row.prior_text, row.current_text, "prior", row.prior_accession, row.prior_report_date)
            current = _side(row.prior_text, row.current_text, "current", row.current_accession, row.current_report_date)
            if prior is None and current is None:
                continue
            passages.append(
                {
                    "kind": "changed",
                    "label": row.label,
                    "content_token_overlap": row.similarity,
                    "prior": prior,
                    "current": current,
                }
            )
            continue
        text = row.current_text if kind == "added" else row.prior_text if kind == "removed" else (
            row.current_text or row.prior_text
        )
        accession = row.current_accession if text and text == row.current_text else row.prior_accession
        report_date = row.current_report_date if text and text == row.current_text else row.prior_report_date
        if not text or not accession or not report_date:
            continue
        shown, truncated = excerpt(text, PASSAGE_CAP)
        entry = {
            "kind": kind,
            "label": row.label,
            "accession": accession,
            "report_date": report_date,
            "item": "1A",
            "text": shown,
            "truncated": truncated,
            "text_is": "exact_prefix_of_passage" if truncated else "full_passage",
        }
        if kind == "added" and peer_pool:
            score = max(content_overlap(text, other) for other in peer_pool)
            entry["content_token_overlap_with_peer"] = round(score, 2)
            if score >= ALIGN_THRESHOLD:
                entry["peer_alignment"] = "aligned"
            elif score >= UNCERTAIN_THRESHOLD:
                entry["peer_alignment"] = "unclassified"
            else:
                entry["peer_alignment"] = "not_aligned"
        passages.append(entry)
    payload["passages"] = passages
    return payload


def _side(prior: str | None, current: str | None, which: str, accession: str | None, report_date: str | None):
    prior_sentences = sentences(prior or "")
    current_sentences = sentences(current or "")
    if which == "prior":
        kept = [sentence for sentence in prior_sentences if not any(norm(sentence) == norm(other) for other in current_sentences)]
        full = prior or ""
    else:
        kept = [sentence for sentence in current_sentences if not any(norm(sentence) == norm(other) for other in prior_sentences)]
        full = current or ""
    if not full or not accession or not report_date:
        return None
    if kept:
        text, truncated = excerpt(" ".join(kept), PASSAGE_CAP)
        text_is = "sentences_not_in_the_other_year"
    else:
        text, truncated = excerpt(full, PASSAGE_CAP)
        text_is = "exact_prefix_of_passage"
    if not text:
        return None
    return {
        "accession": accession,
        "report_date": report_date,
        "item": "1A",
        "text": text,
        "truncated": truncated,
        "text_is": text_is,
    }


def _item_9a_packet(company: CompanyResult, comparison: ItemComparison | None) -> dict:
    if comparison is None:
        return {"comparison_supported": False, "unsupported_reason": "No Item 9A comparison is stored.", "flags": []}
    return {
        "comparison_supported": comparison.supported,
        "unsupported_reason": comparison.reason,
        "prior_report_date": comparison.prior_report_date,
        "current_report_date": comparison.current_report_date,
        "flags": [
            {
                "signal": flag.signal,
                "title": SIGNAL_TITLES.get(flag.signal, flag.signal),
                "comparison_note": flag.statement,
                "quotes": [
                    {
                        "text": quote.text,
                        "accession": quote.accession,
                        "report_date": quote.report_date,
                        "item": quote.item,
                    }
                    for quote in flag.quotes
                    if _quote_in_extract(company, quote.accession, quote.item, quote.text)
                ],
            }
            for flag in comparison.flags
        ],
    }


def _quote_in_extract(company: CompanyResult, accession: str | None, item: str | None, text: str | None) -> bool:
    """True when the quote is inside the stored section, not only a heading that bounds it."""
    if not accession or not item or not text:
        return False
    for filing in company.filings:
        if filing.accession != accession:
            continue
        section = filing.sections.get(item)
        if section is None or not section.text:
            return False
        return clean_text(text) in clean_text(section.text)
    return False


def _passage_pool(company: CompanyResult, item: str) -> list[str]:
    comparison = _latest(company, item)
    if comparison is None:
        return []
    texts = []
    for report_date in (comparison.prior_report_date, comparison.current_report_date):
        for filing in company.filings:
            if filing.report_date != report_date:
                continue
            section = filing.sections.get(item)
            if section and section.status == "ok":
                texts.extend(passage.text for passage in section.passages if passage.text)
    return texts


def _latest(company: CompanyResult, item: str) -> ItemComparison | None:
    pairs = [comparison for comparison in company.comparisons if comparison.item == item]
    if not pairs:
        return None
    return max(pairs, key=lambda comparison: comparison.current_report_date)


def _draft_sector(sector, packet, companies, system, settings, *, label: str):
    user = (
        f"Draft the sector brief for {sector}. "
        "The packet is the only source you may quote.\n\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )
    payload = _complete(
        system, user, companies, settings, kind="sector", sector=sector, allowed_quotes=None, label=label
    )
    brief, errors = validate_document(payload, companies, kind="sector", sector=sector)
    if errors or not isinstance(brief, SectorBrief):
        raise DraftError("Sector draft passed the request loop but failed a final check: " + "; ".join(errors))
    return _sector_document(brief, settings), brief


def _draft_overview(sector_documents, companies, allowed, settings):
    system = load_prompt("overview_brief.md")
    user = (
        "Draft the overview from these sector briefs. "
        "Copy evidence quotes exactly from them.\n\n"
        + json.dumps(sector_documents, ensure_ascii=False, indent=2)
    )
    payload = _complete(
        system,
        user,
        companies,
        settings,
        kind="overview",
        sector=None,
        allowed_quotes=allowed,
        label="overview",
    )
    parsed, errors = validate_document(payload, companies, kind="overview", allowed_quotes=allowed)
    if errors or not isinstance(parsed, OverviewDraft):
        raise DraftError("Overview draft passed the request loop but failed a final check: " + "; ".join(errors))
    return _overview_document(parsed, settings)


def _complete(system, user, companies, settings, *, kind, sector, allowed_quotes, label: str) -> dict:
    prompt = user
    last_errors: list[str] = []
    for attempt in (1, 2):
        response = call_anthropic(
            system,
            prompt,
            model=settings["model"],
            api_key=settings["api_key"],
            max_tokens=settings["max_tokens"],
            effort=settings["effort"],
            version=settings["version"],
            transport=settings["transport"],
        )
        _record_usage(settings, response, label, attempt)
        try:
            payload = parse_json_object(response_text(response))
        except DraftError:
            raise
        except ValueError as exc:
            last_errors = [str(exc)]
        else:
            _brief, errors = validate_document(
                payload,
                companies,
                kind=kind,
                sector=sector,
                allowed_quotes=allowed_quotes,
            )
            if not errors:
                return payload
            last_errors = errors
        if attempt == 1:
            logger.info("Draft check failed; retrying once")
            prompt = (
                user
                + "\n\nThe previous draft failed these checks:\n"
                + "\n".join(f"- {error}" for error in last_errors)
                + "\n\nFix those checks. Respond with one JSON object and no other text."
            )
    raise DraftError("Draft failed checks after one retry:\n" + "\n".join(last_errors))


def call_anthropic(
    system: str,
    user: str,
    *,
    model: str,
    api_key: str,
    max_tokens: int,
    effort: str,
    version: str,
    transport=None,
) -> dict:
    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if effort:
        body["output_config"] = {"effort": effort}
    if transport is not None:
        return transport(body)
    url = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or API_URL
    data = json.dumps(body).encode()
    last_error = "no response"
    for attempt in range(3):
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": version,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code in {429, 500, 503, 529} and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise DraftError(f"Anthropic API request failed. {last_error}") from exc
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise DraftError(f"Anthropic API request failed. {last_error}") from exc
    raise DraftError(f"Anthropic API request failed. {last_error}")


def response_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise DraftError("The model response was not a JSON object.")
    if payload.get("stop_reason") == "refusal":
        raise DraftError("The model refused the request. No draft was written.")
    if payload.get("stop_reason") == "max_tokens":
        raise DraftError(
            "The model response was cut off at max_tokens. "
            "Raise ANTHROPIC_MAX_TOKENS and run again. No draft was written."
        )
    parts = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            parts.append(block["text"])
    if not parts:
        raise DraftError("The model response had no text block. No draft was written.")
    return "\n".join(parts)


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the model response")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"the model response was not valid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError("the model response JSON was not an object")
    return payload


def _sector_document(brief: SectorBrief, settings: dict) -> dict:
    return {
        "schema_version": 1,
        "kind": "sector",
        "sector": brief.sector,
        "model": settings["model"],
        "prompt_file": "sector_brief.md",
        "input_generated_at": settings["input_generated_at"],
        "divergence": {
            "text": brief.divergence,
            "evidence": [_evidence_dict(item) for item in brief.divergence_evidence],
        },
        "themes": [_theme_dict(theme) for theme in brief.themes],
    }


def _overview_document(draft: OverviewDraft, settings: dict) -> dict:
    return {
        "schema_version": 2,
        "kind": "overview",
        "model": settings["model"],
        "prompt_file": "overview_brief.md",
        "input_generated_at": settings["input_generated_at"],
        "themes": [_theme_dict(theme) for theme in draft.themes],
        "sectors": [
            {"sector": sector, "takeaways": [_theme_dict(theme) for theme in themes]}
            for sector, themes in draft.takeaways.items()
        ],
    }


def _record_usage(settings: dict, response: dict, label: str, attempt: int) -> None:
    usage = response.get("usage") if isinstance(response, dict) else None
    row = {
        "label": label,
        "attempt": attempt,
        "model": settings["model"],
        "input_tokens": None,
        "output_tokens": None,
    }
    if isinstance(usage, dict):
        row["input_tokens"] = usage.get("input_tokens")
        row["output_tokens"] = usage.get("output_tokens")
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if key in usage:
                row[key] = usage[key]
    settings["usage"].append(row)
    path = settings.get("usage_path")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"model": settings["model"], "requests": settings["usage"]}, indent=2) + "\n"
        )


def _public_sector(payload: dict) -> dict:
    """Sector JSON the overview model is allowed to copy from."""
    return {
        "sector": payload.get("sector"),
        "divergence": payload.get("divergence"),
        "themes": payload.get("themes"),
    }


def _theme_dict(theme: Theme) -> dict:
    return {
        "title": theme.title,
        "what_changed": theme.what_changed,
        "audit_consideration": theme.audit_consideration,
        "evidence": [_evidence_dict(item) for item in theme.evidence],
    }


def _evidence_dict(evidence: Evidence) -> dict:
    return {
        "ticker": evidence.ticker,
        "accession": evidence.accession,
        "item": evidence.item,
        "report_date": evidence.report_date,
        "change": evidence.change,
        "quote": evidence.quote,
    }


def _publish(out_dir: Path, sectors: list[tuple[str, SectorBrief, dict]], overview: dict) -> None:
    sector_dir = out_dir / "sectors"
    sector_dir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for sector, _brief, payload in sectors:
        filename = f"{sector_slug(sector)}.json"
        keep.add(filename)
        _write(sector_dir / filename, payload)
    for stale in sector_dir.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
    _write(out_dir / "overview.json", overview)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
