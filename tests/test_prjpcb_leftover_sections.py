# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A removal Altium's save leaves in the .PrjPcb is finished by Altium
writing the project file fresh, with no dialog.

Altium rewrites [Document1]..[DocumentN] for a project's new document
count and leaves the sections past N, because its writer merges into the
file already at the path. project.rewrite_file saves the project as a
copy, checks it, deletes the original and saves the project back onto no
file. These hold the flow to: every saved removal is rewritten (the
section left is a second copy of a member when the removed document was
not the last, which loads as that one member), nothing runs when the
removal was not saved, the original is deleted only after the copy lists the expected
count, the copy is deleted only once the original is right again, and the
result comes from Altium, never from the file.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent import prjpcb
from tests.pascal_source import functions, strip_comments

PRJ = r"C:\p\P.PrjPcb"
_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Project.pas"


def _members(tmp_path: Path, *names: str) -> list[str]:
    out = []
    for n in names:
        (tmp_path / n).write_bytes(b"x")
        out.append(str(tmp_path / n))
    return out


class _Bridge:
    def __init__(self, after=None, rewritten=True, **extra):
        self.calls, self.after, self.rewritten, self.extra = [], after, rewritten, extra

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if command == "project.rewrite_file":
            return {"rewritten": self.rewritten, "file_documents": self.after,
                    **self.extra}
        return {"success": True}


def _reply(tmp_path, members, listed, doc="Gone.SchDoc", in_file=True):
    return {"success": not in_file, "removed": True, "in_project_file": in_file,
            "project_path": PRJ, "document_path": str(tmp_path / doc),
            "members": members, "file_documents": listed}


def test_the_file_lists_more_than_the_project():
    assert prjpcb.leftovers(["A.SchDoc", "C.SchDoc", "C.SchDoc", "D.SchDoc"],
                            [r"C:\p\A.SchDoc", r"C:\p\C.SchDoc"])
    assert prjpcb.leftovers(["A.SchDoc", "C.SchDoc", "C.SchDoc"],
                            [r"C:\p\A.SchDoc", r"C:\p\C.SchDoc"])
    assert not prjpcb.leftovers(["A.SchDoc", "C.SchDoc"],
                                [r"C:\p\a.schdoc", r"C:\p\C.SchDoc"])


def test_a_copy_of_a_member_left_past_the_end_is_rewritten_too(tmp_path):
    # Loaded, the file lists exactly the members: the second copy of the
    # last one does not show, but it is in the file.
    members = _members(tmp_path, "A.SchDoc")
    bridge = _Bridge(after=[str(tmp_path / "A.SchDoc")])
    out = asyncio.run(prjpcb.finish_removal(
        bridge, _reply(tmp_path, members, ["A.SchDoc"], in_file=False)))
    assert [c[0] for c in bridge.calls] == ["project.rewrite_file"]
    assert out["success"] is True
    assert "members" not in out and "file_documents" not in out


def test_a_removal_that_was_not_saved_runs_nothing(tmp_path):
    members = _members(tmp_path, "A.SchDoc")
    bridge = _Bridge()
    reply = {**_reply(tmp_path, members, ["A.SchDoc"], in_file=False),
             "project_saved": False}
    asyncio.run(prjpcb.finish_removal(bridge, reply))
    assert bridge.calls == []


def test_leftovers_are_rewritten_by_altium(tmp_path):
    members = _members(tmp_path, "A.SchDoc", "C.SchDoc")
    bridge = _Bridge(after=["A.SchDoc", "C.SchDoc"])
    out = asyncio.run(prjpcb.finish_removal(bridge, _reply(
        tmp_path, members, ["A.SchDoc", "C.SchDoc", "C.SchDoc", "Gone.SchDoc"])))
    assert bridge.calls == [("project.rewrite_file",
                             {"project_path": PRJ, "expected_count": "2"})]
    assert out["success"] is True and out["in_project_file"] is False
    assert out["stale_sections"]["rewritten"] is True
    assert out["stale_sections"]["after"] == ["A.SchDoc", "C.SchDoc"]


def test_a_rewrite_that_could_not_finish_says_so_and_names_the_copy(tmp_path):
    members = _members(tmp_path, "A.SchDoc")
    bridge = _Bridge(after=["A.SchDoc", "Gone.SchDoc"], rewritten=False,
                     copy_kept=True, copy_path=r"C:\p\P.eda-rewrite.PrjPcb",
                     reason="the project file lists 2 documents, not 1")
    out = asyncio.run(prjpcb.finish_removal(bridge, _reply(
        tmp_path, members, ["A.SchDoc", "Gone.SchDoc"])))
    assert out["success"] is False and out["in_project_file"] is True
    assert out["stale_sections"]["rewritten"] is False
    assert out["stale_sections"]["copy_kept"].endswith("P.eda-rewrite.PrjPcb")
    assert "not 1" in out["stale_sections"]["reason"]


def test_a_refused_rewrite_keeps_the_removal_failed(tmp_path):
    class Refusing(_Bridge):
        async def send_command_async(self, command, params=None, timeout=None):
            self.calls.append((command, dict(params or {})))
            raise RuntimeError("COPY_EXISTS")
    members = _members(tmp_path, "A.SchDoc")
    out = asyncio.run(prjpcb.finish_removal(Refusing(), _reply(
        tmp_path, members, ["A.SchDoc", "Gone.SchDoc"])))
    assert out["success"] is False
    assert out["stale_sections"]["after"] == ["A.SchDoc", "Gone.SchDoc"]
    assert "COPY_EXISTS" in out["stale_sections"]["reason"]


