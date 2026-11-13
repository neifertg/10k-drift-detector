from datetime import date, timedelta

from drift_detector.config import load_config
from drift_detector.edgar import (
    document_url,
    lookback_window,
    ranges_overlap,
    require_user_agent,
    select_10ks,
)
import pytest


def test_selects_three_years_and_keeps_the_amendment_out_of_the_diff():
    rows = []
    for year, filed in (("2025-12-31", "2026-02-13"), ("2024-12-31", "2025-02-14"), ("2023-12-31", "2024-02-16"), ("2022-12-31", "2023-02-21")):
        rows.append(
            {
                "form": "10-K",
                "reportDate": year,
                "filingDate": filed,
                "accessionNumber": f"acc-{year}",
                "primaryDocument": f"{year}.htm",
            }
        )
    rows.append(
        {
            "form": "10-K/A",
            "reportDate": "2025-12-31",
            "filingDate": "2026-04-01",
            "accessionNumber": "acc-amend",
            "primaryDocument": "amend.htm",
        }
    )
    rows.append({"form": "8-K", "reportDate": "2026-01-02", "filingDate": "2026-01-02", "accessionNumber": "8k", "primaryDocument": "8k.htm"})
    # A second 10-K for the same period is not the one we compare.
    rows.append(
        {
            "form": "10-K",
            "reportDate": "2025-12-31",
            "filingDate": "2026-02-20",
            "accessionNumber": "acc-later",
            "primaryDocument": "later.htm",
        }
    )
    chosen = select_10ks(rows, 3)
    assert [c.report_date for c in chosen] == ["2025-12-31", "2024-12-31", "2023-12-31"]
    assert chosen[0].accession == "acc-2025-12-31"
    assert chosen[0].amendments[0]["accessionNumber"] == "acc-amend"
    assert chosen[0].other_10k[0]["accessionNumber"] == "acc-later"


def test_lookback_window_covers_the_prior_filing_season():
    start, end = lookback_window("2026-02-13")
    assert start <= "2025-02-14" <= end
    assert not ranges_overlap({"filingFrom": "2025-08-01", "filingTo": "2025-09-01"}, (start, end))
    assert ranges_overlap({"filingFrom": "2025-02-11", "filingTo": "2025-03-12", "name": "x"}, (start, end))


def test_document_url_uses_the_registrant_cik():
    url = document_url("0000019617", "0001628280-26-008131", "jpm-20251231.htm")
    assert url == "https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm"


def test_user_agent_required():
    with pytest.raises(Exception):
        require_user_agent("")
    assert "example.com" in require_user_agent("Jane Analyst jane@example.com")


def test_config_is_the_peer_set():
    cfg = load_config("config/companies.yml")
    assert [c.ticker for c in cfg.companies] == [
        "JPM", "BAC", "V", "MA", "AAPL", "MSFT", "WMT", "COST", "UNH", "JNJ", "XOM", "CVX",
    ]
    assert cfg.sectors == ["Banking", "Payments", "Technology", "Retail", "Healthcare", "Energy"]
    assert all(len(members) == 2 for _, members in cfg.pairs())
    xom = cfg.by_ticker()["XOM"]
    assert xom.cik == "0000034088"
    assert cfg.by_ticker()["JPM"].cik is None


def test_window_dates_are_real_dates():
    start, end = lookback_window("2026-02-13", wide=True)
    assert date.fromisoformat(start) < date.fromisoformat("2026-02-13") - timedelta(days=200)
    assert date.fromisoformat(end) < date.fromisoformat("2026-02-13")
