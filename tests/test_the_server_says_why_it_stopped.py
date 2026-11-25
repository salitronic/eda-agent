# SPDX-License-Identifier: Apache-2.0
"""A server that stops on an error says so and exits non-zero.

serve_mcp leaves through os._exit, which skips the traceback. An error in
the MCP loop used to end the process with exit code 0 and no message.
"""
from __future__ import annotations

import inspect
import logging
import re

from eda_agent import server


def test_a_clean_end_is_exit_code_zero():
    assert server._run_stdio(lambda: None) == 0


def test_an_error_is_logged_with_its_traceback_and_is_exit_code_one(caplog):
    def run():
        raise RuntimeError("the transport broke")

    with caplog.at_level(logging.ERROR):
        assert server._run_stdio(run) == 1
    record = next(r for r in caplog.records if "stopped on an error" in r.getMessage())
    assert record.exc_info and "the transport broke" in str(record.exc_info[1])


def test_a_keyboard_interrupt_is_not_swallowed():
    def run():
        raise KeyboardInterrupt

    try:
        server._run_stdio(run)
    except KeyboardInterrupt:
        return
    raise AssertionError("Ctrl+C must reach the caller")


def test_serve_leaves_with_the_loops_exit_code():
    body = inspect.getsource(server.serve_mcp)
    assert "_run_stdio(" in body
    assert re.search(r"_os\._exit\(code\)", body), "the exit code of the loop is the process's"
    assert "_os._exit(0)" not in body
