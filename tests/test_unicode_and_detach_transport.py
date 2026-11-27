# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The script units start on Altium 21, non-ASCII text crosses the bridge
whole, a set string keeps its ';', and detaching rewrites nothing clean.

Altium 21 stopped on line 1 of a unit with a byte-order mark. A request's
Chinese path reached the script as '?', and a reply sent U+4E2D back as
U+002D. obj_batch_modify cut Text=first;second to Text=first. Detaching
saved every open document, edited or not, and reported a failed stop as a
clean one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_handlers_verify_their_outcome import _body, _decommented, _src

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
BOM = b"\xef\xbb\xbf"


@pytest.mark.parametrize("unit", sorted(p.name for p in SCRIPTS.glob("*.pas")
                                        if p.name != "Altium_MCP.pas"))
def test_every_unit_is_plain_ascii_without_a_mark(unit):
    # ASCII as well: a UTF-8 byte in a comment, read in a double-byte ANSI
    # codepage, can take the closing brace after it as its second byte.
    raw = (SCRIPTS / unit).read_bytes()
    assert not raw.startswith(BOM)
    bad = [i for i, b in enumerate(raw) if b > 127]
    assert not bad, f"{unit}: non-ASCII byte at offset {bad[0]}"


@pytest.mark.parametrize("stamped", [True, False])
def test_the_installer_writes_no_mark(tmp_path, stamped):
    from eda_agent.cli import _stamp_deployed_unit

    body = ("Function UnitStamp_X(Dummy : Integer) : String;\r\n"
            "Begin\r\n    Result := 'dev';\r\nEnd;\r\n") if stamped else "Begin End.\r\n"
    unit = tmp_path / "X.pas"
    unit.write_bytes(BOM + body.encode("ascii"))
    _stamp_deployed_unit(unit)
    assert not unit.read_bytes().startswith(BOM)


def test_a_request_carries_unicode_as_literal_utf8(tmp_path):
    from eda_agent.bridge.altium_bridge import AltiumBridge, CommandRequest
    from eda_agent.config import configure

    configure(workspace_dir=tmp_path)
    bridge = AltiumBridge()
    try:
        path = r"C:\Projects\中文目录\Demo.SchDoc"
        req = CommandRequest(command="generic.query_objects",
                             params={"scope": f"doc:{path}",
                                     "text": "中文测量 Ω μ ° 😀"})
        bridge._publish_request(req)
        raw = (tmp_path / f"request_{req.id}.json").read_bytes()
    finally:
        bridge.detach()
    # The mark is what makes LoadFromStream read UTF-8, and no \u escape
    # is left for UnescapeJsonString to turn into '?'.
    assert raw.startswith(BOM)
    assert b"\\u" not in raw
    data = json.loads(raw.decode("utf-8-sig"))
    assert data["params"]["scope"] == f"doc:{path}"
    assert data["params"]["text"] == "中文测量 Ω μ ° 😀"


def test_a_reply_escapes_all_four_hex_digits():
    hexer = _decommented(_body(_src("Utils.pas"), "CodeUnitHex4"))
    assert "Shr 12" in hexer and "Shr 8" in hexer and "'00'" not in hexer
    escaper = _decommented(_body(_src("Utils.pas"), "EscapeJsonString"))
    assert escaper.count("CodeUnitHex4(O)") == 2
    errors = _decommented(_body(_src("Main.pas"), "BuildErrorResponseDetailed"))
    assert "Shr 12" in errors and "'\\u00'" not in errors


class _Sent:
    command = None
    params = None


def _tools(monkeypatch, module_name, register, reply=None, raises=None):
    import importlib

    sent = _Sent()

    class FakeBridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.command, sent.params = command, params
            if raises:
                raise raises
            return reply

        def detach(self):
            sent.detached = True

    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "get_bridge", lambda: FakeBridge())
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    getattr(module, register)(DummyMcp())
    return captured, sent


