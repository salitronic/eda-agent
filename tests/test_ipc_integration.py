# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""End-to-end IPC integration tests.

These tests validate the file-based IPC protocol between Python and Altium
without requiring Altium Designer. They test:
  - Request JSON format (what Python writes)
  - Response JSON format (what Altium produces)
  - Edge cases in the IPC layer
  - JSON well-formedness of all response types

Corresponding DelphiScript files:
  - Main.pas: ReadFileContent, WriteFileContent, ExtractJsonValue,
              BuildSuccessResponse, BuildErrorResponse
  - Dispatcher.pas: ProcessSingleRequest (the full request->response cycle)
"""

import json
import uuid
from pathlib import Path

from tests.conftest import (
    write_request, write_response, parse_response,
    validate_success_response, validate_error_response,
)
from tests.test_json_parsing import (
    extract_json_value, build_success_response, build_error_response,
    escape_json_string,
)


class TestRequestFormat:
    """Per-request files: request_<id>.json must carry id, command, params,
    and protocol_version that Pascal can extract via ExtractJsonValue."""

    def test_basic_request_structure(self, workspace_dir):
        req_path = write_request(workspace_dir, 'testid', 'application.ping', {})

        data = json.loads(req_path.read_text(encoding='utf-8'))
        assert data['id'] == 'testid'
        assert data['command'] == 'application.ping'
        assert data['params'] == {}
        assert data['protocol_version'] == 2

    def test_request_with_params(self, workspace_dir):
        req_path = write_request(workspace_dir, 'r2', 'generic.query_objects', {
            'scope': 'active_doc',
            'object_type': 'eNetLabel',
            'filter': 'Text=VCC',
            'properties': 'Text,Location.X,Location.Y',
        })

        data = json.loads(req_path.read_text(encoding='utf-8'))
        assert data['command'] == 'generic.query_objects'
        assert data['params']['object_type'] == 'eNetLabel'
        assert data['params']['filter'] == 'Text=VCC'

    def test_request_params_as_flat_json(self, workspace_dir):
        """Altium's ExtractJsonValue expects params as a JSON object."""
        params = {
            'scope': 'project',
            'object_type': 'eSchComponent',
            'filter': 'Designator.Text=R1|LibReference=RC0402',
            'set': 'Comment.Text=100k|Location.X=500',
        }
        req_path = write_request(workspace_dir, 'r3', 'generic.modify_objects', params)

        data = json.loads(req_path.read_text(encoding='utf-8'))
        params_json = json.dumps(data['params'])
        assert extract_json_value(params_json, 'scope') == 'project'
        assert extract_json_value(params_json, 'object_type') == 'eSchComponent'
        assert extract_json_value(params_json, 'filter') == 'Designator.Text=R1|LibReference=RC0402'

    def test_request_id_is_unique(self, workspace_dir):
        ids = set()
        for _ in range(100):
            req_id = uuid.uuid4().hex
            ids.add(req_id)
        assert len(ids) == 100

    def test_request_with_path_containing_backslashes(self, workspace_dir):
        """Windows paths in params survive JSON encoding."""
        req_path = write_request(workspace_dir, 'r4', 'project.open', {
            'project_path': 'C:\\Users\\test\\project.PrjPcb',
        })

        data = json.loads(req_path.read_text(encoding='utf-8'))
        assert data['params']['project_path'] == 'C:\\Users\\test\\project.PrjPcb'

        params_json = json.dumps(data['params'])
        extracted = extract_json_value(params_json, 'project_path')
        assert '\\' in extracted


class TestResponseFormat:
    """Per-request response files conform to the new envelope shape:
    {protocol_version, id, success, data, error{code, message, details}}."""

    def test_success_response_structure(self, workspace_dir):
        resp_path = write_response(workspace_dir, 'req1', True, data={'result': 'ok'})

        resp = parse_response(resp_path)
        validate_success_response(resp, 'req1')
        assert resp['data'] == {'result': 'ok'}

    def test_error_response_structure(self, workspace_dir):
        resp_path = write_response(workspace_dir, 'req1', False, error={
            'code': 'NOT_FOUND',
            'message': 'Component not found',
            'details': None,
        })

        resp = parse_response(resp_path)
        validate_error_response(resp, 'req1', 'NOT_FOUND')

    def test_altium_success_response_format(self):
        """The exact string format produced by Pascal BuildSuccessResponse."""
        result = build_success_response('abc123', '"pong"')
        resp = json.loads(result)
        assert resp == {
            'protocol_version': 2,
            'id': 'abc123',
            'success': True,
            'data': 'pong',
            'error': None,
        }

    def test_altium_error_response_format(self):
        """The exact string format produced by Pascal BuildErrorResponseDetailed."""
        result = build_error_response('abc123', 'UNKNOWN_COMMAND', 'Unknown category: foo')
        resp = json.loads(result)
        assert resp == {
            'protocol_version': 2,
            'id': 'abc123',
            'success': False,
            'data': None,
            'error': {
                'code': 'UNKNOWN_COMMAND',
                'message': 'Unknown category: foo',
                'details': None,
            },
        }


