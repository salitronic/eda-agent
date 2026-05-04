# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Per-command schema registry.

Each MCP tool can register a Pydantic model describing its request params
and (optionally) its response data. The registry exposes those schemas as
JSON Schema files in the workspace so the Pascal side can validate
incoming requests against the same shape Python serialises — single source
of truth, no drift.
"""

import json
from pathlib import Path
from typing import Optional, Type

from pydantic import BaseModel


class CommandSpec:
    """One command's schema bundle."""

    def __init__(
        self,
        command: str,
        params_model: Optional[Type[BaseModel]] = None,
        response_model: Optional[Type[BaseModel]] = None,
        description: str = "",
    ):
        self.command = command
        self.params_model = params_model
        self.response_model = response_model
        self.description = description

    def params_schema(self) -> Optional[dict]:
        if self.params_model is None:
            return None
        return self.params_model.model_json_schema()

    def response_schema(self) -> Optional[dict]:
        if self.response_model is None:
            return None
        return self.response_model.model_json_schema()


_registry: dict[str, CommandSpec] = {}


def register_command(
    command: str,
    *,
    params_model: Optional[Type[BaseModel]] = None,
    response_model: Optional[Type[BaseModel]] = None,
    description: str = "",
) -> CommandSpec:
    """Register a command with its request/response models.

    Calling twice for the same command name updates the registration —
    handy during development without server restart.
    """
    spec = CommandSpec(
        command=command,
        params_model=params_model,
        response_model=response_model,
        description=description,
    )
    _registry[command] = spec
    return spec


def get_command_spec(command: str) -> Optional[CommandSpec]:
    return _registry.get(command)


def list_commands() -> list[str]:
    return sorted(_registry.keys())


def write_schemas_to(workspace_dir: Path) -> Path:
    """Write all registered command schemas to ``mcp_schemas/`` in the workspace.

    Layout::

        mcp_schemas/
            envelope.json          # IPC envelope shape
            scope.json             # structured scope shape
            commands/
                <command>.json     # per-command params + response

    The Pascal side reads ``envelope.json`` at startup so the dispatcher
    can validate the wire envelope itself; per-command schemas are
    consulted by handlers that opt into validation.
    """
    from .envelope import IPCRequest, IPCResponse
    from .scope import ScopeSchema

    schemas_dir = workspace_dir / "mcp_schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    (schemas_dir / "commands").mkdir(parents=True, exist_ok=True)

    _write_json(schemas_dir / "envelope.json", {
        "request": IPCRequest.model_json_schema(),
        "response": IPCResponse.model_json_schema(),
    })

    _write_json(schemas_dir / "scope.json", ScopeSchema.model_json_schema())

    for cmd, spec in _registry.items():
        bundle = {"command": cmd, "description": spec.description}
        params = spec.params_schema()
        if params is not None:
            bundle["params"] = params
        response = spec.response_schema()
        if response is not None:
            bundle["response"] = response
        # Filename-safe form: replace dot with underscore
        safe = cmd.replace(".", "_")
        _write_json(schemas_dir / "commands" / f"{safe}.json", bundle)

    _write_json(schemas_dir / "index.json", {
        "commands": sorted(_registry.keys()),
    })

    return schemas_dir


def _write_json(path: Path, payload) -> None:
    """Idempotent JSON write — only rewrite when content actually changes."""
    serialised = json.dumps(payload, indent=2, sort_keys=True)
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
            if existing == serialised:
                return
        except (OSError, UnicodeDecodeError):
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(serialised, encoding="utf-8")
    tmp.replace(path)
