# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Families of tools advertised as one tool each.

Every member stays reachable: through its family tool by kind, and by its
own name through tool_invoke. The family tool checks and converts the
arguments the way the server would for the member, and tool_catalog
lists every member with how to reach it.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from eda_agent.tools import folds
from eda_agent.tools.metadata import tool_metadata
from eda_agent.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def altium():
    from eda_agent.server import register_backend

    registry = ToolRegistry()
    register_backend(registry, "altium", "full")
    return registry


def _run(coro):
    return asyncio.run(coro)


def test_no_member_is_advertised_and_every_member_is_kept(altium):
    advertised = set(altium.names)
    for fold_name in folds.FOLDS:
        members = folds.built_members(fold_name)
        assert fold_name in advertised, fold_name
        assert members, f"{fold_name} captured no member"
        assert not advertised & set(members.values())
        assert set(members.values()) <= set(altium.hidden)
    for single, bulk in folds.HIDDEN.items():
        assert single in altium.hidden and single not in advertised
        assert bulk in advertised


def test_the_named_members_all_exist(altium):
    """A renamed member would otherwise drop out of its family silently."""
    for fold_name, fold in folds.FOLDS.items():
        missing = set(fold.members.values()) - set(altium.hidden)
        assert not missing, (fold_name, missing)


def test_a_family_tool_runs_its_member_with_converted_arguments(altium):
    out = _run(altium.call_tool("pcb_calc", {
        "kind": "wavelength", "args": {"frequency_hz": "1e9"}}))
    assert out["ok"] is True
    assert round(out["wavelength_mm"], 3) == 299.792


@pytest.mark.parametrize("call, reason", [
    ({"kind": "warp_drive"}, "unknown kind"),
    ({"kind": "wavelength", "args": {"freq": 1}}, "wavelength:"),
    ({"kind": "wavelength", "args": {"frequency_hz": "fast"}}, "bad arguments"),
])
def test_a_family_tool_refuses_before_calling_the_member(altium, call, reason):
    out = _run(altium.call_tool("pcb_calc", call))
    assert out["ok"] is False and reason in out["reason"]


def test_args_that_are_not_a_dict_are_refused_by_type_as_on_the_advertised_tool(altium):
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        _run(altium.call_tool("pcb_calc", {"kind": "wavelength", "args": [1]}))


def test_the_unknown_kind_reply_lists_the_kinds(altium):
    out = _run(altium.call_tool("app_menu", {"how": "teleport"}))
    assert set(out["known"]) == {"find", "run", "click", "drive"}


def test_tool_invoke_reaches_a_member_by_its_own_name(altium):
    out = _run(altium.call_tool("tool_invoke", {
        "name": "pcb_calc_wavelength", "arguments": {"frequency_hz": 1e9}}))
    assert out["tool"] == "pcb_calc_wavelength"
    result = out["result"]
    if isinstance(result, str):
        result = json.loads(result)
    assert result["ok"] is True


def test_the_catalog_lists_members_with_how_to_reach_them(altium):
    out = _run(altium.call_tool("tool_catalog", {"query": "pcb_place_3d_body"}))
    rows = {t["name"]: t for t in out["tools"]}
    assert rows["pcb_place_3d_body"]["reach"].startswith("pcb_body(kind='step'")
    out = _run(altium.call_tool("tool_catalog", {
        "query": "pcb_place_via", "with_schema": True}))
    rows = {t["name"]: t for t in out["tools"]}
    assert rows["pcb_place_via"]["reach"].startswith("pcb_place_vias")
    assert "size_mm" in rows["pcb_place_via"]["parameters"]


def test_the_description_names_every_kind_and_its_arguments(altium):
    doc = altium.get("pcb_dimension").description
    for kind in ("linear", "angular", "radial", "baseline"):
        assert f"- {kind}(" in doc
    assert "[pcb_place_baseline_dimension]" in doc
    assert "baseline(x, y, points" in doc


def test_a_family_takes_its_members_most_demanding_metadata(altium):
    calc = tool_metadata("pcb_calc")
    # polygon_area reads the open board, so the family is live.
    assert calc["maturity"] == "live_only"
    assert calc["category"] == "pcb"
    assert tool_metadata("audit")["category"] == "audit"


def test_call_form_and_reach_name_the_advertised_tool():
    assert folds.call_form("audit_find_acute_angles") == "audit kind=acute_angles"
    assert folds.call_form("app_run_ui_command") == "app_menu how=drive"
    assert folds.call_form("pcb_place_via") == "pcb_place_vias"
    assert folds.call_form("pcb_get_vias") == "pcb_get_vias"
    assert folds.reach_of("pcb_get_vias") is None


def test_tool_guide_says_how_to_reach_a_member(altium):
    out = _run(altium.call_tool("tool_guide", {
        "task": "place a 3d step model on the board"}))
    assert out["reach"]["pcb_place_3d_body"].startswith("pcb_body(kind='step'")


def test_bulk_vias_take_millimetres_as_the_single_tool_did(monkeypatch):
    from eda_agent.tools import pcb

    sent = []

    class _Bridge:
        async def send_command_async(self, command, params, **kw):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(pcb, "get_bridge", lambda: _Bridge())
    registry = ToolRegistry()
    pcb.register_pcb_tools(registry)
    _run(registry.call_tool("pcb_place_vias", {"vias": [
        {"x": 0, "y": 0, "size_mm": 1.2, "hole_size_mm": 0.6}]}))
    fields = sent[0][1]["vias"].split(",")
    assert float(fields[2]) == pytest.approx(1.2 / 0.0254, abs=1e-3)
    assert float(fields[3]) == pytest.approx(0.6 / 0.0254, abs=1e-3)


def test_the_minimal_toolset_still_reaches_members():
    from eda_agent.server import register_backend

    registry = ToolRegistry()
    register_backend(registry, "altium", "minimal")
    assert set(registry.names) == {"tool_catalog", "tool_invoke"}
    out = _run(registry.call_tool("tool_invoke", {
        "name": "pcb_calc", "arguments": {
            "kind": "wavelength", "args": {"frequency_hz": 1e9}}}))
    assert out["result"]["ok"] is True
    out = _run(registry.call_tool("tool_invoke", {
        "name": "pcb_calc_wavelength", "arguments": {"frequency_hz": 1e9}}))
    assert out["result"]["ok"] is True
