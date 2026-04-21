# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Regression tests.

Each test pins a specific end-to-end behaviour so it cannot silently
regress: IPC mechanics, JSON escape correctness, return-type contracts,
and DelphiScript invariants that are expensive or impossible to verify
any other way.

Tests that merely grepped source files for substrings have been removed —
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
    """Ensure there is an event loop for async tests."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


# =========================================================================
# Stale response cleanup
# =========================================================================

class TestStaleResponseDeletion:
    """A response file with a mismatching id must be deleted so the next
    request can complete."""

    def test_stale_response_deleted_on_id_mismatch(self, tmp_path):
        """If response.json has wrong ID, it should be deleted."""
        config = AltiumConfig(
            workspace_dir=tmp_path,
            poll_interval=0.01,
            poll_timeout=0.5,
        )
        bridge = AltiumBridge.__new__(AltiumBridge)
        bridge.config = config
        bridge._attached = True

        class FakePM:
            def is_altium_running(self):
                return True

        bridge.process_manager = FakePM()

        response_path = config.response_path
        stale = {"id": "wrong-id", "success": True, "data": "stale", "error": None}
        response_path.write_text(json.dumps(stale), encoding="latin-1")

        with pytest.raises(AltiumTimeoutError):
            bridge._poll_response("correct-id", timeout=0.3)

        assert not response_path.exists(), (
            "Stale response file should be deleted when ID doesn't match"
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

class TestBatchFileLatin1Encoding:
    """Batch files passed to library.batch_set_params must be Latin-1,
    because that's the encoding Altium's script engine reads."""

    def test_batch_set_params_accepts_latin1_file(self, altium_sim, e2e_bridge):
        """library.batch_set_params must accept a latin-1-encoded batch file."""
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
    runtime — no simulator can reproduce that, so we lock the source
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
        request_path = altium_sim.workspace_dir / "request.json"
        request_path.write_text("", encoding="utf-8")
        time.sleep(0.1)
        assert not request_path.exists(), "Empty request file must be deleted"


# =========================================================================
# BuildObjectJson — no trailing comma
# =========================================================================

class TestNoTrailingComma:
    """BuildObjectJson must not emit a trailing comma before the closing
    brace when the property list is empty — otherwise Python's JSON
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
        assert result["success"] is True
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
        request_path = altium_sim.workspace_dir / "request.json"
        bad_request = {"id": "test-123", "command": "", "params": {}}
        request_path.write_text(json.dumps(bad_request), encoding="utf-8")
        time.sleep(0.15)
        assert not request_path.exists()


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
    must observe the change — the full IPC + generic-primitive stack."""

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
    reflect the new value — covers the write + read path end-to-end."""

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
            ".dfm files MUST be copied — without them DFM-backed forms fail "
            "to compile at Altium startup (issue #2)"
        )

        statusform_dfm = tmp_path / "StatusForm.dfm"
        assert statusform_dfm.exists(), (
            "StatusForm.dfm specifically must be copied — it is referenced "
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


class TestIpcLockSerializesConcurrentCalls:
    """Two threads calling send_command concurrently must each receive
    their own response. Before the IPC lock, concurrent pollers would
    read and delete each other's response files as 'stale', causing
    one of the two calls to timeout even though both had a response
    written. Regression for the keep-alive/user-call race.
    """

    def test_concurrent_send_commands_both_complete(self, altium_sim, e2e_bridge):
        import threading

        results = {}
        errors = {}

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

        assert not errors, f"Concurrent send_command failed for some threads: {errors}"
        assert len(results) == 4, "All four concurrent callers should receive a response"
        for tag, result in results.items():
            assert result is not None, f"Thread {tag} got None"

    def test_ipc_lock_exists_on_bridge(self, e2e_bridge):
        import threading
        assert hasattr(e2e_bridge, "_ipc_lock"), (
            "AltiumBridge must expose _ipc_lock — the serialization primitive "
            "that prevents concurrent pollers from deleting each other's "
            "responses as stale"
        )
        assert isinstance(e2e_bridge._ipc_lock, type(threading.Lock()))
