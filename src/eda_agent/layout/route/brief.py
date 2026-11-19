# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The routing brief: what the AI decides about a net, which the router obeys.

The router is a fast hand for mechanical work, not the designer. The AI
reads the datasheets, the standards and the application notes, and says
how the nets that matter are to be routed; the router lays them that
way, or says which rule it could not meet and why. It never bends a rule
to finish.

A brief is a list of entries, one per net or group of nets:

* ``nets``: the nets the entry rules (each net in one entry at most).
* ``kind``: what the nets are, in the AI's words (differential, clock,
  switching, high_current, sense, analog, signal...).
* ``layers``: the copper layers they may use. Every layer one of their
  surface pads is on must be among them.
* ``width``: the track width, mils. Not under the board's width rule:
  change the rule in Altium first, so DRC agrees.
* ``region``: polygons, mils, the route must stay inside. A net's own
  pads are always open to it.
* ``keep_out``: polygons the route must not enter.
* ``max_vias``: the most vias the net may take.
* ``min_length`` / ``max_length``: the net's routed copper length, mils.
* ``match`` and ``skew``: nets whose entries name the same match group are
  to be the same length within ``skew`` mils.
* ``reference``: the net of the plane that must lie on a layer next to
  every track of the net (its return path), e.g. GND.
* ``impedance`` (single-ended ohms), or ``diff_impedance`` with the pair's
  ``gap`` (mils); ``impedance_tol`` in percent (default 10). The check
  gives the width each layer needs on the board's stack; the entry's width
  must meet the target on every layer it may use.
* ``apart_from``: nets the entry's nets are to keep away from (a switch
  node, a clock, a noisy bus), with ``spacing``, the edge-to-edge gap in
  mils on the same layer, which the router keeps from those nets' tracks,
  vias, pads and pours as it keeps a clearance; and ``max_parallel``, the
  most mils of the net's track that may run within ``parallel_gap`` of
  them on the same layer, which the review measures. A net class name in
  ``apart_from`` stands for its nets. ``max_broadside``: the most mils
  that may run over or under them on the copper layer next door, within
  ``parallel_gap`` across, with no plane between.
* ``guard``: the net (GND) whose copper must run beside the net's tracks
  on both sides, within ``guard_gap`` mils of their edges; at most
  ``max_unguarded`` mils (default 0) may go without, the ends at its own
  pads apart.
* ``terminate``: the part that terminates the net (R7), at its ``end``
  (default: no load past it) or at its ``source`` (beside the driver),
  ``driver`` the pad that drives it (U1.12); ``max_stub``, the most mils
  of copper from the trunk (driver to the far end) to any other pad, or
  for a source termination from the driver to the part.
* ``return_via``: mils. Where the net changes layer through a via, its
  return current changes plane too; a via of the ``reference`` net must
  stand within this distance of each of the net's vias, which the review
  measures.
* ``current``: the current the net carries, amps, and ``temp_rise``, the
  rise allowed over ambient, degrees C (default TEMP_RISE). The check gives
  the width IPC-2221's closed form needs on each layer the entry may use,
  for that layer's copper, and the entry's width must meet it; the review
  measures every track of the net against it.
* ``source``: where the rules come from: a datasheet and its page, a
  standard and its clause (IPC-2221, IPC-2141, IPC-2152), an application
  note. Required for a net the router finds critical.
* ``reason``: why, in a sentence, for the review.

The router lays only what the AI has decided: a net is routed once an
entry rules it with its ``layers`` and a ``reason`` (one entry may decide
a group of plain nets). A net the router finds critical (a differential
pair, a clock, a switching node, a high-current path, an antenna feed)
also needs the entry's source: the reply names it, says why it is
critical and what to read.

