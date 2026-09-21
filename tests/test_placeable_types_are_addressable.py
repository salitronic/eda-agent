# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Anything this server can place, it must also be able to find and delete.

``ObjectTypeFromString`` turns a caller's ``eTextFrame`` into an ObjectId
for ``obj_query`` / ``obj_modify`` / ``obj_delete``.  A type absent from
it resolves to -1, so the object is unreachable: it can be created and
then neither listed nor removed.

Reported 2026-09-21 by a user trying to delete a text frame from a sheet.
``obj_delete`` reported 0 processed, ``tool_guide`` offered no dedicated
tool, and the session went on to try the canvas, then an interactive
Altium process that blocked waiting for a mouse drag.  The object had
been placed by this same server minutes earlier.

SIX types had drifted out, not one: eTextFrame, eNote, eProbe,
eHarnessConnector, eCrossSheetConnector and eCompileMask.  That is why
this guard derives the expected set from the source rather than listing
the types by hand -- a hand-written list is what drifted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import strip_comments


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERIC_PAS = REPO_ROOT / "scripts" / "altium" / "Generic.pas"

#: Types created for internal bookkeeping rather than placed as sheet
#: objects a caller would ever address by type. Keep this SHORT and say
#: why: every entry here is a hole in the guarantee above.
NOT_ADDRESSABLE = {
    # An implementation is a model link owned by a component, reached
    # through the component rather than queried as a sheet object.
    "eImplementation",
}


def _created_types() -> set:
    """Every ``SchObjectFactory(eXxx`` type this server can place."""
    src = strip_comments(GENERIC_PAS.read_text(encoding="utf-8",
                                               errors="replace"))
    return set(re.findall(r"SchObjectFactory\(\s*(e[A-Za-z]+)", src))


def _resolvable_types() -> set:
    """Every type ``ObjectTypeFromString`` can return."""
    src = strip_comments(GENERIC_PAS.read_text(encoding="utf-8",
                                               errors="replace"))
    m = re.search(
        r"Function ObjectTypeFromString\(.*?\).*?\n(.*?)\nEnd;", src,
        re.DOTALL)
    assert m, "ObjectTypeFromString is gone or unparseable"
    return set(re.findall(r"Result\s*:=\s*(e[A-Za-z]+)", m.group(1)))


def test_the_scan_sees_both_sides():
    """Floor: two regexes that stop matching would pass silently."""
    created, resolvable = _created_types(), _resolvable_types()
    assert len(created) >= 15, (
        f"only {len(created)} placeable types found; the SchObjectFactory "
        f"scan has stopped working and this guard checks nothing")
    assert len(resolvable) >= 15, (
        f"only {len(resolvable)} resolvable types found; the "
        f"ObjectTypeFromString scan has stopped working")


@pytest.mark.parametrize("type_name", sorted(_created_types() - NOT_ADDRESSABLE))
def test_every_placeable_type_can_be_addressed(type_name: str) -> None:
    """One test per type, so a failure names the one that drifted."""
    assert type_name in _resolvable_types(), (
        f"{type_name} can be placed by a sch_place_ tool but "
        f"ObjectTypeFromString does not resolve it, so obj_query and "
        f"obj_delete cannot reach it. The server would be able to put "
        f"this object on a sheet and then neither find nor remove it. "
        f"Add it to ObjectTypeFromString and to SchObjectTypeNames.")


def test_the_advertised_list_matches_what_resolves():
    """``SchObjectTypeNames`` is what a refused call prints.

    If it disagrees with the resolver, the error message sends the caller
    to a type that does not work, or hides one that does.
    """
    src = strip_comments(GENERIC_PAS.read_text(encoding="utf-8",
                                               errors="replace"))
    m = re.search(r"Function SchObjectTypeNames\(.*?\).*?\n(.*?)\nEnd;",
                  src, re.DOTALL)
    assert m, "SchObjectTypeNames is gone"
    advertised = set(re.findall(r"(e[A-Z][A-Za-z]+)", m.group(1)))
    resolvable = _resolvable_types()

    missing = sorted(resolvable - advertised)
    assert not missing, (
        f"these types resolve but are not advertised, so a caller told "
        f"'unknown object type' never learns they exist: {missing}")

    phantom = sorted(advertised - resolvable)
    assert not phantom, (
        f"these types are advertised but do not resolve, so the error "
        f"message recommends something that then fails: {phantom}")
