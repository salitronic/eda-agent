# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Remove the [DocumentN] sections Altium leaves behind in a .PrjPcb.

When a project loses a document, Altium rewrites [Document1]..[DocumentN]
for the new count and leaves every section past N in the file. Reopening
the project reads them back, so a removed document returns (issue #94;
measured on AD 26.11: five sections on disk for one member). Saving
again does not remove them and the scripting API has no call that does.

This deletes only those trailing sections, and only when the sections
that remain name exactly the documents Altium holds in memory, so the
file ends up saying what Altium already believes. The original is copied
to a backup first, the new text is written to a temporary file and moved
into place, and the bytes outside the removed sections are kept as they
were: encoding, byte-order mark and line endings included.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from .atomicfile import discard, replace_with_retry

_HEADER = re.compile(r"^\[(?P<name>[^\]\r\n]+)\]\s*$")
_DOCUMENT = re.compile(r"^Document(?P<n>\d+)$", re.IGNORECASE)
_BOM = b"\xef\xbb\xbf"


def _sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """(name, first line, end line exclusive) for every [section]."""
    heads = [(i, m.group("name")) for i, line in enumerate(lines)
             if (m := _HEADER.match(line))]
    out = []
    for k, (start, name) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        out.append((name, start, end))
    return out


def _document_path(lines: list[str], start: int, end: int) -> str:
    for line in lines[start + 1:end]:
        if line.lower().startswith("documentpath="):
            return line.split("=", 1)[1].strip()
    return ""


def _name(path: str) -> str:
    return PureWindowsPath(path).name.lower()


def lists_document(project_path: str, document_path: str) -> bool:
    """Whether any [DocumentN] section of the file names that document."""
    raw = Path(project_path).read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines(keepends=True)
    want = _name(document_path)
    return any(_DOCUMENT.match(name) and _name(_document_path(lines, s, e)) == want
               for name, s, e in _sections(lines))


def finish_removal(reply: Any, backup_dir: str) -> Any:
    """Complete a removal Altium could not write to disk.

    A remove reply carries the project path and the members Altium holds.
    Leftover sections are taken out whether or not they name the removed
    document: measured, removing from the middle of the list left shifted
    copies of the LAST member, three of them after three removals. The
    file is read again and the reply says what it now holds.
    """
    if not (isinstance(reply, dict) and reply.get("removed")
            and reply.get("project_path") and isinstance(reply.get("members"), list)):
        return reply
    from .saving import saving_off
    if saving_off():
        reply.pop("members", None)
        reply["stale_sections"] = {"pruned": 0, "reason": (
            "saving is switched off on the bridge dashboard, so the project "
            "file was not edited")}
        return reply
    try:
        pruned = prune_stale_documents(reply["project_path"], reply["members"],
                                       backup_dir)
        still = lists_document(reply["project_path"], reply.get("document_path", ""))
    except OSError as exc:
        reply["stale_sections"] = {"pruned": 0, "reason": f"could not edit the file: {exc}"}
        return reply
    reply.pop("members", None)
    if pruned.get("pruned") or pruned.get("reason"):
        reply["stale_sections"] = pruned
    was_listed = reply.get("in_project_file")
    reply["in_project_file"] = still
    if not still:
        reply["success"] = True
        reply.pop("project_file_entry", None)
        if was_listed:
            reply["reason"] = (
                "Altium left the removed entry in the project file; the leftover "
                "[DocumentN] sections were taken out (backup in stale_sections).")
    return reply


def prune_stale_documents(project_path: str, members: Iterable[str],
                          backup_dir: str) -> dict[str, Any]:
    """Drop [DocumentN] sections past the project's real document count.

    ``members`` are the project's documents as Altium holds them (full
    paths). Virtual ones with no file on disk (ActiveBOM.VirtualBOM) have
    no section and are not counted.
    """
    path = Path(project_path)
    real = [m for m in members if m and os.path.exists(m)]
    keep = len(real)
    raw = path.read_bytes()
    bom = raw.startswith(_BOM)
    try:
        text = raw[len(_BOM) if bom else 0:].decode("utf-8")
    except UnicodeDecodeError:
        return {"pruned": 0, "reason": "the project file is not UTF-8; left as it is"}
    lines = text.splitlines(keepends=True)
    sections = _sections(lines)

    docs = []
    for name, start, end in sections:
        m = _DOCUMENT.match(name)
        if m:
            docs.append((int(m.group("n")), start, end,
                         _document_path(lines, start, end)))
    stale = [d for d in docs if d[0] > keep]
    if not stale:
        return {"pruned": 0, "kept": keep}

    # THE SAFETY CHECK. The sections that stay must name exactly the
    # documents Altium holds; anything else means this file is not in the
    # state this function understands, and it is left alone.
    kept = sorted(_name(d[3]) for d in docs if d[0] <= keep)
    want = sorted(_name(m) for m in real)
    if kept != want or len(kept) != keep:
        return {"pruned": 0, "kept": keep, "reason": (
            "the remaining [DocumentN] sections do not match the project's "
            "documents, so the file was left as it is"),
            "file_documents": kept, "project_documents": want}

    drop = set()
    for _, start, end, _ in stale:
        drop.update(range(start, end))
    new_text = "".join(line for i, line in enumerate(lines) if i not in drop)

    backup_root = Path(backup_dir)
    backup_root.mkdir(parents=True, exist_ok=True)
    # Two removals in one second must not share a backup: the second would
    # overwrite the copy taken before the first edit.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"{path.stem}.{stamp}{path.suffix}"
    n = 1
    while backup.exists():
        n += 1
        backup = backup_root / f"{path.stem}.{stamp}-{n}{path.suffix}"
    shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".eda-tmp")
    tmp.write_bytes((_BOM if bom else b"") + new_text.encode("utf-8"))
    try:
        replace_with_retry(tmp, path)
    except OSError:
        discard(tmp)
        raise
    return {"pruned": len(stale),
            "removed_sections": [f"Document{d[0]}" for d in stale],
            "removed_paths": [d[3] for d in stale],
            "kept": keep, "backup": str(backup)}
