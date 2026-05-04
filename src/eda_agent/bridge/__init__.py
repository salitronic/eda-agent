# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Altium Bridge - Communication layer between Python and Altium Designer."""

from .altium_bridge import AltiumBridge, get_bridge, reset_bridge, PROTOCOL_VERSION
from .process_manager import AltiumProcessManager
from .script_launcher import ScriptLauncher
from .exceptions import (
    AltiumError,
    AltiumNotRunningError,
    AltiumTimeoutError,
    AltiumCommandError,
    AltiumProtocolError,
    ScriptNotLoadedError,
    PreconditionError,
    NotFoundError,
    InvalidParameterError,
    OperationFailedError,
    InternalError,
    raise_for_code,
)

__all__ = [
    "AltiumBridge",
    "get_bridge",
    "reset_bridge",
    "PROTOCOL_VERSION",
    "AltiumProcessManager",
    "ScriptLauncher",
    "AltiumError",
    "AltiumNotRunningError",
    "AltiumTimeoutError",
    "AltiumCommandError",
    "AltiumProtocolError",
    "ScriptNotLoadedError",
    "PreconditionError",
    "NotFoundError",
    "InvalidParameterError",
    "OperationFailedError",
    "InternalError",
    "raise_for_code",
]
