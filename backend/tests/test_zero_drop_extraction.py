"""Tests for GitHub Issue #11: Zero-drop extraction for Slide Header, KPI units, in-slide CTAs, and Semantic Tone."""
from __future__ import annotations

from pathlib import Path
import pytest

from htmlppt.analyze import ir
from htmlppt.analyze.classifier import NUM_RE
from htmlppt.analyze.segmenter import build_deck
from htmlppt.extract.renderer import snapshot_html

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTOMOTIVE_HTML = REPO_ROOT / "ai_automotive_motor_ecu_v_cycle.html"


@pytest.fixture(scope="module")
def automotive_deck() -> ir.Deck:
    snap = snapshot_html(path=AUTOMOTIVE_HTML)
    return build_deck(snap)


def test_num_re_time_rate_and_engineering_units():
    for sample in (
        "65%",
        "50%",
        "100%",
        "8 wks",
        "12 mos",
        "24 hrs",
        "45 days",
        "3 yrs",
        "100 ms",
        "50 µs",
        "3.5x",
        "10×",
        "800V",
        "250 kW",
        "10 Gbps",
        "€450k+",
    ):
        assert NUM_RE.match(sample), f"NUM_RE failed to match {sample!r}"


def test_kpi_group_fallback_never_drops_fourth_sibling():
    from htmlppt.analyze.classifier import Ctx, try_kpis

    def make_tile(val_text: str, label_text: str, delta_text: str, x: float) -> dict:
        return {
            "id": int(x),
            "tag": "div",
            "box": {"x": x, "y": 100, "w": 250, "h": 120},
            "kids": [
                {"id": 1, "tag": "div", "cls": "value", "fs": 38, "fw": 800, "box": {"x": x, "y": 105, "w": 200, "h": 40}, "runs": [{"t": val_text}]},
                {"id": 2, "tag": "div", "cls": "label", "fs": 14, "fw": 700, "box": {"x": x, "y": 150, "w": 200, "h": 20}, "runs": [{"t": label_text}]},
                {"id": 3, "tag": "div", "cls": "delta", "fs": 12, "fw": 400, "c": "10b981", "box": {"x": x, "y": 175, "w": 200, "h": 20}, "runs": [{"t": delta_text}]},
            ],
        }

    kids = [
        make_tile("65%", "Automation", "▲ 3.5x", 0),
        make_tile("50%", "Calibration", "▲ €450k", 270),
        make_tile("100%", "Sovereignty", "🔒 Air-Gapped", 540),
        make_tile("ASIL-D", "Safety Grade", "✓ ISO 26262 Certified", 810),
    ]
    grp = try_kpis(kids, Ctx(), {"x": 0, "y": 100, "w": 1060, "h": 120, "sw": 1280, "sh": 720})
    assert grp is not None
    assert len(grp.items) == 4
    assert grp.items[3].value == "ASIL-D"
    assert grp.items[3].label == "Safety Grade"
    assert "ISO 26262" in grp.items[3].delta
    assert grp.items[3].sentiment == "positive"


def test_slide_headers_and_footers_zero_bleed(automotive_deck: ir.Deck):
    assert automotive_deck.source_kind == "deck"
    assert len(automotive_deck.slides) == 5

    expected_kickers = [
        "EXECUTIVE VISION · AUTOMOTIVE E-DRIVE SOFTWARE",
        "ENGINEERING FOUNDATIONS · BEYOND GENERIC LLMS",
        "END-TO-END V-MODEL TRANSFORMATION · ASPICE SWE.1 TO SWE.6",
        "ENTERPRISE SECURITY ARCHITECTURE · ZERO TRUST",
        "STRATEGIC ENGAGEMENT & PARTNERSHIP · NEXT STEPS",
    ]
    expected_titles = [
        "Accelerating Motor ECU Engineering with Secure, Sovereign AI",
        "What is AI in Deep Automotive Motor ECU Development?",
        "AI Across the Motor ECU V-Development Cycle",
        "Absolute IP Protection & Zero Data Leakage Guarantee",
        "Turnkey Services: Accelerate Your Motor ECU Programs",
    ]

    for idx, slide in enumerate(automotive_deck.slides):
        assert slide.kicker.upper() == expected_kickers[idx]
        assert slide.title == expected_titles[idx]
        assert len(slide.subtitle) > 140, f"Slide {idx + 1} subtitle should not be truncated or dropped"
        assert slide.footer_left != "", f"Slide {idx + 1} footer_left should be extracted"
        assert f"SLIDE {idx + 1} OF 5" in slide.footer_right.upper()

        # Verify no kicker, subtitle, or footer bled into slide.blocks as Heading or Paragraph
        for b in slide.blocks:
            if isinstance(b, (ir.Heading, ir.Paragraph)):
                txt = ir.runs_text(b.runs).strip()
                assert slide.kicker.lower() not in txt.lower()
                assert slide.subtitle.lower() not in txt.lower()
                assert "SLIDE " not in txt.upper()


