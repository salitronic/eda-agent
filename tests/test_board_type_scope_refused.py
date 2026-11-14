# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A board object type refuses a scope it cannot honour (issue #365).

The PCB route of obj_modify, obj_delete and obj_count acts on the board in
front, or the only open board, whatever the scope says. A document,
project or lib_component scope was dropped without a word, so
``obj_delete(eTrackObject, scope="doc:<another board>")`` deleted on the
board in front and reported success. obj_query already refused such a
scope; the three now refuse it too, before ApplyLibComponentScope moves
the SchLib in front to the named symbol.

A type the schematic resolver knows keeps its scope ("component" is the
schematic part), so a project-wide schematic modify still works.

The helper is lifted out of the scripts and run under Free Pascal with
stand-in resolvers; the handlers are checked for the order of the calls.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pascal_source import functions, strip_comments

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_GENERIC = SCRIPTS / "Generic.pas"


def _bodies() -> dict[str, str]:
    return functions(strip_comments(_GENERIC.read_text(encoding="utf-8")))


@pytest.mark.parametrize("handler,verb", [
    ("Gen_ModifyObjects", "modified"),
    ("Gen_DeleteObjects", "deleted"),
    ("Gen_GetObjectCount", "counted"),
])
def test_a_board_type_scope_is_refused_before_anything_runs(handler, verb):
    body = _bodies()[handler]
    call = f"BoardTypeScopeError(RequestId, Scope, ScopeType, ObjTypeStr, '{verb}')"
    assert call in body, f"{handler} never asks whether a board type has a scope"
    at = body.index(call)
    assert body.index("UnknownScopeError(RequestId, Scope, ScopeType)") < at
    assert at < body.index("ProcessActivePCBDoc("), (
        f"{handler} reaches the board before refusing the scope")
    if "ApplyLibComponentScope" in body:
        assert at < body.index("ApplyLibComponentScope"), (
            f"{handler} moves the SchLib to a symbol for a call it then refuses")


def test_the_query_keeps_its_own_refusal():
    body = _bodies()["Gen_QueryObjects"]
    assert "SCOPE_NOT_SUPPORTED" in body
    assert body.index("SCOPE_NOT_SUPPORTED") < body.index("ApplyLibComponentScope")


def _helper() -> str:
    code = _GENERIC.read_text(encoding="utf-8", errors="replace")
    start = re.search(r"(?m)^Function BoardTypeScopeError\b", code)
    assert start, "missing BoardTypeScopeError"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _fpc() -> str:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


#: (scope type, object type, refused). Stand-ins: the schematic resolver
#: knows component and eNetLabel; the board resolver knows component and
#: eTrackObject; bogus is neither.
CASES = [
    ("active_doc", "eTrackObject", False),
    ("doc", "eTrackObject", True),
    ("project", "eTrackObject", True),
    ("lib_component", "eTrackObject", True),
    ("project", "component", False),       # the schematic part keeps its scope
    ("doc", "eNetLabel", False),
    ("doc", "bogus", False),               # left to the INVALID_TYPE refusal
]


def test_only_a_board_type_with_a_scope_is_refused(tmp_path):
    fpc = _fpc()
    lines = [
        "program boardscope;", "{$mode delphi}", "uses SysUtils;",
        "function ObjectTypeFromString(TypeStr : String) : Integer;",
        "begin",
        "  if (TypeStr = 'component') or (TypeStr = 'eNetLabel') then Result := 1",
        "  else Result := -1;",
        "end;",
        "function ObjectTypeFromStringPCB(TypeStr : String) : Integer;",
        "begin",
        "  if (TypeStr = 'component') or (TypeStr = 'eTrackObject') then Result := 5",
        "  else Result := -1;",
        "end;",
        "function BuildErrorResponse(RequestId, Code, Msg : String) : String;",
        "begin Result := 'ERR:' + Code + ':' + Msg; end;",
        _helper(),
        "begin",
    ]
    for scope_type, obj_type, _ in CASES:
        lines.append(
            f"  WriteLn(BoardTypeScopeError('r', 'x', '{scope_type}', "
            f"'{obj_type}', 'deleted'));")
    lines.append("end.")
    path = tmp_path / "boardscope.pas"
    path.write_text("\n".join(lines), encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("boardscope.exe" if os.name == "nt" else "boardscope")
    out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
    got = out.splitlines()
    assert len(got) == len(CASES), out
    for (scope_type, obj_type, refused), line in zip(CASES, got):
        if refused:
            assert line.startswith("ERR:SCOPE_NOT_SUPPORTED:"), (scope_type, obj_type, line)
            assert "Nothing was deleted." in line
        else:
            assert line == "", (scope_type, obj_type, line)
