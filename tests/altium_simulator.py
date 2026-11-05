# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium Designer Simulator for end-to-end integration testing.

Replaces step 4 of the IPC pipeline: instead of real Altium Designer
reading request.json and writing response.json, this Python-based simulator
does it in a background thread, with realistic mock state and byte-for-byte
compatible JSON responses.

The simulator mirrors the behavior of:
- scripts/altium/Dispatcher.pas (polling loop + command dispatch)
- scripts/altium/Main.pas (JSON helpers, response builders)
- scripts/altium/Application.pas (application commands)
- scripts/altium/Project.pas (project commands)
- scripts/altium/Library.pas (library commands)
- scripts/altium/Generic.pas (generic object primitives)
"""

import json
import os
import re
import string
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional


_VALID_ID_CHARS = set(string.ascii_letters + string.digits + "-_")


def _is_valid_request_id(s: str) -> bool:
    """Mirrors Pascal IsValidRequestId, alphanumeric/-/_ only, length 1..64."""
    if not s or len(s) > 64:
        return False
    return all(c in _VALID_ID_CHARS for c in s)


# ---------------------------------------------------------------------------
# Mock data structures
# ---------------------------------------------------------------------------

class MockDocument:
    """Mirrors an Altium IDocument / SchDoc / PcbDoc."""

    def __init__(self, file_name: str, file_path: str, document_kind: str):
        self.file_name = file_name
        self.file_path = file_path
        self.document_kind = document_kind


class MockComponent:
    """Mirrors a DM_Component from the compiled project."""

    def __init__(
        self,
        designator: str,
        comment: str = "",
        footprint: str = "",
        lib_ref: str = "",
        sheet: str = "",
        parameters: Optional[dict[str, str]] = None,
        pins: Optional[list[dict[str, str]]] = None,
    ):
        self.designator = designator
        self.comment = comment
        self.footprint = footprint
        self.lib_ref = lib_ref
        self.sheet = sheet
        self.parameters = parameters or {}
        self.pins = pins or []


class MockPcbComponent:
    """Mirrors an IPCB_Component (placed footprint on the board)."""

    def __init__(self, designator: str, x: int, y: int, rotation: float = 0.0,
                 layer: str = "Top Layer", footprint: str = "", comment: str = "",
                 width: int = 100, height: int = 60, height_mils: int = 0):
        self.designator = designator
        self.x = x
        self.y = y
        self.rotation = rotation
        self.layer = layer
        self.footprint = footprint
        self.comment = comment
        self.width = width
        self.height = height
        self.height_mils = height_mils


class MockVia:
    """Mirrors an IPCB_Via (x/y mils, net, pad + hole size, layer span)."""

    def __init__(self, x: int, y: int, net: str = "", size: int = 50,
                 hole_size: int = 28, low_layer: str = "Top Layer",
                 high_layer: str = "Bottom Layer"):
        self.x = x
        self.y = y
        self.net = net
        self.size = size
        self.hole_size = hole_size
        self.low_layer = low_layer
        self.high_layer = high_layer


class MockBoard:
    """Mirrors an IPCB_Board: placements, nets, outline, primitive counts."""

    def __init__(self, name: str, components=None, nets=None, outline=None,
                 track_count=0, via_count=0, pad_count=0, fill_count=0,
                 text_count=0, polygon_count=0, layer_count=2,
                 total_trace_length_mils=0, unrouted_connections=0,
                 vias=None, unrouted=None, drc_violations=None):
        self.name = name
        self.components: list[MockPcbComponent] = components or []
        self.nets: list[str] = nets or []
        self.vias: list[MockVia] = vias or []
        # Per-net unrouted connections: list of {"net", "unrouted_connections"}.
        self.unrouted: list[dict] = unrouted or []
        # DRC violations: list of dicts (echoed as-is by run_drc).
        self.drc_violations: list[dict] = drc_violations or []
        # Pad->net bindings from the SCH->PCB bridge: list of
        # {"designator", "pin", "net"}.
        self.pad_nets: list[dict] = []
        # Placed track segments: list of
        # {"x1","y1","x2","y2","width","layer","net"}.
        self.tracks: list[dict] = []
        # outline as a list of (x, y) mils vertices; default a rectangle.
        self.outline = outline or [(0, 0), (2000, 0), (2000, 1500), (0, 1500)]
        self.track_count = track_count
        self.via_count = via_count
        self.pad_count = pad_count
        self.fill_count = fill_count
        self.text_count = text_count
        self.polygon_count = polygon_count
        self.layer_count = layer_count
        self.total_trace_length_mils = total_trace_length_mils
        self.unrouted_connections = unrouted_connections


class MockSchObject:
    """Mirrors an ISch_GraphicalObject with late-bound properties."""

    def __init__(self, object_id: int, props: Optional[dict[str, str]] = None):
        self.object_id = object_id
        self.properties: dict[str, str] = props or {}

    def get_property(self, name: str) -> str:
        if name == "ObjectId":
            return str(self.object_id)
        return self.properties.get(name, "")

    def set_property(self, name: str, value: str) -> None:
        self.properties[name] = value


class MockProject:
    """Mirrors an IProject from the workspace."""

    def __init__(
        self,
        project_name: str,
        project_path: str,
        documents: Optional[list[MockDocument]] = None,
        parameters: Optional[list[dict[str, str]]] = None,
        components: Optional[list[MockComponent]] = None,
    ):
        self.project_name = project_name
        self.project_path = project_path
        self.documents = documents or []
        self.parameters = parameters or []
        self.components = components or []


class MockLibComponent:
    """Mirrors a component inside a SchLib."""

    def __init__(
        self,
        name: str,
        description: str = "",
        parameters: Optional[dict[str, str]] = None,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters or {}


# ---------------------------------------------------------------------------
# JSON helpers that mirror Main.pas exactly
# ---------------------------------------------------------------------------

def _escape_json_string(s: str) -> str:
    """Escape a string for JSON embedding -- mirrors EscapeJsonString in Altium."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\r", "\\r")
    s = s.replace("\n", "\\n")
    s = s.replace("\t", "\\t")
    return s


SIM_PROTOCOL_VERSION = 2


def _build_success_response(request_id: str, data_json: str) -> str:
    """Build a success response, mirrors Main.pas BuildSuccessResponse."""
    if not data_json:
        data_json = "null"
    return (
        '{"protocol_version":' + str(SIM_PROTOCOL_VERSION) + ','
        '"id":"' + request_id + '",'
        '"success":true,'
        '"data":' + data_json + ','
        '"error":null}'
    )


def _build_error_response(
    request_id: str,
    error_code: str,
    error_msg: str,
    details_json: str = "",
) -> str:
    """Build an error response, mirrors Main.pas BuildErrorResponseDetailed."""
    error_msg = error_msg.replace("\\", "\\\\")
    error_msg = error_msg.replace('"', '\\"')
    error_msg = error_msg.replace("\r", "\\r")
    error_msg = error_msg.replace("\n", "\\n")
    error_msg = error_msg.replace("\t", "\\t")
    details_field = details_json if details_json else "null"
    return (
        '{"protocol_version":' + str(SIM_PROTOCOL_VERSION) + ','
        '"id":"' + request_id + '",'
        '"success":false,'
        '"data":null,'
        '"error":{"code":"' + error_code + '",'
        '"message":"' + error_msg + '",'
        '"details":' + details_field + '}}'
    )


# ---------------------------------------------------------------------------
# The Altium Simulator
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Batch payload grammar, mirroring Main.pas NextBatchOp / GetBatchField.
#
# Every bulk tool rides this grammar and until now the simulator parsed it
# ad hoc with split("~~") and split(";"), which is LOOSER than the Pascal:
# a plain split keeps empty operations that NextBatchOp discards, and a
# naive key/value split disagrees with GetBatchField on a value that
# itself contains "=". A harness that accepts payloads Altium would
# reject cannot be used to check a payload builder.
#
# These two are the semantics the FPC cross-validation suite compares
# against the compiled originals, so a handler built on them tests the
# real contract.
# ---------------------------------------------------------------------------

def _next_batch_op(remaining: str) -> tuple[str, str]:
    """(op, rest) for the next non-empty operation. Mirrors NextBatchOp."""
    while remaining:
        sep = remaining.find("~~")
        if sep < 0:
            return remaining, ""
        op, remaining = remaining[:sep], remaining[sep + 2:]
        if op:
            return op, remaining
    return "", ""


def _iter_batch_ops(payload: str):
    """Every operation in a batch payload, empties discarded."""
    remaining = payload
    while True:
        op, remaining = _next_batch_op(remaining)
        if not op:
            return
        yield op
        if not remaining:
            return



def _int_or_zero(text: str) -> int:
    """Mirrors StrToIntDef(x, 0): a non-numeric field reads as zero."""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return 0

def _get_batch_field(op: str, key: str) -> str:
    """One field's value. Mirrors GetBatchField.

    Fields split on ";", key and value on the FIRST "=", so a value may
    contain "=" and is returned whole. Key match is case-insensitive and
    the first match wins. A field with no "=" is skipped, not treated as
    a valueless key.
    """
    remaining = op
    while remaining:
        sep = remaining.find(";")
        if sep < 0:
            field, remaining = remaining, ""
        else:
            field, remaining = remaining[:sep], remaining[sep + 1:]
        eq = field.find("=")
        if eq >= 0 and field[:eq].upper() == key.upper():
            return field[eq + 1:]
    return ""


#: Set EDA_SIM_TRACE=1 to have the poll loop log iterations that did
#: work or ran long. Used to tell a starved simulator apart from one
#: that never saw the request, which look identical from the bridge
#: side (both report a timeout with first_seen_ms=-1).
_SIM_TRACE = bool(os.environ.get("EDA_SIM_TRACE"))

#: How many consecutive read failures a request file gets before the
#: simulator gives up on it. At a 10ms poll that is a fifth of a second,
#: far longer than a transient share-violation and far shorter than the
#: bridge's 5s deadline, so a real answer still beats the timeout.
_MAX_REQUEST_READ_ATTEMPTS = 20


