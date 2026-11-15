# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pin lists of many symbols are read one library at a time.

A sheet's symbols come from several libraries in sheet order. Read one
by one, each read brought its library forward first, so a sheet of
capacitors and resistors switched library for every symbol: 30 symbols
were 60 requests and several seconds. Now each library comes forward
once and all its symbols are read in one request.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

import eda_agent.bridge as bridge_pkg
from eda_agent.bridge import altium_bridge as ab
from eda_agent.tools import bulk as tool_bulk
from eda_agent.tools import library as lib_tools
from eda_agent.tools import sch_template as tmpl_tools

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

A = r"C:\lib\Caps.SchLib"
B = r"C:\lib\Resistors.SchLib"
LIBS = {A: {"CAP 1u": ["1", "2"], "CAP 10n": ["1", "2"], "CAP 4u7": ["1", "2"]},
        B: {"RES 10k": ["1", "2"], "RES 1k": ["1", "2"], "RES | odd": ["1", "2"]}}


class _Libraries:
    """SchLibs behind a bridge that answers the list read, or (old) not."""

    def __init__(self, lists: bool = True):
        self.lists = lists
        self.front = A
        self.sent: list[tuple[str, dict]] = []

    def _pins(self, name):
        pins = LIBS.get(self.front, {}).get(name)
        if pins is None:
            return None
        return {"component": name, "count": len(pins),
                "pins": [{"designator": d, "name": d} for d in pins]}

    async def send_command_async(self, command, params=None, timeout=None, **kw):
        p = dict(params or {})
        self.sent.append((command, p))
        if command == "application.set_active_document":
            self.front = p["file_path"]
            return {"success": True}
        if command == "library.get_pin_list":
            if "component_names" in p and self.lists:
                out = []
                for name in p["component_names"].split("|"):
                    one = self._pins(name)
                    out.append(one or {"component": name,
                                       "error": f"Component not found: {name}"})
                return {"components": out}
            # Older scripts ignore the list and read the symbol selected.
            name = p.get("component_name") or "CAP 1u"
            one = self._pins(name)
            if one is None:
                raise ab.AltiumError("COMPONENT_NOT_FOUND")
            return one
        return {"success": True}

    send_command_direct = send_command_async

    def commands(self):
        return [c for c, _ in self.sent]


def _tools():
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = tool_bulk.with_items(fn, fn.__name__)
                return tools[fn.__name__]
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    lib_tools.register_library_tools(_Mcp())
    return tools


@pytest.fixture
def libs(monkeypatch):
    lib = _Libraries()
    monkeypatch.setattr(bridge_pkg, "get_bridge", lambda: lib)
    monkeypatch.setattr(lib_tools, "get_bridge", lambda: lib)
    return lib


ALTERNATING = [("CAP 1u", A), ("RES 10k", B), ("CAP 10n", A),
               ("RES 1k", B), ("CAP 4u7", A), ("RES 10k", B)]


def test_items_from_alternating_libraries_bring_each_forward_once(libs):
    out = asyncio.run(_tools()["lib_get_pin_list"](items=[
        {"component_name": n, "library_path": lib} for n, lib in ALTERNATING]))
    assert out["failed"] == 0, out
    assert [i["component"] for i in out["items"]] == [n for n, _ in ALTERNATING]
    assert all(i["count"] == 2 for i in out["items"])
    assert libs.commands().count("application.set_active_document") == 2
    reads = [p for c, p in libs.sent if c == "library.get_pin_list"]
    assert [r.get("component_names") for r in reads] == [
        "CAP 1u|CAP 10n|CAP 4u7", "RES 10k|RES 1k"]


def test_a_missing_symbol_fails_only_its_own_item(libs):
    out = asyncio.run(_tools()["lib_get_pin_list"](items=[
        {"component_name": "CAP 1u"}, {"component_name": "NOPE"},
        {"component_name": "CAP 10n"}]))
    assert out["failed"] == 1
    assert out["items"][1]["success"] is False and "NOPE" in out["items"][1]["error"]
    assert out["items"][0]["count"] == 2 and out["items"][2]["count"] == 2


def test_older_scripts_are_read_one_symbol_at_a_time(monkeypatch):
    lib = _Libraries(lists=False)
    monkeypatch.setattr(lib_tools, "get_bridge", lambda: lib)
    out = asyncio.run(lib_tools.read_pin_lists(lib, ["CAP 10n", "CAP 4u7"]))
    assert {n: r["component"] for n, r in out.items()} == {
        "CAP 10n": "CAP 10n", "CAP 4u7": "CAP 4u7"}


def test_a_name_holding_the_separator_is_read_on_its_own(monkeypatch):
    lib = _Libraries()
    lib.front = B
    out = asyncio.run(lib_tools.read_pin_lists(lib, ["RES 10k", "RES | odd", "RES 1k"]))
    assert all(r.get("count") == 2 for r in out.values()), out
    reads = [p for c, p in lib.sent if c == "library.get_pin_list"]
    assert {"component_name": "RES | odd"} in reads
    assert {"component_names": "RES 10k|RES 1k"} in reads


def test_a_long_list_is_read_in_chunks(monkeypatch):
    lib = _Libraries()
    names = [f"CAP {i}" for i in range(lib_tools.PIN_LIST_CHUNK * 2 + 1)]
    asyncio.run(lib_tools.read_pin_lists(lib, names))
    reads = [p for c, p in lib.sent if c == "library.get_pin_list"]
    assert len(reads) == 3
    assert all(len(r.get("component_names", "x").split("|")) <= lib_tools.PIN_LIST_CHUNK
               for r in reads)


def test_the_template_reads_each_library_once(libs):
    parts = [{"designator": f"P{i}", "lib_reference": n, "library_path": lib,
              "x": 0, "y": 0} for i, (n, lib) in enumerate(ALTERNATING)]
    pins, problems = asyncio.run(tmpl_tools._symbol_pins(libs, parts))
    assert not problems, problems
    assert set(pins) == {f"P{i}" for i in range(len(ALTERNATING))}
    assert libs.commands().count("application.set_active_document") == 2
    assert libs.commands().count("library.get_pin_list") == 2


def test_the_library_in_front_is_read_before_another_comes_forward(libs):
    out = asyncio.run(_tools()["lib_get_pin_list"](items=[
        {"component_name": "RES 1k", "library_path": B},
        {"component_name": "CAP 1u"}]))
    assert out["failed"] == 0, out
    first_read = libs.commands().index("library.get_pin_list")
    assert "application.set_active_document" not in libs.commands()[:first_read]


def test_the_handler_reads_a_list_split_on_the_bar():
    code = (SCRIPTS / "Library.pas").read_text(encoding="utf-8", errors="replace")
    body = re.search(r"(?m)^Function Lib_GetPinList\b.*?^End;", code, re.S).group(0)
    assert "ExtractJsonValue(Params, 'component_names')" in body
    assert "PinListsJson(SchLib, Names)" in body
    lists = re.search(r"(?m)^Function PinListsJson\b.*?^End;", code, re.S).group(0)
    assert "Pos('|', Rest)" in lists and "LookupLibComponent(SchLib, Name)" in lists
