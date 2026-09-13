# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Regression tests.

Each test pins a specific end-to-end behaviour so it cannot silently
regress: IPC mechanics, JSON escape correctness, return-type contracts,
and DelphiScript invariants that are expensive or impossible to verify
any other way.

Tests that merely grepped source files for substrings have been removed;
a passing substring-match proves nothing about runtime behaviour and is
trivially defeated by a comment.

Cross-validation of Pascal logic is handled by cross_validate_pascal.pas +
test_cross_validate.py, which compile and run the REAL Pascal code under
FPC and compare byte-identical outputs to Python reimplementations.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from tests.altium_simulator import (
    AltiumSimulator,
    MockComponent,
    _escape_json_string,
)
from tests.conftest import wait_until
from eda_agent.bridge.altium_bridge import (
    AltiumBridge,
    CommandRequest,
    CommandResponse,
    reset_bridge,
)
from eda_agent.bridge.exceptions import (
    AltiumCommandError,
    AltiumTimeoutError,
)
from eda_agent.config import AltiumConfig, configure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _event_loop():
    """Give each test a fresh event loop, and close it afterwards.

    Do NOT probe with ``asyncio.get_event_loop()``: since 3.12 that
    CREATES a loop when none is set (with a DeprecationWarning) instead
    of raising, so a try/except RuntimeError around it never fires and
    the loop it just made is never closed. That loop is then collected
    at interpreter shutdown, where __del__ closes its self-pipe socket
    after Windows has already run WSACleanup, printing an ignored
    "OSError: [WinError 10093]" at the end of every full-suite run.

    Owning the loop outright is both deterministic and quieter.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()
    asyncio.set_event_loop(None)


# =========================================================================
# Per-request file isolation
# =========================================================================

class TestPerRequestFileIsolation:
    """With per-request response files, a foreign caller's response file
    is invisible to our poll, there is no "stale ID" race to handle."""

    def test_foreign_response_does_not_disturb_poll(self, tmp_path):
        from eda_agent.config import AltiumConfig, MCPRuntimeConfig

        config = AltiumConfig(
            workspace_dir=tmp_path,
            runtime=MCPRuntimeConfig(
                py_poll_interval_seconds=0.01,
                py_poll_timeout_seconds=0.5,
            ),
        )
        bridge = AltiumBridge.__new__(AltiumBridge)
        bridge.config = config
        bridge._attached = True

        class FakePM:
            def is_altium_running(self):
                return True

        bridge.process_manager = FakePM()

        # Drop a foreign response file in the workspace
        foreign_path = tmp_path / "response_foreigncaller.json"
        foreign = {
            "protocol_version": 2, "id": "foreigncaller",
            "success": True, "data": "stale", "error": None,
        }
        foreign_path.write_text(json.dumps(foreign), encoding="utf-8")

        # Polling for a different ID times out without consuming the foreign file
        with pytest.raises(AltiumTimeoutError):
            bridge._poll_response("correctid", timeout=0.3)

        assert foreign_path.exists(), (
            "Foreign caller's response file must be left alone, per-request "
            "files mean we never touch responses we don't own."
        )


# =========================================================================
# library.get_components return-type contract
# =========================================================================

class TestLibGetComponentsReturnType:
    """library.get_components must return a dict with count + components,
    not a bare list."""

    def test_lib_get_components_returns_dict_via_simulator(self, altium_sim, e2e_bridge):
        """Verify library.get_components response is actually a dict."""
        result = e2e_bridge.send_command("library.get_components", timeout=5.0)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "count" in result
        assert "components" in result


# =========================================================================
# Batch-file encoding
# =========================================================================

class TestBatchFileAnsiEncoding:
    """Batch files are written in the system ANSI codepage ("mbcs"),
    because Altium's AssignFile/ReadLn hands the script engine bytes in
    exactly that codepage. ASCII content, as here, is identical in every
    candidate encoding; the non-ASCII cases live at the tool layer in
    test_batch_file_encoding.py, where the encoding is actually chosen."""

    def test_batch_set_params_accepts_ansi_file(self, altium_sim, e2e_bridge):
        """library.batch_set_params must accept an ANSI-encoded batch file."""
        batch_path = altium_sim.workspace_dir / "batch_params.txt"
        with open(batch_path, "w", encoding="latin-1") as f:
            f.write("RES_0402|Partnumber|RC0402FR-0710KL\n")

        result = e2e_bridge.send_command(
            "library.batch_set_params",
            {"batch_file": str(batch_path)},
            timeout=5.0,
        )
        assert isinstance(result, dict)


# =========================================================================
# PreProcess / PostProcess signature
# =========================================================================

class TestNoPerObjectPreProcess:
    """In DelphiScript, the second argument to PreProcess / PostProcess
    must be a string, not an object. Passing an object silently fails at
    runtime, no simulator can reproduce that, so we lock the source
    pattern down instead.
    """

    def test_per_object_preprocess_not_in_modify(self):
        """Generic.pas must NOT call PreProcess(SchDoc, Obj) with Obj as 2nd arg."""
        repo_root = Path(__file__).resolve().parent.parent
        gen_pas = repo_root / "scripts" / "altium" / "Generic.pas"
        if not gen_pas.exists():
            pytest.skip("Generic.pas not found")

        content = gen_pas.read_text(encoding="utf-8")

        # Strip out line comments so a comment warning about the pattern
        # doesn't trip the detector.
        stripped_lines = []
        for line in content.splitlines():
            cut = line.find("//")
            if cut >= 0:
                line = line[:cut]
            stripped_lines.append(line)
        code = "\n".join(stripped_lines)

        forbidden = [
            "PreProcess(SchDoc, Obj)",
            "PreProcess(SchDoc,Obj)",
            "PreProcess(SchDoc , Obj)",
            "PostProcess(SchDoc, Obj)",
            "PostProcess(SchDoc,Obj)",
        ]
        for pat in forbidden:
            assert pat not in code, (
                f"{pat!r} must NOT appear in Generic.pas code "
                f"(the second parameter must be a string, not an object)."
            )


# =========================================================================
# JSON escape round-trip
# =========================================================================

class TestJsonEscapeRoundTrip:
    """EscapeJsonString must handle quotes, backslashes, and control
    characters correctly so payloads survive the Python <-> Altium round
    trip byte-for-byte."""

    def test_simulator_round_trips_quotes_in_version(self, altium_sim, e2e_bridge):
        altium_sim.version = 'ver "1.0" with\\backslash'
        result = e2e_bridge.send_command("application.get_version", timeout=5.0)
        assert result["version"] == 'ver "1.0" with\\backslash'

    def test_escape_json_string_handles_all_specials(self):
        assert _escape_json_string('hello "world"') == 'hello \\"world\\"'
        assert _escape_json_string('path\\to\\file') == 'path\\\\to\\\\file'
        assert _escape_json_string('line1\nline2') == 'line1\\nline2'
        assert _escape_json_string('tab\there') == 'tab\\there'


# =========================================================================
# ExtractJsonValue with escaped quotes
# =========================================================================

class TestExtractJsonEscapedQuotes:
    """ExtractJsonValue must count preceding backslashes to decide whether
    a `\"` is a closing quote or an escaped quote inside the string.

    Algorithmic correctness is cross-validated against real FPC code in
    test_cross_validate.py; this just verifies the end-to-end round trip.
    """

    def test_path_with_backslashes(self, altium_sim, e2e_bridge):
        result = e2e_bridge.send_command(
            "application.set_active_document",
            {"file_path": 'C:\\Projects\\TestProject\\Sheet1.SchDoc'},
            timeout=5.0,
        )
        assert result["success"] is True


# =========================================================================
# Empty-request cleanup
# =========================================================================

class TestEmptyRequestCleanup:
    """An empty or invalid request file must be deleted by the script
    loop so the next valid request isn't blocked waiting for it to be
    consumed."""

    def test_empty_request_removed(self, altium_sim):
        request_path = altium_sim.workspace_dir / "request_emptytestone.json"
        request_path.write_text("", encoding="utf-8")
        wait_until(lambda: not request_path.exists(),
                   message="Empty request file must be deleted")


# =========================================================================
# BuildObjectJson, no trailing comma
# =========================================================================

class TestNoTrailingComma:
    """BuildObjectJson must not emit a trailing comma before the closing
    brace when the property list is empty, otherwise Python's JSON
    parser would reject every query response."""

    def test_query_returns_valid_json(self, altium_sim, e2e_bridge):
        """If a trailing comma leaked in, JSON parsing would fail."""
        result = e2e_bridge.send_command(
            "generic.query_objects",
            {
                "scope": "active_doc",
                "object_type": "eNetLabel",
                "filter": "",
                "properties": "Text,Location.X,Location.Y",
            },
            timeout=5.0,
        )
        assert isinstance(result, dict)
        assert "objects" in result
        assert "count" in result


# =========================================================================
# Escape order: backslash before quote
# =========================================================================

class TestEscapeOrder:
    """Backslashes must be escaped FIRST. Escaping quotes first would
    turn an already-escaped `\"` into `\\"` and corrupt the payload."""

    def test_escape_order(self):
        # If " were escaped before \, a \" would become \\" which is wrong.
        # Correct output: path\\with\"quotes (escape \ first, then ")
        result = _escape_json_string('path\\with"quotes')
        assert result == 'path\\\\with\\"quotes'

    def test_backslash_path_round_trip(self, altium_sim, e2e_bridge):
        result = e2e_bridge.send_command(
            "application.get_active_document",
            timeout=5.0,
        )
        assert "C:\\Projects" in result["file_path"]


# =========================================================================
# No stale _cached_process attribute
# =========================================================================

class TestNoCachedProcess:
    """Neither AltiumBridge nor AltiumProcessManager should carry a
    cached-process attribute: the process set is always queried fresh."""

    def test_no_cached_process_attribute(self):
        from eda_agent.bridge.altium_bridge import AltiumBridge
        from eda_agent.bridge.process_manager import AltiumProcessManager

        bridge = AltiumBridge.__new__(AltiumBridge)
        pm = AltiumProcessManager()

        assert not hasattr(bridge, "_cached_process")
        assert not hasattr(pm, "_cached_process")


# =========================================================================
# generic.run_process parameter keys
# =========================================================================

class TestGenericRunProcessKeys:
    """generic.run_process requires the keys `process` and `params`;
    anything else must error rather than silently run nothing."""

    def test_run_process_succeeds_with_correct_keys(self, altium_sim, e2e_bridge):
        result = e2e_bridge.send_command(
            "generic.run_process",
            {"process": "Sch:Compile", "params": "ObjectKind=Document"},
            timeout=5.0,
        )
        # DISPATCHED, not success. Altium accepts a process name it does
        # not know without any error, so the handler cannot tell a command
        # that ran from one that was ignored. MEASURED on AD26:
        # obj_run_process("Sch:ThisProcessDoesNotExist") came back
        # success:true. The key is named for what is actually known, and
        # this assertion is what stops it drifting back.
        assert result["dispatched"] is True
        assert "success" not in result
        assert result["process"] == "Sch:Compile"

    def test_run_process_missing_process_key_errors(self, altium_sim, e2e_bridge):
        with pytest.raises(AltiumCommandError):
            e2e_bridge.send_command(
                "generic.run_process",
                {"process_name": "Sch:Compile"},  # Wrong key!
                timeout=5.0,
            )


# =========================================================================
# Request validation ordering
# =========================================================================

class TestRequestDeletedBeforeValidation:
    """An invalid request file must be deleted from disk before any
    validation check, so a malformed payload doesn't jam the loop."""

    def test_bad_request_still_removed(self, altium_sim):
        """Even an invalid request (empty command) must be removed from disk."""
        request_path = altium_sim.workspace_dir / "request_badtest123.json"
        bad_request = {
            "protocol_version": 2,
            "id": "badtest123",
            "command": "",
            "params": {},
        }
        request_path.write_text(json.dumps(bad_request), encoding="utf-8")
        wait_until(lambda: not request_path.exists(),
                   message="an invalid request must still be removed")