A brief also holds ``areas``: places on the board no net may route through
(an antenna's keep-out, a creepage gap, a mounting area), on the layers
named or all, except the nets an area ``allow`` s (an antenna's feed). Each
gives its source too.

And ``barriers``: isolation barriers, each a side's nets (or net classes)
kept ``creepage`` along the surface and ``clearance`` through air from
another side's, or from every other net; the router keeps the larger on
every layer, and the review measures creepage on the outer layers round
any slot, clearance on every layer.

And ``assembly``: ``via_to_pad`` (mils from any surface pad to a via),
``vias_under_parts`` (false keeps vias from under part bodies) and
``part_gap`` (mils body to body between the round's parts), with their
source; and ``orient``: parts kept at a ``rotation`` or alike, and
``pins`` each on the net its polarity needs. See ``assembly``.

And ``loops``: current loops the AI names (a converter's hot loop, a gate
drive, a decoupling loop), each as its pads in the order the current goes
round, with ``max_area`` (square mils) or ``max_length`` (mils) and its
source. The review measures each over the routed copper, a plane or pour
net taken as a return straight under the path. The router prices
copper outside the hull of a loop's pads, so it keeps the loop's nets in.

And ``pdn``: an IC's power pins and their decoupling, each rule a ``part``,
its ``rails`` (the power nets of its pins), the ``decaps`` that serve them
(default every two-pad capacitor with a pad on a rail), ``max_distance``
(mils from a decoupling capacitor to its pin, along the copper when the
copper joins them, else straight), ``min_vias`` within ``via_reach`` mils
of each power pin and of each capacitor's return pad, and its source.

And ``thermal``: a power part's heat path, each rule a ``part`` and its
thermal ``pad`` (default its largest), ``min_area`` (square mils of its
net's copper on the pad's layer that the pad sits in), ``min_vias``
within ``via_reach`` mils of the pad, and the datasheet's ``power`` (W),
``theta_ja`` (C/W, at the copper the datasheet assumes), ``ambient`` and
``tj_max`` (C), from which the review gives the junction temperature.
"""

from __future__ import annotations

import hashlib
import math
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from ..model import LayoutBoard

Polygon = list[tuple[float, float]]

#: Percent an impedance may be off its target when the entry gives no
#: tolerance: the closed forms' own accuracy.
IMPEDANCE_TOL = 10.0

#: Kinds a net is found to be, and what the AI should read before briefing it.
CRITICAL: dict[str, str] = {
    "differential": ("the interface's layout guide or the transceiver's datasheet: the "
                     "differential impedance, the skew allowed, how many vias, which "
                     "layer and its reference plane"),
    "clock": ("the crystal's or oscillator's datasheet and the chip's clock section: the "
              "longest trace, where the load capacitors go, a ground guard and what "
              "must not run beside it"),
    "switching": ("the regulator's datasheet, its layout section: the hot loop, how small "
                  "the switch node's copper should be, which layer, and where the "
                  "feedback must not run"),
    "high_current": ("the current it carries and the temperature rise allowed: IPC-2152 "
                     "for the width, and the parts' datasheets for the copper at their pads"),
    "antenna": ("the antenna's or the radio module's datasheet: the feed line's impedance "
                "and its reference plane, and the keep-out round the antenna on every layer "
                "(give it as an area of kind antenna, the feed allowed in)"),
}


#: The confidence, 0 to 100, a decision needs before the router lays it.
MIN_CONFIDENCE = 80.0
#: An entry's fields that record its confirmation, not the decision.
_CONFIRMATION = ("verified", "verified_for", "confidence", "alternative")

#: Degrees C a track may rise over ambient when the entry gives no temp_rise.
TEMP_RISE = 10.0


def current_width(current: float, temp_rise: float, copper_mils: float,
                  outer: bool) -> float:
    """The track width, mils, IPC-2221's closed form needs to carry
    ``current`` amps at ``temp_rise`` degrees C over ambient in copper
    ``copper_mils`` thick: I = k dT^0.44 A^0.725, A in square mils, k 0.048
    on an outer layer and 0.024 on an inner one."""
    k = 0.048 if outer else 0.024
    area = (current / (k * temp_rise ** 0.44)) ** (1 / 0.725)
    return area / copper_mils


def current_widths(board: LayoutBoard, e: "NetRules") -> dict[str, float]:
    """Layer -> the width the entry's current needs there, for the copper
    layers it may use (all of them when it names none)."""
    if not e.current:
        return {}
    layers = sorted(board.layers, key=lambda l: l.order)
    names = [l.name for l in layers]
    rise = e.temp_rise or TEMP_RISE
    return {l.name: round(current_width(e.current, rise, l.copper_mils or 1.4,
                                        l.name in (names[0], names[-1])), 1)
            for l in layers if not e.layers or l.name in e.layers}


def _num(d: dict[str, Any], key: str) -> Optional[float]:
    return float(d[key]) if d.get(key) not in (None, "") else None


def _polys(v) -> list[Polygon]:
    # One ring [[x, y], ...] or several [[[x, y], ...], ...].
    if not v:
        return []
    if isinstance(v[0][0], (int, float)):
        v = [v]
    return [[(float(p[0]), float(p[1])) for p in ring] for ring in v]


@dataclass
class AreaRule:
    """A place no net routes through but those it allows."""

    name: str
    polygons: list[Polygon]
    kind: str = "keep_out"         # antenna, creepage, mechanical, keep_out...
    layers: Optional[tuple[str, ...]] = None   # None: every copper layer
    allow: tuple[str, ...] = ()
    source: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AreaRule":
        layers = d.get("layers")
        allow = d.get("allow") or []
        return cls(
            name=str(d.get("name") or "").strip(),
            polygons=_polys(d.get("polygons") or d.get("polygon")),
            kind=str(d.get("kind") or "keep_out").strip().lower(),
            layers=tuple(str(x) for x in ([layers] if isinstance(layers, str) else layers))
            if layers else None,
            allow=tuple(str(n) for n in ([allow] if isinstance(allow, str) else allow)),
            source=str(d.get("source") or "").strip(),
            reason=str(d.get("reason") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", [], ())}
        for k in ("layers", "allow"):
            if getattr(self, k):
                out[k] = list(getattr(self, k))
        return out

    def bars(self, net: str, layer: str) -> bool:
        return net not in self.allow and (self.layers is None or layer in self.layers)


#: Loops measured as the area they enclose; the rest as the length of their
#: path (design/loops.py names the kinds).
AREA_LOOPS = ("hot_loop", "output_loop", "decoupling", "crystal", "loop")


@dataclass
class LoopRule:
    """A current loop the AI names, pad by pad in the order the current goes
    round it, and the most it may enclose or run."""

    name: str
    members: tuple[str, ...]           # pad keys, "C5.1"
    kind: str = "loop"                 # hot_loop, gate_drive, kelvin...
    max_area: Optional[float] = None   # square mils
    max_length: Optional[float] = None
    source: str = ""
    reason: str = ""

    @property
    def is_area(self) -> bool:
        return self.kind in AREA_LOOPS

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LoopRule":
        members = d.get("members") or d.get("pads") or []
        return cls(name=str(d.get("name") or "").strip(),
                   members=tuple(str(m).strip() for m in members),
                   kind=str(d.get("kind") or "loop").strip().lower(),
                   max_area=_num(d, "max_area"), max_length=_num(d, "max_length"),
                   source=str(d.get("source") or "").strip(),
                   reason=str(d.get("reason") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", [], ())}
        out["members"] = list(self.members)
        return out


@dataclass
class BarrierRule:
    """An isolation barrier: one side's nets kept ``creepage`` (along the
    surface, outer layers) and ``clearance`` (through air, every layer)
    from the other side's, or from every other net when ``apart_from`` is
    empty. A net class name stands for its nets."""

    name: str
    nets: tuple[str, ...]
    apart_from: tuple[str, ...] = ()
    creepage: Optional[float] = None
    clearance: Optional[float] = None
    source: str = ""
    reason: str = ""

    @property
    def distance(self) -> float:
        return max(self.creepage or 0.0, self.clearance or 0.0)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BarrierRule":
        def names(v):
            return tuple(str(n) for n in ([v] if isinstance(v, str) else (v or [])))
        return cls(name=str(d.get("name") or "").strip(), nets=names(d.get("nets")),
                   apart_from=names(d.get("apart_from")),
                   creepage=_num(d, "creepage"), clearance=_num(d, "clearance"),
                   source=str(d.get("source") or "").strip(),
                   reason=str(d.get("reason") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", [], ())}
        for k in ("nets", "apart_from"):
            if getattr(self, k):
                out[k] = list(getattr(self, k))
        return out


@dataclass
class AssemblyRule:
    """Where vias may go and how far apart parts are, for assembly (see
    ``assembly``)."""

    via_to_pad: Optional[float] = None
    vias_under_parts: Optional[bool] = None
    part_gap: Optional[float] = None
    source: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> Optional["AssemblyRule"]:
        if not d:
            return None
        under = d.get("vias_under_parts")
        return cls(via_to_pad=_num(d, "via_to_pad"),
                   vias_under_parts=None if under in (None, "") else bool(under),
                   part_gap=_num(d, "part_gap"),
                   source=str(d.get("source") or "").strip(),
                   reason=str(d.get("reason") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


@dataclass
class OrientRule:
    """Parts that must face one way: at ``rotation`` (on ``side``) when
    given, else all alike; and ``pins``, pad key -> the net it must be on."""

    name: str
    parts: tuple[str, ...] = ()
    rotation: Optional[float] = None
    side: str = ""
    pins: dict[str, str] = field(default_factory=dict)
    source: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OrientRule":
        parts = d.get("parts") or []
        pins = {str(k).strip(): str(v).strip() for k, v in (d.get("pins") or {}).items()}
        parts = [parts] if isinstance(parts, str) else list(parts)
        # A part whose pin is named is ruled too.
        for key in pins:
            ref = key.rsplit(".", 1)[0]
            if ref and ref not in parts:
                parts.append(ref)
        return cls(name=str(d.get("name") or "").strip(),
                   parts=tuple(str(p).strip() for p in parts),
                   rotation=_num(d, "rotation"),
                   side=str(d.get("side") or "").strip().lower(),
                   pins=pins, source=str(d.get("source") or "").strip(),
                   reason=str(d.get("reason") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", [], (), {})}
        if self.parts:
            out["parts"] = list(self.parts)
        return out


@dataclass
class PdnRule:
    """An IC's power pins and the capacitors that decouple them."""

    name: str
    part: str
    rails: tuple[str, ...] = ()
    decaps: tuple[str, ...] = ()
    max_distance: Optional[float] = None
    min_vias: Optional[int] = None
    via_reach: Optional[float] = None
    source: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PdnRule":
        def names(v):
            return tuple(str(n).strip() for n in ([v] if isinstance(v, str) else (v or [])))
        return cls(name=str(d.get("name") or d.get("part") or "").strip(),
                   part=str(d.get("part") or "").strip(), rails=names(d.get("rails")),
                   decaps=names(d.get("decaps")), max_distance=_num(d, "max_distance"),
                   min_vias=int(d["min_vias"]) if d.get("min_vias") not in (None, "") else None,
                   via_reach=_num(d, "via_reach"),
                   source=str(d.get("source") or "").strip(),
                   reason=str(d.get("reason") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", [], ())}
        for k in ("rails", "decaps"):
            if getattr(self, k):
                out[k] = list(getattr(self, k))
        return out


@dataclass
class ThermalRule:
    """A power part's heat path: the copper and vias at its thermal pad,
    and its junction temperature from its datasheet's figures."""

    name: str
    part: str
    pad: str = ""
    min_area: Optional[float] = None
    min_vias: Optional[int] = None
    via_reach: Optional[float] = None
    power: Optional[float] = None
    theta_ja: Optional[float] = None
    ambient: Optional[float] = None
    tj_max: Optional[float] = None
    source: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ThermalRule":
        return cls(name=str(d.get("name") or d.get("part") or "").strip(),
                   part=str(d.get("part") or "").strip(), pad=str(d.get("pad") or "").strip(),
                   min_area=_num(d, "min_area"),
                   min_vias=int(d["min_vias"]) if d.get("min_vias") not in (None, "") else None,
                   via_reach=_num(d, "via_reach"), power=_num(d, "power"),
                   theta_ja=_num(d, "theta_ja"), ambient=_num(d, "ambient"),
                   tj_max=_num(d, "tj_max"), source=str(d.get("source") or "").strip(),
                   reason=str(d.get("reason") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", [], ())}

    @property
    def tj(self) -> Optional[float]:
        """The junction temperature its figures give, C."""
        if None in (self.power, self.theta_ja, self.ambient):
            return None
        return round(self.ambient + self.power * self.theta_ja, 1)


#: Mils round a thermal pad its vias are counted in when the rule gives no reach.
THERMAL_REACH = 100.0


def thermal_pad(board: LayoutBoard, rule: ThermalRule):
    """The rule's pad on the board: the one named, else the part's largest."""
    pads = [p for p in board.pads if p.comp == rule.part and p.copper]
    if rule.pad:
        pads = [p for p in pads if p.name == rule.pad]
    return max(pads, key=lambda p: max(c.w * c.h for c in p.copper), default=None)


def _poly_area(ring) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def thermal_measure(board: LayoutBoard, rule: ThermalRule) -> dict[str, Any]:
    """What the board gives the rule's pad: the area of its net's copper on
    the pad's layer that the pad sits in (pours, fills and regions, holes
    taken out; at least the pad's own copper), the vias of its net within the
    reach, and the junction temperature its figures give."""
    p = thermal_pad(board, rule)
    if p is None:
        return {"pad": "", "found": False}
    layer = next((c.layer for c in p.copper), "")
    own = max(c.w * c.h for c in p.copper)
    area = 0.0
    for r in board.regions:
        if (r.net == p.net and p.net and r.layer == layer and len(r.outline) >= 3
                and r.kind not in ("cutout", "keepout") and _inside([r.outline], p.x, p.y)):
            area += _poly_area(list(r.outline)) - sum(_poly_area(list(h)) for h in r.holes
                                                       if len(h) >= 3)
    area = max(area, own)       # a pour the pad sits in holds the pad
    reach = rule.via_reach or THERMAL_REACH
    vias = sum(1 for v in board.vias if v.net == p.net and p.net
               and math.hypot(v.x - p.x, v.y - p.y) <= reach)
    return {"pad": p.key, "net": p.net, "layer": layer, "found": True,
            "area": round(area), "vias": vias, "reach": reach, "tj": rule.tj}


@dataclass
class NetRules:
    nets: tuple[str, ...]
    kind: str = ""
    layers: Optional[tuple[str, ...]] = None
    width: Optional[float] = None
    region: Optional[list[Polygon]] = None
    keep_out: list[Polygon] = field(default_factory=list)
    max_vias: Optional[int] = None
    min_length: Optional[float] = None
    max_length: Optional[float] = None
    match: str = ""
    skew: Optional[float] = None
    reference: str = ""
    impedance: Optional[float] = None
    diff_impedance: Optional[float] = None
    gap: Optional[float] = None
    impedance_tol: Optional[float] = None
    apart_from: tuple[str, ...] = ()
    spacing: Optional[float] = None
    max_parallel: Optional[float] = None
    parallel_gap: Optional[float] = None
    max_broadside: Optional[float] = None
    guard: str = ""
    guard_gap: Optional[float] = None
    max_unguarded: Optional[float] = None
    terminate: str = ""
    terminate_at: str = ""
    driver: str = ""
    max_stub: Optional[float] = None
    current: Optional[float] = None
    temp_rise: Optional[float] = None
    return_via: Optional[float] = None
    source: str = ""
    reason: str = ""
    #: What the AI checked on the board for these nets, given in a later
    #: call than the entry, and the decision it was checked against.
    verified: str = ""
    verified_for: str = ""
    #: How sure the AI is, 0 to 100, that this is the best choice, and the
    #: other choice it weighed it against and why that one lost.
    confidence: Optional[float] = None
    alternative: str = ""

    def decision_key(self) -> str:
        """A digest of the decision itself: a change to the entry makes it
        new, and what was verified no longer stands for it."""
        d = {k: v for k, v in self.as_dict().items() if k not in _CONFIRMATION}
        d["nets"] = sorted(self.nets)
        return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]

    @property
    def confirmed(self) -> bool:
        return (bool(self.verified) and self.verified_for == self.decision_key()
                and (self.confidence or 0) >= MIN_CONFIDENCE)

    @property
    def target_ohms(self) -> Optional[float]:
        return self.diff_impedance or self.impedance

    @property
    def tolerance_pct(self) -> float:
        return self.impedance_tol if self.impedance_tol is not None else IMPEDANCE_TOL

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NetRules":
        nets = d.get("nets") or ([d["net"]] if d.get("net") else [])
        if isinstance(nets, str):
            nets = [nets]
        layers = d.get("layers")
        apart = d.get("apart_from") or []
        if isinstance(apart, str):
            apart = [apart]
        return cls(
            nets=tuple(str(n) for n in nets),
            kind=str(d.get("kind") or "").strip().lower(),
            layers=tuple(str(x) for x in ([layers] if isinstance(layers, str) else layers))
            if layers else None,
            width=_num(d, "width"),
            region=_polys(d.get("region")) or None,
            keep_out=_polys(d.get("keep_out")),
            max_vias=int(d["max_vias"]) if d.get("max_vias") not in (None, "") else None,
            min_length=_num(d, "min_length"),
            max_length=_num(d, "max_length"),
            match=str(d.get("match") or "").strip(),
            skew=_num(d, "skew"),
            reference=str(d.get("reference") or "").strip(),
            impedance=_num(d, "impedance"),
            diff_impedance=_num(d, "diff_impedance"),
            gap=_num(d, "gap"),
            impedance_tol=_num(d, "impedance_tol"),
            apart_from=tuple(str(n) for n in apart),
            spacing=_num(d, "spacing"),
            max_parallel=_num(d, "max_parallel"),
            parallel_gap=_num(d, "parallel_gap"),
            max_broadside=_num(d, "max_broadside"),
            guard=str(d.get("guard") or "").strip(),
            guard_gap=_num(d, "guard_gap"),
            max_unguarded=_num(d, "max_unguarded"),
            terminate=str(d.get("terminate") or "").strip(),
            terminate_at=str(d.get("terminate_at") or "").strip().lower(),
            driver=str(d.get("driver") or "").strip(),
            max_stub=_num(d, "max_stub"),
            current=_num(d, "current"),
            temp_rise=_num(d, "temp_rise"),
            return_via=_num(d, "return_via"),
            source=str(d.get("source") or "").strip(),
            reason=str(d.get("reason") or "").strip(),
            verified=str(d.get("verified") or "").strip(),
            verified_for=str(d.get("verified_for") or "").strip(),
            confidence=_num(d, "confidence"),
            alternative=str(d.get("alternative") or "").strip())

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", [], ())}
        out["nets"] = list(self.nets)
        if self.layers:
            out["layers"] = list(self.layers)
        if self.apart_from:
            out["apart_from"] = list(self.apart_from)
        return out


class Brief:
    """The entries, and each net's own."""

    def __init__(self, entries: Iterable[NetRules] = (), areas: Iterable[AreaRule] = (),
                 loops: Iterable[LoopRule] = (), barriers: Iterable[BarrierRule] = (),
                 assembly: Optional[AssemblyRule] = None, orient: Iterable[OrientRule] = (),
                 pdn: Iterable[PdnRule] = (), thermal: Iterable[ThermalRule] = ()):
        self.entries = list(entries)
        self.areas = list(areas)
        self.loops = list(loops)
        self.barriers = list(barriers)
        self.assembly = assembly
        self.orient = list(orient)
        self.pdn = list(pdn)
        self.thermal = list(thermal)
        self.of: dict[str, NetRules] = {}
        for e in self.entries:
            for n in e.nets:
                self.of.setdefault(n, e)

    @classmethod
    def from_list(cls, items: Iterable[dict[str, Any]],
                  areas: Iterable[dict[str, Any]] = (),
                  loops: Iterable[dict[str, Any]] = (),
                  barriers: Iterable[dict[str, Any]] = (),
                  assembly: Optional[dict[str, Any]] = None,
                  orient: Iterable[dict[str, Any]] = (),
                  pdn: Iterable[dict[str, Any]] = (),
                  thermal: Iterable[dict[str, Any]] = ()) -> "Brief":
        return cls((NetRules.from_dict(d) for d in items or []),
                   (AreaRule.from_dict(d) for d in areas or []),
                   (LoopRule.from_dict(d) for d in loops or []),
                   (BarrierRule.from_dict(d) for d in barriers or []),
                   AssemblyRule.from_dict(assembly),
                   (OrientRule.from_dict(d) for d in orient or []),
                   (PdnRule.from_dict(d) for d in pdn or []),
                   (ThermalRule.from_dict(d) for d in thermal or []))

    def keeping(self, entries: Iterable[NetRules]) -> "Brief":
        """This brief with other net entries, every other rule kept."""
        return Brief(entries, self.areas, self.loops, self.barriers, self.assembly,
                     self.orient, self.pdn, self.thermal)

    def as_list(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.entries]

    def areas_list(self) -> list[dict[str, Any]]:
        return [a.as_dict() for a in self.areas]

    def loops_list(self) -> list[dict[str, Any]]:
        return [lp.as_dict() for lp in self.loops]

    def barriers_list(self) -> list[dict[str, Any]]:
        return [b.as_dict() for b in self.barriers]

    def assembly_dict(self) -> dict[str, Any]:
        return self.assembly.as_dict() if self.assembly else {}

    def orient_list(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.orient]

    def pdn_list(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.pdn]

    def thermal_list(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.thermal]

    def barrier_sides(self, board: LayoutBoard) -> list[tuple[frozenset, Optional[frozenset], float]]:
        """Each barrier as (one side's nets, the other's or None for every
        other net, the larger of its creepage and clearance)."""
        from .creepage import side_nets
        return [(frozenset(side_nets(board, b.nets)),
                 frozenset(side_nets(board, b.apart_from)) if b.apart_from else None,
                 b.distance) for b in self.barriers if b.distance > 0]

    def spacing_pairs(self, board: Optional[LayoutBoard] = None) -> dict[tuple[str, str], float]:
        """(net, net) -> the spacing the brief asks between them, both ways
        round, the larger where two entries ask; a net class in apart_from
        stands for its nets on ``board``."""
        from .creepage import side_nets
        out: dict[tuple[str, str], float] = {}
        for e in self.entries:
            if not e.spacing:
                continue
            others = side_nets(board, e.apart_from) if board is not None else set(e.apart_from)
            for a in e.nets:
                for b in sorted(others - {a}):
                    for k in ((a, b), (b, a)):
                        out[k] = max(out.get(k, 0.0), e.spacing)
        return out

    def __bool__(self) -> bool:
        return bool(self.entries or self.areas or self.loops or self.barriers
                    or self.assembly or self.orient or self.pdn or self.thermal)


# --- what the router finds critical ---------------------------------------------

# A clock or switch node joins a few pads (a crystal, its load capacitor, a
# series resistor and the chip's pin; an inductor, the switch pin, a diode
# and a bootstrap capacitor). Rails and ground join many.
SMALL_NET = 6


def _prefix(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha()).upper()


def _rf_name(net: str) -> bool:
    """A net named for a radio feed: RF or ANT as a word of its name."""
    import re
    return any(w in ("RF", "ANT", "ANTENNA") for w in re.split(r"[^A-Z0-9]+", net.upper()))


def critical_nets(board: LayoutBoard, widths: Optional[dict[str, float]] = None) -> dict[str, str]:
    """Net -> the critical kind the board shows it to be.

    From the board alone: a defined or named differential pair; a small net
    on an antenna (ANT, AE) or named RF or ANT is an antenna feed; a small
    net on a crystal or resonator (Y, X) is a clock; a small net joining an
    inductor (L) to an IC or transistor (U, Q) with no capacitor on it but
    a bootstrap one, and no plane or pour, is a switching node; a net whose
    width rule asks for well over the usual width is high current. A
    crystal's ground pins and a supply rail behind an inductor are neither.
    """
    from .diffpair import find_pairs

    out: dict[str, str] = {}
    for p, n, *_ in find_pairs(board):
        out[p] = out[n] = "differential"
    poured = {r.net for r in board.regions if r.net}
    on_net: dict[str, list] = {}
    nets_of: dict[str, set[str]] = {}
    for pad in board.pads:
        if pad.net and pad.comp:
            on_net.setdefault(pad.net, []).append(pad)
            nets_of.setdefault(pad.comp, set()).add(pad.net)

    def bootstrap(cap: str, net: str, ics: set[str]) -> bool:
        # The capacitor's other end goes only to a pin of the same IC.
        other = nets_of.get(cap, set()) - {net}
        return len(other) == 1 and all(
            len(on_net[o]) <= 3 and {p.comp for p in on_net[o]} - {cap} <= ics
            for o in other)

    for net, pads in on_net.items():
        if net in out or len(pads) > SMALL_NET:
            continue
        kinds = {_prefix(p.comp) for p in pads}
        if kinds & {"ANT", "AE"} or _rf_name(net):
            out[net] = "antenna"
        elif kinds & {"Y", "X"} and len(kinds) > 1:
            out[net] = "clock"
        elif "L" in kinds and kinds & {"U", "Q"} and net not in poured:
            ics = {p.comp for p in pads if _prefix(p.comp) in ("U", "Q")}
            caps = {p.comp for p in pads if _prefix(p.comp) == "C"}
            if all(bootstrap(c, net, ics) for c in caps):
                out[net] = "switching"
    if widths:
        usual = float(np.median(list(widths.values())))
        for net, w in widths.items():
            if net not in out and w > 1.25 * usual + 1e-6:
                out[net] = "high_current"
    return out


# --- checking a brief against the board ------------------------------------------

def board_layers(board: LayoutBoard, brief: Brief) -> Brief:
    """The brief with every layer named as the router names it: a layer
    given by its own name on the board (L3-SIG) becomes its id
    (MidLayer2). A name no layer has is left for ``check`` to refuse."""
    from dataclasses import replace

    def ids(layers):
        return None if layers is None else tuple(board.layer_id(x) for x in layers)

    brief.entries = [replace(e, layers=ids(e.layers)) if e.layers else e
                     for e in brief.entries]
    brief.areas = [replace(a, layers=ids(a.layers)) if a.layers else a
                   for a in brief.areas]
    return brief


def check(board: LayoutBoard, brief: Brief, nets: Optional[Iterable[str]] = None,
          rules=None) -> dict[str, Any]:
    """Whether the brief can be routed as written, and what it still lacks.

    Returns ``errors`` (the brief cannot be obeyed as written: each names
    the entry and the rule), ``needed`` (critical nets among ``nets`` with no
    sourced entry: each says why and what to read) and ``briefed``.
    """
    from ..rules import RuleSet
    from .params import RouteRules

    rules = rules or RuleSet.from_board(board)
    names = board.nets()
    net_id = {n: i + 1 for i, n in enumerate(names)}
    rr = RouteRules(board, rules, net_id)
    pads_of = board.pads_by_net()
    widths = {n: rr.width(n, pads_of.get(n, [])) for n in names}
    copper = set(board.signal_layers()) | set(board.copper_layers())
    errors: list[str] = []
    seen: dict[str, int] = {}
    for k, e in enumerate(brief.entries):
        tag = f"entry {k + 1} ({', '.join(e.nets[:3])}{'...' if len(e.nets) > 3 else ''})"
        if not e.nets:
            errors.append(f"entry {k + 1}: names no nets")
        for n in e.nets:
            if n not in net_id:
                errors.append(f"{tag}: no net {n!r} on the board")
            elif n in seen:
                errors.append(f"{tag}: {n} is ruled by entry {seen[n] + 1} already")
            else:
                seen[n] = k
        if e.layers:
            bad = [x for x in e.layers if x not in copper]
            if bad:
                errors.append(f"{tag}: no copper layer {bad}; the board has {board.copper_layers()}")
            for n in e.nets:
                need = {c.layer for p in pads_of.get(n, []) if p.is_smd for c in p.copper}
                if need - set(e.layers):
                    errors.append(f"{tag}: {n} has surface pads on {sorted(need - set(e.layers))}, "
                                  f"which layers leaves out")
        if e.width is not None:
            for n in e.nets:
                if n in net_id and e.width + 1e-6 < rr.min_width(n):
                    errors.append(f"{tag}: width {e.width:g} is under the board's width rule "
                                  f"for {n} ({rr.min_width(n):g}); change the rule in Altium "
                                  "first so DRC agrees")
        if e.return_via is not None and (e.return_via <= 0 or not e.reference):
            errors.append(f"{tag}: return_via needs a reference net, and a distance over 0")
        if e.current is not None:
            if e.current <= 0 or (e.temp_rise is not None and e.temp_rise <= 0):
                errors.append(f"{tag}: current and temp_rise must be over 0")
            else:
                need = current_widths(board, e)
                short = {l: w for l, w in need.items()
                         if e.width is None or e.width + 1e-6 < w}
                if short:
                    errors.append(
                        f"{tag}: {e.current:g} A at {e.temp_rise or TEMP_RISE:g} C rise needs "
                        + ", ".join(f"{w:g} mil on {l}" for l, w in short.items())
                        + " (IPC-2221)" + ("; give that width" if e.width is None else
                                           f", over its width {e.width:g}")
                        + ", or leave those layers out")
        for ring in (e.region or []) + e.keep_out:
            if len(ring) < 3:
                errors.append(f"{tag}: a polygon needs 3 points or more")
        if e.max_vias is not None and e.max_vias < 0:
            errors.append(f"{tag}: max_vias cannot be negative")
        if (e.min_length is not None and e.max_length is not None
                and e.min_length > e.max_length):
            errors.append(f"{tag}: min_length {e.min_length:g} is over max_length {e.max_length:g}")
        if e.skew is not None and not e.match:
            errors.append(f"{tag}: skew needs a match group to be measured against")
        if e.reference and e.reference not in net_id:
            errors.append(f"{tag}: no reference net {e.reference!r} on the board")
        if e.target_ohms is not None:
            errors += _impedance_errors(board, e, tag)
        errors += _apart_errors(e, tag, net_id, board.net_classes)
        errors += _guard_errors(e, tag, net_id)
        errors += _termination_errors(board, e, tag)
        if e.region:
            for n in e.nets:
                for p in pads_of.get(n, []):
                    if not _inside(e.region, p.x, p.y):
                        errors.append(f"{tag}: {n}'s pad {p.key} at ({p.x:g}, {p.y:g}) is "
                                      "outside its region")
                        break
    names_seen: set[str] = set()
    for k, a in enumerate(brief.areas):
        tag = f"area {k + 1} ({a.name or a.kind})"
        if not a.polygons or any(len(r) < 3 for r in a.polygons):
            errors.append(f"{tag}: a polygon needs 3 points or more")
        if not a.source:
            errors.append(f"{tag}: give its source (datasheet page, standard clause)")
        if a.layers and [x for x in a.layers if x not in copper]:
            errors.append(f"{tag}: no copper layer {[x for x in a.layers if x not in copper]}")
        bad = [n for n in a.allow if n not in net_id]
        if bad:
            errors.append(f"{tag}: allows no net {bad} on the board")
        if a.name and a.name in names_seen:
            errors.append(f"{tag}: another area has the name {a.name!r}")
        names_seen.add(a.name)
        # A pad of a net the area bars, inside it, could never be reached.
        for p in board.pads:
            if p.net and p.net in net_id and _inside(a.polygons, p.x, p.y) and any(
                    a.bars(p.net, c.layer) for c in p.copper if p.is_smd):
                errors.append(f"{tag}: {p.net}'s pad {p.key} is inside it; allow {p.net} "
                              "or draw the area round the pad")
                break
    errors += _loop_errors(board, brief)
    errors += _barrier_errors(board, brief, net_id)
    errors += _assembly_errors(board, brief, net_id)
    errors += _pdn_errors(board, brief, net_id)
    errors += _thermal_errors(board, brief)
    found = critical_nets(board, widths)
    wanted = set(nets) if nets else set(names)
    needed = []
    for n in sorted(wanted & set(found)):
        e = brief.of.get(n)
        why = ("no entry" if e is None else "its entry gives no source" if not e.source
               else "no area of kind antenna allows it" if found[n] == "antenna" and not any(
                   a.kind == "antenna" and n in a.allow for a in brief.areas)
               else "")
        if why:
            needed.append({"net": n, "kind": found[n], "why": why, "read": CRITICAL[found[n]]})
    return {"ok": not errors, "errors": errors, "needed": needed,
            "briefed": sorted(seen), "critical": found}


#: How many undecided nets a reply lists by name; the count gives the rest.
UNDECIDED_LISTED = 40


def undecided(brief: Brief, nets: Iterable[str], skip: Iterable[str] = ()) -> list[dict[str, str]]:
    """The nets among ``nets`` the AI has not decided yet: no entry rules
    them, their entry names no layers or gives no reason (or source), or
    the AI has not confirmed, in a later call, what it verified on the
    board for them. The router lays only what was decided; one entry can
    decide a group. ``skip``: nets reported already (critical ones
    without a source)."""
    out = []
    for n in sorted(set(nets) - set(skip)):
        e = brief.of.get(n)
        why = ("no entry" if e is None else "its entry names no layers" if not e.layers
               else "its entry gives no reason" if not (e.reason or e.source)
               else "not confirmed: what was verified for it is not given, or the entry "
                    "changed since" if not e.confirmed else "")
        if why:
            out.append({"net": n, "why": why})
    return out


#: Pads a fact sheet lists per net; the count gives the rest.
FACT_PADS = 12


def facts(board: LayoutBoard, nets: Iterable[str]) -> list[dict[str, Any]]:
    """What the board shows about each net, for the AI to check its
    decision against before it confirms it: the pads it joins, the layers
    of its surface pads, the span between its farthest pads (mils) and
    the critical kind the router finds it to be, if any."""
    pads_of = board.pads_by_net()
    found = critical_nets(board)
    out = []
    for n in sorted(set(nets)):
        pads = pads_of.get(n, [])
        span = max((math.hypot(a.x - b.x, a.y - b.y) for a in pads for b in pads), default=0.0)
        out.append({"net": n, "pads": [p.key for p in pads[:FACT_PADS]], "pad_count": len(pads),
                    "smd_layers": sorted({c.layer for p in pads if p.is_smd for c in p.copper}),
                    "span": round(span, 1), "critical": found.get(n, "")})
    return out


def _named(text: str, word: str) -> bool:
    import re
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(word.upper()) + r"(?![A-Za-z0-9_])",
                     text.upper()) is not None


#: An entry of more plain nets than this is confirmed by its hardest net.
GROUP_CHECK = 4


def hardest(board: LayoutBoard, nets: Iterable[str]) -> str:
    """The net of a group whose pads lie farthest apart: the one to check
    a group's decision against."""
    best, far = "", -1.0
    for f in facts(board, nets):
        if f["span"] > far:
            best, far = f["net"], f["span"]
    return best


def confirm_errors(board: LayoutBoard, entry: NetRules, verified: str,
                   confidence: Any = None, alternative: str = "") -> list[str]:
    """Why a confirmation does not stand: ``verified`` must say more than
    the entry's reason and name each net with a part it joins (an entry of
    more than GROUP_CHECK nets, none critical, by its hardest net only,
    which ``to_confirm`` names); ``confidence``, 0 to 100, that this is the
    best choice, must reach MIN_CONFIDENCE; and ``alternative`` must name
    the other choice weighed and why it lost."""
    text = (verified or "").strip()
    tag = f"confirm ({', '.join(entry.nets[:3])}{'...' if len(entry.nets) > 3 else ''})"
    try:
        sure = float(confidence)
    except (TypeError, ValueError):
        return [f"{tag}: give confidence, 0 to 100, that this is the best choice for these nets"]
    if not 0 <= sure <= 100:
        return [f"{tag}: confidence is 0 to 100"]
    if sure < MIN_CONFIDENCE:
        return [f"{tag}: confidence {sure:g} is under {MIN_CONFIDENCE:g}, so the router will not "
                "lay it. Weigh the other choices again, read what the decision rests on (the "
                "datasheet, the standard, the board around the nets), or ask the user; then "
                "change the decision or confirm it when you are sure"]
    alt = (alternative or "").strip()
    if not alt or alt.lower() in (text.lower(), (entry.reason or "").strip().lower()):
        return [f"{tag}: name the other choice you weighed (another layer, path, via place or "
                "width) and why it is worse"]
    if not text:
        return [f"{tag}: say what you verified on the board for these nets"]
    if text.lower() == (entry.reason or "").strip().lower():
        return [f"{tag}: the reason again is not a check; say what you verified on the board"]
    pads_of = board.pads_by_net()
    nets = list(entry.nets)
    if len(nets) > GROUP_CHECK and not set(nets) & set(critical_nets(board)):
        nets = [hardest(board, nets)]
    errs = []
    for n in nets:
        if not _named(text, n):
            errs.append(f"{tag}: {n} is not named; say what you checked for each net")
            continue
        parts = sorted({p.comp for p in pads_of.get(n, []) if p.comp})
        if parts and not any(_named(text, c) for c in parts):
            errs.append(f"{tag}: for {n} name the parts you checked it joins "
                        f"({', '.join(parts[:6])})")
    return errs


def _thermal_errors(board: LayoutBoard, brief: Brief) -> list[str]:
    """Why a thermal rule of the brief cannot be measured as written."""
    out: list[str] = []
    seen: set[str] = set()
    refs = {c.ref for c in board.components} | {p.comp for p in board.pads if p.comp}
    for k, r in enumerate(brief.thermal):
        tag = f"thermal {k + 1} ({r.name or r.part or 'unnamed'})"
        if r.name in seen:
            out.append(f"{tag}: another thermal rule has the name {r.name!r}")
        seen.add(r.name)
        if r.part not in refs:
            out.append(f"{tag}: no part {r.part!r} on the board")
            continue
        if thermal_pad(board, r) is None:
            out.append(f"{tag}: {r.part} has no pad {r.pad!r}" if r.pad
                       else f"{tag}: {r.part} has no copper pad")
        if not r.source:
            out.append(f"{tag}: give its source (the part's datasheet: thermal pad, "
                       "theta_ja, tj_max)")
        figs = (r.power, r.theta_ja, r.ambient, r.tj_max)
        if any(f is not None for f in figs) and any(f is None for f in figs):
            out.append(f"{tag}: power, theta_ja, ambient and tj_max go together")
        for nm, v in (("min_area", r.min_area), ("via_reach", r.via_reach),
                      ("power", r.power), ("theta_ja", r.theta_ja)):
            if v is not None and v <= 0:
                out.append(f"{tag}: {nm} must be over 0")
        if r.min_vias is not None and r.min_vias < 0:
            out.append(f"{tag}: min_vias cannot be negative")
    return out


def _barrier_errors(board: LayoutBoard, brief: Brief, net_id: dict[str, int]) -> list[str]:
    """Why a barrier of the brief cannot be kept as written."""
    from .creepage import side_nets

    out: list[str] = []
    seen: set[str] = set()
    for k, b in enumerate(brief.barriers):
        tag = f"barrier {k + 1} ({b.name or 'unnamed'})"
        if not b.name:
            out.append(f"{tag}: give it a name")
        elif b.name in seen:
            out.append(f"{tag}: another barrier has the name {b.name!r}")
        seen.add(b.name)
        if not b.nets:
            out.append(f"{tag}: names no nets for its side")
        for side in (b.nets, b.apart_from):
            bad = [n for n in side if n not in net_id and n not in board.net_classes]
            if bad:
                out.append(f"{tag}: no net or net class {bad} on the board")
        both = side_nets(board, b.nets) & side_nets(board, b.apart_from)
        if both:
            out.append(f"{tag}: {sorted(both)[:5]} on both sides")
        if not b.distance:
            out.append(f"{tag}: give creepage, clearance or both (mils, over 0)")
        for name in ("creepage", "clearance"):
            v = getattr(b, name)
            if v is not None and v < 0:
                out.append(f"{tag}: {name} cannot be negative")
        if not b.source:
            out.append(f"{tag}: give its source (the standard and clause, the working "
                       "voltage, pollution degree and material group it assumes)")
    return out


def _assembly_errors(board: LayoutBoard, brief: Brief, net_id: dict[str, int]) -> list[str]:
    """Why the brief's assembly and orient rules cannot be kept as written."""
    out: list[str] = []
    a = brief.assembly
    if a is not None:
        if a.via_to_pad is None and a.vias_under_parts is None and a.part_gap is None:
            out.append("assembly: give via_to_pad, vias_under_parts or part_gap")
        for name in ("via_to_pad", "part_gap"):
            v = getattr(a, name)
            if v is not None and v < 0:
                out.append(f"assembly: {name} cannot be negative")
        if not a.source:
            out.append("assembly: give its source (the assembler's design rules, "
                       "IPC-7351, the part's datasheet)")
    refs = {c.ref for c in board.components}
    pads = {p.key for p in board.pads if p.comp}
    seen: set[str] = set()
    for k, r in enumerate(brief.orient):
        tag = f"orient {k + 1} ({r.name or 'unnamed'})"
        if not r.name:
            out.append(f"{tag}: give it a name")
        elif r.name in seen:
            out.append(f"{tag}: another orient rule has the name {r.name!r}")
        seen.add(r.name)
        if not r.parts:
            out.append(f"{tag}: names no parts")
        bad = [p for p in r.parts if p not in refs]
        if bad:
            out.append(f"{tag}: no part {bad[:5]} on the board")
        if r.rotation is None and len(r.parts) < 2 and not r.pins:
            out.append(f"{tag}: give rotation, pins, or two parts or more to keep alike")
        if r.side and r.side not in ("top", "bottom"):
            out.append(f"{tag}: side is top or bottom")
        for key, net in r.pins.items():
            if key not in pads:
                out.append(f"{tag}: no pad {key} on the board (give it as D1.K, C5.1)")
            elif net not in net_id:
                out.append(f"{tag}: no net {net!r} on the board for {key}")
        if not r.source:
            out.append(f"{tag}: give its source (the datasheet's polarity marking or "
                       "package drawing, the assembly drawing)")
    return out


def _loop_errors(board: LayoutBoard, brief: Brief) -> list[str]:
    """Why a loop of the brief cannot be measured as written. Each stop
    after the first is reached from the one before through the same part
    (its body) or the same net (its copper), and an area loop closes from
    its last stop back to its first the same way."""
    pads = {p.key: p for p in board.pads if p.comp}
    out: list[str] = []
    seen: set[str] = set()
    for k, lp in enumerate(brief.loops):
        tag = f"loop {k + 1} ({lp.name or 'unnamed'})"
        if not lp.name:
            out.append(f"{tag}: give it a name")
        elif lp.name in seen:
            out.append(f"{tag}: another loop has the name {lp.name!r}")
        seen.add(lp.name)
        if not lp.source:
            out.append(f"{tag}: give its source (datasheet layout section, app note)")
        if lp.max_area is None and lp.max_length is None:
            out.append(f"{tag}: give max_area (square mils) or max_length (mils)")
        for name in ("max_area", "max_length"):
            v = getattr(lp, name)
            if v is not None and v <= 0:
                out.append(f"{tag}: {name} must be over 0")
        least = 3 if lp.is_area else 2
        if len(lp.members) < least:
            out.append(f"{tag}: needs {least} pads or more, in the order the current goes")
            continue
        missing = [m for m in lp.members if m not in pads]
        if missing:
            out.append(f"{tag}: no pad {missing[:5]} on the board (give them as C5.1, U1.3)")
            continue
        hops = list(zip(lp.members, lp.members[1:]))
        if lp.is_area:
            hops.append((lp.members[-1], lp.members[0]))
        for a, b in hops:
            pa, pb = pads[a], pads[b]
            if pa.comp != pb.comp and not (pa.net and pa.net == pb.net):
                out.append(f"{tag}: {a} ({pa.net or 'no net'}) to {b} ({pb.net or 'no net'}) "
                           "is neither one part nor one net; put the stops between")
                break
    return out


def _apart_errors(e: NetRules, tag: str, net_id: dict[str, int],
                  classes: Optional[dict] = None) -> list[str]:
    """Why an entry's spacing from other nets cannot be obeyed as written."""
    out = []
    classes = classes or {}
    if (e.spacing is not None or e.max_parallel is not None
            or e.max_broadside is not None) and not e.apart_from:
        out.append(f"{tag}: spacing, max_parallel and max_broadside need apart_from, the nets "
                   "to keep away from")
    bad = [n for n in e.apart_from if n not in net_id and n not in classes]
    if bad:
        out.append(f"{tag}: apart_from names no net or net class {bad} on the board")
    own = [n for n in e.apart_from if n in e.nets]
    if own:
        out.append(f"{tag}: apart_from names its own nets {own}")
    if e.apart_from and e.spacing is None and e.max_parallel is None             and e.max_broadside is None:
        out.append(f"{tag}: apart_from needs spacing, max_parallel or max_broadside")
    if e.max_broadside is not None and e.parallel_gap is None:
        out.append(f"{tag}: max_broadside needs parallel_gap: how far across, in plan, "
                   "counts as running over or under")
    for name in ("spacing", "max_parallel", "parallel_gap", "max_broadside"):
        v = getattr(e, name)
        if v is not None and v <= 0:
            out.append(f"{tag}: {name} must be over 0")
    if (e.max_parallel is not None and e.parallel_gap is None) or (
            e.parallel_gap is not None and e.max_parallel is None and e.max_broadside is None):
        out.append(f"{tag}: max_parallel and parallel_gap go together: the length, and how "
                   "close counts as running beside")
    if e.parallel_gap is not None and e.spacing is not None and e.parallel_gap <= e.spacing:
        out.append(f"{tag}: parallel_gap {e.parallel_gap:g} is not over spacing "
                   f"{e.spacing:g}, so nothing could ever be that close")
    return out


def _guard_errors(e: NetRules, tag: str, net_id: dict[str, int]) -> list[str]:
    """Why an entry's guard cannot be measured as written."""
    if not e.guard:
        return ([f"{tag}: guard_gap and max_unguarded need guard, the net that guards it"]
                if e.guard_gap is not None or e.max_unguarded is not None else [])
    out = []
    if e.guard not in net_id:
        out.append(f"{tag}: no guard net {e.guard!r} on the board")
    if e.guard in e.nets:
        out.append(f"{tag}: a net cannot guard itself")
    if e.guard_gap is None or e.guard_gap <= 0:
        out.append(f"{tag}: guard needs guard_gap (mils over 0, track edge to guard copper)")
    if e.max_unguarded is not None and e.max_unguarded < 0:
        out.append(f"{tag}: max_unguarded cannot be negative")
    return out


def _termination_errors(board: LayoutBoard, e: NetRules, tag: str) -> list[str]:
    """Why an entry's termination cannot be measured as written."""
    if not (e.terminate or e.driver or e.max_stub is not None or e.terminate_at):
        return []
    out = []
    pads = {p.key: p for p in board.pads if p.comp}
    if e.terminate_at and e.terminate_at not in ("end", "source"):
        out.append(f"{tag}: terminate_at is end or source")
    if e.terminate_at and not e.terminate:
        out.append(f"{tag}: terminate_at needs terminate, the part")
    if not e.driver:
        out.append(f"{tag}: give driver, the pad that drives the net (U1.12)")
    elif e.driver not in pads:
        out.append(f"{tag}: no pad {e.driver} on the board")
    elif pads[e.driver].net not in e.nets:
        out.append(f"{tag}: driver {e.driver} is on {pads[e.driver].net or 'no net'}, "
                   "not one of the entry's nets")
    if e.terminate:
        on = [p for p in board.pads if p.comp == e.terminate and p.net in e.nets]
        if not any(p.comp == e.terminate for p in board.pads):
            out.append(f"{tag}: no part {e.terminate} on the board")
        elif not on:
            out.append(f"{tag}: {e.terminate} has no pad on {list(e.nets)}")
    if e.max_stub is None:
        out.append(f"{tag}: give max_stub (mils): the most copper a load may hang off the "
                   "trunk, or for a source termination the most from the driver to the part")
    elif e.max_stub < 0:
        out.append(f"{tag}: max_stub cannot be negative")
    if len(e.nets) > 1:
        out.append(f"{tag}: a termination rules one net; give each net its own entry")
    return out


def _pdn_errors(board: LayoutBoard, brief: Brief, net_id: dict[str, int]) -> list[str]:
    """Why a pdn rule of the brief cannot be measured as written."""
    out: list[str] = []
    refs = {c.ref for c in board.components} | {p.comp for p in board.pads if p.comp}
    seen: set[str] = set()
    for k, r in enumerate(brief.pdn):
        tag = f"pdn {k + 1} ({r.name or 'unnamed'})"
        if r.name in seen:
            out.append(f"{tag}: another pdn rule has the name {r.name!r}")
        seen.add(r.name)
        if r.part not in refs:
            out.append(f"{tag}: no part {r.part!r} on the board")
            continue
        if not r.rails:
            out.append(f"{tag}: give rails, the power nets of {r.part}'s pins")
        bad = [n for n in r.rails if n not in net_id]
        if bad:
            out.append(f"{tag}: no net {bad} on the board")
        mine = {p.net for p in board.pads if p.comp == r.part}
        off = [n for n in r.rails if n in net_id and n not in mine]
        if off:
            out.append(f"{tag}: {r.part} has no pin on {off}")
        for c in r.decaps:
            pads = [p for p in board.pads if p.comp == c]
            if not pads:
                out.append(f"{tag}: no capacitor {c} on the board")
            elif not any(p.net in r.rails for p in pads):
                out.append(f"{tag}: {c} has no pad on {list(r.rails)}")
        if r.max_distance is None and r.min_vias is None:
            out.append(f"{tag}: give max_distance (mils), min_vias, or both")
        if r.max_distance is not None and r.max_distance <= 0:
            out.append(f"{tag}: max_distance must be over 0")
        if r.min_vias is not None:
            if r.min_vias < 1:
                out.append(f"{tag}: min_vias must be 1 or more")
            if r.via_reach is None or r.via_reach <= 0:
                out.append(f"{tag}: min_vias needs via_reach (mils over 0): how near a pad "
                           "a via counts as its own")
        if not r.source:
            out.append(f"{tag}: give its source (the IC's datasheet layout or decoupling "
                       "section, an application note)")
    return out


def _impedance_errors(board: LayoutBoard, e: NetRules, tag: str) -> list[str]:
    """Why an entry's impedance cannot be met as written: no dielectric in
    the stack, a target no width reaches, no width given, or a width off the
    target on a layer the entry may use. Each says the width every layer
    needs, so the AI can choose the layers and the width."""
    from . import impedance as zi

    if e.diff_impedance and not e.gap:
        return [f"{tag}: diff_impedance needs the pair's gap (mils)"]
    if e.impedance and e.diff_impedance:
        return [f"{tag}: give impedance or diff_impedance, not both"]
    geo = zi.geometry(board)
    # A name that is no copper layer is refused on its own; it is not a
    # gap in the stack.
    copper = set(board.copper_layers())
    layers = [l for l in (e.layers or board.signal_layers()) if l in copper]
    missing = [l for l in layers if l not in geo]
    if missing:
        return [f"{tag}: the board's stack gives no dielectric next to {missing}; set it in "
                "Altium's Layer Stack Manager, or leave those layers out of layers"]
    target, gap = e.target_ohms, (e.gap if e.diff_impedance else None)
    need = {l: zi.width_for(geo[l], target, gap) for l in layers}
    what = f"{target:g} ohm{' differential' if gap else ''}"
    out = [f"{tag}: no width gives {what} on {l}" for l, w in need.items() if w is None]
    if out:
        return out
    widths = ", ".join(f"{w:g} on {l}" for l, w in need.items())
    if e.width is None:
        return [f"{tag}: {what} needs a width of {widths} (IPC-2141 closed form, about 10%; "
                "a field solver has the last word). Give width, and layers whose widths agree"]
    out = []
    for l in layers:
        z = zi.z0(geo[l], e.width, gap)
        if z is None or abs(z - target) > target * e.tolerance_pct / 100.0:
            got = "no physical impedance" if z is None else f"{z:.1f} ohm"
            out.append(f"{tag}: width {e.width:g} gives {got} on {l}, not {what} "
                       f"+/-{e.tolerance_pct:g}%; there it needs {need[l]:g}")
    return out


def _inside(rings: list[Polygon], x: float, y: float) -> bool:
    hit = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
    return hit


# --- what the router obeys ----------------------------------------------------------

def masks(router, brief: Brief) -> dict[str, tuple[np.ndarray, Optional[np.ndarray]]]:
    """Net -> (the routing layers it may use, (L,) bool; the cells it may
    use, (ny, nx) bool, or None for all)."""
    from .grid import fill_polygon

    out = {}
    spec = router.grid.spec
    layers = list(router.grid.layers)
    for net, e in brief.of.items():
        ok = np.ones(len(layers), dtype=bool)
        if e.layers:
            ok = np.array([l in e.layers for l in layers], dtype=bool)
        area = None
        if e.region or e.keep_out:
            area = fill_polygon(spec, e.region) if e.region else np.ones((spec.ny, spec.nx), bool)
            if e.keep_out:
                area &= ~fill_polygon(spec, e.keep_out)
        if e.layers or area is not None:
            out[net] = (ok, area)
    return out


#: Bucket size, mils, of the holes reference_layers indexes.
HOLE_BUCKET = 100.0


def reference_layers(board: LayoutBoard, net: str, layer: str, gap: float) -> list[tuple]:
    """Where copper of ``net`` lies on each copper layer next to ``layer``:
    one (shapes, holes) per such layer. ``shapes`` are its planes, pours
    and regions there (None: the whole layer is its plane); ``holes``, an
    index for ``referenced``, the other nets' copper on that layer that its
    copper clears by ``gap``: pads (a through-hole pad by its hole where it
    has no copper there), tracks, and vias through the layer. A track on
    ``layer`` has its return path where it lies over a shape and clear of
    every hole of one of them."""
    from .. import geom

    order = board.copper_layers()
    if layer not in order:
        return []
    i = order.index(layer)
    out = []
    for k in (i - 1, i + 1):
        if not 0 <= k < len(order):
            continue
        near = order[k]
        plane = any(l.name == near and l.kind == "plane" and l.plane_net == net
                    for l in board.layers)
        shapes = [geom.polygon(r.outline, r.holes) for r in board.regions
                  if r.layer == near and r.net == net and len(r.outline) >= 3
                  and r.kind in ("pour", "copper", "pour_boundary", "plane")]
        if not plane and not shapes:
            continue
        holes = []
        for p in board.pads:
            if p.net == net:
                continue
            sh = p.shape_on(near)
            if sh is None and p.hole > 0:
                sh = geom.circle(p.x, p.y, p.hole)
            if sh is not None:
                holes.append(sh)
        holes += [geom.capsule(t.x1, t.y1, t.x2, t.y2, t.width) for t in board.tracks
                  if t.layer == near and t.net != net and not t.keepout]
        holes += [geom.circle(v.x, v.y, v.diameter) for v in board.vias if v.net != net
                  and near in board.layers_between(v.low_layer, v.high_layer)]
        index: dict[tuple[int, int], list] = {}
        for h in holes:
            x0, y0, x1, y1 = h.bbox
            for bx in range(int((x0 - gap) // HOLE_BUCKET), int((x1 + gap) // HOLE_BUCKET) + 1):
                for by in range(int((y0 - gap) // HOLE_BUCKET), int((y1 + gap) // HOLE_BUCKET) + 1):
                    index.setdefault((bx, by), []).append(h)
        out.append((None if plane else shapes, index))
    return out


def referenced(layers: list[tuple], gap: float, x: float, y: float) -> bool:
    """Whether (x, y) has its return path in ``layers`` (reference_layers)."""
    from .. import geom

    probe = geom.circle(x, y, 0.0)
    key = (int(x // HOLE_BUCKET), int(y // HOLE_BUCKET))
    for shapes, index in layers:
        if shapes is not None and not any(geom.point_in_poly(x, y, s) for s in shapes):
            continue
        if not any(geom.clearance(probe, h) < gap for h in index.get(key, ())):
            return True
    return False


def loop_hulls(router, brief: Brief) -> dict[str, np.ndarray]:
    """Net -> the cells, (ny, nx) bool, inside the hull of the pads of the
    brief's loops it is in, grown by its widest pad and two grid steps: the
    room a loop net is steered to keep to."""
    from ..geom import convex_hull, outline
    from .grid import fill_polygon

    pads = {p.key: p for p in router.board.pads if p.comp}
    grow = 2 * router.pitch
    rings: dict[str, list] = {}
    for lp in brief.loops:
        mine = [pads[m] for m in lp.members if m in pads]
        if len(mine) < 2:
            continue
        pts = []
        reach = 0.0
        for p in mine:
            for c in p.copper:
                s = p.shape_on(c.layer)
                if s is not None:
                    pts += outline(s)
                reach = max(reach, max(c.w, c.h) / 2)
            pts.append((p.x, p.y))
        r = grow + reach
        ring = [(x + r * math.cos(a * math.pi / 4), y + r * math.sin(a * math.pi / 4))
                for x, y in pts for a in range(8)]
        hull = convex_hull(ring)
        if len(hull) < 3:
            continue
        for p in mine:
            if p.net:
                rings.setdefault(p.net, []).append(hull)
    out = {}
    for net, rs in rings.items():
        m = np.zeros((router.grid.spec.ny, router.grid.spec.nx), dtype=bool)
        for ring in rs:
            m |= fill_polygon(router.grid.spec, [ring])
        out[net] = m
    return out


def area_walls(router, brief: Brief) -> list[tuple[np.ndarray, np.ndarray, frozenset]]:
    """The brief's areas on the router's grid: (the routing layers each
    bars, (L,) bool; its cells, (ny, nx) bool; the nets it allows)."""
    from .grid import fill_polygon

    layers = list(router.grid.layers)
    out = []
    for a in brief.areas:
        on = np.array([a.layers is None or l in a.layers for l in layers], dtype=bool)
        if on.any() and a.polygons:
            out.append((on, fill_polygon(router.grid.spec, a.polygons), frozenset(a.allow)))
    return out


def broken(brief: Brief, tracks, vias, before_vias: int = 0) -> dict[str, list[str]]:
    """Net -> the rules its new copper breaks (none: obeyed)."""
    out: dict[str, list[str]] = {}
    per_net_vias: dict[str, int] = {}
    for v in vias:
        per_net_vias[v.net] = per_net_vias.get(v.net, 0) + 1
        for a in brief.areas:
            if v.net not in a.allow and _inside(a.polygons, v.x, v.y):
                out.setdefault(v.net, []).append(
                    f"area {a.name or a.kind}: a via at ({v.x:.0f}, {v.y:.0f}) inside it")
    for t in tracks:
        pts = [(t.x1 + (t.x2 - t.x1) * f, t.y1 + (t.y2 - t.y1) * f) for f in (0.0, 0.5, 1.0)]
        for a in brief.areas:
            if a.bars(t.net, t.layer) and any(_inside(a.polygons, x, y) for x, y in pts):
                why = f"area {a.name or a.kind}: a track near ({pts[1][0]:.0f}, {pts[1][1]:.0f}) enters it"
                if not any(w.startswith(f"area {a.name or a.kind}") for w in out.get(t.net, [])):
                    out.setdefault(t.net, []).append(why)
    for t in tracks:
        e = brief.of.get(t.net)
        if e is None:
            continue
        bad = out.setdefault(t.net, [])
        if e.layers and t.layer not in e.layers and "layers" not in str(bad):
            bad.append(f"layers: a track on {t.layer}, outside {list(e.layers)}")
        if e.width is not None and abs(t.width - e.width) > 0.05 and "width" not in str(bad):
            bad.append(f"width: {t.width:g} where the brief asks {e.width:g}")
        pts = [(t.x1 + (t.x2 - t.x1) * f, t.y1 + (t.y2 - t.y1) * f) for f in (0.0, 0.5, 1.0)]
        if e.region and not all(_inside(e.region, x, y) for x, y in pts) \
                and "region" not in str(bad):
            bad.append(f"region: a track near ({pts[1][0]:.0f}, {pts[1][1]:.0f}) leaves it")
        if e.keep_out and any(_inside(e.keep_out, x, y) for x, y in pts) \
                and "keep_out" not in str(bad):
            bad.append(f"keep_out: a track near ({pts[1][0]:.0f}, {pts[1][1]:.0f}) enters it")
    for net, k in per_net_vias.items():
        e = brief.of.get(net)
        if e is not None and e.max_vias is not None and k > e.max_vias:
            out.setdefault(net, []).append(f"max_vias: {k} vias where the brief allows {e.max_vias}")
    return {n: b for n, b in out.items() if b}


# --- where a board's brief is kept --------------------------------------------------

def _folder() -> Path:
    from ...config import get_config
    return Path(get_config().workspace_dir) / "route_briefs"


def _file(board_path: str) -> Path:
    key = hashlib.sha1(str(Path(board_path)).lower().replace("\\", "/").encode()).hexdigest()[:16]
    return _folder() / f"{key}.json"


def load(board_path: str) -> Brief:
    try:
        data = json.loads(_file(board_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Brief()
    return Brief.from_list(data.get("entries", []), data.get("areas", []), data.get("loops", []),
                           data.get("barriers", []), data.get("assembly"),
                           data.get("orient", []), data.get("pdn", []),
                           data.get("thermal", []))


def save(board_path: str, brief: Brief) -> Path:
    import os

    from ...atomicfile import replace_with_retry
    path = _file(board_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"board": board_path, "entries": brief.as_list(),
                               "areas": brief.areas_list(), "loops": brief.loops_list(),
                               "barriers": brief.barriers_list(),
                               "assembly": brief.assembly_dict(),
                               "orient": brief.orient_list(),
                               "pdn": brief.pdn_list(),
                               "thermal": brief.thermal_list()},
                              indent=1), encoding="utf-8")
    replace_with_retry(tmp, path)
    return path


def _decisions_file(board_path: str) -> Path:
    f = _file(board_path)
    return f.with_name(f.stem + ".decisions.json")


def load_decisions(board_path: str) -> dict[str, dict]:
    """Finding id -> the AI's answer kept for this board: {text, reason}.
    An answer stands for the same finding (the same text) in later rounds."""
    try:
        data = json.loads(_decisions_file(board_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {d["id"]: d for d in data.get("decisions", []) if isinstance(d, dict) and d.get("id")}


def record_decisions(board_path: str, decided: list[dict]) -> Path:
    import os
    import time

    from ...atomicfile import replace_with_retry
    kept = load_decisions(board_path)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for d in decided:
        kept[d["id"]] = {**d, "at": stamp}
    path = _decisions_file(board_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"board": board_path, "decisions": list(kept.values())},
                              indent=1), encoding="utf-8")
    replace_with_retry(tmp, path)
    return path


__all__ = ["CRITICAL", "AreaRule", "BarrierRule", "LoopRule", "NetRules", "PdnRule", "Brief", "critical_nets", "check", "masks",
           "area_walls", "broken", "load", "save", "load_decisions", "record_decisions"]
