"""Unit tests for multi-paragraph textfit, dynamic Slide Header geometry, and height-budgeted overflow."""
from __future__ import annotations

from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.brand.model import BrandProfile
from htmlppt.layout import model as lm
from htmlppt.layout.grid import SlideGeometry
from htmlppt.layout.overflow import _estimate_block_min_height_in, handle_overflow
from htmlppt.layout.textfit import (
    estimate_para_height_emu,
    estimate_paras_height_emu,
    scale_paras_to_fit,
)


def test_estimate_para_and_paras_height_emu():
    width_emu = int(3.0 * EMU_PER_INCH)
    short_para = lm.Para(
        runs=[lm.TRun(text="Short title", font="Calibri", size=14.0, color="111827", bold=True)],
        space_before=2.0,
        space_after=4.0,
    )
    h_short = estimate_para_height_emu(short_para, width_emu)
    expected_min = int((14.0 * 1.18 + 6.0) * EMU_PER_PT)
    assert h_short >= expected_min

    # Long text that wraps across multiple lines
    long_text = " ".join(["Validation"] * 30)
    long_para = lm.Para(
        runs=[lm.TRun(text=long_text, font="Calibri", size=14.0, color="111827")],
        space_after=4.0,
    )
    h_long = estimate_para_height_emu(long_para, width_emu)
    assert h_long > h_short * 2

    # Bullet indent and nested level reduce available width and can increase wrapped line count
    bullet_para = lm.Para(
        runs=[lm.TRun(text=long_text, font="Calibri", size=14.0, color="111827")],
        bullet="•",
        level=2,
        space_after=4.0,
    )
    h_bullet = estimate_para_height_emu(bullet_para, width_emu)
    assert h_bullet >= h_long

    total_h = estimate_paras_height_emu([short_para, long_para], width_emu)
    assert total_h == h_short + h_long


def test_scale_paras_to_fit_returns_unchanged_when_already_fitting():
    width_emu = int(4.0 * EMU_PER_INCH)
    height_emu = int(2.0 * EMU_PER_INCH)
    paras = [
        lm.Para(
            runs=[lm.TRun(text="Fits easily", font="Calibri", size=15.0, color="111827", bold=True)],
            space_after=4.0,
        ),
        lm.Para(
            runs=[lm.TRun(text="Concise body copy inside card.", font="Calibri", size=12.0, color="374151")],
        ),
    ]
    result = scale_paras_to_fit(paras, width_emu, height_emu)
    assert result is paras
    assert result[0].runs[0].size == 15.0


def test_scale_paras_to_fit_shrinks_overflowing_paras_within_bounds():
    width_emu = int(2.6 * EMU_PER_INCH)
    max_height_emu = int(1.15 * EMU_PER_INCH)
    paras = [
        lm.Para(
            runs=[lm.TRun(text="• PILLAR 1", font="Calibri", size=8.2, color="2563EB", bold=True)],
            space_after=4.0,
        ),
        lm.Para(
            runs=[
                lm.TRun(
                    text="Automated Requirements Traceability & Silo Elimination",
                    font="Calibri",
                    size=16.0,
                    color="111827",
                    bold=True,
                )
            ],
            space_after=6.0,
        ),
        lm.Para(
            runs=[
                lm.TRun(
                    text=(
                        "Parses natural-language system specs in DOORS/Polarion to flag ambiguities "
                        "and auto-generate linked Simulink/C test harnesses across all ECU builds."
                    ),
                    font="Calibri",
                    size=13.0,
                    color="374151",
                )
            ],
            space_after=4.0,
        ),
    ]

    before_h = estimate_paras_height_emu(paras, width_emu, default_line_spacing=1.15)
    assert before_h > max_height_emu

    scaled = scale_paras_to_fit(paras, width_emu, max_height_emu, min_size_pt=8.5)
    after_h = estimate_paras_height_emu(scaled, width_emu, default_line_spacing=1.15)
    assert after_h <= max_height_emu
    # Badge run initially at 8.2pt (< 8.5pt) stays >= 8.0pt
    assert scaled[0].runs[0].size >= 8.0
    # Title and body runs scale down but stay >= min_size_pt (8.5pt)
    assert 8.5 <= scaled[1].runs[0].size < 16.0
    assert 8.5 <= scaled[2].runs[0].size < 13.0
    assert scaled[1].space_after < paras[1].space_after


