# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The autonomy guide names tools the active backend actually has.

MEASURED against a live editor: the stage playbooks were written against Altium
and name Altium tools. On the EasyEDA backend 33 of the 50 named
across 13 stages were not registered, and SIX stages named nothing
that existed there. An agent following the guide was told to call
pcb_place_components, proj_compare_sch_pcb and the Altium plan
executor on a backend that has none of them.

The fix has two halves and this file guards both. Tools with a known
equivalent are SUBSTITUTED, and the substitute must be a tool the
backend really registers, or the guide has just renamed the dead end.
Tools with no equivalent are NAMED as absent rather than dropped,
because "there is no tool for this here" is guidance and a silently
short list is a puzzle.
"""
from __future__ import annotations

import pytest

from eda_agent.design.autonomy import _EQUIVALENTS, autonomy_guide
from eda_agent.design.state_machine import STAGE_PLAYBOOKS


def _registered(backend: str) -> set:
    from eda_agent.tools import register_backend

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_backend(_Mcp(), backend)
    return set(captured)


@pytest.mark.parametrize("backend", ["altium", "easyeda", "kicad"])
def test_every_tool_the_guide_names_exists_on_that_backend(
        backend, monkeypatch):
    monkeypatch.setenv("EDA_AGENT_BACKEND", backend)
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    available = _registered(backend)
    guide = autonomy_guide()
    assert guide["backend"] == backend

    for stage in guide["stages"]:
        # A family tool is named with its kind: "pcb_calc kind=impedance".
        unknown = [t for t in stage["tools"] if t.split()[0] not in available]
        assert not unknown, (
            f"the {backend} guide tells stage {stage['stage']!r} to call "
            f"{unknown}, which that backend does not register")


@pytest.mark.parametrize("backend", ["easyeda", "kicad"])
def test_a_substitute_is_never_a_tool_the_backend_lacks(backend):
    """A mapping to a tool nobody has is worse than no mapping: it
    reads as available."""
    available = _registered(backend)
    used_here = {v for v in _EQUIVALENTS.values() if v in available}
    assert used_here, (
        f"no equivalent in the table exists on {backend}, so the "
        f"substitution does nothing there")


def test_the_equivalents_table_has_no_dead_entries():
    """Every mapping must point at a tool SOME backend registers, and
    map a tool that some stage actually names."""
    everywhere = set()
    for backend in ("altium", "easyeda", "kicad"):
        everywhere |= _registered(backend)

    dead = sorted(v for v in _EQUIVALENTS.values() if v not in everywhere)
    assert not dead, (
        f"the equivalents table points at {dead}, which no backend "
        f"registers")

    # The table has THREE consumers: the stage playbooks, the
    # discipline document, and the loop protocol / hard constraints.
    # An entry reachable from none of them can never fire, which is
    # the drift worth catching. This guard has now fired three times,
    # each time a consumer was added without widening the condition,
    # which is precisely its job.
    #
    # The third firing was the interesting one: the guard shared a
    # blind spot with the code it guards. Both matched a name only when
    # a CLOSING backtick followed it, so the three one-call generators,
    # written as `lib_create_ic_symbol(name, ...)`, were invisible to
    # the substitution and equally invisible here. The guard therefore
    # reported the new mappings as unreachable when they were reachable
    # and, until minutes earlier, genuinely unreached. A guard that
    # parses the document the same wrong way as the shipping code
    # cannot catch that class of bug at all.
    import re

    from eda_agent.design.autonomy import HARD_CONSTRAINTS, LOOP_PROTOCOL
    from eda_agent.design.discipline import _DISCIPLINE

    reachable = {t for spec in STAGE_PLAYBOOKS.values()
                 for t in spec["tools"]}
    reachable |= set(re.findall(r"`([a-z]+_[a-z0-9_]+)[`(]", _DISCIPLINE))
    prose = " ".join(LOOP_PROTOCOL + HARD_CONSTRAINTS)
    reachable |= set(re.findall(r"\b([a-z]+_[a-z0-9_]+)\b", prose))

    unused = sorted(set(_EQUIVALENTS) - reachable)
    assert not unused, (
        f"the equivalents table maps {unused}, which neither a stage "
        f"nor the discipline names, so the entries can never be "
        f"reached")


def test_the_equivalents_table_declares_no_key_twice():
    """Python keeps the LAST of two identical keys and says nothing.

    Editing the table produced exactly that: pcb_place_via appeared
    twice, and the dict at runtime looked perfectly normal. A second
    entry silently overriding the first is how a mapping starts
    pointing somewhere nobody intended, so the source is parsed rather
    than the dict inspected.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "eda_agent"
              / "design" / "autonomy.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    seen: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "_EQUIVALENTS" not in targets:
            continue
        assert isinstance(node.value, ast.Dict)
        seen = [k.value for k in node.value.keys
                if isinstance(k, ast.Constant)]

    assert seen, "the equivalents table was not found in the source"
    duplicates = sorted({k for k in seen if seen.count(k) > 1})
    assert not duplicates, (
        f"the equivalents table declares {duplicates} more than once; "
        f"Python keeps the last silently")


