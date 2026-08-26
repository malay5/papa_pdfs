# Source catalogs

The six MBCI pricing catalogs these extractions come from, kept in the repo so
a run reproduces from a clean clone with no external files:

| file | slug | pages |
| --- | --- | ---: |
| `AgriculturalPricingCatalog.pdf` | `agricultural` | 44 |
| `ArchitecturalPricingCatalog.pdf` | `architectural` | 65 |
| `CommercialIndustriaPricingCatalog.pdf` | `commercial-industrial` | 73 |
| `FastenerCatalog.pdf` | `fasteners` | 25 |
| `ResidentialPricingGuideCatalog.pdf` | `residential` | 26 |
| `SSRPricingGuideCatalog.pdf` | `ssr` | 70 |

93 MB in total, largest file 20 MB. The spelling of
`CommercialIndustria...` is as supplied.

These are the **public editions**, in which prices are withheld: an available
product carries a checkmark rather than a figure. No dollar amounts appear
anywhere in the source, so none appear in the extraction.

`extract.py` reads this directory by default. `--uploads` points it somewhere
else; the slug comes from the filename, and a leading `<hash>-` is ignored, so
`428f34ec-ResidentialPricingGuideCatalog.pdf` also resolves to `residential`.

## Why these need OCR

None of the six embeds a font. Every glyph is a filled vector path, so the
text layer is empty:

```
$ pdffonts CommercialIndustriaPricingCatalog.pdf
name    type    encoding    emb sub uni object ID
------- ------- ----------- --- --- --- ---------
                                          (nothing)
```

`pdftotext`, `pdfplumber.extract_text()` and every other text-layer reader
return an empty string for all 303 pages. See the root `README.md` for what
the extractor does instead.
