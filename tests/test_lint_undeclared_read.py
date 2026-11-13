# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The lint refuses a bare name a routine reads that nothing declares.

DelphiScript resolves a name when the line runs, so a misspelt helper or
a variable never declared compiles, and the first call that reaches the
line stops the script ("Undeclared identifier") and the polling loop
with it. The rule checks every bare name against the units, the
routine's own declarations, the form's controls and a reviewed list of
names Altium provides.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
sys.path.insert(0, str(SCRIPTS))
import lint  # noqa: E402

UNIT = """\
Const
    LIMIT = 3;

Function Helper(A : Integer) : Integer;
Begin
    Result := A + LIMIT;
End;

Var
    Counter : Integer;

Function Reader(Params : String) : String;
Var
    First, Second,
    Third : String;
    N : Integer;
Begin
    First := '{"count":' + Params;
    { Prose in a comment: Nonsense words here. }
    Second := Params.Length;
    Third := IntToStr(Helper(Counter)) + StringReplace(First, '}', '', rfReplaceAll);
    N := $FF + 1e5;
    Result := First + Second + Third + IntToStr(N);
End;

Procedure OneLine(X : Integer);
Begin Counter := X; End;
"""


def _scan(tmp_path, text, external=None):
    unit = tmp_path / "Probe.pas"
    unit.write_text(text, encoding="utf-8")
    return lint._scan_undeclared_read(
        [str(unit)], external={"stringreplace", "rfreplaceall"}
        if external is None else external)


def _names(findings):
    return [f.snippet.rsplit(" reads ", 1)[1].split(",")[0] for f in findings]


def test_a_unit_whose_every_name_is_declared_is_clean(tmp_path):
    assert _scan(tmp_path, UNIT) == []


def test_a_name_declared_nowhere_is_reported(tmp_path):
    bad = UNIT.replace("Result := First + Second",
                       "Result := IntToStr(ErrorLevel) + SuppressedText + First + Second")
    found = _scan(tmp_path, bad)
    assert _names(found) == ["ErrorLevel", "SuppressedText"]
    assert all(f.rule == "undeclared-read" and f.severity == "error" for f in found)


def test_a_helper_whose_definition_is_gone_is_reported(tmp_path):
    gone = UNIT.replace("Function Helper(A : Integer) : Integer;\nBegin\n"
                        "    Result := A + LIMIT;\nEnd;\n", "")
    assert _names(_scan(tmp_path, gone)) == ["Helper"]


def test_an_external_name_left_off_the_list_is_reported(tmp_path):
    assert _names(_scan(tmp_path, UNIT, external={"rfreplaceall"})) == ["StringReplace"]


def test_a_global_between_routines_and_a_wrapped_var_list_are_known(tmp_path):
    found = _names(_scan(tmp_path, UNIT))
    assert "Counter" not in found and "Third" not in found


def test_braces_in_a_string_do_not_hide_code():
    lines = lint._blank_block_comments(["X := '{\"a\":' + Y; { gone }", "Z := 1;"])
    assert lines[0].startswith("X := '{\"a\":' + Y;") and "gone" not in lines[0]
    assert lines[1] == "Z := 1;"


def test_the_bridge_scripts_pass_the_rule():
    targets = [str(SCRIPTS / f) for f in lint.PAS_FILES]
    found = lint._scan_undeclared_read(targets)
    assert found == [], "\n".join(f.format() for f in found[:20])


def test_the_build_runs_the_rule():
    body = re.search(r"(?ms)^def run_lint\b.*?^    return errors", (
        SCRIPTS / "lint.py").read_text(encoding="utf-8")).group(0)
    assert "_scan_undeclared_read(targets)" in body


def test_every_listed_name_is_one_the_scripts_read():
    """A name the scripts no longer read would hide the next misspelling."""
    targets = [str(SCRIPTS / f) for f in lint.PAS_FILES]
    read = {_n.lower() for _n in _names(lint._scan_undeclared_read(
        targets, external=lint._form_control_names()))}
    listed = lint.load_external_names()
    assert listed - read == set()


REFERENCE = Path(__file__).resolve().parents[1] / "reference"


@pytest.mark.skipif(not REFERENCE.is_dir(), reason="reference/ is local only")
def test_every_listed_name_is_in_altiums_own_scripts():
    words = set()
    for root, _dirs, files in os.walk(REFERENCE):
        if "coaltium" in root.lower():
            continue
        for f in files:
            if f.lower().endswith((".pas", ".txt", ".md", ".json", ".htm", ".html", ".dfm")):
                text = Path(root, f).read_text(encoding="utf-8", errors="replace")
                words |= set(re.findall(r"[a-z_]\w*", text.lower()))
    assert lint.load_external_names() - words == set()
