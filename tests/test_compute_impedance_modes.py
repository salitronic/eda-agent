# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_compute_impedance can change what a line solves (issue #397).

A line's calculation mode cannot be set from script: the Layer Stack
Manager's transmission line only reads it. It is a record of the manager's
model (TRACEIMPEDANCE_V8_<n>CALCMODE: 1 width, 2 a pair's gap, 3 impedance,
measured on AD 25.8.1), so the tool edits that record and loads the model
through pcb.load_stack_manager_model, which keeps rules linked to their
profile, then solves. Anything it cannot place exactly is refused before
anything is written.
"""
from __future__ import annotations

import asyncio

from eda_agent.layout import stack_model as sm
from eda_agent.tools import pcb as pcb_tools

_MODEL = "\ufeff" + "|".join([
    "STACKUPVERSION=1",
    "LAYERSUBSTACK_V8_0ID={S0}", "LAYERSUBSTACK_V8_0NAME=Rigid",
    "LAYERSUBSTACK_V8_1ID={S1}", "LAYERSUBSTACK_V8_1NAME=Flex",
    "LAYER_V8_0ID={L0}", "LAYER_V8_0NAME=Top Layer", "LAYER_V8_0LAYERID=16973830",
    "LAYER_V8_0_{S0}NAME=Top Layer",
    "LAYER_V8_1ID={L1}", "LAYER_V8_1NAME=Mid-Layer 1",
    "LAYER_V8_2ID={L2}", "LAYER_V8_2NAME=Bottom Layer",
    "IMPEDANCEPROFILE_V8_0ID={P0}", "IMPEDANCEPROFILE_V8_0NAME=DC90",
    "IMPEDANCEPROFILE_V8_0DESCRIPTION=USB",
    "IMPEDANCEPROFILE_V8_1ID={P1}", "IMPEDANCEPROFILE_V8_1NAME=S50",
    "IMPEDANCEPROFILE_V8_1DESCRIPTION=S50",
    # DC90 on the top and bottom of the rigid stack, a pair
    "TRACEIMPEDANCE_V8_0LAYERID={L0}", "TRACEIMPEDANCE_V8_0SUBSTACKID={S0}",
    "TRACEIMPEDANCE_V8_0PROFILEID={P0}", "TRACEIMPEDANCE_V8_0DIFFERENTIAL=True",
    "TRACEIMPEDANCE_V8_0CALCMODE=1",
    "TRACEIMPEDANCE_V8_1LAYERID={L2}", "TRACEIMPEDANCE_V8_1SUBSTACKID={S0}",
    "TRACEIMPEDANCE_V8_1PROFILEID={P0}", "TRACEIMPEDANCE_V8_1DIFFERENTIAL=True",
    "TRACEIMPEDANCE_V8_1CALCMODE=1",
    # S50 on the inner layer in both stacks, single
    "TRACEIMPEDANCE_V8_2LAYERID={L1}", "TRACEIMPEDANCE_V8_2SUBSTACKID={S0}",
    "TRACEIMPEDANCE_V8_2PROFILEID={P1}", "TRACEIMPEDANCE_V8_2DIFFERENTIAL=False",
    "TRACEIMPEDANCE_V8_2CALCMODE=3",
    "TRACEIMPEDANCE_V8_3LAYERID={L1}", "TRACEIMPEDANCE_V8_3SUBSTACKID={S1}",
    "TRACEIMPEDANCE_V8_3PROFILEID={P1}", "TRACEIMPEDANCE_V8_3DIFFERENTIAL=False",
    "TRACEIMPEDANCE_V8_3CALCMODE=3",
])


def _modes(model):
    return {k: v for k, v in (r.partition("=")[::2] for r in model.lstrip("\ufeff").split("|"))
            if k.endswith("CALCMODE")}


def _edit(*modes):
    return sm.impedance_mode_edits(_MODEL, list(modes))


def test_one_line_changes_and_nothing_else():
    model, changes, problems = _edit({"profile": "DC90", "layer": "Top Layer", "solve": "gap"})
    assert problems == []
    assert changes == [{"profile": "DC90", "stack": "Rigid", "layer": "Top Layer",
                        "solve": "gap", "was": "width"}]
    assert _modes(model) == {"TRACEIMPEDANCE_V8_0CALCMODE": "2", "TRACEIMPEDANCE_V8_1CALCMODE": "1",
                             "TRACEIMPEDANCE_V8_2CALCMODE": "3", "TRACEIMPEDANCE_V8_3CALCMODE": "3"}
    changed = [(a, b) for a, b in zip(_MODEL.split("|"), model.split("|")) if a != b]
    assert changed == [("TRACEIMPEDANCE_V8_0CALCMODE=1", "TRACEIMPEDANCE_V8_0CALCMODE=2")]
    assert model.startswith("\ufeff")


def test_the_records_number_each_mode():
    assert sm.CALCMODE_RECORD == {"width": "1", "gap": "2", "impedance": "3"}


def test_a_profile_is_named_by_its_name_or_description_without_case():
    for name in ("usb", "Dc90"):
        _, changes, problems = _edit({"profile": name, "layer": "bottom layer", "solve": "impedance"})
        assert problems == []
        assert [(c["layer"], c["solve"], c["was"]) for c in changes] == [
            ("Bottom Layer", "impedance", "width")]
    _, changes, _ = _edit({"profile": "usb", "layer": "Bottom Layer", "solve": "impedance"})
    assert changes[0]["profile"] == "USB"


def test_a_layer_is_named_with_or_without_its_spaces():
    """pcb_impedance_profile_layer takes TopLayer; the same name works here."""
    for name in ("TopLayer", "toplayer", "Top Layer"):
        _, changes, problems = _edit({"profile": "DC90", "layer": name, "solve": "gap"})
        assert problems == []
        assert [c["layer"] for c in changes] == ["Top Layer"]


def test_a_layer_in_several_stacks_changes_in_each_unless_one_is_named():
    _, both, _ = _edit({"profile": "S50", "layer": "Mid-Layer 1", "solve": "width"})
    assert [c["stack"] for c in both] == ["Rigid", "Flex"]
    model, one, _ = _edit({"profile": "S50", "layer": "Mid-Layer 1", "stack": "flex",
                           "solve": "width"})
    assert [c["stack"] for c in one] == ["Flex"]
    assert _modes(model)["TRACEIMPEDANCE_V8_2CALCMODE"] == "3"
    assert _modes(model)["TRACEIMPEDANCE_V8_3CALCMODE"] == "1"


def test_a_line_already_in_that_mode_is_not_a_change():
    model, changes, problems = _edit({"profile": "DC90", "layer": "Top Layer", "solve": "width"})
    assert (changes, problems, model) == ([], [], _MODEL)


def test_a_gap_is_solved_only_for_a_pair():
    model, changes, problems = _edit({"profile": "S50", "layer": "Mid-Layer 1", "stack": "Rigid",
                                      "solve": "gap"})
    assert changes == [] and model == _MODEL
    assert problems == ["S50 on Mid-Layer 1 in Rigid: a gap is solved only for a pair"]


def test_a_line_the_profile_does_not_have_is_refused():
    _, changes, problems = _edit({"profile": "S50", "layer": "Top Layer", "solve": "width"})
    assert changes == []
    assert "the profile has no line there" in problems[0]
    assert "pcb_impedance_profile_layer" in problems[0]


def test_bad_entries_are_problems():
    _, _, problems = _edit({"profile": "DC90", "layer": "Top Layer", "solve": "fast"},
                           {"layer": "Top Layer", "solve": "width"},
                           {"profile": "DC90", "solve": "width"},
                           "Top Layer")
    assert problems[0] == "DC90 on Top Layer: solve is width, gap or impedance"
    assert problems[1:3] == ["each mode names a profile and a layer"] * 2
    assert len(problems) == 4


def test_a_line_named_twice_is_refused():
    _, _, problems = _edit({"profile": "DC90", "layer": "Top Layer", "solve": "gap"},
                           {"profile": "usb", "layer": "top layer", "solve": "impedance"})
    assert problems == ["usb on top layer: the line is named twice"]


# --- the tool ----------------------------------------------------------------

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


_LAYERS = {"layers": [{"name": "Top Layer", "type": "copper", "thickness_mils": 1.4}]}
_BOARD = {"layers": [{"name": "Top Layer", "kind": "copper", "thickness_mils": 1.4}]}
_STALE = {"layers": [{"name": "Top Layer", "kind": "copper", "thickness_mils": 2.8}]}
_XML = ('<StackupDocument xmlns="http://altium.com/ns/LayerStackManager"><Stackup>'
        '<Stacks><Stack Id="s0" Name="Rigid"><Layers><Layer Id="l0" Name="Top Layer"/>'
        '</Layers></Stack></Stacks><ImpedanceProfiles><ImpedanceProfile AutoName="DC90" '
        'CustomName="USB" Impedance="90ohm" Tolerance="10%"><TransmissionLines>'
        '<TransmissionLine LayerId="l0" StackId="s0" IsDifferential="True" CalcMode="{m}" '
        'TraceWidth="10mil" TraceGap="{g}" ImpedanceError="0.1%">'
        '<ElectricParameters Zo="45ohm"/></TransmissionLine></TransmissionLines>'
        '</ImpedanceProfile></ImpedanceProfiles></Stackup></StackupDocument>')
_LINKS = [{"rule": "R1", "profile": "DC90", "state": "relinked"}]


class _Bridge:
    def __init__(self, board=_BOARD, model=_MODEL, load=None):
        self.sent = []
        self.board, self.model, self.load = board, model, load

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "pcb.layer_stack_manager":
            out = dict(_LAYERS)
            if (params or {}).get("include_model") == "records":
                out["model"] = self.model
            return out
        if command == "pcb.physical_stack":
            return self.board
        if command == "pcb.load_stack_manager_model":
            return self.load or {"loaded": True, "symmetric": True, "symmetric_after": True,
                                 "rules_relinked": 1, "profile_rules": _LINKS}
        if command == "pcb.compute_impedance":
            return {"applied": True, "solve_ms": 600, "apply_ms": 400,
                    "model_before": _XML.format(m="TraceClearance", g="6mil"),
                    "model_after": _XML.format(m="TraceClearance", g="6.0072mil")}
        raise AssertionError(command)

    def commands(self):
        return [c for c, _ in self.sent]


def _call(monkeypatch, bridge, **kw):
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    return asyncio.run(mcp.tools["pcb_compute_impedance"](**kw))


_GAP = [{"profile": "USB", "layer": "Top Layer", "solve": "gap"}]


def test_a_mode_is_loaded_then_solved(monkeypatch):
    bridge = _Bridge()
    out = _call(monkeypatch, bridge, modes=_GAP)
    assert bridge.commands() == ["pcb.layer_stack_manager", "pcb.physical_stack",
                                 "pcb.load_stack_manager_model", "pcb.compute_impedance"]
    assert bridge.sent[0][1] == {"include_model": "records"}
    load = bridge.sent[2][1]["model"]
    assert "TRACEIMPEDANCE_V8_0CALCMODE=2" in load and "TRACEIMPEDANCE_V8_1CALCMODE=1" in load
    assert bridge.sent[3][1] == {"apply": "true"}
    assert out["modes_set"] == [{"profile": "USB", "stack": "Rigid", "layer": "Top Layer",
                                 "solve": "gap", "was": "width"}]
    assert (out["rules_relinked"], out["profile_rules"], out["symmetric_after"]) == (1, _LINKS, True)
    assert out["lines"][0]["solves"] == "gap"
    assert out["lines"][0]["changed"]["gap_mils"] == [6.0, 6.0072]


def test_nothing_to_change_loads_nothing(monkeypatch):
    bridge = _Bridge()
    out = _call(monkeypatch, bridge, modes=[{"profile": "USB", "layer": "Top Layer",
                                             "solve": "width"}])
    assert "pcb.load_stack_manager_model" not in bridge.commands()
    assert out["modes_set"] == [] and "profile_rules" not in out


def test_modes_need_apply(monkeypatch):
    bridge = _Bridge()
    out = _call(monkeypatch, bridge, modes=_GAP, apply=False)
    assert out["ok"] is False and "need apply" in out["reason"]
    assert bridge.sent == []


def test_a_problem_writes_nothing(monkeypatch):
    bridge = _Bridge()
    out = _call(monkeypatch, bridge, modes=[{"profile": "S50", "layer": "Top Layer",
                                             "solve": "width"}])
    assert out["ok"] is False and out["problems"]
    assert "pcb.load_stack_manager_model" not in bridge.commands()
    assert "pcb.compute_impedance" not in bridge.commands()


def test_out_of_step_refuses_before_the_load(monkeypatch):
    bridge = _Bridge(board=_STALE)
    out = _call(monkeypatch, bridge, modes=_GAP)
    assert out["ok"] is False and out["out_of_step"]
    assert bridge.commands() == ["pcb.layer_stack_manager", "pcb.physical_stack"]


def test_force_skips_the_comparison_but_still_reads_the_model(monkeypatch):
    bridge = _Bridge(board=_STALE)
    out = _call(monkeypatch, bridge, modes=_GAP, force=True)
    assert bridge.commands() == ["pcb.layer_stack_manager", "pcb.load_stack_manager_model",
                                 "pcb.compute_impedance"]
    assert out["modes_set"][0]["solve"] == "gap"


def test_a_failed_load_is_passed_on_and_nothing_solved(monkeypatch):
    refusal = {"success": False, "error": {"code": "BAD_PARAM"}}
    bridge = _Bridge(load=refusal)
    out = _call(monkeypatch, bridge, modes=_GAP)
    assert out == refusal
    assert "pcb.compute_impedance" not in bridge.commands()


def test_an_unreadable_model_is_said(monkeypatch):
    bridge = _Bridge(model="")
    out = _call(monkeypatch, bridge, modes=_GAP)
    assert out["ok"] is False and "could not be read" in out["reason"]
    assert bridge.commands() == ["pcb.layer_stack_manager"]


def test_no_modes_reads_no_model(monkeypatch):
    bridge = _Bridge()
    out = _call(monkeypatch, bridge)
    assert bridge.sent[0] == ("pcb.layer_stack_manager", {})
    assert "modes_set" not in out
