"""Read and write the drafting input bundle.

One JSON file per company holds the extracted Item 1A and Item 9A text and
the year-over-year comparison. ``draft-briefs`` reads this folder and does
not call EDGAR. Quote verification reads the same section text.
"""

from __future__ import annotations

import json
from pathlib import Path

from drift_detector.models import CompanyResult
from drift_detector.serialize import dump_company, load_company


def sector_slug(sector: str) -> str:
    return sector.lower().replace(" ", "-").replace("&", "and")


def export_bundle(
    companies: list[CompanyResult],
    dest: Path,
    generated_at: str,
    *,
    sectors: list[str],
    extractor_version: int,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    company_dir = dest / "companies"
    company_dir.mkdir(exist_ok=True)
    ordered = _order_companies(companies, sectors)
    entries = []
    keep: set[str] = set()
    for company in ordered:
        filename = f"{company.ticker.lower()}.json"
        keep.add(filename)
        path = company_dir / filename
        path.write_text(json.dumps(dump_company(company), ensure_ascii=False, indent=2) + "\n")
        entries.append(
            {
                "ticker": company.ticker,
                "name": company.display_name,
                "sector": company.sector,
                "file": f"companies/{filename}",
            }
        )
    for stale in company_dir.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
    manifest = {
        "generated_at": generated_at,
        "extractor_version": extractor_version,
        "description": (
            "Extracted Item 1A and Item 9A text and the year-over-year comparison "
            "for each company. This folder is the input to draft-briefs. "
            "A quote in a draft is checked against filings[].sections for the accession it cites."
        ),
        "sectors": list(sectors),
        "companies": entries,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def load_bundle(path: Path) -> tuple[dict, list[CompanyResult]]:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest at {manifest_path}. Expected the exported briefs/input folder.")
    manifest = json.loads(manifest_path.read_text())
    companies = []
    for entry in manifest.get("companies") or []:
        file_name = entry.get("file")
        if not file_name:
            raise ValueError("manifest company entry is missing file")
        payload = json.loads((path / file_name).read_text())
        companies.append(load_company(payload))
    return manifest, companies


def peer_groups(companies: list[CompanyResult], sectors: list[str]) -> list[tuple[str, list[CompanyResult]]]:
    grouped: dict[str, list[CompanyResult]] = {sector: [] for sector in sectors}
    for company in companies:
        grouped.setdefault(company.sector, []).append(company)
    ordered = []
    seen = set()
    for sector in list(sectors) + [company.sector for company in companies]:
        if sector in seen:
            continue
        seen.add(sector)
        ordered.append((sector, grouped.get(sector, [])))
    return ordered


def _order_companies(companies: list[CompanyResult], sectors: list[str]) -> list[CompanyResult]:
    by_ticker = {company.ticker: company for company in companies}
    ordered = []
    seen = set()
    for _sector, members in peer_groups(companies, sectors):
        for company in members:
            if company.ticker in by_ticker and company.ticker not in seen:
                ordered.append(company)
                seen.add(company.ticker)
    for company in companies:
        if company.ticker not in seen:
            ordered.append(company)
    return ordered
