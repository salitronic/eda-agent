# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_format_value writes a value in the form chosen on the Config tab.

The bridge's engine reads any form and writes RKM. The tool reads the
panel's choice on every call: None leaves a value as written (and refuses
tostring, which has no written form), RKM is the engine's text, SI is
written from the exact value and kind the engine returns.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from eda_agent.tools import application
from eda_agent.tools.application import si_value_text


class _Bridge:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    async def send_command_async(self, command, params=None, **_):
        self.sent.append((command, params))
        return dict(self.reply)


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _call(monkeypatch, tmp_path, prefs, reply, **kwargs):
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    (tmp_path / "preferences.json").write_text(json.dumps(prefs))
    bridge = _Bridge(reply)
    monkeypatch.setattr(application, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    application.register_application_tools(mcp)
    return asyncio.run(mcp.tools["app_format_value"](**kwargs)), bridge.sent


_CLEANED = {"text": "2u2", "kind": "farad", "value": "0.0000022", "rest": "", "notes": []}


@pytest.mark.parametrize("prefs, text, style", [
    ({"value_format": "none"}, "2.2 uF", "none"),
    ({"value_format": "rkm"}, "2u2", "rkm"),
    ({"value_format": "si"}, "2.2u", "si"),
    ({"value_format": "si", "value_unit": "with"}, "2.2uF", "si_unit"),
    # The RKM code has no unit, whatever the file says.
    ({"value_format": "rkm", "value_unit": "with"}, "2u2", "rkm"),
])
def test_cleanup_writes_the_form_the_panel_chose(monkeypatch, tmp_path, prefs, text, style):
    out, sent = _call(monkeypatch, tmp_path, prefs, _CLEANED, action="cleanup", text="2.2 uF")
    assert out["text"] == text and out["value_format"] == style
    assert out["value"] == "0.0000022" and len(sent) == 1


def test_no_file_means_values_are_left_as_written(monkeypatch, tmp_path):
    out, _ = _call(monkeypatch, tmp_path, {}, _CLEANED, action="cleanup", text="2.2 uF")
    assert out["text"] == "2.2 uF" and out["value_format"] == "none"


def test_tostring_is_refused_when_no_format_is_chosen(monkeypatch, tmp_path):
    out, sent = _call(monkeypatch, tmp_path, {"value_format": "none"}, {"text": "4K7"},
                      action="tostring", kind="R", value="4700")
    assert out["ok"] is False and "Config tab" in out["reason"] and sent == []


def test_tostring_in_si_uses_the_value_as_given(monkeypatch, tmp_path):
    out, _ = _call(monkeypatch, tmp_path, {"value_format": "si"}, {"text": "4K7"},
                   action="tostring", kind="R", value="4700")
    assert out["text"] == "4.7k" and out["value_format"] == "si"


def test_fromstring_is_untouched_by_the_panel(monkeypatch, tmp_path):
    reply = {"kind": "farad", "value": "0.0000022", "rest": "", "notes": []}
    out, _ = _call(monkeypatch, tmp_path, {"value_format": "si"}, reply,
                   action="fromstring", text="2u2")
    assert out == reply


def test_a_bridge_error_comes_back_unchanged(monkeypatch, tmp_path):
    err = {"success": False, "error": {"code": "BAD_VALUE", "message": "10.1.1"}}
    out, _ = _call(monkeypatch, tmp_path, {"value_format": "si"}, err,
                   action="cleanup", text="10.1.1k")
    assert out == err


@pytest.mark.parametrize("value, kind, unit, text", [
    ("4700", "ohm", False, "4.7k"), ("4700", "ohm", True, "4.7k"),
    ("0.1", "R", True, "0.1"), ("2200000", "ohm", False, "2.2M"),
    ("0.0000022", "farad", False, "2.2u"), ("1E-7", "C", True, "100nF"),
    ("0.01", "farad", True, "10000uF"),
    ("0.00068", "henry", True, "680uH"), ("0.2", "L", False, "200m"),
    ("3.3", "volt", False, "3.3V"), ("0.09", "ampere", False, "90mA"),
    ("125", "celsius", False, "125C"), ("0.1", "percent", False, "0.1%"),
    ("50", "ppm", True, "50ppm"), ("0", "farad", True, "0F"),
    ("-0.056046", "volt", False, "-56.046mV"),
    ("1234.56789012345678901234567890", "ohm", False, "1.2345678901234567890123456789k"),
    ("42334495641891781.87857058681", "ohm", False, "42334495.64189178187857058681G"),
])
def test_the_si_form_is_exact_and_keeps_to_the_engines_letters(value, kind, unit, text):
    assert si_value_text(value, kind, unit) == text


def test_an_unknown_kind_has_no_si_form():
    assert si_value_text("1", "lumen", False) is None
    assert si_value_text("abc", "ohm", False) is None
