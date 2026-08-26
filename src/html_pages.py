"""Rebuild each catalog page as HTML, laid out from the PDF's own geometry.

The source PDFs have no text layer at all -- every glyph is a vector path --
so they cannot be searched, selected or copied from.  These pages carry the
same layout with the extracted text as real text.

Position and colour come from the PDF: fills, rules and cell boxes are read
back out of the vector layer, so the result keeps the catalog's proportions
rather than approximating them.  The text is what extract.py read, with the
reviewed corrections applied.

    python3 src/html_pages.py                      # every catalog
    python3 src/html_pages.py --catalog ssr --pages 4-6
"""

import argparse
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfplumber

import extract
import geometry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fills covering most of the page are the sheet itself, not a design element.
BACKGROUND_MIN_AREA = 0.85
# Rules thinner than this would vanish; the PDF draws some at a hairline.
MIN_RULE_PX = 0.6
# Type is set at roughly this fraction of the band it sits in, which lands
# within a point of the original at every band height in these catalogs.
TEXT_HEIGHT_RATIO = 0.42
MIN_FONT_PT, MAX_FONT_PT = 5.0, 11.0
# Below this luminance a fill needs knockout-white type over it.
KNOCKOUT_MAX_LUMA = 0.62


def _rgb(colour):
    """A PDF colour of any component count as a CSS rgb()."""
    if colour is None:
        return None
    if isinstance(colour, (int, float)):
        value = [float(colour)] * 3
    else:
        value = [float(c) for c in colour]
    if len(value) == 1:
        value *= 3
    elif len(value) == 4:                      # CMYK
        c, m, y, k = value
        value = [(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)]
    elif len(value) != 3:
        return None
    return tuple(max(0, min(255, round(channel * 255))) for channel in value[:3])


