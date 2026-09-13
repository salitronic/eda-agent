# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A press that did not take says what was tried, and what to try next.

app_press_dialog_button told callers that Altium "can reach a state
where the window pumps messages but acts on none of them, and no
synthetic press will clear it". Nothing measured that. A report has a
real pointer click clearing close-confirmation prompts that the posted
message did not, twelve times in twelve, though with a second Altium
process running during some of it. Either way the sentence closed a
door that is not known to be closed, so a caller stopped trying.
"""
from __future__ import annotations

import inspect

from eda_agent.tools import uiauto


def _press_tool_code() -> str:
    src = inspect.getsource(uiauto)
    body = src.split("async def app_press_dialog_button(", 1)[1]
    body = body.split("\n    async def ", 1)[0]
    body = body.split('"""', 2)[2]                # drop the docstring
    return "\n".join(line.split("#", 1)[0] for line in body.splitlines())


def test_the_unmeasured_dead_end_is_gone():
    assert "no synthetic press will clear it" not in _press_tool_code()


def test_a_press_that_did_not_take_names_the_next_route():
    code = _press_tool_code()
    tail = code.split("the press did not take", 1)
    assert len(tail) == 2, "the not-taken reason is gone"
    assert "app_invoke_element" in tail[1][:400], (
        "the failed press does not point at the UI Automation route")
