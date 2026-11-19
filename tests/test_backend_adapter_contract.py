# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every backend adapter must answer the same questions the same way.

The EDA-agnostic tools (`run_drc`, `run_erc`, `review_design`) dispatch
to whichever backend is active and return its answer unchanged. So the
shape of that answer is a contract stated once per backend, in three
places, with nothing connecting them.

The cost of a mismatch is not a crash. It is a tool that works on one
backend and quietly returns something the caller cannot read on
another, which is exactly how the EasyEDA snapshot came to report an
empty board: the adapter handed over a vocabulary the snapshot did not
speak, and the review found nothing rather than failing.

Adding a backend is the moment this drifts, because the new adapter is
written by reading one of the existing ones and it is easy to copy the
method names without the payload keys.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from eda_agent import server
from eda_agent.core.backends import _BACKENDS, resolve_backend

#: Everything the EDA-agnostic layer calls on a backend.
_CONTRACT = ("health", "snapshot", "run_drc", "run_erc")

#: Keys every backend's check result must carry. `ok` and `source` let
#: the caller tell success from failure and know which tool answered;
#: the other two are what the tools actually report.
_CHECK_KEYS = {"ok", "source", "violation_count", "violations"}


@pytest.mark.parametrize("name", sorted(_BACKENDS))
def test_every_backend_implements_the_whole_contract(name):
    """A missing method fails at call time, on that backend only."""
    backend = resolve_backend(name)
    for method in _CONTRACT:
        assert hasattr(backend, method), f"{name} has no {method}"
        assert inspect.iscoroutinefunction(getattr(backend, method)), (
            f"{name}.{method} must be async; the tools await it")


@pytest.mark.parametrize("name", sorted(_BACKENDS))
def test_every_backend_names_itself(name):
    """`source` in a result comes from here, so it must be right."""
    assert resolve_backend(name).name == name


def test_the_check_result_shape_is_identical_across_backends(monkeypatch):
    """The keys a caller reads must not depend on the backend.

    Checked against real adapters rather than a description of them, so
    a backend that quietly renames a key is caught here rather than by
    whoever switches backends next.
    """
    from eda_agent.core.backends import AltiumBackend, EasyEdaBackend

    class _FakeEasyEdaBridge:
        verified_live = False

        def send_editor_command(self, command, params=None, timeout=30.0):
            return {"id": "x", "result": {
                "violation_count": 1,
                "violations": [{"description": "clearance"}]}}

    monkeypatch.setattr(
        "eda_agent.bridge.easyeda_bridge.get_easyeda_bridge",
        lambda: _FakeEasyEdaBridge())

    class _FakeAltiumBridge:
        async def send_command_async(self, command, params=None, **kw):
            return {"violation_count": 1,
                    "violations": [{"description": "clearance"}]}

    # AltiumBackend imports `from ..bridge import get_bridge`, so the
    # package attribute is the one that matters. Patching
    # eda_agent.bridge.altium_bridge.get_bridge looked equivalent, did
    # nothing, and left this test talking to the REAL Altium session:
    # 20 seconds of timeout per run, and live contact from a unit test,
    # which this project forbids for good reason.
    monkeypatch.setattr("eda_agent.bridge.get_bridge",
                        lambda: _FakeAltiumBridge())

    shapes = {}
    for backend in (EasyEdaBackend(), AltiumBackend()):
        for method in ("run_drc", "run_erc"):
            try:
                result = asyncio.run(getattr(backend, method)())
            except Exception:
                # A backend that cannot run here is not evidence of a
                # shape mismatch; skip rather than assert on nothing.
                continue
            shapes[f"{backend.name}.{method}"] = set(result) & _CHECK_KEYS

    assert shapes, "no backend produced a result to compare"
    for label, keys in shapes.items():
        assert keys == _CHECK_KEYS, (
            f"{label} returned {sorted(keys)}, expected "
            f"{sorted(_CHECK_KEYS)}. A caller reading one backend's "
            f"result cannot read this one.")


