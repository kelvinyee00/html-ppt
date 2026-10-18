"""Unit tests for the zero-drop Compound Slide engine, 3-column V-Cycle bands,
proportional auto-fit typography, and Semantic Tone rendering across themes.
"""
from __future__ import annotations

import io
import pytest
from pptx import Presentation

from htmlppt.analyze import ir
from htmlppt.brand.presets import apply_preset, palette_brand
from htmlppt.layout import model as lm
from htmlppt.layout.archetypes import select_archetype
from htmlppt.layout.engine import (
    _estimate_paras_height_emu,
    layout_deck,
    layout_slide,
    tone_color,
)
from htmlppt.render.pptx_writer import write_pptx


def _slide_all_text(sm: lm.SlideModel) -> str:
    parts: list[str] = []
    for el in sm.elements:
        if isinstance(el, lm.TextEl):
            for p in el.paras:
                parts.append("".join(r.text for r in p.runs))
        elif isinstance(el, lm.ShapeEl) and el.paras:
            for p in el.paras:
                parts.append("".join(r.text for r in p.runs))
        elif isinstance(el, lm.TableEl):
            for row in el.rows:
                for cell in row:
                    for p in cell.paras:
                        parts.append("".join(r.text for r in p.runs))
    return "\n".join(parts)


def _assert_zero_text_overflow(sm: lm.SlideModel, deck_h: int) -> None:
    for el in sm.elements:
        assert el.y >= 0, f"Element {el.name} has negative y={el.y}"
        assert el.y + el.h <= deck_h, f"Element {el.name} bottom {el.y + el.h} exceeds slide height {deck_h}"
        if isinstance(el, lm.TextEl) and el.paras:
            est_h = _estimate_paras_height_emu(el.paras, el.w, el.inset)
            assert est_h <= el.h, (
                f"Text overflow in '{el.name}': estimated height {est_h} EMU > box height {el.h} EMU"
            )
        elif isinstance(el, lm.TableEl):
            for ri, row in enumerate(el.rows):
                rh = el.row_heights[ri]
                for ci, cell in enumerate(row):
                    cw = el.col_widths[min(ci, len(el.col_widths) - 1)]
                    pad_x = 120000 if cell.is_header else 100000
                    pad_y = 85000 if cell.is_header else 65000
                    est_h = _estimate_paras_height_emu(cell.paras, max(100000, cw - 2 * pad_x))
                    assert est_h <= rh - 2 * pad_y + 20000, (
                        f"Table cell ({ri},{ci}) overflow: est_h={est_h} vs avail={rh - 2 * pad_y}"
                    )


def test_compound_slide_1_kpis_plus_cards_zero_drop():
    """Slide 1: 4 KPI tiles (including '8 wks') + 2 bottom context cards + header kicker & footer."""
    brand = apply_preset(palette_brand("ocean"), "corporate", dark=True)
    slide = ir.Slide(
        title="Accelerating Motor ECU Engineering with Secure, Sovereign AI",
        subtitle=(
            "Eliminating redundant manual engineering across the ASPICE V-Cycle for PMSM & Inverter "
            "control software—cutting time-to-market by 50% without compromising ASIL-D safety."
        ),
        kicker="Executive Vision · Automotive E-Drive Software",
        footer_left="Proprietary & Confidential · E-Mobility Powertrain AI Solution",
        blocks=[
            ir.KPIGroup(
                items=[
                    ir.KPI(value="65%", label="Test Authoring Automation", delta="▲ 3.5x SIL/HIL Coverage", sentiment="positive"),
                    ir.KPI(value="50%", label="Dyno Calibration Reduction", delta="▲ €450k+ Saved / Program", sentiment="positive"),
                    ir.KPI(value="100%", label="Zero Data Exposure", delta="🔒 100% On-Prem / Air-Gapped", sentiment="positive"),
                    ir.KPI(value="8 wks", label="Cycle-Time Compression", delta="▼ From 18 to 10 Months", sentiment="positive"),
                ]
            ),
            ir.CardGrid(
                cards=[
                    ir.Card(
                        title="The E-Mobility Motor ECU Bottleneck",
                        body=[ir.Run(t="Next-generation 800V SiC inverters and PMSM demand immense control complexity.")],
                    ),
                    ir.Card(
                        title="The Solution: Automotive-Grade Engineering AI",
                        body=[ir.Run(t="Embeds directly into DOORS, Simulink, TargetLink, Vector CANoe, dSPACE, ETAS INCA.")],
                    ),
                ]
            ),
        ],
    )

    sm = layout_slide(slide, brand, slide_num=1)
    txt = _slide_all_text(sm)

    for expected in (
        "EXECUTIVE VISION",
        "65%",
        "50%",
        "100%",
        "8 wks",
        "The E-Mobility Motor ECU Bottleneck",
        "The Solution: Automotive-Grade Engineering AI",
        "Proprietary & Confidential",
    ):
        assert expected in txt, f"Missing expected content '{expected}' on Slide 1"

    _assert_zero_text_overflow(sm, brand.slide_size.height)


