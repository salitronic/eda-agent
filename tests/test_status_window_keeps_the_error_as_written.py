# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The status window shows an error message as the handler wrote it.

The window read the message back out of the JSON reply, and the
unescaper turns every character above 255 into '?', so a Chinese path
showed as question marks while Python received it whole (issue #91).
Removing that guard is not safe: Chr above 255 truncates in this script
host. The message is kept as written when the reply is built, and the
window uses it when the reply it is showing is that error.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_ALT = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def test_the_builder_keeps_the_message_as_written():
    body = load(_ALT / "Main.pas")["BuildErrorResponseDetailed"]
    assert "LastErrorId := RequestId;" in body
    assert "LastErrorCode := ErrorCode;" in body
    assert "LastErrorMsg := ErrorMsg;" in body


def test_the_window_uses_it_only_for_its_own_reply():
    body = load(_ALT / "StatusForm.pas", minimum=20)["ProcessSingleRequest"]
    reset = body.index("LastErrorId := '';")
    dispatch = body.index("ProcessCommand(Command, Params, RequestId)")
    assert reset < dispatch, "a previous request's message could be shown"
    use = body.index("DashDetail := LastErrorMsg;")
    guard = body.rindex("If (LastErrorId = RequestId) And (LastErrorCode <> '')", 0, use)
    # Only when this reply carries the code the handler recorded.
    assert "Pos('\"error\":{\"code\":\"' + LastErrorCode + '\"'," in body[guard:use]


def test_the_wire_reply_is_unchanged():
    """Python already receives the message whole; only the window was
    wrong, so the JSON escape stays as it was."""
    body = load(_ALT / "Main.pas")["BuildErrorResponseDetailed"]
    assert "'\\u'" in body
