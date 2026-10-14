"""Layout engine: transforms Semantic IR Deck -> Positioned DeckModel.

Applies brand styling, dimensions, font hierarchy, colors, spacing and
grid math to construct EMU-positioned slide models.
"""
from __future__ import annotations

import math
from typing import Sequence

from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.brand.model import BrandProfile, hex_to_rgb
from htmlppt.layout import model as lm
from htmlppt.layout.archetypes import select_archetype
from htmlppt.layout.grid import Rect, SlideGeometry
from htmlppt.layout.overflow import handle_overflow
from htmlppt.layout.textfit import estimate_text_height_emu, fit_font_size


def layout_deck(deck: ir.Deck, brand: BrandProfile) -> lm.DeckModel:
    """Transform an IR Deck into a fully positioned DeckModel."""
    deck_model = lm.DeckModel(
        width=brand.slide_size.width,
        height=brand.slide_size.height,
        slides=[],
        fonts=[brand.fonts.heading.family, brand.fonts.body.family],
    )

    # Process each slide with overflow checks
    raw_slides: list[ir.Slide] = []
    for s in deck.slides:
        raw_slides.extend(handle_overflow(s, brand))

    for idx, slide in enumerate(raw_slides):
        slide_model = layout_slide(slide, brand, slide_num=idx + 1)
        deck_model.slides.append(slide_model)

    return deck_model


def layout_slide(slide: ir.Slide, brand: BrandProfile, slide_num: int) -> lm.SlideModel:
    archetype = select_archetype(slide)
    geom = SlideGeometry.from_brand(brand, has_title=bool(slide.title) and archetype not in ("title", "section", "closing"))

    slide_model = lm.SlideModel(
        id=slide.id,
        archetype=archetype,
        template_layout=_map_template_layout(archetype),
        background=brand.roles.background if not brand.design.dark else brand.colors.dk2,
        elements=[],
        notes=slide.notes,
        show_footer=archetype not in ("title", "section", "closing"),
        number=slide_num,
    )

    # Dispatch to archetype layout handler
    if archetype == "title":
        _layout_title_slide(slide, brand, geom, slide_model)
    elif archetype == "section":
        _layout_section_slide(slide, brand, geom, slide_model)
    elif archetype == "closing":
        _layout_closing_slide(slide, brand, geom, slide_model)
    elif archetype == "kpi_grid":
        _layout_kpi_slide(slide, brand, geom, slide_model)
    elif archetype == "chart_focus":
        _layout_chart_focus_slide(slide, brand, geom, slide_model)
    elif archetype == "chart_plus_text":
        _layout_chart_plus_text_slide(slide, brand, geom, slide_model)
    elif archetype == "two_column":
        _layout_two_column_slide(slide, brand, geom, slide_model)
    elif archetype == "table":
        _layout_table_slide(slide, brand, geom, slide_model)
    elif archetype == "cards":
        _layout_cards_slide(slide, brand, geom, slide_model)
    elif archetype == "quote":
        _layout_quote_slide(slide, brand, geom, slide_model)
    elif archetype == "freeform":
        _layout_freeform_slide(slide, brand, geom, slide_model)
    else:
        _layout_content_slide(slide, brand, geom, slide_model)

    # Add header (title & subtitle & accent treatment) if not title/section/closing
    if archetype not in ("title", "section", "closing") and slide.title:
        _add_slide_header(slide, brand, geom, slide_model)

    # Add footer decorations
    if slide_model.show_footer:
        _add_footer(brand, geom, slide_model)

    return slide_model


def _map_template_layout(archetype: str) -> str:
    mapping = {
        "title": "title",
        "section": "section",
        "closing": "title_only",
        "two_column": "two_content",
        "blank": "blank",
    }
    return mapping.get(archetype, "content")


# ---------------------------------------------------------------- Slide Header & Footer

