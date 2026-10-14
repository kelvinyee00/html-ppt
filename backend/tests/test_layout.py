"""Unit tests for layout engine geometry, CardGrid wrapping, and bounded sizing."""
import pytest
from htmlppt.analyze import ir
from htmlppt.brand.presets import apply_preset, palette_brand
from htmlppt.layout.engine import _layout_cards_slide
from htmlppt.layout.grid import SlideGeometry
from htmlppt.layout import model as lm


def _create_mock_cards_slide(num_cards: int, variant: str = "cards") -> ir.Slide:
    cards = [
        ir.Card(
            title=f"Card {i + 1}",
            kicker=f"Step {i + 1}",
            body=[ir.Run(t=f"Body description for card {i + 1}")],
            bullets=[ir.ListItem(runs=[ir.Run(t=f"Bullet 1 for card {i + 1}")], level=0)],
        )
        for i in range(num_cards)
    ]
    card_grid = ir.CardGrid(cards=cards, variant=variant)
    return ir.Slide(index=1, title="Test Cards Slide", blocks=[card_grid])


@pytest.mark.parametrize("n_cards", [1, 2, 3, 4, 6])
def test_cardgrid_geometry_configurations(n_cards):
    brand = palette_brand("ocean")
    brand = apply_preset(brand, "corporate")
    geom = SlideGeometry.from_brand(brand, has_title=True)
    cr = geom.content_rect
    gutter = brand.design.gutter

    slide = _create_mock_cards_slide(n_cards)
    sm = lm.SlideModel(id=slide.id, archetype="cards")
    _layout_cards_slide(slide, brand, geom, sm)

    # Collect card outer shapes
    card_shapes = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name.startswith("Card Card")]
    assert len(card_shapes) == n_cards, f"Expected {n_cards} card shapes, got {len(card_shapes)}"

    if n_cards == 4:
        # 2x2 grid
        c0, c1, c2, c3 = card_shapes
        # Row 0
        assert c0.x == cr.x
        assert c0.y == cr.y
        assert c1.x == cr.x + c0.w + gutter
        assert c1.y == cr.y
        # Row 1
        assert c2.x == cr.x
        assert c2.y == cr.y + c0.h + gutter
        assert c3.x == cr.x + c2.w + gutter
        assert c3.y == cr.y + c0.h + gutter

        # Check bounds
        expected_w = (cr.w - gutter) // 2
        expected_h = (cr.h - gutter) // 2
        assert c0.w == expected_w
        assert c0.h == expected_h
        assert c0.h < cr.h  # Must NOT be squished full-height pillar

    elif n_cards == 6:
        # 2x3 grid
        expected_w = (cr.w - gutter * 2) // 3
        expected_h = (cr.h - gutter) // 2
        assert card_shapes[0].w == expected_w
        assert card_shapes[0].h == expected_h
        # First row
        assert card_shapes[0].y == cr.y
        assert card_shapes[1].y == cr.y
        assert card_shapes[2].y == cr.y
        # Second row
        assert card_shapes[3].y == cr.y + expected_h + gutter
        assert card_shapes[4].y == cr.y + expected_h + gutter
        assert card_shapes[5].y == cr.y + expected_h + gutter

    elif n_cards in (1, 2, 3):
        # 1-3 cards: 1 row, content-bounded height and vertically centered
        c0 = card_shapes[0]
        expected_w = (cr.w - gutter * (n_cards - 1)) // n_cards
        assert c0.w == expected_w
        assert c0.h < cr.h, f"1-3 cards height {c0.h} should be bounded, not full cr.h {cr.h}"
        # Vertically centered: top margin should equal bottom margin within content_rect
        top_space = c0.y - cr.y
        bottom_space = (cr.y + cr.h) - (c0.y + c0.h)
        assert abs(top_space - bottom_space) <= 1, f"Cards should be vertically centered: top {top_space} vs bottom {bottom_space}"


def test_cardgrid_timeline_process_accent_bar():
    brand = palette_brand("ocean")
    brand = apply_preset(brand, "corporate")
    geom = SlideGeometry.from_brand(brand, has_title=True)

    slide = _create_mock_cards_slide(3, variant="process")
    sm = lm.SlideModel(id=slide.id, archetype="cards")
    _layout_cards_slide(slide, brand, geom, sm)

    accent_bars = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name == "Card Top Accent"]
    assert len(accent_bars) == 3
    for bar in accent_bars:
        assert bar.fill == brand.roles.primary


def test_kpigroup_standalone_compact_and_centered():
    brand = palette_brand("ocean")
    brand = apply_preset(brand, "corporate")
    geom = SlideGeometry.from_brand(brand, has_title=True)
    cr = geom.content_rect

    kpis = [
        ir.KPI(value="$24.5M", label="Annual Recurring Revenue", delta="+34%", sentiment="positive"),
        ir.KPI(value="99.98%", label="Platform Uptime SLA", delta="+0.05%", sentiment="positive"),
        ir.KPI(value="142ms", label="P99 Response Latency", delta="-12ms", sentiment="positive"),
    ]
    slide = ir.Slide(index=1, title="Q3 Performance Metrics", blocks=[ir.KPIGroup(items=kpis)])
    sm = lm.SlideModel(id=slide.id, archetype="kpi_grid")

    from htmlppt.layout.engine import _layout_kpi_slide
    _layout_kpi_slide(slide, brand, geom, sm)

    cards = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name.startswith("KPI Card")]
    assert len(cards) == 3

    c0 = cards[0]
    # Height must be capped to compact budget (25% - 35% of cr.h)
    assert c0.h <= int(cr.h * 0.35), f"KPI height {c0.h} exceeded 35% of content height {cr.h}"
    assert c0.h >= int(cr.h * 0.20), f"KPI height {c0.h} is too small"

    # Vertically centered when standalone
    top_space = c0.y - cr.y
    bottom_space = (cr.y + cr.h) - (c0.y + c0.h)
    assert abs(top_space - bottom_space) <= 1, f"KPI card not vertically centered: top={top_space}, bottom={bottom_space}"

    # Verify prominent typography in text elements
    texts = [el for el in sm.elements if isinstance(el, lm.TextEl) and el.name.startswith("KPI Text")]
    assert len(texts) == 3
    t0 = texts[0]
    val_run = t0.paras[0].runs[0]
    label_run = t0.paras[1].runs[0]
    assert 36 <= val_run.size <= 48, f"KPI metric number size {val_run.size} not in 36-48pt range"
    assert 13 <= label_run.size <= 15, f"KPI label size {label_run.size} not in 13-15pt range"


