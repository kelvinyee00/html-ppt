"""Unit, golden, and regression tests for the Direct DOM Mapper (ADR-0003)."""
from __future__ import annotations

from pathlib import Path
import pytest
from pptx import Presentation

from htmlppt.analyze import ir
from htmlppt.analyze.segmenter import build_deck
from htmlppt.brand.presets import PALETTES, apply_preset, palette_brand
from htmlppt.evaluate import evaluate_deck
from htmlppt.extract.renderer import snapshot_html
from htmlppt.layout import model as lm
from htmlppt.layout.direct_mapper import DomNode, ToneMapper, layout_slide_from_dom
from htmlppt.layout.engine import layout_deck, layout_slide
from htmlppt.layout.overflow import handle_overflow
from htmlppt.render.pptx_writer import write_pptx


CORPUS_HTML = Path(__file__).parent / "corpus" / "ai_automotive_motor_ecu_v_cycle.html"


@pytest.fixture(scope="module")
def corpus_snap() -> dict:
    return snapshot_html(path=CORPUS_HTML)


def _sample_dom_slide_node(height_px: float = 720.0) -> dict:
    return {
        "id": 1,
        "tag": "section",
        "cls": "slide",
        "box": {"x": 0.0, "y": 0.0, "w": 1280.0, "h": height_px},
        "kids": [
            {
                "id": 2,
                "tag": "div",
                "cls": "eyebrow",
                "box": {"x": 64.0, "y": 48.0, "w": 320.0, "h": 20.0},
                "fs": 12.0,
                "fw": 700,
                "c": "38BDF8",
                "runs": [{"t": "ARCHITECTURE OVERVIEW", "fs": 12.0, "b": 1, "c": "38BDF8", "up": 1}],
                "dot": {"box": {"x": 48.0, "y": 53.0, "w": 8.0, "h": 8.0}, "c": "38BDF8", "radius": 4.0},
            },
            {
                "id": 3,
                "tag": "h1",
                "cls": "heading",
                "box": {"x": 48.0, "y": 76.0, "w": 900.0, "h": 44.0},
                "fs": 34.0,
                "fw": 700,
                "c": "F8FAFC",
                "runs": [{"t": "Platform Reliability & Telemetry", "fs": 34.0, "b": 1, "c": "F8FAFC"}],
            },
            {
                "id": 4,
                "tag": "div",
                "cls": "grid-card",
                "box": {"x": 48.0, "y": 150.0, "w": 560.0, "h": 220.0},
                "v": {
                    "bg": "1E293B",
                    "borders": [
                        {"w": 1.0, "c": "334155", "dash": False},
                        {"w": 1.0, "c": "334155", "dash": False},
                        {"w": 1.0, "c": "334155", "dash": False},
                        {"w": 4.0, "c": "10B981", "dash": False},
                    ],
                    "radius": 10.0,
                },
                "kids": [
                    {
                        "id": 5,
                        "tag": "h3",
                        "box": {"x": 68.0, "y": 170.0, "w": 500.0, "h": 26.0},
                        "fs": 18.0,
                        "fw": 700,
                        "c": "F8FAFC",
                        "runs": [{"t": "Deterministic Fault Recovery", "fs": 18.0, "b": 1, "c": "F8FAFC"}],
                    },
                    {
                        "id": 6,
                        "tag": "p",
                        "box": {"x": 68.0, "y": 206.0, "w": 500.0, "h": 60.0},
                        "fs": 14.0,
                        "fw": 400,
                        "c": "94A3B8",
                        "runs": [
                            {
                                "t": "Sub-millisecond watchdog isolation across redundant microcontroller cores.",
                                "fs": 14.0,
                                "c": "94A3B8",
                            }
                        ],
                    },
                ],
            },
        ],
    }