# =========================================================================
# reset_bridge is part of the public bridge surface
# =========================================================================

class TestResetBridgeExported:
    """reset_bridge must be part of eda_agent.bridge's public API so that
    config-driven singletons can be flushed without reaching into
    private modules."""

    def test_reset_bridge_importable(self):
        from eda_agent.bridge import reset_bridge
        assert callable(reset_bridge)

    def test_reset_bridge_in_all(self):
        from eda_agent.bridge import __all__
        assert "reset_bridge" in __all__

    def test_reset_bridge_actually_resets(self):
        from eda_agent.bridge.altium_bridge import reset_bridge as rb
        import eda_agent.bridge.altium_bridge as bridge_mod
        rb()
        assert bridge_mod._bridge is None


# =========================================================================
# End-to-end: query / modify round trip
# =========================================================================

class TestE2E_QueryModifyRoundTrip:
    """query_objects followed by modify_objects followed by another query
    must observe the change, the full IPC + generic-primitive stack."""

    def test_query_and_modify_roundtrip(self, altium_sim, e2e_bridge):
        result = e2e_bridge.send_command(
            "generic.query_objects",
            {
                "scope": "active_doc",
                "object_type": "eNetLabel",
                "filter": "Text=NET1",
                "properties": "Text,Location.X,Location.Y",
            },
            timeout=5.0,
        )
        assert result["count"] == 1
        assert result["objects"][0]["Text"] == "NET1"

        result = e2e_bridge.send_command(
            "generic.modify_objects",
            {
                "scope": "active_doc",
                "object_type": "eNetLabel",
                "filter": "Text=NET1",
                "set": "Text=NET99",
            },
            timeout=5.0,
        )
        assert result["matched"] == 1

        result = e2e_bridge.send_command(
            "generic.query_objects",
            {
                "scope": "active_doc",
                "object_type": "eNetLabel",
                "filter": "Text=NET99",
                "properties": "Text",
            },
            timeout=5.0,
        )
        assert result["count"] == 1
        assert result["objects"][0]["Text"] == "NET99"