@pytest.mark.parametrize("palette_id", ["ocean", "forest", "burgundy", "sunset"])
def test_compound_slide_2_cards_plus_table_and_semantic_tones(palette_id: str):
    """Slide 2: 3 Pillar Cards + Comparison Table with semantic tag-bad and tag-good badges."""
    brand = apply_preset(palette_brand(palette_id), "corporate", dark=True)
    slide = ir.Slide(
        title="What is AI in Deep Automotive Motor ECU Development?",
        subtitle="Deterministic neuro-symbolic framework engineered for ISO 26262 and real-time control physics.",
        kicker="Engineering Foundations · Beyond Generic LLMs",
        blocks=[
            ir.CardGrid(
                cards=[
                    ir.Card(
                        title="1. Physics-Informed ML Surrogates",
                        body=[ir.Run(t="Replaces sluggish FEA and ODE solvers:")],
                        bullets=[
                            ir.ListItem(runs=[ir.Run(t="Iron saturation, cross-coupling Ld/Lq mapping")]),
                            ir.ListItem(runs=[ir.Run(t="Inverter SiC switching losses & thermal transients")]),
                        ],
                    ),
                    ir.Card(
                        title="2. Domain-Tuned Semantic Agents",
                        body=[ir.Run(t="Trained on automotive embedded standards:")],
                        bullets=[
                            ir.ListItem(runs=[ir.Run(t="Deep knowledge of AUTOSAR Classic (RTE/BSW/MCAL)")]),
                            ir.ListItem(runs=[ir.Run(t="Native understanding of A2L/ASAP2, CAN/XCP, and MDF4")]),
                        ],
                    ),
                    ir.Card(
                        title="3. Deterministic Verification Gates",
                        body=[ir.Run(t="Zero hallucination tolerance:")],
                        bullets=[
                            ir.ListItem(runs=[ir.Run(t="Automated MISRA-C:2012 / CERT C rule checkers")]),
                            ir.ListItem(runs=[ir.Run(t="SMT-based formal bounds checking")]),
                        ],
                    ),
                ]
            ),
            ir.Table(
                header_rows=1,
                col_weights=[0.22, 0.38, 0.40],
                rows=[
                    [
                        ir.Cell(runs=[ir.Run(t="Dimension")], header=True),
                        ir.Cell(runs=[ir.Run(t="Generic Consumer AI")], header=True),
                        ir.Cell(runs=[ir.Run(t="Our Automotive Motor ECU AI Platform", tone="primary")], header=True),
                    ],
                    [
                        ir.Cell(runs=[ir.Run(t="Data Confidentiality", b=True)]),
                        ir.Cell(
                            runs=[
                                ir.Run(t="High Risk", tone="negative", badge=True, b=True),
                                ir.Run(t=" Egresses to public cloud; risks model retraining"),
                            ]
                        ),
                        ir.Cell(
                            runs=[
                                ir.Run(t="100% Sovereign", tone="positive", badge=True, b=True),
                                ir.Run(t=" Air-gapped / On-Premise; zero external transmission"),
                            ]
                        ),
                    ],
                ],
            ),
        ],
    )

    sm = layout_slide(slide, brand, slide_num=2)
    txt = _slide_all_text(sm)

    assert "1. Physics-Informed ML Surrogates" in txt
    assert "2. Domain-Tuned Semantic Agents" in txt
    assert "3. Deterministic Verification Gates" in txt
    assert "High Risk" in txt
    assert "100% Sovereign" in txt

    tbl_el = next(el for el in sm.elements if isinstance(el, lm.TableEl))
    bad_run = tbl_el.rows[1][1].paras[0].runs[0]
    good_run = tbl_el.rows[1][2].paras[0].runs[0]
    assert bad_run.color == brand.roles.negative
    assert bad_run.bold is True
    assert good_run.color == brand.roles.positive
    assert good_run.bold is True

    _assert_zero_text_overflow(sm, brand.slide_size.height)


