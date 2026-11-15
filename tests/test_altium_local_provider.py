# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The provider that reads the Altium libraries already on this machine.

It answers a question no other source can: do I ALREADY own this part?
Every other provider proposes something to import, and importing a part
you already have produces a second symbol with a slightly different
name, which is how a library rots.

It is also the only source with no network, no login, no rate limit and
no third party, so it keeps working when every remote one is
unreachable. That matters more than it looks: with the registry
unconfigured and EasyEDA's search endpoint withdrawn upstream, the
remote sources answer very little.

Altium reads the libraries (``library.get_components``); here a
stand-in answers for it, over a synthetic root.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from eda_agent.libimport.providers.altium_local import AltiumLocalProvider
from eda_agent.libimport.providers.base import (
    ProviderError,
    ProviderUnavailable,
)


def test_no_libraries_is_unavailable_not_empty(tmp_path, monkeypatch):
    """"I found nothing" and "I could not look" are different answers.

    An empty result would tell the caller the part does not exist
    anywhere, when in fact this source never ran.
    """
    monkeypatch.setenv("EDA_AGENT_ALTIUM_LIBRARIES", str(tmp_path))

    with pytest.raises(ProviderUnavailable) as excinfo:
        AltiumLocalProvider().search("anything")

    assert "EDA_AGENT_ALTIUM_LIBRARIES" in str(excinfo.value), (
        "the message must say how to point it somewhere useful")
    assert str(tmp_path) in str(excinfo.value), (
        "it must name where it looked, or the user cannot tell whether "
        "the setting took effect")


class _Altium:
    """Altium's library reader, answering from a table of libraries."""

    def __init__(self, libraries, running=True):
        self.libraries, self.running, self.sent = libraries, running, []

    def is_altium_running(self):
        return self.running

    def send_command(self, command, params=None, timeout=None):
        self.sent.append((command, params))
        name = pathlib.Path(params["library_path"]).name
        if name not in self.libraries:
            raise RuntimeError("READ_FAILED")
        return {"components": self.libraries[name]}


def _libraries(tmp_path, monkeypatch, altium, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setenv("EDA_AGENT_ALTIUM_LIBRARIES", str(tmp_path))
    import eda_agent.bridge
    monkeypatch.setattr(eda_agent.bridge, "get_bridge", lambda: altium)


_ICS = [{"name": "TPS54331D", "description": "Buck DC-DC converter"},
        {"name": "SS14", "description": "Schottky diode 1 A 40 V"}]


def test_an_unreadable_library_does_not_hide_the_others(tmp_path,
                                                        monkeypatch):
    """A corrupt or locked file is normal on a shared library drive."""
    altium = _Altium({"ICs.SchLib": _ICS})
    _libraries(tmp_path, monkeypatch, altium, "Broken.SchLib", "ICs.SchLib")
    hits = AltiumLocalProvider().search("", limit=20)
    assert [h.extra["component"] for h in hits] == ["TPS54331D", "SS14"]
    assert [c for c, _ in altium.sent] == ["library.get_components"] * 2


def test_without_altium_it_is_unavailable(tmp_path, monkeypatch):
    _libraries(tmp_path, monkeypatch, _Altium({}, running=False), "ICs.SchLib")
    with pytest.raises(ProviderUnavailable) as excinfo:
        AltiumLocalProvider().search("x")
    assert "Altium is not running" in str(excinfo.value)


def test_the_libraries_are_never_opened_here():
    from eda_agent.libimport.providers import altium_local
    src = pathlib.Path(altium_local.__file__).read_text(encoding="utf-8")
    for call in ("olefile", "read_bytes", "open(", "fileio"):
        assert call not in src, call


def test_the_roots_are_configurable_and_not_a_whole_drive_scan(monkeypatch):
    """Searching every drive behind the user's back is not acceptable."""
    from eda_agent.libimport.providers import altium_local

    monkeypatch.setenv("EDA_AGENT_ALTIUM_LIBRARIES",
                       os.pathsep.join(["A:/one", "B:/two"]))
    roots = [str(r) for r in altium_local._roots()]

    assert len(roots) == 2
    assert any("one" in r for r in roots)
    assert any("two" in r for r in roots)


def test_a_malformed_part_id_is_refused_clearly():
    provider = AltiumLocalProvider()
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch("no-separator-here")
    assert "::" in str(excinfo.value), (
        "the error must show the expected shape, not just reject")


def test_it_offers_no_download_format():
    """Not an omission: a hit is ALREADY an Altium symbol.

    Advertising a convertible format would invite a caller to import a
    part that is by definition already installed.
    """
    provider = AltiumLocalProvider()
    assert provider.formats == ()
    assert provider.usable_in == ("altium",)


def test_it_lists_what_altium_reads(tmp_path, monkeypatch):
    _libraries(tmp_path, monkeypatch, _Altium({"EDAAgentTest_ICs.SchLib": _ICS}),
               "EDAAgentTest_ICs.SchLib")
    provider = AltiumLocalProvider()

    hits = provider.search("", limit=20)
    one = next(h for h in hits if h.extra["component"] == "TPS54331D")
    assert one.provider == "altium_local"
    assert one.part_id == "EDAAgentTest_ICs.SchLib::TPS54331D"
    assert "already installed" in one.provenance

    detail = provider.fetch(one.part_id)
    assert detail["component"] == "TPS54331D"
    assert detail["library_path"].endswith("EDAAgentTest_ICs.SchLib")
    assert detail["files"] == {}, "nothing is downloaded"


def test_search_filters_on_name_and_description(tmp_path, monkeypatch):
    _libraries(tmp_path, monkeypatch, _Altium({"EDAAgentTest_ICs.SchLib": _ICS}),
               "EDAAgentTest_ICs.SchLib")
    provider = AltiumLocalProvider()

    by_name = provider.search("TPS54331", limit=20)
    assert [h.extra["component"] for h in by_name] == ["TPS54331D"]

    # The fixture's SS14 carries "Schottky diode" in its description.
    by_description = provider.search("schottky", limit=20)
    assert any(h.extra["component"] == "SS14" for h in by_description), (
        "description text must be searchable; a part is often known by "
        "what it is rather than by its symbol name")
