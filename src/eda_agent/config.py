# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Configuration management for EDA Agent MCP Server."""

import os
from pathlib import Path
from pydantic import BaseModel, Field


# Pointer file that the DelphiScript reads to find the workspace dir.
# DelphiScript can't read environment variables, so Python writes the
# absolute path here and the script reads it. See scripts/altium/Main.pas:
# ResolveDefaultWorkspaceDir for the reader side.
WORKSPACE_POINTER_FILE = Path(r"C:\ProgramData\eda-agent\workspace-path.txt")


def _default_workspace_dir() -> Path:
    """Resolve the default workspace directory.

    Uses %USERPROFILE%\\EDA Agent\\workspace on Windows so both the Python
    MCP server and the Altium DelphiScript use the same location — and
    it sits alongside the installed scripts in a single visible folder.
    Can be overridden via the EDA_AGENT_WORKSPACE env var.
    """
    override = os.environ.get("EDA_AGENT_WORKSPACE")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "EDA Agent" / "workspace"
    return Path.home() / "EDA Agent" / "workspace"


def write_workspace_pointer(workspace_dir: Path) -> None:
    """Write the workspace path to the pointer file that DelphiScript reads.

    The DelphiScript side has no access to environment variables, so we
    persist the resolved absolute path to a fixed location that both
    sides agree on: C:\\ProgramData\\eda-agent\\workspace-path.txt.

    Failures are non-fatal — DelphiScript falls back to C:\\EDA Agent\\
    workspace\\ if the pointer is missing.
    """
    try:
        WORKSPACE_POINTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Write with a trailing backslash so the DelphiScript side can
        # concatenate file names directly.
        path_str = str(workspace_dir)
        if not path_str.endswith("\\"):
            path_str += "\\"
        WORKSPACE_POINTER_FILE.write_text(path_str, encoding="ascii")
    except (OSError, PermissionError):
        # Not fatal — DelphiScript has a hardcoded fallback.
        pass


class AltiumConfig(BaseModel):
    """Configuration settings for EDA Agent MCP Server."""

    # Workspace directory for JSON communication files
    workspace_dir: Path = Field(default_factory=_default_workspace_dir)

    # Request/response file names
    request_file: str = "request.json"
    response_file: str = "response.json"

    # Polling settings
    poll_interval: float = 0.01  # 10ms between polls — matches server-side active poll
    # Default timeout: generous to survive large-board operations
    # (iterating 6000+ tracks, compiling a 500+ component project, etc).
    # Individual tool calls can override per-invocation when they know
    # they're cheap.
    poll_timeout: float = 10.0  # max wait for response

    # Altium process name
    altium_process_name: str = "X2.exe"

    # Coordinate units (mils by default)
    default_units: str = "mils"

    @property
    def request_path(self) -> Path:
        """Full path to request.json."""
        return self.workspace_dir / self.request_file

    @property
    def response_path(self) -> Path:
        """Full path to response.json."""
        return self.workspace_dir / self.response_file

    def ensure_workspace(self) -> None:
        """Ensure workspace directory exists, and publish its path to the
        pointer file that the DelphiScript side reads."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        write_workspace_pointer(self.workspace_dir)


# Global configuration instance
config = AltiumConfig()


def get_config() -> AltiumConfig:
    """Get the global configuration instance."""
    return config


def configure(**kwargs) -> None:
    """Update configuration settings.

    Also resets the bridge singleton so it picks up the new config.
    """
    global config
    config = AltiumConfig(**{**config.model_dump(), **kwargs})

    # Reset bridge singleton so it re-reads the new config on next access
    from .bridge.altium_bridge import reset_bridge
    reset_bridge()
