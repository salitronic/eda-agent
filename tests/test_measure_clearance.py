# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""How far apart two board objects are, and the clearance rule on the pair.

pcb_find_dominant_rule names the rule that governs a pair but not the
distance between them, nor whether that distance breaks the rule; the
only answer to "is this pair too close" was a whole DRC run. The new
command measures with Altium's own PrimPrimDistance, finds the rule with
FindDominantRuleForObjectPair(eRule_Clearance) and asks the rule itself
(ActualCheck), the check the online DRC makes.

A distance needs one primitive on each side, so a whole component or a
net is refused, and a "<type>:<filter>" spec must match exactly one
object: the distance to "the first of several" answers nothing.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _pcb(name):
    return load(_ALTIUM / "PCB.pas")[name]


def test_handler_measures_finds_the_clearance_rule_and_checks_it():
    body = _pcb("PCB_MeasureClearance")
    dist = body.index("D := Board.PrimPrimDistance(P1, P2);")
    rule = body.index("Board.FindDominantRuleForObjectPair(P1, P2, eRule_Clearance)")
    check = body.index("Violation := Rule.ActualCheck(P1, P2);")
    assert dist < rule < check
    # the check only runs with a rule, and a missing rule reads as no violation
    assert "Violated := False;" in body[:check]
    assert body.rindex("If Rule <> Nil Then", 0, check) > rule
    assert "Violated := Violation <> Nil;" in body[check:]
    assert "RuleJson := 'null';" in body
    for field in ("JsonFloat('distance_mils', D / 10000.0)",
                  "JsonFloat('distance_mm', CoordToMM(D))",
                  "JsonBool('touching', D = 0)",
                  "JsonRaw('rule', RuleJson)",
                  "JsonRaw('gap_mm', GapStr)",
                  "JsonBool('violation', Violated)"):
        assert field in body
    assert "RuleJson := RuleSummaryJson(Rule);" in body
    assert "GapStr := ClearanceGapMM(Board, RuleName);" in body


def test_with_no_rule_the_rule_whose_scopes_take_the_pair_is_named():
    # Altium finds no rule for two objects with no net under a rule on
    # different nets only; the reply names that rule and its net scope
    # instead of a bare null.
    body = _pcb("PCB_MeasureClearance")
    fallback = body[body.index("If Rule = Nil Then"):]
    assert "If ClearanceRuleByScope(Board, P1, P2, ScopedJson, ScopedGap, NetOk) Then" in fallback
    for field in ("JsonRaw('scoped_rule', ScopedJson)",
                  "JsonFloat('scoped_gap_mm', CoordToMM(ScopedGap))",
                  "JsonBool('net_scope_matches', NetOk)",
                  "JsonBool('below_scoped_gap', D < ScopedGap)"):
        assert field in fallback
    assert "JsonBool('violation', Violated) + Extra" in body

    scope = _pcb("ClearanceRuleByScope")
    # a clearance-typed iterator return, so Gap reads (no local narrowing)
    assert "ClearRule : IPCB_ClearanceConstraint;" in scope
    assert "ClearRule := Iter.FirstPCBObject;" in scope
    assert "(Kind = eRule_Clearance) And ClearRule.Enabled" in scope
    assert "(ClearRule.Scope1Includes(P1) And ClearRule.Scope2Includes(P2))" in scope
    assert "Or (ClearRule.Scope1Includes(P2) And ClearRule.Scope2Includes(P1))" in scope
    assert "If Takes And (Pri < Best) Then" in scope
    assert "NetOk := ClearRule.NetScopeMatches(P1, P2);" in scope
    assert "Board.BoardIterator_Destroy(Iter);" in scope


def test_both_objects_go_through_the_primitive_resolver():
    body = _pcb("PCB_MeasureClearance")
    assert "P1 := ResolveClearanceObject(Board, Spec1, Problem);" in body
    assert "P2 := ResolveClearanceObject(Board, Spec2, Problem);" in body
    assert "ResolveRuleObject(" not in body
    # each refusal stops before anything is measured
    measure = body.index("PrimPrimDistance")
    for obj in ("object1", "object2"):
        refuse = body.index(f"'NO_OBJECT', '{obj}: ' + Problem")
        assert refuse < measure
        assert "Exit;" in body[refuse:body.index("End;", refuse)]


def test_type_filter_form_needs_exactly_one_match():
    body = _pcb("ResolveClearanceObject")
    collect = body.index("CollectPCBMatches(Board, T, FilterStr, 2, Matches);")
    assert "T := ObjectTypeFromStringPCB(TypeStr);" in body[:collect]
    zero = body.index("If Matches.Count = 0 Then", collect)
    many = body.index("Else If Matches.Count > 1 Then", collect)
    take = body.index("Result := Matches.Items[0];", collect)
    assert zero < many < take
    assert "matched 0 objects" in body[zero:many]
    assert "ambiguous" in body[many:take]
    assert "Matches.Free" not in body


def test_components_and_nets_are_refused():
    body = _pcb("ResolveClearanceObject")
    net = body.index("If LowerCase(TypeStr) = 'net' Then")
    comp = body.index("If T = eComponentObject Then")
    collect = body.index("CollectPCBMatches(")
    assert net < comp < collect
    for gate in (net, comp):
        block = body[gate:body.index("End;", gate)]
        assert "Problem :=" in block and "Exit;" in block
        assert "Result :=" not in block
    # a bare designator ("U1") is a component: refused before the pad resolver
    bare = body.index("If Pos('.', Spec) = 0 Then")
    pad = body.index("Result := ResolveRuleObject(Board, Spec, Problem);")
    assert bare < pad
    block = body[bare:body.index("End;", bare)]
    assert "Problem :=" in block and "Exit;" in block


def test_registered_in_the_dispatcher():
    body = _pcb("HandlePCBCommand")
    assert "'measure_clearance':" in body
    line = body[body.index("'measure_clearance':"):]
    assert line.split("\n", 1)[0].rstrip().endswith(
        "Result := PCB_MeasureClearance(Params, RequestId);")


def _tool(monkeypatch):
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params, **kwargs):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb_tools.register_pcb_tools(_Mcp())
    return captured["pcb_measure_clearance"], sent


def test_tool_passes_both_objects(monkeypatch):
    tool, sent = _tool(monkeypatch)
    asyncio.run(tool(object1=" U1.3 ",
                     object2="track:Layer=TopLayer|X1=100|Y1=200|X2=300|Y2=200"))
    assert sent == [("pcb.measure_clearance",
                     {"object1": "U1.3",
                      "object2": "track:Layer=TopLayer|X1=100|Y1=200|X2=300|Y2=200"})]


def test_tool_refuses_an_empty_object(monkeypatch):
    tool, sent = _tool(monkeypatch)
    for a, b in (("", "U1.3"), ("U1.3", "  ")):
        result = asyncio.run(tool(object1=a, object2=b))
        assert result["ok"] is False
    assert sent == []
