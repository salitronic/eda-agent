# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Via writes never leave a via without an annular ring.

pcb_normalize_vias read a template-based Routing Via rule's size fields,
which are not what such a rule checks, and set nearly every via on a board
to a pad no larger than its hole. obj_modify could not repair them: a via's
Size had no setter branch, and HoleSize matched the name, wrote nothing
and reported success. These pin the replacements: one via writer that
refuses a ring-less result and orders its two writes so none is ever
ring-less on the way, a normalize that refuses template rules, a tool that
copies a via's template link from a via that has it, and Routing Via
sizes on the rule writer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_module

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:(?:'')|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


def _fpc():
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


def _run(tmp_path, name: str, program: str) -> subprocess.CompletedProcess:
    path = tmp_path / f"{name}.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([_fpc(), str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / (f"{name}.exe" if os.name == "nt" else name)
    return subprocess.run([str(exe)], capture_output=True, text=True)


# ------------------------------------------------------------------ the writer

def test_the_via_writer_is_never_ring_less_on_the_way_and_refuses_a_ring_less_end(tmp_path):
    gen = _code("PCBGeneric.pas")
    # The stub flags any moment at which the pad is not larger than the hole.
    program = "\n".join([
        "program viageom;", "{$mode delphi}", "uses SysUtils;",
        "const c_Broadcast = 0; PCBM_BeginModify = 1; PCBM_EndModify = 2; c_NoEventData = 0;",
        "type TCoord = Integer;",
        "TVia = class FSize, FHole: Integer; Bad: Boolean; Writes: Integer; I_ObjectAddress: Integer;",
        "procedure SetSize(V: Integer); procedure SetHole(V: Integer);",
        "property Size: Integer read FSize write SetSize;",
        "property HoleSize: Integer read FHole write SetHole; end;",
        "IPCB_Via = TVia;",
        "TServer = class procedure SendMessageToRobots(A, B, C, D: Integer); end;",
        "procedure TVia.SetSize(V: Integer); begin FSize := V; Inc(Writes); if FSize <= FHole then Bad := True; end;",
        "procedure TVia.SetHole(V: Integer); begin FHole := V; Inc(Writes); if FSize <= FHole then Bad := True; end;",
        "procedure TServer.SendMessageToRobots(A, B, C, D: Integer); begin end;",
        "var PCBServer: TServer;",
        _routine(gen, "SetViaGeometry"),
        "function Check(S0, H0, S1, H1: Integer; Want: Boolean): Boolean;",
        "var V: TVia; Got: Boolean;",
        "begin V := TVia.Create; V.FSize := S0; V.FHole := H0;",
        "Got := SetViaGeometry(V, S1, H1);",
        "if Want then Result := Got and (not V.Bad) and (V.Size = S1) and (V.HoleSize = H1)",
        "else Result := (not Got) and (V.Writes = 0) and (V.Size = S0) and (V.HoleSize = H0); end;",
        "begin PCBServer := TServer.Create;",
        # 16/8 to 0.6/0.3 mm, 20/12 to 40/24 (the hole outgrows the old pad),
        # 24/12 down to 12/6, and the 12/12 damage itself repaired.
        "  if not Check(160000, 80000, 236220, 118110, True) then Halt(1);",
        "  if not Check(200000, 120000, 400000, 240000, True) then Halt(2);",
        "  if not Check(240000, 120000, 120000, 60000, True) then Halt(3);",
        "  if not Check(120000, 120000, 236220, 118110, True) then Halt(4);",
        # Refused, touching nothing: no ring, a negative ring, no hole.
        "  if not Check(200000, 120000, 120000, 120000, False) then Halt(5);",
        "  if not Check(200000, 120000, 100000, 120000, False) then Halt(6);",
        "  if not Check(200000, 120000, 200000, 0, False) then Halt(7);",
        "end.",
    ])
    assert _run(tmp_path, "viageom", program).returncode == 0


def test_obj_modify_writes_a_vias_size_and_hole_through_that_writer():
    setter = _routine(_code("PCBGeneric.pas"), "SetPCBProperty")
    for prop in ("HoleSize", "Size"):
        start = setter.index(f"Else If PropName = '{prop}' Then")
        branch = setter[start:setter.index("Else If PropName", start + 10)]
        assert "Oid = eViaObject" in branch, f"{prop} has no via branch"
        assert "SetViaGeometry(" in branch and "Refused := True" in branch
        assert "Else Matched := False" in branch
    assert not re.search(r"Via\.(?:Size|HoleSize)\s*:=", setter), "a via written around the guard"


def test_no_setter_branch_reports_a_write_it_did_not_make():
    # A branch that tests the object type and has no fallback matched the
    # name, wrote nothing for any other type, and reported success.
    setter = _routine(_code("PCBGeneric.pas"), "SetPCBProperty")
    starts = [m.start() for m in re.finditer(r"Else If \(?PropName = '", setter)]
    silent = []
    for a, b in zip(starts, starts[1:] + [len(setter)]):
        branch = setter[a:b]
        if "Oid =" in branch or "Oid <>" in branch:
            if "Matched := False" not in branch:
                silent.append(re.search(r"PropName = '(\w+)'", branch).group(1))
    assert silent == [], f"branches that succeed on a type they do not write: {silent}"


# ------------------------------------------------------------------ normalize

def test_normalize_refuses_template_rules_and_ring_less_targets_before_writing():
    body = _routine(_code("PCB.pas"), "PCB_NormalizeVias")
    template = body.index("Pos('TEMPLATE', UpperCase(Desc)) > 0")
    read = body.index("Rule.PreferedWidth")
    ring = body.index("(THole <= 0) Or (TSize <= THole)")
    write = body.index("SetViaGeometry(Via, TSize, THole)")
    assert template < read < ring < write
    assert not re.search(r"Via\.(?:Size|HoleSize)\s*:=", body), "normalize writes around the guard"
    assert "If DryRun Then" in body[ring:write]


