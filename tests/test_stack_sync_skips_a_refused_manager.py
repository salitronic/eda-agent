# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A stack write's follow-up sync does not undo the write's success.

The tools that change the board's stack (pcb_set_dielectric and others)
bring the Layer Stack Manager's copy in step afterwards. Below AD 22 the
script refuses the manager with UNSUPPORTED_VERSION; that refusal used to
propagate, so a write that had landed was reported as a failure.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.bridge.exceptions import AltiumCommandError, raise_for_code
from eda_agent.tools import pcb as pcb_tools


class _Bridge:
    def __init__(self, code):
        self.code = code

    async def send_command_async(self, command, params=None, timeout=None):
        raise_for_code(self.code, "refused here")


def test_a_refused_manager_skips_the_sync():
    out = asyncio.run(pcb_tools.sync_stack_manager(_Bridge("UNSUPPORTED_VERSION")))
    assert out["skipped"] is True and "refused here" in out["reason"]


def test_any_other_failure_still_raises():
    with pytest.raises(AltiumCommandError):
        asyncio.run(pcb_tools.sync_stack_manager(_Bridge("NO_PCB")))
