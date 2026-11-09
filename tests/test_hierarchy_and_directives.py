# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Hierarchy reading and the directive, No ERC, compile-mask and Repeat
arguments: what each tool sends, what it refuses, and what
proj_get_hierarchy reports as not joining up."""

from __future__ import annotations

import asyncio

from eda_agent.tools import generic, project
from eda_agent.tools.project import hierarchy_problems


class _Bridge:
    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply or {"success": True}

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return self.reply


def _call(module, register, monkeypatch, tool, reply=None, **kwargs):
    bridge = _Bridge(reply)
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    getattr(module, register)(_Mcp())
    return asyncio.run(tools[tool](**kwargs)), bridge.sent


def _gen(monkeypatch, tool, **kwargs):
    return _call(generic, "register_generic_tools", monkeypatch, tool, **kwargs)


# Sheets as the bridge returns them: a top with one symbol for PSU.SchDoc.
def _sheets(entries, ports, child_sheets=1, extra=()):
    return [
        {"file": "Top.SchDoc", "parents": [], "ports": [], "sheet_symbols": [
            {"sheet_name": "PSU", "file": "PSU.SchDoc",
             "child_sheets": child_sheets, "entries": entries}]},
        {"file": "PSU.SchDoc", "parents": ["Top.SchDoc"], "ports": ports,
         "sheet_symbols": []},
        *extra]


def _e(name, io="Input"):
    return {"name": name, "net": name, "io": io}


def test_a_hierarchy_that_joins_up_has_no_problems():
    sheets = _sheets([_e("VIN"), _e("EN", "Output")], [_e("vin"), _e("EN", "Output")])
    assert hierarchy_problems(sheets, "Top.SchDoc") == []


def test_each_kind_of_break_is_named():
    sheets = _sheets([_e("VIN"), _e("PG", "Input"), _e("SPARE")],
                     [_e("VIN"), _e("PG", "Output"), _e("FB")],
                     extra=[{"file": "Old.SchDoc", "parents": [], "ports": [],
                             "sheet_symbols": []}])
    kinds = {(p["kind"], p.get("name", p.get("sheet"))) for p in
             hierarchy_problems(sheets, "Top.SchDoc")}
    assert kinds == {("entry_without_port", "SPARE"),
                     ("port_without_entry", "FB"),
                     ("direction_conflict", "PG"),
                     ("orphan_sheet", "Old.SchDoc")}


def test_a_symbol_whose_file_is_not_in_the_project():
    sheets = _sheets([_e("VIN")], [_e("VIN")], child_sheets=0)
    assert [p["kind"] for p in hierarchy_problems(sheets, "Top.SchDoc")] == ["missing_child"]


def test_repeat_and_undirected_entries_do_not_count_as_breaks():
    # Repeat(X) joins port X; bidirectional or passive never conflicts.
    sheets = _sheets([_e("Repeat(DIN)"), _e("SDA", "Bidirectional")],
                     [_e("DIN"), _e("SDA", "Input")])
    assert hierarchy_problems(sheets, "Top.SchDoc") == []


def test_later_channels_of_a_repeated_symbol_are_not_breaks():
    # As Altium 26 compiles Repeat(CH,1,3): CH1 has every entry, CH2 and
    # CH3 only the Repeat() ones. A real break on CH1 is still named.
    full = [_e("Repeat(DIN)"), _e("DOUT", "Output"), _e("SPARE")]
    sheets = [
        {"file": "Top.SchDoc", "parents": [], "ports": [], "sheet_symbols": [
            {"sheet_name": "CH1", "file": "Chan.SchDoc", "child_sheets": 1, "entries": full},
            *({"sheet_name": f"CH{i}", "file": "Chan.SchDoc", "child_sheets": 1,
               "entries": [_e("Repeat(DIN)")]} for i in (2, 3))]},
        {"file": "Chan.SchDoc", "parents": ["Top.SchDoc"] * 3,
         "ports": [_e("DIN"), _e("DOUT", "Output")], "sheet_symbols": []}]
    assert [(p["kind"], p["sheet_symbol"]) for p in hierarchy_problems(sheets, "Top.SchDoc")] \
        == [("entry_without_port", "CH1")]


def test_the_tool_adds_problems_to_the_reply(monkeypatch):
    reply = {"top": "Top.SchDoc",
             "sheets": _sheets([_e("VIN"), _e("X")], [_e("VIN")])}
    out, sent = _call(project, "register_project_tools", monkeypatch,
                      "proj_get_hierarchy", reply=reply)
    assert sent == [("project.get_hierarchy", {})]
    assert [p["kind"] for p in out["problems"]] == ["entry_without_port"]


def test_repeat_names_the_symbol_and_the_entry(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_place_sheet_symbol", x1=0, y1=0, x2=1000,
                   y2=800, sheet_file_name="Chan.SchDoc", sheet_name="CH",
                   repeat_first=1, repeat_last=4)
    assert sent[0][1]["sheet_name"] == "Repeat(CH,1,4)"
    _, sent = _gen(monkeypatch, "sch_place_sheet_entry", sheet_name="CH",
                   entry_name="DIN", repeat=True)
    assert sent[0][1]["entry_name"] == "Repeat(DIN)"
    out, sent = _gen(monkeypatch, "sch_place_sheet_symbol", x1=0, y1=0, x2=1,
                     y2=1, sheet_file_name="Chan.SchDoc", repeat_first=3,
                     repeat_last=1)
    assert sent == [] and out["ok"] is False


def test_a_directive_carries_several_parameters(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_add_directive", x=100, y=200,
                   parameters={"NetClass": "PWR", "Width": "20"},
                   style=1, orientation=2)
    cmd, args = sent[0]
    assert cmd == "generic.place_directive"
    assert args["parameters"] == "NetClass=PWR|Width=20"
    assert (args["style"], args["orientation"]) == ("1", "2")
    # One parameter the old way still goes through.
    _, sent = _gen(monkeypatch, "sch_add_directive", x=1, y=2,
                   param_name="DifferentialPair", param_value="USB")
    assert sent[0][1]["parameters"] == "DifferentialPair=USB"
    for bad in ({"parameters": {"A|B": "1"}}, {}, {"param_name": "N",
                                                   "orientation": 5}):
        out, sent = _gen(monkeypatch, "sch_add_directive", x=1, y=2, **bad)
        assert sent == [] and out["ok"] is False


def test_no_erc_options_are_sent_only_when_given(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_place_no_erc", x=10, y=20)
    assert sent == [("generic.place_no_erc", {"x": "10", "y": "20",
                                              "error_kinds": "",
                                              "connection_pairs": ""})]
    _, sent = _gen(monkeypatch, "sch_place_no_erc", x=10, y=20, symbol=3,
                   orientation=1, suppress_all=False, active=True)
    args = sent[0][1]
    assert (args["symbol"], args["orientation"], args["suppress_all"],
            args["active"]) == ("3", "1", "false", "true")


def test_compile_mask_sends_its_box(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_place_compile_mask", x1=500, y1=400,
                   x2=100, y2=900, collapsed=True)
    assert sent == [("generic.place_compile_mask",
                     {"x1": "500", "y1": "400", "x2": "100", "y2": "900",
                      "collapsed": "true"})]


def test_harness_members_match_their_harness_port():
    """The compiled entries of a signal harness come one per member; the
    child sheet offers the harness port. Neither side is missing."""
    from eda_agent.tools.project import hierarchy_problems
    sheets = [
        {"file": "Top.SchDoc", "parents": [], "ports": [], "sheet_symbols": [
            {"sheet_name": "U_Hub", "file": "Hub.SchDoc", "child_sheets": 1,
             "entries": [{"name": "EXT_USB.USB+"}, {"name": "EXT_USB.USB-"},
                         {"name": "I2C"}, {"name": "LOST.SCL"}]}]},
        {"file": "Hub.SchDoc", "parents": ["Top.SchDoc"],
         "ports": [{"name": "EXT_USB"}, {"name": "I2C.SDA"}, {"name": "SPARE"}],
         "sheet_symbols": []},
    ]
    found = {(p["kind"], p.get("name")) for p in hierarchy_problems(sheets, "Top.SchDoc")}
    assert found == {("entry_without_port", "LOST.SCL"),
                     ("port_without_entry", "SPARE")}
