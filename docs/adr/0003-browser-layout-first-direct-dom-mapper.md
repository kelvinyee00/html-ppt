# ADR 0003: Browser-Layout-First Direct DOM Mapper (`layout_mode="dom"`)

## Status
Accepted (partially supersedes the primary deck-mode layout flow of [ADR-0001](./0001-archetype-elegance-and-palette-contrast.md) and [ADR-0002](./0002-zero-drop-compound-layout-and-semantic-tone-fidelity.md))

## Context
[ADR-0001](./0001-archetype-elegance-and-palette-contrast.md) and [ADR-0002](./0002-zero-drop-compound-layout-and-semantic-tone-fidelity.md) improved the rule-based `Archetype` synthesis engine (`backend/htmlppt/layout/engine.py`), which classified HTML subtrees into intermediate `ir.Block` variants and re-synthesized slide coordinates from scratch. While `Archetype` synthesis works well for unstyled prose documents (`page` mode) and explicit user layout overrides, re-synthesizing coordinates for pre-composed 16:9 HTML slide decks (`deck` mode) discarded the author's CSS Flexbox/Grid topology (e.g., 3-column V-Cycle diagrams with center hubs, side-bordered cards, inline icon boxes, and gradient CTA banners).

## Decisions

1. **Direct DOM Mapper as Default for 16:9 Deck Slides (`layout_mode="dom"`)**:
   - When `brand.design.layout_mode == "dom"` (the default) and an `ir.Slide` has a captured `dom_node` that fits its 16:9 canvas without an explicit `Archetype` override (`slide.layout is None`) or overflow split (`not slide.continued`), `layout_slide` dispatches to `layout_slide_from_dom` (`backend/htmlppt/layout/direct_mapper.py`).
   - `layout_slide_from_dom` maps Chromium's computed bounding boxes `(x, y, w, h)` directly into `DeckModel` shapes, text boxes, tables, charts, images, and SVGs (`DomNode` -> `DirectMapperContext` -> emitters).

2. **Preserved Clauses vs. Superseded Clauses of ADR-0001 & ADR-0002**:
   - **Superseded for `layout_mode="dom"`**: Heuristic `CardGrid` 2×2 re-wrapping, `KPIGroup` 35% height re-allocation, `two_column` balancing (ADR-0001 §§2–4), and `SlideGeometry` header re-sizing / `_layout_block_stack` vertical zone re-synthesis (ADR-0002 §1, §2.4) no longer override the browser's computed coordinates when `layout_mode == "dom"`.
   - **Retained as Fallback (`layout_mode="archetype"`)**: `ARCHETYPE_HANDLERS` and all ADR-0001/0002 layout rules remain active when `brand.design.layout_mode == "archetype"`, when a user selects an explicit `Archetype` override (`slide.layout`), when an overcrowded slide is split into continuation slides (`slide.continued`), or when `slide.blocks` are structurally mutated after segmentation.
   - **Shared Across Both Modes**: Luminous dark-mode WCAG contrast (`ensure_contrast`), unified `Semantic Tone` resolution (`ToneMapper` + `classifier.color_tone`/`cls_tone`/`node_tone` + `engine.tone_color`), chart normalization (`normalize_chart`), strict paragraph auto-fit, and native PowerPoint linear gradient rendering in `shapes.py` apply across the pipeline.

3. **DOM-Aware Overflow & IR Edit Synchronization**:
   - `handle_overflow` checks whether a captured `dom_node` fits its 16:9 canvas (`_dom_slide_fits_canvas`) before consulting IR block-height estimates; if a DOM slide exceeds the 16:9 canvas vertically, `dom_node` is cleared so the slide splits cleanly via `Archetype` continuation slides.
   - `ir.Slide` serializes `dom_node` and `charts` across JSON round-trips, syncs in-place `slide.title` edits onto the DOM title run, and falls back to `Archetype` layout if `slide.blocks` are structurally mutated (`blocks_signature`).
