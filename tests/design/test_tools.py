# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Smoke tests for design MCP tools, they register and dispatch correctly."""

from __future__ import annotations

import asyncio
import json

import pytest

from eda_agent.design.discipline import get_discipline
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _valid_plan_json() -> str:
    plan = DesignPlan(
        spec="LED + 1k on 5V",
        summary="LED tied to GND through 1k on the V5 rail.",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES_0805", value="1k", sheet="main"),
            Part(refdes="D1", lib_ref="LED_RED", sheet="main"),
        ],
        nets=[
            Net(
                name="V5",
                is_power=True,
                pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="D1", pin="A")],
            ),
            Net(
                name="LED_K",
                pins=[PinRef(refdes="R1", pin="2"), PinRef(refdes="D1", pin="K")],
            ),
        ],
    )
    return plan.model_dump_json()


class _CapturingMCP:
    """Minimal stand-in for FastMCP, records registered tools."""

    def __init__(self) -> None:
        self.tools: dict[str, callable] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def _registered_tools():
    from eda_agent.tools.design import register_design_tools

    fake = _CapturingMCP()
    register_design_tools(fake)
    return fake.tools


def test_register_exposes_expected_tools() -> None:
    tools = _registered_tools()
    assert set(tools.keys()) >= {
        "design_get_discipline",
        "design_snapshot_inventory",
        "design_validate_plan",
        "design_execute_plan",
        "design_validate",
    }


def test_get_discipline_returns_text_and_schema() -> None:
    tools = _registered_tools()
    result = asyncio.run(tools["design_get_discipline"]())
    assert "discipline" in result
    assert "schema" in result
    assert "Design Discipline" in result["discipline"]
    assert "DesignPlan" in result["schema"]["title"]


def test_validate_plan_accepts_valid_plan() -> None:
    tools = _registered_tools()
    result = asyncio.run(tools["design_validate_plan"](plan_json=_valid_plan_json()))
    assert result["ok"] is True
    assert "Plan valid" in result["summary"]


def test_validate_plan_rejects_invalid_json() -> None:
    tools = _registered_tools()
    result = asyncio.run(tools["design_validate_plan"](plan_json="not json"))
    assert result["ok"] is False
    assert any("invalid JSON" in e for e in result["errors"])


def test_layout_schematic_runs_the_execution_engine() -> None:
    # THE INVERSE OF WHAT THIS ONCE ASSERTED. design_layout_schematic used
    # to run a second, standalone engine and had to warn that its score
    # and placements were not what design_execute_plan would emit. There
    # is one engine now, so the tool must say which: `engine` names the
    # canvas pipeline, and `symbols` says whether the geometry was read
    # from a library or synthesised, which is the only thing that still
    # separates this from an exact emit.
    tools = _registered_tools()
    result = asyncio.run(tools["design_layout_schematic"](plan_json=_valid_plan_json()))
    assert result["ok"] is True
    assert result["engine"] == "canvas"
    assert result["symbols"] in ("altium", "synthetic")
    # Accurate exactly when real symbol geometry was available.
    assert result["execution_accurate"] is (result["symbols"] == "altium")
    assert not any("neat" in n.lower() for n in result["notes"]), (
        "the retired engine must not be mentioned as if it still ran")


def test_validate_plan_rejects_schema_failure() -> None:
    tools = _registered_tools()
    bad = json.dumps({"spec": "x"})  # missing required fields
    result = asyncio.run(tools["design_validate_plan"](plan_json=bad))
    assert result["ok"] is False
    assert len(result["errors"]) > 0


def test_validate_plan_rejects_cross_check_failure() -> None:
    tools = _registered_tools()
    payload = json.loads(_valid_plan_json())
    payload["nets"][0]["pins"][0]["refdes"] = "R99"  # not in parts
    result = asyncio.run(tools["design_validate_plan"](plan_json=json.dumps(payload)))
    assert result["ok"] is False
    assert any("R99" in e for e in result["errors"])


