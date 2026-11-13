import json

from drift_detector.bundle import export_bundle
from drift_detector.pipeline import render_from_bundle
from tests.fixtures.example_brief import ALPHA_ADDED, BETA_TARIFF, companies, overview_draft, sector_draft


def _bundle(tmp_path):
    bundle = tmp_path / "input"
    export_bundle(companies(), bundle, "2026-11-13 00:00 UTC", sectors=["Example"], extractor_version=5)
    return bundle


def _drafts(tmp_path, overview=None, sector=None):
    drafts = tmp_path / "drafts"
    (drafts / "sectors").mkdir(parents=True)
    (drafts / "sectors" / "example.json").write_text(json.dumps(sector or sector_draft()))
    (drafts / "overview.json").write_text(json.dumps(overview or overview_draft()))
    return drafts


def test_verified_fixture_renders_the_overview_and_the_sector_brief(tmp_path):
    code = render_from_bundle(_bundle(tmp_path), _drafts(tmp_path), tmp_path / "docs")
    assert code == 0
    index = (tmp_path / "docs" / "index.html").read_text()
    sector = (tmp_path / "docs" / "peers" / "example.html").read_text()
    company = (tmp_path / "docs" / "company" / "aaa.html").read_text()
    assert "Emerging risks for the audit plan" in index
    assert "Artificial intelligence" in index
    assert ALPHA_ADDED in index
    assert "Audit plan" in index
    assert "It is not investment advice." in index
    assert "Briefs not yet generated" not in index
    assert "Where the peers diverge" in sector
    assert ALPHA_ADDED in sector
    assert "tariffs increase on imported merchandise" in sector
    assert "Passage-level comparison" in sector
    assert "This page is the passage-level comparison." in company
    assert "<script>" not in index


def test_prose_is_escaped(tmp_path):
    overview = overview_draft()
    overview["themes"][0]["what_changed"] = (
        "Alpha added a sentence about models. The marker <script> is only a test."
    )
    code = render_from_bundle(_bundle(tmp_path), _drafts(tmp_path, overview=overview), tmp_path / "docs")
    assert code == 0
    index = (tmp_path / "docs" / "index.html").read_text()
    assert "<script>" not in index
    assert "&lt;script&gt;" in index


def test_missing_drafts_leave_a_clear_empty_state(tmp_path):
    code = render_from_bundle(_bundle(tmp_path), tmp_path / "drafts", tmp_path / "docs")
    assert code == 0
    index = (tmp_path / "docs" / "index.html").read_text()
    sector = (tmp_path / "docs" / "peers" / "example.html").read_text()
    assert "Briefs not yet generated" in index
    assert "Briefs not yet generated" in sector
    assert ALPHA_ADDED not in index
    assert (tmp_path / "docs" / "company" / "aaa.html").exists()


def test_a_bad_quote_is_not_published_as_a_theme(tmp_path):
    overview = overview_draft()
    overview["themes"][0]["title"] = "Quantum computing"
    overview["themes"][0]["evidence"][0]["quote"] = ALPHA_ADDED.replace(
        "artificial intelligence", "quantum computing"
    )
    code = render_from_bundle(_bundle(tmp_path), _drafts(tmp_path, overview=overview), tmp_path / "docs")
    assert code == 1
    index = (tmp_path / "docs" / "index.html").read_text()
    assert "did not pass quote verification" in index
    assert 'id="theme-quantum-computing"' not in index
    assert (tmp_path / "docs" / "company" / "aaa.html").exists()
