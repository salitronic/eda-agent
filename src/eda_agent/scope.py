# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
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

from pathlib import PureWindowsPath
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


ScopeType = Literal["active_doc", "project", "doc", "lib_component"]


class Scope(BaseModel):
    """Structured scope. Always serialises to the wire form.

    For ``type == "lib_component"`` the ``file_path`` slot carries the
    component's lib-ref name (not a path) -- it reuses the generic string
    slot the Pascal ParseScope already reads.
    """

    type: ScopeType = "active_doc"
    file_path: Optional[str] = Field(default=None)


ScopeInput = Union[Scope, dict, str, None]


# A bare path is taken as the document or project it names, by extension.
_PROJECT_SUFFIXES = (".prjpcb", ".prjhar", ".prjmbd", ".prjscr", ".libpkg")

ACCEPTED = ('"active_doc", "project", "doc:PATH", "project:PATH", '
            '"lib_component:NAME", or the path of a document or project')


class ScopeError(ValueError):
    """A scope string that names no scope. Nothing is sent for it."""


def to_compact(scope: Optional[str]) -> str:
    """The compact string form ("doc:PATH", "active_doc", ...).

    A bare path becomes "doc:PATH", or "project:PATH" for a project
    file. Anything else that is not a scope raises ScopeError, so it
    can never be read as the document in front.
    """
    text = (scope or "").strip()
    if not text:
        return "active_doc"
    if text in ("active_doc", "project", "doc"):
        return text
    if text.startswith(("doc:", "project:", "lib_component:")):
        return text
    suffix = PureWindowsPath(text).suffix.lower()
    if suffix and ("\\" in text or "/" in text or _looks_like_drive(text)):
        kind = "project" if suffix in _PROJECT_SUFFIXES else "doc"
        return f"{kind}:{text}"
    raise ScopeError(f"Not a scope: {scope!r}. Use {ACCEPTED}.")


def _looks_like_drive(text: str) -> bool:
    """A drive-letter path, such as C:/Designs/Board.SchDoc."""
    return (len(text) > 2 and text[0].isalpha() and text[1] == ":"
            and text[2] in "/\\")


def refusal(exc: ScopeError, **extra) -> dict:
    """The reply for a scope that names nothing; nothing was sent."""
    return {"ok": False, "success": False, "error": "INVALID_SCOPE",
            "reason": str(exc), **extra}


def to_wire(scope: ScopeInput) -> dict:
    """Normalise any caller-supplied scope to the wire form.

    Accepts None, a Scope, a dict, or a string as ``to_compact`` reads
    it. A string that is no scope raises ScopeError.
    """
    if scope is None:
        return Scope().model_dump(exclude_none=True)

    if isinstance(scope, Scope):
        return scope.model_dump(exclude_none=True)

    if isinstance(scope, dict):
        return Scope(**scope).model_dump(exclude_none=True)

    if isinstance(scope, str):
        text = to_compact(scope)
        for prefix, kind in (("doc:", "doc"), ("project:", "project"),
                             ("lib_component:", "lib_component")):
            if text.startswith(prefix):
                return Scope(type=kind, file_path=text[len(prefix):]
                             ).model_dump(exclude_none=True)
        return Scope(type=text).model_dump(exclude_none=True)

    raise TypeError(f"Unsupported scope value: {scope!r}")