def _add_slide_header(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    tr = geom.title_rect
    paras: list[lm.Para] = []

    # Title run
    title_size = brand.fonts.sizes.title
    title_text = slide.title.upper() if brand.design.title_case == "upper" else slide.title
    paras.append(
        lm.Para(
            runs=[
                lm.TRun(
                    text=title_text,
                    font=brand.fonts.heading.family,
                    size=title_size,
                    color=brand.roles.text,
                    bold=True,
                )
            ],
            align="center" if brand.design.title_align == "center" else "left",
        )
    )

    # Subtitle run
    if slide.subtitle:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=slide.subtitle,
                        font=brand.fonts.body.family,
                        size=brand.fonts.sizes.subtitle,
                        color=brand.roles.muted,
                        italic=False,
                    )
                ],
                space_before=4,
                align="center" if brand.design.title_align == "center" else "left",
            )
        )

    sm.elements.append(
        lm.TextEl(
            name="Slide Title",
            x=tr.x,
            y=tr.y,
            w=tr.w,
            h=tr.h,
            paras=paras,
            placeholder="title",
            valign="middle" if not slide.subtitle else "top",
        )
    )

    # Accent treatment
    treatment = brand.design.title_treatment
    if treatment == "accent-bar":
        # Small accent bar to the left of the title or below
        bar_w = int(2.5 * EMU_PER_INCH)
        bar_h = int(3 * EMU_PER_PT)
        bar_y = tr.bottom - int(2 * EMU_PER_PT)
        sm.elements.append(
            lm.ShapeEl(
                name="Title Accent Bar",
                x=tr.x,
                y=bar_y,
                w=bar_w,
                h=bar_h,
                geom="rect",
                fill=brand.roles.primary,
                line=None,
            )
        )
    elif treatment == "underline":
        # Full width thin divider
        sm.elements.append(
            lm.ShapeEl(
                name="Title Underline",
                x=tr.x,
                y=tr.bottom,
                w=tr.w,
                h=int(1 * EMU_PER_PT),
                geom="rect",
                fill=brand.roles.border,
                line=None,
            )
        )


def _add_footer(brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    footer = brand.footer
    y = geom.height - geom.margin_bottom // 2
    h = int(0.3 * EMU_PER_INCH)
    
    # Left / Center text: Confidentiality or footer text
    items = []
    if footer.confidentiality:
        items.append(footer.confidentiality)
    if footer.text:
        items.append(footer.text)
    
    if items:
        text = "  |  ".join(items)
        sm.elements.append(
            lm.TextEl(
                name="Footer Text",
                x=geom.margin_x,
                y=y,
                w=int(geom.width * 0.7),
                h=h,
                paras=[
                    lm.Para(
                        runs=[
                            lm.TRun(
                                text=text,
                                font=brand.fonts.body.family,
                                size=brand.fonts.sizes.caption,
                                color=brand.roles.muted,
                            )
                        ]
                    )
                ],
                valign="middle",
            )
        )

    # Right: Slide number
    if footer.slide_number:
        sm.elements.append(
            lm.TextEl(
                name="Slide Number",
                x=geom.width - geom.margin_x - int(1.5 * EMU_PER_INCH),
                y=y,
                w=int(1.5 * EMU_PER_INCH),
                h=h,
                paras=[
                    lm.Para(
                        runs=[
                            lm.TRun(
                                text=str(sm.number),
                                font=brand.fonts.body.family,
                                size=brand.fonts.sizes.caption,
                                color=brand.roles.muted,
                            )
                        ],
                        align="right",
                    )
                ],
                valign="middle",
            )
        )


# ---------------------------------------------------------------- Archetype Handlers

def _layout_title_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cx = geom.margin_x
    cw = geom.width - 2 * geom.margin_x
    cy = int(geom.height * 0.30)
    ch = int(geom.height * 0.45)

    paras: list[lm.Para] = [
        lm.Para(
            runs=[
                lm.TRun(
                    text=slide.title or "Presentation",
                    font=brand.fonts.heading.family,
                    size=brand.fonts.sizes.title * 1.35,
                    color=brand.roles.primary,
                    bold=True,
                )
            ],
            space_after=12,
        )
    ]

    if slide.subtitle:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=slide.subtitle,
                        font=brand.fonts.body.family,
                        size=brand.fonts.sizes.subtitle * 1.1,
                        color=brand.roles.text,
                    )
                ],
                space_after=8,
            )
        )

    for b in slide.blocks:
        if isinstance(b, ir.Paragraph):
            paras.append(
                lm.Para(
                    runs=[_ir_run_to_trun(r, brand, is_heading=False) for r in b.runs],
                    space_before=6,
                )
            )

    sm.elements.append(
        lm.TextEl(
            name="Title Content",
            x=cx,
            y=cy,
            w=cw,
            h=ch,
            paras=paras,
            placeholder="title",
            valign="middle",
        )
    )


