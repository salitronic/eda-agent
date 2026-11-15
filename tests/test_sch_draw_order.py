# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""sch_set_draw_order brings matching sheet objects to the front or the back.

MEASURED 2026-11-14 on AD 25.8.1, on a copy of a test sheet: an opaque
rectangle placed over two net labels hid them, because a sheet draws its
objects in container order and a new object goes to the end. Altium's own
Sch:SendObjectToBack / BringObjectToFront wait for a click on the canvas,
and the script engine has no reorder member. Taking a label out of the
sheet's container (UnRegisterSchObjectFromContainer) and registering it
again (RegisterSchObjectInContainer) drew it over the rectangle at once and
kept it over save and reopen, with the object count unchanged. Front moves
the matches; back moves every other object, so the matches end up first.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import generic

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _function(name: str) -> str:
    code = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_the_command_is_dispatched():
    code = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")
    assert re.search(r"'set_draw_order':\s*Result := Gen_SetDrawOrder\(Params, RequestId\);",
                     code)


def test_only_objects_on_the_sheet_itself_are_walked():
    for name in ("Gen_SetDrawOrder", "DrawOrderHolds"):
        body = _function(name)
        assert "SetState_IterationDepth(eIterateFirstLevel)" in body, name


def test_front_moves_the_matches_and_back_moves_the_rest():
    body = _function("Gen_SetDrawOrder")
    assert "If IsMatch <> ToBack Then Movers.Add(O);" in body
    assert "ToBack := (ToStr = 'back');" in body


def test_an_object_is_taken_out_and_put_back_with_no_registration_message():
    body = _function("Gen_SetDrawOrder")
    out = body.index("SchDoc.UnRegisterSchObjectFromContainer(O);")
    back = body.index("SchDoc.RegisterSchObjectInContainer(O);")
    assert out < back
    # Not a new object: a registration message would give Undo a "created"
    # record, and undoing that removes the object.
    assert "SchRegisterObject(" not in body
    assert "SchDoc.RemoveSchObject" not in body


def test_nothing_moves_without_a_match_and_a_library_is_refused():
    body = _function("Gen_SetDrawOrder")
    edit = body.index("SchBeginEdit(SchDoc")
    assert "If Matched > 0 Then" in body[:edit]
    assert body.index("SchDoc.ObjectId = eSchLib") < body.index("TInterfaceList.Create")
    assert "Movers.Free" not in body


def test_verified_needs_a_match_the_order_read_back_and_the_count_kept():
    body = _function("Gen_SetDrawOrder")
    assert "BoolToJsonStr((Matched > 0) And Holds And (Err = ''))" in body
    assert "(AfterMatched <> Matched) Or (AfterTotal <> Total)" in body


def _holds(order: list[bool], to_back: bool) -> bool:
    """A transcript of DrawOrderHolds over a list of match flags."""
    seen_other = after = False
    matched = 0
    for is_match in order:
        if is_match:
            matched += 1
            if to_back and seen_other:
                after = True
        else:
            if not to_back and matched > 0:
                after = True
            seen_other = True
    return not after


def test_the_read_back_rule_matches_the_transcript():
    body = _function("DrawOrderHolds")
    assert "If ToBack And SeenOther Then SeenMatchAfterOther := True;" in body
    assert "If (Not ToBack) And (Matched > 0) Then SeenMatchAfterOther := True;" in body
    assert _holds([False, False, True, True], to_back=False)
    assert not _holds([False, True, False, True], to_back=False)
    assert _holds([True, True, False], to_back=True)
    assert not _holds([True, False, True], to_back=True)


class _Bridge:
    def __init__(self, log):
        self.log = log

    async def send_command_async(self, command, params=None, timeout=None):
        self.log.append((command, dict(params or {})))
        return {"success": True}


def _tool(monkeypatch, log):
    monkeypatch.setattr(generic, "get_bridge", lambda: _Bridge(log))
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return tools["sch_set_draw_order"]


def test_the_tool_sends_type_filter_and_side(monkeypatch):
    log = []
    tool = _tool(monkeypatch, log)
    asyncio.run(tool(object_type="eBlanket", to="back"))
    asyncio.run(tool(object_type="eNetLabel", filter="Text=SWCLK"))
    assert log[0] == ("generic.set_draw_order",
                      {"object_type": "eBlanket", "filter": "", "to": "back"})
    assert log[1][1]["to"] == "front"
    assert log[1][1]["filter"] == "Text=SWCLK"


def test_a_bad_side_or_filter_is_refused_before_altium(monkeypatch):
    log = []
    tool = _tool(monkeypatch, log)
    bad_side = asyncio.run(tool(object_type="eBlanket", to="top"))
    bad_filter = asyncio.run(tool(object_type="eBlanket", filter="Text"))
    assert bad_side["success"] is False
    assert "Nothing was sent" in bad_filter["error"]
    assert bad_filter["matched"] == 0
    assert log == []