class TestE2E_SpecialCharsRoundTrip:
    """Quotes, backslashes, and other JSON-sensitive characters in
    component parameters must round-trip unchanged through the full
    IPC stack."""

    def test_quotes_and_backslashes_in_component_params(self, altium_sim, e2e_bridge):
        altium_sim.projects[0].components.append(
            MockComponent(
                designator='R3',
                comment='10k "1%"',
                footprint='0402',
                lib_ref='RES_0402',
                sheet='Sheet1.SchDoc',
                parameters={'Note': 'Has "quotes" and \\backslash'},
            )
        )

        result = e2e_bridge.send_command(
            "project.get_component_info",
            {"designator": "R3"},
            timeout=5.0,
        )
        assert result["comment"] == '10k "1%"'
        assert result["parameters"]["Note"] == 'Has "quotes" and \\backslash'


class TestE2E_SetParameterRoundTrip:
    """project.set_parameter followed by project.get_parameters must
    reflect the new value, covers the write + read path end-to-end."""

    def test_set_and_get_parameter(self, altium_sim, e2e_bridge):
        result = e2e_bridge.send_command(
            "project.set_parameter",
            {"name": "NewParam", "value": "NewValue"},
            timeout=5.0,
        )
        assert result["success"] is True

        params = e2e_bridge.send_command(
            "project.get_parameters",
            timeout=5.0,
        )
        param_dict = {p["name"]: p["value"] for p in params}
        assert param_dict.get("NewParam") == "NewValue"


