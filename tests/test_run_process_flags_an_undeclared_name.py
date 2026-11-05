# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""obj_run_process says when a process name is declared nowhere.

An agent ran PCB:Switch3DView (the command is PCB:SwitchTo3D); RunProcess
did nothing and said nothing, and the agent decided the 3D view "isn't
repainting". The install's .ins and .rcs files declare most names, not
all, so an undeclared one is flagged with the closest declared names and
still runs.
"""

from __future__ import annotations

import asyncio

from eda_agent.ui import altium_processes


def _install(tmp_path):
    system = tmp_path / "System"
    system.mkdir()
    (system / "advpcb.ins").write_text(
        "Server\n    EditorName        = 'PCB'\nEnd\n"
        "Command Name = 'ResetCamera'   LongSummary = 'ResetCamera' End\n"
        "Command Name = 'Zoom'          LongSummary = 'Zoom' End\n",
        encoding="latin-1")
    (system / "AdvPcb.rcs").write_text(
        "PL1 Command='PCB:SwitchTo3D' Caption='3D Layout Mode'\n",
        encoding="latin-1")
    altium_processes._declared.cache_clear()
    return tmp_path


def test_a_declared_name_passes_whichever_file_declares_it(tmp_path):
    install = _install(tmp_path)
    for name in ("PCB:ResetCamera", "pcb:zoom", "PCB:SwitchTo3D"):
        assert altium_processes.unlisted(name, install) is None, name


def test_a_guessed_name_is_flagged_with_the_real_one(tmp_path):
    install = _install(tmp_path)
    hit = altium_processes.unlisted("PCB:Switch3DView", install)
    assert hit and hit["unlisted_process"] == "PCB:Switch3DView"
    assert "PCB:SwitchTo3D" in hit["similar_declared"]
    assert "not proof" in hit["warning"]


def test_the_tool_still_runs_it_and_adds_the_warning(tmp_path, monkeypatch):
    from eda_agent.tools import generic

    install = _install(tmp_path)
    monkeypatch.setattr(altium_processes.altium_locale, "install_dir",
                        lambda: install)
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"dispatched": True}

    monkeypatch.setattr(generic, "get_bridge", lambda: _Bridge())
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    generic.register_generic_tools(_Mcp())
    out = asyncio.run(tools["obj_run_process"]("PCB:Switch3DView"))
    assert sent and sent[0][1]["process"] == "PCB:Switch3DView"
    assert out["success"] and out["unlisted_process"] == "PCB:Switch3DView"
    out = asyncio.run(tools["obj_run_process"]("PCB:SwitchTo3D"))
    assert "unlisted_process" not in out
