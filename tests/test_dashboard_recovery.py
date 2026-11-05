# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Test the dashboard /api/recovery endpoint (roadmap 1.2 banner)."""

from __future__ import annotations

import pytest

from eda_agent.bridge.fault_state import record_fault
from eda_agent.bridge.recovery import DEAD_LOOP, recovery_guidance


@pytest.fixture
def client(tmp_path):
    from eda_agent.web.dashboard import create_app
    app = create_app(workspace_dir=tmp_path)
    app.testing = True
    return app.test_client(), tmp_path


def test_recovery_healthy_returns_null(client):
    c, _ = client
    resp = c.get("/api/recovery")
    assert resp.status_code == 200
    assert resp.get_json() == {"fault": None}


def test_recovery_surfaces_recorded_fault(client):
    c, ws = client
    record_fault(ws, recovery_guidance(DEAD_LOOP), when="2026-07-02T12:00:00")
    data = c.get("/api/recovery").get_json()
    assert data["recovery"]["fault"] == DEAD_LOOP
    assert data["recovery"]["steps"]
    assert data["when"] == "2026-07-02T12:00:00"


def test_index_page_wires_recovery_banner(client):
    # Sanity that the inlined HTML/JS for the banner is present and the page
    # still serves (guards against gross corruption of the 9k-line file).
    c, _ = client
    html = c.get("/classic").get_data(as_text=True)
    assert 'id="recovery-banner"' in html
    assert "pollRecovery" in html
    assert "/api/recovery" in html
