# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Exhaustive tests of JSON parsing/building logic.

Each function here is an EXACT mirror of the corresponding DelphiScript function.
Same algorithm, same edge cases. Any divergence IS a bug (either in the test or
the script).

Corresponding DelphiScript files:
  - Main.pas: ExtractJsonValue, BuildSuccessResponse, BuildErrorResponse
  - Utils.pas: EscapeJsonString, ExtractJsonArray
"""

import json
import re


# ---------------------------------------------------------------------------
# Python reimplementations — EXACT mirrors of DelphiScript
# ---------------------------------------------------------------------------


def is_whitespace_or_colon(s: str, idx: int) -> bool:
    """Mirror: Main.pas:74 IsWhitespaceOrColon"""
    if idx >= len(s):
        return False
    c = s[idx]
    return c in (' ', ':', '\t', '\n', '\r')


def is_delimiter(s: str, idx: int) -> bool:
    """Mirror: Main.pas:82 IsDelimiter"""
    if idx >= len(s):
        return True  # past end = delimiter (C = '' case in Delphi)
    c = s[idx]
    return c in ('', ',', '}', ']', ' ', '\t', '\n', '\r')


def extract_json_value(json_str: str, key: str) -> str:
    """Mirror: Main.pas:90 ExtractJsonValue

    This is a simplified JSON parser using string searching.
    It finds the FIRST occurrence of "key" and extracts the value.
    Handles string values, object values (brace-matched), and bare values.

    NOTE: DelphiScript uses 1-based indexing; Python uses 0-based.
    The algorithm is identical but indices are shifted by -1.
    """
    result = ''
    search_key = '"' + key + '"'
    start_pos = json_str.find(search_key)
    if start_pos < 0:
        return result

    start_pos += len(search_key)

    # Skip whitespace and colon
    while start_pos < len(json_str) and is_whitespace_or_colon(json_str, start_pos):
        start_pos += 1

    if start_pos >= len(json_str):
        return result

    if json_str[start_pos] == '"':
        # String value
        start_pos += 1
        end_pos = start_pos
        while end_pos < len(json_str):
            if json_str[end_pos] == '"' and (end_pos == start_pos or json_str[end_pos - 1] != '\\'):
                break
            end_pos += 1
        result = json_str[start_pos:end_pos]

    elif json_str[start_pos] == '{':
        # Object value - find matching brace
        end_pos = start_pos
        brace_count = 1
        end_pos += 1
        while end_pos < len(json_str) and brace_count > 0:
            if json_str[end_pos] == '{':
                brace_count += 1
            elif json_str[end_pos] == '}':
                brace_count -= 1
            end_pos += 1
        result = json_str[start_pos:end_pos]

    else:
        # Number or other bare value
        end_pos = start_pos
        while end_pos < len(json_str) and not is_delimiter(json_str, end_pos):
            end_pos += 1
        result = json_str[start_pos:end_pos]

    return result


def extract_json_array(json_str: str, key: str) -> str:
    """Mirror: Utils.pas:238 ExtractJsonArray

    Finds a JSON array value by key and returns the full array string
    including brackets, using bracket-counting to find the matching ']'.
    """
    result = ''
    search_key = '"' + key + '"'
    start_pos = json_str.find(search_key)
    if start_pos < 0:
        return result

    start_pos += len(search_key)

    # Skip whitespace and colon
    while start_pos < len(json_str) and is_whitespace_or_colon(json_str, start_pos):
        start_pos += 1

    if start_pos >= len(json_str) or json_str[start_pos] != '[':
        return result

    end_pos = start_pos
    bracket_count = 1
    end_pos += 1
    while end_pos < len(json_str) and bracket_count > 0:
        if json_str[end_pos] == '[':
            bracket_count += 1
        elif json_str[end_pos] == ']':
            bracket_count -= 1
        end_pos += 1

    result = json_str[start_pos:end_pos]
    return result


def escape_json_string(s: str) -> str:
    """Mirror: Utils.pas:64 EscapeJsonString

    Order matters! Backslash must be escaped FIRST, then quotes, then
    control characters. This matches the DelphiScript StringReplace order.
    """
    result = s
    result = result.replace('\\', '\\\\')
    result = result.replace('"', '\\"')
    result = result.replace('\r', '\\r')
    result = result.replace('\n', '\\n')
    result = result.replace('\t', '\\t')
    return result


def build_success_response(request_id: str, data: str) -> str:
    """Mirror: Main.pas:146 BuildSuccessResponse

    NOTE: data is a raw JSON string, not a Python object. The DelphiScript
    concatenates it directly into the response JSON string.
    """
    if data == '':
        data = 'null'
    return '{"id":"' + request_id + '","success":true,"data":' + data + ',"error":null}'


def build_error_response(request_id: str, error_code: str, error_msg: str) -> str:
    """Mirror: Main.pas:153 BuildErrorResponse

    The error message gets inline JSON-escaped (same order as EscapeJsonString).
    """
    # Inline escape — same as the DelphiScript in BuildErrorResponse
    error_msg = error_msg.replace('\\', '\\\\')
    error_msg = error_msg.replace('"', '\\"')
    error_msg = error_msg.replace('\r', '\\r')
    error_msg = error_msg.replace('\n', '\\n')
    error_msg = error_msg.replace('\t', '\\t')
    return ('{"id":"' + request_id + '","success":false,"data":null,'
            '"error":{"code":"' + error_code + '","message":"' + error_msg + '"}}')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractJsonValue:
    """Tests for ExtractJsonValue (Main.pas:90)."""

    def test_simple_string(self):
        j = '{"name":"hello"}'
        assert extract_json_value(j, 'name') == 'hello'

    def test_string_with_spaces_around_colon(self):
        j = '{"name" : "hello"}'
        assert extract_json_value(j, 'name') == 'hello'

    def test_empty_string_value(self):
        j = '{"name":""}'
        assert extract_json_value(j, 'name') == ''

    def test_number_value(self):
        j = '{"count":42}'
        assert extract_json_value(j, 'count') == '42'

    def test_negative_number(self):
        j = '{"offset":-10}'
        assert extract_json_value(j, 'offset') == '-10'

    def test_boolean_true(self):
        j = '{"active":true}'
        assert extract_json_value(j, 'active') == 'true'

    def test_boolean_false(self):
        j = '{"active":false}'
        assert extract_json_value(j, 'active') == 'false'

    def test_null_value(self):
        j = '{"data":null}'
        assert extract_json_value(j, 'data') == 'null'

    def test_object_value(self):
        j = '{"params":{"x":100,"y":200}}'
        result = extract_json_value(j, 'params')
        assert result == '{"x":100,"y":200}'

    def test_nested_object(self):
        j = '{"outer":{"inner":{"deep":"value"}}}'
        result = extract_json_value(j, 'outer')
        assert result == '{"inner":{"deep":"value"}}'
        # The extracted object can itself be parsed
        assert extract_json_value(result, 'inner') == '{"deep":"value"}'

    def test_key_not_found(self):
        j = '{"name":"hello"}'
        assert extract_json_value(j, 'missing') == ''

    def test_partial_key_name_no_match(self):
        """ExtractJsonValue searches for the exact quoted key.
        'name' should NOT match 'full_name'."""
        j = '{"full_name":"John","name":"Jane"}'
        assert extract_json_value(j, 'name') == 'Jane'

    def test_key_is_prefix_of_another(self):
        """'id' should not match 'id_extra' — it searches for '"id"' exactly."""
        j = '{"id_extra":"wrong","id":"correct"}'
        # Finds first occurrence of '"id"' — since '"id_extra"' contains '"id'
        # but not '"id"' followed by whitespace/colon, let's check carefully.
        # Actually '"id_extra"' does NOT contain the exact '"id"' substring
        # because the next char after 'id' is '_' not '"'.
        # But wait: '"id"' IS a substring of... no. '"id_extra"' = "id_extra"
        # while we search for "id" = '"id"'. The search finds '"id"' in '"id":"correct"'
        # since "id_extra" does not contain '"id"'.
        assert extract_json_value(j, 'id') == 'correct'

    def test_key_is_suffix_of_another(self):
        """'id' should match the first occurrence of '"id"'."""
        j = '{"request_id":"first","id":"second"}'
        # '"request_id"' does NOT contain '"id"' as substring because
        # the chars before 'id' in '"request_id"' are 'request_' not '"'
        # Actually let's think again: Pos('"id"', '"request_id"') —
        # '"request_id"' = [", r, e, q, u, e, s, t, _, i, d, "]
        # '"id"' = [", i, d, "]
        # Searching for '"id"' in '{"request_id":"first","id":"second"}'
        # Position of '"id"' — first occurrence after "request_id" is in "id":"second"
        # But does 'request_id"' contain '"id"'? Let's check:
        # ...t_id":"first"... — the sequence '_id":"' contains 'id":'
        # but we search for '"id"' — that's [quote][i][d][quote]
        # In '_id":"' we have: _ i d " : " — so 'id"' at positions...
        # The full string: {"request_id":"first","id":"second"}
        # Let me find all occurrences of '"id"':
        # Position: ...quest_id":"first","id":"second"...
        #                    ^ this is 'd"' but preceded by 'i' preceded by '_'
        #                    Not a match for '"id"' because the char before 'id"' is '_' not '"'
        # Wait, let me be more careful:
        # The string is: {"request_id":"first","id":"second"}
        # Looking for '"id"': The first '"id"' occurs at position 21 (0-based)
        # which is in ',"id":"second"'
        assert extract_json_value(j, 'id') == 'second'

    def test_string_with_escaped_quotes(self):
        j = r'{"msg":"say \"hello\""}'
        assert extract_json_value(j, 'msg') == r'say \"hello\"'

    def test_string_with_backslashes(self):
        j = r'{"path":"C:\\Users\\test"}'
        assert extract_json_value(j, 'path') == r'C:\\Users\\test'

    def test_multiple_keys(self):
        j = '{"id":"123","command":"test","params":{"x":1}}'
        assert extract_json_value(j, 'id') == '123'
        assert extract_json_value(j, 'command') == 'test'
        assert extract_json_value(j, 'params') == '{"x":1}'

    def test_value_with_tab_whitespace(self):
        j = '{"key":\t"value"}'
        assert extract_json_value(j, 'key') == 'value'

    def test_value_with_newline_whitespace(self):
        j = '{"key":\n"value"}'
        assert extract_json_value(j, 'key') == 'value'

    def test_floating_point_value(self):
        j = '{"angle":45.5}'
        assert extract_json_value(j, 'angle') == '45.5'

    def test_value_at_end_of_json(self):
        j = '{"last":99}'
        assert extract_json_value(j, 'last') == '99'

    def test_empty_json(self):
        j = '{}'
        assert extract_json_value(j, 'key') == ''

    def test_real_request_format(self):
        """Test with the actual request format used by the bridge."""
        j = '{"id":"abc-123","command":"application.ping","params":{}}'
        assert extract_json_value(j, 'id') == 'abc-123'
        assert extract_json_value(j, 'command') == 'application.ping'
        assert extract_json_value(j, 'params') == '{}'

    def test_real_response_format(self):
        """Test with the actual response format produced by Altium."""
        j = '{"id":"abc-123","success":true,"data":{"version":"connected"},"error":null}'
        assert extract_json_value(j, 'id') == 'abc-123'
        assert extract_json_value(j, 'success') == 'true'
        assert extract_json_value(j, 'data') == '{"version":"connected"}'
        assert extract_json_value(j, 'error') == 'null'


class TestExtractJsonArray:
    """Tests for ExtractJsonArray (Utils.pas:238)."""

    def test_simple_array(self):
        j = '{"items":[1,2,3]}'
        assert extract_json_array(j, 'items') == '[1,2,3]'

    def test_string_array(self):
        j = '{"names":["a","b","c"]}'
        assert extract_json_array(j, 'names') == '["a","b","c"]'

    def test_empty_array(self):
        j = '{"items":[]}'
        assert extract_json_array(j, 'items') == '[]'

    def test_nested_arrays(self):
        j = '{"matrix":[[1,2],[3,4]]}'
        assert extract_json_array(j, 'matrix') == '[[1,2],[3,4]]'

    def test_array_with_objects(self):
        j = '{"items":[{"x":1},{"x":2}]}'
        assert extract_json_array(j, 'items') == '[{"x":1},{"x":2}]'

    def test_key_not_found(self):
        j = '{"items":[1,2,3]}'
        assert extract_json_array(j, 'missing') == ''

    def test_value_not_array(self):
        """When the value is not an array, return empty string."""
        j = '{"items":"not_array"}'
        assert extract_json_array(j, 'items') == ''

    def test_array_with_spaces(self):
        j = '{"items" : [1, 2, 3]}'
        assert extract_json_array(j, 'items') == '[1, 2, 3]'


class TestEscapeJsonString:
    """Tests for EscapeJsonString (Utils.pas:64)."""

    def test_no_escaping_needed(self):
        assert escape_json_string('hello') == 'hello'

    def test_empty_string(self):
        assert escape_json_string('') == ''

    def test_backslash(self):
        assert escape_json_string('a\\b') == 'a\\\\b'

    def test_double_quote(self):
        assert escape_json_string('say "hello"') == 'say \\"hello\\"'

    def test_carriage_return(self):
        assert escape_json_string('line1\rline2') == 'line1\\rline2'

    def test_newline(self):
        assert escape_json_string('line1\nline2') == 'line1\\nline2'

    def test_tab(self):
        assert escape_json_string('col1\tcol2') == 'col1\\tcol2'

    def test_windows_newline(self):
        assert escape_json_string('line1\r\nline2') == 'line1\\r\\nline2'

    def test_backslash_and_quote_together(self):
        """Order matters: backslash first, then quote."""
        # Input: C:\path\to\"file"
        # After \\ escape: C:\\path\\to\\"file"
        # After " escape: C:\\path\\to\\\\"file\\"  -- wait, let's be careful
        #
        # Input string: a\"b
        # Step 1 (\ -> \\): a\\"b
        # Step 2 (" -> \"): a\\\\"b   -- no, wait
        #
        # Let me trace through character by character.
        # Input: backslash, quote
        # Step 1: replace \ with \\ => \\, quote  => the string is now: \\, "
        # Step 2: replace " with \" => \\, \"    => the string is now: \\\"
        s = '\\"'
        result = escape_json_string(s)
        assert result == '\\\\\\"'

    def test_windows_path(self):
        """Test escaping a Windows file path."""
        s = 'C:\\Users\\test\\file.txt'
        expected = 'C:\\\\Users\\\\test\\\\file.txt'
        assert escape_json_string(s) == expected

    def test_all_special_chars(self):
        """Test a string with all special characters."""
        s = 'a\\b"c\rd\ne\tf'
        expected = 'a\\\\b\\"c\\rd\\ne\\tf'
        assert escape_json_string(s) == expected

    def test_result_is_valid_json_string_content(self):
        """After escaping, wrapping in quotes should produce valid JSON."""
        test_strings = [
            'hello world',
            'C:\\Users\\test',
            'say "hello"',
            'line1\nline2',
            'mixed\t"quotes\\and\nnewlines"',
        ]
        for s in test_strings:
            escaped = escape_json_string(s)
            json_str = '"' + escaped + '"'
            # Should be valid JSON
            parsed = json.loads(json_str)
            assert parsed == s, f"Round-trip failed for: {s!r}"

    def test_unicode_passthrough(self):
        """Unicode characters should pass through unchanged."""
        s = 'resistor \u03a9 100k\u2126'
        assert escape_json_string(s) == s


class TestBuildSuccessResponse:
    """Tests for BuildSuccessResponse (Main.pas:146)."""

    def test_with_string_data(self):
        result = build_success_response('req-1', '"pong"')
        parsed = json.loads(result)
        assert parsed['id'] == 'req-1'
        assert parsed['success'] is True
        assert parsed['data'] == 'pong'
        assert parsed['error'] is None

    def test_with_object_data(self):
        result = build_success_response('req-2', '{"version":"1.0"}')
        parsed = json.loads(result)
        assert parsed['data'] == {'version': '1.0'}

    def test_with_null_data(self):
        result = build_success_response('req-3', 'null')
        parsed = json.loads(result)
        assert parsed['data'] is None

    def test_with_empty_data_becomes_null(self):
        """Empty string data is replaced with 'null'."""
        result = build_success_response('req-4', '')
        parsed = json.loads(result)
        assert parsed['data'] is None

    def test_with_array_data(self):
        result = build_success_response('req-5', '[1,2,3]')
        parsed = json.loads(result)
        assert parsed['data'] == [1, 2, 3]

    def test_with_boolean_data(self):
        result = build_success_response('req-6', 'true')
        parsed = json.loads(result)
        assert parsed['data'] is True

    def test_with_number_data(self):
        result = build_success_response('req-7', '42')
        parsed = json.loads(result)
        assert parsed['data'] == 42

    def test_response_is_valid_json(self):
        """All responses should be valid JSON."""
        test_cases = ['"hello"', '{}', '[]', 'null', 'true', 'false', '42', '3.14']
        for data in test_cases:
            result = build_success_response('test', data)
            json.loads(result)  # Should not raise

    def test_real_ping_response(self):
        """Matches App_Ping output."""
        result = build_success_response('r1', '"pong"')
        parsed = json.loads(result)
        assert parsed['data'] == 'pong'

    def test_real_version_response(self):
        """Matches App_GetVersion output."""
        data = '{"version":"connected","product_name":"Altium Designer"}'
        result = build_success_response('r2', data)
        parsed = json.loads(result)
        assert parsed['data']['version'] == 'connected'
        assert parsed['data']['product_name'] == 'Altium Designer'


class TestBuildErrorResponse:
    """Tests for BuildErrorResponse (Main.pas:153)."""

    def test_simple_error(self):
        result = build_error_response('req-1', 'NOT_FOUND', 'Item not found')
        parsed = json.loads(result)
        assert parsed['id'] == 'req-1'
        assert parsed['success'] is False
        assert parsed['data'] is None
        assert parsed['error']['code'] == 'NOT_FOUND'
        assert parsed['error']['message'] == 'Item not found'

    def test_error_with_quotes_in_message(self):
        result = build_error_response('req-2', 'ERR', 'Cannot find "file.txt"')
        parsed = json.loads(result)
        assert parsed['error']['message'] == 'Cannot find "file.txt"'

    def test_error_with_backslash_in_message(self):
        result = build_error_response('req-3', 'ERR', 'Path: C:\\Users\\test')
        parsed = json.loads(result)
        assert parsed['error']['message'] == 'Path: C:\\Users\\test'

    def test_error_with_newline_in_message(self):
        result = build_error_response('req-4', 'ERR', 'Line1\nLine2')
        parsed = json.loads(result)
        assert parsed['error']['message'] == 'Line1\nLine2'

    def test_error_with_all_special_chars(self):
        msg = 'Error: "path\\to\\file"\r\n\tdetails'
        result = build_error_response('req-5', 'ERR', msg)
        parsed = json.loads(result)
        assert parsed['error']['message'] == msg

    def test_response_is_valid_json(self):
        """Error responses should always be valid JSON."""
        tricky_messages = [
            '',
            'simple',
            'with "quotes"',
            'with \\backslashes\\',
            'with\nnewlines\r\nand\ttabs',
            'mixed: "quotes" and \\paths\\ and\nnewlines',
        ]
        for msg in tricky_messages:
            result = build_error_response('t', 'E', msg)
            parsed = json.loads(result)
            assert parsed['error']['message'] == msg

    def test_real_unknown_command_error(self):
        """Matches ProcessCommand unknown category error."""
        result = build_error_response('r1', 'UNKNOWN_COMMAND',
                                       'Unknown command category: foo. Use generic.* for object operations.')
        parsed = json.loads(result)
        assert 'foo' in parsed['error']['message']


class TestExtractJsonValueEdgeCases:
    """Edge cases that test the boundaries of the JSON parser."""

    def test_empty_string(self):
        assert extract_json_value('', 'key') == ''

    def test_key_at_very_start(self):
        j = '{"a":"1"}'
        assert extract_json_value(j, 'a') == '1'

    def test_value_is_zero(self):
        j = '{"count":0}'
        assert extract_json_value(j, 'count') == '0'

    def test_deeply_nested_objects(self):
        j = '{"a":{"b":{"c":{"d":"deep"}}}}'
        result = extract_json_value(j, 'a')
        assert result == '{"b":{"c":{"d":"deep"}}}'
        # Can keep drilling down
        result2 = extract_json_value(result, 'b')
        assert result2 == '{"c":{"d":"deep"}}'

    def test_object_value_with_arrays_inside(self):
        """Object extraction should handle arrays inside the object."""
        j = '{"data":{"items":[1,2],"name":"test"}}'
        result = extract_json_value(j, 'data')
        assert result == '{"items":[1,2],"name":"test"}'

    def test_value_followed_by_comma(self):
        j = '{"a":"first","b":"second"}'
        assert extract_json_value(j, 'a') == 'first'

    def test_value_followed_by_closing_brace(self):
        j = '{"a":"only"}'
        assert extract_json_value(j, 'a') == 'only'

    def test_number_at_end(self):
        j = '{"x":100}'
        assert extract_json_value(j, 'x') == '100'

    def test_number_followed_by_comma(self):
        j = '{"x":100,"y":200}'
        assert extract_json_value(j, 'x') == '100'
        assert extract_json_value(j, 'y') == '200'
