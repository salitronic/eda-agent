# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The release verification risk table must agree with the Pascal.

``docs/RELEASE_VERIFICATION.md`` opens with a table rating each step by
whether the Altium property it writes is already written somewhere in
shipped code. That column is the whole basis for the ranking: a property
that works elsewhere cannot be an undeclared identifier, which is the
failure that halts the polling loop and the reason the ranking exists.

The table is triage. Someone with one Altium session open reads it to
decide what to run first, so a wrong entry spends a scarce live session
on the wrong step.

This is a third kind of claim, after the numbers and the file paths that
other tests here check: a claim ABOUT CODE, asserted in prose. It went
wrong exactly once and in the direction this test now blocks. The table
said ``MoveByXY`` was written nowhere, while ``PCB_ReplicateLayout``
called it at PCB.pas:1907 and the code comment in ``Lib_Link3DModel``
said so. Prose and code disagreed with nothing forcing them to agree.

The table changes every release. That is intended: rewriting it should
mean re-verifying it, and a row this test cannot parse is a failure
rather than a skip, since silently checking nothing is the failure mode
these guards exist to prevent.

**What this cannot catch.** A step that writes several properties gets
one row each, and dropping one of them leaves the step still ranked, so
the omission passes. Closing that would mean this test holding its own
list of which properties each step writes, which is the duplication the
test exists to remove. Verified by mutation: deleting the whole row for
a step is caught, deleting one of a step's several rows is not.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "RELEASE_VERIFICATION.md"
_PAS_DIR = _ROOT / "scripts" / "altium"

# The concatenated build output duplicates every module, so counting it
# would double every site and turn "one module" into "two".
_BUILD_OUTPUT = "Altium_MCP.pas"

_ROW = re.compile(r"^\|\s*(\d+),([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|\s*$")
_IDENT = re.compile(r"`(\w+)`")
# Claims cite either a routine or a module file, so this one allows the
# dot. The property column never needs it.
_CITED = re.compile(r"`([\w.]+)`")


def _modules() -> dict[str, list[str]]:
    return {
        p.name: p.read_text(encoding="utf-8", errors="replace").splitlines()
        for p in sorted(_PAS_DIR.glob("*.pas"))
        if p.name != _BUILD_OUTPUT
    }


def _use_sites(ident: str) -> list[tuple[str, int, str]]:
    """Where `ident` is exercised, ignoring comments.

    Three shapes count, because the table's column asks whether shipped
    code already depends on the identifier resolving:

    * ``X.Prop :=``   a property write
    * ``X.Method(``   a method call on an object
    * ``X.Method;``   a call with no arguments, which Pascal writes
                      without parentheses (also before ``Then``)
    * ``Func(``       a plain call, which is how the ``StrTo*``
                      converters are used

    The declaration itself is excluded. A routine that exists but is
    called nowhere would otherwise count as evidence that it works,
    which is the trap that made an earlier guard in this suite point at
    dead code.
    """
    # An indexed property, X.Prop[Layer] :=, counts like X.Prop :=.
    member = re.compile(r"\.\s*" + re.escape(ident) + r"(?:\[[^\]]*\])?"
                        + r"(?:\s*(?::=|\(|;)|\s+Then\b)", re.I)
    plain = re.compile(r"(?<![.\w])" + re.escape(ident) + r"\s*\(")
    declares = re.compile(r"\s*(?:Function|Procedure)\s+" + re.escape(ident)
                          + r"\b", re.I)
    out: list[tuple[str, int, str]] = []
    for name, lines in _modules().items():
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("{"):
                continue
            if declares.match(line):
                continue
            if member.search(line) or plain.search(line):
                out.append((name, lineno, stripped[:70]))
    return out


