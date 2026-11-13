# Emerging risks for the audit plan

Compares Item 1A (Risk Factors) and Item 9A (Controls and Procedures) across consecutive Form 10-Ks for six sector peer pairs, then drafts a short list of emerging or growing risks a chief audit executive can use when building the audit plan.

The short list is a reading of what the filings added or expanded. Every quote in it has to appear, after whitespace normalization, in the stored Item 1A or Item 9A extract it cites, and in a stored year-over-year passage or Item 9A quote for that accession. If a quote does not match, the brief is not published. The passage-level diffs stay on the company pages, one click away from the overview.

The companies are a fixed peer set, not a screen of the whole market: JPMorgan Chase and Bank of America, Visa and Mastercard, Apple and Microsoft, Walmart and Costco, UnitedHealth Group and Johnson & Johnson, Exxon Mobil and Chevron. The list is `config/companies.yml`.

This sits next to the [risk analytics portfolio](https://github.com/neifertg/risk-analytics-portfolio). No client data. The filings are public.

This is a wording comparison of public filings. It is not investment advice, and it is not a conclusion that a control is effective.

## Draft the briefs

From a fresh checkout. The input bundle is already in `briefs/input/`, so this does not call EDGAR and does not need `SEC_USER_AGENT`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your key"
PYTHONPATH=src python -m drift_detector draft-briefs --input briefs/input --out briefs/drafts
```

Environment variables:

| Variable | Required | Default |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes, except for `--dry-run` | |
| `ANTHROPIC_MODEL` | no | `claude-sonnet-5-5` |
| `ANTHROPIC_EFFORT` | no | `medium`. Set to `off` to omit the effort field. |
| `ANTHROPIC_MAX_TOKENS` | no | `16000` |
| `ANTHROPIC_VERSION` | no | `2026-10-21` |
| `ANTHROPIC_BASE_URL` | no | `https://api.anthropic.com/v1/messages` |

The command sends one request per sector, then one for the overview. The prompts are `src/drift_detector/prompts/sector_brief.md` and `src/drift_detector/prompts/overview_brief.md`. The model may quote only the change text in the request. A draft that fails the quote check is not written. One retry is sent with the failure, and a second failure stops the command.

Output:

- `briefs/drafts/overview.json`
- `briefs/drafts/sectors/<sector>.json`

To run against a copy of the bundle on another machine, pass that folder to `--input`. The folder is `manifest.json` plus `companies/*.json`. The script does not need `config/companies.yml` for this command.

```bash
PYTHONPATH=src python -m drift_detector draft-briefs --input /path/to/briefs/input --out /path/to/briefs/drafts
```

`--dry-run` prints the packet size for each sector and does not call the API.

## Check the quotes and rebuild the report

```bash
PYTHONPATH=src python -m drift_detector verify-briefs --input briefs/input --drafts briefs/drafts
PYTHONPATH=src python -m drift_detector render --input briefs/input --drafts briefs/drafts --out docs
```

`verify-briefs` exits non-zero if any quote is missing from the cited extract, if the quote is not in a stored change for that accession, or if the draft is the wrong shape. With no JSON in `briefs/drafts/`, it exits zero and prints that there is nothing to verify.

`render` reads the bundle, rebuilds the peer comparison, and writes `docs/`. Verified drafts become the overview and the six sector briefs. A failed check is written as a notice, the themes are left off the page, and the command exits non-zero. Company pages are still written. They do not depend on the model.

A full EDGAR run picks the drafts up the same way. After it writes `briefs/input/`, it reads `briefs/drafts/` and includes them when they verify:

```bash
export SEC_USER_AGENT="Your Name you@example.com"
PYTHONPATH=src python -m drift_detector run --config config/companies.yml --out docs
```

`--only JPM,BAC` limits a run to those tickers. A partial run does not rewrite the input bundle and does not apply drafts.

`PYTHONPATH=src pytest` runs the quote checks, including against `briefs/drafts/` when that folder contains JSON.

## What the pages are

`docs/index.html` is the overview: a few cross-sector themes, each with the companies that added or expanded it, two sentences on what changed, one audit-plan consideration, and the supporting quotes behind a disclosure. The quotes link to the filing.

`docs/peers/<sector>.html` is the same shape for one pair, including where the two peers diverge. The passage-level peer comparison is under "Passage-level comparison".

`docs/company/<ticker>.html` is the evidence: the Item 1A and Item 9A diffs, with each passage quoted from the filing.

Until `briefs/drafts/` has a verified set, the overview and the sector pages say **Briefs not yet generated**. They do not invent themes.

The extracts in `briefs/input/` and the pages in `docs/` were retrieved from EDGAR on 2026-11-13 03:32 UTC. No briefs have been drafted from them.

## How a change is called

For each company the fetch takes the three most recent Form 10-Ks on EDGAR and compares each filing with the previous one. Fiscal year-ends are whatever the filing says. Costco, Walmart, Microsoft, Apple, Visa, and Johnson & Johnson do not share a December 31 year-end, and they are not forced onto one.

Passages are split on bold headings where the filing has them, and on the filing's own paragraphs otherwise. Passages are paired one-to-one, greedily, when content-token overlap is at least 0.45. A leftover whose closest passage is still at or above 0.45 was not paired because that closest passage was already matched; it is labeled already paired, and it is not called added or removed. Overlap from 0.30 up to, but not including, 0.45 is left unclassified. Below 0.30 the passage is added or removed. Function words are ignored in the overlap. Negations are not, so dropping "not" is kept.

A matched pair is called a date-only edit when every added or removed token is a four-digit year, a month name, or an integer from 1 to 31 sitting within three tokens of a month name. Those edits are counted and not listed. Other number changes are listed.

Item 9A is also scanned for a fixed set of sentences: the disclosure-controls conclusion, the internal-control conclusion, the change-in-controls sentence, material weakness, significant deficiency, remediation, and auditor attestation. Each hit is quoted. A sentence that does not match the rules is quoted without a classification, or the report says the sentence was not found.

The peer comparison puts the two companies' latest year-over-year changes in columns. A passage is "not aligned" only against the peer's extracted Item 1A for the two years in that column. The page states the period ends and says when they are not the same fiscal period.

The SEC asks for a User-Agent that identifies you and gives a contact, and for no more than 10 requests per second. This tool reads the User-Agent from `SEC_USER_AGENT`, waits at least 0.2 seconds between requests, and caches responses under `data/`. Do not commit the User-Agent or the cache. The value is not written into the report.

The HTML report is `docs/index.html`. Open it locally, or publish the `docs/` folder with GitHub Pages. Pages is not turned on by this repo. To turn it on: Settings, Pages, Build and deployment, Branch `main`, folder `/docs`.

An older optional hook, `DRIFT_LLM_SUMMARY` plus `DRIFT_LLM_ENDPOINT`, can add a paragraph labeled as a model summary. That paragraph is not a finding and is not the audit-plan brief. The sample report was produced with the hook off.

## Limitations

- "Wording change" is a token rule. It is not materiality under the securities laws, and it is not a view on whether the control is effective.
- The audit-plan line is a consideration for the plan. It is not a finding that a control failed.
- Item 8, exhibits, the 10-K/A, 10-Q, and 8-K are not read. Amendments are named and left out of the diff.
- Absence means the phrase was not in the extracted section. It does not mean the company has no such risk. A peer "not aligned" result is the same kind of statement.
- If a section cannot be bounded, or the two years are not split the same way, passage adds and drops are withheld.
- Matching is greedy and one-to-one. A leftover can overlap another passage at 0.45 or more when that passage was already paired. That leftover is labeled already paired. A full rewrite can show up as a removal plus an addition, or as unclassified when the overlap is from 0.30 up to, but not including, 0.45.
- This is a wording comparison of public filings. It is not investment advice.
- An integer from 1 to 31 next to a month name is treated as a date. A percentage that happens to be in that range and sits next to a month name can be treated as a date too.
- Bold-heading detection follows the filing's HTML. A risk written as ordinary paragraphs is compared as paragraphs.
- The auditor's opinion is taken only from words inside Item 9A.
- The sample is a snapshot. A company can amend a filing after the run.
- Exxon Mobil's 10-K is filed by Exxon Mobil Corp, CIK 0000034088. The SEC ticker file currently maps XOM to a different CIK. The report states that from the ticker file at run time.
- A theme is only as good as the quote attached to it. The check confirms the quote is in the extract. It does not confirm that the two sentences are the only fair reading of that quote.
