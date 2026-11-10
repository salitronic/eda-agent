# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium project to a clean KiCad project.

KiCad's own Altium importer does the translation; what it brings in is not
yet a project anyone would keep. This package takes the imported files to
one: one library symbol per part in project libraries, power ports as
power symbols, fields from the Altium parameters, sheets on the connection
grid, board nets named as the schematic names them, rules, stack-up and net
classes read from the Altium board through the bridge, footprints in
project libraries and the mechanical layers mapped by kind. Every step is
checked against the pin-to-net map, which must not change.

Steps, in order (``pipeline.convert``):

1. import    kicad.exe's File > Import Non-KiCad Project > Altium (importer)
2. schematic one symbol per part, power symbols, fields, grid (schematic)
3. wiring    stubs trimmed to their labels, title block, PWR_FLAGs (schematic)
4. board     net names, zones, holes, fields, rules (board_pass, KiCad python)
5. libraries footprints saved to project .pretty libraries (board_pass)
6. text      embedded models, layer kinds, crosses to Fab (footprints)
7. checks    pin-to-net map, ERC, DRC (checks)
"""
