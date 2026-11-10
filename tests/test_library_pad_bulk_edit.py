# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""set_pad_geometry changes many pads of one footprint in one call.

get_pad_geometry names each pad by its object address (id). A row of
set_pad_geometry names its pad by that id, guards it with the position the
read gave, and carries any field set_pad_properties takes. Every row is
checked before anything is written; then one walk writes each pad once.
Matching by id, never by name, is what lets a renumbering table (3 -> 5,
5 -> 7) run without temporary names. Both writers go through the same
per-pad helpers, so a field behaves the same in either.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import library as library_tools

from tests.pascal_source import load

_LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _lib():
    return load(_LIBRARY)


def _bulk():
    return _lib()["Lib_SetPadGeometry"]


def test_the_pad_read_names_each_pad_by_its_address():
    body = _lib()["Lib_GetPadGeometry"]
    assert "Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;" in body
    assert "',\"id\":\"' + PadAddr + '\"' +" in body


def test_the_bulk_writer_is_dispatched():
    text = _LIBRARY.read_text(encoding="utf-8", errors="replace")
    assert "'set_pad_geometry':     Result := Lib_SetPadGeometry(Params, RequestId);" in text


def test_every_row_is_checked_before_anything_is_written():
    body = _bulk()
    checked = body.index("Op := PadEditProblem(Op, True, Code);")
    refused = body.index("'ROWS_REFUSED'")
    edit = body.index("PcbBeginEdit(0);")
    assert checked < refused < edit
    assert "If (Str = '') Or (Ids.IndexOf(Str) >= 0) Then" in body
    assert "If Hits.Strings[I] = '0' Then" in body
    assert "If (Abs(WantX - HaveX) > 0.0005) Or (Abs(WantY - HaveY) > 0.0005) Then" in body


def test_a_row_finds_its_pad_by_id_never_by_name():
    body = _bulk()
    write = body[body.index("PcbBeginEdit(0);"):]
    assert "Idx := Ids.IndexOf(PadAddr);" in write
    assert "Pad.Name" not in body


def test_each_pad_is_written_once_and_a_repeat_is_counted():
    body = _bulk()
    write = body[body.index("PcbBeginEdit(0);"):]
    assert write.index("If Done.IndexOf(PadAddr) >= 0 Then") < write.index("Inc(Repeats)")
    assert write.index("Done.Add(PadAddr);") < write.index("ApplyPadEdit(")
    assert "',\"repeats\":' + IntToStr(Repeats)" in body


def test_both_walks_are_filtered_to_pads():
    assert _bulk().count("GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));") == 2


def test_both_writers_share_one_pad_edit():
    single, bulk = _lib()["Lib_SetPadProperties"], _bulk()
    assert "ApplyPadEdit(Pad, Params, False, XOrg, YOrg, UnitsStr," in single
    assert "ApplyPadEdit(Pad, Rows.Strings[Idx], True, XOrg, YOrg," in bulk
    assert "PadEditJson(Pad, XOrg, YOrg, Ok)" in single
    assert "Rec := PadEditJson(Pad, XOrg, YOrg, Ok);" in bulk
    for body in (single, bulk):
        assert "Pad.TopXSize :=" not in body
        assert "Pad.Name :=" not in body
    assert "Pad.TopXSize :=" in _lib()["ApplyPadEdit"]


def test_a_row_is_read_as_a_batch_op_and_the_request_as_json():
    arg = _lib()["PadArg"]
    assert "If IsOp Then Result := GetBatchField(Src, Key)" in arg
    assert "Else Result := ExtractJsonValue(Src, Key);" in arg


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(library_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    library_tools.register_library_tools(mcp)
    out = asyncio.run(mcp.tools["lib_set_pad_geometry"](**kw))
    return out, bridge.sent


def test_rows_reach_the_script_as_one_batch(monkeypatch):
    _, sent = _call(monkeypatch, footprint_name="F", units="mm", rows=[
        {"id": "101", "x_mm": -1.27, "y_mm": 2.7, "new_name": "5"},
        {"id": "102", "x": 1.5, "plated": False, "x_size": 0.6},
    ])
    assert sent[0][0] == "library.set_pad_geometry"
    params = sent[0][1]
    assert params["units"] == "mm"
    ops = params["rows"].split("~~")
    assert ops[0] == "id=101;x_mm=-1.27;y_mm=2.7;new_name=5"
    assert ops[1] == "id=102;x=1.5;plated=false;x_size=0.6"


def test_a_value_cannot_split_its_row(monkeypatch):
    _, sent = _call(monkeypatch, footprint_name="F",
                    rows=[{"id": "101", "new_name": "A;B~~C=1"}])
    ops = sent[0][1]["rows"].split("~~")
    assert len(ops) == 1
    fields = ops[0].split(";")
    assert fields[0] == "id=101" and len(fields) == 2


def test_bad_rows_are_refused_before_sending(monkeypatch):
    for rows in ([], [{"x_size": 1}], [{"id": "1", "colour": "red"}]):
        out, sent = _call(monkeypatch, footprint_name="F", rows=rows)
        assert out["success"] is False and not sent, rows
