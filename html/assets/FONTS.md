# Fonts

Both faces are bundled so the pages render the same everywhere and need no
network. Both are licensed under the SIL Open Font License 1.1, which permits
redistribution alongside this repository.

| file | family | used for | copyright |
| --- | --- | --- | --- |
| `inter-latin.woff2` | Inter (variable, latin subset) | tables and body | The Inter Project Authors |
| `montserrat-latin.woff2` | Montserrat (variable, latin subset) | banners and headings | The Montserrat Project Authors |

Full licence: https://openfontlicense.org

## Why these two

The catalogs set everything in a geometric sans. That face carries the look
in a banner, where the type is large, but geometric sans are a poor choice at
the 8pt the tables are set in -- the round, wide letterforms and small
x-height are exactly what stops working when the type gets small.

So the two jobs are split. Banners keep a geometric face (Montserrat) because
that is where the catalog's character lives and legibility is not at risk.
Tables use Inter, which was drawn for small sizes on screen and has proper
tabular figures, so a column of prices lines up digit for digit.

Neither is the catalog's actual typeface. The PDFs embed no fonts at all --
that is why this project needs OCR in the first place -- so there is nothing
in the source to reuse.
