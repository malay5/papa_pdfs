"""Extract the MBCI pricing catalogs into structured data.

Each page is read twice over: pdfplumber supplies the vector layout (which
rectangle is which table cell) and tesseract supplies the text, cell by cell.
See geometry.py and ocr.py for why each half works the way it does.

    python3 src/extract.py                 # every catalog
    python3 src/extract.py --catalog fasteners --pages 1-10
"""

import argparse
import collections
import csv
import json
import multiprocessing
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfplumber

import geometry
import ocr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

# The running header sits in the top strip of every page; anything lower is a
# section banner naming the product on that page.
PAGE_HEADER_MAX_TOP = 46.0
# Cells taller than a single line of type may hold a wrapped value.
MULTILINE_MIN_H = 21.0
# Share of a column's values that must look numeric before an odd one out is
# re-read under the digit-only whitelist.
NUMERIC_COLUMN_RATIO = 0.6
# A column headed like this holds numbers whatever its values came back as.
# Many tables carry only one or two rows -- a single closure strip, a single
# grommet -- which is too little for the ratio below to mean anything, and
# those are exactly the tables where a lone bad reading has no neighbours to
# be corrected against.
NUMERIC_HEADER = re.compile(
    r"weight|gauge|yield|psi|\blbs?\b|thickness|per_(each|sq|square|100|1000|piece|ft|foot)",
    re.I)
# Only short values are re-read as numbers.  A longer one -- '.024" Alum ††'
# in a column of plain gauges -- is mixed on purpose, and a digit-only retry
# would silently throw its words away.
NUMERIC_REPAIR_MAX_LEN = 6
# Values that must share a shape before a dissenter is re-read against it.
SHAPE_MIN_AGREEING = 3

NUMERIC_VALUE = re.compile(r'^\.?\d[\d,]*(\.\d+)?\s*[#"\']?$')
# How this typeface's digits come back when OCR misreads them.  Used only to
# judge whether a *column* is numeric -- never to rewrite a value, which is
# what the digit-only re-read is for.
CONFUSABLE = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "l": "1", "I": "1", "i": "1", "T": "1", "|": "1", "/": "1",
    "Z": "2", "A": "4", "S": "5", "s": "5", "G": "6", "b": "6", "B": "8",
    "t": "#", "H": "#", "%": "#", "f": "#",
})
# Prices are withheld in these public catalogs: an available product carries a
# checkmark, which OCR sees as a lone "v" or similar.
CHECKMARK_OCR = re.compile(r'^[vVyY√✓~+\'"·.,|/\\-]{1,2}$')
CHECKMARK = "✓"

EFFECTIVE_DATE = re.compile(r"EFFECTIVE\s+([A-Z][A-Za-z]+\s+\d{1,2},\s*\d{4})")


def catalogs(upload_dir):
    """Map a short slug to each catalog PDF in `upload_dir`."""
    slugs = {
        "ResidentialPricingGuideCatalog": "residential",
        "FastenerCatalog": "fasteners",
        "CommercialIndustriaPricingCatalog": "commercial-industrial",
        "ArchitecturalPricingCatalog": "architectural",
        "AgriculturalPricingCatalog": "agricultural",
        "SSRPricingGuideCatalog": "ssr",
    }
    found = {}
    for name in sorted(os.listdir(upload_dir)):
        if not name.lower().endswith(".pdf"):
            continue
        stem = name[:-4].split("-", 1)[-1]
        found[slugs.get(stem, stem.lower())] = os.path.join(upload_dir, name)
    return found


def _slug(text):
    text = re.sub(r"[^a-z0-9]+", "_", ocr.clean(text).lower()).strip("_")
    return text or "column"


def _split_header_cell(cell, edges, page_image):
    """Spread a header drawn as one wide band across the columns beneath it.

    Most of these catalogs give every header its own shaded cell.  Some draw
    the whole header strip as a single rectangle instead, which leaves the
    columns nameless unless the words are placed by where they sit.
    """
    names = [""] * (len(edges) - 1)
    for word in ocr.words(page_image, cell.bbox):
        middle = (word["x0"] + word["x1"]) / 2
        for index in range(len(edges) - 1):
            if edges[index] <= middle <= edges[index + 1]:
                names[index] = (names[index] + " " + word["text"]).strip()
                break
    return names


