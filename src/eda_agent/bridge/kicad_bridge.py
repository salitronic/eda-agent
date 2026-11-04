# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Live connection to a running KiCad via its IPC API (kicad-python / kipy).

Mirrors the role of the Altium bridge: KiCad stays open and in control, and
eda-agent talks to it over KiCad's supported protobuf IPC socket. There is no
polling loop to maintain here; KiCad hosts the server and the ``kipy`` client
connects to it.

Requirements on the KiCad side (KiCad 9+):
  * Preferences > Plugins: enable the KiCad API server (off by default).
  * A board open in the PCB editor for board queries; the API server only
    answers document/board requests when an editor frame owns a document.

Everything here is read-only for now. ``kipy`` is imported lazily so importing
this module never fails when KiCad or the client library is absent; the failure
surfaces as a clear reason at call time instead.
"""

from __future__ import annotations

from typing import Any, Optional

# KiCad internal coordinates are nanometres.
_NM_PER_MM = 1_000_000


def _mm(nm: Optional[int]) -> Optional[float]:
    if nm is None:
        return None
    return round(nm / _NM_PER_MM, 4)


# What reading a field raises when an item does not carry it (a pad on no
# net, a shape with no radius, a field this KiCad version lacks). A dropped
# connection or a failed API call is not among them and reaches the caller.
_ABSENT = (AttributeError, TypeError, ValueError, KeyError, IndexError)


def _get(read, default=None):
    """What ``read()`` returns, or ``default`` when the item lacks the field."""
    try:
        return read()
    except _ABSENT:
        return default


class _NoApiError(Exception):
    """Stands in for kipy's ApiError where kipy is not installed."""


def _api_error() -> type:
    """kipy's error for a call KiCad received and refused, e.g. one its
    version does not handle."""
    try:
        from kipy.errors import ApiError
    except ImportError:
        return _NoApiError
    return ApiError


class KiCadNotReachableError(RuntimeError):
    """KiCad is not running, its API server is off, or no board is open."""


