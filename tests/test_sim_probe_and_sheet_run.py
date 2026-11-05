# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Probes the simulator sees, and a run for a sheet.

MEASURED on AD 26.11: a probe placed bare (only ProbeValueDisplay) was
passed over by the simulator, which finds probes by their SimulationProbe
parameter; Altium's own voltage probe carries SimulationProbe=V,
ProbeVoltage=True, ProbeName, ProbeCustomName and ProbeValueDisplay. A
run of the project's first simulator output did every analysis whatever
the setup's switches said; the Simulation Dashboard's form
(Kind=AdvSimNetlist, SimulationDashboardConfig=Document=<sheet>) did the
ones switched on.
"""

from __future__ import annotations

import re
from pathlib import Path

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _body(name: str) -> str:
    text = _PAS.read_text(encoding="utf-8")
    start = text.index(f"Function {name}(")
    end = text.index("\nEnd;", start)
    return text[start:end]


def test_a_placed_probe_carries_the_simulation_probe_parameters():
    body = _body("Gen_PlaceProbe")
    for name, value in (("SimulationProbe", "'V'"), ("ProbeVoltage", "'True'"),
                        ("ProbeName", "''"), ("ProbeCustomName", "CustomName")):
        assert re.search(rf"AddProbeParam\(Probe, '{name}', {re.escape(value)},", body), name
    # Added before the probe joins the sheet, so they are saved with it.
    assert body.index("AddProbeParam(") < body.index("RegisterSchObjectInContainer(Probe)")


def test_a_run_for_a_sheet_uses_the_dashboards_form():
    body = _body("Gen_RunSimulation")
    sheet_arm = body[body.index("If Sheet <> '' Then"):body.index("RunProcess('WorkspaceManager:GenerateReport')")]
    assert "AddStringParameter('Kind', 'AdvSimNetlist')" in sheet_arm
    assert "AddStringParameter('SimulationDashboardConfig', 'Document=' + Sheet)" in sheet_arm
    # The sheet in front is used when none is given.
    assert "Sheet := Before" in body


def test_the_probe_list_is_routed():
    text = _PAS.read_text(encoding="utf-8")
    assert re.search(r"'get_probes':\s+Result := Gen_GetProbes\(Params, RequestId\);", text)
    body = _body("Gen_GetProbes")
    assert "MkSet(eProbe)" in body and "not_loaded" in body
