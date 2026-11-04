# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic audit, structured visual/layout violation report.

Slice C.2 scope: visual layout checks on the active schematic. Reads the
parts, wires, and power ports off each SchDoc via the generic
`obj_query` primitive and computes three violation classes:

  1. Component-overlap            , bounding boxes intersect.
  2. Wire-through-component       , a wire segment crosses a component
                                    body rectangle (excluding pin
                                    endpoints, which are legal).
  3. Stacked-port clusters        , 3+ power/ground ports of the same
                                    net huddled inside a small circle.

The report is LLM-readable: each violation carries enough geometry for
the planner to revise the layout (target refdes / segment / centre /
cluster members). No Altium round-trip beyond `obj_query`; all
intelligence lives Python-side.

Property-name uncertainty: as of this slice the Pascal `GetSchProperty`
accepts `Location.X`, `Location.Y`, `Text`, `Designator.Text`, but does
NOT advertise `BoundingRectangle.*` or `Vertex.*.X / .Y` for `eWire`.
The audit code reads bbox / vertex fields defensively and falls back to
`Location.X / Y` + a default bbox half (DEFAULT_BBOX_HALF_MILS) when
those richer properties come back empty. Tests use a fake bridge that
supplies the explicit fields the algorithm reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, Field


# Default half-extent for a component body in mils when no
# BoundingRectangle.* properties are accessible from the Pascal layer.
# 600 mils squares roughly the visual size of a stock symbol; over-
# estimating here is conservative (more false-positive overlap reports
# rather than missed real overlaps).
DEFAULT_BBOX_HALF_MILS = 600

# Margin added to centre-to-centre overlap test so two parts whose
# bboxes only kiss don't trigger a violation.
OVERLAP_MARGIN_MILS = 25

# Default cluster radius for stacked-port detection.
DEFAULT_CLUSTER_RADIUS_MILS = 600

# Minimum members for a power-port group to count as a "stacked cluster".
# A 2-port pair is legitimate (two pins of the same rail on the same
# IC); 3+ ports clumped inside a small circle is the visual-clutter
# pattern this check exists to surface.
STACKED_PORT_MIN_MEMBERS = 3


# --------------------------------------------------------------------
# Pydantic models for the structured report
# --------------------------------------------------------------------


class OverlapViolation(BaseModel):
    """Two components whose bounding boxes intersect."""

    refdes_a: str
    refdes_b: str
    sheet: str
    overlap_mils: int = Field(
        description=(
            "Minimum of the two bbox-axis overlap distances; 0 means "
            "they only touch."
        )
    )
    centre_a: tuple[int, int]
    centre_b: tuple[int, int]


class WireCrossing(BaseModel):
    """A wire segment that crosses a component body rectangle."""

    wire_idx: int
    sheet: str
    refdes_crossed: str
    segment: tuple[tuple[int, int], tuple[int, int]]
    bbox: tuple[int, int, int, int]


class StackedPorts(BaseModel):
    """3+ power-port glyphs of the same net huddled in a small circle."""

    net_name: str
    sheet: str
    cluster_centre: tuple[int, int]
    cluster_count: int
    members: list[tuple[int, int]]


class SchematicAuditReport(BaseModel):
    """Top-level audit result."""

    ok: bool = True
    project_path: Optional[str] = None
    overlaps: list[OverlapViolation] = Field(default_factory=list)
    wire_crossings: list[WireCrossing] = Field(default_factory=list)
    stacked_ports: list[StackedPorts] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Reads that failed, so what they cover was not checked; any entry
    # keeps ok False.
    unread: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_path": self.project_path,
            "overlaps": [v.model_dump() for v in self.overlaps],
            "wire_crossings": [v.model_dump() for v in self.wire_crossings],
            "stacked_ports": [v.model_dump() for v in self.stacked_ports],
            "notes": list(self.notes),
            "unread": list(self.unread),
        }


# --------------------------------------------------------------------
# Geometry helpers, plain Python, no Altium import.
# --------------------------------------------------------------------


@dataclass(frozen=True)
class _CompBox:
    refdes: str
    sheet: str
    x1: int  # left
    y1: int  # bottom
    x2: int  # right
    y2: int  # top

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2


@dataclass(frozen=True)
class _WireSeg:
    idx: int
    sheet: str
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class _Port:
    net: str
    sheet: str
    x: int
    y: int


