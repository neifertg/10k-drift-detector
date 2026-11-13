import json

import pytest

from drift_detector.bundle import export_bundle
from drift_detector.draft import DraftError, build_sector_packet, draft_briefs, load_prompt, parse_json_object
from tests.fixtures.example_brief import (
    ALPHA_ADDED,
    ALPHA_BASE,
    BETA_BASE,
    BETA_TARIFF,
    _company,
    _evidence,
    companies,
    overview_draft,
    sector_draft,
)


def test_prompt_requires_a_verbatim_quote_and_an_accession():
    system = load_prompt("sector_brief.md")
    assert "accession" in system
    assert "Character for character" in system
    overview = load_prompt("overview_brief.md")
    assert "quote exactly" in overview
    assert "Name a company only when the evidence on that same item quotes it." in system
    assert "Name a company only when the evidence on that same item quotes it." in overview
    assert "takeaways" in overview
    assert "at most two of the four themes may be about artificial intelligence" in overview
    assert "audit_consideration" in system
    assert "audit_consideration" in overview


def test_packet_omits_a_heading_that_only_bounds_the_section():
    from drift_detector.models import Flag, ItemComparison, Quote

    drafted = companies()
    drafted[0].comparisons.append(
        ItemComparison(
            item="9A",
            prior_report_date="2024-12-31",
            current_report_date="2025-12-31",
            supported=True,
            reason=None,
            full_overlap=0.9,
            counts={},
            rows=[],
            flags=[
                Flag(
                    signal="remediation",
                    index_line="none",
                    statement="Neither extract contains the word.",
                    quotes=[
                        Quote(
                            text="Item 9B. Other Information.",
                            item="9A",
                            report_date="2025-12-31",
                            accession="0000000001-26-000010",
                            document_url="https://www.sec.gov/example",
                            passage_url="https://www.sec.gov/example",
                        ),
                        Quote(
                            text=(
                                "Management concluded that the disclosure controls and procedures "
                                "were effective as of the period end."
                            ),
                            item="9A",
                            report_date="2025-12-31",
                            accession="0000000001-26-000010",
                            document_url="https://www.sec.gov/example",
                            passage_url="https://www.sec.gov/example",
                        ),
                    ],
                )
            ],
            prior_status="ok",
            current_status="ok",
            segmentation_prior="paragraphs",
            segmentation_current="paragraphs",
        )
    )
    packet = build_sector_packet(drafted[0], drafted[1])
    quotes = [quote["text"] for quote in packet["companies"][0]["item_9a"]["flags"][0]["quotes"]]
    assert "Item 9B. Other Information." not in quotes
    assert any("disclosure controls" in quote for quote in quotes)


def test_packet_contains_the_added_sentence_and_not_the_tool_note_as_a_quote_source():
    drafted = companies()
    packet = build_sector_packet(drafted[0], drafted[1])
    encoded = json.dumps(packet)
    assert ALPHA_ADDED in encoded
    assert packet["sector"] == "Example"
    assert packet["companies"][0]["item_1a"]["passages"][0]["kind"] == "added"
    assert packet["companies"][0]["item_1a"]["passages"][0]["accession"] == "0000000001-26-000010"


