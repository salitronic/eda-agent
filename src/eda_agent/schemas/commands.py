# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Per-command schema declarations.

Add new command schemas here and call ``register_command`` so they're
included in the generated ``mcp_schemas/`` artifact. Tools can also
register their own schemas at module import time.

The schemas serve two roles:
  1. Pydantic validation on the Python side for outgoing requests.
  2. JSON Schema artifact for Pascal-side or external-tool validation.

Coverage is intentionally incremental: the core generic primitives are
declared here; per-tool schemas grow as new tools are added.
"""

from typing import Optional

from pydantic import BaseModel, Field

from .registry import register_command
from .scope import ScopeSchema


# ---------------------------------------------------------------------------
# application.*
# ---------------------------------------------------------------------------

class EmptyParams(BaseModel):
    """Schema for commands that take no parameters."""


class PingResponse(BaseModel):
    pong: bool
    script_version: str
    protocol_version: int
    cast_errors: int = 0


register_command(
    "application.ping",
    params_model=EmptyParams,
    response_model=PingResponse,
    description="Health check. Returns script + protocol version.",
)

register_command(
    "application.get_open_documents",
    params_model=EmptyParams,
    description="List all open documents across all open projects.",
)

register_command(
    "application.get_active_document",
    params_model=EmptyParams,
    description="Return the currently focused document (if any).",
)


# ---------------------------------------------------------------------------
# generic.*
# ---------------------------------------------------------------------------

class QueryObjectsParams(BaseModel):
    object_type: str = Field(min_length=1)
    properties: str = Field(min_length=1)
    scope: ScopeSchema = Field(default_factory=ScopeSchema)
    filter: str = ""
    limit: int = Field(default=0, ge=0)


class ModifyObjectsParams(BaseModel):
    object_type: str = Field(min_length=1)
    set: str = Field(min_length=1)
    scope: ScopeSchema = Field(default_factory=ScopeSchema)
    filter: str = ""


class CreateObjectParams(BaseModel):
    object_type: str = Field(min_length=1)
    properties: str = Field(min_length=1)
    container: str = "document"


class DeleteObjectsParams(BaseModel):
    object_type: str = Field(min_length=1)
    scope: ScopeSchema = Field(default_factory=ScopeSchema)
    filter: str = ""


register_command(
    "generic.query_objects",
    params_model=QueryObjectsParams,
    description="Iterate Altium objects matching a filter and read properties.",
)
register_command(
    "generic.modify_objects",
    params_model=ModifyObjectsParams,
    description="Bulk-set properties on objects matching a filter.",
)
register_command(
    "generic.create_object",
    params_model=CreateObjectParams,
    description="Create and place a single Altium object.",
)
register_command(
    "generic.delete_objects",
    params_model=DeleteObjectsParams,
    description="Delete objects matching a filter.",
)


# ---------------------------------------------------------------------------
# project.*
# ---------------------------------------------------------------------------

class ProjectOpenParams(BaseModel):
    project_path: str = Field(min_length=1)


register_command(
    "project.open",
    params_model=ProjectOpenParams,
    description="Open a .PrjPcb / .PrjSch project file.",
)
register_command(
    "project.get_focused",
    params_model=EmptyParams,
    description="Return the currently focused project, if any.",
)
register_command(
    "project.get_open_projects",
    params_model=EmptyParams,
    description="List all open projects.",
)
