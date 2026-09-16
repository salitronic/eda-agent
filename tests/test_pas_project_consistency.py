# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Cross-touchpoint consistency for the Pascal source set.

Three parallel surfaces reference the .pas files in scripts/altium/:

  - scripts/altium/build.py FILES list: defines what gets concatenated
    into Altium_MCP.pas, the deployed bundle.

  - scripts/altium/Altium_API.PrjScr [DocumentN] sections: defines
    which files are openable in Altium's scripting IDE for debugging.

  - The .pas files themselves on disk.

If any pair drift apart, a real workflow breaks:
  - .pas on disk but missing from build.py -> handler exists but
    bundle deployment skips it (the bug fixed in iteration #65 for
    the parallel audit lists, mirrored on the Pascal side).
  - .pas in build.py but missing from PrjScr -> bundle works in
    production but devs can't open the file in Altium's IDE.

These tests are cheap, sync-checking only -- no Altium round-trip.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "altium"
BUILD_PY = SCRIPTS_DIR / "build.py"
PRJSCR = SCRIPTS_DIR / "Altium_API.PrjScr"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Pascal sources that are deliberately NOT in build.py's bundle list
# but ARE in the PrjScr for IDE-only use (debug harness, etc).
PRJSCR_ONLY = {"SelfTest.pas"}

# Pascal sources that are deliberately NOT in either list (build outputs,
# leftovers, etc).
EXCLUDED = {
    "Altium_MCP.pas",  # the bundle output itself
    "scratch.pas",     # legacy scratch file
    # .tmp.* leftovers ignored by file extension check below
}


