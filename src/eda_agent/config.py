# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Configuration management for EDA Agent MCP Server.

Single source of truth: ``mcp_config.json`` lives in the workspace directory
and is read by both Python and the Altium DelphiScript at startup. Python
also writes/updates the file when configuration changes so that the next
Altium script run picks up the new values.
"""

import json
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


# Pointer file that the DelphiScript reads to find the workspace dir.
# DelphiScript can't read environment variables, so Python writes the
# absolute path here and the script reads it. See scripts/altium/Main.pas:
# ResolveDefaultWorkspaceDir for the reader side.
WORKSPACE_POINTER_FILE = Path(r"C:\ProgramData\eda-agent\workspace-path.txt")

CONFIG_FILE_NAME = "mcp_config.json"


def _default_workspace_dir() -> Path:
    """Resolve the default workspace directory."""
    override = os.environ.get("EDA_AGENT_WORKSPACE")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "EDA Agent" / "workspace"
    return Path.home() / "EDA Agent" / "workspace"


def write_workspace_pointer(workspace_dir: Path) -> None:
    """Write the workspace path to the pointer file that DelphiScript reads."""
    try:
        WORKSPACE_POINTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        path_str = str(workspace_dir)
        if not path_str.endswith("\\"):
            path_str += "\\"
        WORKSPACE_POINTER_FILE.write_text(path_str, encoding="ascii")
    except (OSError, PermissionError):
        pass


class MCPRuntimeConfig(BaseModel):
    """Polling and IPC tunables shared between Python and Pascal.

    The same dict is serialised to ``mcp_config.json`` in the workspace,
    where the Pascal side reads it via LoadMCPConfig. Both sides are
    expected to honour these values; defaults match Pascal's
    InitDefaultConfig so a missing file leaves both sides in sync.
    """

    # Pascal-side polling tunables (milliseconds)
    poll_interval_active_ms: int = 10
    poll_interval_idle_ms: int = 100
    idle_threshold: int = 20
    auto_shutdown_ms: int = 600_000  # 10 min
    yield_iterations: int = 5
    yield_every_n_active: int = 5

    # Python-side polling
    py_poll_interval_seconds: float = 0.01
    py_poll_timeout_seconds: float = 10.0
    py_keepalive_interval_seconds: int = 30


class AltiumConfig(BaseModel):
    """Top-level configuration."""

    workspace_dir: Path = Field(default_factory=_default_workspace_dir)
    altium_process_name: str = "X2.exe"
    default_units: str = "mils"
    runtime: MCPRuntimeConfig = Field(default_factory=MCPRuntimeConfig)

    @property
    def config_file_path(self) -> Path:
        return self.workspace_dir / CONFIG_FILE_NAME

    @property
    def poll_interval(self) -> float:
        return self.runtime.py_poll_interval_seconds

    @property
    def poll_timeout(self) -> float:
        return self.runtime.py_poll_timeout_seconds

    def ensure_workspace(self) -> None:
        """Create workspace dir, publish pointer file, persist runtime config,
        and emit JSON schemas for the Pascal side to validate against."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        write_workspace_pointer(self.workspace_dir)
        self.write_runtime_config()
        self._write_schemas()

    def _write_schemas(self) -> None:
        """Export Pydantic schemas as JSON Schema files in the workspace.

        Importing eda_agent.schemas.commands populates the command registry
        as a side effect — register_command calls run at module import time.
        Failures are non-fatal: Pascal falls back to envelope-only validation
        if the schema files are missing.
        """
        try:
            from .schemas import write_schemas_to
            from .schemas import commands  # noqa: F401  (registers commands)
            write_schemas_to(self.workspace_dir)
        except Exception:
            # Schema export must not break MCP server startup.
            pass

    def write_runtime_config(self) -> None:
        """Write ``mcp_config.json`` so the Pascal side reads the same values.

        Idempotent: if the file already matches what we'd write, no-ops.
        Failures are non-fatal — Pascal falls back to InitDefaultConfig.
        """
        try:
            payload = self.runtime.model_dump()
            target = self.config_file_path
            if target.exists():
                try:
                    existing = json.loads(target.read_text(encoding="utf-8"))
                    if existing == payload:
                        return
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    pass
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(target)
        except (OSError, PermissionError):
            pass

    def reload_runtime_config(self) -> None:
        """Re-read ``mcp_config.json`` from disk into ``self.runtime``.

        Used when a long-running process wants to pick up edits made to the
        config file by an external tool (or by a fresh ensure_workspace).
        """
        path = self.config_file_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.runtime = MCPRuntimeConfig(**data)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
            pass


config = AltiumConfig()


def get_config() -> AltiumConfig:
    return config


def configure(**kwargs) -> None:
    """Update top-level configuration.

    ``runtime`` may be passed as either a MCPRuntimeConfig or a dict.
    Resets the bridge singleton so it picks up the new config.
    """
    global config
    base = config.model_dump()
    base.update(kwargs)
    config = AltiumConfig(**base)
    from .bridge.altium_bridge import reset_bridge
    reset_bridge()
