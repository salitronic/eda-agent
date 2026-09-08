# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Score the engine against a human on the same netlist.

THE COMPARISON THE PREFERENCE CORPUS NEVER WAS. 312 pairwise votes were
collected from ONE design, 95% of them for the same side, most cast
under two seconds apart. The resulting model scored 58% on a binary
choice. A human-drawn sheet supplies the other half of a pair for free:
same netlist, same symbols, one layout drawn by a professional and one
produced by this engine, and nobody has to click anything.

It answers a question nothing else here can: not "is this layout tidy by
our own measure", but "how far is our measure from a layout somebody was
paid to draw".

USE IT AS AN INSTRUMENT, NOT A TARGET. The engine losing on a sheet is
information about the sheet, the features, or the engine, in that order
of likelihood, and three of four structural features were falsified by
looking at exactly this data rather than by reasoning about it.

WHAT IT REACHES. 36 of the 115 KiCad demo sheets are comparable. Of the
79 it holds out:

    63  over a size cap (see compare_sheet)
     8  no net joins two parts, so there is nothing to lay out
     5  a symbol the sheet places has no definition in its own file
     3  plan rejected, engine declined, or no placed symbols

THE CAPS ARE THE LIMIT, not the reader. A first pass at this blamed bus
expansion and hierarchy, on the grounds that 53% of the sheets using
neither are comparable against 21% of those using both. That is a
confound: sheets with buses and hierarchy are the BIG ones, and size is
what excludes them. The reader already reads global and hierarchical
labels as net names.

The eight with no part-to-part net are interface sheets, and they are
genuinely empty of work rather than misread: vme_p1_p2 has 24 symbols,
295 wires and 388 nets, of which 383 touch exactly ONE part, because
its connectors fan outward rather than to each other.

So the corpus this measures is the SMALL half of the demo set, and
raising the caps is what would extend it.

THE CONVENTION FINDINGS NO LONGER REST ON IT. Every human-side figure
quoted around this package was re-measured on 1325 sheets pulled from
172 public hardware repositories, filtered to files the KiCad editor
itself wrote (the generator tag separates hand-drawn sheets from the
output of schematic generators, which are a large share of what those
repositories hold) and deduplicated by content hash, which removed a
fifth of them as vendored copies.

They replicate, several exactly: 4.3 parts per horizontal row, 0.65
ground glyphs per rail pin, 0.50 power glyphs, the same three stub
modes at 50, 100 and 150 mils, 90% of parts aligned against 91%, and
1.55 width-to-height against 1.49. Crossings are held to MORE tightly
in production work than in the demos, median 1 with 49% of sheets clean
against median 2 and 36%.

NOR DOES THE ENGINE COMPARISON ANY MORE. Laying out 1466 of those
sheets under a wall-clock budget produced 313 comparable ones, against
40 from the demos, and it reproduces the demo result term for term:

  * the engine wins 21% of sheets, against 20% on the demos
  * the same three gaps in the same order, length worst (280 of 313),
    then power-glyph count (169), then alignment (204)
  * still ahead of humans on crossings and wires-through-bodies
  * body overlaps on 2 sheets in 313, the defect the widened demo caps
    first exposed on power-supply-2

One number is WORSE here, and it is the one the easier corpus was
flattering: wire length runs 2.88 times the human's against 2.06 on the
demos, because production boards are drawn more tightly than teaching
examples.

WHAT IT DOES NOT CLAIM. A human sheet carries context the engine never
had: mechanical constraints, a house style, a reviewer's preferences,
and hierarchy this reader does not resolve. A win for the human is not
proof the engine is wrong, and a win for the engine is not proof it is
right. It is a measurement, and its value is that it can be repeated.

WHAT IT HAS FOUND, so the next reader knows what to expect of it. Most
of what it reported first was wrong with the INSTRUMENT, and each one
looked like an engine defect until it was chased:

  * Pin angles read as pointing INTO the body, a body box built from
    the pin envelope, every unit of a multi-unit part folded into one
    symbol, and the connection point stored where the body-attach end
    belongs. Together those made 1600 points of "the engine routes
    through a component body" on a two-part sheet. Rebuilding a human
    sheet on this canvas and asking whether its pins land where the
    sheet draws its wires went from 0.6% to 99.1% agreement.
  * A wire crossing the body of the part it CONNECTS to, which a test
    point drawn around its own pin does by construction.
  * A zero-area body satisfying every edge comparison in the overlap
    test while covering nothing.
  * Power nets left anonymous, so a glyph reading GND matched no net
    and the rail-direction feature had 13 comparable placements in the
    whole corpus instead of 1685.

