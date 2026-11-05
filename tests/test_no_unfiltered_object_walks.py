# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Every object walk names the kinds it walks.

``AddFilter_ObjectSet(AllObjects)`` on a footprint crashed the scripting
engine (#44): the iterator handed back an object it could not cast and
ScriptingSystem.DLL read address FFFFFFFFFFFFFFFF, an access violation no
Try catches, which takes the polling loop down with it. Classifying by
ObjectId afterwards does not help, the walk still reaches the object that
faults. An iterator with no object filter at all walks the same set.

Two AllObjects walks were left after the fix: the footprint select and a
component's union. Both now name the kinds they handle, and the sets must
still cover what each routine acts on.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load, strip_comments

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"

# Walks that genuinely need every kind, by function name, each with its
# reason. Keep this short; a new entry needs the same kind of argument.
_UNFILTERED_ALLOWED = {
    # A board walk restricted to ONE layer, counting whatever sits there:
    # a single-type filter would report an assembly layer made of strings
    # and arcs as empty. A board, not a footprint, which is where #44 was
    # measured.
    "PCB_CountPrimitivesOnLayer",
}

_ITERATOR_MADE = re.compile(
    r"(\w+)\s*:=\s*[\w.]+\.(?:BoardIterator_Create|GroupIterator_Create|"
    r"SpatialIterator_Create)\b")


def _all_functions():
    for path in sorted(_ALTIUM.glob("*.pas")):
        minimum = 20 if path.name == "PCBGeneric.pas" else 1
        for name, body in load(path, minimum=minimum).items():
            yield path.name, name, body


def _object_set(body: str, var: str) -> set[str]:
    m = re.search(re.escape(var) + r"\.AddFilter_ObjectSet\s*\(\s*MkSet\s*\(([^;]*?)\)\s*\)",
                  body)
    assert m, f"no MkSet object filter on {var}"
    return {k.strip() for k in m.group(1).split(",")}


def test_no_walk_over_all_objects():
    hits = []
    for path in sorted(_ALTIUM.glob("*.pas")):
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"AddFilter_ObjectSet\s*\(\s*AllObjects\s*\)", text,
                             re.IGNORECASE):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path.name}:{line}")
    assert not hits, f"AllObjects walks (#44): {hits}"


def test_every_iterator_is_filtered_before_it_is_walked():
    unfiltered = []
    seen_iterators = 0
    for fname, name, body in _all_functions():
        for m in _ITERATOR_MADE.finditer(body):
            seen_iterators += 1
            var = m.group(1)
            rest = body[m.end():]
            first = re.search(re.escape(var) + r"\.FirstPCBObject", rest)
            before = rest[:first.start()] if first else rest
            filtered = (re.search(re.escape(var) + r"\.AddFilter_ObjectSet", before)
                        or re.search(r"FilterFootprintWalk\s*\(\s*" + re.escape(var),
                                     before))
            if not filtered and name not in _UNFILTERED_ALLOWED:
                unfiltered.append(f"{fname}:{name}:{var}")
    assert seen_iterators > 100, "the iterator pattern stopped matching"
    assert not unfiltered, f"walks with no object filter (#44): {unfiltered}"


def _footprint_kinds_the_resolver_names() -> set[str]:
    resolver = load(_ALTIUM / "Utils.pas")["ObjectTypeFromStringPCB"]
    kinds = set(re.findall(r"Result\s*:=\s*(e\w+Object)", resolver))
    assert len(kinds) >= 10, "the type resolver stopped parsing"
    # Board-only kinds: a footprint holds no component, rule, ratsnest
    # line or split plane.
    return kinds - {"eComponentObject", "eRuleObject", "eConnectionObject",
                    "eSplitPlaneObject"}


def test_footprint_select_walks_every_kind_a_set_can_name():
    body = load(_ALTIUM / "Library.pas")["Lib_SelectFootprintPrimitives"]
    walked = _object_set(body, "BIter")
    missing = _footprint_kinds_the_resolver_names() - walked
    assert not missing, f"object_type values the select can never match: {missing}"
    # A set with only layer, name or box matches any kind: the delete's
    # footprint kinds, coordinates included.
    walk = load(_ALTIUM / "Library.pas")["FilterFootprintWalk"]
    footprint_set = _object_set(walk.split("Else", 1)[1], "GrpIter")
    assert footprint_set <= walked, footprint_set - walked


def test_component_union_reaches_every_part_with_a_union_index():
    body = load(_ALTIUM / "PCBGeneric.pas", minimum=20)["PCBSetComponentUnion"]
    walked = _object_set(body, "GIter")
    needed = {"eTrackObject", "eArcObject", "ePadObject", "eViaObject",
              "eFillObject", "eRegionObject", "eTextObject",
              "eComponentBodyObject"}
    assert needed <= walked, needed - walked
    assert "SetState_UnionIndex" in body.split("FirstPCBObject", 1)[1]
