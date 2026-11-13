import json

import pytest

from drift_detector.bundle import export_bundle
from drift_detector.draft import DraftError, build_sector_packet, draft_briefs, load_prompt, parse_json_object
from tests.fixtures.example_brief import ALPHA_ADDED, companies, overview_draft, sector_draft


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
