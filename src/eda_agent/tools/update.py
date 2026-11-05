# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""server_update: check for and apply an eda-agent update, with consent."""
from __future__ import annotations

import asyncio
from typing import Any

from .. import updates


def register_update_tools(mcp) -> None:
    @mcp.tool()
    async def server_update(apply: bool = False, relaunch: bool = True) -> dict[str, Any]:
        """Check the eda-agent repository for a newer version, or install it.

        ASK THE USER FIRST. Call with apply=True only after the user has
        agreed in this conversation to update; describe what is new
        (``new_commits``) when you ask. app_ping and app_context report
        ``update`` when the daily check found one.

        With apply=False (default) it fetches and reports what an update
        would bring, and changes nothing. With apply=True it fast-forwards
        the clone, reinstalls when the dependencies changed, copies the
        Altium scripts again when they changed and, with ``relaunch``,
        restarts the Altium loop so it compiles them (other sessions' calls
        wait through the restart). A clone with uncommitted changes or
        commits of its own is refused and left untouched.

        Args:
            apply: Install the update. Only after the user says yes.
            relaunch: Restart the Altium loop when the scripts changed.

        Returns:
            Check: ``update_available``, ``commits_behind`` (as ``behind``),
            ``new_commits``, ``local_changes``, ``ahead``. Apply: ``updated``,
            ``from``, ``to``, ``commits``, ``steps`` (each ok and its output)
            and ``next_step``: reconnect the MCP server to run the new
            Python.
        """
        if apply:
            return await asyncio.to_thread(updates.apply, relaunch)
        return await asyncio.to_thread(updates.check, True)
