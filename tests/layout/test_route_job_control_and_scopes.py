# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A routing job that can be cancelled, stops at its time limit keeping
what it made, and says what it is doing; half a pair named for a round
brings the other half; a scope naming a pad by Name is evaluated; and a
part's own floating pin is not the far side of a barrier.
"""

from __future__ import annotations

import time

from eda_agent import job_control
from eda_agent.design.jobs import CANCELLED, DONE, JobStore
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule
from eda_agent.layout.route import creepage as C
from eda_agent.layout.route.job import route_job
from eda_agent.layout.rules import RuleSet
from eda_agent.tools.route import _with_partners

SMD = [PadCopper("TopLayer", "rect", 20, 20)]


def _board(pads, rules=()) -> LayoutBoard:
    base = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                 "Clearance Constraint (Gap=6mil) (All),(All)"),
            Rule("Width", "1", "All", "All", 1, True, {},
                 "Width Constraint (Min=5mil) (Max=30mil) (Preferred=6mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=base + list(rules))
    b.pads = [Pad(ref, name, x, y, net=net, copper=list(SMD)) for ref, name, x, y, net in pads]
    return b


# -- the job ---------------------------------------------------------------

def test_a_running_job_reports_progress_and_stops_when_cancelled():
    store = JobStore()

    def engine():
        n = 0
        while True:
            n += 1
            job_control.note(f"step {n}")
            time.sleep(0.01)

    jid = store.submit("test", engine)
    deadline = time.time() + 5
    while not (store.status(jid) or {}).get("progress") and time.time() < deadline:
        time.sleep(0.01)
    running = store.status(jid)
    assert running["status"] == "running" and running["progress"].startswith("step ")
    assert "elapsed_s" in running
    assert store.cancel(jid)["cancel_requested"]
    assert store.wait(jid, timeout=5)
    assert store.status(jid)["status"] == CANCELLED
    assert store.result(jid)["result"] is None


def test_a_job_waiting_for_a_worker_never_starts_once_cancelled():
    store = JobStore(max_workers=1)
    started = []
    hold = store.submit("test", lambda: time.sleep(0.3))
    later = store.submit("test", lambda: started.append(1))
    assert store.cancel(later)["status"] == CANCELLED
    store.wait(hold, timeout=5)
    store.wait(later, timeout=5)
    assert not started and store.status(later)["status"] == CANCELLED


def test_past_its_limit_the_engine_is_told_and_returns_what_it_has():
    store = JobStore()

    def engine():
        steps = 0
        while not job_control.over_time():
            steps += 1
            job_control.checkpoint()
            time.sleep(0.01)
        return {"steps": steps}

    jid = store.submit("test", engine, time_limit_s=0.2)
    assert store.wait(jid, timeout=5)
    st = store.status(jid)
    assert st["status"] == DONE and st["stopped_at_time_limit"] and st["time_limit_s"] == 0.2
    assert store.result(jid)["result"]["steps"] > 0


def test_an_engine_that_ignores_its_limit_is_stopped_at_half_as_long_again():
    store = JobStore()

    def engine():
        while True:
            job_control.checkpoint()
            time.sleep(0.01)

    jid = store.submit("test", engine, time_limit_s=0.2)
    assert store.wait(jid, timeout=5)
    st = store.status(jid)
    assert st["status"] == CANCELLED and "time limit" in st["error"]


def test_the_router_stops_negotiating_at_the_limit_and_keeps_its_routes():
    # One layer and two nets that cross: the negotiation goes on past its
    # first round, so it meets the limit.
    b = _board([("J1", "1", 100, 200, "A"), ("U1", "1", 400, 100, "A"),
                ("J1", "2", 100, 100, "B"), ("U1", "2", 400, 200, "B")])
    b.layers = [Layer("TopLayer", "signal", 0)]
    # Over the limit, under half as long again: the engine is told, not stopped.
    c = job_control.Control(time_limit_s=100.0)
    c.started -= 110.0
    job_control.bind(c)
    try:
        res = route_job({"board": b, "gloss": False, "stages": {"negotiate": True}})
    finally:
        job_control.bind(None)
    assert res["summary"]["stopped_at_time_limit"]
    assert res["summary"]["iterations"] == 1


def test_outside_a_job_the_controls_do_nothing():
    job_control.bind(None)
    job_control.checkpoint()
    job_control.note("x")
    assert not job_control.over_time()


# -- a round names half a pair -----------------------------------------------

def test_half_a_pair_named_brings_its_other_half():
    b = _board([("J1", "1", 100, 200, "USB_D_P"), ("U1", "1", 400, 200, "USB_D_P"),
                ("J1", "2", 100, 100, "USB_D_N"), ("U1", "2", 400, 100, "USB_D_N"),
                ("J1", "3", 100, 300, "SDA"), ("U1", "3", 400, 300, "SDA")])
    nets, added = _with_partners(b, ["USB_D_P", "SDA"])
    assert added == ["USB_D_N"] and set(nets) == {"USB_D_P", "USB_D_N", "SDA"}
    assert _with_partners(b, ["SDA"]) == (["SDA"], [])


# -- scopes ------------------------------------------------------------------

def test_a_scope_naming_a_pad_is_evaluated():
    rule = Rule("Clr_Side", "2", "Name = 'U9-2' Or InPad('U9-3')", "All", 1, True,
                {"gap": 40.0}, "Clearance Constraint (Gap=40mil)")
    b = _board([("U9", "2", 100, 200, "X"), ("U9", "3", 140, 200, "Y"),
                ("U9", "4", 180, 200, "Z")], [rule])
    rs = RuleSet.from_board(b)
    assert rs.unevaluated() == []
    rr = next(r for r in rs.rules if r.rule.name == "Clr_Side")
    pads = {p.name: p for p in b.pads}
    from eda_agent.layout.rules import _eval, ObjectFacts
    hit = [n for n in ("2", "3", "4")
           if _eval(rr.scope1, ObjectFacts("pad", pads[n].net, comp="U9", name=f"U9-{n}"))]
    assert hit == ["2", "3"]


def test_a_scope_property_not_evaluated_is_reported_not_guessed():
    rule = Rule("Odd", "3", "Width = '10'", "All", 1, True, {"gap": 40.0},
                "Clearance Constraint (Gap=40mil)")
    rs = RuleSet.from_board(_board([("U1", "1", 100, 100, "A")], [rule]))
    assert [u["rule"] for u in rs.unevaluated()] == ["Odd"]


# -- barriers ----------------------------------------------------------------

def test_a_floating_pin_of_the_sides_own_part_is_not_the_far_side():
    b = _board([("J1", "1", 100, 200, "ISLAND"), ("U9", "1", 300, 300, "ISLAND"),
                ("U9", "2", 140, 200, "")])
    assert C.measure(b, {"ISLAND"}, None, 100, 100) == {}
    # A floating pad of some other part still counts.
    b.pads.append(Pad("H1", "1", 140, 120, net="", copper=list(SMD)))
    assert C.measure(b, {"ISLAND"}, None, 100, 100)


# -- a pair into the side of a part facing away from the other end -----------

def test_a_pair_into_the_far_side_of_a_part_is_routed_round_it_not_swapped():
    from eda_agent.layout.model import Component
    from eda_agent.layout.route.lanes import _body, end_frame

    # The MCU's left-hand pins, the connector to its south-east: the pair
    # goes west, north and east into them, as a person lays it.
    b = _board([("J1", "1", 600, 60, "USB_D_P"), ("J1", "2", 620, 60, "USB_D_N"),
                ("U1", "34", 300, 300, "USB_D_P"), ("U1", "33", 300, 280, "USB_D_N")])
    b.outline = [(0, 0), (900, 0), (900, 600), (0, 600)]
    b.components = [Component("U1", x=400, y=290,
                              courtyard=[(290, 180), (510, 180), (510, 400), (290, 400)]),
                    Component("J1", x=610, y=30,
                              courtyard=[(560, 0), (660, 0), (660, 75), (560, 75)])]
    mcu = [p for p in b.pads if p.comp == "U1"]
    f, _ = end_frame(mcu, (610, 60), _body(b, mcu))
    assert round(f[0], 6) == -1.0 and abs(f[1]) < 1e-9, "the left-hand pins are left westward"
    res = route_job({"board": b, "gloss": False})
    pair = next(p for p in res["pairs"] if p["positive"] == "USB_D_P")
    assert pair["routed"], pair["reason"]
    assert res["summary"]["completion"] == 1.0


def _usb_c(apart):
    """A receptacle that carries each line twice, interleaved (B7 N, A6 P,
    A7 N, B6 P), and an ESD part ``apart`` mils in front of it."""
    from eda_agent.layout.model import Component
    y = 60 + apart
    b = _board([("J1", "B7", 580, 60, "USB_DC_N"), ("J1", "A6", 600, 60, "USB_DC_P"),
                ("J1", "A7", 620, 60, "USB_DC_N"), ("J1", "B6", 640, 60, "USB_DC_P"),
                ("U2", "1", 600, y, "USB_DC_P"), ("U2", "3", 640, y, "USB_DC_N")])
    for p in b.pads:
        if p.comp == "J1":
            p.copper[0].w, p.copper[0].h = 12, 40
    b.outline = [(0, 0), (900, 0), (900, 600), (0, 600)]
    b.components = [Component("J1", x=610, y=20,
                              courtyard=[(520, -60), (700, -60), (700, 85), (520, 85)]),
                    Component("U2", x=620, y=y + 40,
                              courtyard=[(580, y - 15), (660, y - 15), (660, y + 95),
                                         (580, y + 95)])]
    return b


def test_a_pair_from_an_interleaved_receptacle_is_routed_from_its_right_couple():
    res = route_job({"board": _usb_c(200), "gloss": False})
    pair = next(p for p in res["pairs"] if p["positive"] == "USB_DC_P")
    assert pair["routed"], pair["reason"]
    assert res["summary"]["completion"] == 1.0


def test_a_pair_too_short_to_fan_in_says_so_not_that_it_needs_a_swap():
    res = route_job({"board": _usb_c(100), "gloss": False})
    pair = next(p for p in res["pairs"] if p["positive"] == "USB_DC_P")
    assert not pair["routed"]
    assert "too close" in pair["reason"] and "100 mil apart" in pair["reason"]
    assert "J1.A6/A7" in pair["reason"]
    # Left to the router, the two nets are made all the same.
    assert res["summary"]["completion"] == 1.0


def test_a_step_within_its_own_seconds_stops_and_the_job_goes_on():
    def slow():
        while True:
            job_control.checkpoint()
            time.sleep(0.01)

    store = JobStore()

    def engine():
        try:
            job_control.within(0.1, slow)
        except job_control.OutOfTime:
            return {"went_on": True}

    jid = store.submit("test", engine, time_limit_s=60)
    assert store.wait(jid, timeout=5)
    assert store.result(jid)["result"] == {"went_on": True}


def test_cancelling_the_job_leaves_a_step_within_its_seconds_too():
    store = JobStore()

    def engine():
        def spin():
            while True:
                job_control.note("spinning")
                time.sleep(0.01)
        job_control.within(60, spin)

    jid = store.submit("test", engine)
    deadline = time.time() + 5
    while not (store.status(jid) or {}).get("progress") and time.time() < deadline:
        time.sleep(0.01)
    store.cancel(jid)
    assert store.wait(jid, timeout=5)
    assert store.status(jid)["status"] == CANCELLED


def test_netless_pins_a_clearance_rule_names_are_on_its_side_not_the_far_side():
    # An isolator's pins on one side: its nets on 0.635 mm pitch, and two
    # pins with no net that the rule names by Name to put them on that side.
    # Judged as the far side, those two would keep the rule's 50 mil and
    # wall in the pins beside them.
    s1 = "(InNetClass('ISO_SIDE') Or (Name = 'U5-12') Or (Name = 'U5-13'))"
    s2 = ("Not (InNetClass('ISO_SIDE') Or (Name = 'U5-12') Or (Name = 'U5-13')) "
          "And Not InNet('EARTH')")
    rules = [Rule("Clr_Iso", "2", s1, s2, 2, True, {"gap": 50},
                  "Clearance Constraint (Gap=50mil)"),
             Rule("Clearance", "0", "All", "All", 5, True, {"gap": 6},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=8mil) (Max=8mil) (Preferred=8mil) (All)")]
    b = LayoutBoard(name="t.PcbDoc", outline=[(0, 0), (900, 0), (900, 700), (0, 700)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    pitch = 25.0
    pin = lambda: [PadCopper("TopLayer", "rect", 60, 14)]          # noqa: E731
    big = lambda: [PadCopper("TopLayer", "rect", 50, 55)]          # noqa: E731
    field = {9: "ISO_COM", 10: "S_B", 11: "S_D", 12: "", 13: "", 14: "ISO_COM",
             15: "S_A", 16: "S_C"}
    pads = [Pad("U5", str(n), 300, 400 - k * pitch, net=field[n], copper=pin())
            for k, n in enumerate(range(9, 17))]
    pads += [Pad("U5", str(n), 520, 400 - k * pitch, net="GND" if n % 2 else "VCC",
                 copper=pin()) for k, n in enumerate(range(1, 9))]
    pads += [Pad("R2", "1", 180, 250, net="S_A", copper=big()),
             Pad("R2", "2", 105, 250, net="IN_A", copper=big()),
             Pad("R4", "1", 180, 375, net="S_B", copper=big()),
             Pad("R4", "2", 105, 375, net="IN_B", copper=big()),
             Pad("C1", "1", 200, 500, net="S_C", copper=big()),
             Pad("C2", "1", 200, 160, net="S_D", copper=big()),
             Pad("J1", "1", 60, 600, net="ISO_COM", copper=big())]
    b.pads = pads
    b.net_classes = {"ISO_SIDE": ["S_A", "S_B", "S_C", "S_D",
                                  "ISO_COM", "IN_A", "IN_B"]}
    assert RuleSet.from_board(b).named_pads("clearance") == {"u5-12", "u5-13"}
    res = route_job({"board": b, "nets": ["S_A", "S_B"],
                     "stages": {"axis_entry": True, "pin_lanes": True, "join_existing": True}})
    assert res["unrouted"] == {} and res["unreached"] == []
    assert res["summary"]["violations"] == 0
