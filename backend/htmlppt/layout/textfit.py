"""Text measurement and wrapping utilities.

Uses Pillow's ImageFont if available/font loaded, otherwise fallbacks to
proportional font character width metrics. Calculates wrapped line count
and required height in EMU or points.
"""
from __future__ import annotations

import re
from typing import Sequence

from PIL import ImageFont

from htmlppt import EMU_PER_PT
from htmlppt.analyze.ir import Run
from htmlppt.layout import model as lm


# Average character width ratios relative to font size (in pt) for common fonts
APPROX_CHAR_WIDTH_RATIOS = {
    "default": 0.52,
    "mono": 0.60,
    "narrow": 0.45,
    "wide": 0.58,
}


def estimate_run_width_pt(text: str, font_family: str, font_size_pt: float, bold: bool = False) -> float:
    """Estimate string width in points."""
    ratio = APPROX_CHAR_WIDTH_RATIOS.get("mono" if "mono" in font_family.lower() else "default", 0.52)
    if bold:
        ratio *= 1.08
    # Count uppercase and wide letters as slightly wider
    w = 0.0
    for ch in text:
        if ch in "WMm@%#&":
            w += font_size_pt * ratio * 1.3
        elif ch in "ijl|!:,.' ":
            w += font_size_pt * ratio * 0.55
        elif ch.isupper():
            w += font_size_pt * ratio * 1.15
        else:
            w += font_size_pt * ratio
    return w


def wrap_text_to_lines(text: str, max_width_pt: float, font_family: str, font_size_pt: float, bold: bool = False) -> list[str]:
    """Break text into wrapped lines given a max width in points."""
    if not text:
        return [""]
    paragraphs = text.split("\n")
    all_lines: list[str] = []
    
    for p in paragraphs:
        if not p:
            all_lines.append("")
            continue
        words = p.split(" ")
        current_line = []
        current_w = 0.0
        space_w = estimate_run_width_pt(" ", font_family, font_size_pt, bold)
        
        for w in words:
            word_w = estimate_run_width_pt(w, font_family, font_size_pt, bold)
            needed = word_w if not current_line else current_w + space_w + word_w
            if needed <= max_width_pt or not current_line:
                current_line.append(w)
                current_w = needed
            else:
                all_lines.append(" ".join(current_line))
                current_line = [w]
                current_w = word_w
        if current_line:
            all_lines.append(" ".join(current_line))
            
    return all_lines


def estimate_text_height_emu(
    text: str,
    max_width_emu: int,
    font_family: str,
    font_size_pt: float,
    line_spacing: float = 1.2,
    bold: bool = False,
) -> int:
    """Calculate needed height in EMU for a block of text."""
    max_width_pt = max_width_emu / EMU_PER_PT
    lines = wrap_text_to_lines(text, max_width_pt, font_family, font_size_pt, bold)
    line_count = len(lines)
    height_pt = line_count * font_size_pt * line_spacing
    return int(height_pt * EMU_PER_PT)


def fit_font_size(
    text: str,
    max_width_emu: int,
    max_height_emu: int,
    font_family: str,
    preferred_size_pt: float,
    min_size_pt: float = 10.0,
    line_spacing: float = 1.2,
    bold: bool = False,
) -> float:
    """Auto-shrink font size between preferred_size_pt and min_size_pt to fit the bounding box."""
    size = preferred_size_pt
    while size >= min_size_pt:
        h = estimate_text_height_emu(text, max_width_emu, font_family, size, line_spacing, bold)
        if h <= max_height_emu:
            return size
        size -= 1.0
    return min_size_pt


def estimate_para_height_emu(
    para: lm.Para,
    max_width_emu: int,
    default_line_spacing: float = 1.18,
) -> int:
    """Compute wrapped line count and height in EMU for a single Para."""
    spacing_emu = int((max(0.0, para.space_before) + max(0.0, para.space_after)) * EMU_PER_PT)
    if not para.runs:
        return spacing_emu

    full_text = "".join(r.text for r in para.runs)
    if not full_text.strip():
        return spacing_emu

    max_size = max(r.size for r in para.runs)
    font_family = para.runs[0].font or "Calibri"
    any_bold = any(r.bold for r in para.runs)

    indent_pt = (14.0 if para.bullet else 0.0) + max(0, para.level) * 14.0
    max_width_pt = max(24.0, (max_width_emu / EMU_PER_PT) - indent_pt)

    lines = wrap_text_to_lines(full_text, max_width_pt, font_family, max_size, bold=any_bold)
    line_count = max(1, len(lines))
    line_spacing = (
        para.line_spacing
        if (para.line_spacing and abs(para.line_spacing - 1.0) > 1e-4)
        else default_line_spacing
    )
    text_h_emu = int(line_count * max_size * line_spacing * EMU_PER_PT)
    return text_h_emu + spacing_emu


def estimate_paras_height_emu(
    paras: Sequence[lm.Para],
    max_width_emu: int,
    default_line_spacing: float = 1.18,
) -> int:
    """Sum estimated height in EMU across all paragraphs in paras."""
    return sum(
        estimate_para_height_emu(p, max_width_emu, default_line_spacing=default_line_spacing)
        for p in paras
    )


def scale_paras_to_fit(
    paras: list[lm.Para],
    max_width_emu: int,
    max_height_emu: int,
    min_scale: float = 0.62,
    min_size_pt: float = 8.5,
    default_line_spacing: float = 1.15,
) -> list[lm.Para]:
    """Iteratively scale down paragraph run font sizes and spacing to fit within max_height_emu."""
    if not paras or max_height_emu <= 0:
        return paras

    if estimate_paras_height_emu(paras, max_width_emu, default_line_spacing) <= max_height_emu:
        return paras

    # Build decreasing scale steps down to min_scale
    steps: list[float] = []
    cur = 0.94
    while cur > min_scale + 1e-4:
        steps.append(round(cur, 3))
        cur -= 0.04
    steps.append(min_scale)

    best_paras = paras
    for scale in steps:
        eff_line_spacing = max(1.05, round(default_line_spacing - (1.0 - scale) * 0.25, 3))
        scaled: list[lm.Para] = []
        for p in paras:
            scaled_runs: list[lm.TRun] = []
            for r in p.runs:
                floor_pt = min(r.size, 8.0) if r.size < min_size_pt else min_size_pt
                new_size = round(max(floor_pt, r.size * scale), 1)
                scaled_runs.append(r.model_copy(update={"size": new_size}))

            p_line_spacing = (
                max(1.05, round(p.line_spacing * (0.85 + 0.15 * scale), 3))
                if (p.line_spacing and abs(p.line_spacing - 1.0) > 1e-4)
                else eff_line_spacing
            )
            scaled.append(
                p.model_copy(
                    update={
                        "runs": scaled_runs,
                        "space_before": round(p.space_before * scale, 1),
                        "space_after": round(p.space_after * scale, 1),
                        "line_spacing": p_line_spacing,
                    }
                )
            )
        best_paras = scaled
        if estimate_paras_height_emu(scaled, max_width_emu, eff_line_spacing) <= max_height_emu:
            return scaled

    return best_paras

