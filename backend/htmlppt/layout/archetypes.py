"""Slide layout archetype classification."""
from __future__ import annotations

from htmlppt.analyze import ir


def select_archetype(slide: ir.Slide) -> str:
    """Determine the visual layout archetype for a slide."""
    if slide.layout:
        return slide.layout
    if slide.kind == "title":
        return "title"
    if slide.kind == "section":
        return "section"
    if slide.kind == "closing":
        return "closing"

    blocks = slide.blocks
    if not blocks:
        return "blank"

    # Single block checks
    if len(blocks) == 1:
        b0 = blocks[0]
        if isinstance(b0, ir.Quote):
            return "quote"
        if isinstance(b0, ir.KPIGroup):
            return "kpi_grid"
        if isinstance(b0, ir.Chart):
            return "chart_focus"
        if isinstance(b0, ir.Table):
            return "table"
        if isinstance(b0, ir.CardGrid):
            return "cards"
        if isinstance(b0, ir.Freeform):
            return "freeform"

    # Multi-column check: only use two_column when blocks are genuinely 2-column
    # without full-width heavy blocks or 3-column bands
    cols = {b.column for b in blocks if b.column is not None}
    has_full_width_heavy = any(
        isinstance(b, (ir.KPIGroup, ir.CardGrid, ir.Table, ir.Chart)) and b.column is None
        for b in blocks
    )
    if cols == {0, 1} and not has_full_width_heavy and all(b.column in (0, 1) for b in blocks):
        return "two_column"

    # Composite checks
    types = [b.type for b in blocks]
    if "kpis" in types:
        return "kpi_grid"
    if "chart" in types and ("bullets" in types or "paragraph" in types or "heading" in types):
        return "chart_plus_text"
    if "table" in types and "cards" not in types:
        return "table"
    if "cards" in types:
        return "cards"
    if "table" in types:
        return "table"
    if "quote" in types:
        return "quote"
    if "freeform" in types:
        return "freeform"

    return "bullets"

