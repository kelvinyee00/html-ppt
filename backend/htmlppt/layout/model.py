"""Positioned slide model — output of the layout engine.

All coordinates are EMU. Every text run carries its fully resolved font, size
and color, so the PPTX writer and the browser preview render exactly the same
thing without re-applying brand logic.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from htmlppt.analyze.ir import ChartSpec


class TRun(BaseModel):
    text: str
    font: str
    size: float                       # pt
    color: str                        # hex
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    href: str | None = None
    sup: bool = False
    sub: bool = False


class Para(BaseModel):
    runs: list[TRun]
    level: int = 0
    bullet: str | None = None         # "•", "–", "1." (auto-number) or None
    bullet_color: str | None = None
    align: Literal["left", "center", "right", "justify"] = "left"
    space_before: float = 0           # pt
    space_after: float = 0
    line_spacing: float = 1.0         # multiple


class _El(BaseModel):
    x: int
    y: int
    w: int
    h: int
    name: str = ""


class TextEl(_El):
    kind: Literal["text"] = "text"
    paras: list[Para]
    valign: Literal["top", "middle", "bottom"] = "top"
    inset: int = 0                    # EMU on all sides
    placeholder: Literal["title", "subtitle", "body"] | None = None
    fill: str | None = None
    wrap: bool = True


class ShapeEl(_El):
    kind: Literal["shape"] = "shape"
    geom: Literal["rect", "roundRect", "ellipse", "line", "triangle", "chevron", "homePlate"] = "rect"
    fill: str | None = None
    fill_alpha: float = 1.0
    gradient: list[str] | None = None
    gradient_angle: float = 90
    line: str | None = None
    line_w: float = 0                 # pt
    dash: bool = False
    radius: float = 0                 # roundRect adj (0..0.5)
    shadow: bool = False
    paras: list[Para] | None = None   # optional text inside the shape
    valign: Literal["top", "middle", "bottom"] = "middle"
    inset: int = 0
    flip_v: bool = False


class CellEl(BaseModel):
    paras: list[Para]
    fill: str | None = None
    colspan: int = 1
    rowspan: int = 1
    merged: bool = False              # covered by another cell's span
    border_bottom: str | None = None
    border_bottom_w: float = 0
    border_top: str | None = None
    border_top_w: float = 0
    valign: Literal["top", "middle", "bottom"] = "middle"
    is_header: bool = False


class TableEl(_El):
    kind: Literal["table"] = "table"
    col_widths: list[int]
    row_heights: list[int]
    rows: list[list[CellEl]]
    margin: int = 0


class ChartEl(_El):
    kind: Literal["chart"] = "chart"
    spec: ChartSpec
    palette: list[str]
    font: str
    font_size: float
    text_color: str
    grid_color: str
    legend: bool = True
    data_labels: bool = False
    title: bool = False


class ImageEl(_El):
    kind: Literal["image"] = "image"
    mime: str
    b64: str
    crop: tuple[float, float, float, float] = (0, 0, 0, 0)   # l, t, r, b fractions


class SvgEl(_El):
    kind: Literal["svg"] = "svg"
    svg: str
    recolor: str | None = None        # force single color (icons)


class GroupEl(_El):
    kind: Literal["group"] = "group"
    children: list["Element"]


Element = Annotated[
    Union[TextEl, ShapeEl, TableEl, ChartEl, ImageEl, SvgEl, GroupEl],
    Field(discriminator="kind"),
]
GroupEl.model_rebuild()


class SlideModel(BaseModel):
    id: str
    archetype: str
    template_layout: str = "content"  # brand template layout archetype to base the slide on
    background: str | None = None
    elements: list[Element] = Field(default_factory=list)
    notes: str = ""
    show_footer: bool = True
    number: int = 0


class DeckModel(BaseModel):
    width: int
    height: int
    slides: list[SlideModel]
    fonts: list[str] = Field(default_factory=list)   # font families used (for preview @font-face)
