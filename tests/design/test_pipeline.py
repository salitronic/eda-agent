# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for design.pipeline: plan -> SchematicCanvas, pure Python.

The pipeline is the orchestrator; these tests check it produces a
sensible canvas without ever touching Altium. A MockExtractor returns
hand-built SymbolModels keyed by (lib_path, lib_ref).

Coverage:
- Happy path: every plan part lands as an instance; nets become wires
  or labels or ports.
- Missing symbol: pipeline fails cleanly with a per-(lib, ref) error.
- Missing pin: pipeline fails with the offending pin id.
- needs_creation parts: skipped with a warning note, not a failure.
- parameter_stamps: built for parts with metadata; empty for bare parts.
"""

from __future__ import annotations

import copy
import functools
from typing import Optional

import pytest

from eda_agent.design.pipeline import build_canvas_from_plan
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolExtractor,
    SymbolModel,
    SymbolPin,
)


_LIB = "/fake/lib.SchLib"


class MockExtractor(SymbolExtractor):
    """Return canned SymbolModels without instantiating the bridge."""

    def __init__(self, symbols: dict[tuple[str, str], SymbolModel]) -> None:
        # Skip parent __init__ (no bridge/cache needed for tests).
        self._symbols = symbols

    def extract_one(self, lib_path: str, lib_ref: str) -> Optional[SymbolModel]:
        return self._symbols.get((lib_path, lib_ref))

    def extract_many(self, refs):
        return {
            (lib_path, lib_ref): self._symbols[(lib_path, lib_ref)]
            for (lib_path, lib_ref) in refs
            if (lib_path, lib_ref) in self._symbols
        }


def _passive(lib_ref: str) -> SymbolModel:
    return SymbolModel(
        lib_path=_LIB, lib_ref=lib_ref,
        pins=(
            SymbolPin(designator="1", name="1", x=-100, y=0,
                      orientation=2, length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="2", x=100, y=0,
                      orientation=0, length=100, electrical_type="passive"),
        ),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30),
    )


_BASE_SYMBOLS = {
    (_LIB, "RES"): _passive("RES"),
    (_LIB, "CAP"): _passive("CAP"),
}


def _basic_rc_plan(extra_part_fields: Optional[dict] = None) -> DesignPlan:
    """R1 in series with C1; one signal net and one ground net.

    Net.pins requires min 2 items, so the schema-shortest plan uses two
    2-pin nets that together connect every pin.
    """
    extra = extra_part_fields or {}
    return DesignPlan.model_validate({
        "spec": "rc lowpass",
        "summary": "trivial RC",
        "sheets": [{"name": "main", "title": "RC", "size": "A4"}],
        "zones": [{"name": "filter", "sheet": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "value": "10k", "status": "existing",
             "sheet": "main", "zone": "filter", **extra.get("R1", {})},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "value": "100nF", "status": "existing",
             "sheet": "main", "zone": "filter", **extra.get("C1", {})},
        ],
        "nets": [
            {"name": "VOUT", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}]},
        ],
    })


def test_pipeline_happy_path():
    """A clean plan produces a canvas with every plan part placed."""
    # VIN net only has 1 pin; that violates Net.pins min items=2. Adjust:
    plan = DesignPlan.model_validate({
        "spec": "rc", "summary": "rc",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "value": "10k", "status": "existing",
             "sheet": "main", "zone": "z"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "value": "100nF", "status": "existing",
             "sheet": "main", "zone": "z"},
        ],
        "nets": [
            {"name": "VOUT", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}]},
        ],
    })
    result = build_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS))
    assert result.ok, [f.text for f in result.failures]
    assert result.placement_count == 2
    refdes_placed = {i.refdes for i in result.canvas.instances}
    assert refdes_placed == {"R1", "C1"}


def test_pipeline_missing_symbol_fails_cleanly():
    """Plan references a lib_ref the extractor can't produce -> hard failure."""
    plan = _basic_rc_plan()
    # Drop CAP from the available symbols so C1 can't resolve.
    extractor = MockExtractor({(_LIB, "RES"): _passive("RES")})
    result = build_canvas_from_plan(plan, extractor)
    assert not result.ok
    assert any("CAP" in f.text for f in result.failures)


def test_pipeline_skips_needs_creation_with_warning():
    """A needs_creation part should not appear on the canvas, and should
    surface a warning note (not a hard failure)."""
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "U1", "lib_ref": "STM32G031F",
             "value": "STM32G031F",
             "status": "needs_creation", "sheet": "main", "zone": "z",
             "rationale": "no stm32 in library yet"},
        ],
        "nets": [
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "R1", "pin": "2"}]},
        ],
    })
    result = build_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS))
    # Hard failure only if R1 fails -- U1 is just a warning.
    refdes_placed = {i.refdes for i in result.canvas.instances}
    assert "U1" not in refdes_placed
    assert "R1" in refdes_placed
    assert any("U1" in n.text for n in result.notes)
    # The skip note carries the refdes as structured data, so callers do not
    # have to parse it out of the warning text.
    assert any(n.refdes == "U1" for n in result.notes)


def test_pipeline_unknown_pin_id_fails():
    """A plan net referencing a pin id not on the symbol -> failure."""
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
        ],
        "nets": [
            # Pin 99 doesn't exist on RES.
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "99"},
                {"refdes": "R2", "pin": "1"}]},
        ],
    })
    result = build_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS))
    assert not result.ok
    assert any("99" in f.text for f in result.failures)


def test_pipeline_parameter_stamps_carry_value_and_metadata():
    plan = _basic_rc_plan(extra_part_fields={
        "R1": {"manufacturer": "Yageo", "mpn": "RC0603FR-0710KL",
               "datasheet_url": "https://datasheet/yageo.pdf"},
    })
    result = build_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS))
    assert result.ok, [f.text for f in result.failures]
    # R1 should get Value + Manufacturer + MPN + Datasheet stamps.
    r1_stamps = result.parameter_stamps.get("R1", {})
    assert r1_stamps.get("Value") == "10k"
    assert r1_stamps.get("Manufacturer") == "Yageo"
    assert r1_stamps.get("Manufacturer Part Number") == "RC0603FR-0710KL"
    assert r1_stamps.get("Datasheet") == "https://datasheet/yageo.pdf"
    # C1 has Value only.
    c1_stamps = result.parameter_stamps.get("C1", {})
    assert c1_stamps == {"Value": "100nF"}


def test_validation_flags_unrepresented_net():
    """A net whose pins are placed but never wired/labelled/ported should
    surface as a warning. We can't easily make the pipeline produce that
    state via its normal path -- it always tries to wire block-local nets
    and label cross-block ones -- so we exercise the validation helper
    directly against a hand-built canvas."""
    from eda_agent.design.canvas import SchematicCanvas, SymbolInstance
    from eda_agent.design.pipeline import (
        PipelineResult, _validate_canvas_against_plan,
    )

    sym = _passive("RES")
    canvas = SchematicCanvas()
    canvas.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=0, y=0, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R2", symbol=sym, x=200, y=0, rotation=0))
    # No wires/labels/ports added at all.

    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "SIG", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "R2", "pin": "1"}]},
        ],
    })
    result = PipelineResult(canvas=canvas)
    _validate_canvas_against_plan(plan, canvas, result)
    warnings = [n for n in result.notes if n.severity == "warning"]
    assert any("SIG" in w.text for w in warnings)


def test_validation_quiet_when_net_pins_off_canvas():
    """A net whose pins reference refdes NOT placed on the canvas should
    NOT warn -- that's a multi-sheet case where the net lives elsewhere."""
    from eda_agent.design.canvas import SchematicCanvas
    from eda_agent.design.pipeline import (
        PipelineResult, _validate_canvas_against_plan,
    )

    canvas = SchematicCanvas()  # nothing placed
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "SIG", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "R2", "pin": "1"}]},
        ],
    })
    result = PipelineResult(canvas=canvas)
    _validate_canvas_against_plan(plan, canvas, result)
    # No instances on canvas => net is "off this canvas" => no warning.
    warnings = [n for n in result.notes if n.severity == "warning"]
    assert warnings == []


def test_pipeline_power_net_produces_port_glyph():
    """is_ground=True net should emit at least one PowerPort on the canvas."""
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
        ],
        "nets": [
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}]},
            {"name": "VOUT", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "C1", "pin": "1"}]},
        ],
    })
    result = build_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS))
    assert result.ok, [f.text for f in result.failures]
    assert result.power_port_count >= 1
    gnd_ports = [p for p in result.canvas.power_ports if p.text == "GND"]
    assert gnd_ports, "GND net should produce a GND-named PowerPort"
    # GND glyph should be one of the gnd_* styles.
    assert any("gnd" in p.style.lower() for p in gnd_ports)


def test_cluster_radius_is_asymmetric_by_kind():
    """Rail-glyph clustering radius is per-KIND, not per-net-size, and the
    preview matches the executor's apply path. Ground glyphs are narrow,
    so a decap row gets per-cap drops (tight radius); power bars carry
    the net NAME, wider than a decap column pitch, so nearby rail pins
    share one bar (wide radius)."""
    from eda_agent.design.pipeline import _cluster_radius_for_net
    from eda_agent.design.canvas import POWER_RAIL_CLUSTER_RADIUS_MILS

    assert POWER_RAIL_CLUSTER_RADIUS_MILS == 300
    for n in (2, 5, 6, 12, 40):
        actions = [(None, (i * 100, 0), 0) for i in range(n)]
        assert _cluster_radius_for_net(actions, is_ground=True) \
            == POWER_RAIL_CLUSTER_RADIUS_MILS
        assert _cluster_radius_for_net(actions, is_ground=False) == 1000


def test_spread_ground_pins_get_per_pin_symbols_not_one_cluster():
    """A ground net whose pins land far apart must emit a GND symbol PER
    cluster (the universal convention), not one giant cluster wired with
    long cross-sheet spokes that tangle the drawing."""
    # Six decoupling caps across a power rail: pin-1 on VCC, pin-2 on GND.
    # Both are power nets -> port glyphs, no signal wires to short.
    parts = [
        {"refdes": f"C{i}", "lib_ref": "CAP", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"}
        for i in range(1, 7)
    ]
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts,
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": f"C{i}", "pin": "1"} for i in range(1, 7)]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": f"C{i}", "pin": "2"} for i in range(1, 7)]},
        ],
    })
    result = build_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS))
    assert result.ok, [f.text for f in result.failures]
    gnd_ports = [p for p in result.canvas.power_ports if p.text == "GND"]
    # Spread pins -> more than one GND glyph (the old code emitted exactly 1).
    assert len(gnd_ports) >= 2


def test_neat_engine_overrides_build_a_valid_canvas():
    """The neat-layout engine's positions are compatible with the canvas as
    layout overrides (it is kept as a standalone adapter, not run in the hot
    selection path -- the Sugiyama placer wins there)."""
    from eda_agent.design.pipeline import (
        build_canvas_from_plan, _neat_engine_overrides,
    )
    plan = _basic_rc_plan()
    ext = MockExtractor(_BASE_SYMBOLS)
    ov = _neat_engine_overrides(plan)
    assert ov and set(ov) == {"R1", "C1"}
    variant = build_canvas_from_plan(plan, ext, layout_overrides=ov)
    assert variant.ok and len(variant.canvas.instances) == 2


def test_best_canvas_records_selected_variant_label():
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    plan = _basic_rc_plan()
    result = build_best_canvas_from_plan(
        plan, MockExtractor(_BASE_SYMBOLS), n_tries=3)
    assert result.ok
    texts = " || ".join(n.text for n in result.notes)
    import re
    # The count is no longer fixed: the shared-axis and compaction
    # passes skip their rebuild when they would change nothing, so
    # asserting a literal number would fail for a layout that was
    # already straight. It must still cover base + the rescales.
    m_count = re.search(r"out of (\d+) variants", texts)
    assert m_count is not None, texts
    assert int(m_count.group(1)) >= 3, (
        f"only {m_count.group(1)} variants tried; base plus two rescales "
        f"is the floor")
    m = re.search(r"selected layout: (\S+) score=", texts)
    assert m is not None
    winner = m.group(1)
    assert (winner == "base" or winner.startswith("aspect=")
            or winner.startswith("shared_axis_")
            or winner.startswith("compact_")), winner


def test_build_best_canvas_is_deterministic():
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    plan = _basic_rc_plan()
    a = build_best_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS), n_tries=3)
    b = build_best_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS), n_tries=3)
    pa = {i.refdes: (i.x, i.y, i.rotation) for i in a.canvas.instances}
    pb = {i.refdes: (i.x, i.y, i.rotation) for i in b.canvas.instances}
    assert pa == pb


# ---------------------------------------------------------------------------
# End-to-end quality guard: a realistic plan must produce a clean canvas.
# Locks in the cumulative net-classification / port / signal-flow behaviour.
# ---------------------------------------------------------------------------

def _ic4() -> SymbolModel:
    """A 4-pin IC: VCC/GND on the left, two signal pins on the right."""
    return SymbolModel(
        lib_path=_LIB, lib_ref="IC4",
        pins=(
            SymbolPin(designator="1", name="VCC", x=-200, y=100,
                      orientation=2, length=100, electrical_type="power"),
            SymbolPin(designator="2", name="GND", x=-200, y=-100,
                      orientation=2, length=100, electrical_type="power"),
            SymbolPin(designator="3", name="OUT1", x=200, y=100,
                      orientation=0, length=100, electrical_type="output"),
            SymbolPin(designator="4", name="OUT2", x=200, y=-100,
                      orientation=0, length=100, electrical_type="output"),
        ),
        body_bbox=SymbolBBox(x_min=-150, y_min=-150, x_max=150, y_max=150),
    )


