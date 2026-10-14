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