def test_dynamic_slide_geometry_compact_vs_dense_header():
    brand = BrandProfile()
    sh = brand.slide_size.height

    # 1. Backward-compatible static call without slide
    geom_static = SlideGeometry.from_brand(brand, has_title=True)
    assert geom_static.title_rect.h == int(sh * 0.16)

    # 2. Compact 1-line title with no kicker and no subtitle gets smaller header & taller content_rect
    compact_slide = ir.Slide(title="Executive Summary")
    geom_compact = SlideGeometry.from_brand(brand, has_title=True, slide=compact_slide)
    assert geom_compact.title_rect.h == int(sh * 0.11)
    assert geom_compact.content_rect.h > geom_static.content_rect.h

    # 3. Rich Slide Header with kicker + long 2-line title + 2-line subtitle expands up to 24% of sh
    rich_slide = ir.Slide(
        kicker="ASPICE & ISO 26262 COMPLIANCE",
        title="Transforming Automotive Motor Control ECU Development Across the Full V-Cycle Architecture",
        subtitle=(
            "Bridging systems engineering, embedded C2000/AURIX code generation, and automated HIL "
            "validation while preserving strict deterministic safety boundaries."
        ),
    )
    geom_rich = SlideGeometry.from_brand(brand, has_title=True, slide=rich_slide)
    assert geom_rich.title_rect.h > geom_static.title_rect.h
    assert geom_rich.title_rect.h <= int(sh * 0.24)
    assert geom_rich.content_rect.y == geom_rich.title_rect.bottom + int(brand.design.gutter * 0.45)


def test_overflow_retains_compound_slide_2_and_slide_5():
    brand = BrandProfile()

    # Slide 2 archetype: CardGrid of 3 cards (~1.65in) + 5-row Table (~1.90in) = ~3.75in <= 5.35in
    cards_3 = ir.CardGrid(
        cards=[
            ir.Card(title=f"Pillar {i}", body=[ir.Run(t="Key pillar description")])
            for i in range(1, 4)
        ]
    )
    table_5 = ir.Table(
        rows=[
            [ir.Cell(runs=[ir.Run(t=f"R{r}C{c}")], header=(r == 0)) for c in range(4)]
            for r in range(5)
        ]
    )
    slide_2 = ir.Slide(title="Where Engineering Velocity Breaks Down", blocks=[cards_3, table_5])
    assert _estimate_block_min_height_in(cards_3) == 1.65
    assert abs(_estimate_block_min_height_in(table_5) - 1.90) < 1e-6

    parts_2 = handle_overflow(slide_2, brand)
    assert len(parts_2) == 1
    assert len(parts_2[0].blocks) == 2

    # Slide 5 archetype: CardGrid(4 cards) + CardGrid(2 cards) + Callout = ~4.05in <= 5.35in
    cards_4 = ir.CardGrid(cards=[ir.Card(title=f"Step {i}") for i in range(1, 5)])
    cards_2 = ir.CardGrid(cards=[ir.Card(title=f"Phase {i}") for i in range(1, 3)])
    cta_callout = ir.Callout(title="Ready to Accelerate Your Next ECU Program?", runs=[ir.Run(t="Schedule PoC")])
    slide_5 = ir.Slide(title="90-Day Pilot Roadmap", blocks=[cards_4, cards_2, cta_callout])

    parts_5 = handle_overflow(slide_5, brand)
    assert len(parts_5) == 1
    assert len(parts_5[0].blocks) == 3

    # Slide 3 archetype: 3-column V-Cycle band of 7 Callouts + 1 full-width bottom Callout
    v_callouts = [
        ir.Callout(title="Left 1", runs=[ir.Run(t="Spec")], column=0, row_group=1),
        ir.Callout(title="Left 2", runs=[ir.Run(t="Arch")], column=0, row_group=1),
        ir.Callout(title="Left 3", runs=[ir.Run(t="Model")], column=0, row_group=1),
        ir.Callout(title="Center", runs=[ir.Run(t="Target")], column=1, row_group=1),
        ir.Callout(title="Right 1", runs=[ir.Run(t="SIL")], column=2, row_group=1),
        ir.Callout(title="Right 2", runs=[ir.Run(t="HIL")], column=2, row_group=1),
        ir.Callout(title="Right 3", runs=[ir.Run(t="Cal")], column=2, row_group=1),
        ir.Callout(title="Air-Gap", runs=[ir.Run(t="Zero AI in runtime")], column=None),
    ]
    slide_3 = ir.Slide(title="AI-Augmented V-Cycle", blocks=v_callouts)
    parts_3 = handle_overflow(slide_3, brand)
    assert len(parts_3) == 1
    assert len(parts_3[0].blocks) == 8