def test_dispatch_modes_and_ir_edit_fallback() -> None:
    """Verify dispatch between Direct DOM Mapper and Archetype handlers across overrides, continuations, layout_mode, and IR edits."""
    brand = apply_preset(palette_brand("ocean"), "corporate", dark=True)
    dom_node = _sample_dom_slide_node()

    blocks = [
        ir.CardGrid(
            cards=[
                ir.Card(title="Card A", body=[ir.Run(t="Alpha")]),
                ir.Card(title="Card B", body=[ir.Run(t="Beta")]),
            ]
        )
    ]
    slide = ir.Slide(
        title="Platform Reliability & Telemetry",
        initial_title="Platform Reliability & Telemetry",
        blocks=blocks,
        initial_blocks_sig=ir.blocks_signature(blocks),
        dom_node=dom_node,
    )

    # 1. Default (dom_node + layout_mode="dom" + no override) -> Direct DOM Mapper
    sm_dom = layout_slide(slide, brand, slide_num=1)
    assert any(el.name == "Kicker Dot" for el in sm_dom.elements)
    assert sm_dom.show_footer is True
    assert any(el.name == "Slide Number" for el in sm_dom.elements)

    # 2. Explicit archetype override (slide.layout = "cards") -> Archetype handler
    slide_override = slide.model_copy(deep=True)
    slide_override.layout = "cards"
    sm_override = layout_slide(slide_override, brand, slide_num=1)
    assert not any(el.name == "Kicker Dot" for el in sm_override.elements)

    # 3. Continuation slide (slide.continued = True) -> Archetype handler
    slide_cont = slide.model_copy(deep=True)
    slide_cont.continued = True
    sm_cont = layout_slide(slide_cont, brand, slide_num=2)
    assert not any(el.name == "Kicker Dot" for el in sm_cont.elements)

    # 4. Feature flag brand.design.layout_mode = "archetype" -> Archetype handler
    brand_arch = brand.model_copy(deep=True)
    brand_arch.design.layout_mode = "archetype"
    sm_arch = layout_slide(slide, brand_arch, slide_num=1)
    assert not any(el.name == "Kicker Dot" for el in sm_arch.elements)

    # 5. In-place slide.title edit updates the DOM-mapped title text without losing DOM geometry
    slide_renamed = slide.model_copy(deep=True)
    slide_renamed.title = "Updated Executive Title"
    sm_renamed = layout_slide(slide_renamed, brand, slide_num=1)
    title_el = next(el for el in sm_renamed.elements if isinstance(el, lm.TextEl) and el.name == "Slide Title")
    assert "Updated Executive Title" in "".join(r.text for p in title_el.paras for r in p.runs)

    # 6. Structural block mutation falls back to Archetype layout so block edits are never ignored
    slide_mutated = slide.model_copy(deep=True)
    slide_mutated.blocks.append(ir.Paragraph(runs=[ir.Run(t="Added paragraph block")]))
    sm_mutated = layout_slide(slide_mutated, brand, slide_num=1)
    assert not any(el.name == "Kicker Dot" for el in sm_mutated.elements)

    # 7. JSON serialization round-trip preserves dom_node
    roundtripped = ir.Slide.model_validate_json(slide.model_dump_json())
    assert roundtripped.dom_node is not None
    sm_rt = layout_slide(roundtripped, brand, slide_num=1)
    assert any(el.name == "Kicker Dot" for el in sm_rt.elements)


def test_overflow_retains_fitting_dom_slide_and_splits_tall_dom_slide() -> None:
    """Verify handle_overflow preserves 720px DOM slides and splits >720px overflowing DOM slides."""
    brand = apply_preset(palette_brand("ocean"), "corporate", dark=True)
    many_blocks = [
        ir.Callout(title=f"Zone {i}", runs=[ir.Run(t="Content line " * 8)])
        for i in range(8)
    ]

    # Fitting 1280x720 DOM slide is kept intact in DOM mode
    fitting_slide = ir.Slide(
        title="Dense But Fitting Browser Slide",
        blocks=many_blocks,
        dom_node=_sample_dom_slide_node(height_px=720.0),
    )
    parts_fit = handle_overflow(fitting_slide, brand)
    assert len(parts_fit) == 1
    assert parts_fit[0].dom_node is not None

    # Tall (>810px) overflowing DOM slide clears dom_node and splits into continuation slides
    tall_slide = ir.Slide(
        title="Tall Overflowing Browser Slide",
        blocks=many_blocks,
        dom_node=_sample_dom_slide_node(height_px=1080.0),
    )
    parts_tall = handle_overflow(tall_slide, brand)
    assert len(parts_tall) >= 2
    assert all(p.dom_node is None for p in parts_tall)
    assert parts_tall[1].continued is True


