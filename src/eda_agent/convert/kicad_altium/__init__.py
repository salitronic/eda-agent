# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Import a KiCad project into Altium by rebuilding it through the bridge.

The KiCad files are read here (an open format); everything on the Altium
side is drawn with this server's own tools, so Altium creates every
object itself: the libraries, the sheets, the board, its copper and rules.
"""
