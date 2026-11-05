# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One spelling for every file path the bridge is given.

Altium keys an open document by the path string it was opened with. The same
library opened as C:/dir\\x.PcbLib and focused as C:\\dir\\x.PcbLib became two
open documents: the focus loaded a second copy from disk, the tool edited the
stale copy, and the next edit raised "Object accessed after being destroyed"
in ADVPCB (measured on Altium 26.11).
"""

from __future__ import annotations

import re

from tests.test_handlers_verify_their_outcome import _body, _decommented, _src


def test_every_string_value_goes_through_the_canonical_path():
    body = _decommented(_body(_src("Main.pas"), "ExtractJsonValue"))
    assert "Value := CanonicalPathValue(Key," in body


def test_only_path_keys_with_a_drive_or_unc_value_are_rewritten():
    body = _decommented(_body(_src("Main.pas"), "CanonicalPathValue"))
    for suffix in ("= 'path'", "= 'library'", "= '_file'", "K = 'destination'"):
        assert suffix in body, suffix
    assert "Copy(Value, 2, 1) = ':'" in body
    assert "'//'" in body and "'\\\\'" in body
    assert "StringReplace(Value, '/', '\\', -1)" in body


def test_no_focused_path_is_read_raw():
    raw = re.compile(r"FocusedPath\s*:=\s*Doc\.DM_FullPath\s*;")
    for unit in ("Library.pas", "Project.pas", "Generic.pas", "PCB.pas"):
        assert not raw.search(_decommented(_src(unit))), unit


def test_the_project_focus_check_compares_canonical_paths():
    body = _decommented(_src("Project.pas"))
    assert "UpperCase(CanonicalPathValue('path', FocusedPath))" in body