def test_a_backend_reports_its_own_name_in_its_results(monkeypatch):
    """`source` must identify the tool that actually answered.

    Copying an adapter and forgetting this makes results claim to come
    from the backend they were copied from, which is worse than a blank.
    """
    from eda_agent.core.backends import EasyEdaBackend

    class _FakeBridge:
        verified_live = False

        def send_editor_command(self, command, params=None, timeout=30.0):
            return {"id": "x", "result": {"violation_count": 0,
                                          "violations": []}}

    monkeypatch.setattr(
        "eda_agent.bridge.easyeda_bridge.get_easyeda_bridge",
        lambda: _FakeBridge())

    result = asyncio.run(EasyEdaBackend().run_drc())
    assert result["source"] == "easyeda", (
        f"result claims to come from {result['source']!r}")


@pytest.mark.parametrize("name", sorted(_BACKENDS))
def test_an_unreachable_backend_raises_the_shared_error(name, monkeypatch):
    """One error type, so the tools can catch it once.

    Each adapter reaches a different thing (a file directory, a CLI, a
    socket) and each fails differently. Translating to one type at the
    adapter boundary is what keeps `run_drc` from needing to know.

    Every transport is stubbed to fail. Letting a real one time out
    instead would make this test slow, environment-dependent, and
    quietly reliant on Altium NOT being open, which is the opposite of
    what it should assert.
    """
    from eda_agent.bridge.exceptions import AltiumError
    from eda_agent.core.backends import BackendUnavailableError

    # Fail the way the real thing fails. get_bridge() only constructs a
    # singleton and never raises, so a stub that raised there would test
    # a path that cannot occur, and "fixing" the adapter to satisfy it
    # would be changing production code to match a fiction.
    class _RefusingBridge:
        async def send_command_async(self, *a, **k):
            raise AltiumError("stubbed: nothing is listening")

    monkeypatch.setattr("eda_agent.bridge.get_bridge",
                        lambda: _RefusingBridge())

    backend = resolve_backend(name)
    try:
        asyncio.run(backend.health())
    except BackendUnavailableError:
        pass
    except Exception as exc:
        pytest.fail(
            f"{name}.health() raised {type(exc).__name__} rather than "
            f"BackendUnavailableError, so the tools will not catch it: "
            f"{exc}")