def test_execute_plan_rejects_invalid_json() -> None:
    """Smoke check that the executor MCP tool surface validates input
    before any Altium round-trip; a real round-trip is covered by the
    executor unit tests via fake bridge."""
    tools = _registered_tools()
    result = asyncio.run(
        tools["design_execute_plan"](
            plan_json="not json",
            project_path="C:\\fake.PrjPcb",
        )
    )
    assert result["ok"] is False
    assert any("invalid JSON" in n for n in result["notes"])


# design.validate dispatches to the real bridge, covered by test_validator.py
# unit tests with a fake bridge. We do not exercise the MCP wrapper here
# because it would need either a fake bridge injection point or a running
# Altium instance.


def test_discipline_text_contains_key_rules() -> None:
    text = get_discipline()
    # Connectivity policy: ports > block-local wires > cross-block labels.
    assert "block-local" in text.lower()
    assert "port glyph" in text.lower()
    assert "datasheet" in text.lower()
    assert "nda" in text.lower()
    assert "DesignPlan JSON schema" in text


def _floating_plan_json() -> str:
    """Schema-valid plan whose SELF net lands both pins on U1 (floating)."""
    plan = DesignPlan(
        spec="floating", summary="self-net typo",
        sheets=[Sheet(name="main")],
        parts=[Part(refdes="U1", lib_ref="IC", sheet="main"),
               Part(refdes="R1", lib_ref="RES", sheet="main")],
        nets=[
            Net(name="GOOD", pins=[PinRef(refdes="U1", pin="1"),
                                   PinRef(refdes="R1", pin="1")]),
            Net(name="SELF", pins=[PinRef(refdes="U1", pin="2"),
                                   PinRef(refdes="U1", pin="3")]),
        ],
    )
    return plan.model_dump_json()


def _undecoupled_ic_plan_json() -> str:
    """Schema-valid plan: MCU on VCC/GND with no bypass cap (ERC warning)."""
    plan = DesignPlan(
        spec="mcu", summary="undecoupled mcu",
        sheets=[Sheet(name="main")],
        parts=[Part(refdes="U1", lib_ref="MCU", sheet="main"),
               Part(refdes="J1", lib_ref="HDR", sheet="main")],
        nets=[
            Net(name="VCC", is_power=True, pins=[
                PinRef(refdes="J1", pin="1"), PinRef(refdes="U1", pin="1"),
                PinRef(refdes="U1", pin="2")]),
            Net(name="GND", is_ground=True, pins=[
                PinRef(refdes="J1", pin="2"), PinRef(refdes="U1", pin="5")]),
            Net(name="SIG", pins=[PinRef(refdes="U1", pin="6"),
                                  PinRef(refdes="J1", pin="3")]),
        ],
    )
    return plan.model_dump_json()


def test_validate_plan_flags_floating_net_as_error():
    tools = _registered_tools()
    result = asyncio.run(tools["design_validate_plan"](
        plan_json=_floating_plan_json()))
    assert result["ok"] is False
    codes = [e["code"] for e in result["erc"]["errors"]]
    assert "floating_net" in codes


def test_validate_plan_reports_decoupling_warning_but_passes():
    tools = _registered_tools()
    result = asyncio.run(tools["design_validate_plan"](
        plan_json=_undecoupled_ic_plan_json()))
    assert result["ok"] is True                     # warning does not fail
    codes = [w["code"] for w in result["erc"]["warnings"]]
    assert "missing_decoupling" in codes
    assert "warning" in result["summary"].lower()


def _divider_plan_json(rtop="10k", rbot="20k") -> str:
    plan = DesignPlan(
        spec="div", summary="divider",
        sheets=[Sheet(name="main")],
        parts=[Part(refdes="J1", lib_ref="HDR", sheet="main"),
               Part(refdes="R1", lib_ref="RES", value=rtop, sheet="main"),
               Part(refdes="R2", lib_ref="RES", value=rbot, sheet="main")],
        nets=[Net(name="VRAIL", is_power=True, pins=[
                  PinRef(refdes="J1", pin="1"), PinRef(refdes="R1", pin="1")]),
              Net(name="MID", pins=[PinRef(refdes="R1", pin="2"),
                                    PinRef(refdes="R2", pin="1")]),
              Net(name="GND", is_ground=True, pins=[
                  PinRef(refdes="J1", pin="2"), PinRef(refdes="R2", pin="2")])],
    )
    return plan.model_dump_json()


