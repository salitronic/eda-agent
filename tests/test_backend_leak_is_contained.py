# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One test's backend registration must not reach the next test.

``register_backend`` records which backend it registered in a process
global, and most test files that call it never put the previous value
back. Nothing sets ``EDA_AGENT_BACKEND`` in CI or in conftest, so
``active_backend_name`` falls back to that global and a leak is not
masked. A later test can then resolve against a backend it never asked
for, and either pass for the wrong reason or fail for one that has
nothing to do with the file it lives in.

That is what happened: a pair of files enumerating all three backends
left easyeda active and broke ``tests/design/test_autonomy.py``.

An autouse fixture in conftest now restores it around every test. This
file is the reason anyone would notice if that fixture were deleted:
the first step deliberately leaks, the second checks the leak did not
arrive. Verified by disabling the fixture, at which point the second
step fails.

THE PAIR RUNS IN ITS OWN PYTEST PROCESS, IN A FORCED ORDER. It only means
something if step two runs after step one in the same process. Under
``pytest -n`` the two can land on different workers, where step two
passes without the leak ever having happened; under pytest-randomly
they can swap. Either way it goes green and proves nothing. So the
steps are named so the main run does not collect them, and
``test_the_leak_pair_passes_in_order`` runs exactly those two with the
reordering and distributing plugins switched off.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from eda_agent.core import backends

_LEAKED = "easyeda"
_ROOT = Path(__file__).resolve().parents[1]


def leak_step_one_registers_a_backend_and_leaves_it():
    """Deliberately does NOT restore. That is the point."""
    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    register_backend(ToolRegistry(), _LEAKED, "full")
    assert backends._REGISTERED == _LEAKED, (
        "registering no longer sets the global, so this file guards a "
        "mechanism that has changed")


def leak_step_two_does_not_inherit_it():
    assert backends._REGISTERED != _LEAKED, (
        "the backend registered by the previous test survived into this "
        "one. The autouse _restore_active_backend fixture in conftest is "
        "missing or no longer autouse, and every test after a registering "
        "one now resolves against the wrong backend")


def test_the_leak_pair_passes_in_order():
    """Both steps, one process, file order, nothing to shuffle them."""
    here = Path(__file__).relative_to(_ROOT).as_posix()
    env = dict(os.environ)
    env.pop("EDA_AGENT_BACKEND", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", here, "-q", "--no-header",
         "-o", "python_functions=leak_step_*",
         "-p", "no:xdist", "-p", "no:randomly", "-p", "no:random_order",
         "-p", "no:cacheprovider"],
        cwd=str(_ROOT), env=env, capture_output=True, text=True,
        timeout=300)
    assert result.returncode == 0, (
        "the leak pair failed in a fixed order, so a backend registered "
        "in one test reaches the next:\n" + result.stdout[-2000:])
    # Two, not "passed". If the prefix drifted and neither step was
    # collected, the run would still exit clean with nothing checked.
    assert "2 passed" in result.stdout, (
        "the leak pair did not run as two steps:\n" + result.stdout[-2000:])