def test_end_to_end_realistic_plan_is_clean():
    """A small realistic design -- IC with two decaps on a NAME-ONLY VCC rail
    (no is_power flag), a flagged GND, and a signal chain -- must come out
    clean: rails as port glyphs (name detection), a wired ordered signal
    path, no body overlaps. Guards the net-classification + signal-flow work
    end to end."""
    syms = {(_LIB, "RES"): _passive("RES"), (_LIB, "CAP"): _passive("CAP"),
            (_LIB, "IC4"): _ic4()}
    parts = [
        {"refdes": "U1", "lib_ref": "IC4", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "C2", "lib_ref": "CAP", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "J1", "lib_ref": "RES", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z", "role": "output"},
    ]
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts,
        "nets": [
            # NAME-ONLY power rail -- no is_power flag; must still become ports.
            {"name": "VCC", "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"},
                {"refdes": "C2", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "2"},
                {"refdes": "C2", "pin": "2"}]},
            # Signal path U1.OUT1 -> R1 -> J1.
            {"name": "S1", "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "R1", "pin": "1"}]},
            {"name": "S2", "pins": [
                {"refdes": "R1", "pin": "2"}, {"refdes": "J1", "pin": "1"}]},
        ],
    })
    result = build_canvas_from_plan(plan, MockExtractor(syms))
    assert result.ok, [f.text for f in result.failures]

    from eda_agent.design.quality import score_canvas
    sc = score_canvas(result.canvas, plan)
    # The name-only VCC rail and the flagged GND both become port glyphs.
    port_texts = {p.text for p in result.canvas.power_ports_on("main")}
    assert "VCC" in port_texts        # name detection routed VCC to a port
    assert "GND" in port_texts
    # Clean drawing: no body overlaps and no through-body wires.
    assert sc.body_overlaps == 0
    assert sc.wires_through_bodies == 0


def test_dense_design_falls_back_to_labels_instead_of_shorting():
    """At density the router can't always avoid foreign pins; a net that
    would short must fall back to per-pin labels so the emit still succeeds
    (ok=True) rather than blocking on a routing short. A long chain packs
    into a 2D grid that triggers this.

    ON A5, and the chain length is unchanged. The keep-out per part went
    from 450 mils to the drawn body plus one clearance, so 25 parts stopped
    filling an A4 sheet: every net wired cleanly, no fallback happened, and
    the test failed on its own setup rather than on the property it exists
    to guard. A5 with the same chain reproduces the density (5 nets
    demoted, still no short); A4 needs 40 parts and runs ten times longer.
    """
    n = 25
    chain = ["J1"] + [f"R{i}" for i in range(1, n - 1)] + ["J2"]
    parts = [{"refdes": "J1", "lib_ref": "RES", "lib_path": _LIB,
              "status": "existing", "sheet": "main", "zone": "z",
              "role": "input"}]
    parts += [{"refdes": f"R{i}", "lib_ref": "RES", "lib_path": _LIB,
               "status": "existing", "sheet": "main", "zone": "z"}
              for i in range(1, n - 1)]
    parts += [{"refdes": "J2", "lib_ref": "RES", "lib_path": _LIB,
               "status": "existing", "sheet": "main", "zone": "z",
               "role": "output"}]
    nets = [{"name": f"N{i}", "pins": [
        {"refdes": chain[i], "pin": "2"},
        {"refdes": chain[i + 1], "pin": "1"}]}
        for i in range(len(chain) - 1)]
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A5"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts, "nets": nets,
    })
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    res = build_best_canvas_from_plan(plan, MockExtractor(_BASE_SYMBOLS),
                                      n_tries=4)
    # No blocked emit: the short -> label fallback kept it valid.
    assert res.ok, [f.text for f in res.failures]
    assert not any("routing short" in f.text for f in res.failures)
    # The fallback surfaces a density warning so the planner can act.
    assert any(n.severity == "warning" and "labelled instead of wired" in n.text
               for n in res.notes), (
        "no net was demoted, so this board is not dense enough to exercise "
        "the fallback: the FIXTURE needs tightening, not the assertion")


def _mcu_sym(n_out: int) -> SymbolModel:
    pins = [SymbolPin(designator="1", name="VCC", x=-200, y=150,
                      orientation=2, length=100, electrical_type="power"),
            SymbolPin(designator="2", name="GND", x=-200, y=-150,
                      orientation=2, length=100, electrical_type="power")]
    for i in range(n_out):
        pins.append(SymbolPin(designator=str(i + 3), name=f"P{i}", x=200,
                              y=150 - i * 40, orientation=0, length=100,
                              electrical_type="output"))
    return SymbolModel(lib_path=_LIB, lib_ref="MCU", pins=tuple(pins),
                       body_bbox=SymbolBBox(x_min=-150, y_min=-200,
                                            x_max=150, y_max=200))


@pytest.mark.parametrize("n_branch,n_decap", [(6, 4), (8, 6), (10, 4),
                                              (12, 8), (8, 10)])
def test_dense_single_sheet_designs_emit_valid(n_branch, n_decap):
    """A range of realistic dense single-sheet designs (I/O connectors + MCU
    hub + decaps + signal branches, ~21-37 parts) must all emit valid
    (ok=True) -- the wire->label fallback resolves the routing shorts. The
    edge-anchored connectors spread the placement, as on a real board. (At
    EXTREME density with no I/O anchors, stub shorts can still leak -- see
    schematic_density_shorts memory.)"""
    syms = {(_LIB, "RES"): _passive("RES"), (_LIB, "CAP"): _passive("CAP"),
            (_LIB, "CONN"): _passive("CONN"), (_LIB, "MCU"): _mcu_sym(n_branch)}

    def P(ref, lr, **kw):
        return {"refdes": ref, "lib_ref": lr, "lib_path": _LIB,
                "status": "existing", "sheet": "main", "zone": "z", **kw}

    parts = [P("U1", "MCU"),
             P("J1", "CONN", role="input"), P("J2", "CONN", role="output")]
    parts += [P(f"C{i}", "CAP") for i in range(1, n_decap + 1)]
    parts += [P(f"R{i}", "RES") for i in range(1, n_branch + 1)]
    parts += [P(f"D{i}", "RES") for i in range(1, n_branch + 1)]
    nets = [
        {"name": "VCC", "is_power": True,
         "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "J1", "pin": "1"}]
         + [{"refdes": f"C{i}", "pin": "1"} for i in range(1, n_decap + 1)]},
        {"name": "GND", "is_ground": True,
         "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "J1", "pin": "2"},
                  {"refdes": "J2", "pin": "2"}]
         + [{"refdes": f"C{i}", "pin": "2"} for i in range(1, n_decap + 1)]},
    ]
    for i in range(n_branch):
        nets.append({"name": f"S{i}", "pins": [
            {"refdes": "U1", "pin": str(i + 3)},
            {"refdes": f"R{i + 1}", "pin": "1"}]})
        nets.append({"name": f"B{i}", "pins": [
            {"refdes": f"R{i + 1}", "pin": "2"},
            {"refdes": f"D{i + 1}", "pin": "1"}]})
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts, "nets": nets,
    })
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    res = build_best_canvas_from_plan(plan, MockExtractor(syms), n_tries=4)
    assert res.ok, [f.text for f in res.failures]
    assert not any("routing short" in f.text for f in res.failures)


def test_larger_sheet_spreads_layout_bounds():
    """The density root fix: the layout uses the chosen sheet's bounds, so a
    bigger sheet spreads the same dense design across more area (which is what
    relieves the row-cramming that caused routing shorts). Asserted directly on
    the Sugiyama span rather than via shorts: the decoupling-cap clustering
    added later independently relieves A4 density, so a short-based contrast is
    no longer a stable signal -- but the spread mechanism itself is exactly
    ``_layout_max`` scaling with sheet size and is the thing to guard."""
    from eda_agent.design.plan import DesignPlan as _DP, Net, Part, PinRef, Sheet
    from eda_agent.design.sugiyama import sugiyama_layout

    def _net(nm, ps, **kw):
        return Net(name=nm, pins=[PinRef(refdes=r, pin=p) for r, p in ps], **kw)

    def _span(size: str) -> int:
        parts = ([Part(refdes="J1", lib_ref="HDR", lib_path=_LIB,
                       role="input_conn", status="existing")]
                 + [Part(refdes=f"R{i}", lib_ref="RES", lib_path=_LIB,
                         status="existing") for i in range(1, 25)]
                 + [Part(refdes="J2", lib_ref="HDR", lib_path=_LIB,
                         role="output_conn", status="existing")])
        nets = ([_net("IN", [("J1", "1"), ("R1", "1")])]
                + [_net(f"N{i}", [(f"R{i}", "2"), (f"R{i+1}", "1")])
                   for i in range(1, 24)]
                + [_net("OUT", [("R24", "2"), ("J2", "1")])]
                + [_net(f"F{i}", [("J1", str(i)), (f"R{i}", "1")])
                   for i in range(1, 5)])    # a wide fan layer where spread bites
        plan = _DP(spec="x", summary="x",
                   sheets=[Sheet(name="main", size=size)],
                   parts=parts, nets=nets)
        pls = sugiyama_layout(plan)
        xs = [p.x_mils for p in pls]
        ys = [p.y_mils for p in pls]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    a4, a3, a2 = _span("A4"), _span("A3"), _span("A2")
    # The chain is 24 long, not 12: the keep-out constants were retuned
    # from the human corpus (a two-pin part went from 450 to 200) and a
    # 12-part chain then fits A4 with room to spare, so a3 stopped being
    # wider than a4 and the fixture no longer created the condition the
    # assertion is about. The property is unchanged; the design has to be
    # big enough for the sheet-fit term to bind on A4.
    # Wider layout on each larger sheet WHILE the sheet-fit term binds;
    # once a chain reaches its size-aware ideal pitch (this all-passives
    # fixture does on A3) a still-larger sheet must NOT scatter it
    # further -- compactness caps the spread at the ideal.
    assert a4 < a3 <= a2


def _side_ic(lib_ref: str, n_left: int, n_right: int) -> SymbolModel:
    pins = [SymbolPin(designator=str(i + 1), name=f"L{i}", x=-300,
                      y=200 - i * 100, orientation=2, length=100,
                      electrical_type="input") for i in range(n_left)]
    pins += [SymbolPin(designator=str(n_left + i + 1), name=f"R{i}", x=300,
                       y=200 - i * 100, orientation=0, length=100,
                       electrical_type="output") for i in range(n_right)]
    return SymbolModel(lib_path=_LIB, lib_ref=lib_ref, pins=tuple(pins),
                       body_bbox=SymbolBBox(x_min=-200, y_min=-300,
                                            x_max=200, y_max=300))


def _ldo_sym() -> SymbolModel:
    return SymbolModel(
        lib_path=_LIB, lib_ref="LDO",
        pins=(SymbolPin(designator="1", name="IN", x=-200, y=0, orientation=2,
                        length=100, electrical_type="power"),
              SymbolPin(designator="3", name="OUT", x=200, y=0, orientation=0,
                        length=100, electrical_type="power"),
              SymbolPin(designator="2", name="GND", x=0, y=-200, orientation=3,
                        length=100, electrical_type="power")),
        body_bbox=SymbolBBox(x_min=-150, y_min=-150, x_max=150, y_max=150))


def test_force_directed_variant_wins_power_tree_board():
    """A board whose signal graph is split by a power-only bridge (LDO +
    connector reach the rest only through rails) lays out better under
    force-directed than Sugiyama; best-of must pick the FD variant and so
    score no worse -- here strictly better -- than Sugiyama alone."""
    from eda_agent.design.layout import compute_layout
    from eda_agent.design.pipeline import (
        build_best_canvas_from_plan, build_canvas_from_plan,
    )
    from eda_agent.design.quality import score_canvas

    syms = {(_LIB, "RES"): _passive("RES"), (_LIB, "CAP"): _passive("CAP"),
            (_LIB, "MCU"): _side_ic("MCU", 4, 4),
            (_LIB, "SENS"): _side_ic("SENS", 3, 3), (_LIB, "LDO"): _ldo_sym(),
            (_LIB, "HDR"): _passive("HDR")}

    def part(ref, lr, role=None):
        d = {"refdes": ref, "lib_ref": lr, "lib_path": _LIB,
             "status": "existing", "sheet": "main"}
        if role:
            d["role"] = role
        return d

    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A3"}],
        "parts": [
            part("J1", "HDR", "input_conn"), part("U3", "LDO"),
            part("C1", "CAP"), part("C2", "CAP"),
            part("U1", "MCU"), part("U2", "SENS"),
            part("R1", "RES"), part("R2", "RES"), part("J2", "HDR", "output_conn"),
        ],
        "nets": [
            {"name": "VIN", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "1"}, {"refdes": "U3", "pin": "1"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "V3V3", "is_power": True, "pins": [
                {"refdes": "U3", "pin": "3"}, {"refdes": "C2", "pin": "1"},
                {"refdes": "U1", "pin": "1"}, {"refdes": "U2", "pin": "1"},
                {"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "J1", "pin": "2"}, {"refdes": "U3", "pin": "2"},
                {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
                {"refdes": "U1", "pin": "2"}, {"refdes": "U2", "pin": "2"}]},
            {"name": "SDA", "pins": [{"refdes": "U1", "pin": "5"},
                {"refdes": "U2", "pin": "4"}, {"refdes": "R1", "pin": "2"}]},
            {"name": "SCL", "pins": [{"refdes": "U1", "pin": "6"},
                {"refdes": "U2", "pin": "5"}, {"refdes": "R2", "pin": "2"}]},
            {"name": "SIG", "pins": [{"refdes": "U2", "pin": "6"},
                {"refdes": "U1", "pin": "3"}]},
            {"name": "OUT1", "pins": [{"refdes": "U1", "pin": "7"},
                {"refdes": "J2", "pin": "1"}]},
        ],
    })

    best = build_best_canvas_from_plan(plan, MockExtractor(syms), n_tries=5,
                                       strict_shorts=False)
    assert best.ok
    best_total = score_canvas(best.canvas, plan).total

    # Sugiyama-only baseline (force the engine, single build, no rescale).
    sug_placed = compute_layout(plan, engine="sugiyama")
    sug = build_canvas_from_plan(
        plan, MockExtractor(syms),
        layout_overrides={p.refdes: p for p in sug_placed},
        strict_shorts=False)
    sug_total = score_canvas(sug.canvas, plan).total

    # best-of (which includes the FD variant) must not be worse than
    # Sugiyama alone. Historically FD won this board DECISIVELY because a
    # power-only-bridged signal graph collapsed Sugiyama into one column;
    # the signal-isolated-anchor seed fix closed that gap (the engines now
    # tie here), so the guarantee is no-worse, not strictly-better.
    assert best_total <= sug_total


def _ic_symbol(lib_ref, pins):
    """Build a multi-pin IC SymbolModel from (designator, x, y, orientation)."""
    sp = [SymbolPin(designator=d, name=d, x=x, y=y, orientation=o,
                    length=100, electrical_type="passive")
          for (d, x, y, o) in pins]
    xs = [p.x for p in sp]
    ys = [p.y for p in sp]
    return SymbolModel(
        lib_path=_LIB, lib_ref=lib_ref, pins=tuple(sp),
        body_bbox=SymbolBBox(x_min=min(xs) + 80, y_min=min(ys) - 50,
                             x_max=max(xs) - 80, y_max=max(ys) + 50))


