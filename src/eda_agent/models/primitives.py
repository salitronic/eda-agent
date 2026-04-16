# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Schematic and PCB primitive models."""

from typing import Optional
from pydantic import BaseModel, Field


# ============================================================================
# Schematic Primitives
# ============================================================================


class Wire(BaseModel):
    """Represents a schematic wire."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 1  # small, medium, large or value
    color: int = 0  # Color index
    net_name: str = ""


class NetLabel(BaseModel):
    """Represents a net label."""

    x: float
    y: float
    net_name: str
    rotation: float = 0
    font_size: int = 10
    color: int = 0


class Port(BaseModel):
    """Represents a sheet port."""

    x: float
    y: float
    name: str
    io_type: str = "unspecified"  # input, output, bidirectional, unspecified
    style: str = "none_horizontal"
    width: float = 200


class PowerPort(BaseModel):
    """Represents a power port (VCC, GND, etc.)."""

    x: float
    y: float
    net_name: str
    style: str = "bar"  # bar, wave, arrow, power_ground, signal_ground, earth
    rotation: float = 0
    color: int = 0


class SheetSymbol(BaseModel):
    """Represents a hierarchical sheet symbol."""

    x: float
    y: float
    width: float = 500
    height: float = 400
    file_name: str = ""
    sheet_name: str = ""
    unique_id: str = ""


class BusEntry(BaseModel):
    """Represents a bus entry."""

    x: float
    y: float
    rotation: float = 0  # 0, 90, 180, 270


# ============================================================================
# PCB Primitives
# ============================================================================


class Track(BaseModel):
    """Represents a PCB track/trace."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 10  # mils
    layer: str = "TopLayer"
    net_name: str = ""
    locked: bool = False


class Arc(BaseModel):
    """Represents a PCB arc."""

    x_center: float
    y_center: float
    radius: float
    start_angle: float = 0
    end_angle: float = 360
    width: float = 10
    layer: str = "TopLayer"
    net_name: str = ""


class Via(BaseModel):
    """Represents a PCB via."""

    x: float
    y: float
    hole_size: float = 12  # mils
    diameter: float = 24  # mils
    start_layer: str = "TopLayer"
    end_layer: str = "BottomLayer"
    net_name: str = ""
    locked: bool = False


class Pad(BaseModel):
    """Represents a PCB pad."""

    designator: str = ""
    x: float = 0
    y: float = 0
    hole_size: float = 0  # 0 for SMD
    x_size: float = 60  # mils
    y_size: float = 60  # mils
    shape: str = "round"  # round, rectangular, octagonal
    layer: str = "MultiLayer"
    rotation: float = 0
    net_name: str = ""
    plated: bool = True


class Polygon(BaseModel):
    """Represents a PCB polygon pour."""

    name: str = ""
    layer: str = "TopLayer"
    net_name: str = ""
    vertices: list[tuple[float, float]] = Field(default_factory=list)
    pour_over: str = "all"  # all, same_net
    remove_dead_copper: bool = True
    remove_islands: bool = False
    thermal_relief: bool = True
    relief_conductors: int = 4
    relief_expansion: float = 10
    relief_air_gap: float = 10


class Fill(BaseModel):
    """Represents a solid fill region."""

    x1: float
    y1: float
    x2: float
    y2: float
    layer: str = "TopLayer"
    net_name: str = ""
    rotation: float = 0


class Region(BaseModel):
    """Represents a PCB region."""

    layer: str = "TopLayer"
    vertices: list[tuple[float, float]] = Field(default_factory=list)
    kind: str = "solid"  # solid, cutout, board


# ============================================================================
# Common Primitives
# ============================================================================


class Text(BaseModel):
    """Represents a text string (schematic or PCB)."""

    x: float
    y: float
    text: str
    height: float = 60  # mils
    width: float = 0  # 0 = auto
    rotation: float = 0
    layer: str = "TopOverlay"  # PCB layer
    font: str = "Default"
    bold: bool = False
    italic: bool = False
    mirrored: bool = False


class Line(BaseModel):
    """Represents a graphical line."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 10
    layer: str = "TopOverlay"


class Rectangle(BaseModel):
    """Represents a rectangle."""

    x1: float
    y1: float
    x2: float
    y2: float
    line_width: float = 10
    layer: str = "TopOverlay"
    filled: bool = False
    transparent: bool = True


class Dimension(BaseModel):
    """Represents a dimension annotation."""

    x1: float
    y1: float
    x2: float
    y2: float
    layer: str = "MechanicalLayer1"
    text_height: float = 60
    line_width: float = 10
    arrow_size: float = 100
    prefix: str = ""
    suffix: str = ""
    units: str = "mils"
