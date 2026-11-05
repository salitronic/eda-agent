# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A delete that stops part way reports what it already removed.

obj_batch_delete answered deleted 0 and a failure while 25 net labels
were already gone (issue #94). The removal loops had no Try around the
remove, so one raise ended the request as an error and the count went
with it, and the batch read "matched" only from a reply that succeeded.
A caller reading "nothing changed" repeats a delete that happened.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALT = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _delete_branch(body: str) -> str:
    start = body.index("If Mode = 'delete' Then")
    return body[start:body.index("If Mode = 'modify' Then", start)]


def test_the_schematic_loop_stops_and_says_why():
    loop = _delete_branch(load(_ALT / "Generic.pas")["ProcessSchDocObjects"])
    m = re.search(r"Removed := True;\s+Try\s+SchDoc\.RemoveSchObject\(FoundObj\);"
                  r"\s+Except\s+Removed := False;\s+End;", loop)
    assert m, "the plain remove is not inside a Try that records the failure"
    remove = m.end()
    assert loop.index("NoteDeleteFault('removing an object raised", remove) > remove
    # Counted only after a remove that did not raise.
    assert loop.rindex("Inc(TotalMatched);") > loop.index("NoteDeleteFault('removing")


def test_a_remove_that_does_nothing_is_not_counted_again():
    """The loop finds the next match from scratch. If a remove left the
    object there, the same object came back and was counted up to 100000
    times."""
    loop = _delete_branch(load(_ALT / "Generic.pas")["ProcessSchDocObjects"])
    same = loop.index("(ThisAddr = LastAddr)")
    assert loop.index("Dec(TotalMatched);", same) < loop.index("Break;", same)


def test_the_board_loop_stops_and_says_why():
    loop = _delete_branch(
        load(_ALT / "PCBGeneric.pas", minimum=20)["ProcessPCBBoardObjects"])
    remove = loop.index("Board.RemovePCBObject(FoundObj);")
    assert loop.index("Except", remove) < loop.index("NoteDeleteFault(", remove)
    assert loop.index("NoteDeleteFault(", remove) < loop.index("Inc(TotalMatched);", remove)


def test_the_reply_marks_a_partial_delete():
    body = load(_ALT / "Utils.pas")["ModifyOutcomeJson"]
    assert '"partial":true' in body
    assert body.index("_DeleteFaultStr") < body.index("_PropertyDiagStr <> ''")


def test_the_fault_is_cleared_per_request():
    assert "_DeleteFaultStr := '';" in load(_ALT / "Utils.pas")["ResetPropertyDiag"]
    assert "ResetPropertyDiag(0);" in load(_ALT / "Generic.pas")["Gen_DeleteObjects"]


def test_the_batch_counts_a_failed_operation_too():
    body = load(_ALT / "Generic.pas")["Gen_BatchDelete"]
    read = body.index("OpMatched := StrToIntDef(JsonValueAnywhere(OpResult, 'matched'), 0);")
    assert read < body.index("Pos('\"success\":false', OpResult)"), (
        "matched is read only on success, so a partial delete counts 0")
    assert "'partial: '" in body
    assert '"operations_partial":' in body
    # Each operation starts with a clean fault.
    loop = body[body.index("While True Do"):]
    assert loop.index("ResetPropertyDiag(0);") < loop.index("ProcessActiveDoc(")
