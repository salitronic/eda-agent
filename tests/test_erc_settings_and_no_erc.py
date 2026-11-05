# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""ERC report levels are readable and settable, and a placed No-ERC marker
suppresses something.

Asked for on 2026-10-23: a multichannel design reports "Nets with multiple
names" by construction, and No-ERC markers placed through the bridge left
their violations reported, with no way to read what they suppressed.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text)


def test_the_levels_are_dispatched():
    text = (_ALTIUM / "Project.pas").read_text(encoding="utf-8")
    assert "'get_erc_levels':        Result := Proj_GetErcLevels(Params, RequestId);" in text
    assert "'set_erc_levels':        Result := Proj_SetErcLevels(Params, RequestId);" in text


def test_a_level_is_written_and_read_back():
    body = load(_ALTIUM / "Project.pas")["Proj_SetErcLevels"]
    write = body.index("Project.DM_SetErrorLevels(Kind, Want);")
    assert write < body.index("Got := Project.DM_ErrorLevels(Kind);")
    assert "Else If Got <> Want Then" in body


def test_types_are_named_by_altium_not_by_a_table_here():
    body = _code(load(_ALTIUM / "Project.pas")["Proj_GetErcLevels"])
    assert "Workspace.DM_ViolationTypeDescription(I)" in body


@pytest.mark.parametrize("text,level", [
    ("no_report", 0), ("No Report", 0), ("warning", 1), ("error", 2),
    ("fatal", 3), ("Fatal Error", 3), ("2", 2), ("loud", -1),
])
def test_level_names(text, level):
    """ErcLevelFromText, read as Python."""
    body = _code(load(_ALTIUM / "Project.pas")["ErcLevelFromText"])
    t = text.strip().lower().replace(" ", "_")
    got = -1
    for line in body.splitlines():
        m = re.search(r"If (.+) Then Result := (\d)", line)
        if m and any(f"(T = '{alt}')" in m.group(1) for alt in [t]):
            got = int(m.group(2))
            break
    assert got == level


def test_every_creator_makes_a_no_erc_suppress_all():
    gen = load(_ALTIUM / "Generic.pas")
    for func in ("Gen_PlaceNoERC", "Gen_CreateObject", "Gen_BatchCreate"):
        body = gen[func]
        factory = body.index("SchServer.SchObjectFactory(")
        assert body.index("ApplyNoErcDefaults(", factory) > factory, func
    defaults = gen["ApplyNoErcDefaults"]
    assert "If Obj.ObjectId <> eNoERC Then Exit;" in defaults
    assert "WriteNoErcProp(Obj, 'SuppressAll', 'true');" in defaults
    assert "WriteNoErcProp(Obj, 'IsActive', 'true');" in defaults


def test_the_mode_is_readable_and_writable_only_on_a_no_erc():
    gen = load(_ALTIUM / "Generic.pas")
    for func in ("GetSchProperty", "SetSchProperty"):
        body = gen[func]
        i = body.index("(PropName = 'SuppressAll')")
        assert "Obj.ObjectId = eNoERC" in body[i - 80:i], func


def test_the_tool_sends_levels_and_saves(monkeypatch):
    from eda_agent.tools import project as mod
    calls = []

    class _Bridge:
        async def send_command_async(self, command, params=None, **kw):
            calls.append((command, dict(params or {})))
            if command == "project.set_erc_levels":
                return {"set": 2, "failed": 0, "project": "P.PrjPcb"}
            return {"saved": True}

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(mod, "get_bridge", lambda: _Bridge())
    mod.register_project_tools(_Mcp())
    out = asyncio.run(tools["proj_set_erc_levels"](
        {"Nets with multiple names": "no_report", "Nets with no driving source": "warning"}))
    assert calls[0] == ("project.set_erc_levels", {
        "levels": "Nets with multiple names=no_report~~Nets with no driving source=warning"})
    assert calls[1] == ("application.save_document", {"file_path": "P.PrjPcb"})
    assert out["saved"] is True
    assert "error" in asyncio.run(tools["proj_set_erc_levels"]({"a=b": "warning"}))


def test_suppressed_and_not_reported_violations_are_left_out_and_counted():
    """A No ERC marker leaves its violation in DM_Violations with
    DM_IsSuppressed set; listing it made a working marker look broken."""
    body = load(_ALTIUM / "Generic.pas")["Gen_GetErcViolations"]
    assert "Suppressed := Violation.DM_IsSuppressed;" in body
    assert "Level := Violation.DM_ErrorLevel;" in body
    assert "If (Not IncludeAll) And (Suppressed Or (Level = 0)) Then Continue;" in body
    for field in ("suppressed_count", "not_reported_count", "raw_violation_count"):
        assert f'"{field}":' in body
    assert "'{\"violation_count\":' + IntToStr(NReported)" in body


def test_an_object_left_by_a_remove_is_tried_again_before_a_fault():
    """A leftover No ERC moved but would not delete: a second registration,
    or a component holding it, needs another try, not an immediate fault."""
    body = load(_ALTIUM / "Generic.pas")["ProcessSchDocObjects"]
    same = body.index("If (ThisAddr <> 0) And (ThisAddr = LastAddr) Then")
    branch = body[same:body.index("LastAddr := ThisAddr;", same)]
    assert "If SameTries > 2 Then" in branch
    assert "RemoveFromComponents(SchDoc, FoundObj);" in branch
    assert branch.index("If SameTries > 2 Then") < branch.index("NoteDeleteFault(")
    assert "Continue;" in branch
    # The compiler kept a stale copy after such a delete until a reload.
    assert "NoteNextStep('An object here needed more than one remove." in branch