def test_compound_slide_3_vcycle_3column_band_plus_summary_cards():
    """Slide 3: 3-column V-Cycle band (3 left callouts + 1 center spine + 3 right callouts) + 2 summary cards."""
    brand = apply_preset(palette_brand("ocean"), "corporate", dark=True)
    vcycle_blocks: list[ir.Block] = [
        ir.Callout(
            title="SWE.1: Requirements Engineering",
            badge="DOORS / Polarion",
            tone="primary",
            runs=[ir.Run(t="AI Parser: Converts OEM vehicle torque requests into formal Motor Control specs.")],
            column=0,
            row_group=1,
            total_columns=3,
        ),
        ir.Callout(
            title="SWE.2: System Architecture",
            badge="AUTOSAR ARXML",
            tone="primary",
            runs=[ir.Run(t="ARXML Co-Pilot: Automated SWC port mapping and runnable scheduling.")],
            column=0,
            row_group=1,
            total_columns=3,
        ),
        ir.Callout(
            title="SWE.3: Detailed MBD & Code Gen",
            badge="Simulink / TargetLink",
            tone="primary",
            runs=[ir.Run(t="Model & Code Assistant: Automated FOC sub-system harness generation.")],
            column=0,
            row_group=1,
            total_columns=3,
        ),
        ir.Callout(
            title="CLOSED LOOP",
            badge="⚡ ➔ ⚙️",
            tone="info",
            runs=[ir.Run(t="Continuous Automated Traceability")],
            column=1,
            row_group=1,
            total_columns=3,
        ),
        ir.Callout(
            title="SWE.4: Unit & Static Verification",
            badge="Polyspace / TPT",
            tone="positive",
            runs=[ir.Run(t="Test Vector Synthesizer: Achieves 100% MC/DC coverage for ASIL-D safety monitors.")],
            column=2,
            row_group=1,
            total_columns=3,
        ),
        ir.Callout(
            title="SWE.5: SIL / HIL Integration",
            badge="dSPACE / Vector CANoe",
            tone="positive",
            runs=[ir.Run(t="HIL Automation Engine: Generates automated CAPL/Python fault scripts.")],
            column=2,
            row_group=1,
            total_columns=3,
        ),
        ir.Callout(
            title="SWE.6: Dyno Calibration & Mapping",
            badge="ETAS INCA / CANape",
            tone="positive",
            runs=[ir.Run(t="Bayesian Calibration Co-Pilot: Slashes dyno cell operating hours by 50%.")],
            column=2,
            row_group=1,
            total_columns=3,
        ),
        ir.CardGrid(
            cards=[
                ir.Card(
                    title="Traditional Manual Bottleneck:",
                    tone="negative",
                    body=[ir.Run(t="Manual test script authoring, repetitive regression debug in CAN trace logs.")],
                ),
                ir.Card(
                    title="AI-Accelerated Engineering:",
                    tone="positive",
                    body=[ir.Run(t="Automated test synthesis, 10-second MDF4 log root-cause analysis.")],
                ),
            ]
        ),
    ]

    slide = ir.Slide(
        title="AI Across the Motor ECU V-Development Cycle",
        subtitle="Automating time-sink manual tasks from requirements decomposition down to dyno calibration.",
        kicker="End-to-End V-Model Transformation · ASPICE SWE.1 to SWE.6",
        blocks=vcycle_blocks,
    )

    assert select_archetype(slide) != "two_column"
    sm = layout_slide(slide, brand, slide_num=3)
    txt = _slide_all_text(sm)

    for step in ("SWE.1", "SWE.2", "SWE.3", "CLOSED LOOP", "SWE.4", "SWE.5", "SWE.6"):
        assert step in txt, f"Missing V-Cycle step '{step}' on Slide 3"
    assert "DOORS / Polarion" in txt
    assert "Traditional Manual Bottleneck:" in txt
    assert "AI-Accelerated Engineering:" in txt

    # Verify semantic tone accents on the two bottom cards
    tone_accents = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name == "Card Tone Accent"]
    assert len(tone_accents) == 2
    assert tone_accents[0].fill == brand.roles.negative
    assert tone_accents[1].fill == brand.roles.positive

    _assert_zero_text_overflow(sm, brand.slide_size.height)


