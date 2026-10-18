# Unbiased HTML Benchmark Corpus (`backend/tests/corpus/`)

This directory holds real-world HTML presentation decks and dashboards used for unbiased quality evaluation of the `htmlppt` conversion pipeline.

> **Important:** Do **not** add synthetic, hand-crafted micro-fixtures to this directory. Unit test fixtures belong in `backend/tests/fixtures/`. Only real, full-featured HTML decks should be placed here so the `LayoutScorecard` and visual comparison harness evaluate authentic layouts without synthetic bias.

---

## 1. Adding HTML Files to the Corpus

Drop any `.html` file directly into `backend/tests/corpus/`:

```powershell
Copy-Item path\to\your_deck.html backend\tests\corpus\
```

Both the `pytest` scorecard suite and the visual comparison CLI automatically discover every `*.html` file in `backend/tests/corpus/`.

---

## 2. Running the Static `LayoutScorecard` Suite (`pytest`)

Run the automated corpus test suite across themes (`dark=True` / `dark=False`) and curated palettes (`ocean`, `slate`, `forest`):

```powershell
$env:PYTHONIOENCODING="utf-8"
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_corpus_scorecard.py -s
```

The `-s` flag prints a formatted summary table showing the `DeckScorecard` and per-slide diagnostics for every `.html` file and theme combination.

### Scorecard Metrics & Targets

| Metric | Description | Target |
| :--- | :--- | :--- |
| **`content_retention_ratio`** | Fraction of meaningful source HTML text leaves/phrases inside slides preserved in the rendered `DeckModel` (`TextEl`, `ShapeEl.paras`, `TableEl` cells). Excludes outer navigation chrome (`.deck-header`, `<nav>`). | `>= 0.90` (ideal `>= 0.95`) |
| **`overflow_count`** | Number of text containers (`TextEl`, `ShapeEl`, or `TextEl` inside a parent card/KPI/callout shape) whose wrapped text height (`estimate_text_height_emu`) exceeds `1.08 * el.h`. | `0` |
| **`collision_count`** | Number of out-of-canvas bounds violations, `Slide Title` vs. content overlaps, or unintentional sibling card/KPI/callout/table bounding-box collisions. | `0` |
| **`typography_neatness_score`** | `0..100` score penalizing oversized body/card/table fonts (`> 16pt` on non-title, non-KPI text inside cards/callouts/tables), overflows, collisions, and dropped content. | `>= 85.0` |
| **`overall_score`** | `0..100` weighted composite of content retention, zero overflow, zero collision, and typography neatness. | `>= 85.0` |

---

## 3. Generating the Visual Comparison Report (`corpus_report.html`)

To generate a self-contained, side-by-side visual comparison report (**Original HTML Slide via Playwright PNG** on the left vs. **Generated PPTX Slide via Rendered PNG** on the right, alongside `.pptx` outputs and per-slide `SlideScorecard` badges):

### Via the `htmlppt` CLI:

```powershell
.\backend\.venv\Scripts\python.exe -m htmlppt.cli evaluate `
  --corpus-dir backend/tests/corpus `
  --output corpus_report.html `
  --palette ocean `
  --preset corporate `
  --dark
```

### Or directly via the `htmlppt.evaluate` module:

```powershell
.\backend\.venv\Scripts\python.exe -m htmlppt.evaluate `
  --corpus-dir backend/tests/corpus `
  --output corpus_report.html `
  --palette ocean `
  --preset corporate `
  --dark
```

Open the resulting `corpus_report.html` in any browser to inspect every slide side-by-side along with its scorecard metrics, overflow diagnostics, and collision checks.
