# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Three rule settings pcb_set_rule_properties writes and reads back.

- A Clearance rule's minimum clearances by object kind, the grid under
  "Minimum Clearance" (``object_clearances_mils``). Altium's GetClearance
  and SetClearance take two primitives and answer for their kinds, so a
  cell is reached with two primitives made for it and destroyed again.
- A Silk To Solder Mask Clearance rule's choice between the exposed copper
  and the solder mask openings (``clearance_to_exposed_copper``).
- A Differential Pairs Routing rule's longest uncoupled run
  (``dp_max_uncoupled_length_mils``).

Each value given for a rule of another kind is refused by the script.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


@pytest.fixture(scope="module")
def fns() -> dict[str, str]:
    return load(_PCB_PAS, minimum=100)


class _Recorder:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        if command in ("pcb.get_design_rules", "generic.query_objects"):
            return {"rules": [], "objects": []}
        self.sent.append((command, params or {}))
        return {"ok": True}


@pytest.fixture()
def run(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: recorder)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    found = {t.name: t.fn for t in asyncio.run(registry.list_tools())}

    def call(**kwargs):
        before = len(recorder.sent)
        out = asyncio.run(found["pcb_set_rule_properties"](**kwargs))
        sent = recorder.sent[-1][1] if len(recorder.sent) > before else None
        return out, sent

    return call


# -- the table spec the bridge receives -------------------------------------

def test_cells_go_to_the_script_by_altium_kind_names(run):
    _, sent = run(name="Clearance",
                  object_clearances_mils={"track-smdpad": 4, "Text-*": 3.5})
    assert sent["object_clearances"] == "Track-SMDPad=4;Text-*=3.5"


@pytest.mark.parametrize("cells, says", [
    ({"Track": 4}, "two kinds"),
    ({"Track-Hole": 4}, "is one of"),
    ({"Track-Via": -1}, "0 or more"),
    ({"Track-Via": True}, "0 or more"),
    ({"Track-Via": "4"}, "0 or more"),
    ({}, "is a dict"),
])
def test_a_wrong_cell_is_refused_before_anything_is_sent(run, cells, says):
    out, sent = run(name="Clearance", object_clearances_mils=cells)
    assert sent is None
    assert out["ok"] is False and says in out["reason"]


def test_every_kind_the_server_names_is_one_the_script_names(fns):
    body = fns["ClearanceKindName"]
    for kind in pcb_mod.CLEARANCE_KINDS:
        assert f"'{kind}'" in body
    assert "'Hole'" not in body


def test_exposed_copper_and_uncoupled_length_are_sent(run):
    _, sent = run(name="SilkToMask", clearance_to_exposed_copper=False)
    assert sent["clearance_to_exposed_copper"] == "false"
    _, sent = run(name="Pairs", dp_max_uncoupled_length_mils=250)
    assert sent["dp_max_uncoupled_length_mils"] == "250"


def test_a_negative_uncoupled_length_is_refused(run):
    out, sent = run(name="Pairs", dp_max_uncoupled_length_mils=-1)
    assert sent is None and out["ok"] is False


# -- the script --------------------------------------------------------------

def test_each_rule_is_taken_from_the_iterator_into_its_own_interface(fns):
    """A rule assigned from an IPCB_Rule local keeps the base interface and
    its kind's calls fail; only the iterator's return narrows it."""
    for name, interface in (("ClearanceTable", "IPCB_ClearanceConstraint"),
                            ("SilkMaskToCopper", "IPCB_SilkToSolderMaskClearanceRule")):
        body = fns[name]
        decl = next(ln for ln in body.split("Begin", 1)[0].splitlines()
                    if ln.strip().startswith("R :"))
        assert interface in decl
        assert "R := Iter.FirstPCBObject;" in body


def test_a_cell_is_written_both_ways_round_and_its_probes_destroyed(fns):
    body = fns["ClearanceCellAt"]
    assert "Rule.SetClearance(P1, P2, Want)" in body
    assert "Rule.SetClearance(P2, P1, Want)" in body
    assert "Rule.GetClearance(P1, P2)" in body
    assert body.count("PCBServer.DestroyPCBObject") == 2
    assert "AddPCBObject" not in body and "AddPCBObject" not in fns["ClearanceProbe"]


def test_a_written_cell_that_reads_otherwise_is_not_applied(fns):
    body = fns["ClearanceCellsWrite"]
    assert "ClearanceCellAt(Rule, A, B, Want) <> Want" in body
    assert "Missed := Missed" in body


def test_settings_for_another_kind_are_refused(fns):
    body = fns["PCB_SetRuleProperties"]
    assert "(XCells <> '') And (Kind <> eRule_Clearance) Then XBad" in body
    assert "(XToCopper <> '') And (Kind <> 54) Then XBad" in body
    assert "(DUncoupled <> '') And (Kind <> 51) Then XBad" in body


def test_the_uncoupled_length_is_read_back(fns):
    body = fns["PCB_SetRuleProperties"]
    assert "RuleDiff.MaxUncoupledLength := MilsToCoordF" in body
    assert "RuleMilsTook(RuleDiff.MaxUncoupledLength, DUncoupled)" in body
    assert '"dp_max_uncoupled_length_mils":' in body


def test_the_rule_read_returns_table_scopes_and_mask_choice(fns):
    body = fns["PCB_GetRuleProperties"]
    assert "ClearanceTable(Board, RuleName, ''" in body
    assert "SilkMaskToCopper(Board, RuleName, '')" in body
    assert '"scope_1":"' in body and '"comment":"' in body
