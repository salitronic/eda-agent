# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_screenshot writes into a folder that does not exist yet.

A demo run asked for ``work\\screenshots\\sheet.png`` before anything had
made ``screenshots``, and the tool answered with a bare Errno 2.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from eda_agent.tools import uiauto
from eda_agent.ui import menu, ocr


def test_a_missing_folder_is_created(monkeypatch, tmp_path):
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    def _capture(hwnd, path):
        assert os.path.isdir(os.path.dirname(path)), "folder not made first"
        open(path, "wb").close()
        return True

    monkeypatch.setattr(uiauto, "_altium_pid", lambda reads_only=False: (1, None))
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "capture_png", _capture)
    monkeypatch.setattr(menu, "frame", lambda pid: SimpleNamespace(hwnd=7))
    uiauto.register_uiauto_tools(_Mcp())
    target = tmp_path / "screenshots" / "sheet.png"
    out = asyncio.run(tools["app_screenshot"](path=str(target)))
    assert out["ok"] is True and out["created_folder"] is True
    assert target.is_file()