def _layout_section_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cx = geom.margin_x
    cw = geom.width - 2 * geom.margin_x
    cy = int(geom.height * 0.35)
    ch = int(geom.height * 0.35)

    paras = [
        lm.Para(
            runs=[
                lm.TRun(
                    text=slide.title,
                    font=brand.fonts.heading.family,
                    size=brand.fonts.sizes.title * 1.2,
                    color=brand.roles.primary,
                    bold=True,
                )
            ],
            align="center" if brand.design.title_align == "center" else "left",
            space_after=8,
        )
    ]
    if slide.subtitle:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=slide.subtitle,
                        font=brand.fonts.body.family,
                        size=brand.fonts.sizes.subtitle,
                        color=brand.roles.muted,
                    )
                ],
                align="center" if brand.design.title_align == "center" else "left",
            )
        )

    sm.elements.append(
        lm.TextEl(
            name="Section Title",
            x=cx,
            y=cy,
            w=cw,
            h=ch,
            paras=paras,
            valign="middle",
        )
    )


def _layout_closing_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cx = geom.margin_x
    cw = geom.width - 2 * geom.margin_x
    cy = int(geom.height * 0.32)
    ch = int(geom.height * 0.40)

    paras = [
        lm.Para(
            runs=[
                lm.TRun(
                    text=slide.title or "Thank You",
                    font=brand.fonts.heading.family,
                    size=brand.fonts.sizes.title * 1.25,
                    color=brand.roles.primary,
                    bold=True,
                )
            ],
            align="center",
            space_after=12,
        )
    ]
    if slide.subtitle:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=slide.subtitle,
                        font=brand.fonts.body.family,
                        size=brand.fonts.sizes.subtitle,
                        color=brand.roles.muted,
                    )
                ],
                align="center",
            )
        )

    sm.elements.append(
        lm.TextEl(
            name="Closing Text",
            x=cx,
            y=cy,
            w=cw,
            h=ch,
            paras=paras,
            valign="middle",
        )
    )


def _layout_kpi_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    kpi_block = next((b for b in slide.blocks if isinstance(b, ir.KPIGroup)), None)
    other_blocks = [b for b in slide.blocks if b is not kpi_block]

    if not kpi_block or not kpi_block.items:
        _layout_content_slide(slide, brand, geom, sm)
        return

    n_kpis = len(kpi_block.items)
    gutter = brand.design.gutter

    # Bounded compact height budget: 25% - 35% of slide content height (~1.5 to 2.2 inches)
    kpi_height = int(cr.h * (0.28 if other_blocks else 0.32))

    # If no other blocks exist on the slide, vertically center the KPI card row within content rect
    if not other_blocks:
        kpi_y = cr.y + (cr.h - kpi_height) // 2
    else:
        kpi_y = cr.y

    # Calculate tile positions horizontally
    total_gutters = gutter * (n_kpis - 1)
    tile_w = (cr.w - total_gutters) // n_kpis

    for i, item in enumerate(kpi_block.items):
        tx = cr.x + i * (tile_w + gutter)
        _build_kpi_card(item, tx, kpi_y, tile_w, kpi_height, brand, sm)

    # Render any remaining blocks below the KPI tiles
    if other_blocks:
        rem_y = kpi_y + kpi_height + gutter
        rem_h = cr.bottom - rem_y
        rem_rect = Rect(x=cr.x, y=rem_y, w=cr.w, h=rem_h)
        _layout_block_stack(other_blocks, rem_rect, brand, sm)


