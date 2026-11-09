# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A sheet's template can be read, set, updated and removed with no dialog.

Nothing set a sheet's template: only its file name was read. Altium's own
Sch:UpdateCurrentTemplate and Sch:RemoveTemplate always ask for a scope in
a modal and then report in another, which blocks the polling loop.
Measured on AD 25.8.1, Sch:ChangeCurrentTemplate with TemplateSource=Local,
FileName and Silent=True applies a template with no dialog. Removing is done
the way ExtractTemplate.pas does it: the eTemplate container leaves the
sheet. Every action is read back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import generic as generic_tools

from tests.pascal_source import load

_GEN = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _units():
    return load(_GEN, minimum=20)


def _body():
    return _units()["Gen_SheetTemplate"]


def test_the_command_is_routed():
    assert "'sheet_template':    Result := Gen_SheetTemplate(Params, RequestId);" in \
        _GEN.read_text(encoding="utf-8")


def test_set_runs_the_silent_change_with_a_local_file():
    body = _body()
    assert "AddStringParameter('TemplateSource', 'Local');" in body
    assert "AddStringParameter('FileName', TplFile);" in body
    assert "AddStringParameter('Silent', 'True');" in body
    assert "RunProcess('Sch:ChangeCurrentTemplate');" in body


def test_the_dialog_forms_are_never_run():
    body = _body()
    assert "Sch:UpdateCurrentTemplate" not in body
    assert "Sch:RemoveTemplate" not in body
    assert "Sch:SetTemplateFromVault" not in body


def test_a_missing_or_wrong_file_is_refused_before_the_process():
    body = _body()
    run = body.index("RunProcess('Sch:ChangeCurrentTemplate');")
    assert body.index("If Not FileExists(TplFile) Then") < run
    assert body.index("<> '.schdot' Then") < run
    assert "'TEMPLATE_NOT_FOUND'" in body and "'NOT_A_TEMPLATE'" in body


def test_update_reads_the_sheets_own_file_again():
    body = _body()
    assert "If (What = 'update') And (TplFile = '') Then TplFile := Before;" in body


def test_set_is_read_back_by_name_and_by_object():
    body = _body()
    assert "Ok := (LowerCase(After) = LowerCase(TplFile)) And (Count = 1);" in body


def test_remove_takes_the_template_object_off_and_clears_the_name():
    body = _body()
    assert "SchDoc.RemoveSchObject(Tpl);" in body
    assert "SchDoc.TemplateFileName := '';" in body
    assert "Ok := (Count = 0) And (After = '');" in body
    # The same template found twice means the remove did nothing: stop.
    assert "If (ThisAddr <> 0) And (ThisAddr = LastAddr) Then Break;" in body


def test_the_template_is_found_as_the_eTemplate_container():
    assert "If Obj.ObjectId = eTemplate Then" in _units()["SheetTemplateObject"]


def test_the_vault_link_is_read_only_when_asked():
    units = _units()
    assert "If Vault Then" in units["SheetTemplateJson"]
    assert "SheetTemplateVaultJson" not in units["Gen_SheetTemplate"]
    assert "SchDoc.TemplateVaultGUID" in units["SheetTemplateVaultJson"]


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply if reply is not None else {"applied": True}

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return dict(self.reply)


def _call(monkeypatch, reply=None, **kw):
    bridge = _Bridge(reply)
    monkeypatch.setattr(generic_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    generic_tools.register_generic_tools(mcp)
    out = asyncio.run(mcp.tools["sch_sheet_template"](**kw))
    return out, bridge.sent


def test_a_read_sends_only_the_action(monkeypatch):
    _, sent = _call(monkeypatch)
    assert sent == [("generic.sheet_template", {"action": "get"})]


def test_set_sends_the_file(monkeypatch):
    _, sent = _call(monkeypatch, action="Set", template_file=r"C:\t\A.SchDot")
    assert sent == [("generic.sheet_template",
                     {"action": "set", "template_file": r"C:\t\A.SchDot"})]


def test_vault_is_asked_for_by_flag(monkeypatch):
    _, sent = _call(monkeypatch, vault=True)
    assert sent[0][1] == {"action": "get", "vault": "true"}


def test_bad_requests_are_refused_before_sending(monkeypatch):
    for kw in ({"action": "explode"},
               {"action": "set"},
               {"action": "set", "template_file": r"C:\t\A.SchDoc"},
               {"action": "remove", "template_file": r"C:\t\A.SchDot"}):
        out, sent = _call(monkeypatch, **kw)
        assert out["ok"] is False and not sent, kw


def test_update_without_a_file_is_sent(monkeypatch):
    _, sent = _call(monkeypatch, action="update")
    assert sent == [("generic.sheet_template", {"action": "update"})]


def test_a_sheet_that_does_not_read_back_is_not_ok(monkeypatch):
    out, _ = _call(monkeypatch, reply={"applied": False},
                   action="remove")
    assert out["ok"] is False and "read back" in out["reason"]
    out, _ = _call(monkeypatch, reply={"applied": True}, action="remove")
    assert "ok" not in out


def test_a_named_sheet_is_the_one_acted_on_and_the_view_comes_back():
    body = _body()
    named = body.index("SchDoc := SchDocByPathLoading(SheetPath);")
    assert body.index("SheetPath := ExtractJsonValue(Params, 'file_path');") < named
    assert "'SHEET_NOT_FOUND'" in body
    # The template command acts on the sheet in front: the named one is
    # brought forward before it runs and the user's view put back after.
    run = body.index("RunProcess('Sch:ChangeCurrentTemplate');")
    show = body.index("Client.ShowDocument(SheetDoc)")
    restore = body.index("Client.ShowDocument(PrevView.OwnerDocument)", run)
    assert named < show < run < restore
    assert "'SHEET_NOT_SHOWN'" in body


def test_the_tool_passes_the_sheet_it_names(monkeypatch):
    import asyncio
    from eda_agent.tools import generic

    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"applied": True}

    monkeypatch.setattr(generic, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(Mcp())
    asyncio.run(tools["sch_sheet_template"](action="get", file_path=r"C:\p\A.SchDoc"))
    assert sent[-1][1]["file_path"] == r"C:\p\A.SchDoc"
