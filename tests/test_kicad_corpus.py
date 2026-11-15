# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The KiCad importer against the REAL installed library corpus.

Hand-written fixtures only test the cases someone thought of, and this
converter's bugs have all been in cases nobody thought of: symbols with
no geometry of their own, units drawn in two body styles, supply rails
in a shared unit. Those were found by reading the shipped libraries, not
by inventing s-expressions.

So these run the converter over whatever KiCad is installed and assert
INVARIANTS rather than specific values. Values would pin one KiCad
version; invariants hold for any of them, which is the point when the
corpus is somebody else's and changes on their schedule.

Skipped, not failed, when KiCad is absent: CI has no KiCad and these
must not make it red. That does mean they are opportunistic, so nothing
here is the only cover for a behaviour -- each has a fixture-based
sibling in test_kicad_reader.

Each corpus is walked ONCE, module-scoped, and every assertion reads the
same findings. Walking per test was three times the wall clock for
identical coverage: these files are megabytes and the parse is the whole
cost.

KNOWN BLIND SPOT of the round-trip checks below: they cannot see a
reader bug that is SYMMETRIC. Force every region onto top copper and
both passes read it that way, so the signatures agree and the round trip
is green while every fabrication layer is wrong.

Measured, not argued. Drop the Y-mirror negation from pad rotation on
BOTH the reader and the writer and run the two kinds of test:

    corpus round trip   MISSED   (both sides wrong the same way)
    absolute pad test   CAUGHT

That mutation is not academic. Altium consumes the neutral value
directly and never sees the KiCad file, so with it every rotated pad
arrives mirrored while this file stays green.

Round trips catch writer bugs and ASYMMETRIC reader bugs. Absolute
values have to be asserted against source whose right answer is known
independently, which is what test_kicad_reader does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pytest

pytestmark = pytest.mark.kicad_libs

#: Items taken from each library. Every library is visited either way;
#: this only sets how deep. Three is enough to hit derived, multi-unit
#: and plain symbols across 222 libraries without a slow test.
_PER_LIBRARY = 3


# ----------------------------- symbols -------------------------------

def _pin_signature(comp):
    """Everything about a symbol's pins that a round trip must preserve.

    Deliberately includes ``unit``, ``display`` and the ELECTRICAL type:
    those are the fields a writer drops most easily, because losing them
    still yields a file with the right pin count in the right places.
    Electrical type is what ERC reasons about, so a writer that forgets
    open-collector produces a symbol that looks perfect and no longer
    asks for its pull-up.

    ``name_visible`` / ``number_visible`` are here for the same reason
    and are the awkward pair: KiCad declares them ONCE PER SYMBOL while
    this model carries them per pin, so the round trip has to survive a
    narrowing and a widening. 2447 symbols in the corpus hide pin names
    and 412 hide pin numbers, all of them passives, and the failure is
    purely visual: the part converts, it just draws with labels the
    source deliberately suppressed.
    """
    return sorted(
        (p.number, p.name, p.unit, round(p.x), round(p.y),
         round(p.rotation), p.display, p.electric, p.dot, p.clock,
         p.name_visible, p.number_visible)
        for p in comp.symbol.shapes if p.kind == "pin")


