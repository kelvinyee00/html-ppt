"""Semantic intermediate representation (IR).

The IR describes *what* is on each slide (title, bullets, a table, a chart,
KPI tiles, ...) independent of *where* it goes. The layout engine turns it into
positioned shapes using a brand profile and style preset; the UI's slide
operations (reorder / merge / split / change layout) act on the IR.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


def _id() -> str:
    return uuid.uuid4().hex[:10]


SemanticTone = Literal["none", "info", "positive", "warning", "negative", "primary", "muted"]


class Run(BaseModel):
    t: str
    b: bool = False
    i: bool = False
    u: bool = False
    strike: bool = False
    color: str | None = None        # source color (hex). Usually remapped to brand, kept for emphasis detection
    href: str | None = None
    mono: bool = False
    sup: bool = False
    sub: bool = False
    emphasis: bool = False          # run was visually emphasised (color/highlight) in source
    tone: SemanticTone = "none"     # semantic tone preserved across themes
    badge: bool = False             # inline pill/badge styling


class ListItem(BaseModel):
    runs: list[Run]
    level: int = 0


class _Block(BaseModel):
    id: str = Field(default_factory=_id)
    src: dict | None = None         # source box in px relative to slide: {x,y,w,h, sw, sh}
    column: int | None = None       # column index when the source arranged content side-by-side
    row_group: int | None = None    # contiguous horizontal row band id for scoped multi-column layout
    total_columns: int | None = None  # number of columns in this horizontal row band (2 or 3)


class Heading(_Block):
    type: Literal["heading"] = "heading"
    level: int = 3
    runs: list[Run]


class Paragraph(_Block):
    type: Literal["paragraph"] = "paragraph"
    runs: list[Run]
    role: Literal["body", "lead", "caption", "footnote"] = "body"
    align: Literal["left", "center", "right"] = "left"


class BulletList(_Block):
    type: Literal["bullets"] = "bullets"
    items: list[ListItem]
    ordered: bool = False


class Quote(_Block):
    type: Literal["quote"] = "quote"
    runs: list[Run]
    attribution: str = ""


class Callout(_Block):
    type: Literal["callout"] = "callout"
    runs: list[Run]
    title: str = ""
    badge: str = ""                 # step badge or status pill (e.g. "DOORS / Polarion", "✓ CERTIFIED")
    tone: SemanticTone = "info"


class Cell(BaseModel):
    runs: list[Run] = Field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    header: bool = False
    align: Literal["left", "center", "right"] = "left"
    bold: bool = False
    tone: SemanticTone = "none"


class Table(_Block):
    type: Literal["table"] = "table"
    rows: list[list[Cell]]
    header_rows: int = 1
    caption: str = ""
    col_weights: list[float] = Field(default_factory=list)
    total_row: bool = False


class Series(BaseModel):
    name: str = ""
    values: list[float | None] = Field(default_factory=list)
    xy: list[tuple[float | None, float | None]] | None = None
    type: str | None = None


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "area", "pie", "doughnut", "scatter", "radar"] = "bar"
    horizontal: bool = False
    stacked: bool = False
    title: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[Series] = Field(default_factory=list)
    x_title: str = ""
    y_title: str = ""
    show_legend: bool = True
    number_format: str = "General"


class Chart(_Block):
    type: Literal["chart"] = "chart"
    spec: ChartSpec


class KPI(BaseModel):
    value: str
    label: str = ""
    delta: str = ""
    trend: Literal["up", "down", "flat", "none"] = "none"
    sentiment: Literal["positive", "negative", "neutral"] = "neutral"


class KPIGroup(_Block):
    type: Literal["kpis"] = "kpis"
    items: list[KPI]


class Card(BaseModel):
    title: str = ""
    kicker: str = ""                 # small label above title (e.g. "Q4 2026", "Step 1")
    badge: str = ""                  # optional badge/pill or icon label (e.g. "🔒 Pillar 1")
    body: list[Run] = Field(default_factory=list)
    bullets: list[ListItem] = Field(default_factory=list)
    footer: str = ""                 # optional bottom deliverable/highlight line (e.g. "▸ 60% Faster HIL Validation")
    tone: SemanticTone = "none"      # semantic accent tone (e.g. "positive", "negative", "primary")
    icon_svg: str | None = None


class CardGrid(_Block):
    type: Literal["cards"] = "cards"
    cards: list[Card]
    variant: Literal["cards", "process", "timeline"] = "cards"


class Image(_Block):
    type: Literal["image"] = "image"
    mime: str = "image/png"
    b64: str
    w: float = 0                     # natural/source size in px (aspect ratio)
    h: float = 0
    alt: str = ""
    is_logo: bool = False


class VectorGraphic(_Block):
    type: Literal["vector"] = "vector"
    svg: str
    w: float = 0
    h: float = 0
    icon: bool = False


class FreeElement(BaseModel):
    """An element kept at its original relative position (0..1 of the source slide)."""
    kind: Literal["text", "box", "image", "vector", "line"]
    x: float
    y: float
    w: float
    h: float
    runs: list[Run] = Field(default_factory=list)
    font_px: float = 16
    align: str = "left"
    bold: bool = False
    fill: str | None = None
    fill_alpha: float = 1.0
    border: str | None = None
    border_w: float = 0
    radius_px: float = 0
    color: str | None = None
    role: Literal["primary", "secondary", "accent", "surface", "text", "muted", "none"] = "none"
    b64: str | None = None
    mime: str | None = None
    svg: str | None = None


class Freeform(_Block):
    type: Literal["freeform"] = "freeform"
    elements: list[FreeElement]
    aspect: float = 16 / 9


Block = Annotated[
    Union[Heading, Paragraph, BulletList, Quote, Callout, Table, Chart, KPIGroup, CardGrid,
          Image, VectorGraphic, Freeform],
    Field(discriminator="type"),
]

SlideKind = Literal["title", "agenda", "section", "content", "closing"]


class Slide(BaseModel):
    id: str = Field(default_factory=_id)
    kind: SlideKind = "content"
    title: str = ""
    subtitle: str = ""
    kicker: str = ""
    footer_left: str = ""
    footer_right: str = ""
    blocks: list[Block] = Field(default_factory=list)
    notes: str = ""
    layout: str | None = None         # user/LLM override of the layout archetype
    continued: bool = False           # created by overflow split
    source_index: int | None = None   # index of the source slide/segment
    auto: bool = False                # generated (title/agenda/closing) rather than from HTML
    dom_node: dict | None = Field(default=None, exclude=True)
    charts: list[dict] = Field(default_factory=list, exclude=True)


class DeckMeta(BaseModel):
    title: str = ""
    subtitle: str = ""
    author: str = ""
    date: str = ""


class Deck(BaseModel):
    meta: DeckMeta = Field(default_factory=DeckMeta)
    slides: list[Slide] = Field(default_factory=list)
    source_kind: Literal["deck", "page"] = "deck"
    warnings: list[str] = Field(default_factory=list)


def runs_text(runs: list[Run]) -> str:
    return "".join(r.t for r in runs)
