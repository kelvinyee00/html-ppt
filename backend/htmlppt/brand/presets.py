"""Style presets and curated palettes.

A preset is a set of DesignLanguage overrides (plus small font-size tweaks)
applied on top of a brand profile at generation time. The brand's colors and
fonts are never replaced by a preset — only spacing, shape and emphasis rules.
"""
from __future__ import annotations

from htmlppt import EMU_PER_INCH
from htmlppt.brand.model import BrandProfile, ColorRoles, ThemeColors

IN = EMU_PER_INCH

PRESETS: dict[str, dict] = {
    "minimal": {
        "label": "Minimal",
        "description": "Generous whitespace, no card fills, thin lines, few accents.",
        "design": dict(corner_radius=0.0, density="airy", margin_x=int(0.8 * IN), margin_top=int(0.6 * IN),
                       gutter=int(0.4 * IN), title_treatment="plain", card_style="none",
                       accent_usage="subtle", line_weight_pt=0.75, max_bullets=5, table_style="minimal"),
        "sizes": dict(title=30, body=16),
    },
    "corporate": {
        "label": "Corporate Classic",
        "description": "Balanced layout, accent bar under titles, filled cards, banded tables.",
        "design": dict(corner_radius=0.04, density="balanced", title_treatment="accent-bar",
                       card_style="filled", accent_usage="moderate", line_weight_pt=1.0,
                       max_bullets=6, table_style="banded"),
        "sizes": dict(title=28, body=16),
    },
    "bold": {
        "label": "Bold / Modern",
        "description": "Large titles, strong color bands, rounded cards, high contrast.",
        "design": dict(corner_radius=0.14, density="balanced", title_treatment="band",
                       card_style="shadow", accent_usage="bold", line_weight_pt=1.5,
                       max_bullets=5, table_style="banded"),
        "sizes": dict(title=34, body=17, kpi=46),
    },
    "data-dense": {
        "label": "Data-Dense (consulting)",
        "description": "Action titles, tight margins, small type, outlines, many elements per slide.",
        "design": dict(corner_radius=0.0, density="dense", margin_x=int(0.45 * IN), margin_top=int(0.35 * IN),
                       gutter=int(0.2 * IN), title_treatment="underline", card_style="outline",
                       accent_usage="subtle", line_weight_pt=0.75, max_bullets=9, table_style="lines"),
        "sizes": dict(title=22, body=13, heading=15, caption=9, kpi=30, min=8),
    },
    "executive": {
        "label": "Elegant / Executive",
        "description": "Centered titles, refined spacing, subtle outlines, restrained accent color.",
        "design": dict(corner_radius=0.02, density="airy", margin_x=int(0.9 * IN), margin_top=int(0.6 * IN),
                       gutter=int(0.4 * IN), title_align="center", title_treatment="underline",
                       card_style="outline", accent_usage="subtle", line_weight_pt=0.5,
                       max_bullets=5, table_style="minimal"),
        "sizes": dict(title=30, body=16),
    },
}

# Curated palettes for when no brand profile is uploaded.
PALETTES: dict[str, dict] = {
    "ocean": {
        "label": "Ocean Blue",
        "accents": ["1D4ED8", "0891B2", "0D9488", "F59E0B", "DC2626", "7C3AED"],
        "dk2": "0F172A",
        "lt2": "F1F5F9",
        "dark_primary": "38BDF8",  # Sky 400
    },
    "slate": {
        "label": "Graphite",
        "accents": ["334155", "64748B", "0EA5E9", "F97316", "E11D48", "84CC16"],
        "dk2": "1E293B",
        "lt2": "F8FAFC",
        "dark_primary": "60A5FA",  # Blue 400
    },
    "forest": {
        "label": "Forest",
        "accents": ["166534", "15803D", "65A30D", "CA8A04", "B91C1C", "0F766E"],
        "dk2": "14261A",
        "lt2": "F0F7F1",
        "dark_primary": "4ADE80",  # Green 400
    },
    "burgundy": {
        "label": "Burgundy",
        "accents": ["9F1239", "BE123C", "D97706", "475569", "0369A1", "4D7C0F"],
        "dk2": "2A0A12",
        "lt2": "FAF5F6",
        "dark_primary": "FB7185",  # Rose 400
    },
    "sunset": {
        "label": "Sunset",
        "accents": ["EA580C", "DB2777", "7C3AED", "0284C7", "059669", "CA8A04"],
        "dk2": "1C1917",
        "lt2": "FFF7ED",
        "dark_primary": "FB923C",  # Orange 400
    },
    "mono": {
        "label": "Monochrome",
        "accents": ["111827", "4B5563", "9CA3AF", "2563EB", "DC2626", "059669"],
        "dk2": "111827",
        "lt2": "F3F4F6",
        "dark_primary": "E5E7EB",  # Gray 200
    },
}


