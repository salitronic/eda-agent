# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Layer Stack Manager's own copy of the stack can be brought back in
step with the board.

Once a board carries the manager's copy, script writes to its stack do not
reach it. The board's values are written into the manager's model (its
|KEY=VALUE| records), which Altium loads and applies. The document loads
a model only from a file, so the model goes through one temporary file in
the workspace, written and deleted by the handler.

MEASURED (AD 26.11): a copper layer's thickness comes back from its weight
on load unless the weight is written too, and a model loaded as symmetric
mirrors one half of the stack over the other, so it is loaded
non-symmetric and symmetry asked back afterwards. The board's own stack is
compared before and after: the apply writes into the board.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools
from eda_agent.tools.pcb import board_stack_changes, stack_model_edits

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

_MODEL = ("\ufeff|STACKUPVERSION=1|LAYERSUBSTACK_V8_0ID={S}|LAYERSUBSTACK_V8_0SYMMETRIC=True"
          "|LAYER_V8_0NAME=Top Layer|LAYER_V8_0COPTHICK=1.4mil"
          "|LAYER_V8_0$LSM$Thickness=1.4mil|LAYER_V8_0$LSM$Weight=1oz"
          "|LAYER_V8_0_{S}NAME=Top Layer"
          "|LAYER_V8_1NAME=Dielectric 1|LAYER_V8_1DIELTYPE=0|LAYER_V8_1DIELHEIGHT=12.6mil"
          "|LAYER_V8_1DIELCONST=4.8|LAYER_V8_1DIELMATERIAL=FR-4"
          "|LAYER_V8_1$LSM$Thickness=12.6mil|LAYER_V8_1$LSM$Material=FR-4"
          "|LAYER_V8_1_{S}NAME=Dielectric 1")
_KINDS = {"Top Layer": "copper", "Dielectric 1": "dielectric"}


def _records(model):
    return dict(r.partition("=")[::2] for r in model.split("|") if r)


# ---- the model edits --------------------------------------------------------

def test_copper_thickness_writes_both_records_and_the_weight():
    model, changes = stack_model_edits(
        _MODEL, [{"layer": "Top Layer", "field": "thickness_mils", "board": 2.8}], _KINDS)
    rec = _records(model)
    assert rec["LAYER_V8_0COPTHICK"] == "2.8mil"
    assert rec["LAYER_V8_0$LSM$Thickness"] == "2.8mil"
    assert rec["LAYER_V8_0$LSM$Weight"] == "2oz"
    assert changes == [{"layer": "Top Layer", "field": "thickness_mils", "value": "2.8mil"}]


def test_dielectric_values_write_their_records():
    model, changes = stack_model_edits(_MODEL, [
        {"layer": "Dielectric 1", "field": "thickness_mils", "board": 5},
        {"layer": "Dielectric 1", "field": "dielectric_constant", "board": 4.35},
        {"layer": "Dielectric 1", "field": "material", "board": "PP-1080"},
        {"layer": "Dielectric 1", "field": "type", "board": "prepreg"},
    ], _KINDS)
    rec = _records(model)
    assert rec["LAYER_V8_1DIELHEIGHT"] == rec["LAYER_V8_1$LSM$Thickness"] == "5mil"
    assert "LAYER_V8_1$LSM$Weight" not in rec
    assert rec["LAYER_V8_1DIELCONST"] == rec["LAYER_V8_1$LSM$DielectricConstant"] == "4.35"
    assert rec["LAYER_V8_1DIELMATERIAL"] == rec["LAYER_V8_1$LSM$Material"] == "PP-1080"
    assert rec["LAYER_V8_1TYPEID"] == "{1A79611A-039D-4D40-A204-53C26C50F8B5}"
    assert rec["LAYER_V8_1DIELTYPE"] == "2"
    assert len(changes) == 4


def test_a_missing_record_goes_before_the_layers_per_stack_records():
    model, _ = stack_model_edits(
        _MODEL, [{"layer": "Dielectric 1", "field": "dielectric_constant", "board": 4.2}],
        _KINDS)
    recs = model.split("|")
    added = recs.index("LAYER_V8_1$LSM$DielectricConstant=4.2")
    assert recs[added + 1] == "LAYER_V8_1_{S}NAME=Dielectric 1"


def test_a_changed_model_is_loaded_non_symmetric():
    model, _ = stack_model_edits(
        _MODEL, [{"layer": "Top Layer", "field": "thickness_mils", "board": 2.8}], _KINDS)
    assert _records(model)["LAYERSUBSTACK_V8_0SYMMETRIC"] == "False"
    assert not model.startswith("\ufeff")


