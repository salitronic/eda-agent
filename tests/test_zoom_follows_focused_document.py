# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""obj_zoom must zoom the focused document, libraries included.

Gen_Zoom used to probe SchServer and then the board. With a footprint
library focused it found neither and answered "No schematic or PCB
document is open to zoom"; with a schematic library or any schematic
current it sent Sch:Zoom even when a footprint was in front. It now
reads the focused document's kind first.
"""

from __future__ import annotations

from pathlib import Path

GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _gen_zoom_body() -> str:
    src = GENERIC.read_text(encoding="utf-8")
    start = src.index("Function Gen_Zoom(")
    return src[start:src.index("\nFunction ", start + 1)]


def test_focused_kind_is_read_before_the_probe():
    body = _gen_zoom_body()
    kind = body.index("DM_FocusedDocument")
    probe = body.index("SchServer.GetCurrentSchDocument")
    assert kind < probe


def test_every_design_kind_maps_to_its_zoom_process():
    body = _gen_zoom_body()
    assert "If IsSchEditorKind(FocusedKind) Then ZoomProcess := 'Sch:Zoom'" in body
    assert "(FocusedKind = 'PCB') Or (FocusedKind = 'PCBLIB') Then ZoomProcess := 'PCB:Zoom'" in body


def test_a_wiring_diagram_zooms_in_the_schematic_editor():
    src = GENERIC.read_text(encoding="utf-8")
    start = src.index("Function IsSchEditorKind(")
    kinds = src[start:src.index("\nFunction ", start + 1)]
    for kind in ("'SCH'", "'SCHLIB'", "'HARNESSWIRINGDIAGRAM'"):
        assert kind in kinds
    # The point zoom asks the same question, so a wiring diagram never falls
    # through to the board fallback and zooms some other open board.
    start = src.index("Function Gen_ZoomToXY(")
    assert "Else If IsSchEditorKind(FocusedKind) Then" in src[start:src.index("\nFunction ", start + 1)]
