# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Generic motif recognition for schematic layout.

Detects canonical sub-circuit patterns in a ``DesignPlan``'s netlist
(bypass cap, voltage divider, RC chain, pull-up, pull-down, ...). Each
match collapses to a meta-node with a frozen relative layout so the
global placer can optimise ~5-15 metas instead of ~50-200 raw parts.
The resulting schematic shows canonical sub-circuit drawings (a vertical
divider, a cap right next to its rail, etc.) instead of an emergent
force-directed constellation.

This is NOT a topology template. Motifs are pattern-level (two
resistors with an internal mid-tap, a cap between a power net and a
ground net) and detect the same structures wherever they show up:
buck, LDO, MCU power chain, opamp filter, sensor front-end.

The framework is bipartite VF2 subgraph isomorphism on a
component-net graph, with two extra constraints:

- Closed internal nets: a pattern net listed in ``internal_nets``
  must be mapped to a host net with the EXACT same degree, so
  external fan-out kills the match. This is what stops every R-R
  pair from matching ``voltage_divider`` -- the mid-tap must touch
  only the two resistors.
- Zone coherence: by default, all components claimed by one match
  must share the same ``Part.zone`` (functional block). A motif that
  spans across blocks is almost always a false positive.

Overlap arbitration: multiple motifs can claim the same component
(e.g. a divider's Rtop also matches ``pull_up_r``). Resolution is a
greedy maximum independent set by specificity, then component count,
then edge count.

References:

- Kunal et al. "GANA: GCN-Based Automated Netlist Annotation" (DATE 2020)
- Kunal et al. TCAD 2023 (hierarchical analog annotation)
- Sapatnekar et al. "ALIGN: A System for Automating Analog Layout"
- Milo et al. "Network Motifs: Simple Building Blocks of Complex
  Networks" (Science, 2002)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

import networkx as nx
from networkx.algorithms.isomorphism import GraphMatcher

from eda_agent.design.plan import DesignPlan, Net


# ---------------------------------------------------------------------------
# Component kind classification
# ---------------------------------------------------------------------------

# Refdes prefix -> generic kind letter. Library- and project-agnostic;
# falls back to the alphabetic prefix itself for unrecognised letters so
# the matcher never silently accepts a wildcard.
_KIND_BY_PREFIX: dict[str, str] = {
    "R": "R", "RA": "R", "RN": "R", "RV": "R",
    "C": "C", "CN": "C",
    "L": "L", "LN": "L",
    "D": "D", "TVS": "D", "ZD": "D", "LED": "D",
    "Q": "Q", "M": "Q",
    "U": "U", "IC": "U",
    "Y": "Y", "X": "Y", "XTAL": "Y",
    "FB": "FB",
    "T": "T", "TR": "T",
    "J": "J", "P": "J",
    "SW": "SW", "S": "SW", "K": "SW", "RY": "SW",
    "F": "F",
    "LS": "LS", "SP": "LS",
    "MK": "MK",
}


def _kind_from_refdes(refdes: str) -> str:
    """Map an Altium-style refdes to a generic kind letter (R, C, L, D...).

    Falls back to the uppercased alphabetic prefix when unknown so a
    typo or new refdes class is visible rather than silently matched.
    """
    prefix = ""
    for ch in refdes:
        if ch.isalpha():
            prefix += ch
        else:
            break
    if not prefix:
        return "?"
    return _KIND_BY_PREFIX.get(prefix.upper(), prefix.upper())


def _net_role_tag(net: Net) -> str:
    """Map a Net to its role tag used in pattern matching.

    Priority: ``is_power`` -> ``"power"``, ``is_ground`` -> ``"ground"``,
    else the explicit ``role`` field, else ``"signal"``.
    """
    if net.is_power:
        return "power"
    if net.is_ground:
        return "ground"
    return net.role or "signal"


# ---------------------------------------------------------------------------
# Bipartite circuit graph
# ---------------------------------------------------------------------------


# Connector roles that DELIVER power to the board. A net that carries a
# decoupling cap to ground AND reaches one of these is a power rail even when
# the planner forgot to flag it (easy to miss on a raw input / regulator
# output, which are not the obvious "VCC").
_POWER_DELIVERY_ROLES = frozenset({
    "input_conn", "vin_conn", "power_in",
    "output_conn", "vout_conn", "power_out",
})


def _infer_power_nets(plan: DesignPlan) -> set[str]:
    """Power rails the planner did not flag, found structurally and SAFELY.

    A net is inferred power when it (a) is the non-ground leg of a two-pin
    capacitor whose other leg is a ground net -- the decoupling signature --
    AND (b) reaches a power-delivery connector (``_POWER_DELIVERY_ROLES``).
    The connector guard is what keeps this from misfiring on an RC filter's
    output node: a filter's mid/out node has the same cap-to-ground signature
    but never touches a power connector, so it stays ``signal`` and the
    rc_lowpass / rc_highpass motifs still match it.

    Mirrors the PCB engine's structural decap-rail detection, but tighter:
    the PCB ``fanout >= 3`` rule would also tag a filter node, which on the
    schematic side would wrongly suppress a legitimate filter motif.
    """
    ground = {
        n.name for n in plan.nets
        if n.is_ground or (n.role or "") == "ground"
    }
    if not ground:
        return set()
    power_parts = {
        p.refdes for p in plan.parts
        if (p.role or "") in _POWER_DELIVERY_ROLES
    }
    if not power_parts:
        return set()

    nets_by_name = {n.name: n for n in plan.nets}
    parts_nets: dict[str, set[str]] = {}
    for n in plan.nets:
        for pr in n.pins:
            parts_nets.setdefault(pr.refdes, set()).add(n.name)

    inferred: set[str] = set()
    for p in plan.parts:
        if _kind_from_refdes(p.refdes) != "C":
            continue
        legs = parts_nets.get(p.refdes, set())
        if len(legs) != 2 or not (legs & ground):
            continue
        rail = next(iter(legs - ground), None)
        rn = nets_by_name.get(rail) if rail else None
        if rn is None or rn.is_power or rn.is_ground or (rn.role or "") in ("power", "ground"):
            continue
        if {pr.refdes for pr in rn.pins} & power_parts:
            inferred.add(rail)
    return inferred


def build_circuit_graph(plan: DesignPlan) -> nx.MultiGraph:
    """Build the bipartite component-net graph the matcher operates on.

    Nodes:
      - ``("C", refdes)`` -- component, attrs ``bipartite="component"``,
        ``kind`` (R / C / L / U / ...), ``zone`` (None or a Zone.name).
      - ``("N", net_name)`` -- net, attrs ``bipartite="net"``, ``role``
        (power / ground / signal / feedback / ...).

    Edges carry ``pin`` (the pin id on the component side) so a future
    pin-aware matcher can constrain (currently the matcher ignores it).
    Multigraph because a single component can connect to one net on
    multiple pins (rare in schematics, common in transistors).
    """
    G = nx.MultiGraph()
    inferred_power = _infer_power_nets(plan)
    for p in plan.parts:
        G.add_node(
            ("C", p.refdes),
            bipartite="component",
            kind=_kind_from_refdes(p.refdes),
            zone=p.zone,
        )
    for n in plan.nets:
        role = _net_role_tag(n)
        # Promote a structurally-detected, planner-unflagged power rail so its
        # decoupling caps recognise as bypass_cap (not a stray rc_lowpass).
        if role == "signal" and n.name in inferred_power:
            role = "power"
        G.add_node(
            ("N", n.name),
            bipartite="net",
            role=role,
        )
        for pr in n.pins:
            G.add_edge(("C", pr.refdes), ("N", n.name), pin=str(pr.pin))
    return G


# ---------------------------------------------------------------------------
# Motif definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Motif:
    """A motif: pattern graph + canonical sub-layout + arbitration weight.

    The pattern is a bipartite multigraph using the same node-encoding
    as the host (component nodes ``("C", name)`` with ``kind``, net
    nodes ``("N", name)`` with ``role``).

    ``internal_nets`` lists pattern-local net names that must be CLOSED
    in the host (host node mapped to it must have the same degree as in
    the pattern; no external fan-out). Nets not in this set are "ports"
    that may have additional connections in the host.

    ``canonical`` maps a pattern component name to ``(dx, dy)`` in mils,
    relative to the meta-node anchor at ``(0, 0)``. The splat step
    converts these to absolute positions. The IC anchor (when set) is
    NOT listed in ``canonical`` -- it keeps its global position.

    ``specificity`` is the arbitration weight; higher specificity wins
    when two motifs claim the same component. Patterns that constrain
    the host with specific roles (power, ground) or closed nets score
    higher than purely shape-based ones.

    ``ic_anchor`` (optional): pattern-local refdes of the central IC
    around which the motif is laid out. When set:

    - The IC IS in the pattern graph (so VF2 must match a real U with
      the right pin connectivity).
    - The IC is NOT in ``canonical`` (it keeps its placed position).
    - The IC is NOT claimed exclusively by the match -- multiple
      IC-anchored motifs can share the same U (a buck's fb_divider +
      boot_cap + lc_output all coexist around one regulator).
    - Splat uses the IC's actual placed position as motif (0, 0).

    When ``ic_anchor`` is None the motif is self-contained: the first
    component in ``canonical`` iteration order is the splat anchor and
    its canonical offset defines where motif (0, 0) sits in absolute
    coords.
    """

    name: str
    pattern: nx.MultiGraph
    internal_nets: frozenset[str]
    canonical: dict[str, tuple[int, int]]
    specificity: int = 0
    ic_anchor: Optional[str] = None


# ---------------------------------------------------------------------------
# VF2 matcher with internal-net + zone constraints
# ---------------------------------------------------------------------------


def _node_match(host_attrs: dict, pat_attrs: dict) -> bool:
    """VF2 node compatibility test.

    Pattern attrs DEFINE the constraint; host must satisfy. Pattern
    nodes carrying ``kind`` (or ``role``) of ``None`` or ``"*"`` are
    wildcards. Components match on ``kind``; nets match on ``role``.
    """
    if host_attrs.get("bipartite") != pat_attrs.get("bipartite"):
        return False
    if pat_attrs["bipartite"] == "component":
        pat_kind = pat_attrs.get("kind")
        if pat_kind is None or pat_kind == "*":
            return True
        return host_attrs.get("kind") == pat_kind
    # bipartite == "net"
    pat_role = pat_attrs.get("role")
    if pat_role is None or pat_role == "*":
        return True
    host_role = host_attrs.get("role")
    if pat_role == "signal":
        # A motif's generic "signal" net matches any SIGNAL-FAMILY net, not
        # just role=="signal": a net the planner tagged with a specific signal
        # subtype (analog_sensitive, clock, control, differential, switch,
        # feedback, high_current) is still a signal, so the motif must still
        # fire. Without this, adding a documented role hint silently breaks
        # recognition. Power and ground are NOT signal.
        return host_role not in ("power", "ground")
    return host_role == pat_role


class _MotifMatcher(GraphMatcher):
    """VF2 with the closed-internal-net constraint baked in."""

    def __init__(self, host: nx.MultiGraph, motif: Motif):
        super().__init__(host, motif.pattern, node_match=_node_match)
        self._motif = motif

    def semantic_feasibility(self, g1_node, g2_node):  # noqa: D401
        if not super().semantic_feasibility(g1_node, g2_node):
            return False
        # If pattern node is an internal net, host node must match its
        # degree exactly -- no external fan-out allowed.
        if (
            isinstance(g2_node, tuple)
            and g2_node[0] == "N"
            and g2_node[1] in self._motif.internal_nets
        ):
            if self.G1.degree(g1_node) != self.G2.degree(g2_node):
                return False
        return True


# ---------------------------------------------------------------------------
# Match representation
# ---------------------------------------------------------------------------


@dataclass
class Match:
    """One motif match in a circuit graph.

    ``mapping`` maps a pattern node to the corresponding host node. For
    components, the second element of the host node is the actual
    ``refdes``; for nets, it's the actual net name.
    """

    motif_name: str
    mapping: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)

    @property
    def components(self) -> set[str]:
        """Set of actual refdes the match claims."""
        return {
            host_node[1]
            for pat_node, host_node in self.mapping.items()
            if pat_node[0] == "C"
        }

    @property
    def nets(self) -> set[str]:
        """Set of actual net names the match touches (internal + external)."""
        return {
            host_node[1]
            for pat_node, host_node in self.mapping.items()
            if pat_node[0] == "N"
        }

    def host_refdes(self, pattern_name: str) -> Optional[str]:
        """Return the actual refdes the pattern's component name maps to."""
        host = self.mapping.get(("C", pattern_name))
        return None if host is None else host[1]


