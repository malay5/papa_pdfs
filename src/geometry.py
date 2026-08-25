"""Recover table structure from the catalogs' vector page geometry.

The MBCI catalogs carry no embedded fonts -- every glyph is a filled vector
path -- so the text itself has to come from OCR.  The *layout*, however, is
intact: each table cell is a filled rectangle and each rule is a stroked line.
This module turns those primitives back into a grid of cells that OCR output
can be poured into.
"""

from dataclasses import dataclass, field

# A filled rect is a table cell candidate only inside these bounds.  Glyph
# outlines are the main thing being excluded: they are filled rects too, but
# only a couple of points wide.
MIN_CELL_W = 18.0
MIN_CELL_H = 7.0
MAX_CELL_H = 34.0
# A shaded rect this tall is a full-height column stripe, not a row cell:
# some catalogs shade their lookup matrices by column instead of by row.
MIN_STRIPE_H = 60.0
MIN_STRIPE_W = 12.0

# Fill luminance at or below this reads as a band carrying knockout-white
# text: the dark page header, the coloured section banners, and the mid-grey
# column-header strips.  Row shading sits well above it, around 0.91.
DARK_FILL_MAX_LUMA = 0.82
# Fills lighter than this are page background, not a cell shade.
WHITE_FILL_MIN_LUMA = 0.985

# Vertical slack when deciding whether two rects sit on the same band, or
# whether two bands stack into one table.
BAND_TOL = 1.5
STACK_TOL = 3.0
# A stroked rule must be at least this wide to count as table structure.
MIN_RULE_W = 40.0
# How far above a table's first band a rule may sit and still be read as the
# underline of a spanning header ("DESCRIPTION", "PRICED PER SQUARE").
SPAN_HEADER_MAX_GAP = 26.0


def luma(color):
    """Perceptual brightness of a pdfplumber colour, or None if unknown."""
    if not color:
        return None
    if isinstance(color, (int, float)):
        return float(color)
    vals = [float(c) for c in color]
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 3:
        r, g, b = vals
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    if len(vals) == 4:  # CMYK
        c, m, y, k = vals
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    return None


@dataclass
class Cell:
    x0: float
    top: float
    x1: float
    bottom: float
    dark: bool = False
    text: str = ""

    @property
    def bbox(self):
        return (self.x0, self.top, self.x1, self.bottom)


@dataclass
class Band:
    top: float
    bottom: float
    cells: list = field(default_factory=list)
    # True for the ruled, un-shaded header rebuilt by _span_header.  It has to
    # be recorded rather than inferred: it is the one header band with no fill
    # to recognise it by, and counting its cells does not distinguish it -- a
    # data row shades only its alternate columns and can land on the same
    # count.
    is_span_header: bool = False

    @property
    def dark(self):
        return bool(self.cells) and all(c.dark for c in self.cells)

    @property
    def x0(self):
        return min(c.x0 for c in self.cells)

    @property
    def x1(self):
        return max(c.x1 for c in self.cells)


@dataclass
class Table:
    bands: list
    page_number: int = 0

    @property
    def bbox(self):
        return (
            min(b.x0 for b in self.bands),
            min(b.top for b in self.bands),
            max(b.x1 for b in self.bands),
            max(b.bottom for b in self.bands),
        )

    @property
    def cells(self):
        return [c for b in self.bands for c in b.cells]

    def column_edges(self, tol=2.0):
        """Every distinct vertical boundary used anywhere in the table.

        Data rows only shade alternate columns, so no single band knows the
        whole grid; the union of all bands' edges does.
        """
        raw = sorted({round(v, 2) for c in self.cells for v in (c.x0, c.x1)})
        edges = []
        for value in raw:
            if edges and value - edges[-1] <= tol:
                edges[-1] = (edges[-1] + value) / 2.0
                continue
            edges.append(value)
        return edges

    def grid(self):
        """The table as full rows of cells, one entry per column.

        Unshaded columns leave no rect behind, so they are materialised here
        as empty cells; a cell wider than one column is kept once and its
        span recorded.
        """
        edges = self.column_edges()
        rows = []
        for band in self.bands:
            row, index = [], 0
            while index < len(edges) - 1:
                left, right = edges[index], edges[index + 1]
                match = next(
                    (c for c in band.cells if abs(c.x0 - left) <= 2.0 and c.x1 > left + 2.0),
                    None,
                )
                if match is None:
                    row.append((Cell(left, band.top, right, band.bottom), 1))
                    index += 1
                    continue
                span = 1
                while index + span < len(edges) - 1 and edges[index + span] < match.x1 - 2.0:
                    span += 1
                row.append((match, span))
                index += span
            rows.append((band, row))
        return edges, rows


