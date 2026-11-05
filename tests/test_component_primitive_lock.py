# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A placed part's primitive lock can be read and written.

audit_find_unlocked_component_primitives reports parts whose footprint
primitives are unlocked, and its docstring says to re-lock them with
obj_modify, but the generic layer did not know PrimitiveLock: the write
answered unknown property. It is now a part property beside LockStrings,
read and written as a boolean through the component interface. Measured
on AD 25.8: one part unlocked and locked again by Name, then every part
on a 331-part board in one call, each read back by the audit as well.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"


def _body(name):
    return load(_GENERIC, minimum=20)[name]


def test_the_lock_belongs_to_a_part():
    body = _body("ExtraPCBPropertyOwner")
    part = body.index("(PropName = 'EnablePinSwapping')")
    owner = body.index("Then Result := 6", part)
    assert part < body.index("(PropName = 'PrimitiveLock')") < owner


def test_the_lock_is_read_as_a_boolean():
    assert ("Else If PropName = 'PrimitiveLock' Then Result := "
            "BoolToJsonStr(Comp.PrimitiveLock)") in _body("PCBGetExtra")


def test_the_lock_is_written_as_a_boolean():
    body = _body("PCBSetExtra")
    assert "(PropName = 'PrimitiveLock') Or" in body
    assert "Else If PropName = 'PrimitiveLock' Then Comp.PrimitiveLock := B" in body


def test_the_lock_is_advertised():
    assert "LockStrings, PrimitiveLock," in _body("KnownPCBPropertyList")
