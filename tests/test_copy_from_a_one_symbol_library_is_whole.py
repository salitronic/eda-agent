# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""library.copy_component copies the only symbol of a library whole.

The copy takes the source off screen before Replicate by showing another
symbol, because the part on screen is held by the library and Replicate
copies the component's own children only. A library holding ONE symbol, as
every vendor download does, had nothing else to show and its copy came out
with no pins. Such a library now gets an empty holder symbol to show, which
is removed again right after Replicate.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _fns():
    return load(_LIB)


def test_the_holder_is_shown_only_when_no_other_symbol_could_be():
    body = _fns()["Lib_CopyComponent"]
    other = body.index("If SelectLibComponentPart(OtherName, 0) <> Nil Then SourceShown := False;")
    holder = body.index("Holder := ShowLibHolder(SourceLib, SourceComp);")
    replicate = body.index("NewComp := SourceComp.Replicate;")
    assert other < holder < replicate
    gate = body[:holder].rsplit("If SourceShown Then", 1)[1]
    assert "OtherLibComponentName" not in gate


def test_a_shown_holder_clears_source_shown():
    body = _fns()["Lib_CopyComponent"]
    branch = body.split("Holder := ShowLibHolder(SourceLib, SourceComp);", 1)[1]
    branch = branch.split("NewComp := SourceComp.Replicate;", 1)[0]
    assert "If Holder <> Nil Then" in branch
    assert "SourceShown := False;" in branch
    assert "UsedHolder := True;" in branch


def test_the_holder_is_removed_before_any_exit_after_replicate():
    body = _fns()["Lib_CopyComponent"]
    after = body.split("NewComp := SourceComp.Replicate;", 1)[1]
    drop = after.index("If Holder <> Nil Then DropLibHolder(SourceLib, Holder, SourceComp);")
    assert drop < after.index("Exit;")


def test_the_holder_is_dropped_when_the_editor_does_not_show_it():
    fn = _fns()["ShowLibHolder"]
    check = fn.index("If (OnScreen = Nil) Or (OnScreen.I_ObjectAddress <> Holder.I_ObjectAddress) Then")
    assert fn.index("SchLib.CurrentSchComponent := Holder;") < check
    assert "DropLibHolder(SchLib, Holder, Shown);" in fn[check:]
    assert fn.index("Result := Holder;") > check


def test_the_holder_never_becomes_the_remembered_symbol():
    # Later add_symbol_* calls write into the remembered symbol.
    fn = _fns()["ShowLibHolder"]
    assert "RememberLibComponent" not in fn
    assert "Holder.LibReference := HolderName;" in fn.split("AddSchComponent(Holder);", 1)[1]


def test_the_holder_takes_a_free_name():
    fn = _fns()["ShowLibHolder"]
    assert "While FindLibComponentInMemory(SchLib, HolderName) <> Nil Do" in fn


def test_dropping_shows_the_source_again_before_removing_the_holder():
    fn = _fns()["DropLibHolder"]
    assert fn.index("SchLib.CurrentSchComponent := Shown;") < fn.index("SchLib.RemoveSchComponent(Holder);")


def test_the_reply_says_a_holder_was_used():
    body = _fns()["Lib_CopyComponent"]
    assert "',\"source_holder\":' + BoolToJsonStr(UsedHolder)" in body


def test_helpers_are_defined_before_their_callers():
    raw = _LIB.read_text(encoding="utf-8-sig")
    drop = raw.index("Procedure DropLibHolder(")
    show = raw.index("Function ShowLibHolder(")
    copy = raw.index("Function Lib_CopyComponent(")
    assert drop < show < copy