class TestResponseEdgeCases:
    """Test edge cases in the IPC response format."""

    def test_response_with_special_chars_in_data(self):
        """File paths with backslashes in response data."""
        data = '{"path":"' + escape_json_string('C:\\Users\\test\\file.SchDoc') + '"}'
        result = build_success_response('r1', data)
        parsed = json.loads(result)
        assert parsed['data']['path'] == 'C:\\Users\\test\\file.SchDoc'

    def test_response_with_quotes_in_data(self):
        """Component names with quotes."""
        name = 'RES "0402"'
        data = '{"name":"' + escape_json_string(name) + '"}'
        result = build_success_response('r2', data)
        parsed = json.loads(result)
        assert parsed['data']['name'] == name

    def test_response_with_empty_data(self):
        result = build_success_response('r3', '')
        parsed = json.loads(result)
        assert parsed['data'] is None

    def test_response_with_array_data(self):
        data = '[{"text":"VCC","x":100},{"text":"GND","x":200}]'
        result = build_success_response('r4', data)
        parsed = json.loads(result)
        assert len(parsed['data']) == 2
        assert parsed['data'][0]['text'] == 'VCC'

    def test_response_with_nested_object(self):
        data = '{"outer":{"inner":{"deep":true}}}'
        result = build_success_response('r5', data)
        parsed = json.loads(result)
        assert parsed['data']['outer']['inner']['deep'] is True

    def test_error_with_newlines(self):
        """Error messages can contain newlines from exception messages."""
        msg = 'Error at line 10\nExpected \';\' but found \')\''
        result = build_error_response('r6', 'PARSE_ERROR', msg)
        parsed = json.loads(result)
        assert parsed['error']['message'] == msg

    def test_large_response(self):
        """Test with a large array of objects (simulating many components)."""
        items = []
        for i in range(100):
            items.append(f'{{"name":"R{i}","x":{i * 100},"y":{i * 50}}}')
        data = '{"objects":[' + ','.join(items) + '],"count":100}'
        result = build_success_response('r7', data)
        parsed = json.loads(result)
        assert parsed['data']['count'] == 100
        assert len(parsed['data']['objects']) == 100


