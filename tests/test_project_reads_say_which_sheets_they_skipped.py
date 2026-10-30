# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A project-wide read says how many sheets it could not read.

Project loops looked each sheet up with SchServer.GetSchDocumentByPath and
skipped the ones the editor had not loaded. An audit over a partly loaded
project answered checked:0 / violations:0, which reads exactly like "looked
and found nothing". The lookups now go through ProjectSheet, which counts,
and the dispatcher puts the counts on the reply, so a loop added later is
covered at the one place every reply passes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.pascal_source import load

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def test_the_dispatcher_resets_and_reports_per_request():
    # Per request, and per item of a bulk request: ProcessOne runs each.
    body = load(_SCRIPTS / "StatusForm.pas")["ProcessOne"]
    reset = body.index("SheetsSkipped := 0;")
    route = body.index("Case Category Of")
    report = body.index("Result := SheetCoverage(Result);")
    assert reset < route < report


def test_the_lookup_counts_a_sheet_that_is_not_loaded():
    body = load(_SCRIPTS / "Main.pas")["ProjectSheet"]
    assert "Inc(SheetsSeen)" in body
    assert "If Result = Nil Then Inc(SheetsSkipped)" in body


def test_no_project_loop_looks_a_sheet_up_uncounted():
    """A loop over the project's documents that resolves a sheet must do it
    through ProjectSheet. Single-sheet handlers given a sheet_path are not
    project reads and keep the plain lookup."""
    offenders = []
    for pas in _SCRIPTS.glob("*.pas"):
        if pas.name == "Altium_MCP.pas":
            continue
        lines = pas.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "GetSchDocumentByPath(" not in line or line.lstrip().startswith("{"):
                continue
            above = "\n".join(lines[max(0, i - 30):i])
            if re.search(r"DM_LogicalDocuments\(|GetCompiledDoc\(", above):
                offenders.append(f"{pas.name}:{i + 1}")
    # Two loops in Project.pas re-find sheets the same call already
    # touched, and one counts skipped sheets itself.
    allowed = {"Project.pas"}
    left = [o for o in offenders if o.split(":")[0] not in allowed]
    assert not left, f"project loops still resolving sheets uncounted: {left}"


def _coverage(response: str, seen: int, skipped: int) -> str:
    """The Pascal SheetCoverage, line for line, to check the JSON it makes."""
    if skipped == 0:
        return response
    marker = '"success":true,"data":{'
    p = response.find(marker)
    if p < 0:
        return response
    p += len(marker)
    added = (f'"sheets_seen":{seen},"sheets_skipped":{skipped},'
             f'"sheets_warning":"{skipped} of {seen} project sheets are not '
             f'loaded in the editor and were NOT read, so counts and findings '
             f'cover only the rest. Run proj_load_sheets and ask again."')
    if response[p] != "}":
        added += ","
    return response[:p] + added + response[p:]


def test_the_injected_fields_keep_the_reply_valid_json():
    full = ('{"protocol_version":1,"id":"x","success":true,'
            '"data":{"checked":0,"violations":[]},"error":null}')
    empty = ('{"protocol_version":1,"id":"x","success":true,'
             '"data":{},"error":null}')
    for reply in (full, empty):
        out = json.loads(_coverage(reply, 11, 10))
        assert out["data"]["sheets_skipped"] == 10
    assert json.loads(_coverage(full, 11, 10))["data"]["checked"] == 0


def test_the_python_mirror_matches_the_pascal():
    """The mirror above is only worth something while it says the same."""
    body = load(_SCRIPTS / "Main.pas")["SheetCoverage"]
    for piece in ("'\"sheets_seen\":'", "',\"sheets_skipped\":'",
                  "' project sheets are not loaded in the editor '",
                  "'Run proj_load_sheets and ask again.\"'",
                  "If Copy(Response, P, 1) <> Chr(125) Then Added := Added + ','"):
        assert piece in body, piece
