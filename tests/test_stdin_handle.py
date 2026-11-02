# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""While a request read waits on the client's pipe, a tool can still load
a compiled module: on Windows the read holds up every other call on the
pipe, and loading one queries the process's standard input."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

CHILD = textwrap.dedent('''
    import sys, threading, time
    from eda_agent.stdin_handle import private_stdin
    src = private_stdin() if sys.argv[1] == "private" else sys.stdin.buffer
    threading.Thread(target=src.readline, daemon=True).start()
    time.sleep(0.5)
    done = threading.Event()
    def load():
        import numpy  # noqa: F401
        done.set()
    threading.Thread(target=load, daemon=True).start()
    print("loaded" if done.wait(20) else "stuck", flush=True)
''')


def _run(tmp_path, mode: str) -> str:
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    p = subprocess.Popen([sys.executable, str(script), mode], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, env=env)
    try:
        return p.stdout.readline().decode().strip()
    finally:
        p.kill()
        p.wait()


@pytest.mark.skipif(os.name != "nt", reason="the shared pipe holds up the load on Windows only")
def test_a_compiled_module_loads_while_a_request_read_waits(tmp_path):
    assert _run(tmp_path, "private") == "loaded"