def test_kpigroup_with_secondary_content_stacked():
    brand = palette_brand("ocean")
    brand = apply_preset(brand, "corporate")
    geom = SlideGeometry.from_brand(brand, has_title=True)
    cr = geom.content_rect
    gutter = brand.design.gutter

    kpis = [
        ir.KPI(value="4.8/5", label="Customer Satisfaction"),
        ir.KPI(value="92%", label="Feature Retention"),
    ]
    sec_para = ir.Paragraph(runs=[ir.Run(t="Key takeaway: Customer satisfaction reached an all-time high in Q3.")])
    slide = ir.Slide(index=1, title="Customer Highlights", blocks=[ir.KPIGroup(items=kpis), sec_para])
    sm = lm.SlideModel(id=slide.id, archetype="kpi_grid")

    from htmlppt.layout.engine import _layout_kpi_slide
    _layout_kpi_slide(slide, brand, geom, sm)

    cards = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name.startswith("KPI Card")]
    assert len(cards) == 2
    c0 = cards[0]
    # Starts at top of content area when secondary content is present
    assert c0.y == cr.y
    assert c0.h <= int(cr.h * 0.35)


def test_two_column_empty_column_fallback_to_hero():
    brand = palette_brand("ocean")
    brand = apply_preset(brand, "corporate")
    geom = SlideGeometry.from_brand(brand, has_title=True)
    cr = geom.content_rect

    # Explicit column 0 has blocks, column 1 is empty
    p1 = ir.Paragraph(runs=[ir.Run(t="This is content strictly assigned to column 0.")], column=0)
    p2 = ir.Paragraph(runs=[ir.Run(t="Another paragraph also in column 0.")], column=0)
    slide = ir.Slide(index=1, title="Single Sided Slide", blocks=[p1, p2])
    sm = lm.SlideModel(id=slide.id, archetype="two_column")

    from htmlppt.layout.engine import _layout_two_column_slide
    _layout_two_column_slide(slide, brand, geom, sm)

    # Must collapse to Hero Container rather than two column containers
    col_containers = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name.startswith("Column Container")]
    assert len(col_containers) == 0, "Should not render two column containers when one column is empty"

    hero_containers = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name == "Hero Container"]
    assert len(hero_containers) == 1, "Should render a single centered Hero Container"
    assert hero_containers[0].w >= int(cr.w * 0.90)


def test_two_column_sparse_column_fallback_to_hero():
    brand = palette_brand("ocean")
    brand = apply_preset(brand, "corporate")
    geom = SlideGeometry.from_brand(brand, has_title=True)

    # Column 0 has a tiny 10-char line; Column 1 has 300+ chars (< 10% ratio)
    p_sparse = ir.Paragraph(runs=[ir.Run(t="Tiny note.")], column=0)
    p_heavy = ir.Paragraph(runs=[ir.Run(t="Extensive architectural documentation explaining in deep detail the full system flow, hardware constraints, communication protocols, memory allocation, and error recovery strategies across all vehicle sub-systems.")], column=1)
    slide = ir.Slide(index=1, title="Sparse Column Slide", blocks=[p_sparse, p_heavy])
    sm = lm.SlideModel(id=slide.id, archetype="two_column")

    from htmlppt.layout.engine import _layout_two_column_slide
    _layout_two_column_slide(slide, brand, geom, sm)

    col_containers = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name.startswith("Column Container")]
    assert len(col_containers) == 0, "Should collapse sparse column (<15% content) to hero layout"

    hero_containers = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name == "Hero Container"]
    assert len(hero_containers) == 1


def test_two_column_balanced_distribution():
    brand = palette_brand("ocean")
    brand = apply_preset(brand, "corporate")
    geom = SlideGeometry.from_brand(brand, has_title=True)
    cr = geom.content_rect
    gutter = brand.design.gutter

    # 4 balanced paragraphs without explicit columns
    paras = [
        ir.Paragraph(runs=[ir.Run(t=f"Balanced paragraph {i}: detailed topic covering system component {i} in depth.")])
        for i in range(4)
    ]
    slide = ir.Slide(index=1, title="Balanced Two Column", blocks=paras)
    sm = lm.SlideModel(id=slide.id, archetype="two_column")

    from htmlppt.layout.engine import _layout_two_column_slide
    _layout_two_column_slide(slide, brand, geom, sm)

    col_containers = [el for el in sm.elements if isinstance(el, lm.ShapeEl) and el.name.startswith("Column Container")]
    assert len(col_containers) == 2, "Should render 2 column containers for genuine balanced columns"
    c1, c2 = col_containers
    assert c1.x == cr.x
    assert c2.x == cr.x + c1.w + gutter
    assert c1.w == c2.w


