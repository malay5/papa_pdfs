# Extracted data

Output of `src/extract.py` over the six catalogs in `../pdfs`. 303 pages,
15,883 values.

```
summary.json                 per-catalog counts and effective dates
all_tables.csv               every value from every catalog, long form
corrections.json             hand-reviewed readings (see below)
<catalog>/
  document.json              full structure: pages, banners, tables, text
  tables.csv                 that catalog's values, long form
  text/page_###.txt          page prose - notes, footnotes, body copy
```

## The CSVs

Long form, one row per cell, because the tables share no common set of
columns:

| field | meaning |
| --- | --- |
| `catalog` | slug (`ssr`, `fasteners`, ...) |
| `page` | 1-based page number in the source PDF |
| `section` | the section banner the table sits under |
| `table` | index of the table on that page |
| `row` | 0-based row within the table |
| `column` | slugified column name, or `column_N` if the header did not read |
| `column_label` | the header as printed |
| `column_group` | spanning header above it (`DESCRIPTION`), if any |
| `value` | the cell |

Pivot one table back to a grid with:

```python
import pandas as pd
df = pd.read_csv("data/all_tables.csv")
one = df[(df.catalog == "commercial-industrial") & (df.page == 4)]
one.pivot_table(index="row", columns="column", values="value", aggfunc="first")
```

## document.json

Keeps each table in its native shape, plus the page's banners and prose:

```json
{
  "section": "PBR / PBU Panels",
  "columns": ["gauge", "coverage", "yield_psi", "weight_per_sq", "finish", "price"],
  "column_labels": ["GAUGE", "COVERAGE", "YIELD (PSI)", "WEIGHT PER SQ.", "FINISH*", "PRICE"],
  "column_groups": ["DESCRIPTION", "DESCRIPTION", "DESCRIPTION", "DESCRIPTION", "DESCRIPTION", ""],
  "rows": [["29", "36\"", "80,000", "70#", "Galvalume® Plus ¤", "✓"]]
}
```

## corrections.json

169 values that OCR could not resolve, read by eye off a 300 DPI render of
each cell, plus 90 it flagged that were checked and found correct. Applied by
`src/corrections.py`, which `extract.py` runs at the end of a run so a
re-extraction does not lose them.

Each correction records the value it replaces as well as where it sits. If a
later extraction reads that cell differently the correction is **reported,
not applied** - overwriting a fresh reading with a stale one would be silent
and wrong.

This is a human-review layer, not something the pipeline regenerates: a fresh
extraction still produces `2.564`, and the correction is what makes it
`2.56#`.

## Before you rely on a number

Check it against the PDF. `page`, `section` and `table` are in the CSVs to
make that quick. `../REVIEW.md` lists what is known to still be wrong.
