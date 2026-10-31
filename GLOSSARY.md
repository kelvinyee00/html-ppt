# HTML-PPT Domain Glossary

Converts rich HTML presentation decks into native, brand-compliant PowerPoint (.pptx) presentations and live previews while preserving visual hierarchy, context, and aesthetic elegance.

## Language

**Archetype**:
The visual layout template assigned to a slide based on its content structure (e.g., `kpi_grid`, `cards`, `table`, `two_column`, `quote`, `title`).
_Avoid_: Template, layout style, page type

**BrandProfile**:
The complete specification of colors, typography hierarchy, margins, and design treatments applied during layout.
_Avoid_: Theme config, style settings

**DeckModel**:
The fully positioned, coordinate-resolved geometric representation of a presentation deck ready for rendering to PPTX or preview canvas.
_Avoid_: Output JSON, layout tree

**Curated Palette**:
A curated set of harmonious theme colors (accents, dark backgrounds, surfaces) used when no uploaded brand profile exists.
_Avoid_: Default colors, fallback theme

**CardGrid**:
A structured group of related content blocks displayed as distinct visual cards with consistent padding, elevation, or borders.
_Avoid_: Box group, panel list

**KPIGroup**:
A row or grid of prominent numeric metrics with associated descriptive labels and optional delta/sentiment indicators.
_Avoid_: Stat tiles, number boxes

**Kicker**:
A compact, uppercase category or context label placed immediately above a slide or card title to establish topical hierarchy.
_Avoid_: Eyebrow, pre-title, super-title

**Slide Header**:
The top structural zone of a slide comprising an optional `Kicker`, the primary slide title, an optional subtitle, and a decorative accent treatment, sized dynamically to its text content.
_Avoid_: Top bar, title block, page header

**Compound Slide**:
A slide containing multiple distinct content zones stacked vertically (such as a `KPIGroup` above a `CardGrid`, or a `CardGrid` above a `Table` or banner) budgeted to share a single slide canvas without overflowing.
_Avoid_: Mixed slide, multi-widget page

**Layout Scorecard**:
The automated quality evaluation of a rendered `DeckModel` against its source HTML, measuring content retention, text-fit compliance (zero overflow), bounding-box non-collision, and typographic proportion.
_Avoid_: Linter output, test score

**Semantic Tone**:
The communicative intent of an inline badge, metric delta, card accent, or callout (`positive`, `negative`, `warning`, `primary`, `info`) preserved from the source HTML and mapped to the active `BrandProfile` color roles across any theme.
_Avoid_: Hardcoded hex color, raw CSS color

**Callout**:
A visually distinct boxed banner, step card, or highlight block containing a title, badge, or concise body text with a `Semantic Tone` border or surface treatment.
_Avoid_: Generic div, alert box

**Direct DOM Mapper**:
The browser-layout-first engine that translates Chromium's computed DOM geometry (`x`, `y`, `w`, `h`), borders, backgrounds, and text runs directly into native `DeckModel` elements while re-theming surfaces and `Semantic Tone` colors to the active `BrandProfile`.
_Avoid_: Heuristic re-layout, CSS parser


