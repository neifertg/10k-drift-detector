"""Rule-based flags for Item 9A.

Each flag is a sentence the tool can stand behind, plus the filing
sentence it came from. Hypothetical uses of "material weakness" are
not reported as an identification. If the extract is incomplete, the
flag says the comparison is not supported.
"""

from __future__ import annotations

import re

from drift_detector.models import Flag, Quote, Section
from drift_detector.textutil import conclusion_key, sentences

_MW = re.compile(r"material weakness(?:es)?", re.I)
_SD = re.compile(r"significant deficienc(?:y|ies)", re.I)
_NEG_MW = re.compile(
    r"no material weakness|"
    r"not identify any material weakness|"
    r"did not identify|"
    r"has not identified|"
    r"have not identified",
    re.I,
)
_ASSERT_MW = re.compile(
    r"identified (?:a )?material weakness|"
    r"material weakness(?:es)? (?:existed|was identified|were identified)|"
    r"there (?:was|were) (?:a )?material weakness|"
    r"concluded that (?:there (?:was|were) )?(?:a )?material weakness exists|"
    r"determined that (?:a )?material weakness exists|"
    r"disclosed (?:a )?material weakness",
    re.I,
)
# The auditor's standard report says it assessed "the risk that a material weakness exists".
# That is a description of the procedure, not a statement that one was found.
_PROCEDURE_MW = re.compile(
    r"assessing the risk that a material weakness exists|"
    r"risk that a material weakness exists|"
    r"whether (?:or not )?a material weakness exists",
    re.I,
)
_HYP = re.compile(
    r"\b(may|might|could|would|future|no assurance|potential|risk that|will not|can be no)\b",
    re.I,
)
_NEG_SD = re.compile(
    r"no significant deficienc|"
    r"not identify any significant deficienc|"
    r"did not identify|"
    r"has not identified|"
    r"have not identified",
    re.I,
)
_ASSERT_SD = re.compile(
    r"identified (?:a )?significant deficienc|"
    r"significant deficienc(?:y|ies) (?:existed|exists|was identified|were identified)|"
    r"there (?:was|were) (?:a )?significant deficienc",
    re.I,
)
_REM = re.compile(r"\bremediat\w*", re.I)
_DC = re.compile(r"disclosure controls and procedures", re.I)
_ICFR = re.compile(r"internal control over financial reporting", re.I)
_NOT_EFF = re.compile(r"\b(were|was|are)\s+not\s+effective\b|\bineffective\b", re.I)
_EFF = re.compile(r"\b(were|was|are)\s+effective\b", re.I)
_CONCLUDED = re.compile(r"\b(concluded|conclusion)\b", re.I)
_ICFR_POINTER = re.compile(r"management.?s report on internal control", re.I)
_AUDITOR_PTR = re.compile(
    r"(audit report|attestation report|report of the independent|"
    r"independent registered public accounting firm)",
    re.I,
)
_OUTSIDE = re.compile(
    r"item\s*8|incorporated by reference|appears in|included in item",
    re.I,
)
_OPINION = re.compile(
    r"adverse opinion|disclaimer of opinion|qualified opinion|"
    r"unqualified opinion|unmodified opinion",
    re.I,
)

SIGNAL_TITLES = {
    "disclosure_controls": "Disclosure controls conclusion",
    "icfr_conclusion": "Internal control over financial reporting conclusion",
    "icfr_change": "Change in internal control over financial reporting",
    "material_weakness": "Material weakness language",
    "significant_deficiency": "Significant deficiency language",
    "remediation": "Remediation language",
    "auditor": "Auditor attestation language",
}


def classify_phrase_sentence(sentence: str, phrase: str) -> str:
    """Classify one sentence that already contains the phrase.

    Returns assertion, negative, hypothetical, mixed, or other.
    """
    if phrase == "material weakness":
        negative = bool(_NEG_MW.search(sentence))
        assertion = bool(_ASSERT_MW.search(sentence))
        if not negative and not assertion and _PROCEDURE_MW.search(sentence):
            return "procedure"
    elif phrase == "significant deficiency":
        negative = bool(_NEG_SD.search(sentence))
        assertion = bool(_ASSERT_SD.search(sentence))
    else:
        raise ValueError(phrase)
    hypothetical = bool(_HYP.search(sentence))
    if negative and assertion:
        return "mixed"
    if negative:
        return "negative"
    if assertion:
        return "assertion"
    if hypothetical:
        return "hypothetical"
    return "other"


