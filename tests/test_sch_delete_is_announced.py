# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A schematic object the delete takes off the sheet is announced to
Altium, so the next compile no longer holds it.

MEASURED on AD 26.11 (scratch copy of a project sheet): a power port
deleted from a pin's wire and a net label put in its place. Without the
announcement the compile kept the port, the new label's net took in all
nine pins of the rail and the rail itself was gone; a recompile changed
nothing, only closing and reopening the sheet did. With it, the same
steps gave the pin alone on the label's net and the rail with its other
eight pins. Altium's own delete example sends the same message.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _delete_branch() -> str:
    fns = functions(strip_comments(_PAS.read_text(encoding="utf-8")))
    body = next(b for b in fns.values()
                if "If Mode = 'delete' Then" in b and "RemoveFromComponents(SchDoc, FoundObj);" in b)
    return body.split("If Mode = 'delete' Then", 1)[1].split("If Mode = 'modify' Then", 1)[0]


def test_each_removal_from_the_sheet_is_announced():
    branch = _delete_branch()
    remove = branch.index("SchDoc.RemoveSchObject(FoundObj);\n                Except")
    announce = branch.index("SchRegisterObject(SchDoc, FoundObj);", remove)
    assert announce < branch.index("Inc(TotalMatched);", remove)


def test_a_sheet_entry_is_announced_to_its_symbol():
    branch = _delete_branch()
    remove = branch.index("Sym.RemoveSchObject(FoundObj);")
    assert branch.index("SchRegisterObject(Sym, FoundObj);", remove) \
        < branch.index("Break;", remove)
