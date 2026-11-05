# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_sync_pcb says what the change order left undone.

Retake 07b swapped a header's footprint in the library and schematic; the
change order "ran" twice and the board kept the old footprint, and a part
deleted from the board was skipped too. A follow-up take lost both USB
pairs it had defined on the board only. None of it was in the reply.
"""

from __future__ import annotations

import asyncio

from eda_agent.tools import project


class _Bridge:
    def __init__(self):
        self.pairs = [{"name": "USB_MCU"}, {"name": "USB_CONN"}]

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "pcb.get_differential_pairs":
            return {"count": len(self.pairs), "pairs": list(self.pairs)}
        if command == "project.update_pcb":
            self.pairs = []                    # the ECO deleted them
            return {"success": True, "components_added_to_pcb": 0}
        if command == "project.get_bom":
            return {"components": [
                {"designator": "J2", "footprint": "JST_SH_4"},
                {"designator": "R1", "footprint": "R0402"},
                {"designator": "C9", "footprint": "C0402"}]}
        if command == "pcb.get_components":
            return {"components": [
                {"designator": "J2", "footprint": "HDR_2x5"},
                {"designator": "R1", "footprint": "r0402"}]}
        if command == "project.get_connectivity_batch":
            return {"components": [{"designator": "R1", "pins": [
                {"pin_number": "1", "net": "VCC"}]}]}
        if command == "pcb.get_pad_properties":
            return {"pads": [{"component": "R1", "name": "1", "net": "VCC"}]}
        if command == "project.component_models":
            assert params == {"designator": "J2", "limit": "1"}
            return {"parts": [{"designator": "J2", "models": [
                {"name": "JST_SH_4", "type": "SIM", "current": True,
                 "integrated": False, "datafiles": []},
                {"name": "JST_SH_4", "type": "PCBLIB", "current": True,
                 "integrated": False, "datafiles": [
                     {"library": "Connectors.PcbLib", "entity": "JST_SH_4",
                      "kind": "PCBLIB", "found_at": self.found_at}]}]}]}
        raise AssertionError(command)

    found_at = ""


def _tools(bridge, monkeypatch, driver=None):
    monkeypatch.setattr(project, "get_bridge", lambda: bridge)
    from eda_agent.tools import answering
    monkeypatch.setattr(answering, "driver", lambda title, steps, on_the_way=None: driver)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    project.register_project_tools(_Mcp())
    return tools


def test_the_reply_names_what_the_sync_did_not_do(monkeypatch):
    tools = _tools(_Bridge(), monkeypatch)
    out = asyncio.run(tools["proj_sync_pcb"]())
    after = out["after_sync"]
    assert after["footprint_mismatch"] == [
        {"designator": "J2", "schematic": "JST_SH_4", "board": "HDR_2x5"}]
    assert after["missing_on_board"] == ["C9"]
    assert after["diff_pairs_removed"] == ["USB_CONN", "USB_MCU"]
    assert "warning" in after


def test_a_mismatch_carries_the_change_order_line_that_explains_it(monkeypatch):
    """The ECO ran, the part kept its old footprint and nothing said why.
    The failed line read off the dialog now rides on the mismatch it
    explains."""
    import contextlib
    import threading

    bridge = _Bridge()
    bridge.expecting = lambda title: contextlib.nullcontext()
    rows = ["Add Components(1)",
            "Change T J2 To Board.PcbDoc Footprint Not Found JST SH 4"]
    driver = (lambda: {"appeared": True, "done": [], "closed": True,
                       "read_filtered": True, "read": rows}, threading.Event())
    out = asyncio.run(_tools(bridge, monkeypatch, driver)["proj_sync_pcb"]())
    assert out["change_order"]["failed_lines"] == [rows[1]]
    assert "read" not in out["change_order"]
    (row,) = out["after_sync"]["footprint_mismatch"]
    assert row["eco_line"] == rows[1]
    assert "eco_line" in out["after_sync"]["warning"]
    # Footprint Not Found names the library Altium could not resolve.
    assert row["footprint_source"] == [{"library": "Connectors.PcbLib", "found_at": ""}]
    assert "no library named Connectors.PcbLib" in out["after_sync"]["warning"]


def test_a_library_that_resolves_is_not_blamed(monkeypatch):
    """The library is there but lacks the footprint: the path is given and
    the warning does not tell the caller to install it."""
    import contextlib
    import threading

    bridge = _Bridge()
    bridge.found_at = r"C:\Libs\Connectors.PcbLib"
    bridge.expecting = lambda title: contextlib.nullcontext()
    rows = ["Change T J2 To Board.PcbDoc Footprint Not Found JST SH 4"]
    driver = (lambda: {"appeared": True, "done": [], "closed": True,
                       "read_filtered": True, "read": rows}, threading.Event())
    out = asyncio.run(_tools(bridge, monkeypatch, driver)["proj_sync_pcb"]())
    (row,) = out["after_sync"]["footprint_mismatch"]
    assert row["footprint_source"] == [
        {"library": "Connectors.PcbLib", "found_at": r"C:\Libs\Connectors.PcbLib"}]
    assert "no library named" not in out["after_sync"]["warning"]


def test_a_clean_sync_says_nothing(monkeypatch):
    bridge = _Bridge()
    bridge.pairs = []

    async def send(command, params=None, timeout=None):
        if command == "project.get_bom":
            return {"components": [{"designator": "R1", "footprint": "R0402"}]}
        if command == "pcb.get_components":
            return {"components": [{"designator": "R1", "footprint": "R0402"}]}
        return await _Bridge.send_command_async(bridge, command, params, timeout)

    bridge.send_command_async = send
    out = asyncio.run(_tools(bridge, monkeypatch)["proj_sync_pcb"]())
    after = out["after_sync"]
    assert after["pad_nets"]["complete"] is True
    del after["pad_nets"]
    assert after == {"footprint_mismatch": [],
                     "missing_on_board": [],
                     "diff_pairs_removed": []}


def test_automatic_change_orders_are_driven_while_the_call_waits(monkeypatch):
    import contextlib
    import threading

    bridge = _Bridge()
    seen = {}

    @contextlib.contextmanager
    def expecting(title):
        seen.setdefault("expected", []).append(title)
        yield

    bridge.expecting = expecting
    stop = threading.Event()
    driver = (lambda: {"appeared": True, "executed": True,
                       "finished": True, "closed": True}, stop)
    out = asyncio.run(_tools(bridge, monkeypatch, driver)["proj_sync_pcb"]())
    assert seen["expected"] == ["Engineering Change Order", "Comparator Results",
                                "No Differences"]
    assert out["change_order"]["closed"] is True
    assert stop.is_set()
