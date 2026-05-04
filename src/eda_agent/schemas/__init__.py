# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Pydantic-backed schemas for the wire protocol.

Pydantic models on the Python side are the source of truth. JSON Schema
files are emitted into the workspace (``mcp_schemas/``) at startup so the
Pascal side can validate incoming requests against the same shape Python
serialises. Drift between Python and Pascal is impossible by construction:
both read the same generated artifact.
"""

from .envelope import (
    IPCRequest,
    IPCResponse,
    IPCError,
    PROTOCOL_VERSION,
)
from .registry import (
    CommandSpec,
    register_command,
    get_command_spec,
    list_commands,
    write_schemas_to,
)
from .scope import ScopeSchema

__all__ = [
    "IPCRequest",
    "IPCResponse",
    "IPCError",
    "PROTOCOL_VERSION",
    "CommandSpec",
    "register_command",
    "get_command_spec",
    "list_commands",
    "write_schemas_to",
    "ScopeSchema",
]
