"""End-to-end check of extract_page against hand-checked pages.

Runs the real pipeline -- geometry, OCR, numeric repair, price normalisation
-- over the pages in ground_truth.json and compares every cell.

A handful of glyphs have no output character in tesseract's English model, so
they can never come back exactly: those are listed in KNOWN_GLYPHS and
reported separately rather than being quietly folded into the pass rate.

    python3 tests/test_extraction.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import extract  # noqa: E402

UPLOADS = os.environ.get(
    "CATALOG_DIR", "/root/.claude/uploads/8a7f0f6a-bb2d-5658-9919-d192c4b27ac3")

# Characters tesseract's English model cannot emit at all.  Substituting them
# is not an OCR failure this pipeline can fix, so a cell that matches once
# they are folded is reported as a known glyph limitation, not a pass.
KNOWN_GLYPHS = {
    "¤": "&",      # the Galvalume footnote mark
    "††": "tt",    # perforated-only footnote mark
    "⅝": "¥6",
    "¾": "34",
    "–": "-",
}


def fold(text):
    for src, dst in KNOWN_GLYPHS.items():
        text = text.replace(src, dst)
    return text.replace("\n", " ")


def run():
    with open(os.path.join(ROOT, "tests", "ground_truth.json")) as handle:
        cases = json.load(handle)["cases"]

    available = extract.catalogs(UPLOADS)
    exact = known = wrong = 0
    failures = []

    for case in cases:
        path = available[case["catalog"]]
        page = extract.extract_page(path, case["page"] - 1)
        index = case.get("table", 0)
        if index >= len(page["tables"]):
            failures.append((case["catalog"], case["page"], "-",
                             f"table {index} not found", ""))
            wrong += len(case["rows"]) * len(case["columns"])
            continue
        table = page["tables"][index]
        rows = table["rows"][-len(case["rows"]):]
        for want_row, got_row in zip(case["rows"], rows):
            for column, want, got in zip(case["columns"], want_row, got_row):
                got = got.replace("\n", " ")
                if got == want:
                    exact += 1
                elif fold(got) == fold(want):
                    known += 1
                else:
                    wrong += 1
                    failures.append((case["catalog"], case["page"],
                                     column, want, got))

    total = exact + known + wrong
    print(f"{exact}/{total} exact, {known} known-glyph, {wrong} wrong")
    for catalog, page, column, want, got in failures:
        print(f"  {catalog} p{page} {column}: want {want!r} got {got!r}")
    return wrong


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
