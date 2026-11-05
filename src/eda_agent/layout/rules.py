# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The board's own rules, resolved per object the way Altium resolves them.

A clearance is not one number. It belongs to the highest-priority enabled
rule whose two scopes match the two objects in hand, either way round, so
the router must ask "what clearance between THIS track and THAT pad?"
rather than use a global figure. The old router used one global
clearance, taken from defaults rather than the board.

Rule values come from the typed read where the Pascal could reach them
(clearance gap, width limits, hole size) and from Altium's descriptor text
otherwise. Scope expressions are Altium's query language; the common
predicates are evaluated here, and a scope using one that is not is
reported rather than guessed, because treating an unknown scope as "All"
would apply a rule to objects it was never meant for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..units import MILS_PER_MM
from .model import LayoutBoard, Rule

# Descriptor text starts with the rule's kind in Altium's own words.
_KINDS = (
    ("clearance constraint", "clearance"),
    ("width constraint", "width"),
    ("routing via", "via"),
    ("differential pairs routing", "diffpair"),
    ("routing layers", "routing_layers"),
    ("routing corners", "routing_corners"),
    ("routing topology", "routing_topology"),
    ("routing priority", "routing_priority"),
    ("hole size constraint", "hole_size"),
    ("hole to hole clearance", "hole_to_hole"),
    ("board outline clearance", "board_outline"),
    ("component clearance", "component_clearance"),
    ("power plane clearance", "plane_clearance"),
    ("power plane connect", "plane_connect"),
    ("polygon connect", "polygon_connect"),
    ("short-circuit", "short_circuit"),
    ("un-routed net", "unrouted_net"),
    ("smd neck-down", "smd_neckdown"),
    ("smd to corner", "smd_to_corner"),
    ("smd to plane", "smd_to_plane"),
    ("fanout control", "fanout"),
    ("minimum annular ring", "annular_ring"),
    ("solder mask expansion", "soldermask"),
    ("minimum solder mask sliver", "mask_sliver"),
    ("paste mask expansion", "pastemask"),
    ("silk to solder mask", "silk_to_mask"),
    ("silk to silk", "silk_to_silk"),
    ("height", "height"),
)

#: Altium's solder mask expansion when no rule sets one, in mils.
DEFAULT_MASK_EXPANSION = 4.0

_VALUE = re.compile(r"(\w+)\s*=\s*(-?[\d.]+)\s*(mil|mm)?", re.IGNORECASE)


def rule_type(rule: Rule) -> str:
    text = (rule.descriptor or "").strip().lower()
    for prefix, kind in _KINDS:
        if text.startswith(prefix):
            return kind
    return "other"


def descriptor_values(descriptor: str) -> dict[str, float]:
    """``Name=value unit`` pairs from a descriptor, in mils."""
    out: dict[str, float] = {}
    for name, number, unit in _VALUE.findall(descriptor or ""):
        v = float(number)
        if (unit or "").lower() == "mm":
            v *= MILS_PER_MM
        out[name.lower()] = v
    return out


# ---------------------------------------------------------------------------
# Scope expressions
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\s*(?:(\()|(\))|('(?:[^']|'')*')|(\w+)|(,))")


class _Unknown(Exception):
    """A scope uses a predicate this evaluator does not know."""


@dataclass(frozen=True)
class ObjectFacts:
    """What a scope expression can ask about one object."""

    kind: str                 # pad | via | track | arc | region | fill
    net: str = ""
    net_classes: frozenset = frozenset()
    layer: str = ""
    comp: str = ""
    in_polygon: bool = False
    smd: bool = False
    diff_pair: str = ""


def _tokens(expr: str) -> list[tuple[str, str]]:
    out = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos:].strip() == "":
                break
            raise _Unknown(f"cannot tokenise scope near {expr[pos:pos + 20]!r}")
        pos = m.end()
        if m.group(1):
            out.append(("(", "("))
        elif m.group(2):
            out.append((")", ")"))
        elif m.group(3):
            out.append(("str", m.group(3)[1:-1].replace("''", "'")))
        elif m.group(4):
            out.append(("word", m.group(4)))
        elif m.group(5):
            out.append((",", ","))
    return out


