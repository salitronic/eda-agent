# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A custom-shaped pad must be read back as custom, not as a round pad.

Altium turns a pad plus a copper region into one pad with a custom shape
(PCB:CustomPadShape, Action=Convert|Object=Region). Its TopShape is then
eCustomShape. Every pad reader maps the shape to a word through a chain of
comparisons and fell through to round (or Other), so a 6 x 7 mm L-shaped
pad read back as a round pad of 6 x 7 mm. Each mapper now names it, before
its fall-through.
"""

from __future__ import annotations

from pathlib import Path

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"

# (unit, function, the line that must come first, the custom line, the fall-through)
READERS = [
    ("PCB.pas", "Function PCB_GetPadProperties(",
     "Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'RoundedRect'",
     "Else If Pad.TopShape = eCustomShape Then ShapeStr := 'Custom'",
     "Else ShapeStr := 'Other';"),
    ("PCB.pas", "Function LmShapeName(",
     "If S = eRoundedRectangular Then Name := 'roundrect';",
     "If S = eCustomShape Then Name := 'custom';",
     "Result := Name;"),
    ("Generic.pas", "Function Gen_GetPcbGeometry(",
     "Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'RoundedRect'",
     "Else If Pad.TopShape = eCustomShape Then ShapeStr := 'Custom';",
     "Except End;"),
    ("Library.pas", "Function Lib_GetFootprintPads(",
     "Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'",
     "Else If Pad.TopShape = eCustomShape Then ShapeStr := 'custom'",
     "Else ShapeStr := 'round';"),
    ("Library.pas", "Function Lib_GetLibraryGeometry(",
     "Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'",
     "Else If Pad.TopShape = eCustomShape Then ShapeStr := 'custom'",
     "Else ShapeStr := 'round';"),
    ("Library.pas", "Function Lib_GetPadGeometry(",
     "Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'",
     "Else If Pad.TopShape = eCustomShape Then ShapeStr := 'custom'",
     "Else ShapeStr := 'round';"),
]


def _function(unit: str, header: str) -> str:
    src = (ALTIUM / unit).read_text(encoding="latin-1")
    start = src.index(header)
    end = src.index("\nFunction ", start + 1)
    return src[start:end]


def test_every_pad_reader_names_a_custom_shape():
    for unit, header, _, custom, _ in READERS:
        assert custom in _function(unit, header), (unit, header)


def test_the_custom_case_comes_before_the_fall_through():
    for unit, header, before, custom, fallthrough in READERS:
        body = _function(unit, header)
        at = body.index(custom)
        assert body.index(before) < at, (unit, header)
        assert at < body.index(fallthrough, body.index(before)), (unit, header)


def test_no_pad_reader_is_left_without_the_custom_case():
    """Any shape chain that names the rounded rectangle names custom too."""
    for unit in ("PCB.pas", "Generic.pas", "Library.pas"):
        src = (ALTIUM / unit).read_text(encoding="latin-1")
        chains = src.count("TopShape = eRoundedRectangular Then ShapeStr")
        chains += src.count("If S = eRoundedRectangular Then Name")
        named = src.count("TopShape = eCustomShape Then ShapeStr")
        named += src.count("If S = eCustomShape Then Name")
        assert chains == named, (unit, chains, named)
