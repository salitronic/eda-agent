# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The process names an Altium install declares, to catch a guessed one.

RunProcess with a name Altium does not know does nothing and raises
nothing. An agent ran PCB:Switch3DView (the command is PCB:SwitchTo3D),
saw no change and concluded the 3D view "isn't repainting".

The install declares names in two places: each server's .ins file
("Command Name = 'X'" under its EditorName) and the menu resources
(.rcs, "Command='Server:X'"). Neither is complete: ADVSIM:RunSimulator
runs a simulation and is in neither. So a name missing from both earns a
warning with the closest declared names, never a refusal.
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import altium_locale

_RCS_COMMAND = re.compile(r"Command\s*=\s*'([A-Za-z0-9_.]+:[A-Za-z0-9_]+)'")
_INS_EDITOR = re.compile(r"EditorName\s*=\s*'([^']+)'")
_INS_COMMAND = re.compile(r"Command\s+Name\s*=\s*'([^']+)'")


@lru_cache(maxsize=2)
def _declared(install: str) -> dict[str, str]:
    """{UPPER name: name as declared} for one install."""
    names: dict[str, str] = {}
    system = Path(install) / "System"
    if not system.is_dir():
        return names
    for path in system.iterdir():
        suffix = path.suffix.lower()
        if suffix not in (".rcs", ".ins"):
            continue
        try:
            text = path.read_text(encoding="latin-1")
        except OSError:
            continue
        if suffix == ".rcs":
            found = _RCS_COMMAND.findall(text)
        else:
            editor = _INS_EDITOR.search(text)
            server = editor.group(1) if editor else path.stem
            found = [f"{server}:{c}" for c in _INS_COMMAND.findall(text)]
        for name in found:
            names.setdefault(name.upper(), name)
    return names


def unlisted(process: str, install: Optional[Path] = None) -> Optional[dict]:
    """A warning when ``process`` is declared nowhere in the install.

    None when it is declared, when it is not Server:Command shaped, or
    when no install can be read (nothing to compare with).
    """
    if ":" not in (process or ""):
        return None
    install = install or altium_locale.install_dir()
    if install is None:
        return None
    names = _declared(str(install))
    if not names or process.strip().upper() in names:
        return None
    server = process.split(":", 1)[0].strip().upper() + ":"
    same_server = [v for k, v in names.items() if k.startswith(server)]
    close = difflib.get_close_matches(process.strip(), same_server or list(names.values()),
                                      n=5, cutoff=0.5)
    return {
        "unlisted_process": process,
        "warning": (
            f"{process} is not among the {len(names)} process names this "
            f"Altium install declares (its .ins and menu .rcs files). An "
            f"unknown name does nothing and reports nothing, so check that "
            f"the command had its effect. Some real names are declared in "
            f"neither file, so this is not proof the name is wrong."),
        "similar_declared": close,
    }
