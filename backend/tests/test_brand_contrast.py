"""Tests for dark mode color roles, contrast ratios, and luminous accents."""
import pytest
from htmlppt.brand.presets import (
    PALETTES,
    apply_preset,
    palette_brand,
    contrast_ratio,
    relative_luminance,
    ensure_contrast,
)


def test_luminance_and_contrast_utility():
    """Verify WCAG relative luminance and contrast calculations."""
    # Pure black and pure white
    assert relative_luminance("000000") == pytest.approx(0.0, abs=1e-4)
    assert relative_luminance("FFFFFF") == pytest.approx(1.0, abs=1e-4)
    assert contrast_ratio("FFFFFF", "000000") == pytest.approx(21.0, abs=0.1)
    assert contrast_ratio("000000", "000000") == pytest.approx(1.0, abs=0.1)

    # ensure_contrast should lighten dark colors on dark background
    bg = "0F172A"
    deep_blue = "1D4ED8"
    assert contrast_ratio(deep_blue, bg) < 4.5
    lightened = ensure_contrast(deep_blue, bg, min_contrast=4.5)
    assert contrast_ratio(lightened, bg) >= 4.5


@pytest.mark.parametrize("palette_id", ["ocean", "slate", "forest", "burgundy", "sunset", "mono"])
def test_all_curated_palettes_dark_mode_contrast(palette_id):
    """Verify that in dark mode, roles.primary has >= 4.5:1 contrast against both background and surface."""
    brand = palette_brand(palette_id)
    dark_brand = apply_preset(brand, "corporate", dark=True)

    assert dark_brand.design.dark is True
    bg = dark_brand.roles.background
    surface = dark_brand.roles.surface
    primary = dark_brand.roles.primary

    bg_contrast = contrast_ratio(primary, bg)
    surface_contrast = contrast_ratio(primary, surface)

    assert bg_contrast >= 4.5, f"Palette {palette_id}: primary #{primary} vs bg #{bg} contrast is {bg_contrast:.2f} (< 4.5)"
    assert surface_contrast >= 4.5, f"Palette {palette_id}: primary #{primary} vs surface #{surface} contrast is {surface_contrast:.2f} (< 4.5)"


def test_custom_dark_brand_contrast():
    """Even with a custom dark brand with low contrast accent, _make_dark guarantees >= 4.5 contrast."""
    brand = palette_brand("ocean")
    brand.id = "custom-dark-test"
    brand.colors.accent1 = "102040"  # Very dark blue
    brand.derive_roles_from_theme()

    dark_brand = apply_preset(brand, "minimal", dark=True)
    assert contrast_ratio(dark_brand.roles.primary, dark_brand.roles.background) >= 4.5
    assert contrast_ratio(dark_brand.roles.primary, dark_brand.roles.surface) >= 4.5