And two things that were genuinely the engine's:

  * A motif resnap placing a decoupling cap INSIDE a large IC, on a
    pass that runs after the final overlap repair and is never
    re-checked.
  * Layouts that scatter: an alignment penalty of 0.555 against 0.164
    for humans, which the shared-axis variant now closes on the sheets
    where it can do so without costing more elsewhere.

THE REMAINING GAPS LOOK LIKE THREE AND MEASURE AS ONE. Wire length is
2.42 times the human's, glyph count is roughly double on the worst
sheets, and alignment is worse on most. Grouping part centres into
horizontal bands 300 mils apart, over ten sheets:

    humans put a median 4.3 parts in a row; this engine puts 1.8

and it used more bands than the human on EVERY sheet measured. royer1
is the extreme: a person drew seventeen parts as one row, the engine
spread them over nine. More bands is longer vertical runs (length),
parts that share no y (alignment), and ground pins beyond the
clustering radius so each takes its own glyph (ports). One cause,
three symptoms.

Seen first by rendering a sheet and looking at it, which is the whole
argument for that rule: the three metrics had been reported separately
for a dozen measurements without the common cause showing up.

CHECKED AND NOT A PROBLEM, so nobody spends the afternoon again:

  * Dangling wire ends. The engine leaves 12 free wire endpoints in
    1072, and each is a stub carrying a net label with the end running
    250 mils past the label. That is the ordinary labelled-connection
    idiom, not a broken net. The control is what settles it: the same
    detector on the HUMAN sheets finds 29 in 852, so people do it three
    times as often as this engine does.

  * Duplicate wire segments were real but small, 5 in 541, and are
    fixed at the flush. They did not account for the length gap.

  * Junction dots: none missing. Of the dots placed, 19% sit where a
    strict arms>=3 rule says none is needed, and so do 21% of 4293
    dots on the human sheets, so that is convention rather than fault.
    A MISSING dot breaks a connection and is what the guard asserts.

