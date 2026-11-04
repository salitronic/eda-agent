# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Config tab on the Altium status panel, as the Python side reads it.

The user's choices live in ``preferences.json`` in the workspace, written
by the panel and kept across restarts. Only the user changes them; the
server reads them. A missing file, a missing key or a value this version
does not know means the default.

``value_format`` is how a component value is written when it is cleaned
up: ``none`` (left as written, the default), ``rkm`` (4K7, 2u2) or ``si``
(4.7k, 2.2u). ``value_unit`` is ``with`` to add the unit symbol under SI
(100nF, 10uH, 3.3V; never on resistors) or ``without`` (100n, the default).
The RKM code of IEC 60062 has no unit, so under RKM it is always
``without``.

``edits`` is ``readonly`` to refuse every command that is not a read
(default ``allowed``). ``deleting`` and ``closing`` are ``refused`` to turn
away a command that deletes or removes, or that closes a document or
project (default ``allowed``). ``ui_automation`` is ``off`` to refuse menus,
dialogs and keystrokes (default ``on``). ``keep_in_view`` is ``on`` to fit
the board or sheet in view after each edit (default ``off``). Each also
has an environment variable in the AI client's config; for the first two
the stricter of the two wins, for the third either one turns it on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

PREFERENCES_FILE = "preferences.json"

VALUE_FORMATS = ("none", "rkm", "si")
VALUE_UNITS = ("without", "with")

#: Each key, its allowed values; the first is the default.
CHOICES: dict[str, tuple[str, ...]] = {
    "value_format": VALUE_FORMATS,
    "value_unit": VALUE_UNITS,
    "edits": ("allowed", "readonly"),
    "deleting": ("allowed", "refused"),
    "closing": ("allowed", "refused"),
    "ui_automation": ("on", "off"),
    "keep_in_view": ("off", "on"),
}

DEFAULTS: dict[str, str] = {key: allowed[0] for key, allowed in CHOICES.items()}


def _read(workspace: Optional[Path]) -> dict[str, Any]:
    if workspace is None:
        from .config import get_config
        workspace = Path(get_config().workspace_dir)
    try:
        stored = json.loads((Path(workspace) / PREFERENCES_FILE)
                            .read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def preferences(workspace: Optional[Path] = None) -> dict[str, str]:
    """Every preference, each one the stored choice or its default."""
    stored = _read(workspace)
    out = dict(DEFAULTS)
    for key, allowed in CHOICES.items():
        value = str(stored.get(key, "")).lower()
        if value in allowed:
            out[key] = value
    if out["value_format"] != "si":
        out["value_unit"] = "without"
    return out


def value_format(workspace: Optional[Path] = None) -> str:
    """'none', 'rkm' or 'si', as chosen on the Config tab."""
    return preferences(workspace)["value_format"]


def value_unit(workspace: Optional[Path] = None) -> str:
    """'with' or 'without' the unit symbol, as chosen on the Config tab."""
    return preferences(workspace)["value_unit"]


def edits_read_only(workspace: Optional[Path] = None) -> bool:
    """True when edits are set to read-only on the Config tab."""
    return preferences(workspace)["edits"] == "readonly"


def deleting_refused(workspace: Optional[Path] = None) -> bool:
    """True when deleting is switched off on the Config tab."""
    return preferences(workspace)["deleting"] == "refused"


def closing_refused(workspace: Optional[Path] = None) -> bool:
    """True when closing documents is switched off on the Config tab."""
    return preferences(workspace)["closing"] == "refused"


def ui_automation_off(workspace: Optional[Path] = None) -> bool:
    """True when menus, dialogs and keys are switched off on the Config tab."""
    return preferences(workspace)["ui_automation"] == "off"


def keep_in_view(workspace: Optional[Path] = None) -> bool:
    """True when the board is to be kept in view after each edit."""
    return preferences(workspace)["keep_in_view"] == "on"
