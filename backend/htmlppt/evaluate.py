"""Unbiased HTML Corpus Evaluator: LayoutScorecard and Visual Comparison Harness.

Provides:
- `SlideScorecard` and `DeckScorecard` (`LayoutScorecard`) for static verification
  of content retention, text-fit (zero overflow), bounding-box non-collision,
  and typographic neatness across themes and palettes.
- `generate_visual_comparison_report` for rendering side-by-side Playwright HTML
  vs. generated PPTX/SlideModel PNG visual comparison reports (`corpus_report.html`).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import html
import re
from pathlib import Path
from typing import Sequence

from playwright.sync_api import sync_playwright

from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.analyze.classifier import _all
from htmlppt.analyze.segmenter import build_deck, find_slide_nodes
from htmlppt.brand.presets import apply_preset, palette_brand
from htmlppt.extract.renderer import _SCRIPT, _resolve_assets, _scroll_through, snapshot_html
from htmlppt.layout import model as lm
from htmlppt.layout.engine import layout_deck
from htmlppt.layout.textfit import estimate_paras_height_emu, estimate_text_height_emu
from htmlppt.render.pptx_writer import write_pptx


# Navigation/chrome classes outside slides to exclude from content retention
_CHROME_CLS_RE = re.compile(
    r"\b(deck-header|deck-nav|nav-controls|nav-btn|slide-counter|slide-pills|slide-pill|toolbar|navbar)\b",
    re.I,
)
_DECORATIVE_SHAPE_NAME_RE = re.compile(
    r"\b(accent|underline|divider|border|line|badge|pill|icon|dot)\b",
    re.I,
)
_OUTER_CONTAINER_NAME_RE = re.compile(
    r"^(Column Container|Hero Container)\b",
    re.I,
)


@dataclass
class SlideScorecard:
    """Quality scorecard for a single rendered slide."""

    slide_number: int
    slide_id: str
    archetype: str
    title: str = ""
    source_index: int | None = None
    content_retention_ratio: float = 1.0
    retained_phrases: int = 0
    total_phrases: int = 0
    missing_phrases: list[str] = field(default_factory=list)
    overflow_count: int = 0
    overflow_details: list[str] = field(default_factory=list)
    collision_count: int = 0
    collision_details: list[str] = field(default_factory=list)
    oversized_font_count: int = 0
    oversized_font_details: list[str] = field(default_factory=list)
    typography_neatness_score: float = 100.0
    overall_score: float = 100.0


@dataclass
class DeckScorecard:
    """Quality scorecard (`LayoutScorecard`) for an entire converted deck."""

    deck_name: str
    palette: str = "ocean"
    preset: str = "corporate"
    dark: bool = True
    slide_count: int = 0
    source_slide_count: int = 0
    content_retention_ratio: float = 1.0
    retained_phrases: int = 0
    total_phrases: int = 0
    missing_phrases: list[str] = field(default_factory=list)
    overflow_count: int = 0
    overflow_details: list[str] = field(default_factory=list)
    collision_count: int = 0
    collision_details: list[str] = field(default_factory=list)
    oversized_font_count: int = 0
    oversized_font_details: list[str] = field(default_factory=list)
    typography_neatness_score: float = 100.0
    overall_score: float = 100.0
    slides: list[SlideScorecard] = field(default_factory=list)


# Alias matching domain glossary terminology
LayoutScorecard = DeckScorecard


# ---------------------------------------------------------------- Text Normalization & Extraction


def _norm_text(text: str) -> str:
    """Normalize text for case-insensitive, punctuation-tolerant matching."""
    t = text.lower()
    t = t.replace("\u00a0", " ").replace("–", "-").replace("—", "-").replace("−", "-")
    t = re.sub(r"[•▸▪‣⁃✓✔✗✘🔒🛡️⚡📐🧠🏛️📄📽️📥◀▶➔⚙️]", " ", t)
    t = re.sub(r"[^\w\s€$£¥%+.\-/]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm_text(text))


def _is_meaningful_phrase(text: str) -> bool:
    """Return True if text leaf contains at least 2 alphanumeric characters."""
    cleaned = text.strip()
    if not cleaned:
        return False
    alnum = re.findall(r"[A-Za-z0-9]", cleaned)
    return len(alnum) >= 2


def _is_chrome_node(node: dict, inside_slide: bool) -> bool:
    tag = (node.get("tag") or "").lower()
    if tag in ("nav", "script", "style", "noscript", "hr", "img", "canvas", "svg"):
        return True
    cls = (node.get("cls") or "") + " " + (node.get("eid") or "")
    if _CHROME_CLS_RE.search(cls):
        return True
    if not inside_slide and tag == "header":
        return True
    return False


def extract_source_phrases_from_node(node: dict, inside_slide: bool = True) -> list[str]:
    """Extract all meaningful text leaf phrases from a DOM snapshot subtree."""
    phrases: list[str] = []

    def _walk(n: dict) -> None:
        if _is_chrome_node(n, inside_slide=inside_slide):
            return
        # Skip .slide-footer chrome inside slides if it's purely slide numbering/confidentiality chrome
        cls = (n.get("cls") or "").lower()
        tag = (n.get("tag") or "").lower()
        if tag == "footer" or "slide-footer" in cls:
            return

        if tag == "list":
            for it in n.get("items", []) or []:
                raw = "".join(r.get("t", "") for r in (it.get("runs") or []))
                for line in raw.split("\n"):
                    line_s = line.strip()
                    if _is_meaningful_phrase(line_s):
                        phrases.append(line_s)
            return

        if tag == "table":
            for row in n.get("rows", []) or []:
                for cell in row.get("cells", []) or []:
                    raw = "".join(r.get("t", "") for r in (cell.get("runs") or []))
                    for line in raw.split("\n"):
                        line_s = line.strip()
                        if _is_meaningful_phrase(line_s):
                            phrases.append(line_s)
            cap = (n.get("caption") or "").strip()
            if _is_meaningful_phrase(cap):
                phrases.append(cap)
            return

        if n.get("runs"):
            raw = "".join(r.get("t", "") for r in (n.get("runs") or []))
            for line in raw.split("\n"):
                line_s = line.strip()
                if _is_meaningful_phrase(line_s):
                    phrases.append(line_s)
            return

        for k in n.get("kids", []) or []:
            _walk(k)

    _walk(node)
    return phrases


def _extract_slide_model_text(sm: lm.SlideModel) -> str:
    """Collect all rendered text from a SlideModel."""
    chunks: list[str] = []

    def _collect_paras(paras: Sequence[lm.Para] | None) -> None:
        if not paras:
            return
        for p in paras:
            txt = "".join(r.text for r in p.runs).strip()
            if txt:
                chunks.append(txt)

    def _collect_el(el: lm.Element) -> None:
        if isinstance(el, lm.TextEl):
            _collect_paras(el.paras)
        elif isinstance(el, lm.ShapeEl):
            _collect_paras(el.paras)
        elif isinstance(el, lm.TableEl):
            for row in el.rows:
                for cell in row:
                    if not cell.merged:
                        _collect_paras(cell.paras)
        elif isinstance(el, lm.ChartEl):
            if el.spec.title:
                chunks.append(el.spec.title)
            if el.spec.x_title:
                chunks.append(el.spec.x_title)
            if el.spec.y_title:
                chunks.append(el.spec.y_title)
            chunks.extend(el.spec.categories)
            for s in el.spec.series:
                if s.name:
                    chunks.append(s.name)
        elif isinstance(el, lm.SvgEl):
            for m in re.findall(r">([^<]+)<", el.svg or ""):
                if m.strip():
                    chunks.append(m.strip())
        elif isinstance(el, lm.GroupEl):
            for child in el.children:
                _collect_el(child)

    for element in sm.elements:
        _collect_el(element)
    if sm.notes:
        chunks.append(sm.notes)
    return "\n".join(chunks)


def _phrase_is_retained(phrase: str, norm_rendered: str, rendered_word_set: set[str]) -> bool:
    """Check whether a source phrase is preserved in the rendered text."""
    norm_p = _norm_text(phrase)
    if not norm_p:
        return True
    if norm_p in norm_rendered:
        return True
    tokens = _word_tokens(phrase)
    if not tokens:
        return True
    if len(tokens) <= 2:
        return all(t in rendered_word_set for t in tokens)
    matched = sum(1 for t in tokens if t in rendered_word_set)
    # Require numeric tokens in short phrases to also match
    num_tokens = [t for t in tokens if any(ch.isdigit() for ch in t)]
    if num_tokens and not any(nt in rendered_word_set for nt in num_tokens):
        return False
    return (matched / len(tokens)) >= 0.75


# ---------------------------------------------------------------- Overflow, Collision & Typography Checks


def _iter_elements_flat(elements: Sequence[lm.Element]) -> list[lm.Element]:
    out: list[lm.Element] = []
    for el in elements:
        out.append(el)
        if isinstance(el, lm.GroupEl):
            out.extend(_iter_elements_flat(el.children))
    return out


def _estimate_paras_height_emu(paras: Sequence[lm.Para], avail_w_emu: int) -> int:
    """Estimate wrapped height in EMU for a sequence of Para objects."""
    safe_w = max(int(0.2 * EMU_PER_INCH), avail_w_emu)
    return estimate_paras_height_emu(paras, safe_w, default_line_spacing=1.15)


def _is_decorative_shape(el: lm.ShapeEl) -> bool:
    if el.geom == "line":
        return True
    if el.h <= int(10 * EMU_PER_PT) or el.w <= int(10 * EMU_PER_PT):
        return True
    if _DECORATIVE_SHAPE_NAME_RE.search(el.name or ""):
        return True
    return False


def _is_outer_wrapper_shape(el: lm.ShapeEl) -> bool:
    return bool(_OUTER_CONTAINER_NAME_RE.search(el.name or ""))


def _rect_contains(outer: lm._El, inner: lm._El, tol_emu: int = int(4 * EMU_PER_PT)) -> bool:
    return (
        inner.x >= outer.x - tol_emu
        and inner.y >= outer.y - tol_emu
        and (inner.x + inner.w) <= (outer.x + outer.w) + tol_emu
        and (inner.y + inner.h) <= (outer.y + outer.h) + tol_emu
    )


def _rect_intersection(a: lm._El, b: lm._El) -> tuple[int, int]:
    ix = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    iy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    return ix, iy


def _evaluate_slide_geometry_and_typography(
    sm: lm.SlideModel,
    deck_width: int,
    deck_height: int,
    retained_phrases: int,
    total_phrases: int,
    missing_phrases: list[str],
    source_index: int | None,
) -> SlideScorecard:
    flat_els = _iter_elements_flat(sm.elements)

    # Identify Slide Title text element
    title_el = next(
        (
            el
            for el in flat_els
            if isinstance(el, lm.TextEl) and (el.placeholder == "title" or el.name == "Slide Title")
        ),
        None,
    )
    slide_title_str = ""
    if title_el and title_el.paras:
        slide_title_str = " ".join("".join(r.text for r in p.runs).strip() for p in title_el.paras if p.runs).strip()

    # Identify Card / KPI / Callout background ShapeEls (excluding decorative accents & outer column wrappers)
    component_shapes = [
        el
        for el in flat_els
        if isinstance(el, lm.ShapeEl)
        and not _is_decorative_shape(el)
        and not _is_outer_wrapper_shape(el)
    ]

    # 1. Overflow check
    overflow_details: list[str] = []
    for el in flat_els:
        if isinstance(el, (lm.TextEl, lm.ShapeEl)) and el.paras:
            inset = getattr(el, "inset", 0) or 0
            avail_w = max(int(0.2 * EMU_PER_INCH), el.w - 2 * inset)
            est_h = _estimate_paras_height_emu(el.paras, avail_w)

            # Check against element's own bounding box (with 8% tolerance)
            own_overflow = est_h > int(el.h * 1.08)

            # Also check if TextEl sits inside a parent Card/KPI/Callout ShapeEl and overflows it
            parent_overflow = False
            parent_shape_name = ""
            if isinstance(el, lm.TextEl):
                cx = el.x + el.w // 2
                cy = el.y + el.h // 2
                for sh in component_shapes:
                    if sh.x <= cx <= sh.x + sh.w and sh.y <= cy <= sh.y + sh.h:
                        # Height available from el.y to bottom of parent shape
                        avail_in_parent = (sh.y + sh.h) - el.y
                        if est_h > int(avail_in_parent * 1.08):
                            parent_overflow = True
                            parent_shape_name = sh.name or "Parent Shape"
                            break

            if own_overflow or parent_overflow:
                preview_txt = " ".join("".join(r.text for r in p.runs).strip() for p in el.paras)[:48]
                box_h_pt = el.h / EMU_PER_PT
                est_h_pt = est_h / EMU_PER_PT
                target_label = el.name or el.kind
                if parent_overflow and not own_overflow:
                    target_label = f"{target_label} (in {parent_shape_name})"
                overflow_details.append(
                    f"{target_label} ['{preview_txt}...']: est {est_h_pt:.1f}pt > box {box_h_pt:.1f}pt"
                )
        elif isinstance(el, lm.TableEl) and el.rows and el.col_widths and el.row_heights:
            for ri, row in enumerate(el.rows):
                row_h = el.row_heights[min(ri, len(el.row_heights) - 1)]
                for ci, cell in enumerate(row):
                    if not cell.paras or cell.merged:
                        continue
                    col_w = el.col_widths[min(ci, len(el.col_widths) - 1)] * max(1, cell.colspan)
                    cell_h = row_h * max(1, cell.rowspan)
                    avail_cw = max(int(0.3 * EMU_PER_INCH), col_w - 200000)
                    est_ch = _estimate_paras_height_emu(cell.paras, avail_cw)
                    if est_ch > int(cell_h * 1.08):
                        preview_txt = " ".join("".join(r.text for r in p.runs).strip() for p in cell.paras)[:48]
                        overflow_details.append(
                            f"{el.name or 'Table'} R{ri + 1}C{ci + 1} ['{preview_txt}...']: "
                            f"est {est_ch / EMU_PER_PT:.1f}pt > cell {cell_h / EMU_PER_PT:.1f}pt"
                        )

    # 2. Collision check
    collision_details: list[str] = []
    tol_overlap = int(2 * EMU_PER_PT)

    # 2a. Canvas bounds check
    max_x = int(deck_width * 1.01)
    max_y = int(deck_height * 1.01)
    for el in flat_els:
        if el.x < -tol_overlap or el.y < -tol_overlap or (el.x + el.w) > max_x or (el.y + el.h) > max_y:
            collision_details.append(
                f"Out of canvas bounds: {el.name or el.kind} at "
                f"(x={el.x / EMU_PER_PT:.1f}pt, y={el.y / EMU_PER_PT:.1f}pt, "
                f"r={(el.x + el.w) / EMU_PER_PT:.1f}pt, b={(el.y + el.h) / EMU_PER_PT:.1f}pt)"
            )

    # 2b. Slide Title vs. content ShapeEl, TableEl, or ChartEl overlap check
    if title_el is not None and sm.archetype not in ("title", "section", "closing"):
        for el in flat_els:
            if el is title_el:
                continue
            if isinstance(el, lm.ShapeEl) and _is_decorative_shape(el):
                continue
            if isinstance(el, (lm.ShapeEl, lm.TableEl, lm.ChartEl)):
                ix, iy = _rect_intersection(title_el, el)
                if ix > tol_overlap and iy > tol_overlap:
                    collision_details.append(
                        f"Slide Title overlaps with {el.name or el.kind} "
                        f"by ({ix / EMU_PER_PT:.1f}pt x {iy / EMU_PER_PT:.1f}pt)"
                    )

    # 2c. Sibling card/KPI/callout/table/chart shapes collision check
    content_boxes: list[lm.Element] = [
        el
        for el in flat_els
        if (isinstance(el, lm.ShapeEl) and not _is_decorative_shape(el))
        or isinstance(el, (lm.TableEl, lm.ChartEl))
    ]
    for i in range(len(content_boxes)):
        a = content_boxes[i]
        for j in range(i + 1, len(content_boxes)):
            b = content_boxes[j]
            # Exclude parent-child containment (e.g., cards/tables inside a Column Container or Hero Container,
            # or a badge shape inside a parent Card/Callout)
            a_is_wrapper = isinstance(a, lm.ShapeEl) and _is_outer_wrapper_shape(a)
            b_is_wrapper = isinstance(b, lm.ShapeEl) and _is_outer_wrapper_shape(b)
            if (a_is_wrapper and _rect_contains(a, b)) or (b_is_wrapper and _rect_contains(b, a)):
                continue
            # Also exclude small inner sub-shapes (e.g. icon box or pill badge) contained inside a larger card/callout
            a_area = max(1, a.w * a.h)
            b_area = max(1, b.w * b.h)
            if _rect_contains(a, b) and b_area < 0.35 * a_area:
                continue
            if _rect_contains(b, a) and a_area < 0.35 * b_area:
                continue

            ix, iy = _rect_intersection(a, b)
            if ix > tol_overlap and iy > tol_overlap:
                collision_details.append(
                    f"Sibling collision: '{a.name or a.kind}' and '{b.name or b.kind}' "
                    f"overlap by ({ix / EMU_PER_PT:.1f}pt x {iy / EMU_PER_PT:.1f}pt)"
                )

    # 3. Typography neatness check
    # Check non-title, non-KPI text inside a card, callout, or table cell for size > 16.0pt
    oversized_details: list[str] = []
    for el in flat_els:
        if isinstance(el, lm.TableEl):
            for ri, row in enumerate(el.rows):
                for ci, cell in enumerate(row):
                    if cell.merged:
                        continue
                    for p in cell.paras:
                        for r in p.runs:
                            if r.text.strip() and r.size > 16.0:
                                oversized_details.append(
                                    f"Table '{el.name}' cell ({ri},{ci}) '{r.text.strip()[:25]}' size={r.size:.1f}pt > 16.0pt"
                                )
                                break
        elif isinstance(el, (lm.TextEl, lm.ShapeEl)) and el.paras:
            if el is title_el or getattr(el, "placeholder", None) == "title":
                continue
            name_l = (el.name or "").lower()
            if name_l in ("title content", "section title", "closing text", "slide title"):
                continue

            is_kpi = "kpi" in name_l
            is_card_or_callout = any(k in name_l for k in ("card", "callout", "step", "pillar", "banner"))
            if not is_card_or_callout and not is_kpi and isinstance(el, lm.TextEl):
                cx = el.x + el.w // 2
                cy = el.y + el.h // 2
                for sh in component_shapes:
                    sh_name = (sh.name or "").lower()
                    if sh.x <= cx <= sh.x + sh.w and sh.y <= cy <= sh.y + sh.h:
                        if "kpi" in sh_name:
                            is_kpi = True
                        else:
                            is_card_or_callout = True
                        break

            if is_kpi:
                # For KPI elements, paragraph 0 is the prominent metric number; subsequent paras are label/delta
                for pi, p in enumerate(el.paras[1:], start=1):
                    for r in p.runs:
                        if r.text.strip() and r.size > 16.0:
                            oversized_details.append(
                                f"KPI label/delta '{r.text.strip()[:25]}' size={r.size:.1f}pt > 16.0pt"
                            )
                            break
            elif is_card_or_callout or "content text" in name_l:
                for pi, p in enumerate(el.paras):
                    for r in p.runs:
                        if r.text.strip() and r.size > 16.0:
                            oversized_details.append(
                                f"{el.name or 'Component'} '{r.text.strip()[:25]}' size={r.size:.1f}pt > 16.0pt"
                            )
                            break

    retention_ratio = (retained_phrases / total_phrases) if total_phrases > 0 else 1.0
    overflow_count = len(overflow_details)
    collision_count = len(collision_details)
    oversized_count = len(oversized_details)

    neatness = (
        100.0
        - min(40.0, oversized_count * 5.0)
        - overflow_count * 15.0
        - collision_count * 15.0
        - (1.0 - retention_ratio) * 100.0
    )
    neatness = max(0.0, min(100.0, neatness))

    overflow_score = max(0.0, 100.0 - overflow_count * 25.0)
    collision_score = max(0.0, 100.0 - collision_count * 25.0)
    overall = (
        0.35 * (retention_ratio * 100.0)
        + 0.25 * overflow_score
        + 0.20 * collision_score
        + 0.20 * neatness
    )
    overall = max(0.0, min(100.0, overall))

    return SlideScorecard(
        slide_number=sm.number or 1,
        slide_id=sm.id,
        archetype=sm.archetype,
        title=slide_title_str,
        source_index=source_index,
        content_retention_ratio=round(retention_ratio, 4),
        retained_phrases=retained_phrases,
        total_phrases=total_phrases,
        missing_phrases=missing_phrases,
        overflow_count=overflow_count,
        overflow_details=overflow_details,
        collision_count=collision_count,
        collision_details=collision_details,
        oversized_font_count=oversized_count,
        oversized_font_details=oversized_details,
        typography_neatness_score=round(neatness, 2),
        overall_score=round(overall, 2),
    )


def evaluate_deck(
    snap: dict,
    deck: ir.Deck,
    deck_model: lm.DeckModel,
    *,
    deck_name: str = "deck",
    palette: str = "ocean",
    preset: str = "corporate",
    dark: bool = True,
) -> DeckScorecard:
    """Evaluate a converted DeckModel against its DOM snapshot and IR Deck."""
    root = snap.get("root") or {}
    slide_nodes = find_slide_nodes(root) if root else []

    # Map source_index -> list of source phrases
    source_phrases_by_idx: dict[int, list[str]] = {}
    if slide_nodes:
        for i, snode in enumerate(slide_nodes):
            source_phrases_by_idx[i] = extract_source_phrases_from_node(snode, inside_slide=True)
    elif root:
        source_phrases_by_idx[0] = extract_source_phrases_from_node(root, inside_slide=False)

    # Map SlideModel index -> source_index using deck.slides or position
    # Note: layout_deck runs handle_overflow on each ir.Slide, preserving slide.id on primary slide
    # or sequential order. Let's align each SlideModel to its source_index.
    id_to_source_idx: dict[str, int] = {}
    for idx, ir_s in enumerate(deck.slides):
        s_idx = ir_s.source_index if ir_s.source_index is not None else idx
        id_to_source_idx[ir_s.id] = s_idx

    slide_source_indices: list[int] = []
    last_src_idx = 0
    for idx, sm in enumerate(deck_model.slides):
        if sm.id in id_to_source_idx:
            last_src_idx = id_to_source_idx[sm.id]
        elif idx < len(deck.slides):
            last_src_idx = deck.slides[idx].source_index if deck.slides[idx].source_index is not None else idx
        slide_source_indices.append(last_src_idx)

    # Combine rendered text for all SlideModels sharing the same source_index
    rendered_text_by_src_idx: dict[int, str] = {}
    for sm, src_idx in zip(deck_model.slides, slide_source_indices):
        txt = _extract_slide_model_text(sm)
        rendered_text_by_src_idx[src_idx] = (rendered_text_by_src_idx.get(src_idx, "") + "\n" + txt).strip()

    # Also build full deck rendered text
    full_deck_text = "\n".join(_extract_slide_model_text(sm) for sm in deck_model.slides)
    norm_full_deck = _norm_text(full_deck_text)
    full_deck_words = set(_word_tokens(full_deck_text))

    slide_scorecards: list[SlideScorecard] = []
    for sm, src_idx in zip(deck_model.slides, slide_source_indices):
        src_phrases = source_phrases_by_idx.get(src_idx, [])
        combined_txt = rendered_text_by_src_idx.get(src_idx, _extract_slide_model_text(sm))
        norm_comb = _norm_text(combined_txt)
        comb_words = set(_word_tokens(combined_txt))

        retained = 0
        missing: list[str] = []
        for p in src_phrases:
            if _phrase_is_retained(p, norm_comb, comb_words) or _phrase_is_retained(p, norm_full_deck, full_deck_words):
                retained += 1
            else:
                missing.append(p)

        sc = _evaluate_slide_geometry_and_typography(
            sm=sm,
            deck_width=deck_model.width,
            deck_height=deck_model.height,
            retained_phrases=retained,
            total_phrases=len(src_phrases),
            missing_phrases=missing,
            source_index=src_idx,
        )
        slide_scorecards.append(sc)

    # Deck-wide content retention across all source slide nodes
    all_src_phrases = [p for idx in sorted(source_phrases_by_idx) for p in source_phrases_by_idx[idx]]
    deck_retained = 0
    deck_missing: list[str] = []
    for p in all_src_phrases:
        if _phrase_is_retained(p, norm_full_deck, full_deck_words):
            deck_retained += 1
        else:
            deck_missing.append(p)

    total_phrases = len(all_src_phrases)
    deck_retention = (deck_retained / total_phrases) if total_phrases > 0 else 1.0

    total_overflows = sum(s.overflow_count for s in slide_scorecards)
    all_overflow_details = [f"Slide {s.slide_number}: {d}" for s in slide_scorecards for d in s.overflow_details]

    total_collisions = sum(s.collision_count for s in slide_scorecards)
    all_collision_details = [f"Slide {s.slide_number}: {d}" for s in slide_scorecards for d in s.collision_details]

    total_oversized = sum(s.oversized_font_count for s in slide_scorecards)
    all_oversized_details = [f"Slide {s.slide_number}: {d}" for s in slide_scorecards for d in s.oversized_font_details]

    deck_neatness = (
        100.0
        - min(40.0, total_oversized * 5.0)
        - total_overflows * 15.0
        - total_collisions * 15.0
        - (1.0 - deck_retention) * 100.0
    )
    deck_neatness = max(0.0, min(100.0, deck_neatness))

    overflow_score = max(0.0, 100.0 - total_overflows * 25.0)
    collision_score = max(0.0, 100.0 - total_collisions * 25.0)
    deck_overall = (
        0.35 * (deck_retention * 100.0)
        + 0.25 * overflow_score
        + 0.20 * collision_score
        + 0.20 * deck_neatness
    )
    deck_overall = max(0.0, min(100.0, deck_overall))

    return DeckScorecard(
        deck_name=deck_name,
        palette=palette,
        preset=preset,
        dark=dark,
        slide_count=len(deck_model.slides),
        source_slide_count=len(slide_nodes) or 1,
        content_retention_ratio=round(deck_retention, 4),
        retained_phrases=deck_retained,
        total_phrases=total_phrases,
        missing_phrases=deck_missing,
        overflow_count=total_overflows,
        overflow_details=all_overflow_details,
        collision_count=total_collisions,
        collision_details=all_collision_details,
        oversized_font_count=total_oversized,
        oversized_font_details=all_oversized_details,
        typography_neatness_score=round(deck_neatness, 2),
        overall_score=round(deck_overall, 2),
        slides=slide_scorecards,
    )


def evaluate_html_file(
    html_path: Path,
    *,
    palette: str = "ocean",
    preset: str = "corporate",
    dark: bool = True,
    snap: dict | None = None,
) -> DeckScorecard:
    """Snapshot, segment, layout, and evaluate a single HTML file."""
    if snap is None:
        snap = snapshot_html(path=html_path)
    deck = build_deck(snap)
    brand = palette_brand(palette_id=palette)
    brand = apply_preset(brand, preset_id=preset, dark=dark)
    deck_model = layout_deck(deck, brand)
    return evaluate_deck(
        snap=snap,
        deck=deck,
        deck_model=deck_model,
        deck_name=html_path.name,
        palette=palette,
        preset=preset,
        dark=dark,
    )


def format_scorecard_table(scorecards: Sequence[DeckScorecard]) -> str:
    """Format a summary table of DeckScorecards for terminal / pytest output."""
    lines: list[str] = []
    header = (
        f"{'Deck':<36} | {'Theme':<14} | {'Slides':<6} | "
        f"{'Retention':<10} | {'Overflows':<9} | {'Collisions':<10} | "
        f"{'Neatness':<8} | {'Overall':<7}"
    )
    sep = "-" * len(header)
    lines.append(sep)
    lines.append(header)
    lines.append(sep)
    for sc in scorecards:
        theme_str = f"{sc.palette}/{'dark' if sc.dark else 'light'}"
        ret_str = f"{sc.content_retention_ratio * 100:5.1f}%"
        lines.append(
            f"{sc.deck_name[:36]:<36} | {theme_str:<14} | "
            f"{f'{sc.slide_count}/{sc.source_slide_count}':<6} | "
            f"{ret_str:<10} | {sc.overflow_count:<9} | {sc.collision_count:<10} | "
            f"{sc.typography_neatness_score:6.1f}   | {sc.overall_score:6.1f}"
        )
    lines.append(sep)
    return "\n".join(lines)


# ---------------------------------------------------------------- SlideModel Standalone HTML/CSS Renderer


def _hex_css(color: str | None, fallback: str = "transparent") -> str:
    if not color:
        return fallback
    c = color.strip().lstrip("#")
    return f"#{c}"


def _hex_alpha_css(color: str | None, alpha: float = 1.0) -> str:
    if not color:
        return "transparent"
    c = color.strip().lstrip("#")
    if alpha >= 1.0 or len(c) != 6:
        return f"#{c}"
    r = int(c[0:2], 16)
    g = int(c[2:4], 16)
    b = int(c[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def _render_para_html(p: lm.Para, emu_to_px: float) -> str:
    def pt_px(v: float) -> str:
        return f"{v * EMU_PER_PT * emu_to_px:.2f}px"

    align = p.align or "left"
    pad_left = f"padding-left:{p.level * 32:.1f}px;" if p.level else ""
    mt = f"margin-top:{pt_px(p.space_before)};" if p.space_before else ""
    mb = f"margin-bottom:{pt_px(p.space_after)};" if p.space_after else ""
    lh = p.line_spacing if (p.line_spacing and p.line_spacing > 1.0) else 1.2

    bullet_html = ""
    if p.bullet:
        b_col = _hex_css(p.bullet_color, "inherit") if p.bullet_color else "inherit"
        bullet_html = (
            f'<span style="margin-right:7px;color:{b_col};opacity:0.88;flex-shrink:0;">'
            f"{html.escape(p.bullet)}</span>"
        )

    runs_html: list[str] = []
    for r in p.runs:
        decs: list[str] = []
        if r.underline:
            decs.append("underline")
        if r.strike:
            decs.append("line-through")
        td = " ".join(decs) if decs else "none"
        va = "super" if r.sup else ("sub" if r.sub else "baseline")
        ff = f"'{html.escape(r.font)}', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" if r.font else "inherit"
        col = _hex_css(r.color, "inherit")
        escaped_txt = html.escape(r.text).replace("\n", "<br/>")
        runs_html.append(
            f'<span style="font-family:{ff};font-size:{pt_px(r.size)};'
            f"font-weight:{700 if r.bold else 400};font-style:{'italic' if r.italic else 'normal'};"
            f'text-decoration:{td};vertical-align:{va};color:{col};">{escaped_txt}</span>'
        )

    return (
        f'<div style="text-align:{align};{pad_left}{mt}{mb}line-height:{lh};'
        f'display:flex;align-items:baseline;">'
        f'{bullet_html}<div style="flex:1;min-width:0;">{"".join(runs_html)}</div></div>'
    )


def _render_element_html(el: lm.Element, emu_to_px: float) -> str:
    def emu_px(v: int) -> str:
        return f"{v * emu_to_px:.2f}px"

    def pt_px(v: float) -> str:
        return f"{v * EMU_PER_PT * emu_to_px:.2f}px"

    valign_map = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}

    if isinstance(el, lm.TextEl):
        bg = _hex_css(el.fill) if el.fill else "transparent"
        pad = emu_px(el.inset) if el.inset > 0 else "2px"
        jc = valign_map.get(el.valign, "flex-start")
        ws = "normal" if el.wrap else "nowrap"
        inner = "".join(_render_para_html(p, emu_to_px) for p in el.paras)
        return (
            f'<div style="position:absolute;left:{emu_px(el.x)};top:{emu_px(el.y)};'
            f"width:{emu_px(el.w)};height:{emu_px(el.h)};background-color:{bg};padding:{pad};"
            f"display:flex;flex-direction:column;justify-content:{jc};overflow:hidden;"
            f'box-sizing:border-box;white-space:{ws};">{inner}</div>'
        )

    if isinstance(el, lm.ShapeEl):
        br = "0px"
        clip = ""
        if el.geom == "roundRect":
            rad = min(el.radius, 0.5) if el.radius > 0 else 0.04
            br = f"{rad * 320:.1f}px"
        elif el.geom == "ellipse":
            br = "9999px"
        elif el.geom == "homePlate":
            clip = "clip-path:polygon(0% 0%, 82% 0%, 100% 50%, 82% 100%, 0% 100%);"
        elif el.geom == "chevron":
            clip = "clip-path:polygon(0% 0%, 80% 0%, 100% 50%, 80% 100%, 0% 100%, 20% 50%);"
        elif el.geom == "triangle":
            clip = "clip-path:polygon(50% 0%, 0% 100%, 100% 100%);"

        bg_style = "background-color:transparent;"
        if el.gradient and len(el.gradient) > 1:
            stops = ", ".join(_hex_css(c) for c in el.gradient)
            bg_style = f"background-image:linear-gradient({el.gradient_angle or 90}deg, {stops});"
        elif el.fill:
            bg_style = f"background-color:{_hex_alpha_css(el.fill, el.fill_alpha if el.fill_alpha is not None else 1.0)};"

        border = (
            f"border:{pt_px(el.line_w)} {'dashed' if el.dash else 'solid'} {_hex_css(el.line)};"
            if (el.line and el.line_w > 0)
            else "border:none;"
        )
        shadow = "box-shadow:0 5px 14px rgba(0,0,0,0.18);" if el.shadow else ""
        jc = valign_map.get(el.valign, "center")
        pad = emu_px(el.inset) if el.inset > 0 else "3px"
        disp = "flex" if (el.paras and len(el.paras) > 0) else "block"
        inner = "".join(_render_para_html(p, emu_to_px) for p in (el.paras or []))
        return (
            f'<div style="position:absolute;left:{emu_px(el.x)};top:{emu_px(el.y)};'
            f"width:{emu_px(el.w)};height:{emu_px(el.h)};{bg_style}{border}"
            f"border-radius:{br};{clip}{shadow}display:{disp};flex-direction:column;"
            f'justify-content:{jc};padding:{pad};overflow:hidden;box-sizing:border-box;">{inner}</div>'
        )

    if isinstance(el, lm.TableEl):
        colgroup = ""
        if el.col_widths:
            tot_w = max(1, sum(el.col_widths))
            cols = "".join(f'<col style="width:{w / tot_w * 100:.2f}%;"/>' for w in el.col_widths)
            colgroup = f"<colgroup>{cols}</colgroup>"
        rows_html: list[str] = []
        for row in el.rows:
            cells_html: list[str] = []
            for cell in row:
                if cell.merged:
                    continue
                cs = f' colspan="{cell.colspan}"' if cell.colspan > 1 else ""
                rs = f' rowspan="{cell.rowspan}"' if cell.rowspan > 1 else ""
                bg = _hex_css(cell.fill) if cell.fill else "transparent"
                bt = (
                    f"border-top:{pt_px(cell.border_top_w)} solid {_hex_css(cell.border_top)};"
                    if (cell.border_top and cell.border_top_w > 0)
                    else ""
                )
                bb = (
                    f"border-bottom:{pt_px(cell.border_bottom_w)} solid {_hex_css(cell.border_bottom)};"
                    if (cell.border_bottom and cell.border_bottom_w > 0)
                    else "border-bottom:1px solid rgba(120,120,120,0.2);"
                )
                va = cell.valign if cell.valign in ("top", "bottom", "middle") else "middle"
                c_inner = "".join(_render_para_html(p, emu_to_px) for p in cell.paras)
                cells_html.append(
                    f'<td{cs}{rs} style="background-color:{bg};{bt}{bb}'
                    f'padding:4px 8px;vertical-align:{va};">{c_inner}</td>'
                )
            rows_html.append(f"<tr>{''.join(cells_html)}</tr>")
        return (
            f'<div style="position:absolute;left:{emu_px(el.x)};top:{emu_px(el.y)};'
            f'width:{emu_px(el.w)};height:{emu_px(el.h)};overflow:hidden;box-sizing:border-box;">'
            f'<table style="width:100%;height:100%;border-collapse:collapse;table-layout:fixed;">'
            f"{colgroup}<tbody>{''.join(rows_html)}</tbody></table></div>"
        )

    if isinstance(el, lm.ChartEl):
        spec = el.spec
        cats = spec.categories or []
        series = spec.series or []
        pal = el.palette if el.palette else ["1D4ED8", "0891B2", "10B981", "F59E0B"]
        vals = [v for s in series for v in s.values if v is not None]
        max_v = max(vals) if vals else 1.0
        max_v = max(max_v, 1.0)
        bars_html: list[str] = []
        for c_idx, cat in enumerate(cats):
            s_bars: list[str] = []
            for s_idx, s in enumerate(series):
                v = s.values[c_idx] if c_idx < len(s.values) and s.values[c_idx] is not None else 0.0
                pct = max(6.0, (float(v) / max_v) * 100.0)
                col = _hex_css(pal[s_idx % len(pal)])
                s_bars.append(
                    f'<div style="flex:1;height:{pct:.1f}%;background:{col};'
                    f'border-radius:3px 3px 0 0;min-width:3px;"></div>'
                )
            bars_html.append(
                f'<div style="flex:1;height:100%;display:flex;flex-direction:column;'
                f'justify-content:flex-end;align-items:center;min-width:0;">'
                f'<div style="width:100%;height:84%;display:flex;align-items:flex-end;'
                f'justify-content:center;gap:2px;">{"".join(s_bars)}</div>'
                f'<span style="font-size:12px;color:{_hex_css(el.text_color, "#64748b")};'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;margin-top:3px;">'
                f"{html.escape(str(cat))}</span></div>"
            )
        title_html = (
            f'<div style="font-size:14px;font-weight:600;color:{_hex_css(el.text_color, "#1a1d23")};'
            f'text-align:center;margin-bottom:5px;">{html.escape(spec.title)}</div>'
            if spec.title
            else ""
        )
        return (
            f'<div style="position:absolute;left:{emu_px(el.x)};top:{emu_px(el.y)};'
            f"width:{emu_px(el.w)};height:{emu_px(el.h)};display:flex;flex-direction:column;"
            f"padding:8px;background:rgba(240,243,246,0.25);border:1px solid rgba(180,185,195,0.35);"
            f'border-radius:6px;box-sizing:border-box;overflow:hidden;">{title_html}'
            f'<div style="flex:1;display:flex;align-items:flex-end;justify-content:space-around;'
            f'gap:8px;border-bottom:1px solid {_hex_css(el.grid_color, "#cbd5e1")};padding-bottom:4px;min-height:0;">'
            f'{"".join(bars_html)}</div></div>'
        )

    if isinstance(el, lm.ImageEl):
        return (
            f'<img src="data:{el.mime or "image/png"};base64,{el.b64}" '
            f'style="position:absolute;left:{emu_px(el.x)};top:{emu_px(el.y)};'
            f'width:{emu_px(el.w)};height:{emu_px(el.h)};object-fit:contain;" />'
        )

    if isinstance(el, lm.SvgEl):
        col = f"color:{_hex_css(el.recolor)};" if el.recolor else ""
        return (
            f'<div style="position:absolute;left:{emu_px(el.x)};top:{emu_px(el.y)};'
            f'width:{emu_px(el.w)};height:{emu_px(el.h)};{col}">{el.svg}</div>'
        )

    if isinstance(el, lm.GroupEl):
        children_html = "".join(_render_element_html(c, emu_to_px) for c in el.children)
        return (
            f'<div style="position:absolute;left:{emu_px(el.x)};top:{emu_px(el.y)};'
            f'width:{emu_px(el.w)};height:{emu_px(el.h)};">{children_html}</div>'
        )

    return ""


def render_deck_model_to_standalone_html(deck_model: lm.DeckModel, canvas_w: int = 1280, canvas_h: int = 720) -> str:
    """Render all slides in a DeckModel as 1280x720 HTML canvases for Playwright screenshotting."""
    emu_to_px = canvas_w / max(1, deck_model.width)
    slides_html: list[str] = []
    for idx, sm in enumerate(deck_model.slides):
        bg = _hex_css(sm.background, "#ffffff")
        els = "".join(_render_element_html(el, emu_to_px) for el in sm.elements)
        slides_html.append(
            f'<div class="rendered-slide" id="rendered-slide-{idx}" '
            f'style="width:{canvas_w}px;height:{canvas_h}px;position:relative;'
            f'overflow:hidden;background-color:{bg};box-sizing:border-box;margin-bottom:24px;">'
            f"{els}</div>"
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        "<style>*{box-sizing:border-box;margin:0;padding:0;}body{background:#0f172a;padding:16px;}</style>"
        f"</head><body>{''.join(slides_html)}</body></html>"
    )


# ---------------------------------------------------------------- Visual Comparison Report Generator


def _capture_html_and_rendered_screenshots(
    html_path: Path,
    palette: str,
    preset: str,
    dark: bool,
    output_pptx_path: Path | None = None,
) -> tuple[DeckScorecard, list[str], list[str]]:
    """Capture original HTML slide PNGs, convert to DeckModel + PPTX, and capture rendered SlideModel PNGs."""
    url = html_path.resolve().as_uri()
    orig_pngs_b64: list[str] = []
    rendered_pngs_b64: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
            try:
                page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception:
                page.wait_for_load_state("load", timeout=20000)

            page.add_script_tag(content=_SCRIPT)
            page.evaluate("() => document.fonts && document.fonts.ready")
            page.evaluate("window.__hpPrepare()")
            _scroll_through(page)
            page.wait_for_timeout(400)

            snap = page.evaluate("window.__hpSnapshot()")
            _resolve_assets(page, snap, html_path.parent)
            snap["source"] = str(html_path)

            # Screenshot each source slide element
            slide_loc = page.locator("[data-hp-slide], .slide")
            count = slide_loc.count()
            if count > 0:
                for i in range(count):
                    png_bytes = slide_loc.nth(i).screenshot(type="png")
                    orig_pngs_b64.append(base64.b64encode(png_bytes).decode("ascii"))
            else:
                png_bytes = page.screenshot(type="png", full_page=False)
                orig_pngs_b64.append(base64.b64encode(png_bytes).decode("ascii"))

            # Build IR Deck, Brand, DeckModel, and PPTX
            deck = build_deck(snap)
            brand = palette_brand(palette_id=palette)
            brand = apply_preset(brand, preset_id=preset, dark=dark)
            deck_model = layout_deck(deck, brand)
            if output_pptx_path is not None:
                write_pptx(deck_model, output_path=output_pptx_path)

            scorecard = evaluate_deck(
                snap=snap,
                deck=deck,
                deck_model=deck_model,
                deck_name=html_path.name,
                palette=palette,
                preset=preset,
                dark=dark,
            )

            # Render DeckModel slides to HTML and screenshot each slide at 1280x720
            rendered_html = render_deck_model_to_standalone_html(deck_model, canvas_w=1280, canvas_h=720)
            render_page = browser.new_page(viewport={"width": 1320, "height": 900}, device_scale_factor=1)
            render_page.set_content(rendered_html, wait_until="load")
            render_page.evaluate("() => document.fonts && document.fonts.ready")
            for idx in range(len(deck_model.slides)):
                loc = render_page.locator(f"#rendered-slide-{idx}")
                png_bytes = loc.screenshot(type="png")
                rendered_pngs_b64.append(base64.b64encode(png_bytes).decode("ascii"))
            render_page.close()
        finally:
            browser.close()

    return scorecard, orig_pngs_b64, rendered_pngs_b64


def generate_visual_comparison_report(
    corpus_dir: Path,
    output_html: Path,
    palette: str = "ocean",
    preset: str = "corporate",
    dark: bool = True,
) -> list[DeckScorecard]:
    """Generate a self-contained side-by-side HTML vs. PPTX visual comparison report for all HTML files in corpus_dir."""
    corpus_dir = Path(corpus_dir)
    output_html = Path(output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)

    html_files = sorted(corpus_dir.glob("*.html"))
    scorecards: list[DeckScorecard] = []
    deck_sections_html: list[str] = []

    for html_file in html_files:
        pptx_out = output_html.parent / f"{html_file.stem}.pptx"
        sc, orig_pngs, rendered_pngs = _capture_html_and_rendered_screenshots(
            html_path=html_file,
            palette=palette,
            preset=preset,
            dark=dark,
            output_pptx_path=pptx_out,
        )
        scorecards.append(sc)

        slide_cards_html: list[str] = []
        for idx, slide_sc in enumerate(sc.slides):
            src_idx = slide_sc.source_index if slide_sc.source_index is not None else idx
            orig_b64 = orig_pngs[min(src_idx, len(orig_pngs) - 1)] if orig_pngs else ""
            rend_b64 = rendered_pngs[idx] if idx < len(rendered_pngs) else ""

            ret_pct = slide_sc.content_retention_ratio * 100.0
            ret_cls = "good" if ret_pct >= 90.0 else "bad"
            ovf_cls = "good" if slide_sc.overflow_count == 0 else "bad"
            col_cls = "good" if slide_sc.collision_count == 0 else "bad"
            neat_cls = "good" if slide_sc.typography_neatness_score >= 85.0 else "bad"

            diag_items: list[str] = []
            for d in slide_sc.overflow_details:
                diag_items.append(f'<li class="diag-bad">Overflow: {html.escape(d)}</li>')
            for d in slide_sc.collision_details:
                diag_items.append(f'<li class="diag-bad">Collision: {html.escape(d)}</li>')
            for d in slide_sc.oversized_font_details:
                diag_items.append(f'<li class="diag-warn">Typography: {html.escape(d)}</li>')
            if slide_sc.missing_phrases:
                preview_missing = ", ".join(slide_sc.missing_phrases[:5])
                diag_items.append(
                    f'<li class="diag-warn">Missing phrases ({len(slide_sc.missing_phrases)}): '
                    f"{html.escape(preview_missing)}</li>"
                )

            diag_html = (
                f'<ul class="diag-list">{"".join(diag_items)}</ul>'
                if diag_items
                else '<div class="diag-ok">✓ Zero overflows, zero collisions, clean typography</div>'
            )

            slide_cards_html.append(
                f"""
                <div class="slide-comparison-card">
                  <div class="slide-card-header">
                    <div>
                      <span class="slide-badge">Slide {slide_sc.slide_number}</span>
                      <span class="archetype-badge">{html.escape(slide_sc.archetype)}</span>
                      <strong class="slide-title">{html.escape(slide_sc.title or f"Slide {slide_sc.slide_number}")}</strong>
                    </div>
                    <div class="metric-badges">
                      <span class="metric-pill {ret_cls}">Retention: {ret_pct:.1f}% ({slide_sc.retained_phrases}/{slide_sc.total_phrases})</span>
                      <span class="metric-pill {ovf_cls}">Overflows: {slide_sc.overflow_count}</span>
                      <span class="metric-pill {col_cls}">Collisions: {slide_sc.collision_count}</span>
                      <span class="metric-pill {neat_cls}">Neatness: {slide_sc.typography_neatness_score:.1f}</span>
                      <span class="metric-pill">Overall: {slide_sc.overall_score:.1f}</span>
                    </div>
                  </div>
                  <div class="side-by-side">
                    <div class="pane">
                      <div class="pane-label">Original HTML Slide (Playwright PNG — Source #{src_idx + 1})</div>
                      <img src="data:image/png;base64,{orig_b64}" alt="Original HTML Slide {src_idx + 1}" />
                    </div>
                    <div class="pane">
                      <div class="pane-label">Generated PPTX Slide (Rendered SlideModel PNG — Slide #{slide_sc.slide_number})</div>
                      <img src="data:image/png;base64,{rend_b64}" alt="Rendered PPTX Slide {slide_sc.slide_number}" />
                    </div>
                  </div>
                  <div class="diagnostics-bar">{diag_html}</div>
                </div>
                """
            )

        deck_sections_html.append(
            f"""
            <section class="deck-section">
              <div class="deck-summary-banner">
                <div>
                  <h2>{html.escape(sc.deck_name)}</h2>
                  <p>Palette: <code>{html.escape(sc.palette)}</code> · Preset: <code>{html.escape(sc.preset)}</code> · Mode: <code>{'dark' if sc.dark else 'light'}</code> · PPTX: <code>{html.escape(pptx_out.name)}</code></p>
                </div>
                <div class="metric-badges">
                  <span class="metric-pill {'good' if sc.content_retention_ratio >= 0.90 else 'bad'}">Retention: {sc.content_retention_ratio * 100:.1f}%</span>
                  <span class="metric-pill {'good' if sc.overflow_count == 0 else 'bad'}">Overflows: {sc.overflow_count}</span>
                  <span class="metric-pill {'good' if sc.collision_count == 0 else 'bad'}">Collisions: {sc.collision_count}</span>
                  <span class="metric-pill {'good' if sc.typography_neatness_score >= 85.0 else 'bad'}">Typography: {sc.typography_neatness_score:.1f}/100</span>
                  <span class="metric-pill {'good' if sc.overall_score >= 85.0 else 'bad'}">Overall Score: {sc.overall_score:.1f}/100</span>
                </div>
              </div>
              {''.join(slide_cards_html)}
            </section>
            """
        )

    avg_ret = (
        sum(s.content_retention_ratio for s in scorecards) / len(scorecards) * 100.0
        if scorecards
        else 100.0
    )
    tot_ovf = sum(s.overflow_count for s in scorecards)
    tot_col = sum(s.collision_count for s in scorecards)
    avg_neat = (
        sum(s.typography_neatness_score for s in scorecards) / len(scorecards)
        if scorecards
        else 100.0
    )
    avg_overall = (
        sum(s.overall_score for s in scorecards) / len(scorecards)
        if scorecards
        else 100.0
    )

    report_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>HTML-PPT Corpus Visual Comparison &amp; LayoutScorecard Report</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0b1120;
    color: #f1f5f9;
    padding: 28px 36px;
    line-height: 1.5;
  }}
  header.report-header {{
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 12px;
    padding: 20px 26px;
    margin-bottom: 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
  }}
  header.report-header h1 {{ font-size: 22px; font-weight: 800; color: #f8fafc; }}
  header.report-header p {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
  .metric-badges {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
  .metric-pill {{
    background: #1e293b;
    border: 1px solid #334155;
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 700;
    color: #e2e8f0;
  }}
  .metric-pill.good {{ background: rgba(16, 185, 129, 0.15); border-color: #10b981; color: #34d399; }}
  .metric-pill.bad {{ background: rgba(239, 68, 68, 0.18); border-color: #ef4444; color: #f87171; }}
  .deck-section {{ margin-bottom: 40px; }}
  .deck-summary-banner {{
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 16px 22px;
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .deck-summary-banner h2 {{ font-size: 18px; color: #38bdf8; }}
  .deck-summary-banner p {{ font-size: 12.5px; color: #cbd5e1; }}
  .slide-comparison-card {{
    background: #111827;
    border: 1px solid #1f293d;
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 22px;
  }}
  .slide-card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
    flex-wrap: wrap;
    gap: 10px;
  }}
  .slide-badge {{
    background: #2563eb;
    color: #fff;
    font-size: 11.5px;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 6px;
    margin-right: 8px;
  }}
  .archetype-badge {{
    background: #0f172a;
    border: 1px solid #334155;
    color: #38bdf8;
    font-family: monospace;
    font-size: 11.5px;
    padding: 2px 8px;
    border-radius: 6px;
    margin-right: 10px;
  }}
  .slide-title {{ font-size: 15px; color: #f8fafc; }}
  .side-by-side {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
  }}
  .pane {{
    background: #070b14;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 10px;
  }}
  .pane-label {{
    font-size: 12px;
    font-weight: 600;
    color: #94a3b8;
    margin-bottom: 8px;
  }}
  .pane img {{
    width: 100%;
    height: auto;
    border-radius: 6px;
    display: block;
    border: 1px solid #1e293b;
  }}
  .diagnostics-bar {{
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid #1e293b;
    font-size: 12.5px;
  }}
  .diag-ok {{ color: #34d399; font-weight: 600; }}
  .diag-list {{ list-style: none; display: flex; flex-direction: column; gap: 4px; }}
  .diag-bad {{ color: #f87171; }}
  .diag-warn {{ color: #fbbf24; }}
</style>
</head>
<body>
  <header class="report-header">
    <div>
      <h1>HTML-PPT Unbiased Corpus Evaluation Report</h1>
      <p>Evaluated {len(scorecards)} corpus deck(s) in <code>{html.escape(str(corpus_dir))}</code></p>
    </div>
    <div class="metric-badges">
      <span class="metric-pill {'good' if avg_ret >= 90.0 else 'bad'}">Corpus Retention: {avg_ret:.1f}%</span>
      <span class="metric-pill {'good' if tot_ovf == 0 else 'bad'}">Total Overflows: {tot_ovf}</span>
      <span class="metric-pill {'good' if tot_col == 0 else 'bad'}">Total Collisions: {tot_col}</span>
      <span class="metric-pill {'good' if avg_neat >= 85.0 else 'bad'}">Avg Neatness: {avg_neat:.1f}/100</span>
      <span class="metric-pill {'good' if avg_overall >= 85.0 else 'bad'}">Corpus Overall: {avg_overall:.1f}/100</span>
    </div>
  </header>
  {''.join(deck_sections_html)}
</body>
</html>
"""
    output_html.write_text(report_doc, encoding="utf-8")
    return scorecards


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate HTML corpus and generate visual comparison report.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("backend/tests/corpus"),
        help="Directory containing corpus .html files",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("corpus_report.html"),
        help="Output path for visual comparison HTML report",
    )
    parser.add_argument("--palette", default="ocean", help="Built-in palette (ocean, slate, forest, ...)")
    parser.add_argument("--preset", default="corporate", help="Style preset (corporate, minimal, bold, ...)")
    parser.add_argument("--dark", action="store_true", default=True, help="Enable dark theme (default: True)")
    parser.add_argument("--light", dest="dark", action="store_false", help="Enable light theme")

    args = parser.parse_args()
    scorecards = generate_visual_comparison_report(
        corpus_dir=args.corpus_dir,
        output_html=args.output,
        palette=args.palette,
        preset=args.preset,
        dark=args.dark,
    )
    print(format_scorecard_table(scorecards))
    print(f"\nVisual comparison report written to: {args.output.resolve()}")


if __name__ == "__main__":
    main()