def test_compound_slide_4_and_5_multi_zone_and_pptx_export():
    """Slides 4 & 5: 2x2 Security Pillars + Air-Gap Banner, and 1x4 Service Steps + 1x2 Roadmap + CTA Banner."""
    brand = apply_preset(palette_brand("ocean"), "corporate", dark=True)

    slide4 = ir.Slide(
        title="Absolute IP Protection & Zero Data Leakage Guarantee",
        subtitle="Mathematical data isolation with zero cloud exposure.",
        kicker="Enterprise Security Architecture · Zero Trust",
        blocks=[
            ir.CardGrid(
                cards=[
                    ir.Card(title="100% Air-Gapped / On-Premise VPC", badge="🔒 Pillar 1 · Sovereign Hosting", tone="positive", body=[ir.Run(t="Deployed completely behind your corporate firewall.")]),
                    ir.Card(title="Zero Data Retention & No Retraining", badge="🛡️ Pillar 2 · IP Rights", tone="positive", body=[ir.Run(t="Proprietary motor models are never used to train foundation models.")]),
                    ir.Card(title="TISAX Level 3 & ISO/SAE 21434 Certified", badge="⚡ Pillar 3 · Compliance", tone="positive", body=[ir.Run(t="Conforms strictly with automotive cybersecurity standards.")]),
                    ir.Card(title="Complete Audit Trail & Human Sign-off", badge="🔍 Pillar 4 · Traceability", tone="positive", body=[ir.Run(t="Every generated test case includes full cryptographic provenance.")]),
                ]
            ),
            ir.Callout(
                title="Enterprise Air-Gap Deployment Mode",
                badge="✓ ZERO DATA LEAKAGE CERTIFIED",
                tone="positive",
                runs=[ir.Run(t="Containerized microservices running locally with on-prem quantized model weights.")],
            ),
        ],
    )

    slide5 = ir.Slide(
        title="Turnkey Services: Accelerate Your Motor ECU Programs",
        subtitle="End-to-end enablement from toolchain integration to 4-week proof-of-concepts.",
        kicker="Strategic Engagement & Partnership · Next Steps",
        blocks=[
            ir.CardGrid(
                variant="process",
                cards=[
                    ir.Card(kicker="SERVICE 01", title="V-Cycle Test & HIL Suite", body=[ir.Run(t="Turnkey integration with dSPACE, Vector CANoe, and TPT.")], footer="▸ 60% Faster HIL Validation"),
                    ir.Card(kicker="SERVICE 02", title="Dyno Calibration Co-Pilot", body=[ir.Run(t="Bayesian active-learning agents to optimize test bench schedules.")], footer="▸ 50% Dyno Hour Reduction"),
                    ir.Card(kicker="SERVICE 03", title="ASPICE Compliance Accelerator", body=[ir.Run(t="Bidirectional traceability automation from DOORS to test logs.")], footer="▸ 100% Audit-Ready SWE.1-6"),
                    ir.Card(kicker="SERVICE 04", title="Air-Gapped Sovereign Deployment", body=[ir.Run(t="On-premise hardware setup and domain model fine-tuning.")], footer="▸ Zero Security Compromise"),
                ],
            ),
            ir.CardGrid(
                cards=[
                    ir.Card(title="Phase 1: 4-Week Rapid Proof of Concept (PoC)", body=[ir.Run(t="Select one high-friction subsystem and demonstrate 50%+ time savings.")]),
                    ir.Card(title="Phase 2: 12-Week Production Pilot & Toolchain Rollout", body=[ir.Run(t="Full on-prem deployment integrated into your CI/CD and HIL benches.")]),
                ]
            ),
            ir.Callout(
                title="Ready to Cut 6+ Months from Your Next Motor ECU Program?",
                badge="Schedule Technical Briefing",
                tone="primary",
                runs=[ir.Run(t="Book a 45-minute confidential technical deep dive and see our on-premise motor control AI demo.")],
            ),
        ],
    )

    sm4 = layout_slide(slide4, brand, slide_num=4)
    sm5 = layout_slide(slide5, brand, slide_num=5)

    # Slide 4: 4 security cards should be 2x2 grid (2 distinct y coordinates)
    s4_cards = [
        el
        for el in sm4.elements
        if isinstance(el, lm.ShapeEl)
        and el.name.startswith("Card ")
        and not el.name.startswith(("Card Top Accent", "Card Tone Accent"))
    ]
    assert len(s4_cards) == 4
    assert len({c.y for c in s4_cards}) == 2
    assert "ZERO DATA LEAKAGE CERTIFIED" in _slide_all_text(sm4)

    # Slide 5: first 4 service step cards should be 1x4 horizontal row (same y coordinate)
    s5_cards = [
        el
        for el in sm5.elements
        if isinstance(el, lm.ShapeEl)
        and el.name.startswith("Card ")
        and not el.name.startswith(("Card Top Accent", "Card Tone Accent"))
    ]
    assert len(s5_cards) == 6
    assert len({c.y for c in s5_cards[:4]}) == 1
    txt5 = _slide_all_text(sm5)
    assert "▸ 60% Faster HIL Validation" in txt5
    assert "Phase 1: 4-Week Rapid Proof of Concept (PoC)" in txt5
    assert "Schedule Technical Briefing" in txt5

    _assert_zero_text_overflow(sm4, brand.slide_size.height)
    _assert_zero_text_overflow(sm5, brand.slide_size.height)

    # Verify full PPTX rendering succeeds and preserves all shapes
    dm = lm.DeckModel(width=brand.slide_size.width, height=brand.slide_size.height, slides=[sm4, sm5])
    pptx_bytes = write_pptx(dm)
    prs = Presentation(io.BytesIO(pptx_bytes))
    assert len(prs.slides) == 2
