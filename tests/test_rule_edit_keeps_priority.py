# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Editing a rule keeps every rule's priority, and priority can be set.

MEASURED on AD 25.8.1 (a copy of a test board with 55 clearance rules):
pcb_set_rule_properties changing only a rule's comment moved that rule to
priority 1 of its kind, and pcb_find_dominant_rule then answered it in
place of the narrower rule that had been first. PCBM_BeginModify and
PCBM_EndModify sent to a rule with NOTHING written did the same, so every
write the handler brackets moved the rule.

Rule.Priority is a read-only function, but every rule carries a stored
sort key: Rule.SetState_Priority writes it and Board.SortRulesByPriority
re-orders every rule of every kind by it. The sort does not renumber the
keys and does not keep equal keys in order, and a rule whose place had
drifted from its key went back to the key, so the handler writes the key
of EVERY rule (each from its place before the edit) and sorts once. The
new order decided the dominant rule and was kept over save and reopen.

The place arithmetic is lifted out of PCB.pas and run under Free Pascal;
the order of the steps is read from the source.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.registry import ToolRegistry

PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _code() -> str:
    return PCB.read_text(encoding="utf-8", errors="replace")


def _routine(code: str, kind: str, name: str) -> str:
    """One routine's text with comments blanked (string literals kept)."""
    start = re.search(rf"(?m)^{kind} {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body,
                  flags=re.S)


def _run(tmp_path, lines: list[str]) -> dict[str, str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    code = _code()
    program = "\n".join([
        "program ruleprio;", "{$mode delphi}", "uses SysUtils, Classes;",
        _routine(code, "Function", "RulePriorityAsked"),
        _routine(code, "Function", "RulePriorityWanted"),
        "var Names, Kinds, Places, Wanted : TStringList;",
        "    Why : String;",
        "procedure Fill;",
        "begin",
        "  Names := TStringList.Create; Kinds := TStringList.Create;",
        "  Places := TStringList.Create; Wanted := TStringList.Create;",
        # walk order is not place order: kind 0 holds A..D, kind 2 X and Y
        "  Names.Add('C'); Kinds.Add('0'); Places.Add('3');",
        "  Names.Add('X'); Kinds.Add('2'); Places.Add('1');",
        "  Names.Add('A'); Kinds.Add('0'); Places.Add('1');",
        "  Names.Add('D'); Kinds.Add('0'); Places.Add('4');",
        "  Names.Add('Y'); Kinds.Add('2'); Places.Add('2');",
        "  Names.Add('B'); Kinds.Add('0'); Places.Add('2');",
        "end;",
        "procedure Show(Tag, Name : String; MoveTo : Integer);",
        "var I : Integer; S : String;",
        "begin",
        "  Fill;",
        "  Why := RulePriorityWanted(Names, Kinds, Places, Wanted, Name, MoveTo);",
        "  S := '';",
        "  for I := 0 to Wanted.Count - 1 do",
        "    S := S + Names[I] + '=' + Wanted[I] + ' ';",
        "  Writeln(Tag + '|' + Trim(S) + '|' + Why);",
        "end;",
        "begin",
        *lines,
        "end.",
    ])
    path = tmp_path / "ruleprio.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True,
                           text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("ruleprio.exe" if os.name == "nt" else "ruleprio")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    out = {}
    for line in ran.stdout.splitlines():
        key, value = line.split("|", 1)
        out[key] = value
    return out


def test_priority_text_is_a_whole_number_from_1(tmp_path):
    cases = {"": 0, "1": 1, "12": 12, "9999": 9999, "0": -1, "-1": -1,
             "x": -1, "2.5": -1, "99999": -1, "99999999999": -1}
    out = _run(tmp_path, [f"  Writeln('[{t}]|' + IntToStr(RulePriorityAsked('{t}')));"
                          for t in cases])
    for text, want in cases.items():
        assert out[f"[{text}]"] == str(want), text


def test_with_nothing_asked_every_rule_keeps_its_place(tmp_path):
    out = _run(tmp_path, ["  Show('none', 'A', 0);"])
    assert out["none"] == "C=3 X=1 A=1 D=4 Y=2 B=2|"


def test_moving_a_rule_up_or_down_shifts_only_its_kind(tmp_path):
    out = _run(tmp_path, ["  Show('up', 'D', 1);", "  Show('down', 'A', 3);",
                          "  Show('same', 'B', 2);", "  Show('last', 'A', 4);"])
    # D to the top: A, B and C each move down one; X and Y are another kind
    assert out["up"] == "C=4 X=1 A=2 D=1 Y=2 B=3|"
    # A to third: B and C move up one, D stays last
    assert out["down"] == "C=2 X=1 A=3 D=4 Y=2 B=1|"
    assert out["same"] == "C=3 X=1 A=1 D=4 Y=2 B=2|"
    assert out["last"] == "C=2 X=1 A=4 D=3 Y=2 B=1|"


def test_a_place_past_the_last_or_an_unknown_rule_is_refused(tmp_path):
    out = _run(tmp_path, ["  Show('past', 'A', 5);", "  Show('other', 'Y', 3);",
                          "  Show('gone', 'Q', 1);"])
    assert out["past"].endswith("|priority 5 is past the last place: this kind has 4 rule(s)")
    assert out["other"].endswith("|priority 3 is past the last place: this kind has 2 rule(s)")
    assert out["gone"].endswith("|the rule was not among the rules read")


def _handler() -> str:
    return _routine(_code(), "Function", "PCB_SetRuleProperties")


def test_places_are_read_before_the_first_write_and_a_bad_place_writes_nothing():
    body = _handler()
    first_write = body.index("PcbBeginEdit(")
    asked = body.index("RulePriorityAsked(ExtractJsonValue(Params, 'priority'))")
    record = body.index("RulePriorityRecord(")
    wanted = body.index("RulePriorityWanted(")
    assert asked < record < wanted < first_write
    refusals = [m.start() for m in re.finditer("'BAD_PRIORITY'", body)]
    assert len(refusals) == 2 and all(r < first_write for r in refusals)
    # the lists made for the record are freed when the place is refused
    second = body[refusals[0]:refusals[1]]
    for lst in ("PrioNames", "PrioKinds", "PrioPlaces", "PrioWanted"):
        assert f"{lst}.Free" in second


def test_every_way_out_after_a_write_puts_the_places_back():
    body = _handler()
    first_write = body.index("PcbBeginEdit(")
    done = [m.start() for m in re.finditer(r"RulePriorityDone\(", body)]
    assert len(done) == 2 and all(d > first_write for d in done)
    assert done[0] < body.index("'WRONG_RULE_KIND'")
    assert done[1] > body.index("RuleLimitsReport(")
    assert done[1] < body.index("BuildSuccessResponse(")
    assert "LimitsReport + PrioReport + '}'" in body
    assert "',\"priority_set\":'" in body


def test_the_keys_of_all_rules_are_written_then_sorted_once_then_read_back():
    done = _routine(_code(), "Function", "RulePriorityDone")
    first_read = done.index("RulePriorityOff(")
    write = done.index("RulePriorityWrite(")
    sort = done.index("Board.SortRulesByPriority")
    read_back = done.index("RulePriorityOff(", sort)
    assert first_read < write < sort < read_back
    # nothing is written while every rule is already in place
    assert re.search(r"If Moved > 0 Then\s+Begin\s+RulePriorityWrite\(", done)
    for lst in ("Names", "Kinds", "Places", "Wanted"):
        assert f"{lst}.Free" in done
    write_body = _routine(_code(), "Procedure", "RulePriorityWrite")
    assert "Rule.SetState_Priority(" in write_body
    # every kind: a drifted rule of another kind would go back to its key
    assert "RuleKind" not in write_body
    assert "AddFilter_ObjectSet(MkSet(eRuleObject))" in write_body


def test_priority_itself_is_never_assigned():
    assert not re.search(r"\.Priority\s*:=", re.sub(r"\{[^}]*\}", " ", _code()))


class _Bridge:
    def __init__(self, before=None, after=None):
        self.sent = []
        self.reads = [before or [], after or []]

    async def send_command_async(self, command, params=None, **kwargs):
        if command == "pcb.get_design_rules":
            rows = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
            return {"rules": rows}
        if command == "generic.query_objects":
            return {"objects": []}
        self.sent.append((command, params or {}))
        return {"name": "A", "rule_kind": 0, "properties_updated": 1,
                "priority_kept": True, "places_restored": 1}


def _rule(name, priority):
    return {"name": name, "rule_kind": 0, "enabled": True, "priority": priority,
            "scope_1": "All", "scope_2": "All", "comment": "", "descriptor": ""}


def _tool(monkeypatch, bridge):
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: bridge)
    monkeypatch.setattr("eda_agent.export.drc_rules.log_change",
                        lambda *a, **k: None)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    found = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    return found["pcb_set_rule_properties"]