class TestInstallScriptsIncludesDfm:
    """install-scripts must copy DFM form files alongside .pas sources.

    Regression for GitHub issue #2: without the DFM, the DFM-backed
    StatusForm dashboard fails to compile and StartMCPServer crashes
    with 'unknown identifier' errors for the form's controls.
    """

    def test_dfm_files_are_copied(self, tmp_path):
        from eda_agent.cli import cmd_install_scripts

        rc = cmd_install_scripts(dest=str(tmp_path), force=True)
        assert rc == 0, "install-scripts should succeed"

        dfm_files = list(tmp_path.glob("*.dfm"))
        pas_files = list(tmp_path.glob("*.pas"))

        assert pas_files, ".pas files should be copied"
        assert dfm_files, (
            ".dfm files MUST be copied, without them DFM-backed forms fail "
            "to compile at Altium startup (issue #2)"
        )

        statusform_dfm = tmp_path / "StatusForm.dfm"
        assert statusform_dfm.exists(), (
            "StatusForm.dfm specifically must be copied, it is referenced "
            "by the Altium_API.PrjScr and required for the dashboard"
        )

    def test_prjscr_references_match_copied_files(self, tmp_path):
        """Every DocumentPath entry in the .PrjScr must point at a file that
        actually landed in the destination. Catches the case where new files
        are added to the PrjScr but the install-scripts whitelist forgets
        the new suffix."""
        from eda_agent.cli import cmd_install_scripts

        rc = cmd_install_scripts(dest=str(tmp_path), force=True)
        assert rc == 0

        prjscr = tmp_path / "Altium_API.PrjScr"
        assert prjscr.exists()

        missing = []
        for line in prjscr.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("DocumentPath="):
                continue
            name = line.split("=", 1)[1].strip()
            if not name:
                continue
            if not (tmp_path / name).exists():
                missing.append(name)

        assert not missing, (
            f"PrjScr references these files that were not copied: {missing}. "
            f"install-scripts allowed_suffixes probably needs a new entry."
        )


