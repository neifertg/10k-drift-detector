"""Load the peer-company config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class CompanySpec:
    ticker: str
    name: str
    sector: str
    cik: str | None = None


@dataclass
class AppConfig:
    years: int
    request_interval_seconds: float
    sectors: list[str]
    companies: list[CompanySpec]

    def by_ticker(self) -> dict[str, CompanySpec]:
        return {c.ticker: c for c in self.companies}

    def pairs(self) -> list[tuple[str, list[CompanySpec]]]:
        grouped: dict[str, list[CompanySpec]] = {s: [] for s in self.sectors}
        for company in self.companies:
            grouped.setdefault(company.sector, []).append(company)
        ordered = []
        seen = set()
        for sector in list(self.sectors) + [s for s in grouped if s not in self.sectors]:
            if sector in seen:
                continue
            seen.add(sector)
            ordered.append((sector, grouped.get(sector, [])))
        return ordered


def _cik(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text:
        return None
    return text.zfill(10)


def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    years = int(raw.get("years") or 3)
    if years < 2 or years > 5:
        raise ValueError("years must be between 2 and 5")
    interval = float(raw.get("request_interval_seconds") or 0.2)
    if interval < 0.1:
        raise ValueError("request_interval_seconds must be at least 0.1 to stay under the SEC limit")
    sectors = [str(s).strip() for s in raw.get("sectors") or []]
    companies = []
    seen = set()
    for row in raw.get("companies") or []:
        ticker = str(row["ticker"]).strip().upper()
        if ticker in seen:
            raise ValueError(f"Duplicate ticker {ticker}")
        seen.add(ticker)
        sector = str(row["sector"]).strip()
        if sectors and sector not in sectors:
            raise ValueError(f"{ticker} sector {sector!r} is not in sectors")
        companies.append(
            CompanySpec(
                ticker=ticker,
                name=str(row["name"]).strip(),
                sector=sector,
                cik=_cik(row.get("cik")),
            )
        )
    if not companies:
        raise ValueError("config has no companies")
    return AppConfig(
        years=years,
        request_interval_seconds=interval,
        sectors=sectors,
        companies=companies,
    )
