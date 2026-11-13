from drift_detector.flags import (
    classify_phrase_sentence,
    disclosure_class,
    icfr_change_class,
    item_9a_flags,
)
from drift_detector.models import Section
from drift_detector.textutil import passage_fragment


JPM_HYPOTHETICAL = (
    "Deficiencies or lapses in internal controls may occur from time to time, and there can be no "
    "assurance that any such deficiencies will not result in significant deficiencies or material "
    "weaknesses in internal control in the future and collateral consequences therefrom."
)


def _section(text, status="ok"):
    return Section(
        item="9A",
        status=status,
        header="Item 9A. Controls and Procedures",
        end_header="Item 9B. Other Information",
        text=f"Item 9A. Controls and Procedures {text}",
        segmentation="headings",
    )


def _flags(prior_text, current_text, prior_status="ok", current_status="ok"):
    prior = _section(prior_text, prior_status)
    current = _section(current_text, current_status)
    return item_9a_flags(
        prior,
        current,
        "2024-12-31",
        "2025-12-31",
        prior_accession="0000000000-25-000001",
        current_accession="0000000000-26-000001",
        prior_url="https://www.sec.gov/Archives/edgar/data/1/000/prior.htm",
        current_url="https://www.sec.gov/Archives/edgar/data/1/000/current.htm",
        make_url=passage_fragment,
    )


def _by_signal(flags):
    return {flag.signal: flag for flag in flags}


def test_hypothetical_material_weakness_is_not_an_identification():
    assert classify_phrase_sentence(JPM_HYPOTHETICAL, "material weakness") == "hypothetical"
    flags = _by_signal(_flags(JPM_HYPOTHETICAL, JPM_HYPOTHETICAL))
    flag = flags["material_weakness"]
    assert "does not state that one was identified" in flag.statement
    assert JPM_HYPOTHETICAL in flag.quotes[0].text
    assert "sec.gov" in flag.quotes[0].document_url


AUDITOR_PROCEDURE = (
    "Our audit included obtaining an understanding of internal control over financial reporting, "
    "assessing the risk that a material weakness exists, testing and evaluating the design and "
    "operating effectiveness of internal control based on the assessed risk, and performing such "
    "other procedures as we considered necessary in the circumstances."
)


def test_auditor_procedure_sentence_is_not_an_identification():
    assert classify_phrase_sentence(AUDITOR_PROCEDURE, "material weakness") == "procedure"
    flags = _by_signal(_flags(AUDITOR_PROCEDURE, AUDITOR_PROCEDURE))
    flag = flags["material_weakness"]
    assert "does not state that one was identified" in flag.statement
    assert "assertion" not in flag.index_line
    assert AUDITOR_PROCEDURE in flag.quotes[0].text


def test_new_identification_is_quoted():
    added = "Management identified a material weakness in internal control over financial reporting related to revenue cutoff."
    flags = _by_signal(_flags("The firm evaluated its controls and described the framework it used.", added))
    flag = flags["material_weakness"]
    assert "2025-12-31" in flag.statement
    assert any(added in q.text for q in flag.quotes)
    assert classify_phrase_sentence(added, "material weakness") == "assertion"


def test_phrase_absent_says_so_and_quotes_the_boundary():
    text = "Management concluded that the disclosure controls and procedures were effective as of December 31, 2024."
    flags = _by_signal(_flags(text, text.replace("2024", "2025")))
    flag = flags["material_weakness"]
    assert "does not appear" in flag.statement
    assert any(q.text.startswith("Item 9A") for q in flag.quotes)
    assert any(q.text.startswith("Item 9B") for q in flag.quotes)


def test_negative_identification_sentence():
    sentence = "Management did not identify any material weakness in internal control over financial reporting."
    assert classify_phrase_sentence(sentence, "material weakness") == "negative"


def test_effectiveness_and_no_change_and_auditor_pointer():
    prior = (
        "Management concluded that the disclosure controls and procedures were effective as of December 31, 2024. "
        "Management concluded that internal control over financial reporting was effective as of December 31, 2024. "
        "The independent registered public accounting firm has issued an audit report on internal control over financial reporting, which appears in Item 8. "
        "There were no changes in internal control over financial reporting during the fourth quarter of 2024 that have materially affected internal control over financial reporting."
    )
    current = prior.replace("2024", "2025")
    flags = _by_signal(_flags(prior, current))
    assert "effective" in flags["disclosure_controls"].statement
    assert any("were effective" in q.text for q in flags["disclosure_controls"].quotes)
    assert "Item 8" in flags["auditor"].statement or any("Item 8" in q.text for q in flags["auditor"].quotes)
    assert "no_change" in flags["icfr_change"].statement
    assert flags["remediation"].index_line.endswith("phrase not in either Item 9A.")
    assert disclosure_class(prior.split(". ")[0] + ".") == "effective"


def test_effectiveness_can_flip():
    prior = "Management concluded that the disclosure controls and procedures were effective as of December 31, 2024."
    current = "Management concluded that the disclosure controls and procedures were not effective as of December 31, 2025."
    flags = _by_signal(_flags(prior, current))
    statement = flags["disclosure_controls"].statement
    assert "not effective" in statement
    assert "effective" in statement
    assert any("not effective" in q.text for q in flags["disclosure_controls"].quotes)


def test_incomplete_extract_draws_no_conclusion():
    flags = _flags("text", "text", prior_status="incomplete", current_status="ok")
    assert all("not compared" in f.statement.lower() or "No conclusion is drawn" in f.statement for f in flags)
    assert all(f.quotes == [] for f in flags)


def test_icfr_change_sentence_with_both_phrases_is_no_change():
    sentence = (
        "There were no changes in the company's internal control over financial reporting during the fourth quarter "
        "of 2024, which were identified in connection with management's evaluation, that have materially affected, "
        "or are reasonably likely to materially affect, the company's internal control over financial reporting."
    )
    assert icfr_change_class(sentence) == "no_change"


def test_remediation_added_is_quoted_not_judged():
    prior = "Management concluded that the disclosure controls and procedures were effective as of December 31, 2024."
    current = prior + " Management has begun to remediate the control gap identified during the quarter."
    flags = _by_signal(_flags(prior, current))
    flag = flags["remediation"]
    assert "remediat" in flag.statement
    assert any("remediate" in q.text for q in flag.quotes)
    assert "succeeded" in flag.statement
