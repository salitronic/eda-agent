# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_set_pad_properties changes only the fields given and reads them back.

There was no way to change a library pad short of deleting and rebuilding
it, which loses everything not re-specified. A mask or paste value written
without the manual flag is ignored by Altium, so the flag goes with it.
"""

from __future__ import annotations

import asyncio
import re

from tests.test_handlers_verify_their_outcome import _body, _decommented, _src


def test_every_write_is_behind_its_own_parameter():
    body = _decommented(_body(_src("Library.pas"), "ApplyPadEdit"))
    for key, write in (("x_size", "Pad.TopXSize :="), ("y_size", "Pad.TopYSize :="),
                       ("rotation", "Pad.Rotation :="), ("hole_size", "Pad.HoleSize :="),
                       ("hole_width", "Pad.HoleWidth :="),
                       ("hole_rotation", "Pad.HoleRotation :=")):
        guard = body.index(f"PadArg(Src, IsOp, '{key}')")
        assert guard < body.index(write), key


def test_expansions_set_the_manual_flag_and_go_back_through_the_cache():
    body = _decommented(_body(_src("Library.pas"), "ApplyPadEdit"))
    assert "Cache.SolderMaskExpansionValid := eCacheManual" in body
    assert "Cache.PasteMaskExpansionValid := eCacheManual" in body
    assert body.index("Cache := Pad.GetState_Cache") < body.index("Pad.SetState_Cache := Cache")


def test_the_reply_is_the_read_back():
    body = _decommented(_body(_src("Library.pas"), "PadEditJson"))
    reply = body[body.index("Result := '{\"name\""):]
    for read in ("Pad.TopXSize", "Pad.HoleRotation", "Pad.Plated",
                 "CacheStateName(Pad, True)"):
        assert read in reply, read


def test_the_tool_sends_only_what_was_passed(monkeypatch):
    sent = {}

    class Bridge:
        async def send_command_async(self, command, params, **kw):
            sent.update(command=command, params=params)
            return {"ok": True}

    import eda_agent.tools.library as library
    monkeypatch.setattr(library, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register

    library.register_library_tools(Mcp())
    set_pad = tools["lib_set_pad_properties"]
    asyncio.run(set_pad("SOT23", "1", plated=False, units="mm",
                        mask_expansion=0.05))
    assert sent["command"] == "library.set_pad_properties"
    assert set(sent["params"]) == {"footprint_name", "pad_name", "library_path",
                                   "plated", "mask_expansion", "units"}
    nothing = asyncio.run(set_pad("SOT23", "1"))
    assert nothing["success"] is False