def test_no_stage_is_left_without_a_tool_on_easyeda(monkeypatch):
    """Six stages were dead ends before the substitutions. If a stage
    goes empty again the guide should say so loudly here, not quietly
    to a user mid-design."""
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    empty = [s["stage"] for s in autonomy_guide()["stages"]
             if not s["tools"]]
    assert not empty, (
        f"these stages offer no tool on EasyEDA: {empty}. Either add an "
        f"equivalent, or accept it and let the stage carry its note.")


def test_an_absent_tool_is_named_rather_than_dropped(monkeypatch):
    """A stage that loses a tool with no equivalent must say which."""
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    guide = autonomy_guide()
    reported = [s for s in guide["stages"]
                if s.get("tools_not_on_this_backend")]
    assert reported, (
        "no stage reported an absent tool, yet EasyEDA lacks several "
        "the playbooks name; they are being dropped silently")
    for stage in reported:
        assert stage["tools_not_on_this_backend"], stage


def test_the_loop_protocol_names_the_local_checkpoint_tool(monkeypatch):
    """Step 3 tells the client to checkpoint before an autonomous run.

    On EasyEDA it named app_checkpoint, which does not exist there, so
    the one instruction that makes a run revertible was the one a
    reader could not follow.
    """
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    loop = autonomy_guide()["loop"]
    checkpoint_steps = [ln for ln in loop if "checkpoint" in ln]
    assert checkpoint_steps, "the loop no longer mentions checkpointing"
    assert any("easyeda_checkpoint" in ln for ln in checkpoint_steps)
    assert not any("app_checkpoint" in ln for ln in checkpoint_steps), (
        "the loop still tells an EasyEDA client to call app_checkpoint")


def test_a_name_with_no_equivalent_is_left_in_the_sentence(monkeypatch):
    """Deleting it would leave a hole.

    design_job_start has no EasyEDA counterpart. The sentence should
    still read as a sentence; the stage entries are where absences are
    reported explicitly.
    """
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    loop = autonomy_guide()["loop"]
    job_steps = [ln for ln in loop if "design_job_start" in ln]
    assert job_steps, (
        "the long-run step lost its tool name instead of keeping it")
    assert "design_job_status" in " ".join(job_steps)


def test_a_step_whose_capability_is_absent_says_so(monkeypatch):
    """Naming an absent tool is one thing; ADVISING a capability that
    does not exist is another.

    Step 6 tells a client to start a background job and poll it.
    EasyEDA has no job system, so the advice cannot be taken and the
    step says as much.
    """
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    loop = autonomy_guide()["loop"]
    job_step = [ln for ln in loop if "design_job_start" in ln]
    assert job_step, "the long-run step disappeared"
    assert "not available on this backend" in job_step[0]


def test_a_stage_name_is_not_mistaken_for_an_absent_tool(monkeypatch):
    """Step 5 lists sch_to_pcb, routing and pours_tuning as STAGES to
    checkpoint before. Reading sch_to_pcb as a tool annotated the step
    as unavailable, when the tool it actually names, easyeda_checkpoint,
    is right there in the sentence."""
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    loop = autonomy_guide()["loop"]
    step = [ln for ln in loop if "Checkpoint again" in ln]
    assert step, "the checkpoint-again step disappeared"
    assert "not available on this backend" not in step[0], (
        "a step naming only STAGE names was marked unavailable")


def test_the_altium_loop_text_is_untouched(monkeypatch):
    monkeypatch.setenv("EDA_AGENT_BACKEND", "altium")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    from eda_agent.design.autonomy import LOOP_PROTOCOL
    assert autonomy_guide()["loop"] == list(LOOP_PROTOCOL)


def test_the_altium_guide_is_unchanged_by_the_filtering(monkeypatch):
    """The playbooks were written for Altium, so nothing should be
    substituted or dropped there."""
    monkeypatch.setenv("EDA_AGENT_BACKEND", "altium")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})

    from eda_agent.tools.folds import call_form

    for stage in autonomy_guide()["stages"]:
        wanted = list(dict.fromkeys(
            call_form(t) for t in STAGE_PLAYBOOKS[stage["stage"]]["tools"]))
        assert stage["tools"] == wanted, (
            f"the Altium guide altered stage {stage['stage']!r}")
        assert "tools_not_on_this_backend" not in stage
