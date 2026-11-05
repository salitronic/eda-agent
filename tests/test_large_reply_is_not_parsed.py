# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The loop does not parse a whole reply after sending it.

Reading the reply's ``error`` for the status window went through the JSON
index, built character by character over the whole text. On a reply of
several hundred KB that held Altium's main thread for minutes after the
reply had gone out, and every session's calls queued behind it. The
error is now read only from a reply whose envelope says success:false,
from what the handler recorded, and the reply itself is parsed only when
it is small.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _after_the_reply_is_sent(body: str) -> str:
    return body[body.index("WriteResponseFile(RequestId, ResponseContent);"):]


def _unguarded_parses(tail: str) -> list[str]:
    """Whole-reply parses not nested under the failure and size checks."""
    out = []
    for m in re.finditer(r"(ExtractJsonValue|JsonIndexOf|JsonBuildIndex|"
                         r"JsonValueAnywhere)\(ResponseContent\b", tail):
        before = tail[:m.start()]
        failed = before.rfind("If Pos('\"success\":false', Copy(ResponseContent, 1, ")
        small = before.rfind("Length(ResponseContent) <= ")
        if failed < 0 or small < failed:
            out.append(tail[m.start():m.start() + 60])
    return out


def test_the_reply_is_parsed_only_when_it_failed_and_is_small():
    body = load(_SCRIPTS / "StatusForm.pas")["ProcessSingleRequest"]
    tail = _after_the_reply_is_sent(body)
    assert not _unguarded_parses(tail), _unguarded_parses(tail)
    assert "DashCode := LastErrorCode;" in tail


def test_the_guard_still_sees_a_whole_reply_parse():
    """Mutated: the line that froze Altium must still be caught."""
    old = ("WriteResponseFile(RequestId, ResponseContent);\n"
           "    DashErrPayload := ExtractJsonValue(ResponseContent, 'error');\n")
    assert _unguarded_parses(_after_the_reply_is_sent(old))
