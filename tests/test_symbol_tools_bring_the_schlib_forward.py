# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The symbol tools focus the only open SchLib themselves, just before drawing.

Focusing the SchLib by hand first showed its last symbol on screen while the
next one was being prepared; in the footprint demo it read as the wrong part.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools.library import _bring_schlib_forward


class _Docs:
    def __init__(self, focused, open_docs):
        self.focused, self.open_docs, self.calls = focused, open_docs, []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if command == "application.get_active_document":
            return {"file_path": self.focused}
        if command == "application.get_open_documents":
            return [{"file_path": p} for p in self.open_docs]
        if command == "application.set_active_document":
            self.focused = params["file_path"]
            return {"success": True}
        raise AssertionError(command)


def test_the_only_schlib_is_focused_when_the_pcblib_has_focus():
    b = _Docs(r"C:\lib\Parts.PcbLib", [r"C:\lib\Parts.PcbLib", r"C:\lib\Parts.SchLib"])
    asyncio.run(_bring_schlib_forward(b))
    assert b.focused == r"C:\lib\Parts.SchLib"


def test_a_focused_schlib_is_left_alone():
    b = _Docs(r"C:\lib\Parts.SchLib", [r"C:\lib\Parts.SchLib"])
    asyncio.run(_bring_schlib_forward(b))
    assert [c for c, _ in b.calls] == ["application.get_active_document"]


def test_two_open_schlibs_leave_the_choice_to_the_caller():
    b = _Docs(r"C:\lib\Parts.PcbLib", [r"C:\a\A.SchLib", r"C:\b\B.SchLib"])
    asyncio.run(_bring_schlib_forward(b))
    assert b.focused == r"C:\lib\Parts.PcbLib"


class _Sent:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        return {"dispatched": True}


def test_a_new_symbol_is_fitted_clear_of_the_toolbar():
    from eda_agent.tools.library import _show_new_symbol
    b = _Sent()
    assert asyncio.run(_show_new_symbol(b)) == "fitted"
    assert b.calls == [("generic.zoom", {"action": "fit"}),
                       ("generic.run_process",
                        {"process": "Sch:Zoom", "params": "Action=ZoomOut"})]


def test_the_part_tools_need_no_library_focused_by_hand():
    """The agent focused each library by hand because the tools said they
    worked on the active one; the library then showed its last part until
    the next was drawn. Each tool now brings its library forward itself."""
    import inspect

    from eda_agent.tools import library
    src = inspect.getsource(library.register_library_tools)
    for tool, helper in (("lib_create_ic_symbol", "_create_symbol"),
                         ("lib_create_symbol_in_parts", "_create_symbol"),
                         ("lib_create_passive_symbol", "_create_symbol"),
                         ("lib_link_footprint", "_bring_schlib_forward"),
                         ("lib_create_3d_model", "_focus_document"),
                         ("lib_create_footprint_from_package", "_unfocused_pcblib")):
        start = src.index(f"async def {tool}(")
        body = src[start:src.index("@mcp.tool()", start)]
        assert helper + "(bridge" in body, tool
        assert "ACTIVE" not in body.split('"""')[1], tool


class _Lib(_Docs):
    async def send_command_async(self, command, params=None, timeout=None):
        if command == "library.create_symbol":
            self.calls.append((command, dict(params or {})))
            return {"success": True}
        return await super().send_command_async(command, params, timeout)


def test_a_symbol_is_made_in_the_library_behind_before_it_comes_forward():
    """Focused first, the SchLib showed its last symbol until the new one
    existed; the symbol is now made there by path, then shown."""
    from eda_agent.tools.library import _create_symbol
    b = _Lib(r"C:\lib\Parts.PcbLib", [r"C:\lib\Parts.PcbLib", r"C:\lib\Parts.SchLib"])
    asyncio.run(_create_symbol(b, {"name": "U1"}))
    names = [c for c, _ in b.calls]
    create = names.index("library.create_symbol")
    assert b.calls[create][1] == {"name": "U1", "library_path": r"C:\lib\Parts.SchLib"}
    assert create < names.index("application.set_active_document")
    assert b.focused == r"C:\lib\Parts.SchLib"


def test_a_focused_schlib_gets_the_plain_create():
    from eda_agent.tools.library import _create_symbol
    b = _Lib(r"C:\lib\Parts.SchLib", [r"C:\lib\Parts.SchLib"])
    asyncio.run(_create_symbol(b, {"name": "U1"}))
    assert ("library.create_symbol", {"name": "U1"}) in b.calls
    assert "application.set_active_document" not in [c for c, _ in b.calls]


def test_switch_view_brings_the_pcblib_forward_for_a_footprint(monkeypatch):
    """With the SchLib in front, obj_switch_view footprint_name=... was
    refused; the agent drew a symbol after its footprint and could not
    show the footprint."""
    from eda_agent.tools import generic

    b = _Docs(r"C:\lib\Parts.SchLib", [r"C:\lib\Parts.SchLib", r"C:\lib\Parts.PcbLib"])
    sent = []
    orig = b.send_command_async

    async def send(command, params=None, timeout=None):
        if command == "generic.switch_view":
            sent.append((b.focused, dict(params)))
            return {"success": True}
        return await orig(command, params, timeout)

    b.send_command_async = send
    monkeypatch.setattr(generic, "get_bridge", lambda: b)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    asyncio.run(tools["obj_switch_view"](mode="2d", footprint_name="U1", fit=True))
    assert sent == [(r"C:\lib\Parts.PcbLib", {"mode": "2d", "fit": "true",
                                              "footprint_name": "U1"})]
