# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A pin's DefinedFunctions does not stop the loop on Altium 25.

GetDefinedFunctionsCount, GetDefinedFunction, ClearDefinedFunctions and
AddDefinedFunction are Altium 26 names. MEASURED on AD 25.8.1: the first
generic read of a pin's DefinedFunctions raised "Undeclared identifier:
GetDefinedFunctionsCount" in an error box no Try catches, and the polling
loop stopped. The version is now read before any of them; Altium 25 reads
the functions from the pin's record (ExportToParameters) and writes them
through SetState_FunctionsFromName with the name put back.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"
_AD26 = ("GetDefinedFunctionsCount", "GetDefinedFunction(", "ClearDefinedFunctions",
         "AddDefinedFunction(")


def _units():
    return load(_GENERIC, minimum=20)


def test_the_gate_reads_the_version_and_wants_26():
    gate = _units()["AltiumMajorAtLeast"]
    assert "Client.GetProductVersion" in gate
    assert "Result := Major >= Wanted;" in gate
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    # A routine with no parameters shows in Altium's Run Script window.
    assert "Function AltiumMajorAtLeast(Wanted : Integer) : Boolean;" in raw
    assert raw.index("Function AltiumMajorAtLeast(") < raw.index(
        "Function PinFunctionsText("), "no forward declarations in DelphiScript"


def test_the_read_leaves_before_any_altium_26_name():
    body = _units()["PinFunctionsText"]
    leave = body.index("If Not AltiumMajorAtLeast(26) Then")
    assert "Result := PinFunctionsFromRecord(Pin);" in body[leave:]
    assert body.index("Exit;", leave) < body.index("Pin.GetDefinedFunctionsCount")


def test_altium_25_reads_the_record():
    body = _units()["PinFunctionsFromRecord"]
    assert "Pin.ExportToParameters" in body
    assert "'PINDEFINEDFUNCTIONSCOUNT'" in body
    assert "'PINDEFINEDFUNCTION' + IntToStr(I)" in body
    for name in _AD26:
        assert name not in body
    # The key is matched with its '|' and '=' around it: FUNCTION1 must not
    # find FUNCTION10, and COUNT must not find FUNCTION1.
    assert "Pos('|' + Key + '=', Rest)" in _units()["PinRecordValue"]


def test_every_altium_26_write_is_behind_the_gate():
    put = _units()["SchSetExtra"]
    assert "Native := AltiumMajorAtLeast(26);" in put
    lines = [ln for ln in put.splitlines() if any(n in ln for n in _AD26)]
    assert len(lines) == 2, lines
    for ln in lines:
        assert ln.strip().startswith("If Native Then"), ln


def test_altium_25_writes_through_the_name_and_puts_it_back():
    put = _units()["SchSetExtra"]
    branch = put.split("If Not Native Then", 1)[1]
    # MEASURED on AD 25.8.1: "X1" alone leaves the list as it was; "X1/"
    # gives X1. And no name clears the list, so an empty one is refused.
    seq = ["OldName := Pin.Name;", "Pin.Name := Slashed + '/';",
           "Pin.SetState_FunctionsFromName;", "Pin.Name := OldName;"]
    at = [branch.index(s) for s in seq]
    assert at == sorted(at), at
    # A '/' inside one function would split it: refused before the name moves.
    assert branch.index("If (Wanted = '') Or (Pos('/', Wanted) > 0) Then Exit;") < at[0]


def test_no_other_routine_calls_the_altium_26_names():
    for name, body in _units().items():
        if name in ("PinFunctionsText", "SchSetExtra"):
            continue
        for ad26 in _AD26:
            assert not re.search(r"\b" + re.escape(ad26), body), (name, ad26)


def test_no_new_routine_is_runnable_from_altium():
    # Altium lists every routine with no parameters in Run Script; the
    # bridge offers StartMCPServer only.
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    assert not re.search(r"(?mi)^(Function|Procedure) [A-Za-z_]+ *(:|;)", raw)
