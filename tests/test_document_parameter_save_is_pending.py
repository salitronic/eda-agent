# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A handler that only marks a document may not reply that it saved it.


project.set_document_parameter wrote the sheet parameter, called
MarkDocDirtyByPath, and replied "saved":true; proj_set_document_parameter
told the caller the write had gone to disk and no app_save_all was needed.
MarkDocDirtyByPath sets the modified flag and nothing more (see
test_deferred_save_is_not_claimed_as_a_save). Measured on AD 25.8.1: an
added parameter, an updated one and the sheet's own Title were all gone
after the project was closed without a save, while every reply said saved.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests import pascal_source

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts" / "altium"
TOOLS = REPO / "src" / "eda_agent" / "tools"

# Calls that really write a document. A handler that makes one may report
# what it wrote; one that only marks may not say "saved":true.
_WRITES = re.compile(r"DoFileSave|SaveProjectFile|SaveOneDocByDocRef|"
                     r"WorkspaceManager:SaveObject|DoSafeChangeFileNameAndSave")


def _handlers():
    for path in sorted(SCRIPTS.glob("*.pas")):
        if path.name == "Altium_MCP.pas":         # generated bundle
            continue
        for name, body in pascal_source.load(path, minimum=1).items():
            yield path.name, name, body


def test_no_marking_handler_replies_saved_true():
    offenders = [f"{unit}:{name}" for unit, name, body in _handlers()
                 if "MarkDocDirtyByPath" in body
                 and '"saved":true' in body
                 and not _WRITES.search(body)]
    assert not offenders, (
        f"{offenders} only mark the document modified and reply "
        f'"saved":true; nothing was written to disk')


def test_set_document_parameter_says_the_save_is_pending():
    body = pascal_source.load(SCRIPTS / "Project.pas")[
        "Proj_SetDocumentParameter"]
    assert "MarkDocDirtyByPath" in body
    assert '"saved":false' in body
    assert '"pending_save":true' in body


def test_the_tool_does_not_promise_a_disk_write():
    text = (TOOLS / "project.py").read_text(encoding="utf-8")
    start = text.index("async def proj_set_document_parameter(")
    doc = text[start:text.index('"""', text.index('"""', start) + 3)]
    claim = re.search(r"(goes to disk|no `?app_save_all`? is needed|"
                      r"disk write already happened)", doc, re.I)
    assert claim is None, (
        f"proj_set_document_parameter claims {claim.group(0)!r}; the sheet "
        f"is only marked modified")
    assert re.search(r"\bmarked\b", doc), (
        "the docstring should say the sheet is marked, and what writes it")
