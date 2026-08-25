# MBCI pricing catalog extraction

Extracts the product tables and page text out of five MBCI pricing catalogs
into JSON and CSV.

## Why this is not a text-extraction job

The five PDFs contain **no embedded fonts**. Every glyph is a filled vector
path, so `pdftotext`, `pdfplumber.extract_text()` and every other text-layer
reader return an empty string for all 233 pages:

```
$ pdffonts CommercialIndustriaPricingCatalog.pdf
name    type    encoding    emb sub uni object ID
------- ------- ----------- --- --- --- ---------
                                          (nothing)
```

The text therefore has to be read back off a render. The *layout*, though, is
fully intact: each table cell is still a filled rectangle and each rule a
stroked line. The extractor uses both halves:

| half | source | what it gives |
| --- | --- | --- |
| layout | pdfplumber vector primitives | which rectangle is which cell, and the column grid |
| text | tesseract, one crop per cell | the value inside each cell |

Reading a page in a single OCR pass smears values across column boundaries and
mangles short numeric cells; cropping each cell first keeps every value tied to
its column.

## Two details that carry most of the accuracy

**Ink-height normalisation.** At 600 DPI a table row is roughly 90px of cap
height, about triple what tesseract's line recogniser expects. Oversized thin
strokes are what turn `41#` into `Alt` and `87#` into `8/#`. Each crop is
tightened to its glyphs and rescaled to a 32px ink height before OCR, and short
cells are read at several heights and voted on, because which height reads a
given glyph correctly is not predictable.

**Column-aware repair.** In this typeface `4` resembles `A` and `7` resembles
`/`. A column whose other values are all numbers is strong evidence about what
an outlier is, so short non-numeric values in an otherwise-numeric column are
re-read under a digit-only whitelist. Longer mixed values such as
`.024" Alum ††` are left alone, so the repair cannot throw away real words.

## Running it

```bash
./setup.sh                                   # tesseract + Python deps
python3 src/extract.py --uploads /path/to/pdfs
python3 src/extract.py --catalog fasteners --pages 1-10   # a subset
```

`--jobs` sets worker processes (default: CPU count). A full run is roughly
half an hour on four cores.

## Output

```
data/
  summary.json                 per-catalog counts and effective dates
  all_tables.csv               every value from every catalog, long form
  <catalog>/
    document.json              full structure: pages, banners, tables, text
    tables.csv                 that catalog's values, long form
    text/page_###.txt          page prose - notes, footnotes, body copy
```

`document.json` keeps each table in its native shape:

```json
{
  "section": "PBR / PBU Panels",
  "columns": ["gauge", "coverage", "yield_psi", "weight_per_sq", "finish", "price"],
  "column_labels": ["GAUGE", "COVERAGE", "YIELD (PSI)", "WEIGHT PER SQ.", "FINISH*", "PRICE"],
  "column_groups": ["DESCRIPTION", "DESCRIPTION", "DESCRIPTION", "DESCRIPTION", "DESCRIPTION", ""],
  "rows": [["29", "36\"", "80,000", "70#", "Galvalume® Plus ¤", "✓"]]
}
```

The CSVs are long form - one row per cell, with `catalog, page, section,
table, row, column, column_label, column_group, value` - because the tables
have no common set of columns. Pivot a single table back to a grid with:

```python
import pandas as pd
df = pd.read_csv("data/all_tables.csv")
one = df[(df.catalog == "commercial-industrial") & (df.page == 4)]
one.pivot_table(index="row", columns="column", values="value", aggfunc="first")
```

### A note on prices

These are the public editions of the catalogs, in which **prices are
withheld**. The `PRICE` column holds a checkmark meaning the product is
available on request, or the words `Please Inquire`; no dollar figures appear
anywhere in the source. Extracted checkmarks are normalised to `✓`.

## Layout

```
src/geometry.py   vector primitives -> table grid
src/ocr.py        render, crop, normalise, OCR
src/extract.py    orchestration, CLI, JSON/CSV output
```

## Accuracy, and what is still wrong

Against `tests/ground_truth.json` -- 118 values read by eye off three pages --
the pipeline gets **107 exact, 11 known-glyph, 0 wrong**.

"Known-glyph" means a character tesseract's English model has no output
symbol for, so it can never come back exactly. These are reported separately
rather than folded into the pass rate:

| in the PDF | extracted as |
| --- | --- |
| `¤` (Galvalume footnote) | `&` |
| `††` (perforated-only footnote) | `tt` |
| `⅝` | `¥6` |
| `¾` | `34` |

Across the full corpus `qa.py` flags about 2% of values for review. Some of
those are false positives -- a column of dimensions legitimately containing
one different number -- but some are real and unfixed. The stubborn ones are
weights where the `#` reads as a `4` (`134#` as `1344`) at every scale tried,
and they are the reason `qa.py` exists: they look like perfectly good numbers,
so nothing but the column around them says otherwise.

**If you are going to rely on a specific number, check it against the PDF.**
The `page`, `section` and `table` columns in the CSVs are there to make that
quick.

## Checking the output

```bash
python3 src/qa.py                        # flag values worth re-reading
python3 src/qa.py --catalog fasteners --limit 40
```

`repair.py` re-applies the numeric repairs to an existing `data/` tree,
re-reading only the cells they touch instead of all 233 pages:

```bash
python3 src/repair.py --catalog architectural
```

A fresh `extract.py` run needs nothing from it -- the repairs already run
inline.

OCR failures here are not random noise but a short list of recognisable
shapes, so `qa.py` looks for those specifically: digits read as the letters
they resemble, stray punctuation inside a number, tables whose header did not
read, and columns that came out empty.
