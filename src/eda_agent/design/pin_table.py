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
#: Words a subscripted name can be printed as: V, CC, LO for V_CC_LO.
_SUBSCRIPT_WORDS = 3
#: What may follow a pin number: not a letter or digit, and a period only
#: as list numbering ("1. VIO"), never as a decimal point ("1.8 V").
_AFTER_NUMBER = r"(?![A-Za-z0-9]|\.\d)"
#: A pin number or ball name standing alone: 7, 12, A1, AB12, "1.".
_ANY_NUMBER = re.compile(r"(?<![A-Za-z0-9.])[A-Z]{0,2}\d{1,3}" + _AFTER_NUMBER)


#: A line that is a pin number or ball name and nothing else.
_ONLY_NUMBER = re.compile(r"\s*[A-Z]{0,2}\d{1,3}\s*\.?\s*")


#: Pins read from a library being imported (EasyEDA, KiCad), by the id the
#: importer gives them, and how many: the source library is their record.
IMPORTED: dict[str, int] = {}


def norm(name: str) -> str:
    """A pin name as compared: no overbar marks, no #, /, ~ or _, upper case."""
    return _DROP.sub("", str(name or "")).upper()


def _number_in(number: str, line: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9.]){re.escape(number)}{_AFTER_NUMBER}",
                     line, re.I) is not None


def _name_in(name: str, line: str) -> bool:
    want = norm(name)
    if not want:
        return False
    tokens = [norm(t) for t in _SPLIT.split(line)]
    for start in range(len(tokens)):
        # A subscript comes out of the PDF as its own word ("V CC" for
        # V_CC), so up to three neighbouring words are read as one name.
        got = ""
        for token in tokens[start:start + _SUBSCRIPT_WORDS]:
            got += token
            if got == want or got == "N" + want or want == "N" + got:
                return True
            if len(got) >= len(want) + 1:
                break
    return False


#: How many lines a name wrapped in its table cell may run over.
_WRAP_LINES = 4

#: Names an exposed pad is given in a symbol, compared as norm() gives them.
_PAD_NAMES = {"EP", "EPAD", "PAD", "EXPOSEDPAD", "THERMALPAD", "TAB", "SLUG"}
#: Ground pin names, for an exposed pad the table calls ground.
_GROUND_NAMES = {"GND", "GROUND", "VSS", "AGND", "PGND", "DGND", "EPGND"}
#: A table row for the exposed pad, which tables print without a number.
_PAD_ROW = re.compile(r"\b(exposed|thermal|e-?pad|power ?pad|die ?pad|slug)\b", re.I)


def _tokens(lines: list[str]) -> list[tuple[str, str, int]]:
    """Every word of the table, in reading order: (as printed, as compared,
    line index)."""
    out = []
    for i, line in enumerate(lines):
        for raw in _SPLIT.split(line):
            if raw:
                out.append((raw, norm(raw), i))
    return out


def _wrapped_hit(number: str, name: str, tokens: list[tuple[str, str, int]],
                 lines: list[str]) -> Optional[str]:
    """The rows of a name wrapped over lines of its cell, when its number
    is the word just before or just after it.

    A long name breaks in its cell, often mid-word ("UA" / "RT6_RX_M1"),
    and the PDF text gives each piece its own line, the number on the
    line after the last piece (or before the first). The pieces are read
    as one name; the number must touch it, so a neighbouring row's name
    in the same few lines never passes for this pin's.

    A page break can split the cell: the name's last piece then comes
    after the row's own line, the one that starts with the number. That
    line is stepped over, and the number counts as touching the name.
    """
    want = norm(name)
    if not want or not number:
        return None
    num = number.upper()
    # Printed with or without the N an active-low name may carry.
    targets = (want, "N" + want) + ((want[1:],) if want.startswith("N") and len(want) > 1 else ())
    for start, (_raw, first, line0) in enumerate(tokens):
        if not first or not any(t.startswith(first) for t in targets):
            continue
        got, end, matched, straddled = "", start, False, False
        while end < len(tokens) and tokens[end][2] - line0 < _WRAP_LINES + 1:
            raw, piece, line = tokens[end]
            if raw.rstrip(".").upper() == num and not straddled and got \
                    and (end == 0 or tokens[end - 1][2] != line):
                # The row's own line, between two pieces of its name.
                straddled = True
                while end < len(tokens) and tokens[end][2] == line:
                    end += 1
                continue
            got += piece
            if got in targets:
                matched = True
                break
            if not any(t.startswith(got) for t in targets):
                break
            end += 1
        if not matched or tokens[end][2] == line0:
            continue                       # none, or on one line: the plain check's
        before = tokens[start - 1][0] if start else ""
        after = tokens[end + 1][0] if end + 1 < len(tokens) else ""
        if straddled or num in (before.rstrip(".").upper(), after.rstrip(".").upper()):
            return " / ".join(lines[line0:tokens[end][2] + 1])[:120]
    return None


