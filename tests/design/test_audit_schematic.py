# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic-audit tests, fake bridge, no Altium round-trips."""

from __future__ import annotations

from typing import Any

from eda_agent.design.audit import (
    DEFAULT_BBOX_HALF_MILS,
    DEFAULT_CLUSTER_RADIUS_MILS,
    SchematicAuditReport,
    _segment_intersects_rect,
    audit_schematic,
)


SHEET = "C:\\proj\\main.SchDoc"


class _FakeBridge:
    """Stub for the IPC bridge.

    The audit module calls send_command for:
      - project.get_documents          (sheet enumeration)
      - generic.query_objects                  (per (type, scope) read)

    We construct responses keyed by object_type, returning lists of
    dict rows in the same shape the real bridge produces.
    """

    def __init__(
        self,
        components_by_sheet: dict[str, list[dict[str, Any]]] | None = None,
        wires_by_sheet: dict[str, list[dict[str, Any]]] | None = None,
        ports_by_sheet: dict[str, list[dict[str, Any]]] | None = None,
        sheets: list[str] | None = None,
    ) -> None:
        self.components_by_sheet = components_by_sheet or {}
        self.wires_by_sheet = wires_by_sheet or {}
        self.ports_by_sheet = ports_by_sheet or {}
        self.sheets = sheets if sheets is not None else [SHEET]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send_command(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        self.calls.append((command, params or {}))
        if command == "project.get_documents":
            return {
                "documents": [
                    {"file_path": s, "kind": "SCH"} for s in self.sheets
                ]
            }
        if command == "generic.query_objects":
            p = params or {}
            scope = p.get("scope", "")
            sheet = scope[4:] if scope.startswith("doc:") else ""
            obj_type = p.get("object_type", "")
            if obj_type == "eSchComponent":
                return {"objects": self.components_by_sheet.get(sheet, [])}
            if obj_type == "eWire":
                return {"objects": self.wires_by_sheet.get(sheet, [])}
            if obj_type == "ePowerObject":
                return {"objects": self.ports_by_sheet.get(sheet, [])}
            return {"objects": []}
        return {}


def _comp_row(refdes: str, x1: int, y1: int, x2: int, y2: int) -> dict[str, Any]:
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    return {
        "Designator.Text": refdes,
        "BoundingRectangle.X1": str(x1),
        "BoundingRectangle.Y1": str(y1),
        "BoundingRectangle.X2": str(x2),
        "BoundingRectangle.Y2": str(y2),
        "Location.X": str(cx),
        "Location.Y": str(cy),
    }


def _comp_location_only(refdes: str, x: int, y: int) -> dict[str, Any]:
    return {
        "Designator.Text": refdes,
        "BoundingRectangle.X1": "",
        "BoundingRectangle.Y1": "",
        "BoundingRectangle.X2": "",
        "BoundingRectangle.Y2": "",
        "Location.X": str(x),
        "Location.Y": str(y),
    }


def _wire_row(x1: int, y1: int, x2: int, y2: int) -> dict[str, Any]:
    return {
        "Vertex.1.X": str(x1),
        "Vertex.1.Y": str(y1),
        "Vertex.2.X": str(x2),
        "Vertex.2.Y": str(y2),
        "VerticesCount": "2",
        "Location.X": str(x1),
        "Location.Y": str(y1),
    }


def _port_row(net: str, x: int, y: int) -> dict[str, Any]:
    return {
        "Text": net,
        "Location.X": str(x),
        "Location.Y": str(y),
    }


# --------------------------------------------------------------------
# 1. Clean sheet: zero violations.
# --------------------------------------------------------------------


def test_audit_clean_sheet_returns_ok() -> None:
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [
                _comp_row("U1", 1000, 1000, 2000, 2000),
                _comp_row("R1", 3000, 1000, 3300, 1300),
                _comp_row("C1", 4000, 1000, 4200, 1200),
            ]
        },
        wires_by_sheet={SHEET: []},
        ports_by_sheet={SHEET: []},
    )
    report = audit_schematic(bridge=bridge)
    assert isinstance(report, SchematicAuditReport)
    assert report.ok is True
    assert report.overlaps == []
    assert report.wire_crossings == []
    assert report.stacked_ports == []


# --------------------------------------------------------------------
# 2. Component overlap detection.
# --------------------------------------------------------------------