# ---------------------------------------------------------------------------
# Find / resolve
# ---------------------------------------------------------------------------


def find_motif_matches(
    graph: nx.MultiGraph,
    motif: Motif,
    *,
    require_same_zone: bool = True,
) -> Iterator[Match]:
    """Yield every match of one motif in the host graph.

    ``require_same_zone`` (default True): reject matches whose claimed
    components don't all share the same ``zone`` attribute. A motif
    that spans across functional blocks is usually a false positive
    (e.g. an R in the buck and an R in the audio amp do not form a
    "divider" even if they happen to share a rail topology).
    """
    matcher = _MotifMatcher(graph, motif)
    # Use monomorphism (non-induced subgraph isomorphism) rather than
    # induced isomorphism: the host is allowed to have edges between
    # matched nodes that the pattern doesn't constrain. E.g. an
    # fb_divider's U is connected to VOUT and FB_NODE in the pattern,
    # but the host U1 may ALSO connect to GND through another pin --
    # that extra edge must be permitted, not rejected.
    for raw_mapping in matcher.subgraph_monomorphisms_iter():
        # NetworkX returns host_node -> pattern_node; invert.
        mapping = {pat: host for host, pat in raw_mapping.items()}
        match = Match(motif_name=motif.name, mapping=mapping)
        if require_same_zone:
            zones = {
                graph.nodes[host_node].get("zone")
                for pat_node, host_node in mapping.items()
                if pat_node[0] == "C"
            }
            if len(zones) > 1:
                continue
        yield match


