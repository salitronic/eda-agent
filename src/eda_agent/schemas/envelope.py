# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""IPC envelope schema — must agree with Pascal Main.pas BuildSuccessResponse
and BuildErrorResponseDetailed."""

from typing import Any, Optional

from pydantic import BaseModel, Field


PROTOCOL_VERSION = 2


class IPCRequest(BaseModel):
    """Wire envelope for a request."""

    protocol_version: int = Field(default=PROTOCOL_VERSION)
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    command: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class IPCError(BaseModel):
    """Structured error payload — matches BuildErrorResponseDetailed."""

    code: str
    message: str
    details: Optional[Any] = None


class IPCResponse(BaseModel):
    """Wire envelope for a response."""

    protocol_version: int = Field(default=PROTOCOL_VERSION)
    id: str
    success: bool
    data: Optional[Any] = None
    error: Optional[IPCError] = None
