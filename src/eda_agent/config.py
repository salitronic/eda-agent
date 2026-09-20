# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Configuration management for EDA Agent MCP Server.

Single source of truth: ``mcp_config.json`` lives in the workspace directory
and is read by both Python and the Altium DelphiScript at startup. Python
also writes/updates the file when configuration changes so that the next
Altium script run picks up the new values.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("eda_agent.config")
from pydantic import BaseModel, Field
from eda_agent.atomicfile import replace_with_retry


# Pointer file that the DelphiScript reads to find the workspace dir.
# DelphiScript can't read environment variables, so Python writes the
# absolute path here and the script reads it. See scripts/altium/Main.pas:
# ResolveDefaultWorkspaceDir for the reader side.
#
# This path is MACHINE-GLOBAL and shared with any running Altium session:
# the script reads it once at startup, so whatever is written here decides
# where a live polling loop looks for work. Overriding it is what keeps a
# test run (or a second checkout) from redirecting somebody's live bridge.
WORKSPACE_POINTER_FILE = Path(r"C:\ProgramData\eda-agent\workspace-path.txt")

# Env var that redirects the pointer file itself. Set this to a scratch
# path to exercise the pointer-writing code without touching the real one.
WORKSPACE_POINTER_ENV = "EDA_AGENT_POINTER_FILE"


def workspace_pointer_file() -> Path:
    """The pointer file to write, honouring ``EDA_AGENT_POINTER_FILE``."""
    override = os.environ.get(WORKSPACE_POINTER_ENV)
    return Path(override) if override else WORKSPACE_POINTER_FILE


def _running_under_pytest() -> bool:
    return "pytest" in sys.modules or bool(
        os.environ.get("PYTEST_CURRENT_TEST")
    )

CONFIG_FILE_NAME = "mcp_config.json"


def _default_workspace_dir() -> Path:
    """Resolve the default workspace directory."""
    override = os.environ.get("EDA_AGENT_WORKSPACE")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "EDA Agent" / "workspace"
    if _under_wsl():
        # USERPROFILE is not set under WSL, so the Path.home() line below
        # would put the IPC directory on the distro's ext4 filesystem, which
        # Windows can only reach over the 9P share. GH #32 reports Altium
        # enumerating request files there and then failing to read their
        # contents, which surfaces as REQUEST_UNREADABLE while the polling
        # loop reports itself healthy. A drvfs path is NTFS on both sides and
        # sidesteps the question. Overridable by EDA_AGENT_WORKSPACE above.
        return Path("/mnt/c/ProgramData/eda-agent/workspace")
    return Path.home() / "EDA Agent" / "workspace"


def _pointer_encoding() -> str:
    """Codec for the pointer file, which DelphiScript reads a byte at a time.

    ``mbcs`` is the Windows ANSI codepage and is the right answer there, but
    the codec DOES NOT EXIST off Windows: under WSL it raises LookupError,
    which the caller's best-effort handler swallowed, so the pointer file was
    silently never written at all. That is the step GH #32 had to do by hand
    before anything worked, and it is invisible from the outside because
    nothing fails loudly.

    cp1252 is the stand-in from WSL. For an ASCII path, which covers the
    overwhelming majority, the bytes are identical to any Windows ANSI
    codepage, so the Pascal side reads the same string either way.
    """
    return "mbcs" if sys.platform == "win32" else "cp1252"


def _under_wsl() -> bool:
    """Is this a Linux kernel running under Windows?"""
    if sys.platform == "win32":
        return False
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(
            encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "microsoft" in release or "wsl" in release


def _windows_path_str(path: Path) -> str:
    """``path`` as the Windows process on the other side of the bridge sees it.

    On Windows this is just ``str``. Under WSL it is not: the pointer file is
    read by DelphiScript, which is a Windows process and cannot resolve
    ``/mnt/c/...`` or ``/home/...``. Writing ``str(path)`` there produced
    ``/mnt/c/ProgramData/eda-agent/workspace`` with a backslash glued on
    the end, which resolves to nothing on either side. Reported in GH #32, where it had to be corrected by hand and then
    made read-only, because every server start wrote it again.

    ``wslpath -w`` is a Linux binary, so it works even where Windows interop
    is unregistered and no .exe can run at all. Falls back to the plain
    string if it is missing, which leaves the previous behaviour rather than
    raising inside a best-effort write.
    """
    if not _under_wsl():
        return str(path)
    try:
        completed = subprocess.run(
            ["wslpath", "-w", str(path)],
            capture_output=True,
            timeout=10,
        )
        if completed.returncode == 0:
            translated = completed.stdout.decode(
                "utf-8", errors="ignore").strip()
            if translated:
                return translated
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("wslpath translation failed for %s: %s", path, e)
    return str(path)


def write_workspace_pointer(workspace_dir: Path) -> None:
    """Write the workspace path to the pointer file that DelphiScript reads.

    Encoding is the Windows ANSI codepage (``mbcs`` on Windows, cp1252 as
    its stand-in under WSL) because that is what
    DelphiScript's single-byte file read decodes -- ascii raised
    UnicodeEncodeError for an accented user-profile path (and that error
    escaped the OSError guard), while utf-8 would mojibake the same path
    on the Pascal side.
    """
    target = workspace_pointer_file()

    # Never let a test run repoint a live bridge. The pointer is
    # machine-global and the Altium script reads it once at startup, so a
    # test that writes its tmp workspace here silently redirects a running
    # polling loop at a throwaway directory: the script stays healthy and
    # reports zero requests while every real call times out, which is a
    # genuinely baffling failure to debug from the outside. Tests that
    # need to exercise this function set EDA_AGENT_POINTER_FILE to their
    # own scratch path, which is honoured normally.
    if target == WORKSPACE_POINTER_FILE and _running_under_pytest():
        logger.debug(
            "skipping machine-global workspace pointer write under pytest; "
            "set %s to exercise it against a scratch path",
            WORKSPACE_POINTER_ENV,
        )
        return

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        path_str = _windows_path_str(workspace_dir)
        if not path_str.endswith("\\"):
            path_str += "\\"
        target.write_text(path_str, encoding=_pointer_encoding())
    except (OSError, PermissionError, UnicodeEncodeError, LookupError):
        # LookupError: "mbcs" only exists on Windows; the pointer file is
        # meaningless elsewhere anyway.
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
    # Idle poll cadence. Worst-case request-pickup latency when the loop
    # has gone idle. 30 ms keeps the dashboard feeling responsive without
    # meaningfully loading Altium (one FindFiles glob per tick).
    poll_interval_idle_ms: int = 30
    # Empty-poll count before dropping to the idle cadence. At 10 ms active
    # this keeps the loop in fast mode for ~1.5 s after the last request,
    # so a burst of dashboard calls all get 10 ms pickup.
    idle_threshold: int = 150
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
        try:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(
                f"cannot create the IPC workspace at {self.workspace_dir} "
                f"({exc}); every Altium tool call depends on this directory. "
                f"Set EDA_AGENT_WORKSPACE to a writable location."
            ) from exc
        write_workspace_pointer(self.workspace_dir)
        self.write_runtime_config()
        self._write_schemas()

    def _write_schemas(self) -> None:
        """Export Pydantic schemas as JSON Schema files in the workspace.

        Importing eda_agent.schemas.commands populates the command registry
        as a side effect, register_command calls run at module import time.
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
        Failures are non-fatal; Pascal falls back to InitDefaultConfig.
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
            replace_with_retry(tmp, target)
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