@pytest.mark.asyncio
async def test_a_set_string_keeps_its_semicolon(monkeypatch):
    tools, sent = _tools(monkeypatch, "eda_agent.tools.generic",
                         "register_generic_tools", reply={"ok": True})
    await tools["obj_batch_modify"](operations=[
        {"scope": "active_doc", "object_type": "eLabel",
         "filter": "Location.X=100", "set": "Text=first;second"},
        {"scope": "active_doc", "object_type": "eLabel",
         "filter": "Location.X=200", "set": "Text=unchanged"}])
    first, second = sent.params["operations"].split("~~")
    assert first.endswith(";set=Text=first;second")
    assert second.endswith(";set=Text=unchanged")
    # The script reads set with GetBatchTail, which runs to the end.
    gen = _decommented(_body(_src("Generic.pas"), "Gen_BatchModify"))
    assert "SetStr := GetBatchTail(OpStr, 'set');" in gen


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("filter", "Text=a;set=b"),
                                         ("filter", "Text=a~~b"),
                                         ("scope", "doc:a;b.SchDoc"),
                                         ("set", "Text=a~~b")])
async def test_a_delimiter_the_wire_cannot_carry_is_refused(monkeypatch, field, value):
    tools, sent = _tools(monkeypatch, "eda_agent.tools.generic",
                         "register_generic_tools", reply={"ok": True})
    op = {"scope": "active_doc", "object_type": "eLabel",
          "filter": "Location.X=100", "set": "Text=x"}
    op[field] = value
    out = await tools["obj_batch_modify"](operations=[op])
    assert out["ok"] is False and field in out["reason"]
    assert sent.command is None


@pytest.mark.asyncio
async def test_a_failed_stop_is_not_reported_as_stopped(monkeypatch):
    tools, _ = _tools(monkeypatch, "eda_agent.tools.application",
                      "register_application_tools",
                      raises=TimeoutError("no reply in 60 s"))
    out = await tools["app_detach"]()
    assert out["attached"] is False and out["stopped"] is False
    assert "no reply in 60 s" in out["reason"]


@pytest.mark.asyncio
async def test_a_confirmed_stop_reports_what_it_saved(monkeypatch):
    tools, _ = _tools(monkeypatch, "eda_agent.tools.application",
                      "register_application_tools",
                      reply={"stopped": True, "edited_saved": 1,
                             "clean_left_alone": 5})
    out = await tools["app_detach"]()
    assert out["stopped"] is True
    assert (out["edited_saved"], out["clean_left_alone"]) == (1, 5)


def test_stopping_writes_only_edited_documents():
    stop = _decommented(_body(_src("Application.pas"), "App_StopServer"))
    assert stop.index("SaveScope := 'edited'") < stop.index("SaveAllDirty(0)") \
        < stop.index("SaveScope := ''")
    assert "Running := False" in stop
    main = _src("Main.pas")
    save = _decommented(main[main.index("Procedure SaveOneDocByDocRef("):
                             main.index("Procedure SaveProjectMembers(")])
    gate = save.index("If SaveScope = 'edited' Then")
    assert "(Not ServerDoc.Modified) And (Not DocWasEdited(Path))" in save[gate:]
    assert gate < save.index("ServerDoc.SetModified(True)")
    # A written document comes off the edited list whatever its kind, or a
    # sheet edited once is rewritten by every later stop.
    forget = save[save.index("ServerDoc.DoFileSave('')"):]
    assert "ForgetDocEdited(Path)" in forget
    assert "IsLibraryDocPath" not in forget
    one = _decommented(_body(_src("Application.pas"), "App_SaveDocument"))
    assert "If Saved Then ForgetDocEdited(FilePath);" in one
    # app_save_all keeps the full flush: its scope is never 'edited'.
    assert "SaveScope" not in _decommented(_body(_src("Application.pas"), "App_SaveAll"))
