# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Compiled sheet entries and power objects carry the name of the net at
them. proj_get_hierarchy and proj_get_net_items put the object's own name,
read off the sheet, in its place."""

from __future__ import annotations

import asyncio

from eda_agent.tools import project
from eda_agent.tools.project import hierarchy_problems, name_entries, own_texts

TOP = r"C:\d\Top.SchDoc"


class _Bridge:
    """Answers each command with its own reply; records what was sent."""

    def __init__(self, replies):
        self.sent = []
        self.replies = replies

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        reply = self.replies.get((command, params.get("object_type")),
                                 self.replies.get(command, {"success": True}))
        return reply


def _run(module, register, monkeypatch, tool, replies, **kwargs):
    bridge = _Bridge(replies)
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


def _hierarchy():
    """U_CHILD's entries ALERT and SYS, wired on Top to nets CHG_INT_L and
    V_SYS_TOP: compiled, they read as those nets."""
    return [
        {"file": "Top.SchDoc", "parents": [], "ports": [], "sheet_symbols": [
            {"sheet_name": "U_CHILD", "file": "Child.SchDoc", "child_sheets": 1,
             "entries": [{"name": "CHG_INT_L", "net": "CHG_INT_L", "io": "Input",
                          "x": 2000, "y": 2900},
                         {"name": "V_SYS_TOP", "net": "V_SYS_TOP", "io": "Input",
                          "x": 2000, "y": 2800}]}]},
        {"file": "Child.SchDoc", "parents": ["Top.SchDoc"], "sheet_symbols": [],
         "ports": [{"name": "ALERT", "io": "Input"}, {"name": "SYS", "io": "Input"}]},
    ]


PLACED_ENTRIES = [{"_doc": TOP, "Name": "ALERT", "Location.X": "2000", "Location.Y": "2900"},
                  {"_doc": TOP, "Name": "SYS", "Location.X": "2000", "Location.Y": "2800"}]


def test_entries_named_after_their_nets_read_as_broken():
    """Without the entries' own names the hierarchy is all mismatches."""
    kinds = sorted(p["kind"] for p in hierarchy_problems(_hierarchy(), "Top.SchDoc"))
    assert kinds == ["entry_without_port"] * 2 + ["port_without_entry"] * 2


def test_an_entry_takes_its_own_name_and_keeps_its_net():
    sheets = _hierarchy()
    assert name_entries(sheets, PLACED_ENTRIES) == 2
    entries = sheets[0]["sheet_symbols"][0]["entries"]
    assert [(e["name"], e["net"]) for e in entries] == [("ALERT", "CHG_INT_L"),
                                                        ("SYS", "V_SYS_TOP")]
    assert hierarchy_problems(sheets, "Top.SchDoc") == []


def test_an_entry_is_matched_by_sheet_and_place():
    """An entry of the same place on another sheet, or another place on
    the same sheet, lends no name."""
    sheets = _hierarchy()
    other = [{"_doc": r"C:\d\Other.SchDoc", "Name": "ALERT", "Location.X": "2000",
              "Location.Y": "2900"},
             {"_doc": TOP, "Name": "SYS", "Location.X": "2000", "Location.Y": "2700"}]
    assert name_entries(sheets, other) == 0
    assert sheets[0]["sheet_symbols"][0]["entries"][0]["name"] == "CHG_INT_L"


def test_a_real_break_is_still_named():
    sheets = _hierarchy()
    sheets[0]["sheet_symbols"][0]["entries"].append(
        {"name": "NETX", "net": "NETX", "io": "Input", "x": 2000, "y": 2500})
    placed = PLACED_ENTRIES + [{"_doc": TOP, "Name": "BOGUS", "Location.X": "2000",
                                "Location.Y": "2500"}]
    name_entries(sheets, placed)
    assert [(p["kind"], p["name"]) for p in hierarchy_problems(sheets, "Top.SchDoc")] \
        == [("entry_without_port", "BOGUS")]


def test_the_hierarchy_tool_reads_the_entries_of_the_project_it_was_given(monkeypatch):
    prj = r"C:\d\R.PrjPcb"
    out, sent = _run(project, "register_project_tools", monkeypatch, "proj_get_hierarchy",
                     {"project.get_hierarchy": {"top": "Top.SchDoc", "sheets": _hierarchy()},
                      "generic.query_objects": {"objects": PLACED_ENTRIES}},
                     project_path=prj)
    assert sent[0] == ("project.get_hierarchy", {"project_path": prj})
    command, params = sent[1]
    assert command == "generic.query_objects" and params["object_type"] == "eSheetEntry"
    assert params["scope"] == {"type": "project", "file_path": prj}
    assert out["problems"] == []


def test_entries_keep_the_net_names_when_their_own_cannot_be_read(monkeypatch):
    out, _ = _run(project, "register_project_tools", monkeypatch, "proj_get_hierarchy",
                  {"project.get_hierarchy": {"top": "Top.SchDoc", "sheets": _hierarchy()},
                   "generic.query_objects": {"success": False, "error": "x"}})
    assert "could not be read" in out["note"]
    assert len(out["problems"]) == 4


def _nets():
    return [{"net": "3V3_UF",
             "net_labels": [{"text": "3V3_UF", "sheet": "Top.SchDoc", "x": 3500, "y": 1000}],
             "power_objects": [{"text": "3V3_UF", "sheet": "Top.SchDoc", "x": 3000, "y": 1000},
                               {"text": "3V3_UF", "sheet": "Top.SchDoc", "x": 4200, "y": 1000}],
             "sheet_entries": [], "ports": []}]


POWER = [{"_doc": TOP, "Text": "+3V3_AON", "Location.X": "3000", "Location.Y": "1000"},
         {"_doc": TOP, "Text": "+3V3_AON", "Location.X": "4200", "Location.Y": "1000"}]


def test_a_power_port_shows_its_own_text_beside_the_net_it_joins():
    nets = _nets()
    assert own_texts(nets, "power_objects", POWER) == 2
    assert [p["text"] for p in nets[0]["power_objects"]] == ["+3V3_AON", "+3V3_AON"]
    assert nets[0]["net"] == "3V3_UF"


def test_net_items_reads_only_the_kinds_the_reply_holds(monkeypatch):
    out, sent = _run(project, "register_project_tools", monkeypatch, "proj_get_net_items",
                     {"project.get_net_items": {"nets": _nets()},
                      ("generic.query_objects", "ePowerObject"): {"objects": POWER}},
                     net="3V3_UF")
    assert [p["object_type"] for c, p in sent if c == "generic.query_objects"] \
        == ["ePowerObject"]
    assert out["nets"][0]["power_objects"][0]["text"] == "+3V3_AON"


def test_a_sheet_entry_in_net_items_takes_its_own_name(monkeypatch):
    nets = [{"net": "CHG_INT_L", "power_objects": [],
             "sheet_entries": [{"text": "CHG_INT_L", "sheet": "Top.SchDoc",
                                "x": 2000, "y": 2900}]}]
    out, _ = _run(project, "register_project_tools", monkeypatch, "proj_get_net_items",
                  {"project.get_net_items": {"nets": nets},
                   ("generic.query_objects", "eSheetEntry"): {"objects": PLACED_ENTRIES}})
    assert out["nets"][0]["sheet_entries"][0]["text"] == "ALERT"