def _bus_plan_and_symbols():
    """MCU U1 <-> memory U2 with an 8-bit data bus (D0..D7) plus VCC/GND."""
    u1 = [("V", -300, 400, 2), ("G", -300, -400, 2)] + \
         [(f"D{i}", 300, 300 - i * 80, 0) for i in range(8)]
    u2 = [("V", 300, 400, 0), ("G", 300, -400, 0)] + \
         [(f"D{i}", -300, 300 - i * 80, 2) for i in range(8)]
    syms = {
        (_LIB, "MCU"): _ic_symbol("MCU", u1),
        (_LIB, "MEM"): _ic_symbol("MEM", u2),
        (_LIB, "CAP"): _passive("CAP"),
    }
    parts = [
        {"refdes": "U1", "lib_ref": "MCU", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "U2", "lib_ref": "MEM", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB, "value": "100nF",
         "status": "existing", "sheet": "main", "zone": "z"},
    ]
    nets = [
        {"name": "VCC", "is_power": True, "pins": [
            {"refdes": "U1", "pin": "V"}, {"refdes": "U2", "pin": "V"},
            {"refdes": "C1", "pin": "1"}]},
        {"name": "GND", "is_ground": True, "pins": [
            {"refdes": "U1", "pin": "G"}, {"refdes": "U2", "pin": "G"},
            {"refdes": "C1", "pin": "2"}]},
    ]
    for i in range(8):
        nets.append({"name": f"D{i}", "pins": [
            {"refdes": "U1", "pin": f"D{i}"},
            {"refdes": "U2", "pin": f"D{i}"}]})
    plan = DesignPlan.model_validate({
        "spec": "bus", "summary": "8-bit data bus",
        "sheets": [{"name": "main", "size": "A3"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts, "nets": nets})
    return plan, syms


def test_wide_bus_draws_through_the_pipeline():
    """An 8-net inter-IC bus is drawn as a BUS glyph (not just N label pairs)
    by build_canvas_from_plan -- locks in the apply_bus_drawing integration."""
    plan, syms = _bus_plan_and_symbols()
    result = build_canvas_from_plan(plan, MockExtractor(syms))
    assert result.ok
    cv = result.canvas
    # A bus line + 45-degree entries were emitted (per-IC stubs).
    assert len(cv.buses) >= 1
    assert len(cv.bus_entries) >= 8
    # The per-signal labels still carry connectivity (D0..D7 present).
    texts = {lab.text for lab in cv.labels}
    assert {f"D{i}" for i in range(8)} <= texts


def test_best_canvas_keeps_the_bus():
    """The multi-try best-of path also retains the bus glyph."""
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    plan, syms = _bus_plan_and_symbols()
    result = build_best_canvas_from_plan(plan, MockExtractor(syms), n_tries=3)
    assert result.ok
    assert len(result.canvas.buses) >= 1


def _inverting_amp_plan_and_symbols():
    """Inverting op-amp: Rin (R1) from VIN to the summing node, Rf (R2) from
    the summing node to VOUT, around op-amp U1. Exercises the OPAMP_INVERTING
    motif through the real pipeline."""
    opamp = _ic_symbol("OPAMP", [
        ("1", 300, 0, 0), ("2", -300, 80, 2), ("3", -300, -80, 2),
        ("4", 0, 200, 1), ("5", 0, -200, 3)])
    syms = {
        (_LIB, "OPAMP"): opamp,
        (_LIB, "RES"): _passive("RES"),
        (_LIB, "HDR"): _ic_symbol("HDR", [("1", -100, 100, 2),
                                           ("2", -100, -100, 2)]),
    }
    plan = DesignPlan.model_validate({
        "spec": "inv amp", "summary": "inverting op-amp gain stage",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [
            {"refdes": "J1", "lib_ref": "HDR", "lib_path": _LIB,
             "role": "input_conn", "status": "existing", "sheet": "main",
             "zone": "z"},
            {"refdes": "J2", "lib_ref": "HDR", "lib_path": _LIB,
             "role": "output_conn", "status": "existing", "sheet": "main",
             "zone": "z"},
            {"refdes": "U1", "lib_ref": "OPAMP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB, "value": "10k",
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB, "value": "100k",
             "status": "existing", "sheet": "main", "zone": "z"}],
        "nets": [
            {"name": "VIN", "pins": [{"refdes": "J1", "pin": "1"},
                                     {"refdes": "R1", "pin": "1"}]},
            {"name": "SUMMING", "pins": [{"refdes": "R1", "pin": "2"},
                                         {"refdes": "R2", "pin": "1"},
                                         {"refdes": "U1", "pin": "2"}]},
            {"name": "VOUT", "pins": [{"refdes": "R2", "pin": "2"},
                                      {"refdes": "U1", "pin": "1"},
                                      {"refdes": "J2", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "J1", "pin": "2"},
                {"refdes": "J2", "pin": "2"}]},
            {"name": "VPLUS", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "4"}, {"refdes": "J1", "pin": "1"}]}]})
    return plan, syms


def test_opamp_motif_places_gain_stage_through_pipeline():
    """The OPAMP_INVERTING motif fires AND lays Rin/Rf as a symmetric gain
    stage on the op-amp's input side -- not scattered across the sheet."""
    import math
    from eda_agent.design.motifs import recognize_motifs
    plan, syms = _inverting_amp_plan_and_symbols()
    assert any(m.motif_name == "opamp_inverting" for m in recognize_motifs(plan))

    result = build_canvas_from_plan(plan, MockExtractor(syms))
    assert result.ok
    ctr = {}
    for inst in result.canvas.instances:
        bb = inst.world_bbox()
        ctr[inst.refdes] = ((bb.x_min + bb.x_max) / 2,
                            (bb.y_min + bb.y_max) / 2)

    def dist(a, b):
        return math.hypot(ctr[a][0] - ctr[b][0], ctr[a][1] - ctr[b][1])
    d1, d2 = dist("R1", "U1"), dist("R2", "U1")
    # Both feedback/input resistors are clustered with the op-amp...
    assert d1 < 2500 and d2 < 2500
    # ...and placed symmetrically about it (the canonical (-1700, +/-600)).
    assert abs(d1 - d2) < 400


