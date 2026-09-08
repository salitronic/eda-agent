# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A delta emit changes an existing sheet without re-placing every part.

emit_canvas transcribes a whole canvas onto a fresh-or-reused project,
which re-places every component. On a sheet somebody has worked on that
discards what makes a component theirs: locked designators, stamped
parameters, hand-set properties. The point of the delta path is that a
part nobody moved is never touched at all.
"""
from __future__ import annotations

from eda_agent.design.canvas import (
    NetLabel, SchematicCanvas, Sheet, SymbolInstance, WireSegment)
from eda_agent.design.emitter import EmitResult, emit_canvas_delta
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


def _symbol(lib_ref="R"):
    return SymbolModel(
        lib_path="LIB.SchLib", lib_ref=lib_ref,
        pins=[SymbolPin(designator="1", name="A", x=0, y=100, orientation=1,
                        length=100, electrical_type="passive")],
        body_bbox=SymbolBBox(x_min=-50, y_min=-50, x_max=50, y_max=50),
    )


class _Bridge:
    def __init__(self):
        self.sent: list[tuple] = []

    def send_command(self, command, params=None, **kw):
        self.sent.append((command, params or {}))
        return {"success": True, "placed": 99}

    def commands(self):
        return [c for c, _ in self.sent]

    def of(self, command):
        return [p for c, p in self.sent if c == command]


def _canvas(parts):
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    for refdes, x, y, rot in parts:
        cv.add_instance(SymbolInstance(refdes=refdes, symbol=_symbol(),
                                       x=x, y=y, rotation=rot, sheet="main"))
    return cv


def _snapshot(parts):
    return {"instances": [
        {"refdes": r, "lib_path": "LIB.SchLib", "lib_ref": "R",
         "x": x, "y": y, "rotation": rot}
        for r, x, y, rot in parts
    ]}


def test_a_part_nobody_moved_is_never_touched():
    bridge = _Bridge()
    before = [("R1", 1000, 1000, 0), ("R2", 2000, 1000, 0)]
    res = emit_canvas_delta(_canvas(before), _snapshot(before), "p.PrjPcb",
                            bridge)
    assert res.delta["untouched"] == ["R1", "R2"]
    assert res.delta["moved"] == [] and res.delta["added"] == []
    # No component was re-placed and none was moved.
    assert "generic.place_sch_components_from_library" not in bridge.commands()
    assert bridge.of("generic.modify_objects") == []


def test_only_the_moved_part_is_moved():
    bridge = _Bridge()
    before = [("R1", 1000, 1000, 0), ("R2", 2000, 1000, 0)]
    after = [("R1", 1000, 1000, 0), ("R2", 2500, 1400, 0)]
    res = emit_canvas_delta(_canvas(after), _snapshot(before), "p.PrjPcb",
                            bridge)
    assert res.delta["moved"] == ["R2"]
    assert res.delta["untouched"] == ["R1"], "a moved part is not untouched"
    mods = bridge.of("generic.modify_objects")
    assert len(mods) == 1
    assert mods[0]["filter"] == "Designator=R2"
    assert "Location.X=2500" in mods[0]["set"]
    assert "Location.Y=1400" in mods[0]["set"]


def test_a_rotation_change_is_written_before_the_move():
    """The bridge drops Orientation when it shares a set with Location.

    Writing Location triggers a re-layout that can snapshot the previous
    orientation, so the two must be separate ops with the rotation first.
    """
    bridge = _Bridge()
    before = [("R1", 1000, 1000, 0)]
    after = [("R1", 1200, 1000, 90)]
    emit_canvas_delta(_canvas(after), _snapshot(before), "p.PrjPcb", bridge)
    mods = bridge.of("generic.modify_objects")
    assert len(mods) == 2, "rotation and location must not share one set"
    assert "Orientation=1" in mods[0]["set"]
    assert "Location" not in mods[0]["set"]
    assert "Location.X=1200" in mods[1]["set"]


def test_new_parts_are_placed_and_gone_parts_deleted():
    bridge = _Bridge()
    before = [("R1", 1000, 1000, 0), ("R9", 5000, 5000, 0)]
    after = [("R1", 1000, 1000, 0), ("C7", 3000, 2000, 0)]
    res = emit_canvas_delta(_canvas(after), _snapshot(before), "p.PrjPcb",
                            bridge)
    assert res.delta["added"] == ["C7"]
    assert res.delta["removed"] == ["R9"]
    placed = bridge.of("generic.place_sch_components_from_library")
    assert placed and "designator=C7" in placed[0]["placements"]
    assert "designator=R1" not in placed[0]["placements"], (
        "an untouched part must not be re-placed")
    deletes = [p for p in bridge.of("generic.delete_objects")
               if p.get("object_type") == "eSchComponent"]
    assert deletes and deletes[0]["filter"] == "Designator=R9"


def test_copper_is_cleared_before_it_is_redrawn():
    bridge = _Bridge()
    cv = _canvas([("R1", 1000, 1000, 0)])
    cv.add_wires([WireSegment(x1=0, y1=0, x2=100, y2=0, sheet="main",
                              net="N")])
    cv.add_labels([NetLabel(text="N", x=0, y=0, orientation=0, sheet="main")])
    emit_canvas_delta(cv, _snapshot([("R1", 1000, 1000, 0)]), "p.PrjPcb",
                      bridge)
    cleared = {p["object_type"] for p in bridge.of("generic.delete_objects")}
    assert {"eWire", "eNetLabel", "eJunction", "ePowerObject"} <= cleared
    # And the clear happens before the redraw, or the redraw is erased.
    order = bridge.commands()
    assert order.index("generic.delete_objects") < order.index(
        "generic.place_wires")


def test_a_failed_move_is_reported_not_swallowed():
    class _Angry(_Bridge):
        def send_command(self, command, params=None, **kw):
            if command == "generic.modify_objects":
                raise RuntimeError("component is locked")
            return super().send_command(command, params, **kw)

    bridge = _Angry()
    res = emit_canvas_delta(_canvas([("R1", 9000, 1000, 0)]),
                            _snapshot([("R1", 1000, 1000, 0)]), "p.PrjPcb",
                            bridge)
    assert any(f.code == "MOVE_FAILED" and f.refdes == "R1"
               for f in res.failures)
    assert res.ok is False, "a sheet whose parts did not move is not ok"


def test_the_sheet_is_activated_before_anything_is_edited():
    """Every delta command filters on the ACTIVE document.

    Without an explicit activation the edits land on whichever sheet
    Altium happens to have focused and the result still reports success.
    """
    bridge = _Bridge()
    emit_canvas_delta(_canvas([("R1", 1200, 1000, 0)]),
                      _snapshot([("R1", 1000, 1000, 0)]), "p.PrjPcb", bridge)
    order = bridge.commands()
    assert "application.set_active_document" in order
    assert order.index("application.set_active_document") < order.index(
        "generic.modify_objects")


def test_a_sheet_that_will_not_activate_edits_nothing():
    class _NoActivate(_Bridge):
        def send_command(self, command, params=None, **kw):
            if command == "application.set_active_document":
                raise RuntimeError("document is not loaded")
            return super().send_command(command, params, **kw)

    bridge = _NoActivate()
    res = emit_canvas_delta(_canvas([("R1", 1200, 1000, 0)]),
                            _snapshot([("R1", 1000, 1000, 0)]), "p.PrjPcb",
                            bridge)
    assert res.ok is False
    assert bridge.of("generic.modify_objects") == []
    assert bridge.of("generic.delete_objects") == []


def test_a_multi_sheet_canvas_is_refused_not_half_applied():
    bridge = _Bridge()
    cv = _canvas([("R1", 1200, 1000, 0)])
    cv.add_sheet(Sheet(name="power"))
    res = emit_canvas_delta(cv, _snapshot([("R1", 1000, 1000, 0)]),
                            "p.PrjPcb", bridge)
    assert res.ok is False
    assert "one sheet" in " ".join(res.notes)
    assert bridge.sent == []


def test_only_new_parts_are_stamped():
    """A part that moved keeps the parameters it has on the sheet.

    Re-stamping a part that was already there overwrites a value
    somebody edited by hand, which is the thing a delta exists to avoid.
    """
    bridge = _Bridge()
    before = [("R1", 1000, 1000, 0), ("R2", 2000, 1000, 0)]
    after = [("R1", 1000, 1000, 0), ("R2", 2600, 1000, 0),
             ("C3", 3000, 1000, 0)]
    emit_canvas_delta(_canvas(after), _snapshot(before), "p.PrjPcb", bridge,
                      parameter_stamps={"R1": {"Value": "1k"},
                                        "R2": {"Value": "2k"},
                                        "C3": {"Value": "100n"}})
    stamped = " ".join(str(p) for p in
                       bridge.of("generic.set_sch_components_parameters"))
    assert "C3" in stamped
    assert "R1" not in stamped and "R2" not in stamped


# --- the orchestrator picks between the two emit paths ---------------

def _stub_pipeline(monkeypatch, canvas):
    """Cut the orchestrator off above the emit step.

    Everything from plan parsing to symbol extraction is exercised by
    other tests; what is under test here is only which emitter runs.
    """
    import eda_agent.design.orchestrator as orch

    class _Res:
        ok = True
        parameter_stamps: dict = {}
        notes: list = []

        def __init__(self, cv):
            self.canvas = cv

    monkeypatch.setattr(orch, "build_best_canvas_from_plan",
                        lambda *a, **k: _Res(canvas))
    monkeypatch.setattr(orch, "_merge_pipeline_result", lambda out, r: None)
    monkeypatch.setattr(orch, "_merge_emit_result", lambda out, r: None)
    monkeypatch.setattr(orch, "_resolve_bridge", lambda: _Bridge())
    monkeypatch.setattr(orch, "SymbolExtractor", lambda *a, **k: None)
    monkeypatch.setattr(orch, "SymbolCache", lambda *a, **k: None)
    called: dict = {}

    def _mark(which):
        def _emit(*a, **k):
            called[which] = True
            return EmitResult()
        return _emit

    monkeypatch.setattr(orch, "emit_canvas", _mark("full"))
    monkeypatch.setattr(orch, "emit_canvas_delta", _mark("delta"))
    return called


_PLAN = {
    "spec": "two resistors", "summary": "two resistors",
    "sheets": [{"name": "main"}],
    "parts": [{"refdes": "R1", "lib_ref": "R", "lib_path": "LIB.SchLib",
               "sheet": "main", "value": "1k"},
              {"refdes": "R2", "lib_ref": "R", "lib_path": "LIB.SchLib",
               "sheet": "main", "value": "2k"}],
    "nets": [{"name": "N", "pins": [{"refdes": "R1", "pin": "1"},
                                    {"refdes": "R2", "pin": "1"}]}],
}


def test_a_sheet_drawn_before_is_edited_not_redrawn(tmp_path, monkeypatch):
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    project = tmp_path / "p.PrjPcb"
    (tmp_path / "p.canvas.json").write_text(
        '{"canvas": {"instances": [{"refdes": "R1", "x": 1, "y": 2, '
        '"rotation": 0, "lib_path": "LIB.SchLib", "lib_ref": "R"}]}}',
        encoding="utf-8")
    called = _stub_pipeline(monkeypatch, _canvas([("R1", 1000, 1000, 0)]))
    execute_plan_via_canvas_from_json(_PLAN, str(project))
    assert called == {"delta": True}


def test_a_sheet_never_drawn_is_drawn_from_scratch(tmp_path, monkeypatch):
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    called = _stub_pipeline(monkeypatch, _canvas([("R1", 1000, 1000, 0)]))
    execute_plan_via_canvas_from_json(_PLAN, str(tmp_path / "p.PrjPcb"))
    assert called == {"full": True}


def test_delta_mode_refuses_a_sheet_it_has_no_snapshot_for(tmp_path,
                                                           monkeypatch):
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    called = _stub_pipeline(monkeypatch, _canvas([("R1", 1000, 1000, 0)]))
    out = execute_plan_via_canvas_from_json(
        _PLAN, str(tmp_path / "p.PrjPcb"), mode="delta")
    assert out["ok"] is False
    assert called == {}, "refusing must not fall back to redrawing"


def test_full_mode_redraws_even_with_a_snapshot(tmp_path, monkeypatch):
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    (tmp_path / "p.canvas.json").write_text(
        '{"canvas": {"instances": [{"refdes": "R1", "x": 1, "y": 2}]}}',
        encoding="utf-8")
    called = _stub_pipeline(monkeypatch, _canvas([("R1", 1000, 1000, 0)]))
    execute_plan_via_canvas_from_json(
        _PLAN, str(tmp_path / "p.PrjPcb"), mode="full")
    assert called == {"full": True}


def test_an_unreadable_snapshot_does_not_become_a_mass_delete(tmp_path,
                                                              monkeypatch):
    """A half-parsed snapshot names no parts, so every part reads as gone."""
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    (tmp_path / "p.canvas.json").write_text('{"canvas": {"inst',
                                            encoding="utf-8")
    called = _stub_pipeline(monkeypatch, _canvas([("R1", 1000, 1000, 0)]))
    execute_plan_via_canvas_from_json(_PLAN, str(tmp_path / "p.PrjPcb"))
    assert called == {"full": True}


def test_a_multi_sheet_plan_is_drawn_whole(tmp_path, monkeypatch):
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    (tmp_path / "p.canvas.json").write_text(
        '{"canvas": {"instances": [{"refdes": "R1", "x": 1, "y": 2}]}}',
        encoding="utf-8")
    cv = _canvas([("R1", 1000, 1000, 0)])
    cv.add_sheet(Sheet(name="power"))
    called = _stub_pipeline(monkeypatch, cv)
    execute_plan_via_canvas_from_json(_PLAN, str(tmp_path / "p.PrjPcb"))
    assert called == {"full": True}


def test_a_moved_part_takes_its_designator_with_it():
    """Text positions are absolute, with Autoposition off.

    Gen_SetSchTextPositions writes the designator at a sheet coordinate,
    so a part that moves and is not re-emitted leaves its own label
    behind at the old spot.
    """
    bridge = _Bridge()
    cv = _canvas([("R1", 1000, 1000, 0), ("R2", 2500, 1400, 0)])
    for inst in cv.instances:
        inst.designator_pos = (inst.x + 40, inst.y + 60)
    before = [("R1", 1000, 1000, 0), ("R2", 2000, 1000, 0)]
    emit_canvas_delta(cv, _snapshot(before), "p.PrjPcb", bridge)
    positions = bridge.of("generic.set_sch_text_positions")
    assert positions, "the moved part's designator was never repositioned"
    assert "designator=R2;dx=2540;dy=1460" in positions[0]["positions"]


def test_a_refdes_whose_symbol_changed_is_replaced_not_moved():
    """R1 as a resistor and R1 as a capacitor are not the same part.

    Moving the old symbol to the new coordinate leaves the sheet holding
    a part the plan does not contain, wired as though it were the new
    one, and the netlist compiles.
    """
    bridge = _Bridge()
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    cv.add_instance(SymbolInstance(refdes="R1", symbol=_symbol("CAP"),
                                   x=1000, y=1000, rotation=0, sheet="main"))
    res = emit_canvas_delta(cv, _snapshot([("R1", 1000, 1000, 0)]),
                            "p.PrjPcb", bridge)
    assert res.delta["replaced"] == ["R1"]
    assert res.delta["moved"] == [] and res.delta["untouched"] == []
    deletes = [p for p in bridge.of("generic.delete_objects")
               if p.get("object_type") == "eSchComponent"]
    assert deletes and deletes[0]["filter"] == "Designator=R1"
    placed = bridge.of("generic.place_sch_components_from_library")
    assert placed and "designator=R1" in placed[0]["placements"]


def test_the_same_symbol_name_from_another_library_is_a_different_part():
    """Two libraries both define a symbol called RES.

    The pin geometry behind the name is not the same, so a plan that
    changes the library has changed the part even though the lib_ref
    reads identically.
    """
    bridge = _Bridge()
    other = SymbolModel(
        lib_path="OTHER.SchLib", lib_ref="R",
        pins=[SymbolPin(designator="1", name="A", x=0, y=100, orientation=1,
                        length=100, electrical_type="passive")],
        body_bbox=SymbolBBox(x_min=-50, y_min=-50, x_max=50, y_max=50),
    )
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    cv.add_instance(SymbolInstance(refdes="R1", symbol=other, x=1000, y=1000,
                                   rotation=0, sheet="main"))
    res = emit_canvas_delta(cv, _snapshot([("R1", 1000, 1000, 0)]),
                            "p.PrjPcb", bridge)
    assert res.delta["replaced"] == ["R1"]


def test_another_sheets_parts_are_not_reported_as_removed():
    """The snapshot holds the whole project; the delta is one sheet."""
    bridge = _Bridge()
    snapshot = {"instances": [
        {"refdes": "R1", "lib_path": "LIB.SchLib", "lib_ref": "R",
         "x": 1000, "y": 1000, "rotation": 0, "sheet": "main"},
        {"refdes": "U9", "lib_path": "LIB.SchLib", "lib_ref": "R",
         "x": 5000, "y": 5000, "rotation": 0, "sheet": "power"},
    ]}
    res = emit_canvas_delta(_canvas([("R1", 1000, 1000, 0)]), snapshot,
                            "p.PrjPcb", bridge)
    assert res.delta["removed"] == []
    assert res.delta["untouched"] == ["R1"]
    deletes = [p for p in bridge.of("generic.delete_objects")
               if p.get("object_type") == "eSchComponent"]
    assert deletes == []


def test_an_instance_belonging_to_no_declared_sheet_is_not_placed_here():
    """The delta writes to one document, so it places what is on it.

    An instance naming a sheet the canvas does not declare is malformed
    input; putting it on the active sheet anyway is a part appearing on
    a schematic that never asked for it.
    """
    bridge = _Bridge()
    cv = _canvas([("R1", 1000, 1000, 0)])
    cv.add_instance(SymbolInstance(refdes="U9", symbol=_symbol(), x=5000,
                                   y=5000, rotation=0, sheet="power"))
    res = emit_canvas_delta(cv, _snapshot([("R1", 1000, 1000, 0)]),
                            "p.PrjPcb", bridge)
    assert res.delta["added"] == []
    placed = bridge.of("generic.place_sch_components_from_library")
    assert not placed, "U9 belongs to a sheet this delta is not writing"


# --- the snapshot records the sheet, not the intention ----------------

def _emit_result(ok=True, failures=()):
    from eda_agent.design.emitter import EmitResult
    res = EmitResult()
    res.ok = ok
    res.failures = list(failures)
    return res


def _stub_for_snapshot(monkeypatch, emit_result):
    import eda_agent.design.orchestrator as orch

    called = _stub_pipeline(monkeypatch, _canvas([("R1", 1000, 1000, 0)]))
    monkeypatch.setattr(orch, "emit_canvas", lambda *a, **k: emit_result)
    monkeypatch.setattr(orch, "emit_canvas_delta", lambda *a, **k: emit_result)
    return called


def test_the_snapshot_is_written_after_a_successful_emit(tmp_path,
                                                         monkeypatch):
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    _stub_for_snapshot(monkeypatch, _emit_result())
    out = execute_plan_via_canvas_from_json(_PLAN, str(tmp_path / "p.PrjPcb"))
    assert (tmp_path / "p.canvas.json").is_file()
    assert out["canvas_snapshot_path"].endswith("p.canvas.json")


def test_a_failed_emit_leaves_the_previous_snapshot_alone(tmp_path,
                                                          monkeypatch):
    """The snapshot says where the parts ARE.

    Written before the emit, it claims the new layout landed whether or
    not it did: the next edit deltas against a sheet that was never
    drawn, so a part that failed to move counts as untouched from then
    on and never moves again.
    """
    from eda_agent.design.emitter import EmitFailure
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    snapshot = tmp_path / "p.canvas.json"
    snapshot.write_text('{"canvas": {"instances": [{"refdes": "R1", '
                        '"x": 1, "y": 2}]}}', encoding="utf-8")
    # ok=True with a failure listed is the shape emit_canvas returns: a
    # part that would not place does not flip the whole emit to not-ok,
    # so a snapshot written on ok alone records parts that are not there.
    _stub_for_snapshot(monkeypatch, _emit_result(
        ok=True, failures=[EmitFailure(refdes="R1", code="PLACE_FAILED",
                                       reason="no such symbol")]))
    out = execute_plan_via_canvas_from_json(_PLAN, str(tmp_path / "p.PrjPcb"))
    assert '"x": 1' in snapshot.read_text(encoding="utf-8"), (
        "the failed emit overwrote the record of where the parts are")
    assert any("NOT updated" in n for n in out["notes"])


def test_the_delta_reads_the_snapshot_before_it_is_replaced(tmp_path,
                                                            monkeypatch):
    """Read before write, or the delta compares the new state to itself
    and finds nothing changed."""
    import eda_agent.design.orchestrator as orch
    from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

    snapshot = tmp_path / "p.canvas.json"
    snapshot.write_text('{"canvas": {"instances": [{"refdes": "R1", '
                        '"lib_path": "LIB.SchLib", "lib_ref": "R", '
                        '"x": 7777, "y": 8888, "rotation": 0}]}}',
                        encoding="utf-8")
    _stub_pipeline(monkeypatch, _canvas([("R1", 1000, 1000, 0)]))
    seen: dict = {}

    def _delta(canvas, prior, *a, **k):
        seen["prior"] = prior
        return _emit_result()

    monkeypatch.setattr(orch, "emit_canvas_delta", _delta)
    execute_plan_via_canvas_from_json(_PLAN, str(tmp_path / "p.PrjPcb"))
    assert seen["prior"]["instances"][0]["x"] == 7777


def test_an_emit_that_reported_not_ok_leaves_the_snapshot_too():
    """ok and the failure list are separate answers.

    _ensure_sheet_loaded sets ok False and lists no failure when the
    sheet will not open, so checking failures alone records a layout for
    a sheet nothing was written to at all.
    """
    import eda_agent.design.orchestrator as orch
    from eda_agent.design.orchestrator import _write_canvas_snapshot
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "p.canvas.json"
        snapshot.write_text('{"canvas": {"instances": []}}', encoding="utf-8")

        class _Pipeline:
            parameter_stamps: dict = {}
            canvas = _canvas([("R1", 1000, 1000, 0)])

        out: dict = {"notes": []}
        _write_canvas_snapshot(snapshot, {"spec": "x"}, _Pipeline(),
                               _emit_result(ok=False), out)
        assert snapshot.read_text(encoding="utf-8") == (
            '{"canvas": {"instances": []}}')
        assert any("NOT updated" in n for n in out["notes"])
