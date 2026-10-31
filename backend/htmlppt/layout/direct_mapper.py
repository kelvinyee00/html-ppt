"""Direct Browser-Layout-First Mapper: DOM Snapshot -> Positioned SlideModel.

Preserves Chromium's exact Flexbox/Grid/table geometry (x, y, w, h) while
mapping surfaces, borders, badges, typography, and semantic tones to the
active BrandProfile across any palette and dark/light mode.
"""
from __future__ import annotations

import re
from typing import Literal, Sequence

from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.brand.model import BrandProfile, hex_to_rgb
from htmlppt.brand.presets import _mix, contrast_ratio, ensure_contrast, relative_luminance
from htmlppt.layout import model as lm
from htmlppt.layout.textfit import (
    estimate_paras_height_emu,
    estimate_text_height_emu,
    fit_font_size,
    scale_paras_to_fit,
)


def _clean_hex(c: str | None) -> str | None:
    if not c:
        return None
    h = c.strip().lstrip("#").upper()
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return h if len(h) == 6 else None


def _color_tone(color: str | None) -> str:
    h = _clean_hex(color)
    if not h:
        return "none"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return "none"
    # Green / Emerald / Teal-green
    if g > r + 28 and g >= b - 10 and g > 110:
        return "positive"
    # Red / Rose / Crimson
    if r > g + 38 and r > b + 20 and r > 140:
        return "negative"
    # Amber / Gold / Orange
    if r > 175 and g > 100 and b < 90 and r >= g:
        return "warning"
    # Blue / Cyan / Sky / Indigo accent
    if (b >= 170 and b > r + 40) or (b >= 150 and g >= 140 and r < 80):
        return "primary"
    return "none"


def _cls_tone(cls: str | None) -> str:
    if not cls:
        return "none"
    low = cls.lower()
    tokens = set(re.split(r"[\s_\-]+", low))
    if tokens & {"good", "positive", "success", "deliverable", "highlight"} or any(
        k in low for k in ("right-branch", "security-box", "security-badge", "security-banner", "airgap-banner")
    ):
        return "positive"
    if tokens & {"bad", "negative", "danger", "error", "down"}:
        return "negative"
    if tokens & {"warning", "confidential", "caution"}:
        return "warning"
    if tokens & {"kicker", "primary", "num", "value"} or "v-step" in low:
        return "primary"
    return "none"


def _resolve_tone_color(tone: str, brand: BrandProfile, bg_hex: str) -> str:
    if tone == "positive":
        base = brand.roles.positive
    elif tone == "negative":
        base = brand.roles.negative
    elif tone == "warning":
        base = brand.colors.accent4 or brand.roles.primary
    elif tone in ("primary", "info"):
        base = brand.roles.primary
    else:
        base = brand.roles.primary
    return _ensure_readable(base, bg_hex, min_ratio=3.8)


def _ensure_readable(fg_hex: str, bg_hex: str, min_ratio: float = 4.2) -> str:
    fg = _clean_hex(fg_hex) or "FFFFFF"
    bg = _clean_hex(bg_hex) or "0F172A"
    if contrast_ratio(fg, bg) >= min_ratio:
        return fg
    bg_lum = relative_luminance(bg)
    target = "FFFFFF" if bg_lum < 0.45 else "111827"
    for step in range(1, 21):
        cand = _mix(fg, target, step / 20.0)
        if contrast_ratio(cand, bg) >= min_ratio:
            return cand
    return target


def _is_saturated_or_accent_bg(hex_col: str) -> bool:
    h = _clean_hex(hex_col)
    if not h:
        return False
    r, g, b = hex_to_rgb(h)
    mx, mn = max(r, g, b), min(r, g, b)
    return (mx - mn) > 65 and mx > 90


def _map_container_fill(
    node: dict,
    brand: BrandProfile,
    parent_bg: str,
    is_badge_or_btn: bool = False,
) -> tuple[str | None, list[str] | None, float]:
    """Return (fill_hex, gradient_stops, fill_alpha) mapped to the active BrandProfile."""
    v = node.get("v") or {}
    cls = (node.get("cls") or "").lower()
    tag = (node.get("tag") or "").lower()
    bg = _clean_hex(v.get("bg"))
    bg_alpha = float(v.get("bgA", 1.0))
    grad = [_clean_hex(c) for c in (v.get("grad") or []) if _clean_hex(c)]
    grad_alpha = float(v.get("gradA", 1.0))
    tone = _cls_tone(cls)
    if tone == "none" and bg:
        tone = _color_tone(bg)
    if tone == "none" and grad:
        tone = _color_tone(grad[0])

    # 1. Prominent gradient banner (e.g. .cta-banner) vs low-alpha tinted card (e.g. .security-box)
    if grad and len(grad) >= 2:
        if "cta" in cls or (grad_alpha >= 0.5 and any(_is_saturated_or_accent_bg(g) for g in grad)):
            g1 = brand.colors.accent1 or brand.roles.primary
            g2 = brand.colors.accent2 or brand.roles.secondary or g1
            if brand.design.dark and relative_luminance(g1) > 0.45:
                g1 = _mix(g1, brand.roles.background, 0.45)
            return g1, [g1, g2], 1.0
        # Subtle tinted card gradient (e.g. .security-box)
        if tone != "none":
            t_col = _resolve_tone_color(tone, brand, brand.roles.surface)
            tinted = _mix(brand.roles.surface, t_col, 0.10 if brand.design.dark else 0.08)
            return tinted, None, 1.0
        return brand.roles.surface, None, 1.0

    if not bg:
        return None, None, 1.0

    # 2. High-contrast solid CTA button (e.g. white .cta-btn on dark/accent banner)
    if tag == "button" or "btn" in cls:
        bg_lum = relative_luminance(bg)
        if bg_lum > 0.80:
            return ("FFFFFF" if brand.design.dark else brand.roles.primary), None, 1.0
        return brand.roles.primary, None, 1.0

    # 3. Compact badge, pill, or icon box
    if is_badge_or_btn or "badge" in cls or "icon" in cls or "tag-" in cls or bg_alpha <= 0.45:
        if tone == "none":
            tone = _color_tone( node.get("c") )
        if tone != "none":
            t_col = _resolve_tone_color(tone, brand, parent_bg)
            fill_mix = 0.22 if brand.design.dark else 0.14
            return _mix(parent_bg, t_col, fill_mix), None, 1.0
        return _mix(parent_bg, brand.roles.primary, 0.18 if brand.design.dark else 0.12), None, 1.0

    # 4. Standard Card / KPI / Callout / Spine surface
    if "v-badge-center" in cls:
        return _mix(brand.roles.surface, brand.roles.primary, 0.10), None, 1.0
    if parent_bg.upper() == brand.roles.surface.upper():
        # Nested card inside a surface
        return _mix(brand.roles.surface, brand.roles.background, 0.35), None, 1.0
    return brand.roles.surface, None, 1.0