def test_nothing_to_write_leaves_the_model_as_it_was():
    model, changes = stack_model_edits(_MODEL, [
        {"layer": "Nowhere", "field": "thickness_mils", "board": 1},
        {"layer": "Top Layer", "field": "layer", "board": None},
    ], _KINDS)
    assert changes == []
    assert model == _MODEL.lstrip("\ufeff")


def test_board_stack_changes_names_each_value_that_moved():
    before = {"layers": [{"name": "L1", "kind": "copper", "thickness_mils": 2.8},
                         {"name": "D1", "kind": "dielectric", "constant": 4.35,
                          "material": "FR-4"}]}
    after = {"layers": [{"name": "L1", "kind": "copper", "thickness_mils": 1.4},
                        {"name": "D1", "kind": "dielectric", "constant": 4.35,
                         "material": "FR-4"}]}
    assert board_stack_changes(before, after) == [
        {"layer": "L1", "field": "thickness_mils", "before": 2.8, "after": 1.4}]
    assert board_stack_changes(before, before) == []


# ---- the tool -----------------------------------------------------------------

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


_MANAGER = {"layers": [{"name": "Top Layer", "type": "copper", "thickness_mils": 1.4},
                       {"name": "Dielectric 1", "type": "dielectric",
                        "thickness_mils": 12.6, "dielectric_constant": "4.8",
                        "material": "FR-4"}],
            "model": _MODEL}
_BOARD = {"layers": [{"name": "Top Layer", "kind": "copper", "thickness_mils": 2.8},
                     {"name": "Dielectric 1", "kind": "dielectric", "thickness_mils": 12.6,
                      "constant": 4.8, "material": "FR-4"}]}


class _Bridge:
    def __init__(self, manager=_MANAGER, board=_BOARD, board_after=None, in_step_after=True):
        self.sent = []
        self.manager, self.board = manager, board
        self.board_after = board_after or board
        self.loaded = False
        self.in_step_after = in_step_after

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "pcb.layer_stack_manager":
            if self.loaded and self.in_step_after:
                return {"layers": [dict(l, thickness_mils=2.8) if l["name"] == "Top Layer"
                                   else l for l in self.manager["layers"]]}
            return self.manager
        if command == "pcb.physical_stack":
            return self.board_after if self.loaded else self.board
        if command == "pcb.load_stack_manager_model":
            self.loaded = True
            return {"loaded": True, "symmetric": True, "symmetric_after": False}
        raise AssertionError(command)


def _tool(monkeypatch, bridge):
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    return mcp.tools["pcb_sync_stack_manager"]


def _loads(bridge):
    return [p for c, p in bridge.sent if c == "pcb.load_stack_manager_model"]


def test_the_model_is_read_as_records(monkeypatch):
    bridge = _Bridge()
    asyncio.run(_tool(monkeypatch, bridge)())
    assert bridge.sent[0] == ("pcb.layer_stack_manager", {"include_model": "records"})


def test_a_dry_run_lists_the_changes_and_loads_nothing(monkeypatch):
    bridge = _Bridge()
    out = asyncio.run(_tool(monkeypatch, bridge)())
    assert out["changes"] == [{"layer": "Top Layer", "field": "thickness_mils",
                               "value": "2.8mil"}]
    assert out["dry_run"] is True
    assert _loads(bridge) == []


def test_a_run_loads_the_edited_model_and_checks_both_sides(monkeypatch):
    bridge = _Bridge()
    out = asyncio.run(_tool(monkeypatch, bridge)(dry_run=False))
    (load,) = _loads(bridge)
    assert "LAYER_V8_0COPTHICK=2.8mil" in load["model"]
    assert out["in_step_after"] is True
    assert out["board_changed"] == []
    assert out["symmetric_after"] is False


def test_a_board_changed_by_the_apply_is_reported_and_not_in_step(monkeypatch):
    moved = {"layers": [dict(_BOARD["layers"][0], thickness_mils=1.4), _BOARD["layers"][1]]}
    bridge = _Bridge(board_after=moved, in_step_after=False)
    out = asyncio.run(_tool(monkeypatch, bridge)(dry_run=False))
    assert out["board_changed"] == [{"layer": "Top Layer", "field": "thickness_mils",
                                     "before": 2.8, "after": 1.4}]
    assert out["in_step_after"] is False