def _class_word(kind: str) -> str:
    return {
        "assertion": "states that one was identified",
        "negative": "says one was not identified",
        "hypothetical": "uses the phrase hypothetically and does not state that one was identified",
        "procedure": "describes the auditor's procedures and does not state that one was identified",
        "mixed": "contains both identification language and a denial; no single reading is taken",
        "other": "is not classified by these rules as an identification or as a hypothetical",
    }[kind]


def disclosure_class(sentence: str) -> str | None:
    if not _DC.search(sentence):
        return None
    if _NOT_EFF.search(sentence):
        return "not_effective"
    if _CONCLUDED.search(sentence) and re.search(r"\beffective\b", sentence, re.I):
        return "effective"
    if _EFF.search(sentence):
        return "effective"
    return None


def icfr_class(sentence: str) -> str | None:
    if not _ICFR.search(sentence):
        return None
    if _ICFR_POINTER.search(sentence) and not _EFF.search(sentence):
        return None
    if re.search(r"\bdefined\b|\brule\s+13a-15", sentence, re.I) and not (
        _CONCLUDED.search(sentence) or _EFF.search(sentence)
    ):
        return None
    if _NOT_EFF.search(sentence):
        return "not_effective"
    if _CONCLUDED.search(sentence) and re.search(r"\beffective\b", sentence, re.I):
        return "effective"
    if _EFF.search(sentence):
        return "effective"
    return None


def icfr_change_class(sentence: str) -> str | None:
    if not _ICFR.search(sentence) or not re.search(r"\bchange", sentence, re.I):
        return None
    if not re.search(r"\b(quarter|three months|period covered|there (?:was|were))\b", sentence, re.I):
        return None
    if re.search(r"\b(no change|no changes|not been any change|were no changes|was no change)\b", sentence, re.I):
        return "no_change"
    if re.search(r"materially affected", sentence, re.I):
        return "describes_change"
    return "unclassified"


def _phrase_sentences(text: str, pattern: re.Pattern[str]) -> list[str]:
    return [s for s in sentences(text) if pattern.search(s)]


def _paired(prior: list[str], current: list[str]) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """Split sentences into removed, added, and pairs whose number-folded text matches."""
    prior_by = {}
    for s in prior:
        prior_by.setdefault(conclusion_key(s), []).append(s)
    current_by = {}
    for s in current:
        current_by.setdefault(conclusion_key(s), []).append(s)
    removed: list[str] = []
    added: list[str] = []
    same: list[tuple[str, str]] = []
    for key in set(prior_by) | set(current_by):
        left = prior_by.get(key, [])
        right = current_by.get(key, [])
        n = min(len(left), len(right))
        for i in range(n):
            same.append((left[i], right[i]))
        removed.extend(left[n:])
        added.extend(right[n:])
    return removed, added, same


def _unsupported(signal: str, prior: Section, current: Section, prior_date: str, current_date: str) -> Flag:
    return Flag(
        signal=signal,
        index_line=f"{SIGNAL_TITLES[signal]}: comparison not supported.",
        statement=(
            f"{SIGNAL_TITLES[signal]} is not compared. "
            f"Item 9A extraction status is {prior.status} for the period ended {prior_date} "
            f"and {current.status} for the period ended {current_date}. "
            "No conclusion is drawn."
        ),
        quotes=[],
    )


def _q(text: str, section: Section, report_date: str, accession: str, document_url: str, passage_url: str) -> Quote:
    allowed = section.text
    if text not in allowed and text not in {section.header or "", section.end_header or ""}:
        raise ValueError("Quote is not a substring of the extracted section or its boundary headings")
    return Quote(
        text=text,
        item="9A",
        report_date=report_date,
        accession=accession,
        document_url=document_url,
        passage_url=passage_url,
        truncated=False,
    )


def _bounds(section: Section, report_date: str, accession: str, document_url: str, make_url) -> list[Quote]:
    quotes = []
    if section.header and section.header in section.text:
        quotes.append(
            _q(section.header, section, report_date, accession, document_url, make_url(document_url, section.header))
        )
    if section.end_header:
        # The end heading is the next item, not part of this section's text.
        quotes.append(
            Quote(
                text=section.end_header,
                item="9A",
                report_date=report_date,
                accession=accession,
                document_url=document_url,
                passage_url=make_url(document_url, section.end_header),
                truncated=False,
            )
        )
    return quotes


