# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Component data models."""

from typing import Optional
from pydantic import BaseModel, Field


class Pin(BaseModel):
    """Represents a component pin."""

    designator: str
    name: str = ""
    electrical_type: str = "passive"  # passive, input, output, bidirectional, etc.
    x: float = 0
    y: float = 0
    rotation: float = 0
    length: float = 200  # mils
    hidden: bool = False


class Parameter(BaseModel):
    """Represents a component parameter."""

    name: str
    value: str
    visible: bool = True
    x: Optional[float] = None
    y: Optional[float] = None


class Component(BaseModel):
    """Represents a schematic or PCB component."""

    designator: str
    comment: str = ""
    description: str = ""
    footprint: str = ""
    library_ref: str = ""
    library_path: str = ""
    x: float = 0
    y: float = 0
    rotation: float = 0
    layer: str = "TopLayer"  # For PCB components
    mirrored: bool = False
    locked: bool = False
    pins: list[Pin] = Field(default_factory=list)
    parameters: list[Parameter] = Field(default_factory=list)
    unique_id: str = ""

    def get_parameter(self, name: str) -> Optional[Parameter]:
        """Get a parameter by name."""
        for param in self.parameters:
            if param.name.lower() == name.lower():
                return param
        return None

    def set_parameter(self, name: str, value: str) -> None:
        """Set a parameter value, creating if it doesn't exist."""
        param = self.get_parameter(name)
        if param:
            param.value = value
        else:
            self.parameters.append(Parameter(name=name, value=value))


class LibraryComponent(BaseModel):
    """Represents a component in a library."""

    name: str
    description: str = ""
    library_path: str = ""
    footprints: list[str] = Field(default_factory=list)
    models_3d: list[str] = Field(default_factory=list)
    default_designator: str = ""
    parameters: list[Parameter] = Field(default_factory=list)
