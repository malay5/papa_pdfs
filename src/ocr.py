"""OCR helpers.

The catalogs carry no embedded fonts -- every glyph is a filled vector path --
so the text has to be read back off a render.  Two things make that reliable
here:

* Cells are cropped individually.  Reading a whole page in one pass smears
  values across column boundaries and mangles the short numeric cells.
* Each crop is normalised to a fixed ink height.  Rendered at 600 DPI a table
  row is ~90px of cap height, roughly triple what tesseract's line recogniser
  expects, and the oversized thin strokes are what turn "41#" into "Alt".
"""

import os
import re
import subprocess
import tempfile

import pypdfium2 as pdfium
from PIL import Image, ImageOps

DPI = 600
# Whole-page passes run at this resolution instead; see page_text.  Higher
# is not better here: at 300 DPI tesseract's layout analysis drops whole
# dot-leader lines, taking the packaging prices with them.
PAGE_TEXT_DPI = 200
# Ink height tesseract's LSTM is happiest with, in pixels.
TARGET_INK_H = 32
# Points trimmed off each edge of a cell, to keep its border rules and the
# neighbouring cell's ink out of the crop.
CELL_INSET = 1.6
# A binarised row/column at least this dark end to end is a rule, not glyphs.
RULE_DENSITY = 0.92
INK_THRESHOLD = 140


# Tesseract parallelises with OpenMP, which on crops this small costs far
# more than it saves -- and the extractor already runs a process per core, so
# those threads only fight each other.  Holding it to one thread is worth
# about 20x here.
_TESSERACT_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}


class OcrError(RuntimeError):
    pass


def render(pdf_path, page_index, dpi=DPI):
    """Render one page of `pdf_path` to a greyscale PIL image."""
    doc = pdfium.PdfDocument(pdf_path)
    try:
        bitmap = doc[page_index].render(scale=dpi / 72.0, grayscale=True)
        return bitmap.to_pil().convert("L")
    finally:
        doc.close()


def page_count(pdf_path):
    doc = pdfium.PdfDocument(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def _tesseract(image, psm, whitelist=None):
    args = ["-c", "preserve_interword_spaces=1", "--psm", str(psm)]
    if whitelist:
        args += ["-c", "tessedit_char_whitelist=" + whitelist]
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "crop.png")
        image.save(src)
        proc = subprocess.run(
            ["tesseract", src, "stdout", "-l", "eng", *args],
            capture_output=True, text=True, env=_TESSERACT_ENV,
        )
        if proc.returncode != 0:
            raise OcrError(proc.stderr.strip()[:400])
        return proc.stdout


_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}


def clean(text):
    """Tidy raw OCR output without changing what it says."""
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"'))
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _as_dark_on_light(crop):
    """Invert knockout-white text so every crop reaches OCR the same way.

    The coloured header bands and section banners set white type on a fill
    that is often lighter than mid-grey, so polarity is decided by which side
    of the background the minority pixels fall on, not by the fill alone.
    """
    histogram = crop.histogram()
    background = max(range(256), key=lambda v: histogram[v])
    margin = 40
    high, low = background + margin, background - margin
    lighter = sum(histogram[high + 1:]) if high < 255 else 0
    darker = sum(histogram[:low]) if low > 0 else 0
    return ImageOps.invert(crop) if lighter > darker else crop


def _ink_mask(crop):
    """Binarise, then blank out solid rules so they cannot skew the ink box."""
    mask = ImageOps.autocontrast(crop).point(lambda v: 255 if v < INK_THRESHOLD else 0)
    width, height = mask.size
    pixels = mask.load()
    for y in range(height):
        if sum(1 for x in range(width) if pixels[x, y]) >= RULE_DENSITY * width:
            for x in range(width):
                pixels[x, y] = 0
    for x in range(width):
        if sum(1 for y in range(height) if pixels[x, y]) >= RULE_DENSITY * height:
            for y in range(height):
                pixels[x, y] = 0
    return mask


def _normalise(crop):
    """Tighten a crop to its glyphs and scale them to TARGET_INK_H."""
    crop = _as_dark_on_light(crop)
    box = _ink_mask(crop).getbbox()
    if box is None:
        return None
    pad = 2
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(crop.width, box[2] + pad), min(crop.height, box[3] + pad))
    tight = ImageOps.autocontrast(crop.crop(box))
    if tight.height < 3 or tight.width < 3:
        return None
    scale = TARGET_INK_H / tight.height
    # Multi-line cells normalise on their whole block, so cap the blow-up.
    scale = min(scale, 4.0)
    tight = tight.resize((max(1, round(tight.width * scale)),
                          max(1, round(tight.height * scale))), Image.LANCZOS)
    return ImageOps.expand(tight, border=20, fill=255)


