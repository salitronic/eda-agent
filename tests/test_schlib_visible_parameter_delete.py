# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A visible parameter of the symbol on screen can be deleted.

MEASURED on AD 25.8: obj_delete of a VISIBLE parameter in a SchLib
answered matched 10000 and removed nothing, in memory and after a save
and reopen; a hidden parameter of the same symbol went at once. While a
symbol is on screen its visible children are held by the library
document, so Comp.RemoveSchObject did nothing, the loop found the same
parameter on every pass and counted it until the guard ran out.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
GENERIC = load(SCRIPTS / "Generic.pas")


def _remover() -> str:
    return GENERIC["RemoveMatchingParamsFromComponent"]


def test_a_parameter_found_again_is_removed_from_the_library():
    body = _remover()
    same = body.index("ThisAddr = LastAddr")
    assert body.index("Lib.RemoveSchObject(FoundParam)", same) > same


def test_only_a_remove_that_took_is_counted():
    body = _remover()
    count = body.index("Inc(TotalMatched)")
    # Counted when the object last acted on is not found again ...
    assert re.search(r"If Pending And \(\(FoundParam = Nil\) Or "
                     r"\(ThisAddr <> LastAddr\)\) Then", body)
    # ... and never straight after the component's remove.
    remove = body.index("Comp.RemoveSchObject(FoundParam)")
    assert count < remove
    assert "Pending := True" in body[remove:]


def test_a_parameter_that_will_not_go_is_a_fault_not_a_loop():
    body = _remover()
    fault = body.index("NoteDeleteFault(")
    assert body.index("Break", fault) > fault
    assert "(SameTries > 1) Or (Lib = Nil)" in body


def test_the_library_path_passes_the_library_and_a_sheet_passes_nil():
    body = GENERIC["DeleteParametersAnyOwner"]
    assert "RemoveMatchingParamsFromComponent(SchDoc, Comp," in body
    assert re.search(r"RemoveMatchingParamsFromComponent\(\s*Nil, Comp,", body)
