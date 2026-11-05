# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A footprint delete removes while another footprint is current.

The current footprint's primitives live on the library board, which is
written back into the footprint at the next switch. Measured on AD 26.10:
removing from the current footprint reported removed 6 and all of them
returned when the editor switched away and back, and Board.RemovePCBObject
did nothing at all. Removing while another footprint was current lasted
through switches, save and reopen, with the primitive freed, for 25 cycles.
"""

from __future__ import annotations

from pathlib import Path

LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _body(name: str) -> str:
    text = LIBRARY.read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;\n", start)]


def test_the_target_is_moved_off_screen_before_anything_is_removed():
    body = _body("Lib_DeleteFootprintPrimitives")
    swap = body.index("PcbLib.SetState_CurrentComponent(Holder)")
    assert body.index("WasCurrent := IsCurrentFootprint(PcbLib, Target)") < swap
    assert swap < body.index("PcbBeginEdit(0)")
    assert swap < body.index("Target.RemovePCBObject(Prim)")


def test_each_removed_primitive_is_freed_inside_its_open_iterator():
    body = _body("Lib_DeleteFootprintPrimitives")
    remove = body.index("Target.RemovePCBObject(Prim)")
    destroy = body.index("PCBServer.DestroyPCBObject(Prim)")
    assert remove < destroy < body.index("Target.GroupIterator_Destroy(GrpIter)", remove)


def test_the_target_is_shown_again_and_a_temporary_holder_removed():
    body = _body("Lib_DeleteFootprintPrimitives")
    tail = body[body.index("PcbEndEdit(0)"):]
    restore = tail.index("PcbLib.SetState_CurrentComponent(Target)")
    assert tail.index("If WasCurrent Then") < restore
    cleanup = tail[restore:]
    for call in ("PcbLib.RemoveComponent(Holder)", "PcbLib.DeRegisterComponent(Holder)",
                 "PCBServer.DestroyPCBLibComp(Holder)"):
        assert call in cleanup, call
    assert '"was_current":' in body


def test_the_holder_is_another_footprint_or_a_temporary_one():
    body = _body("FootprintToHoldFocus")
    # By name: the footprint's I_ObjectAddress faulted (issue #135).
    assert "Footprint.Name <> Target.Name" in body
    assert "PcbLib.RegisterComponent(Result)" in body
    assert "TempMade := True" in body