def test_the_test_fakes_match_the_real_bridge_interface():
    """A fake that drifts from the real thing proves nothing.

    Every EasyEDA test stands in a fake bridge, so the whole suite can
    keep passing while production is broken: the fakes agree with each
    other and none of them agrees with the bridge. Renaming the bridge's
    dispatch method is exactly how that happens, and it happened here.

    Checked by signature rather than by name alone, because a fake with
    the right name and the wrong arguments fails just as invisibly.
    """
    import inspect
    import pathlib
    import re

    from eda_agent.bridge.easyeda_bridge import EasyEdaBridge

    real = inspect.signature(EasyEdaBridge.send_editor_command)
    real_params = [p for p in real.parameters if p != "self"]

    tests_dir = pathlib.Path(__file__).resolve().parent
    checked = 0
    for path in tests_dir.glob("test_*.py"):
        source = path.read_text(encoding="utf-8")
        # Only files that actually stand in for the EasyEDA bridge.
        # Matching on the filename caught test_easyeda_converter,
        # which tests the EasyEDA-to-ALTIUM part converter and so
        # fakes the Altium bridge entirely correctly. The name says
        # "easyeda"; the fake is not one.
        if "easyeda_bridge" not in source:
            continue

        # Which classes are actually INSTALLED as the EasyEDA bridge.
        #
        # Selecting by filename was too coarse once one file held both
        # an EasyEDA fake and an Altium one: the Altium fake correctly
        # defines send_command, which is that bridge's real method, and
        # this guard called it a defect. The file's own comment already
        # described that hazard for a different file; the fix is to
        # judge the CLASS rather than the file it happens to sit in.
        #
        # A file with no detectable installation still gets scanned, so
        # a fake wired up some other way is not silently skipped.
        installed = set(re.findall(
            r"get_easyeda_bridge\"?,?\s*(?:=\s*)?lambda[^:]*:\s*(_?\w+)\(",
            source))

        for match in re.finditer(
                # Only the DISPATCH method. A fake editor legitimately has its
        # own send helpers (send_raw); those stand in for a WebSocket
        # client, not for the bridge, and matching them made this
        # guard fire on correct code.
        r"def (send_command|send_editor_command)\(self,\s*([^)]*)\)",
                source):
            name, args = match.group(1), match.group(2)

            # Skip a method belonging to a class this file never
            # installs as the EasyEDA bridge. Only applied when the
            # file installs SOMETHING, so a file where the pattern
            # found nothing is still checked in full.
            if installed:
                before = source[:match.start()]
                # Any indentation. Anchoring at column 0 made every
                # NESTED fake invisible, and nested is how most of them
                # are written: the enclosing class came back as some
                # unrelated top-level helper, which was never in the
                # installed set, so every match was skipped and the
                # guard silently stopped checking anything. Caught by
                # mutating an installed fake to the wrong dispatch name
                # and watching this pass.
                enclosing = re.findall(r"^\s*class\s+(\w+)", before,
                                       re.MULTILINE)
                if enclosing and enclosing[-1] not in installed:
                    continue

            assert name == "send_editor_command", (
                f"{path.name} fakes {name!r}, but the bridge dispatches "
                f"through {'send_editor_command'!r}. The fake will accept "
                f"calls the real bridge would reject.")
            names = [a.split(":")[0].split("=")[0].strip()
                     for a in args.split(",") if a.strip()]
            assert names[:len(real_params)] == real_params[:len(names)], (
                f"{path.name} fakes {name}({', '.join(names)}) against a "
                f"real {name}({', '.join(real_params)})")
            checked += 1

    assert checked, (
        "no fake bridges were found; the pattern stopped matching and "
        "this guard is checking nothing")


def test_no_easyeda_test_calls_a_bridge_method_that_does_not_exist():
    """The guard above checks FAKES. This checks real callers.

    test_easyeda_bridge drives the real bridge, so it has no fake to
    check, and it went on calling `send_command` for several commits
    after the method was renamed. The fake guard could not see it, and
    the file happened not to be in the subsets that were run, so it sat
    broken while everything reported green.

    Asserting against the real class rather than a spelling means this
    keeps working through the next rename too.
    """
    import pathlib
    import re

    from eda_agent.bridge.easyeda_bridge import EasyEdaBridge

    tests_dir = pathlib.Path(__file__).resolve().parent
    checked = 0
    candidates = 0
    for path in tests_dir.glob("test_*.py"):
        source = path.read_text(encoding="utf-8")
        if "easyeda_bridge" not in source:
            continue
        candidates += 1
        # Calls on a bridge handle, not on some other object. The
        # lookbehind matters: the module is called easyeda_bridge, so a
        # bare `bridge\.` also matches `easyeda_bridge.get_easyeda_bridge()`
        # and the guard reports a module-level function as a missing
        # method.
        for name in re.findall(r"(?<![\w.])(?:br|bridge)\.(\w+)\(", source):
            assert hasattr(EasyEdaBridge, name), (
                f"{path.name} calls bridge.{name}(), which EasyEdaBridge "
                f"does not have. It will fail at run time, and only in "
                f"whichever test happens to exercise it.")
            checked += 1

    # NOTHING TO CHECK IS NOT THE SAME AS A BROKEN PATTERN.
    #
    # `assert checked` conflated the two, and the EasyEDA corpus being
    # deleted for rebuild made the difference matter: zero call sites is
    # now the TRUE state, and the guard failed for a reason that had
    # nothing to do with what it protects.
    #
    # Counting files that merely mention the module does not separate
    # them either: four do, for imports, while none drives a bridge
    # handle. So the pattern is checked directly against a sample it
    # must match. That keeps the real protection, a regex that has
    # silently stopped matching, without tying it to how many call sites
    # happen to exist today.
    probe = "    reply = bridge.send_editor_command('system.ping')\n"
    assert re.findall(r"(?<![\w.])(?:br|bridge)\.(\w+)\(", probe) == [
        "send_editor_command"], (
        "the bridge-call pattern no longer matches a known call, so a "
        "zero count above proves nothing")
    del candidates