class _Parser:
    """Recursive descent over Altium's query syntax: or, and, not, calls."""

    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        node = self.or_expr()
        if self.i != len(self.t):
            raise _Unknown("trailing tokens in scope")
        return node

    def or_expr(self):
        left = self.and_expr()
        while self.peek()[0] == "word" and self.peek()[1].lower() == "or":
            self.take()
            left = ("or", left, self.and_expr())
        return left

    def and_expr(self):
        left = self.not_expr()
        while self.peek()[0] == "word" and self.peek()[1].lower() == "and":
            self.take()
            left = ("and", left, self.not_expr())
        return left

    def not_expr(self):
        if self.peek()[0] == "word" and self.peek()[1].lower() == "not":
            self.take()
            return ("not", self.not_expr())
        return self.atom()

    def atom(self):
        kind, val = self.take()
        if kind == "(":
            node = self.or_expr()
            if self.take()[0] != ")":
                raise _Unknown("unbalanced parenthesis in scope")
            return node
        if kind != "word":
            raise _Unknown(f"unexpected token {val!r} in scope")
        args = []
        if self.peek()[0] == "(":
            self.take()
            while self.peek()[0] not in (")", None):
                k, v = self.take()
                if k in ("str", "word"):
                    args.append(v)
                elif k != ",":
                    raise _Unknown("unexpected token in call arguments")
            if self.take()[0] != ")":
                raise _Unknown("unterminated call in scope")
        return ("call", val.lower(), tuple(args))


def _eval(node, f: ObjectFacts) -> bool:
    op = node[0]
    if op == "or":
        return _eval(node[1], f) or _eval(node[2], f)
    if op == "and":
        return _eval(node[1], f) and _eval(node[2], f)
    if op == "not":
        return not _eval(node[1], f)
    name, args = node[1], node[2]
    a0 = args[0] if args else ""
    if name == "all":
        return True
    if name == "innet":
        return f.net == a0
    if name == "innetclass":
        return a0 in f.net_classes
    if name == "incomponent":
        return f.comp == a0
    if name == "onlayer":
        return f.layer == a0 or f.layer.replace(" ", "") == a0.replace(" ", "")
    if name in ("indifferentialpair",):
        return f.diff_pair == a0
    if name == "ispad":
        return f.kind == "pad"
    if name == "isvia":
        return f.kind == "via"
    if name == "istrack":
        return f.kind == "track"
    if name == "isarc":
        return f.kind == "arc"
    if name in ("isregion", "isfill"):
        return f.kind == "region"
    if name in ("inpolygon", "ispolygon"):
        return f.in_polygon
    if name == "issmtpad":
        return f.kind == "pad" and f.smd
    if name == "isthpad":
        return f.kind == "pad" and not f.smd
    raise _Unknown(f"scope predicate {name} is not evaluated here")


#: Every predicate ``_eval`` answers. A scope naming any other is unknown
#: when it is read, not when it is first matched: matched, the failure only
#: made the rule look as if it did not apply, so a check that skipped it
#: still reported a pass.
_PREDICATES = frozenset({
    "all", "innet", "innetclass", "incomponent", "onlayer",
    "indifferentialpair", "ispad", "isvia", "istrack", "isarc", "isregion",
    "isfill", "inpolygon", "ispolygon", "issmtpad", "isthpad",
})


def _check_known(node) -> None:
    if node[0] in ("or", "and"):
        _check_known(node[1])
        _check_known(node[2])
    elif node[0] == "not":
        _check_known(node[1])
    elif node[1] not in _PREDICATES:
        raise _Unknown(f"scope predicate {node[1]} is not evaluated here")


# ---------------------------------------------------------------------------

@dataclass
class ResolvedRule:
    rule: Rule
    type: str
    values: dict[str, float]
    scope1: Any = None
    scope2: Any = None
    unknown: str = ""          # why a scope could not be evaluated


