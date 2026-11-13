"""Command line.

    export SEC_USER_AGENT="Your Name you@example.com"
    python -m drift_detector run --config config/companies.yml --out docs
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Item 1A and Item 9A across consecutive 10-Ks.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Fetch filings, compare them, and write the HTML report.")
    run.add_argument("--config", default="config/companies.yml")
    run.add_argument("--out", default="docs")
    run.add_argument("--cache", default="data/cache")
    run.add_argument("--only", default="", help="Comma-separated tickers, for a partial run.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.cmd == "run":
        from drift_detector.pipeline import run as run_pipeline

        try:
            run_pipeline(
                Path(args.config),
                Path(args.out),
                Path(args.cache),
                only=args.only,
            )
        except Exception as exc:
            from drift_detector.edgar import EdgarError

            if isinstance(exc, EdgarError):
                print(str(exc), file=sys.stderr)
                return 2
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