def find_all_matches(
    plan: DesignPlan,
    motifs: Optional[Iterable[Motif]] = None,
    *,
    require_same_zone: bool = True,
) -> list[Match]:
    """Find every motif match in the plan, unresolved.

    Use ``recognize_motifs`` for the resolved (non-overlapping) result.
    """
    catalogue = list(motifs) if motifs is not None else list(MOTIF_CATALOGUE)
    graph = build_circuit_graph(plan)
    out: list[Match] = []
    for m in catalogue:
        out.extend(find_motif_matches(graph, m, require_same_zone=require_same_zone))
    return out


def _claimed_components(match: Match, motif: Optional[Motif]) -> set[str]:
    """Components a match exclusively claims for arbitration.

    IC-anchored motifs do NOT claim their ``ic_anchor`` -- multiple
    IC-anchored motifs (fb_divider + boot_cap + lc_output) can coexist
    around one regulator's U. Only the passives are claimed.
    """
    if motif is None or motif.ic_anchor is None:
        return match.components
    ic_host = match.host_refdes(motif.ic_anchor)
    if ic_host is None:
        return match.components
    return match.components - {ic_host}


def resolve_matches(
    matches: list[Match],
    motifs: Optional[Iterable[Motif]] = None,
) -> list[Match]:
    """Greedy MIS: keep highest-score matches whose components are unused.

    Score order: specificity > pattern-component count > pattern-edge
    count > motif name. Two matches of the SAME motif on different parts
    score identically on every one of those, so the score alone does not
    order them; the remaining tie is broken by the match's own identity
    (its claimed components, then its mapping).

    That last tiebreak is not decoration. Without it the stable sort fell
    back on the order ``find_all_matches`` produced, which comes from
    NetworkX's VF2 enumeration over sets of ``("C", refdes)`` tuples --
    and a tuple containing a string hashes differently in every process,
    because Python randomises string hashes. Two interchangeable bypass
    caps then swapped positions from run to run. Caught by comparing a
    corpus sweep against itself: identical code, 67 findings one run and
    74 the next.

    IC-anchored motifs share their ``ic_anchor`` U with other matches;
    only their passives are exclusively claimed.
    """
    catalogue = list(motifs) if motifs is not None else list(MOTIF_CATALOGUE)
    motif_by_name = {m.name: m for m in catalogue}

    def score(match: Match) -> tuple[int, int, int, str]:
        m = motif_by_name.get(match.motif_name)
        if m is None:
            return (0, 0, 0, match.motif_name)
        comp_count = sum(1 for n in m.pattern.nodes if n[0] == "C")
        return (
            m.specificity,
            comp_count,
            m.pattern.number_of_edges(),
            match.motif_name,
        )

    def identity(match: Match) -> tuple:
        return (
            match.motif_name,
            tuple(sorted(match.components)),
            tuple(sorted((str(pat), str(host))
                         for pat, host in match.mapping.items())),
        )

    # Two passes, relying on sort stability: identity ascending first, then
    # score descending. Equal-scoring matches therefore arrive in identity
    # order, which does not depend on how the matcher happened to enumerate
    # them. One pass with reverse=True cannot do this -- it would reverse the
    # identity tiebreak along with the score.
    matches_sorted = sorted(sorted(matches, key=identity),
                            key=score, reverse=True)
    used: set[str] = set()
    kept: list[Match] = []
    for match in matches_sorted:
        motif = motif_by_name.get(match.motif_name)
        claimed = _claimed_components(match, motif)
        if claimed & used:
            continue
        kept.append(match)
        used.update(claimed)
    return kept


