"""Apply the hand-reviewed corrections in data/corrections.json.

A handful of cells defeat OCR no matter how they are read: a '#' that comes
back as a '4' at every scale, and the fraction glyphs ('1/2', '15/16') that
tesseract's English model has no output symbol for at all.  Those were read
by eye off a render of each flagged cell and recorded in data/corrections.json.

This applies them.  extract.py runs it at the end of a run, so the correction
survives a re-extraction; run it directly to re-apply after editing the file:

    python3 src/corrections.py
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(data_dir):
    path = os.path.join(data_dir, "corrections.json")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return json.load(handle)["corrections"]


def apply(data_dir, catalogs=None, verbose=False):
    """Returns (applied, stale) -- stale being corrections that no longer match.

    A correction is keyed by the value it replaces as well as by where it sits.
    If a later extraction reads that cell differently the correction no longer
    describes it, so it is reported rather than applied: the alternative is
    overwriting a fresh reading with a stale one and never noticing.
    """
    wanted = set(catalogs) if catalogs else None
    by_catalog = {}
    for entry in load(data_dir):
        if wanted and entry["catalog"] not in wanted:
            continue
        by_catalog.setdefault(entry["catalog"], []).append(entry)

    applied = stale = already = 0
    for slug, entries in sorted(by_catalog.items()):
        out_dir = os.path.join(data_dir, slug)
        document_path = os.path.join(out_dir, "document.json")
        if not os.path.exists(document_path):
            continue
        with open(document_path) as handle:
            document = json.load(handle)
        pages = {page["page"]: page for page in document["pages"]}

        touched = 0
        for entry in entries:
            page = pages.get(entry["page"])
            table = next((t for t in (page or {}).get("tables", [])
                          if t["index"] == entry["table"]), None)
            if table is None or entry["row"] >= len(table["rows"]):
                stale += 1
                continue
            if entry["column"] not in table["columns"]:
                stale += 1
                continue
            column = table["columns"].index(entry["column"])
            row = table["rows"][entry["row"]]
            if column >= len(row):
                stale += 1
                continue
            if row[column] == entry["to"]:
                already += 1                    # applied by an earlier run
                continue
            if row[column] != entry["from"]:
                stale += 1
                if verbose:
                    print(f"  stale {slug} p{entry['page']} {entry['column']}:"
                          f" expected {entry['from']!r},"
                          f" found {row[column]!r}")
                continue
            row[column] = entry["to"]
            touched += 1

        if touched:
            with open(document_path, "w") as handle:
                json.dump(document, handle, indent=2, ensure_ascii=False)
            extract.csv_write(os.path.join(out_dir, "tables.csv"),
                              list(extract.tidy_rows(document["pages"])))
        applied += touched
        if touched or verbose:
            print(f"{slug}: {touched} corrections applied", flush=True)

    if applied:
        extract._write_corpus(data_dir, [])
    return applied, stale, already


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=os.path.join(ROOT, "data"))
    parser.add_argument("--catalog", action="append")
    args = parser.parse_args()
    applied, stale, already = apply(args.data, args.catalog, verbose=True)
    print(f"{applied} applied, {already} already in place, "
          f"{stale} no longer matching")


if __name__ == "__main__":
    main()