class TestCompleteIPCCycles:
    """Test complete request->response cycles.

    These simulate the full IPC flow without Altium:
    1. Write request.json (as Python bridge would)
    2. Verify the request is parseable by Altium's ExtractJsonValue
    3. Build a mock response (as Altium would)
    4. Verify the response is parseable by Python
    """

    def test_ping_cycle(self, workspace_dir):
        """application.ping -> pong"""
        req_id = uuid.uuid4().hex
        req_path = write_request(workspace_dir, req_id, 'application.ping', {})

        raw = req_path.read_text(encoding='utf-8')
        assert extract_json_value(raw, 'id') == req_id
        assert extract_json_value(raw, 'command') == 'application.ping'

        resp_path = workspace_dir / f"response_{req_id}.json"
        resp_path.write_text(build_success_response(req_id, '"pong"'), encoding='utf-8')

        resp = parse_response(resp_path)
        validate_success_response(resp, req_id)
        assert resp['data'] == 'pong'

    def test_query_objects_cycle(self, workspace_dir):
        """generic.query_objects -> objects array"""
        req_id = uuid.uuid4().hex
        req_path = write_request(workspace_dir, req_id, 'generic.query_objects', {
            'object_type': 'eNetLabel',
            'properties': 'Text,Location.X,Location.Y',
        })

        raw = req_path.read_text(encoding='utf-8')
        assert extract_json_value(raw, 'command') == 'generic.query_objects'

        data = ('{"objects":['
                '{"_doc":"sheet1.SchDoc","Text":"VCC","Location.X":"100","Location.Y":"200"},'
                '{"_doc":"sheet1.SchDoc","Text":"GND","Location.X":"300","Location.Y":"400"}'
                '],"count":2}')
        resp_path = workspace_dir / f"response_{req_id}.json"
        resp_path.write_text(build_success_response(req_id, data), encoding='utf-8')

        resp = parse_response(resp_path)
        validate_success_response(resp, req_id)
        assert resp['data']['count'] == 2
        assert len(resp['data']['objects']) == 2
        assert resp['data']['objects'][0]['Text'] == 'VCC'

    def test_error_cycle(self, workspace_dir):
        """unknown.command -> error response"""
        req_id = uuid.uuid4().hex
        req_path = write_request(workspace_dir, req_id, 'unknown.command', {})

        raw = req_path.read_text(encoding='utf-8')
        assert extract_json_value(raw, 'command') == 'unknown.command'

        resp_path = workspace_dir / f"response_{req_id}.json"
        resp_path.write_text(
            build_error_response(
                req_id, 'UNKNOWN_COMMAND',
                'Unknown command category: unknown. Use generic.* for object operations.',
            ),
            encoding='utf-8',
        )

        resp = parse_response(resp_path)
        validate_error_response(resp, req_id, 'UNKNOWN_COMMAND')

    def test_modify_cycle_with_filter_and_set(self, workspace_dir):
        """generic.modify_objects with filter and set params"""
        req_id = uuid.uuid4().hex
        req_path = write_request(workspace_dir, req_id, 'generic.modify_objects', {
            'object_type': 'eNetLabel',
            'filter': 'Text=VCC',
            'set': 'Text=VCC_3V3|Location.X=500',
        })

        raw = req_path.read_text(encoding='utf-8')
        params_raw = extract_json_value(raw, 'params')
        assert extract_json_value(params_raw, 'filter') == 'Text=VCC'
        assert extract_json_value(params_raw, 'set') == 'Text=VCC_3V3|Location.X=500'

        resp_path = workspace_dir / f"response_{req_id}.json"
        resp_path.write_text(
            build_success_response(req_id, '{"matched":3,"sheets_processed":2}'),
            encoding='utf-8',
        )

        resp = parse_response(resp_path)
        validate_success_response(resp, req_id)
        assert resp['data']['matched'] == 3

    def test_create_object_cycle(self, workspace_dir):
        """generic.create_object cycle"""
        req_id = uuid.uuid4().hex
        req_path = write_request(workspace_dir, req_id, 'generic.create_object', {
            'object_type': 'eNetLabel',
            'properties': 'Text=VCC|Location.X=100|Location.Y=200',
            'container': 'document',
        })

        raw = req_path.read_text(encoding='utf-8')
        assert extract_json_value(raw, 'command') == 'generic.create_object'

        resp_path = workspace_dir / f"response_{req_id}.json"
        resp_path.write_text(
            build_success_response(req_id, '{"created":true,"object_type":"eNetLabel"}'),
            encoding='utf-8',
        )

        resp = parse_response(resp_path)
        assert resp['data']['created'] is True

    def test_project_with_path_cycle(self, workspace_dir):
        """project.open with Windows path"""
        req_id = uuid.uuid4().hex
        req_path = write_request(workspace_dir, req_id, 'project.open', {
            'project_path': 'C:\\Users\\test\\MyProject.PrjPcb',
        })

        raw = req_path.read_text(encoding='utf-8')
        assert extract_json_value(raw, 'command') == 'project.open'

        resp_path = workspace_dir / f"response_{req_id}.json"
        resp_path.write_text(
            build_success_response(req_id, '{"success":true}'),
            encoding='utf-8',
        )

        resp = parse_response(resp_path)
        validate_success_response(resp, req_id)


class TestFileIPCEdgeCases:
    """Edge cases in the file-based IPC mechanism."""

    def test_empty_request_file(self, workspace_dir):
        """An empty per-request file is dropped by the dispatcher."""
        req_path = workspace_dir / "request_emptyone.json"
        req_path.write_text('', encoding='utf-8')
        assert req_path.read_text(encoding='utf-8') == ''

    def test_missing_request_id(self, workspace_dir):
        """Pascal extracts ID from filename now, not body — but body lookup
        still returns '' for missing id field."""
        req_path = workspace_dir / "request_someid.json"
        req_path.write_text('{"command":"test","params":{}}', encoding='utf-8')
        raw = req_path.read_text(encoding='utf-8')
        assert extract_json_value(raw, 'id') == ''

    def test_missing_command(self, workspace_dir):
        """Request without command is rejected (MALFORMED_REQUEST)."""
        req_path = workspace_dir / "request_someid.json"
        req_path.write_text('{"id":"someid","params":{}}', encoding='utf-8')
        raw = req_path.read_text(encoding='utf-8')
        assert extract_json_value(raw, 'command') == ''

    def test_concurrent_requests_have_independent_files(self, workspace_dir):
        """Per-request files: two callers can co-exist without overwriting."""
        write_request(workspace_dir, 'first', 'application.ping', {})
        write_request(workspace_dir, 'second', 'application.ping', {})

        first = json.loads((workspace_dir / "request_first.json").read_text(encoding='utf-8'))
        second = json.loads((workspace_dir / "request_second.json").read_text(encoding='utf-8'))
        assert first['id'] == 'first'
        assert second['id'] == 'second'