class AltiumSimulator:
    """Simulates Altium Designer's scripting engine for testing.

    Runs a background thread that polls for request.json and writes
    response.json, just like the real Dispatcher.pas StartMCPServer.
    """

    def __init__(self, workspace_dir: str):
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._poll_interval = 0.01  # 10ms for fast tests
        #: Anything the poll loop caught. Empty is the normal
        #: state; a non-empty list explains a timeout that would
        #: otherwise look like a slow machine.
        self.errors: list[str] = []
        #: request path -> consecutive read failures, so a file locked
        #: for an instant is retried rather than destroyed.
        self._read_failures: dict[str, int] = {}
        #: What the bulk library handlers accepted, so a test can assert
        #: on the PARSED payload rather than only on a success count.
        self.lib_pins: list[dict] = []
        self.lib_symbol_texts: list[dict] = []
        self.lib_pads: list[dict] = []
        self.lib_tracks: list[dict] = []
        #: designator -> paste currently suppressed?
        self.dnp_excluded: dict[str, bool] = {}

        # ----- Mock state -----
        self.version = "connected"
        self.product_name = "Altium Designer"

        # Active document tracking
        self.active_document_index = 0

        # Documents across all projects
        self.documents: list[MockDocument] = [
            MockDocument("Sheet1.SchDoc",
                         "C:\\Projects\\TestProject\\Sheet1.SchDoc", "SCH"),
            MockDocument("Sheet2.SchDoc",
                         "C:\\Projects\\TestProject\\Sheet2.SchDoc", "SCH"),
            MockDocument("Board.PcbDoc",
                         "C:\\Projects\\TestProject\\Board.PcbDoc", "PCB"),
        ]

        # Projects
        self.projects: list[MockProject] = [
            MockProject(
                project_name="TestProject.PrjPcb",
                project_path="C:\\Projects\\TestProject\\TestProject.PrjPcb",
                documents=self.documents[:],
                parameters=[
                    {"name": "Revision", "value": "1.0"},
                    {"name": "Author", "value": "Test User"},
                ],
                components=[
                    MockComponent(
                        designator="R1",
                        comment="10k",
                        footprint="0402",
                        lib_ref="RES_0402",
                        sheet="Sheet1.SchDoc",
                        parameters={"Partnumber": "RC0402FR-0710KL", "Manufacturer": "Yageo"},
                        pins=[
                            {"pin": "1", "name": "1", "net": "NET1"},
                            {"pin": "2", "name": "2", "net": "GND"},
                        ],
                    ),
                    MockComponent(
                        designator="R2",
                        comment="4.7k",
                        footprint="0402",
                        lib_ref="RES_0402",
                        sheet="Sheet1.SchDoc",
                        parameters={"Partnumber": "RC0402FR-074K7L"},
                        pins=[
                            {"pin": "1", "name": "1", "net": "VCC"},
                            {"pin": "2", "name": "2", "net": "NET1"},
                        ],
                    ),
                    MockComponent(
                        designator="U1",
                        comment="STM32F405RGT6",
                        footprint="LQFP-64",
                        lib_ref="STM32F405RGT6",
                        sheet="Sheet2.SchDoc",
                        parameters={"Partnumber": "STM32F405RGT6", "Manufacturer": "ST"},
                        pins=[
                            {"pin": "1", "name": "VBAT", "net": "VCC"},
                            {"pin": "2", "name": "PC13", "net": "NET1"},
                            {"pin": "3", "name": "PC14", "net": "NC"},
                            {"pin": "4", "name": "VSS", "net": "GND"},
                        ],
                    ),
                ],
            ),
        ]

        # Schematic objects on the active document (for generic primitives)
        self.sch_objects: list[MockSchObject] = [
            # eNetLabel = 25 (Altium constant)
            MockSchObject(25, {
                "Text": "VCC", "Location.X": "100", "Location.Y": "200",
                "Orientation": "0", "FontId": "1", "Color": "128",
            }),
            MockSchObject(25, {
                "Text": "GND", "Location.X": "100", "Location.Y": "100",
                "Orientation": "0", "FontId": "1", "Color": "128",
            }),
            MockSchObject(25, {
                "Text": "NET1", "Location.X": "300", "Location.Y": "200",
                "Orientation": "0", "FontId": "1", "Color": "128",
            }),
            # eSchComponent = 1
            MockSchObject(1, {
                "Designator.Text": "R1", "Comment.Text": "10k",
                "LibReference": "RES_0402",
                "Location.X": "500", "Location.Y": "300",
                "Orientation": "0",
            }),
            MockSchObject(1, {
                "Designator.Text": "R2", "Comment.Text": "4.7k",
                "LibReference": "RES_0402",
                "Location.X": "500", "Location.Y": "100",
                "Orientation": "0",
            }),
            MockSchObject(1, {
                "Designator.Text": "U1", "Comment.Text": "STM32F405RGT6",
                "LibReference": "STM32F405RGT6",
                "Location.X": "800", "Location.Y": "400",
                "Orientation": "0",
            }),
            # eWire = 27
            MockSchObject(27, {
                "Location.X": "200", "Location.Y": "200",
                "Corner.X": "400", "Corner.Y": "200",
            }),
        ]

        # Library components (for library commands)
        self.lib_components: list[MockLibComponent] = [
            MockLibComponent("RES_0402", "Standard 0402 Resistor",
                             {"Partnumber": "", "Manufacturer": ""}),
            MockLibComponent("CAP_0402", "Standard 0402 Capacitor",
                             {"Partnumber": "", "Manufacturer": ""}),
        ]
        self.lib_has_schlib = True  # Simulate having an active SchLib
        # Lib_AddFootprintPads refuses with NO_PCBLIB when no PCB
        # library is active, so the state is modelled rather than
        # assumed, letting a test exercise that refusal.
        self.lib_has_pcblib = True

        # Active PCB board (for pcb.* commands). Positions in mils.
        self.board = MockBoard(
            name="Board.PcbDoc",
            components=[
                MockPcbComponent("R1", x=500, y=400, rotation=0.0,
                                 layer="Top Layer", footprint="0402",
                                 comment="10k", width=60, height=30),
                MockPcbComponent("R2", x=700, y=400, rotation=90.0,
                                 layer="Top Layer", footprint="0402",
                                 comment="4.7k", width=60, height=30),
                MockPcbComponent("U1", x=1000, y=800, rotation=0.0,
                                 layer="Top Layer", footprint="LQFP-64",
                                 comment="STM32F405RGT6", width=400, height=400),
            ],
            nets=["GND", "VCC", "NET1"],
            track_count=12, via_count=4, pad_count=68, text_count=3,
            layer_count=2, total_trace_length_mils=3200,
            unrouted_connections=1,
            unrouted=[{"net": "NET1", "unrouted_connections": 1}],
            drc_violations=[],
        )

        # Audit results are SHAPE mirrors only (not Pascal detection logic --
        # see the simulator caveat). Default clean; a test seeds a specific
        # action's result to exercise design_lint_report's aggregation.
        self.audit_results: dict[str, dict] = {}

        # Object type string -> integer mapping (mirrors Generic.pas)
        self._sch_type_map = {
            "eNetLabel": 25, "ePort": times_or_default(28),
            "ePowerObject": 23, "eSchComponent": 1,
            "eWire": 27, "eBus": 26, "eBusEntry": 24,
            "eParameter": 41, "ePin": 2, "eLabel": 4,
            "eLine": 13, "eRectangle": 14,
            "eSheetSymbol": 47, "eSheetEntry": 48,
            "eNoERC": 29, "eJunction": 30, "eImage": 31,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start polling for requests in a background thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the simulator."""
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        # Clean up any leftover per-request IPC files (mirrors CleanupMCPServer)
        for pattern in ("request_*.json", "response_*.json"):
            for p in self.workspace_dir.glob(pattern):
                try:
                    p.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Polling loop (mirrors Dispatcher.pas StartMCPServer)
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Main polling loop, scans for any request_<id>.json file.

        Every iteration is guarded. This runs on a DAEMON thread, so an
        exception escaping here kills the simulator silently: the thread
        is gone, no further request is ever answered, and each waiting
        caller reports only a timeout. That is indistinguishable from a
        slow machine, and it is the shape of a failure this suite hit
        twice without being able to explain it.

        The exception is recorded rather than raised so the loop keeps
        serving, and ``self.errors`` gives the next failure something to
        say beyond "no response".
        """
        stop_path = self.workspace_dir / "stop"

        while self.running:
            if stop_path.exists():
                try:
                    stop_path.unlink()
                except OSError:
                    pass
                self.running = False
                break

            try:
                _t0 = time.monotonic()
                _did = self._process_single_request()
                _gap = (time.monotonic() - _t0) * 1000.0
                self._trace(_did, _gap)
            except BaseException as exc:  # noqa: BLE001 - see docstring
                self.errors.append(
                    f"{type(exc).__name__}: {exc}\n"
                    + "".join(traceback.format_exc()))
            time.sleep(self._poll_interval)

    def _write_error_response(self, request_id: str, code: str,
                              message: str) -> None:
        """Answer a request that could not even be read.

        Written directly, with no tmp+rename, because that is what
        Main.pas WriteResponseFile does and the bridge's poll loop is
        built to retry a partial parse. The point is that the caller
        gets a REASON: before this, an unreadable request was dropped
        and the only symptom was a timeout.
        """
        path = self.workspace_dir / f"response_{request_id}.json"
        try:
            path.write_text(
                _build_error_response(request_id, code, message),
                encoding="utf-8")
        except (IOError, OSError):
            pass

    def _trace(self, did: bool, gap_ms: float) -> None:
        """Record loop activity when EDA_SIM_TRACE is set.

        Off by default on purpose: an unconditional file write inside the
        poll loop would perturb the very timing this exists to measure.
        Logs only iterations that did work or ran long, so the file shows
        where a missing response actually went.
        """
        if not _SIM_TRACE:
            return
        self._trace_ticks = getattr(self, "_trace_ticks", 0) + 1
        # A heartbeat every 100 idle iterations, so silence in the log
        # means the THREAD stopped running rather than merely finding
        # nothing to do. Without it the two are indistinguishable, which
        # is the whole question when a request goes unanswered.
        beat = (self._trace_ticks % 100) == 0
        if not did and gap_ms <= 50.0 and not beat:
            return
        try:
            with open(self.workspace_dir / "sim_trace.log", "a",
                      encoding="utf-8") as handle:
                handle.write("%.3f did=%s gap=%.0fms\n"
                             % (time.time(), did, gap_ms))
        except Exception:
            pass

    def _process_single_request(self) -> bool:
        """Mirrors Dispatcher.pas ProcessSingleRequest under IPC v2.

        IPC v2 publishes requests as per-id files ``request_<id>.json`` so
        keep-alive pings and user calls never race on a single shared
        filename. Scan the workspace, pick the first matching file, extract
        the id from the filename (authoritative -- the file body's id must
        agree but does not pick the path), read the body, dispatch, and
        write ``response_<id>.json``.

        Heartbeat protocol: write ``progress_<id>.json`` before dispatch
        and remove it after the response is written. Python's bridge uses
        the progress marker to distinguish "Altium is still working" from
        "polling loop dead", letting legitimately slow handlers run past
        the per-call deadline without false timeouts.
        """
        request_path: Optional[Path] = None
        for entry in self.workspace_dir.glob("request_*.json"):
            request_path = entry
            break
        if request_path is None:
            return False

        # The id lives in the filename: request_<id>.json. The IPC contract
        # demands the body's id field match; if it doesn't, we trust the
        # filename (mirroring Pascal's IsValidRequestId-on-filename pattern).
        stem = request_path.stem  # request_<id>
        filename_id = stem[len("request_"):] if stem.startswith("request_") else ""

        # A read that fails here USED TO delete the request and return,
        # which destroyed the call: no response was ever written and the
        # bridge could only report a timeout, indistinguishable from a
        # slow machine. On Windows the read fails transiently whenever
        # something else holds the file open for an instant (indexer,
        # sync client, AV), so under CPU load this lost roughly 1 request
        # in 25 and produced exactly the trace seen while chasing it:
        # bridge polling healthily with first_seen_ms=-1, simulator loop
        # alive and finding nothing, because the file was already gone.
        #
        # Leave the file for the next iteration instead, and only give up
        # after enough attempts that a genuinely unreadable file cannot
        # spin forever -- reporting an error response, so the caller
        # learns the reason rather than waiting out the deadline.
        try:
            # utf-8-sig, as LoadFromStream does: the bridge writes a mark.
            content = request_path.read_text(encoding="utf-8-sig")
        except (IOError, OSError, UnicodeDecodeError) as exc:
            key = str(request_path)
            attempts = self._read_failures.get(key, 0) + 1
            self._read_failures[key] = attempts
            if attempts < _MAX_REQUEST_READ_ATTEMPTS:
                return False        # transient: try again next iteration
            self._read_failures.pop(key, None)
            try:
                request_path.unlink()
            except OSError:
                pass
            if filename_id and _is_valid_request_id(filename_id):
                self._write_error_response(
                    filename_id, "REQUEST_UNREADABLE",
                    f"Request file was unreadable after {attempts} "
                    f"attempts ({type(exc).__name__}: {exc}) and has been "
                    f"discarded. The polling loop is healthy; retry the "
                    f"call.")
            return False

        self._read_failures.pop(str(request_path), None)
        try:
            request_path.unlink()
        except OSError:
            pass

        if not content:
            return False

        try:
            request_data = json.loads(content)
        except json.JSONDecodeError:
            return False

        body_id = request_data.get("id", "")
        request_id = body_id or filename_id
        command = request_data.get("command", "")
        params = request_data.get("params", {})
        proto_ver = request_data.get("protocol_version")

        if not request_id or not _is_valid_request_id(request_id):
            return False

        progress_path = self.workspace_dir / f"progress_{request_id}.json"
        try:
            progress_path.write_text(
                json.dumps({"started_ms": int(time.monotonic() * 1000)}),
                encoding="utf-8",
            )
        except (IOError, OSError):
            pass

        try:
            if not command:
                response_content = _build_error_response(
                    request_id, "MALFORMED_REQUEST",
                    "Request missing required field: command",
                )
            elif proto_ver is not None and proto_ver != SIM_PROTOCOL_VERSION:
                response_content = _build_error_response(
                    request_id,
                    "PROTOCOL_VERSION_MISMATCH",
                    f"Client protocol_version={proto_ver} does not match server PROTOCOL_VERSION={SIM_PROTOCOL_VERSION}.",
                    details_json=json.dumps(
                        {"client_version": proto_ver, "server_version": SIM_PROTOCOL_VERSION}
                    ),
                )
            else:
                try:
                    response_content = self._dispatch(command, params, request_id)
                except Exception:
                    response_content = _build_error_response(
                        request_id, "INTERNAL_ERROR",
                        f"Unhandled exception processing: {command}",
                    )

            # Written DIRECTLY, exactly as Main.pas WriteResponseFile does.
            # Its comment records why: an earlier tmp+RenameFile was
            # abandoned because DelphiScript's RenameFile silently failed
            # for some paths and the response never reached its final
            # name. Python tolerates a partially-written response --
            # json.load raises and the poller retries -- so a direct
            # write is the contract.
            #
            # This simulator used tmp+replace, i.e. it was MORE atomic
            # than the thing it simulates. Two consequences, both real:
            # the suite never exercised the partial-read retry path, and
            # the bridge's unconditional sweep of "response_*.json.tmp"
            # could unlink an in-flight temp file between the write and
            # the rename, destroying that caller's response. That was
            # the intermittent concurrency failure -- 5 runs in 60.
            response_path = self.workspace_dir / f"response_{request_id}.json"
            try:
                response_path.write_text(response_content, encoding="utf-8")
            except (IOError, OSError):
                pass
        finally:
            # Delete progress AFTER the response is written so there is never
            # a moment where neither file exists (Python's deadline-check
            # would otherwise race the cleanup and fire a false hard-timeout).
            try:
                progress_path.unlink()
            except OSError:
                pass

        return True

    # ------------------------------------------------------------------
    # Command dispatch (mirrors Dispatcher.pas ProcessCommand)
    # ------------------------------------------------------------------

    def _dispatch(self, command: str, params: dict, request_id: str) -> str:
        """Route to handler -- mirrors ProcessCommand."""
        dot_pos = command.find(".")
        if dot_pos > 0:
            category = command[:dot_pos]
            action = command[dot_pos + 1:]
        else:
            category = command
            action = ""

        if category == "application":
            return self._handle_application(action, params, request_id)
        elif category == "project":
            return self._handle_project(action, params, request_id)
        elif category == "library":
            return self._handle_library(action, params, request_id)
        elif category == "generic":
            return self._handle_generic(action, params, request_id)
        elif category == "pcb":
            return self._handle_pcb(action, params, request_id)
        elif category == "audit":
            return self._handle_audit(action, params, request_id)
        else:
            return _build_error_response(
                request_id, "UNKNOWN_COMMAND",
                f"Unknown command category: {category}. Use generic.* for object operations."
            )

    # ------------------------------------------------------------------
    # Application commands (mirrors Application.pas)
    # ------------------------------------------------------------------

    def _handle_application(self, action: str, params: dict, rid: str) -> str:
        if action == "ping":
            return _build_success_response(rid, '"pong"')

        elif action == "get_version":
            data = ('{"version":"' + _escape_json_string(self.version) +
                    '","product_name":"' + _escape_json_string(self.product_name) + '"}')
            return _build_success_response(rid, data)

        elif action == "get_open_documents":
            items = []
            for doc in self.documents:
                item = ('{"file_name":"' + _escape_json_string(doc.file_name) + '"' +
                        ',"file_path":"' + _escape_json_string(doc.file_path) + '"' +
                        ',"document_kind":"' + _escape_json_string(doc.document_kind) + '"}')
                items.append(item)
            return _build_success_response(rid, "[" + ",".join(items) + "]")

        elif action == "get_active_document":
            if self.documents:
                idx = min(self.active_document_index, len(self.documents) - 1)
                doc = self.documents[idx]
                data = ('{"file_name":"' + _escape_json_string(doc.file_name) + '"' +
                        ',"file_path":"' + _escape_json_string(doc.file_path) + '"' +
                        ',"document_kind":"' + _escape_json_string(doc.document_kind) + '"}')
            else:
                data = "{}"
            return _build_success_response(rid, data)

        elif action == "set_active_document":
            file_path = params.get("file_path", "")
            file_path = file_path.replace("\\\\", "\\")
            # Find and set the document
            for i, doc in enumerate(self.documents):
                if doc.file_path == file_path:
                    self.active_document_index = i
                    break
            return _build_success_response(rid, '{"success":true}')

        elif action == "run_process":
            process_name = params.get("process_name", "")
            if not process_name:
                return _build_error_response(rid, "INVALID_PARAMETER", "Process name is required")
            return _build_success_response(rid, '{"success":true}')

        elif action == "stop_server":
            self.running = False
            return _build_success_response(rid, '{"stopped":true}')

        else:
            return _build_error_response(
                rid, "UNKNOWN_ACTION",
                f"Unknown application action: {action}"
            )

    # ------------------------------------------------------------------
    # Project commands (mirrors Project.pas)
    # ------------------------------------------------------------------

    def _get_project(self, params: dict) -> Optional[MockProject]:
        """Find a project by path or return the focused one."""
        project_path = params.get("project_path", "")
        project_path = project_path.replace("\\\\", "\\")
        if project_path:
            for proj in self.projects:
                if proj.project_path == project_path:
                    return proj
            return None
        return self.projects[0] if self.projects else None

    def _handle_project(self, action: str, params: dict, rid: str) -> str:
        if action == "create":
            project_path = params.get("project_path", "").replace("\\\\", "\\")
            return _build_success_response(
                rid,
                '{"success":true,"project_path":"' + _escape_json_string(project_path) + '"}'
            )

        elif action == "open":
            return _build_success_response(rid, '{"success":true}')

        elif action == "get_open_projects":
            items = ['{"project_name":"' + _escape_json_string(p.project_name) + '"'
                     ',"project_path":"' + _escape_json_string(p.project_path) + '"}'
                     for p in self.projects]
            return _build_success_response(
                rid, '{"projects":[' + ",".join(items) + '],"count":'
                + str(len(items)) + '}')

        elif action == "save":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "Project not found")
            return _build_success_response(rid, '{"success":true}')

        elif action == "close":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "Project not found")
            return _build_success_response(rid, '{"success":true}')

        elif action == "get_documents":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "Project not found")
            items = []
            for doc in project.documents:
                item = ('{"file_name":"' + _escape_json_string(doc.file_name) + '"' +
                        ',"file_path":"' + _escape_json_string(doc.file_path) + '"' +
                        ',"document_kind":"' + _escape_json_string(doc.document_kind) + '"}')
                items.append(item)
            return _build_success_response(rid, "[" + ",".join(items) + "]")

        elif action == "add_document":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "Project not found")
            return _build_success_response(rid, '{"success":true}')

        elif action == "remove_document":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "Project not found")
            return _build_success_response(rid, '{"success":true}')

        elif action == "get_parameters":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "Project not found")
            items = []
            for p in project.parameters:
                item = ('{"name":"' + _escape_json_string(p["name"]) + '"' +
                        ',"value":"' + _escape_json_string(p["value"]) + '"}')
                items.append(item)
            return _build_success_response(rid, "[" + ",".join(items) + "]")

        elif action == "set_parameter":
            param_name = params.get("name", "")
            param_value = params.get("value", "")
            if not param_name:
                return _build_error_response(rid, "MISSING_PARAMS", "name is required")
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "No project found")
            # Try to update existing
            found = False
            for p in project.parameters:
                if p["name"] == param_name:
                    p["value"] = param_value
                    found = True
                    break
            if not found:
                project.parameters.append({"name": param_name, "value": param_value})
            project_path = project.project_path
            data = ('{"success":true,"name":"' + _escape_json_string(param_name) +
                    '","value":"' + _escape_json_string(param_value) +
                    '","project_path":"' + _escape_json_string(project_path) + '"}')
            return _build_success_response(rid, data)

        elif action == "compile":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "PROJECT_NOT_FOUND", "Project not found")
            return _build_success_response(rid, '{"success":true}')

        elif action == "get_focused":
            if self.projects:
                proj = self.projects[0]
                data = ('{"project_name":"' + _escape_json_string(proj.project_name) + '"' +
                        ',"project_path":"' + _escape_json_string(proj.project_path) + '"' +
                        ',"document_count":' + str(len(proj.documents)) + '}')
                return _build_success_response(rid, data)
            return _build_success_response(rid, '{}')

        elif action == "get_nets":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "NO_PROJECT", "No project found")
            filter_comp = params.get("component", "")
            filter_net = params.get("net_name", "")
            limit = int(params.get("limit", 500))
            items = []
            count = 0
            for comp in project.components:
                if count >= limit:
                    break
                if filter_comp and comp.designator != filter_comp:
                    continue
                for pin in comp.pins:
                    if count >= limit:
                        break
                    if filter_net and pin["net"] != filter_net:
                        continue
                    item = ('{"component":"' + _escape_json_string(comp.designator) + '"' +
                            ',"pin":"' + _escape_json_string(pin["pin"]) + '"' +
                            ',"pin_name":"' + _escape_json_string(pin["name"]) + '"' +
                            ',"net":"' + _escape_json_string(pin["net"]) + '"}')
                    items.append(item)
                    count += 1
            data = '{"pins":[' + ",".join(items) + '],"count":' + str(count) + '}'
            return _build_success_response(rid, data)

        elif action == "get_bom":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "NO_PROJECT", "No project found")
            limit = int(params.get("limit", 1000))
            items = []
            count = 0
            for comp in project.components:
                if count >= limit:
                    break
                pin_items = []
                for pin in comp.pins:
                    pin_item = ('{"pin":"' + _escape_json_string(pin["pin"]) +
                                '","name":"' + _escape_json_string(pin["name"]) +
                                '","net":"' + _escape_json_string(pin["net"]) + '"}')
                    pin_items.append(pin_item)
                item = ('{"designator":"' + _escape_json_string(comp.designator) + '"' +
                        ',"comment":"' + _escape_json_string(comp.comment) + '"' +
                        ',"footprint":"' + _escape_json_string(comp.footprint) + '"' +
                        ',"lib_ref":"' + _escape_json_string(comp.lib_ref) + '"' +
                        ',"pins":[' + ",".join(pin_items) + ']}')
                items.append(item)
                count += 1
            data = '{"components":[' + ",".join(items) + '],"count":' + str(count) + '}'
            return _build_success_response(rid, data)

        elif action == "get_variants":
            # A simulated project has no variants.
            if self._get_project(params) is None:
                return _build_error_response(rid, "NO_PROJECT", "No project found")
            return _build_success_response(rid, '{"variants":[],"count":0}')

        elif action == "get_component_info":
            designator = params.get("designator", "")
            if not designator:
                return _build_error_response(rid, "MISSING_PARAMS", "designator is required")
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "NO_PROJECT", "No project found")
            for comp in project.components:
                if comp.designator == designator:
                    pin_items = []
                    for pin in comp.pins:
                        pin_item = ('{"pin":"' + _escape_json_string(pin["pin"]) +
                                    '","name":"' + _escape_json_string(pin["name"]) +
                                    '","net":"' + _escape_json_string(pin["net"]) + '"}')
                        pin_items.append(pin_item)
                    param_items = []
                    for k, v in comp.parameters.items():
                        param_items.append('"' + _escape_json_string(k) +
                                           '":"' + _escape_json_string(v) + '"')
                    data = ('{"designator":"' + _escape_json_string(comp.designator) + '"' +
                            ',"comment":"' + _escape_json_string(comp.comment) + '"' +
                            ',"footprint":"' + _escape_json_string(comp.footprint) + '"' +
                            ',"lib_ref":"' + _escape_json_string(comp.lib_ref) + '"' +
                            ',"sheet":"' + _escape_json_string(comp.sheet) + '"' +
                            ',"parameters":{' + ",".join(param_items) + '}' +
                            ',"pins":[' + ",".join(pin_items) + ']}')
                    return _build_success_response(rid, data)
            return _build_error_response(rid, "NOT_FOUND",
                                         "Component not found: " + designator)

        elif action == "export_pdf":
            output_path = params.get("output_path", "")
            if not output_path:
                return _build_error_response(rid, "MISSING_PARAMS", "output_path is required")
            return _build_success_response(
                rid, '{"success":true,"output_path":"' +
                _escape_json_string(output_path) + '"}'
            )

        elif action == "cross_probe":
            designator = params.get("designator", "")
            target = params.get("target", "schematic")
            if not designator:
                return _build_error_response(rid, "MISSING_PARAMS", "designator is required")
            data = ('{"success":true,"designator":"' + _escape_json_string(designator) +
                    '","target":"' + target + '"}')
            return _build_success_response(rid, data)

        elif action == "get_design_stats":
            project = self._get_project(params)
            if project is None:
                return _build_error_response(rid, "NO_PROJECT", "No project found")
            doc_count = len([d for d in project.documents if d.document_kind == "SCH"])
            comp_count = len(project.components)
            pin_count = sum(len(c.pins) for c in project.components)
            # Mirrors Proj_GetDesignStats: sheets/documents/components/
            # pins, and NOTHING else. This used to add a "nets" count the
            # Pascal never emits, so a test asserting nets passed here
            # and failed live (found by the shape comparison, task #39).
            data = ('{"sheets":' + str(doc_count) +
                    ',"documents":' + str(len(project.documents)) +
                    ',"components":' + str(comp_count) +
                    ',"pins":' + str(pin_count) + '}')
            return _build_success_response(rid, data)

        elif action == "get_board_info":
            # Simulate a PCB board -- simplified
            data = ('{"origin_x":0,"origin_y":0,'
                    '"outline":[{"x":0,"y":0},{"x":4000,"y":0},'
                    '{"x":4000,"y":3000},{"x":0,"y":3000}],'
                    '"layers":["Top Layer","Bottom Layer","Top Overlay"]}')
            return _build_success_response(rid, data)

        elif action == "annotate":
            # Mirrors Proj_Annotate's normal-order reply: success/order/
            # renamed/skipped_locked/documents_processed/programmatic.
            # This used to answer {"annotated":true,...}, a key the
            # Pascal never emits (found by the shape comparison, #39).
            order = params.get("order", "down_then_across")
            sch_docs = 0
            project = self._get_project(params)
            if project is not None:
                sch_docs = len([d for d in project.documents
                                if d.document_kind == "SCH"])
            return _build_success_response(
                rid, '{"success":true,"order":"' + order + '"'
                     ',"renamed":0,"skipped_locked":0'
                     ',"documents_processed":' + str(sch_docs) +
                     ',"programmatic":true}'
            )

        elif action == "generate_output":
            output_type = params.get("output_type", "")
            if not output_type:
                return _build_error_response(rid, "MISSING_PARAMS", "output_type is required")
            valid_types = {"gerber", "drill", "pick_place", "ipc_netlist"}
            if output_type not in valid_types:
                return _build_error_response(
                    rid, "INVALID_TYPE",
                    "Unknown output type: " + output_type + ". Use: gerber, drill, pick_place, ipc_netlist"
                )
            return _build_success_response(
                rid, '{"generated":true,"output_type":"' + output_type + '"}'
            )

        else:
            return _build_error_response(
                rid, "UNKNOWN_ACTION",
                f"Unknown project action: {action}"
            )

    # ------------------------------------------------------------------
    # Library commands (mirrors Library.pas)
    # ------------------------------------------------------------------

    def _handle_library(self, action: str, params: dict, rid: str) -> str:
        if action == "create_symbol":
            name = params.get("name", "")
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            self.lib_components.append(MockLibComponent(name))
            return _build_success_response(
                rid, '{"success":true,"name":"' + _escape_json_string(name) + '"}'
            )

        elif action == "add_pin":
            designator = params.get("designator", "")
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            return _build_success_response(
                rid,
                '{"success":true,"designator":"' + _escape_json_string(designator) + '"}'
            )

        elif action == "add_pins":
            # Mirrors Lib_AddPins: one PreProcess for the batch, a pin
            # with no designator is DISCARDED rather than failing the
            # call, and the reply is added/failed/total. Getting the
            # blank-designator rule wrong here would let a payload that
            # silently loses pins in Altium look clean in a test.
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            added = failed = total = 0
            for op in _iter_batch_ops(params.get("pins", "")):
                total += 1
                if _get_batch_field(op, "designator").strip():
                    added += 1
                    self.lib_pins.append({
                        "designator": _get_batch_field(op, "designator"),
                        "name": _get_batch_field(op, "name"),
                        "electrical_type": _get_batch_field(
                            op, "electrical_type"),
                        "symbol_outer_edge": _get_batch_field(
                            op, "symbol_outer_edge"),
                        "symbol_inner_edge": _get_batch_field(
                            op, "symbol_inner_edge"),
                        "show_name": _get_batch_field(op, "show_name"),
                        "show_designator": _get_batch_field(
                            op, "show_designator"),
                        # Captured so an INJECTED field is observable: a
                        # name carrying ";rotation=270" would otherwise
                        # land silently.
                        "rotation": _get_batch_field(op, "rotation"),
                    })
                else:
                    failed += 1
            return _build_success_response(
                rid, '{"added":%d,"failed":%d,"total":%d}' % (
                    added, failed, total))

        elif action == "add_footprint_pads":
            # Mirrors Lib_AddFootprintPads, which creates a pad for EVERY
            # operation and only counts a failure when the object factory
            # returns Nil. Blank designators never reach it: _pads_payload
            # filters them on the Python side and reports the count, so a
            # handler that rejected them here would disagree with Altium
            # and hide that split of responsibility.
            if not params.get("pads"):
                return _build_error_response(rid, "MISSING_PARAM",
                                             "pads is required")
            if not self.lib_has_pcblib:
                return _build_error_response(rid, "NO_PCBLIB",
                                             "No PCB library is active")
            added = total = 0
            for op in _iter_batch_ops(params.get("pads", "")):
                total += 1
                added += 1
                self.lib_pads.append({
                    "designator": _get_batch_field(op, "designator"),
                    "x": _get_batch_field(op, "x"),
                    "y": _get_batch_field(op, "y"),
                    "x_size": _get_batch_field(op, "x_size"),
                    "y_size": _get_batch_field(op, "y_size"),
                    "hole_size": _get_batch_field(op, "hole_size"),
                    "shape": _get_batch_field(op, "shape"),
                    "corner_radius": _get_batch_field(op, "corner_radius"),
                    "rotation": _get_batch_field(op, "rotation"),
                    # EFFECTIVE layer, not the one that was sent.
                    # Lib_AddFootprintPads overrides it: a pad with a
                    # drill is forced to MultiLayer regardless of what
                    # the caller asked for, because a drilled pad on a
                    # single copper layer is not a through-hole pad.
                    # Recording the raw field would make a test assert a
                    # value Altium discards.
                    "layer": ("MultiLayer"
                              if _int_or_zero(_get_batch_field(
                                  op, "hole_size")) > 0
                              else _get_batch_field(op, "layer")),
                    "layer_requested": _get_batch_field(op, "layer"),
                })
            return _build_success_response(
                rid, '{"added":%d,"failed":0,"total":%d}' % (added, total))

        elif action == "add_footprint_tracks":
            # Mirrors Lib_AddFootprintTracks. Note the per-track layer
            # default: an EMPTY layer means silkscreen (eTopOverlay), not
            # the eTopLayer that GetLayerFromString falls back to for an
            # unrecognised name. Modelling the fallback instead of the
            # default would put courtyard and outline art on copper here
            # and hide the distinction the handler is careful about.
            if not params.get("tracks"):
                return _build_error_response(rid, "MISSING_PARAM",
                                             "tracks is required")
            if not self.lib_has_pcblib:
                return _build_error_response(rid, "NO_PCBLIB",
                                             "No PCB library is active")
            added = total = 0
            for op in _iter_batch_ops(params.get("tracks", "")):
                total += 1
                added += 1
                layer = _get_batch_field(op, "layer") or "TopOverlay"
                self.lib_tracks.append({
                    "x1": _get_batch_field(op, "x1"),
                    "y1": _get_batch_field(op, "y1"),
                    "x2": _get_batch_field(op, "x2"),
                    "y2": _get_batch_field(op, "y2"),
                    "width": _get_batch_field(op, "width") or "10",
                    "layer": layer,
                })
            return _build_success_response(
                rid, '{"added":%d,"failed":0,"total":%d}' % (added, total))

        elif action == "add_symbol_text":
            # Mirrors Lib_AddSymbolText: empty text is refused per item.
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            added = failed = total = 0
            for op in _iter_batch_ops(params.get("texts", "")):
                total += 1
                if _get_batch_field(op, "text").strip():
                    added += 1
                    self.lib_symbol_texts.append({
                        "text": _get_batch_field(op, "text"),
                        "rotation": _get_batch_field(op, "rotation"),
                    })
                else:
                    failed += 1
            return _build_success_response(
                rid, '{"added":%d,"failed":%d,"total":%d}' % (
                    added, failed, total))

        elif action == "add_symbol_rectangle":
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            return _build_success_response(rid, '{"success":true}')

        elif action == "add_symbol_polygon":
            # Mirrors Lib_AddSymbolPolygon: a library must be active and
            # the vertex list must hold at least three points.
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            coords = [c for c in str(params.get("vertices", "")).split(",")
                      if c.strip()]
            if len(coords) < 6:
                return _build_error_response(
                    rid, "INVALID_PARAMS", "At least 3 vertices are required")
            return _build_success_response(
                rid, '{"success":true,"vertices":%d}' % (len(coords) // 2))

        elif action == "add_symbol_line":
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            return _build_success_response(rid, '{"success":true}')

        elif action == "create_footprint":
            name = params.get("name", "")
            return _build_success_response(
                rid, '{"success":true,"name":"' + _escape_json_string(name) + '"}'
            )

        elif action == "add_footprint_pad":
            designator = params.get("designator", "")
            return _build_success_response(
                rid,
                '{"success":true,"designator":"' + _escape_json_string(designator) + '"}'
            )

        elif action == "add_footprint_track":
            return _build_success_response(rid, '{"success":true}')

        elif action == "add_footprint_arc":
            return _build_success_response(rid, '{"success":true}')

        elif action == "link_footprint":
            fp_name = params.get("footprint_name", "")
            return _build_success_response(
                rid,
                '{"success":true,"footprint":"' + _escape_json_string(fp_name) + '"}'
            )

        elif action == "link_3d_model":
            model_name = params.get("model_name", "")
            if not model_name:
                model_path = params.get("model_path", "")
                model_name = model_path.rsplit("\\", 1)[-1] if "\\" in model_path else model_path.rsplit("/", 1)[-1]
            bodies = self.__dict__.setdefault("lib_bodies", {})
            fp = params.get("component_name", "")
            bodies[fp] = bodies.get(fp, 0) + 1
            return _build_success_response(
                rid,
                '{"success":true,"model":"' + _escape_json_string(model_name) + '"}'
            )

        elif action == "delete_footprint_primitives":
            # Mirrors Lib_DeleteFootprintPrimitives for 3D bodies, the only
            # kind the simulator tracks: confirm required, then matched,
            # removed and leftover counted.
            fp = params.get("footprint_name", "")
            if str(params.get("confirm", "")).lower() != "true":
                return _build_error_response(rid, "CONFIRM_REQUIRED",
                                             "Pass confirm=true")
            kind = str(params.get("object_type", "")).lower().replace("_", "")
            bodies = self.__dict__.setdefault("lib_bodies", {})
            matched = bodies.get(fp, 0) if kind in ("componentbody", "body") else 0
            if matched:
                bodies[fp] = 0
            return _build_success_response(
                rid,
                '{"library":"","footprint":"' + _escape_json_string(fp) + '",'
                '"matched":' + str(matched) + ',"removed":' + str(matched) + ','
                '"leftover":0,"hint":"","examined":' + str(matched) + ','
                '"layers":[],"pads_included":false,"was_current":false}'
            )

        elif action == "get_components":
            items = []
            for comp in self.lib_components:
                param_items = []
                for k, v in comp.parameters.items():
                    param_items.append('"' + _escape_json_string(k) +
                                       '":"' + _escape_json_string(v) + '"')
                item = ('{"name":"' + _escape_json_string(comp.name) + '"' +
                        ',"description":"' + _escape_json_string(comp.description) + '"' +
                        ',"parameters":{' + ",".join(param_items) + '}}')
                items.append(item)
            count = len(self.lib_components)
            data = '{"count":' + str(count) + ',"components":[' + ",".join(items) + ']}'
            return _build_success_response(rid, data)

        elif action == "search":
            # Mirrors Lib_Search: query/search_type/count/limit/
            # truncated/results, and no "success" key (the Pascal never
            # emits one here; found by the shape comparison, #39).
            query = params.get("query", "")
            search_type = params.get("search_type", "")
            limit = int(params.get("limit", "100") or "100")
            return _build_success_response(
                rid,
                '{"query":"' + _escape_json_string(query) +
                '","search_type":"' + _escape_json_string(search_type) +
                '","count":0,"limit":' + str(limit) +
                ',"truncated":false,"results":[]}'
            )

        elif action == "get_component_details":
            comp_name = params.get("component_name", "")
            for comp in self.lib_components:
                if comp.name == comp_name:
                    data = ('{"name":"' + _escape_json_string(comp.name) + '"' +
                            ',"description":"' + _escape_json_string(comp.description) + '"' +
                            ',"part_count":1}')
                    return _build_success_response(rid, data)
            return _build_error_response(rid, "COMPONENT_NOT_FOUND",
                                         "Component not found: " + comp_name)

        elif action == "get_installed":
            return _build_success_response(
                rid,
                '{"message":"Library panel opened. Use search tools to find components."}'
            )

        elif action == "batch_set_params":
            batch_path = params.get("batch_file", "")
            if not batch_path:
                batch_path = str(self.workspace_dir / "batch_params.txt")
            if not os.path.isfile(batch_path):
                return _build_error_response(rid, "NO_BATCH_FILE",
                                             "Batch file not found: " + batch_path)
            updated = 0
            created = 0
            failed = 0
            line_num = 0
            try:
                with open(batch_path, "r", encoding="latin-1") as f:
                    for line in f:
                        line = line.rstrip("\n").rstrip("\r")
                        line_num += 1
                        if not line:
                            continue
                        parts = line.split("|")
                        if len(parts) < 3:
                            failed += 1
                            continue
                        comp_name, param_name, param_value = parts[0], parts[1], parts[2]
                        # Find component
                        comp_found = None
                        for c in self.lib_components:
                            if c.name == comp_name:
                                comp_found = c
                                break
                        if comp_found is None:
                            failed += 1
                            continue
                        if param_name == "Description":
                            comp_found.description = param_value
                            updated += 1
                        elif param_name in comp_found.parameters:
                            comp_found.parameters[param_name] = param_value
                            updated += 1
                        else:
                            comp_found.parameters[param_name] = param_value
                            created += 1
            except (IOError, OSError):
                failed += 1
            data = ('{"updated":' + str(updated) +
                    ',"created":' + str(created) +
                    ',"failed":' + str(failed) +
                    ',"total_lines":' + str(line_num) + '}')
            return _build_success_response(rid, data)

        elif action == "batch_rename":
            batch_path = params.get("batch_file", "")
            if not batch_path:
                batch_path = str(self.workspace_dir / "batch_rename.txt")
            if not os.path.isfile(batch_path):
                return _build_error_response(rid, "NO_BATCH_FILE",
                                             "Batch file not found: " + batch_path)
            renamed = 0
            failed = 0
            line_num = 0
            try:
                with open(batch_path, "r", encoding="latin-1") as f:
                    for line in f:
                        line = line.rstrip("\n").rstrip("\r")
                        line_num += 1
                        if not line:
                            continue
                        parts = line.split("|")
                        if len(parts) < 2:
                            failed += 1
                            continue
                        old_name, new_name = parts[0], parts[1]
                        comp_found = None
                        for c in self.lib_components:
                            if c.name == old_name:
                                comp_found = c
                                break
                        if comp_found is None:
                            failed += 1
                            continue
                        comp_found.name = new_name
                        renamed += 1
            except (IOError, OSError):
                failed += 1
            data = ('{"renamed":' + str(renamed) +
                    ',"failed":' + str(failed) +
                    ',"total_lines":' + str(line_num) + '}')
            return _build_success_response(rid, data)

        elif action == "diff_libraries":
            # Simplified: return empty diff
            path_a = params.get("library_a", "")
            path_b = params.get("library_b", "")
            if not path_a or not path_b:
                return _build_error_response(
                    rid, "MISSING_PARAMS",
                    "library_a and library_b are required"
                )
            data = ('{"only_in_a":[],"only_in_b":[],"common":[],'
                    '"count_a":0,"count_b":0,"only_a":0,"only_b":0,"shared":0}')
            return _build_success_response(rid, data)

        else:
            return _build_error_response(
                rid, "UNKNOWN_ACTION",
                f"Unknown library action: {action}"
            )

    # ------------------------------------------------------------------
    # Generic commands (mirrors Generic.pas)
    # ------------------------------------------------------------------

    def _resolve_object_type(self, type_str: str) -> int:
        """Resolve object type string to integer ID."""
        # Schematic types
        sch_map = {
            "eNetLabel": 25, "ePort": 28,
            "ePowerObject": 23, "eSchComponent": 1,
            "eWire": 27, "eBus": 26, "eBusEntry": 24,
            "eParameter": 41, "ePin": 2, "eLabel": 4,
            "eLine": 13, "eRectangle": 14,
            "eSheetSymbol": 47, "eSheetEntry": 48,
            "eNoERC": 29, "eJunction": 30, "eImage": 31,
        }
        if type_str in sch_map:
            return sch_map[type_str]
        # PCB types
        pcb_map = {
            "eTrackObject": 100, "ePadObject": 101,
            "eViaObject": 102, "eComponentObject": 103,
            "eArcObject": 104, "eFillObject": 105,
            "eTextObject": 106, "ePolyObject": 107,
            "eRegionObject": 108, "eRuleObject": 109,
            "eDimensionObject": 110,
        }
        if type_str in pcb_map:
            return pcb_map[type_str]
        return -1

    def _matches_filter(self, obj: MockSchObject, filter_str: str) -> bool:
        """Check if object matches pipe-separated filter."""
        if not filter_str:
            return True
        conditions = filter_str.split("|")
        for cond in conditions:
            eq_pos = cond.find("=")
            if eq_pos <= 0:
                continue
            prop_name = cond[:eq_pos]
            expected = cond[eq_pos + 1:]
            actual = obj.get_property(prop_name)
            if actual != expected:
                return False
        return True

    def _build_object_json(self, obj: MockSchObject, props_str: str, doc_path: str = "") -> str:
        """Build JSON for a single object from comma-separated property names."""
        items = []
        for prop_name in props_str.split(","):
            prop_name = prop_name.strip()
            if not prop_name:
                continue
            prop_value = obj.get_property(prop_name)
            items.append('"' + _escape_json_string(prop_name) +
                         '":"' + _escape_json_string(prop_value) + '"')
        # Prepend _doc if doc_path is given (mirrors ProcessSchDocObjects)
        if doc_path:
            doc_entry = '"_doc":"' + _escape_json_string(doc_path) + '"'
            items.insert(0, doc_entry)
        return "{" + ",".join(items) + "}"

    def _handle_generic(self, action: str, params: dict, rid: str) -> str:
        if action == "query_objects":
            return self._gen_query_objects(params, rid)
        elif action == "modify_objects":
            return self._gen_modify_objects(params, rid)
        elif action == "create_object":
            return self._gen_create_object(params, rid)
        elif action == "delete_objects":
            return self._gen_delete_objects(params, rid)
        elif action == "run_process":
            process_name = params.get("process", "")
            if not process_name:
                return _build_error_response(rid, "MISSING_PARAMS",
                                             "process parameter is required")
            # DISPATCHED, not success, mirroring Gen_RunProcess. Altium
            # accepts an unknown process name without error, so the
            # handler can only report that the command was sent. A
            # simulator that answers "success" would teach a test to
            # assert a key the live bridge never emits.
            return _build_success_response(
                rid,
                '{"dispatched":true,"process":"'
                + _escape_json_string(process_name) + '"}'
            )
        elif action == "get_font_spec":
            font_id = int(params.get("font_id", 1))
            data = ('{"font_id":' + str(font_id) +
                    ',"size":10,"rotation":0,"bold":false,"italic":false' +
                    ',"underline":false,"strikeout":false,"font_name":"Arial"}')
            return _build_success_response(rid, data)
        elif action == "get_font_id":
            return _build_success_response(rid, '{"font_id":1}')
        elif action == "select_objects":
            # Mirrors Gen_SelectObjects: select through modify, then read
            # the flag back and report matched and selected, never the
            # property notes.
            obj_type_str = params.get("object_type", "")
            filter_str = params.get("filter", "")
            reply = self._gen_modify_objects(
                {"scope": "active_doc", "object_type": obj_type_str,
                 "filter": filter_str, "set": "Selection=true"}, rid
            )
            parsed = json.loads(reply)
            if not parsed.get("success"):
                return reply
            matched = int(parsed["data"]["matched"])
            type_int = self._resolve_object_type(obj_type_str)
            selected = sum(
                1 for obj in self.sch_objects
                if obj.object_id == type_int
                and self._matches_filter(obj, filter_str)
                and obj.get_property("Selection").lower() == "true")
            ok = "true" if selected >= matched else "false"
            return _build_success_response(
                rid, '{"matched":' + str(matched) + ',"selected":'
                + str(selected) + ',"success":' + ok + '}')
        elif action == "deselect_all":
            return _build_success_response(rid, '{"deselected":true}')
        elif action == "zoom":
            zoom_action = params.get("action", "fit")
            return _build_success_response(
                rid, '{"action":"' + zoom_action + '"}'
            )
        elif action == "zoom_to_xy":
            # Gen_ZoomToXY: x and y required; the reply echoes them.
            x, y = params.get("x", ""), params.get("y", "")
            if x == "" or y == "":
                return _build_error_response(
                    rid, "MISSING_PARAMS", "x and y parameters are required")
            return _build_success_response(
                rid, '{"success":true,"x":' + str(x) + ',"y":' + str(y)
                + ',"context":"pcb"}')
        else:
            return _build_error_response(
                rid, "UNKNOWN_ACTION",
                f"Unknown generic action: {action}"
            )

    def _gen_query_objects(self, params: dict, rid: str) -> str:
        scope = params.get("scope", "active_doc")
        obj_type_str = params.get("object_type", "")
        filter_str = params.get("filter", "")
        props_str = params.get("properties", "Location.X,Location.Y")
        limit = int(params.get("limit", 0))

        obj_type_int = self._resolve_object_type(obj_type_str)
        if obj_type_int == -1:
            return _build_error_response(rid, "INVALID_TYPE",
                                         "Unknown object type: " + obj_type_str)

        # For project scope, add _doc and sheets_processed
        is_project = scope.startswith("project")
        doc_path = "C:\\Projects\\TestProject\\Sheet1.SchDoc" if not is_project else ""

        items = []
        count = 0
        for obj in self.sch_objects:
            if limit > 0 and count >= limit:
                break
            if obj.object_id != obj_type_int:
                continue
            if not self._matches_filter(obj, filter_str):
                continue
            if is_project:
                json_item = self._build_object_json(
                    obj, props_str,
                    doc_path="C:\\Projects\\TestProject\\Sheet1.SchDoc"
                )
            else:
                json_item = self._build_object_json(
                    obj, props_str,
                    doc_path="C:\\Projects\\TestProject\\Sheet1.SchDoc"
                )
            items.append(json_item)
            count += 1

        if is_project:
            data = ('{"objects":[' + ",".join(items) + '],"count":' + str(count) +
                    ',"sheets_processed":2}')
        else:
            data = '{"objects":[' + ",".join(items) + '],"count":' + str(count) + '}'
        return _build_success_response(rid, data)

    def _gen_modify_objects(self, params: dict, rid: str) -> str:
        scope = params.get("scope", "active_doc")
        obj_type_str = params.get("object_type", "")
        filter_str = params.get("filter", "")
        set_str = params.get("set", "")

        if not set_str:
            return _build_error_response(rid, "MISSING_PARAMS",
                                         "set parameter is required")

        obj_type_int = self._resolve_object_type(obj_type_str)
        if obj_type_int == -1:
            return _build_error_response(rid, "INVALID_TYPE",
                                         "Unknown object type: " + obj_type_str)

        is_project = scope.startswith("project")

        count = 0
        for obj in self.sch_objects:
            if obj.object_id != obj_type_int:
                continue
            if not self._matches_filter(obj, filter_str):
                continue
            # Apply set properties
            for assignment in set_str.split("|"):
                eq_pos = assignment.find("=")
                if eq_pos <= 0:
                    continue
                prop_name = assignment[:eq_pos]
                prop_value = assignment[eq_pos + 1:]
                obj.set_property(prop_name, prop_value)
            count += 1

        if is_project:
            data = '{"matched":' + str(count) + ',"sheets_processed":2}'
        else:
            data = '{"matched":' + str(count) + '}'
        return _build_success_response(rid, data)

    def _gen_create_object(self, params: dict, rid: str) -> str:
        obj_type_str = params.get("object_type", "")
        props_str = params.get("properties", "")
        container = params.get("container", "document")

        obj_type_int = self._resolve_object_type(obj_type_str)
        if obj_type_int == -1:
            return _build_error_response(rid, "INVALID_TYPE",
                                         "Unknown object type: " + obj_type_str)

        # Create mock object and add to state
        new_obj = MockSchObject(obj_type_int)
        if props_str:
            for assignment in props_str.split("|"):
                eq_pos = assignment.find("=")
                if eq_pos <= 0:
                    continue
                prop_name = assignment[:eq_pos]
                prop_value = assignment[eq_pos + 1:]
                new_obj.set_property(prop_name, prop_value)

        self.sch_objects.append(new_obj)

        return _build_success_response(
            rid,
            '{"created":true,"object_type":"' + obj_type_str + '"}'
        )

    def _gen_delete_objects(self, params: dict, rid: str) -> str:
        scope = params.get("scope", "active_doc")
        obj_type_str = params.get("object_type", "")
        filter_str = params.get("filter", "")

        obj_type_int = self._resolve_object_type(obj_type_str)
        if obj_type_int == -1:
            return _build_error_response(rid, "INVALID_TYPE",
                                         "Unknown object type: " + obj_type_str)

        is_project = scope.startswith("project")

        to_remove = []
        for obj in self.sch_objects:
            if obj.object_id != obj_type_int:
                continue
            if not self._matches_filter(obj, filter_str):
                continue
            to_remove.append(obj)

        for obj in to_remove:
            self.sch_objects.remove(obj)

        count = len(to_remove)
        if is_project:
            data = '{"matched":' + str(count) + ',"sheets_processed":2}'
        else:
            data = '{"matched":' + str(count) + '}'
        return _build_success_response(rid, data)

    # ------------------------------------------------------------------
    # PCB commands (mirrors PCB.pas -- read handlers)
    # ------------------------------------------------------------------

    def _handle_pcb(self, action: str, params: dict, rid: str) -> str:
        board = self.board
        if board is None:
            return _build_error_response(rid, "NO_PCB", "No PCB document is active")

        if action == "apply_dnp_paste_exclusion":
            # Mirrors PCB_ApplyDnpPasteExclusion. The designator list is
            # ONE pipe-delimited field, and membership is tested with the
            # separators attached ('|R1|' inside '|' + list + '|') so R1
            # cannot match R10. Modelling that anchoring is the point: a
            # substring test here would pass a payload that treats the
            # wrong components on a real board.
            desigs = params.get("designators", "")
            if not desigs:
                return _build_error_response(
                    rid, "MISSING_PARAM", "designators is required")
            restore = str(params.get("restore", "")).lower() in ("true", "1")
            wanted = "|" + desigs + "|"
            matched, pads_changed = [], 0
            for comp in board.components:
                if "|" + comp.designator + "|" not in wanted:
                    continue
                matched.append(comp.designator)
                # Two surface pads per component in the mock board.
                pads_changed += 2
                self.dnp_excluded[comp.designator] = not restore
            return _build_success_response(rid, (
                '{"restored":%s,"components_requested":%d,'
                '"components_matched":%d,"pads_changed":%d,'
                '"pads_skipped_through_hole":0,"items":[%s]}' % (
                    "true" if restore else "false",
                    len([d for d in desigs.split("|") if d]),
                    len(matched), pads_changed,
                    ",".join('{"designator":"%s","pads_changed":2}'
                             % _escape_json_string(d) for d in matched))))

        if action == "get_components":
            items = []
            for c in board.components:
                x1, y1 = c.x - c.width // 2, c.y - c.height // 2
                x2, y2 = c.x + c.width // 2, c.y + c.height // 2
                items.append(
                    '{"designator":"' + _escape_json_string(c.designator) + '",'
                    '"comment":"' + _escape_json_string(c.comment) + '",'
                    '"x":' + str(c.x) + ',"y":' + str(c.y) + ','
                    '"rotation":' + _num(c.rotation) + ','
                    '"layer":"' + _escape_json_string(c.layer) + '",'
                    '"footprint":"' + _escape_json_string(c.footprint) + '",'
                    '"source_designator":"' + _escape_json_string(c.designator) + '",'
                    '"height_mils":' + str(c.height_mils) + ','
                    '"bbox":{"x1":' + str(x1) + ',"y1":' + str(y1) +
                    ',"x2":' + str(x2) + ',"y2":' + str(y2) +
                    ',"width":' + str(c.width) + ',"height":' + str(c.height) + '}}'
                )
            data = '{"components":[' + ",".join(items) + '],"count":' + str(len(items)) + '}'
            return _build_success_response(rid, data)

        elif action == "get_nets":
            items = ['"' + _escape_json_string(n) + '"' for n in board.nets]
            data = '{"nets":[' + ",".join(items) + '],"count":' + str(len(items)) + '}'
            return _build_success_response(rid, data)

        elif action == "get_board_outline":
            verts = []
            for i, (x, y) in enumerate(board.outline):
                verts.append('{"index":' + str(i) + ',"kind":"line","x":' + str(x) +
                             ',"y":' + str(y) + ',"cx":0,"cy":0}')
            xs = [x for x, _ in board.outline]
            ys = [y for _, y in board.outline]
            left, right = (min(xs), max(xs)) if xs else (0, 0)
            bottom, top = (min(ys), max(ys)) if ys else (0, 0)
            data = ('{"point_count":' + str(len(board.outline)) +
                    ',"vertices":[' + ",".join(verts) + '],'
                    '"bounding_rect":{"left":' + str(left) + ',"bottom":' + str(bottom) +
                    ',"right":' + str(right) + ',"top":' + str(top) + '}}')
            return _build_success_response(rid, data)

        elif action == "get_board_statistics":
            xs = [x for x, _ in board.outline] or [0]
            ys = [y for _, y in board.outline] or [0]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            data = ('{"board_name":"' + _escape_json_string(board.name) + '",'
                    '"track_count":' + str(board.track_count) + ','
                    '"via_count":' + str(board.via_count) + ','
                    '"pad_count":' + str(board.pad_count) + ','
                    '"component_count":' + str(len(board.components)) + ','
                    '"fill_count":' + str(board.fill_count) + ','
                    '"text_count":' + str(board.text_count) + ','
                    '"polygon_count":' + str(board.polygon_count) + ','
                    '"layer_count":' + str(board.layer_count) + ','
                    '"total_trace_length_mils":' + str(board.total_trace_length_mils) + ','
                    '"unrouted_connections":' + str(board.unrouted_connections) + ','
                    '"board_width_mils":' + str(w) + ','
                    '"board_height_mils":' + str(h) + ','
                    '"board_area_sq_mils":' + str(w * h) + '}')
            return _build_success_response(rid, data)

        elif action == "move_component":
            desig = params.get("designator", "")
            if not desig:
                return _build_error_response(rid, "MISSING_PARAM",
                                             'Missing "designator" parameter')
            comp = next((c for c in board.components if c.designator == desig), None)
            if comp is None:
                return _build_error_response(rid, "NOT_FOUND",
                                             "Component not found: " + desig)
            if params.get("x", "") not in ("", None):
                comp.x = int(float(params["x"]))
            if params.get("y", "") not in ("", None):
                comp.y = int(float(params["y"]))
            if params.get("rotation", "") not in ("", None):
                comp.rotation = float(params["rotation"])
            data = ('{"designator":"' + _escape_json_string(desig) + '",'
                    '"x":' + str(comp.x) + ',"y":' + str(comp.y) + ','
                    '"rotation":' + _num(comp.rotation) + '}')
            return _build_success_response(rid, data)

        elif action == "place_tracks":
            tracks = params.get("tracks", "")
            if not tracks:
                return _build_error_response(rid, "MISSING_PARAM",
                                             "tracks parameter required")
            placed, failed = 0, 0
            for t in tracks.split("|"):
                if not t:
                    continue
                f = t.split(",")
                if len(f) < 4 or any(v == "" for v in f[:4]):
                    failed += 1
                    continue
                try:
                    seg = {
                        "x1": int(float(f[0])), "y1": int(float(f[1])),
                        "x2": int(float(f[2])), "y2": int(float(f[3])),
                        "width": int(float(f[4])) if len(f) > 4 and f[4] else 10,
                        "layer": f[5] if len(f) > 5 and f[5] else "TopLayer",
                        "net": f[6] if len(f) > 6 else "",
                    }
                except ValueError:
                    failed += 1
                    continue
                board.tracks.append(seg)
                board.track_count += 1
                placed += 1
            data = '{"placed":' + str(placed) + ',"failed":' + str(failed) + '}'
            return _build_success_response(rid, data)

        elif action == "place_components":
            placements = params.get("placements", "")
            placed, failed = 0, 0
            for rec in placements.split("~~"):
                if not rec:
                    continue
                fields = {}
                for f in rec.split(";;"):
                    if "==" in f:
                        k, v = f.split("==", 1)
                        fields[k.strip()] = v.strip()
                if not fields.get("footprint"):
                    failed += 1
                    continue
                comp = MockPcbComponent(
                    designator=fields.get("designator", ""),
                    x=int(float(fields.get("x", 0))),
                    y=int(float(fields.get("y", 0))),
                    rotation=float(fields.get("rotation", 0) or 0),
                    layer=fields.get("layer") or "Top Layer",
                    footprint=fields.get("footprint", ""),
                    comment=fields.get("comment", ""),
                )
                board.components.append(comp)
                # Synced mode: pad_nets creates nets + pad bindings.
                pn = fields.get("pad_nets", "")
                if pn:
                    for pair in pn.split("|"):
                        if "=" not in pair:
                            continue
                        pad, net = pair.split("=", 1)
                        pad, net = pad.strip(), net.strip()
                        if net and net not in board.nets:
                            board.nets.append(net)
                        if comp.designator and pad and net:
                            board.pad_nets.append(
                                {"designator": comp.designator, "pin": pad, "net": net})
                placed += 1
            data = ('{"placed":' + str(placed) + ',"failed":' + str(failed) +
                    ',"total":' + str(placed + failed) + '}')
            return _build_success_response(rid, data)

        elif action == "create_nets_from_list":
            nets_param = params.get("nets", "")
            created, existing = 0, 0
            for name in nets_param.split("|"):
                name = name.strip()
                if not name:
                    continue
                if name in board.nets:
                    existing += 1
                else:
                    board.nets.append(name)
                    created += 1
            data = '{"created":' + str(created) + ',"existing":' + str(existing) + '}'
            return _build_success_response(rid, data)

        elif action == "bind_pad_nets":
            bindings = params.get("bindings", "")
            if not bindings:
                return _build_error_response(rid, "MISSING_PARAM",
                                             "bindings parameter required")
            bound, failed = 0, 0
            missing_components, missing_nets = [], []
            comp_names = {c.designator for c in board.components}
            # PCB_BindPadNets parses with NextBatchOp / GetBatchField, so
            # this does too. The previous split("~~") + split(";") was
            # looser in two ways that matter to a payload builder: it
            # stripped surrounding whitespace, which the Pascal keeps, and
            # it matched keys case-sensitively, which the Pascal does not.
            for op in _iter_batch_ops(bindings):
                desig = _get_batch_field(op, "designator") or None
                pin = _get_batch_field(op, "pin") or None
                net = _get_batch_field(op, "net") or None
                if not desig or not pin or not net:
                    failed += 1
                    continue
                if desig not in comp_names:
                    failed += 1
                    if desig not in missing_components:
                        missing_components.append(desig)
                    continue
                if net not in board.nets:
                    failed += 1
                    if net not in missing_nets:
                        missing_nets.append(net)
                    continue
                board.pad_nets.append({"designator": desig, "pin": pin, "net": net})
                bound += 1
            data = ('{"bound":' + str(bound) + ',"failed":' + str(failed) +
                    ',"missing_components":' + json.dumps(missing_components[:50]) +
                    ',"missing_nets":' + json.dumps(missing_nets[:50]) +
                    ',"missing_pads":[]}')
            return _build_success_response(rid, data)

        elif action == "get_unrouted_nets":
            items = []
            total = 0
            for u in board.unrouted:
                n = int(u.get("unrouted_connections", 0))
                total += n
                items.append('{"net":"' + _escape_json_string(u.get("net", "")) +
                             '","unrouted_connections":' + str(n) + '}')
            data = ('{"unrouted_nets":[' + ",".join(items) + '],'
                    '"net_count":' + str(len(board.unrouted)) + ','
                    '"total_unrouted":' + str(total) + '}')
            return _build_success_response(rid, data)

        elif action == "run_drc":
            viols = json.dumps(board.drc_violations)
            data = ('{"violation_count":' + str(len(board.drc_violations)) +
                    ',"violations":' + viols + '}')
            return _build_success_response(rid, data)

        elif action == "get_vias":
            items = []
            for v in board.vias:
                items.append(
                    '{"x":' + str(v.x) + ',"y":' + str(v.y) + ','
                    '"net":"' + _escape_json_string(v.net) + '",'
                    '"size":' + str(v.size) + ',"hole_size":' + str(v.hole_size) + ','
                    '"low_layer":"' + _escape_json_string(v.low_layer) + '",'
                    '"high_layer":"' + _escape_json_string(v.high_layer) + '"}'
                )
            data = '{"vias":[' + ",".join(items) + '],"count":' + str(len(items)) + '}'
            return _build_success_response(rid, data)

        elif action == "place_via":
            x = params.get("x", "")
            y = params.get("y", "")
            if x in ("", None) or y in ("", None):
                return _build_error_response(rid, "MISSING_PARAM",
                                             "place_via requires x and y")
            # Sizes in fractions of a mil, as PCB_PlaceVia reads them, and
            # the same refusal of a via with no annular ring.
            size = float(params["size"]) if params.get("size") not in ("", None) else 50.0
            hole = (float(params["hole_size"])
                    if params.get("hole_size") not in ("", None) else 28.0)
            if hole <= 0 or size <= hole:
                return _build_error_response(rid, "BAD_SIZE",
                                             "size must be larger than hole_size")
            via = MockVia(
                x=int(float(x)), y=int(float(y)),
                net=params.get("net", ""),
                size=size, hole_size=hole,
                low_layer=params.get("low_layer") or "Top Layer",
                high_layer=params.get("high_layer") or "Bottom Layer",
            )
            board.vias.append(via)
            data = ('{"placed":true,"x":' + str(via.x) + ',"y":' + str(via.y) +
                    ',"size":' + json.dumps(via.size) +
                    ',"hole_size":' + json.dumps(via.hole_size) + '}')
            return _build_success_response(rid, data)

        elif action == "batch_move_components":
            moves = params.get("moves", "")
            if not moves:
                return _build_error_response(rid, "MISSING_PARAM",
                                             "moves parameter required")
            applied, failed = 0, 0
            for op in moves.split("|"):
                if not op:
                    continue
                fields = op.split(",")
                desig = fields[0].strip() if fields else ""
                comp = next((c for c in board.components
                             if c.designator == desig), None)
                if comp is None:
                    failed += 1
                    continue
                if len(fields) > 1 and fields[1] != "":
                    comp.x = int(float(fields[1]))
                if len(fields) > 2 and fields[2] != "":
                    comp.y = int(float(fields[2]))
                if len(fields) > 3 and fields[3] != "":
                    comp.rotation = float(fields[3])
                applied += 1
            data = '{"moves_applied":' + str(applied) + ',"failed":' + str(failed) + '}'
            return _build_success_response(rid, data)

        else:
            return _build_error_response(
                rid, "UNKNOWN_ACTION", f"Unknown pcb action: {action}"
            )


    # ------------------------------------------------------------------
    # Audit commands (SHAPE mirrors only -- canned {checked, violations,
    # items}; the real detection logic lives in Audit.pas and is NOT
    # reimplemented here. This exists so design_lint_report's orchestration
    # can be integration-tested through the real bridge.)
    # ------------------------------------------------------------------

    def _handle_audit(self, action: str, params: dict, rid: str) -> str:
        """Answer ANY audit action with a seeded or empty result.

        Deliberately a catch-all, unlike every other category here: the
        audit checks are Pascal logic, and re-implementing them would
        only test the copy. A test seeds ``audit_results[action]`` to
        drive the Python side against a known verdict.

        So this is NOT per-action coverage, and must not be read as any.
        tests/test_simulator_maturity_is_measured.py unmasks it by
        probing for an action that cannot exist, and excludes the whole
        category from the published simulator label; without that the
        claim inflates by 32 tools whose checks nothing implements.
        """
        if not action:
            return _build_error_response(rid, "UNKNOWN_ACTION",
                                         "Empty audit action")
        seeded = self.audit_results.get(action)
        if seeded is not None:
            checked = int(seeded.get("checked", 0))
            violations = int(seeded.get("violations", 0))
            items = seeded.get("items", [])
        else:
            checked, violations, items = 1, 0, []
        data = ('{"checked":' + str(checked) +
                ',"violations":' + str(violations) +
                ',"items":' + json.dumps(items) + '}')
        return _build_success_response(rid, data)


def _num(v: float) -> str:
    """Format a float without a trailing .0 for integers (JSON-friendly)."""
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def times_or_default(val: int) -> int:
    """Identity helper to avoid issues with lambda in dict literal."""
    return val
