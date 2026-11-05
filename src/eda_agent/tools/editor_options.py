# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium editor preferences by readable name.

The board and schematic option tools share this: each maps its argument
names to Altium's preference names, sends the ones given as one ``set``
string and asks for all of them back, then renames Altium's answer to the
argument names. Altium reads every written option back; the reply lists
any that read otherwise in ``not_applied``.
"""
from __future__ import annotations

from typing import Any

from ..bridge import get_bridge


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


async def run_options(command: str, names: dict[str, str],
                      values: dict[str, Any],
                      extra: dict[str, str] | None = None) -> dict[str, Any]:
    """Read every option in ``names`` and write the ``values`` given.

    ``names`` maps an argument name to Altium's; ``values`` holds the
    arguments, None meaning "leave as it is". ``extra`` is sent as it is
    (the board output tools name their group there).
    """
    pairs = []
    for arg, value in values.items():
        if value is None:
            continue
        text = _text(value)
        if "|" in text or "\n" in text:
            return {"ok": False, "reason": f"{arg} cannot hold '|' or a line break"}
        pairs.append(f"{names[arg]}={text}")
    params = dict(extra or {})
    params["names"] = ",".join(names.values())
    if pairs:
        params["set"] = "|".join(pairs)
    reply = await get_bridge().send_command_async(command, params)
    if isinstance(reply, dict) and isinstance(reply.get("options"), dict):
        back = {altium: arg for arg, altium in names.items()}
        reply["options"] = {back.get(k, k): v for k, v in reply["options"].items()}
        for key in ("not_applied", "refused"):
            if isinstance(reply.get(key), list):
                reply[key] = [back.get(k, k) for k in reply[key]]
    return reply