def test_the_count_lists_survive_a_descriptor_with_equals_signs(tmp_path):
    pcb = _code("PCB.pas")
    program = "\n".join([
        "program hist;", "{$mode delphi}", "uses SysUtils, Classes;",
        # DelphiScript's TStringList publishes Get (shipped code calls it);
        # FPC's keeps it protected.
        "type TStringList = class(Classes.TStringList) public",
        "function Get(Index: Integer): string; override; end;",
        "function TStringList.Get(Index: Integer): string; begin Result := inherited Get(Index); end;",
        "function EscapeJsonString(S: String): String; begin Result := S; end;",
        _routine(pcb, "HistAdd"), _routine(pcb, "HistJson"),
        "var L: TStringList; I: Integer;",
        "begin L := TStringList.Create;",
        "  for I := 1 to 950 do HistAdd(L, '20/12');",
        "  for I := 1 to 198 do HistAdd(L, '16/8');",
        "  for I := 1 to 3 do HistAdd(L, 'template rule viaSize: Routing Via (Gap=1mil)');",
        "  WriteLn(HistJson(L));",
        "end.",
    ])
    ran = _run(tmp_path, "hist", program)
    assert ran.returncode == 0
    assert json.loads(ran.stdout) == {
        "20/12": 950, "16/8": 198, "template rule viaSize: Routing Via (Gap:1mil)": 3}


# ------------------------------------------------------------------ template copy

def test_the_template_copy_follows_the_reference_pattern_behind_its_guards():
    body = _routine(_code("PCB.pas"), "PCB_ApplyViaTemplate")
    link = body.index("Src.TemplateLink.CopyTo(DstT)")
    assert body.index("Client.GetProductVersion") < link
    assert body.index("If Major < 22 Then") < link
    assert body.index("Src.Mode <> ePadMode_Simple") < link
    assert body.index("Src.Size <= Src.HoleSize") < link
    assert "DstT := Via.TemplateLink;" in body
    assert re.search(r"If Ok And \(Via\.Size = Src\.Size\) And \(Via\.HoleSize = Src\.HoleSize\)", body)
    assert body.index("BoardIterator_Destroy(") < link, "a via changes while the iterator walks"
    pcb = _code("PCB.pas")
    assert "'apply_via_template':" in pcb and "PCB_ApplyViaTemplate(Params, RequestId)" in pcb


# ------------------------------------------------------------------ rule writer

def test_the_routing_via_sizes_are_written_through_a_narrowed_local_and_read_back():
    body = _routine(_code("PCB.pas"), "PCB_SetRuleProperties")
    assert "RuleViaIter := Iter.FirstPCBObject;" in body
    check = body.index("every via diameter must be larger than its hole")
    for prop in ("MinWidth", "MaxWidth", "PreferedWidth", "MinHoleWidth",
                 "MaxHoleWidth", "PreferedHoleWidth"):
        write = body.index(f"RuleViaIter.{prop} := ")
        assert check < write, f"{prop} written before the set is checked"
        assert re.search(rf"If RuleViaIter\.{prop} = N\w+ Then Inc\(ViaWritten\)", body)


# ------------------------------------------------------------------ Python tools

class _Bridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        # The rule tools read the rules before and after a write to log
        # what changed; not the command under test.
        if command in ("pcb.get_design_rules", "generic.query_objects"):
            return {"rules": [], "objects": []}
        self.calls.append((command, params or {}))
        return {"success": True}


def _tools(monkeypatch, bridge):
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    pcb_module.register_pcb_tools(DummyMcp())
    return captured


@pytest.mark.asyncio
async def test_normalize_forwards_targets_and_filters(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["pcb_normalize_vias"]
    await tool(size_mils=23.622, hole_mils=11.811, from_size_mils=12, net="GND", dry_run=True)
    assert bridge.calls[-1] == ("pcb.normalize_vias", {
        "dry_run": "true", "size_mils": "23.622", "hole_mils": "11.811",
        "from_size_mils": "12.0", "net": "GND"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"size_mils": 20},
    {"hole_mils": 12},
    {"size_mils": 12, "hole_mils": 12},
    {"size_mils": 10, "hole_mils": 12},
    {"from_size_mils": 0},
])
async def test_normalize_refuses_a_call_without_a_ring_before_sending(monkeypatch, kwargs):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["pcb_normalize_vias"]
    assert (await tool(**kwargs))["ok"] is False
    assert bridge.calls == []


@pytest.mark.asyncio
async def test_apply_via_template_forwards_the_source_and_filters(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["pcb_apply_via_template"]
    await tool(source_x=1200, source_y=850.5, from_hole_mils=12)
    assert bridge.calls[-1] == ("pcb.apply_via_template", {
        "source_x": "1200.0", "source_y": "850.5", "dry_run": "false",
        "from_hole_mils": "12.0"})
    n = len(bridge.calls)
    assert (await tool(source_x=0, source_y=0, from_size_mils=-1))["ok"] is False
    assert len(bridge.calls) == n


@pytest.mark.asyncio
async def test_the_rule_writer_forwards_routing_via_sizes(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["pcb_set_rule_properties"]
    await tool(name="RoutingVias", preferred_via_size_mils=23.622, preferred_via_hole_mils=11.811)
    params = bridge.calls[-1][1]
    assert params["preferred_via_size_mils"] == "23.622"
    assert params["preferred_via_hole_mils"] == "11.811"