def test_overflow_splits_genuinely_overcrowded_slides():
    brand = BrandProfile()

    # 1. Two 10-row tables (3.8in + 0.2in + 3.8in = 7.8in > 5.35in) must split
    t1 = ir.Table(rows=[[ir.Cell(runs=[ir.Run(t=f"A{r}")])] for r in range(10)])
    t2 = ir.Table(rows=[[ir.Cell(runs=[ir.Run(t=f"B{r}")])] for r in range(10)])
    overcrowded_tables = ir.Slide(title="Large Data Tables", blocks=[t1, t2])
    parts_tables = handle_overflow(overcrowded_tables, brand)
    assert len(parts_tables) == 2
    assert parts_tables[0].title == "Large Data Tables"
    assert parts_tables[0].continued is False
    assert parts_tables[1].title == "Large Data Tables (cont.)"
    assert parts_tables[1].continued is True
    assert parts_tables[1].id != parts_tables[0].id

    # 2. Two charts must split onto separate slides
    c1 = ir.Chart(spec=ir.ChartSpec(type="bar", categories=["Q1", "Q2"], series=[ir.Series(name="S1", values=[1, 2])]))
    c2 = ir.Chart(spec=ir.ChartSpec(type="line", categories=["Q1", "Q2"], series=[ir.Series(name="S2", values=[3, 4])]))
    two_charts = ir.Slide(title="Dual Charts", blocks=[c1, c2])
    parts_charts = handle_overflow(two_charts, brand)
    assert len(parts_charts) == 2

    # 3. Three 6-card grids (2.8in each -> 8.8in total) must split into 3 slides
    g1 = ir.CardGrid(cards=[ir.Card(title=f"G1-{i}") for i in range(6)])
    g2 = ir.CardGrid(cards=[ir.Card(title=f"G2-{i}") for i in range(6)])
    g3 = ir.CardGrid(cards=[ir.Card(title=f"G3-{i}") for i in range(6)])
    three_grids = ir.Slide(title="Dense Card Grids", blocks=[g1, g2, g3])
    parts_grids = handle_overflow(three_grids, brand)
    assert len(parts_grids) == 3

    # 4. Single BulletList exceeding max_bullets (6) splits into chunks
    bullets = ir.BulletList(items=[ir.ListItem(runs=[ir.Run(t=f"Item {i}")]) for i in range(10)])
    bullet_slide = ir.Slide(title="Long Bullet List", blocks=[bullets])
    parts_bullets = handle_overflow(bullet_slide, brand)
    assert len(parts_bullets) == 2
    assert len(parts_bullets[0].blocks[0].items) == 6
    assert len(parts_bullets[1].blocks[0].items) == 4