def test_draft_briefs_writes_verified_json_and_does_not_send_the_api_key(tmp_path, monkeypatch):
    bundle = tmp_path / "input"
    export_bundle(companies(), bundle, "2026-11-13 00:00 UTC", sectors=["Example"], extractor_version=5)
    calls = []

    def transport(body):
        calls.append(body)
        if body["system"].startswith("You draft a sector brief"):
            text = json.dumps(sector_draft())
        else:
            text = json.dumps(overview_draft())
        return {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1200, "output_tokens": 340, "cache_read_input_tokens": 50},
            "content": [
                {"type": "thinking", "thinking": "not part of the draft"},
                {"type": "text", "text": text},
            ],
        }

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-should-not-appear")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    out = tmp_path / "drafts"
    draft_briefs(bundle, out, transport=transport)
    assert (out / "overview.json").exists()
    assert (out / "sectors" / "example.json").exists()
    saved = json.loads((out / "sectors" / "example.json").read_text())
    assert saved["model"] == "claude-sonnet-5-5"
    assert saved["themes"][0]["evidence"][0]["quote"] == ALPHA_ADDED
    assert len(calls) == 2
    assert calls[0]["model"] == "claude-sonnet-5-5"
    assert calls[0]["output_config"]["effort"] == "medium"
    assert ALPHA_ADDED in calls[0]["messages"][0]["content"]
    assert "test-key-should-not-appear" not in json.dumps(calls[0])
    overview_request = calls[1]["messages"][0]["content"]
    assert ALPHA_ADDED in overview_request
    usage = json.loads((out / "token-usage.json").read_text())
    assert [row["label"] for row in usage["requests"]] == ["Example", "overview"]
    assert usage["requests"][0]["input_tokens"] == 1200
    assert usage["requests"][1]["output_tokens"] == 340
    assert usage["requests"][0]["cache_read_input_tokens"] == 50
    assert "test-key-should-not-appear" not in (out / "token-usage.json").read_text()


def test_a_bad_quote_is_retried_once_and_then_rejected(tmp_path, monkeypatch):
    bundle = tmp_path / "input"
    export_bundle(companies(), bundle, "2026-11-13 00:00 UTC", sectors=["Example"], extractor_version=5)
    bad = sector_draft()
    bad["themes"][0]["evidence"][0]["quote"] = ALPHA_ADDED.replace("artificial intelligence", "quantum computing")
    responses = [bad, bad]

    def transport(body):
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(responses.pop(0))}]}

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(DraftError) as caught:
        draft_briefs(bundle, tmp_path / "drafts", transport=transport)
    assert "retry" in str(caught.value).lower()
    assert not (tmp_path / "drafts" / "overview.json").exists()


def test_dry_run_does_not_require_a_key(tmp_path, monkeypatch, capsys):
    bundle = tmp_path / "input"
    export_bundle(companies(), bundle, "2026-11-13 00:00 UTC", sectors=["Example"], extractor_version=5)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    draft_briefs(bundle, tmp_path / "drafts", dry_run=True)
    captured = capsys.readouterr()
    assert "Dry run" in captured.out
    assert "Example" in captured.out
    assert not (tmp_path / "drafts" / "overview.json").exists()


def test_missing_api_key_fails_before_a_call(tmp_path, monkeypatch):
    bundle = tmp_path / "input"
    export_bundle(companies(), bundle, "2026-11-13 00:00 UTC", sectors=["Example"], extractor_version=5)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(DraftError) as caught:
        draft_briefs(bundle, tmp_path / "drafts")
    assert "ANTHROPIC_API_KEY" in str(caught.value)


def test_fenced_json_is_accepted():
    payload = parse_json_object("```json\n{\"schema_version\": 1}\n```")
    assert payload["schema_version"] == 1


def _api_ok(payload: dict) -> dict:
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [{"type": "text", "text": json.dumps(payload)}]}


def _other_sector_draft() -> dict:
    raw = sector_draft()
    raw["sector"] = "Other"
    raw["divergence"]["text"] = (
        "Gamma added language on artificial intelligence in customer service, and Delta did not. "
        "Delta added language on tariffs, and Gamma did not."
    )
    raw["divergence"]["evidence"][0]["ticker"] = "CCC"
    raw["divergence"]["evidence"][0]["accession"] = "0000000003-26-000010"
    raw["divergence"]["evidence"][1]["ticker"] = "DDD"
    raw["divergence"]["evidence"][1]["accession"] = "0000000004-26-000010"
    raw["themes"][0]["what_changed"] = (
        "Gamma's latest risk-factor section adds a sentence on incorrect responses from "
        "artificial intelligence used in customer service. "
        "The sentence says those responses could reach clients."
    )
    raw["themes"][0]["evidence"][0]["ticker"] = "CCC"
    raw["themes"][0]["evidence"][0]["accession"] = "0000000003-26-000010"
    return raw


