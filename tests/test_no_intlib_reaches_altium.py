# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An .IntLib path must never reach Altium's library layer.

``SchServer.CreateLibCompInfoReader`` and
``SchServer.LoadComponentFromLibrary`` both accept an ``.IntLib`` path and
then raise Altium's "Open Integrated Library" modal.  Nothing in
DelphiScript can catch that dialog, and it stops the polling loop until a
human clicks it.  Reported from the field on 2026-09-21 as a dialog storm
that reappeared once per library and had to be cleared with "Apply to all
libraries" before the bridge answered again.

The reason this is not caller error: a PLACED component records its
source library as the ``.IntLib``.  So every path that READS a design
reports ``.IntLib``, and every path that WRITES one needs ``.SchLib``.
Reading produced exactly what writing rejected, and the only symptom was
``LOAD_FAILED`` with no indication why.

Every call must therefore go through ``SafeSchLibPath`` at the call site,
or sit in a function that has already called ``ResolveSchLibForLoad``.

This iterates EVERY call site rather than a sampled few.  The bug was
originally fixed at seven of eleven call sites, because a grep for one
variable name missed the four that spelled it differently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts" / "altium"

#: Altium entry points that open a library by path and can raise the modal.
RISKY_CALLS = (
    "CreateLibCompInfoReader",
    "LoadComponentFromLibrary",
)

#: The two ways a call site is allowed to be safe.
GUARDS = ("SafeSchLibPath", "ResolveSchLibForLoad")

#: Pascal units that talk to the library layer.
UNITS = ("Library.pas", "Generic.pas")


def _functions_with_risky_calls():
    """``(unit, func_name, body)`` for every function that opens a library."""
    found = []
    for unit in UNITS:
        for name, body in load(SCRIPTS / unit).items():
            if any(call + "(" in body for call in RISKY_CALLS):
                found.append((unit, name, body))
    return found


def test_the_scan_finds_the_call_sites_at_all():
    """A guard that matches nothing passes over nothing.

    If the loader or the call names drift, every assertion below becomes
    vacuous, so pin a floor on what the scan must see.
    """
    hits = _functions_with_risky_calls()
    assert len(hits) >= 6, (
        f"only {len(hits)} functions found calling {RISKY_CALLS}; the "
        f"Pascal loader or the API names have changed and this guard is "
        f"no longer checking anything")


def _assigned_from_resolver(var: str, body: str) -> bool:
    """Was ``var`` assigned the output of the resolver in this function?

    Accepts either resolver name, since ``SafeSchLibPath`` is itself a
    thin wrapper over ``ResolveSchLibForLoad``.
    """
    if not re.fullmatch(r"\w+", var):
        return False
    return bool(re.search(
        re.escape(var) + r"\s*:=\s*(SafeSchLibPath|ResolveSchLibForLoad)\s*\(",
        body))


def _risky_call_sites():
    """``(unit, func, call, argument)`` for every individual call.

    PER CALL, not per function. Checking that the enclosing function
    mentions a guard somewhere passes a function with two call sites where
    only one was fixed, which is exactly what happened: reverting
    ``SafeSchLibPath(PathA)`` in Lib_DiffLibraries was invisible because
    ``SafeSchLibPath(PathB)`` two lines later satisfied the check.
    """
    sites = []
    for unit, name, body in _functions_with_risky_calls():
        for call in RISKY_CALLS:
            for m in re.finditer(
                    re.escape(call) + r"\(([^;]*?)\)\s*;", body):
                sites.append((unit, name, call, m.group(1)))
    return sites


@pytest.mark.parametrize(
    "unit,func,call,arg",
    _risky_call_sites(),
    ids=[f"{u}:{n}:{c}:{a[:24]}" for u, n, c, a in _risky_call_sites()])
def test_every_library_open_is_resolved_first(
        unit: str, func: str, call: str, arg: str) -> None:
    """No single call may open a library from an unresolved path."""
    body = dict(
        (n, b) for u, n, b in _functions_with_risky_calls() if u == unit
    )[func]

    if call == "CreateLibCompInfoReader":
        # Two legitimate shapes: wrapped at the call site, or a variable
        # this function already assigned from the resolver. ResolveLibRef
        # does the latter, and demanding the wrapper there would be asking
        # it to resolve an already-resolved path.
        if "SafeSchLibPath(" in arg:
            return
        assert _assigned_from_resolver(arg.strip(), body), (
            f"{unit}:{func} calls {call}({arg.strip()}) on an unresolved "
            f"path. Either wrap it as {call}(SafeSchLibPath({arg.strip()})) "
            f"or assign {arg.strip()} from ResolveSchLibForLoad first. An "
            f".IntLib reaching Altium here raises a modal that no "
            f"Try/Except can catch and that stops the polling loop until "
            f"someone clicks it.")
        return

    # LoadComponentFromLibrary(LibRef, LibPath): the path variable must
    # have been reassigned from the resolver earlier in this function.
    parts = [p.strip() for p in arg.split(",")]
    assert len(parts) == 2, f"unexpected {call} signature: {arg!r}"
    path_var = parts[1]
    assert _assigned_from_resolver(path_var, body) or re.search(
        re.escape(path_var) + r"\s*:=\s*Resolved", body), (
        f"{unit}:{func} calls {call} with {path_var}, which is never "
        f"reassigned from ResolveSchLibForLoad in this function. The path "
        f"reaching Altium may still be an .IntLib, which raises the "
        f"uncatchable modal.")


def test_resolver_refuses_intlib_rather_than_passing_it_through():
    """The unresolvable .IntLib case must return empty, not the input.

    Returning the original path would hand Altium the very thing that
    raises the modal, which is the failure this whole file exists to
    prevent. Every caller already handles a Nil reader.
    """
    utils = (SCRIPTS / "Utils.pas").read_text(encoding="utf-8", errors="replace")
    body = load(SCRIPTS / "Utils.pas")["SafeSchLibPath"]
    assert re.search(r"\.INTLIB'\s*Then\s*Result\s*:=\s*''", body,
                     re.IGNORECASE), (
        "SafeSchLibPath must return '' for an .IntLib it cannot resolve. "
        "Passing the .IntLib through is what raises the modal.")
    assert "ResolveSchLibForLoad" in utils


def test_resolver_probes_the_documented_extraction_locations():
    """Mapping .IntLib to .SchLib must match where Altium writes them.

    ``Lib_ExtractIntLib`` already encodes the convention: a folder named
    after the IntLib base, then beside the IntLib itself. If the resolver
    and the extractor disagree, extracting sources and then placing from
    them fails for reasons nobody will guess.
    """
    body = load(SCRIPTS / "Utils.pas")["ResolveSchLibForLoad"]
    assert "FileExists" in body, (
        "candidates must be checked on disk, not handed to Altium to "
        "probe: probing via Altium is what raises the modal")
    assert body.count(".SchLib") >= 2, (
        "both extraction locations must be tried; Lib_ExtractIntLib probes "
        "a sibling folder AND the IntLib's own directory")
