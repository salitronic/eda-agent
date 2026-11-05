# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The workbench page in every language: each piece of text it shows is a
label every language table translates, and the page carries the picker and
puts its static text through the same tables."""
from __future__ import annotations

import json
import re
from pathlib import Path

from eda_agent.web import i18n
from eda_agent.web.workbench_strings import PAGE, strings

STATIC = Path(i18n.__file__).resolve().parent / "dashboard_static" / "i18n"


def _tables():
    return {p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in STATIC.glob("*.json") if not p.name.startswith("_")}


def test_every_string_the_page_shows_is_translated():
    texts = strings()
    assert len(texts) > 250, "the page's text was not found"
    labels = set(json.loads((STATIC / "_labels.json").read_text(encoding="utf-8")))
    assert not [t for t in texts if t not in labels]
    for lang, table in _tables().items():
        missing = [t for t in texts if t not in table and t not in i18n.UNTRANSLATED]
        assert not missing, (lang, missing[:10])


def test_the_string_reader_sees_new_text():
    """A guard is only as good as its reader: text added to the page in a
    T() call, a table of names or the HTML must be found."""
    page = PAGE.read_text(encoding="utf-8")
    probe = page.replace('T("Next problem")', 'T("A brand new label")', 1) \
        .replace(">Language <", ">Speech <", 1)
    found = set(strings(probe))
    assert {"A brand new label", "Speech"} <= found


def test_the_page_carries_the_picker_and_uses_the_tables():
    page = PAGE.read_text(encoding="utf-8")
    assert 'id="lang"' in page and 'get("/api/language")' in page
    assert '"/api/i18n/"' in page and "function T(" in page
    # No English sentence written straight into the page's script output:
    # every string the script shows goes through T().
    script = page[page.index("<script>"):]
    bare = re.findall(r'say\("[A-Z]', script)
    assert not bare, bare
