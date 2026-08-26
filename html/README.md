# The catalogs as HTML

One HTML file per catalog page, laid out from the PDF's own geometry.

```
index.html                 all six catalogs
assets/                    the shared stylesheet and its two fonts
<catalog>/index.html       that catalog's pages
<catalog>/page_###.html    one page
<catalog>/img/p###_NN.png  the drawings on that page
```

Open `index.html` in a browser. Nothing is fetched from the network -- the
fonts are bundled -- so the tree works from a local clone or off a static
host.

## What is faithful, and what is not

**Position and colour come from the PDF.** Fills, rules and cell boxes are
read back out of the vector layer, so the page keeps the catalog's real
proportions and its actual colours -- the panel pages' orange is the orange
in the file, not a guess at it.

**The tabular text is real text.** It is what `extract.py` read, with the
reviewed corrections applied, positioned in the cell it came from. So unlike
the source PDFs -- which have no text layer at all -- these pages can be
searched with ctrl-F, selected and copied.

**The drawings are pictures.** Profile diagrams, the logo and the footnote
type are vector art in the same layer as the glyph outlines, so nothing in
the file distinguishes a drawing from a letter. They are lifted as 150 DPI
crops of the regions no table or banner accounts for, quantised to a small
palette since they are line art. That keeps the page looking right; it does
mean the footnote text in those crops is not searchable.

**Type is not the catalog's typeface, and is chosen to read well.** The PDFs
embed no fonts, so there is nothing to reuse. Two faces are bundled in
`assets/` -- Montserrat for the banners, where the catalog's character lives
and the type is large, and Inter for the tables, which was drawn for small
sizes on screen and carries tabular figures so a column of prices lines up
digit for digit. See `assets/FONTS.md`.

Size is measured rather than guessed: a 13pt data band in these catalogs
carries about 9pt of type, and one size is settled for the whole table rather
than taken from each cell -- bands vary by a point or two, and the last row
of a table is usually the tallest, which made it come out visibly larger than
the rows above. Line breaks inside a long value may still fall differently
from the catalog.

**There is a zoom control** in the bar at the top of each page, since the
tables are dense. The setting is remembered per browser.

## Known gaps

Anything wrong in `data/` is wrong here too, and shows up more plainly --
see `../REVIEW.md`. The fractions are the ones you will notice: `16 7/8"`
reads `16 7%"`, because OCR dropped the denominator.

One thing is missing rather than wrong: a spanning header whose text did not
read leaves that strip blank, where the catalog prints e.g. `PRICED PER
EACH`.

## Regenerating

```bash
python3 src/html_pages.py                        # all six catalogs
python3 src/html_pages.py --catalog ssr --pages 4-6
```

Roughly 1.6 s a page. It reads `data/*/document.json` and `pdfs/`, and
writes here.
