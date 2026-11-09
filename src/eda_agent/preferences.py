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
the board or sheet in view after each edit (default ``off``).
``dialogs`` is ``manual`` to leave the dialogs tools open (the change order,
the Teardrops options) for the user (default ``auto``: the tool answers
them). ``checks_off`` names the checks switched off, comma separated
(see :data:`CHECKS`; default none: every check runs). Each of the others
also has an environment variable in the AI client's config; for the first two
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
    "dialogs": ("auto", "manual"),
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


def dialogs_auto(workspace: Optional[Path] = None) -> bool:
    """True when a tool answers the dialog it opens (the change order, the
    Teardrops options) itself."""
    return preferences(workspace)["dialogs"] == "auto"


#: The checks the Config tab can switch off, and what each one checks. Every
#: check is on unless the user turns it off there.
CHECKS: dict[str, str] = {
    "footprint_drawing": "the footprint against its datasheet drawing",
    "symbol_pins": "the symbol's pins against the datasheet pin table",
    "pins_pads": "the symbol's pin numbers against the footprint's pads",
    "model_body": "the 3D body against the datasheet outline",
    "spice_model": "the SPICE model file",
    "fab_export": "the board and parts against the fab before export",
}


def checks_off(workspace: Optional[Path] = None) -> set[str]:
    """The checks switched off on the Config tab (names from CHECKS)."""
    raw = str(_read(workspace).get("checks_off", "") or "").lower()
    return {n.strip() for n in raw.split(",") if n.strip() in CHECKS}


def check_on(name: str, workspace: Optional[Path] = None) -> bool:
    """True unless check ``name`` is switched off on the Config tab."""
    if name not in CHECKS:
        raise KeyError(f"no check named {name!r}")
    return name not in checks_off(workspace)


#: Each check's row on the Config tab, as the panel labels it.
CHECK_LABELS: dict[str, str] = {
    "footprint_drawing": "Footprint vs drawing",
    "symbol_pins": "Symbol vs pin table",
    "pins_pads": "Symbol pins vs pads",
    "model_body": "3D body vs drawing",
    "spice_model": "SPICE model file",
    "fab_export": "Fab export",
}

#: When a user may rightly want a part that does not pass the check.
CHECK_REASONS: dict[str, str] = {
    "footprint_drawing": ("lands deliberately changed from the vendor's "
                          "pattern (longer for hand soldering, a house rule)"),
    "symbol_pins": ("a pin table that is a picture, or names deliberately "
                    "written another way"),
    "pins_pads": ("a footprint held in an installed library or vault, or "
                  "named pads deliberately left off the symbol"),
    "model_body": ("a deliberately simplified body, or a part with no "
                   "outline drawing"),
    "spice_model": ("the user's own model (measured, or written in-house) "
                    "rather than a vendor's"),
    "fab_export": ("a limit the fab agreed for this order (a fab with no "
                   "data here takes fab \"custom\" with the user's own limits "
                   "instead)"),
}


def check_skipped(name: str) -> dict[str, str]:
    """What a result carries when check ``name`` was switched off, so a part
    built without it is never taken for a checked one."""
    return {"check": name, "skipped": (
        f"not checked: {CHECKS[name]} is switched off on the Config tab")}


def if_the_user_insists(name: str) -> str:
    """What a refusal by check ``name`` tells the agent to do when the
    user wants to go ahead anyway."""
    return (f"Show the user what differs. If they still want it built as it "
            f"is, ask them to switch \"{CHECK_LABELS[name]}\" off on the Config "
            f"tab of the Altium panel, then build it again and switch it back "
            f"on after. Never work around the check another way: not another "
            f"tool, not an edit after the build. See docs/CONFIG.md.")


def checks_guide() -> str:
    """Every check, what it checks, when a user may want to go ahead, and
    the switch: the text tool_guide and docs/CONFIG.md give."""
    rows = "; ".join(
        f"\"{CHECK_LABELS[n]}\" checks {CHECKS[n]} (a user may want past it "
        f"for {CHECK_REASONS[n]})" for n in CHECKS)
    return ("A refused build is the check working: report what differs and "
            "let the user decide. Only the user switches a check off, on the "
            "Config tab of the Altium panel; it takes effect on the next "
            "command, and every result built with it off carries "
            "\"unchecked\". Never pass a check by another route (a different "
            "tool, pads or pins placed one by one, an edit after the build). "
            f"The checks: {rows}.")
