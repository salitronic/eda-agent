# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Match a geometry map against a board's free primitives, offline.

pcb_apply_geometry_map moves, reshapes and rewrites thousands of free
primitives in one pass. Altium hands out no handle that is known to survive
a save and reopen, so a map names each object by its CURRENT geometry and
this module finds it in a dump of the open board (PCB_DumpFreePrimitives),
within a tolerance, and writes the edit file the apply pass reads.

Nothing here talks to Altium. The dump gives every free primitive with the
ordinal its kind's iterator yields it at; the edit file gives, per matched
object, that ordinal, the old geometry exactly as dumped and the new one.
The apply pass changes an object only when its old geometry still reads
back as dumped, so a board edited between dump and apply is reported, not
mangled.

Coordinates in a map are mils, absolute (the board's own frame, not
relative to its origin), as the PcbDoc stores them. The dump and the edit
file are in Altium's internal units, 10000 per mil.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterable, Optional

COORD_PER_MIL = 10000

#: How many unmatched or ambiguous entries a report lists per kind.
SAMPLE_LIMIT = 10


@dataclass
class Dump:
    """The free primitives of one board, as PCB_DumpFreePrimitives wrote them."""

    tracks: list = field(default_factory=list)    # (ord, layer, w, x1, y1, x2, y2)
    arcs: list = field(default_factory=list)      # (ord, layer, w, cx, cy, r, sa_text, ea_text)
    vias: list = field(default_factory=list)      # (ord, x, y)
    texts: list = field(default_factory=list)     # (ord, layer, x, y, text)
    fills: list = field(default_factory=list)     # (ord, layer, x1, y1, x2, y2)
    regions: list = field(default_factory=list)   # (ord, layer, n, x0, y0, x1, y1, holes)
    polygons: list = field(default_factory=list)  # (ord, layer, n, vx0, vy0)
    outline_segments: int = 0
    layers: dict = field(default_factory=dict)    # id -> name
    # The walk a narrowed dump used: (kinds, layer); None for the whole board.
    # The apply pass must walk the same set, so its ordinals count the same.
    walk: Optional[tuple] = None


def _int(text: str) -> Optional[int]:
    try:
        return int(text)
    except ValueError:
        return None


def parse_dump(text: str) -> Dump:
    """Read the tab-separated dump. Unreadable lines are skipped."""
    dump = Dump()
    for line in text.splitlines():
        parts = line.split("\t")
        kind = parts[0]
        try:
            if kind == "T" and len(parts) >= 8:
                dump.tracks.append(tuple(int(p) for p in parts[1:8]))
            elif kind == "A" and len(parts) >= 9:
                # Angles stay as the text Altium printed, so the apply pass
                # can check them back exactly.
                dump.arcs.append(tuple(int(p) for p in parts[1:7])
                                 + (parts[7], parts[8]))
            elif kind == "V" and len(parts) >= 4:
                dump.vias.append(tuple(int(p) for p in parts[1:4]))
            elif kind == "X" and len(parts) >= 5:
                dump.texts.append((int(parts[1]), int(parts[2]), int(parts[3]),
                                   int(parts[4]),
                                   "\t".join(parts[5:]) if len(parts) > 5 else ""))
            elif kind == "F" and len(parts) >= 7:
                dump.fills.append(tuple(int(p) for p in parts[1:7]))
            elif kind == "R" and len(parts) >= 9:
                dump.regions.append((int(parts[1]), int(parts[2]), int(parts[3]),
                                     _int(parts[4]), _int(parts[5]),
                                     _int(parts[6]), _int(parts[7]),
                                     int(parts[8])))
            elif kind == "P" and len(parts) >= 6:
                dump.polygons.append((int(parts[1]), int(parts[2]), int(parts[3]),
                                      _int(parts[4]), _int(parts[5])))
            elif kind == "O" and len(parts) >= 2:
                dump.outline_segments = int(parts[1])
            elif kind == "L" and len(parts) >= 3:
                dump.layers[int(parts[1])] = parts[2]
            elif kind == "W" and len(parts) >= 3:
                dump.walk = (parts[1], parts[2])
        except ValueError:
            continue
    return dump


