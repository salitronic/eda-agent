# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_set_active_document still takes a file name that one open document has.

The full path is the safe form. A bare name is resolved against every open
document, project members and free documents alike, and taken only when
exactly one file carries it; two files of that name, or none, are refused
with a reason that says so, before anything is brought to the front.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import load

_APP_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Application.pas"


@pytest.fixture(scope="module")
def body() -> str:
    return load(_APP_PAS, minimum=10)["App_SetActiveDocument"]


def test_a_bare_name_is_resolved_against_every_open_document(body):
    bare = body.index("Pos('\\', Doc) = 0")
    resolve = body.index("ResolveLoadedDocPath(Doc)", bare)
    spelling = body.index("EnsurePathProblem(Doc, False)")
    assert bare < resolve < spelling


def test_no_match_or_two_matches_is_refused_before_the_front_changes(body):
    resolve = body.index("ResolveLoadedDocPath(Doc)")
    refusal = body.index("no open document, or more than one, has that file name", resolve)
    assert refusal < body.index("Client.ShowDocument")
    assert "'BAD_TARGET'" in body[resolve:refusal]
