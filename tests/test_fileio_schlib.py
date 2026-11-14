# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The .SchLib component-header reader behind the local library search."""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.fileio.altium_schlib import read_schlib_components

LIB = Path(__file__).resolve().parent / "integration" / "fixtures" / "EDAAgentTest_ICs.SchLib"


def test_reads_library_component_headers():
    comps = read_schlib_components(LIB)
    by = {c["lib_reference"]: c for c in comps}
    assert {"SS14", "TPS54331D", "Component_1"} <= set(by)
    assert by["TPS54331D"]["description"].startswith("Buck DC-DC")
    assert by["SS14"]["description"]


def test_bad_library_file_raises(tmp_path):
    junk = tmp_path / "x.SchLib"
    junk.write_bytes(b"not ole")
    with pytest.raises(ValueError):
        read_schlib_components(junk)