def cell_image(page_image, bbox, dpi=DPI):
    """The normalised image of one cell, or None when the cell is empty."""
    scale = dpi / 72.0
    x0, top, x1, bottom = bbox
    box = (round((x0 + CELL_INSET) * scale), round((top + CELL_INSET) * scale),
           round((x1 - CELL_INSET) * scale), round((bottom - CELL_INSET) * scale))
    if box[2] - box[0] < 6 or box[3] - box[1] < 6:
        return None
    return _normalise(page_image.crop(box))


# Ink heights to retry a short cell at.  Which height reads a given glyph
# correctly is not predictable -- "87#" is right at 32 and wrong at 40, "41#"
# the other way round -- so short cells are read at several and voted on.
VOTE_INK_HEIGHTS = (22, 26, 40)
# Longer cells are read correctly at any of these heights, so they get one
# pass.  Below this length a stray character changes the value's meaning.
VOTE_MAX_LEN = 12

# Deliberately tight: a column known to hold numbers is repaired with only
# the glyphs such a column ever uses, so the letters this typeface's digits
# resemble ("4" as "A", "7" as "/") cannot win.
NUMERIC_CHARS = "0123456789#.,"


def _read_image(image, multiline=False, whitelist=None):
    # psm 7 reads a single line; a taller cell may hold several, and the
    # terse numeric cells sometimes only come back under the word/char modes.
    for psm in ((6, 7, 8, 10) if multiline else (7, 6, 8, 10)):
        text = clean(_tesseract(image, psm, whitelist))
        if text:
            return text
    return ""


def _read_at(page_image, bbox, dpi, ink_height, multiline, whitelist):
    global TARGET_INK_H
    previous, TARGET_INK_H = TARGET_INK_H, ink_height
    try:
        image = cell_image(page_image, bbox, dpi)
        return _read_image(image, multiline, whitelist) if image is not None else ""
    finally:
        TARGET_INK_H = previous


def _vote(candidates, preferred):
    """Pick the reading most passes agree on.

    Ties break towards the reading that actually carries characters: a pass
    that resolves "41#" down to "#" agrees with itself as often as the right
    answer does, but says less.
    """
    tally = {}
    for text in candidates:
        if text:
            tally[text] = tally.get(text, 0) + 1
    if not tally:
        return preferred
    winner = max(tally, key=lambda text: (
        tally[text],
        sum(ch.isalnum() for ch in text),
        len(text),
        text == preferred,
    ))
    # A leading decimal point is a pixel or two of ink that the larger scales
    # drop, so it loses a straight vote even though it changes the value by a
    # factor of a thousand.  OCR does not invent one, so any pass that saw it
    # is believed.
    dotted = "." + winner
    return dotted if dotted in tally else winner


def read_cell(page_image, bbox, dpi=DPI, multiline=False, whitelist=None,
              ink_heights=VOTE_INK_HEIGHTS):
    """OCR one table cell. `bbox` is in PDF points."""
    primary = _read_at(page_image, bbox, dpi, TARGET_INK_H, multiline, whitelist)
    if len(primary) > VOTE_MAX_LEN:
        return primary
    others = [_read_at(page_image, bbox, dpi, h, multiline, whitelist)
              for h in ink_heights]
    return _vote([primary, *others], primary)


def read_numeric_cell(page_image, bbox, dpi=DPI):
    """Re-read a cell that should hold a number, restricted to numeric glyphs.

    Used to repair values in an otherwise-numeric column, where the unhinted
    pass has read a digit as the letter it resembles in this typeface.
    """
    return read_cell(page_image, bbox, dpi, whitelist=NUMERIC_CHARS,
                     ink_heights=(22, 26, 34, 40))


def read_block(page_image, bbox=None, dpi=DPI, psm=6):
    """OCR a region of flowing text -- a banner caption, a note block."""
    if bbox is None:
        return clean(_tesseract(page_image, psm))
    scale = dpi / 72.0
    crop = page_image.crop(tuple(round(v * scale) for v in bbox))
    if crop.width < 6 or crop.height < 6:
        return ""
    image = _normalise(crop)
    if image is None:
        return ""
    return clean(_tesseract(image, psm))


def page_text(page_image, psm=3):
    """Flowing text of a whole page -- notes, footnotes, body copy.

    Downscaled first: full-page segmentation on a 600 DPI render takes
    minutes, and page prose is 8-10pt type, which lands right where OCR wants
    it at PAGE_TEXT_DPI.
    """
    if page_image.width > 0:
        scale = PAGE_TEXT_DPI / DPI
        if scale < 1:
            page_image = page_image.resize(
                (max(1, round(page_image.width * scale)),
                 max(1, round(page_image.height * scale))), Image.LANCZOS)
    return clean(_tesseract(page_image, psm))
