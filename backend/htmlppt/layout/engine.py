"""Layout engine: transforms Semantic IR Deck -> Positioned DeckModel.

Applies brand styling, dimensions, font hierarchy, colors, spacing and
grid math to construct EMU-positioned slide models.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.brand.model import BrandProfile
from htmlppt.layout import model as lm
from htmlppt.layout.archetypes import select_archetype
from htmlppt.layout.grid import Rect, SlideGeometry
from htmlppt.layout.overflow import handle_overflow
from htmlppt.layout.textfit import (
    estimate_paras_height_emu,
    estimate_text_height_emu,
    fit_font_size,
    scale_paras_to_fit,
)


def tone_color(tone: str | None, brand: BrandProfile, default: str | None = None) -> str:
    """Map a SemanticTone to the active BrandProfile color role."""
    fallback = default if default is not None else brand.roles.primary
    if not tone or tone == "none":
        return fallback
    if tone == "positive":
        return brand.roles.positive
    if tone == "negative":
        return brand.roles.negative
    if tone == "warning":
        return brand.colors.accent4 or brand.roles.primary
    if tone in ("primary", "info"):
        return brand.roles.primary
    if tone == "muted":
        return brand.roles.muted
    return fallback


def _estimate_paras_height_emu(paras: Sequence[lm.Para], width_emu: int, inset_emu: int = 0) -> int:
    """Calculate total wrapped height in EMU for a list of paragraphs."""
    avail_w = max(int(0.4 * EMU_PER_INCH), width_emu - 2 * inset_emu)
    return estimate_paras_height_emu(paras, avail_w, default_line_spacing=1.2) + 2 * inset_emu


def _autofit_paras_to_box(
    paras: list[lm.Para],
    width_emu: int,
    height_emu: int,
    min_font_pt: float = 8.5,
    inset_emu: int = 0,
) -> None:
    """Proportionally scale down paragraph font sizes and spacing until they fit height_emu."""
    if not paras or height_emu <= 0 or width_emu <= 0:
        return

    target_h = int(height_emu * 0.92) if len(paras) > 1 else height_emu
    avail_w = max(int(0.4 * EMU_PER_INCH), width_emu - 2 * inset_emu)
    avail_h = max(int(8 * EMU_PER_PT), target_h - 2 * inset_emu)

    # Single-paragraph single-run fast path can also consult fit_font_size
    if len(paras) == 1 and len(paras[0].runs) == 1 and not paras[0].bullet:
        r0 = paras[0].runs[0]
        run_avail_h = max(int(8 * EMU_PER_PT), avail_h - int((paras[0].space_before + paras[0].space_after) * EMU_PER_PT))
        r0.size = fit_font_size(
            r0.text,
            avail_w,
            run_avail_h,
            r0.font,
            r0.size,
            min_size_pt=min_font_pt,
            line_spacing=max(paras[0].line_spacing or 1.2, 1.2),
            bold=r0.bold,
        )
    elif _estimate_paras_height_emu(paras, width_emu, inset_emu) > target_h:
        paras[:] = scale_paras_to_fit(paras, avail_w, avail_h, min_size_pt=min_font_pt, default_line_spacing=1.2)

    for _ in range(24):
        needed = _estimate_paras_height_emu(paras, width_emu, inset_emu)
        if needed <= target_h:
            return
        changed = False
        for p in paras:
            if p.space_before > 0.5:
                p.space_before = max(0.0, round(p.space_before * 0.75, 1))
                changed = True
            if p.space_after > 0.5:
                p.space_after = max(0.0, round(p.space_after * 0.75, 1))
                changed = True
            for r in p.runs:
                if r.size > min_font_pt:
                    new_sz = max(min_font_pt, round(r.size - max(0.5, r.size * 0.07), 1))
                    if new_sz < r.size:
                        r.size = new_sz
                        changed = True
        if not changed:
            break


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


def _build_header_paras(slide: ir.Slide, brand: BrandProfile) -> list[lm.Para]:
    paras: list[lm.Para] = []
    kicker_val = getattr(slide, "kicker", "") or ""
    has_sub_or_kicker = bool(kicker_val or slide.subtitle)

    if kicker_val:
        raw_kicker = kicker_val.lstrip("• ").strip().upper()
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=f"• {raw_kicker}",
                        font=brand.fonts.body.family,
                        size=min(11.0, max(9.5, brand.fonts.sizes.caption)),
                        color=brand.roles.primary,
                        bold=True,
                    )
                ],
                align="center" if brand.design.title_align == "center" else "left",
                space_after=2,
            )
        )

    title_size = min(brand.fonts.sizes.title, 24.0 if has_sub_or_kicker else 28.0)
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

    if slide.subtitle:
        sub_size = min(brand.fonts.sizes.subtitle, 12.5)
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=slide.subtitle,
                        font=brand.fonts.body.family,
                        size=sub_size,
                        color=brand.roles.muted,
                        italic=False,
                    )
                ],
                space_before=3,
                align="center" if brand.design.title_align == "center" else "left",
            )
        )
    return paras


def layout_slide(slide: ir.Slide, brand: BrandProfile, slide_num: int) -> lm.SlideModel:
    if getattr(slide, "dom_node", None) is not None and slide.layout is None and not slide.continued:
        from htmlppt.layout.direct_mapper import layout_slide_from_dom

        return layout_slide_from_dom(slide, brand, slide_num=slide_num)

    archetype = select_archetype(slide)
    has_title = bool(slide.title) and archetype not in ("title", "section", "closing")
    geom = SlideGeometry.from_brand(brand, has_title=has_title, slide=slide)

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
        _add_footer(brand, geom, slide_model, slide=slide)

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
    kicker_val = getattr(slide, "kicker", "") or ""
    paras = _build_header_paras(slide, brand)
    _autofit_paras_to_box(paras, tr.w, tr.h, min_font_pt=9.5)

    sm.elements.append(
        lm.TextEl(
            name="Slide Title",
            x=tr.x,
            y=tr.y,
            w=tr.w,
            h=tr.h,
            paras=paras,
            placeholder="title",
            valign="middle" if not (slide.subtitle or kicker_val) else "top",
        )
    )

    # Accent treatment placed cleanly in the gap below tr.bottom so it never collides with Slide Title or content_rect
    treatment = brand.design.title_treatment
    if treatment == "accent-bar":
        bar_w = int(2.5 * EMU_PER_INCH)
        bar_h = int(3 * EMU_PER_PT)
        bar_y = tr.bottom + int(1 * EMU_PER_PT)
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
        sm.elements.append(
            lm.ShapeEl(
                name="Title Underline",
                x=tr.x,
                y=tr.bottom + int(1 * EMU_PER_PT),
                w=tr.w,
                h=int(1 * EMU_PER_PT),
                geom="rect",
                fill=brand.roles.border,
                line=None,
            )
        )


def _add_footer(brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel, slide: ir.Slide | None = None) -> None:
    footer = brand.footer
    h = min(int(0.28 * EMU_PER_INCH), max(int(0.20 * EMU_PER_INCH), geom.margin_bottom - int(0.06 * EMU_PER_INCH)))
    y = geom.height - geom.margin_bottom + int(0.04 * EMU_PER_INCH)

    # Left / Center text: Confidentiality, brand footer text, or source slide footer_left
    items = []
    if footer.confidentiality:
        items.append(footer.confidentiality)
    if footer.text:
        items.append(footer.text)
    if not items and slide is not None and getattr(slide, "footer_left", ""):
        items.append(slide.footer_left)

    num_w = int(1.5 * EMU_PER_INCH)
    left_w = geom.width - 2 * geom.margin_x - num_w - int(0.2 * EMU_PER_INCH)

    if items and left_w > 0:
        text = "  |  ".join(items)
        f_size = min(brand.fonts.sizes.caption, 9.5)
        f_paras = [
            lm.Para(
                runs=[
                    lm.TRun(
                        text=text,
                        font=brand.fonts.body.family,
                        size=f_size,
                        color=brand.roles.muted,
                    )
                ]
            )
        ]
        _autofit_paras_to_box(f_paras, left_w, h, min_font_pt=7.5)
        sm.elements.append(
            lm.TextEl(
                name="Footer Text",
                x=geom.margin_x,
                y=y,
                w=left_w,
                h=h,
                paras=f_paras,
                valign="middle",
            )
        )

    # Right: Slide number
    if footer.slide_number:
        sm.elements.append(
            lm.TextEl(
                name="Slide Number",
                x=geom.width - geom.margin_x - num_w,
                y=y,
                w=num_w,
                h=h,
                paras=[
                    lm.Para(
                        runs=[
                            lm.TRun(
                                text=str(sm.number),
                                font=brand.fonts.body.family,
                                size=min(brand.fonts.sizes.caption, 9.5),
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
    cy = int(geom.height * 0.25)
    ch = int(geom.height * 0.55)

    paras: list[lm.Para] = []
    kicker_val = getattr(slide, "kicker", "") or ""
    if kicker_val:
        raw_kicker = kicker_val.lstrip("• ").strip().upper()
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=f"• {raw_kicker}",
                        font=brand.fonts.body.family,
                        size=11.0,
                        color=brand.roles.primary,
                        bold=True,
                    )
                ],
                space_after=6,
            )
        )

    paras.append(
        lm.Para(
            runs=[
                lm.TRun(
                    text=slide.title or "Presentation",
                    font=brand.fonts.heading.family,
                    size=min(40.0, brand.fonts.sizes.title * 1.25),
                    color=brand.roles.primary,
                    bold=True,
                )
            ],
            space_after=10,
        )
    )

    if slide.subtitle:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=slide.subtitle,
                        font=brand.fonts.body.family,
                        size=min(18.0, brand.fonts.sizes.subtitle),
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
                    runs=[_ir_run_to_trun(r, brand, is_heading=False, size_pt=14.0) for r in b.runs],
                    space_before=4,
                )
            )

    _autofit_paras_to_box(paras, cw, ch, min_font_pt=10.0)
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
    cy = int(geom.height * 0.32)
    ch = int(geom.height * 0.38)

    paras = [
        lm.Para(
            runs=[
                lm.TRun(
                    text=slide.title,
                    font=brand.fonts.heading.family,
                    size=min(36.0, brand.fonts.sizes.title * 1.15),
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
                        size=min(16.0, brand.fonts.sizes.subtitle),
                        color=brand.roles.muted,
                    )
                ],
                align="center" if brand.design.title_align == "center" else "left",
            )
        )

    _autofit_paras_to_box(paras, cw, ch, min_font_pt=10.0)
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
    cy = int(geom.height * 0.30)
    ch = int(geom.height * 0.42)

    paras = [
        lm.Para(
            runs=[
                lm.TRun(
                    text=slide.title or "Thank You",
                    font=brand.fonts.heading.family,
                    size=min(38.0, brand.fonts.sizes.title * 1.2),
                    color=brand.roles.primary,
                    bold=True,
                )
            ],
            align="center",
            space_after=10,
        )
    ]
    if slide.subtitle:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=slide.subtitle,
                        font=brand.fonts.body.family,
                        size=min(16.0, brand.fonts.sizes.subtitle),
                        color=brand.roles.muted,
                    )
                ],
                align="center",
            )
        )

    _autofit_paras_to_box(paras, cw, ch, min_font_pt=10.0)
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
    if len(slide.blocks) == 1 and isinstance(slide.blocks[0], ir.KPIGroup) and slide.blocks[0].items:
        kpi_block = slide.blocks[0]
        n_kpis = len(kpi_block.items)
        gutter = brand.design.gutter
        kpi_height = int(cr.h * 0.32)
        kpi_y = cr.y + (cr.h - kpi_height) // 2
        total_gutters = gutter * (n_kpis - 1)
        tile_w = (cr.w - total_gutters) // n_kpis
        for i, item in enumerate(kpi_block.items):
            tx = cr.x + i * (tile_w + gutter)
            _build_kpi_card(item, tx, kpi_y, tile_w, kpi_height, brand, sm, is_standalone=True)
        return

    _layout_compound_zones(slide.blocks, cr, brand, sm)


def _build_kpi_card(
    kpi: ir.KPI,
    x: int,
    y: int,
    w: int,
    h: int,
    brand: BrandProfile,
    sm: lm.SlideModel,
    is_standalone: bool = False,
) -> None:
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

    padding = min(int(0.16 * EMU_PER_INCH), max(int(0.08 * EMU_PER_INCH), min(w, h) // 9))
    tx = x + padding
    ty = y + padding
    tw = max(int(0.5 * EMU_PER_INCH), w - 2 * padding)
    th = max(int(0.4 * EMU_PER_INCH), h - 2 * padding)

    if is_standalone:
        metric_size = max(36.0, min(44.0, brand.fonts.sizes.kpi))
        label_size = max(13.0, min(14.5, brand.fonts.sizes.body * 0.88))
        delta_size = max(10.0, min(11.5, brand.fonts.sizes.caption))
    else:
        metric_size = min(30.0, max(24.0, brand.fonts.sizes.kpi * 0.75))
        label_size = 12.0
        delta_size = 10.0

    paras: list[lm.Para] = [
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
            space_after=2,
        ),
        lm.Para(
            runs=[
                lm.TRun(
                    text=kpi.label,
                    font=brand.fonts.body.family,
                    size=label_size,
                    color=brand.roles.text if not is_standalone else brand.roles.muted,
                    bold=not is_standalone,
                )
            ],
            space_after=2,
        ),
    ]

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
                        size=delta_size,
                        color=delta_color,
                        bold=True,
                    )
                ]
            )
        )

    _autofit_paras_to_box(paras, tw, th, min_font_pt=8.5)

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
    if len(slide.blocks) == 1 and isinstance(slide.blocks[0], ir.Chart):
        _build_chart_element(slide.blocks[0].spec, cr.x, cr.y, cr.w, cr.h, brand, sm)
        return
    _layout_compound_zones(slide.blocks, cr, brand, sm)


def _layout_chart_plus_text_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cols = geom.split_columns(2)
    chart_block = next((b for b in slide.blocks if isinstance(b, ir.Chart)), None)
    text_blocks = [b for b in slide.blocks if b is not chart_block]

    if chart_block:
        _build_chart_element(chart_block.spec, cols[0].x, cols[0].y, cols[0].w, cols[0].h, brand, sm)
    if text_blocks:
        _layout_compound_zones(text_blocks, cols[1], brand, sm)


def _block_content_weight(block: ir._Block) -> int:
    """Estimate content weight (in characters / text density) of a block."""
    if isinstance(block, ir.Heading):
        return sum(len(r.t) for r in getattr(block, "runs", [])) * 2
    elif isinstance(block, ir.BulletList):
        return sum(len(r.t) for item in block.items for r in getattr(item, "runs", []))
    elif isinstance(block, ir.Callout):
        return len(block.title) * 2 + len(getattr(block, "badge", "")) + sum(len(r.t) for r in getattr(block, "runs", []))
    elif isinstance(block, ir.Table):
        return sum(len(r.t) for row in block.rows for cell in row for r in getattr(cell, "runs", []))
    elif isinstance(block, ir.KPIGroup):
        return sum(len(k.value) * 3 + len(k.label) for k in block.items)
    elif isinstance(block, ir.CardGrid):
        return sum(len(c.title) * 2 + sum(len(r.t) for r in getattr(c, "body", [])) for c in block.cards)
    elif isinstance(block, (ir.Image, ir.Chart)):
        return 150
    elif hasattr(block, "runs") and getattr(block, "runs", None):
        return sum(len(r.t) for r in block.runs)
    return 50


def _render_hero_fallback(blocks: list[ir._Block], cr: Rect, brand: BrandProfile, sm: lm.SlideModel) -> None:
    """Render collapsed single-column hero container rather than leaving a blank half-screen."""
    hero_w = min(cr.w, int(cr.w * 0.92))
    hero_x = cr.x + (cr.w - hero_w) // 2
    pad = int(0.3 * EMU_PER_INCH)

    use_container = brand.design.dark or brand.design.card_style in ("filled", "shadow", "outline")
    if use_container:
        fill = brand.roles.surface if brand.design.dark or brand.design.card_style in ("filled", "shadow") else None
        line = brand.roles.border if brand.design.dark or brand.design.card_style in ("filled", "outline") else None
        radius = brand.design.corner_radius if brand.design.corner_radius > 0 else 0.04
        sm.elements.append(
            lm.ShapeEl(
                name="Hero Container",
                x=hero_x,
                y=cr.y,
                w=hero_w,
                h=cr.h,
                geom="roundRect" if radius > 0 else "rect",
                radius=radius,
                fill=fill,
                line=line,
                line_w=brand.design.line_weight_pt or 1.0,
            )
        )
        inner_rect = Rect(x=hero_x + pad, y=cr.y + pad, w=hero_w - 2 * pad, h=cr.h - 2 * pad)
        _layout_compound_zones(blocks, inner_rect, brand, sm)
    else:
        hero_rect = Rect(x=hero_x, y=cr.y, w=hero_w, h=cr.h)
        _layout_compound_zones(blocks, hero_rect, brand, sm)


def _layout_two_column_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect

    if len(slide.blocks) <= 1:
        _render_hero_fallback(list(slide.blocks), cr, brand, sm)
        return

    # If the slide contains 3+ column bands or mixed full-width blocks + column bands,
    # delegate to _layout_compound_zones so full-width zones are not hijacked into columns
    has_col = any(b.column is not None for b in slide.blocks)
    has_non_col = any(b.column is None for b in slide.blocks)
    has_3plus_col = any(b.column is not None and b.column >= 2 for b in slide.blocks)
    if has_3plus_col or (has_col and has_non_col):
        _layout_compound_zones(slide.blocks, cr, brand, sm)
        return

    cols = geom.split_columns(2)
    left_blocks = [b for b in slide.blocks if b.column == 0]
    right_blocks = [b for b in slide.blocks if b.column == 1]
    unassigned = [b for b in slide.blocks if b.column not in (0, 1)]

    # If unassigned blocks exist or explicit columns are missing, distribute by content weight
    if not left_blocks and not right_blocks:
        blocks = list(slide.blocks)
        weights = [_block_content_weight(b) for b in blocks]
        total_w = sum(weights)
        if total_w == 0:
            best_split = len(blocks) // 2
        else:
            best_split = 1
            min_diff = float("inf")
            running = 0
            for i in range(len(blocks) - 1):
                running += weights[i]
                rem = total_w - running
                diff = abs(running - rem)
                if diff < min_diff:
                    min_diff = diff
                    best_split = i + 1
        left_blocks = blocks[:best_split]
        right_blocks = blocks[best_split:]
    elif unassigned:
        w_left = sum(_block_content_weight(b) for b in left_blocks)
        w_right = sum(_block_content_weight(b) for b in right_blocks)
        for b in unassigned:
            if w_left <= w_right:
                left_blocks.append(b)
                w_left += _block_content_weight(b)
            else:
                right_blocks.append(b)
                w_right += _block_content_weight(b)

    # Content-aware check: check if one column is empty or represents < 15% of total content
    w_left = sum(_block_content_weight(b) for b in left_blocks)
    w_right = sum(_block_content_weight(b) for b in right_blocks)
    total_w = w_left + w_right

    if not left_blocks or not right_blocks or total_w == 0 or (w_left < 0.15 * total_w) or (w_right < 0.15 * total_w):
        all_blocks = left_blocks + right_blocks
        _render_hero_fallback(all_blocks, cr, brand, sm)
        return

    # Two genuine balanced columns: render with clean column separation
    pad = int(0.22 * EMU_PER_INCH)
    use_container = brand.design.dark or brand.design.card_style in ("filled", "shadow", "outline")

    for col_idx, (col_rect, col_blocks) in enumerate(zip(cols, [left_blocks, right_blocks])):
        clean_blocks = [b.model_copy(update={"column": None}) for b in col_blocks]
        if use_container:
            fill = brand.roles.surface if brand.design.dark or brand.design.card_style in ("filled", "shadow") else None
            line = brand.roles.border if brand.design.dark or brand.design.card_style in ("filled", "outline") else None
            radius = brand.design.corner_radius if brand.design.corner_radius > 0 else 0.04
            sm.elements.append(
                lm.ShapeEl(
                    name=f"Column Container {col_idx + 1}",
                    x=col_rect.x,
                    y=col_rect.y,
                    w=col_rect.w,
                    h=col_rect.h,
                    geom="roundRect" if radius > 0 else "rect",
                    radius=radius,
                    fill=fill,
                    line=line,
                    line_w=brand.design.line_weight_pt or 1.0,
                )
            )
            inner_rect = Rect(x=col_rect.x + pad, y=col_rect.y + pad, w=col_rect.w - 2 * pad, h=col_rect.h - 2 * pad)
            _layout_compound_zones(clean_blocks, inner_rect, brand, sm)
        else:
            _layout_compound_zones(clean_blocks, col_rect, brand, sm)


def _layout_table_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    if len(slide.blocks) == 1 and isinstance(slide.blocks[0], ir.Table) and slide.blocks[0].rows:
        _build_table_element(slide.blocks[0], cr.x, cr.y, cr.w, cr.h, brand, sm)
        return
    _layout_compound_zones(slide.blocks, cr, brand, sm)


def _layout_cards_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    if len(slide.blocks) == 1 and isinstance(slide.blocks[0], ir.CardGrid) and slide.blocks[0].cards:
        _layout_card_grid_in_rect(slide.blocks[0], cr, brand, sm, is_standalone=True)
        return
    _layout_compound_zones(slide.blocks, cr, brand, sm)


def _layout_card_grid_in_rect(
    cg: ir.CardGrid,
    rect: Rect,
    brand: BrandProfile,
    sm: lm.SlideModel,
    is_standalone: bool = False,
    force_single_row: bool = False,
) -> None:
    n_cards = len(cg.cards)
    if not n_cards:
        return
    gutter = brand.design.gutter if is_standalone else min(brand.design.gutter, int(0.18 * EMU_PER_INCH))

    if n_cards == 4 and (force_single_row or (not is_standalone and rect.h < int(2.35 * EMU_PER_INCH))):
        # 1x4 horizontal row for compact step/service grids on compound slides
        tile_w = (rect.w - gutter * 3) // 4
        tile_h = rect.h
        for i, card in enumerate(cg.cards):
            cx = rect.x + i * (tile_w + gutter)
            _build_card_element(card, cx, rect.y, tile_w, tile_h, cg.variant, brand, sm)
    elif n_cards == 4:
        # 2x2 grid
        tile_w = (rect.w - gutter) // 2
        tile_h = (rect.h - gutter) // 2
        for i, card in enumerate(cg.cards):
            r = i // 2
            c = i % 2
            cx = rect.x + c * (tile_w + gutter)
            cy = rect.y + r * (tile_h + gutter)
            _build_card_element(card, cx, cy, tile_w, tile_h, cg.variant, brand, sm)
    elif n_cards in (5, 6):
        # 2x3 grid
        tile_w = (rect.w - gutter * 2) // 3
        tile_h = (rect.h - gutter) // 2
        for i, card in enumerate(cg.cards):
            r = i // 3
            c = i % 3
            cx = rect.x + c * (tile_w + gutter)
            cy = rect.y + r * (tile_h + gutter)
            _build_card_element(card, cx, cy, tile_w, tile_h, cg.variant, brand, sm)
    elif n_cards <= 3:
        tile_w = (rect.w - gutter * (n_cards - 1)) // n_cards
        if is_standalone:
            pad = int(0.25 * EMU_PER_INCH)
            estimated_max_h = 0
            for c in cg.cards:
                h_est = 2 * pad
                if c.kicker or getattr(c, "badge", ""):
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
            tile_h = min(rect.h, max(int(rect.h * 0.45), min(int(rect.h * 0.85), content_budget)))
            cy = rect.y + (rect.h - tile_h) // 2
        else:
            tile_h = rect.h
            cy = rect.y

        for i, card in enumerate(cg.cards):
            cx = rect.x + i * (tile_w + gutter)
            _build_card_element(card, cx, cy, tile_w, tile_h, cg.variant, brand, sm)
    else:
        # 7+ cards wrap across 3 columns
        cols = 3
        rows = (n_cards + cols - 1) // cols
        tile_w = (rect.w - gutter * (cols - 1)) // cols
        tile_h = (rect.h - gutter * (rows - 1)) // rows
        for i, card in enumerate(cg.cards):
            r = i // cols
            c = i % cols
            cx = rect.x + c * (tile_w + gutter)
            cy = rect.y + r * (tile_h + gutter)
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
    card_style = brand.design.card_style
    card_tone = getattr(card, "tone", "none") or "none"
    has_tone = card_tone not in ("none", "")
    accent_color = tone_color(card_tone, brand, default=brand.roles.primary)

    fill = brand.roles.surface if card_style in ("filled", "shadow") or brand.design.dark else None
    line = accent_color if has_tone else (brand.roles.border if card_style in ("outline", "filled") or brand.design.dark else None)
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

    is_process = variant in ("process", "timeline")
    top_accent_h = int(4 * EMU_PER_PT) if (is_process or has_tone) else 0
    if top_accent_h > 0:
        sm.elements.append(
            lm.ShapeEl(
                name="Card Top Accent" if is_process else "Card Tone Accent",
                x=x,
                y=y,
                w=w,
                h=top_accent_h,
                geom="rect",
                fill=accent_color if has_tone else brand.roles.primary,
                line=None,
            )
        )

    pad = min(int(0.18 * EMU_PER_INCH), max(int(0.08 * EMU_PER_INCH), min(w, h) // 10))
    paras: list[lm.Para] = []

    badge_val = getattr(card, "badge", "") or ""
    badge_in_title = bool(badge_val and card.title and card.title.startswith(badge_val))
    kicker_str = card.kicker or ("" if badge_in_title else badge_val)
    if kicker_str:
        raw_kicker = kicker_str.lstrip("• ").strip().upper()
        if card.kicker and badge_val and badge_val != card.kicker and not badge_in_title:
            raw_kicker = f"{raw_kicker} · {badge_val.upper()}"
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=f"• {raw_kicker}",
                        font=brand.fonts.body.family,
                        size=10.0,
                        color=accent_color,
                        bold=True,
                    )
                ],
                space_after=2,
            )
        )

    if card.title:
        title_color = accent_color if (has_tone and card_tone in ("positive", "negative") and not kicker_str) else brand.roles.text
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=card.title,
                        font=brand.fonts.heading.family,
                        size=min(14.5, max(12.5, brand.fonts.sizes.heading * 0.72)),
                        color=title_color,
                        bold=True,
                    )
                ],
                space_after=3,
            )
        )

    if card.body:
        body_size = min(11.5, max(10.0, brand.fonts.sizes.body * 0.72))
        paras.extend(_runs_to_paras(card.body, brand, size_pt=body_size, space_after=3))

    bullet_size = min(11.0, max(9.5, brand.fonts.sizes.body * 0.70))
    for item in card.bullets:
        paras.append(
            lm.Para(
                runs=_ir_runs_to_truns(item.runs, brand, size_pt=bullet_size),
                level=item.level,
                bullet="•",
                bullet_color=accent_color,
                space_after=2,
            )
        )

    footer_str = getattr(card, "footer", "") or ""
    if footer_str:
        footer_tone = card_tone if card_tone in ("negative", "warning") else "positive"
        footer_color = tone_color(footer_tone, brand, default=brand.roles.positive)
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=footer_str,
                        font=brand.fonts.body.family,
                        size=10.5,
                        color=footer_color,
                        bold=True,
                    )
                ],
                space_before=3,
            )
        )

    tw = max(int(0.4 * EMU_PER_INCH), w - 2 * pad)
    th = max(int(0.3 * EMU_PER_INCH), h - 2 * pad - top_accent_h)
    _autofit_paras_to_box(paras, tw, th, min_font_pt=8.5)

    sm.elements.append(
        lm.TextEl(
            name=f"Card Content {card.title}",
            x=x + pad,
            y=y + pad + top_accent_h,
            w=tw,
            h=th,
            paras=paras,
            valign="top",
        )
    )


def _build_callout_element(
    callout: ir.Callout,
    x: int,
    y: int,
    w: int,
    h: int,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    c_tone = getattr(callout, "tone", "info") or "info"
    accent_color = tone_color(c_tone, brand, default=brand.roles.primary)
    radius = brand.design.corner_radius if brand.design.corner_radius > 0 else 0.04
    fill = brand.roles.surface
    line = accent_color if c_tone in ("positive", "negative", "warning", "primary") else brand.roles.border

    sm.elements.append(
        lm.ShapeEl(
            name=f"Callout {callout.title or 'Banner'}",
            x=x,
            y=y,
            w=w,
            h=h,
            geom="roundRect" if radius > 0 else "rect",
            radius=radius,
            fill=fill,
            line=line,
            line_w=max(1.0, brand.design.line_weight_pt),
        )
    )

    bar_w = int(4 * EMU_PER_PT)
    sm.elements.append(
        lm.ShapeEl(
            name="Callout Accent Bar",
            x=x,
            y=y,
            w=bar_w,
            h=h,
            geom="rect",
            fill=accent_color,
            line=None,
        )
    )

    pad_x = min(int(0.16 * EMU_PER_INCH), max(int(0.08 * EMU_PER_INCH), w // 18))
    pad_y = min(int(0.10 * EMU_PER_INCH), max(int(0.05 * EMU_PER_INCH), h // 10))
    tx = x + bar_w + pad_x
    ty = y + pad_y
    tw = max(int(0.4 * EMU_PER_INCH), w - bar_w - 2 * pad_x)
    th = max(int(0.3 * EMU_PER_INCH), h - 2 * pad_y)

    paras: list[lm.Para] = []
    badge_str = getattr(callout, "badge", "") or ""
    if callout.title or badge_str:
        head_runs: list[lm.TRun] = []
        if callout.title:
            head_runs.append(
                lm.TRun(
                    text=callout.title,
                    font=brand.fonts.heading.family,
                    size=12.5,
                    color=brand.roles.text,
                    bold=True,
                )
            )
        if badge_str:
            badge_col = brand.roles.positive if ("✓" in badge_str or c_tone == "positive") else accent_color
            prefix = "   " if callout.title else ""
            head_runs.append(
                lm.TRun(
                    text=f"{prefix}[{badge_str}]",
                    font=brand.fonts.body.family,
                    size=10.0,
                    color=badge_col,
                    bold=True,
                )
            )
        paras.append(lm.Para(runs=head_runs, space_after=2 if callout.runs else 0))

    if callout.runs:
        paras.extend(_runs_to_paras(callout.runs, brand, size_pt=11.0, space_after=2, default_color=brand.roles.text))

    _autofit_paras_to_box(paras, tw, th, min_font_pt=8.5)

    sm.elements.append(
        lm.TextEl(
            name=f"Callout Text {callout.title or 'Banner'}",
            x=tx,
            y=ty,
            w=tw,
            h=th,
            paras=paras,
            valign="middle",
        )
    )


def _layout_quote_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    if len(slide.blocks) == 1 and isinstance(slide.blocks[0], ir.Quote):
        q = slide.blocks[0]
        bar_h = int(cr.h * 0.5)
        bar_y = cr.y + int(cr.h * 0.25)
        _build_quote_element(q, cr.x, bar_y, cr.w, bar_h, brand, sm)
        return
    _layout_compound_zones(slide.blocks, cr, brand, sm)


def _build_quote_element(
    q: ir.Quote,
    x: int,
    y: int,
    w: int,
    h: int,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    bar_w = int(6 * EMU_PER_PT)
    sm.elements.append(
        lm.ShapeEl(
            name="Quote Accent Bar",
            x=x,
            y=y,
            w=bar_w,
            h=h,
            geom="rect",
            fill=brand.roles.primary,
        )
    )

    pad_left = int(0.35 * EMU_PER_INCH)
    tw = max(int(0.5 * EMU_PER_INCH), w - pad_left)
    paras = [
        lm.Para(
            runs=[
                lm.TRun(
                    text=ir.runs_text(q.runs),
                    font=brand.fonts.heading.family,
                    size=min(22.0, brand.fonts.sizes.title * 0.75),
                    color=brand.roles.text,
                    italic=True,
                )
            ],
            space_after=8,
        )
    ]
    if q.attribution:
        paras.append(
            lm.Para(
                runs=[
                    lm.TRun(
                        text=f"— {q.attribution}",
                        font=brand.fonts.body.family,
                        size=min(13.0, brand.fonts.sizes.body * 0.85),
                        color=brand.roles.muted,
                    )
                ]
            )
        )

    _autofit_paras_to_box(paras, tw, h, min_font_pt=9.5)
    sm.elements.append(
        lm.TextEl(
            name="Quote Content",
            x=x + pad_left,
            y=y,
            w=tw,
            h=h,
            paras=paras,
            valign="middle",
        )
    )


def _layout_freeform_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    cr = geom.content_rect
    if len(slide.blocks) == 1 and isinstance(slide.blocks[0], ir.Freeform):
        _build_freeform_element(slide.blocks[0], cr, brand, sm)
        return
    _layout_compound_zones(slide.blocks, cr, brand, sm)


def _build_freeform_element(
    ff: ir.Freeform,
    cr: Rect,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
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
                    runs=_ir_runs_to_truns(el.runs, brand, is_heading=el.bold, size_pt=min(14.0, max(9.5, el.font_px * 0.75))),
                    align="center" if el.align == "center" else "right" if el.align == "right" else "left",
                )
            ]
            _autofit_paras_to_box(paras, ew, eh, min_font_pt=8.0)
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
        elif el.kind == "vector" and el.svg:
            _build_vector_element(
                ir.VectorGraphic(svg=el.svg, w=float(ew), h=float(eh)),
                ex,
                ey,
                ew,
                eh,
                sm,
            )
        elif el.kind == "line":
            sm.elements.append(
                lm.ShapeEl(
                    name=f"Free Line {idx}",
                    x=ex,
                    y=ey,
                    w=ew,
                    h=max(eh, int(1.5 * EMU_PER_PT)),
                    geom="line",
                    line=brand.roles.border,
                    line_w=max(0.75, el.border_w or 1.0),
                )
            )


def _layout_content_slide(slide: ir.Slide, brand: BrandProfile, geom: SlideGeometry, sm: lm.SlideModel) -> None:
    _layout_compound_zones(slide.blocks, geom.content_rect, brand, sm)


# ---------------------------------------------------------------- Universal Zero-Drop Compound Zone Engine

@dataclass
class _Zone:
    kind: Literal["columns", "kpis", "cards", "table", "callout", "quote", "chart", "image", "vector", "freeform", "text"]
    blocks: list[ir.Block]
    columns: dict[int, list[ir.Block]] | None = None
    total_cols: int = 1


def _group_into_zones(blocks: Sequence[ir.Block]) -> list[_Zone]:
    """Group slide blocks into contiguous vertical zones (including scoped multi-column bands)."""
    zones: list[_Zone] = []
    i = 0
    n = len(blocks)

    while i < n:
        b = blocks[i]
        if b.column is not None:
            band_blocks: list[ir.Block] = [b]
            cur_rg = getattr(b, "row_group", None)
            last_col = b.column
            j = i + 1
            while j < n and blocks[j].column is not None:
                nxt = blocks[j]
                nxt_rg = getattr(nxt, "row_group", None)
                if cur_rg is not None and nxt_rg is not None:
                    if nxt_rg != cur_rg:
                        break
                else:
                    # Without explicit row_group, column indices are non-decreasing within a single band
                    if nxt.column < last_col:
                        break
                band_blocks.append(nxt)
                last_col = nxt.column
                j += 1

            col_map: dict[int, list[ir.Block]] = {}
            for bb in band_blocks:
                c_idx = bb.column or 0
                col_map.setdefault(c_idx, []).append(bb.model_copy(update={"column": None}))

            max_col = max(col_map.keys()) + 1 if col_map else 2
            explicit_cols = max((getattr(bb, "total_columns", None) or 0) for bb in band_blocks)
            total_cols = max(2, max_col, explicit_cols)
            zones.append(_Zone(kind="columns", blocks=band_blocks, columns=col_map, total_cols=total_cols))
            i = j
            continue

        if isinstance(b, (ir.Heading, ir.Paragraph, ir.BulletList)):
            txt_blocks: list[ir.Block] = [b]
            j = i + 1
            while j < n and blocks[j].column is None and isinstance(blocks[j], (ir.Heading, ir.Paragraph, ir.BulletList)):
                txt_blocks.append(blocks[j])
                j += 1
            zones.append(_Zone(kind="text", blocks=txt_blocks))
            i = j
            continue

        if isinstance(b, ir.KPIGroup):
            zones.append(_Zone(kind="kpis", blocks=[b]))
        elif isinstance(b, ir.CardGrid):
            zones.append(_Zone(kind="cards", blocks=[b]))
        elif isinstance(b, ir.Table):
            zones.append(_Zone(kind="table", blocks=[b]))
        elif isinstance(b, ir.Callout):
            zones.append(_Zone(kind="callout", blocks=[b]))
        elif isinstance(b, ir.Quote):
            zones.append(_Zone(kind="quote", blocks=[b]))
        elif isinstance(b, ir.Chart):
            zones.append(_Zone(kind="chart", blocks=[b]))
        elif isinstance(b, ir.Image):
            zones.append(_Zone(kind="image", blocks=[b]))
        elif isinstance(b, ir.VectorGraphic):
            zones.append(_Zone(kind="vector", blocks=[b]))
        elif isinstance(b, ir.Freeform):
            zones.append(_Zone(kind="freeform", blocks=[b]))
        else:
            zones.append(_Zone(kind="text", blocks=[b]))
        i += 1

    return zones


def _text_blocks_to_paras(blocks: Sequence[ir.Block], brand: BrandProfile, is_first_zone: bool = True) -> list[lm.Para]:
    paras: list[lm.Para] = []
    head_size = min(15.0, max(13.0, brand.fonts.sizes.heading * 0.75))
    body_size = min(12.5, max(10.5, brand.fonts.sizes.body * 0.78))
    for idx, b in enumerate(blocks):
        if isinstance(b, ir.Heading):
            paras.append(
                lm.Para(
                    runs=_ir_runs_to_truns(b.runs, brand, is_heading=True, size_pt=head_size),
                    space_before=6 if (idx > 0 or not is_first_zone) else 0,
                    space_after=3,
                )
            )
        elif isinstance(b, ir.Paragraph):
            paras.extend(
                _runs_to_paras(
                    b.runs,
                    brand,
                    size_pt=body_size,
                    align=b.align,
                    space_before=2,
                    space_after=3,
                )
            )
        elif isinstance(b, ir.BulletList):
            for it in b.items:
                paras.append(
                    lm.Para(
                        runs=_ir_runs_to_truns(it.runs, brand, is_heading=False, size_pt=body_size),
                        level=it.level,
                        bullet="1." if b.ordered else "•",
                        bullet_color=brand.roles.primary,
                        space_before=1.5,
                        space_after=1.5,
                    )
                )
    return paras


def _zone_height_bounds(
    zone: _Zone,
    rect: Rect,
    brand: BrandProfile,
    num_zones: int,
) -> tuple[int, int, int]:
    """Return (min_h, desired_h, max_h) in EMU for a vertical zone."""
    inch = EMU_PER_INCH
    if zone.kind == "kpis":
        min_h = int(1.15 * inch)
        des_h = min(int(1.45 * inch), int(rect.h * 0.30))
        max_h = int(rect.h * 0.34)
        return min_h, max(min_h, des_h), max(min_h, max_h)

    if zone.kind == "callout":
        c: ir.Callout = zone.blocks[0]  # type: ignore[assignment]
        txt_len = len(c.title) + len(ir.runs_text(c.runs))
        min_h = int(0.68 * inch)
        des_h = int(0.82 * inch) if txt_len < 220 else int(1.00 * inch)
        max_h = int(1.20 * inch)
        return min_h, des_h, max_h

    if zone.kind == "cards":
        cg: ir.CardGrid = zone.blocks[0]  # type: ignore[assignment]
        n_cards = len(cg.cards)
        max_chars = max(
            (
                len(c.title)
                + len(ir.runs_text(c.body))
                + sum(len(ir.runs_text(it.runs)) for it in c.bullets)
                for c in cg.cards
            ),
            default=80,
        )
        has_bullets = any(bool(c.bullets) for c in cg.cards)

        # 4 cards in a 3-zone slide or process variant uses 1x4 single row
        if n_cards == 4 and (num_zones >= 3 or cg.variant in ("process", "timeline")):
            return int(1.65 * inch), int(2.10 * inch), int(2.45 * inch)
        if n_cards >= 4:
            # 2x2 or 2x3 grid
            return int(2.40 * inch), int(3.35 * inch), int(4.25 * inch)
        # 1..3 cards in a single row
        if num_zones >= 3 and not has_bullets:
            return int(1.00 * inch), int(1.25 * inch), int(1.55 * inch)
        if has_bullets or max_chars > 220:
            return int(1.60 * inch), int(2.25 * inch), int(3.40 * inch)
        return int(1.00 * inch), int(1.30 * inch), int(2.10 * inch)

    if zone.kind == "table":
        tbl: ir.Table = zone.blocks[0]  # type: ignore[assignment]
        n_rows = max(1, len(tbl.rows))
        min_h = int(max(1.20, n_rows * 0.33) * inch)
        des_h = int(min(2.70, max(1.50, n_rows * 0.44)) * inch)
        max_h = int(min(4.20, max(2.20, n_rows * 0.60)) * inch)
        return min_h, des_h, max_h

    if zone.kind == "columns":
        cols = zone.columns or {}
        max_col_items = max((len(v) for v in cols.values()), default=1)
        all_callouts = all(isinstance(b, ir.Callout) for v in cols.values() for b in v)
        if all_callouts and max_col_items >= 2:
            min_h = int(max_col_items * 0.84 * inch)
            des_h = int(max_col_items * 1.08 * inch)
            max_h = int(max_col_items * 1.35 * inch)
            return min_h, des_h, max_h
        if all_callouts and max_col_items == 1:
            return int(0.95 * inch), int(1.25 * inch), int(1.85 * inch)
        return int(1.50 * inch), int(2.40 * inch), int(4.00 * inch)

    if zone.kind == "text":
        paras = _text_blocks_to_paras(zone.blocks, brand)
        est_h = _estimate_paras_height_emu(paras, rect.w) + int(0.08 * inch)
        min_h = max(int(0.40 * inch), int(est_h * 0.72))
        des_h = max(int(0.50 * inch), est_h)
        max_h = max(des_h, int(rect.h * 0.85))
        return min_h, des_h, max_h

    if zone.kind == "quote":
        return int(0.95 * inch), int(1.45 * inch), int(2.50 * inch)

    return int(1.40 * inch), int(2.40 * inch), int(4.20 * inch)


def _allocate_zone_heights(
    zones: list[_Zone],
    avail_h: int,
    rect: Rect,
    brand: BrandProfile,
) -> list[int]:
    if not zones:
        return []
    n = len(zones)
    bounds = [_zone_height_bounds(z, rect, brand, n) for z in zones]
    mins = [b[0] for b in bounds]
    des = [b[1] for b in bounds]
    maxs = [b[2] for b in bounds]

    sum_des = sum(des)
    sum_min = sum(mins)

    if sum_des <= avail_h:
        heights = list(des)
        extra = avail_h - sum_des
        # Distribute extra breathing room up to each zone's max_h
        headrooms = [max(0, maxs[i] - heights[i]) for i in range(n)]
        tot_headroom = sum(headrooms)
        if tot_headroom > 0 and extra > 0:
            take = min(extra, tot_headroom)
            for i in range(n):
                if headrooms[i] > 0:
                    heights[i] += int(take * (headrooms[i] / tot_headroom))
        return heights

    if sum_min <= avail_h:
        span = max(1, sum_des - sum_min)
        ratio = (avail_h - sum_min) / span
        return [int(mins[i] + ratio * (des[i] - mins[i])) for i in range(n)]

    # Very tight canvas: scale minimums proportionally
    scale = avail_h / max(1, sum_min)
    return [max(int(0.35 * EMU_PER_INCH), int(m * scale)) for m in mins]


def _layout_block_stack(blocks: Sequence[ir.Block], rect: Rect, brand: BrandProfile, sm: lm.SlideModel) -> None:
    """Stack blocks vertically inside the given rectangle with zero content drop."""
    _layout_compound_zones(blocks, rect, brand, sm)


def _layout_compound_zones(
    blocks: Sequence[ir.Block],
    rect: Rect,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    """Render any sequence of IR blocks into vertical and multi-column zones without dropping blocks."""
    if not blocks or rect.w <= 0 or rect.h <= 0:
        return

    zones = _group_into_zones(blocks)
    if not zones:
        return

    n_zones = len(zones)
    if n_zones == 1:
        zone_gutter = 0
    elif n_zones == 2:
        zone_gutter = min(brand.design.gutter, int(0.16 * EMU_PER_INCH))
    else:
        zone_gutter = min(brand.design.gutter, int(0.12 * EMU_PER_INCH))

    avail_h = max(int(0.5 * EMU_PER_INCH), rect.h - zone_gutter * (n_zones - 1))
    heights = _allocate_zone_heights(zones, avail_h, rect, brand)

    cur_y = rect.y
    for idx, (zone, zh) in enumerate(zip(zones, heights)):
        # Clamp to rect.bottom so no zone ever exceeds the container
        if idx == n_zones - 1 and sum(heights) + zone_gutter * (n_zones - 1) > rect.h:
            zh = max(int(0.35 * EMU_PER_INCH), rect.bottom - cur_y)
        z_rect = Rect(x=rect.x, y=cur_y, w=rect.w, h=zh)
        _render_zone(zone, z_rect, brand, sm, num_zones=n_zones, is_first=(idx == 0))
        cur_y += zh + zone_gutter


def _render_zone(
    zone: _Zone,
    rect: Rect,
    brand: BrandProfile,
    sm: lm.SlideModel,
    num_zones: int = 1,
    is_first: bool = True,
) -> None:
    if zone.kind == "kpis":
        kb: ir.KPIGroup = zone.blocks[0]  # type: ignore[assignment]
        if not kb.items:
            return
        n_kpis = len(kb.items)
        gutter = min(brand.design.gutter, int(0.18 * EMU_PER_INCH))
        tile_w = (rect.w - gutter * (n_kpis - 1)) // n_kpis
        for i, item in enumerate(kb.items):
            tx = rect.x + i * (tile_w + gutter)
            _build_kpi_card(item, tx, rect.y, tile_w, rect.h, brand, sm, is_standalone=False)

    elif zone.kind == "cards":
        cg: ir.CardGrid = zone.blocks[0]  # type: ignore[assignment]
        force_1x4 = len(cg.cards) == 4 and (num_zones >= 3 or cg.variant in ("process", "timeline"))
        _layout_card_grid_in_rect(cg, rect, brand, sm, is_standalone=False, force_single_row=force_1x4)

    elif zone.kind == "table":
        tbl: ir.Table = zone.blocks[0]  # type: ignore[assignment]
        _build_table_element(tbl, rect.x, rect.y, rect.w, rect.h, brand, sm)

    elif zone.kind == "callout":
        cal: ir.Callout = zone.blocks[0]  # type: ignore[assignment]
        _build_callout_element(cal, rect.x, rect.y, rect.w, rect.h, brand, sm)

    elif zone.kind == "quote":
        q: ir.Quote = zone.blocks[0]  # type: ignore[assignment]
        _build_quote_element(q, rect.x, rect.y, rect.w, rect.h, brand, sm)

    elif zone.kind == "chart":
        ch: ir.Chart = zone.blocks[0]  # type: ignore[assignment]
        _build_chart_element(ch.spec, rect.x, rect.y, rect.w, rect.h, brand, sm)

    elif zone.kind == "image":
        img: ir.Image = zone.blocks[0]  # type: ignore[assignment]
        aspect = (img.w / max(img.h, 1.0)) if (img.w and img.h) else 1.5
        img_h = min(rect.h, int(rect.w / aspect))
        img_w = min(rect.w, int(img_h * aspect))
        ix = rect.x + (rect.w - img_w) // 2
        iy = rect.y + (rect.h - img_h) // 2
        sm.elements.append(
            lm.ImageEl(
                name="Content Image",
                x=ix,
                y=iy,
                w=img_w,
                h=img_h,
                mime=img.mime,
                b64=img.b64,
            )
        )

    elif zone.kind == "vector":
        vec: ir.VectorGraphic = zone.blocks[0]  # type: ignore[assignment]
        sm.elements.append(
            lm.SvgEl(
                name="Vector Graphic",
                x=rect.x,
                y=rect.y,
                w=rect.w,
                h=rect.h,
                svg=vec.svg,
                recolor=brand.roles.primary if vec.icon else None,
            )
        )

    elif zone.kind == "freeform":
        ff: ir.Freeform = zone.blocks[0]  # type: ignore[assignment]
        _build_freeform_element(ff, rect, brand, sm)

    elif zone.kind == "columns":
        _render_column_band_zone(zone, rect, brand, sm)

    elif zone.kind == "text":
        paras = _text_blocks_to_paras(zone.blocks, brand, is_first_zone=is_first)
        if paras:
            _autofit_paras_to_box(paras, rect.w, rect.h, min_font_pt=8.5)
            sm.elements.append(
                lm.TextEl(
                    name="Content Text",
                    x=rect.x,
                    y=rect.y,
                    w=rect.w,
                    h=rect.h,
                    paras=paras,
                    valign="top",
                )
            )


def _render_column_band_zone(
    zone: _Zone,
    rect: Rect,
    brand: BrandProfile,
    sm: lm.SlideModel,
) -> None:
    cols_map = zone.columns or {}
    n_cols = max(2, zone.total_cols)
    col_gutter = min(brand.design.gutter, int(0.16 * EMU_PER_INCH))
    avail_w = max(int(1.0 * EMU_PER_INCH), rect.w - col_gutter * (n_cols - 1))

    # Detect V-Cycle 3-column layout where middle column is a compact center spine
    col0_blocks = cols_map.get(0, [])
    col1_blocks = cols_map.get(1, [])
    col2_blocks = cols_map.get(2, [])
    is_vcycle_spine = (
        n_cols == 3
        and len(col1_blocks) == 1
        and len(col0_blocks) >= 2
        and len(col2_blocks) >= 2
    )

    if is_vcycle_spine:
        w0 = int(avail_w * 0.41)
        w2 = int(avail_w * 0.41)
        w1 = avail_w - w0 - w2
        col_widths = [w0, w1, w2]
    else:
        cw = avail_w // n_cols
        col_widths = [cw] * n_cols

    cur_x = rect.x
    for c_idx in range(n_cols):
        cw = col_widths[c_idx]
        c_rect = Rect(x=cur_x, y=rect.y, w=cw, h=rect.h)
        cur_x += cw + col_gutter

        c_blocks = cols_map.get(c_idx, [])
        if not c_blocks:
            continue

        if is_vcycle_spine and c_idx == 1 and isinstance(c_blocks[0], ir.Callout):
            spine_h = min(c_rect.h, int(1.30 * EMU_PER_INCH))
            spine_y = c_rect.y + (c_rect.h - spine_h) // 2
            _build_callout_element(c_blocks[0], c_rect.x, spine_y, c_rect.w, spine_h, brand, sm)
        elif all(isinstance(b, ir.Callout) for b in c_blocks):
            k = len(c_blocks)
            item_gutter = min(int(0.09 * EMU_PER_INCH), c_rect.h // max(8, k * 6)) if k > 1 else 0
            item_h = (c_rect.h - item_gutter * (k - 1)) // k
            for idx, cb in enumerate(c_blocks):
                cy = c_rect.y + idx * (item_h + item_gutter)
                _build_callout_element(cb, c_rect.x, cy, c_rect.w, item_h, brand, sm)  # type: ignore[arg-type]
        else:
            _layout_compound_zones(c_blocks, c_rect, brand, sm)


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
            cw = col_widths[min(ci, len(col_widths) - 1)]
            base_size = 11.5 if is_head else 10.5
            cell_tone = getattr(cell, "tone", "none") or "none"
            has_inline_tone_run = any((getattr(r, "tone", "none") or "none") not in ("none", "") for r in cell.runs)
            default_cell_color = (
                tone_color(cell_tone, brand, default=brand.roles.text)
                if (cell_tone not in ("none", "") and (len(cell.runs) == 1 or not has_inline_tone_run))
                else brand.roles.text
            )

            runs: list[lm.TRun] = []
            for r_idx, r in enumerate(cell.runs):
                r_tone = getattr(r, "tone", "none") or "none"
                is_badge = bool(getattr(r, "badge", False))
                if r_tone not in ("none", ""):
                    r_color = tone_color(r_tone, brand, default=default_cell_color)
                elif r.emphasis:
                    r_color = brand.roles.primary
                else:
                    r_color = default_cell_color

                txt = r.t
                if is_badge and r_idx + 1 < len(cell.runs):
                    nxt_txt = cell.runs[r_idx + 1].t
                    if txt and not txt.endswith(" ") and nxt_txt and not nxt_txt.startswith(" "):
                        txt = f"{txt} "

                runs.append(
                    lm.TRun(
                        text=txt,
                        font=brand.fonts.heading.family if is_head else brand.fonts.body.family,
                        size=base_size,
                        color=r_color,
                        bold=bool(is_head or is_total or cell.bold or r.b or is_badge),
                        italic=r.i,
                        underline=r.u,
                        strike=r.strike,
                        href=r.href,
                        sup=r.sup,
                        sub=r.sub,
                    )
                )

            cell_paras = [lm.Para(runs=runs, align=cell.align)]
            pad_x = 120000 if is_head else 100000
            pad_y = 85000 if is_head else 65000
            _autofit_paras_to_box(
                cell_paras,
                max(int(0.4 * EMU_PER_INCH), cw - 2 * pad_x),
                max(int(0.2 * EMU_PER_INCH), row_height - 2 * pad_y),
                min_font_pt=8.5,
            )

            # Background fill
            fill = None
            if is_head:
                fill = brand.roles.surface
            elif ri % 2 == 1:
                fill = brand.roles.surface
            elif is_total:
                fill = brand.roles.surface

            border_bottom = brand.roles.border if (is_head or brand.design.table_style != "minimal") else None
            border_bottom_w = 1.5 if is_head else 0.75

            row_cells.append(
                lm.CellEl(
                    paras=cell_paras,
                    fill=fill,
                    colspan=cell.colspan,
                    rowspan=cell.rowspan,
                    border_bottom=border_bottom,
                    border_bottom_w=border_bottom_w,
                    is_header=is_head,
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


def _runs_to_paras(
    runs: Sequence[ir.Run],
    brand: BrandProfile,
    is_heading: bool = False,
    size_pt: float | None = None,
    align: Literal["left", "center", "right", "justify"] = "left",
    space_before: float = 0.0,
    space_after: float = 3.0,
    default_color: str | None = None,
) -> list[lm.Para]:
    """Convert a list of IR runs (splitting on newline runs) into positioned paragraphs."""
    groups: list[list[ir.Run]] = [[]]
    for r in runs:
        if r.t == "\n":
            if groups[-1]:
                groups.append([])
        else:
            groups[-1].append(r)

    paras: list[lm.Para] = []
    for g in groups:
        if not g:
            continue
        truns = _ir_runs_to_truns(g, brand, is_heading=is_heading, size_pt=size_pt, default_color=default_color)
        if truns:
            paras.append(
                lm.Para(
                    runs=truns,
                    align=align,
                    space_before=space_before if not paras else 1.5,
                    space_after=space_after,
                )
            )
    return paras


def _ir_runs_to_truns(
    runs: Sequence[ir.Run],
    brand: BrandProfile,
    is_heading: bool = False,
    size_pt: float | None = None,
    default_color: str | None = None,
) -> list[lm.TRun]:
    truns: list[lm.TRun] = []
    for idx, r in enumerate(runs):
        t = _ir_run_to_trun(r, brand, is_heading=is_heading, size_pt=size_pt, default_color=default_color)
        if getattr(r, "badge", False) and idx + 1 < len(runs):
            nxt_t = runs[idx + 1].t
            if t.text and not t.text.endswith(" ") and nxt_t and not nxt_t.startswith(" "):
                t.text = f"{t.text} "
        truns.append(t)
    return truns


def _ir_run_to_trun(
    r: ir.Run,
    brand: BrandProfile,
    is_heading: bool = False,
    size_pt: float | None = None,
    default_color: str | None = None,
) -> lm.TRun:
    r_tone = getattr(r, "tone", "none") or "none"
    is_badge = bool(getattr(r, "badge", False))
    base_col = default_color if default_color is not None else (brand.roles.primary if is_heading else brand.roles.text)

    if r_tone not in ("none", ""):
        color = tone_color(r_tone, brand, default=base_col)
    elif r.emphasis:
        color = brand.roles.primary
    else:
        color = base_col

    font = brand.fonts.heading.family if is_heading else brand.fonts.body.family
    if size_pt is not None:
        size = size_pt
    else:
        size = min(15.0, brand.fonts.sizes.heading) if is_heading else min(12.5, brand.fonts.sizes.body)

    return lm.TRun(
        text=r.t,
        font=font,
        size=size,
        color=color,
        bold=bool(r.b or is_heading or is_badge),
        italic=r.i,
        underline=r.u,
        strike=r.strike,
        href=r.href,
        sup=r.sup,
        sub=r.sub,
    )