def test_zero_drop_polar_area_chart_unbacked_media_and_single_borders() -> None:
    """Verify normalize_chart handles polarArea charts and unbacked img/svg/borders never drop silently."""
    brand = apply_preset(palette_brand("ocean"), "corporate", dark=True)
    dom_node = {
        "id": 10,
        "tag": "section",
        "cls": "slide",
        "box": {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0},
        "kids": [
            {
                "id": 11,
                "tag": "chart",
                "chart": 0,
                "box": {"x": 60.0, "y": 100.0, "w": 360.0, "h": 240.0},
            },
            {
                "id": 12,
                "tag": "img",
                "alt": "Architecture Diagram",
                "box": {"x": 450.0, "y": 100.0, "w": 200.0, "h": 140.0},
            },
            {
                "id": 13,
                "tag": "svg",
                "box": {"x": 680.0, "y": 100.0, "w": 64.0, "h": 64.0},
            },
            {
                "id": 14,
                "tag": "div",
                "box": {"x": 60.0, "y": 400.0, "w": 300.0, "h": 50.0},
                "v": {"borders": [None, {"w": 3.0, "c": "10B981", "dash": False}, None, None]},
                "runs": [{"t": "Right-bordered callout note", "fs": 14.0, "c": "F8FAFC"}],
            },
            {
                "id": 15,
                "tag": "div",
                "box": {"x": 400.0, "y": 400.0, "w": 300.0, "h": 50.0},
                "v": {"borders": [None, None, {"w": 1.0, "c": "334155", "dash": False}, None]},
                "runs": [{"t": "Bottom-underlined header note", "fs": 14.0, "c": "F8FAFC"}],
            },
        ],
    }
    charts = [
        {
            "index": 0,
            "spec": {
                "type": "polarArea",
                "title": "Sensor Coverage",
                "series": [{"name": "Coverage", "values": [85, 92, 78]}],
            },
        }
    ]
    slide = ir.Slide(title="Zero Drop Verification", dom_node=dom_node, charts=charts)
    sm = layout_slide_from_dom(slide, brand, slide_num=1)

    chart_els = [el for el in sm.elements if isinstance(el, lm.ChartEl)]
    assert len(chart_els) == 1
    assert chart_els[0].spec.type == "bar"
    assert chart_els[0].spec.categories == ["1", "2", "3"]

    names = [el.name for el in sm.elements]
    assert any("Image Placeholder" in n for n in names)
    assert any("Vector Placeholder" in n for n in names)
    assert any(n == "Card Tone Accent" for n in names)
    assert any(n == "Divider Line" for n in names)
    assert len(slide.warnings) == 2  # unresolved img + empty svg


@pytest.mark.parametrize("palette_id", list(PALETTES.keys()))
@pytest.mark.parametrize("dark", [True, False])
def test_tone_mapper_across_all_palettes_and_modes(palette_id: str, dark: bool) -> None:
    """Verify ToneMapper resolves positive, negative, warning, primary, and muted tones with readable contrast."""
    brand = apply_preset(palette_brand(palette_id), "corporate", dark=dark)
    mapper = ToneMapper(brand)
    bg = brand.roles.surface

    for tone in ("positive", "negative", "warning", "primary", "muted"):
        col = mapper.resolve_tone_color(tone, bg)
        assert len(col) == 6

    # Muted text luminance mapping
    muted_col = mapper.map_text_color("64748B", "", 400, bg, is_heading=False)
    heading_col = mapper.map_text_color("F8FAFC", "", 700, bg, is_heading=True)
    assert muted_col != heading_col


def test_golden_corpus_dom_mapper_and_dom_box_iou_discrimination(corpus_snap: dict) -> None:
    """Golden test on corpus snapshot + verify dom_box_iou discriminates Direct DOM Mapper from Archetype mode."""
    deck_dom = build_deck(corpus_snap)
    brand_dom = apply_preset(palette_brand("ocean"), "corporate", dark=True)
    model_dom = layout_deck(deck_dom, brand_dom)
    sc_dom = evaluate_deck(corpus_snap, deck_dom, model_dom, deck_name="corpus_dom")

    deck_arch = build_deck(corpus_snap)
    brand_arch = brand_dom.model_copy(deep=True)
    brand_arch.design.layout_mode = "archetype"
    model_arch = layout_deck(deck_arch, brand_arch)
    sc_arch = evaluate_deck(corpus_snap, deck_arch, model_arch, deck_name="corpus_arch")

    # Direct DOM Mapper preserves exact browser container coordinates (IoU >= 0.95),
    # whereas heuristic Archetype re-layout shifts/drops containers (IoU < 0.65).
    assert sc_dom.dom_box_iou >= 0.95
    assert sc_arch.dom_box_iou < 0.65

    # Golden element invariants across the 5 corpus slides
    assert len(model_dom.slides) == 5
    # Slide 1: 4 KPI Cards + 2 bottom Cards
    s1_shapes = [el for el in model_dom.slides[0].elements if isinstance(el, lm.ShapeEl)]
    assert sum(1 for s in s1_shapes if s.name.startswith("KPI Card")) == 4
    # Slide 2: 3 Cards + 1 Table
    assert sum(1 for el in model_dom.slides[1].elements if isinstance(el, lm.TableEl)) == 1
    # Slide 5: Gradient CTA banner rendered as native gradient in PPTX
    s5_grads = [
        el for el in model_dom.slides[4].elements if isinstance(el, lm.ShapeEl) and el.gradient and len(el.gradient) >= 2
    ]
    assert len(s5_grads) == 1
    pptx_bytes = write_pptx(model_dom)
    prs = Presentation( Path(__file__).parent / "corpus" / "ai_automotive_motor_ecu_v_cycle.html" if False else __import__("io").BytesIO(pptx_bytes) )
    assert len(prs.slides) == 5
