"""Direct DOM Mapper: DOM Snapshot -> Positioned SlideModel.

Preserves Chromium's computed Flexbox/Grid/Table geometry (x, y, w, h) while
re-theming surfaces, borders, badges, typography, and Semantic Tone colors to
the active BrandProfile across any Curated Palette and dark/light mode.

Structured into:
- `DomNode`: typed, readable view over raw `dom_snapshot.js` dictionaries.
- `ToneMapper`: unified BrandProfile surface, border, and text color resolver
  delegating to `classifier.color_tone` / `cls_tone` / `node_tone`,
  `engine.tone_color`, and `presets.ensure_contrast` / `mix_colors`.
- `DirectMapperContext` & per-tag emitters (`emit_table_node`, `emit_list_node`,
  `emit_chart_node`, `emit_media_node`, `emit_svg_node`, `emit_badge_node`,
  `emit_text_node`, `walk_dom`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.analyze.classifier import cls_tone, color_tone, node_tone, normalize_chart
from htmlppt.brand.model import BrandProfile, hex_to_rgb
from htmlppt.brand.presets import ensure_contrast, mix_colors, relative_luminance
from htmlppt.layout import model as lm
from htmlppt.layout.engine import _add_footer, tone_color
from htmlppt.layout.grid import SlideGeometry
from htmlppt.layout.textfit import (
    estimate_paras_height_emu,
    estimate_text_height_emu,
    scale_paras_to_fit,
)

# Standard PowerPoint internal cell horizontal margin (left + right = 2 * 100000 EMU)
_PPTX_TABLE_CELL_HORIZ_MARGIN_EMU = 200000
_OOXML_CORNER_DENOM_PX = 320.0
_VERT_PAD_BUFFER_PX = 6.0


def clean_hex(c: str | None) -> str | None:
    """Normalize a 3- or 6-digit hex color string to uppercase 6-char hex without '#'."""
    if not c:
        return None
    h = c.strip().lstrip("#").upper()
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return h if len(h) == 6 else None


def _is_saturated_accent(hex_col: str | None) -> bool:
    h = clean_hex(hex_col)
    if not h:
        return False
    r, g, b = hex_to_rgb(h)
    mx, mn = max(r, g, b), min(r, g, b)
    return (mx - mn) > 65 and mx > 90


@dataclass(frozen=True)
class DomNode:
    """Typed view over a raw DOM snapshot dictionary emitted by `dom_snapshot.js`."""

    raw: dict[str, Any]

    @property
    def id(self) -> Any:
        return self.raw.get("id", "")

    @property
    def tag(self) -> str:
        return str(self.raw.get("tag") or "").lower()

    @property
    def cls(self) -> str:
        return str(self.raw.get("cls") or "")

    @property
    def cls_lower(self) -> str:
        return self.cls.lower()

    @property
    def box(self) -> dict[str, float]:
        b = self.raw.get("box") or {}
        return {
            "x": float(b.get("x", 0.0)),
            "y": float(b.get("y", 0.0)),
            "w": float(b.get("w", 0.0)),
            "h": float(b.get("h", 0.0)),
        }

    @property
    def tbox(self) -> dict[str, float]:
        tb = self.raw.get("tbox")
        if not tb:
            return self.box
        return {
            "x": float(tb.get("x", 0.0)),
            "y": float(tb.get("y", 0.0)),
            "w": float(tb.get("w", 0.0)),
            "h": float(tb.get("h", 0.0)),
        }

    @property
    def v(self) -> dict[str, Any]:
        return self.raw.get("v") or {}

    @property
    def bg(self) -> str | None:
        return clean_hex(self.v.get("bg"))

    @property
    def bg_alpha(self) -> float:
        return float(self.v.get("bgA", 1.0))

    @property
    def grad(self) -> list[str]:
        return [h for c in (self.v.get("grad") or []) if (h := clean_hex(c)) is not None]

    @property
    def grad_alpha(self) -> float:
        return float(self.v.get("gradA", 1.0))

    @property
    def grad_angle(self) -> float:
        return float(self.v.get("gradAngle") or 90.0)

    @property
    def border(self) -> dict[str, Any] | None:
        return self.v.get("border")

    @property
    def borders(self) -> list[dict[str, Any] | None] | None:
        return self.v.get("borders")

    @property
    def radius_px(self) -> float:
        return float(self.v.get("radius") or 0.0)

    @property
    def top_bar(self) -> dict[str, Any] | None:
        return self.v.get("topBar")

    @property
    def pad(self) -> list[float] | None:
        p = self.raw.get("pad")
        if not p or len(p) < 4:
            return None
        return [float(x) for x in p[:4]]

    @property
    def runs(self) -> list[dict[str, Any]]:
        return self.raw.get("runs") or []

    @property
    def kids(self) -> list[DomNode]:
        return [DomNode(k) for k in (self.raw.get("kids") or [])]

    @property
    def text_align(self) -> str:
        return str(self.raw.get("ta") or "left").lower()

    @property
    def flex_dir(self) -> str | None:
        return self.raw.get("flex")

    @property
    def justify_content(self) -> str | None:
        return self.raw.get("jc")

    @property
    def font_size_px(self) -> float:
        return float(self.raw.get("fs") or 14.0)

    @property
    def font_weight(self) -> int:
        return int(self.raw.get("fw") or 400)

    @property
    def color_hex(self) -> str | None:
        return clean_hex(self.raw.get("c"))

    @property
    def dot(self) -> dict[str, Any] | None:
        return self.raw.get("dot")

    def text_content(self) -> str:
        if self.runs:
            return "".join(str(r.get("t", "")) for r in self.runs)
        if self.tag == "list":
            return " ".join(
                "".join(str(r.get("t", "")) for r in (it.get("runs") or []))
                for it in (self.raw.get("items") or [])
            )
        return " ".join(k.text_content() for k in self.kids)


class ToneMapper:
    """Resolves BrandProfile surfaces, borders, and text colors from DOM visual properties."""

    def __init__(self, brand: BrandProfile) -> None:
        self.brand = brand

    def resolve_tone_color(self, tone: str | None, bg_hex: str, min_contrast: float = 3.8) -> str:
        base = tone_color(tone, self.brand, default=self.brand.roles.primary)
        return ensure_contrast(base, bg_hex, min_contrast=min_contrast)

    def infer_node_tone(self, node: DomNode) -> str:
        tone = node_tone(node.raw, default="none")
        if tone != "none":
            return tone
        # Check child text runs or badges for semantic color cues when container border/fill is neutral
        for k in node.kids:
            kt = node_tone(k.raw, default="none")
            if kt != "none":
                return kt
        return "none"

    def map_container_fill(
        self,
        node: DomNode,
        parent_bg: str,
        is_badge_or_btn: bool = False,
    ) -> tuple[str | None, list[str] | None, float]:
        """Return (fill_hex, gradient_stops, fill_alpha) mapped to the active BrandProfile."""
        bg = node.bg
        bg_alpha = node.bg_alpha
        grad = node.grad
        grad_alpha = node.grad_alpha
        tone = self.infer_node_tone(node)

        # 1. Prominent opaque gradient banner vs low-alpha tinted surface card
        if len(grad) >= 2:
            if grad_alpha >= 0.5 and any(_is_saturated_accent(g) for g in grad):
                g1 = self.brand.colors.accent1 or self.brand.roles.primary
                g2 = self.brand.colors.accent2 or self.brand.roles.secondary or g1
                if self.brand.design.dark and relative_luminance(g1) > 0.45:
                    g1 = mix_colors(g1, self.brand.roles.background, 0.45)
                return g1, [g1, g2], 1.0
            if tone != "none":
                t_col = self.resolve_tone_color(tone, self.brand.roles.surface)
                tinted = mix_colors(
                    self.brand.roles.surface,
                    t_col,
                    0.10 if self.brand.design.dark else 0.08,
                )
                return tinted, None, 1.0
            return self.brand.roles.surface, None, 1.0

        if not bg:
            return None, None, 1.0

        # 2. High-contrast solid button (e.g. light action button on dark/accent banner)
        if node.tag == "button" or "btn" in node.cls_lower or "button" in node.cls_lower:
            bg_lum = relative_luminance(bg)
            if bg_lum > 0.80:
                return (
                    self.brand.roles.on_primary if self.brand.design.dark else self.brand.roles.primary
                ), None, 1.0
            return self.brand.roles.primary, None, 1.0

        # 3. Compact badge, pill, or icon box (or translucent tint container)
        if is_badge_or_btn or bg_alpha <= 0.45:
            if tone != "none":
                t_col = self.resolve_tone_color(tone, parent_bg)
                fill_mix = 0.22 if self.brand.design.dark else 0.14
                return mix_colors(parent_bg, t_col, fill_mix), None, 1.0
            return (
                mix_colors(
                    parent_bg,
                    self.brand.roles.primary,
                    0.18 if self.brand.design.dark else 0.12,
                ),
                None,
                1.0,
            )

        # 4. Standard Card / KPI / Callout surface (with dashed-border hub or nested card distinction)
        if node.border and node.border.get("dash"):
            return mix_colors(self.brand.roles.surface, self.brand.roles.primary, 0.10), None, 1.0
        if parent_bg.upper() == self.brand.roles.surface.upper():
            return mix_colors(self.brand.roles.surface, self.brand.roles.background, 0.35), None, 1.0
        return self.brand.roles.surface, None, 1.0

    def map_border_color(self, raw_c: str | None, cls: str | None, is_accent: bool = False) -> str:
        tone = color_tone(raw_c)
        if tone == "none":
            tone = cls_tone(cls)
        if tone != "none" or is_accent:
            return self.resolve_tone_color(
                tone if tone != "none" else "primary",
                self.brand.roles.surface,
            )
        return self.brand.roles.border

    def map_text_color(
        self,
        raw_c: str | None,
        cls: str | None,
        fw: int,
        effective_bg: str,
        is_heading: bool = False,
    ) -> str:
        bg_lum = relative_luminance(effective_bg)
        # High-luminance button surface in dark mode (e.g. light CTA button)
        if bg_lum > 0.82 and self.brand.design.dark:
            dark_ink = self.brand.colors.dk2 or self.brand.roles.background
            return ensure_contrast(dark_ink, effective_bg, min_contrast=4.5)
        # Saturated dark accent banner
        if _is_saturated_accent(effective_bg) and bg_lum < 0.45:
            h = clean_hex(raw_c)
            if h and relative_luminance(h) > 0.70:
                return ensure_contrast(self.brand.roles.on_primary, effective_bg, min_contrast=4.5)

        tone = color_tone(raw_c)
        if tone == "none":
            tone = cls_tone(cls)
        if tone != "none":
            return self.resolve_tone_color(tone, effective_bg)

        if is_heading or fw >= 600:
            return ensure_contrast(self.brand.roles.text, effective_bg, min_contrast=4.5)

        # Preserve muted / secondary text hierarchy from source luminance
        h = clean_hex(raw_c)
        if h:
            lum = relative_luminance(h)
            if lum < 0.22:
                return ensure_contrast(self.brand.roles.muted, effective_bg, min_contrast=3.5)
            if lum < 0.55:
                sec = mix_colors(self.brand.roles.text, self.brand.roles.muted, 0.42)
                return ensure_contrast(sec, effective_bg, min_contrast=4.0)

        return ensure_contrast(self.brand.roles.text, effective_bg, min_contrast=4.5)


def compute_roundrect_adj(radius_px: float, w_px: float, h_px: float, is_badge: bool = False) -> float:
    """Compute OOXML roundRect adjustment (0..0.5) from CSS border-radius in px."""
    if radius_px <= 0 or min(w_px, h_px) <= 0:
        return 0.0
    if is_badge and (radius_px >= 16 or radius_px >= h_px * 0.42):
        return 0.12
    return min(0.06, max(0.015, round(radius_px / _OOXML_CORNER_DENOM_PX, 3)))


def has_descendant_card(node: DomNode) -> bool:
    """Return True if node has a descendant container covering >=34% of node's area with its own visual box."""
    nb = node.box
    n_area = max(1.0, nb["w"] * nb["h"])

    def _check(kids: list[DomNode]) -> bool:
        for k in kids:
            if k.bg or k.grad or k.border or k.borders:
                kb = k.box
                if (kb["w"] * kb["h"]) >= 0.34 * n_area:
                    return True
            if _check(k.kids):
                return True
        return False

    return _check(node.kids)