def _covered_columns(cell, edges):
    """The columns a header cell sits over, by geometry.

    A header band holds fewer cells than the table has columns whenever
    anything spans, so a cell's position in the band says nothing about which
    column it names.
    """
    return [index for index in range(len(edges) - 1)
            if cell.x0 - 2 <= (edges[index] + edges[index + 1]) / 2 <= cell.x1 + 2]


def _column_names(header_rows, column_count, edges, page_image):
    """Name each column from the header bands, de-duplicating collisions.

    Returns the names alongside the header cells that turned out to be a whole
    row of column names packed into one rectangle, so the group pass can leave
    those alone.
    """
    names = [""] * column_count
    packed = set()
    for row in header_rows:  # later bands are nearer the data, so they win
        for cell, span in row:
            if not cell.text:
                continue
            covered = _covered_columns(cell, edges)
            if span == 1:
                if covered and covered[0] < column_count:
                    names[covered[0]] = cell.text
                continue
            if span < 3 or page_image is None:
                continue
            # A wide header cell is either one label spanning several columns
            # ("DESCRIPTION") or the whole header strip drawn as a single
            # rectangle.  Splitting it by where the words sit tells them
            # apart: only the latter yields a name for more than one column.
            split = _split_header_cell(cell, edges, page_image)
            if sum(1 for value in split if value) < 2:
                continue
            packed.add(id(cell))
            for position, value in enumerate(split):
                if value and position < column_count:
                    names[position] = value
    used, out = {}, []
    for index, name in enumerate(names):
        base = _slug(name) if name else f"column_{index + 1}"
        used[base] = used.get(base, 0) + 1
        out.append(base if used[base] == 1 else f"{base}_{used[base]}")
    return out, names, packed


def _group_names(header_rows, column_count, edges, packed=()):
    """Spanning header labels ("DESCRIPTION") mapped onto each column."""
    groups = [""] * column_count
    for row in header_rows:
        for cell, span in row:
            if not cell.text or span < 2 or id(cell) in packed:
                continue
            for index in _covered_columns(cell, edges):
                if index < column_count:
                    groups[index] = cell.text
    return groups


def _is_header_band(band):
    """Header bands are the shaded strips leading a table, plus its span rule."""
    return band.is_span_header or band.dark


def _numeric_shaped(value):
    """True if `value` is a number, or a number this typeface could misread as.

    The gate below has to work on a column that OCR got *wrong*, so asking
    how many values are already numeric is circular: a column where most
    weights came back as words would never qualify for the repair that fixes
    exactly that.  Asking how many are numeric up to a known confusion does
    not have that hole.
    """
    return bool(NUMERIC_VALUE.match(value.translate(CONFUSABLE)))


def _value_shape(value):
    """Collapse a value to its pattern of digits, letters and punctuation."""
    out = []
    for char in value:
        out.append("9" if char.isdigit() else "a" if char.isalpha()
                   else " " if char.isspace() else char)
    return re.sub(r"9+", "9", re.sub(r"a+", "a", "".join(out)))


def _repair_column_shape(values, page_image):
    """Re-read values whose shape disagrees with the rest of their column.

    Catches the misreads that look like perfectly good numbers: '124#' comes
    back as '1244' when the '#' is read as a digit, which every value-level
    check accepts.  The column says otherwise -- ten weights ending in '#'
    and one ending in a digit -- so the odd one out is read again, and the
    new reading is taken only if it matches what the column expects.
    """
    if len(values) < SHAPE_MIN_AGREEING + 1:
        return 0
    counts = collections.Counter(_value_shape(text) for _, text in values)
    shape, agreeing = counts.most_common(1)[0]
    if agreeing < SHAPE_MIN_AGREEING or agreeing == len(values):
        return 0
    repaired = 0
    for cell, text in values:
        if _value_shape(text) == shape or len(text) > NUMERIC_REPAIR_MAX_LEN:
            continue
        fixed = _repair_to_shape(cell, text, shape, page_image)
        if fixed is not None:
            cell.text = fixed
            repaired += 1
    return repaired


# A comma followed by exactly two digits is never a thousands separator.
COMMA_DECIMAL = re.compile(r"\d+,\d\d\Z")


