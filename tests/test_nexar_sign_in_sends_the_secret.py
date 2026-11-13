# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A Nexar portal app is a confidential client: its sign-in and every
refresh carry the client secret, and a refusal says why.

Without the secret the code exchange came back HTTP 400 and the reply
said only that.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from eda_agent import altium365 as a3


def test_the_secret_is_read_and_never_shown():
    cfg = a3.read_environment({"EDA_AGENT_NEXAR_APP_ID": "app",
                               "EDA_AGENT_NEXAR_CLIENT_SECRET": " s3cret "})
    assert cfg.nexar_client_secret == "s3cret"
    assert "s3cret" not in repr(cfg)


def test_the_form_carries_the_secret_only_when_there_is_one():
    with_secret = a3.Altium365Config(nexar_app_id="app", nexar_client_secret="s")
    assert a3._token_form(with_secret, {"grant_type": "x"}) == {
        "grant_type": "x", "client_id": "app", "client_secret": "s"}
    assert a3._token_form(a3.Altium365Config(nexar_app_id="app"), {}) == {"client_id": "app"}


class _Reply:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.data).encode()


def test_a_refresh_sends_the_secret(tmp_path, monkeypatch):
    sent = []

    def urlopen(request, timeout=None):
        sent.append(dict(urllib.parse.parse_qsl(request.data.decode())))
        return _Reply({"access_token": "new", "expires_in": 3600})

    saved = {"access_token": "old", "refresh_token": "r", "saved_at": 0, "expires_in": 10}
    monkeypatch.setattr(a3, "fetch_login", lambda path: saved)
    monkeypatch.setattr(a3, "store_login", lambda path, token: None)
    cfg = a3.Altium365Config(nexar_app_id="app", nexar_client_secret="s",
                             saved_login=tmp_path / "login.bin")
    session = a3.Altium365Session(cfg, urlopen=urlopen, now=lambda: 10_000)
    assert session._access_token() == "new"
    assert sent == [{"grant_type": "refresh_token", "refresh_token": "r",
                     "client_id": "app", "client_secret": "s"}]


def test_a_refusal_names_nexars_error_and_a_fix_and_hides_the_secret():
    def urlopen(request, timeout=None):
        body = json.dumps({"error": "invalid_client",
                           "error_description": "client s3cret unknown"}).encode()
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(body))

    with pytest.raises(a3.Altium365Error) as err:
        a3._ask_for_token({"client_secret": "s3cret"}, 5, urlopen)
    said = str(err.value)
    assert "HTTP 400, invalid_client" in said and "EDA_AGENT_NEXAR_CLIENT_SECRET" in said
    assert "s3cret" not in said and "***" in said
