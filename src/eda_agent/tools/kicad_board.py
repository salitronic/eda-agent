# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Board tools over KiCad's API: find, place, change and remove items.

Every write tool takes ``items``, a list of its own argument dicts, and
sends the whole list in one commit, so a call is one undo step in the
PCB editor. Arguments given beside ``items`` apply to every item. The
reply lists each item's result in order and says which failed.

Distances are millimetres and every distance argument says so in its
name (``x_mm``, ``width_mm``); a point is ``[x_mm, y_mm]``. Layers take
KiCad's names ("F.Cu", "B.Silkscreen", "Edge.Cuts", "In1.Cu").
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from typing import Any, Callable, Iterable, Optional

from ..bridge import kicad_api as api
from ..bridge.kicad_bridge import (KiCadApiError, KiCadNotReachableError,
                                   get_kicad_bridge)

_FAILURES = (KiCadNotReachableError, KiCadApiError, ValueError, KeyError,
             TypeError, IndexError, OSError, RuntimeError)

# Kinds read when a call names none.
_ALL_KINDS = ("footprint", "track", "arc", "via", "zone", "shape", "text",
              "textbox", "dimension", "group")
_NET_KINDS = ("track", "arc", "via", "pad", "zone", "shape")


def _guard(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a tool body; a failure becomes the reply's reason."""
    try:
        return fn()
    except _FAILURES as e:
        return {"ok": False, "reason": str(e) or type(e).__name__}


def _board() -> api.KiCadBoard:
    return get_kicad_bridge().board()


def _merge(items: Optional[list], shared: dict[str, Any]) -> list[dict[str, Any]]:
    """Each item's arguments over the shared ones; one item when none given."""
    shared = {k: v for k, v in shared.items() if v is not None}
    if items is None:
        return [shared]
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list of argument dicts")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("each item must be a dict of arguments")
        out.append({**shared, **it})
    return out


def _summary(results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    failed = [i for i, r in enumerate(results) if not r.get("ok")]
    out: dict[str, Any] = {"ok": not failed, "count": len(results),
                           "succeeded": len(results) - len(failed),
                           "failed": len(failed), "items": results}
    if failed:
        out["failed_items"] = failed
    out.update(extra)
    return out


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be [x_mm, y_mm]")
    return float(value[0]), float(value[1])


def _brief(d: dict[str, Any]) -> dict[str, Any]:
    """An item as read, without a footprint's pad list."""
    if d.get("kind") == "footprint":
        d = {k: v for k, v in d.items() if k != "pads"}
        d["pad_count"] = d.get("pad_count", 0)
    return d


def _footprints(board: api.KiCadBoard) -> dict[str, api.Item]:
    out = {}
    for f in board.items("footprint"):
        out.setdefault(f.read()["reference"], f)
    return out


def _pad_ids(footprint: api.Item) -> dict[str, str]:
    """Pad number -> pad id, from a footprint as read."""
    return {p["number"]: p["id"] for p in footprint.read()["pads"]}


def _create_all(board: api.KiCadBoard, built: list[tuple[int, Any]],
                count: int, save: bool, label: str) -> dict[str, Any]:
    """Create the built items in one commit; ``built`` pairs an item's
    index with its Item, or with the reason it could not be built."""
    results: list[dict[str, Any]] = [{"ok": False, "reason": "not built"}] * count
    good = [(i, it) for i, it in built if isinstance(it, api.Item)]
    for i, it in built:
        if not isinstance(it, api.Item):
            results[i] = {"ok": False, "reason": str(it)}
    if good:
        with board.commit(f"eda-agent: {label}"):
            replies = board.create([it for _, it in good])
        for (i, _), r in zip(good, replies):
            if r["ok"]:
                results[i] = {"ok": True, **{k: v for k, v in _brief(r["item"]).items()}}
            else:
                results[i] = {"ok": False, "reason": f"KiCad refused it: {r['status']} "
                                                     f"{r.get('error', '')}".strip()}
        if len(replies) < len(good):
            for i, _ in good[len(replies):]:
                results[i] = {"ok": False, "reason": "KiCad's reply did not list this item"}
    if save and any(r.get("ok") for r in results):
        board.save()
    return _summary(results, saved=bool(save and any(r.get("ok") for r in results)))


def _build_each(merged: list[dict[str, Any]], make: Callable[[dict[str, Any]], Any]
                ) -> list[tuple[int, Any]]:
    built = []
    for i, args in enumerate(merged):
        try:
            made = make(args)
        except (ValueError, KeyError, TypeError) as e:
            made = e
        if isinstance(made, list):
            built.extend((i, m) for m in made)
        else:
            built.append((i, made))
    return built


def _create_grouped(board, merged, make, save, label) -> dict[str, Any]:
    """Like ``_create_all`` when one item may build several board items
    (a polyline of tracks): an item is ok when all its parts are."""
    built = _build_each(merged, make)
    flat = [(n, it) for n, (_, it) in enumerate(built)]
    reply = _create_all(board, flat, len(built), False, label)
    per_item: list[list[dict[str, Any]]] = [[] for _ in merged]
    for (i, _), r in zip(built, reply["items"]):
        per_item[i].append(r)
    results = []
    for parts in per_item:
        if not parts:
            results.append({"ok": False, "reason": "nothing to create"})
        elif len(parts) == 1:
            results.append(parts[0])
        else:
            bad = [p.get("reason") for p in parts if not p.get("ok")]
            results.append({"ok": not bad, "created": [p.get("id") for p in parts if p.get("ok")],
                            **({"reason": "; ".join(bad)} if bad else {})})
    if save and any(r.get("ok") for r in results):
        board.save()
    return _summary(results, saved=bool(save and any(r.get("ok") for r in results)))


# -- selecting what to act on -----------------------------------------------------------
def _inside(d: dict[str, Any], box: list[float]) -> bool:
    x1, y1, x2, y2 = min(box[0], box[2]), min(box[1], box[3]), \
        max(box[0], box[2]), max(box[1], box[3])
    pts = [d.get(k) for k in ("position_mm", "start_mm", "end_mm", "center_mm", "top_left_mm",
                              "bottom_right_mm")]
    pts = [p for p in pts if p]
    for poly in d.get("outline", []) or d.get("polygons", []) or []:
        pts.extend(poly.get("outline", []))
    return bool(pts) and all(x1 <= p[0] <= x2 and y1 <= p[1] <= y2 for p in pts)


def _find(board: api.KiCadBoard, *, kinds: Optional[list] = None, ids: Optional[list] = None,
          nets: Optional[list] = None, layers: Optional[list] = None,
          references: Optional[list] = None, selected: bool = False,
          inside_mm: Optional[list] = None, net_classes: Optional[list] = None
          ) -> list[api.Item]:
    """The board items matching every filter given."""
    kinds = [str(k).lower() for k in (kinds or [])]
    bad = [k for k in kinds if k not in api.KINDS]
    if bad:
        raise ValueError(f"unknown kind {bad}; kinds are {sorted(api.KINDS)}")
    if ids:
        found = board.items_by_id([str(i) for i in ids])
    elif selected:
        found = board.selection(*kinds)
    elif nets:
        found = board.items_on_nets([str(n) for n in nets], *(kinds or _NET_KINDS))
    elif net_classes:
        found = board.items_in_classes([str(c) for c in net_classes], *(kinds or _NET_KINDS))
    elif references:
        fps = _footprints(board)
        missing = [r for r in references if r not in fps]
        if missing:
            raise ValueError(f"no footprint with reference {missing}")
        chosen = [fps[r] for r in references]
        if kinds and kinds != ["footprint"]:
            pad_ids = [p for f in chosen for p in _pad_ids(f).values()]
            found = board.items_by_id(pad_ids) if "pad" in kinds else []
            if "footprint" in kinds:
                found = chosen + found
        else:
            found = chosen
    else:
        found = board.items(*(kinds or _ALL_KINDS))
    if kinds:
        found = [i for i in found if i.kind in kinds]
    if layers:
        want = {api.layer_name(board.layer(l)) for l in layers}
        found = [i for i in found if _on_layers(i.read(), want)]
    if inside_mm:
        if len(inside_mm) != 4:
            raise ValueError("inside_mm must be [x1, y1, x2, y2]")
        found = [i for i in found if _inside(i.read(), [float(v) for v in inside_mm])]
    return found


def _on_layers(d: dict[str, Any], want: set) -> bool:
    if d.get("layer") in want:
        return True
    if any(l in want for l in d.get("layers", []) or []):
        return True
    if d.get("kind") == "via":
        return bool({"F.Cu", "B.Cu"} & want) or d.get("start_layer") in want
    return False


def _addressed(board: api.KiCadBoard, spec: dict[str, Any], fps: Optional[dict] = None
               ) -> list[api.Item]:
    """The items one modify/delete entry names: an id, a reference, or a
    reference and pad number."""
    if spec.get("id"):
        found = board.items_by_id([str(spec["id"])])
        if not found:
            raise ValueError(f"no item with id {spec['id']}")
        return found
    if spec.get("reference"):
        fps = fps if fps is not None else _footprints(board)
        f = fps.get(str(spec["reference"]))
        if f is None:
            raise ValueError(f"no footprint with reference {spec['reference']}")
        if spec.get("pad") is not None:
            pad_id = _pad_ids(f).get(str(spec["pad"]))
            if pad_id is None:
                raise ValueError(f"{spec['reference']} has no pad {spec['pad']}")
            return board.items_by_id([pad_id])
        return [f]
    raise ValueError("name the item by id, or by reference (and pad)")


_ADDRESS_KEYS = ("id", "reference", "pad")


def _check_kept(changes: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Fields KiCad did not take as asked, from the item it sent back."""
    off = []
    for key, want in changes.items():
        if key not in after or isinstance(want, dict):
            continue
        got = after[key]
        if isinstance(want, (int, float)) and not isinstance(want, bool) and \
                isinstance(got, (int, float)):
            if abs(float(got) - float(want)) > 1e-4:
                off.append(f"{key} is {got}")
        elif isinstance(want, (list, tuple)) and isinstance(got, (list, tuple)) and \
                len(want) == len(got) and all(isinstance(v, (int, float)) for v in want):
            if any(abs(float(a) - float(b)) > 1e-4 for a, b in zip(want, got)):
                off.append(f"{key} is {got}")
        elif isinstance(want, str) and key in ("net", "layer", "text", "reference", "value",
                                               "name", "number"):
            if str(got) != want and not (key == "layer" and str(got).lower() == want.lower()):
                off.append(f"{key} is {got}")
    return off


def _update_all(board: api.KiCadBoard, pairs: list[tuple[int, Any, dict[str, Any]]],
                count: int, save: bool, label: str) -> dict[str, Any]:
    """Send patched items in one commit; ``pairs`` holds (index, Item or
    error, changes)."""
    results: list[dict[str, Any]] = [{"ok": False, "reason": "not changed"}] * count
    good = [(i, it, ch) for i, it, ch in pairs if isinstance(it, api.Item)]
    for i, it, _ in pairs:
        if not isinstance(it, api.Item):
            results[i] = {"ok": False, "reason": str(it)}
    if good:
        with board.commit(f"eda-agent: {label}"):
            replies = board.update([it for _, it, _ in good])
        for (i, _, changes), r in zip(good, replies):
            if not r["ok"]:
                results[i] = {"ok": False, "reason": f"KiCad refused it: {r['status']} "
                                                     f"{r.get('error', '')}".strip()}
                continue
            after = _brief(r.get("item", {}))
            off = _check_kept(changes, after)
            results[i] = {"ok": not off, **after}
            if off:
                results[i]["reason"] = "KiCad kept a different value: " + "; ".join(off)
    if save and any(r.get("ok") for r in results):
        board.save()
    return _summary(results, saved=bool(save and any(r.get("ok") for r in results)))


# -- geometry helpers -------------------------------------------------------------------
def _seg_point_dist(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _segs_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and o1 * o2 < 0 and o3 * o4 < 0


def _seg_seg_dist(a, b, c, d) -> float:
    if _segs_cross(a, b, c, d):
        return 0.0
    return min(_seg_point_dist(a, c, d), _seg_point_dist(b, c, d),
               _seg_point_dist(c, a, b), _seg_point_dist(d, a, b))


def _point_in_poly(p, poly) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


def _copper_shapes(board: api.KiCadBoard, item: api.Item) -> list[tuple[str, Any, float]]:
    """An item's copper as primitives: ("seg", (a, b), half-width) or
    ("poly", points, 0)."""
    d = item.read()
    if d["kind"] == "track":
        return [("seg", (d["start_mm"], d["end_mm"]), d["width_mm"] / 2)]
    if d["kind"] == "arc":
        pts = [d["start_mm"], d["mid_mm"], d["end_mm"]]
        return [("seg", (pts[0], pts[1]), d["width_mm"] / 2),
                ("seg", (pts[1], pts[2]), d["width_mm"] / 2)]
    if d["kind"] == "via":
        p = d["position_mm"]
        return [("seg", (p, p), (d["diameter_mm"] or 0) / 2)]
    if d["kind"] == "pad":
        layer = next((l for l in d.get("layers", []) if l.endswith(".Cu")), "F.Cu")
        polys = board.pad_polygons([d["id"]], layer).get(d["id"])
        if polys and polys["outline"]:
            return [("poly", polys["outline"], 0.0)]
        p = d["position_mm"]
        return [("seg", (p, p), max(d.get("size_mm") or [0, 0]) / 2)]
    if d["kind"] == "zone":
        out = []
        for layer in api.read_zone_fill(item.message):
            for poly in layer["polygons"]:
                out.append(("poly", poly["outline"], 0.0))
        if not out:
            out = [("poly", o["outline"], 0.0) for o in d.get("outline", [])]
        return out
    if d["kind"] == "shape" and d.get("start_mm") and d.get("end_mm"):
        return [("seg", (d["start_mm"], d["end_mm"]), (d.get("width_mm") or 0) / 2)]
    raise ValueError(f"cannot measure a {d['kind']}")


def _prim_edges(prim) -> list[tuple[Any, Any]]:
    kind, geom, _ = prim
    if kind == "seg":
        return [geom]
    pts = geom
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def _clearance(board, a: api.Item, b: api.Item) -> float:
    best = math.inf
    for pa in _copper_shapes(board, a):
        for pb in _copper_shapes(board, b):
            if pa[0] == "poly" and _point_in_poly(_prim_edges(pb)[0][0], pa[1]):
                return 0.0
            if pb[0] == "poly" and _point_in_poly(_prim_edges(pa)[0][0], pb[1]):
                return 0.0
            core = min(_seg_seg_dist(e1[0], e1[1], e2[0], e2[1])
                       for e1 in _prim_edges(pa) for e2 in _prim_edges(pb))
            best = min(best, core - pa[2] - pb[2])
    return max(0.0, round(best, 6))


def _board_outline_points(board: api.KiCadBoard) -> list[list[float]]:
    """The board edge as one closed outline, from Edge.Cuts graphics."""
    shapes = [s.read() for s in board.items("shape")]
    edge = [s for s in shapes if s["layer"] == "Edge.Cuts"]
    for s in edge:
        if s.get("shape") == "polygon" and s.get("polygons"):
            return s["polygons"][0]["outline"]
        if s.get("shape") == "rectangle":
            (x1, y1), (x2, y2) = s["top_left_mm"], s["bottom_right_mm"]
            return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    segs = [(tuple(s["start_mm"]), tuple(s["end_mm"])) for s in edge
            if s.get("shape") in ("segment", "arc") and s.get("start_mm") and s.get("end_mm")]
    if not segs:
        raise ValueError("the board has no outline on Edge.Cuts")
    chain = [segs[0][0], segs[0][1]]
    rest = segs[1:]
    while rest:
        tail = chain[-1]
        nxt = next((s for s in rest if math.dist(s[0], tail) < 1e-3 or
                    math.dist(s[1], tail) < 1e-3), None)
        if nxt is None:
            break
        rest.remove(nxt)
        chain.append(nxt[1] if math.dist(nxt[0], tail) < 1e-3 else nxt[0])
    if math.dist(chain[0], chain[-1]) < 1e-3:
        chain.pop()
    return [list(p) for p in chain]


def register_kicad_board_tools(mcp) -> None:

    # -- reading ------------------------------------------------------------------
    @mcp.tool()
    async def kicad_query_items(kinds: Optional[list[str]] = None,
                                ids: Optional[list[str]] = None,
                                nets: Optional[list[str]] = None,
                                net_classes: Optional[list[str]] = None,
                                layers: Optional[list[str]] = None,
                                references: Optional[list[str]] = None,
                                selected: bool = False,
                                inside_mm: Optional[list[float]] = None,
                                count_only: bool = False,
                                limit: int = 500) -> dict[str, Any]:
        """Find board items in the open PCB and read them (mm, degrees).

        kinds: footprint, pad, track, arc, via, zone, shape, text, textbox,
        dimension, group, barcode. Filters combine: ids, nets, net_classes,
        references (footprints, or their pads with kinds=["pad"]), layers,
        selected (the editor's selection), inside_mm [x1,y1,x2,y2] (wholly
        inside). count_only returns counts per kind. Each item carries its
        id, which the modify, move, copy and delete tools take."""
        def body():
            board = _board()
            found = _find(board, kinds=kinds, ids=ids, nets=nets, layers=layers,
                          references=references, selected=selected, inside_mm=inside_mm,
                          net_classes=net_classes)
            counts: dict[str, int] = {}
            for i in found:
                counts[i.kind] = counts.get(i.kind, 0) + 1
            out: dict[str, Any] = {"ok": True, "count": len(found), "by_kind": counts}
            if not count_only:
                rows = []
                for i in found[:max(0, int(limit))]:
                    d = i.read()
                    if d["kind"] == "footprint":
                        d["pad_count"] = len(d["pads"])
                    rows.append(_brief(d))
                out["items"] = rows
                if len(found) > len(rows):
                    out["truncated"] = f"{len(found) - len(rows)} more; raise limit or filter"
            return out
        return _guard(body)

    @mcp.tool()
    async def kicad_get_component_pads(references: list[str],
                                       with_polygons: bool = False) -> dict[str, Any]:
        """Pads of footprints by reference: number, net, type, shape, size,
        drill, position, angle, layers, tenting. with_polygons adds each
        pad's copper outline on its first copper layer (mm)."""
        def body():
            board = _board()
            fps = _footprints(board)
            out, missing = {}, []
            for ref in references:
                f = fps.get(ref)
                if f is None:
                    missing.append(ref)
                    continue
                pads = f.read()["pads"]
                if with_polygons and pads:
                    for p in pads:
                        layer = next((l for l in p["layers"] if l.endswith(".Cu")), "F.Cu")
                        p["polygon"] = board.pad_polygons([p["id"]], layer).get(p["id"])
                out[ref] = pads
            reply: dict[str, Any] = {"ok": not missing, "components": out}
            if missing:
                reply["reason"] = f"no footprint with reference {missing}"
            return reply
        return _guard(body)

    @mcp.tool()
    async def kicad_get_connected_items(ids: Optional[list[str]] = None,
                                        reference: str = "", pad: str = "",
                                        kinds: Optional[list[str]] = None) -> dict[str, Any]:
        """Copper connected to the given items (by id, or a footprint's pad
        by reference and pad number), as KiCad's connectivity sees it:
        tracks, arcs, vias, pads and zones touching it physically."""
        def body():
            board = _board()
            start = list(ids or [])
            if reference:
                start += [i.id for i in _addressed(board, {"reference": reference,
                                                           "pad": pad or None})]
            if not start:
                raise ValueError("give ids, or reference and pad")
            found = board.connected(start, *(kinds or ("track", "arc", "via", "pad", "zone")))
            rows = [_brief(i.read()) for i in found]
            return {"ok": True, "count": len(rows), "items": rows}
        return _guard(body)

    @mcp.tool()
    async def kicad_get_zone_fill(ids: Optional[list[str]] = None,
                                  names: Optional[list[str]] = None) -> dict[str, Any]:
        """The filled copper of zones (by id or name; all zones when none
        given): per layer, each polygon's outline and holes in mm, plus its
        area in mm2. Empty when the zone is unfilled: refill first."""
        def body():
            board = _board()
            zones = board.items_by_id(ids) if ids else board.items("zone")
            out = []
            for z in zones:
                d = z.read()
                if names and d.get("name") not in names:
                    continue
                layers = api.read_zone_fill(z.message)
                area = 0.0
                for layer in layers:
                    for poly in layer["polygons"]:
                        area += abs(_area(poly["outline"])) - sum(abs(_area(h)) for h in poly["holes"])
                out.append({"id": d["id"], "name": d.get("name", ""), "net": d.get("net", ""),
                            "filled": d.get("filled"), "area_mm2": round(area, 4), "layers": layers})
            return {"ok": True, "count": len(out), "zones": out}
        return _guard(body)

    @mcp.tool()
    async def kicad_measure_clearance(id_a: str, id_b: str) -> dict[str, Any]:
        """Edge-to-edge copper distance (mm) between two items: tracks,
        arcs, vias, pads (their true outline) and filled zones. 0 means
        touching or overlapping."""
        def body():
            board = _board()
            found = {i.id: i for i in board.items_by_id([id_a, id_b])}
            missing = [i for i in (id_a, id_b) if i not in found]
            if missing:
                raise ValueError(f"no item with id {missing}")
            gap = _clearance(board, found[id_a], found[id_b])
            a, b = found[id_a].read(), found[id_b].read()
            return {"ok": True, "clearance_mm": gap,
                    "a": {"id": id_a, "kind": a["kind"], "net": a.get("net", "")},
                    "b": {"id": id_b, "kind": b["kind"], "net": b.get("net", "")}}
        return _guard(body)

    @mcp.tool()
    async def kicad_check_placement_collision(references: Optional[list[str]] = None,
                                              clearance_mm: float = 0.0) -> dict[str, Any]:
        """Footprints whose bodies overlap (bounding boxes without text,
        grown by clearance_mm), among the references given or all."""
        def body():
            board = _board()
            fps = _footprints(board)
            refs = references or sorted(fps)
            missing = [r for r in refs if r not in fps]
            if missing:
                raise ValueError(f"no footprint with reference {missing}")
            ids = {fps[r].id: r for r in refs}
            boxes = board.bounding_boxes(list(ids), with_text=False)
            rects = []
            for fid, ref in ids.items():
                b = boxes.get(fid)
                if b:
                    rects.append((ref, b["x_mm"], b["y_mm"], b["x_mm"] + b["w_mm"],
                                  b["y_mm"] + b["h_mm"], fps[ref].read()["side"]))
            c = float(clearance_mm)
            hits = []
            for i in range(len(rects)):
                for j in range(i + 1, len(rects)):
                    a, b = rects[i], rects[j]
                    if a[5] != b[5]:
                        continue
                    ox = min(a[3], b[3]) - max(a[1], b[1]) + c
                    oy = min(a[4], b[4]) - max(a[2], b[2]) + c
                    if ox > 0 and oy > 0:
                        hits.append({"a": a[0], "b": b[0], "side": a[5],
                                     "overlap_mm": [round(ox, 4), round(oy, 4)]})
            return {"ok": True, "checked": len(rects), "collision_count": len(hits),
                    "collisions": hits}
        return _guard(body)

    @mcp.tool()
    async def kicad_get_board_view() -> dict[str, Any]:
        """The PCB editor's view state: visible layers, active layer,
        inactive-layer display, net colouring, ratsnest mode, board flip,
        and the grid and drill/place origins (mm)."""
        def body():
            board = _board()
            return {"ok": True, "visible_layers": _visible(board),
                    "active_layer": board.active_layer(), **board.appearance(),
                    "grid_origin_mm": board.origin(False),
                    "drill_origin_mm": board.origin(True)}
        return _guard(body)

    @mcp.tool()
    async def kicad_get_board_text(output_path: str = "") -> dict[str, Any]:
        """The whole board in KiCad's file format as it stands in the editor,
        unsaved edits included. With output_path it is written there and
        only the path and size come back."""
        def body():
            text = _board().as_text()
            if output_path:
                with open(output_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                return {"ok": True, "output": os.path.abspath(output_path), "chars": len(text)}
            return {"ok": True, "chars": len(text), "text": text}
        return _guard(body)

    @mcp.tool()
    async def kicad_get_drc_violations(include_unconnected: bool = True,
                                       limit: int = 300) -> dict[str, Any]:
        """Run KiCad's DRC on the board as it stands in the editor (a copy
        saved with the project's rules, so unsaved edits count). Lists each
        violation with its type, severity, description, position and items,
        and the unconnected items grouped by net."""
        from ..core.kicad_export import run_cli
        import json

        def prepare():
            br = get_kicad_bridge()
            board = br.board()
            cli = br.kicad_cli_path()
            src = br.board_file_path()
            work = tempfile.mkdtemp(prefix="eda_agent_drc_")
            base = os.path.splitext(os.path.basename(src))[0]
            copy = os.path.join(work, base + ".kicad_pcb")
            board.save_copy(copy, overwrite=True)
            for ext in (".kicad_pro", ".kicad_dru"):
                side = os.path.splitext(src)[0] + ext
                if os.path.exists(side):
                    shutil.copy(side, os.path.join(work, base + ext))
            return cli, copy, work

        try:
            cli, copy, work = prepare()
        except _FAILURES as e:
            return {"ok": False, "reason": str(e)}
        report = os.path.join(work, "drc.json")
        try:
            result = await run_cli(cli, ["pcb", "drc", "--format", "json", "--severity-all",
                                         "--output", report, copy])
            if not os.path.exists(report):
                return {"ok": False, "reason": result.get("stderr") or result.get("stdout")
                        or "kicad-cli wrote no DRC report"}
            with open(report, encoding="utf-8") as fh:
                data = json.load(fh)
        except (RuntimeError, OSError, ValueError) as e:
            return {"ok": False, "reason": str(e)}
        finally:
            shutil.rmtree(work, ignore_errors=True)

        def row(v):
            items = v.get("items", []) or []
            return {"type": v.get("type", ""), "severity": v.get("severity", ""),
                    "description": v.get("description", ""),
                    "items": [{"id": it.get("uuid", ""), "description": it.get("description", ""),
                               "position_mm": [it.get("pos", {}).get("x"),
                                               it.get("pos", {}).get("y")]} for it in items]}
        violations = [row(v) for v in data.get("violations", []) or []]
        out: dict[str, Any] = {"ok": True, "violation_count": len(violations),
                               "violations": violations[:limit],
                               "coordinate_units": data.get("coordinate_units", "")}
        if include_unconnected:
            unconnected = data.get("unconnected_items", []) or []
            by_net: dict[str, int] = {}
            for v in unconnected:
                for it in v.get("items", [])[:1]:
                    desc = it.get("description", "")
                    net = desc.split("[", 1)[1].split("]", 1)[0] if "[" in desc else ""
                    by_net[net] = by_net.get(net, 0) + 1
            out["unconnected_count"] = len(unconnected)
            out["unconnected_by_net"] = by_net
            out["unconnected"] = [row(v) for v in unconnected][:limit]
        if len(violations) > limit:
            out["truncated"] = f"{len(violations) - limit} more violations; raise limit"
        return out

    # -- placing ------------------------------------------------------------------
    @mcp.tool()
    async def kicad_place_tracks(items: list[dict[str, Any]], net: str = "",
                                 layer: str = "F.Cu", width_mm: float = 0.25,
                                 save: bool = False) -> dict[str, Any]:
        """Draw copper tracks. Each item: points [[x_mm, y_mm], ...] (a
        polyline), and net, layer, width_mm, locked. One commit."""
        def body():
            board = _board()
            merged = _merge(items, {"net": net, "layer": layer, "width_mm": width_mm})

            def make(a):
                pts = [_point(p, "a point") for p in a.get("points") or []]
                if len(pts) < 2:
                    raise ValueError("a track needs points with at least two [x_mm, y_mm]")
                lay = board.layer(a["layer"])
                return [api.make_track(pts[k], pts[k + 1], float(a["width_mm"]), lay,
                                       a.get("net", ""), bool(a.get("locked")))
                        for k in range(len(pts) - 1)]
            return _create_grouped(board, merged, make, save, "tracks")
        return _guard(body)

    @mcp.tool()
    async def kicad_place_arcs(items: list[dict[str, Any]], net: str = "",
                               layer: str = "F.Cu", width_mm: float = 0.25,
                               save: bool = False) -> dict[str, Any]:
        """Draw copper arc tracks. Each item: start_mm, mid_mm, end_mm, or
        center_mm, radius_mm, start_deg, end_deg (counter-clockwise from
        +X); and net, layer, width_mm. One commit."""
        def body():
            board = _board()
            merged = _merge(items, {"net": net, "layer": layer, "width_mm": width_mm})

            def make(a):
                s, m, e = _arc_points(a)
                return api.make_arc(s, m, e, float(a["width_mm"]), board.layer(a["layer"]),
                                    a.get("net", ""))
            return _create_all(board, _build_each(merged, make), len(merged), save, "arcs")
        return _guard(body)

    @mcp.tool()
    async def kicad_place_vias(items: list[dict[str, Any]], net: str = "",
                               diameter_mm: float = 0.6, drill_mm: float = 0.3,
                               save: bool = False) -> dict[str, Any]:
        """Place vias. Each item: x_mm, y_mm, and net, diameter_mm,
        drill_mm, via_type (through, blind, buried, micro), start_layer,
        end_layer. One commit."""
        def body():
            board = _board()
            merged = _merge(items, {"net": net, "diameter_mm": diameter_mm,
                                    "drill_mm": drill_mm, "via_type": "through",
                                    "start_layer": "F.Cu", "end_layer": "B.Cu"})
            types = {"through": 1, "blind_buried": 2, "micro": 3, "blind": 4, "buried": 5}

            def make(a):
                if a.get("x_mm") is None or a.get("y_mm") is None:
                    raise ValueError("a via needs x_mm and y_mm")
                if a["via_type"] not in types:
                    raise ValueError(f"via_type must be one of {sorted(types)}")
                return api.make_via((float(a["x_mm"]), float(a["y_mm"])),
                                    float(a["diameter_mm"]), float(a["drill_mm"]),
                                    a.get("net", ""), board.layer(a["start_layer"]),
                                    board.layer(a["end_layer"]), types[a["via_type"]])
            return _create_all(board, _build_each(merged, make), len(merged), save, "vias")
        return _guard(body)

    @mcp.tool()
    async def kicad_place_via_array(net: str, pitch_mm: float,
                                    area_mm: Optional[list[float]] = None,
                                    zone_id: str = "", clearance_mm: float = 0.2,
                                    diameter_mm: float = 0.6, drill_mm: float = 0.3,
                                    max_vias: int = 500, dry_run: bool = False,
                                    save: bool = False) -> dict[str, Any]:
        """Stitch vias on a grid at pitch_mm inside area_mm [x1,y1,x2,y2]
        or a zone's outline, on a net. A grid point is skipped when the via
        would come within clearance_mm of a pad, track or via of another net,
        or of the board edge. dry_run lists the points without placing."""
        def body():
            board = _board()
            if zone_id:
                zones = board.items_by_id([zone_id])
                if not zones:
                    raise ValueError(f"no zone with id {zone_id}")
                outline = zones[0].read()["outline"][0]["outline"]
            elif area_mm and len(area_mm) == 4:
                x1, y1, x2, y2 = [float(v) for v in area_mm]
                outline = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            else:
                raise ValueError("give area_mm [x1,y1,x2,y2] or zone_id")
            try:
                edge = _board_outline_points(board)
            except ValueError:  # no outline: only the area bounds the grid
                edge = None
            r = float(diameter_mm) / 2
            obstacles = []
            for it in board.items("track", "arc", "via", "pad"):
                d = it.read()
                if d.get("net") == net:
                    continue
                if d["kind"] == "pad":
                    p, size = d["position_mm"], d.get("size_mm") or [0, 0]
                    obstacles.append(("seg", (p, p), math.hypot(*size) / 2))
                else:
                    obstacles.extend(_copper_shapes(board, it))
            xs = [p[0] for p in outline]
            ys = [p[1] for p in outline]
            step = float(pitch_mm)
            if step <= 0:
                raise ValueError("pitch_mm must be positive")
            points = []
            y = min(ys) + step / 2
            while y < max(ys) and len(points) < max_vias:
                x = min(xs) + step / 2
                while x < max(xs) and len(points) < max_vias:
                    p = (round(x, 4), round(y, 4))
                    if _point_in_poly(p, outline) and \
                            (edge is None or (_point_in_poly(p, edge) and min(
                                _seg_point_dist(p, edge[k], edge[(k + 1) % len(edge)])
                                for k in range(len(edge))) >= r + clearance_mm)) and \
                            all(_seg_point_dist(p, *g) - hw - r >= clearance_mm
                                for _, g, hw in obstacles):
                        points.append(p)
                    x += step
                y += step
            if dry_run or not points:
                return {"ok": bool(points), "placed": 0, "points_mm": [list(p) for p in points],
                        **({} if points else {"reason": "no grid point clears the obstacles"})}
            built = [(i, api.make_via(p, float(diameter_mm), float(drill_mm), net))
                     for i, p in enumerate(points)]
            reply = _create_all(board, built, len(points), save, "via array")
            reply["placed"] = reply["succeeded"]
            reply["items"] = [{"ok": r.get("ok"), "id": r.get("id"), "position_mm":
                               r.get("position_mm")} for r in reply["items"]]
            return reply
        return _guard(body)

    @mcp.tool()
    async def kicad_place_shapes(items: list[dict[str, Any]], layer: str = "F.SilkS",
                                 width_mm: float = 0.15, save: bool = False) -> dict[str, Any]:
        """Draw graphics on any layer. Each item: shape segment (start_mm,
        end_mm), rectangle (start_mm, end_mm corners), circle (center_mm,
        radius_mm), arc (start_mm, mid_mm, end_mm, or center_mm, radius_mm,
        start_deg, end_deg) or polygon (points); and layer, width_mm,
        filled, net. A filled copper rectangle or polygon is a fill. One
        commit."""
        def body():
            board = _board()
            merged = _merge(items, {"shape": "segment", "layer": layer, "width_mm": width_mm})

            def make(a):
                kind = a["shape"]
                lay = board.layer(a["layer"])
                kw: dict[str, Any] = {"filled": bool(a.get("filled")), "net_name": a.get("net", "")}
                if kind == "segment":
                    kw["segment"] = (_point(a.get("start_mm"), "start_mm"),
                                     _point(a.get("end_mm"), "end_mm"))
                elif kind == "rectangle":
                    kw["rectangle"] = (_point(a.get("start_mm"), "start_mm"),
                                       _point(a.get("end_mm"), "end_mm"))
                elif kind == "circle":
                    kw["circle"] = (_point(a.get("center_mm"), "center_mm"), float(a["radius_mm"]))
                elif kind == "arc":
                    kw["arc"] = _arc_points(a)
                elif kind == "polygon":
                    pts = [_point(p, "a point") for p in a.get("points") or []]
                    if len(pts) < 3:
                        raise ValueError("a polygon needs at least 3 points")
                    kw["polygon"] = pts
                else:
                    raise ValueError("shape must be segment, rectangle, circle, arc or polygon")
                return api.make_shape(lay, float(a["width_mm"]), **kw)
            return _create_all(board, _build_each(merged, make), len(merged), save, "shapes")
        return _guard(body)

    @mcp.tool()
    async def kicad_place_text(items: list[dict[str, Any]], layer: str = "F.SilkS",
                               size_mm: float = 1.0, save: bool = False) -> dict[str, Any]:
        """Place board text. Each item: text, x_mm, y_mm, and layer,
        size_mm, thickness_mm, angle_deg, knockout. Back-layer text reads
        mirrored; variables like ${REVISION} expand. One commit."""
        def body():
            board = _board()
            merged = _merge(items, {"layer": layer, "size_mm": size_mm, "thickness_mm": 0.15,
                                    "angle_deg": 0.0})

            def make(a):
                if not a.get("text"):
                    raise ValueError("text is empty")
                if a.get("x_mm") is None or a.get("y_mm") is None:
                    raise ValueError("text needs x_mm and y_mm")
                return api.make_text(str(a["text"]), (float(a["x_mm"]), float(a["y_mm"])),
                                     board.layer(a["layer"]), float(a["size_mm"]),
                                     float(a["thickness_mm"]), float(a["angle_deg"]),
                                     knockout=bool(a.get("knockout")))
            return _create_all(board, _build_each(merged, make), len(merged), save, "text")
        return _guard(body)

    @mcp.tool()
    async def kicad_place_zones(items: list[dict[str, Any]], net: str = "",
                                fill: bool = True, save: bool = False) -> dict[str, Any]:
        """Add copper zones (pours) or rule areas. Each item: points
        [[x_mm, y_mm], ...] or outline "board" (the board edge; inset_mm
        shrinks it); net, layers (default ["F.Cu"]), name, priority,
        clearance_mm, min_width_mm, connection (thermal, solid, none,
        pth_thermal). keepout {copper, vias, tracks, pads, footprints:
        bool} makes a rule area. fill refills the zones afterwards."""
        def body():
            board = _board()
            merged = _merge(items, {"net": net})

            def make(a):
                if a.get("outline") == "board":
                    pts = _board_outline_points(board)
                    inset = float(a.get("inset_mm") or 0)
                    if inset:
                        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                        pts = [[min(xs) + inset, min(ys) + inset], [max(xs) - inset, min(ys) + inset],
                               [max(xs) - inset, max(ys) - inset], [min(xs) + inset, max(ys) - inset]]
                else:
                    pts = [_point(p, "a point") for p in a.get("points") or []]
                if len(pts) < 3:
                    raise ValueError("a zone needs points (at least 3) or outline \"board\"")
                lays = [board.layer(l) for l in (a.get("layers") or ["F.Cu"])]
                return api.make_zone([tuple(p) for p in pts], lays, net_name=a.get("net", ""),
                                     name=a.get("name", ""), priority=int(a.get("priority") or 0),
                                     clearance_mm=a.get("clearance_mm"),
                                     min_width_mm=a.get("min_width_mm"),
                                     connection=a.get("connection"), keepout=a.get("keepout"))
            reply = _create_all(board, _build_each(merged, make), len(merged), False, "zones")
            copper = [r["id"] for r in reply["items"] if r.get("ok") and r.get("zone_type") == "copper"]
            if fill and copper:
                reply["refilled"] = "all zones" if board.refill_zones() else \
                    "refill started but not every zone reads filled yet"
            if save and reply["succeeded"]:
                board.save()
                reply["saved"] = True
            return reply
        return _guard(body)

    @mcp.tool()
    async def kicad_place_dimensions(items: list[dict[str, Any]], layer: str = "Dwgs.User",
                                     save: bool = False) -> dict[str, Any]:
        """Place dimensions. Each item: start_mm, end_mm (centre and a point
        on the circle for radial and center), style (aligned, orthogonal,
        leader, center, radial), height_mm (line offset), layer,
        text_size_mm, unit (mm, mils, in, auto). One commit."""
        def body():
            board = _board()
            merged = _merge(items, {"style": "aligned", "height_mm": 2.0, "layer": layer,
                                    "text_size_mm": 1.0, "unit": "mm"})

            def make(a):
                return api.make_dimension(a["style"], board.layer(a["layer"]),
                                          _point(a.get("start_mm"), "start_mm"),
                                          _point(a.get("end_mm"), "end_mm"),
                                          height_mm=float(a["height_mm"]),
                                          text_size_mm=float(a["text_size_mm"]), unit=a["unit"])
            return _create_all(board, _build_each(merged, make), len(merged), save, "dimensions")
        return _guard(body)

    @mcp.tool()
    async def kicad_set_board_outline(points: Optional[list[list[float]]] = None,
                                      rect_mm: Optional[list[float]] = None,
                                      fit_components_margin_mm: Optional[float] = None,
                                      width_mm: float = 0.1, save: bool = False) -> dict[str, Any]:
        """Replace the board edge: every Edge.Cuts graphic is removed and one
        closed outline drawn. Give points (a polygon), rect_mm [x1,y1,x2,y2],
        or fit_components_margin_mm to wrap all footprints' bodies plus a
        margin."""
        def body():
            board = _board()
            if points:
                pts = [_point(p, "a point") for p in points]
            elif rect_mm and len(rect_mm) == 4:
                x1, y1, x2, y2 = [float(v) for v in rect_mm]
                pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            elif fit_components_margin_mm is not None:
                fps = board.items("footprint")
                boxes = board.bounding_boxes([f.id for f in fps])
                if not boxes:
                    raise ValueError("the board has no footprints to fit")
                m = float(fit_components_margin_mm)
                x1 = min(b["x_mm"] for b in boxes.values()) - m
                y1 = min(b["y_mm"] for b in boxes.values()) - m
                x2 = max(b["x_mm"] + b["w_mm"] for b in boxes.values()) + m
                y2 = max(b["y_mm"] + b["h_mm"] for b in boxes.values()) + m
                pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            else:
                raise ValueError("give points, rect_mm or fit_components_margin_mm")
            if len(pts) < 3:
                raise ValueError("an outline needs at least 3 points")
            old = [s.id for s in board.items("shape") if s.read()["layer"] == "Edge.Cuts"]
            new = api.make_shape(board.layer("Edge.Cuts"), float(width_mm), polygon=pts)
            with board.commit("eda-agent: board outline"):
                if old:
                    removed = board.delete(old)
                created = board.create([new])[0]
            if not created["ok"]:
                raise ValueError(f"KiCad refused the outline: {created['status']}")
            out = {"ok": True, "removed": len(old), "outline_mm": [list(p) for p in pts],
                   "id": created["item"]["id"]}
            if old:
                kept = [r["id"] for r in removed if not r.get("ok")]
                if kept:
                    out.update(ok=False, reason=f"old edge items still there: {kept}")
            if save:
                board.save()
                out["saved"] = True
            return out
        return _guard(body)

    # -- changing -----------------------------------------------------------------
    @mcp.tool()
    async def kicad_modify_items(items: Optional[list[dict[str, Any]]] = None,
                                 where: Optional[dict[str, Any]] = None,
                                 set: Optional[dict[str, Any]] = None,
                                 save: bool = False) -> dict[str, Any]:
        """Change fields of board items in one commit, keeping the rest.

        Each item names its target by id, by reference (a footprint), or by
        reference and pad, with the fields to set beside it. Or give where
        (the filters of kicad_query_items) and set (the fields) to change
        every match. Fields use the names the query returns: net, layer,
        width_mm, start_mm, end_mm, mid_mm, position_mm, locked,
        diameter_mm, drill_mm, via_type, start_layer, end_layer, tented,
        solder_mask_margin_mm; pad number, size_mm, shape, clearance_mm,
        angle_deg; text text, size_mm, thickness_mm, angle_deg, visible,
        mirrored, knockout; footprint reference, value, datasheet,
        description, dnp, exclude_from_bom, exclude_from_pos, rotation_deg,
        reference_visible, reference_size_mm, reference_thickness_mm,
        reference_position_mm, reference_layer (and value_...); zone name,
        priority, layers, outline, clearance_mm, min_width_mm, connection,
        fill_mode, thermal_gap_mm, thermal_spoke_width_mm, keepout; shape
        filled; dimension override_text, prefix, suffix. Each reply item
        is the item as KiCad kept it; a value KiCad changed is reported."""
        def body():
            board = _board()
            pairs: list[tuple[int, Any, dict[str, Any]]] = []
            if where is not None:
                if not set:
                    raise ValueError("where needs set: the fields to change")
                found = _find(board, **where)
                if not found:
                    return {"ok": False, "reason": "nothing matches where", "count": 0}
                for n, it in enumerate(found):
                    try:
                        pairs.append((n, api.patch(it, dict(set), board), dict(set)))
                    except (ValueError, KeyError) as e:
                        pairs.append((n, e, {}))
                return _update_all(board, pairs, len(found), save, "modify")
            merged = _merge(items, {})
            fps = _footprints(board) if any(m.get("reference") for m in merged) else None
            for n, spec in enumerate(merged):
                changes = {k: v for k, v in spec.items() if k not in _ADDRESS_KEYS}
                try:
                    if not changes:
                        raise ValueError("nothing to change")
                    target = _addressed(board, spec, fps)[0]
                    pairs.append((n, api.patch(target, changes, board), changes))
                except (ValueError, KeyError) as e:
                    pairs.append((n, e, changes))
            return _update_all(board, pairs, len(merged), save, "modify")
        return _guard(body)

    @mcp.tool()
    async def kicad_move_items(ids: Optional[list[str]] = None,
                               references: Optional[list[str]] = None,
                               dx_mm: float = 0.0, dy_mm: float = 0.0,
                               rotate_deg: float = 0.0,
                               about_mm: Optional[list[float]] = None,
                               save: bool = False) -> dict[str, Any]:
        """Move items (by id, or footprints by reference) by dx_mm, dy_mm,
        and/or turn them by rotate_deg counter-clockwise about about_mm
        (default: the centre of their positions). One commit."""
        def body():
            board = _board()
            targets = list(board.items_by_id(ids)) if ids else []
            if references:
                fps = _footprints(board)
                missing = [r for r in references if r not in fps]
                if missing:
                    raise ValueError(f"no footprint with reference {missing}")
                targets += [fps[r] for r in references]
            if not targets:
                raise ValueError("no items: give ids or references that exist")
            pairs = []
            if rotate_deg:
                if about_mm:
                    cx, cy = _point(about_mm, "about_mm")
                else:
                    pts = [_anchor(t.read()) for t in targets]
                    cx = sum(p[0] for p in pts) / len(pts)
                    cy = sum(p[1] for p in pts) / len(pts)
            for n, t in enumerate(targets):
                try:
                    moved = t
                    if rotate_deg:
                        moved = api.rotate_about(moved, cx, cy, float(rotate_deg))
                    if dx_mm or dy_mm:
                        moved = api.translate(moved, float(dx_mm), float(dy_mm))
                    pairs.append((n, moved, {}))
                except ValueError as e:
                    pairs.append((n, e, {}))
            return _update_all(board, pairs, len(targets), save, "move")
        return _guard(body)

    @mcp.tool()
    async def kicad_copy_items(ids: list[str], dx_mm: float = 0.0, dy_mm: float = 0.0,
                               count: int = 1, rotate_deg: float = 0.0,
                               about_mm: Optional[list[float]] = None,
                               net: Optional[str] = None, layer: Optional[str] = None,
                               save: bool = False) -> dict[str, Any]:
        """Copy items (tracks, arcs, vias, shapes, text, zones, dimensions)
        count times, each copy offset by dx_mm, dy_mm and turned by
        rotate_deg about about_mm from the one before; net and layer
        override the copies'. One commit."""
        def body():
            board = _board()
            source = board.items_by_id(ids)
            missing = sorted(set(ids) - {i.id for i in source})
            if missing:
                raise ValueError(f"no item with id {missing}")
            bad = [i.id for i in source if i.kind in ("footprint", "pad", "group")]
            if bad:
                raise ValueError(f"footprints, pads and groups are not copied here: {bad}")
            built = []
            n = 0
            for k in range(1, int(count) + 1):
                for it in source:
                    copy = it
                    if rotate_deg:
                        cx, cy = _point(about_mm, "about_mm") if about_mm else _anchor(it.read())
                        copy = api.rotate_about(copy, cx, cy, float(rotate_deg) * k)
                    if dx_mm or dy_mm:
                        copy = api.translate(copy, float(dx_mm) * k, float(dy_mm) * k)
                    changes = {}
                    if net is not None and copy.kind in ("track", "arc", "via", "shape", "zone"):
                        changes["net"] = net
                    if layer is not None and copy.kind in ("track", "arc", "shape", "text"):
                        changes["layer"] = layer
                    if changes:
                        copy = api.patch(copy, changes, board)
                    m = copy.message.copy()
                    m.remove(4 if copy.kind == "shape" else 1)
                    built.append((n, api.Item(copy.type_name, m)))
                    n += 1
            return _create_all(board, built, n, save, "copy")
        return _guard(body)

    @mcp.tool()
    async def kicad_delete_items(ids: Optional[list[str]] = None,
                                 where: Optional[dict[str, Any]] = None,
                                 save: bool = False) -> dict[str, Any]:
        """Delete board items by id, or every item matching where (the
        filters of kicad_query_items, e.g. {"nets": ["SDA"], "kinds":
        ["track", "arc", "via"]} unroutes a net). Each result says whether
        the item is gone, checked after the commit."""
        def body():
            board = _board()
            targets = list(ids or [])
            if where is not None:
                if not where:
                    raise ValueError("where is empty; it would match the whole board")
                targets += [i.id for i in _find(board, **where)]
            if not targets:
                return {"ok": False, "reason": "nothing to delete", "count": 0}
            results = board.delete(targets)
            if save and any(r["ok"] for r in results):
                board.save()
            return _summary(results, saved=bool(save))
        return _guard(body)

    @mcp.tool()
    async def kicad_cleanup_tracks(dry_run: bool = False, save: bool = False) -> dict[str, Any]:
        """Remove zero-length tracks, tracks that duplicate another (same
        ends, layer and net) and vias stacked on another via of the same
        net. dry_run lists them without deleting."""
        def body():
            board = _board()
            doomed: dict[str, str] = {}
            seen_tracks: dict[tuple, str] = {}
            for t in board.items("track"):
                d = t.read()
                s, e = tuple(d["start_mm"]), tuple(d["end_mm"])
                if math.dist(s, e) < 1e-6:
                    doomed[d["id"]] = "zero length"
                    continue
                key = (min(s, e), max(s, e), d["layer"], d["net"])
                if key in seen_tracks:
                    doomed[d["id"]] = f"duplicates {seen_tracks[key]}"
                else:
                    seen_tracks[key] = d["id"]
            seen_vias: dict[tuple, str] = {}
            for v in board.items("via"):
                d = v.read()
                key = (tuple(d["position_mm"]), d["net"])
                if key in seen_vias:
                    doomed[d["id"]] = f"stacked on {seen_vias[key]}"
                else:
                    seen_vias[key] = d["id"]
            found = [{"id": i, "why": w} for i, w in doomed.items()]
            if dry_run or not doomed:
                return {"ok": True, "found": len(found), "items": found, "deleted": 0}
            results = {r["id"]: r for r in board.delete(list(doomed))}
            for f in found:
                f["deleted"] = results.get(f["id"], {}).get("ok", False)
            if save:
                board.save()
            deleted = sum(1 for f in found if f["deleted"])
            return {"ok": deleted == len(found), "found": len(found), "deleted": deleted,
                    "items": found}
        return _guard(body)

    @mcp.tool()
    async def kicad_place_components(items: list[dict[str, Any]],
                                     save: bool = False) -> dict[str, Any]:
        """Move and turn footprints to absolute positions in one commit. Each
        item: reference, x_mm, y_mm, rotation_deg, locked. Pads, graphics
        and text follow. Change side with kicad_flip_components."""
        def body():
            board = _board()
            merged = _merge(items, {})
            fps = _footprints(board)
            pairs = []
            for n, a in enumerate(merged):
                try:
                    f = fps.get(str(a.get("reference", "")))
                    if f is None:
                        raise ValueError(f"no footprint with reference {a.get('reference')}")
                    changes: dict[str, Any] = {}
                    if a.get("x_mm") is not None or a.get("y_mm") is not None:
                        cur = f.read()["position_mm"]
                        changes["position_mm"] = [
                            float(a["x_mm"]) if a.get("x_mm") is not None else cur[0],
                            float(a["y_mm"]) if a.get("y_mm") is not None else cur[1]]
                    if a.get("rotation_deg") is not None:
                        changes["rotation_deg"] = float(a["rotation_deg"])
                    if a.get("locked") is not None:
                        changes["locked"] = bool(a["locked"])
                    if not changes:
                        raise ValueError("nothing to change")
                    pairs.append((n, api.patch(f, changes, board), changes))
                except ValueError as e:
                    pairs.append((n, e, {}))
            return _update_all(board, pairs, len(merged), save, "place components")
        return _guard(body)

    @mcp.tool()
    async def kicad_flip_components(references: list[str], save: bool = False) -> dict[str, Any]:
        """Turn footprints over to the other side with KiCad's own Flip,
        which mirrors their pads, graphics and text. One undo step."""
        def body():
            board = _board()
            fps = _footprints(board)
            missing = [r for r in references if r not in fps]
            if missing:
                raise ValueError(f"no footprint with reference {missing}")
            before = {r: fps[r].read()["side"] for r in references}
            status = board.flip([fps[r].id for r in references])
            if status != "ok":
                raise ValueError(f"KiCad's flip answered {status}")
            now = {i.id: i.read()["side"] for i in board.items_by_id([fps[r].id for r in references])}
            results = []
            for r in references:
                side = now.get(fps[r].id)
                results.append({"reference": r, "ok": side is not None and side != before[r],
                                "side": side, "was": before[r]})
            if save:
                board.save()
            return _summary(results, saved=bool(save))
        return _guard(body)

    @mcp.tool()
    async def kicad_arrange_components(references: list[str], action: str,
                                       save: bool = False) -> dict[str, Any]:
        """Align or distribute footprints with KiCad's own tools. action:
        align_top, align_bottom, align_left, align_right, align_center_x,
        align_center_y, distribute_horizontally_centers,
        distribute_horizontally_gaps, distribute_vertically_centers,
        distribute_vertically_gaps. Returns the new positions."""
        actions = {
            "align_top": "alignTop", "align_bottom": "alignBottom",
            "align_left": "alignLeft", "align_right": "alignRight",
            "align_center_x": "alignCenterX", "align_center_y": "alignCenterY",
            "distribute_horizontally_centers": "distributeHorizontallyCenters",
            "distribute_horizontally_gaps": "distributeHorizontallyGaps",
            "distribute_vertically_centers": "distributeVerticallyCenters",
            "distribute_vertically_gaps": "distributeVerticallyGaps",
        }

        def body():
            if action not in actions:
                raise ValueError(f"action must be one of {sorted(actions)}")
            board = _board()
            fps = _footprints(board)
            missing = [r for r in references if r not in fps]
            if missing:
                raise ValueError(f"no footprint with reference {missing}")
            ids = [fps[r].id for r in references]
            previous = [i.id for i in board.selection()]
            board.deselect()
            board.select(ids)
            try:
                status = board.run_action("pcbnew.AlignAndDistribute." + actions[action])
            finally:
                board.deselect()
                if previous:
                    board.select(previous)
            if status != "ok":
                raise ValueError(f"KiCad answered {status}")
            now = {i.id: i.read() for i in board.items_by_id(ids)}
            positions = {r: now[fps[r].id]["position_mm"] for r in references if fps[r].id in now}
            if save:
                board.save()
            return {"ok": True, "action": action, "positions_mm": positions}
        return _guard(body)

    @mcp.tool()
    async def kicad_snap_components_to_grid(grid_mm: float = 0.5,
                                            references: Optional[list[str]] = None,
                                            skip_locked: bool = True,
                                            save: bool = False) -> dict[str, Any]:
        """Move footprints (the references given, or all) to the nearest
        point of a grid_mm grid measured from the grid origin. One commit."""
        def body():
            board = _board()
            fps = _footprints(board)
            refs = references or sorted(fps)
            ox, oy = board.origin(False)
            g = float(grid_mm)
            if g <= 0:
                raise ValueError("grid_mm must be positive")
            pairs, skipped = [], []
            for n, r in enumerate(refs):
                f = fps.get(r)
                if f is None:
                    pairs.append((n, ValueError(f"no footprint with reference {r}"), {}))
                    continue
                d = f.read()
                if skip_locked and d["locked"]:
                    skipped.append(r)
                    continue
                x, y = d["position_mm"]
                nx = round(ox + round((x - ox) / g) * g, 6)
                ny = round(oy + round((y - oy) / g) * g, 6)
                if abs(nx - x) < 1e-6 and abs(ny - y) < 1e-6:
                    continue
                changes = {"position_mm": [nx, ny]}
                pairs.append((len(pairs), api.patch(f, changes, board), changes))
            if not pairs:
                return {"ok": True, "moved": 0, "skipped_locked": skipped}
            reply = _update_all(board, [(i, it, ch) for i, (_, it, ch) in enumerate(pairs)],
                                len(pairs), save, "snap to grid")
            reply["moved"] = reply["succeeded"]
            reply["skipped_locked"] = skipped
            reply["items"] = [{"ok": r.get("ok"), "reference": r.get("reference"),
                               "position_mm": r.get("position_mm"),
                               **({"reason": r["reason"]} if r.get("reason") else {})}
                              for r in reply["items"]]
            return reply
        return _guard(body)

    # -- editor state ---------------------------------------------------------------
    @mcp.tool()
    async def kicad_select_items(ids: Optional[list[str]] = None,
                                 references: Optional[list[str]] = None,
                                 nets: Optional[list[str]] = None,
                                 mode: str = "replace",
                                 highlight: bool = False) -> dict[str, Any]:
        """Select items in the PCB editor: by id, footprints by reference,
        or every track, via, pad and zone of nets. mode: replace, add,
        remove, clear. highlight also highlights the selected items' nets
        (clear mode clears the highlight)."""
        def body():
            board = _board()
            if mode not in ("replace", "add", "remove", "clear"):
                raise ValueError("mode must be replace, add, remove or clear")
            if mode == "clear":
                board.deselect()
                status = board.run_action("pcbnew.EditorControl.clearHighlight") \
                    if highlight else None
                return {"ok": True, "selected": 0, **({"highlight": status} if status else {})}
            targets = list(ids or [])
            if references:
                fps = _footprints(board)
                missing = [r for r in references if r not in fps]
                if missing:
                    raise ValueError(f"no footprint with reference {missing}")
                targets += [fps[r].id for r in references]
            if nets:
                targets += [i.id for i in board.items_on_nets(nets, "track", "arc", "via", "pad",
                                                               "zone")]
            if not targets:
                raise ValueError("nothing to select: give ids, references or nets")
            if mode == "replace":
                board.deselect()
            if mode == "remove":
                board.deselect(targets)
            else:
                board.select(targets)
            out: dict[str, Any] = {"ok": True, "selected": len(board.selection())}
            if highlight:
                out["highlight"] = board.run_action("pcbnew.EditorControl.highlightNetSelection")
            return out
        return _guard(body)

    @mcp.tool()
    async def kicad_refill_zones(unfill: bool = False) -> dict[str, Any]:
        """Refill every copper zone (KiCad 10.0 cannot refill only some), or
        unfill them all with unfill. Waits for the fill and reports each
        zone's filled state."""
        def body():
            board = _board()
            if unfill:
                status = board.run_action("pcbnew.ZoneFiller.zoneUnfillAll")
                if status != "ok":
                    raise ValueError(f"KiCad answered {status}")
            else:
                board.refill_zones()
            zones = board.items("zone")
            states = [{"id": z.id, "name": z.read().get("name", ""), "filled": z.read().get("filled")}
                      for z in zones if z.read().get("zone_type") == "copper"]
            want = not unfill
            wrong = [s["id"] for s in states if s["filled"] != want]
            out: dict[str, Any] = {"ok": not wrong, "zones": states}
            if wrong:
                out["reason"] = f"{len(wrong)} zones are not {'filled' if want else 'unfilled'}"
            return out
        return _guard(body)

    @mcp.tool()
    async def kicad_set_board_layers(copper_layers: Optional[int] = None,
                                     enable: Optional[list[str]] = None,
                                     disable: Optional[list[str]] = None) -> dict[str, Any]:
        """Set the board's copper layer count and enable or disable the
        technical and user layers (e.g. "User.1", "F.Adhes"). Copper layers
        are added or removed at the inside of the stack."""
        def body():
            board = _board()
            now = board.enabled_layers()
            count = int(copper_layers) if copper_layers else now["copper_layer_count"]
            layers = [l for l in now["layers"] if not l.endswith(".Cu")]
            for l in enable or []:
                name = api.layer_name(board.layer(l))
                if name not in layers:
                    layers.append(name)
            off = {api.layer_name(board.layer(l)) for l in disable or []}
            layers = [l for l in layers if l not in off]
            after = board.set_enabled_layers(count, layers)
            wrong = []
            if after["copper_layer_count"] != count:
                wrong.append(f"copper layers are {after['copper_layer_count']}")
            wrong += [f"{l} is not enabled" for l in layers if l not in after["layers"]]
            out = {"ok": not wrong, **after}
            if wrong:
                out["reason"] = "; ".join(wrong)
            return out
        return _guard(body)

    @mcp.tool()
    async def kicad_set_board_view(visible_layers: Optional[list[str]] = None,
                                   show: Optional[list[str]] = None,
                                   hide: Optional[list[str]] = None,
                                   active_layer: str = "", inactive_layers: str = "",
                                   net_colors: str = "", ratsnest: str = "",
                                   board_flip: str = "") -> dict[str, Any]:
        """Change what the PCB editor shows: visible_layers (the full set),
        or show and hide some; active_layer; inactive_layers (normal,
        dimmed, hidden); net_colors (all, ratsnest, off); ratsnest
        (all_layers, visible_layers); board_flip (normal, flipped_x).
        Returns the view as it is afterwards."""
        def body():
            board = _board()
            if visible_layers is not None or show or hide:
                layers = list(visible_layers) if visible_layers is not None \
                    else _visible(board)
                names = [api.layer_name(board.layer(l)) for l in layers]
                for l in show or []:
                    n = api.layer_name(board.layer(l))
                    if n not in names:
                        names.append(n)
                off = {api.layer_name(board.layer(l)) for l in hide or []}
                board.set_visible_layers([l for l in names if l not in off])
            if active_layer:
                board.set_active_layer(active_layer)
            settings = {k: v for k, v in (("inactive_layers", inactive_layers),
                                          ("net_colors", net_colors), ("ratsnest", ratsnest),
                                          ("board_flip", board_flip)) if v}
            appearance = board.set_appearance(settings) if settings else board.appearance()
            return {"ok": True, "visible_layers": _visible(board),
                    "active_layer": board.active_layer(), **appearance}
        return _guard(body)

    @mcp.tool()
    async def kicad_set_origin(x_mm: float, y_mm: float, kind: str = "grid") -> dict[str, Any]:
        """Set the board's grid origin or its drill/place origin (kind
        "drill"), which position files and Gerbers can be measured from."""
        def body():
            if kind not in ("grid", "drill"):
                raise ValueError("kind must be grid or drill")
            board = _board()
            board.set_origin(float(x_mm), float(y_mm), drill=kind == "drill")
            now = board.origin(kind == "drill")
            ok = abs(now[0] - float(x_mm)) < 1e-6 and abs(now[1] - float(y_mm)) < 1e-6
            return {"ok": ok, "kind": kind, "origin_mm": now,
                    **({} if ok else {"reason": "KiCad kept a different origin"})}
        return _guard(body)

    @mcp.tool()
    async def kicad_set_title_block(title: Optional[str] = None, date: Optional[str] = None,
                                    revision: Optional[str] = None,
                                    company: Optional[str] = None,
                                    comments: Optional[dict[str, str]] = None,
                                    save: bool = False) -> dict[str, Any]:
        """Set the board's title block: title, date, revision, company, and
        comments {"1": "...", ...} for comment1 to comment9. Fields not
        given are kept."""
        def body():
            board = _board()
            values = {k: v for k, v in (("title", title), ("date", date),
                                        ("revision", revision), ("company", company))
                      if v is not None}
            for k, v in (comments or {}).items():
                values["comment" + str(k).replace("comment", "")] = v
            board.set_title_block(values)
            now = board.title_block()
            off = [k for k, v in values.items() if now.get(k) != str(v)]
            if save:
                board.save()
            return {"ok": not off, "title_block": now,
                    **({"reason": f"KiCad kept other values for {off}"} if off else {})}
        return _guard(body)

    @mcp.tool()
    async def kicad_set_net_classes(items: Optional[list[dict[str, Any]]] = None,
                                    delete: Optional[list[str]] = None) -> dict[str, Any]:
        """Add or change net classes (each item: name, and clearance_mm,
        track_width_mm, diff_pair_width_mm, diff_pair_gap_mm,
        via_diameter_mm, via_drill_mm, priority) live in the editor, or
        delete them by name from the .kicad_pro (read on reopening the
        project). Assign nets with kicad_set_design_rules
        netclass_patterns."""
        def body():
            board = _board()
            specs = _merge(items, {}) if items else []
            for s in specs:
                s.pop("constituents", None)
            for s in specs:
                if not s.get("name"):
                    raise ValueError("each net class needs a name")
            if specs:
                board.set_net_classes(specs)
            removed: list[str] = []
            if delete:
                # Not through the API: KiCad 10.0.1 replaces every class object
                # on a delete while the nets keep pointers to the old ones, and
                # the next request that reads a net's class crashes KiCad.
                if "Default" in delete:
                    raise ValueError("the Default class cannot be deleted")
                pro, _ = _project_files()
                data = _read_json(pro)
                ns = data.setdefault("net_settings", {})
                before = ns.get("classes", []) or []
                ns["classes"] = [c for c in before if c.get("name") not in delete]
                removed = sorted({c.get("name") for c in before} - {c.get("name") for c in
                                                                    ns["classes"]})
                ns["netclass_patterns"] = [p for p in ns.get("netclass_patterns") or []
                                           if p.get("netclass") not in delete]
                import json
                with open(pro, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
                    fh.write("\n")
            now = {nc["name"]: nc for nc in board.net_classes()}
            results = []
            for s in specs:
                got = now.get(s["name"])
                off = [] if got is None else _check_kept(
                    {k: v for k, v in s.items() if k != "constituents"}, got)
                results.append({"name": s["name"], "ok": got is not None and not off,
                                **({"reason": "; ".join(off) if got else "not stored"}
                                   if (got is None or off) else {}), **(got or {})})
            for d in delete or []:
                results.append({"name": d, "ok": d in removed,
                                **({} if d in removed else
                                   {"reason": "not a class in the project file"})})
            reply = _summary(results, classes=sorted(now))
            if delete:
                reply["note"] = ("Deleted from the .kicad_pro (with its net patterns). The "
                                 "open editor keeps the class until the project is reopened; "
                                 "do not save in KiCad before that.")
            return reply
        return _guard(body)

    @mcp.tool()
    async def kicad_save_board_copy(path: str, overwrite: bool = False) -> dict[str, Any]:
        """Save a copy of the board as it stands in the editor to path
        (.kicad_pcb). The open board keeps its own file."""
        def body():
            _board().save_copy(os.path.abspath(path), overwrite=bool(overwrite))
            ok = os.path.exists(path)
            return {"ok": ok, "output": os.path.abspath(path),
                    **({} if ok else {"reason": "KiCad wrote no file there"})}
        return _guard(body)

    @mcp.tool()
    async def kicad_revert_board() -> dict[str, Any]:
        """Throw away unsaved board edits and reload the board file."""
        def body():
            _board().revert()
            return {"ok": True, "reverted": True}
        return _guard(body)

    @mcp.tool()
    async def kicad_add_drc_markers(items: list[dict[str, Any]],
                                    severity: str = "warning") -> dict[str, Any]:
        """Put review markers on the board among KiCad's DRC markers. Each
        item: message, x_mm, y_mm, ids (items it concerns), severity
        (warning, error, info)."""
        def body():
            board = _board()
            merged = _merge(items, {"severity": severity})
            results = []
            for a in merged:
                try:
                    if not a.get("message") or a.get("x_mm") is None or a.get("y_mm") is None:
                        raise ValueError("a marker needs message, x_mm and y_mm")
                    marker = board.mark(str(a["message"]), float(a["x_mm"]), float(a["y_mm"]),
                                        a.get("ids") or [], a.get("severity", "warning"))
                    results.append({"ok": bool(marker), "id": marker,
                                    **({} if marker else {"reason": "KiCad returned no marker"})})
                except (ValueError, KiCadApiError) as e:
                    results.append({"ok": False, "reason": str(e)})
            return _summary(results)
        return _guard(body)

    @mcp.tool()
    async def kicad_generate_fab_package(output_dir: str = "",
                                         zip_gerbers: bool = True) -> dict[str, Any]:
        """Write a fabrication package from the board as it stands in the
        editor (unsaved edits included): Gerbers and Excellon drill files
        (zipped with zip_gerbers), a position file (CSV, mm) and a BOM (CSV,
        from the schematic file). Returns every file written and any step
        that failed."""
        from ..core.kicad_export import run_cli

        try:
            br = get_kicad_bridge()
            board = br.board()
            cli = br.kicad_cli_path()
            src = br.board_file_path()
        except _FAILURES as e:
            return {"ok": False, "reason": str(e)}
        base = os.path.splitext(os.path.basename(src))[0]
        out = os.path.abspath(output_dir) if output_dir else tempfile.mkdtemp(
            prefix=f"eda_agent_fab_{base}_")
        gerbers = os.path.join(out, "gerbers")
        assembly = os.path.join(out, "assembly")
        os.makedirs(gerbers, exist_ok=True)
        os.makedirs(assembly, exist_ok=True)
        work = tempfile.mkdtemp(prefix="eda_agent_fab_")
        copy = os.path.join(work, base + ".kicad_pcb")
        try:
            board.save_copy(copy, overwrite=True)
            for ext in (".kicad_pro", ".kicad_dru"):
                side = os.path.splitext(src)[0] + ext
                if os.path.exists(side):
                    shutil.copy(side, os.path.join(work, base + ext))
        except _FAILURES as e:
            shutil.rmtree(work, ignore_errors=True)
            return {"ok": False, "reason": f"could not save the board copy: {e}"}
        sch = os.path.splitext(src)[0] + ".kicad_sch"
        steps = [
            ("gerbers", ["pcb", "export", "gerbers", "--output", gerbers, copy]),
            ("drill", ["pcb", "export", "drill", "--output", gerbers + os.sep, copy]),
            ("position", ["pcb", "export", "pos", "--format", "csv", "--units", "mm",
                          "--output", os.path.join(assembly, base + "-pos.csv"), copy]),
        ]
        if os.path.exists(sch):
            steps.append(("bom", ["sch", "export", "bom", "--output",
                                  os.path.join(assembly, base + "-bom.csv"), sch]))
        failed = {}
        try:
            for name, args in steps:
                try:
                    result = await run_cli(cli, args)
                except RuntimeError as e:
                    failed[name] = str(e)
                    continue
                if result["returncode"] != 0:
                    failed[name] = result.get("stderr") or result.get("stdout") or \
                        f"exit {result['returncode']}"
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if not os.path.exists(sch):
            failed["bom"] = f"no schematic at {sch}"
        archive = ""
        if zip_gerbers and os.listdir(gerbers):
            archive = shutil.make_archive(os.path.join(out, base + "-gerbers"), "zip", gerbers)
        files = sorted(os.path.relpath(os.path.join(d, f), out)
                       for d, _, fs in os.walk(out) for f in fs)
        reply: dict[str, Any] = {"ok": not failed, "output": out, "files": files}
        if archive:
            reply["gerber_zip"] = archive
        if failed:
            reply["failed_steps"] = failed
        return reply

    @mcp.tool()
    async def kicad_get_design_rules() -> dict[str, Any]:
        """The board's design rules from the project files: the board-setup
        constraints (mm) in the .kicad_pro, the net-class patterns that
        assign nets to classes, and each custom rule in the .kicad_dru with
        its name, layer, condition and constraints. Net-class values are
        read live with kicad_get_net_classes."""
        def body():
            pro, dru = _project_files()
            data = _read_json(pro)
            settings = data.get("board", {}).get("design_settings", {})
            ns = data.get("net_settings", {})
            return {"ok": True, "project_file": pro, "rules_file": dru if os.path.exists(dru) else "",
                    "constraints": settings.get("rules", {}),
                    "netclass_patterns": ns.get("netclass_patterns", []) or [],
                    "custom_rules": _read_rules(dru)}
        return _guard(body)

    @mcp.tool()
    async def kicad_set_design_rules(constraints: Optional[dict[str, float]] = None,
                                     rules: Optional[list[dict[str, Any]]] = None,
                                     delete_rules: Optional[list[str]] = None,
                                     netclass_patterns: Optional[list[dict[str, str]]] = None,
                                     replace_patterns: bool = False) -> dict[str, Any]:
        """Write design rules to the project files.

        rules go to the .kicad_dru, read by every DRC run: each {name,
        condition (e.g. "A.NetClass == 'POWER'"), layer (outer, inner,
        F.Cu), constraints (e.g. ["clearance (min 0.3mm)"])}; a rule of the
        same name is replaced. delete_rules removes rules by name.

        constraints (board-setup minimums in mm: min_clearance,
        min_track_width, min_via_diameter...) and netclass_patterns
        [{netclass, pattern}] (added, or all replaced with
        replace_patterns) go to the .kicad_pro. The open editor does not
        reread it: reopen the project before saving in KiCad."""
        def body():
            pro, dru = _project_files()
            out: dict[str, Any] = {"ok": True}
            if rules or delete_rules:
                text = read_rules_text(dru)
                names = {r.get("name") for r in rules or []} | set(delete_rules or [])
                missing = [n for n in delete_rules or []
                           if n not in {r["name"] for r in _read_rules(dru)}]
                text = _drop_rules(text, names)
                for r in rules or []:
                    text = text.rstrip("\n") + "\n\n" + _rule_text(r) + "\n"
                _write_rules(dru, text)
                now = {r["name"] for r in _read_rules(dru)}
                lost = [r["name"] for r in rules or [] if r["name"] not in now]
                kept = [n for n in delete_rules or [] if n in now]
                out.update(rules_file=dru, custom_rules=sorted(now))
                if lost or kept or missing:
                    out.update(ok=False, reason="; ".join(
                        ([f"not written: {lost}"] if lost else []) +
                        ([f"still there: {kept}"] if kept else []) +
                        ([f"no rule named {missing}"] if missing else [])))
            if constraints or netclass_patterns is not None:
                data = _read_json(pro)
                if constraints:
                    rules_obj = data.setdefault("board", {}).setdefault(
                        "design_settings", {}).setdefault("rules", {})
                    unknown = [k for k in constraints if k not in rules_obj]
                    if unknown:
                        raise ValueError(f"unknown constraints {unknown}; known: {sorted(rules_obj)}")
                    rules_obj.update({k: float(v) for k, v in constraints.items()})
                if netclass_patterns is not None:
                    ns = data.setdefault("net_settings", {})
                    pats = [] if replace_patterns else list(ns.get("netclass_patterns") or [])
                    for p in netclass_patterns:
                        if not p.get("netclass") or not p.get("pattern"):
                            raise ValueError("each pattern needs netclass and pattern")
                        entry = {"netclass": p["netclass"], "pattern": p["pattern"]}
                        if entry not in pats:
                            pats.append(entry)
                    ns["netclass_patterns"] = pats
                import json
                with open(pro, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
                    fh.write("\n")
                out.update(project_file=pro, note=(
                    "Written to the .kicad_pro. The open PCB editor keeps its own copy: reopen "
                    "the project to load these, and do not save in KiCad before that, which "
                    "would write its copy over them."))
            if len(out) == 1:
                raise ValueError("nothing to set: give rules, delete_rules, constraints or "
                                 "netclass_patterns")
            return out
        return _guard(body)

    @mcp.tool()
    async def kicad_export_stackup_csv(output_path: str = "") -> dict[str, Any]:
        """Write the board stackup (layer, type, material, thickness, Er,
        loss tangent) to a CSV file and return its path and rows."""
        def body():
            rows = _board().stackup()
            path = output_path or os.path.join(tempfile.mkdtemp(prefix="eda_agent_"),
                                               "stackup.csv")
            import csv
            cols = ["name", "layer", "type", "material", "thickness_mm", "epsilon_r",
                    "loss_tangent"]
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            return {"ok": True, "output": os.path.abspath(path), "rows": rows}
        return _guard(body)


def _project_files() -> tuple[str, str]:
    """The open project's .kicad_pro and .kicad_dru paths."""
    base = os.path.splitext(get_kicad_bridge().board_file_path())[0]
    pro = base + ".kicad_pro"
    if not os.path.isfile(pro):
        raise ValueError(f"no project file at {pro}")
    return pro, base + ".kicad_dru"


def _read_json(path: str) -> dict[str, Any]:
    import json
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_rules_text(path: str) -> str:
    if os.path.isfile(path):
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    return "(version 1)\n"


def _read_rules(path: str) -> list[dict[str, Any]]:
    from ..core.kicad_sexpr_edit import forms, parse
    text = read_rules_text(path)
    out = []
    for span in forms(text, comments=True):
        node = parse(text, span)
        if not node or node[0] != "rule":
            continue
        rule: dict[str, Any] = {"name": node[1], "constraints": []}
        for child in node[2:]:
            if not isinstance(child, list) or not child:
                continue
            if child[0] in ("condition", "layer", "severity"):
                rule[child[0]] = child[1]
            elif child[0] == "constraint":
                rule["constraints"].append(text_of(child[1:]))
        rule["text"] = text[span[0]:span[1]]
        out.append(rule)
    return out


def text_of(node: Any) -> str:
    if isinstance(node, list):
        return " ".join(f"({text_of(n)})" if isinstance(n, list) else str(n) for n in node)
    return str(node)


def _drop_rules(text: str, names: set) -> str:
    from ..core.kicad_sexpr_edit import cut, forms, parse
    for span in reversed(forms(text, comments=True)):
        node = parse(text, span)
        if node and node[0] == "rule" and node[1] in names:
            text = cut(text, span)
    return text


def _rule_text(r: dict[str, Any]) -> str:
    from ..core.kicad_sexpr_edit import q
    if not r.get("name") or not r.get("constraints"):
        raise ValueError("a rule needs name and constraints")
    lines = [f"(rule {q(r['name'])}"]
    if r.get("layer"):
        lines.append(f"\t(layer {r['layer']})")
    if r.get("severity"):
        lines.append(f"\t(severity {r['severity']})")
    if r.get("condition"):
        lines.append(f"\t(condition {q(r['condition'])})")
    for c in r["constraints"]:
        c = str(c).strip()
        if c.startswith("(constraint"):
            lines.append("\t" + c)
        else:
            lines.append(f"\t(constraint {c})")
    return "\n".join(lines) + ")"


def _write_rules(path: str, text: str) -> None:
    from ..core.kicad_sexpr_edit import forms
    forms(text, comments=True)  # unbalanced text raises here, before writing
    if "(version" not in text:
        text = "(version 1)\n" + text
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _visible(board: api.KiCadBoard) -> list[str]:
    """Visible layers among the enabled ones (the editor's set also holds
    layers the board does not use)."""
    enabled = set(board.enabled_layers()["layers"])
    return [l for l in board.visible_layers() if l in enabled]


def _area(points: list) -> float:
    return sum(points[i][0] * points[(i + 1) % len(points)][1] -
               points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points))) / 2 if len(points) > 2 else 0.0


def _anchor(d: dict[str, Any]) -> tuple[float, float]:
    for key in ("position_mm", "center_mm", "start_mm", "top_left_mm"):
        if d.get(key):
            return tuple(d[key])
    for poly in d.get("outline", []) or d.get("polygons", []) or []:
        pts = poly.get("outline") or []
        if pts:
            return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    raise ValueError(f"a {d.get('kind')} has no position")


def _arc_points(a: dict[str, Any]):
    """An arc's start, mid and end from three points or centre, radius
    and angles (degrees counter-clockwise, Y down)."""
    if a.get("start_mm") and a.get("mid_mm") and a.get("end_mm"):
        return (_point(a["start_mm"], "start_mm"), _point(a["mid_mm"], "mid_mm"),
                _point(a["end_mm"], "end_mm"))
    if a.get("center_mm") and a.get("radius_mm") is not None and \
            a.get("start_deg") is not None and a.get("end_deg") is not None:
        cx, cy = _point(a["center_mm"], "center_mm")
        r = float(a["radius_mm"])
        s, e = float(a["start_deg"]), float(a["end_deg"])
        if e <= s:
            e += 360.0
        def at(deg):
            t = math.radians(deg)
            return (round(cx + r * math.cos(t), 6), round(cy - r * math.sin(t), 6))
        return at(s), at((s + e) / 2), at(e)
    raise ValueError("an arc needs start_mm, mid_mm, end_mm, or center_mm, radius_mm, "
                     "start_deg, end_deg")