def _phrase_flag(
    signal: str,
    phrase: str,
    pattern: re.Pattern[str],
    prior: Section,
    current: Section,
    prior_date: str,
    current_date: str,
    meta: dict,
) -> Flag:
    make_url = meta["make_url"]
    prior_s = _phrase_sentences(prior.text, pattern)
    current_s = _phrase_sentences(current.text, pattern)
    title = SIGNAL_TITLES[signal]
    if not prior_s and not current_s:
        quotes = _bounds(prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url)
        quotes += _bounds(current, current_date, meta["current_accession"], meta["current_url"], make_url)
        return Flag(
            signal=signal,
            index_line=f"{title}: phrase not in either Item 9A.",
            statement=(
                f"The phrase “{phrase}” does not appear in the extracted Item 9A "
                f"for the periods ended {prior_date} and {current_date}. "
                "The search ran from each year's item heading through the next item heading, quoted below."
            ),
            quotes=quotes,
        )
    removed, added, same = _paired(prior_s, current_s)
    quotes: list[Quote] = []
    bits: list[str] = []
    if same and not removed and not added:
        kinds = {classify_phrase_sentence(s, phrase) for _, s in same}
        kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"
        for left, right in same:
            quotes.append(_q(left, prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url(meta["prior_url"], left)))
            if left != right:
                quotes.append(_q(right, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], right)))
        bits.append(
            f"Both extracted Item 9A sections contain “{phrase}”. "
            f"Aside from dates and numbers, the sentence is unchanged. "
            f"The sentence {_class_word(kind)}."
        )
        if kind == "procedure":
            index = f"{title}: phrase appears in the auditor's procedure sentence, not as an identification."
        else:
            index = f"{title}: present in both years ({kind})."
    else:
        for s in removed:
            kind = classify_phrase_sentence(s, phrase)
            quotes.append(_q(s, prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url(meta["prior_url"], s)))
            bits.append(
                f"This sentence is in the {prior_date} Item 9A and not, aside from dates and numbers, "
                f"in the {current_date} Item 9A. It {_class_word(kind)}."
            )
        for s in added:
            kind = classify_phrase_sentence(s, phrase)
            quotes.append(_q(s, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], s)))
            bits.append(
                f"This sentence is in the {current_date} Item 9A and not, aside from dates and numbers, "
                f"in the {prior_date} Item 9A. It {_class_word(kind)}."
            )
        for left, right in same:
            quotes.append(_q(right, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], right)))
        index = f"{title}: wording changed between the two Item 9A sections."
    return Flag(signal=signal, index_line=index, statement=" ".join(bits), quotes=quotes)


def _effectiveness_flag(
    signal: str,
    classifier,
    prior: Section,
    current: Section,
    prior_date: str,
    current_date: str,
    meta: dict,
    pointer_pattern: re.Pattern[str] | None = None,
) -> Flag:
    make_url = meta["make_url"]
    title = SIGNAL_TITLES[signal]
    subject = {
        "disclosure_controls": "disclosure controls and procedures were effective",
        "icfr_conclusion": "internal control over financial reporting was effective",
    }[signal]

    def grab(section: Section) -> list[tuple[str, str]]:
        found = []
        for sentence in sentences(section.text):
            kind = classifier(sentence)
            if kind:
                found.append((kind, sentence))
        return found

    left = grab(prior)
    right = grab(current)
    if not left and not right:
        pointers = []
        if pointer_pattern:
            for section, date, acc, url in (
                (prior, prior_date, meta["prior_accession"], meta["prior_url"]),
                (current, current_date, meta["current_accession"], meta["current_url"]),
            ):
                for sentence in sentences(section.text):
                    if pointer_pattern.search(sentence):
                        pointers.append(_q(sentence, section, date, acc, url, make_url(url, sentence)))
        if pointers:
            return Flag(
                signal=signal,
                index_line=f"{title}: not stated in Item 9A; the text points elsewhere.",
                statement=(
                    f"The extracted Item 9A for {prior_date} and {current_date} does not itself state "
                    f"whether {subject}. It points to text outside this extract. "
                    "That outside text was not read, so no conclusion is drawn."
                ),
                quotes=pointers,
            )
        return Flag(
            signal=signal,
            index_line=f"{title}: conclusion sentence not found.",
            statement=(
                f"The extracted Item 9A for {prior_date} and {current_date} does not contain a sentence "
                f"this tool recognizes as stating whether {subject}. No conclusion is drawn."
            ),
            quotes=_bounds(prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url)
            + _bounds(current, current_date, meta["current_accession"], meta["current_url"], make_url),
        )

    def describe(pairs: list[tuple[str, str]], date: str) -> str:
        kinds = sorted({k for k, _ in pairs})
        if kinds == ["effective"]:
            return f"effective in the {date} extract"
        if kinds == ["not_effective"]:
            return f"not effective in the {date} extract"
        return f"the {date} extract has more than one reading ({', '.join(kinds)})"

    quotes = []
    for kind, sentence in left:
        quotes.append(_q(sentence, prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url(meta["prior_url"], sentence)))
    for kind, sentence in right:
        quotes.append(_q(sentence, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], sentence)))
    left_desc = describe(left, prior_date) if left else f"no conclusion sentence in the {prior_date} extract"
    right_desc = describe(right, current_date) if right else f"no conclusion sentence in the {current_date} extract"
    changed = left_desc != right_desc
    index = f"{title}: {left_desc}; {right_desc}."
    statement = (
        f"{title}: the {prior_date} filing is {left_desc}; the {current_date} filing is {right_desc}. "
        "The sentences are quoted below. This is a comparison of those sentences, not an assessment of the controls."
    )
    if not changed:
        index = f"{title}: {right_desc}."
    return Flag(signal=signal, index_line=index, statement=statement, quotes=quotes)


