# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Two ways an Altium 21 session stopped (issues #260 and #262).

* ``eCustomShape`` is an undeclared identifier before Altium Designer 22.8,
  the release that added custom pad shapes, and an undeclared identifier
  stops the polling loop with an error box no Try catches. On AD 21.4 a
  pad read reached it. Every pad reader asks ``ShapeIsCustom``, which reads
  the version first.
* A filtered ``pcb_set_text_visibility`` skipped a component with
  ``Continue`` from inside a Try and left AD 21 unresponsive. The loop now
  advances once, at its end.
"""

from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNITS = ("Library", "PCBGeneric", "PCB", "Generic", "Audit", "Project", "Application")


def test_the_gate_reads_major_and_minor():
    utils = load(ALTIUM / "Utils.pas", minimum=10)
    code = utils["AltiumVersionCode"]
    assert "Client.GetProductVersion" in code and "Major * 100 + Minor" in code
    assert "AltiumVersionCode(0) >= 2208" in utils["PadCustomShapeSupported"]
    body = utils["ShapeIsCustom"]
    assert body.index("If Not PadCustomShapeSupported(0) Then Exit;") < body.index("eCustomShape")


def test_ecustomshape_is_named_only_behind_its_gate():
    readers = 0
    for unit in UNITS:
        for name, body in load(ALTIUM / f"{unit}.pas", minimum=10).items():
            assert "eCustomShape" not in body, (
                f"{unit}.pas {name} names eCustomShape; ask ShapeIsCustom, "
                "or Altium before 22.8 stops the loop on it")
            readers += body.count("ShapeIsCustom(")
    assert readers >= 8, "fewer shape readers than there were; the scan has stopped working"


def test_filtered_text_visibility_advances_once():
    body = load(ALTIUM / "PCB.pas")["PCB_SetTextVisibility"]
    assert "Continue" not in body
    assert body.count("NextPCBObject") == 1
    assert "If IsMatch Then" in body
