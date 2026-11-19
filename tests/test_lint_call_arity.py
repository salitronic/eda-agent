# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A call to one of the scripts' own routines with the wrong number of
arguments is a lint error.

Altium refuses the whole script at load ("Comma expected", "Not enough
actual parameters") and the loop never starts, without naming the call.
The check counts each call's arguments against the routine's header over
the files Altium compiles together.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

LINT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "lint.py"


@pytest.fixture(scope="module")
def lint():
    spec = importlib.util.spec_from_file_location("_altium_lint_arity", LINT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint_arity"] = mod
    spec.loader.exec_module(mod)
    return mod


def _scan(lint, tmp_path, **files):
    paths = []
    for name, text in files.items():
        p = tmp_path / f"{name}.pas"
        p.write_text(text, encoding="utf-8")
        paths.append(str(p))
    return [(f.file, f.line, f.snippet) for f in lint._scan_call_arity(paths)]


def test_a_call_with_too_few_or_too_many_arguments_is_named(lint, tmp_path):
    got = _scan(lint, tmp_path, A=(
        "Function Two(A : Integer; Var B, C : String) : Integer;\n"
        "Begin\n    Result := 1;\nEnd;\n"), B=(
        "Procedure Go;\nBegin\n"
        "    Two(1, X);\n"
        "    Two(1, X, Y);\n"
        "    Two(F(1, 2), 'a, b', Z[1, 2]);\n"
        "    Two(1, X, Y, W);\n"
        "End;\n"))
    assert got == [("B.pas", 3, "Two called with 2 argument(s); it takes 3"),
                   ("B.pas", 6, "Two called with 4 argument(s); it takes 3")]


def test_defaults_a_bare_call_and_a_result_assignment_are_allowed(lint, tmp_path):
    got = _scan(lint, tmp_path, A=(
        "Function Opt(A : Integer; B : Integer = 0) : Integer;\n"
        "Begin\n    Opt := A;\nEnd;\n"
        "Function Now0 : Integer;\nBegin\n    Result := 0;\nEnd;\n"
        "Procedure Go;\nBegin\n"
        "    Opt(1); Opt(1, 2); X := Now0; X := Now0();\n"
        "    { Opt() in a comment } S := 'Opt()';\n"
        "End;\n"))
    assert got == []


def test_the_scripts_have_no_call_with_the_wrong_count(lint):
    targets = [os.path.join(lint.SCRIPT_DIR, f) for f in lint.PAS_FILES]
    assert lint._scan_call_arity(targets) == []
