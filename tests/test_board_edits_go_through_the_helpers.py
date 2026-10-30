# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Board edits open and close through PcbBeginEdit and PcbEndEdit.

MEASURED 2026-10-30 on a scratch board: Altium does not fold nested
PreProcess/PostProcess pairs into one undo step. Twenty arcs placed by one
bulk call, each handler with its own pair inside the bulk call's pair, took
twenty Undos; five tracks inside one pair took one. The helpers count the
depth and only the outermost pair reaches Altium, which is what makes a
bulk call one edit. One raw call in a handler splits it again, silently.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: The only raw calls: the helpers themselves, the unwind after a crashed
#: handler (App_ExitActiveCommand), and the bulk loop reopening the shared
#: edit a handler closed.
ALLOWED = {
    ("Main.pas", "Procedure PcbBeginEdit"),
    ("Main.pas", "Procedure PcbEndEdit"),
    ("Application.pas", "Function App_ExitActiveCommand"),
    ("StatusForm.pas", "Function ProcessBulk"),
}

RAW = re.compile(r"PCBServer\.(PreProcess|PostProcess)\b")
ROUTINE = re.compile(r"^(Procedure|Function)\s+\w+", re.I)


def _raw_calls():
    for path in sorted(SCRIPTS.glob("*.pas")):
        if path.name == "Altium_MCP.pas":
            continue
        routine = ""
        for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            found = ROUTINE.match(line)
            if found:
                routine = found.group(0)
            code = line.split("{", 1)[0].split("//", 1)[0]
            if RAW.search(code):
                yield path.name, routine, number


def test_no_handler_calls_pre_or_post_process_directly():
    stray = [f"{name}:{number} in {routine}"
             for name, routine, number in _raw_calls()
             if (name, routine) not in ALLOWED]
    assert not stray, (
        "use PcbBeginEdit(0) / PcbEndEdit(0); a raw call splits a bulk "
        "call into separate undo steps:\n  " + "\n  ".join(stray))


def test_the_allowed_places_still_exist():
    """A rename would turn the allow-list into dead entries."""
    seen = {(name, routine) for name, routine, _ in _raw_calls()}
    assert ALLOWED <= seen, sorted(ALLOWED - seen)


#: Schematic edits likewise go through SchBeginEdit / SchEndEdit; the raw
#: calls are the helpers and the unwind.
SCH_ALLOWED = {
    ("Main.pas", "Procedure SchBeginEdit"),
    ("Main.pas", "Procedure SchEndEdit"),
    ("Main.pas", "Procedure SchReleaseHeld"),
    ("Main.pas", "Procedure SchCommitEdit"),
    ("Application.pas", "Function App_ExitActiveCommand"),
}

SCH_RAW = re.compile(r"ProcessControl\.(PreProcess|PostProcess)\b")


def _sch_raw_calls():
    for path in sorted(SCRIPTS.glob("*.pas")):
        if path.name == "Altium_MCP.pas":
            continue
        routine = ""
        for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            found = ROUTINE.match(line)
            if found:
                routine = found.group(0)
            code = line.split("{", 1)[0].split("//", 1)[0]
            if SCH_RAW.search(code):
                yield path.name, routine, number


def test_no_handler_opens_a_schematic_edit_directly():
    stray = [f"{name}:{number} in {routine}"
             for name, routine, number in _sch_raw_calls()
             if (name, routine) not in SCH_ALLOWED]
    assert not stray, (
        "use SchBeginEdit(Doc, '') / SchEndEdit(Doc, 'Edit'); a raw call "
        "splits a bulk call into separate undo steps:\n  " + "\n  ".join(stray))
    seen = {(name, routine) for name, routine, _ in _sch_raw_calls()}
    assert SCH_ALLOWED <= seen, sorted(SCH_ALLOWED - seen)


def test_the_bulk_loop_holds_schematic_edits_and_lets_go_after():
    code = (SCRIPTS / "StatusForm.pas").read_text(encoding="utf-8", errors="replace")
    body = code[code.index("Function ProcessBulk("):code.index("Function ProcessCommand(")]
    hold = body.index("SchBulkHold := True")
    loop = body.index("ProcessOne(Command, ItemParams")
    release = body.index("SchReleaseHeld(0)")
    assert hold < loop < release
    assert body.index("Finally") < release, "the held edit must close on every path"
    command = code[code.index("Function ProcessCommand("):]
    command = command[:command.index("End;")]
    assert "SchReleaseHeld(0)" in command and "SchEditOpen := '|'" in command


def test_the_open_edit_list_pops_the_last_edit_on_a_document(tmp_path):
    """SchPopEdit decides whether a close reaches Altium; run the real one."""
    import os
    import shutil
    import subprocess

    import pytest

    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    code = (SCRIPTS / "Main.pas").read_text(encoding="utf-8", errors="replace")
    start = code.index("Function SchPopEdit(")
    end = code.index("\nEnd;", start) + len("\nEnd;")
    cases = [
        ("|RA|", "A", "R", "|"),
        ("|RA|NA|", "A", "N", "|RA|"),
        ("|RA|RB|", "A", "R", "|RB|"),
        ("|RA|RB|NA|", "A", "N", "|RA|RB|"),
        ("|RB|", "A", "", "|RB|"),
        ("|", "A", "", "|"),
        ("|R|", "", "R", "|"),
    ]
    checks = [
        f"  SchEditOpen := '{before}'; K := SchPopEdit('{path}');\n"
        f"  if (K <> '{kind}') or (SchEditOpen <> '{after}') then Halt({i});"
        for i, (before, path, kind, after) in enumerate(cases, 1)]
    program = "\n".join([
        "program popedit;", "{$mode delphi}", "var SchEditOpen: String; K: String;",
        code[start:end], "begin", *checks, "end."])
    src = tmp_path / "popedit.pas"
    src.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(src)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("popedit.exe" if os.name == "nt" else "popedit")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, f"case {ran.returncode} failed: {cases[ran.returncode - 1]}"


def test_the_server_does_not_use_the_reload_proxy_unless_asked(monkeypatch):
    """Users run the server as it always ran; the proxy is opt-in."""
    import eda_agent.devproxy as devproxy
    import eda_agent.server as server

    called = []
    monkeypatch.setattr(devproxy, "main", lambda args: called.append(args) or 0)
    monkeypatch.setattr(server, "serve_mcp", lambda **k: 7)
    monkeypatch.delenv("EDA_AGENT_RELOAD", raising=False)
    monkeypatch.setattr("sys.argv", ["eda-agent"])
    assert server.main() == 7 and not called

    monkeypatch.setenv("EDA_AGENT_RELOAD", "1")
    assert server.main() == 0 and called == [[]]
