# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sim_run_deck runs a SPICE deck in Altium's simulator.

A demo run drew a simulation sheet of its own symbols, attached
primitives (9/9 "attached", all "ready"), and sim_run failed three times:
Altium's messages said "Missing component models". A deck needs no model
link: Altium runs a .nsx with ADVSIM:RunSimulator (measured 2026-10-06).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import sim as sim_tools

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _tool(monkeypatch, sent, reply=None):
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return reply if reply is not None else {"success": True, "ran": True,
                                                    "results_document": "", "results_saved": False}

    monkeypatch.setattr(sim_tools, "get_bridge", lambda: _Bridge())
    sim_tools.register_sim_tools(_Mcp())
    return tools["sim_run_deck"]


def test_the_deck_is_written_ended_and_run(monkeypatch, tmp_path):
    sent = []
    deck = tmp_path / "hotplug.nsx"
    out = asyncio.run(_tool(monkeypatch, sent)(
        deck="* hot-plug\nV1 in 0 PULSE(0 5 10u 1u)\nR1 in out 0.25\nC1 out 0 22u\n"
             ".TRAN 1u 2m\n.PLOT TRAN V(out)", file_path=str(deck)))
    assert deck.read_text().strip().splitlines()[-1] == ".END"
    assert sent == [("generic.run_spice_deck", {"file_path": str(deck)})]
    assert out["ran"] is True


def test_a_deck_without_an_analysis_is_refused(monkeypatch, tmp_path):
    sent = []
    out = asyncio.run(_tool(monkeypatch, sent)(deck="R1 a 0 1k\n.END",
                                               file_path=str(tmp_path / "x.nsx")))
    assert out["success"] is False and sent == []


def test_only_an_nsx_path(monkeypatch, tmp_path):
    sent = []
    out = asyncio.run(_tool(monkeypatch, sent)(deck=".OP\n.END",
                                               file_path=str(tmp_path / "x.SchDoc")))
    assert out["success"] is False and sent == []


def test_the_handler_runs_the_deck_with_advsim():
    pas = _GENERIC.read_text(encoding="utf-8")
    assert "'run_spice_deck':             Result := Gen_RunSpiceDeck(" in pas
    body = pas[pas.index("Function Gen_RunSpiceDeck"):]
    body = body[:body.index("\nFunction ", 10)]
    assert "RunProcess('ADVSIM:RunSimulator')" in body
    assert "AddStringParameter('ObjectKind', 'Document')" in body