def is_compact_badge_or_button(node: DomNode) -> bool:
    """Return True if node is a leaf visual element (badge, pill, button, icon box) with text runs and no kids."""
    if node.kids or not node.runs:
        return False
    if not (node.bg or node.grad or node.border):
        return False
    w, h = node.box["w"], node.box["h"]
    if node.tag == "button" or any(k in node.cls_lower for k in ("badge", "pill", "icon", "btn", "tag")):
        return True
    return h <= 56.0 and w <= 340.0


def autofit_paras_strict(
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


@dataclass
class DirectMapperContext:
    """Shared coordinate-conversion and BrandProfile state for a single slide."""

    slide: ir.Slide
    brand: BrandProfile
    sm: lm.SlideModel
    tones: ToneMapper
    sx: float
    sy: float
    sw: float
    sh: float
    W: int
    H: int
    scale_x: float
    scale_y: float
    pt_scale: float
    charts_list: list[dict[str, Any]] = field(default_factory=list)
    title_assigned: bool = False
    has_dom_footer: bool = False

    def to_emu_rect(self, box: dict[str, float]) -> tuple[int, int, int, int]:
        rx = int(round((float(box.get("x", self.sx)) - self.sx) * self.scale_x))
        ry = int(round((float(box.get("y", self.sy)) - self.sy) * self.scale_y))
        rw = max(int(2 * EMU_PER_PT), int(round(float(box.get("w", 4.0)) * self.scale_x)))
        rh = max(int(2 * EMU_PER_PT), int(round(float(box.get("h", 4.0)) * self.scale_y)))
        if rx < 0:
            rw = max(int(2 * EMU_PER_PT), rw + rx)
            rx = 0
        if ry < 0:
            rh = max(int(2 * EMU_PER_PT), rh + ry)
            ry = 0
        if rx + rw > self.W:
            rw = max(int(2 * EMU_PER_PT), self.W - rx)
        if ry + rh > self.H:
            rh = max(int(2 * EMU_PER_PT), self.H - ry)
        return rx, ry, rw, rh

    def convert_runs_to_paras(
        self,
        raw_runs: list[dict[str, Any]],
        node: DomNode,
        effective_bg: str,
        *,
        is_heading: bool = False,
        is_kpi_val: bool = False,
        is_slide_title: bool = False,
        inside_card: bool = False,
        force_align: Literal["left", "center", "right", "justify"] | None = None,
    ) -> list[lm.Para]:
        groups: list[list[dict[str, Any]]] = [[]]
        for r in raw_runs:
            if r.get("t") == "\n":
                if groups[-1]:
                    groups.append([])
            else:
                groups[-1].append(r)

        ta = force_align or node.text_align
        if ta not in ("left", "center", "right", "justify"):
            ta = "left"
        align: Literal["left", "center", "right", "justify"] = ta  # type: ignore[assignment]

        node_cls = node.cls
        node_fs = node.font_size_px
        node_fw = node.font_weight
        max_card_pt = max(16.0, float(self.brand.fonts.sizes.body))

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
                sz_pt = round(max(6.5, r_fs * self.pt_scale), 1)
                if not is_slide_title and not is_kpi_val and inside_card:
                    sz_pt = min(max_card_pt, sz_pt)
                elif not is_slide_title and not is_kpi_val and not is_heading:
                    sz_pt = min(max_card_pt, sz_pt)

                r_bold = bool(r.get("b") or node_fw >= 600 or is_heading or is_kpi_val or r.get("hl"))
                r_hl = clean_hex(r.get("hl"))
                r_col_raw = r.get("c") or node.color_hex

                if r_hl:
                    hl_tone = color_tone(r_hl)
                    if hl_tone == "none":
                        hl_tone = color_tone(r_col_raw)
                    col = self.tones.resolve_tone_color(
                        hl_tone if hl_tone != "none" else "primary",
                        effective_bg,
                    )
                    if r_idx + 1 < len(g):
                        nxt_t = str(g[r_idx + 1].get("t", ""))
                        if not txt.endswith(" ") and nxt_t and not nxt_t.startswith(" "):
                            txt = txt + " "
                else:
                    col = self.tones.map_text_color(
                        r_col_raw,
                        node_cls,
                        700 if r_bold else node_fw,
                        effective_bg,
                        is_heading=(is_heading or is_slide_title),
                    )

                font_fam = (
                    self.brand.fonts.heading.family
                    if (is_heading or is_slide_title or is_kpi_val)
                    else self.brand.fonts.body.family
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
    ctx: DirectMapperContext,
    node: DomNode,
    effective_bg: str,
    parent_rect: tuple[int, int, int, int] | None,
    inside_card: bool,
    in_kpi: bool,
    next_sibling_y: int | None = None,
) -> None:
    raw_runs = node.runs
    if not raw_runs:
        return

    tag = node.tag
    cls = node.cls_lower
    box = node.box
    tbox = node.tbox

    if tag == "footer" or "slide-footer" in cls or "footer" in cls.split():
        ctx.has_dom_footer = True

    # Emit decorative dot if present (e.g. .kicker .dot)
    if node.dot:
        d_info = node.dot
        dx, dy, dw, dh = ctx.to_emu_rect(d_info["box"])
        d_col = ctx.tones.map_border_color(d_info.get("c"), cls, is_accent=True)
        ctx.sm.elements.append(
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
    if not ctx.title_assigned and tag in ("h1", "h2") and not inside_card:
        is_slide_title = True
        ctx.title_assigned = True

    is_heading = tag in ("h1", "h2", "h3", "h4", "h5", "h6") or "title" in cls
    is_kpi_val = (in_kpi and ("value" in cls or node.font_size_px >= 28)) or (
        "value" in cls and node.font_size_px >= 30
    )

    # Sync IR title edits onto the slide title run if modified after segmentation
    runs_to_use = raw_runs
    if (
        is_slide_title
        and ctx.slide.title
        and ctx.slide.initial_title
        and ctx.slide.title != ctx.slide.initial_title
    ):
        first_style = dict(raw_runs[0]) if raw_runs else {"fs": node.font_size_px, "b": 1}
        first_style["t"] = ctx.slide.title
        runs_to_use = [first_style]

    paras = ctx.convert_runs_to_paras(
        runs_to_use,
        node,
        effective_bg,
        is_heading=is_heading,
        is_kpi_val=is_kpi_val,
        is_slide_title=is_slide_title,
        inside_card=inside_card,
    )
    if not paras:
        return

    if node.dot and tbox["w"] > 0:
        use_box = {
            "x": tbox["x"],
            "y": min(box["y"], tbox["y"]),
            "w": max(tbox["w"], (box["x"] + box["w"]) - tbox["x"]),
            "h": max(box["h"], tbox["h"]),
        }
    else:
        use_box = {
            "x": box["x"] if box["w"] > 0 else tbox["x"],
            "y": box["y"] if box["h"] > 0 else tbox["y"],
            "w": max(box["w"], tbox["w"]),
            "h": max(box["h"], tbox["h"]),
        }

    rx, ry, rw, rh = ctx.to_emu_rect(use_box)

    pad = node.pad
    if pad and not node.bg:
        pl_emu = int(round(pad[3] * ctx.scale_x))
        pt_emu = int(round(pad[0] * ctx.scale_y))
        if 0 < pl_emu < rw // 2:
            rx += pl_emu
            rw -= pl_emu
        if 0 < pt_emu < rh // 2:
            ry += pt_emu
            rh -= pt_emu

    # Single-line inline/shrink-wrapped width buffer so textfit/PowerPoint never wraps a 1-line label onto 2 lines
    fs_px = node.font_size_px
    was_single_line = use_box["h"] <= fs_px * 1.95 and len(paras) == 1
    if was_single_line:
        p0 = paras[0]
        full_txt = "".join(r.text for r in p0.runs)
        max_sz = max((r.size for r in p0.runs), default=10.0)
        bold_any = any(r.bold for r in p0.runs)
        font_fam = p0.runs[0].font if p0.runs else ctx.brand.fonts.body.family
        one_line_h = int(max_sz * 1.35 * EMU_PER_PT)
        est_at_rw = estimate_text_height_emu(full_txt, rw, font_fam, max_sz, line_spacing=1.15, bold=bold_any)
        if est_at_rw > one_line_h:
            needed_w = int(len(full_txt) * max_sz * (0.62 if bold_any else 0.56) * EMU_PER_PT) + int(10 * EMU_PER_PT)
            max_right = (
                (parent_rect[0] + parent_rect[2] - int(4 * EMU_PER_PT))
                if parent_rect
                else (ctx.W - int(12 * EMU_PER_PT))
            )
            if p0.align == "right" or (rx + needed_w > max_right and not inside_card):
                new_w = min(needed_w, max_right - (parent_rect[0] if parent_rect else int(12 * EMU_PER_PT)))
                if new_w > rw:
                    rx = max(parent_rect[0] if parent_rect else 0, rx - (new_w - rw))
                    rw = new_w
            else:
                rw = min(max(rw, needed_w), max(rw, max_right - rx))

    avail_in_parent = ((parent_rect[1] + parent_rect[3]) - ry) if parent_rect else (ctx.H - ry)
    if next_sibling_y is not None and next_sibling_y > ry:
        avail_in_parent = min(avail_in_parent, next_sibling_y - ry + int(2 * ctx.scale_y))
    avail_in_parent = max(int(8 * EMU_PER_PT), avail_in_parent)

    # Horizontal flex row with space-between (e.g. .slide-footer with left & right <span>)
    if node.justify_content == "space-between" and node.flex_dir in ("row", None) and len(paras) == 2:
        row_h = min(avail_in_parent, max(rh + int(5 * ctx.scale_y), int(16 * EMU_PER_PT)))
        left_p = [paras[0].model_copy(update={"align": "left", "space_before": 0.0, "space_after": 0.0})]
        right_p = [paras[1].model_copy(update={"align": "right", "space_before": 0.0, "space_after": 0.0})]
        left_w = int(rw * 0.58)
        right_w = int(rw * 0.38)
        autofit_paras_strict(left_p, left_w, max(int(8 * EMU_PER_PT), row_h - int(4 * ctx.scale_y)), min_font_pt=6.5)
        autofit_paras_strict(right_p, right_w, max(int(8 * EMU_PER_PT), row_h - int(4 * ctx.scale_y)), min_font_pt=6.5)
        ctx.sm.elements.append(
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
        ctx.sm.elements.append(
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

    rh = min(avail_in_parent, rh + int(_VERT_PAD_BUFFER_PX * ctx.scale_y))
    est_h = estimate_paras_height_emu(paras, rw, default_line_spacing=1.15)
    if est_h > rh and rh < avail_in_parent:
        rh = min(avail_in_parent, max(rh, int(est_h * 1.06)))

    fit_budget_h = max(int(8 * EMU_PER_PT), min(rh, avail_in_parent) - int(5 * ctx.scale_y))
    autofit_paras_strict(paras, rw, fit_budget_h, min_font_pt=6.5)

    if is_slide_title:
        el_name = "Slide Title"
    elif is_kpi_val:
        el_name = "KPI Text"
    elif inside_card:
        el_name = "Card Content"
    else:
        el_name = "Content Text"

    ctx.sm.elements.append(
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
    ctx: DirectMapperContext,
    node: DomNode,
    effective_bg: str,
    parent_rect: tuple[int, int, int, int] | None,
    inside_card: bool,
) -> None:
    items = node.raw.get("items") or []
    if not items:
        return
    rx, ry, rw, rh = ctx.to_emu_rect(node.box)
    ordered = bool(node.raw.get("ordered"))
    paras: list[lm.Para] = []
    for idx, it in enumerate(items):
        it_runs = it.get("runs") or []
        p_list = ctx.convert_runs_to_paras(
            it_runs,
            node,
            effective_bg,
            inside_card=inside_card,
        )
        if not p_list:
            continue
        b_char = it.get("bulletChar") or ("1." if ordered else "•")
        b_col_raw = it.get("bulletColor")
        b_col = (
            ctx.tones.map_border_color(b_col_raw, node.cls, is_accent=True)
            if b_col_raw
            else ctx.brand.roles.primary
        )
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

    avail_in_parent = ((parent_rect[1] + parent_rect[3]) - ry) if parent_rect else (ctx.H - ry)
    avail_in_parent = max(int(10 * EMU_PER_PT), avail_in_parent)
    rh = min(avail_in_parent, rh + int(_VERT_PAD_BUFFER_PX * ctx.scale_y))
    est_h = estimate_paras_height_emu(paras, rw, default_line_spacing=1.15)
    if est_h > rh and rh < avail_in_parent:
        rh = min(avail_in_parent, max(rh, int(est_h * 1.06)))

    fit_budget_h = max(int(8 * EMU_PER_PT), min(rh, avail_in_parent) - int(5 * ctx.scale_y))
    autofit_paras_strict(paras, rw, fit_budget_h, min_font_pt=6.5)

    ctx.sm.elements.append(
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


def emit_table_node(ctx: DirectMapperContext, node: DomNode) -> None:
    rows_data = node.raw.get("rows") or []
    if not rows_data:
        return
    tx, ty, tw, th = ctx.to_emu_rect(node.box)
    n_rows = len(rows_data)
    n_cols = max((sum(int(c.get("cs") or 1) for c in r.get("cells", [])) for r in rows_data), default=0)
    if n_cols <= 0:
        return

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

    raw_row_h = [max(18.0, float(r.get("h") or (th / ctx.scale_y / n_rows))) for r in rows_data]
    tot_rh = sum(raw_row_h)
    row_heights = [max(int(0.24 * EMU_PER_INCH), int(round(th * (h / tot_rh)))) for h in raw_row_h]
    if sum(row_heights) != th:
        row_heights[-1] = max(int(0.24 * EMU_PER_INCH), row_heights[-1] + (th - sum(row_heights)))

    table_cells: list[list[lm.CellEl]] = []
    for ri, r in enumerate(rows_data):
        is_head = bool(r.get("head")) or (ri == 0 and any(c.get("th") for c in (r.get("cells") or [])))
        row_h = row_heights[ri]
        cell_bg = (
            mix_colors(
                ctx.brand.roles.surface,
                ctx.brand.roles.primary,
                0.14 if ctx.brand.design.dark else 0.10,
            )
            if is_head
            else (
                ctx.brand.roles.surface
                if ri % 2 == 1
                else mix_colors(ctx.brand.roles.surface, ctx.brand.roles.background, 0.30)
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

            c_align_raw = str(c.get("ta") or "left").lower()
            c_align: Literal["left", "center", "right", "justify"] = (
                c_align_raw if c_align_raw in ("left", "center", "right", "justify") else "left"  # type: ignore[assignment]
            )
            cell_dom = DomNode(
                {
                    "cls": "",
                    "fs": node.font_size_px or 12.5,
                    "fw": 700 if (is_head or c.get("b")) else 400,
                    "c": c.get("c") or node.color_hex,
                    "ta": c_align,
                }
            )
            c_paras = ctx.convert_runs_to_paras(
                c.get("runs") or [],
                cell_dom,
                cell_bg,
                is_heading=is_head,
                inside_card=True,
                force_align=c_align,
            )
            avail_cw = max(int(0.3 * EMU_PER_INCH), cw - _PPTX_TABLE_CELL_HORIZ_MARGIN_EMU)
            autofit_paras_strict(c_paras, avail_cw, int(row_h * 0.96), min_font_pt=6.5)

            row_cells.append(
                lm.CellEl(
                    paras=c_paras,
                    fill=cell_bg,
                    colspan=c_span,
                    rowspan=r_span,
                    border_bottom=ctx.brand.roles.border,
                    border_bottom_w=1.25 if is_head else 0.75,
                    valign="middle",
                    is_header=is_head,
                )
            )
        table_cells.append(row_cells)

    ctx.sm.elements.append(
        lm.TableEl(
            name=f"Table {node.raw.get('caption') or node.id}".strip(),
            x=tx,
            y=ty,
            w=tw,
            h=th,
            col_widths=col_widths,
            row_heights=row_heights,
            rows=table_cells,
        )
    )


def emit_chart_node(ctx: DirectMapperContext, node: DomNode) -> None:
    cx, cy, cw, ch = ctx.to_emu_rect(node.box)
    c_idx = node.raw.get("chart")
    c_entry = next((c for c in ctx.charts_list if c.get("index") == c_idx), None)
    if c_entry is None and isinstance(c_idx, int) and 0 <= c_idx < len(ctx.charts_list):
        c_entry = ctx.charts_list[c_idx]
    if c_entry is not None:
        try:
            spec = normalize_chart(c_entry.get("spec") or {})
            ctx.sm.elements.append(
                lm.ChartEl(
                    name=f"Chart {spec.title or c_idx}",
                    x=cx,
                    y=cy,
                    w=cw,
                    h=ch,
                    spec=spec,
                    palette=list(ctx.brand.chart_palette),
                    font=ctx.brand.fonts.body.family,
                    font_size=10.0,
                    text_color=ctx.brand.roles.text,
                    grid_color=ctx.brand.roles.border,
                    legend=spec.show_legend,
                    title=bool(spec.title),
                )
            )
            return
        except Exception as e:
            ctx.slide.warnings.append(f"chart parse failed: {e}")
    else:
        ctx.slide.warnings.append(f"chart index {c_idx} missing from snapshot")

    # Zero-drop placeholder shape when chart spec fails to parse
    ctx.sm.elements.append(
        lm.ShapeEl(
            name=f"Chart Placeholder {node.id}".strip(),
            x=cx,
            y=cy,
            w=cw,
            h=ch,
            geom="roundRect",
            radius=0.03,
            fill=ctx.brand.roles.surface,
            line=ctx.brand.roles.border,
            line_w=1.0,
            paras=[
                lm.Para(
                    runs=[
                        lm.TRun(
                            text="[Chart]",
                            font=ctx.brand.fonts.body.family,
                            size=10.0,
                            color=ctx.brand.roles.muted,
                        )
                    ],
                    align="center",
                )
            ],
        )
    )


def emit_media_node(ctx: DirectMapperContext, node: DomNode) -> None:
    ix, iy, iw, ih = ctx.to_emu_rect(node.box)
    img_info = node.raw.get("img")
    if img_info and img_info.get("b64"):
        ctx.sm.elements.append(
            lm.ImageEl(
                name=f"Image {node.raw.get('alt') or node.id}".strip(),
                x=ix,
                y=iy,
                w=iw,
                h=ih,
                mime=img_info.get("mime", "image/png"),
                b64=img_info["b64"],
            )
        )
        return

    alt_text = str(node.raw.get("alt") or node.raw.get("src") or "Image").strip()
    ctx.slide.warnings.append(f"unresolved {node.tag} asset ({alt_text[:40]})")
    ctx.sm.elements.append(
        lm.ShapeEl(
            name=f"Image Placeholder {node.id}".strip(),
            x=ix,
            y=iy,
            w=iw,
            h=ih,
            geom="roundRect",
            radius=0.03,
            fill=ctx.brand.roles.surface,
            line=ctx.brand.roles.border,
            line_w=1.0,
            paras=[
                lm.Para(
                    runs=[
                        lm.TRun(
                            text=alt_text[:60],
                            font=ctx.brand.fonts.body.family,
                            size=9.5,
                            color=ctx.brand.roles.muted,
                        )
                    ],
                    align="center",
                )
            ],
        )
    )


def emit_svg_node(ctx: DirectMapperContext, node: DomNode) -> None:
    sx_e, sy_e, sw_e, sh_e = ctx.to_emu_rect(node.box)
    svg_str = node.raw.get("svg")
    if svg_str:
        ctx.sm.elements.append(
            lm.SvgEl(
                name=f"Vector {node.id}".strip(),
                x=sx_e,
                y=sy_e,
                w=sw_e,
                h=sh_e,
                svg=svg_str,
            )
        )
        return

    ctx.slide.warnings.append(f"empty svg markup on node {node.id}")
    ctx.sm.elements.append(
        lm.ShapeEl(
            name=f"Vector Placeholder {node.id}".strip(),
            x=sx_e,
            y=sy_e,
            w=sw_e,
            h=sh_e,
            geom="roundRect",
            radius=0.03,
            fill=ctx.brand.roles.surface,
            line=ctx.brand.roles.border,
            line_w=0.75,
        )
    )


def emit_badge_node(ctx: DirectMapperContext, node: DomNode, effective_bg: str) -> None:
    bx, by, bw, bh = ctx.to_emu_rect(node.box)
    fill_col, grad_cols, fill_a = ctx.tones.map_container_fill(node, effective_bg, is_badge_or_btn=True)
    b_info = node.border
    line_col = ctx.tones.map_border_color(b_info.get("c"), node.cls, is_accent=True) if b_info else None
    line_w = min(1.5, max(0.75, float(b_info.get("w", 1.0)) * 0.75)) if b_info else 0.0
    dash = bool(b_info.get("dash")) if b_info else False

    rad_px = node.radius_px
    geom: Literal["rect", "roundRect"] = "roundRect" if rad_px > 0 else "rect"
    adj = compute_roundrect_adj(rad_px, node.box["w"], node.box["h"], is_badge=True)

    badge_bg = fill_col or effective_bg
    is_centered = (
        node.tag == "button"
        or node.text_align == "center"
        or node.justify_content == "center"
        or node.box["w"] <= 50.0
    )
    paras = ctx.convert_runs_to_paras(
        node.runs,
        node,
        badge_bg,
        inside_card=True,
        force_align="center" if is_centered else None,
    )
    inset_emu = int(1.5 * EMU_PER_PT)
    autofit_paras_strict(
        paras,
        max(int(0.2 * EMU_PER_INCH), bw - 2 * inset_emu),
        max(int(6 * EMU_PER_PT), bh - 2 * inset_emu),
        min_font_pt=6.0,
    )
    is_icon_box = node.box["w"] <= 48.0 and node.box["h"] <= 48.0
    ctx.sm.elements.append(
        lm.ShapeEl(
            name=f"{'Icon' if is_icon_box else 'Badge'} {node.id}",
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


def _emit_side_border_bar(
    ctx: DirectMapperContext,
    side_idx: int,
    cx: int,
    cy: int,
    cw: int,
    ch: int,
    thick: int,
    color: str,
    as_divider_line: bool = False,
) -> None:
    if side_idx == 0:  # Top
        ctx.sm.elements.append(
            lm.ShapeEl(
                name="Divider Line" if as_divider_line else "Card Top Accent",
                x=cx,
                y=cy,
                w=cw,
                h=int(1.2 * ctx.scale_y) if as_divider_line else thick,
                geom="line" if as_divider_line else "rect",
                fill=color,
            )
        )
    elif side_idx == 1:  # Right
        ctx.sm.elements.append(
            lm.ShapeEl(
                name="Card Tone Accent",
                x=cx + cw - thick,
                y=cy,
                w=thick,
                h=ch,
                geom="rect",
                fill=color,
            )
        )
    elif side_idx == 2:  # Bottom
        ctx.sm.elements.append(
            lm.ShapeEl(
                name="Divider Line" if as_divider_line else "Card Tone Accent",
                x=cx,
                y=cy + ch - (int(1.2 * ctx.scale_y) if as_divider_line else thick),
                w=cw,
                h=int(1.2 * ctx.scale_y) if as_divider_line else thick,
                geom="line" if as_divider_line else "rect",
                fill=color,
            )
        )
    elif side_idx == 3:  # Left
        ctx.sm.elements.append(
            lm.ShapeEl(
                name="Card Tone Accent",
                x=cx,
                y=cy,
                w=thick,
                h=ch,
                geom="rect",
                fill=color,
            )
        )


def walk_dom(
    ctx: DirectMapperContext,
    node: DomNode,
    effective_bg: str,
    parent_rect: tuple[int, int, int, int] | None = None,
    inside_card: bool = False,
    in_kpi: bool = False,
    next_sibling_y: int | None = None,
) -> None:
    tag = node.tag
    cls = node.cls_lower

    if tag == "footer" or "slide-footer" in cls or "footer" in cls.split():
        ctx.has_dom_footer = True

    # 1. Terminal semantic tags
    if tag == "table":
        emit_table_node(ctx, node)
        return
    if tag == "list":
        emit_list_node(ctx, node, effective_bg, parent_rect, inside_card)
        return
    if tag == "chart":
        emit_chart_node(ctx, node)
        return
    if tag in ("img", "canvas"):
        emit_media_node(ctx, node)
        return
    if tag == "svg":
        emit_svg_node(ctx, node)
        return
    if tag == "hr":
        hx, hy, hw, hh = ctx.to_emu_rect(node.box)
        ctx.sm.elements.append(
            lm.ShapeEl(
                name="Divider Line",
                x=hx,
                y=hy,
                w=hw,
                h=max(int(1.0 * EMU_PER_PT), min(hh, int(2.0 * EMU_PER_PT))),
                geom="line",
                fill=ctx.brand.roles.border,
            )
        )
        return

    # 2. Compact self-contained Badge / Pill / Button / Icon Box
    if is_compact_badge_or_button(node):
        emit_badge_node(ctx, node, effective_bg)
        return

    # 3. Container visual styling (backgrounds, borders, partial borders, topBar)
    borders_list = node.borders
    live_sides = [i for i, b in enumerate(borders_list or []) if b]
    has_bg = bool(node.bg or node.grad)
    has_full_border = bool(node.border) or len(live_sides) >= 2

    cur_bg = effective_bg
    cur_parent_rect = parent_rect
    cur_inside_card = inside_card
    cur_in_kpi = in_kpi or ("kpi" in cls)

    if has_bg or has_full_border:
        cx, cy, cw, ch = ctx.to_emu_rect(node.box)
        fill_col, grad_cols, fill_a = ctx.tones.map_container_fill(node, effective_bg, is_badge_or_btn=False)
        if fill_col:
            cur_bg = fill_col

        line_col: str | None = None
        line_w: float = 0.0
        dash: bool = False
        if node.border:
            b_info = node.border
            line_col = ctx.tones.map_border_color(b_info.get("c"), node.cls, is_accent=bool(b_info.get("dash")))
            line_w = min(1.5, max(0.75, float(b_info.get("w", 1.0)) * 0.75))
            dash = bool(b_info.get("dash"))
        elif borders_list and len(live_sides) >= 2:
            min_b = min((borders_list[i] for i in live_sides if borders_list[i]), key=lambda x: float(x.get("w", 1.0)))  # type: ignore[arg-type]
            line_col = ctx.tones.map_border_color(min_b.get("c"), None, is_accent=False)
            line_w = min(1.25, max(0.75, float(min_b.get("w", 1.0)) * 0.75))
            dash = bool(min_b.get("dash"))

        rad_px = node.radius_px
        geom_c: Literal["rect", "roundRect"] = "roundRect" if rad_px > 0 else "rect"
        adj_c = compute_roundrect_adj(rad_px, node.box["w"], node.box["h"], is_badge=False)

        if has_descendant_card(node):
            shape_name = f"Column Container {node.id}"
        elif cur_in_kpi:
            shape_name = f"KPI Card {node.id}"
        else:
            shape_name = f"Card {node.id}"

        ctx.sm.elements.append(
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
                gradient_angle=node.grad_angle,
                line=line_col,
                line_w=line_w,
                dash=dash,
            )
        )

        # Thick accent side border strips (Top, Right, Bottom, Left)
        if borders_list:
            for side_idx, b_side in enumerate(borders_list):
                if not b_side:
                    continue
                bw_px = float(b_side.get("w", 0.0))
                if bw_px >= 2.2:
                    acc_col = ctx.tones.map_border_color(b_side.get("c"), node.cls, is_accent=True)
                    bar_thick = max(int(2.5 * EMU_PER_PT), int(round(bw_px * ctx.scale_x)))
                    _emit_side_border_bar(ctx, side_idx, cx, cy, cw, ch, bar_thick, acc_col, as_divider_line=False)

        if node.top_bar:
            tb = node.top_bar
            bar_h = max(int(2.5 * EMU_PER_PT), int(round(float(tb.get("h", 3.0)) * ctx.scale_y)))
            ctx.sm.elements.append(
                lm.ShapeEl(
                    name="Card Top Accent",
                    x=cx,
                    y=cy,
                    w=cw,
                    h=bar_h,
                    geom="rect",
                    fill=ctx.brand.roles.primary,
                )
            )

        cur_parent_rect = (cx, cy, cw, ch)
        cur_inside_card = True

    elif borders_list and len(live_sides) == 1:
        # Single-edge border with no background fill (supports Top, Right, Bottom, and Left)
        cx, cy, cw, ch = ctx.to_emu_rect(node.box)
        side_idx = live_sides[0]
        b_side = borders_list[side_idx] or {}
        bw_px = float(b_side.get("w", 1.0))
        is_acc = bw_px >= 2.2 or color_tone(b_side.get("c")) != "none"
        line_col = ctx.tones.map_border_color(b_side.get("c"), node.cls if is_acc else None, is_accent=is_acc)
        thick = max(int(0.8 * EMU_PER_PT), int(round(bw_px * ctx.scale_x)))
        as_div = side_idx in (0, 2) and bw_px < 2.2
        _emit_side_border_bar(ctx, side_idx, cx, cy, cw, ch, thick, line_col, as_divider_line=as_div)

    # 4. Emit text runs or walk child nodes (passing next_sibling_y for vertically stacked siblings)
    if node.runs:
        emit_text_node(ctx, node, cur_bg, cur_parent_rect, cur_inside_card, cur_in_kpi, next_sibling_y=next_sibling_y)
    kids = node.kids
    for idx, k in enumerate(kids):
        kb = k.box
        kx1, kx2 = kb["x"], kb["x"] + kb["w"]
        ky2 = kb["y"] + kb["h"]
        sib_y: int | None = None
        for nxt in kids[idx + 1 :]:
            nb = nxt.box
            nx1, nx2 = nb["x"], nb["x"] + nb["w"]
            ny1 = nb["y"]
            if ny1 >= ky2 - 2.0 and min(kx2, nx2) - max(kx1, nx1) > 10.0:
                _, ny_emu, _, _ = ctx.to_emu_rect(nb)
                sib_y = ny_emu if sib_y is None else min(sib_y, ny_emu)
        walk_dom(ctx, k, cur_bg, cur_parent_rect, cur_inside_card, cur_in_kpi, next_sibling_y=sib_y)


def layout_slide_from_dom(slide: ir.Slide, brand: BrandProfile, slide_num: int = 1) -> lm.SlideModel:
    """Directly map a slide's Playwright DOM snapshot subtree into a positioned SlideModel."""
    sroot = DomNode(slide.dom_node or {})
    sbox = sroot.box
    sx = sbox["x"]
    sy = sbox["y"]
    sw = max(sbox["w"] if sbox["w"] > 0 else 1280.0, 320.0)
    sh = max(sbox["h"] if sbox["h"] > 0 else 720.0, 240.0)

    W = brand.slide_size.width
    H = brand.slide_size.height
    scale_x = W / sw
    scale_y = H / sh
    pt_scale = (H / EMU_PER_PT) / sh * 0.92

    archetype = slide.layout or (slide.kind if slide.kind in ("title", "section", "closing") else "content")
    show_footer = archetype not in ("title", "section", "closing")
    sm = lm.SlideModel(
        id=slide.id,
        archetype=archetype,
        template_layout="title" if slide.kind == "title" else "content",
        background=brand.roles.background,
        notes=slide.notes,
        show_footer=show_footer,
        number=slide_num,
    )

    ctx = DirectMapperContext(
        slide=slide,
        brand=brand,
        sm=sm,
        tones=ToneMapper(brand),
        sx=sx,
        sy=sy,
        sw=sw,
        sh=sh,
        W=W,
        H=H,
        scale_x=scale_x,
        scale_y=scale_y,
        pt_scale=pt_scale,
        charts_list=slide.charts or [],
    )

    for child in sroot.kids:
        walk_dom(ctx, child, brand.roles.background, None, False, False)

    # If the source HTML did not contain its own .slide-footer / <footer> element,
    # emit standard BrandProfile footer & slide number when show_footer is True.
    if sm.show_footer and not ctx.has_dom_footer:
        geom = SlideGeometry.from_brand(brand, has_title=bool(slide.title), slide=slide)
        _add_footer(brand, geom, sm, slide=slide)

    return sm
