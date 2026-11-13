from drift_detector.models import (
    AlignedRow,
    CompanyResult,
    FilingRecord,
    ItemComparison,
    Passage,
    Section,
)
from drift_detector.peers import build_peer, period_statement
from drift_detector.textutil import ALIGN_THRESHOLD


def test_period_statement_same_day_and_far_apart():
    kind, text, gap = period_statement(
        "JPMorgan Chase & Co.", "2025-12-31", "2024-12-31",
        "Bank of America Corporation", "2025-12-31", "2024-12-31",
    )
    assert kind == "same"
    assert gap == 0
    assert "2025-12-31" in text
    kind, text, gap = period_statement(
        "Visa Inc.", "2026-09-30", "2025-09-30",
        "Mastercard Incorporated", "2025-12-31", "2024-12-31",
    )
    assert kind == "different"
    assert gap > 120
    assert "not a same-fiscal-period" in text


def _company(ticker, name, added_text, peer_text, report="2025-12-31", prior="2024-12-31"):
    def section(body):
        text = f"Item 1A. Risk Factors {body}"
        return Section(
            item="1A",
            status="ok",
            header="Item 1A. Risk Factors",
            end_header="Item 1B. Unresolved Staff Comments",
            text=text,
            segmentation="headings",
            passages=[Passage(text=text, title="Risk")],
        )

    def filing(report_date, body):
        return FilingRecord(
            report_date=report_date,
            filing_date=report_date,
            accession=f"0000000000-{report_date[:4]}-000001",
            primary_document="x.htm",
            document_url=f"https://www.sec.gov/Archives/edgar/data/1/{report_date}/x.htm",
            index_url="https://www.sec.gov/Archives/edgar/data/1/index.html",
            sha256="abc",
            sections={"1A": section(body), "9A": Section(item="9A", status="missing")},
        )

    added = AlignedRow(
        kind="added",
        similarity=0.05,
        label="New risk",
        current_text=f"Item 1A. Risk Factors {added_text}",
        current_report_date=report,
        prior_report_date=prior,
        current_document_url=f"https://www.sec.gov/Archives/edgar/data/1/{report}/x.htm",
        current_url=f"https://www.sec.gov/Archives/edgar/data/1/{report}/x.htm#:~:text=Item",
        current_accession="0000000000-25-000001",
    )
    comparison = ItemComparison(
        item="1A",
        prior_report_date=prior,
        current_report_date=report,
        supported=True,
        reason=None,
        full_overlap=0.4,
        counts={"added": 1},
        rows=[added],
        prior_status="ok",
        current_status="ok",
        segmentation_prior="headings",
        segmentation_current="headings",
    )
    return CompanyResult(
        ticker=ticker,
        display_name=name,
        edgar_name=name,
        cik="0000000001",
        sector="Banking",
        filings=[filing(report, added_text), filing(prior, peer_text)],
        comparisons=[comparison],
    )


def test_distinct_addition_requires_low_overlap_and_a_quote():
    left_body = (
        "A newly disclosed concentration in aircraft lessors could reduce collateral recovery "
        "if air travel remains weak for several years and lessees default together."
    )
    right_body = (
        "Deposit costs may rise if customers move balances to money market funds after rate cuts "
        "and the bank has to replace them with wholesale funding."
    )
    # The added passage has to live in the left company's current section.
    left = _company("JPM", "JPMorgan Chase & Co.", left_body, "old")
    # Right company's passages are the peer pool. Use a different topic.
    right = _company("BAC", "Bank of America Corporation", right_body, right_body)
    # Fix left current section so the added quote is contained.
    peer = build_peer(left, right)
    assert peer.supported
    assert len(peer.left_only_added) == 1
    assert left_body in peer.left_only_added[0].text
    assert "sec.gov" in peer.left_only_added[0].passage_url
    assert peer.left_only_added[0].closest_similarity < ALIGN_THRESHOLD


def test_similar_text_is_not_called_absent():
    shared = (
        "Wholesale funding markets can close to the firm during stress, which would limit the ability "
        "to refinance maturing debt and meet cash obligations to clients and depositors."
    )
    left = _company("JPM", "JPMorgan Chase & Co.", shared, "unrelated prior text about branches")
    right = _company("BAC", "Bank of America Corporation", shared, shared)
    peer = build_peer(left, right)
    assert peer.left_only_added == []
    assert peer.shared_added >= 1


def test_failed_extraction_is_not_supported():
    left = _company("JPM", "JPMorgan Chase & Co.", "alpha " * 30, "beta")
    right = _company("BAC", "Bank of America Corporation", "gamma " * 30, "delta")
    right.comparisons[0].supported = False
    right.comparisons[0].reason = "Item 1A extraction status is missing for the period ended 2024-12-31."
    peer = build_peer(left, right)
    assert not peer.supported
    assert "not supported" in peer.index_line.lower() or "Item 1A" in peer.reason
    assert peer.left_only_added == []
