# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The save-state hazard of copy_component stays written down where it bites.

MEASURED on AD 25.8.1: after a copy_component into a saved SchLib no later
write marks the library modified until the next save, so the library reads
"saved" while it holds the copy, and Altium can close it without asking.
The cause is not found; the verified workaround is a "touch" (read one
symbol's description, write the same text back) BEFORE the copy. The note
sits above Lib_CopyComponent and MarkLibDirty, so whoever changes either
meets it. These checks read the raw source: the notes are comments.
"""
from __future__ import annotations

from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _raw() -> str:
    return _LIB.read_text(encoding="utf-8-sig")


def _header(raw: str, routine: str) -> str:
    # The handler's header block sits above it, with only the small pin
    # counter (CountComponentPins) in between.
    at = raw.index(routine)
    return raw[max(0, at - 4000):at]


def test_the_copy_handler_carries_the_note_and_the_workaround():
    head = _header(_raw(), "Function Lib_CopyComponent(")
    title = head.rindex("Duplicate a component within or BETWEEN schematic libraries")
    head = head[title:]   # from the handler's own header on
    assert "SAVE STATE AFTER A COPY" in head
    assert "does not hold" in head
    assert "BEFORE the copy" in head and "touch" in head
    assert "A touch AFTER the copy does not help" in head


def test_the_mark_points_at_it():
    raw = _raw()
    at = raw.index("Procedure MarkLibDirty(")
    head = raw[max(0, at - 1500):at]
    assert "The mark does not always hold" in head
    assert "Lib_CopyComponent" in head
