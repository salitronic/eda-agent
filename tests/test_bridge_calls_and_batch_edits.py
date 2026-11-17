# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Board and project reads that answered wrongly, a batch move done as one
edit, a keep-alive that pings only when nothing else reached Altium, and
the documents a session's commands opened, kept so they can be closed.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

import pytest

from eda_agent.bridge import altium_bridge as ab
from eda_agent.tools import application as app_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load, strip_comments

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _fn(unit: str, name: str) -> str:
    return load(_ALTIUM / unit)[name]


# -- the Pascal ------------------------------------------------------------

def test_a_limit_of_0_lists_every_pin():
    body = strip_comments(_fn("Project.pas", "Proj_GetNets"))
    assert "(Limit > 0) And (Count >= Limit)" in body
    assert not re.search(r"If Count >= Limit Then", body)


def test_a_layer_already_in_the_stack_is_refused_before_any_edit():
    body = strip_comments(_fn("PCB.pas", "PCB_AddLayer"))
    assert "ALREADY_IN_STACK" in body
    assert body.index("ALREADY_IN_STACK") < body.index("PcbBeginEdit(0)")
    assert "Walk.LayerID = TargetLayer" in body


def test_a_batch_move_is_one_edit_round_with_each_part_modified_inside_it():
    body = strip_comments(_fn("PCB.pas", "PCB_BatchMoveComponents"))
    assert body.count("PcbBeginEdit(0)") == 1 and body.count("PcbEndEdit(0)") == 1
    # The round opens before the loop over the parts and closes after it.
    assert body.index("PcbBeginEdit(0)") < body.index("While Length(Remaining) > 0 Do")
    assert body.index("PcbEndEdit(0)") > body.index("Applied := Applied + 1;")
    assert body.count("PCBM_BeginModify") == 1 and body.count("PCBM_EndModify") == 1
    # A board-wide registration after a bulk edit stopped the script engine.
    assert "PCBM_BoardRegisteration" not in body


def test_an_ieee_symbol_resolves_and_reads_only_through_its_own_interface():
    generic = (_ALTIUM / "Generic.pas").read_text(encoding="utf-8")
    assert re.search(r"N = 'ieeesymbol'\s+Then Result := eSymbol", generic)
    get = strip_comments(load(_ALTIUM / "Generic.pas")["SchGetShape"])
    branch = get[get.index("Obj.ObjectId = eSymbol"):]
    assert "SymObj := Obj;" in branch and "SymObj.ScaleFactor" in branch
    put = strip_comments(load(_ALTIUM / "Generic.pas")["SchSetShape"])
    # Read only: the setter leaves before any branch casts the object.
    assert put.index("If Owner = 6 Then Exit;") < put.index("If Owner <= 3 Then")


def test_a_components_text_visibility_reads_and_writes_and_a_texts_hidden_flag_reads():
    src = load(_ALTIUM / "PCBGeneric.pas")
    reader = strip_comments(src["GetPCBProperty"])
    setter = strip_comments(src["SetPCBProperty"])
    assert "Obj.IsHidden" in reader
    hidden = setter[setter.index("PropName = 'IsHidden'"):]
    assert hidden.index("Result := -1;") < hidden.index("Exit;") < 200
    text = (_ALTIUM / "PCBGeneric.pas").read_text(encoding="utf-8")
    assert "Comp.NameOn := B" in text and "Comp.CommentOn := B" in text
    assert "BoolToJsonStr(Comp.NameOn)" in text and "BoolToJsonStr(Comp.CommentOn)" in text
    known = strip_comments(src["IsKnownPCBProperty"])
    assert "'IsHidden'" in known


def test_the_dispatcher_reports_the_documents_a_command_opened():
    body = strip_comments(load(_ALTIUM / "StatusForm.pas")["ProcessSingleRequest"])
    # What was loaded is read before the handler and compared after it.
    assert body.index("LoadedBefore := LoadedNow(NamedDocs);") < body.index("StartProgress(RequestId);")
    assert "OpenedDocumentsField(NamedDocs, LoadedBefore)" in body
    assert body.index("NamedDocs.Free;") > body.index("EndProgress(RequestId);")
    collect = strip_comments(load(_ALTIUM / "StatusForm.pas")["CollectDocPaths"])
    # A project is never named: it is not this tool's to close.
    assert "project_path" not in collect


# -- the keep-alive --------------------------------------------------------

def test_an_answer_in_the_interval_spares_the_keepalive_ping(tmp_path):
    assert not ab._answered_within(tmp_path, 30)
    ab._stamp_answer(tmp_path)
    assert ab._answered_within(tmp_path, 30)
    old = time.time() - 60
    os.utime(tmp_path / ab.ANSWER_STAMP, (old, old))
    assert not ab._answered_within(tmp_path, 30)


