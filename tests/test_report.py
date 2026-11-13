from drift_detector.models import CompanyResult, Flag, PeerResult, Quote
from drift_detector.report import write_report


def test_report_escapes_and_keeps_the_quote_and_link(tmp_path):
    quote = Quote(
        text='Management concluded that the disclosure controls were effective as of December 31, 2025.',
        item="9A",
        report_date="2025-12-31",
        accession="0000000000-26-000001",
        document_url="https://www.sec.gov/Archives/edgar/data/1/000/filing.htm",
        passage_url="https://www.sec.gov/Archives/edgar/data/1/000/filing.htm#:~:text=Management",
    )
    company = CompanyResult(
        ticker="JPM",
        display_name="JPMorgan Chase & Co.",
        edgar_name="JPMORGAN CHASE & CO",
        cik="0000019617",
        sector="Banking",
        index_lines=["Disclosure controls: effective in the 2025-12-31 extract."],
        comparisons=[],
        error="<script>alert(1)</script> No 10-K was retrieved.",
    )
    # The error path is used when there are no filings. Put the quote on a peer signal instead.
    from drift_detector.models import PeerSignal

    peer = PeerResult(
        sector="Banking",
        left_ticker="JPM",
        right_ticker="JPM",
        supported=False,
        reason="Comparison not supported.",
        period_statement="Only one company was rendered in this fixture.",
        period_kind="unavailable",
        gap_days=None,
        index_line="Peer comparison is not supported.",
        signals=[
            PeerSignal(
                signal="disclosure_controls",
                title="Disclosure controls conclusion",
                left_statement="effective in the 2025-12-31 extract.",
                right_statement="not compared.",
                left_quotes=[quote],
                right_quotes=[],
            )
        ],
    )
    write_report([company], [peer], tmp_path, "2026-11-13 00:00 UTC")
    index = (tmp_path / "index.html").read_text()
    peer_html = (tmp_path / "peers" / "banking.html").read_text()
    company_html = (tmp_path / "company" / "jpm.html").read_text()
    assert "<script>" not in index
    assert "<script>" not in company_html
    assert "&lt;script&gt;" in company_html
    assert quote.text in peer_html
    assert "https://www.sec.gov/Archives/edgar/data/1/000/filing.htm" in peer_html
    assert "No conclusion is drawn" in company_html or "No comparison is drawn" in company_html
    assert "Optional model summary" not in index
    assert "Briefs not yet generated" in index
    assert "Emerging risks for the audit plan" in index


def test_already_paired_leftover_is_not_described_as_below_the_threshold(tmp_path):
    from drift_detector.models import AlignedRow, ItemComparison

    row = AlignedRow(
        kind="already_paired",
        similarity=0.52,
        label="Funding",
        prior_text="Wholesale funding markets can close during stress and force asset sales.",
        prior_report_date="2024-12-31",
        current_report_date="2025-12-31",
        closest_text="Wholesale funding markets can close during stress and force securities sales.",
        closest_similarity=0.52,
    )
    company = CompanyResult(
        ticker="BAC",
        display_name="Bank of America Corporation",
        edgar_name="BANK OF AMERICA CORP /DE/",
        cik="0000070858",
        sector="Banking",
        comparisons=[
            ItemComparison(
                item="1A",
                prior_report_date="2024-12-31",
                current_report_date="2025-12-31",
                supported=True,
                reason=None,
                full_overlap=0.8,
                counts={"uncertain": 0, "already_paired": 1, "added": 0, "removed": 0, "changed": 0, "cosmetic": 0, "unchanged": 0},
                rows=[row],
                prior_status="ok",
                current_status="ok",
                segmentation_prior="headings",
                segmentation_current="headings",
            )
        ],
    )
    peer = PeerResult(
        sector="Banking",
        left_ticker="BAC",
        right_ticker="BAC",
        supported=False,
        reason="Fixture.",
        period_statement="Fixture.",
        period_kind="unavailable",
        gap_days=None,
        index_line="Fixture.",
    )
    write_report([company], [peer], tmp_path, "2026-11-13 02:00 UTC")
    page = (tmp_path / "company" / "bac.html").read_text()
    assert "already paired" in page
    assert "already matched one-to-one" in page
    assert "overlaps at 0.52, which is below 0.45" not in page
    assert "up to, but not including, 0.45" in page
    assert "It is not investment advice." in page


def test_llm_hook_is_off_by_default(monkeypatch):
    from drift_detector.llm import maybe_summarize

    monkeypatch.delenv("DRIFT_LLM_SUMMARY", raising=False)
    monkeypatch.delenv("DRIFT_LLM_ENDPOINT", raising=False)
    assert maybe_summarize({"lines": []}) is None
    monkeypatch.setenv("DRIFT_LLM_SUMMARY", "1")
    assert maybe_summarize({"lines": []}) is None
