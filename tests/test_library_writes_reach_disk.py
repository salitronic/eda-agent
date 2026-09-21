# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A component added to a library must actually be saveable.

Adding a component to a .SchLib takes two things that are easy to do
separately and useless apart:

1. ``AddSchComponent`` puts it in the data model, and
2. a ``SCHM_PrimitiveRegistration`` broadcast tells the document that
   something was added.

Without (2) the symbol is real in memory -- ``lib_get_component_details``
reads it back in full and the copy reports ``verified: true`` -- while the
document believes it is unchanged, so every save route correctly declines
to write it.

MEASURED 2026-09-21: a component copied into a .SchLib stayed absent from
disk across ``app_save_all``, ``WorkspaceManager:SaveObject`` and Altium's
own File > Save. The file was byte-identical at 662016 bytes with zero
occurrences of the new name. Creating the same symbol from scratch grew
the file, because ``Lib_CreateSymbol`` broadcasts and the copy path did
not.

The other half is ``MarkLibDirty``, which used to flag
``DM_FocusedDocument`` while ignoring the ``ISch_Lib`` handed to it. When
the focused document was anything else, the edited library was never
flagged at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import strip_comments


REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PAS = REPO_ROOT / "scripts" / "altium" / "Library.pas"

#: Handlers that remove and re-add the SAME, already-registered component
#: purely to refresh the library's internal name index. They are not
#: introducing a new object, so the new-component broadcast does not
#: apply. Listed explicitly so that adding a genuinely new add-site here
#: is a deliberate act rather than an oversight.
REINDEX_ONLY = {
    "Lib_BatchRename",
    "Lib_RenameComponent",
}


def _functions() -> dict:
    """``{name: comment-free body}`` for every Function in Library.pas."""
    src = strip_comments(LIBRARY_PAS.read_text(encoding="utf-8",
                                               errors="replace"))
    starts = [(m.group(1), m.start())
              for m in re.finditer(r"^Function\s+(\w+)", src, re.M)]
    out = {}
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(src)
        out[name] = src[start:end]
    return out


def _adders() -> dict:
    return {n: b for n, b in _functions().items() if "AddSchComponent(" in b}


def test_the_scan_finds_the_adders():
    """Floor: a regex that stops matching would pass over everything."""
    found = _adders()
    assert len(found) >= 4, (
        f"only {len(found)} functions found calling AddSchComponent; the "
        f"scan has stopped working and this guard checks nothing")


@pytest.mark.parametrize("handler", sorted(set(_adders()) - REINDEX_ONLY))
def test_a_new_component_is_registered(handler: str) -> None:
    """Every handler that introduces a NEW component broadcasts it."""
    body = _adders()[handler]
    assert "SCHM_PrimitiveRegistration" in body, (
        f"{handler} calls AddSchComponent without broadcasting "
        f"SCHM_PrimitiveRegistration. The component will read back "
        f"correctly and never reach disk, because the document is never "
        f"told it changed. If this handler only re-adds an existing "
        f"registered component to refresh the index, add it to "
        f"REINDEX_ONLY and say why.")


def test_mark_lib_dirty_flags_the_library_it_was_given():
    """Not whatever document happens to be focused.

    Flagging the focused document silently dirties the wrong file and
    leaves the edited one clean, which cannot be distinguished from
    success afterwards.
    """
    body = _functions()["MarkLibDirty"] if "MarkLibDirty" in _functions() else ""
    if not body:
        src = strip_comments(LIBRARY_PAS.read_text(encoding="utf-8",
                                                   errors="replace"))
        m = re.search(r"Procedure\s+MarkLibDirty.*?(?=\nProcedure |\nFunction )",
                      src, re.DOTALL)
        assert m, "MarkLibDirty is gone; this guard must follow it"
        body = m.group(0)

    assert "SchLib.DocumentName" in body, (
        "MarkLibDirty must resolve the library from its own SchLib "
        "parameter via SchLib.DocumentName")
    assert "DM_FocusedDocument" not in body, (
        "MarkLibDirty must not consult the focused document. It is handed "
        "the library to flag; using the focused one instead flags the "
        "wrong file and leaves the edit unsaved with no way to tell.")
