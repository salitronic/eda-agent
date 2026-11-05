# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every document path reaches Altium in one spelling.

Altium keys an open document by the text it was opened with, so a
forward-slash path inside a process string opened a second copy of a
library already open with backslashes.
"""
from __future__ import annotations

import asyncio

from eda_agent.bridge import altium_bridge
from eda_agent.bridge.path_spelling import backslashed, one_spelling, same_file
from eda_agent.tools import sch_template


def test_an_absolute_path_is_backslashed_and_nothing_else_is():
    assert backslashed("C:/a/b.SchLib") == "C:\\a\\b.SchLib"
    assert backslashed("//srv/share/b.SchLib") == "\\\\srv\\share\\b.SchLib"
    for kept in ("File|Open", "lib/b.SchLib", "", None, 3):
        assert backslashed(kept) == kept


def test_a_filename_inside_a_process_string_is_backslashed():
    out = one_spelling({"process_name": "WorkspaceManager:OpenObject",
                        "parameters": "ObjectKind=Document|FileName=R:/H/P.SchLib"})
    assert out["parameters"] == "ObjectKind=Document|FileName=R:\\H\\P.SchLib"
    # Leading, middle and lower-case fields; other fields untouched.
    assert one_spelling({"params": "filename=C:/x.PcbDoc|Kind=A/B"}) == {
        "params": "filename=C:\\x.PcbDoc|Kind=A/B"}


def test_paths_lists_and_nested_parts_are_backslashed():
    out = one_spelling({"file_paths": ["C:/a.SchDoc", "rel/b.SchDoc"],
                        "parts": [{"library_path": "D:/L/x.SchLib"}],
                        "menu_path": "File|Save", "note": "C:/left/alone"})
    assert out["file_paths"] == ["C:\\a.SchDoc", "rel/b.SchDoc"]
    assert out["parts"][0]["library_path"] == "D:\\L\\x.SchLib"
    assert out["menu_path"] == "File|Save"
    assert out["note"] == "C:/left/alone"


def test_the_bridge_writes_the_one_spelling(monkeypatch):
    seen = []
    bridge = altium_bridge.AltiumBridge.__new__(altium_bridge.AltiumBridge)
    monkeypatch.setattr(bridge, "is_altium_running", lambda: True, raising=False)
    monkeypatch.setattr(bridge, "_ensure_keepalive", lambda: None, raising=False)
    monkeypatch.setattr(bridge, "_execute_command",
                        lambda c, p, t: seen.append(p) or {}, raising=False)

    class _Cfg:
        poll_timeout = 1.0

    bridge.config = _Cfg()
    bridge.send_command("application.run_process",
                        {"parameters": "ObjectKind=Document|FileName=C:/a/b.SchLib"})
    assert seen == [{"parameters": "ObjectKind=Document|FileName=C:\\a\\b.SchLib"}]


def test_same_file_falls_back_to_the_text(tmp_path):
    f = tmp_path / "a.SchDoc"
    f.write_text("x")
    assert same_file(str(f), str(f).replace("\\", "/"))
    assert same_file("Z:/none/A.SchDoc", "z:\\none\\a.schdoc")
    assert not same_file("Z:/none/A.SchDoc", "Z:/none/B.SchDoc")


def test_focus_uses_a_copy_open_under_another_spelling(monkeypatch, tmp_path):
    lib = tmp_path / "P.SchLib"
    lib.write_text("x")
    other = str(lib)
    asked = str(lib).replace("\\", "/")
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, **kw):
            sent.append((command, dict(params or {})))
            if command == "application.set_active_document":
                if params["file_path"] == other:
                    return {"success": True}
                return {"success": False, "error": "document not loaded"}
            if command == "application.get_open_documents":
                return [{"file_path": other}]
            return {"success": True}

    reply = asyncio.run(sch_template._focus(_Bridge(), asked))
    assert reply == {"success": True}
    assert not any(c == "application.run_process" for c, _ in sent)
