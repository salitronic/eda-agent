# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A symbol's pins against the datasheet's pin table, before it is built.

Pin numbers and names are typed from the pin table by hand. One swapped
pair compiles, passes ERC and gives a board that does not work. Each pin
the symbol is to have is looked for in the table's own text: a row (or a
row wrapped onto the next line) that prints both its number and its name.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable, Optional

from .landpattern.land_source import cited_pages

#: Headings a pin table is published under.
PIN_TABLE_TERMS = (
    "pin configuration", "pin functions", "pin function", "pin description",
    "pin descriptions", "pin assignment", "pin assignments", "pinout",
    "pin out", "terminal functions", "terminal configuration", "pin definitions",
    "pin list", "ball assignment", "signal description",
)

#: Characters that differ between how a datasheet and a symbol write a name.
_DROP = re.compile(r"[\\#/~!_\s̅‾*']")
_SPLIT = re.compile(r"[\s,;()\[\]|]+")
#: A pin number or ball name standing alone: 7, 12, A1, AB12.
_ANY_NUMBER = re.compile(r"(?<![A-Za-z0-9.])[A-Z]{0,2}\d{1,3}(?![A-Za-z0-9.])")


#: Pins read from a library being imported (EasyEDA, KiCad), by the id the
#: importer gives them, and how many: the source library is their record.
IMPORTED: dict[str, int] = {}


def norm(name: str) -> str:
    """A pin name as compared: no overbar marks, no #, /, ~ or _, upper case."""
    return _DROP.sub("", str(name or "")).upper()


def _number_in(number: str, line: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9.]){re.escape(number)}(?![A-Za-z0-9.])",
                     line, re.I) is not None


def _name_in(name: str, line: str) -> bool:
    want = norm(name)
    if not want:
        return False
    for token in _SPLIT.split(line):
        got = norm(token)
        if got == want or got == "N" + want or want == "N" + got:
            return True
    return False


def pins_from(groups: Iterable[Any]) -> list[dict[str, Any]]:
    """Every pin dict in lists of pins (``None`` rows skipped)."""
    out: list[dict[str, Any]] = []
    for group in groups:
        for pin in group or []:
            if isinstance(pin, dict):
                out.append(pin)
    return out


def numbered_only(pins: list[dict[str, Any]]) -> bool:
    """A connector or header: every pin named by its number or not at all."""
    return all(norm(p.get("name", "")) in ("", norm(p.get("designator", "")))
               for p in pins)


def _page_lines(path: str, pages: Optional[set[int]]) -> tuple[dict[int, list[str]], str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {}, "reading the pin table needs pypdf (pip install pypdf)"
    try:
        reader = PdfReader(path)
    except Exception as e:  # damaged or encrypted: say so
        return {}, f"the PDF could not be opened: {e}"
    out: dict[int, list[str]] = {}
    for number, page in enumerate(reader.pages, start=1):
        if pages is not None and number not in pages:
            continue
        try:
            text = page.extract_text() or ""
        except Exception:  # one bad page does not end the read
            continue
        out[number] = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return out, ""


def check_pin_table(datasheet: Any, pins: list[dict[str, Any]]) -> dict[str, Any]:
    """``{ok, pages, found, missing, reason?}``.

    ``datasheet`` is ``{path, page}``; with no page, the pages carrying a
    pin-table heading are read. A pin passes when one line, or two lines
    together, print both its number and its name.
    """
    if not isinstance(datasheet, dict) or not datasheet.get("path"):
        return {"ok": False, "reason": (
            "give datasheet: {path, page}, the datasheet PDF and the page of "
            "its pin table, so the pins can be checked against it before the "
            "symbol is built")}
    path = str(datasheet["path"])
    if not os.path.isfile(path):
        return {"ok": False, "reason": f"datasheet.path names no file: {path}"}
    pages = cited_pages(str(datasheet.get("page", "") or "")) or None
    if pages is None:
        from .datasheet_search import find_pages
        hit = find_pages(path, PIN_TABLE_TERMS)
        pages = {p["page"] for p in hit.get("pages") or []} or None
        if pages is None:
            return {"ok": False, "reason": (
                "no page of the datasheet carries a pin-table heading: give "
                "datasheet.page, the page the pin table is on")}
    lines_by_page, problem = _page_lines(path, pages)
    if problem:
        return {"ok": False, "reason": problem}
    lines = [ln for n in sorted(lines_by_page) for ln in lines_by_page[n]]
    if not lines:
        return {"ok": False, "pages": sorted(pages), "reason": (
            "the pin table pages have no text to read (the table is a "
            "picture): the pins cannot be checked. Stop and report it; do "
            "not build the symbol another way")}
    # A row wrapped onto the next line: only a line that prints no number of
    # its own joins the next, so one row's number never meets the next name.
    pairs = [*lines, *(f"{a} {b}" for a, b in zip(lines, lines[1:])
                       if not _ANY_NUMBER.search(a))]
    found, missing = [], []
    for pin in pins:
        number = str(pin.get("designator", "")).strip()
        name = str(pin.get("name", "")).strip()
        hit = next((ln for ln in pairs
                    if number and _number_in(number, ln) and _name_in(name, ln)), None)
        if hit:
            found.append({"pin": number, "name": name, "row": hit[:120]})
            continue
        rows = [ln[:120] for ln in lines if number and _number_in(number, ln)][:3]
        missing.append({"pin": number, "name": name, "rows_with_number": rows})
    out: dict[str, Any] = {"ok": not missing, "pages": sorted(lines_by_page),
                           "found": len(found), "missing": missing}
    if missing:
        out["reason"] = (
            f"{len(missing)} pin(s) are not in the pin table as given: "
            + "; ".join(f"{m['pin']} {m['name']}" for m in missing[:15])
            + ". Read the table again and correct the number or the name "
              "(rows_with_number shows what it prints for that number)")
    return out
