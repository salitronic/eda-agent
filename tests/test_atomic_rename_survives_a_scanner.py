# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A temp-then-rename must survive Windows holding the target open.

Every durable write here stages to a temp file and renames over the
destination. On Windows a scanner (Defender, a sync client, an indexer)
can hold the freshly-written TARGET for a few milliseconds and
``os.replace`` fails with PermissionError. Measured on this machine at
two failures in eight tight renames.

That rate is survivable once and fatal in a loop. SymbolCache.put()
rewrites its library cache file once per symbol, so a 38-symbol
extraction made 38 attempts at the same target, and the unretried
exception propagated out of design_execute_plan and killed the run.

The fix is one helper, because the sites disagree about what a FINAL
failure means and that disagreement is the whole design: a cache entry
is skippable, a bridge request file is not.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from eda_agent import atomicfile

_SRC = Path(__file__).resolve().parents[1] / "src" / "eda_agent"


class _Sticky:
    """os.replace that fails PermissionError the first N times."""

    def __init__(self, failures: int, real=os.replace):
        self.left = failures
        self.calls = 0
        self._real = real

    def __call__(self, tmp, target):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise PermissionError(5, "Access is denied")
        return self._real(tmp, target)


@pytest.fixture()
def no_sleep(monkeypatch):
    """The retry must not actually wait during tests."""
    slept: list[float] = []
    monkeypatch.setattr(atomicfile.time, "sleep", slept.append)
    return slept


def test_a_rename_that_loses_twice_still_lands(tmp_path, monkeypatch, no_sleep):
    """The measured case: two failures in a row, then success."""
    tmp = tmp_path / "x.tmp"
    target = tmp_path / "x.json"
    tmp.write_text("payload", encoding="utf-8")

    sticky = _Sticky(2)
    monkeypatch.setattr(atomicfile.os, "replace", sticky)
    atomicfile.replace_with_retry(tmp, target)

    assert target.read_text(encoding="utf-8") == "payload"
    assert sticky.calls == 3
    assert len(no_sleep) == 2


def test_the_common_case_does_not_sleep_at_all(tmp_path, no_sleep):
    tmp = tmp_path / "x.tmp"
    tmp.write_text("payload", encoding="utf-8")
    atomicfile.replace_with_retry(tmp, tmp_path / "x.json")
    assert no_sleep == []


def test_a_target_held_forever_raises(tmp_path, monkeypatch, no_sleep):
    """It gives up, and it gives up LOUDLY. Callers that want to
    degrade catch this; deciding for them here would silently drop a
    bridge request or a checkpoint blob."""
    tmp = tmp_path / "x.tmp"
    tmp.write_text("payload", encoding="utf-8")
    monkeypatch.setattr(atomicfile.os, "replace", _Sticky(10 ** 6))

    with pytest.raises(PermissionError):
        atomicfile.replace_with_retry(tmp, tmp_path / "x.json")


def test_every_backoff_entry_is_a_wait_before_a_real_attempt(
        tmp_path, monkeypatch, no_sleep):
    """A loop that sleeps after its last attempt burns the longest delay
    for nothing. Attempts must be one more than sleeps."""
    tmp = tmp_path / "x.tmp"
    tmp.write_text("payload", encoding="utf-8")
    sticky = _Sticky(10 ** 6)
    monkeypatch.setattr(atomicfile.os, "replace", sticky)

    with pytest.raises(PermissionError):
        atomicfile.replace_with_retry(tmp, tmp_path / "x.json")

    assert sticky.calls == len(no_sleep) + 1


def test_the_backoff_outlasts_the_window_it_is_for(no_sleep):
    """A scanner holds the file for a few ms. Retrying for under that is
    theatre; retrying for a minute hangs a caller behind a genuinely
    locked file."""
    total = sum(atomicfile._BACKOFF)
    assert 0.5 < total < 5.0, f"total retry window is {total}s"
    assert list(atomicfile._BACKOFF) == sorted(atomicfile._BACKOFF), (
        "the backoff must rise, so the common case costs one short sleep")


def test_a_sharing_violation_is_retried_too(tmp_path, monkeypatch, no_sleep):
    """WinError 32 (sharing violation) and WinError 5 (access denied) are
    both PermissionError in Python, and both are this race."""
    tmp = tmp_path / "x.tmp"
    target = tmp_path / "x.json"
    tmp.write_text("payload", encoding="utf-8")

    real = os.replace
    state = {"n": 0}

    def sharing_violation(a, b):
        state["n"] += 1
        if state["n"] == 1:
            raise PermissionError(32, "The process cannot access the file")
        return real(a, b)

    monkeypatch.setattr(atomicfile.os, "replace", sharing_violation)
    atomicfile.replace_with_retry(tmp, target)
    assert target.exists()


