# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Each tool call the client makes is counted: estimated tokens in and
out, images by size, and the time taken, summed per tool."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from types import SimpleNamespace

import pytest

from eda_agent.diag import usage


def _png(w: int, h: int) -> str:
    head = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h)
    return base64.b64encode(head + b"\0" * 20).decode()


@pytest.fixture
def where(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "folder", lambda: tmp_path)
    monkeypatch.delenv(usage.OFF_ENV, raising=False)
    from eda_agent import preferences
    monkeypatch.setattr(preferences, "usage_on", lambda workspace=None: True)
    return tmp_path


def test_text_and_images_are_estimated():
    text = SimpleNamespace(type="text", text="x" * 400)
    pic = SimpleNamespace(type="image", data=_png(3136, 1568))
    out = usage.measure({"a": "y" * 38}, [text, pic])
    assert out["in"] == 12                          # '{"a": "yyy..."}' is 47 characters
    assert out["images"] == round(1568 * 784 / 750)  # scaled to 1568 on the long side
    assert out["out"] == 100 + out["images"]


def test_the_structured_copy_is_what_is_counted():
    blocks = [SimpleNamespace(type="text", text="z" * 4000)]
    out = usage.measure({}, (blocks, {"ok": True}))
    assert out["out"] == usage.text_tokens(len(json.dumps({"ok": True})))


def test_calls_are_recorded_and_summed(where):
    usage.record("pcb_get_components", {}, [SimpleNamespace(type="text", text="a" * 800)], 1.5)
    usage.record("pcb_get_components", {}, [SimpleNamespace(type="text", text="a" * 400)], 0.5)
    usage.record("tool_invoke", {"name": "lib_search"}, [], 0.1, member="lib_search", ok=False)
    report = usage.summary(1, where=where)
    top = report["tools"][0]
    assert (top["tool"], top["calls"], top["out"], top["avg_ms"]) == ("pcb_get_components", 2, 300, 1000)
    assert report["tools"][1]["tool"] == "tool_invoke > lib_search"
    assert report["tools"][1]["failed"] == 1
    assert report["total"]["calls"] == 3


def test_recording_can_be_switched_off(where, monkeypatch):
    monkeypatch.setenv(usage.OFF_ENV, "1")
    assert usage.record("x", {}, [], 0.1) is None
    assert list(where.iterdir()) == []


def test_the_server_counts_the_outermost_call_only(where, monkeypatch):
    from eda_agent import server

    seen = []
    monkeypatch.setattr(usage, "record", lambda *a, **k: seen.append((a[0], k.get("member"))))
    server._count_usage("tool_invoke", {"name": "pcb_view", "arguments": {}}, [], 0.2)
    server._count_usage("pcb_view", {}, [], 0.2)
    assert seen == [("tool_invoke", "pcb_view"), ("pcb_view", "")]
    body = server._CompactFastMCP.call_tool.__code__.co_names
    assert "_count_usage" in body


def test_the_dashboard_serves_the_summary(where, tmp_path):
    flask = pytest.importorskip("flask")  # noqa: F841
    from eda_agent.web.dashboard import create_app

    (tmp_path / "usage").mkdir(exist_ok=True)
    usage.record("lib_search", {}, [], 0.3)
    import shutil
    for f in where.glob("*.jsonl"):
        shutil.copy(f, tmp_path / "usage" / f.name)
    app = create_app(tmp_path)
    reply = app.test_client().get("/api/usage?days=2").get_json()
    assert reply["total"]["calls"] == 1 and reply["tools"][0]["tool"] == "lib_search"


def test_counting_is_off_until_the_panel_turns_it_on(tmp_path, monkeypatch):
    from eda_agent import preferences
    from eda_agent.config import get_config
    monkeypatch.delenv(usage.OFF_ENV, raising=False)
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    assert not usage.enabled()                      # no preferences file
    (tmp_path / "preferences.json").write_text('{"usage":"on"}')
    assert usage.enabled()
    monkeypatch.setenv(usage.OFF_ENV, "1")          # the environment still wins
    assert not usage.enabled()
    assert preferences.DEFAULTS["usage"] == "off"