def _repair_to_shape(cell, text, shape, page_image):
    """A reading of `cell` matching the shape its column expects, or None."""
    retry = ocr.read_numeric_cell(page_image, cell.bbox)
    if retry and _value_shape(retry) == shape:
        return retry
    if shape != "9.9":
        return None
    # A decimal point misread as a comma, or dropped outright.  Re-reading
    # does not recover either: the point is a handful of pixels and the
    # recogniser has already made up its mind about them at every scale.  The
    # column is the evidence that a point belongs, and for the dropped one the
    # pixels say where it goes, so neither is a guess.
    if COMMA_DECIMAL.match(text):
        return text.replace(",", ".")
    if text.isdigit():
        spot = ocr.decimal_index(page_image, cell.bbox)
        if spot is not None and spot[1] == len(text):
            return text[:spot[0]] + "." + text[spot[0]:]
    return None


def _repair_numeric_columns(rows, columns, page_image):
    """Re-read stray values in columns that are otherwise all numbers.

    In this typeface "4" reads as "A" and "7" as "/" often enough that a
    handful of weights and gauges come back as words.  A column that is
    numeric everywhere else is strong evidence about what the outlier is.
    """
    repaired = 0
    for index, name in enumerate(columns):
        values = [(row[index][0], row[index][0].text) for row in rows
                  if index < len(row) and row[index][1] == 1 and row[index][0].text]
        if not values:
            continue
        if not NUMERIC_HEADER.search(name):
            if len(values) < 3:
                continue
            shaped = [text for _, text in values if _numeric_shaped(text)]
            if len(shaped) < NUMERIC_COLUMN_RATIO * len(values):
                continue
        for cell, text in values:
            if NUMERIC_VALUE.match(text) or len(text) > NUMERIC_REPAIR_MAX_LEN:
                continue
            retry = ocr.read_numeric_cell(page_image, cell.bbox)
            if retry and NUMERIC_VALUE.match(retry):
                cell.text = retry
                repaired += 1
        values = [(cell, cell.text) for cell, _ in values if cell.text]
        repaired += _repair_column_shape(values, page_image)
    return repaired


def _normalise_price(value, column_name, group_name):
    """Turn the price column's checkmark glyph back into a checkmark."""
    if "price" not in f"{column_name} {group_name}".lower():
        return value
    if value and CHECKMARK_OCR.match(value):
        return CHECKMARK
    return value


# Words whose vertical centres sit within this fraction of a row's height
# belong to the same row.
ROW_TOLERANCE = 0.6


def _stripe_columns(stripes, tolerance=2.0):
    """Column boundaries implied by a set of shaded column stripes."""
    edges = []
    for value in sorted(x for stripe in stripes for x in stripe[:2]):
        if edges and value - edges[-1] <= tolerance:
            continue
        edges.append(value)
    return edges


class _MatrixCell:
    """The bbox-and-text pair the repair passes expect, for matrix rows.

    A column-shaded matrix has no cell rectangles in the vector layout, so
    there is no geometry.Cell to carry; the bbox is computed from the stripe
    edges and the row pitch instead.
    """

    __slots__ = ("bbox", "text")

    def __init__(self, bbox):
        self.bbox = bbox
        self.text = ""