def _build_kpi_card(
    kpi: ir.KPI,
    x: int,
    y: int,
    w: int,
    h: int,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    # Outer card shape with elevation and subtle rounded corners
    card_style = brand.design.card_style
    fill = brand.roles.surface if card_style in ("filled", "shadow") or brand.design.dark else None
    line = brand.roles.border if card_style in ("outline", "filled") or brand.design.dark else None
    line_w = brand.design.line_weight_pt if line else 0.0
    radius = brand.design.corner_radius if brand.design.corner_radius > 0 else 0.04

    sm.elements.append(
        lm.ShapeEl(
            name=f"KPI Card {kpi.value}",
            x=x,
            y=y,
            w=w,
            h=h,
            geom="roundRect" if radius > 0 else "rect",
            radius=radius,
            fill=fill,
            line=line,
            line_w=line_w,
            shadow=card_style == "shadow",
        )
    )

    padding = int(0.25 * EMU_PER_INCH)
    tx = x + padding
    ty = y + padding
    tw = w - 2 * padding
    th = h - 2 * padding

    # Metric number prominently displayed (36-48pt) in roles.primary
    metric_size = max(36.0, min(48.0, brand.fonts.sizes.kpi))
    # Clear descriptive label (13-15pt) in roles.muted
    label_size = max(13.0, min(15.0, brand.fonts.sizes.body))

    paras: list[lm.Para] = [
        # Big Value
        lm.Para(
            runs=[
                lm.TRun(
                    text=kpi.value,
                    font=brand.fonts.heading.family,
                    size=metric_size,
                    color=brand.roles.primary,
                    bold=True,
                )
            ],
            space_after=4,
        ),
        # Label
        lm.Para(
            runs=[
                lm.TRun(
                    text=kpi.label,
                    font=brand.fonts.body.family,
                    size=label_size,
                    color=brand.roles.muted,
                )
            ],
            space_after=4,
        ),
    ]

    # Delta
    if kpi.delta:
        delta_color = (
            brand.roles.positive
            if kpi.sentiment == "positive"
            else brand.roles.negative
            if kpi.sentiment == "negative"
            else brand.roles.muted
        )
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=kpi.delta,
                        font=brand.fonts.body.family,
                        size=brand.fonts.sizes.caption * 1.1,
                        color=delta_color,
                        bold=True,
                    )
                ]
            )
        )

    sm.elements.append(
        lm.TextEl(
            name=f"KPI Text {kpi.value}",
            x=tx,
            y=ty,
            w=tw,
            h=th,
            paras=paras,
            valign="middle",
        )
    )


def _layout_chart_focus_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    chart_block = next((b for b in slide.blocks if isinstance(b, ir.Chart)), None)
    if not chart_block:
        _layout_content_slide(slide, brand, geom, sm)
        return

    _build_chart_element(chart_block.spec, cr.x, cr.y, cr.w, cr.h, brand, sm)


def _layout_chart_plus_text_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cols = geom.split_columns(2)
    chart_block = next((b for b in slide.blocks if isinstance(b, ir.Chart)), None)
    text_blocks = [b for b in slide.blocks if b is not chart_block]

    if chart_block:
        _build_chart_element(chart_block.spec, cols[0].x, cols[0].y, cols[0].w, cols[0].h, brand, sm)
    if text_blocks:
        _layout_block_stack(text_blocks, cols[1], brand, sm)


def _layout_two_column_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cols = geom.split_columns(2)
    left_blocks = [b for b in slide.blocks if b.column == 0]
    right_blocks = [b for b in slide.blocks if b.column == 1]

    # If column indices were not populated, divide blocks equally
    if not left_blocks and not right_blocks:
        mid = math.ceil(len(slide.blocks) / 2)
        left_blocks = slide.blocks[:mid]
        right_blocks = slide.blocks[mid:]

    _layout_block_stack(left_blocks, cols[0], brand, sm)
    _layout_block_stack(right_blocks, cols[1], brand, sm)


