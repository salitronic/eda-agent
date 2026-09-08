# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read a plan back off a schematic the engine did not draw.

The layout engine takes a DesignPlan. Everything it can do to a sheet it
drew (re-lay-out, edit, score) is out of reach for a sheet somebody else
drew, because there is no plan for it. This builds one from what is
actually on the sheet, and is explicit about the parts of a plan that a
schematic does not record.
"""
from __future__ import annotations

import pytest

from eda_agent.design.orchestrator import plan_from_live_sheet


class _Sheet:
    """A bridge answering the three reads the reconstruction makes."""

    def __init__(self, components=None, power=None, pins=None):
        self.components = components if components is not None else [
            {"Designator.Text": "R1", "LibReference": "RES",
             "SourceLibraryName": "LIB.SchLib", "Comment.Text": "10k"},
            {"Designator.Text": "R2", "LibReference": "RES",
             "SourceLibraryName": "LIB.SchLib", "Comment.Text": "20k"},
        ]
        self.power = power if power is not None else []
        self.pins = pins if pins is not None else [
            {"component": "R1", "pin": "2", "net": "MID"},
            {"component": "R2", "pin": "1", "net": "MID"},
        ]
        self.sent: list[tuple] = []

    def send_command(self, command, params=None, **kw):
        self.sent.append((command, params or {}))
        if command == "project.get_nets":
            return {"pins": self.pins}
        obj_type = (params or {}).get("object_type")
        if obj_type == "eSchComponent":
            return {"objects": self.components}
        if obj_type == "ePowerObject":
            return {"objects": self.power}
        return {}


def test_the_sheet_becomes_a_plan_the_pipeline_accepts():
    out = plan_from_live_sheet("p.PrjPcb", bridge=_Sheet())
    assert out["ok"] is True
    plan = out["plan"]
    assert [p["refdes"] for p in plan["parts"]] == ["R1", "R2"]
    assert plan["parts"][0]["lib_ref"] == "RES"
    assert plan["parts"][0]["value"] == "10k"
    assert plan["nets"] == [{"name": "MID",
                             "pins": [{"refdes": "R1", "pin": "2"},
                                      {"refdes": "R2", "pin": "1"}]}]
    # It validates. A reconstruction that does not is a refusal, not a
    # plan the caller discovers is broken one tool call later.
    from eda_agent.design.plan import DesignPlan
    DesignPlan.model_validate(plan)


def test_the_missing_roles_are_stated_not_guessed():
    """A role is a planner assertion, and inventing one is a wrong answer.

    The placer keys motifs, the decoupling law and the priors on roles,
    so a caller who does not know they are gone gets a worse layout and
    no reason for it.
    """
    out = plan_from_live_sheet("p.PrjPcb", bridge=_Sheet())
    assert all("role" not in p for p in out["plan"]["parts"])
    assert any("role" in n for n in out["notes"])


def test_a_rail_is_read_off_its_glyph_not_its_name():
    """A net called GND is a convention; a ground glyph is evidence."""
    sheet = _Sheet(
        power=[{"Text": "VCC", "Style": "2"}, {"Text": "GND", "Style": "4"}],
        pins=[{"component": "R1", "pin": "1", "net": "VCC"},
              {"component": "R2", "pin": "1", "net": "VCC"},
              {"component": "R1", "pin": "2", "net": "GND"},
              {"component": "R2", "pin": "2", "net": "GND"}],
    )
    nets = {n["name"]: n for n in plan_from_live_sheet(
        "p.PrjPcb", bridge=sheet)["plan"]["nets"]}
    assert nets["VCC"].get("is_power") is True
    assert "is_ground" not in nets["VCC"]
    assert nets["GND"].get("is_ground") is True
    assert "is_power" not in nets["GND"]


def test_a_glyph_outranks_the_name_when_the_two_disagree():
    """The name is what somebody typed; the glyph is what is drawn.

    Both cases below are ordinary: a sense return called GND_SENSE is a
    supply-side net on many sheets, and a negative rail is still drawn
    with a ground glyph.
    """
    sheet = _Sheet(
        power=[{"Text": "GND_SENSE", "Style": "2"},
               {"Text": "VEE", "Style": "4"}],
        pins=[{"component": "R1", "pin": "1", "net": "GND_SENSE"},
              {"component": "R2", "pin": "1", "net": "GND_SENSE"},
              {"component": "R1", "pin": "2", "net": "VEE"},
              {"component": "R2", "pin": "2", "net": "VEE"}],
    )
    nets = {n["name"]: n for n in plan_from_live_sheet(
        "p.PrjPcb", bridge=sheet)["plan"]["nets"]}
    assert nets["GND_SENSE"].get("is_power") is True
    assert "is_ground" not in nets["GND_SENSE"]
    assert nets["VEE"].get("is_ground") is True
    assert "is_power" not in nets["VEE"]


def test_a_rail_named_like_a_rail_but_drawn_as_a_signal_stays_a_signal():
    sheet = _Sheet(power=[], pins=[{"component": "R1", "pin": "1",
                                    "net": "VCC"},
                                   {"component": "R2", "pin": "1",
                                    "net": "VCC"}])
    net = plan_from_live_sheet("p.PrjPcb", bridge=sheet)["plan"]["nets"][0]
    assert "is_power" not in net and "is_ground" not in net


def test_a_single_pin_net_is_reported_not_silently_dropped():
    """Net.pins needs two, so the pin has nowhere to go in a plan.

    A connection that vanishes with no note reads to the caller as a
    wiring change the reconstruction made.
    """
    sheet = _Sheet(pins=[{"component": "R1", "pin": "2", "net": "MID"},
                         {"component": "R2", "pin": "1", "net": "MID"},
                         {"component": "R1", "pin": "1", "net": "LONELY"}])
    out = plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    assert [n["name"] for n in out["plan"]["nets"]] == ["MID"]
    assert out["dropped_pins"] == ["R1.1 on LONELY"]


def test_a_net_name_the_plan_cannot_hold_takes_its_pins_with_it():
    sheet = _Sheet(pins=[{"component": "R1", "pin": "2", "net": "MID"},
                         {"component": "R2", "pin": "1", "net": "MID"},
                         {"component": "R1", "pin": "1", "net": "3V3 RAIL"},
                         {"component": "R2", "pin": "2", "net": "3V3 RAIL"}])
    out = plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    assert [n["name"] for n in out["plan"]["nets"]] == ["MID"]
    assert len(out["dropped_pins"]) == 2
    assert any("3V3 RAIL" in n for n in out["notes"])


def test_a_part_on_another_sheet_is_not_wired_into_this_plan():
    """get_nets is project-wide; the reconstruction is one sheet."""
    sheet = _Sheet(pins=[{"component": "R1", "pin": "2", "net": "MID"},
                         {"component": "R2", "pin": "1", "net": "MID"},
                         {"component": "U9", "pin": "4", "net": "MID"}])
    net = plan_from_live_sheet("p.PrjPcb", bridge=sheet)["plan"]["nets"][0]
    assert [p["refdes"] for p in net["pins"]] == ["R1", "R2"]


def test_a_component_with_no_lib_ref_is_skipped_with_its_name():
    sheet = _Sheet(components=[
        {"Designator.Text": "R1", "LibReference": "RES"},
        {"Designator.Text": "R2", "LibReference": "RES"},
        {"Designator.Text": "R3", "LibReference": ""},
    ])
    out = plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    assert [p["refdes"] for p in out["plan"]["parts"]] == ["R1", "R2"]
    assert any("R3" in n for n in out["notes"])


def test_an_unreadable_designator_does_not_reach_the_plan():
    """The schema takes R1, not "R?" or a blank; validation is not the
    place to find that out, because it refuses the whole plan."""
    sheet = _Sheet(components=[
        {"Designator.Text": "R1", "LibReference": "RES"},
        {"Designator.Text": "R2", "LibReference": "RES"},
        {"Designator.Text": "R?", "LibReference": "RES"},
    ])
    out = plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    assert out["ok"] is True
    assert [p["refdes"] for p in out["plan"]["parts"]] == ["R1", "R2"]


def test_an_empty_sheet_is_refused_not_returned_as_an_empty_plan():
    out = plan_from_live_sheet("p.PrjPcb", bridge=_Sheet(components=[]))
    assert out["ok"] is False and out["plan"] is None
    # And it says the sheet had no components. Falling through to the
    # net check would blame the netlist for an empty sheet.
    assert any("no components" in n for n in out["notes"])


def test_a_sheet_with_no_two_pin_net_is_refused():
    sheet = _Sheet(pins=[{"component": "R1", "pin": "1", "net": "A"}])
    out = plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    assert out["ok"] is False and out["plan"] is None


def test_the_engine_sheet_name_round_trips():
    """The engine writes <project stem>__<sheet>.SchDoc.

    Keeping the plan-side name means re-emitting the reconstructed plan
    lands on the sheet it was read from, not a second one beside it.
    """
    out = plan_from_live_sheet(r"C:\x\Foo.PrjPcb",
                               r"C:\x\Foo__power.SchDoc",
                               bridge=_Sheet())
    assert out["plan"]["sheets"] == [{"name": "power"}]
    assert out["plan"]["parts"][0]["sheet"] == "power"


def test_a_foreign_sheet_keeps_its_own_name():
    out = plan_from_live_sheet(r"C:\x\Foo.PrjPcb", r"C:\x\Legacy.SchDoc",
                               bridge=_Sheet())
    assert out["plan"]["sheets"] == [{"name": "Legacy"}]


def test_the_named_sheet_is_what_is_read():
    sheet = _Sheet()
    plan_from_live_sheet("p.PrjPcb", r"C:\x\Legacy.SchDoc", bridge=sheet)
    scopes = {p.get("scope") for c, p in sheet.sent if c.endswith("query_objects")}
    assert scopes == {r"doc:C:\x\Legacy.SchDoc"}


def test_a_failed_read_refuses_rather_than_returning_a_short_plan():
    """Half a sheet reconstructs into a plan that deletes the other half."""
    class _Broken(_Sheet):
        def send_command(self, command, params=None, **kw):
            self.sent.append((command, params or {}))
            if (params or {}).get("object_type") == "eSchComponent":
                raise RuntimeError("document is not open")
            return super().send_command(command, params, **kw)

    bridge = _Broken()
    out = plan_from_live_sheet("p.PrjPcb", bridge=bridge)
    assert out["ok"] is False and out["plan"] is None
    assert "project.get_nets" not in [c for c, _ in bridge.sent], (
        "a failed read stops; it does not go on to compile the project")
    # And it blames the read, not the sheet. "no components read" sends
    # the caller to look at a sheet that is not the problem.
    assert any("reading the components failed" in n for n in out["notes"])
    assert not any("no components read" in n for n in out["notes"])


def test_two_parts_with_one_designator_is_a_refusal():
    """A duplicate designator is a real sheet fault, not a rare one.

    Both parts cannot be expressed, and keeping whichever came first
    loses the other silently. The plan schema refuses it, which is the
    validation pass earning its place.
    """
    sheet = _Sheet(components=[
        {"Designator.Text": "R1", "LibReference": "RES"},
        {"Designator.Text": "R2", "LibReference": "RES"},
        {"Designator.Text": "R1", "LibReference": "CAP"},
    ])
    out = plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    assert out["ok"] is False and out["plan"] is None
    assert any("R1" in n for n in out["notes"])


def test_a_pin_listed_twice_on_a_net_is_one_connection():
    """Duplicate endpoints are refused by the schema; they are not a
    second connection, so they collapse instead of failing the plan."""
    sheet = _Sheet(pins=[{"component": "R1", "pin": "2", "net": "MID"},
                         {"component": "R1", "pin": "2", "net": "MID"},
                         {"component": "R2", "pin": "1", "net": "MID"}])
    out = plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    assert out["ok"] is True
    assert out["plan"]["nets"][0]["pins"] == [{"refdes": "R1", "pin": "2"},
                                              {"refdes": "R2", "pin": "1"}]


def test_a_failed_netlist_read_refuses_too():
    class _Broken(_Sheet):
        def send_command(self, command, params=None, **kw):
            if command == "project.get_nets":
                raise RuntimeError("project will not compile")
            return super().send_command(command, params, **kw)

    out = plan_from_live_sheet("p.PrjPcb", bridge=_Broken())
    assert out["ok"] is False and out["plan"] is None
    assert any("netlist" in n for n in out["notes"])


def test_unreadable_power_glyphs_cost_the_rails_not_the_plan():
    class _Broken(_Sheet):
        def send_command(self, command, params=None, **kw):
            if (params or {}).get("object_type") == "ePowerObject":
                raise RuntimeError("no")
            return super().send_command(command, params, **kw)

    out = plan_from_live_sheet("p.PrjPcb", bridge=_Broken())
    assert out["ok"] is True
    assert any("power port" in n for n in out["notes"])


def test_no_bridge_is_a_refusal_not_a_crash():
    out = plan_from_live_sheet("p.PrjPcb", bridge=None)
    if out["ok"]:                       # a live bridge answered; skip
        pytest.skip("a bridge is attached in this environment")
    assert out["plan"] is None


def test_a_plan_survives_the_round_trip_it_is_for():
    """Plan -> sheet -> plan, on everything a schematic records.

    The per-piece tests each check one mapping. This is the claim the
    tool actually makes: hand it a sheet the engine drew from a plan and
    the plan comes back, minus only what a schematic does not store.
    """
    plan = {
        "spec": "divider with a rail", "summary": "three parts",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": "L.SchLib",
             "value": "10k", "sheet": "main", "role": "rtop"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": "L.SchLib",
             "value": "1k", "sheet": "main", "role": "rbot"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": "L.SchLib",
             "value": "100n", "sheet": "main", "role": "cout"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "R1", "pin": "1"},
                      {"refdes": "C1", "pin": "1"}]},
            {"name": "MID",
             "pins": [{"refdes": "R1", "pin": "2"},
                      {"refdes": "R2", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "R2", "pin": "2"},
                      {"refdes": "C1", "pin": "2"}]},
        ],
    }

    # The sheet such a plan produces: one component per part carrying its
    # library reference and value, one power glyph per rail, and the
    # compiled netlist.
    sheet = _Sheet(
        components=[{"Designator.Text": p["refdes"],
                     "LibReference": p["lib_ref"],
                     "SourceLibraryName": p["lib_path"],
                     "Comment.Text": p["value"]} for p in plan["parts"]],
        power=[{"Text": "VCC", "Style": "2"}, {"Text": "GND", "Style": "4"}],
        pins=[{"component": pin["refdes"], "pin": pin["pin"],
               "net": net["name"]}
              for net in plan["nets"] for pin in net["pins"]],
    )

    back = plan_from_live_sheet("p.PrjPcb", bridge=sheet)["plan"]

    assert ([{k: v for k, v in p.items() if k != "role"}
             for p in plan["parts"]] == back["parts"])
    assert {n["name"]: (n.get("is_power"), n.get("is_ground"))
            for n in plan["nets"]} == {
                n["name"]: (n.get("is_power"), n.get("is_ground"))
                for n in back["nets"]}
    assert {n["name"]: sorted((p["refdes"], p["pin"]) for p in n["pins"])
            for n in plan["nets"]} == {
                n["name"]: sorted((p["refdes"], p["pin"]) for p in n["pins"])
                for n in back["nets"]}
    # The one thing that does not survive, stated rather than implied.
    assert all("role" not in p for p in back["parts"])


class _WithCompiled(_Sheet):
    """A sheet whose project also answers the compiled component read."""

    def __init__(self, components_info, **kw):
        super().__init__(**kw)
        self.components_info = components_info

    def send_command(self, command, params=None, **kw):
        if command == "project.get_component_info_batch":
            self.sent.append((command, params or {}))
            return {"components": self.components_info}
        return super().send_command(command, params, **kw)


def test_the_value_comes_from_the_parameter_this_engine_stamps():
    """The engine writes the value into a parameter called Value.

    The object query can only read the Comment, so reconstructing from
    the Comment alone gives a plan with no values for every sheet this
    engine drew, and laying that plan out again strips them off.
    """
    sheet = _WithCompiled(
        components_info=[
            {"designator": "R1", "comment": "", "footprint": "0603",
             "parameters": {"Value": "10k",
                            "Manufacturer Part Number": "M1",
                            "Manufacturer": "Acme"}},
            {"designator": "R2", "comment": "", "parameters": {}},
        ],
        components=[{"Designator.Text": "R1", "LibReference": "RES"},
                    {"Designator.Text": "R2", "LibReference": "RES"}],
    )
    parts = {p["refdes"]: p for p in
             plan_from_live_sheet("p.PrjPcb", bridge=sheet)["plan"]["parts"]}
    assert parts["R1"]["value"] == "10k"
    assert parts["R1"]["footprint"] == "0603"
    assert parts["R1"]["mpn"] == "M1"
    assert parts["R1"]["manufacturer"] == "Acme"
    assert "value" not in parts["R2"]


def test_a_hand_drawn_sheet_keeps_its_comment_as_the_value():
    """No Value parameter is the ordinary case on a hand-drawn sheet."""
    sheet = _WithCompiled(
        components_info=[{"designator": "R1", "comment": "4k7",
                          "parameters": {}},
                         {"designator": "R2", "comment": "1k",
                          "parameters": {}}],
        components=[{"Designator.Text": "R1", "LibReference": "RES"},
                    {"Designator.Text": "R2", "LibReference": "RES"}],
    )
    parts = {p["refdes"]: p for p in
             plan_from_live_sheet("p.PrjPcb", bridge=sheet)["plan"]["parts"]}
    assert parts["R1"]["value"] == "4k7"


def test_the_parameter_wins_over_a_stale_comment():
    sheet = _WithCompiled(
        components_info=[{"designator": "R1", "comment": "RES 0603",
                          "parameters": {"Value": "10k"}},
                         {"designator": "R2", "comment": "1k",
                          "parameters": {}}],
        components=[{"Designator.Text": "R1", "LibReference": "RES"},
                    {"Designator.Text": "R2", "LibReference": "RES"}],
    )
    parts = {p["refdes"]: p for p in
             plan_from_live_sheet("p.PrjPcb", bridge=sheet)["plan"]["parts"]}
    assert parts["R1"]["value"] == "10k"


def test_a_project_that_will_not_answer_costs_the_values_not_the_plan():
    class _Broken(_Sheet):
        def send_command(self, command, params=None, **kw):
            if command == "project.get_component_info_batch":
                raise RuntimeError("will not compile")
            return super().send_command(command, params, **kw)

    out = plan_from_live_sheet("p.PrjPcb", bridge=_Broken())
    assert out["ok"] is True
    assert any("compiled component data" in n for n in out["notes"])
    # The sheet query's own Comment survives as the fallback.
    assert out["plan"]["parts"][0]["value"] == "10k"


def test_the_netlist_is_read_fresh_not_from_the_last_compile():
    """This reads a sheet somebody has just been working on.

    A wire drawn since the last compile is missing from a cached
    netlist, and the plan that comes back looks perfectly ordinary
    without it.
    """
    sheet = _Sheet()
    plan_from_live_sheet("p.PrjPcb", bridge=sheet)
    nets = [p for c, p in sheet.sent if c == "project.get_nets"]
    assert nets and nets[0].get("force_recompile") == "true"


class _WithSize(_Sheet):
    def __init__(self, info, **kw):
        super().__init__(**kw)
        self.info = info

    def send_command(self, command, params=None, **kw):
        if command == "generic.get_document_info":
            self.sent.append((command, params or {}))
            return self.info
        return super().send_command(command, params, **kw)


def test_the_paper_the_sheet_is_drawn_on_reaches_the_plan():
    """19% of real sheets are bigger than A4.

    Laying one of those out as A4 confines every part to a band they
    cannot be separated in, so the size is worth a round trip.
    """
    sheet = _WithSize({"file_path": "C:/x/Legacy.SchDoc", "sheet_size": "A3"})
    out = plan_from_live_sheet("p.PrjPcb", r"C:\x\Legacy.SchDoc",
                               bridge=sheet)
    assert out["plan"]["sheets"] == [{"name": "Legacy", "size": "A3"}]


def test_a_size_read_off_the_wrong_document_is_refused():
    """get_document_info honours no scope: it answers for whatever is
    active. Believing it would put another sheet's paper on this plan."""
    sheet = _WithSize({"file_path": "C:/x/SomethingElse.SchDoc",
                       "sheet_size": "A0"})
    out = plan_from_live_sheet("p.PrjPcb", r"C:\x\Legacy.SchDoc",
                               bridge=sheet)
    assert out["plan"]["sheets"] == [{"name": "Legacy"}]
    assert any("not the sheet asked for" in n for n in out["notes"])