def recognize_motifs(
    plan: DesignPlan,
    motifs: Optional[Iterable[Motif]] = None,
    *,
    require_same_zone: bool = True,
) -> list[Match]:
    """Top-level entry point: detect motifs and arbitrate overlaps.

    Each component appears in at most one returned ``Match``.
    Components that don't fit any motif are absent from the result --
    the caller treats them as singleton meta-nodes.
    """
    all_matches = find_all_matches(
        plan, motifs, require_same_zone=require_same_zone
    )
    return resolve_matches(all_matches, motifs)


# ---------------------------------------------------------------------------
# Splat: absolute placement from a match + meta-node anchor
# ---------------------------------------------------------------------------


@dataclass
class MotifPlacement:
    """Absolute placement instructions splatted from a meta-node anchor.

    ``parts`` maps the actual refdes to its absolute ``(x_mils, y_mils)``
    placement.
    """

    motif_name: str
    anchor_x_mils: int
    anchor_y_mils: int
    parts: dict[str, tuple[int, int]] = field(default_factory=dict)


def splat_motif(
    match: Match,
    motif: Motif,
    anchor: tuple[int, int],
) -> MotifPlacement:
    """Convert ``(match, motif, anchor)`` -> absolute part positions.

    Each component listed in ``motif.canonical`` is placed at
    ``anchor + canonical_offset``. Pattern components without an entry
    in ``canonical`` are skipped silently (they were referenced for the
    match but the motif doesn't dictate their position).
    """
    ax, ay = anchor
    parts: dict[str, tuple[int, int]] = {}
    for pat_refdes, (dx, dy) in motif.canonical.items():
        actual = match.host_refdes(pat_refdes)
        if actual is None:
            continue
        parts[actual] = (ax + dx, ay + dy)
    return MotifPlacement(
        motif_name=match.motif_name,
        anchor_x_mils=ax,
        anchor_y_mils=ay,
        parts=parts,
    )


# ---------------------------------------------------------------------------
# Pattern factory
# ---------------------------------------------------------------------------


def _make_pattern(
    components: dict[str, str],
    nets: dict[str, str],
    edges: list[tuple[str, str]],
) -> nx.MultiGraph:
    """Build a bipartite pattern graph from a compact spec.

    ``components``: pattern-local refdes -> kind letter.
    ``nets``: pattern-local net name -> role.
    ``edges``: list of (component_refdes, net_name) pairs.
    """
    G = nx.MultiGraph()
    for refdes, kind in components.items():
        G.add_node(("C", refdes), bipartite="component", kind=kind)
    for net, role in nets.items():
        G.add_node(("N", net), bipartite="net", role=role)
    for refdes, net in edges:
        G.add_edge(("C", refdes), ("N", net))
    return G


# ---------------------------------------------------------------------------
# Motif catalogue -- self-contained motifs (no external IC pin anchor)
# IC-anchored motifs (fb_divider, boot_cap, lc_output, crystal_load,
# opamp stages) live in a future iteration where the splat step can
# read a real IC pin position.
# ---------------------------------------------------------------------------


