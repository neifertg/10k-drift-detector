# Drafted briefs

This folder holds the verified drafts from a `draft-briefs` run. `verify-briefs` and `render` use them to publish the audit-plan briefs in `docs/`.

- `overview.json` — cross-sector themes, plus one or two takeaways for each sector (schema version 2)
- `sectors/<sector>.json` — one brief per peer pair (schema version 1)
- `token-usage.json` — input and output tokens for each API request. It is a log, not a brief.

Do not put a brief here by hand. A quote that is not in the stored filing extract fails `verify-briefs` and is left off the report. Briefs publish only when every quote matches the stored extract and every named company is quoted on that item. The `verify_drafts()` check is all-or-nothing.

To refresh the drafts, rerun `draft-briefs` from a checkout that already has `briefs/input/`. That command does not call EDGAR.
