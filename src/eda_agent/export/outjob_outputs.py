# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Which of an OutJob's containers write what a fab builds from.

The bridge lists each container with the output types it writes
(``project.get_outjob_containers``). A container that writes Gerber, NC
drill, ODB++ or IPC-2581 files writes what a fab builds the bare board from,
so the board is checked against the fab first.
"""
from __future__ import annotations

from typing import Any, Iterable

#: Output types a fab builds the bare board from.
FAB_OUTPUT_TYPES = ("gerber", "gerber x2", "nc drill", "odb", "odb++", "ipc2581")


def fab_kinds(listed: Iterable[Any], container_names: Iterable[str]) -> list[str]:
    """The fab output types the named containers write, sorted, from the
    containers the bridge listed."""
    wanted = {str(n) for n in container_names}
    found: set[str] = set()
    for c in listed or []:
        if not isinstance(c, dict) or str(c.get("name") or "") not in wanted:
            continue
        for kind in c.get("outputs") or []:
            if str(kind).strip().lower() in FAB_OUTPUT_TYPES:
                found.add(str(kind))
    return sorted(found)
