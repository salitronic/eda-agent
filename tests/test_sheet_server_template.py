# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A sheet template from the Altium server is set and updated by its GUIDs.

sch_sheet_template set and updated a template only from a local .SchDot,
through Sch:ChangeCurrentTemplate, so a template kept on the server could
not be applied, and updating a sheet linked to one re-applied its cached
file as a LOCAL template and dropped the link. Measured on AD 25.8.1,
SchServer.SetDocumentManagedTemplate(Doc, Vault, Revision, Action, True)
applies a server template with no dialog (Altium downloads it and writes
the vault, item and revision link), and SchServer.UpdateDocumentTemplate
(Doc, Vault, Revision) moves a linked sheet to another revision and keeps
the link. set takes vault_guid and revision_guid in place of
template_file; update keeps a link, from the GUIDs given or the ones the
sheet carries; both are judged by the link the sheet reads back.

The GUID check and the parameter action names are lifted out of
Generic.pas and run under Free Pascal; the handler's order is read from the
source; the tool runs against a stand-in bridge.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import generic
from eda_agent.tools.generic import TEMPLATE_PARAMETER_ACTIONS, template_guid

from tests.pascal_source import load, strip_comments

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"

_GUID = "1A2B3C4D-5E6F-4A7B-8C9D-0E1F2A3B4C5D"
_REV = "9F8E7D6C-5B4A-4321-8765-0FEDCBA98765"

# (given, what Altium is handed: the GUID as it writes one, or "" = refused)
_GUIDS = [
    (_GUID, _GUID),
    (_GUID.lower(), _GUID),
    ("{" + _GUID + "}", _GUID),
    ("  " + _REV + " ", _REV),
    ("", ""),
    (_GUID[:-1], ""),
    (_GUID + "0", ""),
    (_GUID.replace("5E6F", "5E6G"), ""),
    (_GUID.replace("-", "", 1) + "0", ""),
    (_GUID[:8] + "|" + _GUID[9:], ""),
    (_GUID[:8] + "'" + _GUID[9:], ""),
    ("{" + _GUID, ""),
    ("{{" + _GUID + "}}", ""),
]


def _units() -> dict[str, str]:
    return load(_GENERIC, minimum=20)


def _body() -> str:
    return _units()["Gen_SheetTemplate"]


def _pascal(tmp_path, lines: list[str], routines: list[str]) -> dict[str, str]:
    """Run ``lines`` against the named routines of Generic.pas under fpc."""
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    units = _units()
    program = "\n".join(["program sheettpl;", "{$mode delphi}", "uses SysUtils;",
                         *(units[name] for name in routines),
                         "begin", *lines, "end."])
    path = tmp_path / "sheettpl.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True,
                           text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("sheettpl.exe" if os.name == "nt" else "sheettpl")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    out = {}
    for line in ran.stdout.splitlines():
        key, _, value = line.partition("=>")
        out[key] = value
    return out


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def test_the_handler_takes_a_guid_only_in_altiums_form(tmp_path):
    lines = [f"  Writeln('{i}=>[' + SheetTemplateGuid({_quoted(given)}) + ']');"
             for i, (given, _) in enumerate(_GUIDS)]
    out = _pascal(tmp_path, lines, ["SheetTemplateGuidKey", "SheetTemplateGuid"])
    for i, (given, want) in enumerate(_GUIDS):
        assert out[str(i)] == f"[{want}]", given


def test_the_tool_takes_the_same_guids_as_the_handler():
    for given, want in _GUIDS:
        assert (template_guid(given) or "") == want, given


def test_a_read_back_guid_is_compared_without_case_or_braces(tmp_path):
    out = _pascal(tmp_path, [
        f"  Writeln('a=>[' + SheetTemplateGuidKey({_quoted('{' + _GUID.lower() + '}')}) + ']');",
        f"  Writeln('b=>[' + SheetTemplateGuidKey({_quoted(' ' + _GUID + ' ')}) + ']');",
    ], ["SheetTemplateGuidKey"])
    assert out["a"] == out["b"] == f"[{_GUID}]"


