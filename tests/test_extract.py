from drift_detector.extract import extract_sections

MINI = """
<html><body>
<ix:header><ix:hidden>
Item 1A. Risk Factors hidden garbage that must not be extracted as the section body.
Item 9A. Controls and Procedures hidden garbage that must not be extracted.
</ix:hidden></ix:header>
<table>
<tr><td><a href="#r">Item 1A.</a></td><td>Risk Factors</td></tr>
<tr><td><a href="#b">Item 1B.</a></td><td>Unresolved Staff Comments</td></tr>
<tr><td><a href="#k">Item 9A.</a></td><td>Controls and Procedures</td></tr>
<tr><td><a href="#o">Item 9B.</a></td><td>Other Information</td></tr>
</table>
<p>See Item 1A of this Form 10-K under the heading Risk Factors for a discussion that is not the section.</p>
<div id="r"></div>
<div style="font-weight:700">Item 1A. Risk Factors</div>
<div>The following describes risks that could affect the company, its results, and its capital over a sustained period.</div>
<div style="font-weight:700">Credit</div>
<div style="font-weight:700">We face credit risk from borrowers.</div>
<div>Borrowers may fail to repay loans when the economy slows, and that can reduce earnings and capital for a sustained period of time.</div>
<div style="font-weight:700">Item 1B. Unresolved Staff Comments</div>
<div>None.</div>
<div id="k"></div>
<div style="font-weight:700">Item 9A. Controls and Procedures</div>
<div style="font-weight:700">Evaluation of Disclosure Controls and Procedures</div>
<div>Management concluded that the disclosure controls and procedures were effective as of December 31, 2024.</div>
<div>Management concluded that internal control over financial reporting was effective as of December 31, 2024.</div>
<div>The independent registered public accounting firm has issued an audit report on the Company's internal control over financial reporting, which appears in Item 8.</div>
<div>There were no changes in internal control over financial reporting during the fourth quarter of 2024 that have materially affected internal control over financial reporting.</div>
<div style="font-weight:700">Item 9B. Other Information</div>
<div>Insider trading arrangements were adopted and are not part of Item 9A.</div>
</body></html>
"""


def test_extracts_body_not_toc_or_hidden():
    sections = extract_sections(MINI, min_chars={"1A": 80, "9A": 80})
    risk = sections["1A"]
    controls = sections["9A"]
    assert risk.status == "ok"
    assert "hidden garbage" not in risk.text
    assert "Insider trading" not in controls.text
    assert "Unresolved" not in risk.text
    assert "We face credit risk from borrowers." in risk.text
    assert risk.end_header.startswith("Item 1B")
    titles = [p.title for p in risk.passages]
    assert "We face credit risk from borrowers." in titles
    for passage in risk.passages + controls.passages:
        assert passage.text in (risk.text if passage in risk.passages else controls.text)
    assert controls.status == "ok"
    assert "were effective as of December 31, 2024" in controls.text
    assert "Item 8" in controls.text


def test_running_header_does_not_truncate_the_section():
    html = """
    <html><body>
    <div style="font-weight:700">Item 1A. Risk Factors</div>
    <div>First page of risks about credit exposure that can reduce capital when borrowers default together over a long downturn.</div>
    <div style="font-weight:700">Item 1A. Risk Factors</div>
    <div>Second page of risks about liquidity stress that can limit refinancing when wholesale markets close for an extended period.</div>
    <div style="font-weight:700">Item 1B. Unresolved Staff Comments</div>
    <div>None.</div>
    <div style="font-weight:700">Item 9A. Controls and Procedures</div>
    <div style="display:none"><div style="display:none">hidden Item 1A. Risk Factors secret text that is not part of the section.</div></div>
    <div>Management concluded that the disclosure controls and procedures were effective as of December 31, 2024 and that conclusion is the one to keep.</div>
    <div style="font-weight:700">Item 9B. Other Information</div>
    <div>None.</div>
    </body></html>
    """
    sections = extract_sections(html, min_chars={"1A": 40, "9A": 40})
    text = sections["1A"].text
    assert "credit exposure" in text
    assert "liquidity stress" in text
    assert text.count("Item 1A") == 1
    assert "secret text" not in text
    assert sections["9A"].status == "ok"


def test_split_capital_heading_beats_the_table_of_contents():
    html = """
    <html><body>
    <table><tr><td>Item 1A. Risk Factors</td><td>Item 1B. Unresolved Staff Comments</td></tr></table>
    <div>ITEM 1A. RIS K FACTORS</div>
    <div style="font-weight:700">Competition could reduce our margins and our ability to hire.</div>
    <div>Competitors may undercut prices for an extended period and customers may switch, which would reduce revenue and earnings.</div>
    <div>Item 1A</div>
    <div>A second page of the same risk factor section discusses supply interruptions that can halt shipments for more than a quarter.</div>
    <div>Item 1B. Unresolved Staff Comments</div>
    <div>None.</div>
    </body></html>
    """
    section = extract_sections(html, min_chars={"1A": 40, "9A": 10})["1A"]
    assert section.status == "ok"
    assert "supply interruptions" in section.text
    assert "Competitors may undercut" in section.text
    assert section.text.count("Item 1A") + section.text.count("ITEM 1A") == 1
    for passage in section.passages:
        assert passage.text in section.text


def test_missing_item_is_missing():
    html = "<html><body><div>Item 1. Business</div><p>The company sells goods and services to customers in many markets and has done so for years.</p></body></html>"
    sections = extract_sections(html, min_chars={"1A": 10, "9A": 10})
    assert sections["1A"].status == "missing"
    assert sections["1A"].text == ""
    assert sections["9A"].status == "missing"


def test_short_extract_is_incomplete_under_default_minimum():
    sections = extract_sections(MINI)
    assert sections["1A"].status == "incomplete"
    assert sections["1A"].notes