def test_audit_detects_overlapping_components() -> None:
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [
                _comp_row("U1", 1000, 1000, 2000, 2000),
                _comp_row("U2", 1500, 1500, 2500, 2500),  # overlaps U1
            ]
        },
    )
    report = audit_schematic(bridge=bridge)
    assert report.ok is False
    assert len(report.overlaps) == 1
    v = report.overlaps[0]
    pair = {v.refdes_a, v.refdes_b}
    assert pair == {"U1", "U2"}
    assert v.overlap_mils == 500
    assert v.sheet == SHEET


def test_audit_no_overlap_for_adjacent_non_touching() -> None:
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [
                _comp_row("R1", 1000, 1000, 1500, 1500),
                _comp_row("R2", 1600, 1000, 2100, 1500),  # 100 mil gap
            ]
        },
    )
    report = audit_schematic(bridge=bridge)
    assert report.overlaps == []
    assert report.ok is True


def test_audit_overlap_ignores_cross_sheet_pairs() -> None:
    sheet_b = "C:\\proj\\power.SchDoc"
    bridge = _FakeBridge(
        sheets=[SHEET, sheet_b],
        components_by_sheet={
            SHEET: [_comp_row("U1", 1000, 1000, 2000, 2000)],
            sheet_b: [_comp_row("U2", 1500, 1500, 2500, 2500)],
        },
    )
    report = audit_schematic(bridge=bridge)
    # Different sheets means different coordinate spaces in our model;
    # cross-sheet overlap is never a real-world problem.
    assert report.overlaps == []
    assert report.ok is True


# --------------------------------------------------------------------
# 3. Wire-through-component detection.
# --------------------------------------------------------------------


def test_audit_detects_wire_through_component() -> None:
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [_comp_row("U1", 1000, 1000, 2000, 2000)],
        },
        # Wire crosses straight through U1 from far left to far right,
        # both endpoints sit well outside the component body.
        wires_by_sheet={SHEET: [_wire_row(500, 1500, 2500, 1500)]},
    )
    report = audit_schematic(bridge=bridge)
    assert report.ok is False
    assert len(report.wire_crossings) == 1
    wc = report.wire_crossings[0]
    assert wc.refdes_crossed == "U1"
    assert wc.segment == ((500, 1500), (2500, 1500))
    assert wc.bbox == (1000, 1000, 2000, 2000)


def test_audit_wire_lands_on_pin_is_not_a_crossing() -> None:
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [_comp_row("U1", 1000, 1000, 2000, 2000)],
        },
        # Both endpoints sit on the component boundary (left + right
        # edges) - a normal pin-to-pin trace, not a body crossing.
        wires_by_sheet={SHEET: [_wire_row(1000, 1500, 2000, 1500)]},
    )
    report = audit_schematic(bridge=bridge)
    assert report.wire_crossings == []


def test_audit_wire_that_clears_components_does_not_flag() -> None:
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [_comp_row("U1", 1000, 1000, 2000, 2000)],
        },
        # Wire well below U1; never touches it.
        wires_by_sheet={SHEET: [_wire_row(500, 500, 2500, 500)]},
    )
    report = audit_schematic(bridge=bridge)
    assert report.wire_crossings == []


# --------------------------------------------------------------------
# 4. Stacked-port cluster detection.
# --------------------------------------------------------------------


def test_audit_detects_four_gnd_ports_in_a_400_mil_radius() -> None:
    # Four GND ports clustered tightly; radius 400 fits all four
    # inside the default 600-mil cluster radius.
    bridge = _FakeBridge(
        components_by_sheet={SHEET: []},
        ports_by_sheet={
            SHEET: [
                _port_row("GND", 5000, 5000),
                _port_row("GND", 5100, 5050),
                _port_row("GND", 5050, 5150),
                _port_row("GND", 4950, 5100),
            ]
        },
    )
    report = audit_schematic(bridge=bridge)
    assert report.ok is False
    assert len(report.stacked_ports) == 1
    sp = report.stacked_ports[0]
    assert sp.net_name == "GND"
    assert sp.cluster_count == 4
    assert len(sp.members) == 4


def test_audit_two_ports_is_not_a_cluster() -> None:
    bridge = _FakeBridge(
        ports_by_sheet={
            SHEET: [
                _port_row("VCC", 1000, 1000),
                _port_row("VCC", 1050, 1050),
            ]
        },
    )
    report = audit_schematic(bridge=bridge)
    assert report.stacked_ports == []
    assert report.ok is True


