# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Directive and blanket parameters given as a list: a Rule
parameter, a name that repeats, the directive's own name and a hidden
parameter, sent as indexed fields so a value may hold '=' and '|'."""

from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import generic
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
SEP = "|"
FILE_SEP = "\u00a6"


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _gen(monkeypatch, tool, **kwargs):
    bridge = _Bridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
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
    return asyncio.run(tools[tool](**kwargs)), bridge.sent


RULE = {"RULEKIND": "Length", "NETSCOPE": "AnyNet", "MAXLIMIT": "500mil"}


def test_a_rule_and_repeated_names_go_as_indexed_fields(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_add_directive", x=100, y=200, name="DIFFPAIR",
                   parameter_list=[
                       {"name": "ClassName", "value": "PWR"},
                       {"name": "ClassName", "value": "DIFF-100", "hidden": True},
                       {"rule": RULE},
                       {"name": "Note", "value": "a=b|c"}])
    cmd, args = sent[0]
    assert cmd == "generic.place_directive"
    assert args["param_count"] == "4"
    assert args["set_name"] == "DIFFPAIR"
    assert [args[f"param_name_{i}"] for i in range(4)] == \
        ["ClassName", "ClassName", "Rule", "Note"]
    assert args["param_value_2"] == SEP.join(
        ["RULEKIND=Length", "NETSCOPE=AnyNet", "MAXLIMIT=500mil"])
    assert args["param_value_3"] == "a=b|c"
    assert args["param_hidden_1"] == "true"
    assert "param_hidden_0" not in args and "param_hidden_2" not in args
    assert args["parameters"] == ""


def test_rule_field_names_are_upper_cased(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_add_directive", x=0, y=0,
                   parameter_list=[{"rule": {"rulekind": "Clearance", "gap": "5mil"}}])
    assert sent[0][1]["param_value_0"] == f"RULEKIND=Clearance{SEP}GAP=5mil"


def test_bad_lists_send_nothing(monkeypatch):
    bad = [
        {"parameter_list": []},
        {"parameter_list": [{"value": "x"}]},
        {"parameter_list": [{"rule": {"GAP": "5mil"}}]},
        {"parameter_list": [{"rule": {"RULEKIND": "Length", "rulekind": "Width"}}]},
        {"parameter_list": [{"rule": {"RULEKIND": f"a{SEP}b"}}]},
        {"parameter_list": [{"rule": {"RULEKIND": f"a{FILE_SEP}b"}}]},
        {"parameter_list": [{"rule": RULE, "value": "x"}]},
        {"parameter_list": [{"name": "A", "value": "1"}], "parameters": {"B": "2"}},
        {"parameter_list": [{"name": "A", "value": "1"}], "name": " "},
        {"parameter_list": [{"name": f"P{i}", "value": ""} for i in range(65)]},
    ]
    for kwargs in bad:
        out, sent = _gen(monkeypatch, "sch_add_directive", x=1, y=2, **kwargs)
        assert sent == [] and out["ok"] is False, kwargs


def test_the_old_arguments_still_work_and_still_refuse(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_add_directive", x=1, y=2,
                   parameters={"NetClass": "PWR"}, name="PWR CLASS")
    args = sent[0][1]
    assert args["parameters"] == "NetClass=PWR" and args["set_name"] == "PWR CLASS"
    assert "param_count" not in args
    out, sent = _gen(monkeypatch, "sch_add_directive", x=1, y=2,
                     parameters={"Rule": "RULEKIND=Length"})
    assert sent == [] and "parameter_list" in out["reason"]


def test_a_blanket_takes_the_list_too(monkeypatch):
    _, sent = _gen(monkeypatch, "sch_place_blanket",
                   vertices=[[0, 0], [100, 0], [100, 100]], name="RULES",
                   parameter_list=[{"rule": RULE}, {"rule": {"RULEKIND": "Width"}}])
    args = sent[0][1]
    assert args["param_count"] == "2" and args["set_name"] == "RULES"
    assert args["param_name_0"] == args["param_name_1"] == "Rule"
    for kwargs in ({"parameter_list": [{"rule": {}}]},
                   {"parameter_list": [{"name": "A"}], "parameters": {"B": "1"}},
                   {"name": "LONELY"}):
        out, sent = _gen(monkeypatch, "sch_place_blanket",
                         vertices=[[0, 0], [100, 0], [100, 100]], **kwargs)
        assert sent == [] and out["success"] is False, kwargs


def test_the_script_reads_the_indexed_fields_and_reports_hidden():
    funcs = load(_ALTIUM / "Generic.pas", minimum=20)
    body = funcs["NewParamSetFrom"]
    for key in ("'param_count'", "'set_name'", "'param_name_'", "'param_value_'",
                "'param_hidden_'"):
        assert key in body, key
    # The value is written whole, never split on '|' or '='.
    assert "Param.Text := PValue;" in body
    assert "Param.IsHidden := True" in body
    assert "PSet.Name := SetName" in body
    # Both placers build their set through it.
    assert "NewParamSetFrom(" in funcs["Gen_PlaceDirective"]
    assert "NewParamSetFrom(" in funcs["Gen_PlaceBlanket"]
    assert "NewParamSet(" not in funcs["Gen_PlaceDirective"].replace("NewParamSetFrom(", "")
    assert '"hidden":' in funcs["ParamSetJson"]
    assert "Param.IsHidden" in funcs["ParamSetJson"]
