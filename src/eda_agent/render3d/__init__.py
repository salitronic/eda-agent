# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Photographic pictures of a board: a scene built from what Altium
reports, rendered by eda-agent's own path tracer (eda-agent-render)."""

from . import engine, parts
from .board import FINISHES, MASKS, SILKS, BoardScene, Look, build
from .engine import QUALITY, VIEWS, render, tracer, view_for

__all__ = ["FINISHES", "MASKS", "SILKS", "QUALITY", "VIEWS", "BoardScene", "Look",
           "build", "engine", "parts", "render", "tracer", "view_for"]