def _build_py_files() -> list[str]:
    """Extract the FILES = [...] list from build.py."""
    text = BUILD_PY.read_text(encoding="utf-8")
    match = re.search(r"FILES\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match, "FILES list not found in build.py"
    return re.findall(r"'([A-Za-z0-9_.]+)'", match.group(1))


def _prjscr_document_paths() -> list[str]:
    """Extract every DocumentPath=X.pas / .dfm entry from the PrjScr."""
    text = PRJSCR.read_text(encoding="utf-8")
    return re.findall(r"^DocumentPath=([A-Za-z0-9_.]+)$", text,
                      flags=re.MULTILINE)


def _live_pas_files() -> set[str]:
    """Every real .pas in scripts/altium/ (excluding .tmp.* leftovers)."""
    return {
        p.name
        for p in SCRIPTS_DIR.glob("*.pas")
        if ".tmp." not in p.name and p.name not in EXCLUDED
    }


def _wheel_force_include_filenames() -> set[str]:
    """Extract just the file basenames from the wheel force-include map.

    pyproject.toml's [tool.hatch.build.targets.wheel.force-include] is a
    dict mapping source paths to destination paths. We only care that the
    bundled .pas / .dfm / .PrjScr files are listed (basename comparison).
    Parsed by regex since we can't assume tomllib at this Python version
    boundary and don't want to add a test-time dep.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    block_start = text.find("[tool.hatch.build.targets.wheel.force-include]")
    assert block_start >= 0, "force-include section not found in pyproject.toml"
    # Take everything until the next [section] header.
    rest = text[block_start:]
    next_section = rest.find("\n[", 1)
    block = rest[:next_section] if next_section > 0 else rest
    files = set()
    for m in re.finditer(
            r'"scripts/altium/([A-Za-z0-9_.]+)"\s*=', block):
        files.add(m.group(1))
    return files


def test_build_py_files_all_exist():
    """Every name in build.py FILES has a real .pas file on disk."""
    missing = [f for f in _build_py_files() if not (SCRIPTS_DIR / f).exists()]
    assert not missing, (
        f"build.py FILES references files that don't exist on disk: "
        f"{missing}"
    )


def test_prjscr_referenced_files_all_exist():
    """Every DocumentPath in the PrjScr names a real file on disk."""
    missing = [
        f for f in _prjscr_document_paths()
        if not (SCRIPTS_DIR / f).exists()
    ]
    assert not missing, (
        f"Altium_API.PrjScr references files that don't exist: {missing}"
    )


def test_build_py_files_are_in_prjscr():
    """Every file in build.py FILES is also in the PrjScr -- so devs
    can open the source in Altium's scripting IDE for debugging."""
    build_files = set(_build_py_files())
    prjscr_files = set(_prjscr_document_paths())
    missing_from_prjscr = build_files - prjscr_files
    assert not missing_from_prjscr, (
        f"build.py FILES references .pas files not in Altium_API.PrjScr "
        f"(devs won't be able to open them in the IDE for debugging): "
        f"{sorted(missing_from_prjscr)}. Add a [DocumentN] section."
    )


def test_prjscr_only_has_disk_pas_files():
    """PrjScr's .pas references are either in build.py FILES or in the
    known PRJSCR_ONLY exception set (debug-only files)."""
    build_files = set(_build_py_files())
    prjscr_pas = {f for f in _prjscr_document_paths() if f.endswith(".pas")}
    unexpected = prjscr_pas - build_files - PRJSCR_ONLY
    assert not unexpected, (
        f"PrjScr references .pas files that are neither in build.py FILES "
        f"nor in the PRJSCR_ONLY exception set: {sorted(unexpected)}. "
        f"Either add to build.py or add to PRJSCR_ONLY in this test."
    )


def test_wheel_force_include_covers_build_files():
    """Every file in build.py FILES is also in the wheel's force-include
    list -- a clean ``pip install`` followed by ``eda-agent install-scripts``
    will deploy them. Missing entries silently drop functionality from
    production wheels (the bug fixed in this iteration was Audit.pas +
    StatusForm.pas + StatusForm.dfm).
    """
    build_files = set(_build_py_files())
    wheel_files = _wheel_force_include_filenames()
    missing = build_files - wheel_files
    assert not missing, (
        f"build.py FILES not present in pyproject.toml wheel "
        f"force-include: {sorted(missing)}. End users running `pip install` "
        f"+ `eda-agent install-scripts` will be missing these handlers."
    )


def test_wheel_force_include_has_statusform_dfm():
    """The StatusForm.dfm Delphi form binary must ship in the wheel.
    Without it, StartMCPServer crashes with `unknown identifier` errors
    referencing the form's controls. .dfm is not auto-bundled because
    the file extension isn't a Python source type.
    """
    wheel_files = _wheel_force_include_filenames()
    assert "StatusForm.dfm" in wheel_files, (
        "StatusForm.dfm missing from wheel force-include. Without the .dfm "
        "the form's child controls are undeclared at compile time."
    )


def test_wheel_force_include_covers_everything_the_prjscr_loads():
    """The PrjScr's list, not build.py's, is what Altium actually opens.

    The check above compares against build.py FILES, which is the BUNDLE
    list and is a strict subset: SelfTest.pas is PRJSCR_ONLY, so nothing
    asserted it reaches the wheel. Dropping it from force-include left
    every test in this file green while shipping a script project whose
    members are not all present.

    Altium refuses to open a project with a missing member, so this is
    not a degraded feature, it is the whole bridge failing to load for
    anyone who installed from a wheel rather than the repo.
    """
    referenced = set(_prjscr_document_paths())
    wheel_files = _wheel_force_include_filenames()
    missing = sorted(referenced - wheel_files)
    assert not missing, (
        f"Altium_API.PrjScr loads these but they are not in pyproject's "
        f"wheel force-include: {missing}. A pip-installed user gets a "
        f"script project referencing files the package does not contain."
    )


def test_the_project_file_itself_ships():
    """Without the .PrjScr there is nothing for the user to install.

    Every other check here is about the project's MEMBERS, so all of
    them pass while the project file itself is absent and
    ``eda-agent install-scripts`` deploys a directory of orphaned
    sources with no entry point.
    """
    assert "Altium_API.PrjScr" in _wheel_force_include_filenames(), (
        "Altium_API.PrjScr is not force-included, so install-scripts has "
        "no script project for the user to add to Altium."
    )


def test_force_include_has_no_entries_for_missing_files():
    """A stale entry aborts the wheel build rather than shipping wrong.

    Loud rather than silent, but it fails at release time on whoever is
    packaging, which is the worst moment to discover it. Cheap to catch
    here instead.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    missing = sorted(
        name for name in _wheel_force_include_filenames()
        if not (SCRIPTS_DIR / name).exists()
    )
    assert not missing, (
        f"pyproject force-include names files that do not exist: "
        f"{missing}. hatchling errors on these, so the wheel cannot be "
        f"built at all."
    )


#: Form controls referenced by StatusForm.pas that the .dfm does not
#: declare, with why each is tolerated. An entry is a known hole, not a
#: licence to add more.
UNDECLARED_CONTROLS = {
    "btn_ResetPerf":
        "Unfinished feature, not a typo. btn_ResetPerfClick calls "
        "ResetPerfStats and clears mmo_Perf, and hover styling was "
        "written for it, but the button was never added to the .dfm. "
        "Nothing wires the handlers, so they never run and the missing "
        "control is never touched; the counters still reset at form "
        "init (StatusForm.pas, the SetCheckCaption block). Either add "
        "the button to the .dfm or delete the three handlers. Left as "
        "is because both are UI decisions that want a look at the "
        "running form.",
}


def _declared_controls() -> set[str]:
    """Every ``object name: TType`` the form definition declares."""
    dfm = (SCRIPTS_DIR / "StatusForm.dfm").read_text(encoding="utf-8",
                                                     errors="replace")
    return set(re.findall(r"^\s*object\s+(\w+)\s*:", dfm, flags=re.MULTILINE))


def test_statusform_only_touches_controls_the_dfm_declares():
    """A handler reaching for a control the form lacks faults at runtime.

    The .dfm is what declares the controls, so a name that is not in it
    is an undeclared identifier. DelphiScript resolves late, so this
    does not fail to compile: it raises the moment that handler runs,
    inside the UI of the polling loop, which is a poor place to find
    out. Nothing else catches it, since the form is the one Pascal file
    no test can execute.

    Scoped to names matching the form's own control-naming prefixes so
    ordinary locals and API objects are not dragged in.
    """
    declared = _declared_controls()
    assert len(declared) > 30, (
        f"only {len(declared)} controls parsed from the .dfm; the parse "
        f"broke and this check has gone blind")

    source = (SCRIPTS_DIR / "StatusForm.pas").read_text(encoding="utf-8",
                                                        errors="replace")
    source = re.sub(r"\{[^{}]*\}", " ", source)
    source = re.sub(r"//[^\n]*", " ", source)

    prefixes = tuple(sorted({name.split("_")[0] + "_"
                             for name in declared if "_" in name}))
    assert prefixes, "no prefixed control names found in the .dfm"

    referenced = {m for m in re.findall(r"\b([A-Za-z_]\w*)\s*\.\s*[A-Z]\w*",
                                        source)
                  if m.startswith(prefixes)}
    unknown = sorted(referenced - declared - set(UNDECLARED_CONTROLS))
    assert not unknown, (
        f"StatusForm.pas uses these control names but StatusForm.dfm "
        f"does not declare them: {unknown}. Add the control to the .dfm, "
        f"fix the spelling, or record it in UNDECLARED_CONTROLS with the "
        f"reason.")


def test_the_undeclared_control_list_does_not_go_stale():
    """An entry that is now declared hides the next real one."""
    declared = _declared_controls()
    resolved = sorted(name for name in UNDECLARED_CONTROLS
                      if name in declared)
    assert not resolved, (
        f"these are now declared in the .dfm and should leave "
        f"UNDECLARED_CONTROLS: {resolved}")


def _prjscr_start_proc() -> tuple[str, str]:
    """The (file, procedure) the PrjScr nominates as the run entry point."""
    text = PRJSCR.read_text(encoding="utf-8")
    m = re.search(r"^StartProcName=([A-Za-z0-9_.]+)>(\w+)\s*$", text,
                  flags=re.MULTILINE)
    assert m, "StartProcName not found in Altium_API.PrjScr"
    return m.group(1), m.group(2)


def test_prjscr_entry_point_is_a_parameterless_proc_in_that_file():
    """Attaching is `Run Script... > <file> > <proc>`, and Altium's dialog
    lists a procedure under the file that DEFINES it, and only if it takes
    no parameters.

    So the entry point is pinned to a location, not just a name, and
    moving the procedure to another unit silently breaks the attach step
    for every user while the whole test suite stays green: nothing else
    here reads StartProcName. Giving it an argument breaks it just as
    quietly, by dropping it out of the dialog altogether.

    Caught for real: relocating the poll loop into StatusForm.pas took
    StartMCPServer with it and left the PrjScr, the README, the CLI's
    closing instructions and both recovery hints pointing at a procedure
    that was no longer in Dispatcher.pas.
    """
    entry_file, proc = _prjscr_start_proc()
    path = SCRIPTS_DIR / entry_file
    assert path.exists(), (
        f"Altium_API.PrjScr's StartProcName names {entry_file}, which is "
        f"not on disk.")

    source = path.read_text(encoding="utf-8", errors="replace")
    assert re.search(rf"^\s*Procedure\s+{re.escape(proc)}\s*;",
                     source, flags=re.MULTILINE | re.IGNORECASE), (
        f"Altium_API.PrjScr points the Run Script dialog at "
        f"{entry_file} > {proc}, but {entry_file} does not define a "
        f"parameterless `Procedure {proc};`. Either move the procedure "
        f"back, update StartProcName, or drop the parameter -- the "
        f"dialog only lists parameterless procedures, under the file "
        f"that defines them.")

    # A second definition is a duplicate identifier in the concatenated
    # bundle, and leaves it ambiguous which one the dialog offers.
    defining = [f for f in _build_py_files()
                if re.search(rf"^\s*Procedure\s+{re.escape(proc)}\s*;",
                             (SCRIPTS_DIR / f).read_text(encoding="utf-8",
                                                         errors="replace"),
                             flags=re.MULTILINE | re.IGNORECASE)]
    assert defining == [entry_file], (
        f"`Procedure {proc};` should be defined once, in {entry_file}, "
        f"but is defined in: {defining}.")


# Places that spell out the attach step for a user. Prose naming the
# procedure without naming a file is fine and is not checked.
ENTRY_POINT_DOCS = (
    "README.md",
    "docs/RELEASE_VERIFICATION.md",
    "src/eda_agent/cli.py",
    "src/eda_agent/diag/doctor.py",
    "src/eda_agent/tools/application.py",
    "src/eda_agent/bridge/recovery.py",
)


def test_docs_name_the_same_entry_file_as_the_prjscr():
    """Where a doc names a .pas file next to the entry procedure, it has
    to be the file the PrjScr points the dialog at.

    These strings are what the user follows after an install, and what
    the bridge hands back when it cannot be reached, so drift here sends
    someone to a file that does not offer the procedure -- at exactly the
    moment they are already stuck. The test that pins StartProcName to a
    real definition does not cover these: the PrjScr and the six strings
    can agree with each other and all be wrong together, or the PrjScr
    can be corrected alone and leave the prose behind.

    Deliberately narrow: only mentions with a .pas filename within ~90
    characters are judged, so ordinary prose ("re-launch StartMCPServer")
    stays free.
    """
    entry_file, proc = _prjscr_start_proc()
    checked = 0
    wrong = []
    for rel in ENTRY_POINT_DOCS:
        path = REPO_ROOT / rel
        assert path.exists(), (
            f"{rel} is listed in ENTRY_POINT_DOCS but is not on disk; "
            f"fix the path or drop the entry.")
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(re.escape(proc), text):
            window = text[max(0, m.start() - 90): m.end() + 90]
            named = set(re.findall(r"([A-Za-z0-9_]+\.pas)", window))
            if not named:
                continue
            checked += 1
            if named != {entry_file}:
                line = text.count("\n", 0, m.start()) + 1
                wrong.append(f"{rel}:{line} names {sorted(named)}")

    assert checked >= 5, (
        f"only {checked} file-qualified mentions of {proc} found across "
        f"{len(ENTRY_POINT_DOCS)} documents; the match went blind and this "
        f"check is no longer testing anything.")
    assert not wrong, (
        f"these name a different .pas file than Altium_API.PrjScr's "
        f"StartProcName ({entry_file} > {proc}): {wrong}. The user follows "
        f"these strings to attach, so they have to agree with the dialog.")


def test_disk_pas_files_are_in_build_or_excluded():
    """Every .pas file on disk is either in build.py FILES or in the
    known exception sets (PRJSCR_ONLY for IDE-only, EXCLUDED for
    bundle outputs / legacy)."""
    on_disk = _live_pas_files()
    build_files = set(_build_py_files())
    unexpected = on_disk - build_files - PRJSCR_ONLY
    assert not unexpected, (
        f"Pascal files exist on disk but are not in build.py FILES nor "
        f"the known exception sets: {sorted(unexpected)}. Either add to "
        f"build.py (deploys to the bundle), PRJSCR_ONLY (IDE-debug only), "
        f"or EXCLUDED (build output / legacy)."
    )
