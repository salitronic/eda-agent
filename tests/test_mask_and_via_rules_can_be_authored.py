# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_create_design_rule authors the two rules the toolset points at.

Two refusals used to end with "set it in the dialog". Tenting a via is
a Solder Mask Expansion rule scoped IsVia, because
pcb_set_via_soldermask_relief crashes the scripting engine on this
build and cannot be fixed. Catching via-in-pad is a Vias Under SMD
rule, which the routing discipline recommends. Neither could be
created, so both pieces of advice ended somewhere this toolset could
not follow.

WHAT THE SYMBOLS REST ON. eRule_PasteMaskExpansion with
IPCB_PasteMaskExpansionRule.Expansion is demonstrated by
NofittedNoPaste.pas in the reference corpus, which creates one through
PCBRuleFactory and adds it to the board. The other two enums are used
by three reference scripts each and their interfaces and properties are
in the SDK reference, but no published script calls the FACTORY with
them. That is why paste_mask_expansion is here at all: it is the
control. If it fails live, the handler is wrong; if it works and the
others do not, the enums are.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

#: rule_type -> (factory enum, constraint interface, property written)
_KINDS = {
    "solder_mask_expansion": (
        "eRule_SolderMaskExpansion", "IPCB_SolderMaskExpansionRule",
        "Expansion"),
    "paste_mask_expansion": (
        "eRule_PasteMaskExpansion", "IPCB_PasteMaskExpansionRule",
        "Expansion"),
    "vias_under_smd": (
        "eRule_ViasUnderSMD", "IPCB_ViasUnderSMDConstraint", "Allowed"),
}


class _Recorder:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params or {}))
        return {"created": True}


@pytest.fixture()
def call(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: recorder)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    tools = {t.name: t.fn for t in asyncio.run(registry.list_tools())}

    def run(**kwargs):
        asyncio.run(tools["pcb_create_design_rule"](**kwargs))
        assert recorder.sent, "nothing was sent to the bridge"
        return recorder.sent[-1][1]

    return run


@pytest.fixture(scope="module")
def handler() -> str:
    return load(_PCB_PAS, minimum=100)["PCB_CreateDesignRule"]


def _branch(handler: str, rule_type: str) -> str:
    """The one dispatch arm for this rule_type.

    Scoped to the arm, not the whole handler. Every one of these names
    also appears in the comments, the INVALID_PARAM message and the
    reply, so "the literal is somewhere in the function" is satisfied by
    a handler whose dispatch is broken. A mutation that renamed the
    dispatch condition walked straight through the first version.
    """
    guard = f"RuleTypeStr = '{rule_type}' Then"
    assert guard in handler, f"{rule_type} is not dispatched"
    rest = handler.split(guard, 1)[1]
    # The arm ends at the next Else in the chain, at the arm's own
    # depth. Every arm here is a flat "Else If ... Then Begin ... End".
    end = rest.find(chr(10) + "        Else")
    return rest[:end] if end > 0 else rest


@pytest.mark.parametrize("rule_type", sorted(_KINDS))
def test_the_handler_builds_each_kind(handler: str, rule_type: str):
    enum, interface, prop = _KINDS[rule_type]
    branch = _branch(handler, rule_type)
    assert f"PCBRuleFactory({enum})" in branch, (
        f"the {rule_type} arm does not call the factory with {enum}")
    assert f".{prop} :=" in branch, (
        f"the {rule_type} arm never writes {prop}")

    # The local has to be declared as the constraint subtype: setters do
    # not resolve through the base IPCB_Rule, and DelphiScript has no
    # typecast to narrow with.
    target = branch.split(":= PCBServer.PCBRuleFactory", 1)[0]
    local = target.strip().split()[-1]
    decls = handler.split("Begin", 1)[0]
    line = next((ln for ln in decls.splitlines()
                 if local in ln.split(":")[0]), "")
    assert interface in line, (
        f"the {rule_type} arm assigns the factory result to {local}, "
        f"declared as {line.strip() or 'nothing found'}; it needs to be "
        f"{interface} for .{prop} to resolve")