def _map_border_color(raw_c: str | None, cls: str | None, brand: BrandProfile, is_accent: bool = False) -> str:
    tone = _color_tone(raw_c)
    if tone == "none":
        tone = _cls_tone(cls)
    if tone != "none" or is_accent:
        return _resolve_tone_color(tone if tone != "none" else "primary", brand, brand.roles.surface)
    return brand.roles.border


def _map_text_color(
    raw_c: str | None,
    cls: str | None,
    fw: int,
    brand: BrandProfile,
    effective_bg: str,
    is_heading: bool = False,
) -> str:
    # Check if effective_bg is a bright white button or saturated accent banner
    bg_lum = relative_luminance(effective_bg)
    if bg_lum > 0.85 and brand.design.dark:
        # e.g. white .cta-btn in dark mode
        return "0F172A"
    if _is_saturated_or_accent_bg(effective_bg) and bg_lum < 0.45:
        # e.g. .cta-banner with accent gradient fill
        h = _clean_hex(raw_c)
        if h and relative_luminance(h) > 0.70:
            return "FFFFFF"

    tone = _color_tone(raw_c)
    if tone == "none":
        tone = _cls_tone(cls)
    if tone != "none":
        return _resolve_tone_color(tone, brand, effective_bg)

    if is_heading or fw >= 600:
        return _ensure_readable(brand.roles.text, effective_bg, min_ratio=4.5)

    # Distinguish primary bright text vs secondary/muted text from source luminance
    h = _clean_hex(raw_c)
    if h:
        r, g, b = hex_to_rgb(h)
        lum = relative_luminance(h)
        # In dark source HTML, muted text is #64748b (lum ~0.17) or #94a3b8 (lum ~0.36)
        if lum < 0.22:
            return _ensure_readable(brand.roles.muted, effective_bg, min_ratio=3.5)
        if lum < 0.55:
            sec = _mix(brand.roles.text, brand.roles.muted, 0.42)
            return _ensure_readable(sec, effective_bg, min_ratio=4.0)

    return _ensure_readable(brand.roles.text, effective_bg, min_ratio=4.5)


def _compute_roundrect_adj(radius_px: float, w_px: float, h_px: float, is_badge: bool = False) -> float:
    if radius_px <= 0 or min(w_px, h_px) <= 0:
        return 0.0
    # evaluate.py renders border-radius as (radius * 320)px, so scale radius_px / 320.0
    # for crisp CSS-faithful corners across both PowerPoint and the HTML preview.
    if is_badge and (radius_px >= 16 or radius_px >= h_px * 0.42):
        return 0.12
    return min(0.06, max(0.015, round(radius_px / 320.0, 3)))


def _has_descendant_card(node: dict) -> bool:
    """Return True if node has a child/descendant container covering >=35% of node's area with its own visual box."""
    n_box = node.get("box") or {}
    n_area = max(1.0, float(n_box.get("w", 0)) * float(n_box.get("h", 0)))

    def _check(kids: list[dict]) -> bool:
        for k in kids:
            kv = k.get("v") or {}
            if kv.get("bg") or kv.get("grad") or kv.get("border") or kv.get("borders"):
                kb = k.get("box") or {}
                k_area = float(kb.get("w", 0)) * float(kb.get("h", 0))
                if k_area >= 0.34 * n_area:
                    return True
            if _check(k.get("kids") or []):
                return True
        return False

    return _check(node.get("kids") or [])


def _is_compact_badge_or_button(node: dict) -> bool:
    """Return True if node is a leaf visual element (badge, pill, button, icon box) with text runs and no kids."""
    if node.get("kids") or not node.get("runs"):
        return False
    v = node.get("v") or {}
    if not (v.get("bg") or v.get("grad") or v.get("border")):
        return False
    tag = (node.get("tag") or "").lower()
    cls = (node.get("cls") or "").lower()
    box = node.get("box") or {}
    w = float(box.get("w", 0))
    h = float(box.get("h", 0))
    if tag == "button" or any(k in cls for k in ("badge", "pill", "icon", "btn", "tag-")):
        return True
    if h <= 56 and w <= 340:
        return True
    return False


