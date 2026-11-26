# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_remove_document removes a project entry whose file is missing.

A project listed todo.txt and revisions.Txt, both deleted. The removal
refused them ("That document is not in this project"): Altium's logical
documents never hold a missing file. Yet every save wrote the two entries
back, the rewrite onto no file included. Measured on AD 23.3.1: no call
reaches such an entry in an open project; it comes out with the project
closed, the section taken out of the file, and the project opened again.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent import prjpcb_missing as prjpcb
from eda_agent.bridge.exceptions import AltiumError


def _project(tmp_path: Path, *names: str, bom: bool = False) -> Path:
    body = "[Design]\r\nVersion=1.0\r\n\r\n"
    for i, n in enumerate(names, 1):
        body += (f"[Document{i}]\r\nDocumentPath={n}\r\nAnnotationEnabled=1\r\n"
                 f"DocumentUniqueId=\r\n\r\n")
    body += "[Configuration1]\r\nName=Sources\r\n\r\n"
    prj = tmp_path / "P.PrjPcb"
    prj.write_bytes((b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8"))
    return prj


def _paths(prj: Path) -> list[str]:
    return [line.split("=", 1)[1] for line in prj.read_text(
        encoding="utf-8-sig").splitlines() if line.startswith("DocumentPath=")]


class _Bridge:
    """Altium as measured: a document that is not a member is refused, and
    (writes_back) a save can put an entry back."""

    def __init__(self, prj: Path, unsaved=(), writes_back=False):
        self.prj, self.unsaved, self.writes_back = prj, list(unsaved), writes_back
        self.sent: list[str] = []

    async def send_command_async(self, command, params=None, **kw):
        self.sent.append(command)
        if command == "project.remove_document":
            raise AltiumError("That document is not in this project",
                              code="NOT_A_MEMBER")
        if command == "project.get_documents":
            return {"documents": [{"file_path": str(self.prj.parent / "a.txt")}]}
        if command == "application.get_open_documents":
            return [{"file_path": p, "modified": True} for p in self.unsaved]
        if command == "project.close":
            return {"closed": True}
        if command == "project.open":
            return {"success": True}
        if command == "project.save":
            raw = self.prj.read_bytes()
            if self.writes_back and b"todo.txt" not in raw:
                self.prj.write_bytes(raw.replace(
                    b"[Configuration1]",
                    b"[Document9]\r\nDocumentPath=todo.txt\r\n\r\n[Configuration1]"))
            return {"success": True}
        raise AssertionError(command)


def test_the_entries_without_a_file_are_found(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt", "revisions.Txt")
    assert prjpcb.missing_entries(str(prj)) == [
        str(tmp_path / "todo.txt"), str(tmp_path / "revisions.Txt")]
    assert prjpcb.is_missing_entry(str(prj), str(tmp_path / "TODO.TXT"))
    assert not prjpcb.is_missing_entry(str(prj), str(tmp_path / "a.txt"))
    assert not prjpcb.is_missing_entry(str(prj), str(tmp_path / "other.txt"))
    assert not prjpcb.is_missing_entry(None, str(tmp_path / "todo.txt"))


@pytest.mark.parametrize("bom", [False, True])
def test_one_section_goes_and_the_rest_is_renumbered_and_kept(tmp_path, bom):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "k.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt", "k.txt", bom=bom)
    before = prj.read_bytes()
    assert prjpcb.drop_entry(str(prj), str(tmp_path / "todo.txt")) == 1
    after = prj.read_bytes()
    assert after.startswith(b"\xef\xbb\xbf") is bom
    text = after.decode("utf-8-sig")
    assert _paths(prj) == ["a.txt", "k.txt"]
    assert "[Document1]\r\nDocumentPath=a.txt" in text
    assert "[Document2]\r\nDocumentPath=k.txt" in text
    assert "[Document3]" not in text
    assert "[Design]\r\nVersion=1.0" in text
    assert "[Configuration1]\r\nName=Sources" in text
    assert len(before) - len(after) == len(
        "[Document2]\r\nDocumentPath=todo.txt\r\nAnnotationEnabled=1\r\n"
        "DocumentUniqueId=\r\n\r\n")


def test_a_second_entry_of_the_same_path_goes_too(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt", "todo.txt")
    assert prjpcb.drop_entry(str(prj), str(tmp_path / "todo.txt")) == 2
    assert _paths(prj) == ["a.txt"]


def test_a_path_the_file_does_not_list_changes_nothing(tmp_path):
    prj = _project(tmp_path, "a.txt")
    before = prj.read_bytes()
    assert prjpcb.drop_entry(str(prj), str(tmp_path / "todo.txt")) == 0
    assert prj.read_bytes() == before


def test_the_project_is_closed_edited_opened_and_saved(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt", "revisions.Txt")
    bridge = _Bridge(prj)
    out = asyncio.run(prjpcb.remove_missing_entry(
        bridge, str(prj), str(tmp_path / "todo.txt")))
    assert out["success"] and out["removed"] and out["missing_file"]
    assert out["project_reopened"] and out["in_project_file"] is False
    assert bridge.sent[-3:] == ["project.close", "project.open", "project.save"]
    assert _paths(prj) == ["a.txt", "revisions.Txt"]
    assert "windows closed" in out["reason"]


def test_an_unsaved_document_keeps_the_project_open(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt")
    before = prj.read_bytes()
    bridge = _Bridge(prj, unsaved=[str(tmp_path / "a.txt")])
    out = asyncio.run(prjpcb.remove_missing_entry(
        bridge, str(prj), str(tmp_path / "todo.txt")))
    assert not out["success"] and out["unsaved"] == [str(tmp_path / "a.txt")]
    assert "project.close" not in bridge.sent and prj.read_bytes() == before


def test_an_entry_altium_writes_back_is_not_called_removed(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt")
    out = asyncio.run(prjpcb.remove_missing_entry(
        _Bridge(prj, writes_back=True), str(prj), str(tmp_path / "todo.txt")))
    assert not out["success"] and out["in_project_file"] is True
    assert "wrote the entry back" in out["reason"]


def test_saving_switched_off_changes_nothing(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt")
    before = prj.read_bytes()
    monkeypatch.setattr("eda_agent.saving.saving_off", lambda *a, **k: True)
    bridge = _Bridge(prj)
    out = asyncio.run(prjpcb.remove_missing_entry(
        bridge, str(prj), str(tmp_path / "todo.txt")))
    assert not out["success"] and "switched off" in out["reason"]
    assert bridge.sent == [] and prj.read_bytes() == before


def _tool(monkeypatch, bridge):
    from eda_agent.tools import project
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(project, "get_bridge", lambda: bridge)
    monkeypatch.setattr("eda_agent.bridge.get_bridge", lambda: bridge)
    project.register_project_tools(_Mcp())
    return tools["proj_remove_document"]


def test_the_tool_takes_that_route_for_a_missing_file_only(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt")
    bridge = _Bridge(prj)
    remove = _tool(monkeypatch, bridge)
    out = asyncio.run(remove(document_path=str(tmp_path / "todo.txt"),
                             project_path=str(prj)))
    assert out["success"] and out["missing_file"] and _paths(prj) == ["a.txt"]
    # A document that was never in the project still fails as it did.
    with pytest.raises(AltiumError):
        asyncio.run(remove(document_path=str(tmp_path / "never.txt"),
                           project_path=str(prj)))
    # And so does one whose file exists.
    with pytest.raises(AltiumError):
        asyncio.run(remove(document_path=str(tmp_path / "a.txt"),
                           project_path=str(prj)))


def test_unsaved_removal_of_a_missing_file_is_refused(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt")
    bridge = _Bridge(prj)
    out = asyncio.run(_tool(monkeypatch, bridge)(
        document_path=str(tmp_path / "todo.txt"), project_path=str(prj),
        save_project=False))
    assert not out["success"] and "save_project=False" in out["reason"]
    assert "project.close" not in bridge.sent


def test_a_list_removes_each_missing_entry(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    prj = _project(tmp_path, "a.txt", "todo.txt", "revisions.Txt")
    bridge = _Bridge(prj)
    from eda_agent.prjpcb import removals_together
    from eda_agent.tools.project import _removal_params
    monkeypatch.setattr("eda_agent.bridge.get_bridge", lambda: bridge)
    handler = removals_together("project.remove_document", _removal_params)
    out = asyncio.run(handler([
        {"document_path": str(tmp_path / "todo.txt")},
        {"document_path": str(tmp_path / "revisions.Txt")}],
        {"project_path": str(prj)}))
    assert out["succeeded"] == 2 and out["failed"] == 0, out
    assert _paths(prj) == ["a.txt"]
