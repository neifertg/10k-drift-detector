"""SEC EDGAR client.

Fair-access rules this module follows:
- User-Agent comes from the caller (the SEC_USER_AGENT environment variable).
- Requests are serial and at least `min_interval` seconds apart.
- 429 and 5xx responses are retried with backoff.
- Successful responses are cached on disk so a rerun does not fetch them again.
- Only data.sec.gov and www.sec.gov are requested.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {"www.sec.gov", "data.sec.gov"}
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"


class EdgarError(RuntimeError):
    pass


def require_user_agent(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) < 8:
        raise EdgarError(
            "Set SEC_USER_AGENT to a string that identifies you and gives the SEC a way to contact you. "
            "Example: 'Jane Analyst jane@example.com'. Do not commit that value."
        )
    return text


def submissions_url(cik: str) -> str:
    return f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"


def document_url(cik: str, accession: str, filename: str) -> str:
    acc = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{filename}"


def index_url(cik: str, accession: str) -> str:
    acc = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{accession}-index.html"


def filing_index_json(cik: str, accession: str) -> str:
    acc = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/index.json"


def browse_url(cik: str) -> str:
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={int(cik):010d}&type=10-K&owner=exclude&count=10"
    )


def _parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def lookback_window(filing_date: str, *, wide: bool = False) -> tuple[str, str]:
    """Window, a year before a known 10-K filing date, where the prior 10-K should sit."""
    center = _parse_day(filing_date) - timedelta(days=365)
    slack = 140 if wide else 70
    start = center - timedelta(days=slack)
    end = center + timedelta(days=45 if not wide else 90)
    return start.isoformat(), end.isoformat()


def ranges_overlap(file_meta: dict, window: tuple[str, str]) -> bool:
    start, end = window
    return file_meta.get("filingFrom", "9999") <= end and file_meta.get("filingTo", "0000") >= start


def iter_filing_rows(block: dict):
    if not block or "form" not in block:
        return
    n = len(block["form"])
    keys = (
        "form",
        "filingDate",
        "reportDate",
        "accessionNumber",
        "primaryDocument",
        "primaryDocDescription",
    )
    for i in range(n):
        row = {}
        for key in keys:
            values = block.get(key) or []
            row[key] = values[i] if i < len(values) else ""
        yield row


def distinct_10k_dates(rows: list[dict]) -> list[str]:
    dates = {r["reportDate"] for r in rows if r.get("form") == "10-K" and r.get("reportDate")}
    return sorted(dates, reverse=True)


@dataclass
class Selected10K:
    report_date: str
    filing_date: str
    accession: str
    primary_document: str
    amendments: list[dict]
    other_10k: list[dict]


def select_10ks(rows: list[dict], n: int) -> list[Selected10K]:
    """Pick the n most recent 10-K period ends. Amendments are attached, not selected."""
    by_date: dict[str, list[dict]] = {}
    amendments: dict[str, list[dict]] = {}
    for row in rows:
        form = row.get("form") or ""
        report = row.get("reportDate") or ""
        if not report:
            continue
        if form == "10-K":
            by_date.setdefault(report, []).append(row)
        elif form == "10-K/A":
            amendments.setdefault(report, []).append(row)
    chosen = []
    for report in sorted(by_date, reverse=True)[:n]:
        group = sorted(by_date[report], key=lambda r: r.get("filingDate") or "")
        primary = group[0]
        others = [
            {
                "form": "10-K",
                "filingDate": r.get("filingDate") or "",
                "accessionNumber": r.get("accessionNumber") or "",
                "primaryDocument": r.get("primaryDocument") or "",
            }
            for r in group[1:]
        ]
        ams = [
            {
                "form": "10-K/A",
                "filingDate": r.get("filingDate") or "",
                "accessionNumber": r.get("accessionNumber") or "",
                "primaryDocument": r.get("primaryDocument") or "",
            }
            for r in sorted(amendments.get(report, []), key=lambda r: r.get("filingDate") or "")
        ]
        chosen.append(
            Selected10K(
                report_date=report,
                filing_date=primary.get("filingDate") or "",
                accession=primary.get("accessionNumber") or "",
                primary_document=primary.get("primaryDocument") or "",
                amendments=ams,
                other_10k=others,
            )
        )
    return chosen


class EdgarClient:
    def __init__(self, user_agent: str, cache_dir: Path, min_interval: float = 0.2):
        self.user_agent = require_user_agent(user_agent)
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
            }
        )

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / "http" / digest

    def get_bytes(self, url: str) -> bytes:
        host = urlparse(url).hostname
        if host not in ALLOWED_HOSTS:
            raise EdgarError(f"Refusing non-EDGAR host: {host}")
        cached = self._path(url)
        if cached.exists() and cached.stat().st_size > 0:
            return cached.read_bytes()
        last_error: Exception | None = None
        for attempt in range(4):
            elapsed = time.monotonic() - self._last
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last = time.monotonic()
            try:
                response = self.session.get(url, timeout=120)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                wait = response.headers.get("Retry-After")
                try:
                    delay = min(int(wait), 30) if wait else min(2 ** attempt, 30)
                except ValueError:
                    delay = min(2 ** attempt, 30)
                time.sleep(delay)
                last_error = EdgarError(f"{response.status_code} from {url}")
                continue
            if response.status_code != 200:
                raise EdgarError(f"{response.status_code} from {url}")
            body = response.content
            if not body:
                raise EdgarError(f"Empty response from {url}")
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(body)
            return body
        raise EdgarError(f"Failed to fetch {url}: {last_error}")

    def get_json(self, url: str) -> dict:
        return json.loads(self.get_bytes(url))

    def get_text(self, url: str) -> tuple[bytes, str]:
        body = self.get_bytes(url)
        return body, decode_html(body)


def decode_html(body: bytes) -> str:
    import re

    head = body[:4000]
    found = re.search(br"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", head, re.I)
    encoding = found.group(1).decode() if found else "utf-8"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def ticker_map(client: EdgarClient) -> dict[str, dict]:
    payload = client.get_json(TICKER_URL)
    out = {}
    for row in payload.values():
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        out[ticker] = {
            "cik": f"{int(row['cik_str']):010d}",
            "title": row.get("title") or "",
        }
    return out


def gather_rows(client: EdgarClient, cik: str, years: int) -> tuple[dict, list[dict]]:
    """Load submissions, then only the extra pages that can hold older 10-Ks."""
    data = client.get_json(submissions_url(cik))
    rows = list(iter_filing_rows(data.get("filings", {}).get("recent", {})))
    files = list(data.get("filings", {}).get("files") or [])
    used: set[str] = set()
    stagnant = 0
    while len(distinct_10k_dates(rows)) < years and stagnant < 3:
        dates = distinct_10k_dates(rows)
        if not dates:
            break
        # The oldest 10-K we already have determines where the prior one should be.
        oldest = min(
            (r for r in rows if r.get("form") == "10-K" and r.get("reportDate")),
            key=lambda r: r.get("filingDate") or "9999",
        )
        before = len(dates)
        found_file = False
        for wide in (False, True):
            window = lookback_window(oldest["filingDate"], wide=wide)
            candidates = [
                f for f in files if f.get("name") not in used and ranges_overlap(f, window)
            ]
            for meta in candidates:
                name = meta["name"]
                used.add(name)
                found_file = True
                extra = client.get_json(f"https://data.sec.gov/submissions/{name}")
                rows.extend(iter_filing_rows(extra))
                logger.info("Paged submissions %s for CIK %s", name, cik)
            if len(distinct_10k_dates(rows)) >= years:
                break
            if found_file and len(distinct_10k_dates(rows)) > before:
                break
        if len(distinct_10k_dates(rows)) == before:
            stagnant += 1
        else:
            stagnant = 0
        if not found_file:
            break
    return data, rows
