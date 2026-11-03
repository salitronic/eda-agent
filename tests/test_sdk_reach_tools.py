# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tools that reach further into Altium's SDK.

Board pictures and text dumps, net figures, guides, local grids, plane
state, drill symbols, part parameters on the board, simple bodies,
baseline dimensions, the file version, STEP exporter settings, output
channels, part models, the library
manager, the Output panel and generated documents. What each sends, what
each refuses before Altium sees it, and the Pascal shapes that keep each
reply honest.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from eda_agent.tools import pcb, project

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body(unit: str, name: str) -> str:
    text = (ALTIUM / unit).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;\n", start)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _tools(module, register, monkeypatch, bridge=None):
    bridge = bridge or _Bridge()
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    getattr(module, register)(_Mcp())
    return captured, bridge


def _pcb(monkeypatch):
    return _tools(pcb, "register_pcb_tools", monkeypatch)


def _project(monkeypatch, bridge=None):
    return _tools(project, "register_project_tools", monkeypatch, bridge)


@pytest.mark.parametrize("kwargs", [
    {"path": "board.png"},
    {"path": "C:/out/board.bmp"},
    {"path": "C:/out/board.png", "side": "left"},
    {"path": "C:/out/board.png", "dpi": 10},
    {"path": "C:/out/board.png", "dpi": 5000},
])
def test_render_png_refuses_before_altium(monkeypatch, kwargs):
    tools, bridge = _pcb(monkeypatch)
    out = asyncio.run(tools["pcb_render_png"](**kwargs))
    assert out["ok"] is False and bridge.sent == []


@pytest.mark.parametrize("path, fmt", [
    ("C:/out/board.json", "xml"),
    ("board.json", "json"),
    ("C:/out/Board.PcbDoc", "json"),
])
def test_board_text_never_overwrites_a_document(monkeypatch, path, fmt):
    tools, bridge = _pcb(monkeypatch)
    out = asyncio.run(tools["pcb_save_board_text"](path, format=fmt))
    assert out["ok"] is False and bridge.sent == []


def test_net_figures_sends_the_nets_asked_for(monkeypatch):
    tools, bridge = _pcb(monkeypatch)
    asyncio.run(tools["pcb_net_figures"](nets=[" VBUS", "USB_DP ", " "], limit=20))
    command, params = bridge.sent[0]
    assert command == "pcb.net_figures"
    assert params["nets"] == "VBUS,USB_DP"
    assert params["limit"] == "20"


def test_guides_can_only_be_cleared(monkeypatch):
    tools, bridge = _pcb(monkeypatch)
    assert "pcb_guides" not in tools
    asyncio.run(tools["pcb_clear_guides"]())
    assert bridge.sent == [("pcb.guides", {"action": "delete_all"})]
    body = _body("PCB.pas", "PCB_Guides")
    assert "DeleteAllGuides" in body
    # Scripts are not given the grid manager's add calls; one in the body
    # would halt the loop with an undeclared identifier.
    assert not re.search(r"Mgr\.\w*Add\w*Guide|AddSnapPoint", body)


def test_local_grid_refuses_an_unknown_action(monkeypatch):
    tools, bridge = _pcb(monkeypatch)
    out = asyncio.run(tools["pcb_local_grid"]("add_hex"))
    assert out["ok"] is False and bridge.sent == []


@pytest.mark.parametrize("kwargs", [
    {"designator": "C1", "action": "rename"},
    {"designator": "C1", "action": "set", "value": "1"},
    {"designator": "C1", "action": "remove"},
])
def test_part_parameters_refuse_before_altium(monkeypatch, kwargs):
    tools, bridge = _pcb(monkeypatch)
    out = asyncio.run(tools["pcb_component_parameters"](**kwargs))
    assert out["ok"] is False and bridge.sent == []


def test_part_parameters_use_the_factory_call_scripts_are_given():
    body = _body("PCB.pas", "PCB_ComponentParameters")
    # The factory call is chosen by Altium version in one helper.
    assert "PcbPartParameters(Comp)" in body
    assert "CreatePrimitiveParametersWithoutVariant" not in body
    assert "Internal_CreatePrimitiveParameters" not in body
    assert "PcbPartParameterItems(" in body
    assert "GetParameterByIndex" in _body("PCB.pas", "PcbPartParameterItems")
    # The getter by name halts the loop; the read-back comes from the walk.
    assert "GetValueByNameDef" not in body
    assert "Problem := 'the value does not read back'" in body
    assert "Problem := 'the parameter is still there'" in body


