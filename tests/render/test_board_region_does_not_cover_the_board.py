# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A shaped board's own region is outlined, never filled over the copper.

A round board carries a netless region on MultiLayer the size of its
outline. Filled, it was drawn over every layer, and a routed board came
back from design_visual_review as vias on a grey disc; the agent fell
back to an Altium screenshot.
"""

from __future__ import annotations

import re

from eda_agent.render.pcb_svg import render_pcb_svg

DISC = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]


def test_the_board_region_is_an_outline_and_copper_stays_filled():
    svg = render_pcb_svg({
        "outline": [],
        "regions": [{"layer": "MultiLayer", "net": "", "pts": DISC},
                    {"layer": "BottomLayer", "net": "GND", "pts": DISC}],
        "tracks": [{"layer": "BottomLayer", "net": "SIG", "x1": 100, "y1": 100,
                    "x2": 900, "y2": 100, "width": 10}],
    })
    board = re.search(r'<polygon class="region region-outline"[^>]*>', svg)
    assert board and 'fill="none"' in board.group(0)
    pour = re.search(r'<polygon class="region" data-net="GND"[^>]*>', svg)
    assert pour and 'fill="none"' not in pour.group(0)
    assert 'class="track"' in svg