def _coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort int coercion. Bridge returns strings; tolerate junk."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _bbox_intersect(a: _CompBox, b: _CompBox) -> int:
    """Return the minimum-axis overlap in mils, or -1 if no overlap.

    AABB intersection test: components overlap iff x-spans overlap AND
    y-spans overlap. Returns 0 when they touch on an edge (treated as
    a violation per spec: "touching only at corners still counts").
    """
    if a.sheet != b.sheet:
        return -1
    x_overlap = min(a.x2, b.x2) - max(a.x1, b.x1)
    y_overlap = min(a.y2, b.y2) - max(a.y1, b.y1)
    if x_overlap < 0 or y_overlap < 0:
        return -1
    return min(x_overlap, y_overlap)


def _segment_intersects_rect(
    x1: int, y1: int, x2: int, y2: int,
    rx1: int, ry1: int, rx2: int, ry2: int,
) -> bool:
    """Liang-Barsky segment vs axis-aligned rectangle test.

    Returns True if the segment crosses the rectangle interior OR
    touches it on any edge / corner. Endpoints lying inside the
    rectangle count; an endpoint exactly on the boundary counts.
    """
    # Normalise rect.
    xmin = min(rx1, rx2)
    xmax = max(rx1, rx2)
    ymin = min(ry1, ry2)
    ymax = max(ry1, ry2)

    dx = x2 - x1
    dy = y2 - y1

    # Each clip step shrinks (t_enter, t_exit) on [0, 1]. If
    # t_enter > t_exit at any step, no intersection.
    t_enter = 0.0
    t_exit = 1.0

    for p, q in (
        (-dx, x1 - xmin),
        (dx, xmax - x1),
        (-dy, y1 - ymin),
        (dy, ymax - y1),
    ):
        if p == 0:
            # Segment parallel to this edge; if q < 0 the segment is
            # entirely outside the slab.
            if q < 0:
                return False
            # else: parallel but inside the slab, no clip.
        else:
            t = q / p
            if p < 0:
                if t > t_exit:
                    return False
                if t > t_enter:
                    t_enter = t
            else:
                if t < t_enter:
                    return False
                if t < t_exit:
                    t_exit = t

    return t_enter <= t_exit


# --------------------------------------------------------------------
# Bridge helpers, isolate the IPC call so tests can fake it cleanly.
# --------------------------------------------------------------------


_COMPONENT_PROPS = (
    "Designator.Text,"
    "BoundingRectangle.X1,BoundingRectangle.Y1,"
    "BoundingRectangle.X2,BoundingRectangle.Y2,"
    "Location.X,Location.Y"
)

_WIRE_PROPS = (
    "Vertex.1.X,Vertex.1.Y,Vertex.2.X,Vertex.2.Y,VerticesCount,"
    "Location.X,Location.Y"
)

_PORT_PROPS = "Text,Location.X,Location.Y"


def _doc_scope(sheet_path: str) -> str:
    return f"doc:{sheet_path}"


def _query(
    bridge: Any,
    object_type: str,
    properties: str,
    scope: str,
    unread: list[str],
) -> list[dict[str, Any]]:
    """Run query_objects through the bridge and pull out the list.

    Takes a list of dicts or ``{"objects": [...]}``. A failed call
    returns [] and says so in ``unread``.
    """
    try:
        result = bridge.send_command(
            "generic.query_objects",
            {
                "object_type": object_type,
                "properties": properties,
                "scope": scope,
                "filter": "",
            },
        )
    except Exception as e:  # any bridge failure: record it, check the rest
        unread.append(f"{object_type} in {scope}: {e}")
        return []
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        objs = result.get("objects", [])
        if isinstance(objs, list):
            return [r for r in objs if isinstance(r, dict)]
    return []