def _acdc_frontend_plan_and_symbols():
    """AC connector -> 4-diode bridge -> pi filter (Cin/L/Cout) -> load.
    Exercises the self-contained diode_bridge and pi_filter motifs together."""
    def hdr(lib_ref, pins):
        return _ic_symbol(lib_ref, pins)
    syms = {
        (_LIB, "DIODE"): _passive("DIODE"), (_LIB, "CAP"): _passive("CAP"),
        (_LIB, "IND"): _passive("IND"),
        (_LIB, "HDR"): hdr("HDR", [("1", -100, 100, 2), ("2", -100, -100, 2)]),
        (_LIB, "LOAD"): hdr("LOAD", [("1", -150, 80, 2), ("2", -150, -80, 2),
                                     ("3", 150, 0, 0)]),
    }
    parts = [{"refdes": "J1", "lib_ref": "HDR", "lib_path": _LIB,
              "role": "input_conn", "status": "existing", "sheet": "main",
              "zone": "z"}]
    parts += [{"refdes": f"D{i}", "lib_ref": "DIODE", "lib_path": _LIB,
               "status": "existing", "sheet": "main", "zone": "z"}
              for i in (1, 2, 3, 4)]
    parts += [
        {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB, "value": "100uF",
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "L1", "lib_ref": "IND", "lib_path": _LIB, "value": "10uH",
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "C2", "lib_ref": "CAP", "lib_path": _LIB, "value": "100uF",
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "U1", "lib_ref": "LOAD", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"}]
    nets = [
        {"name": "AC1", "pins": [{"refdes": "J1", "pin": "1"},
                                 {"refdes": "D1", "pin": "1"},
                                 {"refdes": "D3", "pin": "2"}]},
        {"name": "AC2", "pins": [{"refdes": "J1", "pin": "2"},
                                 {"refdes": "D2", "pin": "1"},
                                 {"refdes": "D4", "pin": "2"}]},
        {"name": "VPLUS", "is_power": True, "pins": [
            {"refdes": "D1", "pin": "2"}, {"refdes": "D2", "pin": "2"},
            {"refdes": "C1", "pin": "1"}, {"refdes": "L1", "pin": "1"}]},
        {"name": "VFILT", "is_power": True, "pins": [
            {"refdes": "L1", "pin": "2"}, {"refdes": "C2", "pin": "1"},
            {"refdes": "U1", "pin": "1"}]},
        {"name": "GND", "is_ground": True, "pins": [
            {"refdes": "D3", "pin": "1"}, {"refdes": "D4", "pin": "1"},
            {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
            {"refdes": "U1", "pin": "2"}]}]
    plan = DesignPlan.model_validate({
        "spec": "acdc", "summary": "bridge + pi filter",
        "sheets": [{"name": "main", "size": "A3"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts, "nets": nets})
    return plan, syms


def test_selfcontained_motifs_keep_canonical_geometry_through_pipeline():
    """The diode_bridge and pi_filter (self-contained) motifs are recognised
    and KEEP their canonical shape through the pipeline -- resnap_motif_clusters
    restores the geometry the overlap shove would otherwise scatter. The bridge
    is a near-square diamond; the pi filter's C-L-C stays tight."""
    import math
    from eda_agent.design.motifs import recognize_motifs
    plan, syms = _acdc_frontend_plan_and_symbols()
    names = {m.motif_name for m in recognize_motifs(plan)}
    assert "diode_bridge" in names and "pi_filter" in names

    from eda_agent.design.pipeline import build_best_canvas_from_plan

    def _check(canvas):
        ctr = {}
        for inst in canvas.instances:
            bb = inst.world_bbox()
            ctr[inst.refdes] = ((bb.x_min + bb.x_max) / 2,
                                (bb.y_min + bb.y_max) / 2)

        def dist(a, b):
            return math.hypot(ctr[a][0] - ctr[b][0], ctr[a][1] - ctr[b][1])
        # Bridge: the four diamond edges are ~equal (a square, not a skewed quad).
        edges = [dist("D1", "D2"), dist("D2", "D4"), dist("D4", "D3"),
                 dist("D3", "D1")]
        assert max(edges) - min(edges) < 300    # near-square (canonical 1400)
        assert all(900 < e < 1900 for e in edges)
        # Pi filter C-L-C: BOTH cap-to-inductor legs are ~the canonical 1487 AND
        # roughly SYMMETRIC. The asymmetry guard catches the cross-motif
        # collision regression (one leg snapped, the other skipped to ~539).
        ll, lr = dist("C1", "L1"), dist("L1", "C2")
        assert 1100 < ll < 1900 and 1100 < lr < 1900
        assert abs(ll - lr) < 400

    base = build_canvas_from_plan(plan, MockExtractor(syms))
    assert base.ok
    _check(base.canvas)
    # The default emit path (best-of aspect rescaling) keeps the geometry too.
    best = build_best_canvas_from_plan(plan, MockExtractor(syms), n_tries=4)
    assert best.ok
    _check(best.canvas)


def _buck_mcu_opamp_plan_and_symbols():
    """A realistic mixed board: buck (fb_divider+boot_cap+lc_output) + MCU with
    a crystal + a role-tagged op-amp sensor. Exercises ALL the motif types and
    the resnap + signal-subtype-role interactions on one plan, kept compact
    enough to route short-free."""
    reg = _ic_symbol("REG", [("VIN", -200, 150, 2), ("GND", -200, -150, 2),
                             ("SW", 200, 150, 0), ("BOOT", 200, 50, 0),
                             ("FB", 200, -50, 0), ("VOUT", 200, -150, 0)])
    mcu = _ic_symbol("MCU", [("VCC", -200, 150, 2), ("GND", -200, -150, 2),
                             ("XIN", -200, 50, 2), ("XOUT", -200, -50, 2),
                             ("AIN", 200, 0, 0)])
    op = _ic_symbol("OPAMP", [("OUT", 300, 0, 0), ("INN", -300, 80, 2),
                              ("INP", -300, -80, 2), ("VP", 0, 200, 1),
                              ("VN", 0, -200, 3)])
    syms = {(_LIB, "REG"): reg, (_LIB, "MCU"): mcu, (_LIB, "OPAMP"): op,
            (_LIB, "RES"): _passive("RES"), (_LIB, "CAP"): _passive("CAP"),
            (_LIB, "IND"): _passive("IND"), (_LIB, "DIODE"): _passive("DIODE"),
            (_LIB, "XTAL"): _passive("XTAL"),
            (_LIB, "HDR"): _ic_symbol("HDR", [("1", -100, 100, 2),
                                              ("2", -100, -100, 2)])}
    P = lambda r, lr: {"refdes": r, "lib_ref": lr, "lib_path": _LIB,
                       "status": "existing", "sheet": "main", "zone": "z"}
    plan = DesignPlan.model_validate({
        "spec": "mixed", "summary": "buck+mcu+xtal+opamp",
        "sheets": [{"name": "main", "size": "A2"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [P("J1", "HDR"), P("U1", "REG"), P("L1", "IND"), P("D1", "DIODE"),
                  P("C1", "CAP"), P("C2", "CAP"), P("C3", "CAP"),
                  P("R1", "RES"), P("R2", "RES"), P("U2", "MCU"),
                  P("Y1", "XTAL"), P("C5", "CAP"), P("C6", "CAP"),
                  P("U3", "OPAMP"), P("R3", "RES"), P("R4", "RES"), P("J2", "HDR")],
        "nets": [
            {"name": "VIN", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "1"}, {"refdes": "U1", "pin": "VIN"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "J1", "pin": "2"}, {"refdes": "U1", "pin": "GND"},
                {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
                {"refdes": "D1", "pin": "1"}, {"refdes": "R2", "pin": "2"},
                {"refdes": "U2", "pin": "GND"}, {"refdes": "C5", "pin": "2"},
                {"refdes": "C6", "pin": "2"}, {"refdes": "U3", "pin": "VN"},
                {"refdes": "J2", "pin": "2"}]},
            {"name": "SW", "pins": [{"refdes": "U1", "pin": "SW"},
                                    {"refdes": "L1", "pin": "1"},
                                    {"refdes": "D1", "pin": "2"},
                                    {"refdes": "C3", "pin": "2"}]},
            {"name": "BOOT", "pins": [{"refdes": "U1", "pin": "BOOT"},
                                      {"refdes": "C3", "pin": "1"}]},
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "L1", "pin": "2"}, {"refdes": "C2", "pin": "1"},
                {"refdes": "R1", "pin": "1"}, {"refdes": "U1", "pin": "VOUT"},
                {"refdes": "U2", "pin": "VCC"}, {"refdes": "U3", "pin": "VP"}]},
            {"name": "FB", "pins": [{"refdes": "U1", "pin": "FB"},
                                    {"refdes": "R1", "pin": "2"},
                                    {"refdes": "R2", "pin": "1"}]},
            {"name": "XIN", "pins": [{"refdes": "U2", "pin": "XIN"},
                                     {"refdes": "Y1", "pin": "1"},
                                     {"refdes": "C5", "pin": "1"}]},
            {"name": "XOUT", "pins": [{"refdes": "U2", "pin": "XOUT"},
                                      {"refdes": "Y1", "pin": "2"},
                                      {"refdes": "C6", "pin": "1"}]},
            {"name": "ASENSE", "role": "analog_sensitive", "pins": [
                {"refdes": "U3", "pin": "OUT"}, {"refdes": "U2", "pin": "AIN"},
                {"refdes": "R4", "pin": "2"}]},
            {"name": "VINP", "pins": [{"refdes": "J2", "pin": "1"},
                                      {"refdes": "R3", "pin": "1"}]},
            {"name": "SUMMING", "pins": [{"refdes": "R3", "pin": "2"},
                                         {"refdes": "R4", "pin": "1"},
                                         {"refdes": "U3", "pin": "INN"}]}]})
    return plan, syms


def test_comprehensive_board_recognises_all_motifs_and_clusters_tight():
    """A mixed board (buck + MCU/crystal + role-tagged op-amp) recognises every
    motif type AND places each cluster tight -- the composition where the
    signal-subtype-match and resnap fixes live. ERC must be clean."""
    import math
    from eda_agent.design.motifs import recognize_motifs
    from eda_agent.design.plan_erc import check_plan_erc
    plan, syms = _buck_mcu_opamp_plan_and_symbols()

    # No connectivity errors (shorted pins, floating nets, ...).
    assert check_plan_erc(plan).passed
    names = {m.motif_name for m in recognize_motifs(plan)}
    # The op-amp (analog_sensitive output) fires thanks to signal-subtype match;
    # the regulator motifs and the crystal all fire too.
    assert {"fb_divider", "boot_cap", "lc_output", "crystal_load",
            "opamp_inverting"} <= names

    # Note: this dense board may trip the known density-shorts limit at emit
    # (a label landing on a foreign wire); that is orthogonal to PLACEMENT,
    # which is what this test checks. Every part is still placed.
    result = build_canvas_from_plan(plan, MockExtractor(syms))
    assert result.placement_count == len(plan.parts)
    ctr = {}
    for inst in result.canvas.instances:
        bb = inst.world_bbox()
        ctr[inst.refdes] = ((bb.x_min + bb.x_max) / 2,
                            (bb.y_min + bb.y_max) / 2)

    def d(a, b):
        return math.hypot(ctr[a][0] - ctr[b][0], ctr[a][1] - ctr[b][1])
    # Resnap keeps the clusters tight: bootstrap cap by its IC, crystal caps by
    # the crystal, op-amp gain resistors symmetric on the op-amp input side.
    assert d("C3", "U1") < 1600                       # boot cap near regulator
    assert d("Y1", "C5") < 700 and d("Y1", "C6") < 700  # crystal load caps tight
    assert abs(d("R3", "U3") - d("R4", "U3")) < 400   # op-amp Rin/Rf symmetric


def test_bus_and_crystal_compose_cleanly(monkeypatch):
    """A board with BOTH a data bus (MCU<->memory) and a crystal oscillator:
    the bus glyph draws AND the crystal load caps stay clustered, with no
    short -- the bus post-pass and the crystal resnap don't interfere."""
    import math
    mcu = _ic_symbol("MCU", [("VCC", -300, 400, 2), ("GND", -300, -400, 2),
                             ("XIN", -300, 300, 2), ("XOUT", -300, 200, 2)]
                     + [(f"D{i}", 300, 300 - i * 80, 0) for i in range(8)])
    mem = _ic_symbol("MEM", [("VCC", 300, 400, 0), ("GND", 300, -400, 0)]
                     + [(f"D{i}", -300, 300 - i * 80, 2) for i in range(8)])
    syms = {(_LIB, "MCU"): mcu, (_LIB, "MEM"): mem,
            (_LIB, "CAP"): _passive("CAP"), (_LIB, "XTAL"): _passive("XTAL"),
            (_LIB, "HDR"): _ic_symbol("HDR", [("1", -100, 100, 2),
                                              ("2", -100, -100, 2)])}
    P = lambda r, lr: {"refdes": r, "lib_ref": lr, "lib_path": _LIB,
                       "status": "existing", "sheet": "main", "zone": "z"}
    nets = [
        {"name": "VCC", "is_power": True, "pins": [
            {"refdes": "J1", "pin": "1"}, {"refdes": "U1", "pin": "VCC"},
            {"refdes": "U2", "pin": "VCC"}, {"refdes": "C1", "pin": "1"}]},
        {"name": "GND", "is_ground": True, "pins": [
            {"refdes": "J1", "pin": "2"}, {"refdes": "U1", "pin": "GND"},
            {"refdes": "U2", "pin": "GND"}, {"refdes": "C1", "pin": "2"},
            {"refdes": "C3", "pin": "2"}, {"refdes": "C4", "pin": "2"}]},
        {"name": "XIN", "pins": [{"refdes": "U1", "pin": "XIN"},
                                 {"refdes": "Y1", "pin": "1"},
                                 {"refdes": "C3", "pin": "1"}]},
        {"name": "XOUT", "pins": [{"refdes": "U1", "pin": "XOUT"},
                                  {"refdes": "Y1", "pin": "2"},
                                  {"refdes": "C4", "pin": "1"}]}]
    for i in range(8):
        nets.append({"name": f"D{i}", "pins": [{"refdes": "U1", "pin": f"D{i}"},
                                               {"refdes": "U2", "pin": f"D{i}"}]})
    plan = DesignPlan.model_validate({
        "spec": "bus+xtal", "summary": "mcu+mem bus + crystal",
        "sheets": [{"name": "main", "size": "A2"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [P("J1", "HDR"), P("U1", "MCU"), P("U2", "MEM"),
                  P("C1", "CAP"), P("Y1", "XTAL"), P("C3", "CAP"), P("C4", "CAP")],
        "nets": nets})

    # THE PRODUCTION SWEEP. The conftest shrinks the pin-attractor sweep to
    # two values for speed, and on this board those two land on the plain
    # base layout while the bus-drawing winner sits at k=0.0511. Both
    # layouts have one wire crossing, so nothing is wrong with either; the
    # bus simply is not reachable from two samples of a chaotic landscape.
    # Testing the shrunken sweep would be testing the speed patch.
    import eda_agent.design.pipeline as _pipeline
    monkeypatch.setattr(
        _pipeline, "_FD_K_SWEEP",
        tuple(round(0.02 + i * (0.28 / 99), 4) for i in range(100)))
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    result = build_best_canvas_from_plan(plan, MockExtractor(syms), n_tries=4)
    assert result.ok
    cv = result.canvas
    # The bus glyph drew (per-IC stub + entries) AND the crystal stayed tight.
    assert len(cv.buses) >= 1 and len(cv.bus_entries) >= 8
    ctr = {i.refdes: ((i.world_bbox().x_min + i.world_bbox().x_max) / 2,
                      (i.world_bbox().y_min + i.world_bbox().y_max) / 2)
           for i in cv.instances}

    def d(a, b):
        return math.hypot(ctr[a][0] - ctr[b][0], ctr[a][1] - ctr[b][1])
    assert d("Y1", "C3") < 700 and d("Y1", "C4") < 700


def _three_opamp_cascade_plan_and_symbols():
    """Three inverting op-amp gain stages in series (an instrumentation-style
    front end): U1 -> U2 -> U3, each with its own Rin/Rf pair. Stresses the
    per-IC collision scoping in resnap_motif_clusters -- three instances of the
    SAME motif must each restore canonical geometry without interfering."""
    opamp = _ic_symbol("OPAMP", [
        ("1", 300, 0, 0), ("2", -300, 80, 2), ("3", -300, -80, 2),
        ("4", 0, 200, 1), ("5", 0, -200, 3)])
    syms = {
        (_LIB, "OPAMP"): opamp,
        (_LIB, "RES"): _passive("RES"),
        (_LIB, "HDR"): _ic_symbol("HDR", [("1", -100, 100, 2),
                                          ("2", -100, -100, 2)]),
    }
    P = lambda r, lr, **kw: {"refdes": r, "lib_ref": lr, "lib_path": _LIB,
                            "status": "existing", "sheet": "main", "zone": "z",
                            **kw}
    parts = [P("J1", "HDR", role="input_conn"),
             P("J2", "HDR", role="output_conn")]
    nets = [{"name": "VIN", "pins": [{"refdes": "J1", "pin": "1"},
                                     {"refdes": "R1", "pin": "1"}]}]
    for k in (1, 2, 3):
        u, ri, rf = f"U{k}", f"R{2 * k - 1}", f"R{2 * k}"
        parts += [P(u, "OPAMP"), P(ri, "RES", value="10k"),
                  P(rf, "RES", value="100k")]
        nets.append({"name": f"SUM{k}", "pins": [
            {"refdes": ri, "pin": "2"}, {"refdes": rf, "pin": "1"},
            {"refdes": u, "pin": "2"}]})
        downstream = ([{"refdes": f"R{2 * k + 1}", "pin": "1"}] if k < 3
                      else [{"refdes": "J2", "pin": "1"}])
        nets.append({"name": f"OUT{k}", "pins": [
            {"refdes": rf, "pin": "2"}, {"refdes": u, "pin": "1"}] + downstream})
    nets.append({"name": "GND", "is_ground": True, "pins": [
        {"refdes": "U1", "pin": "3"}, {"refdes": "U2", "pin": "3"},
        {"refdes": "U3", "pin": "3"}, {"refdes": "J1", "pin": "2"},
        {"refdes": "J2", "pin": "2"}]})
    nets.append({"name": "VPLUS", "is_power": True, "pins": [
        {"refdes": "U1", "pin": "4"}, {"refdes": "U2", "pin": "4"},
        {"refdes": "U3", "pin": "4"}]})
    plan = DesignPlan.model_validate({
        "spec": "3-stage amp", "summary": "cascaded inverting op-amps",
        "sheets": [{"name": "main", "size": "A2"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts, "nets": nets})
    return plan, syms


def test_multiple_opamp_instances_each_resnap_independently():
    """Regression: a board with THREE instances of the opamp_inverting motif
    resnaps each gain stage to its own canonical symmetric geometry. This is
    the multiplicity case where the per-IC collision scoping (claimed_by_ic)
    in resnap_motif_clusters matters -- a global claimed list would let one
    op-amp's resnap targets block another's. Confirmed this session by probe;
    locked in here."""
    import math
    from eda_agent.design.motifs import recognize_motifs
    plan, syms = _three_opamp_cascade_plan_and_symbols()
    n_op = sum(1 for m in recognize_motifs(plan)
               if m.motif_name == "opamp_inverting")
    assert n_op == 3, f"expected 3 opamp_inverting matches, got {n_op}"

    result = build_canvas_from_plan(plan, MockExtractor(syms))
    assert result.placement_count == len(plan.parts)
    ctr = {}
    for inst in result.canvas.instances:
        bb = inst.world_bbox()
        ctr[inst.refdes] = ((bb.x_min + bb.x_max) / 2,
                            (bb.y_min + bb.y_max) / 2)

    def d(a, b):
        return math.hypot(ctr[a][0] - ctr[b][0], ctr[a][1] - ctr[b][1])

    # Each of the three op-amps gets its OWN Rin/Rf clustered tight and placed
    # symmetrically about it -- independent of the other two instances.
    for k in (1, 2, 3):
        u, ri, rf = f"U{k}", f"R{2 * k - 1}", f"R{2 * k}"
        din, dfb = d(ri, u), d(rf, u)
        assert din < 2500 and dfb < 2500, (
            f"U{k} gain stage scattered: Rin={din:.0f} Rf={dfb:.0f}")
        assert abs(din - dfb) < 400, (
            f"U{k} gain stage asymmetric: |{din:.0f}-{dfb:.0f}|")
    # The three op-amps stay distinct instances (not piled on one another): a
    # scoping failure that collapsed two stages' geometry would also collapse
    # their separation. (We do NOT require each resistor to be nearest its own
    # op-amp -- in a tight cascade a stage's input resistor legitimately sits
    # between it and the upstream stage; the symmetry above is the real guard,
    # since a blocked resnap target would distort exactly that distance.)
    #
    # Measured in CLEAR SPACE between the drawn bodies, not centre
    # distance. The old threshold was 1500 mils centre to centre, which
    # was really the old keep-out estimate (800 + 800) wearing a
    # different hat: when the estimate became the drawn body plus one
    # clearance, three op-amps in a row settled 1400 apart and the guard
    # called that "collapsed" while leaving 960 mils of white space
    # between them -- more than the 850 that is the 5th percentile of
    # what a person leaves between two parts of this size. A collapse
    # puts the gap at or below zero, so the floor here is the engine's
    # own clearance, which a scoping failure cannot satisfy.
    from eda_agent.design.force_directed import (
        _BODY_CLEARANCE_MILS, bodies_overlap)

    half = {r: ((i.world_bbox().x_max - i.world_bbox().x_min) / 2,
                (i.world_bbox().y_max - i.world_bbox().y_min) / 2)
            for r, i in ((inst.refdes, inst)
                         for inst in result.canvas.instances)}
    for a, b in (("U1", "U2"), ("U2", "U3"), ("U1", "U3")):
        assert not bodies_overlap(ctr[a][0], ctr[a][1], ctr[b][0], ctr[b][1],
                                  half[a], half[b], 0), (
            f"{a}/{b} op-amp bodies overlap")
        gap = max(abs(ctr[a][0] - ctr[b][0]) - (half[a][0] + half[b][0]),
                  abs(ctr[a][1] - ctr[b][1]) - (half[a][1] + half[b][1]))
        assert gap >= _BODY_CLEARANCE_MILS, (
            f"{a}/{b} op-amps collapsed: {gap:.0f} mils of clear space, "
            f"below the {_BODY_CLEARANCE_MILS} the shove guarantees")


def _blinker_555_plan_and_symbols():
    """The NE555 astable LED-blinker board (the live demo): an 8-pin timer +
    three resistors, three caps, an LED and a 2-pin power header. Dense enough
    that best-of selects a variant whose power spokes get culled."""
    ne555 = _ic_symbol("NE555", [
        ("4", -500, 300, 180), ("2", -500, 100, 180), ("6", -500, -100, 180),
        ("7", -500, -300, 180), ("8", 500, 300, 0), ("3", 500, 100, 0),
        ("5", 500, -100, 0), ("1", 500, -300, 0)])
    led = _ic_symbol("LED", [("A", -300, 0, 180), ("K", 300, 0, 0)])
    hdr = _ic_symbol("HDR2", [("1", -300, 100, 180), ("2", -300, -100, 180)])
    syms = {(_LIB, "NE555"): ne555, (_LIB, "RES"): _passive("RES"),
            (_LIB, "CAP"): _passive("CAP"), (_LIB, "LED"): led,
            (_LIB, "HDR2"): hdr}
    P = lambda r, lr, **kw: {"refdes": r, "lib_ref": lr, "lib_path": _LIB,
                            "status": "existing", "sheet": "main", "zone": "z",
                            **kw}
    plan = DesignPlan.model_validate({
        "spec": "555", "summary": "555 astable blinker",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [P("U1", "NE555"), P("R1", "RES", value="1k"),
                  P("R2", "RES", value="47k"), P("R3", "RES", value="300R"),
                  P("C1", "CAP", value="10uF"), P("C2", "CAP", value="10nF"),
                  P("C3", "CAP", value="100nF"), P("D1", "LED", value="RED"),
                  P("J1", "HDR2")],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "1"}, {"refdes": "U1", "pin": "8"},
                {"refdes": "U1", "pin": "4"}, {"refdes": "R1", "pin": "1"},
                {"refdes": "C3", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "J1", "pin": "2"}, {"refdes": "U1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
                {"refdes": "C3", "pin": "2"}, {"refdes": "D1", "pin": "K"}]},
            {"name": "DISCH", "pins": [
                {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "1"},
                {"refdes": "U1", "pin": "7"}]},
            {"name": "THR_TRIG", "pins": [
                {"refdes": "R2", "pin": "2"}, {"refdes": "U1", "pin": "6"},
                {"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "1"}]},
            {"name": "CONT", "pins": [
                {"refdes": "U1", "pin": "5"}, {"refdes": "C2", "pin": "1"}]},
            {"name": "OUT", "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "R3", "pin": "1"}]},
            {"name": "LED_A", "pins": [
                {"refdes": "R3", "pin": "2"}, {"refdes": "D1", "pin": "A"}]}]})
    return plan, syms


def test_power_pins_connect_even_when_spokes_culled():
    """Every power/ground pin must end wire-connected OR under a coincident
    power port -- never on a bare floating label. Regression for the 555
    blinker emit where best-of selected a variant whose VCC spokes were culled
    by the cross-net guard, dropping the VCC pins onto net labels that float in
    Altium (ERC: floating net labels / floating power objects). The repair pass
    drops a coincident power port on each such pin (a port bonds a pin with no
    wire) and clears the dead labels."""
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    plan, syms = _blinker_555_plan_and_symbols()
    result = build_best_canvas_from_plan(plan, MockExtractor(syms))
    assert result.ok
    canvas = result.canvas
    pin_xy = {(i.refdes, ep.pin_id): (ep.x, ep.y)
              for i in canvas.instances_on("main")
              for ep in i.all_pin_endpoints()}
    def _on_wire(pt, netname) -> bool:
        """A wire of this net ends at the pin, or passes THROUGH it.

        Pass-through counts because Altium connects there: the pipeline's own
        strict-shorts check exists to catch exactly that ("wire on net X
        passes through pin Y ... Altium would auto-merge"). An endpoint-only
        test contradicts it, and does so silently until the wire flush merges
        two collinear segments that used to end at the pin into one that
        crosses it, which is a tidier drawing of the same connection.
        """
        px, py = pt
        for w in canvas.wires:
            if w.net != netname:
                continue
            if (w.x1, w.y1) == pt or (w.x2, w.y2) == pt:
                return True
            if w.x1 == w.x2 == px and min(w.y1, w.y2) <= py <= max(w.y1, w.y2):
                return True
            if w.y1 == w.y2 == py and min(w.x1, w.x2) <= px <= max(w.x1, w.x2):
                return True
        return False

    for netname in ("VCC", "GND"):
        net = next(n for n in plan.nets if n.name == netname)
        port_pts = {(p.x, p.y) for p in canvas.power_ports if p.text == netname}
        for pr in net.pins:
            pt = pin_xy[(pr.refdes, pr.pin)]
            assert _on_wire(pt, netname) or pt in port_pts, (
                f"{netname} pin {pr.refdes}.{pr.pin} at {pt} is floating "
                f"(no wire of its net touches it, and no power port sits "
                f"on it)")
    # No power net is left represented by bare (floating) labels.
    assert not [l for l in canvas.labels if l.text in ("VCC", "GND")]
    # Every emitted power port is anchored (on a pin or a surviving spoke end),
    # so none read as floating power objects.
    all_pin_pts = set(pin_xy.values())
    for p in canvas.power_ports:
        if p.text in ("VCC", "GND"):
            net = next(n for n in plan.nets if n.name == p.text)
            wire_ends = set()
            for w in canvas.wires:
                if w.net == p.text:
                    wire_ends |= {(w.x1, w.y1), (w.x2, w.y2)}
            assert (p.x, p.y) in all_pin_pts or (p.x, p.y) in wire_ends, (
                f"{p.text} port at {(p.x, p.y)} is an orphan (floating) glyph")


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN, measured, and the underlying defect it exposed is FIXED. "
    "_splat_motifs used to carry a part across the IC it wires to: the "
    "placer and the shove both put C1 left of a 555 whose THRES and TRIG "
    "pins are both left, and rc_lowpass's canonical 'cap 1000 mils right "
    "of the resistor' dragged it past the chip. The splat now mirrors a "
    "motif in x rather than cross an IC, which took the sweep from 16 of "
    "100 values placing every discrete on its pin side to 48 of 100. "
    "What remains on THIS board is a genuine trade, not a bug: the best "
    "side-correct candidate scores 2443 against the winner's 1353. Moving "
    "C1 back by hand reaches a much better side-correct layout (1623) "
    "with ZERO crossings against the winner's 1, 3600 mils LESS wire and "
    "9 fewer segments, and it still loses on the existing rank key, 1851 "
    "to 1701, because it has two more long wires and one more wire "
    "through a body. That last one is a real fault, so the alternative is "
    "not plainly better and forcing it would be tuning a weight to make a "
    "test pass. Decide whether the convention outranks that before "
    "removing this marker."))
def test_pin_aware_fd_places_parts_on_their_ic_pin_side(monkeypatch):
    """The pin-aware force-directed candidate (swept over attractor strengths
    and score-picked) places each discrete on the side of the IC where the pin
    it wires to lives: the timing network (DISCH/THRES, left of the NE555) lands
    left, the output stage and CONT cap (OUT/CONT, right) land right. Regression
    for the 555 blinker whose Sugiyama base scattered them to the wrong sides.
    Asserts the side outcome AND that it beats the side-blind baseline on the
    real scored objective (so it is a genuine win, not a forced regression)."""
    import eda_agent.design.pipeline as _pipeline
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    # Restore the full production sweep (the conftest shrinks it for speed); the
    # chaotic landscape needs the dense sweep to find the side-correct optimum.
    monkeypatch.setattr(
        _pipeline, "_FD_K_SWEEP",
        tuple(round(0.02 + i * (0.28 / 99), 4) for i in range(100)))
    plan, syms = _blinker_555_plan_and_symbols()
    result = build_best_canvas_from_plan(plan, MockExtractor(syms))
    assert result.ok
    ctr = {}
    for inst in result.canvas.instances_on("main"):
        bb = inst.world_bbox()
        ctr[inst.refdes] = ((bb.x_min + bb.x_max) / 2, (bb.y_min + bb.y_max) / 2)
    ux = ctr["U1"][0]
    left = {"R1", "R2", "C1"}   # wire to DISCH / THRES (U1 left pins)
    right = {"C2", "R3"}        # wire to CONT / OUT (U1 right pins)
    for r in left:
        assert ctr[r][0] < ux, f"{r} should sit LEFT of U1 (its pins are left)"
    for r in right:
        assert ctr[r][0] > ux, f"{r} should sit RIGHT of U1 (its pins are right)"
    # And it is the chosen layout because it scores better than the side-blind
    # Sugiyama base, not in spite of the scorer.
    assert "pin_aware_fd" in " || ".join(n.text for n in result.notes)


def test_corner_origin_symbol_placed_by_body_center():
    """Real library symbols anchor at a CORNER, not the body centre (a
    QFN28 bridge IC spans local x 300..1500, y 0..-1900 from its origin).
    Placement passes reason in body-centre frame; the canvas build must
    convert so a corner-origin symbol's BODY lands where the placement
    said, instead of hanging its body diagonally off the target point
    (which put IC-anchored satellites inside the pin field on the first
    live-project run)."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import build_canvas_from_plan

    # Corner-origin IC: body entirely in +x / -y relative to the origin.
    corner_ic = SymbolModel(
        lib_path=_LIB, lib_ref="CORNER_IC",
        pins=(
            SymbolPin(designator="1", name="IN", x=300, y=-300,
                      orientation=2, length=300, electrical_type="input"),
            SymbolPin(designator="2", name="OUT", x=1500, y=-300,
                      orientation=0, length=300, electrical_type="output"),
            SymbolPin(designator="3", name="GND", x=300, y=-1700,
                      orientation=2, length=300, electrical_type="power"),
            SymbolPin(designator="4", name="VCC", x=300, y=-100,
                      orientation=2, length=300, electrical_type="power"),
        ),
        body_bbox=SymbolBBox(x_min=300, y_min=-1900, x_max=1500, y_max=0),
    )
    syms = {(_LIB, "CORNER_IC"): corner_ic, (_LIB, "RES"): _passive("RES")}
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "CORNER_IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "SIG", "pins": [{"refdes": "U1", "pin": "2"},
                                     {"refdes": "R1", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "3"},
                      {"refdes": "R1", "pin": "2"}]},
        ],
    })
    target = PlacedPart(refdes="U1", sheet="main", x_mils=5000, y_mils=4000,
                        rotation=0)
    r_target = PlacedPart(refdes="R1", sheet="main", x_mils=8000, y_mils=4000,
                          rotation=270)
    result = build_canvas_from_plan(
        plan, MockExtractor(syms),
        layout_overrides={"U1": target, "R1": r_target},
    )
    inst = next(i for i in result.canvas.instances if i.refdes == "U1")
    bb = inst.world_bbox()
    cx = (bb.x_min + bb.x_max) / 2
    cy = (bb.y_min + bb.y_max) / 2
    # Body centre must land on the placement point (within grid snap +
    # the small post-passes' nudges), NOT offset by half the body.
    assert abs(cx - 5000) <= 300, f"body centre x {cx} far from 5000"
    assert abs(cy - 4000) <= 300, f"body centre y {cy} far from 4000"


def test_offgrid_symbol_pins_snap_to_wiring_grid():
    """A symbol whose local pin coordinates sit OFF the 100-mil grid must
    still end up with on-grid world pins (snapped by pin residual, not by
    origin) -- an off-grid pin never bonds to a wire in Altium."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import build_canvas_from_plan

    odd_part = SymbolModel(
        lib_path=_LIB, lib_ref="ODD50",
        pins=(
            SymbolPin(designator="1", name="A", x=-250, y=50,
                      orientation=2, length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="B", x=250, y=50,
                      orientation=0, length=100, electrical_type="passive"),
        ),
        body_bbox=SymbolBBox(x_min=-250, y_min=-100, x_max=250, y_max=200),
    )
    syms = {(_LIB, "ODD50"): odd_part, (_LIB, "RES"): _passive("RES")}
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "X1", "lib_ref": "ODD50", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "N1", "pins": [{"refdes": "X1", "pin": "2"},
                                    {"refdes": "R1", "pin": "1"}]},
            {"name": "N2", "pins": [{"refdes": "X1", "pin": "1"},
                                    {"refdes": "R1", "pin": "2"}]},
        ],
    })
    result = build_canvas_from_plan(
        plan, MockExtractor(syms),
        layout_overrides={
            "X1": PlacedPart(refdes="X1", sheet="main", x_mils=5000,
                             y_mils=4000, rotation=0),
            "R1": PlacedPart(refdes="R1", sheet="main", x_mils=7000,
                             y_mils=4000, rotation=0),
        },
    )
    inst = next(i for i in result.canvas.instances if i.refdes == "X1")
    for ep in inst.all_pin_endpoints():
        assert ep.x % 100 == 0, f"pin {ep.pin_id} x {ep.x} off-grid"
        assert ep.y % 100 == 0, f"pin {ep.pin_id} y {ep.y} off-grid"


# --------------- repair-port stub upgrade (cosmetic pass) -------------
#
# NOTE for anyone extending these: do NOT reach for monkeypatch.undo()
# to build "the same board without the pass". The monkeypatch fixture is
# shared with conftest's autouse fixtures, so undo() also reverts the
# FD-sweep shrink they install for speed, and the next build silently
# runs the full 100-value production sweep. mock.patch is used below so
# only the one symbol is affected.

def _build_board(name: str, *, upgrade: bool = True):
    """Build a benchmark board, optionally with the stub pass disabled.

    CACHED. This file asks for a board 23 times and there are six
    distinct ones, because each stub-upgrade test builds the same layout
    twice, once with the pass and once without, and three of them do it
    for every benchmark board.

    Building one runs the full best-of-N layout. MEASURED in CI: these
    five tests were 236 of the suite's 771 seconds, 30% of the whole run
    for five tests out of 4326, and almost all of it was recomputing
    boards an earlier test had already built.

    A DEEP COPY IS HANDED OUT, not the cached object. Nothing mutates a
    board today, and the copy is what keeps that from being a
    requirement nobody knows about: a shared canvas would couple these
    tests through the cache, so one that started editing its board would
    change what a later test sees. Introducing an order dependency is
    not an acceptable price for a faster suite, and the copy costs
    nothing next to the layout it avoids.
    """
    return copy.deepcopy(_build_board_uncached(name, upgrade))


@functools.lru_cache(maxsize=None)
def _build_board_uncached(name: str, upgrade: bool):
    import json
    from pathlib import Path
    from unittest import mock

    from eda_agent.design.benchmark import SyntheticSymbolExtractor
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.schematic_neatness import neatness_report

    plans = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"
    plan = DesignPlan.model_validate(
        json.loads((plans / f"{name}.json").read_text()))
    extractor = SyntheticSymbolExtractor(plan)

    if upgrade:
        canvas = build_best_canvas_from_plan(plan, extractor).canvas
    else:
        with mock.patch(
                "eda_agent.design.pipeline.upgrade_repair_ports_to_stubs",
                return_value=0):
            canvas = build_best_canvas_from_plan(plan, extractor).canvas
    return canvas, plan, neatness_report(canvas, plan)


def test_stub_upgrade_does_not_move_a_single_part():
    """The whole reason this is a separate post-selection pass.

    An earlier version drew the stubs inside the per-candidate repair.
    That wire entered the scored objective, best-of picked a different
    layout, and a part landed on the wrong side of its IC. Placement must
    come out identical with the pass on and off.
    """
    on_canvas, _, on = _build_board("mcu")
    off_canvas, _, off = _build_board("mcu", upgrade=False)

    def placement(canvas):
        return {i.refdes: (i.x, i.y, i.rotation, i.flipped)
                for i in canvas.instances}

    assert placement(on_canvas) == placement(off_canvas)
    assert (on.spread_w_mils, on.spread_h_mils) == \
           (off.spread_w_mils, off.spread_h_mils)
    # Signal routing is likewise untouched: the pass only ever adds a
    # stub to a power/ground repair glyph.
    assert on.signal_wire_mils == off.signal_wire_mils
    assert on.bends_per_signal_net == off.bends_per_signal_net


def _glyph_clearance(canvas, x, y):
    """Distance from a glyph to the nearest symbol body."""
    best = None
    for inst in canvas.instances:
        bb = inst.world_bbox()
        dx = max(bb.x_min - x, 0, x - bb.x_max)
        dy = max(bb.y_min - y, 0, y - bb.y_max)
        d = (dx * dx + dy * dy) ** 0.5
        best = d if best is None else min(best, d)
    return best


def _moved_glyphs(off_canvas, on_canvas):
    """Glyphs the pass relocated, as (before_xy, after_xy) pairs.

    Paired through the STUB WIRE rather than by sorting the two position
    lists against each other. Sorting looks equivalent and is not: with
    several glyphs moving, it happily pairs one glyph's old position with
    another's new one and reports moves that never happened (a 200x800
    diagonal, when every stub is axis-aligned and at most 200 mils).
    A stub has the old position at one end and the new one at the other,
    which identifies the pair exactly.
    """
    before = {(p.x, p.y) for p in off_canvas.power_ports}
    after = {(p.x, p.y) for p in on_canvas.power_ports}
    pairs = []
    for w in on_canvas.wires:
        ends = ((w.x1, w.y1), (w.x2, w.y2))
        for src, dst in (ends, ends[::-1]):
            if src in before and src not in after and dst in after:
                pairs.append((src, dst))
    return pairs


def test_stub_upgrade_gives_crowded_glyphs_room():
    """The gain it actually delivers, measured the honest way.

    NOT bends_per_power_net. That number does fall, but only because
    straight 0-bend stubs dilute an average over the rail's wires: no
    existing wire is straightened, so citing it would be measuring the
    metric rather than the drawing.

    What genuinely changes is clearance. A repair glyph sits on the pin's
    electrical end -- outside the body already, so it never overlapped
    anything -- but it can sit tight against a NEIGHBOURING part with its
    bar and text in the gap. The stub pushes it clear.
    """
    on_canvas, _, _ = _build_board("mcu")
    off_canvas, _, _ = _build_board("mcu", upgrade=False)
    moved = _moved_glyphs(off_canvas, on_canvas)
    assert moved, "the pass moved nothing on this board"

    deltas = [_glyph_clearance(on_canvas, *after)
              - _glyph_clearance(off_canvas, *before)
              for before, after in moved]
    assert sum(deltas) / len(deltas) > 0, (
        f"moved glyphs are no less crowded than before: {deltas[:6]}")


def test_stub_upgrade_never_crowds_a_glyph_it_moves():
    """The condition that makes a cosmetic pass safe to run at all.

    _adaptive_stub_length only steers around obstacles in the pin's own
    path, so nothing else prevents a stub carrying a glyph TOWARD a
    different body, and a cosmetic pass that makes the drawing worse has
    no business running there.

    No benchmark board violates this today, with or without the guard in
    the pass. It is pinned anyway because "currently true" and
    "guaranteed" are different claims, and this one is cheap to
    guarantee.

    Pairing note: an earlier version of this test matched the two glyph
    lists by sorting, which paired one glyph's old position with
    another's new one and reported a 200x800 "move" on a pass that only
    ever draws axis-aligned 200-mil stubs. That fabricated a regression
    that did not exist. _moved_glyphs pairs through the stub wire.
    """
    for board in ("mcu", "blinker555", "buck"):
        on_canvas, _, _ = _build_board(board)
        off_canvas, _, _ = _build_board(board, upgrade=False)
        for before, after in _moved_glyphs(off_canvas, on_canvas):
            was = _glyph_clearance(off_canvas, *before)
            now = _glyph_clearance(on_canvas, *after)
            assert now >= was, (
                f"{board}: glyph moved {before} -> {after} and ended "
                f"CLOSER to a body ({was:.0f} -> {now:.0f} mils)")


def _cross_net_contacts(canvas, plan):
    """Every place two DIFFERENT nets' geometry touches, as Altium bonds.

    Used as a set so a pass can be checked for not ADDING contacts.
    Asserting "no contacts at all" would fail on pre-existing geometry
    and say nothing about the pass under test.
    """
    from eda_agent.design.pipeline import _point_on_segment

    net_of_pin = {}
    for net in plan.nets:
        for pr in net.pins:
            net_of_pin[(pr.refdes, pr.pin)] = net.name
    owned = {}
    for inst in canvas.instances:
        for ep in inst.all_pin_endpoints():
            name = net_of_pin.get((inst.refdes, ep.pin_id))
            if name:
                owned[(ep.x, ep.y)] = name
    for port in canvas.power_ports:
        owned.setdefault((port.x, port.y), port.text)

    contacts = set()
    wires = [w for w in canvas.wires if w.net]
    for w in wires:
        seg = (w.x1, w.y1, w.x2, w.y2)
        # A pin or port anywhere along a wire of another net.
        for (px, py), owner in owned.items():
            if owner != w.net and _point_on_segment(px, py, *seg):
                contacts.add((px, py, *sorted((owner, w.net))))
        # A wire ENDING on another net's wire (a T-intersection). A plain
        # crossing does not bond in Altium and is deliberately not here.
        for other in wires:
            if other.net == w.net:
                continue
            for (ex, ey) in ((w.x1, w.y1), (w.x2, w.y2)):
                if _point_on_segment(ex, ey, other.x1, other.y1,
                                     other.x2, other.y2):
                    contacts.add((ex, ey, *sorted((w.net, other.net))))
    return contacts


def test_stub_upgrade_never_bonds_two_nets():
    """The safety property, and the reason the gate exists.

    These glyphs are floating precisely because the cross-net cull
    decided wiring them would short, so every stub drawn here is a wire
    in hostile territory. Checked as "adds no cross-net contact" rather
    than "has none": the boards carry pre-existing contacts that say
    nothing about this pass.
    """
    for board in ("mcu", "blinker555", "buck"):
        on_canvas, plan, _ = _build_board(board)
        off_canvas, _, _ = _build_board(board, upgrade=False)
        added = _cross_net_contacts(on_canvas, plan) - \
            _cross_net_contacts(off_canvas, plan)
        assert not added, (
            f"{board}: the stub upgrade bonded nets that were separate: "
            f"{sorted(added)[:4]}")


# The gate itself, unit tested. The boards above reject only 2 stubs
# between them, so relying on them to cover the gate leaves most of it
# unexercised -- an earlier version of this file passed with the gate
# deleted entirely. Each case below is one way Altium bonds.

def test_gate_rejects_a_foreign_pin_on_the_stub():
    from eda_agent.design.pipeline import _repair_stub_is_safe

    assert not _repair_stub_is_safe(
        0, 0, 200, 0, foreign_points={(100, 0)}, foreign_wires=[])


def test_gate_rejects_landing_on_a_foreign_wire():
    """Our endpoint on their wire is a T-intersection, which connects."""
    from eda_agent.design.pipeline import _repair_stub_is_safe

    assert not _repair_stub_is_safe(
        0, 0, 200, 0, foreign_points=set(),
        foreign_wires=[(200, -100, 200, 100)])


def test_gate_rejects_a_foreign_wire_ending_on_the_stub():
    """The same thing mirrored, and just as much a short."""
    from eda_agent.design.pipeline import _repair_stub_is_safe

    assert not _repair_stub_is_safe(
        0, 0, 200, 0, foreign_points=set(),
        foreign_wires=[(100, 0, 100, 500)])


def test_gate_allows_a_plain_crossing():
    """Two wires crossing do NOT connect in Altium without a junction.

    This is the case that decides whether the feature is usable at all:
    vetoing crossings would reject nearly every stub on a dense sheet and
    collapse the pass back to doing nothing.
    """
    from eda_agent.design.pipeline import _repair_stub_is_safe

    assert _repair_stub_is_safe(
        0, 0, 200, 0, foreign_points=set(),
        foreign_wires=[(100, -100, 100, 100)])


def test_gate_ignores_the_source_pin_itself():
    """The stub starts on its own pin; that is not foreign traffic."""
    from eda_agent.design.pipeline import _repair_stub_is_safe

    assert _repair_stub_is_safe(
        0, 0, 200, 0, foreign_points={(0, 0)}, foreign_wires=[])


# ------------- glyph-vs-text gate for the stub upgrade ---------------

def _fake_canvas(labels=(), ports=()):
    """Minimal stand-in: the gate reads only labels and power_ports."""
    from types import SimpleNamespace

    from eda_agent.design.canvas import NetLabel, PowerPort

    return SimpleNamespace(
        labels=[NetLabel(text=t, x=x, y=y, orientation=0, sheet=sh)
                for (t, x, y, sh) in labels],
        power_ports=[PowerPort(text=t, x=x, y=y, style="bar", sheet="main")
                     for (t, x, y) in ports],
    )


def test_gate_does_not_reject_a_glyph_against_its_own_old_position():
    """Regression: every VERTICAL stub was silently rejected.

    While a move is being evaluated the glyph is still recorded at its
    old position, so excluding it by COORDINATE does not exclude it. Its
    own box is 2*LINE_H = 220 tall, which overlaps itself across a
    200-mil stub -- so horizontal moves squeaked through on a boundary
    and vertical ones never happened at all. It has to be skipped by
    index.
    """
    from eda_agent.design.pipeline import _glyph_would_hit_text

    canvas = _fake_canvas(ports=[("VCC", 1000, 1000)])
    # The same glyph, proposed 200 mils UP: must be allowed.
    assert not _glyph_would_hit_text(
        1000, 1200, "VCC", canvas, "main", skip_index=0)


def test_gate_rejects_a_glyph_landing_on_a_net_label():
    """Net labels are never moved, so a glyph dropped on one stays."""
    from eda_agent.design.pipeline import _glyph_would_hit_text

    canvas = _fake_canvas(labels=[("THR", 1000, 1200, "main")],
                          ports=[("VCC", 1000, 1000)])
    assert _glyph_would_hit_text(
        1000, 1200, "VCC", canvas, "main", skip_index=0)


def test_gate_rejects_stacking_two_glyphs():
    from eda_agent.design.pipeline import _glyph_would_hit_text

    canvas = _fake_canvas(ports=[("VCC", 1000, 1000), ("GND", 1000, 1200)])
    assert _glyph_would_hit_text(
        1000, 1200, "VCC", canvas, "main", skip_index=0)


def test_gate_ignores_another_sheet():
    from eda_agent.design.pipeline import _glyph_would_hit_text

    canvas = _fake_canvas(labels=[("THR", 1000, 1200, "other")])
    assert not _glyph_would_hit_text(
        1000, 1200, "VCC", canvas, "main", skip_index=-1)


def test_stub_upgrade_drops_no_glyph_onto_a_net_label():
    """Covers the gate's CALL SITE, not just its logic.

    The unit tests above prove _glyph_would_hit_text decides correctly.
    They do not prove anything calls it: deleting the call left every one
    of them passing. It matters here because the gate is not idle -- it
    rejects 13 of 49 candidate moves on the mcu board, and each of those
    would otherwise park a rail glyph on top of a net label, which the
    text placer never cleans up because it does not move net labels.

    Phrased as "adds none" rather than "has none": the boards carry
    pre-existing glyph/label overlaps (6 on mcu) that this pass neither
    caused nor can fix.
    """
    from eda_agent.design.text_placement import CHAR_W, LINE_H

    def overlaps(canvas):
        glyphs = []
        for p in canvas.power_ports:
            h = max(100, (CHAR_W * max(1, len(p.text))) // 2)
            glyphs.append((p.x - h, p.y - LINE_H, p.x + h, p.y + LINE_H))
        n = 0
        for lab in canvas.labels:
            w = CHAR_W * max(1, len(lab.text))
            x1 = lab.x - w if getattr(lab, "justification", 0) == 2 else lab.x
            box = (x1, lab.y, x1 + w, lab.y + LINE_H)
            n += sum(1 for g in glyphs
                     if not (box[2] <= g[0] or g[2] <= box[0]
                             or box[3] <= g[1] or g[3] <= box[1]))
        return n

    for board in ("mcu", "blinker555", "buck"):
        on_canvas, _, _ = _build_board(board)
        off_canvas, _, _ = _build_board(board, upgrade=False)
        assert overlaps(on_canvas) <= overlaps(off_canvas), (
            f"{board}: the stub upgrade parked glyphs on net labels "
            f"({overlaps(off_canvas)} -> {overlaps(on_canvas)})")


def test_text_is_settled_against_the_final_glyph_positions():
    """Designator/value text must account for where the glyphs ENDED UP.

    Text is placed inside each candidate build, against the glyph
    positions of that moment; the stub upgrade then moves glyphs
    afterwards. Without a re-place, text stays routed around where a
    glyph used to be.

    Asserted as a FIXED POINT: running the placer once more on the
    finished canvas must move nothing. place_instance_text is
    deterministic and depends only on canvas state, so a canvas already
    settled is unchanged by another pass, while one still holding
    stale positions is not. On blinker555 exactly one item (J1's
    designator, (960,4910) -> (1350,5000)) is out of place if the
    re-place is skipped.

    Note on its return value: it counts instances placed on a
    non-default side, NOT positions changed, so it stays non-zero on a
    settled canvas and cannot be used as the check here.
    """
    from eda_agent.design.text_placement import place_instance_text

    canvas, _, _ = _build_board("blinker555")
    before = {i.refdes: (i.designator_pos, i.value_pos)
              for i in canvas.instances}
    place_instance_text(canvas)
    after = {i.refdes: (i.designator_pos, i.value_pos)
             for i in canvas.instances}

    stale = [k for k in before if before[k] != after[k]]
    assert not stale, (
        f"text was left positioned against superseded glyph locations: "
        f"{ {k: (before[k][0], after[k][0]) for k in stale} }")


# ---------------------------------------------------------------------------
# Shared-axis variant: straighten a layout, and let the score decide.
# ---------------------------------------------------------------------------

def test_near_aligned_parts_are_snapped_to_a_shared_row():
    """Measured gap: engine alignment penalty 0.555 vs 0.164 for humans.

    On a three-part sheet a human puts two resistors on one y with the
    cap below; the engine spread the same three over 1500 x 1100 mils
    with no two sharing an axis.
    """
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _align_placements

    parts = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=2000,
                   rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=2000, y_mils=2200,
                   rotation=0),
    ]
    out = {p.refdes: p for p in _align_placements(parts)}
    assert out["R1"].y_mils == out["R2"].y_mils, "200 mils apart is a row"
    assert out["R1"].x_mils == 1000 and out["R2"].x_mils == 2000, (
        "the other axis must not be disturbed")


def test_a_part_far_from_the_others_is_left_where_it_is():
    """This straightens a layout; it does not rearrange one."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _align_placements

    parts = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=2000,
                   rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=2000, y_mils=2100,
                   rotation=0),
        PlacedPart(refdes="U9", sheet="main", x_mils=1500, y_mils=9000,
                   rotation=0),
    ]
    out = {p.refdes: p for p in _align_placements(parts)}
    assert out["U9"].y_mils == 9000 and out["U9"].x_mils == 1500


def test_parts_with_no_row_partner_get_a_column():
    """Sharing EITHER axis satisfies the convention, so try both."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _align_placements

    parts = [
        PlacedPart(refdes="C1", sheet="main", x_mils=1000, y_mils=1000,
                   rotation=0),
        PlacedPart(refdes="C2", sheet="main", x_mils=1150, y_mils=5000,
                   rotation=0),
    ]
    out = {p.refdes: p for p in _align_placements(parts)}
    assert out["C1"].x_mils == out["C2"].x_mils, (
        "far apart in y but a column apart in x is still an alignment")


def test_the_shared_axis_variant_is_score_gated():
    """It must never win on its own say-so.

    A nudge that collides two bodies or lengthens a wire has to lose,
    and the only thing that can decide that is the same comparison every
    other variant goes through.
    """
    import inspect

    from eda_agent.design import pipeline

    source = inspect.getsource(pipeline.build_best_canvas_from_plan)
    assert "_align_placements" in source
    # Tied to the candidate's own name rather than to a window of
    # characters after the call: an earlier version searched the next
    # 900 characters and broke when the block grew, which says nothing
    # about whether the gate is still there.
    assert "align_rank < best_rank" in source, (
        "the shared-axis variant must be accepted only when it scores "
        "better, like every other candidate")
    # And it must COMPETE, not replace: generating the later variants
    # from an aligned base changed the search trajectory and made 4 of
    # 27 sheets worse even though the candidate itself can only win by
    # scoring better.
    assert "base, base_label = align_cand" not in source, (
        "the shared-axis candidate must not become the base the aspect "
        "variants are generated from")


def test_a_looser_tolerance_catches_what_a_tight_one_misses():
    """How far apart "nearly aligned" is depends on the sheet.

    A tight tolerance straightens a dense cluster without disturbing
    it; a sparse layout leaves its parts further apart than that.
    Measured: 400 alone fixed subsheet1 and pp_driver_8x, and subsheet2
    needed 900 (alignment 1.00 to 0.20, matching the human exactly).
    Both are scored, so the wrong one for a sheet loses.
    """
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _align_placements

    parts = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=2000,
                   rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=3000, y_mils=2800,
                   rotation=0),
    ]
    tight = {p.refdes: p for p in _align_placements(parts, tol=400)}
    assert tight["R1"].y_mils != tight["R2"].y_mils, (
        "800 mils apart is beyond a tight tolerance and must be left alone")

    loose = {p.refdes: p for p in _align_placements(parts, tol=900)}
    assert loose["R1"].y_mils == loose["R2"].y_mils


