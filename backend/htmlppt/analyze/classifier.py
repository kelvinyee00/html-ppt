"""Rule-based classification of DOM snapshot nodes into semantic IR blocks.

Recognises headings, paragraphs, bullet lists, tables, charts, KPI tiles, card
grids / process steps / timelines, callouts, quotes, images and SVG graphics.
Positioned/overlapping compositions that can't be understood semantically are
kept as a `Freeform` block (coordinate-faithful fallback).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from htmlppt.analyze import ir

NUM_RE = re.compile(
    r"^[\s€$£¥+\-−~≈<>]*\d[\d.,\s:/\-]*\s*"
    r"(%|[kKmMbBtT]|bn|mn|Mio\.?|Mrd\.?|pp|x|×|€|\$|£|"
    r"wks?|weeks?|mos?|months?|hrs?|hours?|days?|yrs?|years?|min|mins|sec|secs|ms|us|µs|ns|s|"
    r"fps|Hz|kHz|MHz|GHz|W|kW|MW|GW|V|kV|mV|A|mA|B|KB|MB|GB|TB|bps|Kbps|Mbps|Gbps|"
    r"/\w+)?"
    r"\s*[+x×%]?$",
    re.I,
)
DELTA_RE = re.compile(r"([▲▼↑↓△▽⬆⬇]|^[+\-−]\s?\d|\bvs\.?\b|\bYoY\b|\bQoQ\b|pp\b)", re.I)
DATE_RE = re.compile(r"\b(Q[1-4]|H[12]|FY|20\d\d|19\d\d|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Phase|Week|Month)\b", re.I)
STEP_RE = re.compile(r"^(step|phase|stage|service|pillar|stufe|schritt)?\s*0?\d{1,2}[.:)]?$|^(step|phase|stage|service)\b", re.I)
CLOSING_RE = re.compile(r"\b(thank you|thanks|questions\??|q\s?&\s?a|contact us|danke|merci)\b", re.I)


@dataclass
class Ctx:
    body_fs: float = 16.0
    charts: list[dict] = field(default_factory=list)
    slide_box: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    next_row_group: int = 1
    footer_parts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers

def text_of(n: dict) -> str:
    if n.get("runs"):
        return "".join(r.get("t", "") for r in n["runs"])
    if n["tag"] == "list":
        return " ".join("".join(r.get("t", "") for r in it["runs"]) for it in n["items"])
    return " ".join(text_of(k) for k in n.get("kids", []) or [])


def leaves(n: dict) -> list[dict]:
    """All descendant nodes that are terminal (text leaves, lists, tables, media)."""
    if n.get("runs") is not None or n["tag"] in ("list", "table", "chart", "img", "canvas", "svg", "hr"):
        return [n]
    out = []
    for k in n.get("kids", []) or []:
        out.extend(leaves(k))
    return out


def text_leaves(n: dict) -> list[dict]:
    return [l for l in leaves(n) if l.get("runs")]


def has_visual(n: dict) -> bool:
    v = n.get("v") or {}
    return bool(v.get("bg") or v.get("grad") or v.get("border") or v.get("borders") or v.get("shadow"))


def compute_body_fs(root: dict) -> float:
    weights: dict[float, int] = {}
    for l in text_leaves(root):
        t = text_of(l)
        if len(t) > 30:
            weights[l.get("fs", 16)] = weights.get(l.get("fs", 16), 0) + len(t)
    for n in _all(root):
        if n["tag"] == "list":
            weights[n.get("fs", 16)] = weights.get(n.get("fs", 16), 0) + len(text_of(n))
    if not weights:
        return 16.0
    return max(weights.items(), key=lambda kv: kv[1])[0]


def _all(n: dict):
    yield n
    for k in n.get("kids", []) or []:
        yield from _all(k)


def _color_tone(color: str | None) -> str:
    if not color or len(color) < 6:
        return "none"
    try:
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    except ValueError:
        return "none"
    if g > r + 30 and g >= b:
        return "positive"
    if r > g + 40 and r > b + 20:
        return "negative"
    if r > 180 and g > 120 and b < 80 and r >= g:
        return "warning"
    if b >= 180 and b > r + 50 and b >= g:
        return "primary"
    return "none"


def _cls_tone(cls: str | None) -> str:
    if not cls:
        return "none"
    low = cls.lower()
    tokens = set(re.split(r"[\s_\-]+", low))
    if tokens & {"good", "positive", "success", "deliverable"} or any(
        k in low for k in ("right-branch", "security-box", "security-badge", "security-banner", "airgap-banner")
    ):
        return "positive"
    if tokens & {"bad", "negative", "danger", "error", "down"}:
        return "negative"
    if tokens & {"warning", "confidential", "caution"}:
        return "warning"
    if tokens & {"primary", "kicker", "num"} or "v-step-badge" in low or "cta-banner" in low:
        return "primary"
    return "none"


def _node_tone(n: dict, default: str = "none") -> str:
    ct = _cls_tone(n.get("cls"))
    if ct != "none":
        return ct
    v = n.get("v") or {}
    border = v.get("border") or {}
    if border.get("c"):
        bt = _color_tone(border["c"])
        if bt != "none":
            return bt
    for b in (v.get("borders") or []):
        if b and b.get("c"):
            bt = _color_tone(b["c"])
            if bt != "none":
                return bt
    if v.get("bg"):
        bgt = _color_tone(v["bg"])
        if bgt != "none":
            return bgt
    for gcol in (v.get("grad") or []):
        gt = _color_tone(gcol)
        if gt != "none":
            return gt
    return default


def to_runs(raw: list[dict], base_color: str | None = None) -> list[ir.Run]:
    out: list[ir.Run] = []
    color_weights: dict[str, int] = {}
    for r in raw:
        txt_s = r.get("t", "").strip()
        col_s = r.get("c")
        if txt_s and col_s and not r.get("hl"):
            color_weights[col_s] = color_weights.get(col_s, 0) + len(txt_s)
    if not color_weights:
        for r in raw:
            txt_s = r.get("t", "").strip()
            col_s = r.get("c")
            if txt_s and col_s:
                color_weights[col_s] = color_weights.get(col_s, 0) + len(txt_s)
    dominant = base_color or (max(color_weights.items(), key=lambda kv: kv[1])[0] if color_weights else None)
    for r in raw:
        t = r.get("t", "")
        if r.get("up"):
            t = t.upper()
        hl = r.get("hl")
        c = r.get("c")
        is_emph = bool(hl) or (c is not None and dominant is not None and c != dominant)
        rt = _color_tone(c)
        if rt == "none" and hl:
            rt = _color_tone(hl)
        out.append(ir.Run(
            t=t, b=bool(r.get("b")), i=bool(r.get("i")), u=bool(r.get("u")) and not r.get("href"),
            strike=bool(r.get("st")), color=c, href=r.get("href"), mono=bool(r.get("mono")),
            sup=bool(r.get("sup")), sub=bool(r.get("sub")),
            emphasis=is_emph,
            tone=rt if (hl or is_emph or len(raw) == 1) else "none",
            badge=bool(hl),
        ))
    return out


def rel_box(n: dict, ctx: Ctx) -> dict:
    sb = ctx.slide_box or {"x": 0, "y": 0, "w": 1920, "h": 1080}
    b = n["box"]
    return {"x": b["x"] - sb["x"], "y": b["y"] - sb["y"], "w": b["w"], "h": b["h"], "sw": sb["w"], "sh": sb["h"]}


def _align(ta: str | None) -> str:
    return ta if ta in ("center", "right") else "left"


def _sentiment(text: str, color: str | None) -> str:
    if color:
        ct = _color_tone(color)
        if ct in ("positive", "negative"):
            return ct
    if re.search(r"[▲↑⬆]|^\+", text):
        return "positive"
    if re.search(r"[▼↓⬇]|^[\-−]", text):
        return "negative"
    return "neutral"


# ---------------------------------------------------------------- leaf classification

def heading_level(n: dict, ctx: Ctx) -> int | None:
    tag = n["tag"]
    if re.fullmatch(r"h[1-6]", tag):
        return int(tag[1])
    cls = (n.get("cls") or "").lower()
    if any(k in cls for k in ("kicker", "eyebrow", "subtitle", "sub-title", "slide-footer", "footer", "badge", "deliverable")):
        return None
    t = text_of(n).strip()
    if not t or len(t) > 120 or "\n" in t:
        return None
    fs = n.get("fs", ctx.body_fs)
    bold = (n.get("fw") or 400) >= 600 or all(r.get("b") for r in n.get("runs", []) if r.get("t", "").strip())
    all_up = bool(n.get("runs")) and all(r.get("up") for r in n.get("runs", []) if r.get("t", "").strip())
    ratio = fs / max(ctx.body_fs, 1)
    if all_up and ratio < 1.35:
        return None
    if NUM_RE.match(t):
        return None
    if ratio >= 2.2:
        return 1
    if ratio >= 1.5:
        return 2
    if ratio >= 1.15 and bold:
        return 3
    if bold and ratio >= 1.0 and len(t) < 60 and not t.endswith((".", ":")) and len(n.get("runs", [])) <= 2:
        return 4
    return None


def classify_leaf(n: dict, ctx: Ctx) -> list[ir.Block]:
    tag = n["tag"]
    src = rel_box(n, ctx)
    if tag == "list":
        items = [ir.ListItem(runs=to_runs(it["runs"]), level=min(it["level"], 4)) for it in n["items"] if it["runs"]]
        return [ir.BulletList(items=items, ordered=n.get("ordered", False), src=src)] if items else []
    if tag == "table":
        return [build_table(n, src)]
    if tag == "chart":
        c = next((c for c in ctx.charts if c["index"] == n.get("chart")), None)
        if c:
            try:
                return [ir.Chart(spec=normalize_chart(c["spec"]), src=src)]
            except Exception as e:  # pragma: no cover - defensive
                ctx.warnings.append(f"chart parse failed: {e}")
        return []
    if tag in ("img", "canvas"):
        img = n.get("img")
        if not img:
            return []
        b = n["box"]
        return [ir.Image(mime=img["mime"], b64=img["b64"], w=n.get("nw") or b["w"], h=n.get("nh") or b["h"],
                         alt=n.get("alt", ""), src=src,
                         is_logo="logo" in (n.get("alt", "") + n.get("cls", "")).lower())]
    if tag == "svg":
        b = n["box"]
        return [ir.VectorGraphic(svg=n["svg"], w=b["w"], h=b["h"], icon=max(b["w"], b["h"]) <= 72, src=src)]
    if tag == "hr":
        return []
    if not n.get("runs"):
        return []
    runs = to_runs(n["runs"])
    text = ir.runs_text(runs).strip()
    if not text:
        return []
    if tag in ("blockquote", "q"):
        return [ir.Quote(runs=runs, src=src)]
    lvl = heading_level(n, ctx)
    if lvl is not None:
        return [ir.Heading(level=lvl, runs=_strip_styles(runs), src=src)]
    fs = n.get("fs", ctx.body_fs)
    cls = (n.get("cls") or "").lower()
    all_up = bool(n.get("runs")) and all(r.get("up") for r in n.get("runs", []) if r.get("t", "").strip())
    role = "body"
    if any(k in cls for k in ("kicker", "eyebrow")) or (all_up and fs <= ctx.body_fs * 1.2 and len(text) <= 90):
        role = "caption"
    elif any(k in cls for k in ("subtitle", "sub-title", "lead")) or (fs >= ctx.body_fs * 1.15 and len(text) < 350):
        role = "lead"
    elif fs <= ctx.body_fs * 0.8:
        role = "caption" if len(text) < 160 else "footnote"
    if has_visual(n) and len(text) < 300 and (n.get("v") or {}).get("bg"):
        return [ir.Callout(runs=runs, tone=_tone(n), src=src)]
    return [ir.Paragraph(runs=runs, role=role, align=_align(n.get("ta")), src=src)]


def _strip_styles(runs: list[ir.Run]) -> list[ir.Run]:
    return [r.model_copy(update={"b": False, "color": None, "emphasis": r.emphasis}) for r in runs]


def _tone(n: dict) -> str:
    nt = _node_tone(n, default="info")
    return nt if nt in ("info", "positive", "warning", "negative", "primary") else "info"


def build_table(n: dict, src: dict) -> ir.Table:
    rows: list[list[ir.Cell]] = []
    header_rows = 0
    for ri, row in enumerate(n["rows"]):
        cells = []
        for c in row["cells"]:
            runs = to_runs(c["runs"])
            txt = ir.runs_text(runs).strip()
            numeric = len(txt) <= 24 and (bool(NUM_RE.match(txt)) or bool(re.match(r"^[+\-−]?\d", txt)))
            align = c.get("ta") if c.get("ta") in ("center", "right") else ("right" if numeric and ri > 0 else "left")
            tone = "none"
            if numeric and txt[:1] in "+-−":
                s = _sentiment(txt, c.get("c"))
                tone = s if s in ("positive", "negative") else "none"
            elif runs and runs[0].tone in ("positive", "negative", "warning", "primary") and (runs[0].badge or len(runs) == 1):
                tone = runs[0].tone
            cells.append(ir.Cell(runs=runs, colspan=c.get("cs", 1), rowspan=c.get("rs", 1), header=bool(c.get("th")) and ri == header_rows,
                                 align=align, bold=bool(c.get("b")), tone=tone))
        if (row.get("head") or (cells and all(x.header for x in cells))) and ri == header_rows:
            header_rows += 1
        rows.append(cells)
    widths = [c.get("w", 1) for c in n["rows"][0]["cells"]] if n["rows"] else []
    last = rows[-1] if rows else []
    total = bool(last) and (ir.runs_text(last[0].runs).strip().lower().startswith(("total", "sum", "gesamt"))
                            or all(c.bold or all(r.b for r in c.runs) for c in last))
    return ir.Table(rows=rows, header_rows=header_rows, caption=n.get("caption", ""), col_weights=widths,
                    total_row=total and len(rows) > 2, src=src)


def normalize_chart(spec: dict) -> ir.ChartSpec:
    typ = spec.get("type") or "bar"
    if typ not in ("bar", "line", "area", "pie", "doughnut", "scatter", "radar"):
        typ = "bar"
    series = []
    for s in spec.get("series", []):
        vals = [None if v is None else float(v) for v in (s.get("values") or [])]
        xy = s.get("xy")
        series.append(ir.Series(name=s.get("name") or "", values=vals, xy=xy, type=s.get("type")))
    cats = [str(c) for c in spec.get("categories", [])]
    if typ not in ("scatter",) and series and not cats:
        cats = [str(i + 1) for i in range(max(len(s.values) for s in series))]
    return ir.ChartSpec(type=typ, horizontal=bool(spec.get("horizontal")), stacked=bool(spec.get("stacked")),
                        title=spec.get("title") or "", categories=cats, series=series,
                        x_title=spec.get("x_title") or "", y_title=spec.get("y_title") or "",
                        show_legend=bool(spec.get("show_legend", True)) and (len(series) > 1 or typ in ("pie", "doughnut")))


# ---------------------------------------------------------------- composite detection

def _kpi_from(n: dict, ctx: Ctx) -> ir.KPI | None:
    tl = text_leaves(n)
    if not 1 <= len(tl) <= 6:
        return None
    if any(l["tag"] in ("list", "table", "chart") for l in leaves(n)):
        return None
    cands = [l for l in tl if NUM_RE.match(text_of(l).strip()) and len(text_of(l).strip()) <= 20]
    if not cands:
        return None
    val = max(cands, key=lambda l: l.get("fs", 0))
    others = [l for l in tl if l is not val]
    if others and val.get("fs", 0) < 1.35 * max(o.get("fs", 0) for o in others):
        return None
    if not others and val.get("fs", 0) < ctx.body_fs * 1.6:
        return None

    label_leaves: list[dict] = []
    delta_leaves: list[dict] = []
    if len(others) == 1:
        o = others[0]
        if DELTA_RE.search(text_of(o)) or "delta" in (o.get("cls") or "").lower():
            delta_leaves = [o]
        else:
            label_leaves = [o]
    elif len(others) >= 2:
        explicit_labels = [o for o in others if "label" in (o.get("cls") or "").lower()]
        if explicit_labels:
            label_leaves = explicit_labels[:1]
            delta_leaves = [o for o in others if o not in label_leaves]
        else:
            non_deltas = [o for o in others if not DELTA_RE.search(text_of(o)) and "delta" not in (o.get("cls") or "").lower()]
            if non_deltas:
                label_leaves = [non_deltas[0]]
                delta_leaves = [o for o in others if o is not non_deltas[0]]
            else:
                label_leaves = [others[0]]
                delta_leaves = others[1:]

    label = " ".join(text_of(o).strip() for o in label_leaves if text_of(o).strip())
    dt = " · ".join(text_of(o).strip() for o in delta_leaves if text_of(o).strip())
    primary_delta = delta_leaves[0] if delta_leaves else None
    primary_dt = text_of(primary_delta).strip() if primary_delta else dt
    trend = "none"
    if primary_dt:
        trend = "up" if re.search(r"[▲↑⬆]|^\+", primary_dt) else "down" if re.search(r"[▼↓⬇]|^[\-−]", primary_dt) else "flat"
    sentiment = _sentiment(primary_dt, primary_delta.get("c") if primary_delta else None) if primary_dt else "neutral"
    return ir.KPI(value=text_of(val).strip(), label=label[:120], delta=dt, trend=trend, sentiment=sentiment)


def _kpi_fallback(n: dict, ctx: Ctx) -> ir.KPI:
    tl = text_leaves(n)
    if not tl:
        return ir.KPI(value=text_of(n).strip()[:20] or "—")
    val = max(tl, key=lambda l: l.get("fs", 0))
    others = [l for l in tl if l is not val]
    label = text_of(others[0]).strip()[:120] if others else ""
    delta_leaves = others[1:]
    dt = " · ".join(text_of(o).strip() for o in delta_leaves if text_of(o).strip())
    primary_delta = delta_leaves[0] if delta_leaves else None
    primary_dt = text_of(primary_delta).strip() if primary_delta else dt
    trend = "none"
    if primary_dt:
        trend = "up" if re.search(r"[▲↑⬆]|^\+", primary_dt) else "down" if re.search(r"[▼↓⬇]|^[\-−]", primary_dt) else "flat"
    sentiment = _sentiment(primary_dt, primary_delta.get("c") if primary_delta else None) if primary_dt else "neutral"
    return ir.KPI(value=text_of(val).strip(), label=label, delta=dt, trend=trend, sentiment=sentiment)


def _similar_sizes(kids: list[dict], tol: float = 0.3) -> bool:
    ws = [k["box"]["w"] for k in kids]
    hs = [k["box"]["h"] for k in kids]
    return (max(ws) - min(ws)) <= tol * max(ws) and (max(hs) - min(hs)) <= max(tol * max(hs), 40)


def _horizontal(kids: list[dict]) -> bool:
    """True if the kids are laid out side-by-side (at least one row with 2+ items)."""
    if len(kids) < 2:
        return False
    a, b = kids[0]["box"], kids[1]["box"]
    ov = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return ov > 0.4 * min(a["h"], b["h"]) and b["x"] >= a["x"] + a["w"] - 4


def try_kpis(kids: list[dict], ctx: Ctx, src: dict) -> ir.KPIGroup | None:
    if len(kids) < 2 or len(kids) > 8:
        return None
    kpis = [_kpi_from(k, ctx) for k in kids]
    if sum(k is not None for k in kpis) < max(2, int(0.75 * len(kids))):
        return None
    items = [k if k is not None else _kpi_fallback(kid, ctx) for kid, k in zip(kids, kpis)]
    return ir.KPIGroup(items=items, src=src)


def _is_short_icon_leaf(l: dict) -> bool:
    t = text_of(l).strip()
    cls = (l.get("cls") or "").lower()
    if "icon" in cls and len(t) <= 6:
        return True
    return 0 < len(t) <= 4 and not re.search(r"[A-Za-z0-9]", t)


def _is_deliverable_leaf(l: dict, is_last: bool) -> bool:
    if not is_last:
        return False
    t = text_of(l).strip()
    if not t or len(t) > 70:
        return False
    cls = (l.get("cls") or "").lower()
    if "deliverable" in cls or "footer" in cls:
        return True
    if t.startswith(("▸", "✓", "✔", "→")):
        return True
    v = l.get("v") or {}
    borders = v.get("borders") or []
    has_top_border = bool(borders and borders[0])
    if has_top_border and _color_tone(l.get("c")) in ("positive", "primary"):
        return True
    return False


def _card_from(n: dict, ctx: Ctx) -> ir.Card | None:
    ls = leaves(n)
    if any(l["tag"] in ("table", "chart") for l in ls):
        return None
    tl = [l for l in ls if l.get("runs")]
    if not tl and not any(l["tag"] == "list" for l in ls):
        return None
    card = ir.Card(tone=_node_tone(n))
    icon = next((l for l in ls if l["tag"] == "svg" and max(l["box"]["w"], l["box"]["h"]) <= 80), None)
    if icon:
        card.icon_svg = icon["svg"]

    icon_leaves = [l for l in tl if _is_short_icon_leaf(l)]
    deliverable_leaf = tl[-1] if len(tl) >= 2 and _is_deliverable_leaf(tl[-1], True) else None

    title_cands = [
        l for l in tl
        if l not in icon_leaves
        and l is not deliverable_leaf
        and not any(k in (l.get("cls") or "").lower() for k in ("num", "kicker", "eyebrow", "security-badge"))
    ]
    if not title_cands:
        title_cands = [l for l in tl if l not in icon_leaves and l is not deliverable_leaf] or tl

    scored = sorted(
        title_cands,
        key=lambda l: (
            -(
                l.get("fs", 0)
                + (4 if (l.get("fw") or 400) >= 600 else 0)
                + (6 if re.fullmatch(r"h[1-6]", l.get("tag", "")) else 0)
            ),
            tl.index(l),
        ),
    )
    title = scored[0] if scored else None
    if title is not None and len(text_of(title)) > 90:
        title = None

    if icon_leaves:
        card.badge = text_of(icon_leaves[0]).strip()

    for l in ls:
        if l in icon_leaves:
            continue
        if l is deliverable_leaf:
            card.footer = text_of(l).strip()
            if card.tone == "none":
                ft = _color_tone(l.get("c"))
                if ft != "none":
                    card.tone = ft
            continue
        if l is title:
            card.title = text_of(l).strip()
            if card.tone == "none":
                tt = _color_tone(l.get("c"))
                if tt != "none":
                    card.tone = tt
        elif l["tag"] == "list":
            card.bullets.extend(ir.ListItem(runs=to_runs(it["runs"]), level=it["level"]) for it in l["items"])
        elif l.get("runs"):
            t = text_of(l).strip()
            cls = (l.get("cls") or "").lower()
            before_title = title is not None and l["box"]["y"] < title["box"]["y"] - 2
            is_kicker_cls = any(k in cls for k in ("num", "kicker", "eyebrow", "badge"))
            if not card.kicker and (before_title or is_kicker_cls or (title is None and not card.title)) and len(t) <= 50:
                card.kicker = t
            else:
                if card.body:
                    card.body.append(ir.Run(t="\n"))
                card.body.extend(to_runs(l["runs"]))

    if card.badge and card.title and not card.title.startswith(card.badge):
        card.title = f"{card.badge} {card.title}"

    if not (card.title or card.body or card.bullets):
        return None
    return card


def try_cards(parent: dict, kids: list[dict], ctx: Ctx, src: dict) -> ir.CardGrid | None:
    if len(kids) < 2 or len(kids) > 12:
        return None
    if not _horizontal(kids) and not (parent.get("gridCols", 0) >= 2):
        return None
    visual = sum(has_visual(k) for k in kids)
    if visual < 0.6 * len(kids) and len(kids) < 3:
        return None
    if not _similar_sizes(kids, 0.35):
        return None
    cards = [_card_from(k, ctx) for k in kids]
    if any(c is None for c in cards):
        return None
    kickers = [c.kicker for c in cards if c.kicker]
    variant = "cards"
    if len(kickers) >= 0.75 * len(cards):
        if all(STEP_RE.match(k) for k in kickers):
            variant = "process"
        elif all(DATE_RE.search(k) for k in kickers):
            variant = "timeline"
    return ir.CardGrid(cards=cards, variant=variant, src=src)


def _is_decorative(n: dict) -> bool:
    return n.get("pos") in ("absolute", "fixed") and not text_of(n).strip() and \
        not any(l["tag"] in ("img", "chart", "canvas", "table") for l in leaves(n))


def _is_footer_node(n: dict, ctx: Ctx) -> bool:
    if not ctx.slide_box:
        return False
    tag = n.get("tag", "")
    cls = (n.get("cls") or "").lower()
    cls_tokens = set(cls.split())
    return tag == "footer" or "slide-footer" in cls or "footer" in cls_tokens


def _extract_footer_node(n: dict, ctx: Ctx) -> None:
    if n.get("runs"):
        parts = [p.strip() for p in text_of(n).split("\n") if p.strip()]
    else:
        parts = [text_of(l).strip() for l in text_leaves(n) if text_of(l).strip()]
    ctx.footer_parts.extend(parts)


def _overlapping(kids: list[dict]) -> bool:
    boxes = [k["box"] for k in kids if k["box"]["w"] > 4 and k["box"]["h"] > 4]
    hits = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox > 8 and oy > 8 and ox * oy > 0.15 * min(a["w"] * a["h"], b["w"] * b["h"]):
                hits += 1
    return hits >= 2


def build_freeform(n: dict, ctx: Ctx) -> ir.Freeform:
    sb = dict(n["box"])
    sb["w"], sb["h"] = max(sb["w"], 1), max(sb["h"], 1)
    els: list[ir.FreeElement] = []

    def add(node: dict):
        b = node["box"]
        rx, ry = (b["x"] - sb["x"]) / sb["w"], (b["y"] - sb["y"]) / sb["h"]
        rw, rh = b["w"] / sb["w"], b["h"] / sb["h"]
        v = node.get("v") or {}
        if has_visual(node) and node is not n:
            border = v.get("border") or {}
            els.append(ir.FreeElement(kind="box", x=rx, y=ry, w=rw, h=rh, fill=v.get("bg") or (v.get("grad") or [None])[0],
                                      fill_alpha=v.get("bgA", 1.0), border=border.get("c"), border_w=border.get("w", 0),
                                      radius_px=v.get("radius", 0)))
        if node.get("runs"):
            tb = node.get("tbox") or b
            els.append(ir.FreeElement(kind="text", x=(tb["x"] - sb["x"]) / sb["w"], y=(tb["y"] - sb["y"]) / sb["h"],
                                      w=max(tb["w"], 10) / sb["w"], h=max(tb["h"], 10) / sb["h"],
                                      runs=to_runs(node["runs"]), font_px=node.get("fs", 16), align=_align(node.get("ta")),
                                      bold=(node.get("fw") or 400) >= 600, color=node.get("c")))
            return
        if node["tag"] in ("img", "canvas") and node.get("img"):
            els.append(ir.FreeElement(kind="image", x=rx, y=ry, w=rw, h=rh, b64=node["img"]["b64"], mime=node["img"]["mime"]))
            return
        if node["tag"] == "svg":
            els.append(ir.FreeElement(kind="vector", x=rx, y=ry, w=rw, h=rh, svg=node["svg"]))
            return
        if node["tag"] == "hr":
            els.append(ir.FreeElement(kind="line", x=rx, y=ry, w=rw, h=0, border=node.get("c")))
            return
        if node["tag"] == "list":
            for it in node["items"]:
                ib = it["box"]
                els.append(ir.FreeElement(kind="text", x=(ib["x"] - sb["x"]) / sb["w"], y=(ib["y"] - sb["y"]) / sb["h"],
                                          w=ib["w"] / sb["w"], h=ib["h"] / sb["h"],
                                          runs=[ir.Run(t="• ")] + to_runs(it["runs"]), font_px=node.get("fs", 16)))
            return
        for k in node.get("kids", []) or []:
            add(k)

    add(n)
    return ir.Freeform(elements=els, aspect=sb["w"] / max(sb["h"], 1), src=rel_box(n, ctx))


def _build_callout(n: dict, tl: list[dict], src: dict) -> ir.Callout:
    rem = list(tl)
    icon_prefix = ""
    if len(rem) >= 2:
        t0 = text_of(rem[0]).strip()
        if len(t0) <= 8 and not re.search(r"[A-Za-z0-9]", t0):
            icon_prefix = t0
            rem = rem[1:]

    title = ""
    badge = ""
    runs_src = rem

    if len(rem) >= 2:
        b0, b1 = rem[0]["box"], rem[1]["box"]
        same_row_01 = abs(b0["y"] - b1["y"]) < max(b0["h"], b1["h"], 24) and b1["x"] >= b0["x"] + b0["w"] * 0.5
        b_last = rem[-1]["box"]
        last_cls = (rem[-1].get("cls") or "").lower()
        last_is_right_badge = (
            rem[-1]["tag"] == "button"
            or any(k in last_cls for k in ("badge", "btn", "button", "pill", "tag"))
            or has_visual(rem[-1])
            or (len(rem) >= 3 and b_last["x"] > b0["x"] + 80)
        )
        if same_row_01 and (len(rem) >= 3 or has_visual(rem[1]) or "badge" in (rem[1].get("cls") or "").lower() or rem[1]["tag"] == "button"):
            title = text_of(rem[0]).strip()
            badge = text_of(rem[1]).strip()
            runs_src = rem[2:]
        elif last_is_right_badge and len(rem) >= 2:
            badge = text_of(rem[-1]).strip()
            mid = rem[:-1]
            if len(mid) >= 2 and ((mid[0].get("fw") or 400) >= 600 or mid[0].get("fs", 0) >= mid[1].get("fs", 0)):
                title = text_of(mid[0]).strip()
                runs_src = mid[1:]
            elif len(mid) == 1:
                title = text_of(mid[0]).strip()
                runs_src = []
            else:
                runs_src = mid
        elif (rem[0].get("fw") or 400) >= 600 or rem[0].get("fs", 0) > rem[1].get("fs", 0):
            title = text_of(rem[0]).strip()
            runs_src = rem[1:]
    elif len(rem) == 1 and icon_prefix:
        title = text_of(rem[0]).strip()
        runs_src = []

    if icon_prefix:
        title = f"{icon_prefix} {title}".strip() if title else icon_prefix

    runs: list[ir.Run] = []
    for i, l in enumerate(runs_src):
        if i:
            runs.append(ir.Run(t="\n"))
        runs.extend(to_runs(l["runs"]))
    return ir.Callout(runs=runs, title=title, badge=badge, tone=_tone(n), src=src)


# ---------------------------------------------------------------- main recursion

def classify(n: dict, ctx: Ctx, column: int | None = None) -> list[ir.Block]:
    if _is_footer_node(n, ctx):
        _extract_footer_node(n, ctx)
        return []

    if n.get("runs") is not None or n["tag"] in ("list", "table", "chart", "img", "canvas", "svg", "hr", "#text"):
        blocks = classify_leaf(n, ctx)
        for b in blocks:
            b.column = column
        return blocks

    kids = [k for k in (n.get("kids") or []) if not _is_decorative(k)]
    if not kids:
        return []
    src = rel_box(n, ctx)

    def tag(blocks: list[ir.Block]) -> list[ir.Block]:
        for b in blocks:
            if b.column is None:
                b.column = column
        return blocks

    # Freeform: absolutely positioned / overlapping compositions (diagrams, infographics)
    absolute = sum(1 for k in kids if k.get("pos") == "absolute")
    if (absolute >= 3 or _overlapping(kids)) and text_of(n).strip():
        return tag([build_freeform(n, ctx)])

    meaningful = []
    for k in kids:
        if _is_footer_node(k, ctx):
            _extract_footer_node(k, ctx)
            continue
        if text_of(k).strip() or (leaves(k) and any(l["tag"] in ("img", "chart", "canvas", "table", "svg") for l in leaves(k))):
            meaningful.append(k)

    # Callout: a visually boxed container holding concise text (e.g. V-Cycle step, Air-Gap banner, CTA banner)
    tl = text_leaves(n)
    ls = leaves(n)
    is_slide_root = (n.get("data") or {}).get("hpSlide") == "1" or "slide" in (n.get("cls") or "").lower().split()
    multi_visual_kids = (
        len(meaningful) >= 2
        and (_horizontal(meaningful) or n.get("gridCols", 0) >= 2)
        and sum(has_visual(k) for k in meaningful) >= 2
    )
    if (
        not is_slide_root
        and not multi_visual_kids
        and has_visual(n)
        and 1 <= len(tl) <= 4
        and len(ls) == len(tl)
        and n["box"]["h"] <= 0.45 * ctx.slide_box.get("h", 1080)
        and len(text_of(n)) < 500
        and not NUM_RE.match(text_of(n).strip())
    ):
        return tag([_build_callout(n, tl, src)])

    if len(meaningful) >= 2:
        kpis = try_kpis(meaningful, ctx, src)
        if kpis:
            return tag([kpis])
        cards = try_cards(n, meaningful, ctx, src)
        if cards:
            return tag([cards])
        if (_horizontal(meaningful) or n.get("gridCols", 0) in (2, 3)) and column is None and len(meaningful) <= 3:
            rg = ctx.next_row_group
            ctx.next_row_group += 1
            ncols = len(meaningful)
            out: list[ir.Block] = []
            for i, k in enumerate(meaningful):
                col_blocks = classify(k, ctx, column=i)
                for b in col_blocks:
                    if b.row_group is None:
                        b.row_group = rg
                    if b.total_columns is None:
                        b.total_columns = ncols
                out.extend(col_blocks)
            return out

    out = []
    for k in kids:
        if _is_footer_node(k, ctx):
            continue
        out.extend(classify(k, ctx, column))
    return _merge_quote_attribution(out)


def _merge_quote_attribution(blocks: list[ir.Block]) -> list[ir.Block]:
    out: list[ir.Block] = []
    for b in blocks:
        prev = out[-1] if out else None
        if isinstance(prev, ir.Quote) and isinstance(b, ir.Paragraph):
            t = ir.runs_text(b.runs).strip()
            if t[:1] in "—–-~" and len(t) < 120:
                prev.attribution = t.lstrip("—–-~ ").strip()
                continue
        out.append(b)
    return out
