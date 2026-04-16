# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Layer definitions for PCB design."""

from typing import Optional
from pydantic import BaseModel, Field


class Layer(BaseModel):
    """Represents a PCB layer."""

    name: str
    layer_id: int = 0
    layer_type: str = "signal"  # signal, plane, mechanical, mask, silkscreen, etc.
    copper_weight: float = 1.0  # oz
    dielectric_const: float = 4.2
    thickness: float = 1.4  # mils
    visible: bool = True
    color: int = 0


class LayerStack(BaseModel):
    """Represents a PCB layer stackup."""

    layers: list[Layer] = Field(default_factory=list)
    board_thickness: float = 62  # mils (typical 1.6mm = 62 mils)

    def get_layer(self, name: str) -> Optional[Layer]:
        """Get a layer by name."""
        for layer in self.layers:
            if layer.name.lower() == name.lower():
                return layer
        return None

    def get_signal_layers(self) -> list[Layer]:
        """Get all signal layers."""
        return [l for l in self.layers if l.layer_type == "signal"]

    def get_plane_layers(self) -> list[Layer]:
        """Get all plane layers."""
        return [l for l in self.layers if l.layer_type == "plane"]


# Standard Altium layer names and IDs
STANDARD_LAYERS = {
    # Signal Layers
    "TopLayer": 1,
    "MidLayer1": 2,
    "MidLayer2": 3,
    "MidLayer3": 4,
    "MidLayer4": 5,
    "MidLayer5": 6,
    "MidLayer6": 7,
    "MidLayer7": 8,
    "MidLayer8": 9,
    "MidLayer9": 10,
    "MidLayer10": 11,
    "MidLayer11": 12,
    "MidLayer12": 13,
    "MidLayer13": 14,
    "MidLayer14": 15,
    "MidLayer15": 16,
    "MidLayer16": 17,
    "MidLayer17": 18,
    "MidLayer18": 19,
    "MidLayer19": 20,
    "MidLayer20": 21,
    "MidLayer21": 22,
    "MidLayer22": 23,
    "MidLayer23": 24,
    "MidLayer24": 25,
    "MidLayer25": 26,
    "MidLayer26": 27,
    "MidLayer27": 28,
    "MidLayer28": 29,
    "MidLayer29": 30,
    "MidLayer30": 31,
    "BottomLayer": 32,
    # Internal Planes
    "InternalPlane1": 33,
    "InternalPlane2": 34,
    "InternalPlane3": 35,
    "InternalPlane4": 36,
    "InternalPlane5": 37,
    "InternalPlane6": 38,
    "InternalPlane7": 39,
    "InternalPlane8": 40,
    "InternalPlane9": 41,
    "InternalPlane10": 42,
    "InternalPlane11": 43,
    "InternalPlane12": 44,
    "InternalPlane13": 45,
    "InternalPlane14": 46,
    "InternalPlane15": 47,
    "InternalPlane16": 48,
    # Silkscreen
    "TopOverlay": 49,
    "BottomOverlay": 50,
    # Solder Mask
    "TopSolder": 51,
    "BottomSolder": 52,
    # Paste Mask
    "TopPaste": 53,
    "BottomPaste": 54,
    # Drill
    "DrillGuide": 55,
    "DrillDrawing": 56,
    # Mechanical Layers
    "Mechanical1": 57,
    "Mechanical2": 58,
    "Mechanical3": 59,
    "Mechanical4": 60,
    "Mechanical5": 61,
    "Mechanical6": 62,
    "Mechanical7": 63,
    "Mechanical8": 64,
    "Mechanical9": 65,
    "Mechanical10": 66,
    "Mechanical11": 67,
    "Mechanical12": 68,
    "Mechanical13": 69,
    "Mechanical14": 70,
    "Mechanical15": 71,
    "Mechanical16": 72,
    # Keep Out
    "KeepOutLayer": 73,
    # Multi Layer
    "MultiLayer": 74,
}


def get_layer_id(layer_name: str) -> Optional[int]:
    """Get the layer ID for a layer name."""
    return STANDARD_LAYERS.get(layer_name)


def get_layer_name(layer_id: int) -> Optional[str]:
    """Get the layer name for a layer ID."""
    for name, id in STANDARD_LAYERS.items():
        if id == layer_id:
            return name
    return None
