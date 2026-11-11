# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The session's length unit, read and set: see lengths.py."""

from __future__ import annotations

from typing import Any, Optional


def register_length_units_tools(mcp) -> None:
    @mcp.tool()
    async def length_units(to: Optional[str] = None) -> dict[str, Any]:
        """Read, or set, the unit this session gives and reads lengths in.

        One rule on every tool: a length whose name has no unit (x,
        width, hole_size) is in this unit, in arguments and replies alike.
        A name with a unit always means it: thickness_mils is mils, and
        thickness_mm may be given instead. Replies rename such keys to
        this unit and say ``length_units``. One call can use the other
        unit by giving ``length_units`` beside its own arguments.

        The setting is this session's own; other sessions keep theirs. A
        new session starts in the unit chosen on the Config tab of the
        Altium panel (mil unless changed there).

        Args:
            to: "mil" or "mm" to set it; omit to read it.

        Returns:
            ``length_units`` (this session's) and ``default`` (the Config
            tab's, for new sessions).
        """
        from .. import lengths
        if to is not None:
            try:
                lengths.set_session_unit(to)
            except ValueError as e:
                return {"ok": False, "reason": str(e)}
        return {"ok": True, "length_units": lengths.session_unit(),
                "default": lengths.default_unit()}