@dataclass
class RuleSet:
    rules: list[ResolvedRule] = field(default_factory=list)
    net_classes: dict[str, frozenset] = field(default_factory=dict)
    diff_pair_of: dict[str, str] = field(default_factory=dict)
    _memo: dict = field(default_factory=dict)

    @classmethod
    def from_board(cls, board: LayoutBoard) -> "RuleSet":
        classes: dict[str, set] = {}
        for cname, nets in board.net_classes.items():
            for n in nets:
                classes.setdefault(n, set()).add(cname)
        pairs = {}
        for dp in board.diff_pairs:
            pairs[dp.positive] = dp.name
            pairs[dp.negative] = dp.name
        out = cls(net_classes={n: frozenset(c) for n, c in classes.items()},
                  diff_pair_of=pairs)
        for r in board.rules:
            if not r.enabled:
                continue
            values = descriptor_values(r.descriptor)
            values.update({k.lower(): v for k, v in (r.values or {}).items()})
            rr = ResolvedRule(r, rule_type(r), values)
            try:
                rr.scope1 = _Parser(_tokens(r.scope1 or "All")).parse()
                rr.scope2 = _Parser(_tokens(r.scope2 or "All")).parse()
                _check_known(rr.scope1)
                _check_known(rr.scope2)
            except _Unknown as exc:
                rr.unknown = str(exc)
            out.rules.append(rr)
        out.rules.sort(key=lambda rr: rr.rule.priority)
        return out

    def of_type(self, kind: str) -> list[ResolvedRule]:
        return [r for r in self.rules if r.type == kind]

    def unevaluated(self, kinds=None) -> list[dict]:
        """Rules whose scope could not be evaluated, of ``kinds`` when given."""
        return [{"rule": r.rule.name, "type": r.type, "why": r.unknown}
                for r in self.rules
                if r.unknown and (kinds is None or r.type in kinds)]

    # -- matching -----------------------------------------------------------

    def facts(self, item, board: LayoutBoard) -> ObjectFacts:
        comp = item.comp
        smd = False
        in_poly = False
        if item.kind == "pad":
            smd = board.pads[item.idx].is_smd
        elif item.kind == "region":
            in_poly = board.regions[item.idx].kind == "pour"
        return ObjectFacts(item.kind, item.net,
                           self.net_classes.get(item.net, frozenset()),
                           item.layer, comp, in_poly, smd,
                           self.diff_pair_of.get(item.net, ""))

    def _matches(self, node, facts: ObjectFacts) -> Optional[bool]:
        key = (id(node), facts)
        if key in self._memo:
            return self._memo[key]
        try:
            v = _eval(node, facts)
        except _Unknown:
            v = None
        self._memo[key] = v
        return v

    def binary(self, kind: str, fa: ObjectFacts, fb: ObjectFacts) -> Optional[ResolvedRule]:
        """The rule of ``kind`` that governs the pair, highest priority first."""
        for rr in self.rules:
            if rr.type != kind or rr.unknown:
                continue
            s1a, s2b = self._matches(rr.scope1, fa), self._matches(rr.scope2, fb)
            if s1a and s2b:
                return rr
            s1b, s2a = self._matches(rr.scope1, fb), self._matches(rr.scope2, fa)
            if s1b and s2a:
                return rr
        return None

    def unary(self, kind: str, fa: ObjectFacts) -> Optional[ResolvedRule]:
        for rr in self.rules:
            if rr.type != kind or rr.unknown:
                continue
            if self._matches(rr.scope1, fa):
                return rr
        return None

    # -- the questions engines ask -------------------------------------------

    def clearance(self, a, b, board: LayoutBoard) -> float:
        rr = self.binary("clearance", self.facts(a, board), self.facts(b, board))
        return rr.values.get("gap", 0.0) if rr else self.default_clearance()

    def default_clearance(self) -> float:
        for rr in self.of_type("clearance"):
            if rr.rule.scope1.strip().lower() == "all" and rr.rule.scope2.strip().lower() == "all":
                return rr.values.get("gap", 0.0)
        return 0.0

    def max_clearance(self) -> float:
        gaps = [rr.values.get("gap", 0.0) for rr in self.of_type("clearance")]
        return max(gaps) if gaps else 0.0

    def mask_expansion(self, facts: ObjectFacts) -> float:
        """The solder mask expansion the board's rule gives this object.

        Altium's own default, 4 mil, when no rule covers it."""
        rr = self.unary("soldermask", facts)
        return rr.values.get("expansion", DEFAULT_MASK_EXPANSION) if rr else DEFAULT_MASK_EXPANSION

    def mask_sliver(self, fa: ObjectFacts, fb: ObjectFacts) -> float:
        """The narrowest mask web allowed between two openings; 0 for none."""
        rr = self.binary("mask_sliver", fa, fb)
        return rr.values.get("gap", 0.0) if rr else 0.0

    def width(self, facts: ObjectFacts) -> dict[str, float]:
        """Min, preferred and max track width for this net on this layer."""
        rr = self.unary("width", facts)
        if not rr:
            return {}
        v = rr.values
        return {"min": v.get("min", v.get("minwidth", 0.0)),
                "preferred": v.get("preferred", v.get("preferedwidth",
                                   v.get("preferredwidth", 0.0))),
                "max": v.get("max", v.get("maxwidth", 0.0))}
