# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A compile is reused only while nothing has been written since.

SmartCompile skipped DM_Compile for 2 s unless a document read modified.
The Modified flag can read False while the editor holds an edit made
through ProcessControl, and nothing of ours cleared the cache after a
write, so a net read straight after a schematic write could be answered
from the netlist as it was before the write. Every write goes through one
of the edit helpers; each bumps WriteSerial, and a compile is reused only
while it has not moved.
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(name: str) -> str:
    code = (SCRIPTS / "Main.pas").read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^(Procedure|Function) {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _reuses_only_without_writes(body: str) -> bool:
    skip = body[:body.index("Project.DM_Compile;")]
    return ("(WriteSerial = CompiledAtSerial)" in skip
            and "CompiledAtSerial := WriteSerial;" in body[body.index("Project.DM_Compile;"):])


def test_a_compile_is_reused_only_while_nothing_was_written():
    assert _reuses_only_without_writes(_routine("SmartCompile"))


def test_the_rule_rejects_a_cache_that_ignores_writes():
    body = _routine("SmartCompile").replace("(WriteSerial = CompiledAtSerial) And", "")
    assert not _reuses_only_without_writes(body)


def test_every_edit_helper_moves_the_serial():
    for name in ("SchBeginModify", "PcbBeginEdit", "NoteDocEdited"):
        body = _routine(name)
        assert "WriteSerial := WriteSerial + 1;" in body, name
    # MarkDocDirtyByPath goes through NoteDocEdited.
    assert "NoteDocEdited(FilePath);" in _routine("MarkDocDirtyByPath")