def _luma(rgb):
    red, green, blue = (channel / 255 for channel in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _fills(page):
    """Every filled rectangle worth drawing, back to front."""
    area = page.width * page.height
    out = []
    for rect in page.rects:
        if not rect.get("fill"):
            continue
        colour = _rgb(rect.get("non_stroking_color"))
        if colour is None:
            continue
        width = rect["x1"] - rect["x0"]
        height = rect["bottom"] - rect["top"]
        if width <= 0 or height <= 0:
            continue
        if width * height >= BACKGROUND_MIN_AREA * area:
            continue                            # the sheet itself
        if colour == (255, 255, 255):
            continue                            # white on white
        out.append((rect["x0"], rect["top"], width, height, colour))
    return out


def _rules(page):
    """Stroked lines, as thin filled boxes."""
    out = []
    for line in page.lines:
        colour = _rgb(line.get("stroking_color")) or (0, 0, 0)
        width = max(line["x1"] - line["x0"], MIN_RULE_PX)
        height = max(line["bottom"] - line["top"], MIN_RULE_PX)
        out.append((line["x0"], line["top"], width, height, colour))
    return out


def _background_at(fills, bbox):
    """The colour of the last fill covering `bbox`, or None for bare paper."""
    x0, top, x1, bottom = bbox
    mid_x, mid_y = (x0 + x1) / 2, (top + bottom) / 2
    found = None
    for fx, fy, fw, fh, colour in fills:
        if fx <= mid_x <= fx + fw and fy <= mid_y <= fy + fh:
            found = colour
    return found


# Average glyph advance as a fraction of type size, for this sort of sans.
# Only used to decide when a label has to shrink, so it needs to be close
# rather than exact.
GLYPH_ADVANCE = 0.58


def _fit(text, width, height, base, max_lines=1, advance=GLYPH_ADVANCE):
    """Largest size at or below `base` that keeps `text` inside its box.

    Column labels carry their own line breaks ("WEIGHT\nEACH"), so the break
    the label asks for is counted rather than a wrap being guessed at.
    """
    given = text.split("\n")
    allowed = max(max_lines, len(given))
    size = base
    while size > MIN_FONT_PT:
        per_line = max(1, int(width / (size * advance)))
        lines = sum(max(1, -(-len(part) // per_line)) for part in given)
        if lines <= allowed and size * 1.2 * lines <= height:
            break
        size -= 0.25
    return max(MIN_FONT_PT, size)


def _text_box(text, bbox, fills, align="center", weight=None, ratio=None,
              lines=1):
    """One positioned run of text, coloured to stay legible on its fill."""
    if not text:
        return ""
    x0, top, x1, bottom = bbox
    height = bottom - top
    size = max(MIN_FONT_PT, min(MAX_FONT_PT,
                                height * (ratio or TEXT_HEIGHT_RATIO)))
    # Bold labels are set with a little tracking, so they run wider than the
    # body advance would predict.
    advance = 0.78 if weight else GLYPH_ADVANCE
    size = _fit(text, x1 - x0 - 5, height, size, lines, advance)
    background = _background_at(fills, bbox)
    colour = "#fff" if background and _luma(background) < KNOCKOUT_MAX_LUMA \
        else "#231f20"
    style = (f"left:{x0:.2f}pt;top:{top:.2f}pt;"
             f"width:{x1 - x0:.2f}pt;height:{height:.2f}pt;"
             f"font-size:{size:.2f}pt;color:{colour};"
             f"justify-content:{'flex-start' if align == 'left' else 'center'};"
             f"text-align:{align}")
    if weight:
        style += f";font-weight:{weight};letter-spacing:.01em"
    return f'  <div class="t" style="{style}">{html.escape(text)}</div>\n'


# The drawings are vector art in the same layer as the glyph outlines, so
# they cannot be told apart by object type.  They are lifted as pictures
# instead: everything outside the tables and banners, at this resolution.
ART_DPI = 150
# Art nearer than this vertically belongs to the same drawing.
ART_GAP = 9.0
# Below this a region is a stray rule end or a speck, not a drawing.
ART_MIN_W, ART_MIN_H = 24.0, 16.0
ART_PAD = 2.0
# Palette size for the art crops; these are drawings, not photographs.
ART_COLOURS = 64


def _art_regions(page, reserved):
    """Blocks of vector art that no table or banner accounts for."""
    boxes = []
    for item in list(page.curves) + list(page.images):
        x0, top = item["x0"], item["top"]
        x1, bottom = item["x1"], item["bottom"]
        if x1 - x0 <= 0 or bottom - top <= 0:
            continue
        mid_x, mid_y = (x0 + x1) / 2, (top + bottom) / 2
        if any(rx0 - 1 <= mid_x <= rx1 + 1 and rtop - 1 <= mid_y <= rbot + 1
               for rx0, rtop, rx1, rbot in reserved):
            continue
        boxes.append((x0, top, x1, bottom))
    if not boxes:
        return []

    # Group by vertical run: art on the same drawing shares a band of the
    # page, and the gaps between drawings are far wider than the gaps inside
    # one.
    boxes.sort(key=lambda b: b[1])
    groups, current = [], [boxes[0]]
    for box in boxes[1:]:
        if box[1] <= max(b[3] for b in current) + ART_GAP:
            current.append(box)
        else:
            groups.append(current)
            current = [box]
    groups.append(current)

    regions = []
    for group in groups:
        x0 = min(b[0] for b in group) - ART_PAD
        top = min(b[1] for b in group) - ART_PAD
        x1 = max(b[2] for b in group) + ART_PAD
        bottom = max(b[3] for b in group) + ART_PAD
        if x1 - x0 < ART_MIN_W or bottom - top < ART_MIN_H:
            continue
        regions.append((max(0, x0), max(0, top),
                        min(page.width, x1), min(page.height, bottom)))
    return regions


def _write_art(pdf_path, page_index, regions, out_dir, stem):
    """Crop each art region out of a render; returns (bbox, filename) pairs."""
    if not regions:
        return []
    import ocr
    image = ocr.render(pdf_path, page_index, dpi=ART_DPI)
    scale = ART_DPI / 72.0
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for index, (x0, top, x1, bottom) in enumerate(regions):
        crop = image.crop((int(x0 * scale), int(top * scale),
                           int(x1 * scale), int(bottom * scale)))
        if crop.width < 2 or crop.height < 2:
            continue
        name = f"{stem}_{index:02d}.png"
        # Line art on paper: a small palette is indistinguishable from full
        # colour here and halves the tree.
        crop.convert("RGB").quantize(colors=ART_COLOURS).save(
            os.path.join(out_dir, name), optimize=True)
        out.append(((x0, top, x1, bottom), name))
    return out


def _page_html(page, pdf_path, page_index, stored, art_dir=None, art_href=""):
    """One page: its fills and rules, with the extracted text placed on top."""
    width, height = page.width, page.height
    fills = _fills(page)
    rules = _rules(page)
    tables = geometry.tables(page)
    found_banners = geometry.banners(page)
    stripes = geometry.column_stripes(page)

    # document.json keeps the banner text but not its box, so the boxes are
    # read back and paired with the text in the order extract_page used.
    table_boxes = [t.bbox for t in tables]
    found_banners = [b for b in found_banners
                     if not any(box[1] - 1 <= b.top and b.bottom <= box[3] + 1
                                for box in table_boxes)]
    # A column-shaded matrix draws its own header as a wide coloured strip,
    # which is indistinguishable from a section banner by shape alone.
    # extract_page consumes it as the table's header; it must not be drawn
    # again here, on top of the column labels it became.
    if stripes and not tables:
        top = stripes[0][2]
        found_banners = [b for b in found_banners
                         if not (0 < top - b.top <= 30
                                 and b.x1 - b.x0 > 0.5 * width)]

    heads = [b for b in found_banners if b.top < extract.PAGE_HEADER_MAX_TOP]
    rest = [b for b in found_banners if b.top >= extract.PAGE_HEADER_MAX_TOP]
    banners = []
    head_text = [t for t in (stored.get("title"), stored.get("category")) if t]
    for banner, text in zip(heads, head_text):
        banners.append({"text": text, "bbox": banner.bbox})
    for banner, text in zip(rest, stored.get("sections", [])):
        banners.append({"text": text, "bbox": banner.bbox})
    stored = {**stored, "_banners": banners}

    # Anything the extraction accounted for is drawn as text, so it must not
    # also be lifted as a picture.  The stored boxes matter as well as the
    # geometry ones: a column-shaded matrix has no cell rectangles for
    # geometry.tables to return, and its stripes cover only every other
    # column, which would leave the rest to be captured twice.
    reserved = [t.bbox for t in tables] + [b["bbox"] for b in banners]
    reserved += [tuple(t["bbox"]) for t in stored.get("tables", [])]
    if stripes:
        reserved += [(s[0], s[2], s[1], s[3]) for s in stripes]
    art = []
    if art_dir is not None:
        art = _write_art(pdf_path, page_index, _art_regions(page, reserved),
                         art_dir, f"p{page_index + 1:03d}")

    parts = [f'<div class="page" style="width:{width}pt;height:{height}pt">\n']
    for x, y, w, h, colour in fills:
        parts.append(f'  <div class="f" style="left:{x:.2f}pt;top:{y:.2f}pt;'
                     f'width:{w:.2f}pt;height:{h:.2f}pt;'
                     f'background:rgb{colour}"></div>\n')
    # A few hairlines sit inside the coloured strips, where the catalog shows
    # nothing but the strip and its type.  Drawn here they read as stray marks
    # through the heading.
    banner_boxes = [b["bbox"] for b in banners]
    for x, y, w, h, colour in rules:
        mid_x, mid_y = x + w / 2, y + h / 2
        if any(bx0 <= mid_x <= bx1 and btop <= mid_y <= bbot
               for bx0, btop, bx1, bbot in banner_boxes):
            continue
        parts.append(f'  <div class="f" style="left:{x:.2f}pt;top:{y:.2f}pt;'
                     f'width:{w:.2f}pt;height:{h:.2f}pt;'
                     f'background:rgb{colour}"></div>\n')

    for (x0, top, x1, bottom), name in art:
        parts.append(f'  <img class="a" src="{art_href}{name}" alt="" '
                     f'style="left:{x0:.2f}pt;top:{top:.2f}pt;'
                     f'width:{x1 - x0:.2f}pt;height:{bottom - top:.2f}pt">\n')

    for banner in stored.get("_banners", []):
        parts.append(_text_box(banner["text"], banner["bbox"], fills,
                               weight=600, ratio=0.5))

    for table in stored.get("tables", []):
        index = table["index"]
        if index >= len(tables):
            # A column-shaded matrix leaves no cell rectangles behind, so its
            # grid is rebuilt the way _matrix_table built it: columns from the
            # stripes, rows evenly divided over the block.
            if not stripes or not table["rows"]:
                continue
            parts.append(_matrix_html(table, stripes, fills))
            continue
        edges, bands = tables[index].grid()
        count = len(edges) - 1
        rows = [row for _band, row in bands]
        data = rows[-len(table["rows"]):] if table["rows"] else []
        if len(data) != len(table["rows"]):
            data = []

        # Header labels sit in the shaded strip above the data.
        header_bands = rows[:len(rows) - len(data)] if data else []
        for row in header_bands:
            for cell, _span in row:
                pass                            # drawn from labels below

        top = table["bbox"][1]
        if table["column_labels"] and header_bands:
            for row in header_bands:
                for cell, span in row:
                    covered = extract._covered_columns(cell, edges)
                    label = ""
                    if span == 1 and covered:
                        label = table["column_labels"][covered[0]] \
                            if covered[0] < len(table["column_labels"]) else ""
                    elif covered:
                        label = table["column_groups"][covered[0]] \
                            if covered[0] < len(table["column_groups"]) else ""
                    parts.append(_text_box(label, cell.bbox, fills,
                                           weight=700, ratio=0.42, lines=3))

        for stored_row, cells in zip(table["rows"], data):
            column = 0
            for cell, span in cells:
                if column < count and column < len(stored_row):
                    parts.append(_text_box(stored_row[column], cell.bbox,
                                           fills))
                column += span

    parts.append("</div>\n")
    return "".join(parts)


def _matrix_html(table, stripes, fills):
    """Place a column-shaded matrix, whose rows are evenly spaced."""
    edges = extract._stripe_columns(stripes)
    x0, top, x1, bottom = table["bbox"]
    edges = [e for e in edges if x0 - 1 <= e <= x1 + 1]
    if len(edges) - 1 != len(table["columns"]):
        edges = [x0 + (x1 - x0) * i / len(table["columns"])
                 for i in range(len(table["columns"]) + 1)]
    pitch = (bottom - top) / len(table["rows"])
    out = []
    labels = table.get("column_labels") or []
    for position, label in enumerate(labels):
        if not label or position + 1 >= len(edges):
            continue
        out.append(_text_box(label, (edges[position], top - pitch,
                                     edges[position + 1], top), fills,
                             weight=700))
    for index, row in enumerate(table["rows"]):
        row_top = top + index * pitch
        for position, value in enumerate(row):
            if position + 1 >= len(edges):
                break
            out.append(_text_box(value, (edges[position], row_top,
                                         edges[position + 1], row_top + pitch),
                                 fills))
    return "".join(out)


STYLE = """
:root { --paper: #fff; --edge: rgba(0,0,0,.16); }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24pt 0; background: #f4f4f5;
  font-family: Montserrat, "Century Gothic", "Futura", "Avenir Next",
               "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.page {
  position: relative; margin: 0 auto 24pt; background: var(--paper);
  box-shadow: 0 1pt 3pt rgba(0,0,0,.18), 0 8pt 24pt rgba(0,0,0,.10);
  overflow: hidden;
}
.f { position: absolute; }
.a { position: absolute; image-rendering: auto; }
.t {
  position: absolute; display: flex; align-items: center;
  padding: 0 1.5pt; line-height: 1.15; white-space: pre-wrap;
  overflow: hidden; word-break: break-word;
}
.bar {
  position: sticky; top: 0; z-index: 5; background: #fff;
  border-bottom: 1px solid var(--edge); padding: 10pt 16pt;
  display: flex; gap: 16pt; align-items: baseline; flex-wrap: wrap;
  font-size: 11pt;
}
.bar a { color: #0b62c4; text-decoration: none; }
.bar a:hover { text-decoration: underline; }
.bar .muted { color: #6b7280; font-size: 10pt; }
@media print {
  body { background: #fff; padding: 0; }
  .bar { display: none; }
  .page { margin: 0; box-shadow: none; page-break-after: always; }
}
@media (prefers-color-scheme: dark) {
  body { background: #18181b; }
  .bar { background: #18181b; color: #e4e4e7; border-color: rgba(255,255,255,.14); }
  .bar a { color: #7cb7ff; }
}
"""


def _document(title, body, nav=""):
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
{nav}
{body}
"""


def write_catalog(slug, pdf_path, document, out_dir, pages=None):
    """One HTML file per page, plus an index for the catalog."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    pdf = pdfplumber.open(pdf_path)
    for stored in document["pages"]:
        number = stored["page"]
        if pages and number not in pages:
            continue
        body = _page_html(pdf.pages[number - 1], pdf_path, number - 1, stored,
                          art_dir=os.path.join(out_dir, "img"),
                          art_href="img/")
        title = f"{slug} p{number}"
        heading = stored.get("title") or slug
        nav = (f'<div class="bar"><a href="index.html">&larr; {html.escape(slug)}</a>'
               f'<b>Page {number}</b>'
               f'<span class="muted">{html.escape(heading)}</span></div>')
        name = f"page_{number:03d}.html"
        with open(os.path.join(out_dir, name), "w") as handle:
            handle.write(_document(title, body, nav))
        written.append((number, stored, name))
        pdf.pages[number - 1].close()      # pdfplumber caches every page
        print(f"  {slug} p{number}", flush=True)
    pdf.close()

    rows = []
    for number, stored, name in written:
        section = "; ".join(stored.get("sections", [])[:2])
        rows.append(
            f'<tr><td><a href="{name}">{number}</a></td>'
            f'<td>{html.escape(stored.get("title") or "")}</td>'
            f'<td>{html.escape(section)}</td>'
            f'<td>{len(stored.get("tables", []))}</td></tr>')
    body = (f'<div class="idx"><h1>{html.escape(slug)}</h1>'
            f'<p class="muted">{len(written)} pages</p>'
            f'<table><thead><tr><th>Page</th><th>Title</th><th>Sections</th>'
            f'<th>Tables</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<p><a href="../index.html">&larr; all catalogs</a></p></div>')
    with open(os.path.join(out_dir, "index.html"), "w") as handle:
        handle.write(_document(f"{slug} pages", body + INDEX_STYLE))
    return len(written)


INDEX_STYLE = """<style>
.idx { max-width: 60rem; margin: 0 auto; padding: 24pt; background: #fff;
       border-radius: 6pt; box-shadow: 0 1pt 3pt rgba(0,0,0,.12); }
.idx h1 { margin: 0 0 4pt; font-size: 20pt; text-transform: capitalize; }
.idx .muted { color: #6b7280; margin: 0 0 16pt; }
.idx table { border-collapse: collapse; width: 100%; font-size: 10pt; }
.idx th { text-align: left; border-bottom: 2px solid #e4e4e7; padding: 6pt 8pt; }
.idx td { border-bottom: 1px solid #f0f0f1; padding: 5pt 8pt; }
.idx tr:hover td { background: #fafafa; }
.idx a { color: #0b62c4; text-decoration: none; }
.idx a:hover { text-decoration: underline; }
@media (prefers-color-scheme: dark) {
  .idx { background: #1f1f23; color: #e4e4e7; }
  .idx th { border-color: #3f3f46; } .idx td { border-color: #2a2a2f; }
  .idx tr:hover td { background: #26262b; }
  .idx a { color: #7cb7ff; }
}
</style>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=os.path.join(ROOT, "data"))
    parser.add_argument("--uploads", default=os.path.join(ROOT, "pdfs"))
    parser.add_argument("--out", default=os.path.join(ROOT, "html"))
    parser.add_argument("--catalog", action="append")
    parser.add_argument("--pages", help="1-based range, e.g. 4-6")
    args = parser.parse_args()

    pages = None
    if args.pages:
        first, _, last = args.pages.partition("-")
        pages = set(range(int(first), int(last or first) + 1))

    available = extract.catalogs(args.uploads)
    slugs = args.catalog or sorted(
        name for name in os.listdir(args.data)
        if os.path.isdir(os.path.join(args.data, name)))

    os.makedirs(args.out, exist_ok=True)
    counts = []
    for slug in slugs:
        path = os.path.join(args.data, slug, "document.json")
        if not os.path.exists(path) or slug not in available:
            continue
        with open(path) as handle:
            document = json.load(handle)
        written = write_catalog(slug, available[slug], document,
                                os.path.join(args.out, slug), pages)
        counts.append((slug, written))

    rows = "".join(
        f'<tr><td><a href="{slug}/index.html">{slug}</a></td><td>{n}</td></tr>'
        for slug, n in counts)
    body = ('<div class="idx"><h1>MBCI catalogs</h1>'
            '<p class="muted">The catalog pages as HTML, with the extracted '
            'text as real text.</p>'
            '<table><thead><tr><th>Catalog</th><th>Pages</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')
    with open(os.path.join(args.out, "index.html"), "w") as handle:
        handle.write(_document("MBCI catalogs", body + INDEX_STYLE))
    print(f"{sum(n for _, n in counts)} page(s) written to {args.out}")


if __name__ == "__main__":
    main()
