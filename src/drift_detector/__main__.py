"""Command line.

    export SEC_USER_AGENT="Your Name you@example.com"
    python -m drift_detector run --config config/companies.yml --out docs

    export ANTHROPIC_API_KEY="..."
    python -m drift_detector draft-briefs --input briefs/input --out briefs/drafts
    python -m drift_detector verify-briefs --input briefs/input --drafts briefs/drafts
    python -m drift_detector render --input briefs/input --drafts briefs/drafts --out docs
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
    run.add_argument("--bundle", default="briefs/input", help="Where a full run writes the drafting input.")
    run.add_argument("--drafts", default="briefs/drafts", help="Verified drafts are included when this folder has them.")

    draft = sub.add_parser(
        "draft-briefs",
        help="Draft the audit-plan briefs from an exported input bundle, using the Anthropic API.",
    )
    draft.add_argument("--input", default="briefs/input")
    draft.add_argument("--out", default="briefs/drafts")
    draft.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the packet sizes and do not call the API.",
    )

    verify = sub.add_parser(
        "verify-briefs",
        help="Check every draft quote against the stored filing extract. A mismatch exits non-zero.",
    )
    verify.add_argument("--input", default="briefs/input")
    verify.add_argument("--drafts", default="briefs/drafts")

    render = sub.add_parser(
        "render",
        help="Rebuild the HTML report from the input bundle. Verified drafts become the overview and sector briefs.",
    )
    render.add_argument("--input", default="briefs/input")
    render.add_argument("--drafts", default="briefs/drafts")
    render.add_argument("--out", default="docs")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.cmd == "run":
        from drift_detector.edgar import EdgarError
        from drift_detector.pipeline import run as run_pipeline

        try:
            return run_pipeline(
                Path(args.config),
                Path(args.out),
                Path(args.cache),
                only=args.only,
                bundle_dir=Path(args.bundle),
                drafts_dir=Path(args.drafts),
            )
        except EdgarError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.cmd == "draft-briefs":
        from drift_detector.draft import DraftError, draft_briefs

        try:
            draft_briefs(Path(args.input), Path(args.out), dry_run=args.dry_run)
        except (DraftError, FileNotFoundError, ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    if args.cmd == "verify-briefs":
        return _verify(Path(args.input), Path(args.drafts))
    if args.cmd == "render":
        from drift_detector.pipeline import render_from_bundle

        try:
            return render_from_bundle(Path(args.input), Path(args.drafts), Path(args.out))
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return 0


def _verify(bundle_dir: Path, drafts_dir: Path) -> int:
    from drift_detector.bundle import load_bundle
    from drift_detector.verify import BriefVerificationError, drafts_present, verify_drafts

    try:
        _manifest, companies = load_bundle(bundle_dir)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not drafts_present(drafts_dir):
        print(f"No drafts in {drafts_dir}. Nothing to verify.")
        return 0
    try:
        briefs = verify_drafts(drafts_dir, companies)
    except BriefVerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"Verified {len(briefs.overview)} overview themes and {len(briefs.sectors)} sector briefs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