def test_nothing_is_rewritten_while_saving_is_off(tmp_path, monkeypatch):
    from eda_agent import saving
    monkeypatch.setattr(saving, "saving_off", lambda workspace=None: True)
    members = _members(tmp_path, "A.SchDoc")
    bridge = _Bridge()
    out = asyncio.run(prjpcb.finish_removal(bridge, _reply(
        tmp_path, members, ["A.SchDoc", "Gone.SchDoc"])))
    assert bridge.calls == []
    assert "switched off" in out["stale_sections"]["reason"]


def test_a_reply_without_members_is_passed_through():
    bridge = _Bridge()
    reply = {"success": False, "error": "NOT_A_MEMBER"}
    assert asyncio.run(prjpcb.finish_removal(bridge, dict(reply))) == reply
    assert bridge.calls == []


def test_a_list_rewrites_each_project_once(tmp_path):
    members = _members(tmp_path, "A.SchDoc")
    bridge = _Bridge(after=["A.SchDoc"])
    first = _reply(tmp_path, members + [str(tmp_path / "D.SchDoc")],
                   ["A.SchDoc", "C.SchDoc", "D.SchDoc"], doc="C.SchDoc")
    second = _reply(tmp_path, members, ["A.SchDoc", "D.SchDoc", "D.SchDoc"],
                    doc="D.SchDoc")
    out = asyncio.run(prjpcb.finish_removals(bridge, [first, second]))
    assert [c for c, _ in bridge.calls] == ["project.rewrite_file"]
    assert [o["success"] for o in out] == [True, True]
    assert [o["in_project_file"] for o in out] == [False, False]
    assert "members" not in out[0] and "file_documents" not in out[0]


def test_the_module_never_opens_the_file_or_drives_the_interface():
    src = Path(prjpcb.__file__).read_text(encoding="utf-8")
    for call in ("open(", "read_bytes", "read_text", "write_bytes", "write_text",
                 "shutil", "replace_with_retry", "unlink", "os.remove",
                 ".ui", "run_process", "uiauto"):
        assert call not in src, call


# The Altium side.

def _rewrite() -> str:
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))["Proj_RewriteFile"]


def test_the_rewrite_is_routed():
    text = _PAS.read_text(encoding="utf-8")
    assert "'rewrite_file':      Result := Proj_RewriteFile(Params, RequestId);" in text
    assert "'file_documents':    Result := Proj_FileDocuments(Params, RequestId);" in text


def test_refusals_come_before_any_write():
    body = _rewrite()
    write = body.index("DoSafeChangeFileNameAndSave")
    for code in ("'PROJECT_NOT_FOUND'", "'SAVING_OFF'", "'PROJECT_NOT_OPEN'",
                 "'BAD_COUNT'", "'COPY_EXISTS'"):
        assert body.index(code) < write, code
    assert body.index("DeleteFile(") > write


def test_the_original_goes_only_after_the_copy_is_checked():
    body = _rewrite()
    copy = body.index("ProjDoc.DoSafeChangeFileNameAndSave(Via, Kind)")
    counted = body.index("ViaList := ProjectFileDocumentList(Via, ViaLoaded);", copy)
    unloaded = body.index("Else If Not ViaLoaded Then", counted)
    checked = body.index("ViaCount <> Expected", unloaded)
    gone = body.index("DeleteFile(ProjPath);", checked)
    back = body.index("ProjDoc.DoSafeChangeFileNameAndSave(ProjPath, Kind)", gone)
    recount = body.index("List := ProjectFileDocumentList(ProjPath, Loaded);", back)
    copy_gone = body.index("DeleteFile(Via)", recount)
    assert copy < counted < unloaded < checked < gone < back < recount < copy_gone
    assert body.count("DeleteFile(ProjPath)") == 1
    assert body.count("DeleteFile(Via)") == 1


def test_altium_loads_the_project_file_and_nothing_opens_it():
    """What a saved project file holds is asked of Altium: it loads a copy
    as a hidden project, and the copy and what Altium writes beside it go."""
    fns = functions(strip_comments(_PAS.read_text(encoding="utf-8")))
    body = fns["ProjectFileDocumentList"]
    copied = body.index("CopyFile(ProjPath, CopyPath, False)")
    loaded = body.index("Workspace.DM_LoadProjectHidden(CopyPath)", copied)
    closed = body.index("Workspace.DM_CloseProject(CopyPath)", loaded)
    gone = body.index("DeleteFile(CopyPath);", closed)
    assert copied < loaded < closed < gone
    assert "If (Not HadSide) And FileExists(SidePath) Then DeleteFile(SidePath);" in body
    assert "If (DocPath <> '') And FileExists(DocPath) Then" in body
    for name in ("ProjectFileDocumentList", "ProjectFileDocumentsJson", "Proj_FileDocuments",
                 "Proj_RewriteFile", "Proj_RemoveDocument"):
        assert "LoadTextFile(" not in fns[name], name
    assert "ProjectFileEntryFor" not in fns


def test_a_file_altium_could_not_load_is_not_an_empty_one():
    fns = functions(strip_comments(_PAS.read_text(encoding="utf-8")))
    assert "Json := 'null';" in fns["ProjectListJson"]
    removal = fns["Proj_RemoveDocument"]
    assert removal.index("If Not FileLoaded Then") < removal.index("Else If Entry <> '' Then")
    assert '"project_file_checked":false' in removal


def test_the_copy_stays_when_the_original_is_not_right():
    body = _rewrite()
    guard = body[body.index("If FileExists(Via) Then"):]
    assert guard.index("(Count = Expected)") < guard.index("DeleteFile(Via)")
    assert "CopyKept := True;" in guard
