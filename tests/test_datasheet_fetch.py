# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Datasheet fetch and cache, against a local fake of ``urlopen``.

Nothing here reaches the network: ``urlopen`` is replaced by a fake that
serves bytes from memory and counts its calls, and the session-wide guard
in conftest denies any real fetch that slips past it.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error

import pytest

from eda_agent.design import datasheet_fetch as dsf

PDF = (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
       b"2 0 obj << /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >> endobj\n"
       b"3 0 obj << /Type /Page /Parent 2 0 R >> endobj\n"
       b"4 0 obj << /Type/Page /Parent 2 0 R >> endobj\n"
       b"5 0 obj << /Type /Page /Parent 2 0 R >> endobj\n%%EOF\n")
URL = "https://www.example.com/ds/part.pdf"


class _Response(io.BytesIO):
    def __init__(self, data: bytes, url: str, ctype: str):
        super().__init__(data)
        self._url = url
        self.headers = {"Content-Type": ctype}

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeNet:
    """Serves one body per URL and records every request."""

    def __init__(self, body: bytes = PDF, ctype: str = "application/pdf",
                 final_url: str = "", error: Exception = None):
        self.body, self.ctype = body, ctype
        self.final_url, self.error = final_url, error
        self.calls: list[str] = []

    def __call__(self, request, timeout=None):
        self.calls.append(request.full_url)
        assert timeout and timeout > 0
        if self.error is not None:
            raise self.error
        return _Response(self.body, self.final_url or request.full_url,
                         self.ctype)


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(dsf.urllib.request, "urlopen", fake)
    return fake