def _table_body() -> list[str]:
    """The risk table's data lines, located by its header.

    Scanning the whole document for anything pipe-shaped would let a row
    that stopped matching disappear into the gap between "not a row" and
    "not in the table". Anchoring on the header means an unparseable row
    is still a row, and fails.
    """
    lines = _DOC.read_text(encoding="utf-8").splitlines()
    header = None
    for i, line in enumerate(lines):
        if line.startswith("| Step ") and "Written elsewhere" in line:
            header = i
            break
    assert header is not None, (
        f"no risk-table header found in {_DOC.name}. It was renamed or "
        "removed, and every check in this file reads that table.")
    body = []
    for line in lines[header + 2:]:          # +2 skips the |---| rule
        if not line.startswith("|"):
            break
        body.append(line)
    return body


def _rows() -> list[tuple[str, list[str], str]]:
    """(step, identifiers, claim) for each risk-table row.

    Every line in the table body must parse. A row this cannot read is
    an error, not something to pass over, because skipping is
    indistinguishable from approving.
    """
    rows = []
    for line in _table_body():
        m = _ROW.match(line)
        assert m, (
            f"could not parse this risk-table row:\n  {line}\n"
            "Expected: | <step>, <label> | <props> | <claim> | <risk> |")
        step, _label, props, claim, _risk = m.groups()
        rows.append((step, _IDENT.findall(props), claim.strip()))
    return rows


def test_every_step_that_writes_a_property_is_ranked():
    """Guard the guard, against a count rather than a magic number.

    Every other assertion here iterates the parsed rows, so a table that
    lost rows, or that this parser stopped recognising, would make the
    whole file pass while verifying less and less. Tying the table to
    the document's own step headings means a step added later cannot be
    left out of the ranking, and a row deleted from the table is caught
    by the step it abandoned.
    """
    rows = _rows()
    for step, idents, claim in rows:
        assert idents, f"step {step} row names no property in backticks"
        assert claim, f"step {step} row has an empty claim"

    ranked = {step for step, _, _ in rows}
    for step, title in _step_headings():
        if step in _STEPS_WITHOUT_A_PROPERTY:
            assert step not in ranked, (
                f"step {step} ({title}) is ranked in the risk table but "
                "is listed here as writing no Altium property. One of "
                "the two is now wrong.")
            continue
        assert step in ranked, (
            f"step {step} ({title}) writes an Altium property but has no "
            "row in the risk table, so nothing rates its risk and "
            "nothing checks its claim against the Pascal.")


# Step 0 pings the bridge, step 1 runs SelfTest, and steps 8 and 9 only
# READ (violations, and a symbol's pins). None writes an Altium property,
# so none can carry the wrong-value risk the table ranks. Every other
# step must appear.
#
# Read-only does NOT mean risk-free, and step 8 is the clearest case:
# it calls DM_PrimaryCrossProbeString, and an undeclared identifier
# faults where Try/Except cannot catch it and halts the polling loop.
# That risk is recorded in the step's own "If it fails" paragraph. What
# this table rates is a property written with a wrong value, which is a
# different failure and one a read-only step cannot produce.
#
# Step 10 (UNC paths) is also read-only: it opens a library through a
# UNC path with lib_get_components. The change it verifies is a pure
# DELETION of a StringReplace, so there is no new property write to
# rank; the risk it carries (a mangled path) is the very thing the
# step observes directly.
#
# Step 21 closes a document with Client.CloseDocument, a method call, not
# a property write; its risk (an undeclared identifier) is in the step.
#
# Step 19 (the layout model read) only reads. Its undeclared-identifier
# risk is listed in the step itself, identifier by identifier, for the
# same reason step 8's is.
#
# Step 30 (footprint validation) only reads a footprint's primitives; the
# properties it reads are listed in the step with where each is already read.
#
# Step 58 (phase gates, SDK coverage, LTspice, policies, Altium 365) is
# Python only: it reads Altium through commands other steps rank and
# writes no property.
#
# Step 59 (the Saving switch) writes the dashboard's saving.json and makes
# the save paths refuse; it sets no Altium property.
#
# Step 61 (items on every tool) runs existing handlers in one request and
# one undo step; every property it reaches is ranked under its own step.
_STEPS_WITHOUT_A_PROPERTY = {"0", "1", "8", "9", "10", "19", "21", "24",
                             "30", "58", "59", "61"}


