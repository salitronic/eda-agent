# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Reading back what the environment has installed.

install_library and uninstall_library shipped from the start with
nothing to report the result, so answering "what is installed?" meant
reading the registry from outside Altium. lib_search walks only the
SchLibs already open in the workspace, and design_snapshot_inventory has
to be handed explicit .SchLib paths, so neither covers it.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools import library as lib_mod
from eda_agent.tools.registry import ToolRegistry


def _tools():
    registry = ToolRegistry()
    lib_mod.register_library_tools(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


class _Sent:
    def __init__(self):
        self.calls: list[tuple] = []

    def install(self, monkeypatch, reply=None):
        sent = self

        class _Fake:
            async def send_command_async(self, command, params=None,
                                         timeout=None):
                sent.calls.append((command, params or {}))
                return reply if reply is not None else {"libraries": []}

        monkeypatch.setattr(lib_mod, "get_bridge", lambda: _Fake())
        return self


def test_it_needs_nothing_to_answer(monkeypatch):
    """One call, no arguments. Any tool that needs a path first cannot
    answer the question, which is why this one exists."""
    sent = _Sent().install(monkeypatch)
    asyncio.run(_tools()["lib_get_installed_libraries"]())
    assert sent.calls[0][0] == "library.get_installed_libraries"


def test_the_default_leaves_the_count_flag_to_the_handler(monkeypatch):
    """Sending nothing is what selects the handler's own default.

    with_counts is read as "anything but false", so passing "true" and
    passing nothing are the same call; the empty payload keeps the two
    sides from disagreeing about which one is the default.
    """
    sent = _Sent().install(monkeypatch)
    asyncio.run(_tools()["lib_get_installed_libraries"]())
    assert sent.calls[0][1] == {}


def test_declining_the_counts_is_sent_as_the_handler_reads_it(monkeypatch):
    """The counts are the expensive half: each one opens a library."""
    sent = _Sent().install(monkeypatch)
    asyncio.run(_tools()["lib_get_installed_libraries"](with_counts=False))
    assert sent.calls[0][1] == {"with_counts": "false"}


def test_the_reply_reaches_the_caller_unflattened(monkeypatch):
    """The per-library type and the two totals are the answer, not
    decoration: a library that is available but not switched on shows up
    only as the gap between installed_count and available_count."""
    reply = {
        "libraries": [
            {"library_path": "C:/lib/Parts.IntLib", "file_name": "Parts.IntLib",
             "library_type": "integrated", "library_type_ordinal": 0,
             "component_count": 412},
        ],
        "installed_count": 1, "available_count": 3, "counts_included": True,
    }
    _Sent().install(monkeypatch, reply)
    out = asyncio.run(_tools()["lib_get_installed_libraries"]())
    assert out["installed_count"] == 1 and out["available_count"] == 3
    assert out["libraries"][0]["library_type"] == "integrated"


def test_the_handler_is_wired_to_the_dispatcher():
    """A handler no dispatcher case reaches is dead code, and the tool
    would fail at runtime with an unknown action rather than at import."""
    pas = (lib_mod.__file__.rsplit("src", 1)[0]
           + "scripts/altium/Library.pas")
    text = open(pas, encoding="utf-8", errors="replace").read()
    assert "Function Lib_GetInstalledLibraries(" in text
    assert "'get_installed_libraries':" in text


def test_the_type_ordinals_are_named_in_declaration_order():
    """TLibraryType is read as an integer and named here, because an enum
    identifier this build does not declare faults at runtime as a modal
    the polling loop cannot catch. The order is the enum's own."""
    pas = (lib_mod.__file__.rsplit("src", 1)[0]
           + "scripts/altium/Library.pas")
    text = open(pas, encoding="utf-8", errors="replace").read()
    body = text.split("Function LibTypeName(", 1)[1].split("End;", 1)[0]
    for ordinal, name in enumerate(("integrated", "source", "datafile",
                                    "database", "none", "query",
                                    "design_items")):
        assert f"Ordinal = {ordinal} Then Result := '{name}'" in body


def test_the_type_lookup_does_not_walk_the_installed_list():
    """The TYPE is published on the Available list only.

    Reading AvailableLibraryType at an INSTALLED index would return a
    type belonging to a different library, silently, because both calls
    succeed and both lists are indexed from zero.
    """
    pas = (lib_mod.__file__.rsplit("src", 1)[0]
           + "scripts/altium/Library.pas")
    text = open(pas, encoding="utf-8", errors="replace").read()
    body = text.split("Function InstalledLibTypeOrdinal(", 1)[1].split(
        "Function Lib_GetInstalledLibraries", 1)[0]
    assert "AvailableLibraryCount" in body
    assert "AvailableLibraryPath(I) = LibPath" in body
    assert "InstalledLibraryPath" not in body


def test_a_library_missing_from_the_available_list_is_not_called_a_type():
    """Installed but absent from Available is a real state.

    Defaulting it to the first enum member would report every one of them
    as an integrated library.
    """
    pas = (lib_mod.__file__.rsplit("src", 1)[0]
           + "scripts/altium/Library.pas")
    text = open(pas, encoding="utf-8", errors="replace").read()
    body = text.split("Function InstalledLibTypeOrdinal(", 1)[1].split(
        "Function Lib_GetInstalledLibraries", 1)[0]
    assert "Result := -1;" in body


@pytest.mark.parametrize("name", ["lib_get_installed_libraries"])
def test_it_is_published_as_needing_a_live_editor(name):
    """It reads the running environment, so offline would be a false
    promise to anyone filtering the catalog for what works without it."""
    from eda_agent.tools.metadata import tool_metadata

    meta = tool_metadata(name)
    assert meta["maturity"] == "live_only"
    assert meta["interaction"] == "readonly"
