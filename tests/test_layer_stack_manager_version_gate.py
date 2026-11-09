# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Layer Stack Manager handlers ask the version before touching it.

Reported on AD 21.4: RemoveLayerStackProvider, GetState_Weight,
GetDrillPairCount and OpenDocumentFromPcb are undeclared there and halt
the script past any Try, and reading the provider without the refresh
answers with the stack as it was before the last change. Below AD 22 the
handlers refuse and point at pcb_physical_stack; AD 22 to 24 are not
measured and are let through.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


@pytest.fixture(scope="module")
def fns():
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))


@pytest.mark.parametrize("handler", [
    "PCB_LayerStackManager", "PCB_MoveLayer", "PCB_LoadStackManagerModel"])
def test_the_version_is_asked_before_the_manager_is_reached(fns, handler):
    body = fns[handler]
    gate = body.index("Result := LayerStackManagerRefusal(RequestId);")
    assert body.index("If Result <> '' Then Exit;") > gate
    for member in ("LayerStackManager", "RemoveLayerStackProvider",
                   "OpenDocumentFromPcb", "GetLayerStackProvider"):
        at = body.find(member, gate + len("LayerStackManagerRefusal"))
        if at >= 0:
            assert at > gate, f"{handler} reaches {member} before the gate"


def test_every_handler_that_drops_the_provider_is_gated(fns):
    for name, body in fns.items():
        if "RemoveLayerStackProvider" in body or "OpenDocumentFromPcb" in body:
            if name.startswith("PCB_"):
                assert "LayerStackManagerRefusal(RequestId)" in body, name


def test_the_refusal_names_the_route_that_works(fns):
    body = fns["LayerStackManagerRefusal"]
    assert "(Code > 0) And (Code < 2200)" in body
    assert "pcb_physical_stack" in body and "'UNSUPPORTED_VERSION'" in body
