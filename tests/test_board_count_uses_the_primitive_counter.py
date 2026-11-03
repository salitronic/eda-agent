# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""An unfiltered board count comes from Altium's own counter.

obj_count on a PCB ran the query handler, which builds the JSON of every
matching object only to report how many there were: about 6 s for the
8722 tracks of one board. IPCB_Board.GetPrimitiveCounter answers the same
number at once (checked equal to a full board walk for tracks, pads,
vias, polygons, components and texts). A filter still needs every object
looked at, so that keeps the query path, and so does a counter that
raises or gives a negative number.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALT = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic() -> dict[str, str]:
    return load(_ALT / "Generic.pas")


def _pcb_branch() -> str:
    body = _generic()["Gen_GetObjectCount"]
    start = body.index("ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);")
    return body[start:body.index("MatchCount := 0;", start)]


def test_the_counter_is_read_through_typed_locals():
    body = _generic()["PCBCountFromCounter"]
    assert re.search(r"Counter\s*:\s*IPCB_PrimitiveCounter;", body)
    assert re.search(r"Counter := Board\.GetPrimitiveCounter;", body)
    assert re.search(r"N := Counter\.GetObjectCount\(ObjTypeInt\);", body)
    # Chain nothing: the counter is never reached through the board in
    # one expression.
    assert "Board.GetPrimitiveCounter.GetObjectCount" not in body


def test_the_board_is_resolved_like_the_query_path():
    body = _generic()["PCBCountFromCounter"]
    assert "Board := GetPCBBoardAnywhere(0);" in body
    miss = body.index("If Board = Nil Then")
    assert body.index("'NO_PCB', 'No PCB document is active'", miss) > miss


def test_the_reply_carries_the_count_and_says_how():
    body = _generic()["PCBCountFromCounter"]
    reply = body[body.index("BuildSuccessResponse"):]
    assert "'{\"count\":' + IntToStr(N)" in reply
    assert '"counted_by":"counter"' in reply


def test_a_counter_that_fails_falls_back():
    body = _generic()["PCBCountFromCounter"]
    assert re.search(r"Try\s+Counter := Board\.GetPrimitiveCounter;", body)
    assert re.search(r"Except\s+N := -1;\s+End;", body)
    # A negative number answers nothing: the caller gets '' and queries.
    assert re.search(r"If N < 0 Then Exit;", body)
    assert body.index("Result := '';") < body.index("If N < 0 Then Exit;")
    branch = _pcb_branch()
    call = branch.index("Result := PCBCountFromCounter(ObjTypeInt, RequestId);")
    assert branch.index("If Result <> '' Then Exit;", call) > call
    assert branch.index("ProcessActivePCBDoc(", call) > call


def test_only_an_empty_filter_uses_the_counter():
    branch = _pcb_branch()
    gate = branch.index("If FilterStr = '' Then")
    call = branch.index("PCBCountFromCounter(", gate)
    end = branch.index("End;", gate)
    assert gate < call < end
    # The query path is still there, after the gate, with the filter.
    query = branch.index(
        "Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', '', 'query', "
        "RequestId, 0);")
    assert query > end
