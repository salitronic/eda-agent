# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fab files are written only for a board inside the fab's published limits."""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.design import fab_library
from eda_agent.design.fab_check import check_board
from eda_agent.export.outjob_outputs import fab_kinds

_STATS = {"num_copper_layers": 2, "min_track_width_mils": 6.0,
          "smallest_hole_mils": 12.0, "min_annular_ring_mils": 6.0,
          "vias_blind": 0, "vias_buried": 0}
_RULES = [
    {"name": "Clearance", "enabled": True,
     "descriptor": "Clearance Constraint (Gap=6mil) (All),(All)", "gap_mm": 0.1524},
    {"name": "HoleToHole", "enabled": True,
     "descriptor": "Hole To Hole Clearance (Gap=10mil) (All),(All)"},
    {"name": "Loose", "enabled": False,
     "descriptor": "Clearance Constraint (Gap=1mil) (All),(All)"},
]


def _row(out, figure):
    return next(r for r in out["rows"] if r["figure"] == figure)


def test_a_board_inside_the_limits_passes_with_each_figure_quoted():
    out = check_board(_STATS, _RULES, "JLCPCB")
    assert out["ok"], out
    track = _row(out, "min_track")
    assert track["ok"] and track["quote"] and track["board_mils"] == 6.0
    assert _row(out, "min_gap")["measured"].endswith("(Clearance)"), "disabled rule ignored"


def test_a_track_below_the_fab_floor_is_refused_and_named():
    out = check_board({**_STATS, "min_track_width_mils": 3.0}, _RULES, "OSH Park")
    assert not out["ok"] and "narrowest track" in out["reason"]
    assert _row(out, "min_track")["fab_mils"] == 6.0


def test_a_layer_count_or_a_blind_via_the_fab_does_not_build_is_refused():
    six = check_board({**_STATS, "num_copper_layers": 6}, _RULES, "OSH Park")
    assert not six["ok"] and "6 copper layers" in six["reason"]
    blind = check_board({**_STATS, "vias_blind": 3}, _RULES, "OSH Park")
    assert not blind["ok"] and "builds none" in blind["reason"]


def test_an_unpublished_figure_is_not_checked_never_passed():
    out = check_board(_STATS, _RULES, "PCBCart")
    hole = _row(out, "min_hole_to_hole")
    assert hole["ok"] is None and hole["why"].startswith("not checked")
    assert out["unchecked"] >= 1


def test_an_unknown_fab_names_the_fabs_with_data():
    out = check_board(_STATS, _RULES, "NoSuchFab")
    assert not out["ok"] and "JLCPCB" in out["reason"] and "Eurocircuits" in out["reason"]


@pytest.mark.parametrize("vendor", [v["vendor"] for v in fab_library.list_vendors()])
def test_every_fab_answers_a_two_layer_board(vendor):
    out = check_board(_STATS, _RULES, vendor)
    assert "rows" in out, out
    assert all(r["ok"] in (True, False, None) for r in out["rows"])


_JOB = r"C:\work\Fab.OutJob"
_BOARD = r"C:\work\Board.PcbDoc"
_CONTAINERS = [
    {"name": "Fabrication", "type": "GeneratedFiles", "outputs": ["Gerber", "NC Drill"]},
    {"name": "Reports", "type": "GeneratedFiles", "outputs": ["BOM_PartType"]},
]


@pytest.fixture
def project():
    return _JOB


def test_the_bridge_listing_says_which_containers_write_fab_files():
    assert fab_kinds(_CONTAINERS, ["Fabrication"]) == ["Gerber", "NC Drill"]
    assert fab_kinds(_CONTAINERS, ["Reports"]) == []
    assert fab_kinds(_CONTAINERS, ["Reports", "Fabrication"]) == ["Gerber", "NC Drill"]


def test_nothing_reads_the_outjob_or_project_file_itself():
    import inspect
    from eda_agent.export import outjob_outputs
    src = inspect.getsource(outjob_outputs)
    assert "open(" not in src and "configparser" not in src and "read_text" not in src


def _tools(monkeypatch, stats, checks_off=""):
    from eda_agent import preferences
    from eda_agent.tools import project as proj

    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            if command == "pcb.get_fab_stats":
                return stats
            if command == "pcb.get_design_rules":
                return {"rules": _RULES, "board_path": "Board.PcbDoc"}
            if command == "project.get_outjob_containers":
                return {"outjob_path": (params or {}).get("outjob_path", ""),
                        "containers": _CONTAINERS}
            if command == "project.get_open_projects":
                return {"projects": [{"project_path": r"C:\other\Other.PrjPcb"},
                                     {"project_path": r"C:\work\Board.PrjPcb"}]}
            if command == "project.get_documents":
                if params["project_path"].startswith(r"C:\work"):
                    return [{"file_path": _BOARD}, {"file_path": _JOB}]
                return [{"file_path": r"C:\other\Other.PcbDoc"}]
            if command in ("pcb.focus_board",):
                return {"success": True}
            return {"success": True}

    monkeypatch.setattr(proj, "get_bridge", lambda: Bridge())
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": checks_off})
    ran = []

    async def fake_run(bridge, params, timeout):
        ran.append(params["container_name"])
        return {"success": True}

    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    proj.register_project_tools(Mcp())
    return tools, sent, ran


