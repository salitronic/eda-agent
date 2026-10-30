# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Placed parts can be pointed at another library without Altium's dialog.

The link is several properties that must change together (source library,
symbol, design item, database table and flag, footprint model and its
library); one written alone is the Not Found state. sch_relink_components
checks the target first, writes them together and reads each back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import generic as generic_tools

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _body():
    return load(_GENERIC)["Gen_RelinkComponents"]


def test_the_symbol_is_checked_before_anything_is_written():
    body = _body()
    check = body.index("If Not ResolveLibRef(LibPath, LibRef, Avail) Then")
    assert check < body.index("SchBeginEdit(")
    assert "'LIB_REF_NOT_FOUND'" in body


def test_a_named_sheet_that_is_not_open_is_refused_not_swapped():
    body = _body()
    assert "'SHEET_NOT_OPEN'" in body
    refusal = body.index("'SHEET_NOT_OPEN'")
    assert refusal < body.index("SchDoc := SchServer.GetCurrentSchDocument;")


def test_every_written_field_is_read_back():
    body = _body()
    for field in ('"source_library"', '"lib_ref"', '"database_table"',
                  '"use_db_table_name"', '"design_item_id"', '"footprint"',
                  '"footprint_library"'):
        assert f"Missing := Missing + ',{field}'" in body, field


def test_library_identifier_is_reported_never_written():
    assert "Comp.LibraryIdentifier :=" not in _body()
    assert '"library_identifier"' in load(_GENERIC)["CompLinkJson"]


def test_a_dry_run_writes_nothing():
    body = _body()
    assert "If Not DryRun Then SchBeginEdit(" in body
    assert "If Not DryRun Then\n                        Begin" in body \
        or "If Not DryRun Then\r\n                        Begin" in body


def test_a_footprint_link_names_a_file_never_a_full_path():
    """A full .PcbLib path in a datafile link wedged AD26."""
    body = _body()
    assert "FpLibName := ExtractFileName(FpLib);" in body
    assert "SetPlacedFootprintLibrary(Comp, FpLibName)" in body
    assert "AddPlacedFootprintModel(Comp, FpName, FpLibName)" in body
    assert "SetPlacedFootprintLibrary(Comp, FpLib)" not in body


def test_a_part_with_no_footprint_model_gets_one():
    body = _body()
    added = body.index("AddPlacedFootprintModel(Comp, FpName, FpLibName)")
    assert body.index("If CurrentFootprintImpl(Comp) = Nil Then") < added
    assert "Comp.AddSchImplementation" in load(_GENERIC)["AddPlacedFootprintModel"]


def test_the_command_is_routed():
    assert "'relink_components': Result := Gen_RelinkComponents(Params, " \
        "RequestId);" in _GENERIC.read_text(encoding="utf-8")


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self, row=None):
        self.sent = []
        self.row = row

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "library.get_dblib_record":
            return self.row
        names = (params or {}).get("designators", "").split(",")
        return {"success": True, "matched": len(names), "changed": len(names),
                "failed": 0, "parts": [{"designator": n} for n in names]}


def _call(monkeypatch, bridge, **kw):
    monkeypatch.setattr(generic_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    generic_tools.register_generic_tools(mcp)
    return asyncio.run(mcp.tools["sch_relink_components"](**kw))


def test_a_database_row_fills_the_symbol_and_footprint(monkeypatch):
    # As the handler answers: one row, the whole table read.
    bridge = _Bridge(row={"found": True, "match_count": 1, "scan_capped": False,
                          "symbol_ref": "RES",
                          "symbol_library": "Passives.SchLib",
                          "footprints": [{"ref": "0603",
                                          "library": "Passives.PcbLib"}]})
    out = _call(monkeypatch, bridge, designators=["R1", "R2"],
                library_path=r"C:\libs\Parts.DbLib", dblib_table="Resistors",
                dblib_key="RC0603-10K")
    sent = dict(bridge.sent)["generic.relink_components"]
    assert sent["kind"] == "dblib"
    assert sent["design_item_id"] == "RC0603-10K"
    assert (sent["lib_ref"], sent["footprint"], sent["footprint_library"]) \
        == ("RES", "0603", "Passives.PcbLib")
    assert out["success"] and out["changed"] == 2 and out["not_found"] == []


def test_a_missing_database_row_changes_nothing(monkeypatch):
    bridge = _Bridge(row={"found": False})
    out = _call(monkeypatch, bridge, designators=["R1"],
                library_path=r"C:\libs\Parts.DbLib", dblib_table="Resistors",
                dblib_key="NOPE")
    assert out["error"] == "DBLIB_ROW_NOT_FOUND"
    assert [c for c, _ in bridge.sent] == ["library.get_dblib_record"]


def test_bad_arguments_are_refused_before_sending(monkeypatch):
    bridge = _Bridge()
    assert _call(monkeypatch, bridge, designators=[])["error"] == \
        "MISSING_PARAM"
    assert _call(monkeypatch, bridge, designators=["R1"],
                 library_path="x.PcbLib")["error"] == "BAD_PARAM"
    assert _call(monkeypatch, bridge, designators=["R1"],
                 library_path="x.DbLib")["error"] == "MISSING_PARAM"
    assert _call(monkeypatch, bridge, designators=["R1,R2"])["error"] == \
        "BAD_PARAM"
    assert bridge.sent == []
