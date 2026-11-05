# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dashboard's model of the board (parts, nets, the status strip) and
its two engines, the placer and the route planner: started, previewed,
then applied or dropped, one at a time."""
from __future__ import annotations

import threading

from eda_agent.web import workbench as wb

SNAP = {
    "board": "A",
    "components": [{"ref": "R10", "footprint": "0402", "side": "top", "x": 0, "y": 0, "rot": 0},
                   {"ref": "R2", "footprint": "0402", "side": "bottom", "x": 100, "y": 0,
                    "rot": 90, "locked": True},
                   {"ref": "U1", "footprint": "SOIC8", "side": "top", "x": 50, "y": 50, "rot": 0}],
    "pads": [{"comp": "R10", "name": "1", "net": "VCC"}, {"comp": "R10", "name": "2", "net": "N1"},
             {"comp": "R2", "name": "1", "net": "VCC"}, {"comp": "R2", "name": "2", "net": "GND"},
             {"comp": "U1", "name": "1", "net": "N1"}, {"comp": "U1", "name": "2", "net": "LONE"}],
    "tracks": [{"x1": 0, "y1": 0, "x2": 30, "y2": 40, "net": "VCC", "layer": "Top"}],
    "arcs": [], "vias": [{"x": 5, "y": 5, "net": "VCC"}],
    "status": {"nets": 3, "routed": 1, "partly": 0, "not_started": 2, "missing_connections": 2,
               "unrouted": {"N1": "open", "GND": "open"}},
    "counts": {"components": 3},
}
FINDINGS = [{"check": "placement", "severity": "error", "refs": ["R10", "U1"], "text": "overlap"},
            {"check": "corners", "severity": "warning", "refs": ["VCC"], "text": "bend"}]


def test_parts_are_in_natural_order_with_their_nets_and_problems():
    rows = wb.parts_index(SNAP, FINDINGS, {"U1": {"comment": "LM358", "lib_ref": "OPAMP"}})
    assert [r["ref"] for r in rows] == ["R2", "R10", "U1"]
    r10 = rows[1]
    assert r10["nets"] == ["N1", "VCC"] and r10["pads"] == 2 and r10["problems"] == 1
    assert rows[0]["locked"] and rows[2]["comment"] == "LM358"


def test_nets_lead_with_what_is_left_to_route():
    rows = wb.nets_index(SNAP, FINDINGS)
    # GND has two pads but one on this board's parts here: one pad, a single.
    assert [(r["net"], r["status"]) for r in rows] == [
        ("N1", "open"), ("VCC", "routed"), ("GND", "single"), ("LONE", "single")]
    by = {r["net"]: r for r in rows}
    assert by["VCC"]["length"] == 50.0 and by["VCC"]["vias"] == 1 and by["VCC"]["problems"] == 1


def test_the_status_strip_counts_what_is_left():
    s = wb.summary(SNAP, FINDINGS)
    assert (s["nets"], s["routed"], s["missing_connections"]) == (3, 1, 2)
    assert (s["errors"], s["warnings"], s["parts"]) == (1, 1, 3)
    assert wb.summary(None, []) == {}


class Jobs:
    def __init__(self):
        self.state = {}
        self.results = {}

    def status(self, job):
        return {"status": self.state.get(job)} if job in self.state else None

    def result(self, job):
        return {"status": self.state[job], "result": self.results.get(job)}


def test_the_placer_is_proposed_then_applied():
    jobs, sent = Jobs(), []
    applied = threading.Event()

    def call(tool, args):
        sent.append((tool, args))
        if tool == "pcb_autoplace":
            jobs.state["job-1"] = "running"
            return {"job_id": "job-1"}
        applied.set()
        return {"moved": 1}

    e = wb.Engine(call=call, jobs=jobs)
    assert e.start("place", "C:/b/A.PcbDoc", parts=["R1"], compact=True)["ok"]
    assert sent[0] == ("pcb_autoplace", {"expect_file": "C:/b/A.PcbDoc", "parts": ["R1"],
                                         "compact": True})
    assert e.start("route", "C:/b/A.PcbDoc")["ok"] is False      # one at a time
    assert e.apply()["ok"] is False                               # nothing proposed yet
    assert e.snapshot()["state"] == "running"
    jobs.state["job-1"] = "done"
    jobs.results["job-1"] = {"moves": [{"designator": "R1", "x": 1, "y": 2, "rotation": 0}],
                             "summary": {"hpwl_before": 900.0, "hpwl_after": 600.0},
                             "notes": ["n"]}
    snap = e.snapshot()
    assert snap["state"] == "proposed" and "preview" not in snap
    assert snap["summary"]["moves"] == 1 and snap["summary"]["wirelength_after"] == 600.0
    assert e.run["preview"]["moves"][0]["designator"] == "R1"
    assert e.apply()["ok"]
    assert applied.wait(2)
    for _ in range(100):
        if e.run["state"] == "applied":
            break
        threading.Event().wait(0.01)
    assert e.run["state"] == "applied"
    assert sent[-1] == ("pcb_autoplace_apply", {"job_id": "job-1", "expect_file": "C:/b/A.PcbDoc"})


def test_the_route_preview_is_drawable_and_a_failed_job_is_reported():
    jobs = Jobs()

    def call(tool, args):
        jobs.state["job-2"] = "running"
        return {"job_id": "job-2"}

    e = wb.Engine(call=call, jobs=jobs)
    assert e.start("route", "B.PcbDoc", nets=["N1"])["ok"]
    jobs.state["job-2"] = "done"
    jobs.results["job-2"] = {
        "tracks": [{"x1": 0, "y1": 0, "x2": 10, "y2": 0, "width": 8, "layer": "Top",
                    "net_name": "N1"}],
        "vias": [{"x": 10, "y": 0, "size": 24, "hole_size": 12, "net": "N1"}],
        "summary": {"completion": 1.0, "unrouted_nets": 0}, "unrouted": []}
    assert e.snapshot()["summary"]["tracks"] == 1
    assert e.run["preview"]["tracks"][0] == {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "w": 8,
                                             "layer": "Top", "net": "N1"}
    assert e.run["preview"]["vias"][0]["d"] == 24
    assert e.drop()["ok"] and e.run == {}

    assert e.start("route", "B.PcbDoc")["ok"]
    jobs.state["job-2"] = "error"
    assert e.snapshot()["state"] == "failed"


def test_a_refused_start_says_why():
    e = wb.Engine(call=lambda t, a: {"error": "board read failed"}, jobs=Jobs())
    r = e.start("place", "A.PcbDoc")
    assert r == {"ok": False, "reason": "board read failed"}
    assert e.start("paint", "A.PcbDoc")["ok"] is False