def _symbol_graphic_signature(comp):
    """The symbol's body art, as a round trip must return it.

    Separate from the pin signature because the two writers are
    separate: reversing the sweep of a SYMBOL arc leaves every pin
    untouched, so a pins-only comparison passes while the body draws
    inside out. Verified by mutating exactly that.
    """
    out = []
    for s in comp.symbol.shapes:
        if s.kind == "rect":
            out.append(("rect", round(s.x), round(s.y),
                        round(s.width), round(s.height)))
        elif s.kind == "circle":
            out.append(("circle", round(s.cx), round(s.cy), round(s.radius)))
        elif s.kind == "arc":
            out.append(("arc", round(s.x1), round(s.y1), round(s.x2),
                        round(s.y2), round(s.rx), s.large_arc, s.sweep))
        elif s.kind == "polyline":
            out.append(("poly",
                        tuple((round(x), round(y)) for x, y in s.points)))
        elif s.kind == "text":
            # Symbol body text was absent from this signature entirely,
            # which is how a hardcoded angle and size lived here: the
            # reader discarded the angle and the writer emitted a
            # literal 0, so nothing disagreed. .kicad_sym states these
            # angles in DECIDEGREES while pins in the same file use
            # degrees, so rotation is the field most likely to be wrong.
            out.append(("text", s.text, round(s.x), round(s.y),
                        round(s.rotation), round(s.font_size)))
    return sorted(out, key=repr)


@dataclass
class _Findings:
    checked: int = 0
    derived: int = 0
    multi_part: int = 0
    arcs: int = 0
    graphic_round_trip: list[str] = field(default_factory=list)
    incoherent: list[str] = field(default_factory=list)
    empty_derived: list[str] = field(default_factory=list)
    repeated_pins: list[str] = field(default_factory=list)
    round_trip: list[str] = field(default_factory=list)
    #: Symbols whose pin NAMES / NUMBERS the source hides. Counted so the
    #: round trip cannot go quietly vacuous on those fields, see
    #: test_the_sample_still_contains_hidden_label_symbols.
    hidden_names: int = 0
    hidden_numbers: int = 0
    #: Symbol body text seen, and how much of it is rotated or sized
    #: unusually. Counted so _symbol_graphic_signature's text entry
    #: cannot go vacuous; see test_the_symbol_sample_still_contains_text.
    sym_texts: int = 0
    sym_texts_rotated: int = 0
    sym_text_heights: set = field(default_factory=set)