def _list_sheets(bridge: Any, project_path: Optional[str],
                 unread: Optional[list[str]] = None) -> list[str]:
    """Find every .SchDoc in the project (or focused project).

    Uses `project.get_project_documents` when available, falls back to
    a single-sheet sentinel ("") so the audit still runs on the active
    document if document enumeration fails. A failed enumeration is
    recorded in ``unread``: the other sheets go unchecked.
    """
    params: dict[str, Any] = {}
    if project_path:
        params["project_path"] = project_path
    try:
        # Pascal dispatcher exposes this as 'get_documents' (the Python
        # MCP tool 'proj_list_documents' wraps it). Using the wrong
        # action name surfaces as an UNKNOWN_ACTION error in the
        # activity feed.
        result = bridge.send_command("project.get_documents", params)
    except Exception as e:  # any bridge failure: audit the active sheet only
        if unread is not None:
            unread.append(f"the project's sheet list: {e}; "
                          "only the active sheet was checked")
        return [""]

    # Proj_GetDocuments answers with a BARE JSON ARRAY as its data, and
    # send_command hands back response.data unwrapped, so `result` is a
    # LIST. This used to require a dict, so the loop never ran, `sheets`
    # stayed empty and the function always fell through to the
    # single-sheet sentinel below. The sentinel is documented as a
    # fallback for when enumeration fails; enumeration failed every
    # time, and the only symptom was a multi-sheet project being audited
    # on its active sheet alone, quietly.
    #
    # The dict shapes are kept because they cost nothing and other
    # handlers do wrap their arrays.
    if isinstance(result, list):
        docs: Any = result
    elif isinstance(result, dict):
        docs = result.get("documents") or result.get("sheets") or []
    else:
        docs = []

    sheets: list[str] = []
    if isinstance(docs, list):
        for entry in docs:
            if isinstance(entry, dict):
                path = (
                    entry.get("file_path")
                    or entry.get("path")
                    or entry.get("FileName")
                    or ""
                )
                kind = (
                    entry.get("kind")
                    or entry.get("document_kind")
                    or entry.get("Kind")
                    or ""
                )
                if path and (not kind or "SCH" in str(kind).upper()
                             or str(path).lower().endswith(".schdoc")):
                    sheets.append(str(path))
            elif isinstance(entry, str):
                if entry.lower().endswith(".schdoc"):
                    sheets.append(entry)
    return sheets or [""]


# --------------------------------------------------------------------
# Row -> dataclass parsers (bbox / wire / port).
# --------------------------------------------------------------------


def _row_to_box(row: dict[str, Any], sheet: str) -> Optional[_CompBox]:
    refdes = str(row.get("Designator.Text") or row.get("Designator") or "").strip()
    if not refdes:
        return None

    # Prefer explicit bbox when available; otherwise synthesize from
    # Location + DEFAULT_BBOX_HALF_MILS.
    has_bbox = any(
        row.get(k) not in (None, "")
        for k in (
            "BoundingRectangle.X1",
            "BoundingRectangle.Y1",
            "BoundingRectangle.X2",
            "BoundingRectangle.Y2",
        )
    )
    if has_bbox:
        x1 = _coerce_int(row.get("BoundingRectangle.X1"))
        y1 = _coerce_int(row.get("BoundingRectangle.Y1"))
        x2 = _coerce_int(row.get("BoundingRectangle.X2"))
        y2 = _coerce_int(row.get("BoundingRectangle.Y2"))
        # Normalise so x1<x2 / y1<y2.
        xmin, xmax = (x1, x2) if x1 <= x2 else (x2, x1)
        ymin, ymax = (y1, y2) if y1 <= y2 else (y2, y1)
        return _CompBox(refdes=refdes, sheet=sheet,
                        x1=xmin, y1=ymin, x2=xmax, y2=ymax)

    # Fallback path: pivot a square around Location.
    if row.get("Location.X") in (None, "") and row.get("Location.Y") in (None, ""):
        return None
    lx = _coerce_int(row.get("Location.X"))
    ly = _coerce_int(row.get("Location.Y"))
    h = DEFAULT_BBOX_HALF_MILS
    return _CompBox(
        refdes=refdes, sheet=sheet,
        x1=lx - h, y1=ly - h, x2=lx + h, y2=ly + h,
    )


def _row_to_wire(row: dict[str, Any], sheet: str, idx: int) -> Optional[_WireSeg]:
    # Prefer Vertex.1.X / .2.X if exposed; otherwise no wire geometry
    # is available (Location.X gives one point only, not a segment).
    v1x = row.get("Vertex.1.X")
    v1y = row.get("Vertex.1.Y")
    v2x = row.get("Vertex.2.X")
    v2y = row.get("Vertex.2.Y")
    if all(v not in (None, "") for v in (v1x, v1y, v2x, v2y)):
        return _WireSeg(
            idx=idx, sheet=sheet,
            x1=_coerce_int(v1x), y1=_coerce_int(v1y),
            x2=_coerce_int(v2x), y2=_coerce_int(v2y),
        )
    return None


