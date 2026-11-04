# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A handler that catches everything and carries on says why on its line.

``except Exception: pass`` (or a bare ``except``, or one that returns a
constant) turns any failure into a normal-looking answer: a dropped
connection reads as an empty board, a failed save as a saved one. Either
catch what the code expects (``AttributeError``, ``OSError``...) or put
the reason on the ``except`` line as a comment.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "eda_agent"
BROAD = {"Exception", "BaseException"}


def _broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    return any(isinstance(t, ast.Name) and t.id in BROAD for t in types)


def _literal(node) -> bool:
    return node is None or not any(
        isinstance(n, (ast.Name, ast.Call, ast.Attribute)) for n in ast.walk(node))


def _quiet(stmt: ast.stmt) -> bool:
    """Does nothing with the error: pass, continue, or a constant result."""
    if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(stmt, (ast.Return, ast.Assign)):
        return _literal(stmt.value)
    return False


def silent_handlers(source: str) -> list[int]:
    lines = source.splitlines()
    return [h.lineno for h in ast.walk(ast.parse(source))
            if isinstance(h, ast.ExceptHandler) and _broad(h)
            and all(_quiet(s) for s in h.body)
            and "#" not in lines[h.lineno - 1]]


def test_every_silent_broad_handler_states_its_reason():
    found = [f"{path.relative_to(SRC.parent)}:{line}"
             for path in sorted(SRC.rglob("*.py"))
             for line in silent_handlers(path.read_text(encoding="utf-8"))]
    assert not found, (
        "these swallow every error without saying why; catch the expected "
        "exception, report the failure, or state the reason on the except "
        "line:\n  " + "\n  ".join(found))


def test_the_check_sees_what_it_is_for():
    caught = silent_handlers(
        "try:\n    x()\nexcept Exception:\n    pass\n"
        "try:\n    x()\nexcept:\n    return None\n"
        "try:\n    x()\nexcept (OSError, Exception):\n    y = []\n")
    assert caught == [3, 7, 11]
    passed = silent_handlers(
        "try:\n    x()\nexcept Exception:  # reason\n    pass\n"
        "try:\n    x()\nexcept OSError:\n    pass\n"
        "try:\n    x()\nexcept Exception as e:\n    log(e)\n")
    assert passed == []
