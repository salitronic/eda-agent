# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""library.copy_component copies the part on screen too.

While a symbol is shown in the SchLib editor, the objects of the part on
screen are held by the library, not the component, and Replicate copies
the component's own children only. MEASURED on AD 25.8.1, a 5-part symbol
with part 3 on screen: the copy's part 3 was empty, 11 of 14 pins, and it
stayed so after save and reopen; with another symbol shown first the copy
was whole. The source is now taken off screen before Replicate, and a
library with no other symbol says why its copy is short.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _copy():
    return load(_LIB)["Lib_CopyComponent"]


def test_the_source_is_taken_off_screen_before_replicate():
    body = _copy()
    shown = body.index("SourceShown := (OnScreen.I_ObjectAddress = SourceComp.I_ObjectAddress);")
    other = body.index("OtherName := OtherLibComponentName(SourceLib, SourceName);")
    select = body.index("If SelectLibComponentPart(OtherName, 0) <> Nil Then SourceShown := False;")
    replicate = body.index("NewComp := SourceComp.Replicate;")
    assert shown < other < select < replicate


def test_it_comes_after_the_load_that_shows_the_source():
    # The 0-pin load shows the source; the switch must follow it.
    body = _copy()
    load_ = body.index("Shown := SelectLibComponentPart(SourceName, 0);")
    switch = body.index("If SelectLibComponentPart(OtherName, 0) <> Nil")
    assert load_ < switch


def test_only_when_there_is_another_symbol():
    body = _copy()
    assert "If OtherName <> '' Then" in body
    assert body.index("If SourceShown Then") < body.index("OtherName := OtherLibComponentName(")


def test_a_short_copy_says_why_when_it_could_not_switch():
    body = _copy()
    branch = body.split("If CopyPins <> SourcePins Then", 1)[1]
    assert branch.index("If SourceShown Then") < branch.index("BuildSuccessResponse(")
    assert "which replaces it.' + ShownNote)" in branch


def test_the_helper_is_defined_before_the_copy():
    raw = _LIB.read_text(encoding="utf-8-sig")
    assert raw.index("Function OtherLibComponentName(") < raw.index("Function Lib_CopyComponent(")
