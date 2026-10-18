"""Automated corpus LayoutScorecard and Playwright/PPTX visual comparison test suite."""
from __future__ import annotations

from pathlib import Path
import pytest

from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.extract.renderer import snapshot_html
from htmlppt.layout import model as lm
from htmlppt.evaluate import (
    DeckScorecard,
    LayoutScorecard,
    SlideScorecard,
    evaluate_deck,
    evaluate_html_file,
    format_scorecard_table,
    generate_visual_comparison_report,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
CORPUS_HTML_FILES = sorted(CORPUS_DIR.glob("*.html"))

THEME_MATRIX = [
    ("ocean", True),
    ("ocean", False),
    ("slate", True),
    ("slate", False),
    ("forest", True),
    ("forest", False),
    ("burgundy", True),
    ("burgundy", False),
    ("sunset", True),
    ("sunset", False),
    ("mono", True),
    ("mono", False),
]

# Cache DOM snapshots per HTML file so Playwright only snapshots each corpus file once per test session
_SNAPSHOT_CACHE: dict[Path, dict] = {}


def _get_cached_snapshot(html_path: Path) -> dict:
    resolved = html_path.resolve()
    if resolved not in _SNAPSHOT_CACHE:
        _SNAPSHOT_CACHE[resolved] = snapshot_html(path=resolved)
    return _SNAPSHOT_CACHE[resolved]


def test_corpus_directory_has_unbiased_html_files():
    """Verify the benchmark corpus directory exists and contains at least one real HTML deck."""
    assert CORPUS_DIR.exists(), f"Corpus directory {CORPUS_DIR} does not exist"
    assert len(CORPUS_HTML_FILES) >= 1, f"No .html files found in {CORPUS_DIR}"


@pytest.mark.parametrize("html_path", CORPUS_HTML_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("palette,dark", THEME_MATRIX, ids=[f"{p}-{'dark' if d else 'light'}" for p, d in THEME_MATRIX])
def test_corpus_html_layout_scorecard(html_path: Path, palette: str, dark: bool):
    """Evaluate every HTML file in backend/tests/corpus/ across dark/light themes and brand palettes."""
    snap = _get_cached_snapshot(html_path)
    scorecard = evaluate_html_file(
        html_path,
        palette=palette,
        preset="corporate",
        dark=dark,
        snap=snap,
    )

    print("\n" + format_scorecard_table([scorecard]))
    for s in scorecard.slides:
        if s.overflow_details or s.collision_details or s.missing_phrases or s.oversized_font_details:
            print(
                f"  Slide {s.slide_number} ({s.archetype}) '{s.title[:40]}': "
                f"ret={s.content_retention_ratio * 100:.1f}%, "
                f"ovf={s.overflow_count}, col={s.collision_count}, "
                f"neat={s.typography_neatness_score:.1f}"
            )
            for d in s.overflow_details:
                print(f"    [OVERFLOW] {d}")
            for d in s.collision_details:
                print(f"    [COLLISION] {d}")
            for d in s.oversized_font_details:
                print(f"    [OVERSIZED] {d}")
            if s.missing_phrases:
                print(f"    [MISSING ({len(s.missing_phrases)})] {s.missing_phrases[:5]}")

    assert isinstance(scorecard, LayoutScorecard)
    assert scorecard.slide_count >= 1
    assert scorecard.content_retention_ratio >= 0.95, (
        f"{html_path.name} ({palette}/{'dark' if dark else 'light'}) content retention "
        f"{scorecard.content_retention_ratio:.2%} < 95%. Missing: {scorecard.missing_phrases[:8]}"
    )
    assert scorecard.overflow_count == 0, (
        f"{html_path.name} ({palette}/{'dark' if dark else 'light'}) had {scorecard.overflow_count} "
        f"text overflows: {scorecard.overflow_details}"
    )
    assert scorecard.collision_count == 0, (
        f"{html_path.name} ({palette}/{'dark' if dark else 'light'}) had {scorecard.collision_count} "
        f"bounding-box collisions: {scorecard.collision_details}"
    )
    assert scorecard.typography_neatness_score >= 85.0, (
        f"{html_path.name} ({palette}/{'dark' if dark else 'light'}) typography neatness "
        f"{scorecard.typography_neatness_score:.1f} < 85.0. Oversized: {scorecard.oversized_font_details}"
    )


def test_scorecard_detects_overflow_collision_and_oversized_typography():
    """Verify LayoutScorecard accurately flags overflows, collisions, missing content, and oversized fonts."""
    snap = {
        "root": {
            "tag": "div",
            "data": {"hpSlide": "1"},
            "box": {"x": 0, "y": 0, "w": 1280, "h": 720},
            "kids": [
                {"tag": "h2", "runs": [{"t": "Slide Header Title"}], "box": {"x": 40, "y": 40, "w": 800, "h": 50}},
                {"tag": "p", "runs": [{"t": "Retained card phrase here"}], "box": {"x": 40, "y": 150, "w": 400, "h": 40}},
                {"tag": "p", "runs": [{"t": "Dropped secret requirement phrase"}], "box": {"x": 40, "y": 220, "w": 400, "h": 40}},
            ],
        }
    }
    deck = ir.Deck(slides=[ir.Slide(id="s1", title="Slide Header Title", source_index=0)])

    # Construct a SlideModel with:
    # 1) A tiny height TextEl that overflows
    # 2) Two sibling Card ShapeEls that collide
    # 3) A card TextEl with oversized 20pt text
    sm = lm.SlideModel(
        id="s1",
        archetype="cards",
        number=1,
        elements=[
            lm.TextEl(
                name="Slide Title",
                x=int(0.5 * EMU_PER_INCH),
                y=int(0.4 * EMU_PER_INCH),
                w=int(10 * EMU_PER_INCH),
                h=int(0.8 * EMU_PER_INCH),
                placeholder="title",
                paras=[lm.Para(runs=[lm.TRun(text="Slide Header Title", font="Calibri", size=24.0, color="FFFFFF", bold=True)])],
            ),
            lm.ShapeEl(
                name="Card Alpha",
                x=int(1.0 * EMU_PER_INCH),
                y=int(2.0 * EMU_PER_INCH),
                w=int(4.0 * EMU_PER_INCH),
                h=int(2.0 * EMU_PER_INCH),
                geom="roundRect",
                fill="1E293B",
            ),
            lm.ShapeEl(
                name="Card Beta",
                x=int(3.0 * EMU_PER_INCH),  # overlaps Card Alpha horizontally by 2 inches!
                y=int(2.0 * EMU_PER_INCH),
                w=int(4.0 * EMU_PER_INCH),
                h=int(2.0 * EMU_PER_INCH),
                geom="roundRect",
                fill="1E293B",
            ),
            lm.TextEl(
                name="Card Content Alpha",
                x=int(1.1 * EMU_PER_INCH),
                y=int(2.1 * EMU_PER_INCH),
                w=int(1.0 * EMU_PER_INCH),
                h=int(20 * EMU_PER_PT),  # tiny 20pt height box that will overflow
                paras=[
                    lm.Para(
                        runs=[
                            lm.TRun(
                                text="Retained card phrase here with a very long description that wraps across many lines",
                                font="Calibri",
                                size=20.0,  # oversized (> 16pt) inside a card!
                                color="FFFFFF",
                            )
                        ]
                    )
                ],
            ),
        ],
    )
    deck_model = lm.DeckModel(
        width=int(13.333 * EMU_PER_INCH),
        height=int(7.5 * EMU_PER_INCH),
        slides=[sm],
    )

    sc = evaluate_deck(snap, deck, deck_model, deck_name="synthetic_bad.html")
    assert sc.content_retention_ratio < 1.0
    assert "Dropped secret requirement phrase" in sc.missing_phrases
    assert sc.overflow_count >= 1
    assert sc.collision_count >= 1
    assert sc.oversized_font_count >= 1
    assert sc.typography_neatness_score < 70.0


def test_visual_comparison_report_generation(tmp_path: Path):
    """Verify generate_visual_comparison_report produces a self-contained HTML report with side-by-side PNGs."""
    report_path = tmp_path / "corpus_report.html"
    scorecards = generate_visual_comparison_report(
        corpus_dir=CORPUS_DIR,
        output_html=report_path,
        palette="ocean",
        preset="corporate",
        dark=True,
    )

    assert len(scorecards) == len(CORPUS_HTML_FILES)
    assert report_path.exists()
    html_content = report_path.read_text(encoding="utf-8")
    assert "HTML-PPT Unbiased Corpus Evaluation Report" in html_content
    assert "data:image/png;base64," in html_content
    assert "Original HTML Slide" in html_content
    assert "Generated PPTX Slide" in html_content

    # Also verify that the .pptx file was written alongside the report
    for html_file in CORPUS_HTML_FILES:
        expected_pptx = tmp_path / f"{html_file.stem}.pptx"
        assert expected_pptx.exists(), f"Expected generated PPTX at {expected_pptx}"
        assert expected_pptx.stat().st_size > 10_000