def test_the_parameter_action_names_are_altiums_update_actions(tmp_path):
    words = ["", "none", "add_new", "override_all", "auto", " AUTO ",
             "replace", "1"]
    lines = [f"  Writeln('{w}=>' + IntToStr(SheetTemplateUpdateAction({_quoted(w)})));"
             for w in words]
    out = _pascal(tmp_path, lines, ["SheetTemplateUpdateAction"])
    # TTemplateUpdateAction: DoNothing 0, OnlyAddNewParameters 1,
    # OverrideAllParameters 2, Auto 3. Nothing given adds new parameters.
    assert {w: out[w] for w in words} == {
        "": "1", "none": "0", "add_new": "1", "override_all": "2",
        "auto": "3", " AUTO ": "3", "replace": "-1", "1": "-1"}
    assert TEMPLATE_PARAMETER_ACTIONS == ("none", "add_new", "override_all", "auto")


def test_the_server_arguments_are_checked_before_the_sheet_is_touched():
    body = _body()
    sheet = body.index("SchDoc := SchDocByPathLoading(SheetPath);")
    assert "RawVault := ExtractJsonValue(Params, 'vault_guid');" in body
    assert "RawRev := ExtractJsonValue(Params, 'revision_guid');" in body
    assert body.index("'vault_guid and revision_guid are for set and update'") < sheet
    flat = " ".join(body.split())
    assert ("If Server Then Begin If (What <> 'set') And (What <> 'update') Then "
            "Begin Result := BuildErrorResponse(RequestId, 'BAD_ARGUMENT', "
            "'vault_guid and revision_guid are for set and update');") in flat
    assert body.index("'Give template_file or vault_guid and revision_guid, not both'") < sheet
    assert body.index("VaultGuid := SheetTemplateGuid(RawVault);") < sheet
    assert body.index("'BAD_GUID'") < sheet
    assert body.index("'parameter_action is for a set from the server'") < sheet
    assert "If Not (Server And (What = 'set')) Then" in body
    assert body.index("UpdAction := SheetTemplateUpdateAction(ActName);") < sheet


def test_set_and_update_call_the_server_with_the_sheets_document():
    body = _body()
    doc = body.index("SheetDoc := Client.GetDocumentByPath(SchDoc.DocumentName);")
    put = body.index("SchServer.SetDocumentManagedTemplate(SheetDoc, VaultGuid, "
                     "RevGuid, UpdAction, True)")
    upd = body.index("SchServer.UpdateDocumentTemplate(SheetDoc, VaultGuid, RevGuid);")
    assert doc < put < upd
    assert "If What = 'set' Then" in body[doc:put]
    assert "'NO_SERVER_DOCUMENT'" in body[upd:]


def test_the_process_route_runs_only_for_a_local_template():
    body = _body()
    upd = body.index("SchServer.UpdateDocumentTemplate(")
    run = body.index("RunProcess('Sch:ChangeCurrentTemplate');")
    assert body.index("If Server Then", body.index("Client.GetCurrentView")) < upd
    assert "Else" in body[upd:run]
    assert body.count("RunProcess(") == 1


def test_update_alone_keeps_the_link_the_sheet_carries():
    body = _body()
    link = body.index("VaultGuid := Trim(SheetTemplateLinkVault(SchDoc));")
    assert "RevGuid := Trim(SheetTemplateLinkRevision(SchDoc));" in body
    decide = body.index("Server := (VaultGuid <> '') And (RevGuid <> '');")
    local = body.index("If (What = 'update') And (TplFile = '') Then TplFile := Before;")
    assert "If (What = 'update') And (Not Server) And (TplFile = '') Then" in body
    assert link < decide < local
    assert body.index("If Not Server Then") < local


def test_a_named_sheet_is_brought_forward_for_the_server_calls_too():
    # Measured only on the sheet in front: a named sheet behind another is
    # brought forward whichever route runs, and the view put back after.
    body = " ".join(_body().split())
    checks = body.index("'No such file: ' + TplFile")
    front = body.index("Front := SchServer.GetCurrentSchDocument; If (Front = Nil) "
                       "Or (UpperCase(Front.DocumentName) <> "
                       "UpperCase(SchDoc.DocumentName)) Then")
    show = body.index("Client.ShowDocument(SheetDoc)")
    put = body.index("SchServer.SetDocumentManagedTemplate(")
    restore = body.index("Client.ShowDocument(PrevView.OwnerDocument)", put)
    assert checks < front < show < put < restore


def test_a_server_template_is_judged_by_the_link_read_back():
    body = " ".join(_body().split())
    assert ("Ok := (After <> '') "
            "And (SheetTemplateGuidKey(SheetTemplateLinkVault(SchDoc)) = "
            "SheetTemplateGuidKey(VaultGuid)) "
            "And (SheetTemplateGuidKey(SheetTemplateLinkRevision(SchDoc)) = "
            "SheetTemplateGuidKey(RevGuid))") in body
    # The local route keeps its own check: the file and one template.
    assert "Ok := (LowerCase(After) = LowerCase(TplFile)) And (Count = 1);" in body