def _matrix_table(page_image, stripes, header_band, index):
    """Read a table that is shaded by column, with no per-row rules.

    The columns come from the stripes; the rows are recovered by grouping the
    words by where they sit, since nothing in the vector layout marks them.
    """
    edges = _stripe_columns(stripes)
    if len(edges) < 3:
        return None
    top, bottom = stripes[0][2], stripes[0][3]
    body = (edges[0], top, edges[-1], bottom)
    found = ocr.words(page_image, body)
    if not found:
        return None

    lines = []
    for word in sorted(found, key=lambda w: (w["top"], w["x0"])):
        height = word["bottom"] - word["top"]
        centre = (word["top"] + word["bottom"]) / 2
        for line in lines:
            if abs(line["centre"] - centre) <= ROW_TOLERANCE * height:
                line["words"].append(word)
                line["centre"] = sum((w["top"] + w["bottom"]) / 2
                                     for w in line["words"]) / len(line["words"])
                break
        else:
            lines.append({"centre": centre, "words": [word]})

    # The word pass is only used to find where the rows are; each cell is
    # then read through the same per-cell path as every other table, which is
    # both more accurate and handles the knockout-white row labels.
    # Word boxes put each row within a point or two, which is not close
    # enough: these rows are ~12pt tall, so a small error clips the glyphs and
    # the cell reads badly.  The rows are evenly spaced, though, so the pitch
    # of the ones found gives every row a band of the right size.
    centres = sorted(line["centre"] for line in lines)
    gaps = sorted(b - a for a, b in zip(centres, centres[1:]))
    pitch = gaps[len(gaps) // 2] if gaps else 0.0

    rows = []
    for line in lines:
        if pitch > 2:
            row_top = line["centre"] - pitch / 2
            row_bottom = line["centre"] + pitch / 2
        else:
            row_top = min(w["top"] for w in line["words"])
            row_bottom = max(w["bottom"] for w in line["words"])
        if row_bottom - row_top < 4:
            continue
        cells = [_MatrixCell((edges[position], row_top,
                              edges[position + 1], row_bottom))
                 for position in range(len(edges) - 1)]
        for cell in cells:
            cell.text = ocr.read_cell(page_image, cell.bbox).replace("\n", " ")
        if any(cell.text for cell in cells):
            rows.append(cells)
    if not rows:
        return None

    # These matrices are the one table shape with no header rules to hang a
    # numeric-column test on, but every column is a column of numbers, so the
    # shape repair that catches a dropped decimal point applies to all of them.
    repaired = 0
    for position in range(len(edges) - 1):
        values = [(row[position], row[position].text) for row in rows
                  if row[position].text]
        repaired += _repair_column_shape(values, page_image)

    names = [""] * (len(edges) - 1)
    if header_band is not None:
        names = _split_header_cell(header_band, edges, page_image)
    columns, used = [], {}
    for position, name in enumerate(names):
        base = _slug(name) if name else f"column_{position + 1}"
        used[base] = used.get(base, 0) + 1
        columns.append(base if used[base] == 1 else f"{base}_{used[base]}")
    return {
        "index": index, "section": "", "bbox": [round(v, 1) for v in body],
        "columns": columns, "column_labels": [ocr.clean(n) for n in names],
        "column_groups": [""] * len(columns),
        "rows": [[cell.text for cell in row] for row in rows],
        "repaired_cells": repaired,
    }


def extract_page(pdf_path, page_index):
    """Everything readable on one page: banners, tables, and prose."""
    image = ocr.render(pdf_path, page_index)
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        found_tables = geometry.tables(page)
        found_banners = geometry.banners(page)
        stripes = geometry.column_stripes(page)
        page_width = page.width

    # A table's own shaded column-header strip looks just like a section
    # banner; only the ones outside every table actually name a section.
    table_boxes = [t.bbox for t in found_tables]
    found_banners = [b for b in found_banners
                     if not any(box[1] - 1 <= b.top and b.bottom <= box[3] + 1
                                for box in table_boxes)]

    header, sections = [], []
    for banner in found_banners:
        text = ocr.read_block(image, banner.bbox)
        if not text:
            continue
        (header if banner.top < PAGE_HEADER_MAX_TOP else sections).append({
            "text": text, "bbox": [round(v, 1) for v in banner.bbox],
        })

    text = ocr.page_text(image)
    result = {
        "page": page_index + 1,
        "title": header[0]["text"] if header else "",
        "category": header[-1]["text"] if len(header) > 1 else "",
        "sections": [s["text"] for s in sections],
        "tables": [],
        "text": text,
        "repaired_cells": 0,
    }
    match = EFFECTIVE_DATE.search(text)
    result["effective_date"] = match.group(1) if match else ""

    # A column-shaded matrix leaves no row cells behind, so geometry.tables
    # never sees it; handle it before the ordinary tables.
    if stripes and not found_tables:
        header_band = None
        top = stripes[0][2]
        for banner in found_banners:
            if 0 < top - banner.top <= 30 and banner.x1 - banner.x0 > 0.5 * page_width:
                header_band = banner
        if header_band is not None:
            found_banners = [b for b in found_banners if b is not header_band]
            header = [h for h in header if h["text"] != ocr.read_block(image, header_band.bbox)]
        matrix = _matrix_table(image, stripes, header_band, 0)
        if matrix:
            matrix["section"] = sections[-1]["text"] if sections else ""
            result["repaired_cells"] += matrix.pop("repaired_cells", 0)
            result["tables"].append(matrix)

    for table_index, table in enumerate(found_tables):
        edges, bands = table.grid()
        column_count = len(edges) - 1
        for band, row in bands:
            for cell, _span in row:
                cell.text = ocr.read_cell(
                    image, cell.bbox,
                    multiline=(cell.bottom - cell.top) >= MULTILINE_MIN_H,
                )

        header_rows, data_rows = [], []
        in_header = True
        for index, (band, row) in enumerate(bands):
            if in_header and _is_header_band(band):
                header_rows.append(row)
            else:
                in_header = False
                data_rows.append(row)

        columns, labels, packed = _column_names(
            header_rows, column_count, edges, image)
        groups = _group_names(header_rows, column_count, edges, packed)
        result["repaired_cells"] += _repair_numeric_columns(
            data_rows, columns, image)

        # Which section banner this table sits under.
        section = ""
        for banner in sections:
            if banner["bbox"][1] < table.bbox[1]:
                section = banner["text"]

        rows = []
        for row in data_rows:
            values, index = [None] * column_count, 0
            for cell, span in row:
                if index < column_count:
                    values[index] = _normalise_price(
                        cell.text, columns[index], groups[index])
                    for offset in range(1, span):
                        if index + offset < column_count:
                            values[index + offset] = ""
                index += span
            if any(v for v in values):
                rows.append(["" if v is None else v for v in values])

        if not rows:
            continue
        result["tables"].append({
            "index": table_index,
            "section": section,
            "bbox": [round(v, 1) for v in table.bbox],
            "columns": columns,
            "column_labels": [ocr.clean(l) for l in labels],
            "column_groups": groups,
            "rows": rows,
        })
    return result


def _worker(job):
    slug, pdf_path, page_index = job
    try:
        page = extract_page(pdf_path, page_index)
        page["catalog"] = slug
        return page
    except Exception:
        return {"catalog": slug, "page": page_index + 1, "tables": [], "text": "",
                "error": traceback.format_exc(limit=3)}


def csv_write(path, rows):
    """Write tidy long-form records to `path`."""
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIDY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_catalog(slug, pages, out_dir, source=""):
    os.makedirs(out_dir, exist_ok=True)
    document = {
        "catalog": slug,
        "source": source,
        "page_count": len(pages),
        "effective_dates": sorted({p.get("effective_date") for p in pages
                                   if p.get("effective_date")}),
        "pages": pages,
    }
    with open(os.path.join(out_dir, "document.json"), "w") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)

    text_dir = os.path.join(out_dir, "text")
    os.makedirs(text_dir, exist_ok=True)
    for page in pages:
        path = os.path.join(text_dir, f"page_{page['page']:03d}.txt")
        with open(path, "w") as handle:
            handle.write(page.get("text", "") + "\n")

    rows = list(tidy_rows(pages))
    csv_write(os.path.join(out_dir, "tables.csv"), rows)
    return rows


