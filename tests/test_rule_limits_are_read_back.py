# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Length, signal impedance and flight time rules: made, changed, read back.

pcb_create_design_rule could not make any of the three and
pcb_set_rule_properties could not change their limits, so a length or
timing budget could only be set in the Rules and Constraints Editor.
Both now go through RuleLimitsReport, which writes each limit through
the rule's own interface and reports what the rule holds afterwards.

MEASURED 2026-10-31 on AD 26.11 (scratch board, discarded): Length
100 / 2500.5 mil then max 3000, Impedance 45-55 then 90-110 ohm, flight
time rising 1.5 ns and falling 2 then 0.75 ns, each read back; Altium's
descriptors agreed ("Max=3000mil", "Maximum=750.0pS",
"Minimum=90.00Ohms Maximum=110.0Ohms"). A flight time sent to the
impedance rule was named in limits_note, not written.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

#: interface -> (local, properties written)
_WRITES = {
    "IPCB_MaxMinLengthConstraint": ("LenRule", ("MinLimit", "MaxLimit")),
    "IPCB_RuleMaxMinImpedance": ("ImpRule", ("Minimum", "Maximum")),
    "IPCB_RuleFlightTime_RisingEdge": ("RiseRule", ("MaximumFlightTime",)),
    "IPCB_RuleFlightTime_FallingEdge": ("FallRule", ("MaximumFlightTime",)),
}


@pytest.fixture(scope="module")
def fns() -> dict[str, str]:
    return load(_PCB_PAS, minimum=100)


class _Recorder:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params or {}))
        return {"ok": True}


@pytest.fixture()
def tools(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: recorder)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    found = {t.name: t.fn for t in asyncio.run(registry.list_tools())}

    def run(tool, **kwargs):
        before = len(recorder.sent)
        out = asyncio.run(found[tool](**kwargs))
        return out, (recorder.sent[-1] if len(recorder.sent) > before else None)

    return run


@pytest.mark.parametrize("interface", sorted(_WRITES))
def test_each_limit_is_written_and_read_back(fns, interface):
    body = fns["RuleLimitsReport"]
    local, props = _WRITES[interface]
    decl = next(ln for ln in body.split("Begin", 1)[0].splitlines()
                if ln.strip().startswith(local))
    assert interface in decl, f"{local} must be declared {interface} to narrow"
    assert f"{local} := Iter.FirstPCBObject;" in body
    for prop in props:
        assert f"{local}.{prop} := " in body, f"{interface}.{prop} is never written"
        assert f":= {local}.{prop};" in body, f"{interface}.{prop} is never read back"


def test_a_limit_for_another_kind_is_named_not_written(fns):
    body = fns["RuleLimitsReport"]
    assert "Wrong := Wrong + ' length'" in body
    assert "Wrong := Wrong + ' signal_impedance'" in body
    assert "Wrong := Wrong + ' flight_time'" in body
    assert "these apply to another kind" in body
    assert "Written but not held" in body


def test_flight_time_goes_in_as_seconds(fns):
    body = fns["RuleLimitsReport"]
    assert body.count("StrToFloatDef(Flight, -1) / 1000000000") == 2
    assert "FloatToJsonStr(GotD * 1000000000)" in body


def test_both_handlers_report_the_limits(fns):
    setter = fns["PCB_SetRuleProperties"]
    assert "LimitsReport := RuleLimitsReport(Board, Kind, RuleName, Params, UpdatedCount);" in setter
    assert setter.index("LimitsReport := RuleLimitsReport") < setter.rindex('"properties_updated":')
    create = fns["PCB_CreateDesignRule"]
    assert "RuleLimitsReport(Board, Rule.RuleKind, RuleName, Params, LimitCount)" in create
    for enum in ("eRule_MaxMinLength", "eRule_MaxMinImpedance",
                 "eRule_FlightTime_RisingEdge", "eRule_FlightTime_FallingEdge"):
        assert f"PCBRuleFactory({enum})" in create
    message = create.split("'Unknown rule_type: '", 1)[1].split(";", 1)[0]
    for rule_type in ("length", "signal_impedance", "flight_time_rising", "flight_time_falling"):
        assert rule_type in message


@pytest.mark.parametrize("rule_type,value,max_value,sent", [
    ("length", 100, 2500.5, {"min_length_mils": "100", "max_length_mils": "2500.5"}),
    ("signal_impedance", 45, 55, {"min_signal_impedance_ohms": "45",
                                  "max_signal_impedance_ohms": "55"}),
    ("flight_time_rising", 1.5, None, {"max_flight_time_ns": "1.5"}),
    ("flight_time_falling", 2, None, {"max_flight_time_ns": "2"}),
])
def test_create_sends_the_limits_by_name(tools, rule_type, value, max_value, sent):
    kwargs = {"name": "R", "rule_type": rule_type, "value": value}
    if max_value is not None:
        kwargs["max_value"] = max_value
    _, (command, params) = tools("pcb_create_design_rule", **kwargs)
    assert command == "pcb.create_design_rule"
    for key, want in sent.items():
        assert params[key] == want


def test_a_flight_time_of_zero_is_refused_before_altium(tools):
    out, sent = tools("pcb_create_design_rule", name="R",
                      rule_type="flight_time_rising", value=0)
    assert out["ok"] is False and sent is None
    out, sent = tools("pcb_set_rule_properties", name="R", max_flight_time_ns=0)
    assert out["ok"] is False and "ns" in out["reason"] and sent is None


def test_set_rule_properties_passes_the_limits(tools):
    _, (_, params) = tools("pcb_set_rule_properties", name="R", max_length_mils=3000,
                           min_signal_impedance_ohms=90, max_flight_time_ns=0.75)
    assert params["max_length_mils"] == "3000"
    assert params["min_signal_impedance_ohms"] == "90"
    assert params["max_flight_time_ns"] == "0.75"


def test_the_copper_read_takes_its_filters(tools):
    _, (command, params) = tools("pcb_get_copper", kinds="polygon", layer="Mechanical17",
                                 selected=True, offset=10, limit=5)
    assert command == "pcb.get_layout_model"
    assert params == {"section": "copper", "offset": "10", "limit": "5",
                      "kinds": "polygon", "layer": "Mechanical17", "selected": "true"}
    _, (_, params) = tools("pcb_get_copper")
    assert "kinds" not in params and "layer" not in params and "selected" not in params
