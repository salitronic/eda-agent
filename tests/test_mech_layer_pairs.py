# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Mechanical layer pairs: who is paired with whom, and moving a pair kind.

pcb_get_mech_layer_names listed every enabled mechanical layer without its
partner, so a caller could not see which layers form a pair.

A pair kind belongs to ONE pair, the way a single kind belongs to one
layer. A single kind was moved off its old layer before it was written; a
pair kind was never moved off its old pair, so giving "Assembly" to a new
pair while another pair held it read back unchanged and was refused.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _pcb() -> dict:
    return load(_ALTIUM / "PCB.pas", minimum=20)


def _lib() -> dict:
    return load(_ALTIUM / "Library.pas", minimum=20)


def test_layer_names_report_each_layers_partner():
    body = _pcb()["PCB_GetMechLayerNames"]
    assert "Board.MechanicalPairs" in body
    assert re.search(r"PairIsDefined\(MechPairs,\s*Lyr,\s*OtherLyr\)", body)
    assert "'\"partner_layer\":' + PartnerJson" in body
    # Only ENABLED layers are offered as partners.
    assert re.search(r"If Enabled Then EnabledList := EnabledList", body)


def test_release_clears_the_kind_from_every_other_pair():
    body = _lib()["ReleasePairKindHolders"]
    assert re.search(r"If I <> KeepIdx Then", body)
    assert re.search(r"If K = PairKind Then", body)
    assert re.search(r"SetState_LayerPairKind\(I\)\s*:=\s*0", body)
    # Not Set is shared by any number of pairs and is never released.
    assert re.search(r"If PairKind <= 0 Then Exit;", body)


def test_restore_puts_the_kind_back():
    body = _lib()["RestorePairKindHolders"]
    assert re.search(r"SetState_LayerPairKind\(I\)\s*:=\s*PairKind", body)


def _retries_after_release(body: str) -> None:
    release = body.index("ReleasePairKindHolders(MechPairs, PairKind, PairIdx)")
    rewrite = body.index("SetState_LayerPairKind(PairIdx) := PairKind", release)
    restore = body.index("RestorePairKindHolders(MechPairs, PairKind, Released)", rewrite)
    assert release < rewrite < restore


def test_set_mech_layer_kind_releases_and_reports():
    body = _pcb()["PCB_SetMechLayerKind"]
    _retries_after_release(body)
    assert "'\"pairs_released\":'" in body


def test_layer_ops_release_through_the_shared_join():
    _retries_after_release(_lib()["JoinAndKindPair"])
