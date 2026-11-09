# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fresh-library export guards; native save/reopen checks remain separate."""
from __future__ import annotations

import asyncio
from pathlib import Path
import re

import pytest

from tests.pascal_source import load
from tests.test_library_command_focuses_its_only_library import _bridge

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def routine():
    return load(ALTIUM / "Library.pas")["Lib_SaveFootprintLibrary"]


def test_source_resolution_keeps_the_current_upstream_focus_helper():
    body = routine()
    focus = body.index("PcbLib := FocusPcbLibAt(LibPath);")
    assert focus < body.index("Footprint := FootprintByName(PcbLib, FpWanted);")
    assert body.index("If Footprint = Nil Then") < body.index("Client.OpenNewDocument(")


@pytest.mark.parametrize("guard", [
    "UpperCase(OutPath) = UpperCase(LibPath)",
    "UpperCase(ExtractFileExt(OutPath)) <> '.PCBLIB'",
    "Client.GetDocumentByPath(OutPath) <> Nil",
    "FileExists(OutPath)",
    "Not DirectoryExists(ExtractFilePath(OutPath))",
    "SaveHeldBack(OutPath)",
])
def test_destination_rejections_exit_before_creation(guard):
    body = routine()
    preflight = body[:body.index("Client.OpenNewDocument(")]
    start = preflight.index("If " + guard + " Then")
    block = preflight[start:].split("End;", 1)[0]
    assert "BuildErrorResponse(" in block
    assert "Exit;" in block


def test_the_one_call_export_runs_only_where_it_does_not_stop_the_script():
    """SaveComponentWithLibrary stopped AD21's script engine; 25 and later
    keep it (it writes over an existing output). The version is asked first."""
    body = routine()
    gate = body.index("If AltiumVersionCode(0) >= 2500 Then")
    call = body.index("PcbLib.SaveComponentWithLibrary(")
    assert gate < call < body.index("Exit;", call) < body.index("Client.OpenNewDocument(")
    assert body.count(".SaveComponentWithLibrary(") == 1


def test_export_never_calls_one_call_export_or_mutates_source():
    body = routine()
    fresh = body[body.index("Client.OpenNewDocument("):]
    assert ".SaveComponentWithLibrary(" not in fresh
    assert not re.search(r"\bPcbLib\.(?:RemoveComponent|DeRegisterComponent)", body)
    assert not re.search(r"\bPcbLib\.Save(?!ComponentWithLibrary)", body)
    assert "Footprint.CopyTo(NewFP, eFullCopy);" in body
    assert "AlignCopiedFootprint(Footprint, NewFP)" in body
    assert "DestLib.RegisterComponent(NewFP);" in body
    assert "MarkDocDirtyByPath(OutPath);" in body
    assert "MarkDocDirtyByPath(LibPath);" not in body


def test_destination_identity_is_checked_before_any_edit():
    body = routine()
    check = body.index("Problem := PcbLibPathProblem(DestLib, OutPath);")
    assert body.index("Client.ShowDocument(OutDoc);") < check
    assert check < body.index("PcbBeginEdit(0);")
    assert "'WRONG_LIBRARY'" in body[check:body.index("PcbBeginEdit(0);")]
    assert re.search(r"Finally\s+PcbEndEdit\(0\);", body)


def test_only_the_named_footprint_is_saved_and_edit_mark_cleared_after_save():
    body = routine()
    assert body.index("DestLib.ComponentCount <> 1") < body.index("OutDoc.DoFileSave('')")
    assert body.index("FootprintByName(DestLib, FpWanted) = Nil") < body.index("OutDoc.DoFileSave('')")
    saved = body.index("OutDoc.DoFileSave('')")
    assert saved < body.index("Said := FileExists(OutPath);")
    assert body.index("If Not Said Then") < body.index("ForgetDocEdited(OutPath)")


def test_export_retains_upstream_single_library_focus_retry():
    b, state = _bridge(r"C:\l\P.SchLib", [r"C:\l\P.SchLib", r"C:\l\P.PcbLib"])
    result = asyncio.run(b.send_command_async("library.save_footprint_library", {}))
    assert result == {"success": True}
    assert state["focused"] == r"C:\l\P.PcbLib"
    assert b.calls.count("library.save_footprint_library") == 2


def test_export_does_not_guess_between_two_open_libraries():
    b, _ = _bridge(r"C:\l\P.SchLib", [r"C:\a\A.PcbLib", r"C:\b\B.PcbLib"])
    with pytest.raises(Exception, match="No PCB library"):
        asyncio.run(b.send_command_async("library.save_footprint_library", {}))
    assert "application.set_active_document" not in b.calls


def _assert_alignment_failure_gate(body):
    before = body.index("FailuresBefore := WritesFailed;")
    align = body.index("AlignCopiedFootprint(Footprint, NewFP)")
    refused = body.index("If WritesFailed > FailuresBefore Then")
    save = body.index("OutDoc.DoFileSave('')")
    assert before < align < refused < save
    block = body[refused:save].split("End;", 1)[0]
    assert "'COPY_FAILED'" in block and "Exit;" in block
    assert body.index("MarkDocDirtyByPath(OutPath);") < align


def test_alignment_failure_is_rejected_before_save():
    _assert_alignment_failure_gate(routine())


def test_alignment_guard_detects_its_removed_exit():
    body = routine()
    start = body.index("If WritesFailed > FailuresBefore Then")
    end = body.index("End;", start)
    broken = body[:start] + body[start:end].replace("Exit;", "") + body[end:]
    with pytest.raises(AssertionError):
        _assert_alignment_failure_gate(broken)