BYPASS_CAP = Motif(
    name="bypass_cap",
    pattern=_make_pattern(
        components={"C": "C"},
        nets={"VRAIL": "power", "GND": "ground"},
        edges=[("C", "VRAIL"), ("C", "GND")],
    ),
    internal_nets=frozenset(),  # both nets fan out to the rest of the design
    canonical={"C": (0, 0)},
    specificity=2,
)


VOLTAGE_DIVIDER = Motif(
    name="voltage_divider",
    pattern=_make_pattern(
        components={"Rtop": "R", "Rbot": "R"},
        nets={"VRAIL": "power", "MID": "signal", "GND": "ground"},
        edges=[
            ("Rtop", "VRAIL"),
            ("Rtop", "MID"),
            ("Rbot", "MID"),
            ("Rbot", "GND"),
        ],
    ),
    # MID must be closed at 2 pins -- no external fan-out. fb_divider
    # (with MID connecting to a U.FB pin) is a future motif that needs
    # MID with degree 3.
    internal_nets=frozenset({"MID"}),
    # Canonical separations exceed 2 * BBOX_HALF_2PIN (900 mil) so the
    # splatted parts don't immediately overlap each other. The shove
    # pass has already run before splat, so the canonical positions
    # must be self-non-overlapping.
    canonical={"Rtop": (0, 0), "Rbot": (0, -1000)},
    specificity=4,
)


PULL_UP_R = Motif(
    name="pull_up_r",
    pattern=_make_pattern(
        components={"R": "R"},
        nets={"VRAIL": "power", "SIG": "signal"},
        edges=[("R", "VRAIL"), ("R", "SIG")],
    ),
    internal_nets=frozenset(),
    canonical={"R": (0, 0)},
    specificity=2,
)


PULL_DOWN_R = Motif(
    name="pull_down_r",
    pattern=_make_pattern(
        components={"R": "R"},
        nets={"SIG": "signal", "GND": "ground"},
        edges=[("R", "SIG"), ("R", "GND")],
    ),
    internal_nets=frozenset(),
    canonical={"R": (0, 0)},
    specificity=2,
)


RC_SNUBBER = Motif(
    name="rc_snubber",
    pattern=_make_pattern(
        components={"R": "R", "C": "C"},
        nets={"A": "signal", "MID": "signal", "B": "signal"},
        edges=[("R", "A"), ("R", "MID"), ("C", "MID"), ("C", "B")],
    ),
    internal_nets=frozenset({"MID"}),
    canonical={"R": (0, 0), "C": (0, -1000)},
    specificity=3,
)


# RC low-pass filter: R in series, C to ground on the OUT node. The
# bottom plate of C must be GND (role=ground); B in rc_snubber is
# signal (any other net), which is how we distinguish.
RC_LOWPASS = Motif(
    name="rc_lowpass",
    pattern=_make_pattern(
        components={"R": "R", "C": "C"},
        nets={"IN": "signal", "OUT": "signal", "GND": "ground"},
        edges=[("R", "IN"), ("R", "OUT"), ("C", "OUT"), ("C", "GND")],
    ),
    # OUT is NOT internal -- it can fan out to a load. R-C topology is
    # specific enough that the GND-role bottom plate is the distinguisher.
    internal_nets=frozenset(),
    canonical={"R": (0, 0), "C": (1000, -1000)},
    specificity=4,
)


# RC high-pass: C in series, R to ground on the OUT node. Mirror image
# of RC_LOWPASS with R and C swapped.
RC_HIGHPASS = Motif(
    name="rc_highpass",
    pattern=_make_pattern(
        components={"C": "C", "R": "R"},
        nets={"IN": "signal", "OUT": "signal", "GND": "ground"},
        edges=[("C", "IN"), ("C", "OUT"), ("R", "OUT"), ("R", "GND")],
    ),
    internal_nets=frozenset(),
    canonical={"C": (0, 0), "R": (1000, -1000)},
    specificity=4,
)


# Pi (C-L-C) EMI / power-line filter: an input cap to ground, a series
# inductor, and an output cap to ground. The canonical conducted-EMI
# filter on a noisy supply input or an RF/audio rail. Distinct from
# lc_output (single cap + inductor anchored on a switching node U): a pi
# filter has TWO caps to the SAME ground, bridged by the inductor, and no
# IC. IN/OUT roles are wildcards so it matches both a power-rail filter
# (IN/OUT power) and a signal-line filter (IN/OUT signal); the structural
# signature (two C-to-ground around one L) is the constraint. The input
# cap must sit on the SAME net as one inductor terminal, so a decoupling
# cap on an unrelated net cannot be mistaken for the input cap.
PI_FILTER = Motif(
    name="pi_filter",
    pattern=_make_pattern(
        components={"Cin": "C", "L": "L", "Cout": "C"},
        nets={"IN": None, "OUT": None, "GND": "ground"},
        edges=[
            ("Cin", "IN"),
            ("Cin", "GND"),
            ("L", "IN"),
            ("L", "OUT"),
            ("Cout", "OUT"),
            ("Cout", "GND"),
        ],
    ),
    # IN and OUT can fan out (the source feeds IN, the load draws OUT), so
    # neither is closed; GND is shared with the rest of the design.
    internal_nets=frozenset(),
    # Inductor centred, caps dropping to ground on each side: IN -> Cin\,
    # L across the top, Cout / -> OUT. Separations exceed 2*BBOX_HALF_2PIN.
    canonical={"Cin": (-1100, -1000), "L": (0, 0), "Cout": (1100, -1000)},
    specificity=5,
)


