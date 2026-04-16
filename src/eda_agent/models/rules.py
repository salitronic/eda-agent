# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Design rule definitions."""

from typing import Optional, Any
from enum import Enum
from pydantic import BaseModel, Field


class RuleType(str, Enum):
    """Types of design rules."""

    # Electrical Rules
    CLEARANCE = "Clearance"
    SHORT_CIRCUIT = "ShortCircuit"
    UN_ROUTED_NET = "UnRoutedNet"
    UN_CONNECTED_PIN = "UnConnectedPin"

    # Routing Rules
    WIDTH = "Width"
    ROUTING_TOPOLOGY = "RoutingTopology"
    ROUTING_PRIORITY = "RoutingPriority"
    ROUTING_LAYERS = "RoutingLayers"
    ROUTING_CORNERS = "RoutingCorners"
    ROUTING_VIA_STYLE = "RoutingViaStyle"
    FANOUT_CONTROL = "FanoutControl"
    DIFFERENTIAL_PAIRS_ROUTING = "DiffPairsRouting"

    # SMT Rules
    SMD_TO_CORNER = "SMDToCorner"
    SMD_TO_PLANE = "SMDToPlane"
    SMD_NECK_DOWN = "SMDNeckDown"

    # Mask Rules
    SOLDER_MASK_EXPANSION = "SolderMaskExpansion"
    PASTE_MASK_EXPANSION = "PasteMaskExpansion"

    # Plane Rules
    PLANE_CONNECT_STYLE = "PlaneConnectStyle"
    PLANE_CLEARANCE = "PlaneClearance"
    POLYGON_CONNECT_STYLE = "PolygonConnectStyle"

    # Manufacturing Rules
    MINIMUM_ANNULAR_RING = "MinimumAnnularRing"
    HOLE_SIZE = "HoleSize"
    LAYER_PAIRS = "LayerPairs"
    HOLE_TO_HOLE_CLEARANCE = "HoleToHoleClearance"
    MINIMUM_SOLDER_MASK_SLIVER = "MinimumSolderMaskSliver"
    SILK_TO_SOLDER_MASK_CLEARANCE = "SilkToSolderMaskClearance"
    SILK_TO_SILK_CLEARANCE = "SilkToSilkClearance"
    NET_ANTENNAE = "NetAntennae"

    # High Speed Rules
    MATCHED_LENGTHS = "MatchedLengths"
    DAISY_CHAIN_STUB_LENGTH = "DaisyChainStubLength"
    VIAS_UNDER_SMD = "ViasUnderSMD"
    MAX_VIA_COUNT = "MaxViaCount"

    # Placement Rules
    ROOM_DEFINITION = "RoomDefinition"
    PLACEMENT_ROOMS = "ComponentPlacement"
    COMPONENT_CLEARANCE = "ComponentClearance"
    COMPONENT_ORIENTATIONS = "ComponentOrientations"
    PERMITTED_LAYERS = "PermittedLayers"
    NETS_TO_IGNORE = "NetsToIgnore"

    # Signal Integrity Rules
    SIGNAL_STIMULUS = "SignalStimulus"
    OVERSHOOT_FALLING_EDGE = "OvershootFallingEdge"
    OVERSHOOT_RISING_EDGE = "OvershootRisingEdge"
    UNDERSHOOT_FALLING_EDGE = "UndershootFallingEdge"
    UNDERSHOOT_RISING_EDGE = "UndershootRisingEdge"
    IMPEDANCE = "Impedance"
    SIGNAL_TOP_VALUE = "SignalTopValue"
    SIGNAL_BASE_VALUE = "SignalBaseValue"
    FLIGHT_TIME_RISING_EDGE = "FlightTimeRisingEdge"
    FLIGHT_TIME_FALLING_EDGE = "FlightTimeFallingEdge"
    SLOPE_RISING_EDGE = "SlopeRisingEdge"
    SLOPE_FALLING_EDGE = "SlopeFallingEdge"
    SUPPLY_NETS = "SupplyNets"


class Rule(BaseModel):
    """Base design rule model."""

    name: str
    rule_type: RuleType
    enabled: bool = True
    priority: int = 1
    comment: str = ""
    scope_1: str = "All"  # First scope expression (e.g., "All", "InNet('VCC')")
    scope_2: str = "All"  # Second scope expression (for binary rules)
    unique_id: str = ""


class Clearance(Rule):
    """Clearance rule - minimum spacing between objects."""

    rule_type: RuleType = RuleType.CLEARANCE
    minimum: float = 10  # mils
    # Specific clearances for different object types
    track_to_track: Optional[float] = None
    track_to_pad: Optional[float] = None
    track_to_via: Optional[float] = None
    pad_to_pad: Optional[float] = None
    pad_to_via: Optional[float] = None
    via_to_via: Optional[float] = None
    track_to_polygon: Optional[float] = None


class Width(Rule):
    """Width rule - track width constraints."""

    rule_type: RuleType = RuleType.WIDTH
    minimum: float = 6  # mils
    preferred: float = 10  # mils
    maximum: float = 100  # mils


class RoutingVia(Rule):
    """Via style rule - via size constraints."""

    rule_type: RuleType = RuleType.ROUTING_VIA_STYLE
    via_diameter_min: float = 20  # mils
    via_diameter_preferred: float = 24  # mils
    via_diameter_max: float = 50  # mils
    via_hole_min: float = 10  # mils
    via_hole_preferred: float = 12  # mils
    via_hole_max: float = 25  # mils


class SolderMaskExpansion(Rule):
    """Solder mask expansion rule."""

    rule_type: RuleType = RuleType.SOLDER_MASK_EXPANSION
    expansion: float = 4  # mils


class PasteMaskExpansion(Rule):
    """Paste mask expansion rule."""

    rule_type: RuleType = RuleType.PASTE_MASK_EXPANSION
    expansion: float = 0  # mils (negative = shrink)


class MinimumAnnularRing(Rule):
    """Minimum annular ring rule."""

    rule_type: RuleType = RuleType.MINIMUM_ANNULAR_RING
    minimum: float = 7  # mils


class HoleSize(Rule):
    """Hole size constraints."""

    rule_type: RuleType = RuleType.HOLE_SIZE
    minimum: float = 8  # mils
    maximum: float = 250  # mils


class DRCViolation(BaseModel):
    """Represents a DRC violation."""

    rule_name: str
    rule_type: str
    message: str
    object_1: str = ""
    object_2: str = ""
    x: float = 0
    y: float = 0
    layer: str = ""
    actual_value: Optional[float] = None
    allowed_value: Optional[float] = None
