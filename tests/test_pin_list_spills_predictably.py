# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A pin list too big for the reply must spill the same way everywhere.

A 699-pin module returns more than a conversation can hold.  Left to
overflow, WHICH HALF SURVIVES IS DECIDED BY THE CLIENT: one writes the
payload to a file, another truncates it, a third drops the call.  The
answer then depends on the environment rather than on the symbol.

Reported in GH #11 on 2026-09-22 by a user whose client happened to spill
to a file, and who found that the better artefact: the full pin set can be
diffed against the datasheet pin table by script, field by field, without
any of it passing through the conversation.  This makes that outcome
deliberate instead of incidental.
"""

from __future__ import annotations

import json
from pathlib import Path

from eda_agent.tools.library import _spill_pin_list


def _pins(n, parts=4):
    return [
        {"designator": str(i), "name": f"P{i}",
         "electrical_type": "input" if i % 2 else "passive",
         "owner_part_id": (i % parts) + 1}
        for i in range(n)
    ]


def test_the_full_array_reaches_the_file(tmp_path):
    """Nothing is summarised away: every pin is on disk."""
    pins = _pins(699)
    out = _spill_pin_list({"component": "BIG", "pins": pins}, pins, "BIG",
                          str(tmp_path / "pins.json"))
    written = json.loads(Path(out["pins_path"]).read_text(encoding="utf-8"))
    assert len(written) == 699
    assert written == pins, "the file must be the pins, not a digest of them"


def test_the_payload_no_longer_carries_the_array(tmp_path):
    """Spilling is pointless if the array is returned anyway."""
    pins = _pins(699)
    out = _spill_pin_list({"component": "BIG", "pins": pins}, pins, "BIG",
                          str(tmp_path / "pins.json"))
    assert "pins" not in out, (
        "the whole point is to keep the array out of the reply")
    assert out["summary"]["pins"] == 699


def test_the_summary_carries_the_two_useful_distributions(tmp_path):
    """Pins per part and electrical type are the checks that catch a
    symbol built from a mis-transcribed datasheet table."""
    pins = _pins(20, parts=4)
    out = _spill_pin_list({"component": "X", "pins": pins}, pins, "X",
                          str(tmp_path / "p.json"))
    assert sum(out["summary"]["per_part"].values()) == 20
    assert sum(out["summary"]["per_electrical_type"].values()) == 20
    assert set(out["summary"]["per_part"]) == {"1", "2", "3", "4"}


def test_an_unwritable_path_keeps_the_data(tmp_path):
    """A failed write must not lose the pins.

    Returning them inline is worse than a file and far better than
    silently returning nothing, and the reason has to travel with it.
    """
    pins = _pins(10)
    target = tmp_path / "nodir"
    target.write_text("i am a file, not a directory", encoding="utf-8")
    out = _spill_pin_list({"component": "X", "pins": pins}, pins, "X",
                          str(target / "sub" / "p.json"))
    assert "spill_error" in out, "a failed write must say so"
    assert out.get("pins") == pins, "and must not discard the pins"
