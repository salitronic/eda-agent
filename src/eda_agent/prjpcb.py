# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Finish a document removal Altium's project writer leaves half done.

When a project loses a document, Altium rewrites [Document1]..[DocumentN]
for the new count and leaves every section past N in the file. Reopening
the project reads them back, so a removed document returns (measured on
AD 26.11). Saving again does not remove them: Altium's writer merges into
the file already at the target path.

Saved to a path with no file, it writes only the documents the project
holds. So after a removal whose saved file still holds more than that,
project.rewrite_file has Altium save the project as a copy, check it,
delete the original and save the project back under its own name.

What the saved file holds is asked of Altium too: it loads a copy of the
file as a hidden project and the bridge reads the documents off that
(``file_documents``, full paths of the documents whose files exist; None
when Altium could not load the copy). Measured on AD 26.11.1.5: after the
last of A, C, D was removed and saved, the loaded copy held A, C, D; after
the rewrite it held A, C, and a reopen listed A and C.

Every removal leaves one section too many: the removed document when it
was the last, else a second copy of the last member. That copy loads as
the one member, so the loaded file cannot show it, and the file is
rewritten after every removal that was saved.
Altium does every write and every read; nothing here opens the file.
"""
from __future__ import annotations

import os
from pathlib import PureWindowsPath
from typing import Any, Optional


def _name(path: str) -> str:
    return PureWindowsPath(path).name.lower()


def leftovers(file_documents: list[str], members: list[str]) -> bool:
    """Whether the saved file holds anything the project no longer does.

    ``file_documents`` are the documents Altium loaded from the saved file
    and ``members`` the project's documents with a file on disk (a virtual
    one such as ActiveBOM.VirtualBOM has none). Compared by file name and
    count.
    """
    return sorted(_name(p) for p in file_documents) != sorted(_name(m) for m in members)


def _removal(reply: Any) -> bool:
    return (isinstance(reply, dict) and bool(reply.get("removed"))
            and bool(reply.get("project_path")) and isinstance(reply.get("members"), list))


async def finish_removals(bridge, replies: list) -> list:
    """finish_removal for several removals: one rewrite per project, after
    its last removal, and each reply told what the file then lists."""
    last: dict[str, int] = {}
    for i, reply in enumerate(replies):
        if _removal(reply):
            last[os.path.normcase(os.path.normpath(reply["project_path"]))] = i
    for key, i in last.items():
        listed = replies[i].get("file_documents")
        replies[i] = await finish_removal(bridge, replies[i])
        final = replies[i]
        after = (final.get("stale_sections") or {}).get("after", listed)
        for j, reply in enumerate(replies):
            if j == i or not _removal(reply) or os.path.normcase(
                    os.path.normpath(reply["project_path"])) != key:
                continue
            reply.pop("members", None)
            reply.pop("file_documents", None)
            if isinstance(after, list):
                still = _name(reply.get("document_path", "")) in {_name(p) for p in after}
                reply["in_project_file"] = still
                reply["success"] = not still and final.get("success") is not False
                if reply["success"]:
                    reply.pop("project_file_entry", None)
                    reply.pop("reason", None)
            reply["project_file_finished_by"] = final.get("document_path")
    return replies


def removals_together(command: str, params_of):
    """A list handler (``bulk_items_by``) for a removal tool: the removals
    one after another, then the project file rewritten once per project.

    ``params_of(args)`` gives one item's bridge parameters, or None for an
    item that must run on its own (then the whole list does).
    """
    async def handler(items: list, shared: dict) -> Optional[dict[str, Any]]:
        from .bridge import get_bridge
        from .tools.bulk import _failed, _item_result
        calls = [{**shared, **i} for i in items if isinstance(i, dict)]
        params = [params_of(c) for c in calls]
        if len(calls) != len(items) or any(p is None for p in params):
            return None
        bridge = get_bridge()
        replies: list = []
        for p in params:
            try:
                replies.append(await bridge.send_command_async(command, p))
            except Exception as exc:                      # noqa: BLE001
                replies.append({"success": False, "error": str(exc)})
        out = [_item_result(r) for r in await finish_removals(bridge, replies)]
        failed = sum(1 for r in out if _failed(r))
        return {"bulk": True, "count": len(items), "succeeded": len(items) - failed,
                "failed": failed, "items": out}
    return handler


async def finish_removal(bridge, reply: Any) -> Any:
    """Make a removal last: have Altium rewrite the project file when it
    lists more than the project holds, and report what it lists afterwards."""
    if not _removal(reply):
        return reply
    members = [m for m in reply.pop("members") if m and os.path.exists(m)]
    listed = reply.pop("file_documents", None)
    if not isinstance(listed, list) or reply.get("project_saved") is False:
        return reply
    from .saving import saving_off
    if saving_off():
        reply["stale_sections"] = {"rewritten": False, "file_documents": listed,
                                   "reason": ("saving is switched off on the bridge "
                                              "dashboard, so the project file was "
                                              "not rewritten")}
        return reply
    project_path = reply["project_path"]
    try:
        done = await bridge.send_command_async(
            "project.rewrite_file",
            {"project_path": project_path, "expected_count": str(len(members))}) or {}
    except Exception as exc:                              # noqa: BLE001
        done = {"rewritten": False, "reason": str(exc)}
    after = done.get("file_documents")
    if not isinstance(after, list):
        after = listed
    still = _name(reply.get("document_path", "")) in {_name(p) for p in after}
    clean = not leftovers(after, members)
    reply["stale_sections"] = {
        "rewritten": bool(done.get("rewritten")) and clean,
        "before": listed, "after": after,
        **({"copy_kept": done["copy_path"]} if done.get("copy_kept") else {}),
        **({"reason": done["reason"]} if done.get("reason") else {})}
    reply["in_project_file"] = still
    reply["success"] = not still and clean
    if reply["success"]:
        reply.pop("project_file_entry", None)
        reply["reason"] = ("Altium's save leaves a section past the last document in "
                           "the project file; Altium wrote it again without it.")
    return reply