def _change_flag(prior: Section, current: Section, prior_date: str, current_date: str, meta: dict) -> Flag:
    make_url = meta["make_url"]
    signal = "icfr_change"
    title = SIGNAL_TITLES[signal]

    def grab(section: Section) -> list[tuple[str, str]]:
        out = []
        for sentence in sentences(section.text):
            kind = icfr_change_class(sentence)
            if kind:
                out.append((kind, sentence))
        return out

    left, right = grab(prior), grab(current)
    if not left and not right:
        return Flag(
            signal=signal,
            index_line=f"{title}: sentence not found.",
            statement=(
                f"The extracted Item 9A for {prior_date} and {current_date} does not contain a sentence "
                "this tool recognizes as the required disclosure on changes in internal control. "
                "No conclusion is drawn."
            ),
            quotes=_bounds(prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url)
            + _bounds(current, current_date, meta["current_accession"], meta["current_url"], make_url),
        )
    quotes = []
    for _, sentence in left:
        quotes.append(_q(sentence, prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url(meta["prior_url"], sentence)))
    for _, sentence in right:
        quotes.append(_q(sentence, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], sentence)))
    left_kinds = sorted({k for k, _ in left}) or ["not found"]
    right_kinds = sorted({k for k, _ in right}) or ["not found"]
    return Flag(
        signal=signal,
        index_line=f"{title}: {', '.join(left_kinds)} then {', '.join(right_kinds)}.",
        statement=(
            f"Change-in-controls sentence: {prior_date} is classified {', '.join(left_kinds)}; "
            f"{current_date} is classified {', '.join(right_kinds)}. "
            "“no_change” means the sentence says there was no change. "
            "The sentences are quoted and are not rephrased."
        ),
        quotes=quotes,
    )


def _auditor_flag(prior: Section, current: Section, prior_date: str, current_date: str, meta: dict) -> Flag:
    make_url = meta["make_url"]
    signal = "auditor"
    title = SIGNAL_TITLES[signal]

    def grab(section: Section) -> list[str]:
        found = []
        for sentence in sentences(section.text):
            if _OPINION.search(sentence) and (_ICFR.search(sentence) or _AUDITOR_PTR.search(sentence)):
                found.append(sentence)
                continue
            if _AUDITOR_PTR.search(sentence) and (_OUTSIDE.search(sentence) or _ICFR.search(sentence)):
                found.append(sentence)
        return found

    left, right = grab(prior), grab(current)
    if not left and not right:
        return Flag(
            signal=signal,
            index_line=f"{title}: not discussed in either extracted Item 9A.",
            statement=(
                f"The extracted Item 9A for {prior_date} and {current_date} does not mention an auditor "
                "attestation report or an opinion on internal control. No attestation conclusion is drawn. "
                "The auditor's report may sit in Item 8, which this tool does not read."
            ),
            quotes=_bounds(prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url)
            + _bounds(current, current_date, meta["current_accession"], meta["current_url"], make_url),
        )
    removed, added, same = _paired(left, right)
    quotes = []
    for sentence in removed:
        quotes.append(_q(sentence, prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url(meta["prior_url"], sentence)))
    for sentence in added:
        quotes.append(_q(sentence, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], sentence)))
    for left_s, right_s in same:
        quotes.append(_q(left_s, prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url(meta["prior_url"], left_s)))
        if left_s != right_s:
            quotes.append(_q(right_s, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], right_s)))
    if same and not removed and not added:
        opinions = sorted({m.group(0).lower() for _, s in same for m in _OPINION.finditer(s)})
        outside = any(_OUTSIDE.search(s) for _, s in same)
        extra = ""
        if opinions:
            extra = " The sentence contains the opinion phrase " + ", ".join(f"“{o}”" for o in opinions) + "."
        if outside and not opinions:
            extra = " The sentence points to Item 8 or otherwise outside Item 9A. Item 8 was not read, so the opinion is not characterized."
        return Flag(
            signal=signal,
            index_line=f"{title}: present in both years, unchanged aside from dates.",
            statement=(
                "Auditor wording is in both extracted Item 9A sections and matches aside from dates and numbers."
                + extra
            ),
            quotes=quotes,
        )
    return Flag(
        signal=signal,
        index_line=f"{title}: the wording changed.",
        statement=(
            "The auditor-attestation sentences in the two Item 9A extracts do not match aside from dates and numbers. "
            "Both sides are quoted. This is not a characterization of the opinion beyond those words."
        ),
        quotes=quotes,
    )