def test_a_miss_then_a_hit(net, tmp_path):
    first = dsf.fetch_datasheet(URL, "PART-1", directory=tmp_path)
    assert first["ok"] and first["cache"] == "miss"
    assert first["pages"] == 3 and first["pages_approximate"] is True
    assert first["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert first["size"] == len(PDF)
    assert open(first["path"], "rb").read() == PDF
    meta = json.loads(open(first["sidecar"], encoding="utf-8").read())
    assert meta["url"] == URL and meta["part_number"] == "PART-1"
    assert meta["sha256"] == first["sha256"] and meta["size"] == len(PDF)
    assert meta["fetched_at"].endswith("+00:00")
    second = dsf.fetch_datasheet(URL, directory=tmp_path)
    assert second["cache"] == "hit" and second["path"] == first["path"]
    assert second["part_number"] == "PART-1"
    assert net.calls == [URL], "the hit must not fetch again"


def test_the_file_is_named_by_a_hash_of_the_url(net, tmp_path):
    a = dsf.fetch_datasheet(URL, directory=tmp_path)
    b = dsf.fetch_datasheet(URL + "?rev=2", directory=tmp_path)
    assert a["path"] != b["path"]
    key = hashlib.sha256(URL.encode()).hexdigest()[:32]
    assert a["path"].endswith(f"{key}.pdf")


def test_a_cached_copy_that_changed_is_fetched_again(net, tmp_path):
    first = dsf.fetch_datasheet(URL, directory=tmp_path)
    with open(first["path"], "ab") as fh:
        fh.write(b"tampered")
    again = dsf.fetch_datasheet(URL, directory=tmp_path)
    assert again["cache"] == "miss" and "no longer matched" in again["note"]
    assert open(again["path"], "rb").read() == PDF
    assert len(net.calls) == 2


@pytest.mark.parametrize("url", ["ftp://example.com/a.pdf",
                                 "file:///C:/data/a.pdf",
                                 "javascript:alert(1)", "", "https://"])
def test_only_http_and_https_are_fetched(net, tmp_path, url):
    r = dsf.fetch_datasheet(url, directory=tmp_path)
    assert not r["ok"]
    assert net.calls == []
    assert not any(tmp_path.iterdir())


def test_an_html_page_is_not_cached_as_a_pdf(monkeypatch, tmp_path):
    fake = FakeNet(body=b"<!DOCTYPE html><html><body>Sign in</body></html>",
                   ctype="text/html")
    monkeypatch.setattr(dsf.urllib.request, "urlopen", fake)
    r = dsf.fetch_datasheet(URL, directory=tmp_path)
    assert not r["ok"] and "HTML page" in r["reason"]
    assert "text/html" in r["reason"]
    assert not any(tmp_path.iterdir())


def test_a_file_over_the_cap_is_refused(monkeypatch, tmp_path):
    fake = FakeNet(body=PDF + b"0" * 4000)
    monkeypatch.setattr(dsf.urllib.request, "urlopen", fake)
    r = dsf.fetch_datasheet(URL, directory=tmp_path, max_bytes=2000)
    assert not r["ok"] and "larger than 2000 bytes" in r["reason"]
    assert not any(tmp_path.iterdir())
    assert dsf.MAX_BYTES == 50 * 1024 * 1024


@pytest.fixture(autouse=True)
def _no_remembered_hosts():
    dsf._failed_hosts.clear()
    yield
    dsf._failed_hosts.clear()


def test_http_errors_and_unreachable_hosts(monkeypatch, tmp_path):
    fake = FakeNet(error=urllib.error.HTTPError(URL, 404, "Not Found", {}, None))
    monkeypatch.setattr(dsf.urllib.request, "urlopen", fake)
    assert "HTTP 404" in dsf.fetch_datasheet(URL, directory=tmp_path)["reason"]
    fake.error = TimeoutError("timed out")
    assert "timed out" in dsf.fetch_datasheet(URL, directory=tmp_path)["reason"]
    dsf._failed_hosts.clear()
    fake.error = urllib.error.URLError("no route")
    assert "cannot reach" in dsf.fetch_datasheet(URL, directory=tmp_path)["reason"]


def test_a_host_that_did_not_answer_is_not_waited_on_again(monkeypatch, tmp_path):
    """st.com timed out at 60 s on every try during a take."""
    fake = FakeNet(error=TimeoutError("timed out"))
    monkeypatch.setattr(dsf.urllib.request, "urlopen", fake)
    dsf.fetch_datasheet(URL, directory=tmp_path)
    again = dsf.fetch_datasheet(URL + "?2", directory=tmp_path)
    assert len(fake.calls) == 1, "the dead host was asked again"
    assert again["host_unreachable"] and "browser" in again["reason"]
    # An HTTP answer is an answer: the host is not remembered for it.
    dsf._failed_hosts.clear()
    fake.error = urllib.error.HTTPError(URL, 403, "Forbidden", {}, None)
    dsf.fetch_datasheet(URL, directory=tmp_path)
    dsf.fetch_datasheet(URL, directory=tmp_path)
    assert len(fake.calls) == 3
    assert dsf.TIMEOUT_S <= 30


def test_a_redirect_off_http_is_refused(monkeypatch, tmp_path):
    fake = FakeNet(final_url="ftp://elsewhere.example/a.pdf")
    monkeypatch.setattr(dsf.urllib.request, "urlopen", fake)
    r = dsf.fetch_datasheet(URL, directory=tmp_path)
    assert not r["ok"] and "redirected" in r["reason"]


def test_page_count_falls_back_to_the_page_tree():
    assert dsf.count_pages(PDF)[0] == 3
    compressed = b"%PDF-1.5\n7 0 obj << /Type /Pages /Kids [8 0 R] /Count 37 >>"
    n, how = dsf.count_pages(compressed)
    assert n == 37 and "/Count" in how
    reverse = b"%PDF-1.5\n7 0 obj << /Count 12 /Kids [8 0 R] /Type /Pages >>"
    assert dsf.count_pages(reverse)[0] == 12
    n, how = dsf.count_pages(b"%PDF-1.7\nstream...endstream")
    assert n is None and "not found" in how


def test_the_default_folder_is_in_the_workspace(monkeypatch, tmp_path):
    from eda_agent.config import get_config

    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path)
    assert dsf.datasheet_dir() == tmp_path / "datasheets"
