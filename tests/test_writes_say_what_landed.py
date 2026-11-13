# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Replies say what a write did, and writes land where they were meant.

- A schematic modify that landed no write answers modified:false.
- A harness wire, splice or cable answers Name from its description, so
  a Name filter finds it.
- Project writes refuse Free Documents, the project in focus behind a
  loose document, which has no project file.
- Panels docked with a caption and floating panel windows are listed.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from eda_agent.ui import panels
from eda_agent.ui.windows import Control

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, head: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    return re.search(rf"(?ms)^(?:Function|Procedure) {head}\b.*?^End;", code).group(0)


def test_a_modify_counts_only_the_writes_that_landed():
    apply = _routine("Generic.pas", "ApplySetProperties")
    assert "If SetSchProperty(Obj, PropName, PropValue) = 1 Then NoteSchWriteLanded(0);" in apply
    assert apply.index("Obj.Location := Loc;") < apply.index("NoteSchWriteLanded(0);") \
        < apply.index("NotePropertyDiag('failed', 'Location')")
    said = _routine("Generic.pas", "SchCallModified")
    assert "(Mode = 'modify') And (SchWritesLanded(0) = 0)" in said
    reset = _routine("Utils.pas", "ResetPropertyDiag")
    assert "_SchWritesLanded := 0;" in reset


def test_every_schematic_write_reply_uses_the_count():
    code = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")
    assert "BoolToJsonStr(IsMutating And ((TotalMatched > 0)" not in code
    assert code.count("BoolToJsonStr(SchCallModified(IsMutating, TotalMatched, Mode))") == 2


def test_a_harness_wire_answers_name_from_its_description():
    code = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")
    reader = _routine("Generic.pas", "GetSchProperty")
    name = reader[reader.index("PropName = 'Name'"):]
    name = name[:name.index("Else If PropName = 'LibReference'")]
    assert "HarnessNameFromDescription(Obj.GetState_DescriptionString)" in name
    assert code.index("Function HarnessNameFromDescription") < code.index("Function GetSchProperty")


def test_project_writes_refuse_a_project_without_a_file():
    for head in ("Proj_SetErcLevels", "Proj_SetParameter"):
        body = _routine("Project.pas", head)
        guard = body.index("If Not FileExists(")
        assert guard < body.index("Nothing was written")
        first_write = min(i for i in (body.find("DM_SetErrorLevels"),
                                      body.find("DM_BeginUpdate")) if i > 0)
        assert guard < first_write, head


def _frame(monkeypatch, main, floating=()):
    windows = {1: main, **{10 + i: c for i, c in enumerate(floating)}}
    monkeypatch.setattr(panels.menu, "frame", lambda pid: SimpleNamespace(hwnd=1))
    monkeypatch.setattr(panels.win, "capture",
                        lambda h: SimpleNamespace(controls=windows[h]))
    monkeypatch.setattr(panels.win, "enumerate_windows", lambda pid=None: [
        SimpleNamespace(hwnd=10 + i, class_name="TClientBarControl")
        for i in range(len(floating))])


def test_a_docked_panel_with_a_caption_and_no_button_is_listed(monkeypatch):
    _frame(monkeypatch, [
        Control(2, "TXPPanel", "Projects", (30, 100, 330, 120)),
        Control(3, "TViewComponent", "Projects", (30, 120, 330, 900)),
        Control(4, "TViewComponent", "Board.PcbDoc", (340, 100, 1800, 900)),
        Control(5, "TXPPanel", "", (0, 0, 10, 10)),
    ])
    assert [p["name"] for p in panels.list_open(1)] == ["Projects"]


def test_a_floating_panel_window_is_listed_once(monkeypatch):
    _frame(monkeypatch,
           [Control(2, "TPopupPanelButton", "Messages", (100, 1100, 160, 1120))],
           floating=[[Control(7, "TPopupPanelButton", "Properties", (2000, 50, 2020, 120)),
                      Control(8, "TPopupPanelButton", "Messages", (2000, 130, 2020, 200))]])
    assert [p["name"] for p in panels.list_open(1)] == ["Messages", "Properties"]