def _layout_table_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    table_block = next((b for b in slide.blocks if isinstance(b, ir.Table)), None)
    if not table_block or not table_block.rows:
        _layout_content_slide(slide, brand, geom, sm)
        return

    _build_table_element(table_block, cr.x, cr.y, cr.w, cr.h, brand, sm)


def _layout_cards_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    cg = next((b for b in slide.blocks if isinstance(b, ir.CardGrid)), None)
    if not cg or not cg.cards:
        _layout_content_slide(slide, brand, geom, sm)
        return

    n_cards = len(cg.cards)
    gutter = brand.design.gutter

    if n_cards == 4:
        # 2x2 grid
        tile_w = (cr.w - gutter) // 2
        tile_h = (cr.h - gutter) // 2
        for i, card in enumerate(cg.cards):
            r = i // 2
            c = i % 2
            cx = cr.x + c * (tile_w + gutter)
            cy = cr.y + r * (tile_h + gutter)
            _build_card_element(card, cx, cy, tile_w, tile_h, cg.variant, brand, sm)
    elif n_cards in (5, 6):
        # 2x3 grid
        tile_w = (cr.w - gutter * 2) // 3
        tile_h = (cr.h - gutter) // 2
        for i, card in enumerate(cg.cards):
            r = i // 3
            c = i % 3
            cx = cr.x + c * (tile_w + gutter)
            cy = cr.y + r * (tile_h + gutter)
            _build_card_element(card, cx, cy, tile_w, tile_h, cg.variant, brand, sm)
    elif n_cards <= 3:
        # Single row with content-bounded height and balanced vertical centering
        tile_w = (cr.w - gutter * (n_cards - 1)) // n_cards
        pad = int(0.25 * EMU_PER_INCH)
        estimated_max_h = 0
        for c in cg.cards:
            h_est = 2 * pad
            if c.kicker:
                h_est += int(0.35 * EMU_PER_INCH)
            if c.title:
                h_est += int(0.45 * EMU_PER_INCH)
            if c.body:
                h_est += int(0.3 * EMU_PER_INCH) * max(1, len(c.body))
            if c.bullets:
                h_est += int(0.35 * EMU_PER_INCH) * len(c.bullets)
            if cg.variant in ("process", "timeline"):
                h_est += int(4 * EMU_PER_PT)
            estimated_max_h = max(estimated_max_h, h_est)

        content_budget = int(estimated_max_h * 1.25)
        tile_h = min(cr.h, max(int(cr.h * 0.45), min(int(cr.h * 0.85), content_budget)))
        cy = cr.y + (cr.h - tile_h) // 2

        for i, card in enumerate(cg.cards):
            cx = cr.x + i * (tile_w + gutter)
            _build_card_element(card, cx, cy, tile_w, tile_h, cg.variant, brand, sm)
    else:
        # 7+ cards wrap across 3 columns
        cols = 3
        rows = (n_cards + cols - 1) // cols
        tile_w = (cr.w - gutter * (cols - 1)) // cols
        tile_h = (cr.h - gutter * (rows - 1)) // rows
        for i, card in enumerate(cg.cards):
            r = i // cols
            c = i % cols
            cx = cr.x + c * (tile_w + gutter)
            cy = cr.y + r * (tile_h + gutter)
            _build_card_element(card, cx, cy, tile_w, tile_h, cg.variant, brand, sm)


