# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_set_rule_properties writes a Polygon Connect relief angle Altium keeps.

SetState_PolygonReliefAngle takes the TPolygonReliefAngle value, not
degrees: 45 written as a number leaves the rule reading "Relief
Angle=Polygon" and 90 leaves it with an empty descriptor. 45 and 90 go in
as ePolygonReliefAngle_45 (0) and ePolygonReliefAngle_90 (1); any other
value is refused before anything is written, and the angle is read back
from the rule's descriptor.

The degrees-to-value helper is lifted out of PCB.pas and run under Free
Pascal; the handler is checked for where it uses it.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body() -> str:
    return load(_PCB)["PCB_SetRuleProperties"]


def test_the_setter_gets_the_enum_value_not_degrees():
    body = _body()
    assert "RulePolyConn.SetState_PolygonReliefAngle(AngleNo);" in body
    assert "StrToIntDef(CAngle" not in body, "degrees reach the setter again"
    assert "AngleNo := PolygonReliefAngleNo(CAngle);" in body


def test_an_angle_altium_does_not_keep_is_refused_before_the_write():
    body = _body()
    refusal = "ConnProblem := 'relief_angle must be 45 or 90 (degrees)';"
    assert refusal in body
    assert "If (ConnProblem = '') And (CAngle <> '') And (AngleNo < 0) Then" in body
    assert body.index(refusal) < body.index("RulePolyConn := Iter.FirstPCBObject;")


def test_the_angle_is_read_back_from_the_descriptor():
    body = _body()
    assert "Pos('RELIEFANGLE=' + Trim(CAngle) + 'ANGLE'," in body
    assert "UpperCase(StringReplace(Desc, ' ', '', -1))" in body
    assert body.index("Desc := Rule.Descriptor;") < body.index("relief_angle_confirmed")
    assert ',"relief_angle_confirmed":true' in body
    assert ',"relief_angle_confirmed":false' in body


def _helper() -> str:
    code = _PCB.read_text(encoding="utf-8", errors="replace")
    start = re.search(r"(?m)^Function PolygonReliefAngleNo\b", code)
    assert start, "missing PolygonReliefAngleNo"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _fpc() -> str:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


#: (text sent, value written). -1 is refused, nothing written.
CASES = [
    ("45", 0), ("90", 1), (" 90 ", 1),
    ("0", -1), ("135", -1), ("180", -1), ("45.0", -1), ("", -1),
    ("x", -1), ("99999999999", -1),
]


def test_only_45_and_90_have_a_value(tmp_path):
    fpc = _fpc()
    lines = ["program reliefangle;", "{$mode delphi}", "uses SysUtils;",
             _helper(), "begin"]
    for text, _ in CASES:
        lines.append(f"  WriteLn(PolygonReliefAngleNo('{text}'));")
    lines.append("end.")
    path = tmp_path / "reliefangle.pas"
    path.write_text("\n".join(lines), encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("reliefangle.exe" if os.name == "nt" else "reliefangle")
    got = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
    assert [int(v) for v in got] == [want for _, want in CASES]


def _tool(monkeypatch, reply):
    from eda_agent.tools import pcb as pcb_tools
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            if command in ("pcb.get_design_rules", "generic.query_objects"):
                return {"rules": [], "objects": []}
            sent.append((command, dict(params or {})))
            return dict(reply)

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    pcb_tools.register_pcb_tools(_Mcp())
    return tools["pcb_set_rule_properties"], sent


@pytest.mark.parametrize("angle", [30, 0, 135, 180, True])
def test_the_tool_refuses_other_angles_without_a_call(monkeypatch, angle):
    tool, sent = _tool(monkeypatch, {})
    out = asyncio.run(tool("PolygonConnect", relief_angle=angle))
    assert out["ok"] is False and "45 or 90" in out["reason"]
    assert sent == []


@pytest.mark.parametrize("angle,text", [(45, "45"), (90, "90"), (90.0, "90")])
def test_the_tool_sends_whole_degrees(monkeypatch, angle, text):
    tool, sent = _tool(monkeypatch, {"relief_angle_confirmed": True})
    out = asyncio.run(tool("PolygonConnect", relief_angle=angle))
    assert sent[0][1]["relief_angle"] == text
    assert out.get("ok") is not False


def test_an_angle_the_rule_does_not_read_back_fails_the_call(monkeypatch):
    tool, _ = _tool(monkeypatch, {"relief_angle_confirmed": False,
                                  "connect_descriptor": ""})
    out = asyncio.run(tool("PolygonConnect", relief_angle=45))
    assert out["ok"] is False
    assert "relief_angle 45" in out["reason"]


def test_a_call_without_an_angle_is_not_judged_on_it(monkeypatch):
    tool, _ = _tool(monkeypatch, {"relief_angle_confirmed": False})
    out = asyncio.run(tool("PolygonConnect", entries=4))
    assert "ok" not in out