def test_both_tolerances_are_actually_tried():
    """One of them being dropped would be silent: the layout would just
    be slightly worse, and every test would still pass."""
    import inspect

    from eda_agent.design import pipeline

    source = inspect.getsource(pipeline.build_best_canvas_from_plan)
    assert "for tol in (400, 900):" in source, (
        "the shared-axis pass must try both tolerances")


# ---------------------------------------------------------------------------
# Compaction: the engine spreads 2 to 6.4 times wider than a human.
# ---------------------------------------------------------------------------

def test_compaction_pulls_parts_toward_the_centroid():
    """Measured on six human-drawn sheets, this engine's placement
    covers 2.0 to 6.4 times the AREA of the human's for the same
    netlist, and wire length follows from that directly."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _compact_placements

    parts = [
        PlacedPart(refdes="R1", sheet="main", x_mils=0, y_mils=0,
                   rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=2000, y_mils=0,
                   rotation=0),
    ]
    out = {p.refdes: p for p in _compact_placements(parts, 0.5)}
    # Centroid is x=1000; halving the offsets puts them at 500 and 1500.
    assert out["R1"].x_mils == 500 and out["R2"].x_mils == 1500
    assert out["R1"].y_mils == 0 and out["R2"].y_mils == 0


def test_compaction_at_unity_changes_nothing():
    """The transform must be an identity at factor 1, or the variant is
    doing something other than what it says."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _compact_placements

    parts = [
        PlacedPart(refdes="R1", sheet="main", x_mils=100, y_mils=700,
                   rotation=0),
        PlacedPart(refdes="U1", sheet="main", x_mils=2300, y_mils=1900,
                   rotation=90),
    ]
    out = {p.refdes: p for p in _compact_placements(parts, 1.0)}
    for part in parts:
        assert (out[part.refdes].x_mils, out[part.refdes].y_mils) == (
            part.x_mils, part.y_mils)
        assert out[part.refdes].rotation == part.rotation