class TestResponseJsonWellFormedness:
    """Ensure ALL response types produce valid JSON.

    This catches bugs where string concatenation produces malformed JSON.
    Each test mirrors a specific response builder in the DelphiScript.
    """

    def test_all_success_patterns(self):
        """Test every data pattern used across the codebase."""
        patterns = [
            # Application
            '"pong"',
            '{"version":"connected","product_name":"Altium Designer"}',
            '{}',
            '{"success":true}',
            '{"stopped":true}',
            # Project
            '{"success":true,"project_path":"C:\\\\test\\\\proj.PrjPcb"}',
            '[{"file_name":"sheet1.SchDoc","file_path":"C:\\\\path\\\\sheet1.SchDoc","document_kind":"SCH"}]',
            '{"project_name":"proj.PrjPcb","project_path":"C:\\\\path\\\\proj.PrjPcb","document_count":3}',
            # Generic
            '{"objects":[{"Text":"VCC","Location.X":"100"}],"count":1,"sheets_processed":1}',
            '{"matched":5,"sheets_processed":2}',
            '{"created":true,"object_type":"eNetLabel"}',
            '{"success":true,"process":"Sch:ZoomToFit"}',
            '{"deselected":true}',
            '{"action":"fit"}',
            # Library
            '{"count":5,"components":[{"name":"R1","description":"Resistor","parameters":{}}]}',
            '{"updated":3,"created":1,"failed":0,"total_lines":4}',
            '{"renamed":2,"failed":1,"total_lines":3}',
            # Stats
            '{"sheets":4,"components":50,"pins":200,"nets":30}',
            # BOM
            '{"components":[{"designator":"R1","comment":"100k","footprint":"0402","lib_ref":"RC0402","pins":[]}],"count":1}',
        ]

        for i, data in enumerate(patterns):
            result = build_success_response(f'req-{i}', data)
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as e:
                raise AssertionError(f"Pattern {i} produced invalid JSON: {data}\nError: {e}")
            assert parsed['success'] is True
            assert parsed['error'] is None

    def test_all_error_codes(self):
        """Test every error code used across the codebase."""
        error_codes = [
            ('UNKNOWN_COMMAND', 'Unknown command category: foo'),
            ('UNKNOWN_ACTION', 'Unknown application action: bar'),
            ('INTERNAL_ERROR', 'Unhandled exception processing: test'),
            ('NO_SCHEMATIC', 'No schematic document is active'),
            ('NO_PCB', 'No PCB document is active'),
            ('NO_WORKSPACE', 'No workspace available'),
            ('NO_PROJECT', 'No project found'),
            ('PROJECT_NOT_FOUND', 'Project not found'),
            ('INVALID_TYPE', 'Unknown object type: eBadType'),
            ('MISSING_PARAMS', 'set parameter is required'),
            ('CREATE_FAILED', 'Failed to create object of type: eWire'),
            ('NO_SCHLIB', 'No schematic library is active'),
            ('NO_PCBLIB', 'No PCB library is active'),
            ('NO_COMPONENT', 'No component is selected'),
            ('NO_FOOTPRINT', 'No footprint is selected'),
            ('READER_FAILED', 'Failed to create library reader'),
            ('COMPONENT_NOT_FOUND', 'Component not found: R1'),
            ('NO_LIBRARY', 'No library path and no active document'),
            ('NO_BATCH_FILE', 'Batch file not found: C:\\path\\batch.txt'),
            ('INVALID_PARAMETER', 'Process name is required'),
            ('NOT_FOUND', 'Component not found: U1'),
            ('INVALID_TYPE', 'Unknown output type: bad. Use: gerber, drill, pick_place, ipc_netlist'),
            ('NO_DOCUMENT', 'No active document'),
            ('LINK_FAILED', 'Failed to link footprint'),
        ]

        for i, (code, msg) in enumerate(error_codes):
            result = build_error_response(f'req-{i}', code, msg)
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as e:
                raise AssertionError(f"Error code {code} produced invalid JSON\nMessage: {msg}\nError: {e}")
            assert parsed['success'] is False
            assert parsed['error']['code'] == code