#: Short names a PcbDoc's own records use, to the names Altium reports.
#: Polygons6 stores a pour's layer as TOP or BOTTOM.
_LAYER_ALIASES = {"top": "toplayer", "bottom": "bottomlayer",
                  "keepout": "keepoutlayer"}


def _norm_layer(name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(name).lower())
    key = re.sub(r"^mech(?!anical)", "mechanical", key)
    return _LAYER_ALIASES.get(key, key)


class LayerResolver:
    """A map's layer names to the board's layer ids, by the names the dump gave."""

    def __init__(self, layers: dict):
        self.by_name = {}
        for lid, name in layers.items():
            self.by_name.setdefault(_norm_layer(name), lid)
        self.names = sorted(layers.values())

    def __call__(self, layer: Any) -> Optional[int]:
        if isinstance(layer, int):
            return layer
        return self.by_name.get(_norm_layer(layer))


#: Altium's coordinates are 32-bit integers, so about 214,748 mil is the
#: furthest a value can reach. MEASURED: an arc radius of 662,000 mil raised
#: an EConvertError dialog in the middle of an apply and stopped the script.
_COORD_MAX = 2 ** 31 - 1


class OutOfRange(ValueError):
    """A map value Altium cannot hold, refused before anything is sent."""


def _mil(value: Any) -> int:
    mils = float(value)
    if not math.isfinite(mils) or abs(mils) * COORD_PER_MIL > _COORD_MAX:
        raise OutOfRange(f"{value!r} mil is outside Altium's coordinate range "
                         f"(+/-{_COORD_MAX // COORD_PER_MIL:,} mil)")
    return int(round(mils * COORD_PER_MIL))


def _arc_fits(cx: Any, cy: Any, r: Any) -> None:
    """An arc's extremes are coordinates too: centre plus radius must fit."""
    for c in (cx, cy):
        _mil(abs(float(c)) + abs(float(r)))


def _refuse(rep: "KindReport", i: int, exc: Exception, usage: str) -> None:
    """Count a refused entry, naming the value when it was out of range."""
    rep.refused += 1
    if isinstance(exc, OutOfRange):
        rep.out_of_range += 1
    rep.note("refused", i, str(exc) if isinstance(exc, OutOfRange) else usage)


def _buckets(value: float, tol: float, q: float) -> range:
    return range(math.floor((value - tol) / q), math.floor((value + tol) / q) + 1)


class _Index:
    """Objects bucketed on some numeric fields, for tolerance lookups."""

    def __init__(self, tol: float):
        self.tol = tol
        self.q = max(2.0 * tol, 1.0)
        self.cells: dict = {}

    def add(self, key: tuple, values: tuple, item: Any) -> None:
        cell = key + tuple(math.floor(v / self.q) for v in values)
        self.cells.setdefault(cell, []).append(item)

    def near(self, key: tuple, values: tuple) -> list:
        found = []
        for combo in product(*(_buckets(v, self.tol, self.q) for v in values)):
            found.extend(self.cells.get(key + combo, ()))
        return found


def _angle_close(a: float, b: float, tol: float) -> bool:
    d = (a - b) % 360.0
    return min(d, 360.0 - d) <= tol


def _angle_text(deg: float) -> str:
    return repr(float(deg))


@dataclass
class KindReport:
    entries: int = 0
    matched: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    refused: int = 0
    out_of_range: int = 0
    samples: list = field(default_factory=list)

    def note(self, what: str, index: int, detail: str) -> None:
        if len(self.samples) < SAMPLE_LIMIT:
            self.samples.append({"entry": index, what: detail})

    def as_dict(self) -> dict:
        out = {"entries": self.entries, "matched": self.matched,
               "unmatched": self.unmatched, "ambiguous": self.ambiguous,
               "refused": self.refused}
        if self.out_of_range:
            out["out_of_range"] = self.out_of_range
        if self.samples:
            out["samples"] = self.samples
        return out


def _claim(report: KindReport, picks: dict, pending: dict, entry_index: int,
           candidates: list, what: str) -> None:
    """Record an entry's candidates. None is unmatched; several wait in
    ``pending`` for _settle, keyed by the set of objects that fit."""
    if not candidates:
        report.unmatched += 1
        report.note("unmatched", entry_index, what)
    elif len(candidates) > 1:
        pending.setdefault(tuple(sorted(candidates)), []).append((entry_index, what))
    else:
        picks.setdefault(candidates[0], []).append(entry_index)