def _running_heads(pages: dict[int, list[str]]) -> set[str]:
    """Lines a page break puts between a table's rows: a header or footer
    repeated near the top or bottom of most pages, and each page's own
    number."""
    if len(pages) < 2:
        return set()
    seen: dict[str, int] = {}
    for lines in pages.values():
        for ln in set(lines[:3] + lines[-3:]):
            seen[ln] = seen.get(ln, 0) + 1
    return {ln for ln, n in seen.items() if n >= max(2, len(pages) // 2)}


def _without_page_breaks(pages: dict[int, list[str]]) -> list[str]:
    """The pages' lines in order, without their running headers, footers
    and page numbers, so a row split by a page break reads on."""
    heads = _running_heads(pages)
    out = []
    for number in sorted(pages):
        lines = pages[number]
        edge = set(range(3)) | set(range(len(lines) - 3, len(lines)))
        out.extend(ln for i, ln in enumerate(lines)
                   if not (i in edge and (ln in heads or ln == str(number))))
    return out


def _as_exposed_pad(number: str, name: str, pad_rows: list[str], lines: list[str],
                    found: list[dict[str, Any]]) -> Optional[str]:
    """The exposed pad's row, when this pin is it: the table prints the pad
    without a number, the pin's number is nowhere in the table, its name
    is the pad's (as the row prints it, or EP, EPAD, PAD...), and no other
    pin has been taken for the pad."""
    if not pad_rows or any(f.get("exposed_pad") for f in found):
        return None
    if number and any(_number_in(number, ln) for ln in lines):
        return None
    want = norm(name)
    for row in pad_rows:
        if want in _PAD_NAMES or _name_in(name, row):
            return row
        # "Exposed pad: ground" is the GND pin by any of its usual names.
        if want in _GROUND_NAMES and re.search(r"\b(ground|gnd|vss)\b", row, re.I):
            return row
    return None


def _exposed_pad_rows(lines: list[str]) -> list[str]:
    """Rows for the exposed pad that print no pin number."""
    return [ln for ln in lines if _PAD_ROW.search(ln) and not _ANY_NUMBER.search(ln)]


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
    together, print both its number and its name; when its name runs
    wrapped over a few lines of its cell with the number touching it; or,
    for one pin, when it is the exposed pad the table lists without a
    number (``exposed_pad`` in its entry of ``found``).
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
    lines = _without_page_breaks(lines_by_page)
    if not lines:
        return {"ok": False, "pages": sorted(pages), "reason": (
            "the pin table pages have no text to read (the table is a "
            "picture): the pins cannot be checked. Stop and report it; do "
            "not build the symbol another way")}
    # A row wrapped onto the next line: only a line that prints no number of
    # its own joins the next, so one row's number never meets the next name.
    wrapped = [f"{a} {b}" for a, b in zip(lines, lines[1:]) if not _ANY_NUMBER.search(a)]
    # A package drawing labels a pin as its number alone, the name on the
    # line after. A number labelled so is read with its label and never
    # with the wrapped rows: joined the other way, each name in such a
    # drawing would meet the next pin's number.
    labels: dict[str, list[str]] = {}
    for a, b in zip(lines, lines[1:]):
        # One pin's name: a word, or two for a subscript ("V BAT"); a
        # grid's column number over a row of names is not a label.
        if _ONLY_NUMBER.fullmatch(a) and not b.strip().isdigit() and len(b.split()) <= 2:
            labels.setdefault(a.strip().rstrip(".").strip().upper(), []).append(f"{a} {b}")
    tokens = _tokens(lines)
    pad_rows = _exposed_pad_rows([*lines, *wrapped])
    found, missing = [], []
    for pin in pins:
        number = str(pin.get("designator", "")).strip()
        name = str(pin.get("name", "")).strip()
        label = labels.get(number.upper())
        rows = [*lines, *label] if label else [*lines, *wrapped]
        hit = next((ln for ln in rows
                    if number and _number_in(number, ln) and _name_in(name, ln)), None)
        if hit is None:
            hit = _wrapped_hit(number, name, tokens, lines)
        if hit:
            found.append({"pin": number, "name": name, "row": hit[:120]})
            continue
        pad = _as_exposed_pad(number, name, pad_rows, lines, found)
        if pad:
            found.append({"pin": number, "name": name, "row": pad[:120],
                          "exposed_pad": True})
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