def _row_to_port(row: dict[str, Any], sheet: str) -> Optional[_Port]:
    net = str(row.get("Text") or "").strip()
    if not net:
        return None
    if row.get("Location.X") in (None, "") and row.get("Location.Y") in (None, ""):
        return None
    return _Port(
        net=net, sheet=sheet,
        x=_coerce_int(row.get("Location.X")),
        y=_coerce_int(row.get("Location.Y")),
    )


# --------------------------------------------------------------------
# Violation detectors.
# --------------------------------------------------------------------


def _detect_overlaps(boxes: list[_CompBox]) -> list[OverlapViolation]:
    out: list[OverlapViolation] = []
    n = len(boxes)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            ovl = _bbox_intersect(a, b)
            if ovl < 0:
                continue
            # Apply small margin: only flag when they really overlap.
            if ovl + OVERLAP_MARGIN_MILS < 0:
                continue
            out.append(
                OverlapViolation(
                    refdes_a=a.refdes,
                    refdes_b=b.refdes,
                    sheet=a.sheet,
                    overlap_mils=int(ovl),
                    centre_a=(a.cx, a.cy),
                    centre_b=(b.cx, b.cy),
                )
            )
    return out


def _wire_endpoint_on_pin(
    x: int, y: int,
    boxes: list[_CompBox],
    tolerance: int = 25,
) -> bool:
    """A wire endpoint is "on a pin" when it sits on the boundary of a
    component bbox (within tolerance). True body-internal endpoints
    are NOT pin endpoints; they're stray wire stubs and still count.
    """
    for b in boxes:
        # On the rectangle boundary: x near one of x1/x2 with y in
        # [y1, y2], or y near one of y1/y2 with x in [x1, x2].
        near_x_edge = abs(x - b.x1) <= tolerance or abs(x - b.x2) <= tolerance
        near_y_edge = abs(y - b.y1) <= tolerance or abs(y - b.y2) <= tolerance
        in_y_span = b.y1 - tolerance <= y <= b.y2 + tolerance
        in_x_span = b.x1 - tolerance <= x <= b.x2 + tolerance
        if (near_x_edge and in_y_span) or (near_y_edge and in_x_span):
            return True
    return False


def _detect_wire_crossings(
    wires: list[_WireSeg],
    boxes: list[_CompBox],
) -> list[WireCrossing]:
    out: list[WireCrossing] = []
    by_sheet: dict[str, list[_CompBox]] = {}
    for b in boxes:
        by_sheet.setdefault(b.sheet, []).append(b)

    for w in wires:
        sheet_boxes = by_sheet.get(w.sheet, [])
        # Skip wires whose BOTH endpoints sit on component pin
        # boundaries; that's a normal pin-to-pin connection, not a
        # cross-through.
        if (
            _wire_endpoint_on_pin(w.x1, w.y1, sheet_boxes)
            and _wire_endpoint_on_pin(w.x2, w.y2, sheet_boxes)
        ):
            continue
        for b in sheet_boxes:
            if not _segment_intersects_rect(
                w.x1, w.y1, w.x2, w.y2,
                b.x1, b.y1, b.x2, b.y2,
            ):
                continue
            # If one endpoint is on this same component's boundary, the
            # wire is just attached, not crossing through.
            if _wire_endpoint_on_pin(w.x1, w.y1, [b]) or \
               _wire_endpoint_on_pin(w.x2, w.y2, [b]):
                continue
            out.append(
                WireCrossing(
                    wire_idx=w.idx,
                    sheet=w.sheet,
                    refdes_crossed=b.refdes,
                    segment=((w.x1, w.y1), (w.x2, w.y2)),
                    bbox=(b.x1, b.y1, b.x2, b.y2),
                )
            )
    return out


