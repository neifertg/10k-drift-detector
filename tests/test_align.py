from drift_detector.align import align_passages, count_kinds
from drift_detector.models import Passage
from drift_detector.textutil import content_overlap, is_cosmetic


def _p(text, title=None):
    return Passage(text=text, title=title)


def test_identical_is_unchanged():
    text = "We face credit risk from borrowers when unemployment rises and collateral values fall."
    rows = align_passages([_p(text)], [_p(text)])
    assert [r.kind for r in rows] == ["unchanged"]


def test_year_and_day_next_to_month_is_cosmetic():
    prior = "Management concluded that disclosure controls were effective as of September 27, 2025."
    current = "Management concluded that disclosure controls were effective as of September 26, 2026."
    assert is_cosmetic(prior, current)
    rows = align_passages([_p(prior)], [_p(current)])
    assert rows[0].kind == "cosmetic"


def test_negation_is_not_cosmetic():
    prior = "The company does not face intense competition in its primary market from larger rivals."
    current = "The company does face intense competition in its primary market from larger rivals."
    assert not is_cosmetic(prior, current)
    rows = align_passages([_p(prior)], [_p(current)])
    assert rows[0].kind == "changed"


def test_small_number_not_next_to_a_month_is_kept():
    prior = "The reserve increased 20 percent during the year because loss rates rose across the book."
    current = "The reserve increased 35 percent during the year because loss rates rose across the book."
    assert not is_cosmetic(prior, current)


def test_added_and_removed():
    keep = "Funding depends on wholesale deposits and market confidence in the bank's liquidity position over time."
    gone = "Mortgage servicing rights expose the company to prepayment speeds that can change quickly when rates move."
    added = "A new payments network rule could require the company to change how it authorizes consumer transactions."
    rows = align_passages([_p(keep), _p(gone)], [_p(keep), _p(added)])
    kinds = count_kinds(rows)
    assert kinds["unchanged"] == 1
    assert kinds["removed"] == 1
    assert kinds["added"] == 1
    removed = next(r for r in rows if r.kind == "removed")
    assert removed.prior.text == gone


def test_leftover_at_or_above_threshold_is_already_paired():
    """A second passage can overlap the same partner at >= 0.45 and still be leftover.

    One-to-one matching keeps the first pair. The leftover is not unclassified
    and is not called added or removed.
    """
    base = (
        "Wholesale funding markets can close to the firm during stress which limits "
        "the ability to refinance maturing debt and meet cash obligations to clients "
        "and depositors over several quarters."
    )
    variant = (
        base
        + " Those same funding markets also reprice the firm's outstanding bonds when "
        "investors demand a wider credit spread."
    )
    assert content_overlap(base, variant) >= 0.45
    rows = align_passages([_p(base, "Base"), _p(variant, "Variant")], [_p(base, "Base")])
    leftover = next(row for row in rows if row.prior and row.prior.title == "Variant")
    paired = next(row for row in rows if row.kind in {"unchanged", "changed", "cosmetic"})
    assert paired.prior.title == "Base"
    assert leftover.kind == "already_paired"
    assert leftover.closest_similarity >= 0.45
    assert leftover.kind != "uncertain"
    assert count_kinds(rows)["already_paired"] == 1
    assert count_kinds(rows)["removed"] == 0


def test_leftover_between_thresholds_stays_unclassified():
    prior = "credit collateral mortgage prepayment servicing capital liquidity wholesale"
    current_close = "credit collateral mortgage prepayment deposit branch treasury custody"
    current_other = "succession planning minutes director committee agenda calendar retreat"
    overlap = content_overlap(prior, current_close)
    assert 0.30 <= overlap < 0.45
    rows = align_passages([_p(prior)], [_p(current_close), _p(current_other)])
    leftover = next(row for row in rows if row.prior and row.prior.text == prior)
    assert leftover.kind == "uncertain"
    assert leftover.closest_similarity == overlap
    assert "already_paired" not in {row.kind for row in rows}


def test_low_overlap_is_not_forced_into_a_match():
    prior = "Earthquake insurance covers only a narrow set of owned facilities in California and nowhere else."
    current = "The board reviews succession planning for the chief executive at least once each fiscal year."
    assert content_overlap(prior, current) < 0.45
    rows = align_passages([_p(prior)], [_p(current)])
    assert {r.kind for r in rows} <= {"added", "removed", "uncertain"}
    assert "changed" not in {r.kind for r in rows}