def test_an_unrelated_oserror_is_not_swallowed(tmp_path, monkeypatch, no_sleep):
    """A missing temp file or a bad path is a real bug, not a race, and
    retrying it wastes a second before reporting the same thing."""
    tmp = tmp_path / "x.tmp"
    tmp.write_text("payload", encoding="utf-8")

    def gone(a, b):
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr(atomicfile.os, "replace", gone)
    with pytest.raises(FileNotFoundError):
        atomicfile.replace_with_retry(tmp, tmp_path / "x.json")
    assert no_sleep == []


def test_discard_removes_the_abandoned_temp(tmp_path):
    tmp = tmp_path / "x.tmp"
    tmp.write_text("payload", encoding="utf-8")
    atomicfile.discard(tmp)
    assert not tmp.exists()
    atomicfile.discard(tmp)          # already gone, must not raise


def _code_only(body: str) -> str:
    """Drop comments, so a check for a keyword cannot match the prose
    explaining that keyword. The first version of the test below found
    the word "raises" in put()'s own comment and failed on correct code.
    """
    out = []
    for line in body.splitlines():
        stripped = line.split("#", 1)[0]
        if stripped.strip():
            out.append(stripped)
    return chr(10).join(out)


# --- the sites -----------------------------------------------------------

def _rename_calls(path: Path) -> list[str]:
    """Every os.replace / Path.replace / os.rename in a module.

    Read from the AST rather than by regex, because ``.replace(`` is
    overwhelmingly str.replace in this codebase and a text search cannot
    tell the two apart.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("replace", "rename"):
            # os.replace(a, b) / os.rename(a, b): two positional args.
            # tmp.replace(target): one. str.replace takes two but is
            # called on a string expression, so filter on the receiver
            # being a name that looks like a path.
            recv = f.value
            name = getattr(recv, "id", None) or getattr(recv, "attr", "")
            if f.attr == "replace" and len(node.args) == 1:
                if re.search(r"tmp|temp", str(name), re.I):
                    out.append(f"{path.name}:{node.lineno} {name}.replace(...)")
            elif isinstance(recv, ast.Name) and recv.id in ("os", "shutil", "_os"):
                out.append(f"{path.name}:{node.lineno} {recv.id}.{f.attr}(...)")
    return out


def test_no_module_renames_a_temp_file_without_the_retry():
    """The guard against the next one being written the old way.

    Scoped to renames whose source reads as a temp file, which is the
    shape of the atomic-write idiom here.
    """
    offenders = []
    checked = 0
    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "atomicfile.py":
            continue          # the helper is where os.replace belongs
        checked += 1
        offenders.extend(_rename_calls(path))

    assert checked > 50, f"only scanned {checked} modules; this guard is blind"
    assert not offenders, (
        "these rename a staged temp file without replace_with_retry, so a "
        "Windows scanner holding the target takes them down:" + chr(10)
        + chr(10).join(offenders))


def test_the_bridge_request_publish_does_not_swallow_a_failed_rename():
    """The most consequential site. A request that is never published
    leaves the caller waiting out the full poll timeout with nothing to
    explain the silence, so this one must raise."""
    src = (_SRC / "bridge" / "altium_bridge.py").read_text(encoding="utf-8")
    body = src.split("def _publish_request", 1)[1].split(chr(10) + "    def ", 1)[0]
    # Comments AND the docstring go, for the same reason as below: the
    # comment beside this code says the word "raise", and a containment
    # check on the raw text passed while the raise itself was deleted.
    body = body.split('"""', 2)[-1]
    code = _code_only(body)
    assert "replace_with_retry" in code
    assert re.search(r"^\s*raise\s*$", code, re.M), (
        "the bridge request publish is swallowing a failed rename; the "
        "command would never reach Altium and nothing would say so")


def test_the_symbol_cache_does_swallow_one():
    """The opposite decision, deliberately. A cache is an optimisation:
    losing a whole extraction run to a rename race is never the trade."""
    src = (_SRC / "design" / "symbols.py").read_text(encoding="utf-8")
    body = src.split("    def put(", 1)[1].split(chr(10) + "    def ", 1)[0]
    code = _code_only(body)
    assert "except PermissionError:" in code
    assert "discard(tmp)" in code, (
        "the abandoned .tmp is left beside the cache file")
    assert not re.search(r"\braise\b", code), (
        "put() propagating again is the defect that killed a 38-symbol "
        "design_execute_plan run")
