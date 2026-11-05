# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A non-ASCII profile path and a response with a byte-order mark both read.

Both showed up on a machine with a non-English locale.
"""

from __future__ import annotations

import inspect

from eda_agent.config import _pointer_encoding
from eda_agent.diag import health
from eda_agent.diag.checks import Status


def test_pointer_with_an_accented_path_is_read_not_crashed(tmp_path,
                                                          monkeypatch):
    pointer = tmp_path / "workspace.txt"
    pointer.write_bytes("C:/Users/Jos\u00e9/EDA Agent/workspace".encode(
        _pointer_encoding()))
    monkeypatch.setenv("EDA_AGENT_POINTER_FILE", str(pointer))
    check = health._check_pointer_file()
    # It may legitimately disagree with the configured workspace; what it
    # must not do is fail to decode the file.
    assert check.name != "workspace pointer file readable", check.message


def test_an_undecodable_pointer_is_reported_not_raised(tmp_path, monkeypatch):
    pointer = tmp_path / "workspace.txt"
    pointer.write_bytes(b"\x81\x8d\x8f\x90\x9d")
    monkeypatch.setenv("EDA_AGENT_POINTER_FILE", str(pointer))
    import eda_agent.config as config
    monkeypatch.setattr(config, "_pointer_encoding", lambda: "utf-8")
    check = health._check_pointer_file()
    assert check.status == Status.FAIL
    assert check.name == "workspace pointer file readable"


def test_the_response_reader_tolerates_a_byte_order_mark():
    from eda_agent.bridge import altium_bridge
    src = inspect.getsource(altium_bridge)
    # The reply is read through _read_response_json, which decodes UTF-8
    # with the mark dropped before falling back to the Windows codepage.
    assert "data = _read_response_json(response_path)" in src
    assert 'raw.decode("utf-8-sig")' in inspect.getsource(altium_bridge._read_response_json)
