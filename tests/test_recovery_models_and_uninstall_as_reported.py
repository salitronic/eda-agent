# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Script recovery, 3D model standoffs and library uninstall, as reported.

Recovery refused to restart a loop that no longer answered a ping because
Altium's title did not name the script project. A link with a standoff
reported it applied and saved a model Z offset of 0, because a STEP body
ignores its own StandoffHeight. Setting the standoff afterwards skipped
the body because it compared the saved file name with the full path the
body was loaded from. An uninstalled .IntLib stayed locked with nothing
saying so.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from eda_agent.bridge import script_crash
from eda_agent.tools import library

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
SRC = Path(__file__).resolve().parents[1] / "src" / "eda_agent"


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


# -- recovery -------------------------------------------------------------

def test_a_running_call_keeps_the_loop_from_being_restarted(tmp_path):
    assert not script_crash.another_call_running(tmp_path)
    (tmp_path / "progress_abcd1234-1.json").write_text("{}")
    assert script_crash.another_call_running(tmp_path)


def test_a_halted_handlers_marker_does_not_count(tmp_path):
    marker = tmp_path / "progress_abcd1234-1.json"
    marker.write_text("{}")
    old = time.time() - script_crash.CALL_CEILING_S - 5
    os.utime(marker, (old, old))
    assert not script_crash.another_call_running(tmp_path)


def test_a_fresh_queue_counts(tmp_path):
    (tmp_path / "queue.json").write_text("{}")
    assert script_crash.another_call_running(tmp_path)


def test_recovery_restarts_a_loop_that_does_not_answer():
    code = (SRC / "tools" / "uiauto.py").read_text(encoding="utf-8")
    body = code[code.index("async def app_recover_script"):]
    body = body[:body.index("@mcp.tool()")]
    assert "ping_with_version" in body
    assert "another_call_running(workspace)" in body
    assert "force=force or dead" in body


# -- 3D models --------------------------------------------------------------

def test_the_link_puts_the_standoff_on_the_model():
    body = _routine("Library.pas", "Lib_Link3DModel")
    assert "Model.SetState(RotX, RotY, RotZ, MilsToCoordF(OffZ))" in body
    assert "Body.StandoffHeight" not in body
    # Written before the model is bound, or the body never sees it.
    assert body.index("Model.SetState(") < body.index("Body.Model := Model")


def test_set_3d_body_matches_the_model_by_file_name():
    body = _routine("Library.pas", "Lib_Set3DBody")
    assert "UpperCase(ExtractFileName(SavedFile))" in body
    assert "UpperCase(ExtractFileName(ModelFile))" in body
    assert "UpperCase(SavedFile) <> UpperCase(ModelFile)" not in body


# -- uninstall --------------------------------------------------------------

def test_uninstall_is_checked_and_closes_an_open_intlib():
    body = _routine("Library.pas", "Lib_UninstallLibrary")
    assert "InstalledLibraryPath(I)" in body
    assert "BoolToJsonStr(Ok And Not Still)" in body
    assert "Client.CloseDocument(ServerDoc)" in body


def test_a_held_file_is_reported_locked(tmp_path, monkeypatch):
    lib = tmp_path / "Lib.IntLib"
    lib.write_bytes(b"x")
    assert library._file_locked(str(lib)) is False
    assert library._file_locked(str(tmp_path / "missing.IntLib")) is None

    def held(*a, **k):
        raise PermissionError("in use")
    monkeypatch.setattr("builtins.open", held)
    assert library._file_locked(str(lib)) is True
