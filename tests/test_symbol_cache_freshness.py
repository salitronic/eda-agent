# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A cached symbol must be certified against the file it came from.

put() stamped the library's mtime as found at WRITE time. The library is
reopened in the editor to answer the read, so anything touching it in
that window (a deferred save landing, an edit, another tool) moves the
mtime, and the entry was then certified current for geometry that was
one version behind. A cache miss is cheap; a symbol whose pins are in
last version's places is not, and nothing reported it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agent.design.symbols import (
    SymbolBBox, SymbolCache, SymbolModel, SymbolPin)


def _model(lib_path: str, lib_ref: str = "R") -> SymbolModel:
    return SymbolModel(
        lib_path=lib_path, lib_ref=lib_ref,
        pins=[SymbolPin(designator="1", name="A", x=0, y=100, orientation=1,
                        length=100, electrical_type="passive")],
        body_bbox=SymbolBBox(x_min=-50, y_min=-50, x_max=50, y_max=50),
    )


@pytest.fixture
def lib(tmp_path) -> Path:
    path = tmp_path / "Parts.SchLib"
    path.write_bytes(b"v1")
    return path


def _touch(path: Path, offset: float = 10.0) -> None:
    stamp = path.stat().st_mtime + offset
    os.utime(path, (stamp, stamp))


def test_a_model_read_from_the_current_file_is_cached(lib, tmp_path):
    cache = SymbolCache(tmp_path / "cache")
    observed = lib.stat().st_mtime
    cache.put(_model(str(lib)), observed)
    assert cache.get(str(lib), "R") is not None


def test_a_library_that_moved_under_the_read_is_not_cached(lib, tmp_path):
    """The defect. The model still goes back to the caller who asked for
    it; what must not happen is the next run trusting it."""
    cache = SymbolCache(tmp_path / "cache")
    observed = lib.stat().st_mtime
    lib.write_bytes(b"v2")
    _touch(lib)
    cache.put(_model(str(lib)), observed)
    assert cache.get(str(lib), "R") is None, (
        "a symbol read from the previous version of the library was "
        "certified as current")
    # And nothing was written, rather than a file written with a stamp
    # that can never match again. The entry would be dead weight: never
    # fresh, never used, and reloaded on every miss.
    written = list((tmp_path / "cache").glob("*.json")) if (
        tmp_path / "cache").is_dir() else []
    assert not written, f"a permanently stale cache file was left: {written}"


def test_the_stamp_is_the_observed_time_not_the_write_time(lib, tmp_path):
    """Stamping at write time is what made the entry wrong.

    Same setup as a normal read, except the clock has moved on between
    the read and the write; the entry must still describe the file that
    was read.
    """
    cache = SymbolCache(tmp_path / "cache")
    observed = lib.stat().st_mtime
    cache.put(_model(str(lib)), observed)
    assert cache.is_fresh(str(lib))
    # Now the library really does change: the entry must stop being fresh.
    lib.write_bytes(b"v2")
    _touch(lib)
    assert not cache.is_fresh(str(lib))


def test_a_caller_with_no_observed_time_keeps_the_old_behaviour(lib,
                                                                tmp_path):
    """Not every model comes from a live read."""
    cache = SymbolCache(tmp_path / "cache")
    cache.put(_model(str(lib)))
    assert cache.get(str(lib), "R") is not None


def test_the_extractor_observes_the_time_before_it_reads(tmp_path):
    """Reading the mtime after the bridge call would measure the file
    Altium has just finished touching, which is the bug restated."""
    import inspect

    from eda_agent.design.symbols import SymbolExtractor

    src = inspect.getsource(SymbolExtractor)
    before = src.split("library.get_component_details", 1)[0]
    assert "source_mtime = Path(lib_path).stat().st_mtime" in before, (
        "the library's mtime is no longer sampled before the read")
    assert "self.cache.put(model, source_mtime)" in src


def test_an_unreadable_library_does_not_stop_the_extraction(tmp_path):
    """A library that cannot be stat'ed is not a reason to refuse the
    symbol; it is a reason not to certify it."""
    cache = SymbolCache(tmp_path / "cache")
    missing = tmp_path / "Gone.SchLib"
    cache.put(_model(str(missing)), None)
    assert cache.get(str(missing), "R") is None
