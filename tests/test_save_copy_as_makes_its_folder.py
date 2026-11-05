# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_save_copy_as writes into a folder that did not exist yet.

Altium will not save into a missing folder, and the tool answered only
written:false, with nothing to say why.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import pcb as pcb_tools


def test_a_missing_folder_is_made_and_named(monkeypatch, tmp_path):
    target = tmp_path / "new" / "deeper" / "Copy.PcbDoc"

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            if params.get("check_only"):
                return {"target_exists": False}
            assert target.parent.is_dir(), "the folder must exist before the save"
            return {"written": True}

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    pcb_tools.register_pcb_tools(_Mcp())
    out = asyncio.run(tools["pcb_save_copy_as"](str(target)))
    assert out["written"] is True
    assert out["created_folder"] == str(target.parent)
