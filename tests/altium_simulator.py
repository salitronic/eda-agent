# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
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
from pathlib import Path
from typing import Any, Optional


_VALID_ID_CHARS = set(string.ascii_letters + string.digits + "-_")


def _is_valid_request_id(s: str) -> bool:
    """Mirrors Pascal IsValidRequestId — alphanumeric/-/_ only, length 1..64."""
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
    """Build a success response — mirrors Main.pas BuildSuccessResponse."""
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
    """Build an error response — mirrors Main.pas BuildErrorResponseDetailed."""
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
        """Main polling loop — scans for any request_<id>.json file."""
        stop_path = self.workspace_dir / "stop"

        while self.running:
            if stop_path.exists():
                try:
                    stop_path.unlink()
                except OSError:
                    pass
                self.running = False
                break

            self._process_single_request()
            time.sleep(self._poll_interval)

    def _process_single_request(self) -> bool:
        """Mirrors Dispatcher.pas ProcessSingleRequest.

        Pascal reads request.json (single file), extracts the id from its
        body, and writes the response to response_<id>.json.
        """
        request_path = self.workspace_dir / "request.json"
        if not request_path.exists():
            return False

        try:
            content = request_path.read_text(encoding="utf-8")
        except (IOError, OSError, UnicodeDecodeError):
            try:
                request_path.unlink()
            except OSError:
                pass
            return False

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

        request_id = request_data.get("id", "")
        command = request_data.get("command", "")
        params = request_data.get("params", {})
        proto_ver = request_data.get("protocol_version")

        if not request_id or not _is_valid_request_id(request_id):
            return False

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

        response_path = self.workspace_dir / f"response_{request_id}.json"
        tmp_path = response_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(response_content, encoding="utf-8")
            tmp_path.replace(response_path)
        except (IOError, OSError):
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
            # Estimate net count from unique net names
            nets = set()
            for c in project.components:
                for p in c.pins:
                    nets.add(p["net"])
            net_count = len(nets)
            data = ('{"sheets":' + str(doc_count) +
                    ',"components":' + str(comp_count) +
                    ',"pins":' + str(pin_count) +
                    ',"nets":' + str(net_count) + '}')
            return _build_success_response(rid, data)

        elif action == "get_board_info":
            # Simulate a PCB board -- simplified
            data = ('{"origin_x":0,"origin_y":0,'
                    '"outline":[{"x":0,"y":0},{"x":4000,"y":0},'
                    '{"x":4000,"y":3000},{"x":0,"y":3000}],'
                    '"layers":["Top Layer","Bottom Layer","Top Overlay"]}')
            return _build_success_response(rid, data)

        elif action == "annotate":
            order = params.get("order", "down_then_across")
            return _build_success_response(
                rid, '{"annotated":true,"order":"' + order + '"}'
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

        elif action == "add_symbol_rectangle":
            if not self.lib_has_schlib:
                return _build_error_response(rid, "NO_SCHLIB",
                                             "No schematic library is active")
            return _build_success_response(rid, '{"success":true}')

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
            return _build_success_response(
                rid,
                '{"success":true,"model":"' + _escape_json_string(model_name) + '"}'
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
            query = params.get("query", "")
            return _build_success_response(
                rid,
                '{"success":true,"query":"' + _escape_json_string(query) + '"}'
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
            return _build_success_response(
                rid,
                '{"success":true,"process":"' + _escape_json_string(process_name) + '"}'
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
            # Route through modify with Selection=true
            obj_type_str = params.get("object_type", "")
            filter_str = params.get("filter", "")
            return self._gen_modify_objects(
                {"scope": "active_doc", "object_type": obj_type_str,
                 "filter": filter_str, "set": "Selection=true"}, rid
            )
        elif action == "deselect_all":
            return _build_success_response(rid, '{"deselected":true}')
        elif action == "zoom":
            zoom_action = params.get("action", "fit")
            return _build_success_response(
                rid, '{"action":"' + zoom_action + '"}'
            )
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


def times_or_default(val: int) -> int:
    """Identity helper to avoid issues with lambda in dict literal."""
    return val
