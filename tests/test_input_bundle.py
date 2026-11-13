"""The committed briefs/input folder is the drafting script's source.

Quotes in any committed draft must match these extracts. An empty drafts
folder is the expected state until someone runs draft-briefs.
"""

import json
from pathlib import Path

from drift_detector.bundle import load_bundle
from drift_detector.verify import BriefVerificationError, drafts_present, verify_drafts

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "briefs" / "input"
DRAFTS = ROOT / "briefs" / "drafts"
TICKERS = ["JPM", "BAC", "V", "MA", "AAPL", "MSFT", "WMT", "COST", "UNH", "JNJ", "XOM", "CVX"]


def test_committed_bundle_has_every_peer_and_the_extracts_hold_the_change_text():
    manifest, companies = load_bundle(BUNDLE)
    assert [entry["ticker"] for entry in manifest["companies"]] == TICKERS
    assert [company.ticker for company in companies] == TICKERS
    sectors = {company.sector for company in companies}
    assert sectors == {"Banking", "Payments", "Technology", "Retail", "Healthcare", "Energy"}
    for company in companies:
        assert len(company.filings) >= 2
        by_accession = {filing.accession: filing for filing in company.filings}
        for comparison in company.comparisons:
            for row in comparison.rows:
                if row.prior_text:
                    filing = by_accession[row.prior_accession]
                    assert row.prior_text in filing.sections[comparison.item].text
                if row.current_text:
                    filing = by_accession[row.current_accession]
                    assert row.current_text in filing.sections[comparison.item].text
            for flag in comparison.flags:
                for quote in flag.quotes:
                    if not quote.text or not quote.accession:
                        continue
                    filing = by_accession.get(quote.accession)
                    assert filing is not None
                    section = filing.sections[quote.item]
                    # The search bound (the next item's heading) is quoted when a
                    # phrase is absent. It is not part of the section body, and the
                    # drafter does not offer it as a sentence the model may quote.
                    bounds = {section.header, section.end_header}
                    assert quote.text in section.text or quote.text in bounds


def test_committed_drafts_are_verified_when_present():
    if not drafts_present(DRAFTS):
        overview = DRAFTS / "overview.json"
        assert not overview.exists()
        return
    _manifest, companies = load_bundle(BUNDLE)
    try:
        verify_drafts(DRAFTS, companies)
    except BriefVerificationError as exc:
        raise AssertionError("Committed drafts failed quote verification:\n" + "\n".join(exc.errors)) from exc


def test_bundle_manifest_points_at_readable_company_files():
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    for entry in manifest["companies"]:
        path = BUNDLE / entry["file"]
        payload = json.loads(path.read_text())
        assert payload["ticker"] == entry["ticker"]
        assert payload["filings"][0]["sections"]["1A"]["text"]
        assert payload["filings"][0]["sections"]["9A"]["text"]
