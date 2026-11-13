"""Hand-made filing extracts and a brief, for the verifier and the report.

The sentences are not from a Form 10-K. They exist so the renderer and the
quote check can run without an API key and without pretending the result is
the peer-set report.
"""

from __future__ import annotations

from drift_detector.models import (
    AlignedRow,
    CompanyResult,
    FilingRecord,
    ItemComparison,
    Passage,
    Section,
)

ALPHA_ADDED = (
    "Alpha also faces risk from the use of artificial intelligence in customer service, "
    "which could produce incorrect responses to clients."
)
BETA_TARIFF = (
    "Beta's cost of goods may rise if tariffs increase on imported merchandise sold in its stores."
)
ALPHA_BASE = (
    "Alpha depends on suppliers in one region. A disruption could delay shipments of merchandise to stores."
)
BETA_BASE = (
    "Beta depends on suppliers in one region. A disruption could delay shipments of merchandise to stores."
)

ALPHA_CURRENT = "0000000001-26-000010"
ALPHA_PRIOR = "0000000001-25-000010"
BETA_CURRENT = "0000000002-26-000010"
BETA_PRIOR = "0000000002-25-000010"


def companies() -> list[CompanyResult]:
    return [
        _company(
            ticker="AAA",
            name="Alpha Audit Co.",
            cik="0000000001",
            prior_accession=ALPHA_PRIOR,
            current_accession=ALPHA_CURRENT,
            base=ALPHA_BASE,
            added=ALPHA_ADDED,
            label="Artificial intelligence",
            document_stem="aaa",
        ),
        _company(
            ticker="BBB",
            name="Beta Audit Co.",
            cik="0000000002",
            prior_accession=BETA_PRIOR,
            current_accession=BETA_CURRENT,
            base=BETA_BASE,
            added=BETA_TARIFF,
            label="Tariffs",
            document_stem="bbb",
        ),
    ]


def sector_draft() -> dict:
    return {
        "schema_version": 1,
        "kind": "sector",
        "sector": "Example",
        "divergence": {
            "text": (
                "Alpha added language on artificial intelligence in customer service, and Beta did not. "
                "Beta added language on tariffs, and Alpha did not."
            ),
            "evidence": [
                _evidence("AAA", ALPHA_CURRENT, ALPHA_ADDED, "added"),
                _evidence("BBB", BETA_CURRENT, BETA_TARIFF, "added"),
            ],
        },
        "themes": [
            {
                "title": "Artificial intelligence in customer service",
                "what_changed": (
                    "Alpha's latest risk-factor section adds a sentence on incorrect responses from "
                    "artificial intelligence used in customer service. "
                    "Beta's latest section does not contain that sentence."
                ),
                "audit_consideration": (
                    "Ask whether customer-service responses produced by a model are reviewed before they reach a client."
                ),
                "evidence": [_evidence("AAA", ALPHA_CURRENT, ALPHA_ADDED, "added")],
            }
        ],
    }


def overview_draft() -> dict:
    return {
        "schema_version": 1,
        "kind": "overview",
        "themes": [
            {
                "title": "Artificial intelligence",
                "what_changed": (
                    "Alpha added a risk-factor sentence on incorrect responses from artificial intelligence "
                    "in customer service. The other company in the pair did not add that sentence."
                ),
                "audit_consideration": (
                    "Ask whether a model that answers clients is checked before the response is sent."
                ),
                "evidence": [_evidence("AAA", ALPHA_CURRENT, ALPHA_ADDED, "added")],
            }
        ],
    }


def _evidence(ticker: str, accession: str, quote: str, change: str) -> dict:
    return {
        "ticker": ticker,
        "accession": accession,
        "item": "1A",
        "report_date": "2025-12-31",
        "change": change,
        "quote": quote,
    }


def _company(
    *,
    ticker: str,
    name: str,
    cik: str,
    prior_accession: str,
    current_accession: str,
    base: str,
    added: str,
    label: str,
    document_stem: str,
) -> CompanyResult:
    prior_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{document_stem}-2024.htm"
    current_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{document_stem}-2025.htm"
    prior = _filing(prior_accession, "2024-12-31", "2025-02-01", prior_url, base, base)
    current = _filing(
        current_accession,
        "2025-12-31",
        "2026-02-01",
        current_url,
        f"{base} {added}",
        base,
        added,
    )
    comparison = ItemComparison(
        item="1A",
        prior_report_date="2024-12-31",
        current_report_date="2025-12-31",
        supported=True,
        reason=None,
        full_overlap=0.55,
        counts={"added": 1, "removed": 0, "changed": 0, "cosmetic": 0, "uncertain": 0, "already_paired": 0, "unchanged": 1},
        rows=[
            AlignedRow(
                kind="added",
                similarity=0.05,
                label=label,
                current_text=added,
                prior_report_date="2024-12-31",
                current_report_date="2025-12-31",
                prior_document_url=prior_url,
                current_document_url=current_url,
                prior_accession=prior_accession,
                current_accession=current_accession,
            )
        ],
        prior_status="ok",
        current_status="ok",
        prior_header="Item 1A. Risk Factors.",
        current_header="Item 1A. Risk Factors.",
        prior_end="Item 1B.",
        current_end="Item 1B.",
        segmentation_prior="paragraphs",
        segmentation_current="paragraphs",
    )
    return CompanyResult(
        ticker=ticker,
        display_name=name,
        edgar_name=name.upper(),
        cik=cik,
        sector="Example",
        filings=[current, prior],
        comparisons=[comparison],
        edgar_browse_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
    )


def _filing(accession, report_date, filing_date, document_url, section_text, *passages: str) -> FilingRecord:
    item_1a = Section(
        item="1A",
        status="ok",
        header="Item 1A. Risk Factors.",
        end_header="Item 1B.",
        text=section_text,
        segmentation="paragraphs",
        passages=[Passage(text=text, title=None, part=None) for text in passages],
    )
    item_9a = Section(
        item="9A",
        status="ok",
        header="Item 9A. Controls and Procedures.",
        end_header="Item 9B.",
        text="Management concluded that the disclosure controls and procedures were effective as of the period end.",
        segmentation="paragraphs",
        passages=[
            Passage(
                text="Management concluded that the disclosure controls and procedures were effective as of the period end.",
                title=None,
                part=None,
            )
        ],
    )
    return FilingRecord(
        report_date=report_date,
        filing_date=filing_date,
        accession=accession,
        primary_document=document_url.rsplit("/", 1)[-1],
        document_url=document_url,
        index_url=document_url + "-index.html",
        sha256="0" * 64,
        sections={"1A": item_1a, "9A": item_9a},
    )