def _remediation_flag(prior: Section, current: Section, prior_date: str, current_date: str, meta: dict) -> Flag:
    make_url = meta["make_url"]
    signal = "remediation"
    title = SIGNAL_TITLES[signal]
    left = _phrase_sentences(prior.text, _REM)
    right = _phrase_sentences(current.text, _REM)
    if not left and not right:
        return Flag(
            signal=signal,
            index_line=f"{title}: phrase not in either Item 9A.",
            statement=(
                f"Neither extracted Item 9A ({prior_date} and {current_date}) contains a word beginning with “remediat”. "
                "No remediation conclusion is drawn."
            ),
            quotes=_bounds(prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url)
            + _bounds(current, current_date, meta["current_accession"], meta["current_url"], make_url),
        )
    removed, added, same = _paired(left, right)
    quotes = []
    bits = []
    for sentence in removed:
        quotes.append(_q(sentence, prior, prior_date, meta["prior_accession"], meta["prior_url"], make_url(meta["prior_url"], sentence)))
        bits.append(f"A sentence containing “remediat…” is in the {prior_date} Item 9A and not in the {current_date} extract.")
    for sentence in added:
        quotes.append(_q(sentence, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], sentence)))
        bits.append(f"A sentence containing “remediat…” is in the {current_date} Item 9A and not in the {prior_date} extract.")
    for left_s, right_s in same:
        quotes.append(_q(right_s, current, current_date, meta["current_accession"], meta["current_url"], make_url(meta["current_url"], right_s)))
    if same and not removed and not added:
        return Flag(
            signal=signal,
            index_line=f"{title}: present in both years, unchanged aside from dates.",
            statement=(
                "Both extracted Item 9A sections contain remediation wording, and the sentences match aside from dates and numbers. "
                "The wording is quoted and is not read as a conclusion that remediation succeeded or failed."
            ),
            quotes=quotes,
        )
    if same:
        bits.append("Other remediation sentences match aside from dates and numbers.")
    return Flag(
        signal=signal,
        index_line=f"{title}: the sentences changed.",
        statement=" ".join(bits) + " The sentences are quoted and are not read as a conclusion that remediation succeeded or failed.",
        quotes=quotes,
    )


def item_9a_flags(
    prior: Section,
    current: Section,
    prior_date: str,
    current_date: str,
    *,
    prior_accession: str,
    current_accession: str,
    prior_url: str,
    current_url: str,
    make_url,
) -> list[Flag]:
    meta = {
        "prior_accession": prior_accession,
        "current_accession": current_accession,
        "prior_url": prior_url,
        "current_url": current_url,
        "make_url": make_url,
    }
    if prior.status != "ok" or current.status != "ok":
        return [
            _unsupported(signal, prior, current, prior_date, current_date)
            for signal in SIGNAL_TITLES
        ]
    return [
        _effectiveness_flag("disclosure_controls", disclosure_class, prior, current, prior_date, current_date, meta),
        _effectiveness_flag(
            "icfr_conclusion",
            icfr_class,
            prior,
            current,
            prior_date,
            current_date,
            meta,
            pointer_pattern=_ICFR_POINTER,
        ),
        _change_flag(prior, current, prior_date, current_date, meta),
        _phrase_flag("material_weakness", "material weakness", _MW, prior, current, prior_date, current_date, meta),
        _phrase_flag("significant_deficiency", "significant deficiency", _SD, prior, current, prior_date, current_date, meta),
        _remediation_flag(prior, current, prior_date, current_date, meta),
        _auditor_flag(prior, current, prior_date, current_date, meta),
    ]