@pytest.fixture(scope="module")
def findings():
    """Convert a slice of the installed corpus and collect every problem."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent
    from eda_agent.libimport.kicad.reader import read_kicad_symbol
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    root = _symbol_dir()
    if root is None:
        pytest.skip("no KiCad symbol libraries installed on this machine")

    out = _Findings()
    for path in sorted(root.glob("*.kicad_sym")):
        text = path.read_text(encoding="utf-8", errors="replace")
        lib = path.stem
        names = re.findall(r'^\t\(symbol "([^"]+)"', text, re.M)
        for name in names[:_PER_LIBRARY]:
            block = re.search(
                r'^\t\(symbol "' + re.escape(name)
                + r'"(.*?)(?=^\t\(symbol "|\Z)', text, re.S | re.M)
            is_derived = bool(block) and '(extends "' in block.group(1)

            try:
                comp = read_kicad_symbol(text, name=name)
            except Exception as exc:  # noqa: BLE001 - collect, do not abort
                out.incoherent.append(f"{lib}:{name} read raised {exc!r}")
                continue

            pins = [s for s in comp.symbol.shapes if s.kind == "pin"]
            if is_derived:
                out.derived += 1
                if not pins:
                    out.empty_derived.append(f"{lib}:{name}")
            if not pins:
                continue  # graphics / mechanical items legitimately have none
            out.checked += 1
            if comp.unit_count > 1:
                out.multi_part += 1
            # Symbol-level in KiCad, so every pin carries the same flag.
            if not pins[0].name_visible:
                out.hidden_names += 1
            if not pins[0].number_visible:
                out.hidden_numbers += 1
            for _t in comp.symbol.shapes:
                if _t.kind == "text":
                    out.sym_texts += 1
                    out.sym_text_heights.add(round(_t.font_size, 1))
                    if round(_t.rotation) % 360:
                        out.sym_texts_rotated += 1

            # Write the neutral model back out as KiCad and read it
            # again. Exercises the EXPORT direction against real data,
            # which nothing else does: lib_easyeda_import(target=kicad)
            # uses this writer, and its only other cover is fixtures.
            try:
                from eda_agent.libimport.easyeda.kicad import (
                    symbol_to_kicad_sym,
                )

                again = read_kicad_symbol(symbol_to_kicad_sym(comp))
            except Exception as exc:  # noqa: BLE001
                out.round_trip.append(f"{lib}:{name} re-read raised {exc!r}")
            else:
                before = _pin_signature(comp)
                after = _pin_signature(again)
                if before != after:
                    lost = sorted(set(before) - set(after))[:2]
                    gained = sorted(set(after) - set(before))[:2]
                    out.round_trip.append(
                        f"{lib}:{name} lost {lost} gained {gained}")

                out.arcs += sum(1 for s in comp.symbol.shapes
                                if s.kind == "arc")
                g_before = _symbol_graphic_signature(comp)
                g_after = _symbol_graphic_signature(again)
                if g_before != g_after:
                    lost_g = sorted(set(g_before) - set(g_after), key=repr)
                    got_g = sorted(set(g_after) - set(g_before), key=repr)
                    out.graphic_round_trip.append(
                        f"{lib}:{name} lost {str(lost_g[:1])[:90]} "
                        f"gained {str(got_g[:1])[:90]}")

            per_unit: dict[int, list[str]] = {}
            for pin in pins:
                per_unit.setdefault(pin.unit, []).append(pin.number)
            for unit, numbers in per_unit.items():
                if len(numbers) != len(set(numbers)):
                    repeated = sorted({n for n in numbers
                                       if numbers.count(n) > 1})
                    out.repeated_pins.append(
                        f"{lib}:{name} sub-part {unit} repeats {repeated[:4]}")

            try:
                plan = build_altium_plan(
                    EasyEdaComponent(mpn=name, symbol=comp.symbol),
                    "T.SchLib", "T.PcbLib")
            except Exception as exc:  # noqa: BLE001
                out.incoherent.append(f"{lib}:{name} plan raised {exc!r}")
                continue

            create = next(s["args"] for s in plan["steps"]
                          if s["tool"] == "lib_create_symbol")
            part_count = int(create.get("part_count", 1))
            plan_pins = next(s["args"]["pins"] for s in plan["steps"]
                             if s["tool"] == "lib_add_pins")
            for pin in plan_pins:
                owner = int(pin.get("owner_part_id", 1))
                if not 0 <= owner <= part_count:
                    out.incoherent.append(
                        f"{lib}:{name} pin {pin['designator']} owns sub-part "
                        f"{owner} but part_count is {part_count}")
                    break
            if any(not p.get("designator") for p in plan_pins):
                out.incoherent.append(
                    f"{lib}:{name} emitted a pin with no designator")
    return out


def test_the_symbol_sample_is_large_enough(findings):
    """Guards the rest: a skipped or shrunken corpus proves nothing."""
    assert findings.checked > 100, (
        f"only {findings.checked} symbols converted; the corpus assertions "
        f"would pass vacuously")
    assert findings.derived > 50, (
        f"only {findings.derived} derived symbols sampled")
    assert findings.multi_part > 10, (
        f"only {findings.multi_part} multi-part symbols sampled; the "
        f"sub-part assertions would barely be exercised")


def test_symbols_survive_a_write_and_read_round_trip(findings):
    """Export then import must return the same pins.

    This is the only check the KiCad WRITER gets against real data, and
    it is where the writer's mistakes hide: emitting every pin into unit
    1 produces a file with the correct pin count, correct coordinates
    and correct names, which merely happens to have collapsed a quad
    gate into one gate. The signature compared here includes each pin's
    unit and visibility for exactly that reason.
    """
    assert not findings.round_trip, (
        f"{len(findings.round_trip)} of {findings.checked} symbols changed "
        f"when written and read back:\n  "
        + "\n  ".join(findings.round_trip[:10]))


def test_symbol_body_art_survives_a_round_trip(findings):
    """The body, not just the pins.

    Symbols and footprints have SEPARATE arc writers, so the footprint
    round trip says nothing about this one: reversing a symbol arc's
    sweep leaves every pin exactly where it was, and the pins-only
    comparison above passes while the body draws inside out. Confirmed
    by mutating that specific line.
    """
    assert findings.arcs > 50, (
        f"only {findings.arcs} symbol arcs sampled; this would barely "
        f"exercise the case it exists for")
    assert not findings.graphic_round_trip, (
        f"{len(findings.graphic_round_trip)} of {findings.checked} symbols "
        f"changed their body art when written and read back:\n  "
        + "\n  ".join(findings.graphic_round_trip[:10]))


def test_every_sampled_symbol_converts_to_a_coherent_plan(findings):
    """A pin must never be owned by a sub-part that does not exist.

    ``lib_create_symbol`` declares ``part_count`` and every pin names an
    owner, so an owner above that count is a symbol Altium cannot build.
    That is exactly the mismatch a tidy two-unit fixture never produces.
    """
    assert not findings.incoherent, (
        f"{len(findings.incoherent)} of {findings.checked} symbols "
        f"converted incoherently:\n  "
        + "\n  ".join(findings.incoherent[:10]))


def test_derived_symbols_inherit_real_geometry(findings):
    """Over half the corpus is ``extends``; none may come out empty.

    A derived symbol yielding no pins is the failure mode this reader
    shipped with, and it is invisible downstream: the plan looks
    structurally fine and produces a part with nothing to wire.
    """
    assert not findings.empty_derived, (
        f"{len(findings.empty_derived)} of {findings.derived} derived "
        f"symbols inherited no pins: {findings.empty_derived[:10]}")


def test_no_sub_part_repeats_a_pin_number(findings):
    """Within ONE sub-part a pin number may appear only once.

    A repeat there means units or body styles were merged. Both mistakes
    look like a converted part and neither shows up in a pin count.

    ACROSS sub-parts a repeat is legitimate and must not be flagged: it
    is how a shared electrode is drawn. The corpus makes the difference
    concrete -- the EABC80 valve puts pin 7 in units 2 and 3 because
    those triode sections share a cathode, and the CD4002 repeats pin 12
    the same way. An earlier version of this test banned duplicates
    outright and called all three a bug.
    """
    assert not findings.repeated_pins, (
        f"{len(findings.repeated_pins)} sub-parts repeat a pin number:\n  "
        + "\n  ".join(findings.repeated_pins[:10]))


# ---------------------------- footprints -----------------------------
#
# The same argument, applied where it matters more: a symbol that
# converts wrongly is annoying, a land pattern that converts wrongly is
# a board that cannot be assembled.

def _graphic_signature(comp):
    """The footprint's non-pad geometry, as a round trip must return it.

    Arcs matter most here. A Y mirror reverses an arc's sweep, and the
    reader rebuilds arcs from KiCad's three-point form while the writer
    has to put them back, so the endpoints alone would agree even with
    the curve bulging the wrong way -- hence radius and both flags.
    """
    out = []
    for s in comp.footprint.shapes:
        if s.kind in ("track", "polyline"):
            out.append((s.kind,
                        tuple((round(x), round(y)) for x, y in s.points)))
        elif s.kind == "circle":
            out.append(("circle", round(s.cx), round(s.cy), round(s.radius)))
        elif s.kind == "arc":
            out.append(("arc", round(s.x1), round(s.y1), round(s.x2),
                        round(s.y2), round(s.rx), s.large_arc, s.sweep))
        elif s.kind == "text":
            # Size, stroke, mirror and layer are here because their
            # absence is what let a defect live: the reader stamped a
            # constant 60 mils on every string and the writer emitted a
            # constant 1 mm, so both sides were wrong and a signature of
            # only text/position/rotation agreed with itself perfectly.
            # The corpus states 91 distinct heights, none of which
            # reached Altium.
            out.append(("text", s.text, round(s.x), round(s.y),
                        round(s.rotation), round(s.font_size),
                        round(s.stroke_width), bool(s.mirror), s.layer))
        elif s.kind == "rect":
            out.append(("rect", round(s.x), round(s.y), round(s.width),
                        round(s.height), s.layer))
        elif s.kind == "solid_region":
            # Vertices AND layer: a filled region that drifts onto the
            # wrong layer is copper or mask where none was drawn, and a
            # vertex count that grows every trip means the closure
            # convention is not canonical.
            out.append(("region", s.layer,
                        tuple((round(x), round(y)) for x, y in s.points)))
    return sorted(out, key=repr)


def _pad_signature(comp):
    """Everything about a footprint's pads a round trip must preserve.

    Includes rotation, shape, corner ratio and PLATING on purpose: those
    are the fields a writer loses while still producing a footprint with
    the right pad count in the right places, which is the only kind of
    bug that survives to a fab house.

    Plating was added after a mutation showed the gap. Writing every
    through-hole pad as plated -- copper in a mounting hole -- passed
    this check while every coordinate matched, because the only other
    cover was an EasyEDA fixture whose unplated hole takes a different
    code path entirely. 212 of the 6902 sampled pads are unplated.

    LAYER was added for the same reason and found a live defect: the
    writer emitted the full copper stack for every non-bottom pad, so a
    paste-only stencil aperture (331 of those 6902 pads) came back as
    copper. On the fine-pitch parts that use paste subdivision, that is
    a short between adjacent pads.

    Slot length is included too. The slot AXES bug was fixed by looking
    at hole_radius, and nothing was comparing the length that pairs
    with it.
    """
    return sorted(
        (p.number, round(p.cx), round(p.cy), round(p.width),
         round(p.height), round(p.hole_radius, 1), p.shape,
         round(p.rotation), round(getattr(p, "corner_ratio", 0.0), 4),
         bool(getattr(p, "plated", True)), p.layer,
         round(getattr(p, "hole_length", 0.0), 1))
        for p in comp.footprint.shapes if p.kind == "pad")


@dataclass
class _FpFindings:
    checked: int = 0
    pads: int = 0
    through_hole: int = 0
    errors: list[str] = field(default_factory=list)
    bad_size: list[str] = field(default_factory=list)
    bad_drill: list[str] = field(default_factory=list)
    far_away: list[str] = field(default_factory=list)
    silent_drops: list[str] = field(default_factory=list)
    round_trip: list[str] = field(default_factory=list)
    graphic_round_trip: list[str] = field(default_factory=list)
    arcs: int = 0
    #: Distinct text heights and stroke widths seen, and how many text
    #: items are mirrored. Counted so the graphic round trip cannot go
    #: vacuous on the fields a constant used to hide; see
    #: test_the_footprint_sample_still_varies_its_text.
    text_heights: set = field(default_factory=set)
    text_strokes: set = field(default_factory=set)
    mirrored_text: int = 0


@pytest.fixture(scope="module")
def fp_findings():
    """Convert a slice of the installed footprint corpus."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent
    from eda_agent.libimport.kicad.reader import read_kicad_footprint
    from eda_agent.libimport.providers.kicad_local import _footprint_dir

    root = _footprint_dir()
    if root is None:
        pytest.skip("no KiCad footprint libraries installed on this machine")

    out = _FpFindings()
    for lib in sorted(root.glob("*.pretty")):
        for mod in sorted(lib.glob("*.kicad_mod"))[:_PER_LIBRARY]:
            where = f"{lib.stem}/{mod.stem}"
            try:
                comp = read_kicad_footprint(
                    mod.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001 - collect, do not abort
                out.errors.append(f"{where} read raised {exc!r}")
                continue
            out.checked += 1
            pads = [s for s in comp.footprint.shapes if s.kind == "pad"]
            out.pads += len(pads)
            for pad in pads:
                if pad.width <= 0 or pad.height <= 0:
                    out.bad_size.append(
                        f"{where} pad {pad.number!r} is "
                        f"{pad.width:.0f}x{pad.height:.0f}")
                if abs(pad.cx) > 20000 or abs(pad.cy) > 20000:
                    out.far_away.append(
                        f"{where} pad {pad.number!r} at "
                        f"({pad.cx:.0f},{pad.cy:.0f})")
                if pad.is_through_hole:
                    out.through_hole += 1
                    if pad.hole_radius <= 0:
                        out.bad_drill.append(f"{where} pad {pad.number!r}")

            if not pads:
                continue
            plan = build_altium_plan(
                EasyEdaComponent(mpn=mod.stem, footprint=comp.footprint),
                "T.SchLib", "T.PcbLib")
            emitted = next(
                (s["args"]["pads"] for s in plan["steps"]
                 if s["tool"] == "lib_add_footprint_pads"), [])
            # A paste or mask opening is drawn as a region on its layer,
            # not as a pad: an Altium pad is copper and would short the
            # pads the opening subdivides.
            openings = sum(
                1 for s in plan["steps"] if s["tool"] == "lib_add_footprint_regions"
                for r in s["args"]["regions"]
                if r["layer"] in ("TopPaste", "BottomPaste", "TopSolder", "BottomSolder"))
            missing = len(pads) - len(emitted) - min(openings, sum(
                1 for p in pads if p.layer in (5, 6, 7, 8)))
            # Every other reason a pad may legitimately be absent.
            explained = any(
                "no pad number" in w or "UNPLATED" in w or "SLOT" in w
                or "APERTURE" in w
                for w in plan["warnings"])
            if missing and not explained:
                out.silent_drops.append(
                    f"{where}: {missing} pad(s) absent from the plan with "
                    f"no warning")

            # Write back out as KiCad and read it again. This found five
            # separate defects the moment it was first run, none of them
            # visible to any other check here: pad and text rotation not
            # mirrored with the Y flip, roundrect pads written as ovals,
            # a slot's axes read in the wrong order (which doubled the
            # hole on any horizontal slot), and a falsy-zero corner ratio
            # rounding pads the source left square.
            try:
                from eda_agent.libimport.easyeda.kicad import (
                    footprint_to_kicad_mod,
                )

                again = read_kicad_footprint(footprint_to_kicad_mod(comp))
            except Exception as exc:  # noqa: BLE001
                out.round_trip.append(f"{where} re-read raised {exc!r}")
                continue
            for _t in comp.footprint.shapes:
                if _t.kind == "text":
                    out.text_heights.add(round(_t.font_size, 1))
                    out.text_strokes.add(round(_t.stroke_width, 1))
                    out.mirrored_text += bool(_t.mirror)
            out.arcs += sum(1 for s in comp.footprint.shapes
                            if s.kind == "arc")
            g_before = _graphic_signature(comp)
            g_after = _graphic_signature(again)
            if g_before != g_after:
                lost_g = sorted(set(g_before) - set(g_after), key=repr)
                got_g = sorted(set(g_after) - set(g_before), key=repr)
                out.graphic_round_trip.append(
                    f"{where} lost {str(lost_g[:1])[:90]} "
                    f"gained {str(got_g[:1])[:90]}")

            before = _pad_signature(comp)
            after = _pad_signature(again)
            if before != after:
                lost = sorted(set(before) - set(after))
                gained = sorted(set(after) - set(before))
                # POLYGON -> RECT is the one conversion this writer
                # cannot do faithfully: KiCad has no Altium-style
                # polygon pad, the bounding rectangle is emitted instead
                # and document.py warns. Everything else is a defect.
                if all(a[6] == "POLYGON" and b[6] == "RECT"
                       for a, b in zip(lost, gained)):
                    continue
                out.round_trip.append(
                    f"{where} lost {lost[:1]} gained {gained[:1]}")
    return out


def test_the_footprint_sample_is_large_enough(fp_findings):
    """Same guard as the symbol side, for the same reason."""
    assert fp_findings.checked > 100, (
        f"only {fp_findings.checked} footprints converted")
    assert fp_findings.through_hole > 100, (
        f"only {fp_findings.through_hole} through-hole pads seen; the "
        f"drill assertion would be nearly vacuous")


def test_every_sampled_footprint_parses(fp_findings):
    assert not fp_findings.errors, (
        f"{len(fp_findings.errors)} of {fp_findings.checked} footprints "
        f"failed to read:\n  " + "\n  ".join(fp_findings.errors[:10]))


def test_no_pad_has_zero_size(fp_findings):
    """A zero-size pad is unbuildable and reads as a clean conversion.

    It is also the shape a units or field-order mistake produces, which
    is why it is worth asserting over thousands of real pads rather than
    the handful a fixture can carry.
    """
    assert not fp_findings.bad_size, (
        f"{len(fp_findings.bad_size)} of {fp_findings.pads} pads have no "
        f"area:\n  " + "\n  ".join(fp_findings.bad_size[:10]))


def test_through_hole_pads_all_keep_a_drill(fp_findings):
    """A through-hole pad with no hole cannot take its lead."""
    assert not fp_findings.bad_drill, (
        f"{len(fp_findings.bad_drill)} through-hole pads lost their "
        f"drill:\n  " + "\n  ".join(fp_findings.bad_drill[:10]))


def test_no_pad_lands_absurdly_far_from_the_origin(fp_findings):
    """Catches a units error, which nothing else here would.

    Every conversion in this reader is a scale factor, and a wrong one
    still produces a well-formed footprint, just one the size of a room.
    The largest real pad offset in the installed corpus is about 5100
    mils, so 20000 flags a mistake without tripping on a big connector.
    """
    assert not fp_findings.far_away, (
        f"{len(fp_findings.far_away)} pads sit implausibly far out:\n  "
        + "\n  ".join(fp_findings.far_away[:10]))


def test_no_pad_vanishes_from_the_plan_without_a_word(fp_findings):
    """The failure this converter actually shipped once.

    ``lib_add_footprint_pads`` drops any pad with a blank designator and
    reports a count rather than an error, so geometry can disappear
    between the reader and the plan with nothing raised anywhere. Every
    such loss has to be named in ``warnings``.
    """
    assert not fp_findings.silent_drops, (
        f"{len(fp_findings.silent_drops)} footprints lost pads "
        f"silently:\n  " + "\n  ".join(fp_findings.silent_drops[:10]))


def test_footprints_survive_a_write_and_read_round_trip(fp_findings):
    """Export then import must return the same land pattern.

    The strongest check in this file, and the one that earned its place:
    on its first run it failed on 243 of 439 footprints and every one
    was a real defect. A land pattern that converts wrongly is a board
    that cannot be assembled, and none of these showed up as an error,
    a warning, or a wrong pad count -- only as different geometry after
    a round trip.

    POLYGON pads are exempt, and only those: KiCad has no equivalent of
    an arbitrary polygon pad, so the bounding rectangle is written and
    the loss is reported in warnings rather than hidden.
    """
    assert not fp_findings.round_trip, (
        f"{len(fp_findings.round_trip)} of {fp_findings.checked} footprints "
        f"changed when written and read back:\n  "
        + "\n  ".join(fp_findings.round_trip[:10]))


def test_footprint_graphics_survive_a_round_trip(fp_findings):
    """Silkscreen and courtyard geometry, not just the copper.

    Arcs are the reason this is separate from the pad check. The reader
    rebuilds them from KiCad's start/mid/end form and the writer has to
    emit them back, across a Y mirror that reverses the sweep. An arc
    bulging the wrong way keeps both endpoints, so it is invisible in
    anything that compares positions.
    """
    assert fp_findings.arcs > 100, (
        f"only {fp_findings.arcs} arcs in the sample; this would barely "
        f"exercise the case it exists for")
    assert not fp_findings.graphic_round_trip, (
        f"{len(fp_findings.graphic_round_trip)} of {fp_findings.checked} "
        f"footprints changed their graphics when written and read "
        f"back:\n  " + "\n  ".join(fp_findings.graphic_round_trip[:10]))


def test_the_sample_still_contains_hidden_label_symbols(findings):
    """Guard the guard: the round trip must not go vacuous.

    _pin_signature compares name_visible / number_visible, but a
    comparison over a sample where every symbol shows its labels is
    green no matter what the reader and writer do with the flag. The
    corpus belongs to KiCad and changes on their schedule, and
    _PER_LIBRARY is a knob someone may turn down for speed, so coverage
    here is not something to assume once and forget.

    Measured on KiCad 10.0.1 at _PER_LIBRARY=3: 51 symbols hide pin
    names and 17 hide pin numbers, out of 615 with pins. The floors are
    deliberately far below that, so this fails when coverage COLLAPSES,
    not when the corpus shifts a little.
    """
    assert findings.checked > 100, (
        f"sample too small to conclude anything: {findings.checked}")
    assert findings.hidden_names >= 5, (
        f"only {findings.hidden_names} symbols in the sample hide pin "
        f"names, so the round trip barely covers name_visible")
    assert findings.hidden_numbers >= 3, (
        f"only {findings.hidden_numbers} symbols in the sample hide pin "
        f"numbers, so the round trip barely covers number_visible")


def test_the_footprint_sample_still_varies_its_text(fp_findings):
    """Guard the guard, footprint side.

    _graphic_signature compares text height, stroke and mirror, but a
    sample where every string happens to share one height would agree
    with itself no matter what the reader and writer did, which is
    exactly the state that let a hardcoded 60 mils survive.

    Measured on KiCad 10.0.1 at _PER_LIBRARY=3: 545 text items, 17
    distinct heights, 19 stroke widths, 4 mirrored. The floors sit well
    below that so this fails on a COLLAPSE in variety, not on the corpus
    shifting.
    """
    assert fp_findings.checked > 50, fp_findings.checked
    assert len(fp_findings.text_heights) >= 4, (
        f"only {len(fp_findings.text_heights)} distinct text heights in "
        f"the sample: {sorted(fp_findings.text_heights)}")
    assert len(fp_findings.text_strokes) >= 4, (
        f"only {len(fp_findings.text_strokes)} distinct stroke widths: "
        f"{sorted(fp_findings.text_strokes)}")
    assert fp_findings.mirrored_text >= 1, (
        "no mirrored text in the sample, so the round trip says nothing "
        "about the mirror flag")


def test_the_symbol_sample_still_contains_text(findings):
    """Guard the guard, symbol side.

    _symbol_graphic_signature now compares body text including its
    rotation and height. A sample with no symbol text, or none rotated,
    would agree with itself however the decidegree conversion was
    written, which is the state that let a discarded angle and a
    constant size sit here unnoticed.

    Measured on KiCad 10.0.1 at _PER_LIBRARY=3: 301 text items across
    624 symbols, 19 of them rotated, 10 distinct heights.
    """
    assert findings.sym_texts >= 20, (
        f"only {findings.sym_texts} symbol text items in the sample")
    assert findings.sym_texts_rotated >= 1, (
        "no rotated symbol text in the sample, so the round trip says "
        "nothing about the decidegree conversion")
    assert len(findings.sym_text_heights) >= 3, (
        f"only {len(findings.sym_text_heights)} distinct symbol text "
        f"heights: {sorted(findings.sym_text_heights)}")