# Full-wave diode-bridge rectifier (4 discrete diodes): the AC/DC front
# end on any mains or transformer input. Topologically a single bipartite
# 4-cycle -- two AC nodes and two DC rails (VPLUS power, VMINUS ground),
# each node bridged by two diodes:
#     AC1 -D1- VPLUS -D2- AC2 -D4- VMINUS -D3- AC1
# The matcher ignores pin polarity, so it is the 4-cycle plus the
# AC/AC/power/ground roles that identify a bridge -- an ESD array (N
# diodes in a STAR to one ground) or a 2-diode steering pair has the wrong
# structure and cannot match. A single-package bridge (DB#/BR#) is ONE
# part and needs no motif; this is for the discrete-diode build.
DIODE_BRIDGE = Motif(
    name="diode_bridge",
    pattern=_make_pattern(
        components={"D1": "D", "D2": "D", "D3": "D", "D4": "D"},
        nets={
            "AC1": "signal",
            "AC2": "signal",
            "VPLUS": "power",
            "VMINUS": "ground",
        },
        edges=[
            ("D1", "AC1"), ("D1", "VPLUS"),
            ("D2", "AC2"), ("D2", "VPLUS"),
            ("D3", "VMINUS"), ("D3", "AC1"),
            ("D4", "VMINUS"), ("D4", "AC2"),
        ],
    ),
    # All four rails fan out (AC to the source, VPLUS/VMINUS to the
    # smoothing cap and the rest of the supply), so none are closed.
    internal_nets=frozenset(),
    # Classic diamond: AC on the sides, DC top/bottom. D1/D2 feed VPLUS
    # (top), D3/D4 return VMINUS (bottom).
    canonical={
        "D1": (-700, 700), "D2": (700, 700),
        "D3": (-700, -700), "D4": (700, -700),
    },
    specificity=8,
)


# ---------------------------------------------------------------------------
# IC-anchored motifs -- a central U is referenced for VF2 matching but
# kept as a singleton (shared across motifs). Canonical positions are
# offsets from U's actual placed position.
# ---------------------------------------------------------------------------


# Feedback divider on a regulator's FB pin: R-R series, mid-tap connects
# to a U pin. The mid-tap (FB_NODE) is internal with degree-3 (Rtop +
# Rbot + U). Distinguishes from voltage_divider, where MID is closed
# at degree-2.
FB_DIVIDER = Motif(
    name="fb_divider",
    pattern=_make_pattern(
        components={"Rtop": "R", "Rbot": "R", "U": "U"},
        nets={
            "VOUT": "power",
            "FB_NODE": "signal",
            "GND": "ground",
        },
        edges=[
            ("Rtop", "VOUT"),
            ("Rtop", "FB_NODE"),
            ("Rbot", "FB_NODE"),
            ("Rbot", "GND"),
            ("U", "VOUT"),
            ("U", "FB_NODE"),
        ],
    ),
    internal_nets=frozenset({"FB_NODE"}),
    canonical={"Rtop": (1500, 0), "Rbot": (1500, -1000)},
    specificity=6,
    ic_anchor="U",
)


# Same feedback divider for regulators whose output rail does NOT touch
# the IC directly: an external-inductor buck reaches VOUT via U-SW-L-
# VOUT, so FB_DIVIDER's U-VOUT edge never exists and the divider used to
# degrade into independent pull_up_r / pull_down_r splats scattered by
# their own centroids (R_bot measured at the sheet's bottom edge, FB
# demoted to floating labels). Identical shape minus the U-VOUT edge;
# lower specificity so the stricter FB_DIVIDER wins when U does touch
# VOUT. Column x=3000 keeps it clear of the comp cluster at x<=2200 on
# ICs where both fire.
FB_DIVIDER_EXT = Motif(
    name="fb_divider_ext",
    pattern=_make_pattern(
        components={"Rtop": "R", "Rbot": "R", "U": "U"},
        nets={
            "VOUT": "power",
            "FB_NODE": "signal",
            "GND": "ground",
        },
        edges=[
            ("Rtop", "VOUT"),
            ("Rtop", "FB_NODE"),
            ("Rbot", "FB_NODE"),
            ("Rbot", "GND"),
            ("U", "FB_NODE"),
        ],
    ),
    internal_nets=frozenset({"FB_NODE"}),
    canonical={"Rtop": (4000, -200), "Rbot": (4000, -1200)},
    specificity=5,
    ic_anchor="U",
)


# Bootstrap cap on a buck regulator: C between U.BOOT and U.SW pins,
# both pins on the same U. BOOT is internal degree-2 (C and U).
BOOT_CAP = Motif(
    name="boot_cap",
    pattern=_make_pattern(
        components={"C": "C", "U": "U"},
        nets={"BOOT": "signal", "SW": "signal"},
        edges=[
            ("C", "BOOT"),
            ("C", "SW"),
            ("U", "BOOT"),
            ("U", "SW"),
        ],
    ),
    internal_nets=frozenset({"BOOT"}),
    # Cap sits directly ABOVE the IC, the way every regulator datasheet
    # draws the bootstrap cap: BOOT wire rises from the left-side pin,
    # SW wire rises from the right-side pin, and the cap bridges them
    # over the body. The old left-of-body slot (-1000, 600) forced the
    # SW-side wire to wrap around the IC's left edge -- a full-body
    # detour on every buck sheet.
    canonical={"C": (300, 1300)},
    specificity=5,
    ic_anchor="U",
)