def _step_headings() -> list[tuple[str, str]]:
    out = []
    for line in _DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(\d+)\.\s+(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    assert out, f"no numbered step headings found in {_DOC.name}"
    return out


def test_every_named_property_exists_in_the_pascal():
    """A property nothing writes is a typo, whatever the claim says."""
    for step, idents, _claim in _rows():
        for ident in idents:
            assert _use_sites(ident), (
                f"step {step} names `{ident}`, which is never used "
                f"anywhere in {_PAS_DIR}. Either the table has a "
                "typo or the feature was removed.")


def test_written_nowhere_claims_are_true():
    """"no, nowhere" must mean exactly one module writes it.

    One module is the new code itself. Two or more means the property is
    already exercised, so the step is checking a call site rather than
    an unproven API, and it is ranked too high.
    """
    for step, idents, claim in _rows():
        if not claim.lower().startswith("no"):
            continue
        for ident in idents:
            sites = _use_sites(ident)
            modules = sorted({name for name, _, _ in sites})
            assert len(modules) == 1, (
                f"step {step} claims `{ident}` is written {claim!r}, but "
                f"{len(modules)} modules write it: {modules}. "
                + "; ".join(f"{n}:{i}" for n, i, _ in sites[:4]))


def test_claims_naming_a_writer_name_a_real_one():
    """"yes, `PCB_MakePasteGrid`" must really write it, and there.

    Citing a specific writer is the strongest form of the claim and the
    easiest to leave behind after a rename, so it is checked hardest:
    the named routine must exist AND the use must fall inside it. A
    claim naming a module instead is held to the weaker version of the
    same rule, that the module exists and uses the identifier.
    """
    for step, idents, claim in _rows():
        for cited in _CITED.findall(claim):
            if cited.endswith(".pas"):
                _assert_module_uses(step, cited, idents, claim)
            else:
                _assert_function_writes(step, cited, idents, claim)


def _assert_module_uses(step, module, idents, claim):
    modules = _modules()
    assert module in modules, (
        f"step {step} cites {claim!r}, but {module} is not in {_PAS_DIR}")
    for ident in idents:
        if any(name == module for name, _, _ in _use_sites(ident)):
            return
    pytest.fail(
        f"step {step} cites {claim!r}, but {module} uses none of {idents}")


def _assert_function_writes(step, func, idents, claim):
    for name, lines in _modules().items():
        start = None
        for lineno, line in enumerate(lines, 1):
            if re.match(r"\s*(?:Function|Procedure)\s+" + func + r"\b",
                        line, re.I):
                start = lineno
                break
        if start is None:
            continue
        end = _end_of_routine(lines, start)
        for ident in idents:
            inside = [i for n, i, _ in _use_sites(ident)
                      if n == name and start <= i <= end]
            if inside:
                return
        pytest.fail(
            f"step {step} cites {claim!r}, and {func} exists at "
            f"{name}:{start}, but none of {idents} is written between "
            f"lines {start} and {end}")
    pytest.fail(
        f"step {step} cites {claim!r}, but no routine named {func} exists "
        f"in {_PAS_DIR}. It was probably renamed.")


def _end_of_routine(lines: list[str], start: int) -> int:
    """Line number where the routine beginning at `start` ends.

    The next routine header, or end of file. Good enough to attribute a
    write, and it cannot silently widen: a missed header would only make
    the window larger, which this test's callers treat as a pass, so the
    window is also bounded by the following header rather than by brace
    counting, which DelphiScript comments would confuse.
    """
    for lineno in range(start + 1, len(lines) + 1):
        if re.match(r"\s*(?:Function|Procedure)\s+\w+", lines[lineno - 1],
                    re.I):
            return lineno - 1
    return len(lines)
