# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The pin/pad check reads the PcbLib beside the symbol's SchLib first.

Live, with the user's own libraries open beside a scratch pair, the check
read every open PcbLib in name order, bringing each forward, and left the
last one in front, so the link that followed found no SchLib.
"""
from __future__ import annotations

import asyncio

from eda_agent.bridge.exceptions import AltiumError
from tests.test_link_footprint_reads_the_link_back import _tools

_SCH = r"C:\work\Chk.SchLib"
_MINE = r"C:\work\Chk.PcbLib"
_THEIRS = r"C:\lib\A_Users.PcbLib"


class _Bridge:
    def __init__(self):
        self.front = _SCH
        self.read = []

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "application.get_open_documents":
            return {"documents": [{"file_path": p} for p in (_THEIRS, _SCH, _MINE)]}
        if command == "application.get_active_document":
            return {"file_path": self.front}
        if command == "application.set_active_document":
            self.front = params["file_path"]
            return {"success": True}
        if command == "library.get_pad_geometry":
            self.read.append(params["library_path"])
            self.front = params["library_path"]
            if params["library_path"] != _MINE:
                raise AltiumError("Footprint not found", code="NO_FOOTPRINT")
            return {"name": "FP1", "pads": [{"name": "1"}, {"name": "2"}]}
        if command == "library.get_pin_list":
            assert self.front == _SCH, self.front
            return {"pins": [{"designator": "1"}, {"designator": "2"}]}
        if command == "library.link_footprint":
            assert self.front == _SCH, self.front
            return {"success": True}
        if command == "library.get_component_details":
            return {"models": [{"model_name": "FP1", "model_type": "PCBLIB"}]}
        raise AssertionError(command)


def test_the_library_beside_the_symbol_is_read_and_the_schlib_put_back(monkeypatch):
    b = _Bridge()
    out = asyncio.run(_tools(monkeypatch, b)["lib_link_footprint"](
        component_name="U1", footprint_name="FP1"))
    assert out["success"] and out["pin_pad_parity"]["ok"]
    assert b.read == [_MINE]
    assert out["pin_pad_parity"]["footprint_library"] == _MINE