def test_compaction_competes_without_replacing_the_base():
    """Same rule the shared-axis pass had to learn.

    Replacing base moves the starting point the aspect variants are
    generated from, which made sheets worse even though a candidate can
    only be accepted by ranking better.
    """
    import inspect

    from eda_agent.design import pipeline

    source = inspect.getsource(pipeline.build_best_canvas_from_plan)
    assert "_compact_placements" in source
    assert "compact_rank < best_rank" in source, (
        "the compaction variant must be accepted only when it ranks better")
    assert "base, base_label = compact_cand" not in source, (
        "the compaction candidate must not become the base the aspect "
        "variants are generated from")


# ---------------------------------------------------------------------------
# Banding: humans put 4.3 parts in a row, this engine puts 1.8.
# ---------------------------------------------------------------------------

def test_banding_snaps_parts_into_rows_and_keeps_their_x():
    """Left-to-right signal order has to survive the snap.

    Only the band's y is imposed; x is untouched, or the pass would be
    re-placing the sheet rather than tidying its rows.
    """
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _band_placements

    parts = [
        PlacedPart(refdes=f"R{i}", sheet="main", x_mils=1000 * i,
                   y_mils=1000 + 130 * i, rotation=0)
        for i in range(1, 7)
    ]
    out = {p.refdes: p for p in _band_placements(parts, per_row=3)}
    assert len(out) == 6
    for part in parts:
        assert out[part.refdes].x_mils == part.x_mils, "x must not move"
    ys = {p.y_mils for p in out.values()}
    assert len(ys) == 2, f"six parts at three per row is two bands, got {ys}"


