# 10-K disclosure drift

Compares Item 1A (Risk Factors) and Item 9A (Controls and Procedures) across consecutive Form 10-Ks, then compares those changes between two peers in each of six sectors. Every finding quotes the filing sentence it came from and links to that filing. If the text does not support a conclusion, the report says so.

The companies are a fixed peer set, not a screen of the whole market: JPMorgan Chase and Bank of America, Visa and Mastercard, Apple and Microsoft, Walmart and Costco, UnitedHealth Group and Johnson & Johnson, Exxon Mobil and Chevron. The list is `config/companies.yml`.

This sits next to the [risk analytics portfolio](https://github.com/neifertg/risk-analytics-portfolio). No client data. The filings are public.

## Why this is useful in audit

A review of risk-factor and controls language is a question about what changed, not a score. A new sentence that says a material weakness was identified, a risk that disappeared, or a risk one peer added and the other did not, is a prompt for the next conversation. The prompt is only as good as the passage attached to it.

The rules are narrow on purpose. "Material weakness" in a sentence about what might happen in the future is not reported as an identification. A pointer from Item 9A to the auditor's report in Item 8 is quoted as a pointer. Item 8 is not opened, and the opinion is not inferred.

## What the comparison does

For each company it takes the three most recent Form 10-Ks on EDGAR and compares each filing with the previous one. Fiscal year-ends are whatever the filing says. Costco, Walmart, Microsoft, Apple, Visa, and Johnson & Johnson do not share a December 31 year-end, and they are not forced onto one.

Passages are split on bold headings where the filing has them, and on the filing's own paragraphs otherwise. Passages are paired one-to-one, greedily, when content-token overlap is at least 0.45. A leftover whose closest passage is still at or above 0.45 was not paired because that closest passage was already matched; it is labeled already paired, and it is not called added or removed. Overlap from 0.30 up to, but not including, 0.45 is left unclassified. Below 0.30 the passage is added or removed. Function words are ignored in the overlap. Negations are not, so dropping "not" is kept.

A matched pair is called a date-only edit when every added or removed token is a four-digit year, a month name, or an integer from 1 to 31 sitting within three tokens of a month name. Those edits are counted and not listed. Other number changes are listed.

Item 9A is also scanned for a fixed set of sentences: the disclosure-controls conclusion, the internal-control conclusion, the change-in-controls sentence, material weakness, significant deficiency, remediation, and auditor attestation. Each hit is quoted. A sentence that does not match the rules is quoted without a classification, or the report says the sentence was not found.

The peer page puts the two companies' latest year-over-year changes in columns. A passage is "not aligned" only against the peer's extracted Item 1A for the two years in that column. The page states the period ends and says when they are not the same fiscal period.

No model is required. Set `DRIFT_LLM_SUMMARY=1` and `DRIFT_LLM_ENDPOINT` only if you want an optional summary from an endpoint you run. That summary is labeled as a model summary and is not a finding. The sample report was produced with the hook off.

## How to run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name you@example.com"
PYTHONPATH=src python -m drift_detector run --config config/companies.yml --out docs
PYTHONPATH=src pytest
```

The SEC asks for a User-Agent that identifies you and gives a contact, and for no more than 10 requests per second. This tool reads the User-Agent from `SEC_USER_AGENT`, waits at least 0.2 seconds between requests, and caches responses under `data/`. Do not commit the User-Agent or the cache. The value is not written into the report.

`--only JPM,BAC` limits a run to those tickers. A partial run does not rebuild peer pages for pairs you left out.

The HTML report is `docs/index.html`. Open it locally, or publish the `docs/` folder with GitHub Pages. Pages is not turned on by this repo. To turn it on: Settings, Pages, Build and deployment, Branch `main`, folder `/docs`.

## Sample

The report in `docs/` was generated from EDGAR on 2026-11-13 02:16 UTC. Two excerpts:

JPMorgan Chase, Item 9A, 2024-12-31 and 2025-12-31. The phrase "material weakness" is in both years. The sentence is the same aside from dates and numbers, and the rules call it hypothetical, not an identification.

> Deficiencies or lapses in internal controls may occur from time to time, and there can be no assurance that any such deficiencies will not result in significant deficiencies or material weaknesses in internal control in the future and collateral consequences therefrom.

[2024 10-K, accession 0000019617-25-000270](https://www.sec.gov/Archives/edgar/data/19617/000001961725000270/jpm-20241231.htm#:~:text=Deficiencies%20or%20lapses%20in%20internal%20controls%20may%20occur). Bank of America's Item 9A for the same two period ends does not contain the phrase. That page quotes the item headings that bounded the search.

JPMorgan Chase, Item 1A, from the 2024-12-31 10-K to the 2025-12-31 10-K. This passage is in the later filing. The closest passage in the earlier filing overlaps at 0.15, under the 0.30 floor, so it is listed as added rather than as a rewrite.

> JPMorganChase’s operations, results, and competitive standing could be adversely affected by the development of advanced technologies such as AI.

[2025 10-K, accession 0001628280-26-008131](https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm#:~:text=JPMorganChase%20s%20operations%20results%20and%20competitive%20standing%20could).

Visa's latest period ends 2026-09-30 and Mastercard's ends 2025-12-31, 273 days apart. The payments peer page says that is not a same-fiscal-period comparison, and each column is that company's own prior-year change.

## Limitations

- "Wording change" is a token rule. It is not materiality under the securities laws, and it is not a view on whether the control is effective.
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