def test_audit_three_ports_far_apart_not_a_cluster() -> None:
    # Three ports of the same net but spread across the sheet, each
    # pair is >> cluster_radius_mils apart, no cluster.
    bridge = _FakeBridge(
        ports_by_sheet={
            SHEET: [
                _port_row("GND", 1000, 1000),
                _port_row("GND", 5000, 5000),
                _port_row("GND", 9000, 9000),
            ]
        },
    )
    report = audit_schematic(bridge=bridge)
    assert report.stacked_ports == []


def test_audit_different_nets_do_not_merge_into_a_cluster() -> None:
    bridge = _FakeBridge(
        ports_by_sheet={
            SHEET: [
                # Three different nets at the same location, should
                # NOT cluster because the audit groups by net.
                _port_row("VCC", 1000, 1000),
                _port_row("V3V3", 1010, 1010),
                _port_row("V5", 1020, 1020),
            ]
        },
    )
    report = audit_schematic(bridge=bridge)
    assert report.stacked_ports == []


def test_audit_cluster_radius_parameter_is_honoured() -> None:
    # Three GND ports spaced ~800 mils apart; default 600-mil radius
    # would not fit them all into one cluster, but a 2000-mil radius
    # does.
    ports = [
        _port_row("GND", 1000, 1000),
        _port_row("GND", 1800, 1000),
        _port_row("GND", 2600, 1000),
    ]
    bridge_default = _FakeBridge(ports_by_sheet={SHEET: ports})
    bridge_wide = _FakeBridge(ports_by_sheet={SHEET: ports})

    rep_default = audit_schematic(bridge=bridge_default)
    rep_wide = audit_schematic(bridge=bridge_wide, cluster_radius_mils=2000)

    assert rep_default.stacked_ports == []
    assert len(rep_wide.stacked_ports) == 1
    assert rep_wide.stacked_ports[0].cluster_count == 3


# --------------------------------------------------------------------
# 5. Mixed violations and serialisation.
# --------------------------------------------------------------------


def test_audit_serialises_to_dict_with_three_violation_classes() -> None:
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [
                _comp_row("U1", 1000, 1000, 2000, 2000),
                _comp_row("U2", 1500, 1500, 2500, 2500),
                _comp_row("R5", 4000, 1000, 4200, 1200),
            ]
        },
        wires_by_sheet={SHEET: [_wire_row(3500, 1100, 4500, 1100)]},
        ports_by_sheet={
            SHEET: [
                _port_row("V3V3", 6000, 6000),
                _port_row("V3V3", 6100, 6050),
                _port_row("V3V3", 6050, 6150),
            ]
        },
    )
    report = audit_schematic(bridge=bridge, cluster_radius_mils=600)
    blob = report.to_dict()
    assert blob["ok"] is False
    assert len(blob["overlaps"]) == 1
    assert len(blob["wire_crossings"]) == 1
    assert len(blob["stacked_ports"]) == 1
    # All numeric fields round-trip as ints / tuples-of-ints.
    overlap = blob["overlaps"][0]
    assert isinstance(overlap["overlap_mils"], int)
    assert isinstance(overlap["centre_a"], (list, tuple))


# --------------------------------------------------------------------
# 6. Fallback: bbox unavailable, use Location +/- DEFAULT_BBOX_HALF_MILS.
# --------------------------------------------------------------------


def test_audit_falls_back_to_location_when_bbox_missing() -> None:
    # Two parts whose Location.X / Y put them within
    # 2 * DEFAULT_BBOX_HALF_MILS of each other => bbox overlap once
    # the fallback is applied.
    bridge = _FakeBridge(
        components_by_sheet={
            SHEET: [
                _comp_location_only("R1", 5000, 5000),
                _comp_location_only(
                    "R2",
                    5000 + DEFAULT_BBOX_HALF_MILS,  # half a box step away
                    5000,
                ),
            ]
        },
    )
    report = audit_schematic(bridge=bridge)
    assert len(report.overlaps) == 1
    assert any(
        "BoundingRectangle.* not exposed" in n for n in report.notes
    )


# --------------------------------------------------------------------
# 7. Liang-Barsky primitive tests, exercised directly.
# --------------------------------------------------------------------


