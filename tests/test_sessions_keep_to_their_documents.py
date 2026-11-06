# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Sessions sharing one Altium keep to their own documents and dialogs.

Several Claude sessions share one Altium (one instance per licence). A
write from one session could land on a board another was editing, and a
dialog one session raised blocked the others with nothing to say whose it
was. A tool that writes marks its commands; Altium gives the document it
acts on to that session and refuses another session's writes to it while
the owner is active. Reads are never refused, and neither is the person
at the dashboard. A call blocked by a dialog says whose call raised it.
"""
from __future__ import annotations

import re
from pathlib import Path

from eda_agent.bridge import altium_bridge as ab

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^(Function|Procedure) {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


# -- what the server sends --------------------------------------------------

def test_a_writing_tool_marks_its_commands_and_a_read_does_not():
    with ab.tool_call("pcb_place_tracks"):
        assert ab._call_writes() is True
    with ab.tool_call("pcb_get_components"):
        assert ab._call_writes() is False
    assert ab._call_writes() is False          # outside a tool call


def test_a_proxy_marks_its_commands_by_the_tool_it_runs():
    with ab.tool_call("tool_invoke"):
        ab.name_tool_call("pcb_get_components")
        assert ab._call_writes() is False
        ab.name_tool_call("pcb_place_vias")
        assert ab._call_writes() is True


def test_the_marks_ride_ahead_of_the_params():
    req = ab.CommandRequest(command="pcb.place_tracks",
                            params={"writes": "0", "actor": "x"},
                            writes=True, actor="user")
    keys = list(req.to_dict())
    assert keys.index("actor") < keys.index("params")
    assert keys.index("writes") < keys.index("params")
    assert req.to_dict()["writes"] == "1" and req.to_dict()["actor"] == "user"
    plain = ab.CommandRequest(command="pcb.get_components").to_dict()
    assert "writes" not in plain and "actor" not in plain


def test_the_dashboard_acts_as_the_user():
    assert ab._actor() == ""
    with ab.following_front(background=False):
        assert ab._actor() == "user"


# -- whose dialog --------------------------------------------------------------

def _workspace(tmp_path, tag, command):
    (tmp_path / f"progress_{tag}-00000012-k1a2b.json").write_text('{"started_ms":1}')
    (tmp_path / "bridge_trace.log").write_text(
        f"1.0 SEND cmd=application.ping id={tag}\n"
        f"2.0 SEND cmd={command} id={tag}\n"
        f"3.0 SEND cmd=application.ping id={tag}\n", encoding="utf-8")


def test_a_dialog_raised_by_another_session_is_named_and_left_alone(tmp_path):
    _workspace(tmp_path, "deadbeef", "library.compile_package")
    owner = ab.dialog_owner(tmp_path)
    assert owner == {"session": "deadbeef", "yours": False,
                     "command": "library.compile_package"}
    text = ab.AltiumBridge._dialog_suffix({"summary": "Save all?"}, owner)
    assert "ANOTHER CLAUDE SESSION" in text and "library.compile_package" in text
    assert "press a button" not in text


def test_a_dialog_of_this_session_is_its_own_to_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "_SESSION_TAG", "cafef00d")
    _workspace(tmp_path, "cafef00d", "pcb.teardrops")
    owner = ab.dialog_owner(tmp_path)
    assert owner["yours"] is True
    text = ab.AltiumBridge._dialog_suffix({"summary": "Teardrops"}, owner)
    assert "own call" in text and "press a button" in text


def test_no_marker_says_nothing(tmp_path):
    assert ab.dialog_owner(tmp_path) is None


# -- what Altium does ----------------------------------------------------------

def _refusal_rule(body: str) -> bool:
    """Refused only for a marked write, not from the dashboard, not a read
    verb, and never the hand-over itself."""
    gate = body[body.index("OwnRefused := False;"):body.index("ProcessCommand(Command, Params, RequestId)")]
    return all(x in gate for x in ("Writes", "(Actor <> 'user')",
                                   "Not CommandOnlyLooks(Command)",
                                   "OwnerRefusal(OwnTarget, RequestId, Command)"))


def test_dispatch_refuses_another_sessions_write_before_the_handler():
    body = _routine("StatusForm.pas", "ProcessSingleRequest")
    assert _refusal_rule(body)
    after = body[body.index("ProcessCommand(Command, Params, RequestId)"):]
    assert "OwnerSet(OwnerKey(OwnTarget), RequestTag(RequestId), Command)" in after
    assert "(Not OwnRefused)" in after and "Not CommandOnlyLooks(Command)" in after
    # The target is a path the command names, else the document in front
    # once the session's own is back.
    focus = body.index("SessionFocus(ExtractJsonValue(RequestContent, 'session_doc_path'))")
    assert focus < body.index("If OwnTarget = '' Then OwnTarget := FocusBefore;")


def test_the_rule_rejects_a_gate_that_lets_the_dashboard_be_refused():
    body = _routine("StatusForm.pas", "ProcessSingleRequest")
    gate = "If (ResponseContent = '') And Writes And (Actor <> 'user')"
    assert gate in body
    assert not _refusal_rule(body.replace(gate, "If (ResponseContent = '') And Writes"))


def test_the_refusal_names_the_owner_and_ends_after_idle():
    body = _routine("StatusForm.pas", "OwnerRefusal")
    assert "If Owner = RequestTag(RequestId) Then Exit;" in body
    assert "If Age >= OWNER_IDLE_MS Then Exit;" in body
    assert "'DOCUMENT_IN_USE'" in body and "app_claim_document" in body


def test_paths_are_one_key_however_spelled():
    body = _routine("Main.pas", "OwnerKey")
    assert "UpperCase(" in body and "StringReplace(Path, '/', '\\', -1)" in body


def test_owners_are_listed_and_handed_over():
    code = (SCRIPTS / "Application.pas").read_text(encoding="utf-8")
    assert "'document_owners':     Result := App_DocumentOwners(RequestId);" in code
    assert "'claim_document':      Result := App_ClaimDocument(Params, RequestId);" in code
    claim = _routine("Application.pas", "App_ClaimDocument")
    assert "OwnerSet(Key, RequestTag(RequestId), 'claimed')" in claim


def test_bringing_a_document_forward_neither_claims_nor_is_refused():
    looks = _routine("StatusForm.pas", "CommandOnlyLooks")
    assert "CommandIsReadOnly(Command)" in looks
    for verb in ("focus_board", "set_active_document", "zoom", "claim_document"):
        assert f"(Verb = '{verb}')" in looks, verb


# -- one tag per Claude session ------------------------------------------------

class _Proc:
    def __init__(self, pid, name, cmd=(), parents=()):
        self.pid, self._name, self._cmd, self._parents = pid, name, list(cmd), list(parents)

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmd

    def create_time(self):
        return 1000.0 + self.pid

    def parents(self):
        return self._parents


def _tag_under(monkeypatch, chain):
    import psutil
    monkeypatch.setattr(psutil, "Process", lambda: _Proc(1, "python.exe", parents=chain))
    return ab._claude_session_tag()


def test_every_server_of_one_claude_session_shares_its_tag(monkeypatch):
    """A session runs two eda-agent servers, one behind a reload proxy.
    With a random tag each, Altium refused the session's own write."""
    claude = _Proc(500, "claude.exe")
    full = _tag_under(monkeypatch, [_Proc(2, "eda-agent.exe"), claude])
    proxied = _tag_under(monkeypatch, [_Proc(3, "python.exe"),
                                       _Proc(4, "eda-agent.exe"), claude])
    other = _tag_under(monkeypatch, [_Proc(5, "eda-agent.exe"), _Proc(600, "claude.exe")])
    assert full == proxied != other
    assert re.fullmatch(r"[0-9a-f]{8}", full)


def test_a_server_outside_claude_gets_its_own_tag(monkeypatch):
    first = _tag_under(monkeypatch, [_Proc(7, "bash.exe")])
    second = _tag_under(monkeypatch, [_Proc(7, "bash.exe")])
    assert first != second