def test_slide1_zero_drop_kpis_and_cards(automotive_deck: ir.Deck):
    s1 = automotive_deck.slides[0]
    kpi_groups = [b for b in s1.blocks if isinstance(b, ir.KPIGroup)]
    assert len(kpi_groups) == 1
    kpis = kpi_groups[0].items
    assert len(kpis) == 4, "All 4 KPI tiles including '8 wks' must be retained"
    assert [k.value for k in kpis] == ["65%", "50%", "100%", "8 wks"]
    assert [k.label for k in kpis] == [
        "Test Authoring Automation",
        "Dyno Calibration Reduction",
        "Zero Data Exposure",
        "Cycle-Time Compression",
    ]
    # Primary delta highlight and secondary description are both preserved in delta, not merged into label
    assert "3.5x SIL/HIL Coverage" in kpis[0].delta
    assert "Automates CAPL" in kpis[0].delta
    assert "From 18 to 10 Months" in kpis[3].delta
    assert "SWE.1-SWE.6" in kpis[3].delta
    assert all(k.sentiment == "positive" for k in kpis)

    card_grids = [b for b in s1.blocks if isinstance(b, ir.CardGrid)]
    assert len(card_grids) == 1
    cards = card_grids[0].cards
    assert len(cards) == 2
    assert "The E-Mobility Motor ECU Bottleneck" in cards[0].title
    assert cards[0].badge == "⚡"
    assert not ir.runs_text(cards[0].body).startswith("⚡")
    assert "The Solution: Automotive-Grade Engineering AI" in cards[1].title
    assert cards[1].badge == "🛡️"


def test_slide2_cards_and_table_semantic_tones(automotive_deck: ir.Deck):
    s2 = automotive_deck.slides[1]
    card_grids = [b for b in s2.blocks if isinstance(b, ir.CardGrid)]
    assert len(card_grids) == 1
    assert len(card_grids[0].cards) == 3
    for c in card_grids[0].cards:
        assert len(c.bullets) == 3

    tables = [b for b in s2.blocks if isinstance(b, ir.Table)]
    assert len(tables) == 1
    tbl = tables[0]
    assert len(tbl.rows) == 5
    assert tbl.header_rows == 1

    # Row 1: Data Confidentiality -> tag-bad ("High Risk") and tag-good ("100% Sovereign")
    bad_cell = tbl.rows[1][1]
    good_cell = tbl.rows[1][2]
    assert any(r.badge and r.tone == "negative" and "High Risk" in r.t for r in bad_cell.runs)
    assert any(r.badge and r.tone == "positive" and "100% Sovereign" in r.t for r in good_cell.runs)
    assert bad_cell.tone == "negative"
    assert good_cell.tone == "positive"