def _build_card_element(
    card: ir.Card,
    x: int,
    y: int,
    w: int,
    h: int,
    variant: str,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    # Outer card shape with elevation and subtle rounded corners
    card_style = brand.design.card_style
    fill = brand.roles.surface if card_style in ("filled", "shadow") or brand.design.dark else None
    line = brand.roles.border if card_style in ("outline", "filled") or brand.design.dark else None
    radius = brand.design.corner_radius if brand.design.corner_radius > 0 else 0.04

    sm.elements.append(
        lm.ShapeEl(
            name=f"Card {card.title}",
            x=x,
            y=y,
            w=w,
            h=h,
            geom="roundRect" if radius > 0 else "rect",
            radius=radius,
            fill=fill,
            line=line,
            line_w=brand.design.line_weight_pt if line else 0.0,
        )
    )

    top_accent_h = int(4 * EMU_PER_PT) if variant in ("process", "timeline") else 0
    # Accent top border if timeline or process
    if top_accent_h > 0:
        sm.elements.append(
            lm.ShapeEl(
                name="Card Top Accent",
                x=x,
                y=y,
                w=w,
                h=top_accent_h,
                geom="rect",
                fill=brand.roles.primary,
                line=None,
            )
        )

    pad = int(0.25 * EMU_PER_INCH)
    paras: list[lm.Para] = []

    if card.kicker:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=card.kicker,
                        font=brand.fonts.body.family,
                        size=brand.fonts.sizes.caption * 1.1,
                        color=brand.roles.primary,
                        bold=True,
                    )
                ],
                space_after=4,
            )
        )

    if card.title:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=card.title,
                        font=brand.fonts.heading.family,
                        size=brand.fonts.sizes.heading,
                        color=brand.roles.text,
                        bold=True,
                    )
                ],
                space_after=6,
            )
        )

    if card.body:
        paras.append(
            lm.Para(
                runs=[_ir_run_to_trun(r, brand, is_heading=False) for r in card.body],
                space_after=4,
            )
        )

    for item in card.bullets:
        paras.append(
            lm.Para(
                runs=[_ir_run_to_trun(r, brand, is_heading=False) for r in item.runs],
                level=item.level,
                bullet="•",
                space_after=2,
            )
        )

    sm.elements.append(
        lm.TextEl(
            name=f"Card Content {card.title}",
            x=x + pad,
            y=y + pad + top_accent_h,
            w=w - 2 * pad,
            h=h - 2 * pad - top_accent_h,
            paras=paras,
            valign="top",
        )
    )


def _layout_quote_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    q = next((b for b in slide.blocks if isinstance(b, ir.Quote)), None)
    if not q:
        _layout_content_slide(slide, brand, geom, sm)
        return

    # Draw left decorative accent bar
    bar_w = int(6 * EMU_PER_PT)
    bar_h = int(cr.h * 0.5)
    bar_y = cr.y + int(cr.h * 0.25)
    sm.elements.append(
        lm.ShapeEl(
            name="Quote Accent Bar",
            x=cr.x,
            y=bar_y,
            w=bar_w,
            h=bar_h,
            geom="rect",
            fill=brand.roles.primary,
        )
    )

    pad_left = int(0.4 * EMU_PER_INCH)
    paras = [
        lm.Para(
            runs=[
                lm.TRun(
                    text=ir.runs_text(q.runs),
                    font=brand.fonts.heading.family,
                    size=brand.fonts.sizes.title * 0.85,
                    color=brand.roles.text,
                    italic=True,
                )
            ],
            space_after=12,
        )
    ]
    if q.attribution:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=f"— {q.attribution}",
                        font=brand.fonts.body.family,
                        size=brand.fonts.sizes.body,
                        color=brand.roles.muted,
                    )
                ]
            )
        )

    sm.elements.append(
        lm.TextEl(
            name="Quote Content",
            x=cr.x + pad_left,
            y=bar_y,
            w=cr.w - pad_left,
            h=bar_h,
            paras=paras,
            valign="middle",
        )
    )


