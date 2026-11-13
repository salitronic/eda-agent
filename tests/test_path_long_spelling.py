# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A short 8.3 spelling of a path reaches Altium as the long one.

A project's document named through a short spelling of its path loaded a
second time beside the project's copy, and the compiler then reported two
documents of one name.
"""
from __future__ import annotations

import asyncio
import ctypes
import sys

import pytest

from eda_agent.bridge import path_spelling
from eda_agent.bridge.path_spelling import backslashed, long_spelling, one_spelling
from eda_agent.tools import library

windows_only = pytest.mark.skipif(sys.platform != "win32",
                                  reason="8.3 names exist on Windows only")


def _short(path: str) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    got = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
    return buf.value if got else path


@pytest.fixture
def short_dir(tmp_path):
    """A folder with a long name whose 8.3 spelling differs, or a skip when
    the volume makes no 8.3 names."""
    long_dir = tmp_path / "A folder with a long name"
    long_dir.mkdir()
    (long_dir / "Sheet One.SchDoc").write_text("x")
    short = _short(str(long_dir))
    if "~" not in short:
        pytest.skip("8.3 names are not made on this volume")
    return str(long_dir), short


@windows_only
def test_a_short_spelling_is_expanded(short_dir):
    long_dir, short = short_dir
    assert long_spelling(short) == long_dir
    doc = short + "\\Sheet One.SchDoc"
    assert backslashed(doc) == long_dir + "\\Sheet One.SchDoc"
    assert backslashed(doc.replace("\\", "/")) == long_dir + "\\Sheet One.SchDoc"


@windows_only
def test_a_file_not_made_yet_keeps_its_tail(short_dir):
    long_dir, short = short_dir
    new = short + "\\New\\Sheet Two.SchDoc"
    assert long_spelling(new) == long_dir + "\\New\\Sheet Two.SchDoc"


@windows_only
def test_every_place_a_path_travels_is_expanded(short_dir):
    long_dir, short = short_dir
    doc = short + "\\Sheet One.SchDoc"
    out = one_spelling({
        "file_path": doc,
        "file_paths": [doc],
        "project_file": doc,
        "dest_library": doc,
        "destination": doc,
        "parts": [{"library_path": doc}],
        "params": "ObjectKind=Document|FileName=" + doc,
        "library": "PlainName",
        "note": doc,
    })
    want = long_dir + "\\Sheet One.SchDoc"
    for key in ("file_path", "project_file", "dest_library", "destination"):
        assert out[key] == want, key
    assert out["file_paths"] == [want]
    assert out["parts"][0]["library_path"] == want
    assert out["params"] == "ObjectKind=Document|FileName=" + want
    assert out["library"] == "PlainName"
    assert out["note"] == want


def test_the_keys_main_pas_turns_round_get_backslashes():
    # The same keys CanonicalPathValue in Main.pas covers.
    out = one_spelling({"project_file": "C:/a/b.PrjPcb",
                        "dest_library": "C:/a/b.SchLib",
                        "destination": "C:/a/c.SchLib",
                        "move_document": "C:/a/d.SchDoc"})
    assert out["project_file"] == "C:\\a\\b.PrjPcb"
    assert out["dest_library"] == "C:\\a\\b.SchLib"
    assert out["destination"] == "C:\\a\\c.SchLib"
    assert out["move_document"] == "C:/a/d.SchDoc"


@windows_only
def test_a_path_under_any_key_is_expanded(short_dir):
    long_dir, short = short_dir
    doc = short + "\\Sheet One.SchDoc"
    out = one_spelling({"move_document": doc,
                        "rows": [{"document": doc}],
                        "title": "C~1 is not a path"})
    assert out["move_document"] == long_dir + "\\Sheet One.SchDoc"
    assert out["rows"][0]["document"] == long_dir + "\\Sheet One.SchDoc"
    assert out["title"] == "C~1 is not a path"


def test_a_path_without_a_tilde_is_never_looked_up(monkeypatch):
    called = []
    monkeypatch.setattr(path_spelling, "_long_name",
                        lambda p: called.append(p) or p)
    assert long_spelling("C:\\a\\b.SchDoc") == "C:\\a\\b.SchDoc"
    assert backslashed("relative~1/b.SchDoc") == "relative~1/b.SchDoc"
    assert called == []


def test_a_name_windows_cannot_resolve_is_left_as_given(monkeypatch):
    monkeypatch.setattr(path_spelling, "_long_name", lambda p: p)
    assert long_spelling("Z:\\NOPE~1\\x.SchDoc") == "Z:\\NOPE~1\\x.SchDoc"


@windows_only
def test_a_library_in_front_is_matched_under_either_spelling(short_dir):
    long_dir, short = short_dir
    lib = long_dir + "\Parts.PcbLib"
    open(lib, "w").close()

    class _Bridge:
        async def send_command_async(self, command, params=None, **kw):
            return {"file_path": lib}

    got = asyncio.run(library._unfocused_pcblib(_Bridge(), short + "\Parts.PcbLib"))
    assert got == ""