def test_the_keepalive_skips_its_ping_only_while_altium_answers():
    src = Path(ab.__file__).read_text(encoding="utf-8")
    loop = src[src.index("    def _keepalive_loop"):src.index("    def _keepalive_loop") + 4000]
    skip = loop.index("_answered_within(")
    assert loop.index("consecutive_failures == 0", skip - 120) < skip < loop.index('"application.ping"')


# -- documents a session opened --------------------------------------------

def test_a_reply_names_what_it_opened_and_the_session_keeps_it(tmp_path):
    r = ab.CommandResponse.from_dict({"id": "1", "success": True, "data": {},
                                      "opened_documents": ["C:\\w\\a.PcbDoc"]})
    assert r.opened_documents == ["C:\\w\\a.PcbDoc"]
    ab.note_opened_documents(tmp_path, r.opened_documents)
    ab.note_opened_documents(tmp_path, ["c:\\W\\A.PcbDoc", "C:\\w\\b.SchDoc"])
    assert ab.read_opened_documents(tmp_path) == ["C:\\w\\a.PcbDoc", "C:\\w\\b.SchDoc"]
    ab.keep_opened_documents(tmp_path, [])
    assert ab.read_opened_documents(tmp_path) == []


class _Bridge:
    def __init__(self, docs):
        self.docs = docs
        self.closed = []

    async def send_command_async(self, command, params=None, *a, **k):
        if command == "application.get_open_documents":
            return self.docs
        if command == "application.close_document":
            self.closed.append(params["file_path"])
            return {"closed": True, "file_path": params["file_path"]}
        return {}


@pytest.fixture()
def close_opened(monkeypatch, tmp_path):
    from eda_agent import config

    monkeypatch.setattr(config.get_config(), "workspace_dir", tmp_path, raising=False)

    def run(opened, docs, **kw):
        ab.keep_opened_documents(tmp_path, opened)
        bridge = _Bridge(docs)
        monkeypatch.setattr(app_mod, "get_bridge", lambda: bridge)
        registry = ToolRegistry()
        app_mod.register_application_tools(registry)
        tool = {t.name: t.fn for t in asyncio.run(registry.list_tools())}["app_close_opened_documents"]
        return asyncio.run(tool(**kw)), bridge, ab.read_opened_documents(tmp_path)

    return run


_OPEN = [{"file_path": "C:\\w\\a.PcbDoc", "loaded": True, "modified": False},
         {"file_path": "C:\\w\\b.SchDoc", "loaded": True, "modified": True},
         {"file_path": "C:\\w\\mine.PcbDoc", "loaded": True, "modified": False},
         {"file_path": "C:\\person\\theirs.PcbDoc", "loaded": True, "modified": False}]


def test_a_dry_run_closes_nothing(close_opened):
    out, bridge, left = close_opened(["C:\\w\\a.PcbDoc"], _OPEN)
    assert out["would_close"] == ["C:\\w\\a.PcbDoc"] and not bridge.closed
    assert left == ["C:\\w\\a.PcbDoc"]


def test_only_what_the_session_opened_is_closed_and_modified_or_kept_ones_stay(close_opened):
    opened = ["C:\\w\\a.PcbDoc", "C:\\w\\b.SchDoc", "C:\\w\\mine.PcbDoc", "C:\\w\\gone.PcbDoc"]
    out, bridge, left = close_opened(opened, _OPEN, dry_run=False, keep=["c:\\w\\MINE.PcbDoc"])
    assert bridge.closed == ["C:\\w\\a.PcbDoc"]
    assert "C:\\person\\theirs.PcbDoc" not in bridge.closed
    assert out["kept_modified"] == ["C:\\w\\b.SchDoc"]
    assert out["kept"] == ["C:\\w\\mine.PcbDoc"]
    assert out["already_closed"] == ["C:\\w\\gone.PcbDoc"]
    assert left == ["C:\\w\\b.SchDoc", "C:\\w\\mine.PcbDoc"]


def test_a_document_the_close_finds_modified_is_kept_as_modified(close_opened, monkeypatch):
    async def refuse(self, command, params=None, *a, **k):
        if command == "application.get_open_documents":
            return self.docs
        raise RuntimeError("The document has unsaved changes, so it was not closed: x")
    monkeypatch.setattr(_Bridge, "send_command_async", refuse)
    out, _, left = close_opened(["C:\\w\\a.PcbDoc"], _OPEN, dry_run=False)
    assert out["kept_modified"] == ["C:\\w\\a.PcbDoc"] and not out["failed"]
    assert left == ["C:\\w\\a.PcbDoc"]