def test_fab_files_need_the_fab_named_and_the_board_inside_it(monkeypatch, project):
    tools, sent, ran = _tools(monkeypatch, {**_STATS, "min_track_width_mils": 3.0})
    run = tools["proj_run_outjob"]
    unnamed = asyncio.run(run("Fabrication", outjob_path=str(project)))
    assert not unnamed["success"] and "name the fab" in unnamed["reason"]
    assert unnamed["if_the_user_insists"]
    outside = asyncio.run(run("Fabrication", outjob_path=str(project), fab="JLCPCB"))
    assert not outside["success"] and outside["check"] == "fab_export"
    board = _BOARD
    assert ("pcb.focus_board", {"board_path": board}) in sent
    assert outside["fab_check"]["board"] == board
    assert not any(c == "project.run_outjob" for c, _ in sent)


def test_a_board_inside_the_fab_runs_and_carries_the_check(monkeypatch, project):
    tools, sent, ran = _tools(monkeypatch, _STATS)
    out = asyncio.run(tools["proj_run_outjob"]("Fabrication", outjob_path=str(project),
                                               fab="JLCPCB"))
    assert out["fab_check"]["ok"] and out["fab_check"]["fab_outputs"] == ["Gerber", "NC Drill"]
    assert any(c == "project.run_outjob" for c, _ in sent)


def test_a_report_container_runs_without_a_fab(monkeypatch, project):
    tools, sent, ran = _tools(monkeypatch, _STATS)
    out = asyncio.run(tools["proj_run_outjob"]("Reports", outjob_path=str(project)))
    assert "check" not in out and "fab_check" not in out
    assert any(c == "project.run_outjob" for c, _ in sent)
    assert not any(c == "pcb.get_fab_stats" for c, _ in sent)


def test_the_panel_can_switch_the_fab_check_off(monkeypatch, project):
    tools, sent, ran = _tools(monkeypatch, {**_STATS, "min_track_width_mils": 1.0},
                              checks_off="fab_export")
    out = asyncio.run(tools["proj_run_outjob"]("Fabrication", outjob_path=str(project)))
    assert "check" not in out and out["unchecked"]["check"] == "fab_export"
    assert any(c == "project.run_outjob" for c, _ in sent)
    assert not any(c == "pcb.get_fab_stats" for c, _ in sent)


def test_run_all_and_the_fab_package_are_checked_too(monkeypatch, project):
    tools, sent, ran = _tools(monkeypatch, {**_STATS, "min_track_width_mils": 3.0})
    for name in ("proj_run_outjob_all", "proj_generate_fab_package"):
        sent.clear()
        out = asyncio.run(tools[name](outjob_path=str(project), fab="JLCPCB"))
        assert not out["ok"] and out["check"] == "fab_export", name
        assert not any(c == "project.run_outjob" for c, _ in sent), name


def test_a_metric_value_passes_its_imperial_twin_and_a_missing_rule_is_named():
    out = check_board({**_STATS, "min_track_width_mils": 3.937}, _RULES, "Sierra Circuits")
    assert _row(out, "min_track")["ok"], "0.1 mm is the fab's 4 mil"
    assert not check_board({**_STATS, "min_track_width_mils": 3.8}, _RULES,
                           "Sierra Circuits")["ok"]
    edge = _row(out, "copper_to_edge")
    assert edge["ok"] is None
    assert edge["why"] == "not checked: the board has no board outline clearance rule"


def test_custom_limits_are_checked_and_marked_as_the_users():
    limits = {"min_track": 5, "min_drill": 8, "copper_layer_counts": [2, 4]}
    out = check_board(_STATS, _RULES, "custom", limits=limits)
    assert out["ok"] and out["vendor"] == "custom" and "supplied by the user" in out["note"]
    assert _row(out, "min_track")["quote"] == "supplied by the user: 5 mil"
    assert _row(out, "min_gap")["ok"] is None, "a figure not given is not checked"
    tight = check_board(_STATS, _RULES, "custom", limits={"min_track": 8})
    assert not tight["ok"] and "narrowest track" in tight["reason"]


@pytest.mark.parametrize("limits,words", [
    (None, "needs fab_limits"), ({"min_trak": 5}, "unknown figures"),
    ({"min_track": "thin"}, "not a number")])
def test_bad_custom_limits_are_refused(limits, words):
    out = check_board(_STATS, _RULES, "custom", limits=limits)
    assert not out["ok"] and words in out["reason"]


def test_a_fab_without_layer_counts_leaves_them_unchecked():
    out = check_board(_STATS, _RULES, "Epec")
    assert _row(out, "copper_layer_counts")["ok"] is None
    assert "publishes no layer counts" in fab_library.fab_profile(
        "Epec", copper_layers=2)["reason"]


def test_an_export_can_use_custom_limits(monkeypatch, project):
    tools, sent, ran = _tools(monkeypatch, _STATS)
    out = asyncio.run(tools["proj_run_outjob"](
        "Fabrication", outjob_path=str(project), fab="custom",
        fab_limits={"min_track": 5, "copper_layer_counts": [2]}))
    assert out["fab_check"]["ok"] and out["fab_check"]["vendor"] == "custom"
    assert any(c == "project.run_outjob" for c, _ in sent)
