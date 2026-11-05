# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What the board's rules say a router must do, per net.

Track width and via style come from the board's width and via rules, per
net. Clearances come from the clearance rules, per pair of owners, as a
row per routed net indexed by the owner id the grid stores, so a whole
window of cells is checked in one numpy lookup.

Nets that every clearance rule treats alike share one row: most boards
have a handful of rule classes and hundreds of nets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..model import LayoutBoard
from ..rules import ObjectFacts, RuleSet

#: Scope predicates a net alone answers; any other in a clearance rule
#: (a component, a layer, a pad's type, polygon copper) makes the rule
#: object-specific.
NET_PREDICATES = {"all", "innet", "innetclass", "indifferentialpair",
                  "ispad", "isvia", "istrack", "isarc", "isregion", "isfill"}
from .grid import EDGE, KEEPOUT, NETLESS, NONE

OBSTACLE_KINDS = ("pad", "track", "via", "region")

#: Row offset: owner id -> row index. Ids run from KEEPOUT (-2) upwards;
#: NONE (-9) is folded onto its own slot.
_OFFSET = 3


def _slot(owner: np.ndarray) -> np.ndarray:
    return np.where(owner == NONE, 0, owner + _OFFSET)


@dataclass
class ViaStyle:
    diameter: float
    hole: float