@pytest.mark.parametrize("kwargs", [
    {"shape": "cube", "x": 0, "y": 0, "radius": 10, "height": 10},
    {"shape": "cylinder", "x": 0, "y": 0, "radius": 10},
    {"shape": "sphere", "x": 0, "y": 0, "radius": 0},
    {"shape": "sphere", "x": 0, "y": 0, "radius": 5, "side": "middle"},
])
def test_simple_body_refuses_before_altium(monkeypatch, kwargs):
    tools, bridge = _pcb(monkeypatch)
    out = asyncio.run(tools["pcb_place_simple_body"](**kwargs))
    assert out["ok"] is False and bridge.sent == []


def test_baseline_dimension_sends_its_points(monkeypatch):
    tools, bridge = _pcb(monkeypatch)
    asyncio.run(tools["pcb_place_baseline_dimension"](
        200, 200, [[600, 200], [900, 200]], layer="Mechanical1"))
    command, params = bridge.sent[0]
    assert command == "pcb.place_baseline_dimension"
    assert params["points"] == "600.0,200.0/900.0,200.0"
    out = asyncio.run(tools["pcb_place_baseline_dimension"](0, 0, [[1, 2, 3]]))
    assert out["ok"] is False and len(bridge.sent) == 1


def test_plane_state_reads_by_layer_number_only():
    body = _body("PCB.pas", "PCB_PlaneState")
    assert "IsPlaneLayerIsPoured(Lyr)" in body
    # The layer object's net and the void list both halted the loop.
    assert "NetName" not in body and "GetVoidsOnPlaneLayer" not in body


def test_step_options_convert_and_refuse_unknown_names():
    params = project._step_option_params({
        "folded": 1, "fold_rate": "50", "component_suffix": "_x"})
    assert params == {"folded": "true", "fold_rate": "50",
                      "component_suffix": "_x", "set_component_suffix": "true"}
    with pytest.raises(ValueError):
        project._step_option_params({"colour": True})


def test_the_older_output_export_keeps_its_own_command(monkeypatch):
    tools, bridge = _project(monkeypatch)
    asyncio.run(tools["proj_run_output"]("drill", "C:/out/drill"))
    assert bridge.sent == [("project.generate_output",
                            {"output_type": "drill", "output_path": "C:/out/drill"})]
    text = (ALTIUM / "Project.pas").read_text(encoding="utf-8-sig")
    assert re.search(r"'generate_output':\s*Result := Proj_GenerateOutput\(", text)
    assert text.count("'generate_output':") == 1
    assert "OutputType := ExtractJsonValue(Params, 'output_type')" in _body(
        "Project.pas", "Proj_GenerateOutput")


def test_library_contents_say_when_altium_does_not_hold_the_library():
    body = _body("Project.pas", "Proj_LibraryLookup")
    assert "AvailableLibraryPath(I)" in body
    assert "JsonBool('known_to_manager', Known)" in body
    assert "If (Count = 0) And Not Known Then" in body


def test_extract_takes_only_an_intlib(monkeypatch, tmp_path):
    tools, bridge = _project(monkeypatch)
    out = asyncio.run(tools["lib_library_manager"](
        "extract", library="C:/l/Parts.SchLib", destination=str(tmp_path)))
    assert out["ok"] is False and bridge.sent == []
    out = asyncio.run(tools["lib_library_manager"]("rebuild"))
    assert out["ok"] is False and bridge.sent == []


def _lint():
    path = ALTIUM / "lint.py"
    spec = importlib.util.spec_from_file_location("_altium_lint_dups", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint_dups"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_lint_catches_a_routine_defined_twice(tmp_path):
    lint = _lint()
    a = tmp_path / "A.pas"
    b = tmp_path / "B.pas"
    a.write_text("Function Twice(X : Integer) : Integer;\nBegin\nEnd;\n", encoding="utf-8")
    b.write_text("{ Function Twice( in a comment }\n"
                 "Function Twice(Y : String) : String;\nBegin\nEnd;\n", encoding="utf-8")
    found = lint._scan_duplicate_definitions([str(a), str(b)])
    assert [(f.file, f.line, f.rule) for f in found] == [("B.pas", 2, "duplicate-definition")]


def test_no_routine_is_defined_twice_in_the_script():
    lint = _lint()
    targets = [str(ALTIUM / f) for f in lint.PAS_FILES]
    assert lint._scan_duplicate_definitions(targets) == []