class KiCadBridge:
    """Thin, reconnecting wrapper over the kipy client.

    One instance is reused across calls (see :func:`get_kicad_bridge`). The
    underlying client is cached and re-created transparently after a dropped
    connection, since KiCad can be closed and reopened under a long-lived
    server.
    """

    def __init__(self) -> None:
        self._client = None  # kipy.KiCad, created lazily

    # -- connection ---------------------------------------------------------
    def _kicad(self):
        """Return a live kipy.KiCad, (re)connecting as needed.

        Raises KiCadNotReachableError with an actionable message when the
        client library is missing or KiCad's API server is not answering.
        """
        try:
            from kipy import KiCad
            from kipy.errors import ConnectionError as KiConnError
        except Exception as e:  # pragma: no cover - import guard
            raise KiCadNotReachableError(
                "kicad-python (kipy) is not installed: " + str(e)) from None

        if self._client is not None:
            try:
                self._client.ping()
                return self._client
            except Exception:  # any failure means a stale client: reconnect below
                self._client = None

        try:
            client = KiCad()
            client.ping()
        except KiConnError as e:
            raise KiCadNotReachableError(
                "cannot reach KiCad's API server (" + str(e) + "). Open KiCad, "
                "enable Preferences > Plugins > KiCad API server, and restart "
                "KiCad.") from None
        except Exception as e:
            raise KiCadNotReachableError(
                "failed to connect to KiCad: " + str(e)) from None
        self._client = client
        return client

    def ping(self) -> dict[str, Any]:
        """Connection status plus KiCad and API versions."""
        k = self._kicad()
        return {
            "connected": True,
            "kicad_version": str(k.get_version()),
            "api_version": str(k.get_api_version()),
        }

    def _board(self):
        """The open PCB, or a clear error when none is available."""
        k = self._kicad()
        try:
            return k.get_board()
        except Exception as e:
            raise KiCadNotReachableError(
                "no board is available (" + str(e) + "). Open a project and its "
                "PCB in the KiCad PCB editor.") from None

    # -- read helpers -------------------------------------------------------
    @staticmethod
    def _field_text(field) -> str:
        return _get(lambda: field.text.value, "")

    def footprints(self) -> list[dict[str, Any]]:
        """Every footprint on the board, as plain dicts."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for f in board.get_footprints():
            ref = self._field_text(getattr(f, "reference_field", None))
            val = self._field_text(getattr(f, "value_field", None))
            pos = getattr(f, "position", None)
            x = _mm(getattr(pos, "x", None)) if pos is not None else None
            y = _mm(getattr(pos, "y", None)) if pos is not None else None
            out.append({"reference": ref, "value": val,
                        "footprint_id": _get(lambda: str(f.definition.id), ""),
                        "x_mm": x, "y_mm": y,
                        "layer": _get(lambda: int(f.layer)),
                        "locked": _get(lambda: bool(f.locked))})
        return out

    def nets(self) -> list[dict[str, Any]]:
        board = self._board()
        # Net.code is deprecated in KiCad 10, where a net is known by name.
        return [{"name": n.name, "code": _get(lambda: int(n.code))}
                for n in board.get_nets()]

    def component_pins(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """(parts, pins, unconnected-pad-count) for the normalized snapshot.

        Each part: ``{refdes, value, footprint, layer, locked, dnp}``. Each pin:
        ``{refdes, pin, net}`` where net ``""`` means the pad is on no net. A
        placed footprint's ``definition.pads`` carry the live net assignments,
        which is what lets one pass recover the full net<->pin adjacency.
        """
        board = self._board()
        parts: list[dict[str, Any]] = []
        pins: list[dict[str, Any]] = []
        unconnected = 0
        for f in board.get_footprints():
            ref = self._field_text(getattr(f, "reference_field", None))
            val = self._field_text(getattr(f, "value_field", None))
            parts.append({
                "refdes": ref, "value": val,
                "footprint": _get(lambda: str(f.definition.id), ""),
                "layer": _get(lambda: int(f.layer)),
                "locked": _get(lambda: bool(f.locked)),
                "dnp": _get(lambda: bool(f.attributes.do_not_populate), False)})
            for p in _get(lambda: list(f.definition.pads), []):
                net = _get(lambda: p.net.name or "", "")
                pins.append({"refdes": ref, "pin": _get(lambda: str(p.number), ""),
                             "net": net})
                if not net:
                    unconnected += 1
        return parts, pins, unconnected

    def pad_net_counts(self) -> tuple[dict[str, int], int]:
        """(pads-per-net-name, count of pads with no net)."""
        board = self._board()
        per_net: dict[str, int] = {}
        no_net = 0
        for p in board.get_pads():
            name = _get(lambda: p.net.name, "")
            if name:
                per_net[name] = per_net.get(name, 0) + 1
            else:
                no_net += 1
        return per_net, no_net

    def _layer_name(self, board, layer) -> str:
        """The layer's name, its number where KiCad (before 9.0.8) has no
        call for the name, or "" for an item with no layer."""
        if layer is None:
            return ""
        try:
            return board.get_layer_name(layer)
        except _api_error():
            return _get(lambda: str(int(layer)), "")

    @staticmethod
    def _point(item, attr):
        return _get(lambda: [_mm(getattr(item, attr).x), _mm(getattr(item, attr).y)])

    def tracks(self) -> list[dict[str, Any]]:
        """Copper tracks and arcs: net, layer, width, endpoints, length."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for t in board.get_tracks():
            row: dict[str, Any] = {
                "net": _get(lambda: t.net.name or "", ""),
                "layer": self._layer_name(board, _get(lambda: t.layer)),
                "width_mm": _get(lambda: _mm(t.width)),
                "length_mm": _get(lambda: _mm(t.length())),
                "is_arc": hasattr(t, "mid")}
            for end in ("start", "end"):
                point = self._point(t, end)
                if point is not None:
                    row[f"{end}_mm"] = point
            out.append(row)
        return out

    def vias(self) -> list[dict[str, Any]]:
        board = self._board()
        out: list[dict[str, Any]] = []
        for v in board.get_vias():
            x, y = self._point(v, "position") or (None, None)
            out.append({"net": _get(lambda: v.net.name or "", ""),
                        "x_mm": x, "y_mm": y,
                        "diameter_mm": _get(lambda: _mm(v.diameter)),
                        "drill_mm": _get(lambda: _mm(v.drill_diameter))})
        return out

    def zones(self) -> list[dict[str, Any]]:
        board = self._board()
        out: list[dict[str, Any]] = []
        for z in board.get_zones():
            out.append({
                "name": _get(lambda: z.name or "", ""),
                "net": _get(lambda: z.net.name if z.net is not None else "", ""),
                "layers": [self._layer_name(board, l)
                           for l in _get(lambda: list(z.layers), [])],
                "filled": _get(lambda: z.filled),
                "priority": _get(lambda: z.priority),
                "is_rule_area": _get(lambda: z.is_rule_area())})
        return out

    def stackup(self) -> list[dict[str, Any]]:
        board = self._board()
        out: list[dict[str, Any]] = []
        for l in board.get_stackup().layers:
            out.append({
                "name": _get(lambda: l.user_name),
                "type": _get(lambda: l.type),
                "material": _get(lambda: l.material_name),
                "thickness_mm": _get(lambda: _mm(l.thickness)),
                "layer": self._layer_name(board, _get(lambda: l.layer))})
        return out

    def layers(self) -> list[str]:
        board = self._board()
        return [self._layer_name(board, l) for l in board.get_enabled_layers()]

    def board_outline(self) -> dict[str, Any]:
        """Bounding box (mm) of the board edge, from Edge.Cuts graphics."""
        board = self._board()
        xs: list[int] = []
        ys: list[int] = []
        for s in board.get_shapes():
            if self._layer_name(board, _get(lambda: s.layer)) != "Edge.Cuts":
                continue
            for pt in ("start", "end"):
                xy = _get(lambda: (getattr(s, pt).x, getattr(s, pt).y))
                if xy is not None:
                    xs.append(xy[0])
                    ys.append(xy[1])
        if not xs or not ys:
            return {"bbox_mm": None, "edge_shape_count": 0}
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        return {"bbox_mm": {"x": _mm(x0), "y": _mm(y0),
                            "w": _mm(x1 - x0), "h": _mm(y1 - y0)},
                "edge_shape_count": len(xs) // 2}

    def title_block(self) -> dict[str, Any]:
        tb = self._board().get_title_block_info()
        return {attr: _get(lambda: getattr(tb, attr))
                for attr in ("title", "date", "revision", "company", "comment1",
                             "comment2", "comment3", "comment4")}

    def selection(self) -> list[dict[str, Any]]:
        """Items currently selected in the PCB editor: type and (for
        footprints) reference."""
        out: list[dict[str, Any]] = []
        for item in self._board().get_selection():
            row: dict[str, Any] = {"type": type(item).__name__}
            ref = self._field_text(getattr(item, "reference_field", None))
            if ref:
                row["reference"] = ref
            out.append(row)
        return out

    def shapes(self) -> list[dict[str, Any]]:
        """Graphic shapes (segment/arc/circle/rect/polygon): type, layer, net,
        and the geometry points available for the shape (mm)."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for s in board.get_shapes():
            row: dict[str, Any] = {
                "type": type(s).__name__.replace("Board", "").lower(),
                "layer": self._layer_name(board, _get(lambda: s.layer)),
                "net": _get(lambda: s.net.name if s.net is not None else "", "") or ""}
            for attr in ("start", "end", "center"):
                point = self._point(s, attr)
                if point is not None:
                    row[attr + "_mm"] = point
            radius = _get(lambda: _mm(s.radius))
            if radius is not None:
                row["radius_mm"] = radius
            out.append(row)
        return out

    def texts(self) -> list[dict[str, Any]]:
        """Free board text: value, layer, position (mm)."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for t in board.get_text():
            x, y = self._point(t, "position") or (None, None)
            out.append({"value": _get(lambda: t.value or "", ""),
                        "layer": self._layer_name(board, _get(lambda: t.layer)),
                        "x_mm": x, "y_mm": y})
        return out

    def groups(self) -> list[dict[str, Any]]:
        """Item groups: name and member count."""
        return [{"name": _get(lambda: g.name or "", ""),
                 "item_count": _get(lambda: len(list(g.items)), 0)}
                for g in self._board().get_groups()]

    def dimensions(self) -> list[dict[str, Any]]:
        """Dimension annotations: layer, override text, height (mm)."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for d in board.get_dimensions():
            override = ""
            if getattr(d, "override_text_enabled", False):
                override = _get(lambda: d.override_text or "", "")
            out.append({"layer": self._layer_name(board, _get(lambda: d.layer)),
                        "override_text": override,
                        "height_mm": _get(lambda: _mm(d.height))})
        return out

    def open_documents(self) -> list[dict[str, Any]]:
        """Open documents in KiCad: PCB and schematic, with filename."""
        k = self._kicad()
        from kipy.proto.common.types import DocumentType
        out: list[dict[str, Any]] = []
        for dtype, label in ((DocumentType.DOCTYPE_PCB, "pcb"),
                             (DocumentType.DOCTYPE_SCHEMATIC, "schematic")):
            try:
                docs = k.get_open_documents(dtype)
            except _api_error():  # KiCad 9 has no schematic API
                continue
            for d in docs:
                name = next((v for v in (getattr(d, attr, "") for attr in
                             ("board_filename", "schematic_filename", "name"))
                             if v), "")
                proj = getattr(getattr(d, "project", None), "name", "") or ""
                out.append({"type": label, "filename": name, "project": proj})
        return out

    def project_info(self) -> dict[str, Any]:
        """Open project's name, directory, and net-class names."""
        board = self._board()
        try:
            proj = board.get_project()
        except Exception as e:
            raise KiCadNotReachableError(
                "no project available (" + str(e) + ")") from None
        return {"name": getattr(proj, "name", "") or "",
                "path": getattr(proj, "path", "") or "",
                "net_classes": [nc.name for nc in proj.get_net_classes()]}

    def text_variables(self) -> dict[str, Any]:
        """Project text (substitution) variables as a plain dict."""
        tv = self._board().get_project().get_text_variables()
        return {str(k): str(v) for k, v in tv.items()}

    def net_classes(self) -> dict[str, Any]:
        """Per-net net-class assignment and each class's routing rules (mm)."""
        board = self._board()
        mapping = board.get_netclass_for_nets(list(board.get_nets()))

        def _safe_mm(nc, attr):
            return _get(lambda: _mm(getattr(nc, attr)))

        by_net: dict[str, str] = {}
        classes: dict[str, Any] = {}
        for net_name, nc in mapping.items():
            cls = _get(lambda: nc.name or "", "")
            by_net[net_name] = cls
            if cls and cls not in classes:
                classes[cls] = {
                    "clearance_mm": _safe_mm(nc, "clearance"),
                    "diff_pair_gap_mm": _safe_mm(nc, "diff_pair_gap"),
                    "diff_pair_track_width_mm":
                        _safe_mm(nc, "diff_pair_track_width"),
                    "bus_width_mm": _safe_mm(nc, "bus_width"),
                }
        return {"by_net": by_net, "classes": classes}

    def pads(self) -> list[dict[str, Any]]:
        """All pads: number, net, position (mm), pad type."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for p in board.get_pads():
            x, y = self._point(p, "position") or (None, None)
            out.append({"number": _get(lambda: str(p.number), ""),
                        "net": _get(lambda: p.net.name or "", ""),
                        "x_mm": x, "y_mm": y,
                        "pad_type": _get(lambda: int(p.pad_type))})
        return out

    def sch_file_path(self) -> str:
        """Absolute path of the project's schematic (.kicad_sch) on disk."""
        import os
        board = self.board_file_path()
        root, _ = os.path.splitext(board)
        return root + ".kicad_sch"

    # -- authoring (writes) -------------------------------------------------
    # These mutate the open board. Each groups its change in a commit (one undo
    # step) and applies it; pass save=True to also write the file to disk.
    def _find_footprint(self, board, reference: str):
        for f in board.get_footprints():
            if self._field_text(getattr(f, "reference_field", None)) == reference:
                return f
        raise KiCadNotReachableError(
            "no footprint with reference '" + str(reference) + "'")

    def _apply(self, board, apply_fn, save: bool):
        """Run a mutation inside a commit; push it, or drop it on error.

        A failed push or save raises: the change did not land, or did not
        reach the file, and the caller must not report it done.
        """
        commit = board.begin_commit()
        try:
            result = apply_fn()
        except Exception:
            try:
                board.drop_commit(commit)
            except Exception:  # the mutation's own error is the one to report
                pass
            raise
        board.push_commit(commit, "eda-agent")
        if save:
            board.save()
        return result

    def move_component(self, reference: str, x_mm: float, y_mm: float,
                       save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.position = Vector2.from_xy_mm(float(x_mm), float(y_mm))
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "x_mm": x_mm, "y_mm": y_mm,
                "saved": save}

    def rotate_component(self, reference: str, degrees: float,
                         save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Angle
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.orientation = Angle.from_degrees(float(degrees))
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "orientation_deg": degrees,
                "saved": save}

    def set_component_side(self, reference: str, side: str,
                           save: bool = False) -> dict[str, Any]:
        from kipy.board_types import BoardLayer
        board = self._board()
        f = self._find_footprint(board, reference)
        bottom = str(side).strip().lower() in ("bottom", "b", "back", "b.cu")

        def apply():
            f.layer = BoardLayer.BL_B_Cu if bottom else BoardLayer.BL_F_Cu
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "side": "bottom" if bottom else "top",
                "saved": save}

    def set_component_locked(self, reference: str, locked: bool,
                             save: bool = False) -> dict[str, Any]:
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.locked = bool(locked)
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "locked": bool(locked), "saved": save}

    def set_component_value(self, reference: str, value: str,
                            save: bool = False) -> dict[str, Any]:
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.value_field.text.value = str(value)
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "value": value, "saved": save}

    def delete_component(self, reference: str,
                         save: bool = False) -> dict[str, Any]:
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            board.remove_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "deleted": True, "saved": save}

    def _layer_by_name(self, board, layer: str):
        """Friendly layer name ("F.Cu", "F.Silkscreen") -> BoardLayer enum.

        Copper enum names follow "BL_<name>" (F.Cu -> BL_F_Cu), but technical
        layers do not (F.Silkscreen -> BL_F_SilkS), so fall back to matching
        against ``get_layer_name`` for anything the direct form misses. A
        name that matches no layer raises rather than drawing on F.Cu.
        """
        from kipy.board_types import BoardLayer
        key = str(layer).strip()
        try:
            return BoardLayer.Value("BL_" + key.replace(".", "_"))
        except ValueError:  # not an enum name: try the board's layer names
            pass
        for val in BoardLayer.values():
            try:
                if board.get_layer_name(val) == key:
                    return val
            except _api_error():  # a value with no layer on this board
                continue
        raise ValueError("no layer named '" + key + "' on this board")

    def create_track(self, net: str, layer: str, x1_mm: float, y1_mm: float,
                     x2_mm: float, y2_mm: float, width_mm: float,
                     save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import Track, Net
        board = self._board()
        t = Track()
        t.start = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        t.end = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        t.width = int(round(float(width_mm) * _NM_PER_MM))
        t.layer = self._layer_by_name(board, layer)
        if net:
            t.net = Net(name=str(net))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([t])
        self._apply(board, apply, save)
        return {"net": net, "layer": layer, "width_mm": width_mm,
                "start_mm": [x1_mm, y1_mm], "end_mm": [x2_mm, y2_mm],
                "created": len(created), "saved": save}

    def create_via(self, net: str, x_mm: float, y_mm: float,
                   diameter_mm: float, drill_mm: float,
                   save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import Via, Net
        board = self._board()
        v = Via()
        v.position = Vector2.from_xy_mm(float(x_mm), float(y_mm))
        v.diameter = int(round(float(diameter_mm) * _NM_PER_MM))
        v.drill_diameter = int(round(float(drill_mm) * _NM_PER_MM))
        if net:
            v.net = Net(name=str(net))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([v])
        self._apply(board, apply, save)
        return {"net": net, "x_mm": x_mm, "y_mm": y_mm,
                "diameter_mm": diameter_mm, "drill_mm": drill_mm,
                "created": len(created), "saved": save}

    def create_text(self, text: str, x_mm: float, y_mm: float,
                    layer: str = "F.Silkscreen",
                    save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardText
        board = self._board()
        t = BoardText()
        t.value = str(text)
        t.position = Vector2.from_xy_mm(float(x_mm), float(y_mm))
        t.layer = self._layer_by_name(board, layer)

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([t])
        self._apply(board, apply, save)
        return {"text": text, "x_mm": x_mm, "y_mm": y_mm, "layer": layer,
                "created": len(created), "saved": save}

    def create_line(self, x1_mm: float, y1_mm: float, x2_mm: float,
                    y2_mm: float, layer: str = "F.Silkscreen",
                    width_mm: float = 0.15,
                    save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardSegment
        board = self._board()
        s = BoardSegment()
        s.start = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        s.end = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        s.layer = self._layer_by_name(board, layer)
        s.width = int(round(float(width_mm) * _NM_PER_MM))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([s])
        self._apply(board, apply, save)
        return {"layer": layer, "width_mm": width_mm,
                "start_mm": [x1_mm, y1_mm], "end_mm": [x2_mm, y2_mm],
                "created": len(created), "saved": save}

    def create_circle(self, cx_mm: float, cy_mm: float, radius_mm: float,
                      layer: str = "F.Silkscreen", width_mm: float = 0.15,
                      save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardCircle
        board = self._board()
        c = BoardCircle()
        c.center = Vector2.from_xy_mm(float(cx_mm), float(cy_mm))
        c.radius = int(round(float(radius_mm) * _NM_PER_MM))
        c.layer = self._layer_by_name(board, layer)
        c.width = int(round(float(width_mm) * _NM_PER_MM))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([c])
        self._apply(board, apply, save)
        return {"layer": layer, "center_mm": [cx_mm, cy_mm],
                "radius_mm": radius_mm, "created": len(created), "saved": save}

    def create_rectangle(self, x1_mm: float, y1_mm: float, x2_mm: float,
                         y2_mm: float, layer: str = "F.Silkscreen",
                         width_mm: float = 0.15,
                         save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardRectangle
        board = self._board()
        r = BoardRectangle()
        r.top_left = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        r.bottom_right = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        r.layer = self._layer_by_name(board, layer)
        r.width = int(round(float(width_mm) * _NM_PER_MM))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([r])
        self._apply(board, apply, save)
        return {"layer": layer, "top_left_mm": [x1_mm, y1_mm],
                "bottom_right_mm": [x2_mm, y2_mm], "created": len(created),
                "saved": save}

    def create_arc(self, x1_mm: float, y1_mm: float, mid_x_mm: float,
                   mid_y_mm: float, x2_mm: float, y2_mm: float,
                   layer: str = "F.Silkscreen", width_mm: float = 0.15,
                   save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardArc
        board = self._board()
        a = BoardArc()
        a.start = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        a.mid = Vector2.from_xy_mm(float(mid_x_mm), float(mid_y_mm))
        a.end = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        a.layer = self._layer_by_name(board, layer)
        a.width = int(round(float(width_mm) * _NM_PER_MM))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([a])
        self._apply(board, apply, save)
        return {"layer": layer, "start_mm": [x1_mm, y1_mm],
                "mid_mm": [mid_x_mm, mid_y_mm], "end_mm": [x2_mm, y2_mm],
                "created": len(created), "saved": save}

    def create_zone(self, net: str, points_mm: list,
                    layer: str = "F.Cu", name: str = "", priority: int = 0,
                    save: bool = False) -> dict[str, Any]:
        from kipy.geometry import (Vector2, PolyLine, PolyLineNode,
                                   PolygonWithHoles)
        from kipy.board_types import Zone, Net
        if not points_mm or len(points_mm) < 3:
            raise KiCadNotReachableError(
                "a zone outline needs at least 3 points")
        board = self._board()
        pl = PolyLine()
        for pt in points_mm:
            pl.append(PolyLineNode.from_point(
                Vector2.from_xy_mm(float(pt[0]), float(pt[1]))))
        poly = PolygonWithHoles()
        poly.outline = pl
        z = Zone()
        z.outline = poly
        z.layers = [self._layer_by_name(board, layer)]
        if name:
            z.name = name
        z.priority = int(priority)
        if net:
            z.net = Net(name=str(net))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([z])
        self._apply(board, apply, save)
        return {"net": net, "layer": layer, "name": name,
                "point_count": len(points_mm), "created": len(created),
                "saved": save}

    def set_text_variable(self, key: str, value: str,
                          save: bool = False) -> dict[str, Any]:
        """Set (merge) one project text variable."""
        board = self._board()
        proj = board.get_project()
        tv = proj.get_text_variables()
        tv[str(key)] = str(value)
        proj.set_text_variables(tv)  # default merge keeps other variables
        if save:
            board.save()
        return {"key": key, "value": value, "saved": save}

    def run_action(self, action: str) -> dict[str, Any]:
        """Run an arbitrary KiCad tool action by name. Unstable KiCad API."""
        k = self._kicad()
        resp = k.run_action(str(action))
        return {"action": action, "status": str(resp)}

    def save_board(self) -> dict[str, Any]:
        board = self._board()
        board.save()
        return {"saved": True}

    def _pcb_document(self):
        """The first open PCB document specifier, or a clear error."""
        k = self._kicad()
        try:
            from kipy.proto.common.types import DocumentType
            docs = list(k.get_open_documents(DocumentType.DOCTYPE_PCB))
        except Exception as e:
            raise KiCadNotReachableError(
                "could not list open PCB documents (" + str(e) + ")") from None
        if not docs:
            raise KiCadNotReachableError(
                "no PCB document is open in the KiCad PCB editor")
        return docs[0]

    def board_file_path(self) -> str:
        """Absolute path of the open board's ``.kicad_pcb`` file on disk."""
        import os
        doc = self._pcb_document()
        fname = getattr(doc, "board_filename", "") or ""
        proj = getattr(doc, "project", None)
        proj_path = (getattr(proj, "path", "") or "") if proj is not None else ""
        if fname and os.path.isabs(fname):
            return fname
        if fname and proj_path:
            base = proj_path
            # project.path may be the directory or the .kicad_pro file.
            if base.lower().endswith(".kicad_pro") or os.path.isfile(base):
                base = os.path.dirname(base)
            return os.path.join(base, fname)
        raise KiCadNotReachableError(
            "could not resolve the board file path; save the board first")

    def kicad_cli_path(self) -> str:
        """Path to KiCad's bundled ``kicad-cli`` executable."""
        k = self._kicad()
        try:
            path = str(k.get_kicad_binary_path("kicad-cli"))
        except Exception as e:
            raise KiCadNotReachableError(
                "kicad-cli path unavailable (" + str(e) + ")") from None
        return path

    def board_stats(self) -> dict[str, Any]:
        """Object counts and layer count for the open board."""
        board = self._board()

        def _n(getter) -> int:
            return len(list(getter()))

        return {
            "name": _get(lambda: board.name),
            "footprints": _n(board.get_footprints),
            "nets": _n(board.get_nets),
            "pads": _n(board.get_pads),
            "tracks": _n(board.get_tracks),
            "vias": _n(board.get_vias),
            "zones": _n(board.get_zones),
            "stackup_layers": _n(lambda: board.get_stackup().layers),
        }


_bridge: Optional[KiCadBridge] = None


def get_kicad_bridge() -> KiCadBridge:
    """Process-wide KiCad bridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = KiCadBridge()
    return _bridge


def reset_kicad_bridge() -> None:
    global _bridge
    _bridge = None
