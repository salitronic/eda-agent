# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Vias read a page or one net at a time; a save counts the bridge's own
edits; the document on screen is found when nothing has focus.

pcb_get_vias built one reply holding every via of the board.
app_save_document said was_modified false after an edit made through the
API, which leaves Altium's Modified flag False, although the close path
already counted the bridge's own record. With no focused document,
app_get_active_document went straight to the schematic server's current
sheet, which can be a sheet shown earlier than the library or board now
on screen.

MEASURED 2026-10-31 on AD 26.11 (scratch board, put back): offset 1
limit 1 returned the second of 2 vias with more false; net GND returned
0 of 0; a via placed by pcb_place_vias then app_save_document answered
was_modified true (it said false before).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


@pytest.fixture(scope="module")
def pcb() -> dict[str, str]:
    return load(_ALTIUM / "PCB.pas", minimum=100)


@pytest.fixture(scope="module")
def app() -> dict[str, str]:
    return load(_ALTIUM / "Application.pas", minimum=20)


def test_a_via_is_counted_after_the_net_filter_and_before_paging(pcb):
    body = pcb["PCB_GetVias"]
    net = body.index("If (WantNet <> '') And (NetName <> WantNet) Then")
    count = body.index("Inc(Count);")
    page = body.index("If (Count <= Offset) Or ((Limit > 0) And (Returned >= Limit)) Then")
    assert net < count < page
    assert "',\"more\":' + BoolToJsonStr(Offset + Returned < Count)" in body


def test_the_tool_sends_only_what_was_asked(monkeypatch):
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, **kwargs):
            sent.append((command, params))
            return {}

    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: _Bridge())
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    tool = {t.name: t.fn for t in asyncio.run(registry.list_tools())}["pcb_get_vias"]
    asyncio.run(tool())
    asyncio.run(tool(net="GND", offset=5, limit=10))
    assert sent == [("pcb.get_vias", {}),
                    ("pcb.get_vias", {"net": "GND", "offset": "5", "limit": "10"})]
    assert asyncio.run(tool(offset=-1))["ok"] is False


def test_a_save_counts_the_bridges_own_edits(app):
    body = app["App_SaveDocument"]
    read = body.index("Try WasModified := ServerDoc.Modified; Except End;")
    record = body.index("If DocWasEdited(FilePath) Then WasModified := True;")
    save = body.index("ServerDoc.DoFileSave('')")
    assert read < record < save


def test_the_view_on_screen_comes_before_the_servers_last_sheet(app):
    body = app["App_GetActiveDocument"]
    view = body.index("View := Client.CurrentView;")
    assert view < body.index("SchServer.GetCurrentSchDocument")
    assert view < body.index("GetPCBBoardAnywhere(0)")
    assert "ServerDoc := View.OwnerDocument;" in body


def test_the_focused_path_falls_back_to_the_view():
    body = load(_ALTIUM / "Utils.pas", minimum=20)["CurrentFocusedDocPath"]
    assert body.index("Workspace.DM_FocusedDocument") < body.index("Client.CurrentView")
    assert "If Result <> '' Then Exit;" in body