# LC output stage of a switching converter: L from U.SW to VOUT, C from
# VOUT to GND. VOUT is power so cap can fan out to a load.
LC_OUTPUT = Motif(
    name="lc_output",
    pattern=_make_pattern(
        components={"L": "L", "C": "C", "U": "U"},
        nets={"SW": "signal", "VOUT": "power", "GND": "ground"},
        edges=[
            ("U", "SW"),
            ("L", "SW"),
            ("L", "VOUT"),
            ("C", "VOUT"),
            ("C", "GND"),
        ],
    ),
    internal_nets=frozenset(),
    # Inductor to the right of U; output cap further right and down.
    canonical={"L": (1800, 0), "C": (3400, -800)},
    specificity=6,
    ic_anchor="U",
)


# Catch / clamp diode on an IC pin: D from a U-touching signal net down
# to GND. In a non-synchronous buck this is the freewheel diode on the
# switch node -- every datasheet draws it hanging BELOW the SW trunk
# between the IC and the inductor, cathode up, anode down to its ground
# glyph. Without this motif the diode fell through to Sugiyama and
# drifted to the sheet's top edge with a coincident GND port stamped on
# top of its own designator text (measured on the buck fixture). Claims
# only D, so it composes with lc_output (which claims L + C) and
# boot_cap around the same U. The same shape also covers protection
# clamps to ground on any IC pin, which want the identical treatment.
CATCH_DIODE = Motif(
    name="catch_diode",
    pattern=_make_pattern(
        components={"D": "D", "U": "U"},
        nets={"SW": "signal", "GND": "ground"},
        edges=[
            ("U", "SW"),
            ("D", "SW"),
            ("D", "GND"),
        ],
    ),
    internal_nets=frozenset(),
    # Below the SW trunk (lc_output's L sits at (1500, 0)), between the
    # IC body and the inductor.
    canonical={"D": (1000, -1400)},
    specificity=4,
    ic_anchor="U",
)


# Crystal oscillator load network: Y between U.XIN and U.XOUT, two
# load caps from XIN/XOUT to GND.
CRYSTAL_LOAD = Motif(
    name="crystal_load",
    pattern=_make_pattern(
        components={"Y": "Y", "Cx": "C", "Cy": "C", "U": "U"},
        nets={
            "XIN": "signal",
            "XOUT": "signal",
            "GND": "ground",
        },
        edges=[
            ("U", "XIN"),
            ("U", "XOUT"),
            ("Y", "XIN"),
            ("Y", "XOUT"),
            ("Cx", "XIN"),
            ("Cx", "GND"),
            ("Cy", "XOUT"),
            ("Cy", "GND"),
        ],
    ),
    # XIN and XOUT are each shared by U, Y, and one cap -- degree 3.
    # Neither is internal (they're U pins; could theoretically fan out
    # for test points). Keep them open.
    internal_nets=frozenset(),
    canonical={
        "Y": (1500, 0),
        "Cx": (1200, -1000),
        "Cy": (1800, -1000),
    },
    specificity=7,
    ic_anchor="U",
)


# Type-II RC compensation network on a regulator's COMP pin: R + C in
# series from COMP to GND. COMP_MID (between R and C) is internal
# degree-2. COMP_NET (U pin out) is external.
RC_COMPENSATION = Motif(
    name="rc_compensation",
    pattern=_make_pattern(
        components={"R": "R", "C": "C", "U": "U"},
        nets={
            "COMP_NET": "signal",
            "COMP_MID": "signal",
            "GND": "ground",
        },
        edges=[
            ("U", "COMP_NET"),
            ("R", "COMP_NET"),
            ("R", "COMP_MID"),
            ("C", "COMP_MID"),
            ("C", "GND"),
        ],
    ),
    internal_nets=frozenset({"COMP_MID"}),
    # COMP pin to the right of U, R-C chain heading down.
    canonical={"R": (1500, -200), "C": (1500, -1200)},
    specificity=6,
    ic_anchor="U",
)


# Type-2 error-amp compensation, the full three-part network every
# current-mode buck datasheet draws hanging off the COMP pin: Cz in
# series with Rz to ground, Cp in parallel straight to ground.
# RC_COMPENSATION above only matches the R-first series order and knows
# nothing of Cp, so on a standard TI/ADI reference design the network
# fell to a free-standing rc_highpass splatted at its own centroid --
# the compensation parts drifted to a far corner of the sheet instead
# of clustering on the COMP pin. COMP_NET is internal at degree 3
# (U + Cz + Cp); COMP_MID at degree 2.
COMP_TYPE2 = Motif(
    name="comp_type2",
    pattern=_make_pattern(
        components={"Cz": "C", "Rz": "R", "Cp": "C", "U": "U"},
        nets={
            "COMP_NET": "signal",
            "COMP_MID": "signal",
            "GND": "ground",
        },
        edges=[
            ("U", "COMP_NET"),
            ("Cz", "COMP_NET"),
            ("Cz", "COMP_MID"),
            ("Rz", "COMP_MID"),
            ("Rz", "GND"),
            ("Cp", "COMP_NET"),
            ("Cp", "GND"),
        ],
    ),
    internal_nets=frozenset({"COMP_NET", "COMP_MID"}),
    # Series leg in one column right of U, parallel cap one column
    # further so both bottoms drop straight down to their GND glyphs.
    canonical={"Cz": (2000, -400), "Rz": (2000, -1400), "Cp": (2600, -400)},
    specificity=8,
    ic_anchor="U",
)


