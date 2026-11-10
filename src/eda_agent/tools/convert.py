# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium project to KiCad project: ``proj_convert_to_kicad``.

The conversion itself is ``convert.altium_kicad``; this registers it on the
Altium server, where the open board's rules, stack-up, net classes, layer
kinds and compiled netlist are read through the bridge first.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ..bridge import get_bridge


async def _project_and_board(bridge, project_path: str, board_path: str) -> tuple[str, str]:
    if not project_path:
        reply = await bridge.send_command_async("project.get_project_path")
        project_path = str((reply or {}).get("project_path") or (reply or {}).get("path") or "")
    if not board_path and project_path:
        docs = await bridge.send_command_async("project.get_documents",
                                               {"project_path": project_path})
        rows = docs if isinstance(docs, list) else (docs or {}).get("documents") or []
        boards = [d.get("file_path") for d in rows
                  if str(d.get("file_path", "")).lower().endswith(".pcbdoc")]
        board_path = boards[0] if len(boards) == 1 else ""
    return project_path, board_path


def register_convert_tools(mcp):

    @mcp.tool()
    async def proj_convert_to_kicad(
        output_dir: str,
        project_path: str = "",
        board_path: str = "",
        kicad_cli: str = "",
        remove_title_block: bool = True,
    ) -> dict[str, Any]:
        """Convert an Altium project into a clean KiCad project.

        KiCad's own Altium importer brings the project in (kicad.exe is
        driven through its import dialogs). The result is then made into
        a project to keep:
        - one symbol per part in project libraries;
        - power ports as power symbols;
        - Value, Footprint and Datasheet filled in;
        - sheets on the 1.27 mm grid, wire stubs trimmed;
        - a flat project given a root sheet;
        - board nets named as the schematic names them;
        - rules, stack-up and net classes read from the open Altium board;
        - footprints in project libraries, mechanical layers mapped by kind;
        - pours filled with Altium's neck width and island removal;
        - Altium's variants as KiCad variants (not fitted = DNP, varied
          values as field values).
        The Altium files are only read.

        Every step is checked against the pin-to-net map. ``ok`` is true
        only when the schematic, the board and Altium's own compiled
        netlist put the same pins together. Takes a few minutes: KiCad
        opens on screen while it imports.

        Args:
            output_dir: An empty folder for the KiCad project.
            project_path: The .PrjPcb; empty = the focused project.
            board_path: Its .PcbDoc; empty = the project's only board.
            kicad_cli: kicad-cli.exe; empty = the newest KiCad installed.
            remove_title_block: Take out what each sheet's Altium template
                drew (border, title block, logo), found by the template
                objects' own boxes, so what the user drew stays; KiCad
                draws its own.

        Returns:
            ``ok``, ``project`` (the .kicad_pro), ``steps`` (what each step
            did, with the ``check_*`` pin comparisons, ERC and DRC counts
            and the rules not ported), and ``stage`` / ``reason`` when a
            step stopped it.
        """
        from ..convert.altium_kicad import altium_side, pipeline

        bridge = get_bridge()
        project_path, board_path = await _project_and_board(bridge, project_path, board_path)
        if not project_path or not os.path.isfile(project_path):
            return {"ok": False, "reason": "no Altium project: pass project_path"}
        if not board_path:
            return {"ok": False, "reason": "the project has no single board: pass board_path"}
        if os.path.isdir(output_dir) and os.listdir(output_dir):
            return {"ok": False, "reason": f"{output_dir} is not empty"}
        facts = await altium_side.read_board(bridge, board_path, project_path)
        report = await asyncio.to_thread(pipeline.convert, project_path, output_dir, facts,
                                         kicad_cli, remove_title_block)
        report["altium"] = {"project": project_path, "board": board_path,
                            "pins_read": len(facts.get("pins") or [])}
        return report
