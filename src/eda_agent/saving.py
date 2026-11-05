# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Saving switch on the bridge dashboard, as the Python side honours it.

The Altium script refuses its own saves while the switch is off. This covers
the few places the server writes a design file itself (the project file
cleanup after a removal, the simulation settings file, a checkpoint restore)
and the interface tools that could reach File > Save, a Save button or
Ctrl+S. The state lives in ``saving.json`` in the workspace, written by the
dashboard; no file, or one that does not say off, means saving is allowed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

SAVING_FILE = "saving.json"


def saving_off(workspace: Optional[Path] = None) -> bool:
    """True when the dashboard's Saving switch is off."""
    if workspace is None:
        from .config import get_config
        workspace = Path(get_config().workspace_dir)
    try:
        state = json.loads((Path(workspace) / SAVING_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and str(state.get("saving", "")).lower() == "off"


def refused(what: str) -> dict[str, Any]:
    """The reply for something that would write while saving is off."""
    return {"success": False, "error": "SAVING_OFF",
            "message": f"{what} Saving is switched off on the bridge dashboard, "
                       "so nothing was written. Turn the Saving switch back on "
                       "there to save."}


def _words(text: str) -> str:
    return re.sub(r"[&.]", "", text).strip().lower()


def menu_saves(menu_path: str) -> bool:
    """A menu path with a Save item anywhere in it (Save, Save All, Save As)."""
    return any("save" in _words(part) for part in menu_path.split("|"))


def button_saves(caption: str) -> bool:
    """A dialog button that saves. "Don't Save" is the opposite and passes."""
    words = _words(caption).replace("’", "'")
    return "save" in words and not re.search(r"\b(don't|do not|discard)\b", words)


def keys_save(sequence: str) -> bool:
    """A key sequence holding Ctrl+S, alone or with other modifiers."""
    for item in sequence.split():
        parts = [p.strip().lower() for p in item.split("+")]
        if "ctrl" in parts and "s" in parts:
            return True
    return False
