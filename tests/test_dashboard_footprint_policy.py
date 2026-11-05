# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Test the dashboard /api/footprint-policy endpoint."""

from __future__ import annotations

import pytest

import eda_agent.web.dashboard as dash


@pytest.fixture
def client(tmp_path):
    from eda_agent.web.dashboard import create_app
    app = create_app(workspace_dir=tmp_path)
    app.testing = True
    return app.test_client()


_NAMES = ["R0402", "C0402", "THT"]


def _geometry(name):
    if name == "THT":
        pads = [{"name": "1", "shape": "rectangular", "layer": "top", "hole": 20},
                {"name": "2", "shape": "round", "layer": "multi", "hole": 20}]
    else:
        pads = [{"name": "1", "shape": "rectangular", "layer": "top", "hole": 0},
                {"name": "2", "shape": "round", "layer": "top", "hole": 0}]
    return {"name": name, "pads": pads, "primitives": [], "bodies": 0}


def _fake_bridge_call(command, params=None, timeout=None):
    if command == "library.get_library_geometry":
        offset = (params or {}).get("offset", 0)
        limit = (params or {}).get("limit", 250)
        window = _NAMES[offset:offset + limit]
        return {"library_path": "Demo.PcbLib", "total": len(_NAMES),
                "offset": offset, "count": len(window),
                "footprints": [_geometry(n) for n in window]}


def _paging_bridge_call(calls):
    """Bulk command that serves one footprint per page, recording offsets."""
    def _call(command, params=None, timeout=None):
        if command != "library.get_library_geometry":
            return None
        offset = (params or {}).get("offset", 0)
        calls.append(offset)
        window = _NAMES[offset:offset + 1]
        return {"library_path": "Demo.PcbLib", "total": len(_NAMES),
                "offset": offset, "count": len(window),
                "footprints": [_geometry(n) for n in window]}
    return _call


def test_footprint_policy_unavailable_without_altium(client, monkeypatch):
    monkeypatch.setattr(dash, "_bridge_call", lambda *a, **k: None)
    data = client.get("/api/footprint-policy").get_json()
    assert data["available"] is False
    assert "reason" in data


def test_footprint_policy_audits_live_library(client, monkeypatch):
    monkeypatch.setattr(dash, "_bridge_call", _fake_bridge_call)
    data = client.get("/api/footprint-policy").get_json()
    assert data["available"] is True
    assert data["footprint_count"] == 3
    assert data["library_path"] == "Demo.PcbLib"
    drill = [f for f in data["findings"]
             if f["dimension"] == "pad_drill" and f["footprint"] == "THT"]
    assert drill and drill[0]["actual"] == "top"
    # the fix plan rides along so the panel can show auto-corrections
    assert any(a["auto"] for a in data["fixes"])


def test_footprint_policy_pages_the_whole_library(client, monkeypatch):
    calls: list = []
    monkeypatch.setattr(dash, "_bridge_call", _paging_bridge_call(calls))
    data = client.get("/api/footprint-policy").get_json()
    assert data["footprint_count"] == 3
    assert calls == [0, 1, 2]  # paged to exhaustion, no re-reads


def test_footprint_policy_never_calls_per_footprint(client, monkeypatch):
    seen: list = []

    def _spy(command, params=None, timeout=None):
        seen.append(command)
        return _fake_bridge_call(command, params, timeout)

    monkeypatch.setattr(dash, "_bridge_call", _spy)
    client.get("/api/footprint-policy")
    assert "library.get_footprint_pads" not in seen


def test_index_page_wires_footprint_panel(client):
    # The tab, pane, loader and endpoint are all inlined and the 9k-line page
    # still serves (guards against gross corruption of the file).
    html = client.get("/classic").get_data(as_text=True)
    assert 'data-tab="footprints"' in html
    assert 'data-pane="footprints"' in html
    assert "loadFootprints" in html
    assert "renderFootprints" in html
    assert "/api/footprint-policy" in html
