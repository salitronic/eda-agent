# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Each Claude session's commands run on that session's own document.

Most commands act on the focused document, and with several sessions on one
Altium any of them could move it between two calls of another: 309 of about
1500 commands on 2026-10-23 were set_active_document, sessions taking the
focus back from each other, with a gap in which a write could land on the
wrong document. Each server now sends the document its session last had in
front, and the loop puts it back in the same turn as the command.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from eda_agent.bridge import altium_bridge as ab
from tests.pascal_source import load

ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = ROOT / "scripts" / "altium"
HARNESS = ROOT / "tests" / "cross_validate_pascal.pas"


def _function(code: str, name: str) -> str:
    start = code.index(f"Function {name}(")
    return code[start:code.index("\nEnd;", start) + 5]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", "")).strip()


# ---- Pascal -------------------------------------------------------------

def test_the_cross_checked_copy_is_the_shipped_function():
    main = _function((_ALTIUM / "Main.pas").read_text(encoding="ascii"),
                     "SessionFocusAction")
    copy = _function(HARNESS.read_text(encoding="ascii"), "SessionFocusAction")
    assert _norm(main) == _norm(copy)


def test_the_document_is_put_back_before_the_handler_and_before_focus_is_noted():
    body = load(_ALTIUM / "StatusForm.pas", minimum=20)["ProcessSingleRequest"]
    pin = body.index("SessionFocus(ExtractJsonValue(RequestContent, 'session_doc_path'))")
    before = body.index("FocusBefore := CurrentFocusedDocPath(0);")
    handler = body.index("ResponseContent := ProcessCommand(Command, Params, RequestId);")
    assert pin < before < handler
    assert "AppendEnvelopeField(ResponseContent, SessionNote)" in body


def test_only_an_open_document_is_shown_never_reopened():
    body = load(_ALTIUM / "StatusForm.pas", minimum=20)["SessionFocus"]
    assert "Client.GetDocumentByPath(Pin)" in body
    assert "Client.ShowDocument(ServerDoc)" in body
    assert "OpenObject" not in body, "reopening outside its project makes a free document"
    # What came of it is read back, not assumed.
    assert body.index("Client.ShowDocument(ServerDoc)") < body.index(
        "After := CanonicalPathValue('file_path', CurrentFocusedDocPath(0));")
    for key in ("'missing'", "'not_restored'", "'restored'"):
        assert key in body


# ---- Python -------------------------------------------------------------

def test_the_pin_rides_ahead_of_params_and_only_when_there_is_one():
    with_pin = ab.CommandRequest("pcb.get_nets", {"session_doc_path": "x"},
                                 session_doc=r"C:\A\a.PcbDoc").to_dict()
    keys = list(with_pin)
    assert keys.index("session_doc_path") < keys.index("params")
    # Pascal's ExtractJsonValue takes the first match: the envelope's.
    text = json.dumps(with_pin)
    assert text.index('"session_doc_path"') < text.index('"params"')
    assert "session_doc_path" not in ab.CommandRequest("pcb.get_nets").to_dict()


@pytest.mark.parametrize("command,params,unpinned", [
    ("application.ping", {}, True),
    ("application.stop_server", {}, True),
    ("application.get_unit_stamps", {}, True),
    ("application.get_active_document", {"front": "true"}, True),
    ("application.get_active_document", {}, False),
    ("pcb.get_nets", {}, False),
])
def test_what_goes_without_the_pin(command, params, unpinned):
    assert ab._unpinned(command, params) is unpinned


def _reply(**kw):
    return ab.CommandResponse(id="x", success=kw.pop("success", True), **kw)


def test_a_command_that_moved_the_focus_takes_the_session_with_it():
    r = _reply(active_document_changed={"from": "A", "to": "B"})
    assert ab.next_session_doc("A", "pcb.focus_board", {}, r) == "B"


def test_focusing_what_is_already_in_front_still_chooses_it():
    r = _reply(data={"success": True, "file_path": "C"})
    assert ab.next_session_doc("A", "application.set_active_document",
                               {"file_path": "C"}, r) == "C"


def test_a_failed_focus_changes_nothing():
    r = _reply(success=False)
    assert ab.next_session_doc("A", "application.set_active_document",
                               {"file_path": "C"}, r) == "A"


def test_a_closed_document_is_forgotten():
    r = _reply(session_document={"missing": "A", "kept": "B"})
    assert ab.next_session_doc("A", "pcb.get_nets", {}, r) == ""


def test_a_restore_keeps_the_pin():
    r = _reply(session_document={"restored": "A", "from": "B"})
    assert ab.next_session_doc("A", "pcb.get_nets", {}, r) == "A"


def test_the_note_reaches_the_caller():
    r = _reply(data={"nets": []}, session_document={"restored": "A", "from": "B"})
    assert ab._with_envelope_notes(r.data, r)["session_document"]["restored"] == "A"


class _Captured:
    def __init__(self, replies):
        self.sent = []
        self.replies = list(replies)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    bridge = ab.AltiumBridge.__new__(ab.AltiumBridge)
    ab.AltiumBridge.__init__(bridge)
    monkeypatch.setattr(bridge.config, "workspace_dir", tmp_path, raising=False)
    box = _Captured([])

    def publish(request):
        box.sent.append(request.to_dict())

    def poll(request_id, timeout):
        return box.replies.pop(0)

    monkeypatch.setattr(bridge, "_publish_request", publish)
    monkeypatch.setattr(bridge, "_poll_response", poll)
    monkeypatch.setattr(bridge, "_reshow_hidden_frame", lambda c, i: [])
    return bridge, box


def test_the_server_carries_its_document_from_call_to_call(wired):
    bridge, box = wired
    box.replies = [
        _reply(data={}, active_document_changed={"from": "", "to": r"C:\A\a.PcbDoc"}),
        _reply(data={}),
        _reply(data="pong"),
        _reply(data={"file_path": r"C:\B\b.PcbDoc"}),
        _reply(data={}),
    ]
    bridge._execute_command("pcb.focus_board", {}, 5)
    bridge._execute_command("pcb.get_nets", {}, 5)
    bridge._execute_command("application.ping", {}, 5)
    bridge._execute_command("application.get_active_document", {"front": "true"}, 5)
    bridge._execute_command("pcb.get_nets", {}, 5)
    pins = [s.get("session_doc_path", "") for s in box.sent]
    assert pins == ["", r"C:\A\a.PcbDoc", "", "", r"C:\A\a.PcbDoc"]


def test_the_public_tool_asks_for_what_is_really_in_front(monkeypatch):
    from eda_agent.tools import application as app_tools

    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, dict(params or {})))
            return {}

    class _Mcp:
        def __init__(self):
            self.tools = {}

        def tool(self, *a, **k):
            def reg(fn):
                self.tools[fn.__name__] = fn
                return fn
            return reg

    import asyncio
    monkeypatch.setattr(app_tools, "get_bridge", lambda: _Bridge())
    mcp = _Mcp()
    app_tools.register_application_tools(mcp)
    asyncio.run(mcp.tools["app_get_active_document"]())
    asyncio.run(mcp.tools["app_get_active_document"](session=True))
    assert sent[0][1] == {"front": "true"}
    assert sent[1][1] == {}
