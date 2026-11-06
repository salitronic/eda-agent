# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A copy marks its destination modified before it lands, and two library
writes cannot leave a symbol half changed.

After a copy into a saved SchLib, no later write marks the library modified
until the next save, so it reads saved while it holds the copy. Writing a
symbol's description back unchanged BEFORE the copy keeps the mark.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def test_the_destination_is_touched_before_the_copy_lands():
    body = load(_ALTIUM / "Library.pas")["Lib_CopyComponent"]
    touch = body.index("TouchComp.ComponentDescription := TouchText;")
    assert body.index("TouchComp := DestLib.CurrentSchComponent;") < touch
    assert touch < body.index("MarkLibDirty(DestLib);")
    assert touch < body.index("DestLib.AddSchComponent(NewComp);")
    assert touch < body.index("DestLib.RemoveSchComponent(Existing);")


def test_a_pin_gets_its_name_back_even_when_the_split_raises():
    body = load(_ALTIUM / "Generic.pas", minimum=20)["SchSetExtra"]
    at = body.index("OldName := Pin.Name;")
    block = body[at:at + 300]
    assert block.index("Try") < block.index("Pin.SetState_FunctionsFromName;")
    assert block.index("Finally") < block.index("Pin.Name := OldName;")


def test_a_refused_mode_name_is_reported_not_raised():
    body = load(_ALTIUM / "Generic.pas", minimum=20)["WriteDisplayModeNames"]
    assert ("Try Comp.SetState_CustomDisplayModeName(I, Item); "
            "Except NoteWriteFailed('DisplayModeNames'); End;") in body