def test_the_reply_says_which_route_ran_and_carries_the_link():
    body = " ".join(_body().split())
    assert "JsonStr('template_source', 'server')" in body
    assert "JsonStr('template_source', 'local')" in body
    assert "JsonRaw('asked', JsonObj(JsonStr('vault_guid', VaultGuid)" in body
    assert "SheetTemplateJson(SchDoc, Vault Or Server)" in body
    units = _units()
    assert "SchDoc.TemplateVaultGUID" in units["SheetTemplateLinkVault"]
    assert "SchDoc.TemplateRevisionGUID" in units["SheetTemplateLinkRevision"]


def test_the_workspace_template_manager_is_never_asked():
    code = strip_comments(_GENERIC.read_text(encoding="utf-8", errors="replace"))
    assert "DM_GetTemplateManager" not in code


class _Bridge:
    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply if reply is not None else {"applied": True}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, dict(params or {})))
        return dict(self.reply)


def _call(monkeypatch, reply=None, **kwargs):
    bridge = _Bridge(reply)
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    out = asyncio.run(captured["sch_sheet_template"](**kwargs))
    return out, bridge.sent


def test_set_from_the_server_sends_the_guids_as_altium_writes_them(monkeypatch):
    _, sent = _call(monkeypatch, action="set", vault_guid=_GUID.lower(),
                    revision_guid="{" + _REV + "}")
    assert sent == [("generic.sheet_template",
                     {"action": "set", "vault_guid": _GUID, "revision_guid": _REV})]


def test_the_parameter_action_is_sent_only_when_given(monkeypatch):
    _, sent = _call(monkeypatch, action="set", vault_guid=_GUID,
                    revision_guid=_REV, parameter_action="Override_All")
    assert sent[0][1]["parameter_action"] == "override_all"
    _, sent = _call(monkeypatch, action="set", vault_guid=_GUID, revision_guid=_REV)
    assert "parameter_action" not in sent[0][1]


def test_update_sends_the_guids_or_nothing(monkeypatch):
    _, sent = _call(monkeypatch, action="update", vault_guid=_GUID,
                    revision_guid=_REV, file_path=r"C:\p\A.SchDoc")
    assert sent == [("generic.sheet_template",
                     {"action": "update", "vault_guid": _GUID,
                      "revision_guid": _REV, "file_path": r"C:\p\A.SchDoc"})]
    _, sent = _call(monkeypatch, action="update")
    assert sent == [("generic.sheet_template", {"action": "update"})]


def test_bad_server_requests_are_refused_before_sending(monkeypatch):
    for kwargs in ({"action": "set", "vault_guid": _GUID},
                   {"action": "update", "revision_guid": _REV},
                   {"action": "set", "vault_guid": _GUID, "revision_guid": "12"},
                   {"action": "set", "vault_guid": _GUID + "|x", "revision_guid": _REV},
                   {"action": "get", "vault_guid": _GUID, "revision_guid": _REV},
                   {"action": "remove", "vault_guid": _GUID, "revision_guid": _REV},
                   {"action": "set", "vault_guid": _GUID, "revision_guid": _REV,
                    "template_file": r"C:\t\A.SchDot"},
                   {"action": "set", "template_file": r"C:\t\A.SchDot",
                    "parameter_action": "none"},
                   {"action": "update", "vault_guid": _GUID, "revision_guid": _REV,
                    "parameter_action": "none"},
                   {"action": "set", "vault_guid": _GUID, "revision_guid": _REV,
                    "parameter_action": "replace"}):
        out, sent = _call(monkeypatch, **kwargs)
        assert out["ok"] is False and not sent, kwargs


def test_a_server_set_that_does_not_read_back_is_not_ok(monkeypatch):
    out, _ = _call(monkeypatch, reply={"applied": False, "template_source": "server"},
                   action="set", vault_guid=_GUID, revision_guid=_REV)
    assert out["ok"] is False and "vault and revision asked" in out["reason"]
    out, _ = _call(monkeypatch, reply={"applied": False, "template_source": "local"},
                   action="set", template_file=r"C:\t\A.SchDot")
    assert out["ok"] is False and "template_objects" in out["reason"]
    out, _ = _call(monkeypatch, reply={"applied": True, "template_source": "server"},
                   action="update")
    assert "ok" not in out