def test_priority_is_sent_and_alone_is_enough(monkeypatch):
    bridge = _Bridge()
    tool = _tool(monkeypatch, bridge)
    asyncio.run(tool(name="A", priority=2))
    assert bridge.sent == [("pcb.set_rule_properties", {"name": "A", "priority": "2"})]


@pytest.mark.parametrize("bad", [0, -3, True])
def test_a_priority_below_1_is_refused_before_sending(monkeypatch, bad):
    bridge = _Bridge()
    tool = _tool(monkeypatch, bridge)
    out = asyncio.run(tool(name="A", priority=bad))
    assert out["ok"] is False and "priority counts from 1" in out["reason"]
    assert bridge.sent == []


def test_an_edit_that_reorders_rules_says_so_at_the_top(monkeypatch):
    bridge = _Bridge([_rule("A", 2), _rule("B", 1)], [_rule("A", 1), _rule("B", 2)])
    tool = _tool(monkeypatch, bridge)
    out = asyncio.run(tool(name="A", comment="x"))
    assert "A 2 -> 1" in out["priority_warning"]
    assert "B 1 -> 2" in out["priority_warning"]


def test_no_warning_when_the_order_was_asked_for_or_kept(monkeypatch):
    moved = _Bridge([_rule("A", 2), _rule("B", 1)], [_rule("A", 1), _rule("B", 2)])
    out = asyncio.run(_tool(monkeypatch, moved)(name="A", priority=1))
    assert "priority_warning" not in out
    kept = _Bridge([_rule("A", 2), _rule("B", 1)], [_rule("A", 2), _rule("B", 1)])
    out = asyncio.run(_tool(monkeypatch, kept)(name="A", comment="x"))
    assert "priority_warning" not in out
