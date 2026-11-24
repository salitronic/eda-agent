# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Per-unit stamps that say which build of each script unit Altium runs.

Altium keeps compiled script units between runs. Re-running
StartMCPServer does not recompile an edited unit, and a unit that failed
to compile can keep serving its previous image while SCRIPT_VERSION in
Main.pas reads new. So one version constant cannot say whether the code
on disk is the code running.

Each unit ends with ``Function UnitStamp_<unit>(Dummy : Integer)`` returning
``'dev'`` in the repository. ``install-scripts`` writes a hash of the
unit's content in its place in the deployed copy, the script reports
every stamp through ``application.get_unit_stamps``, and ``app_ping``
recomputes them from the files and names any unit that differs.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

#: The stamp function's return line, with the value in group 2.
_STAMP = re.compile(
    r"(Function UnitStamp_\w+\(Dummy : Integer\) : String;\s*Begin\s*Result := ')([^']*)(';)",
    re.IGNORECASE)

#: The units the script can report; Dispatcher.pas comes after the unit
#: that collects the stamps, so it cannot be asked.
STAMPED_UNITS = ("Main", "Utils", "Application", "Project", "Library",
                 "PCBGeneric", "PCB", "Generic", "RowsCore", "RowsPcbLib",
                 "RowsPcbLibMore", "RowsPcb", "RowsPadVia", "RowsSchLib",
                 "RowsSchGraph", "Rows", "Audit", "StatusForm")


def unit_stamp(text: str) -> str:
    """The stamp for a unit's source: a hash with its own stamp set to dev."""
    canonical = _STAMP.sub(lambda m: m.group(1) + "dev" + m.group(3), text)
    canonical = canonical.replace("\r\n", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def stamp_source(text: str) -> str:
    """``text`` with its UnitStamp function returning the unit's stamp."""
    value = unit_stamp(text)
    return _STAMP.sub(lambda m: m.group(1) + value + m.group(3), text)


def has_stamp(text: str) -> bool:
    return _STAMP.search(text) is not None


def expected_stamps(folder: Path) -> dict[str, Optional[str]]:
    """``{unit: stamp}`` computed from the .pas files in ``folder``."""
    out: dict[str, Optional[str]] = {}
    for unit in STAMPED_UNITS:
        path = folder / f"{unit}.pas"
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            out[unit] = None
            continue
        out[unit] = unit_stamp(text) if has_stamp(text) else None
    return out


def stale_units(reported: dict, folder: Path) -> tuple[list[str], list[str]]:
    """``(stale, unstamped)`` units, comparing the script's report to files.

    A unit reporting ``dev`` runs from an unstamped copy, the repository
    itself, so it cannot be judged and is listed as unstamped.
    """
    stale: list[str] = []
    unstamped: list[str] = []
    for unit, want in expected_stamps(folder).items():
        have = reported.get(unit)
        if have is None or want is None:
            continue
        if have == "dev":
            unstamped.append(unit)
        elif have != want:
            stale.append(unit)
    return stale, unstamped
