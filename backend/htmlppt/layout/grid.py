"""Layout grid calculation and bounding boxes."""
from __future__ import annotations

from dataclasses import dataclass
from htmlppt import EMU_PER_INCH, EMU_PER_PT
from htmlppt.analyze import ir
from htmlppt.brand.model import BrandProfile
from htmlppt.layout.textfit import estimate_text_height_emu


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass
class SlideGeometry:
    width: int
    height: int
    margin_x: int
    margin_top: int
    margin_bottom: int
    gutter: int

    # Computed zones
    title_rect: Rect
    content_rect: Rect

    @classmethod
    def from_brand(
        cls,
        brand: BrandProfile,
        has_title: bool = True,
        slide: ir.Slide | None = None,
    ) -> SlideGeometry:
        sw = brand.slide_size.width
        sh = brand.slide_size.height
        mx = brand.design.margin_x
        mt = brand.design.margin_top
        mb = brand.design.margin_bottom
        gutter = brand.design.gutter
        avail_w = sw - 2 * mx

        if has_title:
            if slide is not None:
                kicker_val = (getattr(slide, "kicker", "") or "").strip()
                kicker_h = int(0.24 * EMU_PER_INCH) if kicker_val else 0

                title_text = (slide.title or "").strip()
                if brand.design.title_case == "upper":
                    title_text = title_text.upper()
                title_size = min(brand.fonts.sizes.title, 26.0)
                title_h_est = estimate_text_height_emu(
                    title_text or "Title",
                    avail_w,
                    brand.fonts.heading.family,
                    title_size,
                    line_spacing=1.15,
                    bold=True,
                )

                sub_text = (slide.subtitle or "").strip()
                if sub_text:
                    sub_size = min(brand.fonts.sizes.subtitle, 14.0)
                    sub_h_est = (
                        estimate_text_height_emu(
                            sub_text,
                            avail_w,
                            brand.fonts.body.family,
                            sub_size,
                            line_spacing=1.2,
                            bold=False,
                        )
                        + int(6 * EMU_PER_PT)
                    )
                else:
                    sub_h_est = 0

                raw_title_h = kicker_h + title_h_est + sub_h_est + int(0.16 * EMU_PER_INCH)
                title_h = max(int(sh * 0.11), min(int(sh * 0.24), raw_title_h))
                title_rect = Rect(x=mx, y=mt, w=avail_w, h=title_h)
                content_y = title_rect.bottom + int(gutter * 0.45)
                content_h = sh - content_y - mb
                content_rect = Rect(x=mx, y=content_y, w=avail_w, h=content_h)
            else:
                # Default static header zone (~16% of slide height)
                title_h = int(sh * 0.16)
                title_rect = Rect(x=mx, y=mt, w=avail_w, h=title_h)
                content_y = title_rect.bottom + int(gutter * 0.5)
                content_h = sh - content_y - mb
                content_rect = Rect(x=mx, y=content_y, w=avail_w, h=content_h)
        else:
            title_rect = Rect(x=mx, y=mt, w=0, h=0)
            content_rect = Rect(x=mx, y=mt, w=avail_w, h=sh - mt - mb)

        return cls(
            width=sw,
            height=sh,
            margin_x=mx,
            margin_top=mt,
            margin_bottom=mb,
            gutter=gutter,
            title_rect=title_rect,
            content_rect=content_rect,
        )

    def split_columns(self, n_cols: int) -> list[Rect]:
        """Split content area horizontally into n columns with gutter."""
        if n_cols <= 1:
            return [self.content_rect]
        total_gutter = self.gutter * (n_cols - 1)
        avail_w = self.content_rect.w - total_gutter
        col_w = avail_w // n_cols
        
        cols: list[Rect] = []
        for i in range(n_cols):
            cx = self.content_rect.x + i * (col_w + self.gutter)
            cols.append(Rect(x=cx, y=self.content_rect.y, w=col_w, h=self.content_rect.h))
        return cols

    def split_rows(self, n_rows: int) -> list[Rect]:
        """Split content area vertically into n rows with gutter."""
        if n_rows <= 1:
            return [self.content_rect]
        total_gutter = self.gutter * (n_rows - 1)
        avail_h = self.content_rect.h - total_gutter
        row_h = avail_h // n_rows
        
        rows: list[Rect] = []
        for i in range(n_rows):
            cy = self.content_rect.y + i * (row_h + self.gutter)
            rows.append(Rect(x=self.content_rect.x, y=cy, w=self.content_rect.w, h=row_h))
        return rows