# C-first series order of the same two-part compensation (Cz from the
# COMP pin, Rz to ground). Both element orders appear in real designs;
# RC_COMPENSATION only matches R-first.
RC_COMPENSATION_CR = Motif(
    name="rc_compensation_cr",
    pattern=_make_pattern(
        components={"C": "C", "R": "R", "U": "U"},
        nets={
            "COMP_NET": "signal",
            "COMP_MID": "signal",
            "GND": "ground",
        },
        edges=[
            ("U", "COMP_NET"),
            ("C", "COMP_NET"),
            ("C", "COMP_MID"),
            ("R", "COMP_MID"),
            ("R", "GND"),
        ],
    ),
    internal_nets=frozenset({"COMP_MID"}),
    canonical={"C": (1500, -200), "R": (1500, -1200)},
    specificity=6,
    ic_anchor="U",
)


# Inverting op-amp gain stage: Rin from the input signal to the summing
# node (U's inverting input), Rf bridging the summing node and the output.
# The summing node (SUMMING) is the virtual-ground inverting input -- it is
# internal degree-3 (Rin + Rf + U). The feedback resistor Rf is what
# distinguishes this from fb_divider: Rf touches BOTH of U's motif nets
# (SUMMING and VOUT), whereas a feedback divider's two resistors each touch
# only one U net. The non-inverting amp's gain network (Rf from VOUT to IN-,
# Rg from IN- to GND) is topologically the same as fb_divider and is already
# covered there; only the INVERTING configuration (input via Rin, no ground
# leg) is new here. Keeping VIN role=signal blocks a feedback divider from
# matching (its ground leg would map VIN onto a ground net -> role mismatch).
OPAMP_INVERTING = Motif(
    name="opamp_inverting",
    pattern=_make_pattern(
        components={"Rin": "R", "Rf": "R", "U": "U"},
        nets={"VIN": "signal", "SUMMING": "signal", "VOUT": "signal"},
        edges=[
            ("Rin", "VIN"),
            ("Rin", "SUMMING"),
            ("Rf", "SUMMING"),
            ("Rf", "VOUT"),
            ("U", "SUMMING"),
            ("U", "VOUT"),
        ],
    ),
    internal_nets=frozenset({"SUMMING"}),
    # Input resistor and feedback resistor stacked on U's input (left) side.
    # Separations exceed 2 * BBOX_HALF_2PIN so the splatted parts don't
    # overlap each other after the pre-splat shove.
    canonical={"Rin": (-1700, -600), "Rf": (-1700, 600)},
    specificity=6,
    ic_anchor="U",
)


# Catalogue order is the registration order. Resolution sorts by
# specificity, so absolute order here only affects deterministic
# tie-break for motifs of equal specificity.
MOTIF_CATALOGUE: list[Motif] = [
    # IC-anchored first by convention (higher specificity).
    CRYSTAL_LOAD,
    LC_OUTPUT,
    COMP_TYPE2,
    FB_DIVIDER,
    FB_DIVIDER_EXT,
    OPAMP_INVERTING,
    RC_COMPENSATION,
    RC_COMPENSATION_CR,
    BOOT_CAP,
    CATCH_DIODE,
    # Self-contained.
    VOLTAGE_DIVIDER,
    RC_LOWPASS,
    RC_HIGHPASS,
    RC_SNUBBER,
    PI_FILTER,
    DIODE_BRIDGE,
    BYPASS_CAP,
    PULL_UP_R,
    PULL_DOWN_R,
]


def get_motif_by_name(name: str) -> Optional[Motif]:
    """Look up a motif in the default catalogue by name."""
    for m in MOTIF_CATALOGUE:
        if m.name == name:
            return m
    return None


__all__ = [
    "Match",
    "Motif",
    "MotifPlacement",
    "MOTIF_CATALOGUE",
    "BOOT_CAP",
    "BYPASS_CAP",
    "CRYSTAL_LOAD",
    "DIODE_BRIDGE",
    "FB_DIVIDER",
    "LC_OUTPUT",
    "OPAMP_INVERTING",
    "PI_FILTER",
    "PULL_DOWN_R",
    "PULL_UP_R",
    "RC_COMPENSATION",
    "RC_HIGHPASS",
    "RC_LOWPASS",
    "RC_SNUBBER",
    "VOLTAGE_DIVIDER",
    "build_circuit_graph",
    "find_all_matches",
    "find_motif_matches",
    "get_motif_by_name",
    "recognize_motifs",
    "resolve_matches",
    "splat_motif",
]