class RouteRules:
    def __init__(self, board: LayoutBoard, rules: RuleSet, net_id: dict[str, int]):
        self.board = board
        self.rules = rules
        self.net_id = net_id
        self.names = {i: n for n, i in net_id.items()}
        self.default_clearance = rules.default_clearance() or 6.0
        edge = rules.of_type("board_outline")
        self.edge_clearance = edge[0].values.get("gap", 0.0) if edge else 0.0
        self._sig: dict[int, tuple] = {}
        self._pair: dict[tuple, float] = {}
        self._rows: dict[tuple, np.ndarray] = {}
        self._crules = [rr for rr in rules.of_type("clearance") if not rr.unknown]
        self._grow: dict[tuple, float] = {}
        self.object_specific = any((_calls(rr.scope1) | _calls(rr.scope2)) - NET_PREDICATES
                                   for rr in self._crules)

    # -- facts -------------------------------------------------------------

    def facts(self, net: str, kind: str, polygon: bool = False, layer: str = "",
              comp: str = "", smd: bool = False) -> ObjectFacts:
        rs = self.rules
        return ObjectFacts(kind, net, rs.net_classes.get(net, frozenset()), layer,
                           comp, polygon, smd, rs.diff_pair_of.get(net, ""))

    def _signature(self, owner: int) -> tuple:
        if owner in self._sig:
            return self._sig[owner]
        net = self.names.get(owner, "")
        sig = []
        for rr in self._crules:
            for kind in ("track",) + OBSTACLE_KINDS:
                f = self.facts(net, kind)
                sig.append((self.rules._matches(rr.scope1, f), self.rules._matches(rr.scope2, f)))
        self._sig[owner] = tuple(sig)
        return self._sig[owner]

    def group(self, owner: int) -> tuple:
        """What every clearance rule says of this owner's copper: owners
        alike here need the same clearance from anything."""
        return self._signature(owner)

    def pair_clearance(self, routed: int, owner: int, polygon: bool = False) -> float:
        """Clearance from a track of ``routed`` (or its pour copper, with
        ``polygon``) to copper of ``owner``."""
        if owner == EDGE:
            return self.edge_clearance
        if owner == KEEPOUT or owner == NONE:
            return 0.0
        key = (self._signature(routed), self._signature(owner), polygon)
        if key in self._pair:
            return self._pair[key]
        ft = self.facts(self.names.get(routed, ""), "region" if polygon else "track", polygon)
        other = self.names.get(owner, "") if owner != NETLESS else ""
        best = 0.0
        for kind in OBSTACLE_KINDS:
            rr = self.rules.binary("clearance", ft, self.facts(other, kind))
            gap = rr.values.get("gap", 0.0) if rr else self.default_clearance
            best = max(best, gap)
        self._pair[key] = best
        return best

    def clearance_row(self, routed: int, polygon: bool = False) -> np.ndarray:
        """Clearance to every owner id, indexed through ``lookup``."""
        if (routed, polygon) in self._rows:
            return self._rows[(routed, polygon)]
        n = max(self.net_id.values(), default=0)
        row = np.zeros(n + _OFFSET + 1, dtype=np.float32)
        for owner in [KEEPOUT, EDGE, NETLESS] + list(range(1, n + 1)):
            row[owner + _OFFSET] = self.pair_clearance(routed, owner, polygon)
        self._rows[(routed, polygon)] = row
        return row

    def grow(self, kind: str, net: str, comp: str, layer: str, smd: bool = False,
             polygon: bool = False, routed=()) -> float:
        """How much more than its net's clearance a routed track or pour
        of any of ``routed`` must keep from this one object: what a rule
        scoped to its component, its layer or its kind of pad asks beyond
        the rule its net falls under. 0 on a board with no such rule.

        The grid holds distances per net, so the object's shape is grown
        by this much instead. A board can keep a wide clearance round a
        list of connectors and a narrow one everywhere else; seeing only
        the narrow one, the router let a pour inside a connector's
        clearance.
        """
        if not self.object_specific:
            return 0.0
        key = (kind, net, comp, layer, smd, polygon, tuple(routed))
        if key in self._grow:
            return self._grow[key]
        fo = self.facts(net, kind, polygon, layer, comp, smd)
        owner = self.net_id.get(net, NETLESS) if net else NETLESS
        out = 0.0
        for rid in routed:
            name = self.names.get(rid, "")
            for poly in (False, True):
                ft = self.facts(name, "region" if poly else "track", poly, layer)
                rr = self.rules.binary("clearance", ft, fo)
                gap = rr.values.get("gap", 0.0) if rr else self.default_clearance
                out = max(out, gap - self.pair_clearance(rid, owner, poly))
        self._grow[key] = out
        return out

    @staticmethod
    def lookup(row: np.ndarray, owner: np.ndarray) -> np.ndarray:
        return row[_slot(owner)]

    # -- widths and vias -----------------------------------------------------

    def width(self, net: str, pads=(), cap: bool = True) -> float:
        """The rule's preferred width, but never wider than the narrowest
        pad the net lands on (unless ``cap`` is False: the router then
        necks down at that pad alone), nor narrower than the rule's minimum.

        A rule's preferred width is not always a width anyone routes: a
        rule can prefer its maximum, far wider than the board is routed
        at, and a track that wide leaving a small pad runs straight into
        the next one. A person necks down; this keeps a net to what its
        smallest pad takes.
        """
        w = self.rules.width(self.facts(net, "track"))
        width = w.get("preferred") or w.get("min") or 6.0
        sizes = [min(c.w, c.h) for p in pads for c in p.copper if min(c.w, c.h) > 0]
        if sizes and cap:
            width = min(width, min(sizes))
        if w.get("min"):
            width = max(width, w["min"])
        return width

    def min_width(self, net: str) -> float:
        """The narrowest the width rule allows this net."""
        w = self.rules.width(self.facts(net, "track"))
        return w.get("min") or min(6.0, w.get("preferred") or 6.0)

    def via(self, net: str) -> ViaStyle:
        rr = self.rules.unary("via", self.facts(net, "via"))
        v = rr.values if rr else {}

        def pick(pref, lo, hi, default):
            x = v.get(pref) or default
            if v.get(lo):
                x = max(x, v[lo])
            if v.get(hi):
                x = min(x, v[hi])
            return x

        d = pick("preferedwidth", "minwidth", "maxwidth", v.get("preferredwidth") or 20.0)
        h = pick("preferredholewidth", "minholewidth", "maxholewidth", 10.0)
        return ViaStyle(d, min(h, d - 4.0) if d > 8.0 else h)

    #: Copper round a via's hole when the board sets no annular ring rule.
    DEFAULT_RING = 3.0

    def ring(self) -> float:
        rr = self.rules.of_type("annular_ring")
        if rr:
            v = rr[0].values
            return v.get("minimum", v.get("min", self.DEFAULT_RING)) or self.DEFAULT_RING
        return self.DEFAULT_RING

    def vias(self, net: str) -> list[ViaStyle]:
        """Via styles from largest to smallest: preferred, a middle size,
        and the smallest the rule allows.

        Where the preferred via will not fit, as between the balls of a
        fine-pitch BGA, a smaller one often will: the first public board
        read put 26 of them straight into its BGA's pads. The smallest is
        the rule's minimum hole with a ring round it, since a rule's
        minimum width is not always a via at all: one public board allows
        a 6 mil via with a 6 mil hole, and prefers 26 mil where the person
        used 18 and 12 throughout.
        """
        pref = self.via(net)
        rr = self.rules.unary("via", self.facts(net, "via"))
        v = rr.values if rr else {}
        h = v.get("minholewidth") or pref.hole
        d = max(v.get("minwidth") or 0.0, h + 2 * self.ring())
        if d >= pref.diameter - 1e-6:
            return [pref]
        smallest = ViaStyle(d, h)
        out = [pref]
        mid_d = round((pref.diameter + d) / 2)
        mid_h = round((pref.hole + h) / 2)
        if d + 2 < mid_d < pref.diameter - 2 and mid_h < mid_d - 2 * self.ring() + 1e-6:
            out.append(ViaStyle(float(mid_d), float(mid_h)))
        out.append(smallest)
        return out


def _calls(node) -> set[str]:
    """The predicate names a parsed scope calls."""
    if not node:
        return set()
    if node[0] == "call":
        return {node[1]}
    return set().union(*(_calls(n) for n in node[1:] if isinstance(n, tuple)))
