"""Apply the numeric column repairs to already-extracted data.

extract.py runs these repairs as it goes, so a fresh run needs nothing from
here.  This exists so an existing `data/` tree can be brought up to date
without re-OCRing all 233 pages: it re-reads only the cells the repairs
actually touch, which is a few hundred rather than ten thousand.

    python3 src/repair.py                 # every catalog under data/
    python3 src/repair.py --catalog architectural
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfplumber

import extract
import geometry
import ocr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Cell:
    """Enough of geometry.Cell for the repair functions to work on."""

    def __init__(self, bbox, text):
        self.bbox = bbox
        self.text = text


def _page_cells(pdf_path, page_index, page):
    """Line up each stored table row with the cell boxes it came from."""
    with pdfplumber.open(pdf_path) as pdf:
        tables = geometry.tables(pdf.pages[page_index])
    out = []
    for stored in page.get("tables", []):
        index = stored["index"]
        if index >= len(tables):
            continue
        edges, bands = tables[index].grid()
        boxes = [[cell.bbox for cell, _span in row] for _band, row in bands]
        rows = boxes[-len(stored["rows"]):] if stored["rows"] else []
        if len(rows) != len(stored["rows"]):
            continue
        out.append((stored, rows))
    return out


def repair_page(pdf_path, page_index, page):
    """Re-run the numeric repairs over one already-extracted page."""
    matched = _page_cells(pdf_path, page_index, page)
    if not matched:
        return 0
    image = ocr.render(pdf_path, page_index)
    repaired = 0
    for stored, rows in matched:
        columns = stored["columns"]
        cells = [[_Cell(box, value) for box, value in zip(boxes, values)]
                 for boxes, values in zip(rows, stored["rows"])]
        # _repair_numeric_columns expects (cell, span) pairs.
        as_rows = [[(cell, 1) for cell in row] for row in cells]
        repaired += extract._repair_numeric_columns(as_rows, columns, image)
        for row, values in zip(cells, stored["rows"]):
            for index, cell in enumerate(row):
                values[index] = cell.text
    return repaired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=os.path.join(ROOT, "data"))
    parser.add_argument("--uploads", default=os.environ.get(
        "CATALOG_DIR", "/root/.claude/uploads"))
    parser.add_argument("--catalog", action="append")
    args = parser.parse_args()

    available = extract.catalogs(args.uploads)
    catalogs = args.catalog or sorted(
        name for name in os.listdir(args.data)
        if os.path.isdir(os.path.join(args.data, name)))

    for slug in catalogs:
        out_dir = os.path.join(args.data, slug)
        with open(os.path.join(out_dir, "document.json")) as handle:
            document = json.load(handle)
        pdf_path = available[slug]
        repaired = 0
        for page in document["pages"]:
            count = repair_page(pdf_path, page["page"] - 1, page)
            if count:
                page["repaired_cells"] = page.get("repaired_cells", 0) + count
                repaired += count
                print(f"  {slug} p{page['page']}: {count} repaired", flush=True)
        with open(os.path.join(out_dir, "document.json"), "w") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
        rows = list(extract.tidy_rows(document["pages"]))
        extract.csv_write(os.path.join(out_dir, "tables.csv"), rows)
        print(f"{slug}: {repaired} cells repaired", flush=True)

    # Repairing one catalog must leave the others in the corpus-wide files.
    extract._write_corpus(args.data, [])


if __name__ == "__main__":
    main()