def _autofit_paras_strict(
    paras: list[lm.Para],
    avail_w_emu: int,
    avail_h_emu: int,
    min_font_pt: float = 6.5,
) -> None:
    """Ensure estimate_paras_height_emu(paras, avail_w_emu, default_line_spacing=1.15) <= avail_h_emu."""
    if not paras or avail_w_emu <= 0 or avail_h_emu <= 0:
        return
    safe_w = max(int(0.2 * EMU_PER_INCH), avail_w_emu)
    target_h = max(int(6 * EMU_PER_PT), int(avail_h_emu * 1.02))

    if estimate_paras_height_emu(paras, safe_w, default_line_spacing=1.15) <= target_h:
        return

    paras[:] = scale_paras_to_fit(
        paras,
        safe_w,
        target_h,
        min_size_pt=min_font_pt,
        default_line_spacing=1.15,
    )
    for _ in range(25):
        est = estimate_paras_height_emu(paras, safe_w, default_line_spacing=1.15)
        if est <= target_h:
            return
        changed = False
        for p in paras:
            if p.space_before > 0.2:
                p.space_before = max(0.0, round(p.space_before * 0.65, 1))
                changed = True
            if p.space_after > 0.2:
                p.space_after = max(0.0, round(p.space_after * 0.65, 1))
                changed = True
            if p.line_spacing and p.line_spacing > 1.05:
                p.line_spacing = max(1.0, round(p.line_spacing - 0.05, 2))
                changed = True
            for r in p.runs:
                if r.size > min_font_pt:
                    new_sz = max(min_font_pt, round(r.size - max(0.4, r.size * 0.07), 1))
                    if new_sz < r.size:
                        r.size = new_sz
                        changed = True
        if not changed:
            break