def list_presets() -> list[dict]:
    return [{"id": k, "label": v["label"], "description": v["description"]} for k, v in PRESETS.items()]


def list_palettes() -> list[dict]:
    return [{"id": k, "label": v["label"], "colors": v["accents"]} for k, v in PALETTES.items()]


def palette_brand(palette_id: str = "ocean", font: str = "Calibri") -> BrandProfile:
    """Create a brand profile from a built-in palette (no uploaded brand)."""
    p = PALETTES.get(palette_id, PALETTES["ocean"])
    a = p["accents"]
    colors = ThemeColors(dk1="000000", lt1="FFFFFF", dk2=p["dk2"], lt2=p["lt2"],
                         accent1=a[0], accent2=a[1], accent3=a[2], accent4=a[3], accent5=a[4], accent6=a[5],
                         hlink=a[0])
    b = BrandProfile(id=f"palette-{palette_id}", organization="Built-in", name=p["label"], colors=colors)
    b.fonts.heading.family = font
    b.fonts.body.family = font
    b.derive_roles_from_theme()
    b.roles.text = "1A1A1A"
    b.roles.surface = p["lt2"]
    return b


def apply_preset(brand: BrandProfile, preset_id: str | None, dark: bool = False) -> BrandProfile:
    """Return a copy of `brand` with the preset's design overrides applied."""
    b = brand.model_copy(deep=True)
    preset = PRESETS.get(preset_id or b.default_preset) or PRESETS["corporate"]
    for k, v in preset["design"].items():
        setattr(b.design, k, v)
    for k, v in preset.get("sizes", {}).items():
        setattr(b.fonts.sizes, k, v)
    if dark:
        _make_dark(b)
    return b


def relative_luminance(hex_code: str) -> float:
    """Calculate WCAG 2.1 relative luminance for a hex color code."""
    hex_clean = hex_code.lstrip("#")
    if len(hex_clean) != 6:
        return 0.5
    r, g, b = (int(hex_clean[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _channel_lum(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _channel_lum(r) + 0.7152 * _channel_lum(g) + 0.0722 * _channel_lum(b)


def contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculate WCAG 2.1 contrast ratio between two hex color codes."""
    l1 = relative_luminance(hex1)
    l2 = relative_luminance(hex2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def ensure_contrast(fg_hex: str, bg_hex: str, min_contrast: float = 4.5) -> str:
    """Lighten fg_hex towards white until it meets min_contrast against bg_hex."""
    fg_clean = fg_hex.lstrip("#").upper()
    bg_clean = bg_hex.lstrip("#").upper()
    if contrast_ratio(fg_clean, bg_clean) >= min_contrast:
        return fg_clean

    for step in range(1, 21):
        t = step / 20.0
        candidate = _mix(fg_clean, "FFFFFF", t)
        if contrast_ratio(candidate, bg_clean) >= min_contrast:
            return candidate
    return "FFFFFF"


def _make_dark(b: BrandProfile) -> None:
    r: ColorRoles = b.roles
    b.design.dark = True
    r.background = b.colors.dk2 if b.colors.dk2 else "0F172A"
    r.surface = _mix(r.background, "FFFFFF", 0.08)
    r.text = "F9FAFB"
    r.muted = "9CA3AF"
    r.border = _mix(r.background, "FFFFFF", 0.18)

    # Resolve luminous primary accent in dark mode
    palette_key = b.id.replace("palette-", "") if b.id and b.id.startswith("palette-") else None
    if palette_key and palette_key in PALETTES and "dark_primary" in PALETTES[palette_key]:
        primary_candidate = PALETTES[palette_key]["dark_primary"]
    else:
        primary_candidate = r.primary or b.colors.accent1 or "38BDF8"

    # Guarantee >= 4.5:1 contrast against both dark background and elevated surface
    primary_candidate = ensure_contrast(primary_candidate, r.background, min_contrast=4.5)
    primary_candidate = ensure_contrast(primary_candidate, r.surface, min_contrast=4.5)
    r.primary = primary_candidate


def _mix(a: str, b: str, t: float) -> str:
    ar, ag, ab = (int(a[i:i + 2], 16) for i in (0, 2, 4))
    br, bg, bb = (int(b[i:i + 2], 16) for i in (0, 2, 4))
    return "%02X%02X%02X" % (round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t))

