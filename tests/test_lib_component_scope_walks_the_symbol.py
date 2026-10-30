# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A lib_component scope reads and writes the named symbol, all of it.

Reported 2026-10-30 from a live library: obj_query and obj_modify on ePin
with scope lib_component:<symbol> never saw the symbol's hidden power pins
(lib_get_pin_list listed them), so a hidden GND pin could not be given its
HiddenNetName; and obj_modify on eSchComponent with that scope and no
filter set ShowHiddenPins on all 25 symbols of the library. The scope made
the symbol current and walked the library's sheet, which shows neither.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic(name: str) -> str:
    return load(SCRIPTS / "Generic.pas")[name]


def test_each_scope_clears_the_symbol_the_last_one_named():
    body = _generic("ApplyLibComponentScope")
    clear = body.index("LibScopeName := '';")
    assert clear < body.index("If ScopeType <> 'lib_component' Then Exit;"), (
        "a batch applies scopes op by op: a plain scope after a symbol's must "
        "not walk that symbol")
    assert "LibScopeName := CompName;" in body and "LibScopePart := PartId;" in body
    one = load(SCRIPTS / "StatusForm.pas")["ProcessOne"]
    assert "LibScopeName := '';" in one


def test_the_walk_is_the_named_symbol_fetched_through_the_library():
    body = _generic("ProcessSchDocObjects")
    assert "If ObjRef = LibScopeName Then ScopeComp := LastCreatedLibComponent;" in body
    # MEASURED 2026.10.30.6: with LookupLibComponent called from Generic the
    # whole unit read as undeclared in Altium (HandleGenericCommand), every
    # generic command halted the loop, and no compile error was shown.
    code = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")
    calls = [line for line in code.splitlines()
             if "LookupLibComponent(" in line.split("{", 1)[0]]
    assert not calls, calls
    assert re.search(r"If ParamsOfSymbol Or OwnWalk Then\s*Iterator := LibComp\.SchIterator_Create", body)
    assert re.search(r"If ParamsOfSymbol Or OwnWalk Then\s*LibComp\.SchIterator_Destroy", body)
    # Parts: the one asked for and the shared part 0, unless the filter asks.
    assert "(ObjPart <> 0) And (ObjPart <> WantPart)" in body
    assert "If OwnWalk And (Pos('OwnerPart', FilterStr) = 0) Then" in body


def test_a_component_scope_matches_only_the_named_symbol():
    body = _generic("ProcessSchDocObjects")
    check = body.index("If (ScopeComp <> Nil) And (ObjTypeInt = eSchComponent) Then")
    assert "If ObjRef <> LibScopeName Then Counted := False;" in body[check:check + 300]
    assert body.index("Counted := False;", check) < body.index("MatchesFilter(Obj, FilterStr)", check)
