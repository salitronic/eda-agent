# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
r"""CLI subcommands for eda-agent.

Exposed via the `eda-agent` console script. The default subcommand
(invoked when no arguments are given) is `serve`, which starts the
MCP server on stdio -- this is what MCP-compatible clients call.

Other subcommands:
  - scripts-path      : print the path to the bundled DelphiScript sources
  - install-scripts   : copy bundled scripts to a user-chosen directory
                        (default: %USERPROFILE%\EDA Agent\scripts\)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------
# Package-resource helpers
# --------------------------------------------------------------------------

def get_package_root() -> Path:
    """Return the filesystem path to the installed `eda_agent` package."""
    import eda_agent
    return Path(eda_agent.__file__).resolve().parent


def get_bundled_scripts_path() -> Path:
    """Return the path to the bundled DelphiScript sources.

    In a regular (wheel) install this is `eda_agent/scripts/` inside
    the installed package -- populated by `force-include` at wheel build
    time (see pyproject.toml).

    In an editable install (`pip install -e .`), the `force-include`
    mapping is not honoured; the loader falls back to `<repo>/scripts/altium/`
    by walking up from the package's source location.
    """
    bundled = get_package_root() / "scripts"
    if bundled.exists() and (bundled / "Altium_API.PrjScr").exists():
        return bundled

    # Editable-install fallback.
    pkg_parent = get_package_root().parent
    repo_root = pkg_parent.parent
    dev_scripts = repo_root / "scripts" / "altium"
    if dev_scripts.exists() and (dev_scripts / "Altium_API.PrjScr").exists():
        return dev_scripts

    return bundled


# --------------------------------------------------------------------------
# User-profile paths
# --------------------------------------------------------------------------

def get_default_scripts_dest() -> Path:
    """Default destination for `install-scripts`: %USERPROFILE%\\EDA Agent\\scripts\\."""
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "EDA Agent" / "scripts"
    return Path.home() / "EDA Agent" / "scripts"


# --------------------------------------------------------------------------
# scripts-path
# --------------------------------------------------------------------------

def cmd_scripts_path() -> int:
    """Print the path to the bundled DelphiScript sources."""
    path = get_bundled_scripts_path()
    if not path.exists():
        print(f"ERROR: bundled scripts directory not found: {path}", file=sys.stderr)
        print("Try reinstalling: pip install --force-reinstall eda-agent", file=sys.stderr)
        return 1
    print(str(path))
    return 0


# --------------------------------------------------------------------------
# install-scripts
# --------------------------------------------------------------------------

def cmd_install_scripts(dest: Optional[str] = None, force: bool = False) -> int:
    """Copy the bundled script project to a user-chosen directory.

    Default destination: %USERPROFILE%\\EDA Agent\\scripts\\
    Override with --dest PATH.

    After the copy, the user opens `Altium_API.PrjScr` in Altium
    (File > Open, or via the Scripts panel) and runs `StartMCPServer`
    from `Dispatcher.pas`.
    """
    src = get_bundled_scripts_path()
    dst = Path(dest).expanduser().resolve() if dest else get_default_scripts_dest()

    if not src.exists():
        print(f"ERROR: bundled scripts directory not found: {src}", file=sys.stderr)
        return 1

    if dst.exists() and any(dst.iterdir()) and not force:
        if not sys.stdin.isatty():
            print(
                f"ERROR: target directory already exists and is not empty:\n"
                f"  {dst}\n"
                f"Re-run with --force to overwrite.",
                file=sys.stderr,
            )
            return 1
        print(f"Target directory already exists and contains files:")
        print(f"  {dst}")
        try:
            reply = input("Overwrite existing files? [y/N] ").strip().lower()
        except EOFError:
            print("\nAborted (no input available -- re-run with --force).", file=sys.stderr)
            return 1
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    dst.mkdir(parents=True, exist_ok=True)

    # Only ship the files Altium needs: .pas sources, DFM form files, and
    # the project file. Without .dfm the DFM-backed StatusForm dashboard
    # fails to compile and StartMCPServer crashes with "unknown identifier"
    # errors referencing the form's controls.
    allowed_suffixes = {".pas", ".dfm", ".PrjScr"}

    copied = 0
    for child in src.iterdir():
        if not child.is_file():
            continue
        if ".tmp." in child.name:
            continue
        if child.suffix not in allowed_suffixes:
            continue
        shutil.copy2(child, dst / child.name)
        copied += 1

    # Publish the workspace path for DelphiScript to pick up, even before
    # the MCP server has ever run. Workspace lives alongside scripts: if
    # scripts are at <dest>\, workspace is at <dest's parent>\workspace\.
    from .config import write_workspace_pointer
    workspace_dir = dst.parent / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    write_workspace_pointer(workspace_dir)

    prjscr = dst / "Altium_API.PrjScr"
    print(f"Copied {copied} files to: {dst}")
    print(f"Workspace:     {workspace_dir}")
    print()
    print("One-time setup in Altium Designer:")
    print("  DXP -> Preferences -> Scripting System -> Global Projects")
    print(f"  Install from file -> select:")
    print(f"  {prjscr}")
    print()
    print("Each Altium session, start the polling loop via:")
    print("  File -> Run Script... -> Altium_API -> Dispatcher.pas -> StartMCPServer -> Run")
    return 0