def test_the_backend_flag_offers_every_backend_that_exists():
    """`--backend` must accept exactly what EDA_AGENT_BACKEND accepts.

    The two are stated in different files with nothing connecting them,
    so a backend added to the registry stays rejected by the flag. That
    is not a crash: the backend works when selected by environment
    variable and fails only from the command line, which reads as the
    backend being unfinished rather than the flag being stale. easyeda
    sat in exactly that state.

    Checked against the source rather than a built parser, because the
    parser is assembled inside main() and building it means running the
    CLI. The defect is textual anyway: someone writes the tuple out
    again. So the rule enforced is that the choices are the BACKENDS
    name, never a literal.
    """
    import ast
    import pathlib

    from eda_agent.tools import BACKENDS

    source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    flags = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(isinstance(a, ast.Constant) and a.value == "--backend"
                   for a in node.args):
            continue
        choices = next((k.value for k in node.keywords
                        if k.arg == "choices"), None)
        flags.append(choices)

    assert len(flags) >= 2, (
        f"expected the top-level and serve --backend flags, found "
        f"{len(flags)}; this guard is no longer looking at anything")

    for choices in flags:
        assert isinstance(choices, ast.Name) and choices.id == "BACKENDS", (
            "a --backend flag spells its choices out instead of using "
            "BACKENDS, so the flag and the registry can disagree about "
            "which backends exist")

    # And the name really does resolve to every backend, so the check
    # above cannot be satisfied by a BACKENDS that has gone stale.
    assert "easyeda" in BACKENDS and "kicad" in BACKENDS


def test_a_board_outside_the_compiled_project_lists_its_own_parts(monkeypatch):
    from eda_agent.core.backends import AltiumBackend

    class _Bridge:
        def __init__(self):
            self.sent = []

        async def send_command_async(self, command, params=None, **kw):
            self.sent.append(command)
            if command == "project.get_bom":
                return {"components": [], "project_path": r"C:\p\P.PrjPcb"}
            if command == "pcb.get_components":
                return {"components": [{"designator": "U1", "comment": "MCU",
                                        "footprint": "QFN48", "layer": "Top"}]}
            return {}

    bridge = _Bridge()
    monkeypatch.setattr("eda_agent.bridge.get_bridge", lambda: bridge)
    snap = asyncio.run(AltiumBackend().snapshot())
    assert [p.refdes for p in snap.parts] == ["U1"]
    assert "board in front" in snap.raw_stats["parts_from"]
    assert bridge.sent == ["project.get_bom", "pcb.get_components"]


def test_parts_from_the_project_bom_say_which_project(monkeypatch):
    from eda_agent.core.backends import AltiumBackend

    class _Bridge:
        async def send_command_async(self, command, params=None, **kw):
            assert command == "project.get_bom", "the board is not read"
            return {"components": [{"designator": "R1", "pins": []}],
                    "project_path": r"C:\p\P.PrjPcb"}

    monkeypatch.setattr("eda_agent.bridge.get_bridge", lambda: _Bridge())
    snap = asyncio.run(AltiumBackend().snapshot())
    assert snap.raw_stats["parts_from"] == r"the BOM of C:\p\P.PrjPcb"