def test_describe_circuits_reports_divider_ratio():
    tools = _registered_tools()
    out = asyncio.run(tools["design_describe_circuits"](
        plan_json=_divider_plan_json("10k", "20k")))
    assert out["ok"] is True
    div = [c for c in out["circuits"] if c["motif"] == "voltage_divider"]
    assert len(div) == 1
    assert div[0]["params"]["ratio"] == pytest.approx(20.0 / 30.0)


def test_describe_circuits_rejects_bad_json():
    tools = _registered_tools()
    out = asyncio.run(tools["design_describe_circuits"](plan_json="not json"))
    assert out["ok"] is False


def test_review_plan_bundles_all_sections():
    tools = _registered_tools()
    out = asyncio.run(tools["design_review_plan"](
        plan_json=_undecoupled_ic_plan_json()))   # MCU on VCC/GND, no decap
    assert out["ok"] is True
    # All four sections present.
    assert set(["stats", "erc", "circuits", "placement_constraints"]) <= set(out)
    # Stats see the MCU and the power rail.
    assert out["stats"]["ic_count"] == 1
    assert out["stats"]["power_rails"] == ["VCC"]
    # ERC flags the missing decoupling (a warning, so passed stays True).
    codes = [w["code"] for w in out["erc"]["warnings"]]
    assert "missing_decoupling" in codes
    assert out["passed"] is True


def test_review_plan_reports_divider_circuit():
    tools = _registered_tools()
    out = asyncio.run(tools["design_review_plan"](
        plan_json=_divider_plan_json("10k", "20k")))
    assert out["ok"] is True
    div = [c for c in out["circuits"] if c["motif"] == "voltage_divider"]
    assert len(div) == 1
    assert div[0]["params"]["ratio"] == pytest.approx(20.0 / 30.0)


def test_review_plan_rejects_bad_json():
    tools = _registered_tools()
    out = asyncio.run(tools["design_review_plan"](plan_json="not json"))
    assert out["ok"] is False


def test_review_plan_includes_net_classes():
    tools = _registered_tools()
    out = asyncio.run(tools["design_review_plan"](
        plan_json=_undecoupled_ic_plan_json()))   # VCC power, GND ground, SIG
    assert out["ok"] is True
    nc = out["net_classes"]
    assert nc["power"] == ["VCC"]
    assert nc["ground"] == ["GND"]
    assert "SIG" in nc.get("signal", [])


def _usb_plan_json() -> str:
    plan = DesignPlan(
        spec="usb", summary="usb pair", sheets=[Sheet(name="main")],
        parts=[Part(refdes="J1", lib_ref="USB", sheet="main"),
               Part(refdes="U1", lib_ref="PHY", sheet="main")],
        nets=[
            Net(name="DP", role="differential", pins=[
                PinRef(refdes="J1", pin="1"), PinRef(refdes="U1", pin="10")]),
            Net(name="DM", role="differential", pins=[
                PinRef(refdes="J1", pin="2"), PinRef(refdes="U1", pin="11")]),
            Net(name="VBUS", is_power=True, pins=[
                PinRef(refdes="J1", pin="8"), PinRef(refdes="U1", pin="9")]),
            Net(name="GND", is_ground=True, pins=[
                PinRef(refdes="J1", pin="7"), PinRef(refdes="U1", pin="6")])],
    )
    return plan.model_dump_json()


def test_suggest_diff_pair_traces_tool():
    tools = _registered_tools()
    out = asyncio.run(tools["design_suggest_diff_pair_traces"](
        plan_json=_usb_plan_json(), target_ohms=90, dielectric_height_mils=7.0,
        spacing_mils=6.0))
    assert out["ok"] is True
    assert len(out["pairs"]) == 1
    p = out["pairs"][0]
    assert set(p["nets"]) == {"DP", "DM"}
    assert p["feasible"] and p["width_mils"] > 0


def test_suggest_diff_pair_traces_rejects_single_ended():
    tools = _registered_tools()
    out = asyncio.run(tools["design_suggest_diff_pair_traces"](
        plan_json=_usb_plan_json(), geometry="microstrip"))
    assert out["ok"] is False
