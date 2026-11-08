# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Render a region of a datasheet page as an image, zoomed.

A land-pattern figure read as a whole page is a few hundred pixels wide:
the BGT60LTR11AIP's "0.75" between two ball rows and the TPSM63603's
stepped corner lands were lost at that size, take after take, and the
lands were built on an even grid and as plain rectangles. Rendered alone
at 300 to 500 dpi the same figure reads plainly. Numbers printed as text
are listed with where they sit; a figure drawn as a picture has none.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Sequence

#: PDFium is not thread safe: three pages rendered at once from one batched
#: call left it answering "Data format error" for every file after, for the
#: rest of the server's life. Every use goes through this lock.
_PDFIUM = threading.Lock()

#: Printed values: 0.75, .5, 16.70 (whole numbers are mostly labels).
_NUMBER = re.compile(r"(?<![\d.])\d*\.\d+(?![\d.])")
#: A ball or land name printed in a figure: row letters and a column number.
_BALL = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{1,2}(?![A-Za-z0-9])")
#: A row letter or column number standing alone, as along a ball map's edges.
_LABEL = re.compile(r"(?<![A-Za-z0-9.])(?:[A-Z]{1,2}|\d{1,2})(?![A-Za-z0-9.])")


def _region(region: Sequence[float] | None) -> tuple[float, float, float, float]:
    if not region:
        return 0.0, 0.0, 1.0, 1.0
    if len(region) != 4:
        raise ValueError("region is [left, top, right, bottom], each 0 to 1 "
                         "of the page")
    x0, y0, x1, y1 = (float(v) for v in region)
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("region is [left, top, right, bottom] with 0 <= left "
                         "< right <= 1 and 0 <= top < bottom <= 1, as fractions "
                         "of the page from its top left")
    return x0, y0, x1, y1


def render_region(path: str, page: int, region: Sequence[float] | None = None,
                  dpi: int = 400, out: str = "") -> dict[str, Any]:
    """``{ok, png, width, height, region, numbers, names, labels}``: ``numbers``
    are the decimals printed as text inside the region, ``names`` the ball
    names (A1, H8, AB12) and ``labels`` the lone row letters and column
    numbers, each with its position as fractions of the image (left,
    top)."""
    with _PDFIUM:
        return _render_region(path, page, region, dpi, out)


def _render_region(path: str, page: int, region: Sequence[float] | None,
                   dpi: int, out: str) -> dict[str, Any]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return {"ok": False, "reason": "rendering a PDF page needs pypdfium2 "
                                       "(pip install pypdfium2)"}
    if not Path(path).is_file():
        return {"ok": False, "reason": f"no file at {path}"}
    try:
        x0, y0, x1, y1 = _region(region)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    dpi = max(72, min(int(dpi or 400), 800))
    try:
        pdf = pdfium.PdfDocument(path)
    except Exception as exc:  # damaged or encrypted: say so
        return {"ok": False, "reason": f"the PDF could not be opened: {exc}"}
    try:
        if not 1 <= int(page) <= len(pdf):
            return {"ok": False, "reason": f"page {page} is outside 1 to {len(pdf)}"}
        pg = pdf[int(page) - 1]
        width_pt, height_pt = pg.get_size()
        crop = (x0 * width_pt, (1 - y1) * height_pt,
                (1 - x1) * width_pt, y0 * height_pt)  # left, bottom, right, top
        image = pg.render(scale=dpi / 72.0, crop=crop).to_pil()
        numbers: list[dict[str, Any]] = []
        names: list[dict[str, Any]] = []
        labels: list[dict[str, Any]] = []
        try:
            text = pg.get_textpage()
            content = text.get_text_range()
            for pattern, found in ((_NUMBER, numbers), (_BALL, names), (_LABEL, labels)):
                for m in pattern.finditer(content):
                    boxes = [text.get_charbox(i) for i in range(m.start(), m.end())]
                    cx = (min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2
                    cy = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2
                    fx, fy = cx / width_pt, 1 - cy / height_pt
                    if x0 <= fx <= x1 and y0 <= fy <= y1:
                        found.append({"value": m.group(),
                                      "at": [round((fx - x0) / (x1 - x0), 3),
                                             round((fy - y0) / (y1 - y0), 3)]})
        except Exception:  # a page without a text layer has no text to give
            numbers, names, labels = [], [], []
    finally:
        pdf.close()
    target = Path(out) if out else Path(path).with_name(
        f"{Path(path).stem}_p{int(page)}.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    result: dict[str, Any] = {
        "ok": True, "png": str(target), "width": image.width,
        "height": image.height, "region": [x0, y0, x1, y1], "dpi": dpi,
        "numbers": numbers, "names": names, "labels": labels}
    if not numbers:
        result["note"] = ("no number in this region is text: the figure is a "
                          "picture, so read its numbers from the image")
    return result
