# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""KiCad's board API in plain Python: items as dicts, changes as commits.

Reads give every board item as a dict in millimetres (``read_item``),
with its KiCad id under "id". Writes build or patch the item messages
and send them inside a commit, so each call is one undo step in the PCB
editor. An update starts from the item exactly as KiCad sent it and
changes only the fields asked for; the rest go back untouched.

KiCad 10.0 answers board requests only. Its schematic editor answers no
item request, so schematic work goes through the files.
"""
from __future__ import annotations

import contextlib
import math
from typing import Any, Iterable, Iterator, Optional

from .kicad_ipc import KiCadApiError, KiCadIpc, KiCadNotReachableError
from .kicad_wire import Message

NM_PER_MM = 1_000_000
NM_PER_MIL = 25_400

COMMON = "kiapi.common.commands."
BOARD_CMD = "kiapi.board.commands."
BOARD_TYPES = "kiapi.board.types."

# Document types.
DOC_SCHEMATIC, DOC_PCB = 1, 3

# Item kinds: the name KiCad packs an item under, and the number a
# request uses to ask for that kind.
KINDS: dict[str, tuple[str, int]] = {
    "footprint": ("FootprintInstance", 1),
    "pad": ("Pad", 2),
    "shape": ("BoardGraphicShape", 3),
    "text": ("BoardText", 7),
    "textbox": ("BoardTextBox", 8),
    "track": ("Track", 11),
    "via": ("Via", 12),
    "arc": ("Arc", 13),
    "dimension": ("Dimension", 15),
    "zone": ("Zone", 16),
    "group": ("Group", 17),
    "barcode": ("Barcode", 52),
}
KIND_OF_TYPE = {type_name: kind for kind, (type_name, _) in KINDS.items()}

# -- layers -----------------------------------------------------------------
# Layer numbers as the API counts them, with KiCad's file names.
_LAYER_FILE_NAMES = {
    3: "F.Cu", 34: "B.Cu", 35: "B.Adhes", 36: "F.Adhes", 37: "B.Paste",
    38: "F.Paste", 39: "B.SilkS", 40: "F.SilkS", 41: "B.Mask", 42: "F.Mask",
    43: "Dwgs.User", 44: "Cmts.User", 45: "Eco1.User", 46: "Eco2.User",
    47: "Edge.Cuts", 48: "Margin", 49: "B.CrtYd", 50: "F.CrtYd",
    51: "B.Fab", 52: "F.Fab", 62: "Rescue",
}
for _i in range(1, 31):
    _LAYER_FILE_NAMES[3 + _i] = f"In{_i}.Cu"
for _i in range(1, 10):
    _LAYER_FILE_NAMES[52 + _i] = f"User.{_i}"
for _i in range(10, 46):
    _LAYER_FILE_NAMES[53 + _i] = f"User.{_i}"

# The names the editor shows by default, which people also type.
_LAYER_ALIASES = {
    "F.Silkscreen": 40, "B.Silkscreen": 39, "F.Adhesive": 36,
    "B.Adhesive": 35, "F.Courtyard": 50, "B.Courtyard": 49,
    "User.Drawings": 43, "User.Comments": 44, "User.Eco1": 45,
    "User.Eco2": 46, "Top": 3, "Bottom": 34, "TopLayer": 3,
    "BottomLayer": 34,
}
LAYER_NUMBERS = {name: num for num, name in _LAYER_FILE_NAMES.items()}
LAYER_NUMBERS.update(_LAYER_ALIASES)
COPPER = set(range(3, 35))


def layer_name(num: int) -> str:
    return _LAYER_FILE_NAMES.get(int(num), str(num))


def layer_number(name: Any, board_names: Optional[dict[str, int]] = None) -> int:
    """A layer given by name (file, display or the board's own) or number."""
    if isinstance(name, int):
        return name
    key = str(name).strip()
    if board_names and key in board_names:
        return board_names[key]
    if key in LAYER_NUMBERS:
        return LAYER_NUMBERS[key]
    lowered = {k.lower(): v for k, v in LAYER_NUMBERS.items()}
    if key.lower() in lowered:
        return lowered[key.lower()]
    raise ValueError(f"no layer named '{key}'")


# -- small value messages ------------------------------------------------------
def mm(nm: int) -> float:
    return round(nm / NM_PER_MM, 6)


def to_nm(value_mm: float) -> int:
    return int(round(float(value_mm) * NM_PER_MM))


def vec(x_mm: float, y_mm: float) -> Message:
    return Message().set_int(1, to_nm(x_mm)).set_int(2, to_nm(y_mm))


def read_vec(m: Optional[Message]) -> Optional[list[float]]:
    return None if m is None else [mm(m.sint(1)), mm(m.sint(2))]


def dist(value_mm: float) -> Message:
    return Message().set_int(1, to_nm(value_mm))


def read_dist(m: Optional[Message]) -> Optional[float]:
    return None if m is None else mm(m.sint(1))


def angle(degrees: float) -> Message:
    return Message().set_double(1, degrees)


def read_angle(m: Optional[Message]) -> float:
    return 0.0 if m is None else m.double(1)


def kiid(value: str) -> Message:
    return Message().set_str(1, value)


def net(name: str) -> Message:
    return Message().set_str(2, name)


def read_net(m: Optional[Message]) -> str:
    return "" if m is None else m.str(2)


def locked_state(locked: bool) -> int:
    return 2 if locked else 1


def read_locked(value: int) -> Optional[bool]:
    return {1: False, 2: True}.get(value)


def polyline(points_mm: Iterable[Iterable[float]], closed: bool = True) -> Message:
    nodes = [Message().set_msg(1, vec(p[0], p[1])) for p in points_mm]
    return Message().set_msgs(1, nodes).set_bool(2, closed)


def read_polyline(m: Message) -> list[list[float]]:
    points = []
    for node in m.msgs(1):
        point = node.msg(1)
        if point is not None:
            points.append(read_vec(point))
        else:  # an arc segment: keep its three points
            arc = node.msg(2)
            if arc is not None:
                points.extend(read_vec(arc.msg(i)) for i in (1, 2, 3))
    return points


def polyset(outlines: Iterable[Iterable[Iterable[float]]]) -> Message:
    return Message().set_msgs(1, [Message().set_msg(1, polyline(o))
                                  for o in outlines])


def read_polyset(m: Optional[Message]) -> list[dict[str, Any]]:
    if m is None:
        return []
    return [{"outline": read_polyline(p.msg(1) or Message()),
             "holes": [read_polyline(h) for h in p.msgs(2)]} for p in m.msgs(1)]


def read_text(m: Optional[Message]) -> dict[str, Any]:
    """A Text message: the string, its position and its size."""
    if m is None:
        return {"text": ""}
    attrs = m.msg(3) or Message()
    size = read_vec(attrs.msg(14))
    return {"text": m.str(5), "position_mm": read_vec(m.msg(2)),
            "size_mm": size, "angle_deg": read_angle(attrs.msg(4)),
            "visible": attrs.bool(10) if attrs.has(10) else True,
            "mirrored": attrs.bool(11)}


# -- reading items ------------------------------------------------------------
def read_item(type_name: str, m: Message) -> dict[str, Any]:
    """One board item as a dict, distances in mm, angles in degrees."""
    kind = KIND_OF_TYPE.get(type_name, type_name)
    out: dict[str, Any] = {"kind": kind}
    if kind == "track":
        out.update(id=_id(m, 1), start_mm=read_vec(m.msg(2)), end_mm=read_vec(m.msg(3)),
                   width_mm=read_dist(m.msg(4)), locked=read_locked(m.int(5)),
                   layer=layer_name(m.int(6)), net=read_net(m.msg(7)))
        s, e = out["start_mm"], out["end_mm"]
        out["length_mm"] = round(math.dist(s, e), 6) if s and e else None
    elif kind == "arc":
        out.update(id=_id(m, 1), start_mm=read_vec(m.msg(2)), mid_mm=read_vec(m.msg(3)),
                   end_mm=read_vec(m.msg(4)), width_mm=read_dist(m.msg(5)),
                   locked=read_locked(m.int(6)), layer=layer_name(m.int(7)),
                   net=read_net(m.msg(8)))
    elif kind == "via":
        stack = m.msg(3) or Message()
        drill = stack.msg(3) or Message()
        layers = stack.msgs(5)
        size = read_vec(layers[0].msg(3)) if layers else None
        out.update(id=_id(m, 1), position_mm=read_vec(m.msg(2)),
                   locked=read_locked(m.int(4)), net=read_net(m.msg(5)),
                   via_type={1: "through", 2: "blind_buried", 3: "micro",
                             4: "blind", 5: "buried"}.get(m.int(6), "unknown"),
                   diameter_mm=size[0] if size else None,
                   drill_mm=(read_vec(drill.msg(3)) or [None])[0],
                   start_layer=layer_name(drill.int(1)) if drill.has(1) else "F.Cu",
                   end_layer=layer_name(drill.int(2)) if drill.has(2) else "B.Cu")
        modes = {1: True, 2: False, 3: "from_rules"}
        front, back = stack.msg(7), stack.msg(8)
        if front is not None or back is not None:
            out["tented"] = {"front": modes.get((front or Message()).int(6), "unknown"),
                             "back": modes.get((back or Message()).int(6), "unknown")}
    elif kind == "pad":
        stack = m.msg(6) or Message()
        layers = stack.msgs(5)
        first = layers[0] if layers else Message()
        drill = stack.msg(3)
        out.update(id=_id(m, 1), locked=read_locked(m.int(2)), number=m.str(3),
                   net=read_net(m.msg(4)),
                   pad_type={1: "pth", 2: "smd", 3: "edge_connector", 4: "npth"}.get(m.int(5), "unknown"),
                   position_mm=read_vec(m.msg(7)),
                   shape={1: "circle", 2: "rect", 3: "oval", 4: "trapezoid",
                          5: "roundrect", 6: "chamfered_rect", 7: "custom"}.get(first.int(2), "unknown"),
                   size_mm=read_vec(first.msg(3)),
                   angle_deg=read_angle(stack.msg(6)),
                   drill_mm=read_vec(drill.msg(3)) if drill is not None else None,
                   layers=[layer_name(n) for n in stack.ints(2)])
        pin = m.msg(10)
        if pin is not None:
            out["pin_name"] = pin.str(1)
    elif kind == "shape":
        shape = m.msg(1) or Message()
        stroke = (shape.msg(3) or Message()).msg(1) or Message()
        fill = (shape.msg(3) or Message()).msg(2) or Message()
        out.update(id=_id(m, 4), layer=layer_name(m.int(2)), net=read_net(m.msg(3)),
                   locked=read_locked(m.int(5)), width_mm=read_dist(stroke.msg(1)),
                   filled=fill.int(1) == 2)
        for num, name, points in ((4, "segment", ("start", "end")),
                                  (5, "rectangle", ("top_left", "bottom_right")),
                                  (6, "arc", ("start", "mid", "end")),
                                  (7, "circle", ("center", "radius_point")),
                                  (9, "bezier", ("start", "control1", "control2", "end"))):
            geom = shape.msg(num)
            if geom is not None:
                out["shape"] = name
                for i, p in enumerate(points, start=1):
                    out[p + "_mm"] = read_vec(geom.msg(i))
                if name == "circle" and out["center_mm"] and out["radius_point_mm"]:
                    out["radius_mm"] = round(math.dist(out["center_mm"], out["radius_point_mm"]), 6)
        if shape.has(8):
            out["shape"] = "polygon"
            out["polygons"] = read_polyset(shape.msg(8))
    elif kind == "text":
        out.update(id=_id(m, 1), layer=layer_name(m.int(3)), locked=read_locked(m.int(5)),
                   knockout=m.bool(4), **read_text(m.msg(2)))
    elif kind == "textbox":
        box = m.msg(2) or Message()
        out.update(id=_id(m, 1), layer=layer_name(m.int(3)), locked=read_locked(m.int(4)),
                   text=box.str(6), top_left_mm=read_vec(box.msg(2)),
                   bottom_right_mm=read_vec(box.msg(3)))
    elif kind == "zone":
        copper = m.msg(6)
        rule = m.msg(7)
        out.update(id=_id(m, 1),
                   zone_type={1: "copper", 2: "graphical", 3: "rule_area", 4: "teardrop"}.get(m.int(2), "unknown"),
                   layers=[layer_name(n) for n in m.ints(3)],
                   outline=read_polyset(m.msg(4)), name=m.str(5),
                   priority=m.int(8), filled=m.bool(9), locked=read_locked(m.int(12)),
                   net=read_net(copper.msg(8)) if copper is not None else "")
        if copper is not None:
            conn = copper.msg(1) or Message()
            out.update(clearance_mm=read_dist(copper.msg(2)),
                       min_width_mm=read_dist(copper.msg(3)),
                       connection={1: "inherited", 2: "none", 3: "thermal", 4: "solid",
                                   5: "pth_thermal"}.get(conn.int(1), "unknown"),
                       fill_mode={1: "solid", 2: "hatched"}.get(copper.int(6), "unknown"))
        if rule is not None:
            out["keepout"] = {k: rule.bool(n) for n, k in (
                (1, "copper"), (2, "vias"), (3, "tracks"), (4, "pads"), (5, "footprints"))}
    elif kind == "footprint":
        definition = m.msg(6) or Message()
        lib = definition.msg(1) or Message()
        attrs = m.msg(11) or Message()
        out.update(id=_id(m, 1), position_mm=read_vec(m.msg(2)),
                   rotation_deg=read_angle(m.msg(3)),
                   layer=layer_name(m.int(4)), side="bottom" if m.int(4) == 34 else "top",
                   locked=read_locked(m.int(5)),
                   reference=_field_text(m.msg(7)), value=_field_text(m.msg(8)),
                   datasheet=_field_text(m.msg(9)), description=_field_text(m.msg(10)),
                   footprint=(lib.str(1) + ":" + lib.str(2)) if lib.str(1) else lib.str(2),
                   dnp=attrs.bool(7), exclude_from_bom=attrs.bool(5),
                   exclude_from_pos=attrs.bool(4),
                   mounting={1: "through_hole", 2: "smd", 3: "unspecified"}.get(attrs.int(8), "unknown"),
                   sheet=m.str(14))
        out["pads"] = [read_item("Pad", Message.decode(a.bytes(2)))
                       for a in definition.msgs(11)
                       if a.str(1).endswith(".Pad")]
    elif kind == "dimension":
        out.update(id=_id(m, 1), locked=read_locked(m.int(2)), layer=layer_name(m.int(3)),
                   text=(read_text(m.msg(4)) or {}).get("text", ""),
                   override_text=m.str(11) if m.bool(10) else "")
        for num, style, points in ((5, "aligned", ("start", "end")),
                                   (6, "orthogonal", ("start", "end")),
                                   (7, "radial", ("center", "radius_point")),
                                   (8, "leader", ("start", "end")),
                                   (9, "center", ("center", "end"))):
            geom = m.msg(num)
            if geom is not None:
                out["style"] = style
                for i, p in enumerate(points, start=1):
                    out[p + "_mm"] = read_vec(geom.msg(i))
                if num in (5, 6):
                    out["height_mm"] = read_dist(geom.msg(3))
    elif kind == "group":
        out.update(id=_id(m, 1), name=m.str(2), items=[i.str(1) for i in m.msgs(3)])
    elif kind == "barcode":
        out.update(id=_id(m, 1), text=m.str(2), position_mm=read_vec(m.msg(5)),
                   layer=layer_name(m.int(7)))
    return out


def _id(m: Message, num: int) -> str:
    sub = m.msg(num)
    return sub.str(1) if sub is not None else ""


def _field_text(field: Optional[Message]) -> str:
    if field is None:
        return ""
    board_text = field.msg(3) or Message()
    return (board_text.msg(2) or Message()).str(5)


# -- the board ----------------------------------------------------------------
class Item:
    """A board item as KiCad sent it: its packed type and its message."""

    __slots__ = ("type_name", "message")

    def __init__(self, type_name: str, message: Message):
        self.type_name, self.message = type_name, message

    @property
    def kind(self) -> str:
        return KIND_OF_TYPE.get(self.type_name, self.type_name)

    @property
    def id(self) -> str:
        return _id(self.message, 4 if self.kind == "shape" else 1)

    def read(self) -> dict[str, Any]:
        return read_item(self.type_name, self.message)

    def packed(self) -> Message:
        return Message().set_str(1, "type.googleapis.com/" + BOARD_TYPES + self.type_name) \
            .set_bytes(2, self.message.encode())


def _unpack(packed: Message) -> Item:
    type_url = packed.str(1)
    return Item(type_url.rsplit(".", 1)[-1], Message.decode(packed.bytes(2)))


class KiCadBoard:
    """The board open in KiCad's PCB editor."""

    def __init__(self, ipc: KiCadIpc, document: Message):
        self.ipc, self.document = ipc, document
        self._layer_names: Optional[dict[str, int]] = None
        self._open_commit = False
        self._deleting: list[dict[str, Any]] = []

    @property
    def filename(self) -> str:
        return self.document.str(4)

    @property
    def project(self) -> dict[str, str]:
        p = self.document.msg(5) or Message()
        return {"name": p.str(1), "path": p.str(2)}

    def _header(self) -> Message:
        return Message().set_msg(1, self.document)

    def call(self, command: str, body: Optional[Message] = None) -> Message:
        return self.ipc.call(command, body)

    # -- reading ------------------------------------------------------------
    def items(self, *kinds: str) -> list[Item]:
        numbers = [KINDS[k][1] for k in kinds] if kinds else [n for _, n in KINDS.values()]
        reply = self.call(COMMON + "GetItems",
                          Message().set_msg(1, self._header()).set_ints(2, numbers))
        return [_unpack(p) for p in reply.msgs(3)]

    def items_by_id(self, ids: Iterable[str]) -> list[Item]:
        """The items with these ids that exist; missing ones are left out."""
        body = Message().set_msg(1, self._header()).set_msgs(2, [kiid(i) for i in ids])
        try:
            reply = self.call(COMMON + "GetItemsById", body)
        except KiCadApiError as e:
            if e.status == 3 and "none of the requested" in str(e):
                return []
            raise
        return [_unpack(p) for p in reply.msgs(3)]

    def items_on_nets(self, nets: Iterable[str], *kinds: str) -> list[Item]:
        body = Message().set_msg(1, self._header()) \
            .set_ints(2, [KINDS[k][1] for k in kinds]) \
            .set_msgs(4, [net(n) for n in nets])
        return [_unpack(p) for p in self.call(BOARD_CMD + "GetItemsByNet", body).msgs(3)]

    def items_in_classes(self, classes: Iterable[str], *kinds: str) -> list[Item]:
        body = Message().set_msg(1, self._header()) \
            .set_ints(2, [KINDS[k][1] for k in kinds])
        for c in classes:
            body.fields.append([3, 2, str(c).encode("utf-8")])
        return [_unpack(p) for p in self.call(BOARD_CMD + "GetItemsByNetClass", body).msgs(3)]

    def connected(self, ids: Iterable[str], *kinds: str) -> list[Item]:
        body = Message().set_msg(1, self._header()).set_msgs(2, [kiid(i) for i in ids]) \
            .set_ints(3, [KINDS[k][1] for k in kinds])
        return [_unpack(p) for p in self.call(BOARD_CMD + "GetConnectedItems", body).msgs(3)]

    def selection(self, *kinds: str) -> list[Item]:
        body = Message().set_msg(1, self._header()).set_ints(2, [KINDS[k][1] for k in kinds])
        return [_unpack(p) for p in self.call(COMMON + "GetSelection", body).msgs(1)]

    def nets(self, classes: Iterable[str] = ()) -> list[dict[str, Any]]:
        body = Message().set_msg(1, self.document)
        for c in classes:
            body.fields.append([2, 2, str(c).encode("utf-8")])
        return [{"name": n.str(2), "code": (n.msg(1) or Message()).sint(1)}
                for n in self.call(BOARD_CMD + "GetNets", body).msgs(1)]

    def as_text(self) -> str:
        """The whole board in KiCad's file format, as it stands in the editor."""
        reply = self.call(COMMON + "SaveDocumentToString", Message().set_msg(1, self.document))
        return reply.str(2)

    def bounding_boxes(self, ids: Iterable[str], with_text: bool = False) -> dict[str, Any]:
        body = Message().set_msg(1, self._header()).set_msgs(2, [kiid(i) for i in ids]) \
            .set_int(3, 2 if with_text else 1)
        reply = self.call(COMMON + "GetBoundingBox", body)
        out = {}
        for item_id, box in zip(reply.msgs(1), reply.msgs(2)):
            pos, size = read_vec(box.msg(1)), read_vec(box.msg(2))
            out[item_id.str(1)] = {"x_mm": pos[0], "y_mm": pos[1],
                                   "w_mm": size[0], "h_mm": size[1]}
        return out

    def hit_test(self, item_id: str, x_mm: float, y_mm: float, tolerance_mm: float = 0) -> bool:
        body = Message().set_msg(1, self._header()).set_msg(2, kiid(item_id)) \
            .set_msg(3, vec(x_mm, y_mm)).set_int(4, to_nm(tolerance_mm))
        return self.call(COMMON + "HitTest", body).int(1) == 2

    def pad_polygons(self, pad_ids: Iterable[str], layer: Any) -> dict[str, Any]:
        body = Message().set_msg(1, self.document).set_msgs(2, [kiid(i) for i in pad_ids]) \
            .set_int(3, self.layer(layer))
        reply = self.call(BOARD_CMD + "GetPadShapeAsPolygon", body)
        return {i.str(1): {"outline": read_polyline(p.msg(1) or Message()),
                           "holes": [read_polyline(h) for h in p.msgs(2)]}
                for i, p in zip(reply.msgs(1), reply.msgs(2))}

    def layer_names(self) -> dict[str, int]:
        """The board's own layer names (which a user may have renamed)."""
        if self._layer_names is None:
            names = {}
            for num in self.enabled_layers()["layers"]:
                n = layer_number(num)
                body = Message().set_msg(1, self.document).set_int(2, n)
                names[self.call(BOARD_CMD + "GetBoardLayerName", body).str(1)] = n
            self._layer_names = names
        return self._layer_names

    def layer(self, name: Any) -> int:
        try:
            return layer_number(name)
        except ValueError:
            return layer_number(name, self.layer_names())

    def enabled_layers(self) -> dict[str, Any]:
        reply = self.call(BOARD_CMD + "GetBoardEnabledLayers", Message().set_msg(1, self.document))
        return {"copper_layer_count": reply.int(1),
                "layers": [layer_name(n) for n in reply.ints(2)]}

    def set_enabled_layers(self, copper_count: int, layers: Iterable[Any]) -> dict[str, Any]:
        body = Message().set_msg(1, self.document).set_int(2, int(copper_count)) \
            .set_ints(3, [self.layer(l) for l in layers])
        reply = self.call(BOARD_CMD + "SetBoardEnabledLayers", body)
        self._layer_names = None
        return {"copper_layer_count": reply.int(1),
                "layers": [layer_name(n) for n in reply.ints(2)]}

    def stackup(self) -> list[dict[str, Any]]:
        reply = self.call(BOARD_CMD + "GetBoardStackup", Message().set_msg(1, self.document))
        stack = reply.msg(1) or Message()
        out = []
        for layer in stack.msgs(4):
            row = {"layer": layer_name(layer.int(2)) if layer.int(2) > 2 else "",
                   "thickness_mm": read_dist(layer.msg(1)),
                   "type": {1: "copper", 2: "dielectric", 3: "silkscreen", 4: "soldermask",
                            5: "solderpaste", 7: "undefined"}.get(layer.int(4), "unknown"),
                   "material": layer.str(7), "name": layer.str(8)}
            diel = layer.msg(5)
            if diel is not None:
                props = diel.msgs(1)
                if props:
                    p = props[0]
                    row.update(epsilon_r=p.double(1), loss_tangent=p.double(2),
                               material=p.str(3) or row["material"],
                               thickness_mm=read_dist(p.msg(4)) or row["thickness_mm"])
            out.append(row)
        return out

    def title_block(self) -> dict[str, str]:
        reply = self.call(COMMON + "GetTitleBlockInfo", Message().set_msg(1, self.document))
        keys = ["title", "date", "revision", "company"] + [f"comment{i}" for i in range(1, 10)]
        return {k: reply.str(i) for i, k in enumerate(keys, start=1)}

    def set_title_block(self, values: dict[str, str]) -> dict[str, str]:
        keys = ["title", "date", "revision", "company"] + [f"comment{i}" for i in range(1, 10)]
        current = self.title_block()
        current.update({k: str(v) for k, v in values.items() if k in keys})
        block = Message()
        for i, k in enumerate(keys, start=1):
            if current.get(k):
                block.set_str(i, current[k])
        self.call(COMMON + "SetTitleBlockInfo",
                  Message().set_msg(1, self.document).set_msg(2, block))
        return current

    def visible_layers(self) -> list[str]:
        reply = self.call(BOARD_CMD + "GetVisibleLayers", Message().set_msg(1, self.document))
        return [layer_name(n) for n in reply.ints(1)]

    def set_visible_layers(self, layers: Iterable[Any]) -> None:
        self.call(BOARD_CMD + "SetVisibleLayers", Message().set_msg(1, self.document)
                  .set_ints(2, [self.layer(l) for l in layers]))

    def active_layer(self) -> str:
        reply = self.call(BOARD_CMD + "GetActiveLayer", Message().set_msg(1, self.document))
        return layer_name(reply.int(1))

    def set_active_layer(self, layer: Any) -> None:
        self.call(BOARD_CMD + "SetActiveLayer",
                  Message().set_msg(1, self.document).set_int(2, self.layer(layer)))

    def set_origin(self, x_mm: float, y_mm: float, drill: bool = False) -> None:
        self.call(BOARD_CMD + "SetBoardOrigin", Message().set_msg(1, self.document)
                  .set_int(2, 2 if drill else 1).set_msg(3, vec(x_mm, y_mm)))

    def origin(self, drill: bool = False) -> list[float]:
        reply = self.call(BOARD_CMD + "GetBoardOrigin", Message().set_msg(1, self.document)
                          .set_int(2, 2 if drill else 1))
        return read_vec(reply) or [0.0, 0.0]

    _APPEARANCE = {
        "inactive_layers": (1, {"normal": 1, "dimmed": 2, "hidden": 3}),
        "net_colors": (2, {"all": 1, "ratsnest": 2, "off": 3}),
        "board_flip": (3, {"normal": 1, "flipped_x": 2}),
        "ratsnest": (4, {"all_layers": 1, "visible_layers": 2}),
    }

    def appearance(self) -> dict[str, str]:
        reply = self.call(BOARD_CMD + "GetBoardEditorAppearanceSettings")
        out = {}
        for key, (num, names) in self._APPEARANCE.items():
            back = {v: k for k, v in names.items()}
            out[key] = back.get(reply.int(num), "unknown")
        return out

    def set_appearance(self, values: dict[str, str]) -> dict[str, str]:
        current = self.call(BOARD_CMD + "GetBoardEditorAppearanceSettings").copy()
        for key, value in values.items():
            if key not in self._APPEARANCE:
                raise ValueError(f"no appearance setting {key}; use {sorted(self._APPEARANCE)}")
            num, names = self._APPEARANCE[key]
            if value not in names:
                raise ValueError(f"{key} must be one of {sorted(names)}")
            current.set_int(num, names[value])
        self.call(BOARD_CMD + "SetBoardEditorAppearanceSettings", Message().set_msg(1, current))
        return self.appearance()

    def expand_text(self, texts: Iterable[str]) -> list[str]:
        body = Message().set_msg(1, self.document)
        for t in texts:
            body.fields.append([2, 2, str(t).encode("utf-8")])
        reply = self.call(COMMON + "ExpandTextVariables", body)
        return [v.decode("utf-8") for v in reply.raw(1)]

    # -- project ------------------------------------------------------------
    def project_document(self) -> Message:
        """The project this board belongs to, as the project calls name it."""
        project = self.document.msg(5) or Message()
        return Message().set_int(1, 6).set_msg(5, project)

    def text_variables(self) -> dict[str, str]:
        reply = self.call(COMMON + "GetTextVariables", Message().set_msg(1, self.project_document()))
        return {e.str(1): e.str(2) for e in reply.msgs(1)}

    def set_text_variables(self, values: dict[str, str], replace: bool = False) -> None:
        entries = [Message().set_str(1, k).set_str(2, v) for k, v in values.items()]
        body = Message().set_msg(1, self.project_document()) \
            .set_msg(2, Message().set_msgs(1, entries)).set_int(3, 2 if replace else 1)
        self.call(COMMON + "SetTextVariables", body)

    def net_classes(self) -> list[dict[str, Any]]:
        out = []
        for nc in self.call(COMMON + "GetNetClasses").msgs(1):
            board = nc.msg(3) or Message()
            constituents = [v.decode("utf-8") for v in nc.raw(6)]
            # KiCad 10.0.1 types a class implicit whenever it lists
            # constituents, and an explicit class lists itself; only one made
            # of other classes is implicit.
            row: dict[str, Any] = {"name": nc.str(1), "priority": nc.sint(2),
                                   "implicit": bool(constituents) and constituents != [nc.str(1)],
                                   "constituents": constituents}
            for num, key in ((1, "clearance_mm"), (2, "track_width_mm"),
                             (3, "diff_pair_width_mm"), (4, "diff_pair_gap_mm"),
                             (5, "diff_pair_via_gap_mm")):
                if board.has(num):
                    row[key] = read_dist(board.msg(num))
            via = board.msg(6)
            if via is not None:
                copper = via.msgs(5)
                drill = via.msg(3)
                if copper:
                    row["via_diameter_mm"] = (read_vec(copper[0].msg(3)) or [None])[0]
                if drill is not None:
                    row["via_drill_mm"] = (read_vec(drill.msg(3)) or [None])[0]
            out.append(row)
        return out

    def set_net_classes(self, classes: list[dict[str, Any]]) -> None:
        """Add or change net classes. Each dict takes the keys ``net_classes``
        gives; a class named here is updated in place.

        Only merging is offered. KiCad 10.0.1's replace mode frees every
        class object while the board's nets still point at them, and the
        next request that reads a net's class (GetItemsByNetClass, a save)
        crashes KiCad."""
        current = {nc.str(1): nc for nc in self.call(COMMON + "GetNetClasses").msgs(1)}
        out = []
        for spec in classes:
            nc = current.get(spec["name"], Message().set_str(1, spec["name"]).set_int(5, 1)).copy()
            board = nc.msg(3) or Message()
            for num, key in ((1, "clearance_mm"), (2, "track_width_mm"),
                             (3, "diff_pair_width_mm"), (4, "diff_pair_gap_mm"),
                             (5, "diff_pair_via_gap_mm")):
                if spec.get(key) is not None:
                    board.set_msg(num, dist(spec[key]))
            if spec.get("via_diameter_mm") is not None or spec.get("via_drill_mm") is not None:
                via = board.msg(6) or Message().set_int(1, 1).set_ints(2, [3, 34])
                if spec.get("via_diameter_mm") is not None:
                    d = spec["via_diameter_mm"]
                    via.set_msgs(5, [Message().set_int(1, 3).set_int(2, 1).set_msg(3, vec(d, d))])
                if spec.get("via_drill_mm") is not None:
                    d = spec["via_drill_mm"]
                    via.set_msg(3, (via.msg(3) or Message().set_int(1, 3).set_int(2, 34))
                                .set_msg(3, vec(d, d)))
                board.set_msg(6, via)
            nc.set_msg(3, board)
            if spec.get("priority") is not None:
                nc.set_int(2, int(spec["priority"]))
            if spec.get("constituents") is not None:
                nc.remove(6)
                for c in spec["constituents"]:
                    nc.fields.append([6, 2, str(c).encode("utf-8")])
            out.append(nc)
        self.call(COMMON + "SetNetClasses", Message().set_msgs(1, out).set_int(3, 1))

    def binary_path(self, name: str = "kicad-cli") -> str:
        return self.call(COMMON + "GetKiCadBinaryPath", Message().set_str(1, name)).str(1)

    # -- writing ------------------------------------------------------------
    @contextlib.contextmanager
    def commit(self, message: str = "eda-agent") -> Iterator[None]:
        """One undo step: everything inside lands together or not at all.

        Deletes made inside are checked once the commit lands, since
        KiCad applies them only then; their results are filled in place.
        """
        if self._open_commit:  # already inside one: join it
            yield
            return
        try:
            reply = self.call(COMMON + "BeginCommit")
        except KiCadApiError as e:
            if "already has a commit in progress" not in str(e):
                raise
            # Left open by an earlier call of this process: nothing in it
            # ever landed, so drop it and start afresh.
            self.drop_stale_commit()
            reply = self.call(COMMON + "BeginCommit")
        commit_id = reply.msg(1) or Message()
        self._open_commit, self._deleting = True, []
        try:
            yield
        except BaseException:
            self._open_commit = False
            try:
                self.call(COMMON + "EndCommit", Message().set_msg(1, commit_id).set_int(2, 2))
            except (KiCadApiError, KiCadNotReachableError):  # the caller's error is the one to see
                pass
            raise
        self._open_commit = False
        self.call(COMMON + "EndCommit", Message().set_msg(1, commit_id).set_int(2, 1)
                  .set_str(3, message))
        if self._deleting:
            remaining = {i.id for i in self.items_by_id([r["id"] for r in self._deleting])}
            for r in self._deleting:
                gone = r["id"] not in remaining
                r.update(ok=gone, status="ok" if gone else "cannot be deleted")
            self._deleting = []

    def create(self, items: Iterable[Item]) -> list[dict[str, Any]]:
        """Create items; each result says ok with the new item, or why not."""
        body = Message().set_msg(1, self._header()).set_msgs(2, [i.packed() for i in items])
        reply = self.call(COMMON + "CreateItems", body)
        return [_item_result(r) for r in reply.msgs(3)]

    def update(self, items: Iterable[Item]) -> list[dict[str, Any]]:
        body = Message().set_msg(1, self._header()).set_msgs(2, [i.packed() for i in items])
        reply = self.call(COMMON + "UpdateItems", body)
        return [_item_result(r) for r in reply.msgs(3)]

    def delete(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        """Delete items; each result says whether the item is gone.

        KiCad 10.0's reply lists nothing per item, so the result comes
        from asking afterwards which of the ids still exist. Inside a
        commit that is when the commit lands, and the results read
        "pending" until then.
        """
        ids = [i for i in ids if i]
        if not ids:
            return []
        existed = {i.id for i in self.items_by_id(ids)}
        out = [{"id": i, "ok": False, "status": "no such item"} if i not in existed
               else {"id": i, "ok": False, "status": "pending"} for i in ids]
        targets = [i for i in ids if i in existed]
        if not targets:
            return out
        with self.commit("eda-agent: delete"):
            body = Message().set_msg(1, self._header()).set_msgs(2, [kiid(i) for i in targets])
            self.call(COMMON + "DeleteItems", body)
            self._deleting.extend(r for r in out if r["status"] == "pending")
        return out

    def refill_zones(self, wait_s: float = 15.0) -> bool:
        """Refill every zone and wait for the fill to land.

        KiCad 10.0.1 refuses a list of zones (unimplemented) and runs the
        fill of all zones after it has answered, so this polls until every
        copper zone reads filled. Returns whether they all did in time."""
        import time
        self.call(BOARD_CMD + "RefillZones", Message().set_msg(1, self.document))
        deadline = time.monotonic() + wait_s
        while True:
            time.sleep(0.25)
            try:
                zones = [z.read() for z in self.items("zone")]
            except KiCadApiError as e:
                if "busy" not in str(e).lower():
                    raise
                zones = None  # still filling: KiCad answers busy until done
            if zones is not None and all(z.get("filled") for z in zones
                                         if z.get("zone_type") == "copper"):
                return True
            if time.monotonic() > deadline:
                return False

    def select(self, ids: Iterable[str]) -> None:
        self.call(COMMON + "AddToSelection",
                  Message().set_msg(1, self._header()).set_msgs(2, [kiid(i) for i in ids]))

    def deselect(self, ids: Iterable[str] = ()) -> None:
        ids = list(ids)
        if ids:
            self.call(COMMON + "RemoveFromSelection",
                      Message().set_msg(1, self._header()).set_msgs(2, [kiid(i) for i in ids]))
        else:
            self.call(COMMON + "ClearSelection", Message().set_msg(1, self._header()))

    def flip(self, ids: Iterable[str]) -> str:
        """Turn items over to the other side with KiCad's own Flip, which
        mirrors their layers, pads and text. One undo step per call."""
        ids = list(ids)
        previous = [i.id for i in self.selection()]
        self.deselect()
        self.select(ids)
        try:
            return self.run_action("pcbnew.InteractiveEdit.flip")
        finally:
            self.deselect()
            if previous:
                self.select(previous)

    def interactive_move(self, ids: Iterable[str]) -> None:
        self.call(BOARD_CMD + "InteractiveMoveItems",
                  Message().set_msg(1, self.document).set_msgs(2, [kiid(i) for i in ids]))

    def mark(self, message: str, x_mm: float, y_mm: float, ids: Iterable[str] = (),
             severity: str = "warning") -> str:
        """Put a DRC marker on the board at a point, against some items."""
        level = {"warning": 1, "error": 2, "info": 5}.get(severity, 1)
        body = Message().set_msg(1, self.document).set_int(2, level).set_str(3, message) \
            .set_msg(4, vec(x_mm, y_mm)).set_msgs(5, [kiid(i) for i in ids])
        # KiCad 10.0.1 pushes the marker through the client's current commit
        # and, when there was none, leaves the one it made open, which
        # refuses every later BeginCommit. Inside a commit of our own the
        # marker lands and the commit closes normally.
        with self.commit("eda-agent: marker"):
            reply = self.call(BOARD_CMD + "InjectDrcError", body)
        return (reply.msg(1) or Message()).str(1)

    def drop_stale_commit(self) -> bool:
        """Close a commit this client left open (a crashed call, or the
        marker leak above). Returns whether there was one."""
        try:
            self.call(COMMON + "EndCommit", Message().set_int(2, 2))
        except KiCadApiError as e:
            if "does not has a commit" in str(e) or "does not have a commit" in str(e):
                return False
            raise
        return True

    def save(self) -> None:
        self.call(COMMON + "SaveDocument", Message().set_msg(1, self.document))

    def save_copy(self, path: str, overwrite: bool = False) -> None:
        body = Message().set_msg(1, self.document).set_str(2, path) \
            .set_msg(3, Message().set_bool(1, overwrite))
        self.call(COMMON + "SaveCopyOfDocument", body)

    def revert(self) -> None:
        self.call(COMMON + "RevertDocument", Message().set_msg(1, self.document))

    def run_action(self, action: str) -> str:
        reply = self.call(COMMON + "RunAction", Message().set_str(1, action))
        return {1: "ok", 2: "no such action", 3: "editor not open"}.get(reply.int(1), "unknown")


def _item_result(r: Message) -> dict[str, Any]:
    status = r.msg(1) or Message()
    names = {1: "ok", 2: "wrong item type", 3: "already exists", 4: "no such item",
             5: "cannot be changed", 7: "invalid data"}
    code = status.int(1)
    out: dict[str, Any] = {"ok": code == 1, "status": names.get(code, "unknown")}
    if status.str(2):
        out["error"] = status.str(2)
    packed = r.msg(2)
    if packed is not None and packed.has(1):
        item = _unpack(packed)
        out["item"] = item.read()
    return out


# -- building items -------------------------------------------------------------
def make_track(start, end, width_mm: float, layer: int, net_name: str = "",
               locked: bool = False) -> Item:
    m = Message().set_msg(2, vec(*start)).set_msg(3, vec(*end)) \
        .set_msg(4, dist(width_mm)).set_int(5, locked_state(locked)).set_int(6, layer)
    if net_name:
        m.set_msg(7, net(net_name))
    return Item("Track", m)


def make_arc(start, mid, end, width_mm: float, layer: int, net_name: str = "") -> Item:
    m = Message().set_msg(2, vec(*start)).set_msg(3, vec(*mid)).set_msg(4, vec(*end)) \
        .set_msg(5, dist(width_mm)).set_int(6, 1).set_int(7, layer)
    if net_name:
        m.set_msg(8, net(net_name))
    return Item("Arc", m)


def make_via(position, diameter_mm: float, drill_mm: float, net_name: str = "",
             start_layer: int = 3, end_layer: int = 34, via_type: int = 1) -> Item:
    copper = Message().set_int(1, 3).set_int(2, 1) \
        .set_msg(3, vec(diameter_mm, diameter_mm))
    drill = Message().set_int(1, start_layer).set_int(2, end_layer) \
        .set_msg(3, vec(drill_mm, drill_mm)).set_int(4, 1)
    stack = Message().set_int(1, 1).set_ints(2, [start_layer, end_layer]) \
        .set_msg(3, drill).set_msgs(5, [copper])
    m = Message().set_msg(2, vec(*position)).set_msg(3, stack).set_int(4, 1).set_int(6, via_type)
    if net_name:
        m.set_msg(5, net(net_name))
    return Item("Via", m)


def make_shape(layer: int, width_mm: float, *, segment=None, rectangle=None, arc=None,
               circle=None, polygon=None, filled: bool = False, net_name: str = "") -> Item:
    stroke = Message().set_msg(1, dist(width_mm)).set_int(2, 1)
    attrs = Message().set_msg(1, stroke).set_msg(2, Message().set_int(1, 2 if filled else 1))
    shape = Message().set_msg(3, attrs)
    if segment:
        shape.set_msg(4, Message().set_msg(1, vec(*segment[0])).set_msg(2, vec(*segment[1])))
    elif rectangle:
        shape.set_msg(5, Message().set_msg(1, vec(*rectangle[0])).set_msg(2, vec(*rectangle[1])))
    elif arc:
        shape.set_msg(6, Message().set_msg(1, vec(*arc[0])).set_msg(2, vec(*arc[1]))
                      .set_msg(3, vec(*arc[2])))
    elif circle:
        (cx, cy), r = circle
        shape.set_msg(7, Message().set_msg(1, vec(cx, cy)).set_msg(2, vec(cx + r, cy)))
    elif polygon:
        shape.set_msg(8, polyset([polygon]))
    else:
        raise ValueError("a shape needs a segment, rectangle, arc, circle or polygon")
    m = Message().set_msg(1, shape).set_int(2, layer).set_int(5, 1)
    if net_name:
        m.set_msg(3, net(net_name))
    return Item("BoardGraphicShape", m)


def make_text(text: str, position, layer: int, size_mm: float = 1.0,
              thickness_mm: float = 0.15, angle_deg: float = 0.0,
              mirrored: Optional[bool] = None, knockout: bool = False) -> Item:
    if mirrored is None:
        mirrored = layer in (34, 39, 41, 37, 35, 49, 51)  # back-side layers read mirrored
    attrs = Message().set_int(2, 2).set_int(3, 2).set_msg(4, angle(angle_deg)) \
        .set_msg(6, dist(thickness_mm)).set_bool(10, True).set_bool(11, mirrored) \
        .set_msg(14, vec(size_mm, size_mm))
    body = Message().set_msg(2, vec(*position)).set_msg(3, attrs).set_str(5, text)
    return Item("BoardText", Message().set_msg(2, body).set_int(3, layer)
                .set_bool(4, knockout).set_int(5, 1))


def make_zone(outline, layers: list[int], *, net_name: str = "", name: str = "",
              priority: int = 0, clearance_mm: Optional[float] = None,
              min_width_mm: Optional[float] = None, connection: Optional[str] = None,
              keepout: Optional[dict[str, bool]] = None) -> Item:
    m = Message().set_ints(3, layers).set_msg(4, polyset([outline]))
    if name:
        m.set_str(5, name)
    if keepout is not None:
        rule = Message()
        for num, key in ((1, "copper"), (2, "vias"), (3, "tracks"), (4, "pads"), (5, "footprints")):
            rule.set_bool(num, bool(keepout.get(key, key in ("copper", "vias", "tracks"))))
        m.set_int(2, 3).set_msg(7, rule)
    else:
        copper = Message()
        if connection:
            styles = {"inherited": 1, "none": 2, "thermal": 3, "solid": 4, "pth_thermal": 5}
            copper.set_msg(1, Message().set_int(1, styles[connection]))
        if clearance_mm is not None:
            copper.set_msg(2, dist(clearance_mm))
        if min_width_mm is not None:
            copper.set_msg(3, dist(min_width_mm))
        if net_name:
            copper.set_msg(8, net(net_name))
        m.set_int(2, 1).set_msg(6, copper)
    m.set_int(8, int(priority)).set_int(12, 1)
    return Item("Zone", m)


# -- moving a footprint's contents ----------------------------------------------------
# KiCad rebuilds a footprint's pads, graphics and fields from the absolute
# positions in the update, so moving or turning one means moving every
# child with it.

def _map_vec(m: Optional[Message], fn) -> Optional[Message]:
    if m is None:
        return None
    x, y = fn(m.sint(1), m.sint(2))
    return m.copy().set_int(1, x).set_int(2, y)


def _map_field(m: Message, num: int, fn) -> None:
    sub = m.msg(num)
    if sub is not None:
        m.set_msg(num, _map_vec(sub, fn))


def _add_angle(m: Message, num: int, degrees: float) -> None:
    current = m.msg(num)
    value = (current.double(1) if current is not None else 0.0) + degrees
    value = (value + 180.0) % 360.0 - 180.0
    m.set_msg(num, (current or Message()).copy().set_double(1, value))


def _map_polyline(line: Message, fn) -> Message:
    nodes = []
    for node in line.msgs(1):
        if node.has(1):
            node.set_msg(1, _map_vec(node.msg(1), fn))
        arc = node.msg(2)
        if arc is not None:
            for i in (1, 2, 3):
                _map_field(arc, i, fn)
            node.set_msg(2, arc)
        nodes.append(node)
    return line.copy().set_msgs(1, nodes)


def _map_polyset(ps: Message, fn) -> Message:
    polys = []
    for poly in ps.msgs(1):
        outline = poly.msg(1)
        if outline is not None:
            poly.set_msg(1, _map_polyline(outline, fn))
        poly.set_msgs(2, [_map_polyline(h, fn) for h in poly.msgs(2)])
        polys.append(poly)
    return ps.copy().set_msgs(1, polys)


def _map_text(text: Message, fn, degrees: float) -> Message:
    """A common Text: position and angle."""
    _map_field(text, 2, fn)
    if degrees:
        attrs = text.msg(3) or Message()
        _add_angle(attrs, 4, degrees)
        text.set_msg(3, attrs)
    return text


def _map_board_text(bt: Message, fn, degrees: float) -> Message:
    inner = bt.msg(2)
    if inner is not None:
        bt.set_msg(2, _map_text(inner, fn, degrees))
    return bt


def _map_graphic(gs: Message, fn, degrees: float) -> Message:
    shape = gs.msg(1)
    if shape is None:
        return gs
    rect = shape.msg(5)
    if rect is not None and degrees % 90:
        # A rectangle stays axis-aligned in KiCad, so a turn that is not a
        # right angle makes it a four-sided polygon.
        (x1, y1), (x2, y2) = [(v.sint(1), v.sint(2)) for v in (rect.msg(1), rect.msg(2))]
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        line = Message().set_msgs(1, [Message().set_msg(1, Message().set_int(1, x).set_int(2, y))
                                      for x, y in corners]).set_bool(2, True)
        shape.remove(5).set_msg(8, _map_polyset(
            Message().set_msgs(1, [Message().set_msg(1, line)]), fn))
    else:
        for num, count in ((4, 2), (5, 2), (6, 3), (7, 2), (9, 4)):
            geom = shape.msg(num)
            if geom is not None:
                for i in range(1, count + 1):
                    _map_field(geom, i, fn)
                shape.set_msg(num, geom)
        poly = shape.msg(8)
        if poly is not None:
            shape.set_msg(8, _map_polyset(poly, fn))
    return gs.set_msg(1, shape)


def _map_child(packed: Message, fn, degrees: float) -> Message:
    type_name = packed.str(1).rsplit(".", 1)[-1]
    m = Message.decode(packed.bytes(2))
    if type_name == "Pad":
        _map_field(m, 7, fn)
        if degrees:
            stack = m.msg(6) or Message()
            _add_angle(stack, 6, degrees)
            m.set_msg(6, stack)
    elif type_name == "BoardText":
        _map_board_text(m, fn, degrees)
    elif type_name == "BoardTextBox":
        box = m.msg(2)
        if box is not None:
            _map_field(box, 2, fn)
            _map_field(box, 3, fn)
            if degrees:
                attrs = box.msg(4) or Message()
                _add_angle(attrs, 4, degrees)
                box.set_msg(4, attrs)
            m.set_msg(2, box)
    elif type_name == "BoardGraphicShape":
        _map_graphic(m, fn, degrees)
    elif type_name == "Zone":
        if m.has(4):
            m.set_msg(4, _map_polyset(m.msg(4), fn))
        filled = []
        for fp in m.msgs(10):
            if fp.has(2):
                fp.set_msg(2, _map_polyset(fp.msg(2), fn))
            filled.append(fp)
        m.set_msgs(10, filled)
    elif type_name == "Field":
        bt = m.msg(3)
        if bt is not None:
            m.set_msg(3, _map_board_text(bt, fn, degrees))
    return packed.copy().set_bytes(2, m.encode())


def translate(item: Item, dx_mm: float, dy_mm: float) -> Item:
    """The item moved by (dx, dy) mm, every point of it."""
    dx, dy = to_nm(dx_mm), to_nm(dy_mm)

    def fn(x: int, y: int) -> tuple[int, int]:
        return x + dx, y + dy

    m = item.message.copy()
    kind = item.kind
    if kind == "footprint":
        pos = read_vec(m.msg(2)) or [0.0, 0.0]
        return Item(item.type_name, _place_footprint(
            m, (pos[0] + dx_mm, pos[1] + dy_mm), None))
    if kind in ("track", "arc"):
        for num in ((2, 3) if kind == "track" else (2, 3, 4)):
            _map_field(m, num, fn)
    elif kind == "via":
        _map_field(m, 2, fn)
    elif kind == "dimension":
        for num in range(5, 10):
            geom = m.msg(num)
            if geom is not None:
                for i in (1, 2):
                    _map_field(geom, i, fn)
                m.set_msg(num, geom)
        text = m.msg(4)
        if text is not None:
            m.set_msg(4, _map_text(text, fn, 0.0))
    elif kind == "barcode":
        _map_field(m, 5, fn)
    else:
        packed = _map_child(item.packed(), fn, 0.0)
        m = Message.decode(packed.bytes(2))
    return Item(item.type_name, m)


def rotate_about(item: Item, cx_mm: float, cy_mm: float, degrees: float) -> Item:
    """The item turned counter-clockwise by ``degrees`` about a point."""
    if item.kind == "footprint":
        pos = read_vec(item.message.msg(2)) or [0.0, 0.0]
        theta = math.radians(degrees)
        dx, dy = pos[0] - cx_mm, pos[1] - cy_mm
        nx = cx_mm + dx * math.cos(theta) + dy * math.sin(theta)
        ny = cy_mm - dx * math.sin(theta) + dy * math.cos(theta)
        new_angle = read_angle(item.message.msg(3)) + degrees
        new_angle = (new_angle + 180.0) % 360.0 - 180.0
        return Item(item.type_name, _place_footprint(item.message.copy(), (nx, ny), new_angle))
    ox, oy = to_nm(cx_mm), to_nm(cy_mm)
    theta = math.radians(degrees)
    c, s = math.cos(theta), math.sin(theta)

    def fn(x: int, y: int) -> tuple[int, int]:
        dx, dy = x - ox, y - oy
        return int(round(ox + dx * c + dy * s)), int(round(oy - dx * s + dy * c))

    m = item.message.copy()
    if item.kind in ("track", "arc"):
        for num in ((2, 3) if item.kind == "track" else (2, 3, 4)):
            _map_field(m, num, fn)
        return Item(item.type_name, m)
    if item.kind == "via":
        _map_field(m, 2, fn)
        return Item(item.type_name, m)
    if item.kind in ("pad", "text", "textbox", "shape", "zone"):
        packed = _map_child(item.packed(), fn, degrees)
        return Item(item.type_name, Message.decode(packed.bytes(2)))
    raise ValueError(f"a {item.kind} cannot be rotated here")


def read_zone_fill(m: Message) -> list[dict[str, Any]]:
    """A zone's filled copper per layer, as polygons in mm."""
    return [{"layer": layer_name(fp.int(1)), "polygons": read_polyset(fp.msg(2))}
            for fp in m.msgs(10)]


def make_dimension(style: str, layer: int, a, b, *, height_mm: float = 0.0,
                   text_size_mm: float = 1.0, thickness_mm: float = 0.15,
                   unit: str = "mm") -> Item:
    """An aligned, orthogonal, leader, center or radial dimension.

    ``a`` and ``b`` are the two points: start and end, or centre and the
    point on the radius for radial and center."""
    nums = {"aligned": 5, "orthogonal": 6, "radial": 7, "leader": 8, "center": 9}
    if style not in nums:
        raise ValueError(f"style must be one of {sorted(nums)}")
    geom = Message().set_msg(1, vec(*a)).set_msg(2, vec(*b))
    if style in ("aligned", "orthogonal"):
        geom.set_msg(3, dist(height_mm))
    attrs = Message().set_msg(6, dist(thickness_mm)).set_bool(10, True) \
        .set_msg(14, vec(text_size_mm, text_size_mm))
    text = Message().set_msg(2, vec(*a)).set_msg(3, attrs)
    units = {"in": 1, "inch": 1, "mil": 2, "mils": 2, "mm": 3, "auto": 4}
    m = Message().set_int(2, 1).set_int(3, layer).set_msg(4, text) \
        .set_msg(nums[style], geom).set_int(14, units.get(unit, 3)) \
        .set_int(15, 2).set_int(16, 2).set_int(17, 3) \
        .set_msg(19, dist(thickness_mm)).set_msg(20, dist(1.27)) \
        .set_msg(21, dist(0.5)).set_int(22, 1).set_bool(23, True)
    return Item("Dimension", m)


def make_group(name: str, ids: Iterable[str]) -> Item:
    return Item("Group", Message().set_str(2, name).set_msgs(3, [kiid(i) for i in ids]))


def _place_footprint(m: Message, new_pos: Optional[tuple[float, float]],
                     new_angle: Optional[float]) -> Message:
    """Move and turn a footprint and everything it carries."""
    pos = m.msg(2) or Message()
    ox, oy = pos.sint(1), pos.sint(2)
    tx, ty = (to_nm(new_pos[0]), to_nm(new_pos[1])) if new_pos is not None else (ox, oy)
    current = read_angle(m.msg(3))
    degrees = (float(new_angle) - current) if new_angle is not None else 0.0
    theta = math.radians(degrees)
    c, s = math.cos(theta), math.sin(theta)

    def fn(x: int, y: int) -> tuple[int, int]:
        dx, dy = x - ox, y - oy
        # Y points down in KiCad, so a positive (counter-clockwise) turn is
        # this way round.
        rx = dx * c + dy * s
        ry = -dx * s + dy * c
        return int(round(tx + rx)), int(round(ty + ry))

    m.set_msg(2, Message().set_int(1, tx).set_int(2, ty))
    if new_angle is not None:
        m.set_msg(3, angle(float(new_angle)))
    for num in (7, 8, 9, 10):  # the instance's fields
        field = m.msg(num)
        if field is not None:
            bt = field.msg(3)
            if bt is not None:
                field.set_msg(3, _map_board_text(bt, fn, degrees))
            m.set_msg(num, field)
    definition = m.msg(6)
    if definition is not None:
        for num in (7, 8, 9, 10):
            field = definition.msg(num)
            if field is not None and field.msg(3) is not None:
                field.set_msg(3, _map_board_text(field.msg(3), fn, degrees))
                definition.set_msg(num, field)
        definition.set_msgs(11, [_map_child(p, fn, degrees) for p in definition.msgs(11)])
        m.set_msg(6, definition)
    return m


# -- patching items -------------------------------------------------------------
def patch(item: Item, changes: dict[str, Any], board: Optional[KiCadBoard] = None) -> Item:
    """The item with ``changes`` applied and every other field kept.

    Takes the keys ``read_item`` gives for the item's kind. A key that
    cannot be written for that kind raises ValueError naming it.
    """
    layer_of = board.layer if board is not None else layer_number
    m = item.message.copy()
    kind = item.kind
    if kind == "footprint" and ("position_mm" in changes or "rotation_deg" in changes):
        changes = dict(changes)
        pos = changes.pop("position_mm", None)
        rot = changes.pop("rotation_deg", None)
        m = _place_footprint(m, tuple(pos) if pos is not None else None, rot)
    if kind == "footprint" and str(changes.get("side", changes.get("layer", ""))).lower() in \
            ("bottom", "b", "back", "b.cu", "34", "top", "f", "front", "f.cu", "3"):
        raise ValueError("turn a footprint over with flip_footprints: the "
                         "layers, pads and text all mirror")
    for key, value in changes.items():
        if key == "locked":
            m.set_int({"track": 5, "arc": 6, "via": 4, "pad": 2, "shape": 5, "text": 5,
                       "textbox": 4, "zone": 12, "footprint": 5, "dimension": 2,
                       "barcode": 14}[kind], locked_state(bool(value)))
        elif key == "net" and kind in ("track", "arc", "via", "pad", "shape", "zone"):
            if kind == "zone":
                copper = m.msg(6) or Message()
                m.set_msg(6, copper.set_msg(8, net(value)))
            else:
                m.set_msg({"track": 7, "arc": 8, "via": 5, "pad": 4, "shape": 3}[kind], net(value))
        elif key == "layer" and kind in ("track", "arc", "shape", "text", "textbox", "dimension"):
            m.set_int({"track": 6, "arc": 7, "shape": 2, "text": 3, "textbox": 3,
                       "dimension": 3}[kind], layer_of(value))
        elif key == "width_mm" and kind in ("track", "arc"):
            m.set_msg(4 if kind == "track" else 5, dist(value))
        elif key == "width_mm" and kind == "shape":
            shape = m.msg(1) or Message()
            attrs = shape.msg(3) or Message()
            stroke = (attrs.msg(1) or Message()).set_msg(1, dist(value))
            m.set_msg(1, shape.set_msg(3, attrs.set_msg(1, stroke)))
        elif key in ("start_mm", "end_mm", "mid_mm") and kind in ("track", "arc"):
            num = {"track": {"start_mm": 2, "end_mm": 3},
                   "arc": {"start_mm": 2, "mid_mm": 3, "end_mm": 4}}[kind].get(key)
            if num is None:
                raise ValueError(f"{kind} has no {key}")
            m.set_msg(num, vec(*value))
        elif key == "position_mm" and kind in ("via", "pad", "footprint"):
            m.set_msg({"via": 2, "pad": 7, "footprint": 2}[kind], vec(*value))
        elif key == "position_mm" and kind == "text":
            body = (m.msg(2) or Message()).set_msg(2, vec(*value))
            m.set_msg(2, body)
        elif key in ("reference", "value", "datasheet", "description") and kind == "footprint":
            num = {"reference": 7, "value": 8, "datasheet": 9, "description": 10}[key]
            field = m.msg(num) or Message()
            board_text = field.msg(3) or Message()
            text = (board_text.msg(2) or Message()).set_str(5, str(value))
            m.set_msg(num, field.set_msg(3, board_text.set_msg(2, text)))
        elif key in ("dnp", "exclude_from_bom", "exclude_from_pos") and kind == "footprint":
            attrs = m.msg(11) or Message()
            m.set_msg(11, attrs.set_bool({"dnp": 7, "exclude_from_bom": 5,
                                          "exclude_from_pos": 4}[key], bool(value)))
        elif key == "text" and kind == "text":
            body = (m.msg(2) or Message()).set_str(5, str(value))
            m.set_msg(2, body)
        elif key == "text" and kind == "textbox":
            box = (m.msg(2) or Message()).set_str(6, str(value))
            m.set_msg(2, box)
        elif key in ("diameter_mm", "drill_mm") and kind == "via":
            stack = m.msg(3) or Message()
            if key == "diameter_mm":
                layers = stack.msgs(5) or [Message().set_int(1, 3).set_int(2, 1)]
                stack.set_msgs(5, [l.set_msg(3, vec(value, value)) for l in layers])
            else:
                drill = (stack.msg(3) or Message()).set_msg(3, vec(value, value))
                stack.set_msg(3, drill)
            m.set_msg(3, stack)
        elif key == "priority" and kind == "zone":
            m.set_int(8, int(value))
        elif key == "name" and kind in ("zone", "group"):
            m.set_str(5 if kind == "zone" else 2, str(value))
        elif key in ("clearance_mm", "min_width_mm", "connection") and kind == "zone":
            copper = m.msg(6)
            if copper is None:
                raise ValueError("only a copper zone has " + key)
            if key == "connection":
                styles = {"inherited": 1, "none": 2, "thermal": 3, "solid": 4, "pth_thermal": 5}
                conn = (copper.msg(1) or Message()).set_int(1, styles[value])
                copper.set_msg(1, conn)
            else:
                copper.set_msg(2 if key == "clearance_mm" else 3, dist(value))
            m.set_msg(6, copper)
        elif key == "outline" and kind == "zone":
            m.set_msg(4, polyset([value]))
        elif key == "number" and kind == "pad":
            m.set_str(3, str(value))
        elif kind == "pad" and key in ("size_mm", "shape", "drill_mm", "clearance_mm",
                                       "angle_deg"):
            stack = m.msg(6) or Message()
            if key == "size_mm":
                stack.set_msgs(5, [l.set_msg(3, vec(*value)) for l in stack.msgs(5)])
            elif key == "shape":
                shapes = {"circle": 1, "rect": 2, "oval": 3, "trapezoid": 4,
                          "roundrect": 5, "chamfered_rect": 6}
                if value not in shapes:
                    raise ValueError(f"pad shape must be one of {sorted(shapes)}")
                stack.set_msgs(5, [l.set_int(2, shapes[value]) for l in stack.msgs(5)])
            elif key == "drill_mm":
                d = value if isinstance(value, (list, tuple)) else (value, value)
                drill = stack.msg(3)
                if drill is None:
                    raise ValueError("an SMD pad has no drill")
                stack.set_msg(3, drill.set_msg(3, vec(*d)))
            elif key == "angle_deg":
                stack.set_msg(6, angle(float(value)))
            else:
                m.set_msg(8, dist(value))
            if key != "clearance_mm":
                m.set_msg(6, stack)
        elif kind in ("via", "pad") and key in ("tented", "solder_mask_margin_mm"):
            num = 3 if kind == "via" else 6
            stack = m.msg(num) or Message()
            for side in (7, 8):
                outer = stack.msg(side) or Message()
                if key == "tented":
                    # Covered / uncovered / from the design rules: a via's
                    # covering mode, a pad's solder mask mode.
                    mode = 3 if value is None else (1 if value else 2)
                    outer.set_int(6 if kind == "via" else 1, mode)
                else:
                    outer.set_msg(3, Message().set_msg(1, dist(value)))
                stack.set_msg(side, outer)
            m.set_msg(num, stack)
        elif kind == "via" and key in ("start_layer", "end_layer"):
            stack = m.msg(3) or Message()
            drill = stack.msg(3) or Message()
            drill.set_int(1 if key == "start_layer" else 2, layer_of(value))
            stack.set_msg(3, drill)
            m.set_msg(3, stack)
        elif kind == "via" and key == "via_type":
            types = {"through": 1, "blind_buried": 2, "micro": 3, "blind": 4, "buried": 5}
            m.set_int(6, types[value])
        elif kind == "text" and key in ("size_mm", "thickness_mm", "angle_deg", "mirrored",
                                        "visible", "bold", "italic"):
            body = m.msg(2) or Message()
            body.set_msg(3, _text_attr(body.msg(3) or Message(), key, value))
            m.set_msg(2, body)
        elif kind == "text" and key == "knockout":
            m.set_bool(4, bool(value))
        elif kind == "footprint" and "_" in key and key.split("_", 1)[0] in (
                "reference", "value") and key.split("_", 1)[1] in (
                "size_mm", "thickness_mm", "visible", "position_mm", "angle_deg", "layer"):
            field_name, prop = key.split("_", 1)
            num = 7 if field_name == "reference" else 8
            field = m.msg(num) or Message()
            board_text = field.msg(3) or Message()
            if prop == "layer":
                board_text.set_int(3, layer_of(value))
            elif prop == "position_mm":
                board_text.set_msg(2, (board_text.msg(2) or Message()).set_msg(2, vec(*value)))
            else:
                text = board_text.msg(2) or Message()
                text.set_msg(3, _text_attr(text.msg(3) or Message(), prop, value))
                board_text.set_msg(2, text)
                if prop == "visible":
                    field.set_bool(4, bool(value))
            m.set_msg(num, field.set_msg(3, board_text))
        elif kind == "footprint" and key in ("exempt_from_courtyard", "allow_soldermask_bridges",
                                             "not_in_schematic"):
            attrs = m.msg(11) or Message()
            m.set_msg(11, attrs.set_bool({"not_in_schematic": 3, "exempt_from_courtyard": 6,
                                          "allow_soldermask_bridges": 9}[key], bool(value)))
        elif kind == "footprint" and key == "clearance_mm":
            over = m.msg(12) or Message()
            m.set_msg(12, over.set_msg(3, dist(value)))
        elif kind == "zone" and key == "layers":
            m.set_ints(3, [layer_of(l) for l in value])
        elif kind == "zone" and key in ("fill_mode", "thermal_gap_mm", "thermal_spoke_width_mm",
                                        "island_removal", "min_island_area_mm2"):
            copper = m.msg(6)
            if copper is None:
                raise ValueError("only a copper zone has " + key)
            if key == "fill_mode":
                copper.set_int(6, {"solid": 1, "hatched": 2}[value])
            elif key == "island_removal":
                copper.set_int(4, {"always": 1, "never": 2, "area": 3}[value])
            elif key == "min_island_area_mm2":
                copper.set_int(5, int(round(float(value) * NM_PER_MM * NM_PER_MM)))
            else:
                conn = copper.msg(1) or Message()
                spokes = conn.msg(2) or Message()
                spokes.set_msg(3 if key == "thermal_gap_mm" else 1, dist(value))
                copper.set_msg(1, conn.set_msg(2, spokes))
            m.set_msg(6, copper)
        elif kind == "zone" and key == "keepout":
            rule = m.msg(7)
            if rule is None:
                raise ValueError("only a rule area has keepout settings")
            for num, k in ((1, "copper"), (2, "vias"), (3, "tracks"), (4, "pads"), (5, "footprints")):
                if k in value:
                    rule.set_bool(num, bool(value[k]))
            m.set_msg(7, rule)
        elif kind == "shape" and key == "filled":
            shape = m.msg(1) or Message()
            attrs = shape.msg(3) or Message()
            fill = (attrs.msg(2) or Message()).set_int(1, 2 if value else 1)
            m.set_msg(1, shape.set_msg(3, attrs.set_msg(2, fill)))
        elif kind == "shape" and key in ("start_mm", "end_mm", "center_mm", "mid_mm"):
            shape = m.msg(1) or Message()
            done = False
            for num, points in ((4, ("start_mm", "end_mm")), (6, ("start_mm", "mid_mm", "end_mm")),
                                (7, ("center_mm",))):
                geom = shape.msg(num)
                if geom is not None and key in points:
                    geom.set_msg(points.index(key) + 1, vec(*value))
                    shape.set_msg(num, geom)
                    done = True
            if not done:
                raise ValueError(f"this shape has no {key}")
            m.set_msg(1, shape)
        elif kind == "dimension" and key in ("override_text", "prefix", "suffix"):
            if key == "override_text":
                m.set_bool(10, bool(value)).set_str(11, str(value))
            else:
                m.set_str(12 if key == "prefix" else 13, str(value))
        else:
            raise ValueError(f"{key} cannot be set on a {kind}")
    return Item(item.type_name, m)


def _text_attr(attrs: Message, key: str, value: Any) -> Message:
    """TextAttributes with one property changed."""
    if key == "size_mm":
        size = value if isinstance(value, (list, tuple)) else (value, value)
        return attrs.set_msg(14, vec(*size))
    if key == "thickness_mm":
        return attrs.set_msg(6, dist(value))
    if key == "angle_deg":
        return attrs.set_msg(4, angle(float(value)))
    return attrs.set_bool({"visible": 10, "mirrored": 11, "bold": 8, "italic": 7}[key],
                          bool(value))


# -- finding the editor -----------------------------------------------------------
def open_board(ipc: KiCadIpc) -> KiCadBoard:
    """The board open in the PCB editor, or a clear error."""
    try:
        docs = ipc.call(COMMON + "GetOpenDocuments", Message().set_int(1, DOC_PCB)).msgs(1)
    except KiCadApiError as e:
        raise KiCadNotReachableError(
            f"no PCB editor is open in KiCad ({e}). Open the project's board.") from None
    if not docs:
        raise KiCadNotReachableError("no board is open in KiCad's PCB editor")
    return KiCadBoard(ipc, docs[0])


def open_schematics(ipc: KiCadIpc) -> list[dict[str, str]]:
    try:
        docs = ipc.call(COMMON + "GetOpenDocuments", Message().set_int(1, DOC_SCHEMATIC)).msgs(1)
    except KiCadApiError:  # the schematic editor is not open
        return []
    out = []
    for d in docs:
        sheet = d.msg(3) or Message()
        project = d.msg(5) or Message()
        # KiCad 10.0 names the schematic file in the board-file field.
        out.append({"filename": d.str(4), "sheet": sheet.str(2),
                    "project": project.str(1), "project_path": project.str(2)})
    return out
