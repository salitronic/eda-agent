# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""After a schematic-to-board sync, the reply names pads left without their net."""
from __future__ import annotations

import asyncio

from eda_agent.tools.project import _after_sync


class _Bridge:
    def __init__(self, pad_net: str):
        self.pad_net = pad_net
        self.sent: list[str] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append(command)
        if command == "project.get_bom":
            return {"components": [{"designator": "R1", "footprint": "0603"}]}
        if command == "pcb.get_components":
            return {"components": [{"designator": "R1", "footprint": "0603"}]}
        if command == "pcb.get_differential_pairs":
            return {"pairs": []}
        if command == "project.get_connectivity_batch":
            assert params == {"designators": "R1"}
            return {"components": [{"designator": "R1", "pins": [
                {"pin_number": "1", "net": "VCC"}, {"pin_number": "2", "net": "GND"}]}]}
        if command == "pcb.get_pad_properties":
            return {"pads": [{"component": "R1", "name": "1", "net": "VCC"},
                             {"component": "R1", "name": "2", "net": self.pad_net}]}
        raise AssertionError(command)


def test_an_unbound_pad_is_reported_with_a_warning():
    out = asyncio.run(_after_sync(_Bridge(pad_net=""), set()))
    assert out["pad_nets"]["unbound"] == [{"designator": "R1", "pin": "2", "net": "GND"}]
    assert "1 pad(s) have no net" in out["warning"]


def test_a_complete_sync_has_no_warning():
    out = asyncio.run(_after_sync(_Bridge(pad_net="GND"), set()))
    assert out["pad_nets"]["complete"] is True
    assert "warning" not in out
