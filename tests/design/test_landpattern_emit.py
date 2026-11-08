# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The land pattern emitters: what each backend is asked to draw.

Offline. The model comes from the datasheet cases the engine is tested
on; these check that the Altium and EasyEDA plans and the KiCad file
carry exactly what the model holds, on the layer each role belongs on,
and that the Altium readback check fails on any difference.
"""

from __future__ import annotations

import math

import pytest

from eda_agent.design.landpattern import SpecError
from eda_agent.design.landpattern.emit import (
    ALTIUM_UNIT_MM,
    LayerRoleError,
    altium_plan,
    easyeda_plan,
    model_from_package,
    resolve_altium_layers,
    verify_altium_readback,
)
from eda_agent.units import MILS_PER_MM
from tests.design import _landpattern_cases as cases

LAYERS = resolve_altium_layers([], assembly_layer="Mechanical13",
                               courtyard_layer="Mechanical15")


def _model(case: str, **kw):
    return model_from_package(getattr(cases, case)().to_dict(), **kw)


# --- the request -------------------------------------------------------------


def test_a_package_dict_and_its_json_build_the_same_model():
    spec = cases.soic8().to_dict()
    import json
    a = model_from_package(spec)
    b = model_from_package(json.dumps(spec))
    assert a.to_dict() == b.to_dict()
    assert a.density == "N" and len(a.pads) == 8


def test_a_bad_package_is_refused_with_the_specs_own_words():
    with pytest.raises(SpecError, match="needs pins, pitch"):
        model_from_package({"family": "soic"})
    with pytest.raises(SpecError, match="unknown family"):
        model_from_package({"family": "soic9"})
    with pytest.raises(SpecError, match="not valid JSON"):
        model_from_package("{family: soic")
    with pytest.raises(SpecError, match="empty"):
        model_from_package({})
    with pytest.raises(SpecError, match="unknown PackageSpec fields"):
        model_from_package({"family": "chip", "pad_w": 1})


def test_density_name_description_and_drafting_overrides():
    m = _model("soic8", density="M", name="MY_SOIC", description="test part",
               drafting={"silk_width": 0.15})
    assert m.density == "M" and m.name == "MY_SOIC"
    assert m.description.endswith("; test part")
    assert {g.width for g in m.layer("silk") if g.kind == "line"} == {0.15}
    with pytest.raises(SpecError, match="density must be"):
        _model("soic8", density="X")
    with pytest.raises(SpecError, match="unknown drafting rules: pad_gap"):
        _model("soic8", drafting={"pad_gap": 0.2})


# --- layers by role --------------------------------------------------------------


def _mech(num, name, kind="Not Set"):
    return {"layer": f"Mechanical{num}", "number": num, "name": name,
            "kind": kind}


def test_layers_are_found_by_kind_before_name():
    out = resolve_altium_layers([
        _mech(1, "Assembly"),                       # a name hint, but...
        _mech(13, "Mechanical 13", "Assembly Top"),  # ...the kind wins
        _mech(15, "Mechanical 15", "Courtyard Top"),
    ])
    assert out["assembly"]["layer"] == "Mechanical13"
    assert out["courtyard"]["layer"] == "Mechanical15"
    assert "kind" in out["assembly"]["rule"]
    assert out["silk"]["layer"] == "TopOverlay"
    assert out["paste"]["layer"] == "TopPaste"


def test_layers_are_found_by_name_with_the_audits_hints():
    out = resolve_altium_layers([
        _mech(2, "Assembly Bottom"), _mech(3, "Assy"),
        _mech(4, "Courtyard"), _mech(5, "Dimensions", "Dimensions"),
    ])
    # The bottom-side layer is skipped, not merely outranked by "top".
    assert out["assembly"]["layer"] == "Mechanical3"
    assert out["courtyard"]["layer"] == "Mechanical4"
    assert "name" in out["courtyard"]["rule"]


def test_no_role_layer_is_refused_and_lists_what_is_there():
    with pytest.raises(LayerRoleError) as err:
        resolve_altium_layers([_mech(1, "Mechanical 1")])
    msg = str(err.value)
    assert "Mechanical1 'Mechanical 1'" in msg and "assembly_layer" in msg


def test_two_candidates_with_no_top_are_refused():
    with pytest.raises(LayerRoleError, match="several layers"):
        resolve_altium_layers([_mech(6, "Assy A"), _mech(7, "Assy B"),
                               _mech(15, "Courtyard")])


def test_a_named_layer_wins_and_one_layer_cannot_take_both_roles():
    out = resolve_altium_layers([], assembly_layer="Mechanical21",
                                courtyard_layer="Mechanical25")
    assert out["assembly"]["rule"] == "named by the caller"
    with pytest.raises(LayerRoleError, match="separate layers"):
        resolve_altium_layers([], assembly_layer="Mechanical5",
                              courtyard_layer="mechanical5")


def test_the_hints_are_the_policy_audits_own():
    from eda_agent.design import footprint_policy as fp
    from eda_agent.design.landpattern import emit
    hints = emit._role_hints()
    assert hints["assembly"] is fp._ASSEMBLY_HINTS
    assert hints["courtyard"] is fp._COURTYARD_HINTS


# --- the Altium plan ---------------------------------------------------------------


def test_soic8_plan_carries_the_model_in_mm():
    m = _model("soic8")
    plan = altium_plan(m, LAYERS)
    assert len(plan["pads"]) == 8
    for sent, pad in zip(plan["pads"], m.pads):
        assert (sent["x"], sent["y"]) == (pad.x, pad.y)
        assert (sent["x_size"], sent["y_size"]) == (pad.width, pad.height)
        assert sent["layer"] == "TopLayer" and sent["hole_size"] == 0
        # Altium's percent is of HALF the short side, so ratio x 200.
        assert sent["corner_radius"] == round(pad.corner_ratio * 200)
        assert sent["shape"] == "roundrect"
        assert "paste_expansion" not in sent and "mask_expansion" not in sent
    layers = {t["layer"] for t in plan["tracks"]}
    assert layers == {"TopOverlay", "Mechanical13", "Mechanical15"}
    silk = [t for t in plan["tracks"] if t["layer"] == "TopOverlay"]
    assert len(silk) == len(m.layer("silk")) - 1   # the dot is an arc
    court = [t for t in plan["tracks"] if t["layer"] == "Mechanical15"]
    assert len(court) == 4
    assert plan["regions"] == []
    assert plan["height_mm"] == 1.75
    text = plan["texts"][-1]
    assert text["text"] == ".Designator" and text["layer"] == "Mechanical13"


def test_the_pin1_dot_is_a_filled_arc():
    m = _model("soic8")
    dot = next(g for g in m.layer("silk") if g.kind == "circle")
    plan = altium_plan(m, LAYERS)
    arc = next(a for a in plan["arcs"] if a["layer"] == "TopOverlay")
    assert arc["radius"] == pytest.approx(dot.r / 2)
    assert arc["width"] == pytest.approx(dot.r)
    assert (arc["start_angle"], arc["end_angle"]) == (0, 360)


def test_qfn_exposed_pad_paste_is_closed_and_windowed():
    m = _model("qfn16")
    ep = next(p for p in m.pads if p.role == "thermal")
    plan = altium_plan(m, LAYERS)
    sent = next(p for p in plan["pads"] if p["designator"] == ep.number)
    assert sent["paste_expansion"] == -max(ep.width, ep.height)
    signal = [p for p in plan["pads"] if p["designator"] != ep.number]
    assert all("paste_expansion" not in p for p in signal)
    assert len(plan["regions"]) == len(ep.paste_apertures) == 4
    for region, window in zip(plan["regions"], ep.paste_apertures):
        assert region["layer"] == "TopPaste"
        xs = [x for x, _ in region["points"]]
        ys = [y for _, y in region["points"]]
        assert max(xs) - min(xs) == pytest.approx(window.width)
        assert max(ys) - min(ys) == pytest.approx(window.height)
        assert (max(xs) + min(xs)) / 2 == pytest.approx(window.x)


def test_holes_slots_and_non_plated_holes():
    m = model_from_package({
        "family": "custom", "body_x": 6, "body_y": 4, "custom_pads": [
            {"number": "1", "x": -1.5, "y": 0, "width": 2.4, "height": 1.4,
             "shape": "oval", "hole": 0.8, "hole_length": 1.6},
            {"number": "MH", "x": 1.5, "y": 0, "width": 1.0, "height": 1.0,
             "shape": "circle", "hole": 1.0, "plated": False}]})
    plan = altium_plan(m, LAYERS)
    slot, mh = plan["pads"]
    assert slot["shape"] == "round" and slot["layer"] == "MultiLayer"
    assert slot["hole_size"] == 0.8 and slot["hole_length"] == 1.6
    assert "plated" not in slot and "paste_expansion" not in slot
    assert mh["plated"] == "false" and "hole_length" not in mh


def test_a_mask_expansion_is_written_only_when_asked():
    plan = altium_plan(_model("soic8", drafting={"mask_expansion": 0.05}),
                       LAYERS)
    assert {p["mask_expansion"] for p in plan["pads"]} == {0.05}
    assert not any("1:1" in n for n in plan["notes"])


def test_the_designator_note_gives_the_printed_height_and_the_rule():
    """The literal '.Designator' runs past a small body in the library
    editor; an agent once regenerated a correct footprint over it."""
    plan = altium_plan(_model("soic8"), LAYERS)
    ref = next(t for t in plan["texts"] if t["text"] == ".Designator")
    note = next(n for n in plan["notes"] if "'.Designator' placeholder" in n)
    assert f"{ref['size']:g} mm" in note
    assert "ref_height_max" in note


def test_the_designator_is_centred_on_the_body_and_fits_it():
    """Anchored at its lower left at the body centre, the eleven-character
    placeholder ran far off a 6.4 mm sensor in the 2D view of the demo."""
    from eda_agent.design.landpattern.emit import _STROKE_ADVANCE
    m = _model("soic8")
    plan = altium_plan(m, LAYERS)
    ref = next(t for t in plan["texts"] if t["text"] == ".Designator")
    width = _STROKE_ADVANCE * ref["size"] * len(".Designator")
    cx = (m.body.x0 + m.body.x1) / 2
    assert abs(ref["x"] + width / 2 - cx) < 1e-6
    assert abs(ref["y"] + ref["size"] / 2 - m.ref.y) < 1e-6
    assert width <= 0.9 * (m.body.x1 - m.body.x0) + 1e-6 or ref["size"] == 0.3


def test_a_shape_altium_cannot_draw_is_refused():
    m = _model("custom_sot23_vendor")
    m.pads[0].shape = "trapezoid"
    with pytest.raises(SpecError, match="trapezoid"):
        altium_plan(m, LAYERS)


# --- the readback check ----------------------------------------------------------


def _readback(plan, **change):
    """What lib_get_pad_geometry would read for exactly what was sent."""
    shapes = {"roundrect": "roundrectangle", "rectangular": "rectangular",
              "round": "round", "octagonal": "octagonal"}
    pads = []
    for s in plan["pads"]:
        pads.append({
            "name": s["designator"], "x_mm": s["x"], "y_mm": s["y"],
            "w_mm": s["x_size"], "h_mm": s["y_size"],
            "shape": shapes[s["shape"]],
            "corner_pct": s["corner_radius"] if s["shape"] == "roundrect" else 0,
            "rotation": s["rotation"], "hole_mm": s["hole_size"],
            "hole_w_mm": s.get("hole_length", 0),
            "hole_type": "slot" if "hole_length" in s else "round",
            "plated": s.get("plated") != "false",
            "layer": "top" if s["layer"] == "TopLayer" else "multi",
            "paste_expansion_mm": s.get("paste_expansion", 0),
            "paste_expansion_source": "manual" if "paste_expansion" in s else "rule",
            "mask_expansion_mm": s.get("mask_expansion", 0),
            "mask_expansion_source": "manual" if "mask_expansion" in s else "rule",
        })
    for key, value in change.items():
        pads[0][key] = value
    return {"name": "X", "pad_count": len(pads), "pads": pads}


def test_a_faithful_readback_passes():
    plan = altium_plan(_model("qfn16"), LAYERS)
    out = verify_altium_readback(plan, _readback(plan))
    assert out["ok"], out["mismatches"]
    assert out["tolerance_mm"] == pytest.approx(2.54e-6)


def test_half_a_unit_passes_and_two_units_fail():
    # One Altium internal unit is 1/10000 mil, stated here independently
    # of the module's constant so a widened tolerance cannot follow along.
    unit = 0.0254 / 10000
    assert ALTIUM_UNIT_MM == pytest.approx(unit, rel=1e-12)
    plan = altium_plan(_model("soic8"), LAYERS)
    x0 = plan["pads"][0]["x"]
    assert verify_altium_readback(
        plan, _readback(plan, x_mm=x0 + 0.5 * unit))["ok"]
    out = verify_altium_readback(plan, _readback(plan, x_mm=x0 + 2 * unit))
    assert not out["ok"] and out["mismatches"][0]["check"] == "x"


@pytest.mark.parametrize("change,check", [
    ({"w_mm": 1.0}, "width"),
    ({"rotation": 90.0}, "rotation"),
    ({"shape": "rectangular"}, "shape"),
    ({"corner_pct": 25}, "corner_pct"),
    ({"layer": "bottom"}, "layer"),
    ({"name": "99"}, "pad_missing"),
])
def test_each_difference_is_a_failure(change, check):
    plan = altium_plan(_model("soic8"), LAYERS)
    out = verify_altium_readback(plan, _readback(plan, **change))
    assert not out["ok"]
    assert check in {m["check"] for m in out["mismatches"]}


def test_a_paste_expansion_that_did_not_take_fails():
    plan = altium_plan(_model("qfn16"), LAYERS)
    rb = _readback(plan)
    ep = next(p for p in rb["pads"] if p["paste_expansion_source"] == "manual")
    ep["paste_expansion_source"] = "rule"
    ep["paste_expansion_mm"] = 0
    out = verify_altium_readback(plan, rb)
    assert {"paste_expansion_source", "paste_expansion"} <= {
        m["check"] for m in out["mismatches"]}


def test_a_missing_or_extra_pad_and_no_list_fail():
    plan = altium_plan(_model("soic8"), LAYERS)
    rb = _readback(plan)
    rb["pads"].pop()
    out = verify_altium_readback(plan, rb)
    assert {"pad_count", "pad_missing"} <= {m["check"] for m in out["mismatches"]}
    rb = _readback(plan)
    rb["pads"].append(dict(rb["pads"][0], name="9"))
    assert "pad_extra" in {m["check"] for m in
                           verify_altium_readback(plan, rb)["mismatches"]}
    assert not verify_altium_readback(plan, {"error": "x"})["ok"]


def test_a_slot_that_came_back_round_fails():
    m = model_from_package({
        "family": "custom", "body_x": 6, "body_y": 4, "custom_pads": [
            {"number": "1", "x": 0, "y": 0, "width": 2.4, "height": 1.4,
             "shape": "oval", "hole": 0.8, "hole_length": 1.6}]})
    plan = altium_plan(m, LAYERS)
    out = verify_altium_readback(plan, _readback(plan, hole_type="round",
                                                 hole_w_mm=0))
    assert {"hole_type", "slot_length"} <= {m["check"] for m in out["mismatches"]}


# --- the EasyEDA plan --------------------------------------------------------------


def test_easyeda_plan_is_in_mils_on_easyedas_layers():
    m = _model("qfn16")
    plan = easyeda_plan(m)
    assert plan["unsupported"] == []
    p1, pad = plan["pads"][0], m.pads[0]
    assert p1["x"] == pytest.approx(pad.x * MILS_PER_MM)
    assert p1["width"] == pytest.approx(pad.width * MILS_PER_MM)
    assert p1["shape"] == "RECTANGLE" and p1["layer"] == "TOP"
    assert p1["corner_radius"] == pytest.approx(pad.corner_radius * MILS_PER_MM)
    layers = {pl["layer"] for pl in plan["polylines"]}
    assert layers == {"TOP_SILKSCREEN", "TOP_ASSEMBLY", "DOCUMENT"}
    paste = [f for f in plan["fills"] if f["layer"] == "TOP_PASTE_MASK"]
    assert len(paste) == 4
    assert plan["texts"][-1]["layer"] == "TOP_ASSEMBLY"


def test_easyeda_closed_outlines_get_their_closing_edge():
    """The extension drops a repeated closing point, so the last edge of
    a closed outline has to go as its own two-point run."""
    m = _model("soic8")
    plan = easyeda_plan(m)
    court = [pl for pl in plan["polylines"] if pl["layer"] == "DOCUMENT"]
    assert len(court) == 2
    ring, closing = court
    assert len(ring["points"]) == 4 and closing["points"] == [
        ring["points"][-1], ring["points"][0]]


def test_easyeda_refuses_what_the_extension_cannot_draw():
    m = model_from_package({
        "family": "custom", "body_x": 6, "body_y": 4, "custom_pads": [
            {"number": "MH", "x": 0, "y": 0, "width": 1.0, "height": 1.0,
             "shape": "circle", "hole": 1.0, "plated": False}]})
    assert any("non-plated" in u for u in easyeda_plan(m)["unsupported"])
    m = _model("soic8", drafting={"mask_expansion": 0.05})
    assert any("expansion" in u for u in easyeda_plan(m)["unsupported"])


def test_easyeda_pin1_dot_is_a_filled_polygon_round_its_centre():
    m = _model("soic8")
    dot = next(g for g in m.layer("silk") if g.kind == "circle")
    plan = easyeda_plan(m)
    fill = next(f for f in plan["fills"] if f["layer"] == "TOP_SILKSCREEN")
    for x, y in fill["points"]:
        assert math.hypot(x - dot.x * MILS_PER_MM, y - dot.y * MILS_PER_MM) == \
            pytest.approx(dot.r * MILS_PER_MM, rel=1e-6)
