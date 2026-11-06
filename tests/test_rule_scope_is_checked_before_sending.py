# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A rule scope Altium would misread is refused before it is sent.

Reported: pcb_create_design_rule with scope "IsPad AND HoleSize > 3"
raised "; expected at Line:2, Char:76. Continue execution?", the dialog
came back on every call, the rule was created anyway, and the Rules
editor later said some rules had incorrect definitions while DRC quietly
skipped them.

The scope is not spliced into script text: the handler assigns it to
Scope1Expression. Altium's own query engine compiles it, and its
precedence (Query Language Reference TR0110, "Brackets and Order of
Precedence") puts And, Or, Xor and Not above the comparisons, so that
scope reads as "(IsPad AND HoleSize) > 3". The tools now say so, with the
bracketed form, and send nothing.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_module
from eda_agent.tools.pcb import scope_problem

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


@pytest.mark.parametrize("scope,fixed", [
    ("IsPad AND HoleSize > 3", "IsPad AND (HoleSize > 3)"),
    ("IsVia and HoleSize < 12", "IsVia and (HoleSize < 12)"),
    ("A > 3 Or B <= 4", "(A > 3) Or (B <= 4)"),
    ("Not HoleSize >= 3", "Not (HoleSize >= 3)"),
    ("Name Like 'ADDR*' And IsPad", "(Name Like 'ADDR*') And IsPad"),
    ("IsPad And (OnLayer('Top') Or HoleSize<>0)",
     "IsPad And (OnLayer('Top') Or (HoleSize<>0))"),
])
def test_a_misread_scope_is_named_with_its_bracketed_form(scope, fixed):
    reason, suggested = scope_problem(scope)
    assert reason and "Nothing was sent" in reason
    assert suggested == fixed
    assert scope_problem(suggested) == ("", ""), "the suggestion must pass"


@pytest.mark.parametrize("scope", [
    "All", "IsVia", "InNet('GND')", "InNetClass('Power')",
    "IsDifferentialPair", "InDifferentialPair('USB')",
    "IsPad And (HoleSize > 3)", "IsPad && HoleSize > 3",
    "IsPad || HoleSize > 3", "HoleSize > 3", "HoleSize Between 30 And 50",
    "((IsPad And OnMultiLayer) Or IsVia) And (HoleDiameter < 16)",
    "Name = 'A And B'", "IsPad And Not IsVia",
])
def test_scopes_altium_reads_as_written_pass(scope):
    assert scope_problem(scope) == ("", "")


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        # The rule tools read the rules before and after a write to log
        # what changed; not the command under test.
        if command in ("pcb.get_design_rules", "generic.query_objects"):
            return {"rules": [], "objects": []}
        self.calls.append((command, params or {}))
        return {"created": True}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    pcb_module.register_pcb_tools(DummyMcp())
    return captured, bridge


def test_create_design_rule_refuses_before_sending(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["pcb_create_design_rule"](
        name="Big holes", scope="IsPad AND HoleSize > 3"))
    assert out["success"] is False and out["error"] == "BAD_SCOPE"
    assert out["suggested_scope"] == "IsPad AND (HoleSize > 3)"
    assert bridge.calls == [], "nothing may reach Altium"


def test_create_design_rule_sends_a_good_scope_unchanged(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["pcb_create_design_rule"](
        name="Big holes", scope="IsPad AND (HoleSize > 3)"))
    assert bridge.calls[0][1]["scope"] == "IsPad AND (HoleSize > 3)"


@pytest.mark.parametrize("field", ["scope1", "scope2"])
def test_set_rule_properties_refuses_before_sending(monkeypatch, field):
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["pcb_set_rule_properties"](
        name="Big holes", **{field: "IsVia And HoleSize < 12"}))
    assert out["error"] == "BAD_SCOPE" and out["properties_updated"] == 0
    assert bridge.calls == []


def test_the_scope_is_assigned_not_spliced_into_script_text():
    """Why the fix is a check and not an escape: nothing to escape."""
    text = (SCRIPTS / "PCB.pas").read_text(encoding="utf-8", errors="replace")
    start = text.index("Function PCB_CreateDesignRule")
    body = text[start:text.index("\nEnd;", start)]
    assignments = re.findall(r"\.Scope1Expression\s*:=\s*(\w+)\s*;", body)
    assert assignments and set(assignments) == {"ScopeStr"}
    assert "RunScriptText" not in body and "RunScript" not in body