def _layout_freeform_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    ff = next((b for b in slide.blocks if isinstance(b, ir.Freeform)), None)
    if not ff:
        _layout_content_slide(slide, brand, geom, sm)
        return

    for idx, el in enumerate(ff.elements):
        ex = cr.x + int(el.x * cr.w)
        ey = cr.y + int(el.y * cr.h)
        ew = max(int(el.w * cr.w), int(0.1 * EMU_PER_INCH))
        eh = max(int(el.h * cr.h), int(0.1 * EMU_PER_INCH))

        if el.kind == "box":
            sm.elements.append(
                lm.ShapeEl(
                    name=f"Free Box {idx}",
                    x=ex,
                    y=ey,
                    w=ew,
                    h=eh,
                    geom="roundRect" if el.radius_px > 0 else "rect",
                    radius=brand.design.corner_radius if el.radius_px > 0 else 0.0,
                    fill=brand.roles.surface if el.fill else None,
                    line=brand.roles.border if el.border else None,
                )
            )
        elif el.kind == "text":
            paras = [
                lm.Para(
                    runs=[_ir_run_to_trun(r, brand, is_heading=el.bold) for r in el.runs],
                    align="center" if el.align == "center" else "right" if el.align == "right" else "left",
                )
            ]
            sm.elements.append(
                lm.TextEl(
                    name=f"Free Text {idx}",
                    x=ex,
                    y=ey,
                    w=ew,
                    h=eh,
                    paras=paras,
                    valign="middle",
                )
            )
        elif el.kind == "image" and el.b64 and el.mime:
            sm.elements.append(
                lm.ImageEl(
                    name=f"Free Img {idx}",
                    x=ex,
                    y=ey,
                    w=ew,
                    h=eh,
                    mime=el.mime,
                    b64=el.b64,
                )
            )


def _layout_content_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    _layout_block_stack(slide.blocks, geom.content_rect, brand, sm)


# ---------------------------------------------------------------- Block Stack Layout

def _layout_block_stack(blocks: Sequence[ir.Block], rect: Rect, brand: BrandProfile, sm: lm.SlideModel) -> None:
    """Stack blocks vertically inside the given rectangle."""
    if not blocks:
        return

    cur_y = rect.y
    text_paras: list[lm.Para] = []
    text_start_y = cur_y

    def flush_text():
        nonlocal text_paras, text_start_y, cur_y
        if not text_paras:
            return
        th = max(cur_y - text_start_y, int(0.5 * EMU_PER_INCH))
        sm.elements.append(
            lm.TextEl(
                name="Content Text",
                x=rect.x,
                y=text_start_y,
                w=rect.w,
                h=th,
                paras=text_paras,
                valign="top",
            )
        )
        text_paras = []

    for b in blocks:
        if isinstance(b, ir.Heading):
            flush_text()
            text_start_y = cur_y
            text_paras.append(
                lm.Para(
                    runs=[_ir_run_to_trun(r, brand, is_heading=True) for r in b.runs],
                    space_before=8 if cur_y > rect.y else 0,
                    space_after=4,
                )
            )
            cur_y += int(brand.fonts.sizes.heading * 1.5 * EMU_PER_PT)
        elif isinstance(b, ir.Paragraph):
            if not text_paras:
                text_start_y = cur_y
            text_paras.append(
                lm.Para(
                    runs=[_ir_run_to_trun(r, brand, is_heading=False) for r in b.runs],
                    space_before=4,
                    space_after=4,
                )
            )
            cur_y += int(brand.fonts.sizes.body * 1.4 * EMU_PER_PT)
        elif isinstance(b, ir.BulletList):
            if not text_paras:
                text_start_y = cur_y
            for it in b.items:
                text_paras.append(
                    lm.Para(
                        runs=[_ir_run_to_trun(r, brand, is_heading=False) for r in it.runs],
                        level=it.level,
                        bullet="1." if b.ordered else "•",
                        space_before=2,
                        space_after=2,
                    )
                )
                cur_y += int(brand.fonts.sizes.body * 1.35 * EMU_PER_PT)
        elif isinstance(b, ir.Table):
            flush_text()
            avail_h = rect.bottom - cur_y
            tbl_h = min(avail_h, int(len(b.rows) * 0.45 * EMU_PER_INCH))
            _build_table_element(b, rect.x, cur_y, rect.w, tbl_h, brand, sm)
            cur_y += tbl_h + brand.design.gutter
            text_start_y = cur_y
        elif isinstance(b, ir.Chart):
            flush_text()
            avail_h = rect.bottom - cur_y
            _build_chart_element(b.spec, rect.x, cur_y, rect.w, avail_h, brand, sm)
            cur_y = rect.bottom
            text_start_y = cur_y
        elif isinstance(b, ir.Image):
            flush_text()
            img_w = min(rect.w, int(3.5 * EMU_PER_INCH))
            aspect = (b.w / max(b.h, 1)) if (b.w and b.h) else 1.5
            img_h = int(img_w / aspect)
            sm.elements.append(
                lm.ImageEl(
                    name="Content Image",
                    x=rect.x,
                    y=cur_y,
                    w=img_w,
                    h=img_h,
                    mime=b.mime,
                    b64=b.b64,
                )
            )
            cur_y += img_h + brand.design.gutter
            text_start_y = cur_y

    flush_text()


