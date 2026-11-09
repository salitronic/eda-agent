# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_rename_document: Altium renames, the project and the sheet
symbols follow, and no file is moved from outside Altium.

MEASURED on AD 26.11 (scratch project): DoSafeChangeFileNameAndSave on a
sheet returned false and wrote nothing, but had already renamed the open
document, so the project listed a file that did not exist. Pointing the
document at the new name and saving it wrote the file, and the project
entry followed in place. A sheet symbol's file name written without the
modify announcement stayed old in the compiled hierarchy even after a
forced recompile; with it, the hierarchy followed at once.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import project
from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Project.pas"


def _fns():
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))


def test_the_rename_is_routed():
    text = _PAS.read_text(encoding="utf-8")
    assert "'rename_document':   Result := Proj_RenameDocument(Params, RequestId);" in text


def test_altium_writes_the_new_file_and_a_failure_is_put_back():
    body = _fns()["Proj_RenameDocument"]
    first = body.index("ServerDoc.DoSafeChangeFileNameAndSave(NewPath, Kind)")
    point = body.index("ServerDoc.SetFileName(NewPath);", first)
    save = body.index("ServerDoc.DoFileSave('');", point)
    back = body.index("ServerDoc.SetFileName(OldPath);", save)
    assert first < point < save < back
    # Nothing here touches the file system directly.
    for call in ("RenameFile(", "DeleteFile(", "CopyFile(", "TFileStream"):
        assert call not in body


def test_refusals_come_before_any_write():
    body = _fns()["Proj_RenameDocument"]
    write = body.index("DoSafeChangeFileNameAndSave")
    for code in ("'FILE_EXISTS'", "'BAD_NAME'", "'NOT_A_MEMBER'"):
        assert body.index(code) < write, code


def test_the_project_entry_is_read_back_not_assumed():
    body = _fns()["Proj_RenameDocument"]
    assert "ListsNew := ProjectListsDocument(Project, NewPath);" in body
    assert "ListsOld := ProjectListsDocument(Project, OldPath);" in body
    assert "BoolToJsonStr(ListsNew And (Not ListsOld) And (Failed = ''))" in body


def test_sheet_symbol_edits_are_announced_and_read_back():
    body = _fns()["RenameSheetSymbolsIn"]
    begin = body.index("SchBeginModify(Sym);")
    write = body.index("Sym.SheetFileName.Text := NewRef;")
    end = body.index("SchEndModify(Sym);")
    readback = body.index("If Ref = NewRef Then")
    assert begin < write < end < readback


def test_the_tool_sends_what_it_is_given(monkeypatch):
    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(project, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    project.register_project_tools(Mcp())
    asyncio.run(tools["proj_rename_document"](
        document_path=r"C:\p\Child.SchDoc", new_name="Power"))
    assert sent == [("project.rename_document",
                     {"document_path": r"C:\p\Child.SchDoc", "new_name": "Power"})]


def test_a_pruned_copy_of_a_kept_document_is_named_as_one(tmp_path):
    from eda_agent.prjpcb import prune_stale_documents
    a = tmp_path / "A.SchDoc"
    b = tmp_path / "B.SchDoc"
    a.write_text("x")
    b.write_text("x")
    prj = tmp_path / "P.PrjPcb"
    prj.write_text(
        "[Design]\r\nVersion=1.0\r\n\r\n"
        f"[Document1]\r\nDocumentPath={a.name}\r\n\r\n"
        f"[Document2]\r\nDocumentPath={b.name}\r\n\r\n"
        f"[Document3]\r\nDocumentPath={b.name}\r\n\r\n",
        encoding="utf-8")
    out = prune_stale_documents(str(prj), [str(a), str(b)], str(tmp_path / "bk"))
    assert out["pruned"] == 1
    assert out["duplicates_of_kept"] == [b.name]