def layout_slide_from_dom(slide: ir.Slide, brand: BrandProfile, slide_num: int = 1) -> lm.SlideModel:
    """Directly map a slide's Playwright DOM snapshot subtree into a positioned SlideModel."""
    snode = slide.dom_node or {}
    sbox = snode.get("box") or {"x": 0, "y": 0, "w": 1280, "h": 720}
    sx = float(sbox.get("x", 0.0))
    sy = float(sbox.get("y", 0.0))
    sw = max(float(sbox.get("w", 1280.0)), 320.0)
    sh = max(float(sbox.get("h", 720.0)), 240.0)

    W = brand.slide_size.width
    H = brand.slide_size.height
    scale_x = W / sw
    scale_y = H / sh
    # CSS px -> PowerPoint pt scale factor with a slight glyph-width breathing margin
    pt_scale = (H / EMU_PER_PT) / sh * 0.92

    archetype = slide.layout or (slide.kind if slide.kind in ("title", "section", "closing") else "content")
    sm = lm.SlideModel(
        id=slide.id,
        archetype=archetype,
        template_layout="title" if slide.kind == "title" else "content",
        background=brand.roles.background,
        notes=slide.notes,
        show_footer=False,
        number=slide_num,
    )

    title_assigned = False
    charts_list = slide.charts or []

    def to_emu_rect(box: dict) -> tuple[int, int, int, int]:
        rx = int(round((float(box.get("x", sx)) - sx) * scale_x))
        ry = int(round((float(box.get("y", sy)) - sy) * scale_y))
        rw = max(int(2 * EMU_PER_PT), int(round(float(box.get("w", 4.0)) * scale_x)))
        rh = max(int(2 * EMU_PER_PT), int(round(float(box.get("h", 4.0)) * scale_y)))
        if rx < 0:
            rw = max(int(2 * EMU_PER_PT), rw + rx)
            rx = 0
        if ry < 0:
            rh = max(int(2 * EMU_PER_PT), rh + ry)
            ry = 0
        if rx + rw > W:
            rw = max(int(2 * EMU_PER_PT), W - rx)
        if ry + rh > H:
            rh = max(int(2 * EMU_PER_PT), H - ry)
        return rx, ry, rw, rh

    def convert_runs_to_paras(
        raw_runs: list[dict],
        node: dict,
        effective_bg: str,
        *,
        is_heading: bool = False,
        is_kpi_val: bool = False,
        is_slide_title: bool = False,
        inside_card: bool = False,
        force_align: Literal["left", "center", "right", "justify"] | None = None,
    ) -> list[lm.Para]:
        groups: list[list[dict]] = [[]]
        for r in raw_runs:
            if r.get("t") == "\n":
                if groups[-1]:
                    groups.append([])
            else:
                groups[-1].append(r)

        ta = force_align or (node.get("ta") or "left").lower()
        if ta not in ("left", "center", "right", "justify"):
            ta = "left"
        align: Literal["left", "center", "right", "justify"] = ta  # type: ignore[assignment]

        node_cls = node.get("cls") or ""
        node_fs = float(node.get("fs") or 14.0)
        node_fw = int(node.get("fw") or 400)

        paras: list[lm.Para] = []
        for g_idx, g in enumerate(groups):
            if not g:
                continue
            truns: list[lm.TRun] = []
            for r_idx, r in enumerate(g):
                txt = str(r.get("t", ""))
                if not txt:
                    continue
                if r.get("up"):
                    txt = txt.upper()

                r_fs = float(r.get("fs") or node_fs)
                sz_pt = round(max(6.5, r_fs * pt_scale), 1)
                if not is_slide_title and not is_kpi_val and inside_card:
                    sz_pt = min(15.5, sz_pt)
                elif not is_slide_title and not is_kpi_val and not is_heading:
                    sz_pt = min(15.8, sz_pt)

                r_bold = bool(r.get("b") or node_fw >= 600 or is_heading or is_kpi_val or r.get("hl"))
                r_hl = _clean_hex(r.get("hl"))
                r_col_raw = r.get("c") or node.get("c")

                if r_hl:
                    hl_tone = _color_tone(r_hl) or _color_tone(r_col_raw)
                    col = _resolve_tone_color(hl_tone if hl_tone != "none" else "primary", brand, effective_bg)
                    if r_idx + 1 < len(g):
                        nxt_t = str(g[r_idx + 1].get("t", ""))
                        if not txt.endswith(" ") and nxt_t and not nxt_t.startswith(" "):
                            txt = txt + " "
                else:
                    col = _map_text_color(
                        r_col_raw,
                        node_cls,
                        700 if r_bold else node_fw,
                        brand,
                        effective_bg,
                        is_heading=(is_heading or is_slide_title),
                    )

                font_fam = (
                    "Consolas"
                    if r.get("mono")
                    else (brand.fonts.heading.family if (is_heading or is_slide_title or is_kpi_val) else brand.fonts.body.family)
                )
                truns.append(
                    lm.TRun(
                        text=txt,
                        font=font_fam,
                        size=sz_pt,
                        color=col,
                        bold=r_bold,
                        italic=bool(r.get("i")),
                        underline=bool(r.get("u")),
                        strike=bool(r.get("st")),
                        href=r.get("href"),
                        sup=bool(r.get("sup")),
                        sub=bool(r.get("sub")),
                    )
                )
            if truns:
                paras.append(
                    lm.Para(
                        runs=truns,
                        align=align,
                        space_before=1.5 if g_idx > 0 else 0.0,
                        space_after=0.0,
                        line_spacing=1.12,
                    )
                )
        return paras

    def emit_text_node(
        node: dict,
        effective_bg: str,
        parent_rect: tuple[int, int, int, int] | None,
        inside_card: bool,
        in_kpi: bool,
        next_sibling_y: int | None = None,
    ) -> None:
        nonlocal title_assigned
        raw_runs = node.get("runs") or []
        if not raw_runs:
            return

        tag = (node.get("tag") or "").lower()
        cls = (node.get("cls") or "").lower()
        box = node.get("box") or {}
        tbox = node.get("tbox") or box

        # Emit decorative dot if present (e.g. .kicker .dot)
        if node.get("dot"):
            d_info = node["dot"]
            dx, dy, dw, dh = to_emu_rect(d_info["box"])
            d_col = _map_border_color(d_info.get("c"), cls, brand, is_accent=True)
            sm.elements.append(
                lm.ShapeEl(
                    name="Kicker Dot",
                    x=dx,
                    y=dy,
                    w=dw,
                    h=dh,
                    geom="ellipse",
                    fill=d_col,
                )
            )

        is_slide_title = False
        if not title_assigned and tag in ("h1", "h2") and not inside_card:
            is_slide_title = True
            title_assigned = True

        is_heading = tag in ("h1", "h2", "h3", "h4", "h5", "h6") or "title" in cls
        is_kpi_val = (in_kpi and ("value" in cls or float(node.get("fs") or 14) >= 28)) or (
            "value" in cls and float(node.get("fs") or 14) >= 30
        )

        paras = convert_runs_to_paras(
            raw_runs,
            node,
            effective_bg,
            is_heading=is_heading,
            is_kpi_val=is_kpi_val,
            is_slide_title=is_slide_title,
            inside_card=inside_card,
        )
        if not paras:
            return

        # Compute base box in EMU
        if node.get("dot") and tbox.get("w", 0) > 0:
            use_box = {
                "x": tbox["x"],
                "y": min(float(box.get("y", tbox["y"])), float(tbox["y"])),
                "w": max(float(tbox["w"]), (float(box.get("x", 0)) + float(box.get("w", 0))) - float(tbox["x"])),
                "h": max(float(box.get("h", 0)), float(tbox.get("h", 0))),
            }
        else:
            use_box = {
                "x": box.get("x", tbox.get("x", 0)),
                "y": box.get("y", tbox.get("y", 0)),
                "w": max(float(box.get("w", 0)), float(tbox.get("w", 0))),
                "h": max(float(box.get("h", 0)), float(tbox.get("h", 0))),
            }

        rx, ry, rw, rh = to_emu_rect(use_box)

        # If node had internal CSS padding (and no visual background), offset text inset
        pad = node.get("pad")
        if pad and not (node.get("v") or {}).get("bg"):
            pl_emu = int(round(float(pad[3]) * scale_x))
            pt_emu = int(round(float(pad[0]) * scale_y))
            if pl_emu > 0 and pl_emu < rw // 2:
                rx += pl_emu
                rw -= pl_emu
            if pt_emu > 0 and pt_emu < rh // 2:
                ry += pt_emu
                rh -= pt_emu

        # Single-line inline/shrink-wrapped width buffer so textfit/PowerPoint never wraps a 1-line label onto 2 lines
        fs_px = float(node.get("fs") or 14.0)
        was_single_line = float(use_box.get("h", 0)) <= fs_px * 1.95 and len(paras) == 1
        if was_single_line:
            p0 = paras[0]
            full_txt = "".join(r.text for r in p0.runs)
            max_sz = max((r.size for r in p0.runs), default=10.0)
            bold_any = any(r.bold for r in p0.runs)
            # Estimate minimum width needed for 1 line in textfit.py
            one_line_h = int(max_sz * 1.35 * EMU_PER_PT)
            est_at_rw = estimate_text_height_emu(full_txt, rw, "Calibri", max_sz, line_spacing=1.15, bold=bold_any)
            if est_at_rw > one_line_h:
                needed_w = int(len(full_txt) * max_sz * (0.62 if bold_any else 0.56) * EMU_PER_PT) + int(10 * EMU_PER_PT)
                max_right = (parent_rect[0] + parent_rect[2] - int(4 * EMU_PER_PT)) if parent_rect else (W - int(12 * EMU_PER_PT))
                if p0.align == "right" or (rx + needed_w > max_right and not inside_card):
                    new_w = min(needed_w, max_right - (parent_rect[0] if parent_rect else int(12 * EMU_PER_PT)))
                    if new_w > rw:
                        rx = max(parent_rect[0] if parent_rect else 0, rx - (new_w - rw))
                        rw = new_w
                else:
                    rw = min(max(rw, needed_w), max(rw, max_right - rx))

        # Available height inside parent card or slide canvas (and before any stacked sibling below)
        avail_in_parent = ((parent_rect[1] + parent_rect[3]) - ry) if parent_rect else (H - ry)
        if next_sibling_y is not None and next_sibling_y > ry:
            avail_in_parent = min(avail_in_parent, next_sibling_y - ry + int(2 * scale_y))
        avail_in_parent = max(int(8 * EMU_PER_PT), avail_in_parent)

        # Horizontal flex row with space-between (e.g. .slide-footer with left & right <span>)
        if node.get("jc") == "space-between" and node.get("flex") in ("row", None) and len(paras) == 2:
            row_h = min(avail_in_parent, max(rh + int(5 * scale_y), int(16 * EMU_PER_PT)))
            left_p = [paras[0].model_copy(update={"align": "left", "space_before": 0.0, "space_after": 0.0})]
            right_p = [paras[1].model_copy(update={"align": "right", "space_before": 0.0, "space_after": 0.0})]
            left_w = int(rw * 0.68)
            right_w = int(rw * 0.45)
            _autofit_paras_strict(left_p, left_w, max(int(8 * EMU_PER_PT), row_h - int(4 * scale_y)), min_font_pt=6.5)
            _autofit_paras_strict(right_p, right_w, max(int(8 * EMU_PER_PT), row_h - int(4 * scale_y)), min_font_pt=6.5)
            sm.elements.append(
                lm.TextEl(
                    name="Footer Left" if "footer" in cls else "Content Text",
                    x=rx,
                    y=ry,
                    w=left_w,
                    h=row_h,
                    paras=left_p,
                    valign="top",
                    inset=0,
                )
            )
            sm.elements.append(
                lm.TextEl(
                    name="Footer Right" if "footer" in cls else "Content Text",
                    x=rx + rw - right_w,
                    y=ry,
                    w=right_w,
                    h=row_h,
                    paras=right_p,
                    valign="top",
                    inset=0,
                )
            )
            return

        # Add vertical breathing room (+6px) for evaluate.py's 4px vertical padding and descenders
        rh = min(avail_in_parent, rh + int(6 * scale_y))
        est_h = estimate_paras_height_emu(paras, rw, default_line_spacing=1.15)
        if est_h > rh and rh < avail_in_parent:
            rh = min(avail_in_parent, max(rh, int(est_h * 1.06)))

        # Strictly fit paragraphs leaving room for 4px vertical preview padding
        fit_budget_h = max(int(8 * EMU_PER_PT), min(rh, avail_in_parent) - int(5 * scale_y))
        _autofit_paras_strict(paras, rw, fit_budget_h, min_font_pt=6.5)

        if is_slide_title:
            el_name = "Slide Title"
        elif is_kpi_val:
            el_name = "KPI Text"
        elif inside_card:
            el_name = "Card Content"
        else:
            el_name = "Content Text"

        sm.elements.append(
            lm.TextEl(
                name=el_name,
                x=rx,
                y=ry,
                w=rw,
                h=rh,
                paras=paras,
                valign="top",
                inset=0,
                placeholder="title" if is_slide_title else None,
            )
        )

    def emit_list_node(
        node: dict,
        effective_bg: str,
        parent_rect: tuple[int, int, int, int] | None,
        inside_card: bool,
    ) -> None:
        items = node.get("items") or []
        if not items:
            return
        rx, ry, rw, rh = to_emu_rect(node.get("box") or {})
        ordered = bool(node.get("ordered"))
        paras: list[lm.Para] = []
        for idx, it in enumerate(items):
            it_runs = it.get("runs") or []
            p_list = convert_runs_to_paras(
                it_runs,
                node,
                effective_bg,
                inside_card=inside_card,
            )
            if not p_list:
                continue
            b_char = it.get("bulletChar") or ("1." if ordered else "•")
            b_col_raw = it.get("bulletColor")
            b_col = _map_border_color(b_col_raw, node.get("cls"), brand, is_accent=True) if b_col_raw else brand.roles.primary
            for p_i, p in enumerate(p_list):
                p.level = int(it.get("level") or 0)
                if p_i == 0:
                    p.bullet = b_char
                    p.bullet_color = b_col
                    p.space_before = 2.0 if idx > 0 else 0.0
                p.space_after = 1.5
                paras.append(p)

        if not paras:
            return

        avail_in_parent = ((parent_rect[1] + parent_rect[3]) - ry) if parent_rect else (H - ry)
        avail_in_parent = max(int(10 * EMU_PER_PT), avail_in_parent)
        rh = min(avail_in_parent, rh + int(6 * scale_y))
        est_h = estimate_paras_height_emu(paras, rw, default_line_spacing=1.15)
        if est_h > rh and rh < avail_in_parent:
            rh = min(avail_in_parent, max(rh, int(est_h * 1.06)))

        fit_budget_h = max(int(8 * EMU_PER_PT), min(rh, avail_in_parent) - int(5 * scale_y))
        _autofit_paras_strict(paras, rw, fit_budget_h, min_font_pt=6.5)

        sm.elements.append(
            lm.TextEl(
                name="Card List" if inside_card else "Bullet List",
                x=rx,
                y=ry,
                w=rw,
                h=rh,
                paras=paras,
                valign="top",
                inset=0,
            )
        )

    def emit_table_node(node: dict, effective_bg: str) -> None:
        rows_data = node.get("rows") or []
        if not rows_data:
            return
        tx, ty, tw, th = to_emu_rect(node.get("box") or {})
        n_rows = len(rows_data)
        n_cols = max(( sum(int(c.get("cs") or 1) for c in r.get("cells", [])) for r in rows_data ), default=0)
        if n_cols <= 0:
            return

        # Determine column widths from first unmerged row
        raw_col_w: list[float] = [0.0] * n_cols
        for r in rows_data:
            cells = r.get("cells") or []
            if len(cells) == n_cols and all(int(c.get("cs") or 1) == 1 for c in cells):
                raw_col_w = [max(20.0, float(c.get("w") or 100.0)) for c in cells]
                break
        if sum(raw_col_w) <= 0:
            raw_col_w = [1.0] * n_cols
        tot_cw = sum(raw_col_w)
        col_widths = [max(int(0.4 * EMU_PER_INCH), int(round(tw * (w / tot_cw)))) for w in raw_col_w]
        if sum(col_widths) != tw:
            col_widths[-1] = max(int(0.4 * EMU_PER_INCH), col_widths[-1] + (tw - sum(col_widths)))

        # Determine row heights from measured tr heights
        raw_row_h = [max(18.0, float(r.get("h") or (th / scale_y / n_rows))) for r in rows_data]
        tot_rh = sum(raw_row_h)
        row_heights = [max(int(0.24 * EMU_PER_INCH), int(round(th * (h / tot_rh)))) for h in raw_row_h]
        if sum(row_heights) != th:
            row_heights[-1] = max(int(0.24 * EMU_PER_INCH), row_heights[-1] + (th - sum(row_heights)))

        table_cells: list[list[lm.CellEl]] = []
        for ri, r in enumerate(rows_data):
            is_head = bool(r.get("head")) or (ri == 0 and any(c.get("th") for c in (r.get("cells") or [])))
            row_h = row_heights[ri]
            cell_bg = (
                _mix(brand.roles.surface, brand.roles.primary, 0.14 if brand.design.dark else 0.10)
                if is_head
                else (
                    brand.roles.surface
                    if ri % 2 == 1
                    else _mix(brand.roles.surface, brand.roles.background, 0.30)
                )
            )
            row_cells: list[lm.CellEl] = []
            col_cursor = 0
            for c in r.get("cells") or []:
                if col_cursor >= n_cols:
                    break
                c_span = max(1, int(c.get("cs") or 1))
                r_span = max(1, int(c.get("rs") or 1))
                cw = sum(col_widths[col_cursor : min(n_cols, col_cursor + c_span)])
                col_cursor += c_span

                c_align_raw = (c.get("ta") or "left").lower()
                c_align: Literal["left", "center", "right", "justify"] = (
                    c_align_raw if c_align_raw in ("left", "center", "right", "justify") else "left"  # type: ignore[assignment]
                )
                fake_node = {
                    "cls": "",
                    "fs": node.get("fs") or 12.5,
                    "fw": 700 if (is_head or c.get("b")) else 400,
                    "c": c.get("c") or node.get("c"),
                    "ta": c_align,
                }
                c_paras = convert_runs_to_paras(
                    c.get("runs") or [],
                    fake_node,
                    cell_bg,
                    is_heading=is_head,
                    inside_card=True,
                    force_align=c_align,
                )
                # Fit cell paragraphs within evaluate.py's (col_w - 200000) width budget
                avail_cw = max(int(0.3 * EMU_PER_INCH), cw - 200000)
                _autofit_paras_strict(c_paras, avail_cw, int(row_h * 0.96), min_font_pt=6.5)

                row_cells.append(
                    lm.CellEl(
                        paras=c_paras,
                        fill=cell_bg,
                        colspan=c_span,
                        rowspan=r_span,
                        border_bottom=brand.roles.border,
                        border_bottom_w=1.25 if is_head else 0.75,
                        valign="middle",
                        is_header=is_head,
                    )
                )
            table_cells.append(row_cells)

        sm.elements.append(
            lm.TableEl(
                name=f"Table {node.get('caption') or node.get('id', '')}".strip(),
                x=tx,
                y=ty,
                w=tw,
                h=th,
                col_widths=col_widths,
                row_heights=row_heights,
                rows=table_cells,
            )
        )

    def walk_dom(
        node: dict,
        effective_bg: str,
        parent_rect: tuple[int, int, int, int] | None = None,
        inside_card: bool = False,
        in_kpi: bool = False,
        next_sibling_y: int | None = None,
    ) -> None:
        tag = (node.get("tag") or "").lower()
        cls = (node.get("cls") or "").lower()
        v = node.get("v") or {}
        box = node.get("box") or {}

        # 1. Special terminal tags: table, list, chart, img, canvas, svg, hr
        if tag == "table":
            emit_table_node(node, effective_bg)
            return
        if tag == "list":
            emit_list_node(node, effective_bg, parent_rect, inside_card)
            return
        if tag == "chart":
            c_idx = node.get("chart")
            if isinstance(c_idx, int) and 0 <= c_idx < len(charts_list):
                raw_spec = charts_list[c_idx].get("spec") or {}
                try:
                    spec = ir.ChartSpec.model_validate(raw_spec)
                    cx, cy, cw, ch = to_emu_rect(box)
                    sm.elements.append(
                        lm.ChartEl(
                            name=f"Chart {spec.title or c_idx}",
                            x=cx,
                            y=cy,
                            w=cw,
                            h=ch,
                            spec=spec,
                            palette=list(brand.chart_palette),
                            font=brand.fonts.body.family,
                            font_size=10.0,
                            text_color=brand.roles.text,
                            grid_color=brand.roles.border,
                            legend=spec.show_legend,
                            title=bool(spec.title),
                        )
                    )
                except Exception:
                    pass
            return
        if tag in ("img", "canvas"):
            img_info = node.get("img")
            if img_info and img_info.get("b64"):
                ix, iy, iw, ih = to_emu_rect(box)
                sm.elements.append(
                    lm.ImageEl(
                        name=f"Image {node.get('alt') or node.get('id', '')}".strip(),
                        x=ix,
                        y=iy,
                        w=iw,
                        h=ih,
                        mime=img_info.get("mime", "image/png"),
                        b64=img_info["b64"],
                    )
                )
            return
        if tag == "svg":
            if node.get("svg"):
                sx_e, sy_e, sw_e, sh_e = to_emu_rect(box)
                sm.elements.append(
                    lm.SvgEl(
                        name=f"Vector {node.get('id', '')}".strip(),
                        x=sx_e,
                        y=sy_e,
                        w=sw_e,
                        h=sh_e,
                        svg=node["svg"],
                    )
                )
            return
        if tag == "hr":
            hx, hy, hw, hh = to_emu_rect(box)
            sm.elements.append(
                lm.ShapeEl(
                    name="Divider Line",
                    x=hx,
                    y=hy,
                    w=hw,
                    h=max(int(1.0 * EMU_PER_PT), min(hh, int(2.0 * EMU_PER_PT))),
                    geom="line",
                    fill=brand.roles.border,
                )
            )
            return

        # 2. Compact self-contained Badge / Pill / Button / Icon Box
        if _is_compact_badge_or_button(node):
            bx, by, bw, bh = to_emu_rect(box)
            fill_col, grad_cols, fill_a = _map_container_fill(node, brand, effective_bg, is_badge_or_btn=True)
            b_info = v.get("border")
            line_col = _map_border_color(b_info["c"], cls, brand, is_accent=True) if b_info else None
            line_w = min(1.5, max(0.75, float(b_info["w"]) * 0.75)) if b_info else 0.0
            dash = bool(b_info.get("dash")) if b_info else False

            rad_px = float(v.get("radius") or 0.0)
            geom: Literal["rect", "roundRect"] = "roundRect" if rad_px > 0 else "rect"
            adj = _compute_roundrect_adj(rad_px, float(box.get("w", 40)), float(box.get("h", 24)), is_badge=True)

            badge_bg = fill_col or effective_bg
            is_centered = (
                tag == "button"
                or node.get("ta") == "center"
                or node.get("jc") == "center"
                or float(box.get("w", 100)) <= 50
            )
            paras = convert_runs_to_paras(
                node.get("runs") or [],
                node,
                badge_bg,
                inside_card=True,
                force_align="center" if is_centered else None,
            )
            inset_emu = int(1.5 * EMU_PER_PT)
            _autofit_paras_strict(
                paras,
                max(int(0.2 * EMU_PER_INCH), bw - 2 * inset_emu),
                max(int(6 * EMU_PER_PT), bh - 2 * inset_emu),
                min_font_pt=6.0,
            )
            is_icon_box = float(box.get("w", 100)) <= 48 and float(box.get("h", 100)) <= 48
            sm.elements.append(
                lm.ShapeEl(
                    name=f"{'Icon' if is_icon_box else 'Badge'} {node.get('id', '')}",
                    x=bx,
                    y=by,
                    w=bw,
                    h=bh,
                    geom=geom,
                    radius=adj,
                    fill=fill_col,
                    fill_alpha=fill_a,
                    gradient=grad_cols,
                    line=line_col,
                    line_w=line_w,
                    dash=dash,
                    paras=paras,
                    valign="middle",
                    inset=inset_emu,
                )
            )
            return

        # 3. Check container visual styling (backgrounds, borders, partial borders, topBar)
        borders_list = v.get("borders")  # [Top, Right, Bottom, Left]
        live_sides = [i for i, b in enumerate(borders_list or []) if b]
        has_bg = bool(v.get("bg") or v.get("grad"))
        has_full_border = bool(v.get("border")) or len(live_sides) >= 2

        cur_bg = effective_bg
        cur_parent_rect = parent_rect
        cur_inside_card = inside_card
        cur_in_kpi = in_kpi or ("kpi" in cls)

        if has_bg or has_full_border:
            cx, cy, cw, ch = to_emu_rect(box)
            fill_col, grad_cols, fill_a = _map_container_fill(node, brand, effective_bg, is_badge_or_btn=False)
            if fill_col:
                cur_bg = fill_col

            # Resolve main 4-sided border
            line_col: str | None = None
            line_w: float = 0.0
            dash: bool = False
            if v.get("border"):
                b_info = v["border"]
                line_col = _map_border_color(b_info.get("c"), cls, brand, is_accent=bool(b_info.get("dash")))
                line_w = min(1.5, max(0.75, float(b_info.get("w", 1.0)) * 0.75))
                dash = bool(b_info.get("dash"))
            elif borders_list and len(live_sides) >= 2:
                # Find the thinner base border for the 4-sided frame
                min_b = min((borders_list[i] for i in live_sides), key=lambda x: float(x.get("w", 1.0)))
                line_col = _map_border_color(min_b.get("c"), None, brand, is_accent=False)
                line_w = min(1.25, max(0.75, float(min_b.get("w", 1.0)) * 0.75))
                dash = bool(min_b.get("dash"))

            rad_px = float(v.get("radius") or 0.0)
            geom_c: Literal["rect", "roundRect"] = "roundRect" if rad_px > 0 else "rect"
            adj_c = _compute_roundrect_adj(rad_px, float(box.get("w", 100)), float(box.get("h", 100)))

            if _has_descendant_card(node):
                shape_name = f"Column Container {node.get('id', '')}"
            elif cur_in_kpi:
                shape_name = f"KPI Card {node.get('id', '')}"
            else:
                shape_name = f"Card {node.get('id', '')}"

            sm.elements.append(
                lm.ShapeEl(
                    name=shape_name,
                    x=cx,
                    y=cy,
                    w=cw,
                    h=ch,
                    geom=geom_c,
                    radius=adj_c,
                    fill=fill_col,
                    fill_alpha=fill_a,
                    gradient=grad_cols,
                    gradient_angle=float(v.get("gradAngle") or 90.0),
                    line=line_col,
                    line_w=line_w,
                    dash=dash,
                )
            )

            # Emit thick accent side border strips (e.g., border-left, border-right, border-top on .v-step / .step / .card)
            if borders_list:
                for side_idx, b_side in enumerate(borders_list):
                    if not b_side:
                        continue
                    bw_px = float(b_side.get("w", 0.0))
                    if bw_px >= 2.2:
                        acc_col = _map_border_color(b_side.get("c"), cls, brand, is_accent=True)
                        bar_thick = max(int(2.5 * EMU_PER_PT), int(round(bw_px * scale_x)))
                        if side_idx == 0:  # Top
                            sm.elements.append(
                                lm.ShapeEl(
                                    name="Card Top Accent",
                                    x=cx,
                                    y=cy,
                                    w=cw,
                                    h=bar_thick,
                                    geom="rect",
                                    fill=acc_col,
                                )
                            )
                        elif side_idx == 1:  # Right
                            sm.elements.append(
                                lm.ShapeEl(
                                    name="Card Tone Accent",
                                    x=cx + cw - bar_thick,
                                    y=cy,
                                    w=bar_thick,
                                    h=ch,
                                    geom="rect",
                                    fill=acc_col,
                                )
                            )
                        elif side_idx == 2:  # Bottom
                            sm.elements.append(
                                lm.ShapeEl(
                                    name="Card Tone Accent",
                                    x=cx,
                                    y=cy + ch - bar_thick,
                                    w=cw,
                                    h=bar_thick,
                                    geom="rect",
                                    fill=acc_col,
                                )
                            )
                        elif side_idx == 3:  # Left
                            sm.elements.append(
                                lm.ShapeEl(
                                    name="Card Tone Accent",
                                    x=cx,
                                    y=cy,
                                    w=bar_thick,
                                    h=ch,
                                    geom="rect",
                                    fill=acc_col,
                                )
                            )

            # Emit ::before topBar accent strip (e.g. .kpi::before)
            if v.get("topBar"):
                tb = v["topBar"]
                bar_h = max(int(2.5 * EMU_PER_PT), int(round(float(tb.get("h", 3.0)) * scale_y)))
                sm.elements.append(
                    lm.ShapeEl(
                        name="Card Top Accent",
                        x=cx,
                        y=cy,
                        w=cw,
                        h=bar_h,
                        geom="rect",
                        fill=brand.roles.primary,
                    )
                )

            cur_parent_rect = (cx, cy, cw, ch)
            cur_inside_card = True

        elif borders_list and len(live_sides) == 1:
            # Single-edge border with no background fill (e.g. .slide-footer, .step-deliverable, blockquote)
            cx, cy, cw, ch = to_emu_rect(box)
            side_idx = live_sides[0]
            b_side = borders_list[side_idx]
            bw_px = float(b_side.get("w", 1.0))
            is_acc = bw_px >= 2.2 or _color_tone(b_side.get("c")) != "none"
            line_col = _map_border_color(b_side.get("c"), cls if is_acc else None, brand, is_accent=is_acc)
            thick = max(int(0.8 * EMU_PER_PT), int(round(bw_px * scale_x)))
            if side_idx == 0:  # Top divider line
                sm.elements.append(
                    lm.ShapeEl(
                        name="Divider Line",
                        x=cx,
                        y=cy,
                        w=cw,
                        h=int(1.2 * scale_y),
                        geom="line",
                        fill=line_col,
                    )
                )
            elif side_idx == 3:  # Left accent bar (e.g. blockquote)
                sm.elements.append(
                    lm.ShapeEl(
                        name="Card Tone Accent",
                        x=cx,
                        y=cy,
                        w=thick,
                        h=ch,
                        geom="rect",
                        fill=line_col,
                    )
                )

        # 4. Emit text runs or walk child nodes (passing next_sibling_y for vertically stacked siblings)
        if node.get("runs"):
            emit_text_node(node, cur_bg, cur_parent_rect, cur_inside_card, cur_in_kpi, next_sibling_y=next_sibling_y)
        kids = node.get("kids") or []
        for idx, k in enumerate(kids):
            kb = k.get("box") or {}
            kx1 = float(kb.get("x", 0))
            kx2 = kx1 + float(kb.get("w", 0))
            ky2 = float(kb.get("y", 0)) + float(kb.get("h", 0))
            sib_y: int | None = None
            for nxt in kids[idx + 1 :]:
                nb = nxt.get("box") or {}
                nx1 = float(nb.get("x", 0))
                nx2 = nx1 + float(nb.get("w", 0))
                ny1 = float(nb.get("y", 0))
                # Vertically below and horizontally overlapping
                if ny1 >= ky2 - 2.0 and min(kx2, nx2) - max(kx1, nx1) > 10.0:
                    _, ny_emu, _, _ = to_emu_rect(nb)
                    sib_y = ny_emu if sib_y is None else min(sib_y, ny_emu)
            walk_dom(k, cur_bg, cur_parent_rect, cur_inside_card, cur_in_kpi, next_sibling_y=sib_y)

    for child in snode.get("kids") or []:
        walk_dom(child, brand.roles.background, None, False, False)

    return sm