# ---------------------------------------------------------------- Component Builders

def _build_chart_element(
    spec: ir.ChartSpec,
    x: int,
    y: int,
    w: int,
    h: int,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    sm.elements.append(
        lm.ChartEl(
            name=f"Chart {spec.title or spec.type}",
            x=x,
            y=y,
            w=w,
            h=h,
            spec=spec,
            palette=brand.chart_palette,
            font=brand.fonts.body.family,
            font_size=brand.fonts.sizes.caption,
            text_color=brand.roles.text,
            grid_color=brand.roles.border,
            legend=spec.show_legend,
        )
    )


def _build_table_element(
    table: ir.Table,
    x: int,
    y: int,
    w: int,
    h: int,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    n_rows = len(table.rows)
    n_cols = len(table.rows[0]) if n_rows else 0
    if not n_rows or not n_cols:
        return

    # Calculate column widths
    if table.col_weights and len(table.col_weights) == n_cols:
        tot = sum(table.col_weights) or 1.0
        col_widths = [int(w * (wt / tot)) for wt in table.col_weights]
    else:
        col_widths = [w // n_cols] * n_cols

    # Calculate row heights
    row_height = h // n_rows
    row_heights = [row_height] * n_rows

    table_cells: list[list[lm.CellEl]] = []
    for ri, row in enumerate(table.rows):
        row_cells: list[lm.CellEl] = []
        is_head = ri < table.header_rows
        is_total = table.total_row and ri == n_rows - 1

        for ci, cell in enumerate(row):
            runs = [
                lm.TRun(
                    text=r.t,
                    font=brand.fonts.heading.family if is_head else brand.fonts.body.family,
                    size=brand.fonts.sizes.body if not is_head else brand.fonts.sizes.body * 1.05,
                    color=brand.roles.on_primary if (is_head and brand.design.table_style == "banded") else brand.roles.text,
                    bold=is_head or is_total or cell.bold,
                )
                for r in cell.runs
            ]

            # Background fill
            fill = None
            if is_head:
                fill = brand.roles.primary if brand.design.table_style == "banded" else brand.roles.surface
            elif brand.design.table_style == "banded" and ri % 2 == 1:
                fill = brand.roles.surface
            elif is_total:
                fill = brand.roles.surface

            row_cells.append(
                lm.CellEl(
                    paras=[lm.Para(runs=runs, align=cell.align)],
                    fill=fill,
                    colspan=cell.colspan,
                    rowspan=cell.rowspan,
                    border_bottom=brand.roles.border if brand.design.table_style != "minimal" else None,
                    border_bottom_w=1.0,
                )
            )
        table_cells.append(row_cells)

    sm.elements.append(
        lm.TableEl(
            name=f"Table {table.caption or ''}",
            x=x,
            y=y,
            w=w,
            h=h,
            col_widths=col_widths,
            row_heights=row_heights,
            rows=table_cells,
        )
    )


def _ir_run_to_trun(r: ir.Run, brand: BrandProfile, is_heading: bool = False) -> lm.TRun:
    color = brand.roles.primary if r.emphasis else (brand.roles.text if not is_heading else brand.roles.primary)
    font = brand.fonts.heading.family if is_heading else brand.fonts.body.family
    size = brand.fonts.sizes.heading if is_heading else brand.fonts.sizes.body
    return lm.TRun(
        text=r.t,
        font=font,
        size=size,
        color=color,
        bold=r.b or is_heading,
        italic=r.i,
        underline=r.u,
        strike=r.strike,
        href=r.href,
        sup=r.sup,
        sub=r.sub,
    )
