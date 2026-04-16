# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Altium Bridge - Communication layer between Python and Altium Designer."""

from .altium_bridge import AltiumBridge, get_bridge, reset_bridge
from .process_manager import AltiumProcessManager
from .script_launcher import ScriptLauncher
from .exceptions import (
    AltiumError,
    AltiumNotRunningError,
    AltiumTimeoutError,
    AltiumCommandError,
    ScriptNotLoadedError,
)

__all__ = [
    "AltiumBridge",
    "get_bridge",
    "reset_bridge",
    "AltiumProcessManager",
    "ScriptLauncher",
    "AltiumError",
    "AltiumNotRunningError",
    "AltiumTimeoutError",
    "AltiumCommandError",
    "ScriptNotLoadedError",
]
