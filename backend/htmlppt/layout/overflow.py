"""Overflow detection and slide auto-splitting."""
from __future__ import annotations

import copy
from htmlppt.analyze import ir
from htmlppt.brand.model import BrandProfile


def handle_overflow(slide: ir.Slide, brand: BrandProfile) -> list[ir.Slide]:
    """Check if slide content exceeds capacity; split into continuation slides if needed.

    The first part keeps the original slide id; continuation parts get fresh ids so
    they can be addressed individually (reorder / delete / layout override).
    """
    parts = _split(slide, brand)
    if len(parts) > 1:
        for part in parts:
            part.dom_node = None
    for part in parts[1:]:
        part.id = ir._id()
    return parts


def _estimate_block_min_height_in(block: ir.Block) -> float:
    """Estimate the minimum compact vertical height (in inches) required by an IR block."""
    if isinstance(block, ir.KPIGroup):
        return 1.25
    if isinstance(block, ir.CardGrid):
        n_cards = len(block.cards)
        if n_cards == 0:
            return 0.0
        if n_cards <= 2:
            return 1.25
        if n_cards <= 4:
            return 1.65
        return 2.80
    if isinstance(block, ir.Table):
        return 0.38 * len(block.rows)
    if isinstance(block, ir.Chart):
        return 2.60
    if isinstance(block, ir.Callout):
        return 0.85 if block.column is not None else 0.65
    if isinstance(block, ir.BulletList):
        return 0.28 * len(block.items)
    if isinstance(block, (ir.Heading, ir.Paragraph)):
        return 0.35
    if isinstance(block, ir.Quote):
        return 1.10
    if isinstance(block, (ir.Image, ir.VectorGraphic)):
        if getattr(block, "icon", False) or getattr(block, "is_logo", False):
            return 0.50
        return 1.60
    if isinstance(block, ir.Freeform):
        return 2.00
    return 0.50


def _group_blocks_into_vertical_zones(blocks: list[ir.Block]) -> list[list[ir.Block]]:
    """Group slide blocks into vertical zones.

    Contiguous blocks with column is not None (and matching row_group) form a single
    horizontal multi-column band zone; each full-width block (column is None) forms
    its own vertical zone.
    """
    zones: list[list[ir.Block]] = []
    cur_band: list[ir.Block] = []
    cur_row_group: int | None = None

    for b in blocks:
        if b.column is not None:
            if cur_band and b.row_group == cur_row_group:
                cur_band.append(b)
            else:
                if cur_band:
                    zones.append(cur_band)
                cur_band = [b]
                cur_row_group = b.row_group
        else:
            if cur_band:
                zones.append(cur_band)
                cur_band = []
                cur_row_group = None
            zones.append([b])

    if cur_band:
        zones.append(cur_band)
    return zones


def _estimate_zone_height_in(zone: list[ir.Block]) -> float:
    """Estimate vertical height (in inches) of a vertical zone."""
    if not zone:
        return 0.0
    if len(zone) == 1 and zone[0].column is None:
        return _estimate_block_min_height_in(zone[0])

    col_heights: dict[int, float] = {}
    for b in zone:
        col_idx = b.column if b.column is not None else 0
        col_heights[col_idx] = col_heights.get(col_idx, 0.0) + _estimate_block_min_height_in(b)
    return max(col_heights.values(), default=0.0)


def _split(slide: ir.Slide, brand: BrandProfile) -> list[ir.Slide]:
    max_bullets = brand.design.max_bullets or 6

    # 1. Check if single bullet list exceeds max_bullets
    for block in slide.blocks:
        if isinstance(block, ir.BulletList) and len(block.items) > max_bullets and len(slide.blocks) == 1:
            chunks = [block.items[k:k + max_bullets] for k in range(0, len(block.items), max_bullets)]
            slides_out: list[ir.Slide] = []
            for idx, chunk in enumerate(chunks):
                new_slide = copy.deepcopy(slide)
                new_slide.title = f"{slide.title} (cont.)" if idx > 0 else slide.title
                new_slide.continued = idx > 0
                new_slide.blocks = [ir.BulletList(items=chunk, ordered=block.ordered, src=block.src)]
                slides_out.append(new_slide)
            return slides_out

    # 2. Height-budgeted Compound Slide retention
    zones = _group_blocks_into_vertical_zones(slide.blocks)
    if len(zones) <= 1:
        return [slide]

    zone_gutter_in = 0.20
    max_height_in = 5.35
    total_height_in = sum(_estimate_zone_height_in(z) for z in zones) + zone_gutter_in * (len(zones) - 1)
    total_charts = sum(1 for b in slide.blocks if isinstance(b, ir.Chart))
    heavy_blocks = [b for b in slide.blocks if isinstance(b, (ir.Table, ir.Chart, ir.CardGrid))]
    has_large_table = any(isinstance(b, ir.Table) and len(b.rows) > 8 for b in slide.blocks)

    must_split = (
        (total_height_in > max_height_in)
        or (total_charts >= 2)
        or (has_large_table and len(heavy_blocks) >= 2)
    )
    if not must_split:
        return [slide]

    slides_out = []
    cur_blocks: list[ir.Block] = []
    cur_height = 0.0
    cur_charts = 0
    cur_heavy = 0
    cur_large_table = False

    for zone in zones:
        z_h = _estimate_zone_height_in(zone)
        z_charts = sum(1 for b in zone if isinstance(b, ir.Chart))
        z_heavy = sum(1 for b in zone if isinstance(b, (ir.Table, ir.Chart, ir.CardGrid)))
        z_large_table = any(isinstance(b, ir.Table) and len(b.rows) > 8 for b in zone)

        can_flush = bool(cur_blocks) and (cur_heavy > 0 or cur_height >= 1.2 or z_heavy == 0)
        would_overflow = (
            (cur_height + zone_gutter_in + z_h > max_height_in)
            or (cur_charts + z_charts >= 2)
            or (cur_large_table and z_heavy > 0)
            or (z_large_table and cur_heavy > 0)
        )

        if can_flush and would_overflow:
            new_slide = copy.deepcopy(slide)
            new_slide.title = f"{slide.title} (cont.)" if slides_out else slide.title
            new_slide.continued = bool(slides_out)
            new_slide.blocks = cur_blocks
            slides_out.append(new_slide)
            cur_blocks = list(zone)
            cur_height = z_h
            cur_charts = z_charts
            cur_heavy = z_heavy
            cur_large_table = z_large_table
        else:
            if cur_blocks:
                cur_height += zone_gutter_in + z_h
            else:
                cur_height = z_h
            cur_blocks.extend(zone)
            cur_charts += z_charts
            cur_heavy += z_heavy
            cur_large_table = cur_large_table or z_large_table

    if cur_blocks:
        new_slide = copy.deepcopy(slide)
        new_slide.title = f"{slide.title} (cont.)" if slides_out else slide.title
        new_slide.continued = bool(slides_out)
        new_slide.blocks = cur_blocks
        slides_out.append(new_slide)

    return slides_out