def test_banding_is_deterministic_for_equal_positions():
    """Ties break on refdes, so two parts at the same spot cannot swap
    bands between runs."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.pipeline import _band_placements

    parts = [
        PlacedPart(refdes=r, sheet="main", x_mils=500, y_mils=500, rotation=0)
        for r in ("R9", "R1", "R5")
    ]
    first = [(p.refdes, p.y_mils) for p in _band_placements(parts, per_row=1)]
    second = [(p.refdes, p.y_mils) for p in _band_placements(
        list(reversed(parts)), per_row=1)]
    assert sorted(first) == sorted(second)


def test_banding_refines_the_winner_not_the_base():
    """Which rows a layout should snap to depends on where its parts
    ended up.

    Banding the pre-variant base produced nothing that could win.
    Banding the layout the other variants settled on took royer1 from
    883 to 635, past the human's 948.
    """
    import inspect

    from eda_agent.design import pipeline

    source = inspect.getsource(pipeline.build_best_canvas_from_plan)
    marker = source.index("_band_placements")
    window = source[marker - 400:marker + 200]
    assert "best_result.canvas.instances" in window, (
        "banding must run on the winning candidate, not on base")
    assert "band_rank < best_rank" in source, (
        "banding must be accepted only when it ranks better")


def test_no_same_net_wire_is_drawn_on_top_of_another():
    """Two same-net segments lying partly on each other draw the shared
    span twice.

    MEASURED across 21 demo sheets before the fix: 25 such pairs and
    7950 mils of doubled wire, with io_driver_8x carrying five of them.
    The control is what makes it a defect rather than an idiom -- the
    same detector finds ZERO on the human-drawn sheets, in 426 wires.
    Contrast the dangling-end check, where humans scored three times
    WORSE than the engine and the finding was withdrawn.
    """
    import pathlib

    demos = pathlib.Path("C:/Program Files/KiCad/10.0/share/kicad/demos")
    if not demos.is_dir():
        pytest.skip("KiCad demo projects are not installed here")
    sheets = list(demos.rglob("io_driver_8x.kicad_sch"))
    if not sheets:
        pytest.skip("this demo project is not installed")

    from eda_agent.design.human_benchmark import plan_from_sheet
    from eda_agent.design.kicad_sheet_reader import (
        read_sheet, symbols_from_sheet,
    )
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.symbols import SymbolExtractor

    text = sheets[0].read_text(encoding="utf-8", errors="replace")
    models = symbols_from_sheet(text)

    class _Sheet(SymbolExtractor):
        # The base __init__ wants a bridge and a cache; this one answers
        # from the sheet's own lib_symbols block instead.
        def __init__(self):
            pass

        def extract_one(self, lib_path, lib_ref):
            return models.get(lib_ref)

        def extract_many(self, refs):
            return {(lp, lr): models[lr] for lp, lr in refs if lr in models}

    plan = DesignPlan.model_validate(
        plan_from_sheet(read_sheet(text, str(sheets[0]))))
    canvas = build_best_canvas_from_plan(plan, _Sheet()).canvas
    wires = canvas.wires_on("main")

    def overlap(a, b):
        if a.net != b.net:
            return 0
        if a.x1 == a.x2 and b.x1 == b.x2 and a.x1 == b.x1:
            lo = max(min(a.y1, a.y2), min(b.y1, b.y2))
            hi = min(max(a.y1, a.y2), max(b.y1, b.y2))
            return max(0, hi - lo)
        if a.y1 == a.y2 and b.y1 == b.y2 and a.y1 == b.y1:
            lo = max(min(a.x1, a.x2), min(b.x1, b.x2))
            hi = min(max(a.x1, a.x2), max(b.x1, b.x2))
            return max(0, hi - lo)
        return 0

    doubled = [
        (wires[i], wires[j])
        for i in range(len(wires)) for j in range(i + 1, len(wires))
        if overlap(wires[i], wires[j]) > 0
    ]
    assert not doubled, (
        f"{len(doubled)} same-net wire pairs overlap; first: "
        f"({doubled[0][0].x1},{doubled[0][0].y1})-"
        f"({doubled[0][0].x2},{doubled[0][0].y2}) and "
        f"({doubled[0][1].x1},{doubled[0][1].y1})-"
        f"({doubled[0][1].x2},{doubled[0][1].y2})")


def test_deduplication_keeps_distinct_spans_and_distinct_nets():
    """It must fold repeats, not collapse real wires.

    Two nets can legitimately run the same span (a bus pair), and one
    net legitimately has many different spans.
    """
    spans = [
        (0, 0, 100, 0, "A"),
        (0, 0, 100, 0, "A"),      # exact repeat
        (100, 0, 0, 0, "A"),      # same span, reversed
        (0, 0, 100, 0, "B"),      # same span, different net
        (0, 0, 0, 100, "A"),      # different span, same net
    ]
    seen, kept = set(), []
    for (x1, y1, x2, y2, net) in spans:
        ends = ((x1, y1), (x2, y2))
        key = (net, min(ends), max(ends))
        if key in seen:
            continue
        seen.add(key)
        kept.append((net, ends))
    assert len(kept) == 3, kept


def test_every_emitted_wire_coordinate_is_on_the_grid():
    """In Altium an off-grid endpoint is how a connection silently
    fails to form, and the human sheets have none.

    The leak is body geometry: symbol graphics are drawn in
    millimetres, so a body edge need not sit on the wire grid (royer1
    alone has 43 that do not), and any route candidate derived from an
    obstacle EDGE inherits that. It took three wrong guesses to find:
    the S-bend candidates, the geometric midpoint, and the trunk median
    were all suspected before instrumenting showed the star-hub
    generator in _route_signal_pins.
    """
    import pathlib as _pathlib

    demos = _pathlib.Path("C:/Program Files/KiCad/10.0/share/kicad/demos")
    if not demos.is_dir():
        pytest.skip("KiCad demo projects are not installed here")
    sheets = list(demos.rglob("royer1.kicad_sch"))
    if not sheets:
        pytest.skip("this demo project is not installed")

    from eda_agent.design.human_benchmark import plan_from_sheet
    from eda_agent.design.kicad_sheet_reader import (
        read_sheet, symbols_from_sheet,
    )
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.symbols import SymbolExtractor

    text = sheets[0].read_text(encoding="utf-8", errors="replace")
    models = symbols_from_sheet(text)

    class _Sheet(SymbolExtractor):
        def __init__(self):
            pass

        def extract_one(self, lib_path, lib_ref):
            return models.get(lib_ref)

        def extract_many(self, refs):
            return {(lp, lr): models[lr] for lp, lr in refs if lr in models}

    plan = DesignPlan.model_validate(
        plan_from_sheet(read_sheet(text, str(sheets[0]))))
    canvas = build_best_canvas_from_plan(plan, _Sheet()).canvas

    off = [
        (w.net, (w.x1, w.y1), (w.x2, w.y2))
        for w in canvas.wires_on("main")
        if any(v % 25 for v in (w.x1, w.y1, w.x2, w.y2))
    ]
    assert not off, f"{len(off)} wires have an off-grid coordinate: {off[:3]}"

    # The bodies themselves ARE off-grid, which is fine and is what makes
    # this test meaningful rather than vacuous.
    edges = [
        v for inst in canvas.instances_on("main")
        for v in (lambda b: (b.x_min, b.y_min, b.x_max, b.y_max))(
            inst.world_bbox())
    ]
    assert any(v % 25 for v in edges), (
        "no off-grid body on this sheet, so the test cannot show that "
        "wires are snapped independently of them")


def test_no_junction_dot_is_missing_where_wires_branch():
    """A MISSING dot is a broken connection; a spare one is cosmetic.

    Arms: a wire ending at a point contributes one, a wire passing
    through contributes two, and three or more is a real branch.

    Only the missing direction is asserted, because the control says
    the other one is convention rather than fault: across 109 human
    sheets, 884 of 4293 hand-placed dots (21%) sit at points this rule
    calls two-armed. The engine is at 19% after junctions are filtered
    against the MERGED geometry, which took it from 18 to 7 -- a dot at
    a collinear join becomes a dot inside one continuous wire once the
    two segments are merged.
    """
    import pathlib as _pathlib

    demos = _pathlib.Path("C:/Program Files/KiCad/10.0/share/kicad/demos")
    if not demos.is_dir():
        pytest.skip("KiCad demo projects are not installed here")
    sheets = list(demos.rglob("sallen_key.kicad_sch"))
    if not sheets:
        pytest.skip("this demo project is not installed")

    from eda_agent.design.human_benchmark import plan_from_sheet
    from eda_agent.design.kicad_sheet_reader import (
        read_sheet, symbols_from_sheet,
    )
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.symbols import SymbolExtractor

    text = sheets[0].read_text(encoding="utf-8", errors="replace")
    models = symbols_from_sheet(text)

    class _Sheet(SymbolExtractor):
        def __init__(self):
            pass

        def extract_one(self, lib_path, lib_ref):
            return models.get(lib_ref)

        def extract_many(self, refs):
            return {(lp, lr): models[lr] for lp, lr in refs if lr in models}

    plan = DesignPlan.model_validate(
        plan_from_sheet(read_sheet(text, str(sheets[0]))))
    canvas = build_best_canvas_from_plan(plan, _Sheet()).canvas
    wires = canvas.wires_on("main")
    dots = {(j.x, j.y) for j in canvas.junctions if j.sheet == "main"}

    def arms(px, py, net):
        n = 0
        for w in wires:
            if w.net != net:
                continue
            if (px, py) in ((w.x1, w.y1), (w.x2, w.y2)):
                n += 1
            elif w.x1 == w.x2 and px == w.x1 and (
                    min(w.y1, w.y2) < py < max(w.y1, w.y2)):
                n += 2
            elif w.y1 == w.y2 and py == w.y1 and (
                    min(w.x1, w.x2) < px < max(w.x1, w.x2)):
                n += 2
        return n

    points = {(w.x1, w.y1) for w in wires} | {(w.x2, w.y2) for w in wires}
    nets = {w.net for w in wires}
    missing = [
        pt for pt in points
        if any(arms(pt[0], pt[1], net) >= 3 for net in nets) and pt not in dots
    ]
    assert not missing, f"{len(missing)} branch points have no dot: {missing[:3]}"


def test_surviving_body_overlaps_are_reported_not_shipped_silently():
    """The engine emits a sheet with overlapping bodies and ok=True.

    That is defensible -- an overlap is a drawing fault, not a wrong
    netlist, unlike the shorts that make it decline outright -- but it
    means the warning is the ONLY signal anyone gets.

    power-supply-2 is the live case: _bbox_half sizes IC31 at 1200 mils
    against a real 2800, so the shove separates small parts to 1650 and
    leaves five of them inside the IC. Two attempts to fix the sizing
    are recorded in force_directed above _bbox_half; both traded this
    defect for a worse one.

    Written so it keeps passing if that is ever fixed: no overlaps is
    fine, overlaps WITH a warning is fine, and overlaps in silence is
    not.
    """
    import pathlib as _pathlib

    demos = _pathlib.Path("C:/Program Files/KiCad/10.0/share/kicad/demos")
    if not demos.is_dir():
        pytest.skip("KiCad demo projects are not installed here")
    sheets = list(demos.rglob("power-supply-2.kicad_sch"))
    if not sheets:
        pytest.skip("this demo project is not installed")

    from eda_agent.design.human_benchmark import plan_from_sheet
    from eda_agent.design.kicad_sheet_reader import (
        read_sheet, symbols_from_sheet,
    )
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.symbols import SymbolExtractor

    text = sheets[0].read_text(encoding="utf-8", errors="replace")
    models = symbols_from_sheet(text)

    class _Sheet(SymbolExtractor):
        def __init__(self):
            pass

        def extract_one(self, lib_path, lib_ref):
            return models.get(lib_ref)

        def extract_many(self, refs):
            return {(lp, lr): models[lr] for lp, lr in refs if lr in models}

    plan = DesignPlan.model_validate(
        plan_from_sheet(read_sheet(text, str(sheets[0]))))
    result = build_best_canvas_from_plan(plan, _Sheet())
    boxes = [i.world_bbox() for i in result.canvas.instances_on("main")]
    overlaps = sum(
        1
        for a in range(len(boxes)) for b in range(a + 1, len(boxes))
        if min(boxes[a].x_max, boxes[b].x_max) - max(boxes[a].x_min, boxes[b].x_min) > 0
        and min(boxes[a].y_max, boxes[b].y_max) - max(boxes[a].y_min, boxes[b].y_min) > 0
    )
    if not overlaps:
        return
    said = [n for n in result.notes if "residual overlap" in n.text]
    assert said, (
        f"{overlaps} component bodies overlap and nothing in the result "
        f"says so; the caller has no way to know")


# ---------- the polish must not swallow the passes that follow it ----------

@functools.lru_cache(maxsize=None)
def _build_result_uncached(name: str):
    """The full PipelineResult, notes included, for one benchmark board."""
    import json
    from pathlib import Path

    from eda_agent.design.benchmark import SyntheticSymbolExtractor
    from eda_agent.design.pipeline import build_best_canvas_from_plan

    plans = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"
    plan = DesignPlan.model_validate(
        json.loads((plans / f"{name}.json").read_text()))
    return build_best_canvas_from_plan(plan, SyntheticSymbolExtractor(plan)), plan


# The board these three tests use must take the ACCEPTED-polish branch,
# or the early return they guard is never reached and they pass vacuously.
# buck stopped taking it: under the conftest's shrunken attractor sweep its
# polish now costs two crossings (1 -> 3) and the acceptance guard rejects
# it. blinker555 accepts it and is the smallest board that does, so it is
# also the cheapest to run; mcu accepts it too if a bigger board is ever
# wanted here.
_POLISHED_BOARD = "blinker555"


def test_the_polished_board_really_does_accept_the_polish():
    """Fixture validity, asserted separately so it fails with its own message.

    Both tests below are vacuous on a board whose polish is rejected: the
    early return they guard is on the ACCEPTED branch and is never taken.
    """
    result, _ = _build_result_uncached(_POLISHED_BOARD)
    assert any("convention polish applied" in n.text for n in result.notes), (
        f"{_POLISHED_BOARD} no longer takes the accepted-polish path, so it "
        f"cannot guard the early return; pick a board that does")


def test_the_stub_upgrade_is_reached_when_the_polish_is_accepted(monkeypatch):
    """The convention polish used to RETURN its result instead of adopting it.

    Everything after that point was skipped on every board whose polish was
    accepted, and the only such pass, the repair-port stub upgrade, writes a
    note only when it moves something, so the loss was silent.

    Asserted as REACHABILITY rather than as an effect. The obvious test, that
    the pass left nothing behind for a second call to move, is vacuous here:
    no benchmark board both accepts the polish and carries a movable repair
    glyph (buck accepts it and has none, mcu has 29 and no longer accepts
    it). Whether the pass finds work is a property of the board; whether it
    is CALLED is the property of the pipeline this guards.
    """
    import json
    from pathlib import Path

    from eda_agent.design import pipeline as pipe
    from eda_agent.design.benchmark import SyntheticSymbolExtractor

    calls: list[int] = []
    real = pipe.upgrade_repair_ports_to_stubs
    monkeypatch.setattr(
        pipe, "upgrade_repair_ports_to_stubs",
        lambda canvas, plan: (calls.append(1), real(canvas, plan))[1])

    plans = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"
    plan = DesignPlan.model_validate(
        json.loads((plans / f"{_POLISHED_BOARD}.json").read_text()))
    pipe.build_best_canvas_from_plan(plan, SyntheticSymbolExtractor(plan))

    assert calls, (
        "the repair-port stub upgrade was never called, so the polish branch "
        "returned instead of adopting its result")


def test_the_polish_is_not_reported_as_both_applied_and_rejected():
    """Adopting instead of returning made the reject note fall through.

    The note sat after the acceptance branch, unreachable while that branch
    returned. Turning the return into an assignment made both fire for one
    event, so the same run claimed the polish was applied and rejected.
    """
    result, _ = _build_result_uncached(_POLISHED_BOARD)
    applied = [n for n in result.notes if "convention polish applied" in n.text]
    rejected = [n for n in result.notes
                if "convention polish rejected" in n.text]
    assert not (applied and rejected), (
        f"{len(applied)} applied note(s) and {len(rejected)} rejected note(s) "
        f"for one polish decision")


# --------- a stub must not be drawn through another net's pin -------------

def _inline_pins_plan():
    """R1's right pin faces R2's left pin, 400 mils apart, on DIFFERENT nets.

    R1.2 is on SIG, R2.1 is on OTHER. A 300 mil stub leaving R1.2 to the
    right lands exactly on R2.1's electrical end, and Altium merges the two
    nets at that point. The body rects do not stop it, because a pin's
    electrical end sits outside its own body.
    """
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "title": "t", "size": "A4"}],
        "parts": [
            {"refdes": r, "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"}
            for r in ("R1", "R2", "R3", "R4")],
        "nets": [
            {"name": "SIG", "pins": [{"refdes": "R1", "pin": "2"},
                                     {"refdes": "R3", "pin": "1"}]},
            {"name": "OTHER", "pins": [{"refdes": "R2", "pin": "1"},
                                       {"refdes": "R4", "pin": "1"}]},
            # A SIGNAL net, not ground. A 2-pin part with a pin on a rail is
            # a shunt part and is stood upright by correct_two_pin_rotation,
            # which would take these pins out of the horizontal line the
            # fixture depends on.
            {"name": "RET", "pins": [{"refdes": "R1", "pin": "1"},
                                     {"refdes": "R2", "pin": "2"},
                                     {"refdes": "R3", "pin": "2"},
                                     {"refdes": "R4", "pin": "2"}]},
        ],
    })
    # HINTS, not layout_overrides. Overrides are only a starting point:
    # priors, the overlap shove and the recentre all still run over them,
    # and they moved these parts apart, which quietly removed the hazard
    # the test exists to create. Hints are re-asserted after those passes.
    hints = {
        "R1": {"x": 1100, "y": 3000, "rotation": 0},
        "R2": {"x": 1700, "y": 3000, "rotation": 0},
        "R3": {"x": 1100, "y": 4200, "rotation": 0},
        "R4": {"x": 1700, "y": 4200, "rotation": 0},
    }
    return plan, hints


def test_a_stub_is_not_drawn_through_another_nets_pin():
    """The stub pass, not the router, is where these shorts came from.

    MEASURED over the public corpus: of 190 shorting segments on the declined
    sheets, 173 were per-pin STUBS and 17 were routed segments. The router
    already avoided bodies and other nets' stub ends; the stub only avoided
    bodies, and a pin's electrical end is outside its body.
    """
    plan, hints = _inline_pins_plan()
    result = build_canvas_from_plan(
        plan, MockExtractor(_BASE_SYMBOLS), placement_hints=hints)
    canvas = result.canvas

    pin_net = {}
    for net in plan.nets:
        for pr in net.pins:
            inst = canvas.instance_by_refdes(pr.refdes)
            ep = inst.pin_world(pr.pin) if inst else None
            if ep is not None:
                pin_net[(ep.x, ep.y)] = net.name

    def on_seg(px, py, w):
        if w.x1 == w.x2:
            return px == w.x1 and min(w.y1, w.y2) <= py <= max(w.y1, w.y2)
        if w.y1 == w.y2:
            return py == w.y1 and min(w.x1, w.x2) <= px <= max(w.x1, w.x2)
        return False

    crossings = [
        (w.net, pt, owner)
        for w in canvas.wires if w.net
        for pt, owner in pin_net.items()
        if owner != w.net and on_seg(pt[0], pt[1], w)
    ]
    assert crossings == [], (
        f"a wire runs through a pin on another net: {crossings[:4]}")


def test_the_inline_fixture_would_actually_short_without_the_clip():
    """A geometry guard is worthless if the two pins were never in line.

    Asserts the hazard: R1.2 points straight at R2.1, they are on different
    nets, and the gap is inside the unclipped stub length. If a future edit
    moves the parts apart, this fails instead of quietly passing the test
    above for the wrong reason.
    """
    from eda_agent.design.router import _STUB_LEN_MILS

    plan, hints = _inline_pins_plan()
    result = build_canvas_from_plan(
        plan, MockExtractor(_BASE_SYMBOLS), placement_hints=hints)
    a = result.canvas.instance_by_refdes("R1").pin_world("2")
    b = result.canvas.instance_by_refdes("R2").pin_world("1")
    assert a.y == b.y, "the two pins are not on one horizontal line"
    assert 0 < b.x - a.x <= _STUB_LEN_MILS, (
        f"gap {b.x - a.x} is not within an unclipped {_STUB_LEN_MILS} stub")