TIDY_FIELDS = ["catalog", "page", "section", "table", "row",
               "column", "column_label", "column_group", "value"]


def _summarise(out_dir, slug):
    """Rebuild a catalog's summary entry from the document it already wrote."""
    with open(os.path.join(out_dir, slug, "document.json")) as handle:
        document = json.load(handle)
    pages = document["pages"]
    with open(os.path.join(out_dir, slug, "tables.csv"), newline="") as handle:
        values = sum(1 for _ in csv.DictReader(handle))
    return {
        "catalog": slug,
        "source": document.get("source", ""),
        "pages": len(pages),
        "tables": sum(len(p.get("tables", [])) for p in pages),
        "table_rows": sum(len(t["rows"]) for p in pages
                          for t in p.get("tables", [])),
        "values": values,
        "repaired_cells": sum(p.get("repaired_cells", 0) for p in pages),
        "pages_with_errors": [p["page"] for p in pages if p.get("error")],
        "effective_dates": document.get("effective_dates", []),
    }


def _write_corpus(out_dir, fresh):
    """Rebuild the corpus-wide files from every catalog on disk.

    Extracting one catalog must not drop the other five out of
    `all_tables.csv` and `summary.json`, so those are assembled from what is
    in `out_dir` rather than from what this run happened to produce.
    """
    summary_path = os.path.join(out_dir, "summary.json")
    kept = {}
    if os.path.exists(summary_path):
        with open(summary_path) as handle:
            kept = {entry["catalog"]: entry for entry in json.load(handle)}
    kept.update({entry["catalog"]: entry for entry in fresh})

    on_disk = sorted(
        name for name in os.listdir(out_dir)
        if os.path.exists(os.path.join(out_dir, name, "tables.csv")))
    summary = [kept[slug] if slug in kept else _summarise(out_dir, slug)
               for slug in on_disk]

    with open(os.path.join(out_dir, "all_tables.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIDY_FIELDS)
        writer.writeheader()
        for slug in on_disk:
            with open(os.path.join(out_dir, slug, "tables.csv"), newline="") as part:
                writer.writerows(csv.DictReader(part))

    with open(summary_path, "w") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
    return summary


def tidy_rows(pages):
    """Flatten every table cell into one long-form record."""
    for page in pages:
        for table in page.get("tables", []):
            for row_index, row in enumerate(table["rows"]):
                for column_index, value in enumerate(row):
                    if not value:
                        continue
                    yield {
                        "catalog": page["catalog"],
                        "page": page["page"],
                        "section": table["section"],
                        "table": table["index"],
                        "row": row_index,
                        "column": table["columns"][column_index],
                        "column_label": table["column_labels"][column_index],
                        "column_group": table["column_groups"][column_index],
                        "value": value.replace("\n", " "),
                    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uploads", default=os.environ.get(
        "CATALOG_DIR", "/root/.claude/uploads"))
    parser.add_argument("--out", default=DATA_DIR)
    parser.add_argument("--catalog", action="append",
                        help="slug to extract; repeatable, default all")
    parser.add_argument("--pages", help="1-based page range, e.g. 3-12")
    parser.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 1))
    args = parser.parse_args()

    available = catalogs(args.uploads)
    wanted = args.catalog or sorted(available)
    missing = [slug for slug in wanted if slug not in available]
    if missing:
        parser.error(f"unknown catalog(s): {', '.join(missing)}")

    jobs = []
    for slug in wanted:
        path = available[slug]
        total = ocr.page_count(path)
        if args.pages:
            first, _, last = args.pages.partition("-")
            start, end = int(first), int(last or first)
        else:
            start, end = 1, total
        jobs += [(slug, path, index) for index in range(start - 1, min(end, total))]

    print(f"{len(jobs)} page(s) across {len(wanted)} catalog(s), {args.jobs} worker(s)",
          flush=True)
    with multiprocessing.Pool(args.jobs) as pool:
        results = []
        for done, page in enumerate(pool.imap_unordered(_worker, jobs, chunksize=1), 1):
            results.append(page)
            print(f"  [{done}/{len(jobs)}] {page['catalog']} p{page['page']}"
                  f" tables={len(page.get('tables', []))}"
                  f"{' ERROR' if page.get('error') else ''}", flush=True)

    os.makedirs(args.out, exist_ok=True)
    summary = []
    for slug in wanted:
        pages = sorted((p for p in results if p["catalog"] == slug),
                       key=lambda p: p["page"])
        rows = _write_catalog(slug, pages, os.path.join(args.out, slug),
                              os.path.basename(available[slug]))
        summary.append({
            "catalog": slug,
            "source": os.path.basename(available[slug]),
            "pages": len(pages),
            "tables": sum(len(p.get("tables", [])) for p in pages),
            "table_rows": sum(len(t["rows"]) for p in pages
                              for t in p.get("tables", [])),
            "values": len(rows),
            "repaired_cells": sum(p.get("repaired_cells", 0) for p in pages),
            "pages_with_errors": [p["page"] for p in pages if p.get("error")],
            "effective_dates": sorted({p.get("effective_date") for p in pages
                                       if p.get("effective_date")}),
        })

    summary = _write_corpus(args.out, summary)

    for entry in summary:
        print(f"{entry['catalog']:>22}: {entry['pages']:>3} pages, "
              f"{entry['tables']:>4} tables, {entry['table_rows']:>5} rows, "
              f"{entry['values']:>6} values", flush=True)


if __name__ == "__main__":
    main()
