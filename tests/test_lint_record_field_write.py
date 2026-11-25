# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The lint catches a field write through a by-value record property.

``Pin.Location.X := v`` compiles, runs and reports success, and the pin
stays at 0,0: Location is returned by value, so the write changes a
temporary. Lib_AddPin did this while Lib_AddSymbolLine, a few routines
down, already read, modified and wrote the record back.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LINT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "lint.py"


@pytest.fixture(scope="module")
def lint():
    spec = importlib.util.spec_from_file_location("_altium_lint_rec", LINT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint_rec"] = mod
    spec.loader.exec_module(mod)
    return mod


def _hit(lint, line: str) -> bool:
    code = lint.strip_comments_and_strings(line)
    return bool(lint.RULE_RECORD_FIELD_WRITE.pattern.search(code))


@pytest.mark.parametrize("line", [
    "        Pin.Location.X := MilsToCoordF(X);",
    "    Rect.Corner.Y:=Y2;",
])
def test_a_direct_field_write_is_flagged(lint, line):
    assert _hit(lint, line)


@pytest.mark.parametrize("line", [
    "        Loc := Pin.Location;",
    "        Loc.X := MilsToCoordF(X);",
    "        Pin.Location := Loc;",
    "    If Pin.Location.X = X Then Exit;",
    "    PinX := CoordToMils(Pin.Location.X);",
    "    { direct `Line.Location.X := value` writes to a copy }",
])
def test_reads_and_whole_record_writes_are_not_flagged(lint, line):
    assert not _hit(lint, line)


def test_the_rule_is_an_error_and_is_registered(lint):
    assert lint.RULE_RECORD_FIELD_WRITE in lint.LINE_RULES
    assert lint.RULE_RECORD_FIELD_WRITE.severity == "error"