ONE SHEET IN 115 PRODUCES NO LAYOUT AT ALL, and it fails honestly.
fp_connectors packs seven connectors and 22 nets tightly enough that
several nets cannot be wired without crossing another net's pin, and
their pins have no clear stub, so they would come out as floating
labels. The engine reports that and declines rather than emitting a
schematic whose netlist is quietly wrong. Shortening the pin stub eases
it slightly (eleven failures to ten) without fixing it: the cause is
the placement, not the stub.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from eda_agent.design.canvas import (
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet as CanvasSheet,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.kicad_sheet_reader import (
    KicadSheet,
    extract_nets,
    symbols_from_sheet,
    wire_nets,
)


@dataclass
class Comparison:
    """One sheet, scored as drawn and as this engine would lay it out."""

    path: str = ""
    parts: int = 0
    nets: int = 0
    human_total: float = 0.0
    engine_total: float = 0.0
    human_breakdown: dict = None
    engine_breakdown: dict = None
    #: Convention terms the wirelength scorer cannot see, which the
    #: pipeline's own candidate selection DOES weigh. Reported apart
    #: from the totals rather than folded in, because only two of the
    #: four have a human analogue at all.
    human_convention: float = 0.0
    engine_convention: float = 0.0
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.note

    @property
    def engine_wins(self) -> bool:
        return self.ok and self.engine_total < self.human_total


#: DesignPlan constrains both, and a real sheet does not have to
#: comply: KiCad accepts 'Module302' as a designator and '+3V3(A)' as a
#: net. Sanitised rather than the schema relaxed, because the schema is
#: the contract everything downstream relies on.
_REFDES_OK = __import__("re").compile(r"^[A-Z]+[0-9]+[A-Z]?$")
_NET_BAD = __import__("re").compile(r"[^A-Za-z0-9_+\-/]")


def _clean_refdes(ref: str) -> Optional[str]:
    """Upper-cased when that is enough, otherwise None.

    'Module302' becomes 'MODULE302' and is fine. Anything still outside
    the pattern is dropped rather than mangled into a name that collides
    with a different part.
    """
    candidate = str(ref or "").upper()
    return candidate if _REFDES_OK.match(candidate) else None


def _clean_net(name: str) -> str:
    """Replace what the schema forbids, and never start with a digit."""
    cleaned = _NET_BAD.sub("_", str(name or "").strip()) or "N_0"
    if cleaned[0].isdigit():
        cleaned = "N_" + cleaned
    return cleaned


def _is_ground_text(text: str) -> bool:
    """Whether a glyph's own text names a return rather than a supply.

    Read off the text because that is what the glyph carries; a style
    guessed from position would beg the question the rail-direction
    feature is asking.
    """
    name = str(text or "").strip().upper()
    return name.startswith("GND") or name in {"AGND", "DGND", "VSS", "EARTH"}


def plan_from_sheet(sheet: KicadSheet, lib_path: str = "sheet") -> dict:
    """A DesignPlan payload describing what the human drew.

    Nets come from the drawn geometry rather than from proximity, which
    is the distinction that decided every layout feature in this
    package. Single-pin groups are dropped: the plan schema requires two
    pins on a net, and a pin joined to nothing is not connectivity.
    """
    renamed: dict[str, str] = {}
    #: Nets the human marked with a power symbol, by the glyph's own text.
    rail_names = {p.glyph_text for p in sheet.pins
                  if p.is_power_glyph and p.glyph_text}
    nets = []
    for name, pins in extract_nets(sheet).items():
        refs = []
        for p in pins:
            if p.is_power_glyph:
                continue
            ref = _clean_refdes(p.reference)
            if ref is None:
                continue
            renamed[p.reference] = ref
            refs.append({"refdes": ref, "pin": p.number or p.name})
        # Deduplicate: a pin can be reached through more than one wire.
        seen, unique = set(), []
        for r in refs:
            key = (r["refdes"], r["pin"])
            if key not in seen:
                seen.add(key)
                unique.append(r)
        # TWO OR MORE, and that is a limit of this benchmark rather than a
        # choice. A pin the human tied to a rail with its own power symbol,
        # or labelled to carry the net off-sheet, is genuinely connected and
        # forms a group with ONE component pin. Measured over 250 corpus
        # sheets: 5349 placed pins have no plan net, 2643 of them touch a
        # wire or a label, and admitting those single-pin groups netted 1598
        # more pins and took the unnetted share from 22.5% to 14.8%.
        #
        # It cannot be done here: ``Net.pins`` requires at least two
        # entries, so a one-pin net is not a representable plan, and the
        # attempt produced ValidationErrors instead of better plans. The
        # engine is therefore laid out against about a tenth less
        # connectivity than the human drew, and a wire crossing such a pin
        # is reported as a short against '_unnetted_'. Fixing it means
        # teaching DesignPlan about an off-sheet endpoint, which is a change
        # to every consumer of the schema, not to this reader.
        if len(unique) >= 2:
            # A net the human hung a POWER SYMBOL on is a rail, and the
            # plan is where the engine learns that. Without the flags it
            # falls back to matching names, which catches GND and VDD and
            # misses anything a designer named otherwise, so the engine
            # was working from less than a real plan would carry.
            #
            # THIS MAKES THE ENGINE SCORE WORSE, and is kept anyway. A
            # flagged rail is drawn as PORTS where an unflagged net was
            # drawn as wires, so glyph counts rise (royer1 8 to 12,
            # amplifier-ac 8 to 14) and nPM1300 goes 1304 to 3172 on
            # +1000 crossings and +800 wires-through-bodies. That is
            # what the engine does with correct input, and hiding the
            # input to flatter the score would make every number here
            # measure a plan nobody would ever hand it.
            entry = {"name": _clean_net(name), "pins": unique}
            if name in rail_names:
                if _is_ground_text(name):
                    entry["is_ground"] = True
                else:
                    entry["is_power"] = True
            nets.append(entry)

    # Net names must be unique after cleaning, or two different nets
    # merge into one and the plan describes a short.
    seen_names: dict[str, int] = {}
    for net in nets:
        base = net["name"]
        if base in seen_names:
            seen_names[base] += 1
            net["name"] = f"{base}_{seen_names[base]}"
        else:
            seen_names[base] = 0

    placed = {n["refdes"] for net in nets for n in net["pins"]}
    parts = [
        {"refdes": renamed[s.reference], "lib_ref": s.lib_id,
         "lib_path": lib_path, "sheet": "main"}
        for s in sheet.symbols
        if s.reference in renamed and renamed[s.reference] in placed
    ]
    return {
        "spec": sheet.title or "imported sheet",
        "summary": sheet.title or "imported sheet",
        # THE FRAME THE HUMAN HAD. 58 of the KiCad demo sheets are A3
        # against 51 A4, and hardcoding A4 handed the engine 87.4M
        # square mils to place a netlist the person drew across 193M.
        # It also fed the aspect term the wrong target ratio, since
        # that now comes from the sheet's own proportions.
        "sheets": [{"name": "main", "size": sheet.paper or "A4"}],
        "parts": parts,
        "nets": nets,
    }


def _keep_connected(
    wires: list[tuple[int, int, int, int, str]],
    seeds: set[tuple[int, int]],
) -> tuple[list[tuple[int, int, int, int, str]], set[tuple[int, int]]]:
    """Wire segments reachable from ``seeds`` through shared endpoints.

    Two segments connect when they share an endpoint, or when an endpoint
    of one lies on the other (a T). Returns the kept segments and every
    point they touch, so labels and glyphs can be kept by the same rule.
    """
    def on_seg(px, py, x1, y1, x2, y2):
        if x1 == x2:
            return px == x1 and min(y1, y2) <= py <= max(y1, y2)
        if y1 == y2:
            return py == y1 and min(x1, x2) <= px <= max(x1, x2)
        return False

    reached = set(seeds)
    kept: list[tuple[int, int, int, int, str]] = []
    pending = list(wires)
    changed = True
    while changed and pending:
        changed = False
        rest = []
        for w in pending:
            x1, y1, x2, y2, _ = w
            # Either end on a reached point, a reached point on this
            # segment (a wire dropping onto this one), or either end of
            # this segment on a kept segment (this one dropping onto a
            # wire already kept). The last case is the T that matters
            # most: a stub from a placed pin joining a trunk mid-span.
            touches = ((x1, y1) in reached or (x2, y2) in reached
                       or any(on_seg(px, py, x1, y1, x2, y2)
                              for (px, py) in reached)
                       or any(on_seg(x1, y1, *k[:4]) or on_seg(x2, y2, *k[:4])
                              for k in kept))
            if touches:
                kept.append(w)
                reached.add((x1, y1))
                reached.add((x2, y2))
                changed = True
            else:
                rest.append(w)
        pending = rest
    return kept, reached


def human_canvas_from_sheet(sheet, plan: DesignPlan, symbols):
    """The human layout on this package's canvas, same nets as the plan.

    Returns ``(canvas, "")`` or ``(None, reason)``.

    ONLY WIRING CONNECTED TO A PLACED PART IS KEPT. The plan is built from
    the annotated, single-unit parts the reader can resolve; the sheet may
    hold many more (unannotated parts, other units, power symbols), and their
    wires and glyphs are on the same nets. Keeping every wire on a plan net
    handed the human side copper that belonged to parts the engine was never
    asked to draw. Found on a public sheet with 11 plan parts and 146 power
    glyphs: a grid of 70 unannotated parts above the circuit, each with its
    rail stub and two glyphs, all counted against the human. It inflated the
    human's wire length, ports and crossings, and a model fitted on such
    pairs learned that "fewer ports looks human" and traded the engine's
    ports for a rail trunk with junctions.

    So wires are kept only when reachable, through shared endpoints, from a
    pin of a placed part; labels and glyphs only where they sit on kept
    copper or on a placed pin. KiCad Y grows DOWN and the canvas is Y-up, so
    the sign is flipped throughout.
    """
    canvas = SchematicCanvas()
    canvas.add_sheet(CanvasSheet(name="main", size=sheet.paper or "A4"))
    missing = 0
    for sym in sheet.symbols:
        model = symbols.get(sym.lib_id)
        if model is None:
            missing += 1
            continue
        ref = _clean_refdes(sym.reference)
        if ref is None:
            continue
        if canvas.instance_by_refdes(ref) is not None:
            return None, f"multi-unit part {ref} appears more than once"
        canvas.add_instance(SymbolInstance(
            refdes=ref, symbol=model, x=sym.x, y=-sym.y, rotation=sym.rotation))
    if missing:
        return None, f"{missing} symbols had no definition on the sheet"
    if not canvas.instances:
        return None, "no placed parts"

    wanted = {net["name"] if isinstance(net, dict) else net.name
              for net in plan.nets}
    names = [_clean_net(n) for n in wire_nets(sheet)]
    all_wires = [(w.x1, -w.y1, w.x2, -w.y2, name)
                 for w, name in zip(sheet.wires, names) if name in wanted]
    seeds = {(ep.x, ep.y) for inst in canvas.instances
             for ep in inst.all_pin_endpoints()}
    kept, reached = _keep_connected(all_wires, seeds)
    canvas.add_wires([
        WireSegment(x1=x1, y1=y1, x2=x2, y2=y2, sheet="main", net=name)
        for (x1, y1, x2, y2, name) in kept])
    canvas.add_labels([
        NetLabel(text=_clean_net(lab.text), x=lab.x, y=-lab.y,
                 orientation=0, sheet="main")
        for lab in sheet.labels
        if _clean_net(lab.text) in wanted and (lab.x, -lab.y) in reached])
    canvas.add_power_ports([
        PowerPort(text=_clean_net(g.glyph_text), x=g.x, y=-g.y,
                 style=("gnd_power" if _is_ground_text(g.glyph_text)
                        else "circle"),
                 sheet="main")
        for g in sheet.pins
        if g.is_power_glyph and _clean_net(g.glyph_text) in wanted
        and (g.x, -g.y) in reached])
    return canvas, ""


def compare_sheet(text: str, path: str = "", max_parts: int = 40,
                  max_nets: int = 25, max_net_pins: int = 40) -> Comparison:
    """Score a human sheet against this engine's layout of it.

    ALL THREE CAPS MATTER, and the last two matter most. The placement
    sweep costs with CONNECTIVITY, not part count: measured, a sheet
    with five parts and thirty-seven nets took 176 seconds, while
    sheets of thirty parts finished in under a second. A part-only
    guard let exactly the wrong sheets through.

    max_net_pins is a LOOSE guard, and the measurements say it cannot be
    anything else. Timing sheets with the caps lifted:

        v_i_sources   36 parts  19 nets  widest 36      2.2s
        sfp           35 parts  17 nets  widest 25    157.0s
        Debugger      28 parts  22 nets  widest 29    228.6s
        m2            51 parts  37 nets  widest 35      0.1s

    The sheet with the WIDEST net is the fastest of them, and the
    narrowest of the three real layouts is the slowest. Width does not
    predict cost, and neither does the part-times-net product (36x19
    runs in seconds, 28x22 takes four minutes). This started at 24 on
    the strength of sfp alone, which excluded v_i_sources for being
    cheap in the same way sfp is expensive.

    So it sits at 40: high enough to admit what was measured, low
    enough to stop a genuinely pathological net. What would replace it
    is a cost model that actually predicts layout time, and none of
    the three quantities available here does.

    (An earlier version of this note blamed CM5, whose GND net carries
    52 pins. CM5 returns in 0.1 seconds: it bails out early because a
    symbol has no definition, and the sweep that looked stalled on it
    was a broken progress check of mine.)
    """
    from eda_agent.design.canvas import Sheet as CanvasSheet
    from eda_agent.design.canvas import (
        NetLabel,
        PowerPort,
        SchematicCanvas,
        SymbolInstance,
        WireSegment,
    )
    from eda_agent.design.kicad_sheet_reader import read_sheet
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.quality import score_canvas

    sheet = read_sheet(text, path)
    result = Comparison(path=path)
    if not sheet.symbols:
        result.note = "no placed symbols"
        return result

    symbols = symbols_from_sheet(text, lib_path="sheet")

    # A hierarchical sheet instantiated more than once repeats its
    # refdes, and a flat canvas holds one instance per refdes: placing
    # them raised ValueError and lost the sheet entirely. Comparing such
    # a sheet flatly would not mean anything either, so it is skipped
    # and SAID, rather than crashing or quietly keeping the first one.
    #
    # COUNTED OVER WHAT WILL BE PLACED, not over the plan's parts. The
    # plan holds only symbols that sit on a net, so a repeated instance
    # missing from it passed this check and still collided on the
    # canvas.
    counts: dict[str, int] = {}
    for sym in sheet.symbols:
        ref = _clean_refdes(sym.reference)
        if ref is not None and sym.lib_id in symbols:
            counts[ref] = counts.get(ref, 0) + 1
    repeated = sorted(r for r, n in counts.items() if n > 1)
    if repeated:
        result.note = (f"skipped: {len(repeated)} refdes appear more than "
                       f"once ({', '.join(repeated[:3])}); the sheet is "
                       f"instantiated more than once")
        return result

    payload = plan_from_sheet(sheet)
    result.parts = len(payload["parts"])
    result.nets = len(payload["nets"])
    if not payload["parts"] or not payload["nets"]:
        result.note = "no connectivity recovered"
        return result
    if result.parts > max_parts:
        result.note = f"skipped: {result.parts} parts exceeds max_parts"
        return result
    if result.nets > max_nets:
        result.note = f"skipped: {result.nets} nets exceeds max_nets"
        return result
    widest = max((len(net["pins"]) for net in payload["nets"]), default=0)
    if widest > max_net_pins:
        result.note = (f"skipped: a net carries {widest} pins, over "
                       f"max_net_pins")
        return result

    try:
        plan = DesignPlan.model_validate(payload)
    except Exception as exc:                      # noqa: BLE001
        reason = str(exc).splitlines()[0][:80] if str(exc) else ""
        result.note = f"plan rejected: {type(exc).__name__}: {reason}"
        return result

    human, note = human_canvas_from_sheet(sheet, plan, symbols)
    if human is None:
        result.note = note
        return result

    # Scored once: crossing detection is quadratic in wire count, and
    # the human canvas now carries every wire on its nets.
    human_score = score_canvas(human, plan)
    result.human_total = human_score.total
    result.human_breakdown = dict(human_score.breakdown)

    try:
        from eda_agent.design.pipeline import build_best_canvas_from_plan
        from eda_agent.design.symbols import SymbolExtractor

        class _SheetExtractor(SymbolExtractor):
            def __init__(self, models):
                self._models = models

            def extract_one(self, lib_path, lib_ref):
                return self._models.get(lib_ref)

            def extract_many(self, refs):
                out = {}
                for lib_path, lib_ref in refs:
                    model = self._models.get(lib_ref)
                    if model is not None:
                        out[(lib_path, lib_ref)] = model
                return out

        built = build_best_canvas_from_plan(plan, _SheetExtractor(symbols))
    except Exception as exc:                      # noqa: BLE001
        result.note = f"engine raised: {type(exc).__name__}: {exc}"
        return result

    if not getattr(built, "ok", False):
        result.note = "engine declined to lay the sheet out"
        return result

    engine_score = score_canvas(built.canvas, plan)
    result.engine_total = engine_score.total
    result.engine_breakdown = dict(engine_score.breakdown)

    # THE ENGINE DOES NOT OPTIMISE THE TOTAL ALONE. Its candidate
    # selection ranks on the score PLUS convention terms the scorer
    # cannot see, so a variant that trades raw points for a convention
    # win is a deliberate choice and used to read here as a regression:
    # royer1 was reported 70 points "worse" while the winning candidate
    # was the one the engine judged better.
    #
    # Only the two terms computable from a canvas and a plan are used.
    # The other two, forced label demotions and span-labelled mils, are
    # artifacts of the pipeline's own decisions with no analogue on a
    # sheet somebody drew by hand.
    try:
        from eda_agent.design.pipeline import (
            _count_pin_side_violations,
            _count_polarity_inversions,
        )

        def convention(canvas) -> float:
            return (100.0 * _count_polarity_inversions(canvas, plan)
                    + 120.0 * _count_pin_side_violations(canvas, plan))

        result.human_convention = convention(human)
        result.engine_convention = convention(built.canvas)
    except Exception:                             # noqa: BLE001
        pass
    return result
