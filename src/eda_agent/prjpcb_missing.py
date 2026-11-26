# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Remove a project entry whose file is missing: the one removal that
edits the project file itself.

Everything else about a project file is asked of Altium (``prjpcb``).
This case Altium cannot reach. Measured on
AD 23.3.1 with a project listing a.txt (there), b.txt and c.txt (deleted):
the open project's logical documents hold a.txt alone, and so does a
hidden load of the file; DM_IndexOfSourceDocument answers -1 for b.txt;
DM_RemoveSourceDocument with its full path or its bare name raises nothing
and removes nothing. Yet Altium holds the two: a document added afterwards
was written as [Document4], and the copy project.rewrite_file saves onto
no file lists them again. Putting a file back at the path and adding it
makes a second entry beside the first. So no call removes such an entry
from an open project, and every save writes it back.

What does remove it: the project closed, its [DocumentN] section taken out
of the file and the later ones renumbered, the project opened again. After
that a save writes only the real documents (measured the same day).
``remove_missing_entry`` does that, and only for a path with no file.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

_DOC_HEAD = re.compile(r"^\[Document(\d+)\]\s*$")
_ANY_HEAD = re.compile(r"^\[.+\]\s*$")
_UTF8_BOM = b"\xef\xbb\xbf"


def _read_project_text(project_path: str) -> tuple[str, bool, str]:
    """The project file's text, whether it starts with a BOM, its newline.
    Bytes that are not UTF-8 pass through unchanged."""
    raw = open(project_path, "rb").read()
    bom = raw.startswith(_UTF8_BOM)
    text = (raw[len(_UTF8_BOM):] if bom else raw).decode(
        "utf-8", errors="surrogateescape")
    return text, bom, "\r\n" if "\r\n" in text else "\n"


def _sections(lines: list[str]) -> list[list]:
    """``[header or None, body lines]`` in file order."""
    out: list[list] = []
    cur: list = [None, []]
    for line in lines:
        if _ANY_HEAD.match(line):
            out.append(cur)
            cur = [line, []]
        else:
            cur[1].append(line)
    out.append(cur)
    return out


def _entry_path(project_path: str, body: list[str]) -> str:
    rel = next((b.split("=", 1)[1].strip() for b in body
                if b.startswith("DocumentPath=")), "")
    if not rel:
        return ""
    return os.path.normpath(os.path.join(os.path.dirname(
        os.path.abspath(project_path)), rel))


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b))


def missing_entries(project_path: str) -> list[str]:
    """Full paths the project file lists as documents and that have no
    file, in file order. Empty when the file cannot be read."""
    try:
        text, _bom, nl = _read_project_text(project_path)
    except OSError:
        return []
    out = []
    for head, body in _sections(text.split(nl)):
        if head and _DOC_HEAD.match(head):
            full = _entry_path(project_path, body)
            if full and not os.path.isfile(full):
                out.append(full)
    return out


def drop_entry(project_path: str, document_path: str) -> int:
    """Take every [DocumentN] section naming ``document_path`` out of the
    project file and renumber the ones after it. Returns how many were
    taken out. Every other line is written back as it was."""
    text, bom, nl = _read_project_text(project_path)
    kept: list[str] = []
    n = dropped = 0
    for head, body in _sections(text.split(nl)):
        if head and _DOC_HEAD.match(head):
            if _same(_entry_path(project_path, body), document_path):
                dropped += 1
                continue
            n += 1
            head = f"[Document{n}]"
        if head is not None:
            kept.append(head)
        kept.extend(body)
    if dropped:
        with open(project_path, "wb") as fh:
            fh.write((_UTF8_BOM if bom else b"") + nl.join(kept).encode(
                "utf-8", errors="surrogateescape"))
    return dropped


async def _unsaved_members(bridge, project_path: str) -> Optional[list[str]]:
    """Open documents of the project that hold unsaved changes; None when
    that cannot be established."""
    try:
        docs = await bridge.send_command_async(
            "project.get_documents", {"project_path": project_path})
        opened = await bridge.send_command_async(
            "application.get_open_documents", {})
    except Exception:                                     # noqa: BLE001
        return None
    if isinstance(docs, dict):
        docs = docs.get("documents")
    if isinstance(opened, dict):
        opened = opened.get("documents")
    if not isinstance(docs, list) or not isinstance(opened, list):
        return None
    members = {os.path.normcase(str(d.get("file_path", "")))
               for d in docs if isinstance(d, dict)}
    members.add(os.path.normcase(project_path))
    return [str(d.get("file_path")) for d in opened if isinstance(d, dict)
            and os.path.normcase(str(d.get("file_path", ""))) in members
            and (d.get("unsaved") or d.get("modified"))]


def is_missing_entry(project_path: Optional[str], document_path: str) -> bool:
    """Whether ``document_path`` has no file and the project file lists it."""
    return bool(project_path and document_path
                and not os.path.exists(document_path)
                and os.path.isfile(project_path)
                and any(_same(m, document_path)
                        for m in missing_entries(project_path)))


async def remove_missing_entry(bridge, project_path: str,
                               document_path: str) -> dict[str, Any]:
    """Remove a project entry whose file is missing: close the project,
    take the entry out of the file, open the project again, save it, and
    read the file to see that Altium did not write the entry back."""
    out: dict[str, Any] = {
        "success": False, "removed": False, "missing_file": True,
        "document_path": document_path, "project_path": project_path}
    from .saving import saving_off
    if saving_off():
        out["reason"] = ("saving is switched off on the bridge dashboard, and "
                         "this entry can only be removed by rewriting the "
                         "project file")
        return out
    unsaved = await _unsaved_members(bridge, project_path)
    if unsaved is None or unsaved:
        out["unsaved"] = unsaved
        out["reason"] = (
            "the file of this entry is missing, so Altium holds no document "
            "for it that a call can remove; it comes out only with the "
            "project closed. "
            + ("These documents of the project hold unsaved changes; save or "
               "discard them, then call again: " + ", ".join(unsaved)
               if unsaved else
               "Whether the project's documents hold unsaved changes could "
               "not be read, so it was not closed"))
        return out
    try:
        closed = await bridge.send_command_async(
            "project.close", {"project_path": project_path, "save": True}) or {}
    except Exception as exc:                              # noqa: BLE001
        out["reason"] = f"the project could not be closed ({exc}); nothing was changed"
        return out
    if closed.get("closed") is False:
        out["reason"] = "Altium did not close the project; nothing was changed"
        return out
    out["dropped_sections"] = drop_entry(project_path, document_path)
    try:
        await bridge.send_command_async("project.open", {"project_path": project_path})
        await bridge.send_command_async("project.save", {"project_path": project_path})
    except Exception as exc:                              # noqa: BLE001
        out["reason"] = (f"the entry was taken out of the project file, but "
                         f"the project did not open and save again ({exc}); "
                         f"open it with proj_open")
        out["removed"] = True
        return out
    still = any(_same(m, document_path) for m in missing_entries(project_path))
    out.update(
        success=not still, removed=not still, in_project_file=still,
        project_reopened=True,
        reason=("Altium wrote the entry back when the project was saved"
                if still else
                "the file of this entry is missing, so Altium held no "
                "document a call could remove: the project was closed, the "
                "entry taken out of the project file, and the project opened "
                "and saved again. Its document windows closed with it; open "
                "the ones you need"))
    return out
