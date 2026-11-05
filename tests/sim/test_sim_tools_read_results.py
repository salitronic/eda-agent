# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sim_run returns the numbers, and sim_get_results the traces.

The bridge is faked; the results files are the ones Altium wrote (see
test_sdf_reader.py for the circuits). Nothing here touches Altium.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sim"


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def sim(monkeypatch):
    from eda_agent.tools import sim as sim_mod
    sim_mod._last_results.clear()
    replies = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            # sim_run counts the charts already in the project's results
            # before it runs; no project is focused here.
            if command == "project.get_focused":
                return {}
            return replies.pop(0)

    monkeypatch.setattr(sim_mod, "get_bridge", lambda: Bridge())
    mcp = _Mcp()
    sim_mod.register_sim_tools(mcp)
    return mcp.tools, replies


def test_a_saved_run_comes_back_with_its_values(sim):
    tools, replies = sim
    path = str(FIX / "divider_op_tran.sdf")
    replies.append({"success": True, "ran": True, "results_document": path,
                    "results_saved": True})
    out = asyncio.run(tools["sim_run"]())
    op = {w["name"]: w for w in out["results"]["Operating Point"]}
    assert op["v(out)"]["last"] == 5.0 and op["v(in)"]["last"] == 10.0
    tran = {w["name"]: w for w in out["results"]["Transient Analysis"]}
    assert tran["v(out)"]["max"] == pytest.approx(5.0, abs=1e-3)
    assert "samples" not in tran["v(out)"], "sim_run stays compact"


def test_an_unsaved_run_says_so_and_reads_nothing(sim):
    tools, replies = sim
    replies.append({"success": True, "ran": True,
                    "results_document": str(FIX / "nowhere.sdf"),
                    "results_saved": False})
    out = asyncio.run(tools["sim_run"]())
    assert "results" not in out and "could not be saved" in out["results_note"]


def test_a_failed_run_is_passed_through_untouched(sim):
    tools, replies = sim
    refusal = {"success": False, "error": "SIM_NOT_RUN"}
    replies.append(dict(refusal))
    assert asyncio.run(tools["sim_run"]()) == refusal


def test_get_results_defaults_to_the_last_run(sim):
    tools, replies = sim
    path = str(FIX / "rc_ac_dc.sdf")
    replies.append({"success": True, "ran": True, "results_document": path,
                    "results_saved": True})
    asyncio.run(tools["sim_run"]())
    out = asyncio.run(tools["sim_get_results"](analysis="AC Analysis",
                                               waves=["v(out)"], max_points=5))
    assert out["results_document"] == path
    [entry] = out["analyses"]
    [wave] = entry["waves"]
    assert wave["name"] == "v(out)" and wave["complex"]
    assert len(wave["samples"]) == 5
    assert wave["samples"][0][1] == pytest.approx(0.5, rel=1e-5)


def test_get_results_names_the_analyses_when_one_is_missing(sim):
    tools, _ = sim
    out = asyncio.run(tools["sim_get_results"](
        results_document=str(FIX / "rc_ac_dc.sdf"), analysis="Transient Analysis"))
    assert out["error"] == "NO_SUCH_ANALYSIS"
    assert set(out["analyses"]) == {"AC Analysis", "DC Sweep"}


def test_a_run_names_the_analyses_it_produced(sim):
    tools, replies = sim
    replies.append({"success": True, "ran": True,
                    "results_document": str(FIX / "rc_ac_dc.sdf"),
                    "results_saved": True})
    out = asyncio.run(tools["sim_run"]())
    assert sorted(out["ran_analyses"]) == ["ac", "dc"]


def test_only_the_charts_this_run_added_are_reported(sim, monkeypatch):
    # The results file keeps every earlier run's charts ahead of the new ones.
    from eda_agent.tools import sim as sim_mod
    tools, replies = sim
    path = str(FIX / "rc_ac_dc.sdf")

    async def one_chart_before(bridge):
        return {path.lower(): 1}

    monkeypatch.setattr(sim_mod, "_chart_count", one_chart_before)
    replies.append({"success": True, "ran": True, "results_document": path,
                    "results_saved": True})
    out = asyncio.run(tools["sim_run"]())
    assert out["ran_analyses"] == ["dc"]
    assert list(out["results"]) == ["DC Sweep"]


def _switch_ok(monkeypatch):
    from eda_agent.tools import sim as sim_mod

    async def ok(bridge, project_path, changes):
        return {"success": True, "simcfg": "x.simcfg", "set": changes}

    monkeypatch.setattr(sim_mod, "_change_simcfg", ok)


def test_a_selection_that_took_says_so(sim, monkeypatch):
    tools, replies = sim
    _switch_ok(monkeypatch)
    replies.append({"success": True, "ran": True,
                    "results_document": str(FIX / "rc_ac_dc.sdf"),
                    "results_saved": True})
    out = asyncio.run(tools["sim_run"](analyses=["ac", "dc"]))
    assert out["switched"]["on"] == ["ac", "dc"]
    assert out["selection_applied"] is True and "selection_note" not in out


def test_a_selection_altium_did_not_take_is_reported_not_hidden(sim, monkeypatch):
    tools, replies = sim
    _switch_ok(monkeypatch)
    replies.append({"success": True, "ran": True,
                    "results_document": str(FIX / "rc_ac_dc.sdf"),
                    "results_saved": True})
    out = asyncio.run(tools["sim_run"](analyses=["ac"]))
    assert out["selection_applied"] is False
    assert "reopened" in out["selection_note"]


def test_an_unknown_analysis_is_refused_before_anything_runs(sim):
    tools, replies = sim
    out = asyncio.run(tools["sim_run"](analyses=["noise"]))
    assert out["error"] == "BAD_ANALYSES" and not replies


def test_get_results_without_any_run_is_an_error(sim):
    tools, _ = sim
    out = asyncio.run(tools["sim_get_results"]())
    assert out["success"] is False and out["error"] == "NO_RESULTS"