def _detect_stacked_ports(
    ports: list[_Port],
    cluster_radius_mils: int,
    min_members: int = STACKED_PORT_MIN_MEMBERS,
) -> list[StackedPorts]:
    out: list[StackedPorts] = []
    grouped: dict[tuple[str, str], list[_Port]] = {}
    for p in ports:
        grouped.setdefault((p.net, p.sheet), []).append(p)

    r2 = cluster_radius_mils * cluster_radius_mils

    for (net, sheet), members in grouped.items():
        if len(members) < min_members:
            continue

        # Greedy clustering: pick the densest seed (max neighbours
        # within radius), emit it, remove its members, repeat. Simple
        # and deterministic; good enough for visual-audit signal.
        remaining = list(members)
        while len(remaining) >= min_members:
            best_idx = -1
            best_cluster: list[_Port] = []
            for i, seed in enumerate(remaining):
                cluster = [
                    p for p in remaining
                    if (p.x - seed.x) ** 2 + (p.y - seed.y) ** 2 <= r2
                ]
                if len(cluster) > len(best_cluster):
                    best_cluster = cluster
                    best_idx = i
            if len(best_cluster) < min_members or best_idx < 0:
                break

            cx = sum(p.x for p in best_cluster) // len(best_cluster)
            cy = sum(p.y for p in best_cluster) // len(best_cluster)
            out.append(
                StackedPorts(
                    net_name=net,
                    sheet=sheet,
                    cluster_centre=(cx, cy),
                    cluster_count=len(best_cluster),
                    members=[(p.x, p.y) for p in best_cluster],
                )
            )
            remaining = [p for p in remaining if p not in best_cluster]

    return out


# --------------------------------------------------------------------
# Public entry point.
# --------------------------------------------------------------------


def audit_schematic(
    project_path: Optional[str] = None,
    *,
    bridge: Optional[Any] = None,
    cluster_radius_mils: int = DEFAULT_CLUSTER_RADIUS_MILS,
) -> SchematicAuditReport:
    """Audit visual / layout problems on the project's schematic sheets.

    Args:
        project_path: Absolute path to a .PrjPcb. None = focused project.
        bridge: Optional bridge override (tests inject a fake).
        cluster_radius_mils: Radius for stacked-port clustering.

    Returns:
        SchematicAuditReport with ok=True iff zero violations.
    """
    report = SchematicAuditReport(project_path=project_path)

    if bridge is None:
        from eda_agent.bridge import get_bridge  # late import, needs Altium
        bridge = get_bridge()

    sheets = _list_sheets(bridge, project_path, report.unread)
    if not sheets:
        report.notes.append("no schematic sheets found")
        return report

    all_boxes: list[_CompBox] = []
    all_wires: list[_WireSeg] = []
    all_ports: list[_Port] = []
    wire_counter = 0

    for sheet in sheets:
        scope = _doc_scope(sheet) if sheet else "active_doc"
        # Components.
        comp_rows = _query(bridge, "eSchComponent", _COMPONENT_PROPS, scope,
                           report.unread)
        had_bbox = False
        for row in comp_rows:
            box = _row_to_box(row, sheet)
            if box is None:
                continue
            if any(
                row.get(k) not in (None, "")
                for k in (
                    "BoundingRectangle.X1",
                    "BoundingRectangle.Y1",
                    "BoundingRectangle.X2",
                    "BoundingRectangle.Y2",
                )
            ):
                had_bbox = True
            all_boxes.append(box)
        if comp_rows and not had_bbox:
            report.notes.append(
                f"sheet {sheet or '<active>'}: BoundingRectangle.* not "
                "exposed by Pascal layer; used Location +/- "
                f"{DEFAULT_BBOX_HALF_MILS} mils fallback"
            )

        # Wires.
        wire_rows = _query(bridge, "eWire", _WIRE_PROPS, scope, report.unread)
        unread_wires = 0
        for row in wire_rows:
            wire_counter += 1
            seg = _row_to_wire(row, sheet, wire_counter)
            if seg is None:
                unread_wires += 1
                continue
            all_wires.append(seg)
        if unread_wires:
            report.notes.append(
                f"sheet {sheet or '<active>'}: {unread_wires} wire(s) "
                "had no readable Vertex.*.X/Y; wire-crossing check "
                "skipped those segments"
            )

        # Power / ground ports.
        port_rows = _query(bridge, "ePowerObject", _PORT_PROPS, scope,
                           report.unread)
        for row in port_rows:
            port = _row_to_port(row, sheet)
            if port is None:
                continue
            all_ports.append(port)

    report.overlaps = _detect_overlaps(all_boxes)
    report.wire_crossings = _detect_wire_crossings(all_wires, all_boxes)
    report.stacked_ports = _detect_stacked_ports(all_ports, cluster_radius_mils)

    report.ok = (
        not report.overlaps
        and not report.wire_crossings
        and not report.stacked_ports
        and not report.unread
    )
    return report
