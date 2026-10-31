"""Brand profile data model.

A BrandProfile captures everything needed to render a deck in an organisation's
corporate design: theme colors, fonts, slide size, logo, footer conventions,
an optional base template (whose masters/layouts are reused) and the inferred
"design language" (corner radius, density, margins, ...).
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from htmlppt import EMU_PER_INCH


def _id() -> str:
    return uuid.uuid4().hex[:12]


class ThemeColors(BaseModel):
    """The 12 OOXML theme color slots (hex, no '#')."""
    dk1: str = "000000"
    lt1: str = "FFFFFF"
    dk2: str = "1F2937"
    lt2: str = "F3F4F6"
    accent1: str = "2563EB"
    accent2: str = "0EA5E9"
    accent3: str = "10B981"
    accent4: str = "F59E0B"
    accent5: str = "EF4444"
    accent6: str = "8B5CF6"
    hlink: str = "2563EB"
    folHlink: str = "7C3AED"


class ColorRoles(BaseModel):
    """Semantic color roles used by the layout engine (hex, no '#')."""
    primary: str = "2563EB"
    secondary: str = "0EA5E9"
    background: str = "FFFFFF"
    surface: str = "F3F4F6"       # card / table banding background
    text: str = "111827"
    muted: str = "6B7280"
    on_primary: str = "FFFFFF"    # text drawn on primary fills
    positive: str = "10B981"
    negative: str = "EF4444"
    border: str = "E5E7EB"


class FontSpec(BaseModel):
    family: str = "Calibri"
    fallback: str = "Arial"
    file_id: str | None = None    # uploaded .ttf/.otf used for measurement/preview


class FontSizes(BaseModel):
    """Sizes in points."""
    title: float = 32
    subtitle: float = 18
    heading: float = 20
    body: float = 16
    caption: float = 11
    kpi: float = 40
    min: float = 10


class Fonts(BaseModel):
    heading: FontSpec = Field(default_factory=FontSpec)
    body: FontSpec = Field(default_factory=FontSpec)
    sizes: FontSizes = Field(default_factory=FontSizes)


class SlideSize(BaseModel):
    width: int = int(13.333 * EMU_PER_INCH)
    height: int = int(7.5 * EMU_PER_INCH)


class Logo(BaseModel):
    file_id: str
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-right"
    width: int = int(1.2 * EMU_PER_INCH)  # EMU; height derived from aspect ratio
    from_template: bool = False            # True -> logo already on the master, don't add


class Footer(BaseModel):
    text: str = ""
    slide_number: bool = True
    date: bool = False
    confidentiality: str = ""              # e.g. "Confidential — internal use only"


Archetype = Literal["title", "section", "content", "two_content", "title_only", "blank", "closing"]


class TemplateRef(BaseModel):
    file_id: str
    filename: str = ""
    # archetype -> layout name in the template
    layout_map: dict[str, str] = Field(default_factory=dict)
    layouts: list[dict] = Field(default_factory=list)  # summary of discovered layouts (for UI)


class DesignLanguage(BaseModel):
    corner_radius: float = 0.08            # 0 = sharp; fraction of the shorter side (OOXML adj)
    density: Literal["airy", "balanced", "dense"] = "balanced"
    margin_x: int = int(0.6 * EMU_PER_INCH)
    margin_top: int = int(0.45 * EMU_PER_INCH)
    margin_bottom: int = int(0.5 * EMU_PER_INCH)
    gutter: int = int(0.3 * EMU_PER_INCH)
    title_align: Literal["left", "center"] = "left"
    title_case: Literal["as-is", "upper", "title"] = "as-is"
    title_treatment: Literal["plain", "accent-bar", "underline", "band"] = "plain"
    card_style: Literal["filled", "outline", "none", "shadow"] = "filled"
    accent_usage: Literal["subtle", "moderate", "bold"] = "moderate"
    line_weight_pt: float = 1.0
    max_bullets: int = 6
    table_style: Literal["banded", "lines", "minimal"] = "banded"
    dark: bool = False
    layout_mode: Literal["dom", "archetype"] = "dom"


class SourceInfo(BaseModel):
    kind: Literal["template", "deck", "preset", "manual", "default"]
    filename: str = ""
    notes: str = ""
    confidence: dict[str, float] = Field(default_factory=dict)


class BrandProfile(BaseModel):
    id: str = Field(default_factory=_id)
    organization: str = "Default"
    name: str = "Neutral"
    colors: ThemeColors = Field(default_factory=ThemeColors)
    roles: ColorRoles = Field(default_factory=ColorRoles)
    chart_palette: list[str] = Field(
        default_factory=lambda: ["2563EB", "0EA5E9", "10B981", "F59E0B", "EF4444", "8B5CF6"]
    )
    fonts: Fonts = Field(default_factory=Fonts)
    slide_size: SlideSize = Field(default_factory=SlideSize)
    logo: Logo | None = None
    footer: Footer = Field(default_factory=Footer)
    template: TemplateRef | None = None
    design: DesignLanguage = Field(default_factory=DesignLanguage)
    default_preset: str = "corporate"
    sources: list[SourceInfo] = Field(default_factory=list)

    def derive_roles_from_theme(self) -> None:
        """Fill semantic roles from theme colors (used after template extraction)."""
        c = self.colors
        self.roles.primary = c.accent1
        self.roles.secondary = c.accent2
        self.roles.text = c.dk1 if c.dk1.upper() != "000000" else "1A1A1A"
        self.roles.background = c.lt1
        self.roles.surface = c.lt2
        self.roles.positive = c.accent3 if _is_greenish(c.accent3) else self.roles.positive
        self.chart_palette = [c.accent1, c.accent2, c.accent3, c.accent4, c.accent5, c.accent6]
        self.roles.on_primary = "FFFFFF" if luminance(c.accent1) < 0.55 else "111111"


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luminance(h: str) -> float:
    r, g, b = hex_to_rgb(h)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _is_greenish(h: str) -> bool:
    r, g, b = hex_to_rgb(h)
    return g > r and g > b
