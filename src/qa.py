"""Quality checks over the extracted data.

OCR failures in this corpus are not random noise -- they are a short list of
recognisable shapes (a digit read as the letter it resembles, an empty cell, a
column whose values do not agree with its neighbours).  This flags those so a
reviewer can go straight to the pages worth re-reading.

    python3 src/qa.py                   # report over data/
    python3 src/qa.py --catalog fasteners --limit 40
"""

import argparse
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

# A value that mixes letters into an otherwise numeric shape is the signature
# of this typeface's digit/letter confusions.
SUSPECT_MIXED = re.compile(r'(?<=\d)[A-Za-z]{1,2}$|^[A-Za-z]{1,2}(?=\d)')
# Stray punctuation where a digit belongs: "8/#" for "87#", "/0#" for "70#".
# A slash between two digits is left alone -- these catalogs are full of
# genuine fractions like 5/16" and 40-7/8".
SUSPECT_PUNCT = re.compile(r'\d[/\\|]\s*#|^[/\\|]\d|\d[\\|]\d')
NUMERIC_VALUE = re.compile(r'^\.?\d[\d,]*(\.\d+)?\s*[#"\']?$')


def load(catalog_dir):
    with open(os.path.join(catalog_dir, "document.json")) as handle:
        return json.load(handle)


def issues(document):
    """Every value worth a second look, with where to find it."""
    for page in document["pages"]:
        if page.get("error"):
            yield {"kind": "page-error", "page": page["page"],
                   "detail": page["error"].strip().splitlines()[-1]}
        for table in page.get("tables", []):
            columns = table["columns"]
            for row_index, row in enumerate(table["rows"]):
                for index, value in enumerate(row):
                    kind = _classify(value)
                    if kind:
                        yield {"kind": kind, "page": page["page"],
                               "table": table["index"], "row": row_index,
                               "column": columns[index] if index < len(columns) else "?",
                               "value": value}
            for kind, detail in _table_issues(table):
                yield {"kind": kind, "page": page["page"],
                       "table": table["index"], "detail": detail}


def _classify(value):
    if not value:
        return None
    if SUSPECT_PUNCT.search(value):
        return "stray-punctuation"
    if len(value) <= 6 and SUSPECT_MIXED.search(value):
        return "digit-letter-mix"
    return None


def _table_issues(table):
    """Table-level smells: no header, or a column that is entirely empty."""
    if not any(name and not name.startswith("column_") for name in table["columns"]):
        yield "unnamed-columns", f"{len(table['columns'])} columns, no header read"
    for index, name in enumerate(table["columns"]):
        values = [row[index] for row in table["rows"] if index < len(row)]
        if values and not any(values):
            yield "empty-column", name


def report(data_dir, catalogs, limit):
    total = collections.Counter()
    for slug in catalogs:
        path = os.path.join(data_dir, slug)
        if not os.path.isdir(path):
            continue
        document = load(path)
        found = list(issues(document))
        cells = sum(len(r) for p in document["pages"]
                    for t in p.get("tables", []) for r in t["rows"])
        values = sum(1 for p in document["pages"] for t in p.get("tables", [])
                     for r in t["rows"] for v in r if v)
        counts = collections.Counter(entry["kind"] for entry in found)
        total.update(counts)
        rate = 100.0 * len(found) / values if values else 0.0
        print(f"\n{slug}: {document['page_count']} pages, {values} values "
              f"({cells} cells), {len(found)} flagged ({rate:.2f}%)")
        for kind, count in counts.most_common():
            print(f"    {kind:<20} {count}")
        for entry in found[:limit]:
            where = f"p{entry['page']}"
            if "row" in entry:
                where += f" t{entry['table']} r{entry['row']} {entry['column']}"
            print(f"      {entry['kind']:<20} {where:<28} "
                  f"{entry.get('value', entry.get('detail', ''))!r}")
    print("\ntotal:", dict(total.most_common()))
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=DATA_DIR)
    parser.add_argument("--catalog", action="append")
    parser.add_argument("--limit", type=int, default=15,
                        help="examples to print per catalog")
    args = parser.parse_args()
    catalogs = args.catalog or sorted(
        name for name in os.listdir(args.data)
        if os.path.isdir(os.path.join(args.data, name)))
    report(args.data, catalogs, args.limit)


if __name__ == "__main__":
    main()
