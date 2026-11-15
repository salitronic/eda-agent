# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Every handler that takes the library's target symbol checks for a SchLib first.

GetTargetLibComponent reads ``SchLib.CurrentSchComponent``. A schematic
SHEET has no such member, and DelphiScript resolves members at run time,
so a sheet in front stopped the script past any Try. Every caller but
library.split_pin_functions checked ``ObjectId = eSchLib`` before it.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"
_CALL = "GetTargetLibComponent(SchLib)"
_CHECK = re.compile(r"SchLib\.ObjectId\s*<>\s*eSchLib")


def _callers():
    fns = load(_LIB)
    return {name: body for name, body in fns.items()
            if _CALL in body and name != "GetTargetLibComponent"}


def test_there_are_callers_to_check():
    assert len(_callers()) >= 8


def test_every_caller_checks_for_a_schlib_before_the_call():
    missing = []
    for name, body in _callers().items():
        head = body[:body.index(_CALL)]
        if not _CHECK.search(head):
            missing.append(name)
    assert not missing, missing


def test_split_pin_functions_refuses_a_sheet():
    body = load(_LIB)["Lib_SplitPinFunctions"]
    head = body[:body.index(_CALL)]
    assert "If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then" in head
    assert "NO_SCHLIB" in head
