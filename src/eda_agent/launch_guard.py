# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Who started this server: the AI client, or a script in its shell.

The server serves the AI client that starts it. A script the AI runs in
its shell can start a server of its own as an MCP client and call the
tools in a loop. Each such script is then a separate server on the one
Altium, unseen by the session and the user: its calls show in no
transcript, and several running at once write the same board. That server
is refused; the tools are called through the session's own connection.

A script is told from a client by where it runs. Claude Code gives the
commands of its shell variables it does not give the servers it starts
(SHELL_MARKERS). The server is refused when the process that started it
is not the client itself and that process, or this one, carries one.
"""
from __future__ import annotations

import os
from pathlib import PureWindowsPath
from typing import Any, Optional

#: Set for a command run in Claude Code's shell, never for its MCP servers.
SHELL_MARKERS = ("CLAUDE_PID", "CLAUDE_CODE_CHILD_SESSION")

_OWN_MODULES = ("eda_agent", "eda_agent.server", "eda_agent.devproxy")


def _is_own(name: str, cmdline: list[str]) -> bool:
    """One of eda-agent's own processes: its launcher or its reload proxy."""
    name = name.lower()
    if name.startswith("eda-agent"):
        return True
    if not name.startswith("python"):
        return False
    for i, arg in enumerate(cmdline):
        if PureWindowsPath(arg).name.lower().startswith("eda-agent"):
            return True
        if arg == "-m" and i + 1 < len(cmdline) and cmdline[i + 1] in _OWN_MODULES:
            return True
    return False


def _is_client(name: str, cmdline: list[str]) -> bool:
    name = name.lower()
    return name in ("claude.exe", "claude") or (
        name.startswith("node") and any("claude" in part.lower()
                                        for part in cmdline[:3]))


def _marked(env) -> bool:
    return any(str(env.get(key, "")).strip() for key in SHELL_MARKERS)


def script_launcher() -> Optional[dict[str, Any]]:
    """The script that started this server, or None when a client did (or
    nothing says otherwise). ``pid``, ``name`` and ``command``."""
    try:
        import psutil
        launcher = None
        for parent in psutil.Process().parents():
            name, cmdline = parent.name(), list(parent.cmdline())
            if _is_own(name, cmdline):
                continue
            launcher = (parent, name, cmdline)
            break
        if launcher is None:
            return None
        parent, name, cmdline = launcher
        if _is_client(name, cmdline):
            return None
        marked = _marked(os.environ)
        if not marked:
            try:
                marked = _marked(parent.environ())
            except Exception:  # noqa: BLE001 - not readable: nothing says
                marked = False
        if not marked:
            return None
        return {"pid": parent.pid, "name": name,
                "command": " ".join(cmdline)[:200]}
    except Exception:  # noqa: BLE001 - never stop a server on a failed look
        return None


def refusal(launcher: dict[str, Any]) -> str:
    return (
        "eda-agent does not serve a script. It was started by "
        f"{launcher['name']} (pid {launcher['pid']}: {launcher['command']}) "
        "running in an AI session's shell. Call the tools through the "
        "session's own eda-agent connection instead: nearly every tool "
        "takes items, a list of up to 200 argument sets, so many objects "
        "are one call.")