def _dedupe(rects):
    """Drop repeated rects (each band is emitted as both a fill and a stroke)."""
    seen, out = set(), []
    for r in rects:
        key = tuple(round(v, 1) for v in (r["x0"], r["top"], r["x1"], r["bottom"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def cell_rects(page):
    """Filled rects on `page` that plausibly bound a table cell."""
    out = []
    page_area = page.width * page.height
    for r in _dedupe(page.rects):
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if w < MIN_CELL_W or not (MIN_CELL_H <= h <= MAX_CELL_H):
            continue
        if w * h > 0.5 * page_area:
            continue
        lum = luma(r.get("non_stroking_color"))
        if lum is None or lum >= WHITE_FILL_MIN_LUMA:
            continue
        out.append(Cell(r["x0"], r["top"], r["x1"], r["bottom"], dark=lum <= DARK_FILL_MAX_LUMA))
    return out


def _bands(cells):
    """Group cells that share a top/bottom into left-to-right bands."""
    bands = []
    for cell in sorted(cells, key=lambda c: (c.top, c.x0)):
        for band in bands:
            if abs(band.top - cell.top) <= BAND_TOL and abs(band.bottom - cell.bottom) <= BAND_TOL:
                band.cells.append(cell)
                break
        else:
            bands.append(Band(cell.top, cell.bottom, [cell]))
    for band in bands:
        band.cells.sort(key=lambda c: c.x0)
        # Collapse cells that a band draws twice at slightly different widths.
        merged = []
        for cell in band.cells:
            if merged and cell.x0 - merged[-1].x0 < 1.0:
                merged[-1].x1 = max(merged[-1].x1, cell.x1)
                continue
            merged.append(cell)
        band.cells = merged
    bands.sort(key=lambda b: b.top)
    return bands


def _x_overlap(a, b):
    lo, hi = max(a.x0, b.x0), min(a.x1, b.x1)
    span = min(a.x1 - a.x0, b.x1 - b.x0)
    return (hi - lo) / span if span > 0 else 0.0


def horizontal_rules(page):
    """Long horizontal strokes, as (x0, y, x1) triples."""
    rules = []
    for line in page.lines:
        if abs(line["y0"] - line["y1"]) > 0.6:
            continue
        if line["x1"] - line["x0"] < MIN_RULE_W:
            continue
        rules.append((line["x0"], line["top"], line["x1"]))
    for r in _dedupe(page.rects):  # thin filled rects are drawn as rules too
        if r["bottom"] - r["top"] <= 1.2 and r["x1"] - r["x0"] >= MIN_RULE_W:
            rules.append((r["x0"], r["top"], r["x1"]))
    return sorted(set(rules), key=lambda t: (t[1], t[0]))


def _span_header(bands, rules):
    """Rebuild the ruled, un-shaded header that sits above a table's first band.

    Those cells ("DESCRIPTION", "PRICED PER SQUARE") are black on white with
    only a rule above them, so no fill marks them out.
    """
    first = bands[0]
    candidates = [r for r in rules if 0 < first.top - r[1] <= SPAN_HEADER_MAX_GAP]
    if not candidates:
        return None
    y = max(r[1] for r in candidates)
    segments = [r for r in candidates if abs(r[1] - y) <= BAND_TOL]
    segments = [s for s in segments if _x_overlap(
        Cell(s[0], 0, s[2], 0), Cell(first.x0, 0, first.x1, 0)) > 0.05]
    if not segments:
        return None
    cells = [Cell(s[0], y, s[2], first.top) for s in sorted(segments)]
    return Band(y, first.top, cells, is_span_header=True)


def tables(page):
    """Every table on `page`, as vertically contiguous stacks of bands."""
    bands = _bands(cell_rects(page))
    rules = horizontal_rules(page)
    stacks, current = [], []
    for band in bands:
        if current:
            prev = current[-1]
            contiguous = band.top - prev.bottom <= STACK_TOL
            if not (contiguous and _x_overlap(prev, band) > 0.5):
                stacks.append(current)
                current = []
        current.append(band)
    if current:
        stacks.append(current)

    out = []
    for stack in stacks:
        # A lone shaded band with no neighbours is a section title, not a table.
        if len(stack) == 1 and len(stack[0].cells) < 2:
            continue
        header = _span_header(stack, rules)
        out.append(Table(([header] if header else []) + stack, page.page_number))
    return out


def column_stripes(page):
    """Full-height shaded columns, as (x0, x1, top, bottom) tuples.

    Where a table is shaded by column rather than by row, these are all the
    grid the vector layout gives: the columns are exact, and the rows have to
    come from the text.
    """
    found = []
    for r in _dedupe(page.rects):
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if h < MIN_STRIPE_H or w < MIN_STRIPE_W or w > 0.5 * page.width:
            continue
        lum = luma(r.get("non_stroking_color"))
        if lum is None or lum >= WHITE_FILL_MIN_LUMA:
            continue
        found.append((r["x0"], r["x1"], r["top"], r["bottom"]))
    if len(found) < 2:
        return []
    # Keep only stripes that share the dominant vertical extent.
    spans = {}
    for x0, x1, top, bottom in found:
        key = (round(top, 0), round(bottom, 0))
        spans.setdefault(key, []).append((x0, x1, top, bottom))
    best = max(spans.values(), key=len)
    return sorted(best) if len(best) >= 2 else []


def banners(page, min_width_ratio=0.16):
    """Wide coloured strips: the running page header and the section titles."""
    found = {}
    for r in _dedupe(page.rects):
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if w < min_width_ratio * page.width or not (12 <= h <= 44):
            continue
        lum = luma(r.get("non_stroking_color"))
        if lum is None or lum > DARK_FILL_MAX_LUMA:
            continue
        cell = Cell(r["x0"], r["top"], r["x1"], r["bottom"], dark=True)
        found[tuple(round(v, 1) for v in cell.bbox)] = cell
    return sorted(found.values(), key=lambda c: (c.top, c.x0))
