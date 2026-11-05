# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The server warms CadQuery in a throwaway process, never in its own.

The first footprint with a STEP model waited 140 s on a cold CadQuery
import and passed the client's two-minute wait. The warm-up imports it
in a child process that exits, so no server holds the kernel for
nothing, and it does nothing where CadQuery is not installed.
"""
from __future__ import annotations

import inspect
import subprocess

from eda_agent.design import models3d


def test_it_imports_in_a_child_process(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: seen.append(args))
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    models3d.warm_cadquery()
    assert seen and seen[0][1:] == ["-c", "import cadquery"]


def test_nothing_starts_without_cadquery(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: seen.append(args))
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    models3d.warm_cadquery()
    assert seen == []


def test_the_server_starts_it():
    from eda_agent import server
    assert "warm_cadquery()" in inspect.getsource(server)


def test_a_model_is_written_without_importing_cadquery_here(tmp_path):
    """The first model inside a take's server took 124 s and 224 s, all of
    it CadQuery's import; a fresh process imports it in about 2 s. The
    server hands the writing to a worker process instead."""
    import subprocess
    import sys

    import pytest
    from eda_agent.design import models3d

    if not models3d._has_cadquery():
        pytest.skip("CadQuery is not installed")
    code = (
        "import sys\n"
        "from eda_agent.design.models3d import ModelPlan, Solid, write_step\n"
        "p = ModelPlan('chip', [Solid('box', 'ceramic', 0, 0, 0, 1, .5, .5)], [], .5,"
        " {'x': 'drawing'}, [])\n"
        f"r = write_step(p, r'{tmp_path / 'a.step'}', writers=['cadquery'])\n"
        "assert r['ok'], r\n"
        "assert 'cadquery' not in sys.modules, 'imported in the calling process'\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr[-800:]
    assert (tmp_path / "a.step").read_bytes().startswith(b"ISO-10303-21")