def test_slide3_v_cycle_callouts_and_scoped_row_group(automotive_deck: ir.Deck):
    s3 = automotive_deck.slides[2]
    callouts = [b for b in s3.blocks if isinstance(b, ir.Callout)]
    assert len(callouts) == 7, "All 6 SWE.1-SWE.6 V-Cycle steps plus center CLOSED LOOP spine must be Callouts"

    # Left branch (col 0): SWE.1, SWE.2, SWE.3
    left = [c for c in callouts if c.column == 0]
    assert len(left) == 3
    assert [c.title for c in left] == [
        "SWE.1: Requirements Engineering",
        "SWE.2: System Architecture",
        "SWE.3: Detailed MBD & Code Gen",
    ]
    assert [c.badge for c in left] == [
        "DOORS / Polarion",
        "AUTOSAR ARXML",
        "Simulink / TargetLink",
    ]
    assert all(c.tone == "primary" for c in left)

    # Center spine (col 1): CLOSED LOOP
    center = [c for c in callouts if c.column == 1]
    assert len(center) == 1
    assert "CLOSED LOOP" in center[0].title
    assert "Continuous Automated Traceability" in ir.runs_text(center[0].runs)

    # Right branch (col 2): SWE.4, SWE.5, SWE.6
    right = [c for c in callouts if c.column == 2]
    assert len(right) == 3
    assert [c.title for c in right] == [
        "SWE.4: Unit & Static Verification",
        "SWE.5: SIL / HIL Integration",
        "SWE.6: Dyno Calibration & Mapping",
    ]
    assert [c.badge for c in right] == [
        "Polyspace / TPT",
        "dSPACE / Vector CANoe",
        "ETAS INCA / CANape",
    ]
    assert all(c.tone == "positive" for c in right)

    # All 7 V-Cycle callouts share a scoped 3-column row_group
    row_groups = {c.row_group for c in callouts}
    assert len(row_groups) == 1 and None not in row_groups
    assert all(c.total_columns == 3 for c in callouts)

    # Bottom comparison cards are a full-width CardGrid (column=None, row_group=None) with negative/positive tones
    card_grids = [b for b in s3.blocks if isinstance(b, ir.CardGrid)]
    assert len(card_grids) == 1
    cg = card_grids[0]
    assert cg.column is None
    assert cg.row_group is None
    assert len(cg.cards) == 2
    assert cg.cards[0].tone == "negative"
    assert cg.cards[1].tone == "positive"


def test_slide4_security_pillars_and_airgap_callout(automotive_deck: ir.Deck):
    s4 = automotive_deck.slides[3]
    card_grids = [b for b in s4.blocks if isinstance(b, ir.CardGrid)]
    assert len(card_grids) == 1
    pillars = card_grids[0].cards
    assert len(pillars) == 4
    for idx, p in enumerate(pillars, 1):
        assert f"PILLAR {idx}" in p.kicker.upper()
        assert p.tone == "positive"

    callouts = [b for b in s4.blocks if isinstance(b, ir.Callout)]
    assert len(callouts) == 1
    airgap = callouts[0]
    assert airgap.column is None
    assert "Enterprise Air-Gap Deployment Mode" in airgap.title
    assert "ZERO DATA LEAKAGE CERTIFIED" in airgap.badge
    assert "Containerized microservices" in ir.runs_text(airgap.runs)


def test_slide5_steps_deliverables_and_in_slide_cta_button(automotive_deck: ir.Deck):
    s5 = automotive_deck.slides[4]
    card_grids = [b for b in s5.blocks if isinstance(b, ir.CardGrid)]
    assert len(card_grids) == 2

    services = card_grids[0].cards
    assert len(services) == 4
    expected_footers = [
        "▸ 60% Faster HIL Validation",
        "▸ 50% Dyno Hour Reduction",
        "▸ 100% Audit-Ready SWE.1-6",
        "▸ Zero Security Compromise",
    ]
    for idx, srv in enumerate(services):
        assert srv.kicker == f"SERVICE 0{idx + 1}"
        assert srv.footer == expected_footers[idx]
        assert expected_footers[idx] not in ir.runs_text(srv.body)

    phases = card_grids[1].cards
    assert len(phases) == 2
    assert "Phase 1" in phases[0].title
    assert "Phase 2" in phases[1].title

    # CTA Banner with in-slide <button class="cta-btn">Schedule Technical Briefing</button>
    callouts = [b for b in s5.blocks if isinstance(b, ir.Callout)]
    assert len(callouts) == 1
    cta = callouts[0]
    assert cta.column is None
    assert "Ready to Cut 6+ Months" in cta.title
    assert cta.badge.upper() == "SCHEDULE TECHNICAL BRIEFING"
    assert "Book a 45-minute" in ir.runs_text(cta.runs)