@pytest.mark.parametrize("rule_type", sorted(_KINDS))
def test_each_arm_writes_its_own_scope(handler: str, rule_type: str):
    """Scope1Expression is what makes solder_mask_expansion mean "vias
    only". An arm that drops it creates a rule over every pad."""
    assert "Scope1Expression := ScopeStr" in _branch(handler, rule_type)


def test_the_refusal_list_names_every_kind_that_works(handler: str):
    """The INVALID_PARAM message is where a caller learns what exists.

    A kind that dispatches but is missing from that list is a feature
    nobody finds.
    """
    message = handler.split("'Unknown rule_type: '", 1)[1].split(";", 1)[0]
    for rule_type in _KINDS:
        assert rule_type in message, (
            f"{rule_type} works but the error message does not offer it")


def test_a_boolean_rule_does_not_report_a_measurement(handler: str):
    """vias_under_smd holds no value, so answering value_mils for it
    would be reporting a number the rule does not have.

    Read the two arms of the reply separately. Checking only that both
    ``"allowed"`` and ``"value_mils"`` appear somewhere in the handler
    passes even when the branch is keyed on a rule type that can never
    match, which is what the first version of this did and what a
    mutation walked straight through.
    """
    tail = handler.split("MarkDocDirtyByPath", 1)[1]
    guard = "If RuleTypeStr = 'vias_under_smd' Then"
    assert guard in tail, (
        "the reply no longer branches on vias_under_smd, so the boolean "
        "rule is answered with whatever the measurement arm reports")

    boolean_arm, _, measured_arm = tail.partition(chr(10) + "    Else")
    boolean_arm = boolean_arm.split(guard, 1)[1]
    assert '"allowed":' in boolean_arm
    assert '"value_mils"' not in boolean_arm
    assert '"value_mils":' in measured_arm
    assert '"allowed"' not in measured_arm


def test_omitting_allowed_creates_a_rule_that_forbids_nothing(handler: str):
    """Defaulting to False would silently ban via-in-pad board-wide on
    a caller who just wanted the rule to exist."""
    assert "If AllowedStr = '' Then AllowedVal := True" in handler


def test_the_flag_reaches_the_bridge_only_when_asked(call):
    assert "allowed" not in call(name="r", rule_type="vias_under_smd")
    assert call(name="r", rule_type="vias_under_smd",
                allowed=False)["allowed"] == "false"
    assert call(name="r", rule_type="vias_under_smd",
                allowed=True)["allowed"] == "true"


def test_a_negative_expansion_survives_the_trip(call):
    """Tenting needs a negative value: it contracts the mask opening.
    A str() that dropped the sign, or a clamp at zero, would leave the
    via bare while reporting success."""
    sent = call(name="tent", rule_type="solder_mask_expansion",
                scope="IsVia", value=-4)
    assert sent["value"] == "-4"
    assert sent["scope"] == "IsVia"


def test_the_advice_that_used_to_end_in_a_dialog_now_names_the_tool():
    """Both refusals pointed at Design > Rules and stopped. If either
    goes back to that wording while the rule kind exists, the toolset is
    telling a caller to do by hand what it can do."""
    from eda_agent.design import discipline

    text = discipline.__doc__ or ""
    src = Path(discipline.__file__).read_text(encoding="utf-8")
    assert "vias_under_smd" in src, (
        "the routing discipline no longer names the rule that enforces "
        "the via-in-pad advice it gives")
    assert "cannot create that kind" not in src
    assert "cannot create that kind" not in text

    relief = load(_PCB_PAS, minimum=100)["PCB_SetViaSoldermaskRelief"]
    assert "solder_mask_expansion" in relief, (
        "the via-relief refusal no longer names the rule kind that does "
        "the job")