def test_segment_intersects_rect_horizontal_through_centre() -> None:
    assert _segment_intersects_rect(0, 50, 100, 50, 10, 10, 90, 90) is True


def test_segment_intersects_rect_clears_above() -> None:
    assert _segment_intersects_rect(0, 200, 100, 200, 10, 10, 90, 90) is False


def test_segment_intersects_rect_endpoint_inside() -> None:
    # Segment starts inside the rect, ends outside, still counts.
    assert _segment_intersects_rect(50, 50, 200, 200, 10, 10, 90, 90) is True


def test_segment_intersects_rect_corner_touch() -> None:
    # Segment that just clips the upper-right corner is a crossing.
    assert _segment_intersects_rect(80, 100, 100, 80, 10, 10, 90, 90) is True


# --------------------------------------------------------------------
# 8. Multi-sheet enumeration.
# --------------------------------------------------------------------


def test_audit_walks_every_sheet_in_the_project() -> None:
    sheet_a = "C:\\proj\\main.SchDoc"
    sheet_b = "C:\\proj\\power.SchDoc"
    bridge = _FakeBridge(
        sheets=[sheet_a, sheet_b],
        components_by_sheet={
            sheet_a: [_comp_row("U1", 1000, 1000, 2000, 2000)],
            sheet_b: [
                _comp_row("U2", 3000, 3000, 4000, 4000),
                _comp_row("U3", 3500, 3500, 4500, 4500),  # overlap on sheet B
            ],
        },
    )
    report = audit_schematic(bridge=bridge, project_path="C:\\proj\\proj.PrjPcb")
    assert len(report.overlaps) == 1
    assert report.overlaps[0].sheet == sheet_b
    # The bridge enumeration call must have been issued.
    cmds = [c for c, _ in bridge.calls]
    assert "project.get_documents" in cmds


# --------------------------------------------------------------------
# 9. Bridge failure modes.
# --------------------------------------------------------------------


def test_audit_handles_query_objects_failure_gracefully() -> None:
    class _ErrBridge:
        def send_command(self, command, params=None, timeout=None):
            if command == "project.get_documents":
                return {"documents": [{"file_path": SHEET, "kind": "SCH"}]}
            if command == "generic.query_objects":
                raise RuntimeError("forced")
            return {}

    report = audit_schematic(bridge=_ErrBridge())
    # Nothing was read, so nothing was checked: no crash, and no clean bill.
    assert report.ok is False
    assert report.overlaps == []
    assert len(report.unread) == 3
    assert all("forced" in u for u in report.unread)
    assert report.to_dict()["unread"] == report.unread


def test_audit_handles_missing_sheet_enumeration() -> None:
    class _NoSheets:
        def __init__(self):
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def send_command(self, command, params=None, timeout=None):
            self.calls.append((command, params or {}))
            if command == "project.get_documents":
                raise RuntimeError("no project")
            if command == "generic.query_objects":
                obj_type = (params or {}).get("object_type", "")
                if obj_type == "eSchComponent":
                    return {
                        "objects": [_comp_row("U1", 1000, 1000, 2000, 2000)]
                    }
                return {"objects": []}
            return {}

    bridge = _NoSheets()
    report = audit_schematic(bridge=bridge)
    # The other sheets went unchecked, and the report says so.
    assert report.ok is False
    assert "only the active sheet" in report.unread[0]
    # Falls back to active_doc scope when sheet list is unavailable.
    scopes = [
        p.get("scope", "")
        for c, p in bridge.calls
        if c == "generic.query_objects"
    ]
    assert "active_doc" in scopes


# --------------------------------------------------------------------
# 10. Defaults / constants sanity.
# --------------------------------------------------------------------


def test_audit_default_cluster_radius_is_600_mils() -> None:
    assert DEFAULT_CLUSTER_RADIUS_MILS == 600


def test_audit_report_to_dict_round_trip_keys() -> None:
    report = SchematicAuditReport(project_path="C:\\proj\\x.PrjPcb")
    blob = report.to_dict()
    assert set(blob.keys()) == {
        "ok",
        "project_path",
        "overlaps",
        "wire_crossings",
        "stacked_ports",
        "notes",
        "unread",
    }
    assert blob["ok"] is True
    assert blob["project_path"] == "C:\\proj\\x.PrjPcb"