def test_a_size_the_layout_cannot_draw_is_said_rather_than_used():
    sheet = _WithSize({"file_path": "C:/x/Legacy.SchDoc",
                       "sheet_size": "OrCAD_C"})
    out = plan_from_live_sheet("p.PrjPcb", r"C:\x\Legacy.SchDoc",
                               bridge=sheet)
    assert out["plan"]["sheets"] == [{"name": "Legacy"}]
    assert any("no drawing area" in n for n in out["notes"])


def test_an_a4_sheet_needs_no_note():
    sheet = _WithSize({"file_path": "C:/x/Legacy.SchDoc", "sheet_size": "A4"})
    out = plan_from_live_sheet("p.PrjPcb", r"C:\x\Legacy.SchDoc",
                               bridge=sheet)
    assert out["plan"]["sheets"] == [{"name": "Legacy", "size": "A4"}]
    assert not any("drawing area" in n for n in out["notes"])


def test_an_unreadable_document_info_says_the_size_is_a_default():
    """Silence here reads as "this sheet is A4", which is a claim."""
    class _Broken(_Sheet):
        def send_command(self, command, params=None, **kw):
            if command == "generic.get_document_info":
                raise RuntimeError("no document")
            return super().send_command(command, params, **kw)

    out = plan_from_live_sheet("p.PrjPcb", bridge=_Broken())
    assert out["ok"] is True
    assert out["plan"]["sheets"] == [{"name": "main"}]
    assert any("could not read the sheet size" in n for n in out["notes"])
