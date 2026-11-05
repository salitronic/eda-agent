# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Each type allowed an Orientation write has one in Altium's SDK.

Apart from test_sch_properties_match_their_interface because the SDK
index lives in reference/, which is untracked: here it skips, and the
fpc job in CI, which fails on any skip, does not run this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_sch_properties_match_their_interface import _ORIENTATION_TYPES

INDEX = Path(__file__).parent.parent / "reference" / "altium_sdk" / "altium_sdk_index.json"


def test_every_allowed_type_has_an_orientation_in_the_sdk():
    if not INDEX.exists():
        pytest.skip("the SDK index is not in this checkout (reference/ is untracked)")
    interfaces = json.loads(INDEX.read_text(encoding="utf-8-sig"))["interfaces"]

    def lineage(name):
        out = [name]
        for base in interfaces.get(name, {}).get("bases") or []:
            out += lineage(base)
        return out

    def has_orientation(name):
        return any("Orientation" in str(member) for n in lineage(name)
                   for member in interfaces.get(n, {}).get("props", [])
                   + interfaces.get(n, {}).get("methods", []))

    for constant, interface in _ORIENTATION_TYPES.items():
        assert has_orientation(interface), f"{constant}: {interface} has no Orientation"
    assert not has_orientation("ISch_SheetSymbol")
