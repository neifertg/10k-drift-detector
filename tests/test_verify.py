import copy
import json

import pytest

from drift_detector.models import AlignedRow, CompanyResult, Section
from drift_detector.verify import named_tickers
from drift_detector.textutil import clean_text
from drift_detector.verify import BriefVerificationError, validate_document, verify_drafts
from tests.fixtures.example_brief import (
    ALPHA_ADDED,
    ALPHA_CURRENT,
    ALPHA_PRIOR,
    companies,
    overview_draft,
    sector_draft,
)


def test_fixture_quotes_match_the_extracts(tmp_path):
    briefs = _write(tmp_path, sector_draft(), overview_draft())
    verified = verify_drafts(briefs, companies())
    assert verified.overview[0].title == "Artificial intelligence"
    assert verified.overview[0].evidence[0].quote == ALPHA_ADDED
    assert ALPHA_CURRENT in verified.overview[0].evidence[0].document_url or verified.overview[0].evidence[0].accession == ALPHA_CURRENT
    assert "Filing" not in verified.overview[0].evidence[0].quote


def test_whitespace_and_nbsp_still_match():
    drafted = companies()
    alpha = drafted[0]
    filing = next(item for item in alpha.filings if item.accession == ALPHA_CURRENT)
    broken = ALPHA_ADDED.replace("artificial intelligence", "artificial\u00a0intelligence")
    broken = broken.replace("customer service", "customer\n   service")
    filing.sections["1A"].text = filing.sections["1A"].text.replace(ALPHA_ADDED, broken)
    for row in alpha.comparisons[0].rows:
        if row.current_text:
            row.current_text = row.current_text.replace(ALPHA_ADDED, broken)
    payload = sector_draft()
    brief, errors = validate_document(payload, drafted, kind="sector", sector="Example")
    assert errors == []
    assert brief is not None
    assert clean_text(ALPHA_ADDED) in clean_text(broken)


def test_a_changed_word_fails():
    payload = sector_draft()
    payload["themes"][0]["evidence"][0]["quote"] = ALPHA_ADDED.replace(
        "artificial intelligence", "quantum computing"
    )
    _brief, errors = validate_document(payload, companies(), kind="sector", sector="Example")
    assert errors
    assert any("not in the Item 1A extract" in error for error in errors)


def test_wrong_accession_fails():
    payload = sector_draft()
    payload["themes"][0]["evidence"][0]["accession"] = ALPHA_PRIOR
    payload["themes"][0]["evidence"][0]["report_date"] = "2024-12-31"
    _brief, errors = validate_document(payload, companies(), kind="sector", sector="Example")
    assert errors
    assert any("not in the Item 1A extract" in error or "not part of the latest" in error for error in errors)


def test_quote_in_the_extract_but_not_in_a_change_fails():
    drafted = companies()
    alpha = drafted[0]
    filing = next(item for item in alpha.filings if item.accession == ALPHA_CURRENT)
    extra = "Alpha also leases its headquarters under a contract that renews every fifth year."
    filing.sections["1A"].text = filing.sections["1A"].text + " " + extra
    payload = sector_draft()
    payload["themes"][0]["evidence"][0]["quote"] = extra
    _brief, errors = validate_document(payload, drafted, kind="sector", sector="Example")
    assert errors
    assert any("not in a stored added passage" in error for error in errors)


def test_overview_cannot_invent_a_quote_that_is_not_in_a_sector_brief(tmp_path):
    bad_overview = overview_draft()
    bad_overview["themes"][0]["what_changed"] = (
        "Beta's latest risk-factor section adds a sentence on tariffs increasing the cost of imported merchandise. "
        "The sentence says that cost may rise."
    )
    bad_overview["themes"][0]["evidence"][0]["quote"] = (
        "Beta's cost of goods may rise if tariffs increase on imported merchandise sold in its stores."
    )
    bad_overview["themes"][0]["evidence"][0]["ticker"] = "BBB"
    bad_overview["themes"][0]["evidence"][0]["accession"] = "0000000002-26-000010"
    briefs = _write(tmp_path, sector_draft(), bad_overview)
    # The tariff sentence is a real added passage, but this sector theme did not use it
    # as evidence, and the overview theme is trying to introduce it on its own.
    # Put the tariff quote on the sector divergence so it IS allowed, then remove it
    # from the sector file to show the gate. The file written above already has the
    # tariff quote on the divergence, so this overview quote is allowed.
    verified = verify_drafts(briefs, companies())
    assert verified.overview[0].evidence[0].ticker == "BBB"

    sector = sector_draft()
    sector["divergence"]["text"] = (
        "Alpha added language on artificial intelligence in customer service, and the peer extract does not contain that sentence. "
        "The comparison uses only the extracted passages."
    )
    sector["divergence"]["evidence"] = [sector["divergence"]["evidence"][0]]
    sector["themes"][0]["evidence"] = [sector["themes"][0]["evidence"][0]]
    blocked = _write(tmp_path / "blocked", sector, bad_overview)
    with pytest.raises(BriefVerificationError) as caught:
        verify_drafts(blocked, companies())
    assert any("not one of the sector-brief quotes" in error for error in caught.value.errors)