def test_in_step_loads_nothing(monkeypatch):
    board = {"layers": [dict(_BOARD["layers"][0], thickness_mils=1.4), _BOARD["layers"][1]]}
    bridge = _Bridge(board=board)
    out = asyncio.run(_tool(monkeypatch, bridge)(dry_run=False))
    assert out == {"in_step": True, "changes": [], "dry_run": False}
    assert _loads(bridge) == []


def test_layers_only_one_side_has_are_refused(monkeypatch):
    board = {"layers": _BOARD["layers"] + [{"name": "GND", "kind": "copper",
                                            "thickness_mils": 1.2}]}
    bridge = _Bridge(board=board)
    out = asyncio.run(_tool(monkeypatch, bridge)(dry_run=False))
    assert out["ok"] is False
    assert out["layers_differ"] == [{"layer": "GND", "field": "layer",
                                     "board": "copper", "stack_manager": None}]
    assert _loads(bridge) == []


# ---- the tools that change the stack sync after the change -----------------------

class _WriteBridge(_Bridge):
    """A stack write recorded, then the sync's reads answered as above."""
    async def send_command_async(self, command, params=None, timeout=None):
        if command in ("pcb.set_dielectric", "pcb.modify_layer"):
            self.sent.append((command, dict(params or {})))
            return {"layer": params.get("layer"), "not_applied": []}
        return await super().send_command_async(command, params, timeout)


def _writer(monkeypatch, name):
    bridge = _WriteBridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    return mcp.tools[name], bridge


def test_set_dielectric_brings_the_manager_in_step_after_the_write(monkeypatch):
    tool, bridge = _writer(monkeypatch, "pcb_set_dielectric")
    out = asyncio.run(tool(layer="Dielectric 1", constant=4.35))
    assert bridge.sent[0][0] == "pcb.set_dielectric"
    assert len(_loads(bridge)) == 1
    assert out["stack_manager"]["in_step_after"] is True


def test_modify_layer_brings_the_manager_in_step_after_the_write(monkeypatch):
    tool, bridge = _writer(monkeypatch, "pcb_modify_layer")
    out = asyncio.run(tool(layer="TopLayer", copper_thickness_mils=2.8))
    assert bridge.sent[0][0] == "pcb.modify_layer"
    assert len(_loads(bridge)) == 1
    assert out["stack_manager"]["changes"][0]["layer"] == "Top Layer"


def test_a_rebuild_that_matches_brings_the_manager_in_step():
    text = Path(pcb_tools.__file__).read_text(encoding="utf-8")
    body = text[text.index("async def pcb_make_layer_stack("):
                text.index("async def pcb_set_dielectric(")]
    done = body.index('result["done"] = trouble is None and not result["mismatches"]')
    sync = body.index('result["stack_manager"] = await sync_stack_manager(bridge)')
    assert done < sync < body.index('result["restored"] = True')
    assert 'if result["done"]:' in body[done:sync]


# ---- the handler ----------------------------------------------------------------

def _handler():
    return load(_PCB)["PCB_LoadStackManagerModel"]


def test_the_model_goes_through_one_workspace_file_that_is_deleted():
    body = _handler()
    assert "Path := WorkspaceDir + 'stack_model_' + RequestId + '.txt';" in body
    load_at = body.index("Doc.LoadFromFile(Path, 0);")
    assert body.index("SL.SaveToFile(Path);") < load_at < body.index(
        "Doc.ApplyToPcb(Board.FileName);", load_at)
    finally_at = body.index("Finally", load_at)
    assert "If FileExists(Path) Then DeleteFile(Path);" in body[finally_at:]


def test_only_a_records_model_is_loaded():
    body = _handler()
    refuse = body.index("If Pos('|LAYER_V8_', Model) = 0 Then")
    assert refuse < body.index("Doc.LoadFromFile(Path, 0);")
    assert "'BAD_PARAM'" in body[refuse:body.index("Doc.LoadFromFile(Path, 0);")]


def test_symmetry_is_asked_back_only_when_the_stack_had_it():
    body = _handler()
    assert "WasSym := St.IsSymmetric;" in body
    back = body.index("If WasSym Then")
    assert "St.SetState_IsSymmetric(True);" in body[back:]
    assert body.index("SymAfter := St.IsSymmetric;") > back


def test_the_command_is_dispatched():
    text = _PCB.read_text(encoding="utf-8-sig")
    assert ("'load_stack_manager_model': Result := "
            "PCB_LoadStackManagerModel(Params, RequestId);") in text