def _new_of(entry: dict) -> str:
    """Everything an entry asks for, without the key that found the object."""
    return repr(sorted((k, v) for k, v in entry.items() if k != "key"))


def _settle(report: KindReport, picks: dict, pending: dict, entries: list) -> dict:
    """One entry per object; an object two entries claim is applied by neither.

    Exact duplicates are the exception. A board can hold the same track
    two or three times over, and a map made from it then holds as many
    entries with one key: when n entries that all ask for the same change
    fit the same n objects, which goes where cannot matter, so they are
    paired off. Entries that ask for different changes stay ambiguous.
    """
    for objs, items in pending.items():
        asks = {_new_of(entries[i]) for i, _what in items}
        if len(items) == len(objs) and len(asks) == 1:
            for obj, (i, _what) in zip(objs, items):
                picks.setdefault(obj, []).append(i)
        else:
            report.ambiguous += len(items)
            report.note("ambiguous", items[0][0],
                        f"{items[0][1]}: {len(objs)} board objects fit")
    chosen = {}
    for obj, entries in picks.items():
        if len(entries) == 1:
            chosen[obj] = entries[0]
        else:
            report.ambiguous += len(entries)
            report.note("ambiguous", entries[0],
                        f"{len(entries)} entries name the same board object")
    report.matched = len(chosen)
    return chosen


def _segments_lines(segments: Iterable, report: KindReport, index: int) -> Optional[list]:
    lines = []
    for seg in segments:
        try:
            kind = int(seg.get("kind", 0))
            vx, vy = _mil(seg["vx"]), _mil(seg["vy"])
            if kind == 1:
                _arc_fits(seg["cx"], seg["cy"], seg["r"])
                lines.append(f"s\t1\t{vx}\t{vy}\t{_mil(seg['cx'])}\t{_mil(seg['cy'])}\t"
                             f"{_mil(seg['r'])}\t{_angle_text(seg['sa'])}\t"
                             f"{_angle_text(seg['ea'])}")
            else:
                lines.append(f"s\t0\t{vx}\t{vy}")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            _refuse(report, index, exc, "a segment needs kind, vx, vy (and cx, "
                    "cy, r, sa, ea for an arc)")
            return None
    if len(lines) < 3:
        report.refused += 1
        report.note("refused", index, "an outline needs three or more segments")
        return None
    return lines


#: Marks a "delete" entry once it has joined the move entries of its kind.
_DELETE = "__delete__"


def _deletes_of(geometry_map: dict, kind: str) -> list:
    """The map's "delete" entries of one kind, marked, ready to be matched
    alongside that kind's moves."""
    out = []
    for e in geometry_map.get("delete") or []:
        if isinstance(e, dict) and str(e.get("kind", "")).lower() == kind:
            out.append({**e, _DELETE: True})
    return out