def test_incomplete_draft_set_fails(tmp_path):
    sector_dir = tmp_path / "sectors"
    sector_dir.mkdir()
    (sector_dir / "example.json").write_text(json.dumps(sector_draft()))
    with pytest.raises(BriefVerificationError) as caught:
        verify_drafts(tmp_path, companies())
    assert any("overview.json" in error for error in caught.value.errors)


def test_prose_cannot_name_a_company_it_does_not_cite():
    payload = sector_draft()
    payload["themes"][0]["what_changed"] = (
        "Alpha's latest risk-factor section adds a sentence on incorrect responses from artificial intelligence. "
        "Beta's latest section does not contain that sentence."
    )
    _brief, errors = validate_document(payload, companies(), kind="sector", sector="Example")
    assert any("Beta Audit Co." in error and "no quote" in error for error in errors)


def test_three_artificial_intelligence_themes_must_be_merged():
    payload = overview_draft()
    theme = payload["themes"][0]
    payload["themes"] = []
    for title in (
        "Artificial intelligence in service",
        "Artificial intelligence in operations",
        "Artificial intelligence in controls",
    ):
        copy = dict(theme)
        copy["title"] = title
        payload["themes"].append(copy)
    _brief, errors = validate_document(payload, companies(), kind="overview")
    assert any("artificial intelligence" in error for error in errors)


def test_ordinary_words_are_not_company_names():
    costco = CompanyResult(
        ticker="COST",
        display_name="Costco Wholesale Corporation",
        edgar_name="COSTCO",
        cik="0000909832",
        sector="Retail",
    )
    bac = CompanyResult(
        ticker="BAC",
        display_name="Bank of America Corporation",
        edgar_name="BANK OF AMERICA",
        cik="0000070858",
        sector="Banking",
    )
    jnj = CompanyResult(
        ticker="JNJ",
        display_name="Johnson & Johnson",
        edgar_name="JOHNSON",
        cik="0000200406",
        sector="Healthcare",
    )
    assert named_tickers("the cost of goods may rise", [costco]) == []
    assert named_tickers("Costco expanded its trade language.", [costco]) == ["COST"]
    assert named_tickers("COST expanded its trade language.", [costco]) == ["COST"]
    assert named_tickers("a bank could fail in a stress", [bac]) == []
    assert named_tickers("Bank of America expanded its capital language.", [bac]) == ["BAC"]
    assert named_tickers("J&J added a separation risk.", [jnj]) == ["JNJ"]


def test_two_sentences_are_required():
    payload = sector_draft()
    payload["themes"][0]["what_changed"] = "Only one sentence about Alpha."
    _brief, errors = validate_document(payload, companies(), kind="sector", sector="Example")
    assert any("what_changed has 1 sentences" in error for error in errors)


def _write(directory, sector, overview):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sectors").mkdir(exist_ok=True)
    (directory / "sectors" / "example.json").write_text(json.dumps(sector))
    (directory / "overview.json").write_text(json.dumps(overview))
    return directory


def test_round_trip_keeps_a_newline_inside_the_extract(tmp_path):
    from drift_detector.bundle import export_bundle, load_bundle

    drafted = companies()
    alpha = next(company for company in drafted if company.ticker == "AAA")
    filing = next(item for item in alpha.filings if item.accession == ALPHA_CURRENT)
    filing.sections["1A"].text = "line one\nline two stays"
    export_bundle(drafted, tmp_path, "2026-11-13 00:00 UTC", sectors=["Example"], extractor_version=5)
    _manifest, loaded = load_bundle(tmp_path)
    restored = next(company for company in loaded if company.ticker == "AAA")
    restored_filing = next(item for item in restored.filings if item.accession == ALPHA_CURRENT)
    assert restored_filing.sections["1A"].text == "line one\nline two stays"
    assert isinstance(restored, CompanyResult)
    assert isinstance(restored.sections if False else restored_filing.sections["1A"], Section)


def test_change_label_must_match_the_row_kind():
    payload = copy.deepcopy(sector_draft())
    payload["themes"][0]["evidence"][0]["change"] = "removed"
    _brief, errors = validate_document(payload, companies(), kind="sector", sector="Example")
    assert any("not in a stored removed passage" in error for error in errors)
    unused = AlignedRow(
        kind="added",
        similarity=None,
        label="",
        current_text=ALPHA_ADDED,
        current_accession=ALPHA_CURRENT,
        current_report_date="2025-12-31",
    )
    assert unused.current_text == ALPHA_ADDED
