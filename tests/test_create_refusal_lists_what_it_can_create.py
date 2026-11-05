# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""obj_create's refusal lists only what obj_create can make.

obj_create makes schematic objects. Given eComponentBodyObject it answered
"Unknown object type: eComponentBodyObject" and then listed the PCB types,
eComponentBodyObject among them, because it shared the refusal that the
query and delete tools use. Now a PCB type is named as one and pointed at
the pcb_ tool that places it, and the list is the schematic types only.

The message function and the real resolvers it calls are lifted out of the
scripts and run under Free Pascal; only NormalizeTypeName and the enum
values are stand-ins.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

_PCB_TYPES = ["eTrackObject", "ePadObject", "eViaObject", "eComponentObject",
              "eArcObject", "eFillObject", "eTextObject", "ePolyObject",
              "eRegionObject", "eRuleObject", "eDimensionObject",
              "eComponentBodyObject", "eConnectionObject", "eSplitPlaneObject"]


def _code(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


def _messages(tmp_path, cases):
    """{(type, tool): message} from the real SchOnlyTypeMessage."""
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    utils, generic = _code("Utils.pas"), _code("Generic.pas")
    consts = "const " + " ".join(f"{t} = {i};" for i, t in enumerate(_PCB_TYPES, 1))
    lines = [f"  Writeln('{t}|{tool}|' + SchOnlyTypeMessage('{t}', '{tool}'));"
             for t, tool in cases]
    program = "\n".join([
        "program createmsg;", "{$mode delphi}", "uses SysUtils;", consts,
        "function NormalizeTypeName(S: String): String;",
        "begin Result := LowerCase(StringReplace(StringReplace(S, ' ', '', [rfReplaceAll]),"
        " '_', '', [rfReplaceAll]));",
        "  if (Length(Result) > 1) and (Result[1] = 'e') then Result := Copy(Result, 2, Length(Result)); end;",
        _routine(utils, "ObjectTypeFromStringPCB"),
        _routine(utils, "PCBObjectTypeNames"),
        _routine(generic, "SchObjectTypeNames"),
        _routine(generic, "SchOnlyTypeMessage"),
        "begin", *lines, "end.",
    ])
    path = tmp_path / "createmsg.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("createmsg.exe" if os.name == "nt" else "createmsg")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    out = {}
    for line in ran.stdout.splitlines():
        t, tool, msg = line.split("|", 2)
        out[(t, tool)] = msg
    return out


def test_a_pcb_type_is_named_and_the_list_is_schematic_only(tmp_path):
    cases = [(t, "obj_create") for t in _PCB_TYPES] + [("Sheet", "obj_create"),
                                                       ("eComponentBodyObject", "obj_copy")]
    msgs = _messages(tmp_path, cases)
    for t in _PCB_TYPES:
        msg = msgs[(t, "obj_create")]
        assert "is a PCB type" in msg, msg
        assert "Unknown object type" not in msg, msg
        assert "Types obj_create can take: eNetLabel" in msg, msg
        # No PCB type name may appear in the list of what it can take.
        listed = msg.split("Types obj_create can take:", 1)[1]
        assert not any(p in listed for p in _PCB_TYPES), msg
    assert "pcb_place_3d_body" in msgs[("eComponentBodyObject", "obj_create")]
    assert "pcb_place_tracks" in msgs[("eTrackObject", "obj_create")]
    assert "ratsnest" in msgs[("eConnectionObject", "obj_create")]
    assert "pcb_place" not in msgs[("eConnectionObject", "obj_create")]
    sheet = msgs[("Sheet", "obj_create")]
    assert sheet.startswith("Unknown object type: Sheet")
    assert not any(p in sheet for p in _PCB_TYPES), sheet
    copy = msgs[("eComponentBodyObject", "obj_copy")]
    assert "obj_copy works on schematic objects only" in copy
    assert "pcb_place_3d_body" not in copy, "a placing tool is not a copy"


def test_create_and_copy_use_it():
    generic = _code("Generic.pas")
    for handler, tool in (("Gen_CreateObject", "obj_create"), ("Gen_CopyObjects", "obj_copy")):
        body = _routine(generic, handler)
        assert f"SchOnlyTypeMessage(ObjTypeStr, '{tool}')" in body, handler
        assert "UnknownObjectTypeMessage" not in body, handler


def test_every_tool_it_names_is_real():
    from eda_agent.tools.pcb import register_pcb_tools
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_pcb_tools(registry)
    real = {t.name for t in asyncio.run(registry.list_tools())}
    named = set(re.findall(r"'(pcb_\w+)'", _routine(_code("Generic.pas"), "SchOnlyTypeMessage")))
    assert len(named) >= 12
    assert named <= real, f"not registered: {sorted(named - real)}"
