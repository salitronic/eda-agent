# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A double-click on the status window's auto-off card toggles "always on".

The bridge stops after auto_shutdown_ms without a request (10 minutes by
default), which ended long manual checks in Altium mid-way. auto_shutdown_ms
0 already disabled the stop, but only through the config file, and the card
then showed a meaningless countdown (0 minus the elapsed time). Now a
double-click on the card switches auto-off off ("always on") or back to the
countdown from the full configured period, for the session only.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _form() -> dict:
    return load(_ALTIUM / "StatusForm.pas")


def test_the_card_its_label_and_its_value_take_the_double_click():
    dfm = (_ALTIUM / "StatusForm.dfm").read_text(encoding="utf-8")
    for obj in ("pnl_K4", "lbl_LblStop", "lbl_ValStop"):
        block = re.search(rf"object {obj}: \w+(.*?)\n\s*(object |end)", dfm, re.S)
        assert block and "OnDblClick = pnl_K4DblClick" in block.group(1), obj


def test_the_double_click_toggles_between_off_and_the_configured_period():
    body = _form()["pnl_K4DblClick"]
    assert re.search(r"If AutoShutdownMs > 0 Then\s+AutoShutdownMs := 0", body)
    assert "AutoShutdownMs := ConfiguredShutdownMs;" in body
    # Back on: from the full period, as Renew does.
    assert "RenewRequested := True" in body
    assert body.index("ConfiguredShutdownMs") < body.index("RenewRequested := True")


def test_always_on_is_shown_instead_of_a_countdown():
    body = _form()["ColorCountdown"]
    assert re.search(r"If AutoShutdownMs = 0 Then\s+Begin.*?'always on'.*?Exit;", body, re.S)
    assert body.index("'always on'") < body.index("IdleSecToShutdown <= 30")


def test_the_period_to_return_to_comes_from_the_config():
    main = load(_ALTIUM / "Main.pas")
    assert "ConfiguredShutdownMs := 600000;" in main["InitDefaultConfig"]
    assert re.search(r"If AutoShutdownMs > 0 Then ConfiguredShutdownMs := AutoShutdownMs;",
                     main["LoadMCPConfig"])
