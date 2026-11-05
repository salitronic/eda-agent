# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A board text's UnderlyingString can be read through the generic layer.

Text and ConvertedString both answer the RESOLVED string, so a text typed as
".Layer_Count" read back as "4" and nothing said it was a special string.
UnderlyingString is what the text was typed as. It is read only: writing
Text already sets it.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _units() -> dict:
    return load(_ALTIUM / "PCBGeneric.pas", minimum=20)


def test_underlying_string_belongs_to_the_text_group():
    owner = _units()["ExtraPCBPropertyOwner"]
    # Group 3 is the board text; the name must be in the list that returns 3.
    group = re.search(r"\(PropName = 'BarCodeKind'\).*?Then Result := 3", owner, re.S)
    assert group and "'UnderlyingString'" in group.group(0)


def test_underlying_string_reads_the_text_member():
    getter = _units()["PCBGetExtra"]
    assert re.search(r"PropName\s*=\s*'UnderlyingString'\s+Then\s+Result\s*:=\s*"
                     r"Txt\.UnderlyingString", getter), "no UnderlyingString reader"


def test_underlying_string_is_refused_as_a_write():
    setter = _units()["PCBSetExtra"]
    assert re.search(r"If\s+PropName\s*=\s*'UnderlyingString'\s+Then\s+Exit;", setter)
    # Refused before the writes, so no branch below can take it.
    assert (setter.index("'UnderlyingString' Then Exit")
            < setter.index("Owner := ExtraPCBPropertyOwner"))


def test_underlying_string_is_advertised():
    assert "UnderlyingString" in _units()["KnownPCBPropertyList"]