def build_edits(geometry_map: dict, dump: Dump, tolerance_mil: float = 0.01,
                angle_tolerance_deg: float = 1e-4) -> tuple[list, dict]:
    """Match every map entry to one board object and write the edit lines.

    Returns ``(lines, report)``. Lines are grouped by kind in the order the
    apply pass reads them (T A V X F R P O) and sorted by ordinal. An entry
    that fits no object, fits several, or names an object another entry
    also names is left out and counted, never guessed.
    """
    tol = tolerance_mil * COORD_PER_MIL
    layer_of = LayerResolver(dump.layers)
    report: dict = {}
    out: dict = {k: [] for k in "TAVXFRPO"}
    deleted = {"track": 0, "arc": 0, "region": 0}

    def bad_layer(rep: KindReport, i: int, layer: Any) -> None:
        rep.refused += 1
        rep.note("refused", i, f"layer {layer!r} is not on this board "
                 f"(known: {', '.join(layer_of.names)})")

    # Tracks: layer and width exact-ish, endpoints in either order.
    rep = KindReport()
    idx = _Index(tol)
    for obj in dump.tracks:
        _, lyr, w, x1, y1, x2, y2 = obj
        idx.add((lyr,), (w, x1, y1, x2, y2), obj)
    picks: dict = {}
    pending: dict = {}
    # Deletes join the moves in one pass, so an object a move and a delete
    # both name comes out ambiguous instead of being moved and then lost.
    entries = (list(geometry_map.get("tracks") or [])
               + _deletes_of(geometry_map, "track"))
    rep.entries = len(entries)
    # (object, entry) pairs that fit only with the key's ends swapped; the
    # new ends are swapped with them.
    flipped = set()
    for i, e in enumerate(entries):
        try:
            lyr = layer_of(e["layer"])
            w = float(e["width"]) * COORD_PER_MIL
            k = [float(v) * COORD_PER_MIL for v in e["key"]]
            new = [0, 0, 0, 0] if e.get(_DELETE) else [_mil(v) for v in e["new"]]
            if not e.get(_DELETE) and e.get("new_width") is not None:
                _mil(e["new_width"])
            assert len(k) == 4 and len(new) == 4
        except (KeyError, TypeError, ValueError, AssertionError) as exc:
            _refuse(rep, i, exc, "needs layer, width, key [x1,y1,x2,y2], new")
            continue
        if lyr is None:
            bad_layer(rep, i, e.get("layer"))
            continue
        cands = []
        for flip, kk in ((False, k), (True, [k[2], k[3], k[0], k[1]])):
            for obj in idx.near((lyr,), (w, *kk)):
                if obj in cands:
                    continue
                if all(abs(a - b) <= tol for a, b in zip(obj[2:], (w, *kk))):
                    cands.append(obj)
                    if flip:
                        flipped.add((obj, i))
        _claim(rep, picks, pending, i, cands, f"track on {e['layer']} at {e['key']}")
    for obj, i in _settle(rep, picks, pending, entries).items():
        e = entries[i]
        o, lyr, w, x1, y1, x2, y2 = obj
        old = f"T\t{o}\t{x1}\t{y1}\t{x2}\t{y2}\t{lyr}\t{w}"
        if e.get(_DELETE):
            out["T"].append((o, old + "\tDEL"))
            deleted["track"] += 1
            continue
        new = [_mil(v) for v in e["new"]]
        if (obj, i) in flipped:
            new = [new[2], new[3], new[0], new[1]]
        nw = _mil(e["new_width"]) if e.get("new_width") is not None else obj[2]
        out["T"].append((o, old + f"\t{new[0]}\t{new[1]}\t{new[2]}\t{new[3]}\t{nw}"))
    report["tracks"] = rep

    # Arcs: centre, radius and width bucketed, angles checked modulo 360.
    rep = KindReport()
    idx = _Index(tol)
    for obj in dump.arcs:
        _, lyr, w, cx, cy, r, _sa, _ea = obj
        idx.add((lyr,), (w, cx, cy, r), obj)
    picks, pending = {}, {}
    entries = (list(geometry_map.get("arcs") or [])
               + _deletes_of(geometry_map, "arc"))
    rep.entries = len(entries)
    for i, e in enumerate(entries):
        try:
            lyr = layer_of(e["layer"])
            w = float(e["width"]) * COORD_PER_MIL
            cx, cy, r = (float(v) * COORD_PER_MIL for v in e["key"][:3])
            sa, ea = float(e["key"][3]), float(e["key"][4])
            assert e.get(_DELETE) or len(e["new"]) == 5
            if not e.get(_DELETE):
                n = e["new"]
                _arc_fits(n[0], n[1], n[2])
                float(n[3]), float(n[4])
                if e.get("new_width") is not None:
                    _mil(e["new_width"])
        except (KeyError, TypeError, ValueError, IndexError, AssertionError) as exc:
            _refuse(rep, i, exc, "needs layer, width, key [cx,cy,r,start,end], new")
            continue
        if lyr is None:
            bad_layer(rep, i, e.get("layer"))
            continue
        cands = [obj for obj in idx.near((lyr,), (w, cx, cy, r))
                 if all(abs(a - b) <= tol for a, b in zip(obj[2:6], (w, cx, cy, r)))
                 and _angle_close(float(obj[6]), sa, angle_tolerance_deg)
                 and _angle_close(float(obj[7]), ea, angle_tolerance_deg)]
        _claim(rep, picks, pending, i, cands, f"arc on {e['layer']} at {e['key']}")
    for obj, i in _settle(rep, picks, pending, entries).items():
        e = entries[i]
        o, lyr, w, cx, cy, r, sa, ea = obj
        old = f"A\t{o}\t{cx}\t{cy}\t{r}\t{sa}\t{ea}\t{lyr}\t{w}"
        if e.get(_DELETE):
            out["A"].append((o, old + "\tDEL"))
            deleted["arc"] += 1
            continue
        n = e["new"]
        nw = _mil(e["new_width"]) if e.get("new_width") is not None else obj[2]
        out["A"].append((o, old + f"\t{_mil(n[0])}\t{_mil(n[1])}\t{_mil(n[2])}\t"
                                  f"{_angle_text(n[3])}\t{_angle_text(n[4])}\t{nw}"))
    report["arcs"] = rep

    # Vias: position only.
    rep = KindReport()
    idx = _Index(tol)
    for obj in dump.vias:
        idx.add((), obj[1:3], obj)
    picks, pending = {}, {}
    entries = geometry_map.get("vias") or []
    rep.entries = len(entries)
    for i, e in enumerate(entries):
        try:
            x, y = (float(v) * COORD_PER_MIL for v in e["key"][:2])
            assert len(e["new"]) == 2
            _mil(e["new"][0]), _mil(e["new"][1])
        except (KeyError, TypeError, ValueError, IndexError, AssertionError) as exc:
            _refuse(rep, i, exc, "needs key [x,y], new [x,y]")
            continue
        cands = [obj for obj in idx.near((), (x, y))
                 if abs(obj[1] - x) <= tol and abs(obj[2] - y) <= tol]
        _claim(rep, picks, pending, i, cands, f"via at {e['key']}")
    for obj, i in _settle(rep, picks, pending, entries).items():
        n = entries[i]["new"]
        o, x, y = obj
        out["V"].append((o, f"V\t{o}\t{x}\t{y}\t{_mil(n[0])}\t{_mil(n[1])}"))
    report["vias"] = rep

    # Texts: layer, position and the string itself.
    rep = KindReport()
    idx = _Index(tol)
    for obj in dump.texts:
        idx.add((obj[1],), obj[2:4], obj)
    picks, pending = {}, {}
    entries = geometry_map.get("texts") or []
    rep.entries = len(entries)
    for i, e in enumerate(entries):
        try:
            lyr = layer_of(e["layer"])
            x, y = (float(v) * COORD_PER_MIL for v in e["key"][:2])
            text = str(e["key"][2]) if len(e["key"]) > 2 else None
            n = e["new"]
            assert len(n) >= 2
            _mil(n[0]), _mil(n[1])
        except (KeyError, TypeError, ValueError, IndexError, AssertionError) as exc:
            _refuse(rep, i, exc, "needs layer, key [x,y,text], new [x,y,text]")
            continue
        if lyr is None:
            bad_layer(rep, i, e.get("layer"))
            continue
        new_text = str(n[2]) if len(n) > 2 and n[2] is not None else None
        if new_text is not None and any(c in new_text for c in "\t\r\n"):
            rep.refused += 1
            rep.note("refused", i, "the new text holds a tab or line break")
            continue
        cands = [obj for obj in idx.near((lyr,), (x, y))
                 if abs(obj[2] - x) <= tol and abs(obj[3] - y) <= tol
                 and (text is None or obj[4] == text)]
        _claim(rep, picks, pending, i, cands, f"text {text!r} on {e['layer']} at {e['key'][:2]}")
    for obj, i in _settle(rep, picks, pending, entries).items():
        n = entries[i]["new"]
        new_text = str(n[2]) if len(n) > 2 and n[2] is not None else None
        o, lyr, x, y, _t = obj
        tail = f"1\t{new_text}" if new_text is not None else "0\t"
        out["X"].append((o, f"X\t{o}\t{x}\t{y}\t{lyr}\t{_mil(n[0])}\t{_mil(n[1])}\t{tail}"))
    report["texts"] = rep

    # Fills: layer and both corners.
    rep = KindReport()
    idx = _Index(tol)
    for obj in dump.fills:
        idx.add((obj[1],), obj[2:6], obj)
    picks, pending = {}, {}
    entries = geometry_map.get("fills") or []
    rep.entries = len(entries)
    for i, e in enumerate(entries):
        try:
            lyr = layer_of(e["layer"])
            k = [float(v) * COORD_PER_MIL for v in e["key"][:4]]
            assert len(k) == 4 and len(e["new"]) == 4
            [_mil(v) for v in e["new"]]
        except (KeyError, TypeError, ValueError, AssertionError) as exc:
            _refuse(rep, i, exc, "needs layer, key [x1,y1,x2,y2], new")
            continue
        if lyr is None:
            bad_layer(rep, i, e.get("layer"))
            continue
        cands = [obj for obj in idx.near((lyr,), tuple(k))
                 if all(abs(a - b) <= tol for a, b in zip(obj[2:6], k))]
        _claim(rep, picks, pending, i, cands, f"fill on {e['layer']} at {e['key']}")
    for obj, i in _settle(rep, picks, pending, entries).items():
        n = [_mil(v) for v in entries[i]["new"]]
        o, lyr, x1, y1, x2, y2 = obj
        out["F"].append((o, f"F\t{o}\t{x1}\t{y1}\t{x2}\t{y2}\t{lyr}\t"
                            f"{n[0]}\t{n[1]}\t{n[2]}\t{n[3]}"))
    report["fills"] = rep

    # Regions: layer, vertex count, hole count and the first vertex, which
    # the dump gives at contour index 0 and 1.
    rep = KindReport()
    idx = _Index(tol)
    for obj in dump.regions:
        o, lyr, n, x0, y0, x1, y1, holes = obj
        # Which index holds the first vertex is the contour's base, and the
        # apply pass writes from it. MEASURED: writing 1..n into a contour
        # that counts from 0 moved 124 of 228 regions to the wrong shape.
        for base, fx, fy in ((0, x0, y0), (1, x1, y1)):
            if fx is not None and fy is not None:
                idx.add((lyr, n, holes), (fx, fy), (obj, fx, fy, base))
    picks, pending = {}, {}
    first_of: dict = {}
    entries = (list(geometry_map.get("regions") or [])
               + _deletes_of(geometry_map, "region"))
    rep.entries = len(entries)
    for i, e in enumerate(entries):
        try:
            lyr = layer_of(e["layer"])
            key = e["key"]
            n, holes = int(key["n"]), int(key.get("holes", 0))
            fx, fy = (float(v) * COORD_PER_MIL for v in key["first"][:2])
            if not e.get(_DELETE):
                contour = e["contour"]
                new_holes = e.get("holes") or []
                assert len(contour) >= 3 and len(new_holes) == holes
        except (KeyError, TypeError, ValueError, IndexError, AssertionError):
            rep.refused += 1
            rep.note("refused", i, "needs layer, key {n, first, holes}, contour "
                     "(3+ points) and one new hole per old hole")
            continue
        if lyr is None:
            bad_layer(rep, i, e.get("layer"))
            continue
        cands = []
        for obj, ox, oy, base in idx.near((lyr, n, holes), (fx, fy)):
            if abs(ox - fx) <= tol and abs(oy - fy) <= tol and obj not in cands:
                cands.append(obj)
                first_of[(obj, i)] = (ox, oy, base)
        _claim(rep, picks, pending, i, cands, f"region on {e['layer']} with {n} vertices "
               f"starting {key['first']}")
    for obj, i in _settle(rep, picks, pending, entries).items():
        e = entries[i]
        o, lyr, n, *_rest, holes = obj
        ox, oy, base = first_of[(obj, i)]
        if e.get(_DELETE):
            out["R"].append((o, f"R\t{o}\t{lyr}\t{n}\t{ox}\t{oy}\t{holes}\tDEL"))
            deleted["region"] += 1
            continue
        try:
            main = [(_mil(p[0]), _mil(p[1])) for p in e["contour"]]
            hole_pts = [[(_mil(p[0]), _mil(p[1])) for p in h] for h in e["holes"] or []]
        except (TypeError, ValueError, IndexError) as exc:
            rep.matched -= 1
            _refuse(rep, i, exc, "contour and holes must be [x, y] points")
            continue
        block = [f"R\t{o}\t{lyr}\t{n}\t{ox}\t{oy}\t{holes}\t{len(main)}\t{len(hole_pts)}"
                 f"\t{base}"]
        block += [f"p\t{x}\t{y}" for x, y in main]
        for h in hole_pts:
            block.append(f"h\t{len(h)}")
            block += [f"p\t{x}\t{y}" for x, y in h]
        out["R"].append((o, "\n".join(block)))
    report["regions"] = rep

    # Polygons: by layer and first vertex, not by index, so a different
    # iteration order cannot reshape the wrong pour.
    rep = KindReport()
    idx = _Index(tol)
    for obj in dump.polygons:
        o, lyr, n, vx, vy = obj
        if vx is not None and vy is not None:
            idx.add((lyr,), (vx, vy), obj)
    picks, pending = {}, {}
    entries = geometry_map.get("polygons") or []
    rep.entries = len(entries)
    for i, e in enumerate(entries):
        try:
            lyr = layer_of(e["layer"])
            fx, fy = (float(v) * COORD_PER_MIL for v in e["key_first"][:2])
            assert e["segments"]
        except (KeyError, TypeError, ValueError, IndexError, AssertionError):
            rep.refused += 1
            rep.note("refused", i, "needs layer, key_first [vx,vy], segments")
            continue
        if lyr is None:
            bad_layer(rep, i, e.get("layer"))
            continue
        cands = [obj for obj in idx.near((lyr,), (fx, fy))
                 if abs(obj[3] - fx) <= tol and abs(obj[4] - fy) <= tol]
        _claim(rep, picks, pending, i, cands, f"polygon on {e['layer']} starting {e['key_first']}")
    for obj, i in _settle(rep, picks, pending, entries).items():
        segs = _segments_lines(entries[i]["segments"], rep, i)
        if segs is None:
            rep.matched -= 1
            continue
        o, lyr, n, vx, vy = obj
        out["P"].append((o, "\n".join([f"P\t{o}\t{lyr}\t{n}\t{vx}\t{vy}\t{len(segs)}"]
                                      + segs)))
    report["polygons"] = rep

    # The board outline, whole.
    rep = KindReport()
    outline = geometry_map.get("board_outline")
    if outline:
        rep.entries = 1
        segs = outline.get("segments") if isinstance(outline, dict) else outline
        lines = _segments_lines(segs or [], rep, 0)
        if lines is not None:
            rep.matched = 1
            out["O"].append((0, "\n".join(
                [f"O\t{dump.outline_segments}\t{len(lines)}"] + lines)))
    report["board_outline"] = rep

    # New free tracks, arcs, regions, through vias and polygon pours, each
    # with an optional net, after everything that is matched. Lines go out
    # in entry order, so the n-th C line is the n-th accepted entry.
    rep = KindReport()
    creates = geometry_map.get("create") or []
    rep.entries = len(creates)
    for i, e in enumerate(creates):
        try:
            kind = str(e.get("kind", "")).lower()
            net = str(e.get("net") or "")
            assert not any(c in net for c in "\t\r\n")
            if kind == "via":
                # A through via, TopLayer to BottomLayer, as pcb_place_vias
                # makes one. The layer field carries MultiLayer.
                size, hole = _mil(e["size"]), _mil(e["hole"])
                assert 0 < hole < size
                x, y = _mil(e["x"]), _mil(e["y"])
                out.setdefault("C", []).append((i, "C\tV\tMultiLayer\t{}\t{}\t{}\t{}\t{}".format(
                    size, x, y, hole, net)))
                rep.matched += 1
                continue
            layer = str(e["layer"]).strip()
            assert kind in ("track", "arc", "region", "polygon") and layer
            assert not any(c in layer for c in "\t\r\n")
            if kind == "polygon":
                name = str(e.get("name") or "")
                assert not any(c in name for c in "\t\r\n")
                xy = [(_mil(q[0]), _mil(q[1])) for q in e["contour"]]
                if len(xy) > 3 and xy[-1] == xy[0]:
                    xy.pop()
                assert len(xy) >= 3
                order = int(e.get("order", -1))
                over = "1" if e.get("pour_over", True) else "0"
                pts = "\t".join(f"{x}\t{y}" for x, y in xy)
                out.setdefault("C", []).append((i, "C\tP\t" + "\t".join(
                    [layer, str(len(xy)), pts, str(order), over, name, net])))
                if layer_of(layer) is None:
                    rep.note("layer_not_in_dump", i, layer)
                rep.matched += 1
                continue
            # MEASURED: copies on the keep-out layer carry IsKeepout, and a
            # track created there without it read false, so DRC would treat
            # it differently. Default to the original's flag.
            keep = e.get("keepout", _norm_layer(layer) == "keepoutlayer")
            flag = "1" if keep else "0"
            if kind == "region":
                pts = e["contour"]
                xy = [(_mil(p[0]), _mil(p[1])) for p in pts]
                # A closing vertex equal to the first is dropped; Altium
                # closes a region's contour itself.
                if len(xy) > 3 and xy[-1] == xy[0]:
                    xy.pop()
                assert len(xy) >= 3
                tear = "1" if e.get("teardrop") else "0"
                line = "C\tR\t{}\t{}\t{}\t{}\t{}\t{}".format(
                    layer, len(xy), tear, flag,
                    "\t".join(f"{x}\t{y}" for x, y in xy), net)
                if layer_of(layer) is None:
                    rep.note("layer_not_in_dump", i, layer)
                out.setdefault("C", []).append((i, line))
                rep.matched += 1
                continue
            w = _mil(e["width"])
            g = e["geom"]
            assert w > 0
            if kind == "track":
                assert len(g) == 4
                line = "C\tT\t{}\t{}\t{}\t{}\t{}".format(
                    layer, w, "\t".join(str(_mil(v)) for v in g), flag, net)
            else:
                assert len(g) == 5 and float(g[2]) > 0
                _arc_fits(g[0], g[1], g[2])
                line = (f"C\tA\t{layer}\t{w}\t{_mil(g[0])}\t{_mil(g[1])}\t"
                        f"{_mil(g[2])}\t{_angle_text(g[3])}\t{_angle_text(g[4])}\t{flag}\t{net}")
        except (KeyError, TypeError, ValueError, IndexError, AssertionError) as exc:
            _refuse(rep, i, exc, "needs kind track|arc, layer, width > 0 and "
                    "geom [x1,y1,x2,y2] or [cx,cy,r,start,end]; or kind "
                    "region|polygon, layer and contour [[x,y],...] of 3+ "
                    "points; or kind via, x, y, size and hole < size")
            continue
        if layer_of(layer) is None:
            # Created by name in Altium, which knows every layer the board
            # has; the dump only lists layers with free primitives on them.
            rep.note("layer_not_in_dump", i, layer)
        out.setdefault("C", []).append((i, line))
        rep.matched += 1
    if creates:
        report["create"] = rep

    ordered = []
    if dump.walk:
        # The narrowed walk goes first: the apply pass walks the same set.
        ordered.append("W\t%s\t%s" % dump.walk)
    for kind in "TAVXFRPOC":
        for _o, text in sorted(out.get(kind, []), key=lambda t: t[0]):
            ordered.extend(text.split("\n"))
    summary = {k: v.as_dict() for k, v in report.items()}
    if "create" in summary:
        # Altium names a refused create by its ordinal among the C lines;
        # this turns that back into the entry of the map.
        summary["create"]["line_entries"] = [i for i, _ in sorted(
            out.get("C", []), key=lambda t: t[0])]
    dels = geometry_map.get("delete") or []
    if dels:
        summary["delete"] = {"entries": len(dels), "matched": sum(deleted.values()),
                             "tracks": deleted["track"], "arcs": deleted["arc"],
                             "regions": deleted["region"]}
    comps = geometry_map.get("components") or []
    if comps:
        summary["components"] = {
            "entries": len(comps), "applied": 0,
            "note": "components are not moved here; send them to "
                    "pcb_move_components"}
    return ordered, summary