class TestConcurrentCallsBothComplete:
    """Two threads calling send_command concurrently must each receive
    their own response. With per-request response files (response_<id>.json)
    each caller polls only for its own filename, so concurrent pollers
    cannot collide. Regression for the keep-alive/user-call race.
    """

    def test_concurrent_send_commands_both_complete(self, altium_sim, e2e_bridge):
        import threading

        results = {}
        errors = {}

        # This failed intermittently for a long time -- one thread of
        # four, roughly twice in ten full-suite runs -- and was blamed
        # in turn on leaked threads, CPU starvation, neighbouring tests
        # and host I/O latency. All four were wrong, and raising the
        # timeout to ride it out did not help because it was never a
        # latency problem.
        #
        # The cause: this simulator wrote responses via a .tmp file and
        # a rename, while the bridge swept "response_*.json.tmp"
        # unconditionally. A concurrent caller's sweep could unlink
        # another's temp file between the write and the rename, and that
        # caller then polled a response that no longer existed. Real
        # Altium writes responses directly (Main.pas WriteResponseFile),
        # so the simulator was more atomic than the thing it simulates
        # and invented the race by itself.
        #
        # Reproduced at 5 failures in 60 runs, then 0 in 60 after the
        # simulator was made to match Pascal and the sweep was
        # age-filtered. The original 5s bound is kept: it was never the
        # problem, and a short bound surfaces a real hang quickly.
        def call(tag):
            try:
                results[tag] = e2e_bridge.send_command(
                    "application.ping", timeout=5.0
                )
            except Exception as e:
                errors[tag] = e

        threads = [threading.Thread(target=call, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # Report what the SIMULATOR hit, not just what the caller saw.
        # A timeout on its own says nothing: the dispatcher thread dying
        # on an unhandled exception and the machine being slow look
        # identical from here, and this failed twice without either
        # being distinguishable. altium_sim.errors makes the difference
        # visible the next time it happens.
        assert not errors, (
            f"Concurrent send_command failed for some threads: {errors}\n"
            f"simulator errors: {altium_sim.errors or 'none recorded'}\n"
            f"simulator thread alive: "
            f"{altium_sim._thread.is_alive() if altium_sim._thread else None}")
        assert len(results) == 4, "All four concurrent callers should receive a response"
        for tag, result in results.items():
            assert result is not None, f"Thread {tag} got None"

    def test_no_ipc_lock_needed(self, e2e_bridge):
        """Per-request files eliminate the cross-caller race; the bridge no
        longer carries an _ipc_lock attribute."""
        assert not hasattr(e2e_bridge, "_ipc_lock"), (
            "AltiumBridge should not expose _ipc_lock, per-request files "
            "make the lock unnecessary."
        )


class TestIpcWriteAtomicityIsDeliberatelyAsymmetric:
    """The two directions of the IPC use DIFFERENT write strategies.

    Requests are written atomically (stage to ``.json.tmp``, rename);
    responses are written directly. That looks like an inconsistency
    worth tidying, and tidying it either way reintroduces a real bug:

      * Make responses atomic and the bridge's sweep of stale
        ``response_*.json.tmp`` can unlink one mid-flight, destroying a
        concurrent caller's reply. The simulator did exactly this and
        produced an intermittent failure that took five wrong diagnoses
        to pin down.
      * Make requests direct and Pascal's Reset() collides with Python's
        still-open write handle, which surfaces as a sharing-violation
        modal in Altium -- an uncatchable dialog, not an exception.

    The asymmetry exists because each side is constrained by the tool at
    the other end: Python can rename reliably, DelphiScript's RenameFile
    could not (see Main.pas WriteResponseFile), and DelphiScript's reader
    cannot tolerate a half-written file while Python's poller can retry.
    """

    def test_requests_are_staged_then_renamed(self, tmp_path, monkeypatch):
        """A request must never be visible under its final name partial.

        Checked by OBSERVING the write, not by reading the source: this
        module drops substring assertions on principle, since a passing
        grep proves nothing about runtime behaviour and any comment
        defeats it. Here the promotion itself is recorded -- if the
        implementation ever writes the final path directly, no rename
        happens and this fails.
        """
        import os as _os
        from pathlib import Path as _Path

        from eda_agent.bridge.altium_bridge import CommandRequest

        # Spy on os.replace, NOT Path.replace. Both spellings appear in
        # this codebase and every atomic write now funnels through
        # eda_agent.atomicfile, which calls os.replace; pathlib's
        # Path.replace calls the same function, so this sees either.
        # Patching Path.replace saw only one of them, and when the
        # bridge moved behind the retry helper this test failed while
        # the behaviour it guards was unchanged.
        renames: list[tuple[str, str]] = []
        real_replace = _os.replace

        def spy(src, target):
            renames.append((_Path(src).name, _Path(target).name))
            return real_replace(src, target)

        monkeypatch.setattr(_os, "replace", spy)

        bridge = _bare_bridge_for(tmp_path)
        request = CommandRequest(command="application.ping", params={})
        bridge._publish_request(request)

        final = f"request_{request.id}.json"
        assert any(dst == final for _, dst in renames), (
            f"the request was not promoted by rename ({renames}); Pascal's "
            f"Reset() will collide with Python's open write handle and "
            f"raise an uncatchable modal in Altium")
        staged = [src for src, dst in renames if dst == final]
        assert all(s.endswith(".tmp") for s in staged), (
            f"the request was staged under {staged}, which Pascal's "
            f"request_*.json glob would pick up half-written")
        assert (tmp_path / final).exists()
        assert not list(tmp_path.glob("request_*.json.tmp"))

    def test_the_response_sweep_never_deletes_a_fresh_temp_file(self,
                                                               tmp_path):
        """Age-filtered, so an in-flight temp file is left alone.

        Deleting one between a writer's write and its rename destroys
        that caller's response and leaves it polling until timeout.
        """
        from eda_agent.bridge.altium_bridge import AltiumBridge

        bridge = _bare_bridge_for(tmp_path)
        fresh = tmp_path / "response_deadbeef.json.tmp"
        fresh.write_text("{}", encoding="utf-8")
        stale = tmp_path / "response_00000000.json.tmp"
        stale.write_text("{}", encoding="utf-8")
        import os
        import time as _time
        old = _time.time() - 3600
        os.utime(stale, (old, old))

        AltiumBridge._sweep_orphan_responses(bridge)

        assert fresh.exists(), "the sweep deleted an in-flight temp file"
        assert not stale.exists(), "the sweep left genuine debris behind"


def _bare_bridge_for(workspace):
    """A bridge whose config points at ``workspace``, nothing started."""
    from unittest.mock import patch

    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.config import AltiumConfig, MCPRuntimeConfig

    cfg = AltiumConfig(
        workspace_dir=workspace,
        runtime=MCPRuntimeConfig(py_poll_interval_seconds=0.01,
                                 py_poll_timeout_seconds=5.0),
    )
    with patch("eda_agent.bridge.altium_bridge.get_config", return_value=cfg):
        return AltiumBridge()
