# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The batch library handlers never open a library by its bare file name.

MEASURED on AD 25.8.1: library.batch_set_params called WITHOUT
library_path took the focused document's DM_FileName, which is the file
NAME ("Copy of x.SchLib"), and passed it to WorkspaceManager:OpenObject.
Altium does not find a bare name; it makes a NEW empty SchLib of that name
and brings it to the front, so every parameter of the batch went into the
new library and the one the caller meant was never touched.
library.batch_rename has the same code. Both now take the full path, and
with no full path they open nothing: the focused library is already the
current one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _focused_block(body: str) -> str:
    # From the focused-document lookup to the open. Located first, so the
    # checks below cannot pass by matching nothing.
    assert "DM_FocusedDocument" in body
    block = body.split("DM_FocusedDocument", 1)[1]
    assert "WorkspaceManager:OpenObject" in block
    return block.split("WorkspaceManager:OpenObject", 1)[0]


@pytest.mark.parametrize("handler", ["Lib_BatchSetParams", "Lib_BatchRename"])
def test_the_focused_library_is_taken_by_its_full_path(handler):
    block = _focused_block(load(_ALTIUM / "Library.pas")[handler])
    assert "DocFullPath(" in block
    assert "DM_FileName" not in block, "the bare file name must never be opened"


@pytest.mark.parametrize("handler", ["Lib_BatchSetParams", "Lib_BatchRename"])
def test_a_path_that_is_not_absolute_opens_nothing(handler):
    block = _focused_block(load(_ALTIUM / "Library.pas")[handler])
    assert "If Not LooksAbsolutePath(LibPath) Then LibPath := '';" in block
    # The open after the guard runs only on a non-empty path.
    after = load(_ALTIUM / "Library.pas")[handler].split(
        "If Not LooksAbsolutePath(LibPath) Then LibPath := '';", 1)[1]
    assert after.index("If LibPath <> '' Then") < after.index(
        "WorkspaceManager:OpenObject")


def test_the_helpers_are_defined_before_library():
    # DelphiScript has no forward declarations: Main.pas builds first.
    main = load(_ALTIUM / "Main.pas", minimum=50)
    assert "DocFullPath" in main and "LooksAbsolutePath" in main
