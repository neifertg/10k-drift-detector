"""Dataclasses shared by extraction, comparison, and the report."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Passage:
    text: str
    title: str | None = None
    part: str | None = None

    @property
    def label(self) -> str:
        if self.part and self.title and self.part != self.title:
            return f"{self.part} — {self.title}"
        if self.title:
            return self.title
        if self.part:
            return self.part
        return ""


@dataclass
class Section:
    item: str
    status: str  # ok, missing, incomplete
    header: str | None = None
    end_header: str | None = None
    text: str = ""
    passages: list[Passage] = field(default_factory=list)
    segmentation: str = "none"  # headings, paragraphs, none
    notes: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class Amendment:
    form: str
    filing_date: str
    accession: str
    document_url: str


@dataclass
class FilingRecord:
    report_date: str
    filing_date: str
    accession: str
    primary_document: str
    document_url: str
    index_url: str
    sha256: str
    sections: dict[str, Section] = field(default_factory=dict)
    amendments: list[Amendment] = field(default_factory=list)
    other_10k: list[Amendment] = field(default_factory=list)


@dataclass
class Quote:
    text: str
    item: str
    report_date: str
    accession: str
    document_url: str
    passage_url: str
    truncated: bool = False


@dataclass
class Flag:
    signal: str
    index_line: str
    statement: str
    quotes: list[Quote] = field(default_factory=list)


@dataclass
class AlignedRow:
    kind: str  # added, removed, changed, uncertain
    similarity: float | None
    label: str
    prior_text: str | None = None
    current_text: str | None = None
    prior_report_date: str | None = None
    current_report_date: str | None = None
    prior_url: str | None = None
    current_url: str | None = None
    prior_document_url: str | None = None
    current_document_url: str | None = None
    prior_accession: str | None = None
    current_accession: str | None = None
    closest_text: str | None = None
    closest_similarity: float | None = None
    closest_url: str | None = None
    closest_report_date: str | None = None


@dataclass
class ItemComparison:
    item: str
    prior_report_date: str
    current_report_date: str
    supported: bool
    reason: str | None
    full_overlap: float | None
    counts: dict[str, int]
    rows: list[AlignedRow] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    prior_status: str = ""
    current_status: str = ""
    prior_header: str | None = None
    current_header: str | None = None
    prior_end: str | None = None
    current_end: str | None = None
    segmentation_prior: str = "none"
    segmentation_current: str = "none"


@dataclass
class CompanyResult:
    ticker: str
    display_name: str
    edgar_name: str
    cik: str
    sector: str
    filings: list[FilingRecord] = field(default_factory=list)
    comparisons: list[ItemComparison] = field(default_factory=list)
    index_lines: list[str] = field(default_factory=list)
    error: str | None = None
    cik_note: str | None = None
    edgar_browse_url: str = ""


@dataclass
class PeerPassage:
    label: str
    text: str
    report_date: str
    accession: str
    document_url: str
    passage_url: str
    truncated: bool = False
    closest_text: str | None = None
    closest_similarity: float | None = None
    closest_url: str | None = None
    closest_report_date: str | None = None
    closest_document_url: str | None = None


@dataclass
class PeerSignal:
    signal: str
    title: str
    left_statement: str
    right_statement: str
    left_quotes: list[Quote] = field(default_factory=list)
    right_quotes: list[Quote] = field(default_factory=list)


@dataclass
class PeerResult:
    sector: str
    left_ticker: str
    right_ticker: str
    supported: bool
    reason: str | None
    period_statement: str
    period_kind: str  # same, close, different, unavailable
    gap_days: int | None
    signals: list[PeerSignal] = field(default_factory=list)
    left_only_added: list[PeerPassage] = field(default_factory=list)
    right_only_added: list[PeerPassage] = field(default_factory=list)
    left_only_removed: list[PeerPassage] = field(default_factory=list)
    right_only_removed: list[PeerPassage] = field(default_factory=list)
    uncertain: list[PeerPassage] = field(default_factory=list)
    shared_added: int = 0
    index_line: str = ""
