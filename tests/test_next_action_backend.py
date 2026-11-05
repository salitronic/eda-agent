# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""design_next_action must speak the active backend's tool names.

This is the tool an autonomous run calls on every iteration, and the
client does what the reply says. Until now nothing drove it at the tool
level on any backend, and the reply came straight out of the pure state
machine: an EasyEDA client was told its goal, its exit gate and its
suggested tools in Altium names.

The stage playbooks were adapted in ONE place, the tools list served by
design_autonomy_guide, which is what made the hole hard to see. A stage
read as fully translated while the exit gate beside it still named a
tool the client cannot call, and an exit gate is the sentence that
decides when a stage is finished.

Driven through the registered tool rather than by reading source, so a
future refactor that moves the adaptation elsewhere still passes as
long as the reply is right.
"""

from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools.design import register_design_tools


class _Mcp:
    """Minimal registrar: the design tools only ever call .tool()."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """Register the design tools against a throwaway workspace.

    The session journal is written to disk, so it must not land in the
    real workspace: a test that leaves design sessions behind in the
    user's workspace is the same class of accident as a test that
    reaches a live editor.
    """
    import eda_agent.config as config

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    real_get_config = config.get_config

    def sandboxed_config():
        cfg = real_get_config()
        object.__setattr__(cfg, "workspace_dir", workspace)
        return cfg

    monkeypatch.setattr(config, "get_config", sandboxed_config)
    mcp = _Mcp()
    register_design_tools(mcp)
    return mcp.tools


def _run(fn, **kw):
    return asyncio.run(fn(**kw))


def _first_action(tools):
    started = _run(tools["design_session_start"],
                   requirement="a 5V to 3V3 regulator")
    sid = started.get("session_id")
    assert sid, started
    return _run(tools["design_next_action"], session_id=sid)


def test_the_reply_names_altium_tools_on_altium(tools, monkeypatch):
    """The baseline. If this stops holding the substitution has become
    unconditional and is rewriting the backend it was written for."""
    monkeypatch.setenv("EDA_AGENT_BACKEND", "altium")
    action = _first_action(tools)
    assert action["stage"] == "requirement"
    assert action["suggested_tools"] == ["design_validate_requirement"]


def test_no_stage_reply_names_a_tool_this_backend_lacks(tools, monkeypatch):
    """Walk every stage, not just the one a fresh session starts on.

    A single-stage check passes on `requirement`, whose tools are
    shared, and says nothing about `sch_to_pcb`, where every suggested
    tool was Altium-only.
    """
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    from eda_agent.design.autonomy import _registered_tools
    from eda_agent.design.state_machine import STAGE_PLAYBOOKS

    available = _registered_tools("easyeda")
    assert len(available) > 150, (
        "the easyeda surface did not register; this guard would pass by "
        "checking nothing")

    # Drive the adaptation the tool uses, over every stage's tools.
    from eda_agent.design.autonomy import _EQUIVALENTS
    offenders = {}
    for stage, play in STAGE_PLAYBOOKS.items():
        swapped = [_EQUIVALENTS[t] if _EQUIVALENTS.get(t) in available else t
                   for t in play["tools"]]
        missing = [t for t in swapped if t not in available]
        if missing:
            offenders[stage] = missing

    # Some stages genuinely have no counterpart here and the guide says
    # so explicitly; what must not happen is a stage where a mapping
    # EXISTS and was not applied.
    unapplied = {s: [t for t in m if _EQUIVALENTS.get(t) in available]
                 for s, m in offenders.items()}
    unapplied = {s: t for s, t in unapplied.items() if t}
    assert not unapplied, (
        f"these stages suggest a tool that has a known equivalent which "
        f"was never substituted: {unapplied}")


def test_the_exit_gate_is_translated_not_only_the_tools(monkeypatch):
    """The specific hole. sch_to_pcb's gate named proj_compare_sch_pcb
    while its tools list beside it was already adapted."""
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    from eda_agent.design.autonomy import autonomy_guide

    gates = {s["stage"]: s["exit_gate"] for s in autonomy_guide()["stages"]}
    assert "easyeda_compare_schematic_pcb" in gates["sch_to_pcb"], gates
    assert "proj_compare_sch_pcb" not in gates["sch_to_pcb"]


def test_no_stage_goal_or_gate_names_altium(monkeypatch):
    """Product and mechanism names, not just tool names.

    "before any Altium round-trip" and "without the modal ECO dialog"
    described the objective in terms of a mechanism only one backend
    has. Both are true statements about Altium and meaningless to an
    EasyEDA client, so they were rewritten to state the objective
    itself, which is the same everywhere.
    """
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    from eda_agent.design.autonomy import autonomy_guide

    leaks = []
    for stage in autonomy_guide()["stages"]:
        for key in ("goal", "exit_gate"):
            text = stage[key]
            for word in ("Altium", "ECO", "DelphiScript", "SchLib", "PrjPcb"):
                if word in text:
                    leaks.append(f"{stage['stage']}.{key}: {text}")
    assert not leaks, (
        "these stage descriptions name an Altium-only product or "
        "mechanism on a backend that has neither:\n  " + "\n  ".join(leaks))


def test_a_name_that_prefixes_a_longer_one_is_not_corrupted():
    """The substring bug, stated as the property that must hold.

    _EQUIVALENTS maps `design_validate`, and `design_validate_plan` is
    a real tool whose name starts with it. A plain str.replace turned
    an exit gate into "design_validate_plan_plan", which nothing
    registers, so the sentence then collected a "(not available on this
    backend)" annotation telling the client its own working tool was
    missing.
    """
    from eda_agent.design.autonomy import _adapt_lines, _registered_tools

    available = _registered_tools("easyeda")
    lines = [
        "design_validate_plan ok:true; then run design_validate.",
        "lib_add_footprint_pads then lib_add_footprint_tracks.",
    ]
    out = _adapt_lines(lines, "easyeda", available)

    assert "design_validate_plan_plan" not in out[0], out[0]
    assert "design_validate_plan" in out[0], out[0]
    assert "not available on this backend" not in out[0], (
        f"a corrupted name was reported as an absent tool: {out[0]}")
    assert out[1] == "easyeda_add_pads then easyeda_add_polyline.", out[1]
