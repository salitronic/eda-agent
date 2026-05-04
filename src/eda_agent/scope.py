# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Structured scope parameter for object-iterating commands.

The wire form is a JSON object the Pascal side parses via ParseScope:

    {"type": "active_doc"}
    {"type": "project"}
    {"type": "project", "file_path": "C:/.../proj.PrjPcb"}
    {"type": "doc",     "file_path": "C:/.../sheet.SchDoc"}

User-facing tools accept either the new dict form, a Scope model, or a
legacy string ("doc:C:/...", "project:C:/...", "active_doc"). ``to_wire``
normalises every input to the structured wire form so the Pascal side
sees one consistent shape.
"""

from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


ScopeType = Literal["active_doc", "project", "doc"]


class Scope(BaseModel):
    """Structured scope. Always serialises to the wire form."""

    type: ScopeType = "active_doc"
    file_path: Optional[str] = Field(default=None)


ScopeInput = Union[Scope, dict, str, None]


def to_wire(scope: ScopeInput) -> dict:
    """Normalise any caller-supplied scope to the wire form.

    Accepts:
      - None                  → {"type": "active_doc"}
      - Scope model           → its dict
      - dict                  → validated through Scope
      - str  (legacy compact) → parsed:
          "active_doc" | "project"           → {"type": ...}
          "doc:C:/path"                       → {"type": "doc",     "file_path": "..."}
          "project:C:/path"                   → {"type": "project", "file_path": "..."}

    File paths are validated only for shape (non-empty when present); the
    Pascal side will surface NOT_LOADED / DOCUMENT_NOT_FOUND if the path
    doesn't resolve.
    """
    if scope is None:
        return Scope().model_dump(exclude_none=True)

    if isinstance(scope, Scope):
        return scope.model_dump(exclude_none=True)

    if isinstance(scope, dict):
        return Scope(**scope).model_dump(exclude_none=True)

    if isinstance(scope, str):
        if scope.startswith("doc:"):
            path = scope[4:]
            return Scope(type="doc", file_path=path).model_dump(exclude_none=True)
        if scope.startswith("project:"):
            path = scope[8:]
            return Scope(type="project", file_path=path).model_dump(exclude_none=True)
        if scope in ("active_doc", "project", "doc"):
            return Scope(type=scope).model_dump(exclude_none=True)
        # Unknown bare string — surface as-is so Pascal returns a clear
        # INVALID_PARAMETER rather than silently treating it as active_doc.
        return Scope(type="active_doc", file_path=None).model_dump(exclude_none=True)

    raise TypeError(f"Unsupported scope value: {scope!r}")
