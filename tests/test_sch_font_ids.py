# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""FontId is asked only of the objects that have one.

A FontId read on a sheet entry raised an undeclared-identifier modal and
stopped the polling loop. FontId belongs to ISch_Label and its descendants,
ISch_Port and ISch_TextFrame; an entry's label font is TextFontID.
"""
from __future__ import annotations

import re

from tests.test_sch_properties_match_their_interface import _routine, _source

_HAS_FONT = {"eLabel", "eNetLabel", "ePowerObject", "eCrossSheetConnector",
             "eParameter", "eDesignator", "eSheetName", "eSheetFileName",
             "ePort", "eTextFrame", "eNote"}


def _types(routine: str) -> set[str]:
    return set(re.findall(r"Obj\.ObjectId = (e\w+)", _routine(_source(), routine)))


def test_the_fontid_allowlist_is_the_label_port_and_text_frame_family():
    assert _types("SchObjectHasFontId") == _HAS_FONT
    assert _types("SchObjectHasTextFontId") == {"eSheetEntry", "eHarnessEntry"}


def _branch(routine: str, prop: str) -> str:
    body = _routine(_source(), routine)
    start = body.index(f"Else If PropName = '{prop}' Then")
    nxt = body.find("Else If PropName = ", start + 10)
    return body[start:nxt]


def test_reads_and_writes_go_through_the_gate():
    for routine in ("GetSchProperty", "SetSchProperty"):
        fontid = _branch(routine, "FontId")
        assert fontid.index("SchObjectHasFontId(Obj)") < fontid.index("Obj.FontId")
        entry = _branch(routine, "TextFontId")
        # Only through the typed entry locals, never the base object.
        assert "Obj.TextFontID" not in entry.replace("EntryObj.TextFontID", "")
        assert "eSheetEntry" in entry and "eHarnessEntry" in entry


def test_the_preflight_names_both_properties():
    body = _routine(_source(), "UnsupportedSchProperty")
    assert "(PropName = 'FontId') And (Not SchObjectHasFontId(Obj))" in body
    assert "(PropName = 'TextFontId') And (Not SchObjectHasTextFontId(Obj))" in body
