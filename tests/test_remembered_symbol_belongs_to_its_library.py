# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The remembered library symbol is used only while its library is current.

LastCreatedLibComponent is held across commands (it finds a symbol made in
this session before the library can see it), and every lib_component scope
sets it. Nothing cleared it when that library was closed outside
RefreshSchLibFromDisk. MEASURED on AD 25.8.1: after scopes on a library
that was closed and reopened, library.add_symbol_rectangle answered
shown=false and the rectangle landed in no part of the symbol on screen,
in memory or after save + reopen: it went into a freed component.

Now the symbol is remembered with its document's object address, and handed
out only while the current library is that same document object. The
stale reference is never read to decide.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _main():
    return load(_ALTIUM / "Main.pas", minimum=50)


def _units(name, minimum=40):
    return load(_ALTIUM / name, minimum=minimum)


def test_remembering_stores_the_document():
    body = _main()["RememberLibComponent"]
    assert "LastCreatedLibComponent := Comp;" in body
    assert "LastCreatedLibComponentName := Name;" in body
    assert "LastCreatedLibDocAddr := LibDocAddress(Doc);" in body
    assert "Doc.I_ObjectAddress" in _main()["LibDocAddress"]


def test_the_symbol_is_handed_out_only_for_its_own_document():
    body = _main()["RememberedLibComponent"]
    assert "If LastCreatedLibComponent = Nil Then Exit;" in body
    assert "If LastCreatedLibDocAddr = 0 Then Exit;" in body
    assert "If (Addr = 0) Or (Addr <> LastCreatedLibDocAddr) Then Exit;" in body
    # Decided before the reference is handed out, and nothing is read off it.
    assert body.index("Addr <> LastCreatedLibDocAddr") < body.index(
        "Result := LastCreatedLibComponent;")
    assert "LastCreatedLibComponent." not in body


def test_every_place_that_remembers_goes_through_the_helper():
    for unit in ("Library.pas", "Generic.pas", "Project.pas", "Application.pas"):
        for name, body in _units(unit, minimum=10).items():
            for line in body.splitlines():
                m = re.match(r"\s*LastCreatedLibComponent\s*:=\s*(\S+?);", line)
                if m:
                    assert m.group(1) == "Nil", (unit, name, line.strip())
    lib = _units("Library.pas")
    assert "RememberLibComponent(SchLib, Component, Name);" in lib["Lib_CreateSymbol"]
    assert "RememberLibComponent(SchLib, Component, Name);" in lib["SelectLibComponentPart"]
    assert "RememberLibComponent(DestLib, NewComp, NewName);" in lib["Lib_CopyComponent"]
    assert "RememberLibComponent(SchLib, Component, NewName);" in lib["Lib_RenameComponent"]


def test_the_targets_ask_for_the_current_document():
    lib = _units("Library.pas")
    assert "Result := RememberedLibComponent(SchLib);" in lib["GetTargetLibComponent"]
    assert "If (RememberedLibComponent(SchLib) <> Nil) And" in lib["FindLibComponentInMemory"]
    gen = _units("Generic.pas")
    assert "Comp := RememberedLibComponent(SchDoc);" in gen["DeleteParametersAnyOwner"]
    assert "LibComp := RememberedLibComponent(SchDoc);" in gen["Gen_TogglePinVisibility"]


def test_no_unguarded_read_of_the_remembered_symbol():
    # A read off a freed component raises an error box no Try catches. Every
    # read of LastCreatedLibComponent outside Main must sit behind a check
    # that it belongs to the current document.
    for unit in ("Library.pas", "Generic.pas"):
        for name, body in _units(unit).items():
            lines = body.splitlines()
            for i, line in enumerate(lines):
                if not re.search(r"\bLastCreatedLibComponent\b", line) or ":= Nil" in line:
                    continue
                window = "\n".join(lines[max(0, i - 3):i + 1])
                assert "RememberedLibComponent(" in window, (unit, name, line.strip())


def test_the_resets_forget_the_document_too():
    lib = _units("Library.pas")
    body = lib["RefreshSchLibFromDisk"]
    assert body.index("LastCreatedLibComponent := Nil;") < body.index("LastCreatedLibDocAddr := 0;")
    form = (_ALTIUM / "StatusForm.pas").read_text(encoding="utf-8-sig")
    assert "LastCreatedLibDocAddr := 0;" in form
