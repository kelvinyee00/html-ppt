# ADR 0002: Zero-Drop Compound Layout, Proportional Auto-Fit Typography, and Semantic Tone Fidelity

## Status
Accepted

## Context
Evaluating `ai_automotive_motor_ecu_v_cycle.html` revealed that converting rich multi-section HTML slides into native PowerPoint presentations suffered from four systemic issues:
1. **Silent Content Discarding**: Single-block archetype handlers (`_layout_cards_slide`, `_layout_table_slide`, `_layout_quote_slide`, `_layout_chart_focus_slide`) rendered only the first matching block and dropped all other blocks on the slide. Furthermore, `_layout_block_stack` lacked renderers for `Callout`, `CardGrid`, `KPIGroup`, `Quote`, and `VectorGraphic`, causing all 7 V-Cycle step callouts, secondary card grids, air-gap certification banners, and 4th KPI tiles (`"8 wks"`) to vanish.
2. **Oversized, Non-Adaptive Typography**: `engine.py` imported `fit_font_size` and `estimate_text_height_emu` from `textfit.py` but never called them. Card titles were hardcoded to `20pt` and card/body text to `16pt`, causing text inside KPI tiles and cards to overflow bounding boxes by >50%.
3. **Header & Footer Bleed**: Pre-title `.kicker` elements were misclassified as `Heading(level=4)` and rendered in the slide body at `20pt`; subtitles over 140–180 characters were rejected and dumped into body paragraphs; and HTML `.slide-footer` elements were extracted as `16pt` body text.
4. **Loss of Semantic Styling**: Status badges (`tag-bad` / `tag-good`), step badges, card deliverable footers (`step-deliverable`), and semantic border accents lost their communicative tone when flattened into plain text runs.

## Decisions

1. **Zero-Drop Universal Zone Layout**:
   - `_layout_block_stack` must render every `ir.Block` variant (`Callout`, `CardGrid`, `KPIGroup`, `Table`, `Chart`, `Quote`, `BulletList`, `Paragraph`, `Heading`, `Image`, `VectorGraphic`, `Freeform`) using content-aware vertical height budgets.
   - Archetype handlers must never discard secondary blocks; compound slides keep all zones on a single slide when their compact height budget fits `content_rect.h`, only splitting in `overflow.py` when the combined minimum height exceeds the canvas.
   - Horizontal multi-column rows (2-column and 3-column bands) are scoped to the contiguous blocks in that row rather than hijacking full-width blocks above or below them.
   - `try_kpis` retains all sibling tiles in a matched KPI grid and supports time/engineering units (`wks`, `mos`, `hrs`, `days`, `yrs`, `ms`, `s`, `x`). Styled CTA `<button>` elements inside slides are preserved while navigation chrome outside slides is skipped.

2. **Component-Proportional Typography & Active Auto-Fit**:
   - Component text uses a proportional typographic scale (compact `14–16pt` card/callout titles, `11–13pt` card/callout/table body and bullets, `28–36pt` KPI values with `11–12.5pt` labels and `10–11pt` deltas).
   - Every bounded text container (Slide Header, KPI tile, Card, Callout, Table cell, Stacked block) actively runs `fit_font_size` and `estimate_text_height_emu` so dense text scales down cleanly (down to `min_size_pt`) with zero bounding-box overflow and accurate multi-line vertical advancement.
   - `SlideGeometry` dynamically sizes `title_rect` based on the presence and wrapped line count of `kicker`, `title`, and `subtitle`.

3. **Semantic Tone Preservation Across Themes**:
   - Inline runs, badges, card deliverables, cards, and callouts preserve semantic tones (`positive`, `negative`, `warning`, `primary`, `muted`) extracted from source colors, classes, and borders, mapping them dynamically to the active `BrandProfile` color roles (`roles.positive`, `roles.negative`, `roles.primary`, `roles.surface`) so regenerating a deck under any theme or dark/light mode retains its visual meaning.

4. **Unbiased Corpus Evaluation Harness (`LayoutScorecard` & Visual Comparison)**:
   - `backend/tests/corpus/` holds real user-supplied HTML decks (starting with `ai_automotive_motor_ecu_v_cycle.html`) without synthetic bias.
   - `pytest` statically enforces the `LayoutScorecard` (content retention ratio, zero text overflow, zero unplanned box collisions, and typographic neatness) across all `.html` files in `backend/tests/corpus/`.
   - An evaluation runner generates side-by-side Playwright HTML vs. rendered slide/PPTX visual comparison reports.