def _two_overview() -> dict:
    raw = overview_draft()
    raw["sectors"].append(
        {
            "sector": "Other",
            "takeaways": [
                {
                    "title": "Customer service models",
                    "what_changed": (
                        "Gamma's latest risk-factor section adds a sentence on incorrect responses from "
                        "artificial intelligence used in customer service. "
                        "The sentence says those responses could reach clients."
                    ),
                    "audit_consideration": (
                        "Ask whether customer-service responses produced by a model are reviewed before they reach a client."
                    ),
                    "evidence": [_evidence("CCC", "0000000003-26-000010", ALPHA_ADDED, "added")],
                }
            ],
        }
    )
    return raw


def _two_sector_bundle(tmp_path):
    paired = companies() + [
        _company(
            ticker="CCC",
            name="Gamma Audit Co.",
            cik="0000000003",
            prior_accession="0000000003-25-000010",
            current_accession="0000000003-26-000010",
            base=ALPHA_BASE,
            added=ALPHA_ADDED,
            label="Models",
            document_stem="ccc",
        ),
        _company(
            ticker="DDD",
            name="Delta Audit Co.",
            cik="0000000004",
            prior_accession="0000000004-25-000010",
            current_accession="0000000004-26-000010",
            base=BETA_BASE,
            added=BETA_TARIFF,
            label="Tariffs",
            document_stem="ddd",
        ),
    ]
    paired[2].sector = "Other"
    paired[3].sector = "Other"
    bundle = tmp_path / "input"
    export_bundle(paired, bundle, "2026-11-13 00:00 UTC", sectors=["Example", "Other"], extractor_version=5)
    return bundle


def _reply_for(body: dict, *, fail_other: bool = False) -> dict:
    user = body["messages"][0]["content"]
    if body["system"].startswith("You draft a sector brief"):
        if "Draft the sector brief for Example." in user:
            return _api_ok(sector_draft())
        if "Draft the sector brief for Other." in user:
            if fail_other:
                raise DraftError("Anthropic API request failed. HTTP 400: credit balance is too low.")
            return _api_ok(_other_sector_draft())
        raise AssertionError(user[:120])
    return _api_ok(_two_overview())


def test_an_api_error_keeps_sector_files_already_written(tmp_path, monkeypatch):
    bundle = _two_sector_bundle(tmp_path)
    out = tmp_path / "drafts"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(DraftError) as caught:
        draft_briefs(bundle, out, transport=lambda body: _reply_for(body, fail_other=True))
    message = str(caught.value)
    assert "Other was not saved" in message
    assert "HTTP 400" in message
    assert "kept" in message
    assert (out / "sectors" / "example.json").exists()
    assert not (out / "sectors" / "other.json").exists()
    assert not (out / "overview.json").exists()


def test_a_later_run_skips_a_valid_sector_unless_forced(tmp_path, monkeypatch):
    bundle = _two_sector_bundle(tmp_path)
    out = tmp_path / "drafts"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    draft_briefs(bundle, out, transport=lambda body: _reply_for(body))

    def boom(body):
        raise DraftError("should not be called")

    draft_briefs(bundle, out, transport=boom)
    forced = []

    def collect(body):
        forced.append(body["messages"][0]["content"][:80])
        return _reply_for(body)

    draft_briefs(bundle, out, transport=collect, force=True)
    assert any("for Example" in text for text in forced)
    assert any("for Other" in text for text in forced)
    assert any("sector briefs" in text for text in forced)


def test_only_drafts_the_named_sector_and_leaves_the_others(tmp_path, monkeypatch):
    bundle = _two_sector_bundle(tmp_path)
    out = tmp_path / "drafts"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = []

    def transport(body):
        seen.append(body["messages"][0]["content"][:48])
        return _reply_for(body)

    draft_briefs(bundle, out, transport=transport, only="Other")
    assert any("Other" in text for text in seen)
    assert not any("Example" in text for text in seen)
    assert (out / "sectors" / "other.json").exists()
    assert not (out / "sectors" / "example.json").exists()
    assert not (out / "overview.json").exists()

    seen.clear()
    draft_briefs(bundle, out, transport=transport, only="example")
    assert any("Example" in text for text in seen)
    assert not any("for Other" in text for text in seen)
    assert (out / "overview.json").exists()
