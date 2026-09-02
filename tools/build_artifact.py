"""Build the single-file catalog viewer published as an artifact.

An artifact is one self-contained HTML file with a hard size ceiling and no
external images, so the pages cannot be linked the way html/ links them:
every drawing has to travel inside the file.  That is what limits this to one
catalog.  Everything else is the same geometry html_pages.py lays out, kept
as data and drawn by a small renderer rather than written out as 70 pages of
markup.
"""

import base64, hashlib, io, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pdfplumber
from PIL import Image

import html_pages as hp
import ocr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "ssr"
PDF = os.path.join(ROOT, "pdfs", "SSRPricingGuideCatalog.pdf")
OUT = os.path.join(ROOT, "artifact", "catalog.html")


def build():
    with open(os.path.join(ROOT, "data", SLUG, "document.json")) as handle:
        document = json.load(handle)

    palette, palette_index = [], {}

    def colour(rgb):
        key = "%d,%d,%d" % rgb
        if key not in palette_index:
            palette_index[key] = len(palette)
            palette.append(key)
        return palette_index[key]

    images, image_index = [], {}
    pages = []
    pdf = pdfplumber.open(PDF)
    for stored in document["pages"]:
        number = stored["page"]
        model = hp._page_html(pdf.pages[number - 1], PDF, number - 1, stored,
                              as_model=True)
        art = []
        if model["regions"]:
            render = ocr.render(PDF, number - 1, dpi=hp.ART_DPI)
            scale = hp.ART_DPI / 72.0
            for x0, top, x1, bottom in model["regions"]:
                crop = render.crop((int(x0 * scale), int(top * scale),
                                    int(x1 * scale), int(bottom * scale)))
                if crop.width < 2 or crop.height < 2:
                    continue
                buf = io.BytesIO()
                crop.convert("RGB").save(buf, "WEBP", lossless=True, method=6)
                raw = buf.getvalue()
                key = hashlib.sha1(raw).hexdigest()
                if key not in image_index:
                    image_index[key] = len(images)
                    images.append(base64.b64encode(raw).decode())
                art.append([round(x0, 1), round(top, 1), round(x1 - x0, 1),
                            round(bottom - top, 1), image_index[key]])
        pdf.pages[number - 1].close()

        pages.append({
            "n": number,
            "t": stored.get("title", ""),
            "c": stored.get("category", ""),
            "s": stored.get("sections", []),
            "e": stored.get("effective_date", ""),
            "w": model["w"], "h": model["h"],
            "f": [[round(x, 1), round(y, 1), round(w, 1), round(h, 1), colour(c)]
                  for x, y, w, h, c in model["fills"]],
            "r": [[round(x, 1), round(y, 1), round(w, 1), round(h, 1), colour(c)]
                  for x, y, w, h, c in model["rules"]],
            "a": art,
            "x": [[r["x"], r["y"], r["w"], r["h"], r["s"], r["b"], r["k"],
                   r["d"], 1 if r["a"] == "left" else 0, r["t"]]
                  for r in model["runs"]],
        })
        print(f"  page {number}: {len(model['runs'])} runs, {len(art)} drawings",
              flush=True)
    pdf.close()

    data = {"slug": SLUG, "palette": palette, "images": images, "pages": pages}
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(os.path.join(ROOT, "tools", "viewer.html")) as handle:
        shell = handle.read()
    with open(OUT, "w") as handle:
        handle.write(shell.replace("/*__DATA__*/null", blob))
    size = os.path.getsize(OUT)
    print(f"{len(pages)} pages, {len(images)} drawings, "
          f"{len(palette)} colours -> {size/1e6:.1f} MB")


if __name__ == "__main__":
    build()
