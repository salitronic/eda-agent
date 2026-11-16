# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Project options proj_project_setup could not reach, and removing a
project parameter.

Allow single pin nets, automatic sheet numbering and the class generation
options (project wide and per sheet) had no setter in the bridge, and no
tool removed a project parameter. The script engine names the getters and
setters (DM_Get/SetNetlistSinglePinNets, DM_Get/SetAutoSheetNumbering,
DM_GetProjectClassGenerationOptions, DM_DocumentClassGenerationOptions,
DM_RemoveParameterByName); they were measured on AD 25.8.1 to write, to
reach the .PrjPcb on save and to survive a close and reopen.

What the tool sends and refuses, and the Pascal shapes: every write is
read back, the two options without a setter are only read, and a remove of
a name the project does not have is refused instead of passing silently
(DM_RemoveParameterByName raises nothing for it).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import project
from eda_agent.tools.option_hints import project_option_collision

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body(name: str) -> str:
    text = (ALTIUM / "Project.pas").read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;\n", start)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _call(monkeypatch, tool, **kwargs):
    bridge = _Bridge()
    monkeypatch.setattr(project, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    project.register_project_tools(_Mcp())
    result = asyncio.run(captured[tool](**kwargs))
    return result, bridge.sent


def test_setup_sends_the_new_options(monkeypatch):
    _, sent = _call(monkeypatch, "proj_project_setup",
                    single_pin_nets=True, auto_sheet_numbering=False,
                    class_generation={"net_class_auto_comp": True,
                                      "comp_class_manual_room": False},
                    class_gen_document="C:/p/A.SchDoc",
                    sheet_class_generation={"comp_class_auto": False,
                                            "net_class_auto_scope": "all"})
    assert sent == [("project.project_setup", {
        "single_pin_nets": "true", "auto_sheet_numbering": "false",
        "net_class_auto_comp": "true", "comp_class_manual_room": "false",
        "comp_class_auto": "false", "net_class_auto_scope": "2",
        "class_gen_document": "C:/p/A.SchDoc"})]


def test_scope_words_and_numbers(monkeypatch):
    for given, sent_as in (("none", "0"), ("Local", "1"), ("all", "2"), ("1", "1")):
        _, sent = _call(monkeypatch, "proj_project_setup",
                        class_gen_document="C:/p/A.SchDoc",
                        sheet_class_generation={"net_class_auto_scope": given})
        assert sent[0][1]["net_class_auto_scope"] == sent_as, given


def test_setup_refuses_before_altium(monkeypatch):
    for kwargs in (
            {"class_generation": {"net_class_auto_comp": "yes"}},
            {"class_generation": {"no_such_flag": True}},
            {"sheet_class_generation": {"comp_class_auto": True}},
            {"class_gen_document": "C:/p/A.SchDoc"},
            {"class_gen_document": "C:/p/A.SchDoc",
             "sheet_class_generation": {"net_class_auto_scope": "some"}},
            {"class_gen_document": "C:/p/A.SchDoc",
             "sheet_class_generation": {"comp_class_manual": True}}):
        result, sent = _call(monkeypatch, "proj_project_setup", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs


def test_remove_parameter_sends_and_refuses(monkeypatch):
    _, sent = _call(monkeypatch, "proj_remove_parameter", name="REV",
                    project_path="C:/p/X.PrjPcb")
    assert sent == [("project.remove_parameter",
                     {"name": "REV", "project_path": "C:/p/X.PrjPcb"})]
    result, sent = _call(monkeypatch, "proj_remove_parameter", name="  ")
    assert sent == [] and result["ok"] is False


def test_every_new_write_is_read_back():
    body = _body("Proj_ProjectSetup")
    for write, check in (
            ("Project.DM_SetNetlistSinglePinNets(SinglePinStr = 'true');",
             "If Project.DM_GetNetlistSinglePinNets <> (SinglePinStr = 'true') Then"),
            ("Project.DM_SetAutoSheetNumbering(AutoNumStr = 'true');",
             "If Project.DM_GetAutoSheetNumbering <> (AutoNumStr = 'true') Then"),
            ("DocOpts.CompClassAutoEnabled := CCAutoStr = 'true';",
             "If DocOpts.CompClassAutoEnabled <> (CCAutoStr = 'true') Then"),
            ("DocOpts.CompClassAutoRoomEnabled := CCRoomStr = 'true';",
             "If DocOpts.CompClassAutoRoomEnabled <> (CCRoomStr = 'true') Then"),
            ("DocOpts.GenerateClassCluster := ClusterStr = 'true';",
             "If DocOpts.GenerateClassCluster <> (ClusterStr = 'true') Then"),
            ("DocOpts.NetClassAutoScope := StrToInt(ScopeStr);",
             "If DocOpts.NetClassAutoScope <> StrToInt(ScopeStr) Then")):
        assert body.index(write) < body.index(check), write
    flag = _body("SetProjectClassGenFlag")
    for prop in ("CompClassManualEnabled", "CompClassManualRoomEnabled",
                 "NetClassAutoBusEnabled", "NetClassAutoCompEnabled",
                 "NetClassAutoNamedHarnessEnabled", "NetClassManualEnabled",
                 "NetClassSeparateForBusSections"):
        assert f"Opts.{prop} := Want; Got := Opts.{prop};" in flag, prop
    assert "If Got <> Want Then Result :=" in flag


def test_sheet_flags_need_a_project_sheet():
    body = _body("Proj_ProjectSetup")
    assert "are per sheet: name it in class_gen_document" in body
    assert "Project.DM_IndexOfSourceDocument(CGDoc) < 0" in body
    assert "'net_class_auto_scope is 0, 1 or 2'" in body


def test_options_without_a_setter_are_only_read():
    setup = _body("Proj_ProjectSetup")
    assert "SetNameNetsHierarchically" not in setup
    assert "SetPowerPortNamesTakePriority" not in setup
    reader = _body("ProjectSetupJson")
    for read in ("Project.DM_GetNameNetsHierarchically",
                 "Project.DM_GetPowerPortNamesTakePriority",
                 "Project.DM_GetNetlistSinglePinNets",
                 "Project.DM_GetAutoSheetNumbering",
                 "ClassGenJson(Project)"):
        assert read in reader, read


def test_remove_refuses_a_missing_name_and_reads_back():
    body = _body("Proj_RemoveParameter")
    assert body.index("'NOT_FOUND'") < body.index("Project.DM_RemoveParameterByName(ParamName);")
    assert body.index("Project.DM_RemoveParameterByName(ParamName);") < body.index("Still := True")
    assert "If Not Still Then FileSaved := SaveProjectFile(Project);" in body
    assert "JsonBool('removed', Not Still)" in body


def test_option_names_warn_on_set_parameter():
    for name in ("single_pin_nets", "auto_sheet_numbering"):
        assert "proj_project_setup" in project_option_collision(name)
