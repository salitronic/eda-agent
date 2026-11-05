# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Live connection to a running KiCad through its API server.

KiCad stays open and in control; eda-agent talks to the PCB editor over
KiCad's own socket (``kicad_ipc``), with items read and written as
messages (``kicad_api``). Nothing outside Python's standard library is
needed.

KiCad's side: Preferences > Plugins > Enable KiCad API, and the board
open in the PCB editor. KiCad 10.0 answers board requests only; the
schematic is read and written through its file.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from . import kicad_api as api
from .kicad_ipc import KiCadApiError, KiCadIpc, KiCadNotReachableError

__all__ = ["KiCadBridge", "KiCadNotReachableError", "KiCadApiError",
           "get_kicad_bridge", "reset_kicad_bridge"]

_mm = api.mm


class KiCadBridge:
    """The running KiCad, reconnecting when it has been closed and reopened."""

    def __init__(self) -> None:
        self._ipc: Optional[KiCadIpc] = None

    # -- connection ---------------------------------------------------------
    def ipc(self) -> KiCadIpc:
        if self._ipc is None:
            self._ipc = KiCadIpc()
        try:
            self._ipc.call(api.COMMON + "Ping")
        except KiCadNotReachableError:
            # KiCad may have restarted with a new socket or token: try once
            # more from scratch before giving up.
            self._ipc.close()
            self._ipc = KiCadIpc()
            self._ipc.call(api.COMMON + "Ping")
        return self._ipc

    def board(self) -> api.KiCadBoard:
        return api.open_board(self.ipc())

    def ping(self) -> dict[str, Any]:
        """Connection status plus KiCad's version."""
        version = self.ipc().call(api.COMMON + "GetVersion").msg(1)
        return {"connected": True,
                "kicad_version": version.str(4) if version is not None else "",
                "socket": self.ipc().socket_path}

    # -- reads ----------------------------------------------------------------
    def items(self, *kinds: str) -> list[dict[str, Any]]:
        return [i.read() for i in self.board().items(*kinds)]

    def footprints(self) -> list[dict[str, Any]]:
        """Every footprint on the board."""
        out = []
        for f in self.items("footprint"):
            x, y = f["position_mm"] or (None, None)
            out.append({"reference": f["reference"], "value": f["value"],
                        "footprint_id": f["footprint"], "x_mm": x, "y_mm": y,
                        "rotation_deg": f["rotation_deg"], "layer": f["layer"],
                        "side": f["side"], "locked": f["locked"], "dnp": f["dnp"],
                        "id": f["id"]})
        return out

    def nets(self) -> list[dict[str, Any]]:
        return self.board().nets()

    def component_pins(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """(parts, pins, unconnected-pad-count) for the normalized snapshot."""
        parts: list[dict[str, Any]] = []
        pins: list[dict[str, Any]] = []
        unconnected = 0
        for f in self.items("footprint"):
            parts.append({"refdes": f["reference"], "value": f["value"],
                          "footprint": f["footprint"], "layer": f["layer"],
                          "locked": f["locked"], "dnp": f["dnp"]})
            for p in f["pads"]:
                pins.append({"refdes": f["reference"], "pin": p["number"], "net": p["net"]})
                if not p["net"] and p["pad_type"] != "npth":
                    unconnected += 1
        return parts, pins, unconnected

    def pad_net_counts(self) -> tuple[dict[str, int], int]:
        """(pads-per-net-name, count of pads with no net)."""
        per_net: dict[str, int] = {}
        no_net = 0
        for p in self.items("pad"):
            if p["net"]:
                per_net[p["net"]] = per_net.get(p["net"], 0) + 1
            elif p["pad_type"] != "npth":
                no_net += 1
        return per_net, no_net

    def tracks(self) -> list[dict[str, Any]]:
        """Copper tracks and arcs: net, layer, width, endpoints, length."""
        out = []
        for t in self.items("track", "arc"):
            row = dict(t)
            row["is_arc"] = t["kind"] == "arc"
            if row["is_arc"] and t["start_mm"] and t["mid_mm"] and t["end_mm"]:
                row["length_mm"] = _arc_length(t["start_mm"], t["mid_mm"], t["end_mm"])
            out.append(row)
        return out

    def vias(self) -> list[dict[str, Any]]:
        out = []
        for v in self.items("via"):
            x, y = v["position_mm"] or (None, None)
            out.append({**v, "x_mm": x, "y_mm": y})
        return out

    def zones(self) -> list[dict[str, Any]]:
        return self.items("zone")

    def stackup(self) -> list[dict[str, Any]]:
        return self.board().stackup()

    def layers(self) -> list[str]:
        return self.board().enabled_layers()["layers"]

    def board_outline(self) -> dict[str, Any]:
        """Bounding box (mm) of the board edge, from Edge.Cuts graphics."""
        xs: list[float] = []
        ys: list[float] = []
        count = 0
        for s in self.items("shape"):
            if s["layer"] != "Edge.Cuts":
                continue
            count += 1
            for key in ("start_mm", "end_mm", "mid_mm", "top_left_mm", "bottom_right_mm"):
                if s.get(key):
                    xs.append(s[key][0])
                    ys.append(s[key][1])
            if s.get("shape") == "circle" and s.get("center_mm") and s.get("radius_mm"):
                (cx, cy), r = s["center_mm"], s["radius_mm"]
                xs += [cx - r, cx + r]
                ys += [cy - r, cy + r]
            for poly in s.get("polygons", []):
                for x, y in poly["outline"]:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return {"bbox_mm": None, "edge_shape_count": 0}
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        return {"bbox_mm": {"x": round(x0, 6), "y": round(y0, 6),
                            "w": round(x1 - x0, 6), "h": round(y1 - y0, 6)},
                "edge_shape_count": count}

    def title_block(self) -> dict[str, Any]:
        return self.board().title_block()

    def selection(self) -> list[dict[str, Any]]:
        """Items currently selected in the PCB editor."""
        return [i.read() for i in self.board().selection()]

    def shapes(self) -> list[dict[str, Any]]:
        return self.items("shape")

    def texts(self) -> list[dict[str, Any]]:
        out = []
        for t in self.items("text"):
            x, y = t.get("position_mm") or (None, None)
            out.append({**t, "value": t.get("text", ""), "x_mm": x, "y_mm": y})
        return out

    def groups(self) -> list[dict[str, Any]]:
        return [{**g, "item_count": len(g["items"])} for g in self.items("group")]

    def dimensions(self) -> list[dict[str, Any]]:
        return self.items("dimension")

    def pads(self) -> list[dict[str, Any]]:
        out = []
        for p in self.items("pad"):
            x, y = p["position_mm"] or (None, None)
            out.append({**p, "x_mm": x, "y_mm": y})
        return out

    def open_documents(self) -> list[dict[str, Any]]:
        """Open documents in KiCad: the board and any schematic."""
        ipc = self.ipc()
        out: list[dict[str, Any]] = []
        try:
            board = api.open_board(ipc)
            out.append({"type": "pcb", "filename": board.filename,
                        "project": board.project["name"]})
        except KiCadNotReachableError:  # no PCB editor open; the schematic may be
            pass
        for sch in api.open_schematics(ipc):
            out.append({"type": "schematic", "filename": sch["filename"],
                        "project": sch["project"] or (out[0]["project"] if out else "")})
        return out

    def project_info(self) -> dict[str, Any]:
        """Open project's name, directory and net-class names."""
        board = self.board()
        return {"name": board.project["name"], "path": board.project["path"],
                "net_classes": [nc["name"] for nc in board.net_classes()]}

    def text_variables(self) -> dict[str, Any]:
        return self.board().text_variables()

    def net_classes(self) -> dict[str, Any]:
        """Per-net net-class assignment and each class's rules (mm)."""
        board = self.board()
        classes = {nc["name"]: {k: v for k, v in nc.items()
                                if k not in ("name", "constituents")}
                   for nc in board.net_classes()}
        by_net: dict[str, str] = {}
        for name in classes:
            for item in board.items_in_classes([name], "pad", "track", "via"):
                net_name = item.read().get("net")
                if net_name:
                    by_net.setdefault(net_name, name)
        return {"by_net": by_net, "classes": classes}

    def board_file_path(self) -> str:
        """Absolute path of the open board's ``.kicad_pcb`` file."""
        board = self.board()
        name, path = board.filename, board.project["path"]
        if name and os.path.isabs(name):
            return name
        if name and path:
            base = os.path.dirname(path) if path.lower().endswith(".kicad_pro") else path
            return os.path.join(base, name)
        raise KiCadNotReachableError("could not resolve the board file path; save the board first")

    def sch_file_path(self) -> str:
        """Absolute path of the project's root schematic."""
        root, _ = os.path.splitext(self.board_file_path())
        return root + ".kicad_sch"

    def kicad_cli_path(self) -> str:
        """Path to KiCad's bundled ``kicad-cli``."""
        try:
            path = self.board().binary_path("kicad-cli")
        except KiCadApiError as e:
            raise KiCadNotReachableError(f"kicad-cli path unavailable ({e})") from None
        return path

    def board_stats(self) -> dict[str, Any]:
        """Object counts and layer count for the open board."""
        board = self.board()
        counts: dict[str, int] = {}
        for item in board.items():
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return {"name": board.filename, "footprints": counts.get("footprint", 0),
                "nets": len(board.nets()), "pads": counts.get("pad", 0),
                "tracks": counts.get("track", 0) + counts.get("arc", 0),
                "vias": counts.get("via", 0), "zones": counts.get("zone", 0),
                "stackup_layers": len(board.stackup()),
                "copper_layers": board.enabled_layers()["copper_layer_count"]}

    # -- writes -----------------------------------------------------------------
    # Each groups its change in a commit (one undo step); save=True also
    # writes the board file.
    def _footprint(self, board: api.KiCadBoard, reference: str) -> api.Item:
        for f in board.items("footprint"):
            if f.read()["reference"] == reference:
                return f
        raise ValueError(f"no footprint with reference '{reference}'")

    def _change_footprint(self, reference: str, changes: dict[str, Any],
                          save: bool) -> dict[str, Any]:
        board = self.board()
        f = self._footprint(board, reference)
        with board.commit(f"eda-agent: {reference}"):
            result = board.update([api.patch(f, changes, board)])[0]
        if not result["ok"]:
            raise ValueError(f"{reference}: {result['status']} {result.get('error', '')}".strip())
        if save:
            board.save()
        return result["item"]

    def move_component(self, reference: str, x_mm: float, y_mm: float,
                       save: bool = False) -> dict[str, Any]:
        item = self._change_footprint(reference, {"position_mm": (float(x_mm), float(y_mm))}, save)
        return {"reference": reference, "x_mm": item["position_mm"][0],
                "y_mm": item["position_mm"][1], "saved": save}

    def rotate_component(self, reference: str, degrees: float,
                         save: bool = False) -> dict[str, Any]:
        item = self._change_footprint(reference, {"rotation_deg": float(degrees)}, save)
        return {"reference": reference, "orientation_deg": item["rotation_deg"], "saved": save}

    def set_component_side(self, reference: str, side: str,
                           save: bool = False) -> dict[str, Any]:
        board = self.board()
        f = self._footprint(board, reference)
        want_bottom = str(side).strip().lower() in ("bottom", "b", "back", "b.cu")
        if (f.read()["side"] == "bottom") != want_bottom:
            status = board.flip([f.id])
            if status != "ok":
                raise ValueError(f"{reference}: KiCad's flip answered {status}")
        if save:
            board.save()
        now = board.items_by_id([f.id])[0].read()
        return {"reference": reference, "side": now["side"], "saved": save}

    def set_component_locked(self, reference: str, locked: bool,
                             save: bool = False) -> dict[str, Any]:
        item = self._change_footprint(reference, {"locked": bool(locked)}, save)
        return {"reference": reference, "locked": item["locked"], "saved": save}

    def set_component_value(self, reference: str, value: str,
                            save: bool = False) -> dict[str, Any]:
        item = self._change_footprint(reference, {"value": str(value)}, save)
        return {"reference": reference, "value": item["value"], "saved": save}

    def delete_component(self, reference: str, save: bool = False) -> dict[str, Any]:
        board = self.board()
        f = self._footprint(board, reference)
        result = board.delete([f.id])[0]
        if not result["ok"]:
            raise ValueError(f"{reference}: {result['status']}")
        if save:
            board.save()
        return {"reference": reference, "deleted": True, "saved": save}

    def _create(self, board: api.KiCadBoard, item: api.Item, save: bool) -> dict[str, Any]:
        with board.commit("eda-agent: create"):
            result = board.create([item])[0]
        if not result["ok"]:
            raise ValueError(f"KiCad refused the {item.kind}: {result['status']} "
                             f"{result.get('error', '')}".strip())
        if save:
            board.save()
        return result["item"]

    def create_track(self, net: str, layer: str, x1_mm: float, y1_mm: float,
                     x2_mm: float, y2_mm: float, width_mm: float,
                     save: bool = False) -> dict[str, Any]:
        board = self.board()
        item = self._create(board, api.make_track((x1_mm, y1_mm), (x2_mm, y2_mm), width_mm,
                                                  board.layer(layer), net or ""), save)
        return {"net": item["net"], "layer": item["layer"], "width_mm": item["width_mm"],
                "start_mm": item["start_mm"], "end_mm": item["end_mm"], "id": item["id"],
                "created": 1, "saved": save}

    def create_via(self, net: str, x_mm: float, y_mm: float, diameter_mm: float,
                   drill_mm: float, save: bool = False) -> dict[str, Any]:
        board = self.board()
        item = self._create(board, api.make_via((x_mm, y_mm), diameter_mm, drill_mm, net or ""), save)
        return {"net": item["net"], "x_mm": item["position_mm"][0], "y_mm": item["position_mm"][1],
                "diameter_mm": item["diameter_mm"], "drill_mm": item["drill_mm"],
                "id": item["id"], "created": 1, "saved": save}

    def create_text(self, text: str, x_mm: float, y_mm: float,
                    layer: str = "F.Silkscreen", save: bool = False) -> dict[str, Any]:
        board = self.board()
        item = self._create(board, api.make_text(text, (x_mm, y_mm), board.layer(layer)), save)
        return {"text": item["text"], "x_mm": x_mm, "y_mm": y_mm, "layer": item["layer"],
                "id": item["id"], "created": 1, "saved": save}

    def create_line(self, x1_mm: float, y1_mm: float, x2_mm: float, y2_mm: float,
                    layer: str = "F.Silkscreen", width_mm: float = 0.15,
                    save: bool = False) -> dict[str, Any]:
        board = self.board()
        item = self._create(board, api.make_shape(board.layer(layer), width_mm,
                                                  segment=((x1_mm, y1_mm), (x2_mm, y2_mm))), save)
        return {"layer": item["layer"], "width_mm": item["width_mm"], "start_mm": item["start_mm"],
                "end_mm": item["end_mm"], "id": item["id"], "created": 1, "saved": save}

    def create_circle(self, cx_mm: float, cy_mm: float, radius_mm: float,
                      layer: str = "F.Silkscreen", width_mm: float = 0.15,
                      save: bool = False) -> dict[str, Any]:
        board = self.board()
        item = self._create(board, api.make_shape(board.layer(layer), width_mm,
                                                  circle=((cx_mm, cy_mm), radius_mm)), save)
        return {"layer": item["layer"], "center_mm": item["center_mm"],
                "radius_mm": item.get("radius_mm"), "id": item["id"], "created": 1, "saved": save}

    def create_rectangle(self, x1_mm: float, y1_mm: float, x2_mm: float, y2_mm: float,
                         layer: str = "F.Silkscreen", width_mm: float = 0.15,
                         save: bool = False) -> dict[str, Any]:
        board = self.board()
        item = self._create(board, api.make_shape(board.layer(layer), width_mm,
                                                  rectangle=((x1_mm, y1_mm), (x2_mm, y2_mm))), save)
        return {"layer": item["layer"], "top_left_mm": item["top_left_mm"],
                "bottom_right_mm": item["bottom_right_mm"], "id": item["id"],
                "created": 1, "saved": save}

    def create_arc(self, x1_mm: float, y1_mm: float, mid_x_mm: float, mid_y_mm: float,
                   x2_mm: float, y2_mm: float, layer: str = "F.Silkscreen",
                   width_mm: float = 0.15, save: bool = False) -> dict[str, Any]:
        board = self.board()
        item = self._create(board, api.make_shape(
            board.layer(layer), width_mm,
            arc=((x1_mm, y1_mm), (mid_x_mm, mid_y_mm), (x2_mm, y2_mm))), save)
        return {"layer": item["layer"], "start_mm": item["start_mm"], "mid_mm": item["mid_mm"],
                "end_mm": item["end_mm"], "id": item["id"], "created": 1, "saved": save}

    def create_zone(self, net: str, points_mm: list, layer: str = "F.Cu", name: str = "",
                    priority: int = 0, save: bool = False) -> dict[str, Any]:
        if not points_mm or len(points_mm) < 3:
            raise ValueError("a zone outline needs at least 3 points")
        board = self.board()
        item = self._create(board, api.make_zone([tuple(p) for p in points_mm], [board.layer(layer)],
                                                 net_name=net or "", name=name,
                                                 priority=int(priority)), save)
        return {"net": item["net"], "layer": layer, "name": item["name"],
                "point_count": len(points_mm), "id": item["id"], "created": 1, "saved": save}

    def set_text_variable(self, key: str, value: str, save: bool = False) -> dict[str, Any]:
        """Set (merge) one project text variable."""
        board = self.board()
        board.set_text_variables({str(key): str(value)})
        if save:
            board.save()
        return {"key": key, "value": value, "saved": save}

    def run_action(self, action: str) -> dict[str, Any]:
        """Run a KiCad tool action by name in the PCB editor."""
        return {"action": action, "status": self.board().run_action(str(action))}

    def save_board(self) -> dict[str, Any]:
        self.board().save()
        return {"saved": True}


def _arc_length(start, mid, end) -> Optional[float]:
    """Length of the circular arc through three points, in mm."""
    import math
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return round(math.dist(start, end), 6)
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1)
          + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3)
          + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.dist((ux, uy), start)
    a1 = math.atan2(y1 - uy, x1 - ux)
    a2 = math.atan2(y2 - uy, x2 - ux)
    a3 = math.atan2(y3 - uy, x3 - ux)
    sweep = (a3 - a1) % (2 * math.pi)
    if (a2 - a1) % (2 * math.pi) > sweep:  # the mid point lies the other way round
        sweep = 2 * math.pi - sweep
    return round(r * sweep, 6)


_bridge: Optional[KiCadBridge] = None


def get_kicad_bridge() -> KiCadBridge:
    """Process-wide KiCad bridge."""
    global _bridge
    if _bridge is None:
        _bridge = KiCadBridge()
    return _bridge


def reset_kicad_bridge() -> None:
    global _bridge
    _bridge = None
