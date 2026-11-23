# Drafting input

This folder is the export `python -m drift_detector run` writes after it reads EDGAR. It is the only input `draft-briefs` needs.

- `manifest.json` lists the companies and when the extracts were retrieved.
- `companies/<ticker>.json` holds each filing's Item 1A and Item 9A text and the year-over-year comparison.

Quote verification reads `filings[].sections`. A draft may quote only a span that appears there, and only from a stored change or an Item 9A quote for the accession it cites.

Copy this folder to another machine and point `--input` at it. The command does not call EDGAR.
