# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Find the pages of a datasheet PDF that carry given words.

A manufacturer's land pattern is often far from the package outline (under
"Layout and design recommendations", "Soldering", "PCB layout"), and a
reader who opens the pin table and the outline concludes there is none.
This lists the pages that mention it, so the drawing can be read there.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

#: Phrases a manufacturer's recommended land pattern is published under.
LAND_PATTERN_TERMS = (
    "land pattern", "landing pattern", "recommended footprint", "footprint", "pad layout",
    "pcb layout", "solder pad", "pcb pad", "mounting pad", "solder land",
    "layout recommendation", "design recommendation", "stencil",
    "recommended pad", "pad dimension", "board layout",
)

#: Lines quoted per page, so a long table cannot flood the reply.
_LINES_PER_PAGE = 3


def find_pages(path: str, terms: Iterable[str] = LAND_PATTERN_TERMS) -> dict[str, Any]:
    """``{ok, path, pages_searched, terms, pages: [{page, lines}]}`` for the
    pages whose text contains any of ``terms`` (case ignored), 1-based."""
    words = [t.strip().lower() for t in terms if t and t.strip()]
    if not words:
        return {"ok": False, "reason": "give at least one word to look for"}
    pdf = Path(path)
    if not pdf.is_file():
        return {"ok": False, "reason": f"no file at {path}"}
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"ok": False, "reason": "searching a PDF needs pypdf "
                                       "(pip install pypdf)"}
    try:
        reader = PdfReader(str(pdf))
    except Exception as e:  # a damaged or encrypted file: say which
        return {"ok": False, "reason": f"the PDF could not be opened: {e}"}
    found: list[dict[str, Any]] = []
    unreadable: list[int] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # one bad page does not end the search
            unreadable.append(number)
            continue
        lines = [ln.strip() for ln in text.splitlines()
                 if any(w in ln.lower() for w in words)]
        if lines:
            found.append({"page": number,
                          "lines": [ln[:160] for ln in lines[:_LINES_PER_PAGE]]})
    out: dict[str, Any] = {"ok": True, "path": str(pdf),
                           "pages_searched": len(reader.pages),
                           "terms": words, "pages": found}
    if unreadable:
        out["unreadable_pages"] = unreadable
    if not found:
        out["note"] = ("no page mentions these words; a scanned page has no "
                       "text to search, so look at the drawings too")
    return out


#: (path, size, mtime) -> result, so a large datasheet is read once a session.
_CACHE: dict[tuple[str, int, float], dict[str, Any]] = {}


def land_pattern_pages(path: str) -> dict[str, Any]:
    """:func:`find_pages` with the land-pattern words, cached per file."""
    try:
        st = Path(path).stat()
    except OSError:
        return find_pages(path)
    key = (str(Path(path).resolve()).lower(), st.st_size, st.st_mtime)
    if key not in _CACHE:
        _CACHE[key] = find_pages(path)
    return _CACHE[key]
