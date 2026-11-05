# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every Altium tool is accounted for on the KiCad backend.

An Altium tool is either registered on KiCad under its own name, or listed
in ``kicad_parity`` as covered by a named KiCad tool, not applicable, or a
known gap. A new Altium tool that lands in none of them fails here, and so
does a mapping to a KiCad tool that is not registered.
"""
from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from eda_agent.tools import register_backend
from eda_agent.tools.kicad_parity import COVERED, GAPS, NOT_APPLICABLE


def _names(backend: str) -> set[str]:
    m = FastMCP(backend)
    register_backend(m, backend, "full")
    return {t.name for t in m._tool_manager.list_tools()}


@pytest.fixture(scope="module")
def surfaces():
    return _names("altium"), _names("kicad")


def test_every_altium_tool_is_accounted_for(surfaces):
    altium, kicad = surfaces
    unaccounted = sorted(altium - kicad - set(COVERED) - set(NOT_APPLICABLE) - set(GAPS))
    assert not unaccounted, (
        f"Altium tools with no KiCad answer in kicad_parity: {unaccounted}. Map each to "
        f"the KiCad tool that covers it, a reason it does not apply, or a gap.")


def test_the_map_names_only_altium_tools_kicad_lacks(surfaces):
    altium, kicad = surfaces
    listed = set(COVERED) | set(NOT_APPLICABLE) | set(GAPS)
    stale = sorted(listed - (altium - kicad))
    assert not stale, f"kicad_parity lists tools that are not Altium-only: {stale}"


def test_each_tool_is_in_one_table():
    tables = [set(COVERED), set(NOT_APPLICABLE), set(GAPS)]
    twice = sorted((tables[0] & tables[1]) | (tables[0] & tables[2]) | (tables[1] & tables[2]))
    assert not twice, f"listed in more than one table: {twice}"


def test_every_covering_tool_is_registered_on_kicad(surfaces):
    _, kicad = surfaces
    missing = sorted({v for v in COVERED.values() if v not in kicad})
    assert not missing, f"kicad_parity names KiCad tools that are not registered: {missing}"


def test_reasons_are_short_and_present():
    for table in (NOT_APPLICABLE, GAPS):
        for name, reason in table.items():
            assert reason and len(reason) <= 100, (name, reason)
            assert "\N{EM DASH}" not in reason, name
