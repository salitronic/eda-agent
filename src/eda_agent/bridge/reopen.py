# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Save, close and reopen one open document, so Altium builds from the file
what it only builds when a document loads."""

from __future__ import annotations

import os
from typing import Any

from .exceptions import AltiumError


async def save_close_reopen(bridge, path: str, save: bool = True) -> dict[str, Any]:
    """``file_path``, ``saved``, ``closed`` and ``reopened``, with ``ok``;
    ``ok`` false carries ``stage`` (save, close or reopen) and ``reason`` at
    the first step that did not take. The document stays open when it fails
    before the close."""
    out: dict[str, Any] = {"file_path": path, "saved": False}
    if save:
        saved = await bridge.send_command_async(
            "application.save_document", {"file_path": path}, timeout=60.0)
        if not (isinstance(saved, dict) and saved.get("saved")):
            return {"ok": False, "stage": "save", **out,
                    "reason": (saved or {}).get("reason", "the save did not reach "
                               "the file") if isinstance(saved, dict) else str(saved)}
        out["saved"] = True
    try:
        closed = await bridge.send_command_async(
            "application.close_document", {"file_path": path}, timeout=120.0)
    except AltiumError as exc:
        return {"ok": False, "stage": "close", **out, "reason": str(exc)}
    out["closed"] = bool(isinstance(closed, dict) and closed.get("closed"))
    if not out["closed"]:
        return {"ok": False, "stage": "close", **out, "close": closed}
    if not os.path.isfile(path):
        return {"ok": False, "stage": "reopen", **out,
                "reason": "the document is not on disk to reopen"}
    await bridge.send_command_async("application.run_process", {
        "process_name": "WorkspaceManager:OpenObject",
        "parameters": f"ObjectKind=Document|FileName={path}"})
    try:
        await bridge.send_command_async(
            "application.set_active_document", {"file_path": path})
    except AltiumError as exc:
        return {"ok": False, "stage": "reopen", **out, "reason": str(exc)}
    out["reopened"] = True
    return {"ok": True, **out}
