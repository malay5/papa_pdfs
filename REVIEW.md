# Items needing human review

Everything `qa.py` flags is resolved: 283 findings were opened, every flagged
value was read off a render, and **no flagged values remain**. What follows
is what `qa.py` does *not* catch, found by scanning the finished data for
patterns rather than by the checks built into it.

Nothing here is guesswork about whether it is wrong - each is a confirmed
defect. What is open is what the correct value is.

---

## 1. Fractions are flattened - 596 values

**The biggest remaining gap, and the only one that loses information.**

The catalogs set fractions as a single glyph (`⅞`, `¹⁵⁄₁₆`, `½`). Tesseract's
English model has no output symbol for most of them, so it emits the
numerator and a stray character and the denominator is lost:

| in the PDF | extracted as |
| --- | --- |
| `16 ⅞"` | `16 7%"` |
| `10 ⁵⁄₁₆"` | `10%6"` |
| `5 ⁷⁄₁₆"` | `5 7A6"` |
| `1½"` | `1%"` |

596 values across 170 distinct forms:

| catalog | values | worst columns |
| --- | ---: | --- |
| commercial-industrial | 270 | `girth` (113), `actual_size` (51), `normal_size` (26) |
| architectural | 146 | `girth` (88), `dim_a` (36) |
| ssr | 94 | `girth` (40) |
| agricultural | 49 | `girth` (39) |
| residential | 36 | `girth` (32) |
| fasteners | 1 | |

**Why I did not fix these.** `1%"` is ambiguous from the text alone: it could
be `1½"`, or it could be `½"` with the `1` being the numerator. The
denominator is simply not in the output, and inferring it would be inventing
data. They are perfectly legible in the render.

**Proposed remedy.** The same crop-and-read pass used on the flagged values -
596 cells is about 17 contact sheets. Say the word and I will do it. Note
these are dimensions, not prices, so their impact depends on what the data is
for.

---

## 2. Footnote glyphs - 166 values

Same cause, lower stakes, because the surrounding word survives:

| in the PDF | extracted as | where |
| --- | --- | --- |
| `Galvalume® Plus ¤` | `Galvalume® Plus &` | `finish` (73), `ssr_system` (63) |
| `.024" Alum ††` | `.024" Alum tt` | `gauge` (9) |

The `¤` and `††` are footnote markers pointing at conditions printed at the
foot of the page (available finishes, perforated-only stock). The product
name itself is intact, so a lookup on `finish` still works; only the marker
is wrong.

**Remedy.** A substitution table would fix all 166 mechanically. I did not
apply one because `&` is a legitimate character and I would rather not have a
blanket rule rewriting it. Worth doing if the footnote markers matter to you.

---

## 3. Individual misreads found by scanning - 8 values

Not flagged by `qa.py` because each is inside a value long enough to look
plausible. All confirmed wrong; correct values need a look at the page.

| catalog | page | column | extracted | almost certainly |
| --- | ---: | --- | --- | --- |
| agricultural | 16 | `length` | `50O'-0"` | `50'-0"` |
| architectural | 18 | `weight_each` | `27. A0#` | `27.40#` |
| architectural | 37 | `length` | `1Q0'-2"` | `10'-2"` |
| architectural | 62 | `part` | `HW-1 200A` | `HW-1200A` |
| commercial-industrial | 24 | `weight_each` | `27. A0#` | `27.40#` |
| ssr | 57 | `girth` | `?| mr` | unreadable as extracted |
| ssr | 65 | `part` | `FL-31\|` | `FL-31I` |
| residential | 9 | `column_1` | `-1O00°F to +437°F` | `-100°F to +437°F` |

I have not applied these: the "almost certainly" column is inference from
shape, and every other correction in this repo was read off the render. They
are listed so you can decide whether inference is good enough, or ask me to
read them.

---

## 4. Fixed since this list was written

Building the HTML view (`html/`) put every page in front of the eye at once,
which surfaced a class the checks had no way to catch: the row labels down
the side of the SSR conversion matrices. `20 FT.` had come back as `90 FT`,
`25 FT.` as `95 ET`, `35 FT.` as `45 FT`. Nothing flagged them - the label
column is not numeric, so the column tests do not apply, and each value is a
plausible string on its own.

All ten were read off the render and corrected. They are listed here because
they say something about the checks rather than about the data: a column of
labels has no shape for `qa.py` to test, so **anything wrong in a label
column is invisible to it**. The same is true of any non-numeric column in
any catalog.

---

## 5. Structural findings - 25

Not wrong values; tables whose shape did not come through cleanly.

### Headers that did not read (14)

The columns are named `column_1`, `column_2`, ... and the values under them
are fine. Affected pages:

| catalog | pages |
| --- | --- |
| commercial-industrial | 35 (×2), 39, 44, 51 (×2), 52 (×2) |
| architectural | 20, 37 |
| ssr | 47, 48 |
| agricultural | 10 |
| residential | 7 |

### Columns that came out empty (11)

| catalog | page | column |
| --- | ---: | --- |
| commercial-industrial | 21 | `price` |
| commercial-industrial | 22 | `price` |
| commercial-industrial | 26 | `column_2` |
| commercial-industrial | 35 | `column_2` |
| commercial-industrial | 41 | `weight_each` |
| ssr | 7 | `column_1`, `column_15` |
| ssr | 39 | `signature_300_metallic`, `column_10` |
| ssr | 49 | `column_8`, `column_13` |

The two SSR page 7 columns are a page-wide background rectangle picked up as
a table column; harmless. The `price` columns on commercial-industrial 21-22
are worth a look - an empty price column may mean the checkmarks did not
read, which would be a real omission rather than a cosmetic one.

### Spanning headers that did not read

Not counted above, because `qa.py` does not check for them. A table's group
header (`PRICED PER EACH`, above the availability columns) is sometimes
blank in `column_groups` where the catalog prints one - SSR page 65 is an
example. The column names underneath are correct; only the label spanning
them is missing. Visible in the HTML pages as an empty strip above the right
half of a table.

---

## 6. Things to know about the data as a whole

**The corrections layer is not reproducible by the pipeline.** A fresh
extraction still produces `2.564`; `data/corrections.json` is what makes it
`2.56#`. `extract.py` and `repair.py` both re-apply it at the end of a run.
If you change the OCR path and a cell then reads differently, the correction
for it is **reported, not applied** - check that output rather than assuming
silence means success.

**`commercial-industrial` carries the most residual risk.** It is the largest
catalog (4,082 values), had the highest flag rate before review (4.09%), and
holds 270 of the 596 flattened fractions.

**Prices are absent from the source.** These are the public editions; an
available product carries a checkmark. If you expected figures, the problem
is the source PDFs, not the extraction.

**The `#` suffix means pounds.** `70#` is 70 lb, not a part number. Values
that genuinely are part numbers start with `#` (`#17C`).

---

## How these numbers were produced

```bash
python3 src/qa.py                      # the built-in checks
python3 src/corrections.py             # re-apply the reviewed readings
```

The pattern scans behind sections 1-3 were one-off greps over
`data/all_tables.csv`, not part of the tooling. If they should become
standing checks in `qa.py`, that is a small change and worth making.
