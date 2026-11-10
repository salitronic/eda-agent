# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_get_nets lists each pin once, with the net a copy names for it.

A 16-part module symbol placed over six sheets came back with every pin
three or four times, ran into the limit, and some pins read '?' while
other copies named their net (#296). The BOM listed such a part once per
copy too.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Project.pas"


@pytest.fixture(scope="module")
def fns():
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))


def test_a_question_mark_is_no_net(fns):
    body = fns["IsNamedNet"]
    assert "(NetName <> '')" in body and "(NetName <> '?')" in body


def test_the_named_copies_are_noted_before_any_pin_is_listed(fns):
    body = fns["Proj_GetNets"]
    first = body.index("If Pass = 1 Then")
    assert body.index("Named.Add(Key)", first) < body.index("Listed.Add(Key)")
    assert "For Pass := 1 To 2 Do" in body


def test_a_pin_is_listed_once_and_an_unnamed_copy_gives_way(fns):
    body = fns["Proj_GetNets"]
    skip = body.index("If (Listed.IndexOf(Key) >= 0)")
    assert "(Not IsNamedNet(NetName)) And (Named.IndexOf(Key) >= 0)" in body[skip:]
    # The limit counts listed pins, so it is checked after the repeats.
    assert skip < body.index("If Count >= Limit Then")
    assert '"repeats_skipped":' in body


def test_skipped_nils_are_counted_on_one_pass_only(fns):
    body = fns["Proj_GetNets"]
    assert "Begin Inc(Skipped); Continue; End;" not in body
    assert body.count("If Pass = 2 Then Inc(Skipped);") == 3


def test_the_bom_lists_a_designator_once(fns):
    body = fns["Proj_GetBOM"]
    seen = body.index("If Listed.IndexOf(CompDesig) >= 0 Then")
    assert seen < body.index("If Count >= Limit Then") < body.index("Listed.Add(CompDesig);")
    assert "Listed.Free;" in body and '"repeats_skipped":' in body
