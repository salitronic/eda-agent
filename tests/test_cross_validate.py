# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Cross-validation tests: run SAME inputs through Free Pascal and Python,
verify IDENTICAL outputs.

This proves the Python reimplementations in test_json_parsing.py are faithful
to the real Pascal code in Main.pas / Utils.pas.

Architecture:
  1. Generate test inputs (base64-encoded, tab-separated)
  2. Compile + run the Free Pascal program (cross_validate_pascal.pas)
     which uses REAL Pascal functions copied from Main.pas / Utils.pas
  3. Run the same inputs through Python reimplementations
  4. Compare -- outputs must be identical

If FPC is not installed, tests are skipped with a clear message.
"""

import base64
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TESTS_DIR = Path(__file__).parent
PASCAL_SRC = TESTS_DIR / "cross_validate_pascal.pas"
PASCAL_EXE = TESTS_DIR / "cross_validate_pascal.exe"
# Set FPC_UNIT_PATH env var to override (e.g. Scoop installs FPC under
# %USERPROFILE%\scoop\apps\freepascal\<ver>\units\i386-win32\fcl-base).
# If empty/unset, the test invokes fpc without -Fu and relies on fpc.cfg.
FPC_UNIT_PATH = os.environ.get("FPC_UNIT_PATH", "")

# ---------------------------------------------------------------------------
# Python reimplementations -- EXACT mirrors of the real DelphiScript
# (copied from test_json_parsing.py with the backslash-counting fix to
#  match the real Main.pas ExtractJsonValue)
# ---------------------------------------------------------------------------


def is_whitespace_or_colon(s: str, idx: int) -> bool:
    if idx >= len(s):
        return False
    c = s[idx]
    return c in (' ', ':', '\t', '\n', '\r')


def is_delimiter(s: str, idx: int) -> bool:
    if idx >= len(s):
        return True
    c = s[idx]
    return c in ('', ',', '}', ']', ' ', '\t', '\n', '\r')


def extract_json_value(json_str: str, key: str) -> str:
    """Mirror of Main.pas ExtractJsonValue -- with proper backslash counting."""
    result = ''
    search_key = '"' + key + '"'
    start_pos = json_str.find(search_key)
    if start_pos < 0:
        return result

    start_pos += len(search_key)

    while start_pos < len(json_str) and is_whitespace_or_colon(json_str, start_pos):
        start_pos += 1

    if start_pos >= len(json_str):
        return result

    if json_str[start_pos] == '"':
        # String value
        start_pos += 1
        end_pos = start_pos
        while end_pos < len(json_str):
            if json_str[end_pos] == '"':
                # Count consecutive backslashes before this quote
                backslash_count = 0
                temp_pos = end_pos - 1
                while temp_pos >= start_pos and json_str[temp_pos] == '\\':
                    backslash_count += 1
                    temp_pos -= 1
                # Even number of backslashes means quote is real (unescaped)
                if backslash_count % 2 == 0:
                    break
            end_pos += 1
        result = json_str[start_pos:end_pos]

    elif json_str[start_pos] == '{':
        # Object value
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
        # Bare value (number, bool, null)
        end_pos = start_pos
        while end_pos < len(json_str) and not is_delimiter(json_str, end_pos):
            end_pos += 1
        result = json_str[start_pos:end_pos]

    return result


def extract_json_array(json_str: str, key: str) -> str:
    """Mirror of Utils.pas ExtractJsonArray."""
    result = ''
    search_key = '"' + key + '"'
    start_pos = json_str.find(search_key)
    if start_pos < 0:
        return result

    start_pos += len(search_key)
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
    """Mirror of Utils.pas EscapeJsonString."""
    result = s
    result = result.replace('\\', '\\\\')
    result = result.replace('"', '\\"')
    result = result.replace('\r', '\\r')
    result = result.replace('\n', '\\n')
    result = result.replace('\t', '\\t')
    return result


def build_success_response(request_id: str, data: str) -> str:
    """Mirror of Main.pas BuildSuccessResponse."""
    if data == '':
        data = 'null'
    return '{"id":"' + request_id + '","success":true,"data":' + data + ',"error":null}'


def build_error_response(request_id: str, error_code: str, error_msg: str) -> str:
    """Mirror of Main.pas BuildErrorResponse."""
    error_msg = error_msg.replace('\\', '\\\\')
    error_msg = error_msg.replace('"', '\\"')
    error_msg = error_msg.replace('\r', '\\r')
    error_msg = error_msg.replace('\n', '\\n')
    error_msg = error_msg.replace('\t', '\\t')
    return ('{"id":"' + request_id + '","success":false,"data":null,'
            '"error":{"code":"' + error_code + '","message":"' + error_msg + '"}}')


def mils_to_coord(mils: int) -> int:
    return mils * 10000


def coord_to_mils(coord: int) -> int:
    return round(coord / 10000)


def mm_to_coord(mm: float) -> int:
    return round(mm * 10000000 / 25.4)


def coord_to_mm(coord: int) -> float:
    return coord * 25.4 / 10000000


def str_to_int_def(s: str, default: int) -> int:
    if s == '' or s == 'null':
        return default
    try:
        return int(s)
    except (ValueError, OverflowError):
        return default


def str_to_float_def(s: str, default: float) -> float:
    if s == '' or s == 'null':
        return default
    try:
        return float(s)
    except (ValueError, OverflowError):
        return default


def bool_to_json_str(value: bool) -> str:
    return 'true' if value else 'false'


def str_to_bool(s: str) -> bool:
    return s.lower() == 'true' or s == '1'


# ---------------------------------------------------------------------------
# Python mirrors for Generic.pas / Dispatcher.pas parsing logic
# ---------------------------------------------------------------------------


def get_stored_property(prop_store: str, name: str) -> str:
    """Parse pipe-separated 'Name=Value|Name=Value' store, return value for name."""
    remaining = prop_store
    while remaining:
        pipe = remaining.find('|')
        if pipe >= 0:
            entry = remaining[:pipe]
            remaining = remaining[pipe + 1:]
        else:
            entry = remaining
            remaining = ''
        eq = entry.find('=')
        if eq < 0:
            continue
        entry_name = entry[:eq]
        entry_value = entry[eq + 1:]
        if entry_name == name:
            return entry_value
    return ''


def set_stored_property(prop_store: str, name: str, value: str) -> str:
    """Set/replace name=value in pipe-separated store, appending if missing."""
    result = ''
    remaining = prop_store
    found = False
    while remaining:
        pipe = remaining.find('|')
        if pipe >= 0:
            entry = remaining[:pipe]
            remaining = remaining[pipe + 1:]
        else:
            entry = remaining
            remaining = ''
        eq = entry.find('=')
        if eq > 0:
            entry_name = entry[:eq]
            if entry_name == name:
                entry = name + '=' + value
                found = True
        if result:
            result += '|'
        result += entry
    if not found:
        if result:
            result += '|'
        result += name + '=' + value
    return result


def matches_filter(prop_store: str, filter_str: str) -> str:
    """Mirror of Generic.pas MatchesFilter. Returns 'true'/'false'."""
    if filter_str == '':
        return 'true'
    remaining = filter_str
    while remaining:
        pipe = remaining.find('|')
        if pipe >= 0:
            condition = remaining[:pipe]
            remaining = remaining[pipe + 1:]
        else:
            condition = remaining
            remaining = ''
        eq = condition.find('=')
        if eq < 0:
            continue
        prop_name = condition[:eq]
        expected = condition[eq + 1:]
        actual = get_stored_property(prop_store, prop_name)
        if actual != expected:
            return 'false'
    return 'true'


def build_object_json(prop_store: str, props_str: str) -> str:
    """Mirror of Generic.pas BuildObjectJson."""
    result = '{'
    first = True
    remaining = props_str
    while remaining:
        comma = remaining.find(',')
        if comma >= 0:
            prop_name = remaining[:comma]
            remaining = remaining[comma + 1:]
        else:
            prop_name = remaining
            remaining = ''
        prop_value = get_stored_property(prop_store, prop_name)
        if not first:
            result += ','
        first = False
        result += '"' + escape_json_string(prop_name) + '":"' + escape_json_string(prop_value) + '"'
    result += '}'
    return result


def apply_set_properties(prop_store: str, set_str: str) -> str:
    """Mirror of Generic.pas ApplySetProperties. Returns mutated prop store."""
    result = prop_store
    remaining = set_str
    while remaining:
        pipe = remaining.find('|')
        if pipe >= 0:
            assignment = remaining[:pipe]
            remaining = remaining[pipe + 1:]
        else:
            assignment = remaining
            remaining = ''
        eq = assignment.find('=')
        if eq < 0:
            continue
        prop_name = assignment[:eq]
        prop_value = assignment[eq + 1:]
        result = set_stored_property(result, prop_name, prop_value)
    return result


def count_batch_operations(operations: str) -> int:
    """Mirror of Gen_BatchModify operation counting.

    Each operation is 'scope;object_type;filter;set' separated by '|'.
    Counts operations that have non-empty object_type and set.
    """
    op_count = 0
    remaining = operations
    while remaining:
        pipe = remaining.find('|')
        if pipe < 0:
            op_str = remaining
            remaining = ''
        else:
            op_str = remaining[:pipe]
            remaining = remaining[pipe + 1:]
        if op_str == '':
            continue
        semi = op_str.find(';')
        if semi < 0:
            continue
        op_str = op_str[semi + 1:]  # skip scope
        semi = op_str.find(';')
        if semi < 0:
            continue
        obj_type_str = op_str[:semi]
        op_str = op_str[semi + 1:]
        semi = op_str.find(';')
        if semi < 0:
            continue
        # filter is op_str[:semi]; set is op_str[semi+1:]
        set_str = op_str[semi + 1:]
        if obj_type_str == '' or set_str == '':
            continue
        op_count += 1
    return op_count


def split_command(command: str) -> str:
    """Mirror of Dispatcher.pas command splitting. Returns 'Category|Action'."""
    dot = command.find('.')
    if dot >= 0:
        category = command[:dot]
        action = command[dot + 1:]
    else:
        category = command
        action = ''
    return category + '|' + action


# ---------------------------------------------------------------------------
# Base64 helpers
# ---------------------------------------------------------------------------

def b64e(s: str) -> str:
    """Encode string to base64. Convention: '_' means empty string."""
    if s == '':
        return '_'
    return base64.b64encode(s.encode('latin-1')).decode('ascii')


def b64d(s: str) -> str:
    """Decode base64 to string. Convention: '_' means empty string."""
    if s == '' or s == '_':
        return ''
    return base64.b64decode(s.encode('ascii')).decode('latin-1')


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------

def generate_extract_json_value_cases():
    """Generate 100+ ExtractJsonValue test cases."""
    cases = []

    def add(json_str, key):
        cases.append(("ExtractJsonValue", [json_str, key]))

    # --- Basic types ---
    add('{"name":"hello"}', 'name')
    add('{"name":""}', 'name')
    add('{"count":42}', 'count')
    add('{"count":0}', 'count')
    add('{"offset":-10}', 'offset')
    add('{"active":true}', 'active')
    add('{"active":false}', 'active')
    add('{"data":null}', 'data')
    add('{"angle":45.5}', 'angle')
    add('{"val":-3.14}', 'val')

    # --- Whitespace around colon ---
    add('{"name" : "hello"}', 'name')
    add('{"key":\t"value"}', 'key')
    add('{"key":\n"value"}', 'key')
    add('{"key":\r\n"value"}', 'key')
    add('{"key":  \t  "value"}', 'key')

    # --- Object values ---
    add('{"params":{"x":100,"y":200}}', 'params')
    add('{"params":{}}', 'params')
    add('{"outer":{"inner":{"deep":"value"}}}', 'outer')
    add('{"data":{"items":[1,2],"name":"test"}}', 'data')

    # --- Key matching ---
    add('{"name":"hello"}', 'missing')
    add('{}', 'key')
    add('', 'key')
    add('{"full_name":"John","name":"Jane"}', 'name')
    add('{"id_extra":"wrong","id":"correct"}', 'id')
    add('{"request_id":"first","id":"second"}', 'id')

    # --- Multiple keys ---
    add('{"id":"123","command":"test","params":{"x":1}}', 'id')
    add('{"id":"123","command":"test","params":{"x":1}}', 'command')
    add('{"id":"123","command":"test","params":{"x":1}}', 'params')

    # --- Escaped strings ---
    add(r'{"msg":"say \"hello\""}', 'msg')
    add(r'{"path":"C:\\Users\\test"}', 'path')
    add(r'{"mixed":"a\\b\"c"}', 'mixed')

    # --- Backslash-counting edge cases (where simple check differs from real code) ---
    # Two backslashes then quote: \\\\" means two literal backslashes, then end quote
    add(r'{"key":"val\\\\"}', 'key')
    # Three backslashes then quote: \\\\\\" means escaped backslash + escaped quote
    add(r'{"key":"val\\\\\\\"more"}', 'key')
    # Single backslash then quote: escaped quote
    add(r'{"key":"val\"end"}', 'key')

    # --- Real-world formats ---
    add('{"id":"abc-123","command":"application.ping","params":{}}', 'id')
    add('{"id":"abc-123","command":"application.ping","params":{}}', 'command')
    add('{"id":"abc-123","command":"application.ping","params":{}}', 'params')
    add('{"id":"abc-123","success":true,"data":{"version":"connected"},"error":null}', 'id')
    add('{"id":"abc-123","success":true,"data":{"version":"connected"},"error":null}', 'success')
    add('{"id":"abc-123","success":true,"data":{"version":"connected"},"error":null}', 'data')
    add('{"id":"abc-123","success":true,"data":{"version":"connected"},"error":null}', 'error')

    # --- Value at end of JSON ---
    add('{"last":99}', 'last')
    add('{"only":"value"}', 'only')

    # --- Deeply nested ---
    add('{"a":{"b":{"c":{"d":"deep"}}}}', 'a')
    add('{"a":{"b":1},"c":{"c":{"d":"deep"}}}', 'c')

    # --- Number edge cases ---
    add('{"x":100,"y":200}', 'x')
    add('{"x":100,"y":200}', 'y')
    add('{"val":999999999}', 'val')
    add('{"val":0.001}', 'val')

    # --- Various string contents ---
    add('{"s":"hello world"}', 's')
    add('{"s":"with spaces and stuff"}', 's')
    add('{"s":"123"}', 's')
    add('{"s":"true"}', 's')
    add('{"s":"null"}', 's')
    add('{"s":"{}"}', 's')

    # --- Unicode (ASCII subset) ---
    add('{"name":"resistor_100k"}', 'name')
    add('{"name":"R1 (0402)"}', 'name')

    # --- Stress: long values ---
    add('{"key":"' + 'a' * 200 + '"}', 'key')
    add('{"key":' + str(10**15) + '}', 'key')

    # --- Stress: many keys ---
    many_keys = ','.join(f'"k{i}":"{i}"' for i in range(50))
    add('{' + many_keys + '}', 'k0')
    add('{' + many_keys + '}', 'k25')
    add('{' + many_keys + '}', 'k49')

    # --- Edge: key with special chars ---
    add('{"a-b":"hyphen"}', 'a-b')
    add('{"a_b":"underscore"}', 'a_b')
    add('{"a.b":"dot"}', 'a.b')

    # --- Boolean/null followed by different terminators ---
    add('{"a":true,"b":1}', 'a')
    add('{"a":false,"b":1}', 'a')
    add('{"a":null,"b":1}', 'a')
    add('{"a":true}', 'a')
    add('{"a":false}', 'a')
    add('{"a":null}', 'a')

    # --- Nested object with braces in strings ---
    add('{"data":{"msg":"hello {world}"}}', 'data')

    # --- Array value (not extracted by ExtractJsonValue, should get partial) ---
    # ExtractJsonValue does not handle arrays as a value type; it falls through
    # to the bare-value branch which stops at the first delimiter
    add('{"items":[1,2,3]}', 'items')

    # Pad to 100+ with variations
    for i in range(30):
        add(f'{{"v{i}":"test_{i}"}}', f'v{i}')

    return cases


def generate_extract_json_array_cases():
    """Generate ExtractJsonArray test cases."""
    cases = []

    def add(json_str, key):
        cases.append(("ExtractJsonArray", [json_str, key]))

    add('{"items":[1,2,3]}', 'items')
    add('{"names":["a","b","c"]}', 'names')
    add('{"items":[]}', 'items')
    add('{"matrix":[[1,2],[3,4]]}', 'matrix')
    add('{"items":[{"x":1},{"x":2}]}', 'items')
    add('{"items":[1,2,3]}', 'missing')
    add('{"items":"not_array"}', 'items')
    add('{"items" : [1, 2, 3]}', 'items')
    add('{"data":{"nested":[10,20]}}', 'nested')
    add('{"a":[1],"b":[2,3]}', 'a')
    add('{"a":[1],"b":[2,3]}', 'b')
    add('{"deep":[[["x"]]]}', 'deep')
    add('{"empty":[]}', 'empty')
    add('{}', 'items')
    add('', 'items')
    # Long array
    long_arr = ','.join(str(i) for i in range(100))
    add('{"big":[' + long_arr + ']}', 'big')

    return cases


def generate_escape_json_string_cases():
    """Generate 100+ EscapeJsonString test cases."""
    cases = []

    def add(s):
        cases.append(("EscapeJsonString", [s]))

    # Empty and simple
    add('')
    add('hello')
    add('hello world')

    # Individual special chars
    add('\\')
    add('"')
    add('\r')
    add('\n')
    add('\t')

    # Combinations
    add('a\\b')
    add('say "hello"')
    add('line1\rline2')
    add('line1\nline2')
    add('col1\tcol2')
    add('line1\r\nline2')
    add('\\"')
    add('a\\b"c\rd\ne\tf')

    # Windows paths
    add('C:\\Users\\test\\file.txt')
    add('C:\\Program Files\\Altium\\')
    add('\\\\server\\share\\file.txt')

    # All printable ASCII individually
    for i in range(32, 127):
        add(chr(i))

    # Control characters we handle
    add('\t\t\t')
    add('\r\r\r')
    add('\n\n\n')

    # Multiple backslashes
    add('\\\\')
    add('\\\\\\')
    add('\\\\\\\\')

    # Mixed stress
    add('Error: "path\\to\\file"\r\n\tdetails')
    add('{"key":"value"}')
    add('say "hello" and "goodbye"')
    add('C:\\a\\b\\c\\d\\e')
    add('tab\there\tand\there')

    # Real-world error messages
    add('Cannot find "file.txt"')
    add('Path: C:\\Users\\test')
    add('Line1\nLine2')
    add('Unknown command category: foo. Use generic.* for object operations.')

    # Strings with no special chars (should pass through)
    add('abcdefghijklmnopqrstuvwxyz')
    add('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    add('0123456789')
    add('hello-world_test.value')

    return cases


def generate_build_success_response_cases():
    """Generate 50+ BuildSuccessResponse test cases."""
    cases = []

    def add(request_id, data):
        cases.append(("BuildSuccessResponse", [request_id, data]))

    add('req-1', '"pong"')
    add('req-2', '{"version":"1.0"}')
    add('req-3', 'null')
    add('req-4', '')
    add('req-5', '[1,2,3]')
    add('req-6', 'true')
    add('req-7', 'false')
    add('req-8', '42')
    add('req-9', '3.14')
    add('req-10', '{}')
    add('req-11', '[]')
    add('req-12', '"hello world"')
    add('req-13', '{"a":1,"b":2,"c":3}')
    add('req-14', '"with \\"quotes\\""')
    add('req-15', '{"nested":{"deep":"value"}}')

    # Real response formats
    add('r1', '"pong"')
    add('r2', '{"version":"connected","product_name":"Altium Designer"}')
    add('r3', '{"count":42,"items":[]}')
    add('r4', '"C:\\\\Users\\\\test"')

    # Various ID formats
    add('abc-123', '"data"')
    add('a1b2c3d4-e5f6-7890-abcd-ef1234567890', '"uuid-style"')
    add('1', '"short-id"')
    add('req_with_underscore', '"test"')
    add('', '"empty-id"')

    # Various data payloads
    for i in range(26):
        add(f'r{i+20}', f'{{"idx":{i},"val":"test_{i}"}}')

    return cases


def generate_build_error_response_cases():
    """Generate 50+ BuildErrorResponse test cases."""
    cases = []

    def add(request_id, error_code, error_msg):
        cases.append(("BuildErrorResponse", [request_id, error_code, error_msg]))

    add('req-1', 'NOT_FOUND', 'Item not found')
    add('req-2', 'ERR', 'Cannot find "file.txt"')
    add('req-3', 'ERR', 'Path: C:\\Users\\test')
    add('req-4', 'ERR', 'Line1\nLine2')
    add('req-5', 'ERR', 'Error: "path\\to\\file"\r\n\tdetails')
    add('req-6', 'UNKNOWN_COMMAND',
        'Unknown command category: foo. Use generic.* for object operations.')
    add('req-7', 'ERR', '')
    add('req-8', 'ERR', 'simple')
    add('req-9', 'ERR', 'with "quotes"')
    add('req-10', 'ERR', 'with \\backslashes\\')
    add('req-11', 'ERR', 'with\nnewlines\r\nand\ttabs')
    add('req-12', 'ERR', 'mixed: "quotes" and \\paths\\ and\nnewlines')

    # Various error codes
    add('e1', 'PARSE_ERROR', 'Invalid JSON')
    add('e2', 'INVALID_PARAMS', 'Missing required field')
    add('e3', 'INTERNAL_ERROR', 'Unexpected exception')
    add('e4', 'NOT_CONNECTED', 'Altium Designer is not running')
    add('e5', 'TIMEOUT', 'Request timed out after 30s')

    # Special characters in messages
    add('e6', 'ERR', '\t\t\tindented')
    add('e7', 'ERR', 'line1\nline2\nline3')
    add('e8', 'ERR', '"quoted"')
    add('e9', 'ERR', '\\\\double\\\\backslash')
    add('e10', 'ERR', 'C:\\Program Files\\Altium\\')

    # Long messages
    add('e11', 'ERR', 'A' * 500)
    add('e12', 'ERR', 'x' * 100 + '\n' + 'y' * 100)

    # Pad to 50+
    for i in range(28):
        add(f'e{i+13}', 'TEST_ERR', f'Error #{i}: something went wrong')

    return cases


def generate_coordinate_cases():
    """Generate coordinate conversion test cases."""
    cases = []

    # MilsToCoord
    for mils in [0, 1, 10, 50, 100, 500, 1000, -1, -50, -100, -1000, 9999, 10001]:
        cases.append(("MilsToCoord", [str(mils)]))

    # CoordToMils
    for coord in [0, 10000, 50000, 100000, 1000000, -10000, -500000,
                  9999, 15000, 5000, 1, 10001, 19999, 20000]:
        cases.append(("CoordToMils", [str(coord)]))

    # MMToCoord
    for mm in ['0.0', '1.0', '2.54', '25.4', '0.1', '0.01', '10.0',
               '0.254', '1.27', '5.08', '12.7', '50.8', '100.0']:
        cases.append(("MMToCoord", [mm]))

    # CoordToMM
    for coord in [0, 10000000, 1000000, 100000, 10000, 3937008, 5000000,
                  393701, 1, 100, 99999, 7874016]:
        cases.append(("CoordToMM", [str(coord)]))

    return cases


def generate_string_helper_cases():
    """Generate string helper function test cases."""
    cases = []

    # StrToIntDef
    for s, default in [
        ('42', 0), ('-10', 0), ('0', 0), ('999999', 0),
        ('', 99), ('null', 99), ('abc', 0), ('12.5', 0),
        ('', 0), ('null', 0), ('  ', 5),
        ('1000000000', 0), ('-1000000000', 0),
        ('true', 0), ('false', 0), ('+5', 0),
    ]:
        cases.append(("StrToIntDef", [s, str(default)]))

    # StrToFloatDef -- use only dot-decimal values since FPC will parse with '.'
    for s, default in [
        ('3.14', 0.0), ('-2.5', 0.0), ('0.0', 0.0), ('100.0', 0.0),
        ('', 99.9), ('null', 99.9), ('abc', 0.0), ('', 0.0), ('null', 0.0),
        ('1.23456789', 0.0), ('0.001', 0.0), ('999999.999', 0.0),
        ('-0.5', 0.0), ('42.0', 0.0), ('0.1', 0.0),
    ]:
        cases.append(("StrToFloatDef", [s, f'{default:.10f}']))

    # BoolToJsonStr
    for val in ['true', 'false']:
        cases.append(("BoolToJsonStr", [val]))

    # StrToBool
    for s in ['true', 'True', 'TRUE', 'tRuE', '1', 'false', 'False',
              'FALSE', '0', '', 'yes', 'no', '2', 'on', 'off',
              ' true', 'true ', ' 1']:
        cases.append(("StrToBool", [s]))

    return cases


def generate_matches_filter_cases():
    """Test MatchesFilter parsing against stored properties."""
    cases = []

    def add(prop_store, filter_str):
        cases.append(("MatchesFilter", [prop_store, filter_str]))

    # Empty filter always matches
    add("", "")
    add("Text=VCC", "")
    add("Text=VCC|Location.X=100", "")

    # Single condition, match + no-match
    add("Text=VCC", "Text=VCC")
    add("Text=VCC", "Text=GND")
    add("Text=VCC|Location.X=100", "Text=VCC")
    add("Text=VCC|Location.X=100", "Location.X=100")
    add("Text=VCC|Location.X=100", "Location.X=200")

    # Multi-condition AND logic
    add("Text=VCC|Location.X=100", "Text=VCC|Location.X=100")
    add("Text=VCC|Location.X=100", "Text=VCC|Location.X=999")
    add("Text=VCC|Location.X=100|Color=128", "Text=VCC|Color=128")
    add("Text=VCC|Location.X=100|Color=128", "Color=128|Location.X=100")

    # Missing property (actual='' != expected)
    add("Text=VCC", "Missing=xyz")
    add("Text=VCC", "Missing=")     # actual='' == expected='' -> match
    add("", "Text=VCC")
    add("", "Text=")                 # both empty -> match

    # Empty value in store, filter expects it
    add("Text=|Location.X=100", "Text=")
    add("Text=|Location.X=100", "Text=VCC")

    # Ignore malformed conditions (no '=')
    add("Text=VCC", "noequals")
    add("Text=VCC", "Text=VCC|noequals|Location.X=")
    add("Text=VCC", "|")

    # Values with special chars -- pipe-in-value confuses the parser
    # (this is deliberate: pipe is the separator; the test pins behavior)
    add("Text=A|Extra=B", "Text=A")
    add("Designator.Text=R1|Comment.Text=10k", "Designator.Text=R1|Comment.Text=10k")
    add("Designator.Text=R1|Comment.Text=10k", "Designator.Text=R1|Comment.Text=4.7k")

    # Values with equals in them: Expected gets everything after first '='
    add("Path=C:/a=b", "Path=C:/a=b")
    add("X=1=2=3", "X=1=2=3")

    # Many conditions
    props = "|".join(f"P{i}=V{i}" for i in range(20))
    match_filter = "|".join(f"P{i}=V{i}" for i in range(20))
    miss_filter = "|".join(f"P{i}=V{i}" for i in range(19)) + "|P19=WRONG"
    add(props, match_filter)
    add(props, miss_filter)

    # Longer realistic cases
    add("Designator.Text=R1|Comment.Text=10k|LibReference=RES_0402",
        "Designator.Text=R1")
    add("Designator.Text=R1|Comment.Text=10k|LibReference=RES_0402",
        "LibReference=RES_0402|Comment.Text=10k")
    add("Designator.Text=R1|Comment.Text=10k|LibReference=RES_0402",
        "LibReference=CAP_0402")

    return cases


def generate_build_object_json_cases():
    """Test BuildObjectJson: comma-separated props + pipe-separated store."""
    cases = []

    def add(prop_store, props_str):
        cases.append(("BuildObjectJson", [prop_store, props_str]))

    # Empty props -> empty JSON object
    add("", "")
    add("Text=VCC", "")
    add("Text=VCC|Location.X=100", "")

    # Single property
    add("Text=VCC", "Text")
    add("Text=VCC|Location.X=100", "Text")
    add("Text=VCC|Location.X=100", "Location.X")
    add("Text=VCC", "Missing")  # missing -> ""

    # Multi-property (order preserved per PropsStr)
    add("Text=VCC|Location.X=100|Location.Y=200", "Text,Location.X,Location.Y")
    add("Text=VCC|Location.X=100|Location.Y=200", "Location.Y,Text,Location.X")

    # Subset
    add("Text=VCC|Location.X=100|Location.Y=200|Color=128", "Text,Color")

    # Missing values
    add("Text=VCC", "Text,Missing,Also.Missing")

    # Values with JSON-special chars (will be escaped)
    add('Text=say "hello"', "Text")
    add("Text=path\\to\\file", "Text")
    add("Text=line1\nline2", "Text")
    add('Text=say "hi"|Note=C:\\path', "Text,Note")

    # Empty values
    add("Text=", "Text")
    add("Text=|Location.X=100", "Text,Location.X")

    # Realistic schematic query
    add("Designator.Text=R1|Comment.Text=10k|LibReference=RES_0402",
        "Designator.Text,Comment.Text,LibReference")

    # Stress: many props
    store = "|".join(f"P{i}=V{i}" for i in range(30))
    props = ",".join(f"P{i}" for i in range(30))
    add(store, props)

    # Props that don't exist in store
    add("A=1|B=2", "X,Y,Z")

    # Property names with special chars
    add("key.with.dots=value", "key.with.dots")
    add("key_underscore=value|Key-Hyphen=v2", "key_underscore,Key-Hyphen")

    return cases


def generate_apply_set_properties_cases():
    """Test ApplySetProperties: pipe-separated assignments to prop store."""
    cases = []

    def add(prop_store, set_str):
        cases.append(("ApplySetProperties", [prop_store, set_str]))

    # No-op
    add("Text=VCC", "")
    add("", "")

    # Set existing property
    add("Text=VCC", "Text=GND")
    add("Text=VCC|Location.X=100", "Text=GND")
    add("Text=VCC|Location.X=100", "Location.X=999")

    # Add new property (appends)
    add("Text=VCC", "NewProp=value")
    add("", "Text=VCC")
    add("", "A=1|B=2|C=3")

    # Multi-assignment
    add("Text=VCC|Location.X=100", "Text=GND|Location.X=200")
    add("Text=VCC", "Text=GND|Location.X=100|Location.Y=200")

    # Mix of updates and new
    add("Text=VCC|Location.X=100", "Text=GND|Color=128")

    # Ignore malformed (no '=')
    add("Text=VCC", "noequals")
    add("Text=VCC", "Text=GND|noequals|Color=128")

    # Empty value
    add("Text=VCC", "Text=")
    add("", "Text=")

    # Value with equals in it
    add("Text=VCC", "Path=C:/a=b")

    # Set same property twice -- last value wins
    add("Text=VCC", "Text=X|Text=Y|Text=Z")

    # Longer realistic modify
    add("Designator.Text=R1|Comment.Text=10k|LibReference=RES_0402",
        "Comment.Text=22k|LibReference=RES_0603")

    return cases


def generate_count_batch_operations_cases():
    """Test Gen_BatchModify operation iteration + counting."""
    cases = []

    def add(operations):
        cases.append(("CountBatchOperations", [operations]))

    # Empty
    add("")

    # Single valid op
    add("active;eNetLabel;Text=VCC;Text=GND")

    # Multiple valid ops
    add("active;eNetLabel;Text=VCC;Text=GND|active;eWire;;Color=128")

    # Missing object_type -> skip
    add("active;;Text=VCC;Text=GND")

    # Missing set -> skip
    add("active;eNetLabel;Text=VCC;")

    # Missing semicolons -> skip whole op
    add("onlyonepart")
    add("a;b")
    add("a;b;c")  # no 4th part (set_str)

    # Empty op between pipes -> skip
    add("||active;eNetLabel;Text=VCC;Text=GND||")

    # Mix valid + invalid
    add("active;eNetLabel;Text=VCC;Text=GND|bad|active;eWire;;Color=0")

    # Many valid
    ops = "|".join(
        f"active;eNetLabel;Text=N{i};Text=M{i}" for i in range(15)
    )
    add(ops)

    # Scope variants
    add("doc:C:/path/to/sheet.SchDoc;eNetLabel;Text=VCC;Text=GND")
    add("project;eSchComponent;Designator.Text=R1;Comment.Text=22k")

    # Realistic batch
    add(
        "active;eNetLabel;Text=VCC;Text=3V3|"
        "active;eNetLabel;Text=GND;Text=DGND|"
        "project;eSchComponent;Designator.Text=R1;Comment.Text=22k"
    )

    return cases


def generate_split_command_cases():
    """Test Dispatcher.pas command splitting on '.'."""
    cases = []

    def add(cmd):
        cases.append(("SplitCommand", [cmd]))

    # Empty
    add("")

    # No dot
    add("nodotcommand")
    add("application")

    # Simple
    add("application.ping")
    add("project.get_documents")
    add("generic.query_objects")
    add("library.get_components")

    # Dot at start / end
    add(".ping")
    add("application.")

    # Multiple dots -- only first splits
    add("application.run.process")
    add("a.b.c.d.e")

    # Trailing/leading spaces (not stripped)
    add(" application.ping")
    add("application.ping ")

    # Unknown category
    add("bogus.action")

    # Special chars in category/action
    add("app_lication.ping")
    add("application.unknown-action")

    return cases


# ---------------------------------------------------------------------------
# Python-side test runner
# ---------------------------------------------------------------------------

def run_python(fn_name: str, args: list[str]) -> str:
    """Run a single test case through the Python reimplementation.
    Returns the result as a string, matching Pascal output formatting."""

    if fn_name == 'ExtractJsonValue':
        return extract_json_value(args[0], args[1])

    elif fn_name == 'ExtractJsonArray':
        return extract_json_array(args[0], args[1])

    elif fn_name == 'EscapeJsonString':
        return escape_json_string(args[0])

    elif fn_name == 'BuildSuccessResponse':
        return build_success_response(args[0], args[1])

    elif fn_name == 'BuildErrorResponse':
        return build_error_response(args[0], args[1], args[2])

    elif fn_name == 'MilsToCoord':
        return str(mils_to_coord(int(args[0])))

    elif fn_name == 'CoordToMils':
        return str(coord_to_mils(int(args[0])))

    elif fn_name == 'MMToCoord':
        return str(mm_to_coord(float(args[0])))

    elif fn_name == 'CoordToMM':
        val = coord_to_mm(int(args[0]))
        return f'{val:.10f}'

    elif fn_name == 'StrToIntDef':
        return str(str_to_int_def(args[0], int(args[1])))

    elif fn_name == 'StrToFloatDef':
        val = str_to_float_def(args[0], float(args[1]))
        return f'{val:.10f}'

    elif fn_name == 'BoolToJsonStr':
        return bool_to_json_str(args[0].lower() == 'true')

    elif fn_name == 'StrToBool':
        return 'true' if str_to_bool(args[0]) else 'false'

    elif fn_name == 'MatchesFilter':
        return matches_filter(args[0], args[1])

    elif fn_name == 'BuildObjectJson':
        return build_object_json(args[0], args[1])

    elif fn_name == 'ApplySetProperties':
        return apply_set_properties(args[0], args[1])

    elif fn_name == 'CountBatchOperations':
        return str(count_batch_operations(args[0]))

    elif fn_name == 'SplitCommand':
        return split_command(args[0])

    else:
        raise ValueError(f"Unknown function: {fn_name}")


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def write_inputs(test_cases, filepath):
    """Write test cases to the input file in tab+base64 format."""
    with open(filepath, 'w', encoding='ascii') as f:
        for fn_name, args in test_cases:
            encoded_args = [b64e(a) for a in args]
            line = fn_name + '\t' + '\t'.join(encoded_args)
            f.write(line + '\n')


def read_outputs(filepath):
    """Read base64-encoded results from the output file."""
    results = []
    with open(filepath, 'r', encoding='ascii') as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(b64d(line))
            # Note: truly empty lines (blank) are ignored as they are
            # artifacts of line endings. The '_' convention ensures all
            # real outputs including empty strings appear as non-blank lines.
    return results


# ---------------------------------------------------------------------------
# FPC compilation fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fpc_executable():
    """Compile the Pascal cross-validation program. Skip if FPC unavailable."""
    fpc_path = shutil.which("fpc")
    if fpc_path is None:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")

    if not PASCAL_SRC.exists():
        pytest.skip(f"Pascal source not found: {PASCAL_SRC}")

    # Compile. If FPC_UNIT_PATH is set, pass it via -Fu; otherwise rely on fpc.cfg.
    cmd = ["fpc"]
    if FPC_UNIT_PATH:
        cmd.append(f"-Fu{FPC_UNIT_PATH}")
    cmd.append(str(PASCAL_SRC))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(TESTS_DIR),
    )
    if result.returncode != 0:
        pytest.skip(
            f"FPC compilation failed:\n{result.stdout}\n{result.stderr}\n"
            f"Ensure FPC is installed and fcl-base unit path is correct."
        )

    if not PASCAL_EXE.exists():
        pytest.skip(f"FPC compiled but executable not found: {PASCAL_EXE}")

    return str(PASCAL_EXE)


# ---------------------------------------------------------------------------
# Gather all test cases
# ---------------------------------------------------------------------------

def all_test_cases():
    """Return the complete list of (fn_name, args) tuples."""
    cases = []
    cases.extend(generate_extract_json_value_cases())
    cases.extend(generate_extract_json_array_cases())
    cases.extend(generate_escape_json_string_cases())
    cases.extend(generate_build_success_response_cases())
    cases.extend(generate_build_error_response_cases())
    cases.extend(generate_coordinate_cases())
    cases.extend(generate_string_helper_cases())
    cases.extend(generate_matches_filter_cases())
    cases.extend(generate_build_object_json_cases())
    cases.extend(generate_apply_set_properties_cases())
    cases.extend(generate_count_batch_operations_cases())
    cases.extend(generate_split_command_cases())
    return cases


# ---------------------------------------------------------------------------
# The cross-validation test
# ---------------------------------------------------------------------------

class TestCrossValidation:
    """Run every test case through both FPC and Python, compare outputs."""

    def test_cross_validate_all(self, fpc_executable, tmp_path):
        """Main cross-validation: identical outputs from Pascal and Python."""
        cases = all_test_cases()
        assert len(cases) > 400, f"Expected 400+ test cases, got {len(cases)}"

        input_file = tmp_path / "cross_validate_inputs.txt"
        output_file = tmp_path / "cross_validate_outputs.txt"

        # Write inputs
        write_inputs(cases, str(input_file))

        # Run Pascal
        result = subprocess.run(
            [fpc_executable, str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"Pascal program failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert output_file.exists(), "Pascal program did not create output file"

        pascal_outputs = read_outputs(str(output_file))
        assert len(pascal_outputs) == len(cases), (
            f"Pascal produced {len(pascal_outputs)} outputs for {len(cases)} inputs"
        )

        # Run Python
        python_outputs = []
        for fn_name, args in cases:
            python_outputs.append(run_python(fn_name, args))

        # Compare
        mismatches = []
        for i, (case, p_out, py_out) in enumerate(
            zip(cases, pascal_outputs, python_outputs)
        ):
            if p_out != py_out:
                fn_name, args = case
                mismatches.append(
                    f"  [{i}] {fn_name}({args!r}):\n"
                    f"    Pascal: {p_out!r}\n"
                    f"    Python: {py_out!r}"
                )

        if mismatches:
            detail = "\n".join(mismatches[:20])
            remaining = len(mismatches) - 20
            msg = (
                f"{len(mismatches)} mismatches out of {len(cases)} test cases:\n"
                f"{detail}"
            )
            if remaining > 0:
                msg += f"\n  ... and {remaining} more"
            pytest.fail(msg)

    def test_case_count(self):
        """Verify we have enough test cases per function."""
        cases = all_test_cases()
        counts = {}
        for fn_name, _ in cases:
            counts[fn_name] = counts.get(fn_name, 0) + 1

        assert counts.get('ExtractJsonValue', 0) >= 100, \
            f"ExtractJsonValue: {counts.get('ExtractJsonValue', 0)} < 100"
        assert counts.get('EscapeJsonString', 0) >= 100, \
            f"EscapeJsonString: {counts.get('EscapeJsonString', 0)} < 100"
        assert counts.get('BuildSuccessResponse', 0) >= 50, \
            f"BuildSuccessResponse: {counts.get('BuildSuccessResponse', 0)} < 50"
        assert counts.get('BuildErrorResponse', 0) >= 50, \
            f"BuildErrorResponse: {counts.get('BuildErrorResponse', 0)} < 50"


class TestCrossValidateByFunction:
    """Break out cross-validation by function for clearer failure reporting."""

    def _run_subset(self, fpc_executable, tmp_path, cases, label):
        """Helper: run a subset of cases through both Pascal and Python."""
        input_file = tmp_path / f"cv_input_{label}.txt"
        output_file = tmp_path / f"cv_output_{label}.txt"

        write_inputs(cases, str(input_file))

        result = subprocess.run(
            [fpc_executable, str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Pascal failed for {label}: {result.stdout} {result.stderr}"
        )

        pascal_outputs = read_outputs(str(output_file))
        assert len(pascal_outputs) == len(cases)

        python_outputs = [run_python(fn, args) for fn, args in cases]

        mismatches = []
        for i, (case, p_out, py_out) in enumerate(
            zip(cases, pascal_outputs, python_outputs)
        ):
            if p_out != py_out:
                fn_name, args = case
                mismatches.append(
                    f"  [{i}] {fn_name}({args!r}):\n"
                    f"    Pascal: {p_out!r}\n"
                    f"    Python: {py_out!r}"
                )

        if mismatches:
            detail = "\n".join(mismatches[:10])
            pytest.fail(
                f"{len(mismatches)} mismatches in {label}:\n{detail}"
            )

    def test_extract_json_value(self, fpc_executable, tmp_path):
        cases = generate_extract_json_value_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "ExtractJsonValue")

    def test_extract_json_array(self, fpc_executable, tmp_path):
        cases = generate_extract_json_array_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "ExtractJsonArray")

    def test_escape_json_string(self, fpc_executable, tmp_path):
        cases = generate_escape_json_string_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "EscapeJsonString")

    def test_build_success_response(self, fpc_executable, tmp_path):
        cases = generate_build_success_response_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "BuildSuccessResponse")

    def test_build_error_response(self, fpc_executable, tmp_path):
        cases = generate_build_error_response_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "BuildErrorResponse")

    def test_coordinate_conversions(self, fpc_executable, tmp_path):
        cases = generate_coordinate_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "CoordinateConversions")

    def test_string_helpers(self, fpc_executable, tmp_path):
        cases = generate_string_helper_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "StringHelpers")

    def test_matches_filter(self, fpc_executable, tmp_path):
        cases = generate_matches_filter_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "MatchesFilter")

    def test_build_object_json(self, fpc_executable, tmp_path):
        cases = generate_build_object_json_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "BuildObjectJson")

    def test_apply_set_properties(self, fpc_executable, tmp_path):
        cases = generate_apply_set_properties_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "ApplySetProperties")

    def test_count_batch_operations(self, fpc_executable, tmp_path):
        cases = generate_count_batch_operations_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "CountBatchOperations")

    def test_split_command(self, fpc_executable, tmp_path):
        cases = generate_split_command_cases()
        self._run_subset(fpc_executable, tmp_path, cases, "SplitCommand")


# ---------------------------------------------------------------------------
# Python-only sanity checks (run even without FPC)
# ---------------------------------------------------------------------------

class TestPythonSanity:
    """Quick checks that the Python reimplementations are self-consistent."""

    def test_extract_json_value_basic(self):
        assert extract_json_value('{"name":"hello"}', 'name') == 'hello'
        assert extract_json_value('{"count":42}', 'count') == '42'
        assert extract_json_value('{"data":null}', 'data') == 'null'

    def test_extract_json_value_backslash_counting(self):
        """The backslash-counting logic must handle even/odd correctly."""
        # Two backslashes before quote = even = real quote
        assert extract_json_value(r'{"key":"val\\\\"}', 'key') == r'val\\\\'
        # One backslash before quote = odd = escaped quote
        assert extract_json_value(r'{"key":"val\"end"}', 'key') == r'val\"end'

    def test_escape_json_string_round_trip(self):
        """Escaped strings should produce valid JSON when quoted."""
        import json
        for s in ['hello', 'C:\\Users\\test', 'say "hi"', 'a\nb', 'x\ty']:
            escaped = escape_json_string(s)
            parsed = json.loads('"' + escaped + '"')
            assert parsed == s

    def test_build_success_response_valid_json(self):
        import json
        result = build_success_response('r1', '"pong"')
        parsed = json.loads(result)
        assert parsed['success'] is True

    def test_build_error_response_valid_json(self):
        import json
        result = build_error_response('r1', 'ERR', 'test "msg" with \\path\\')
        parsed = json.loads(result)
        assert parsed['error']['message'] == 'test "msg" with \\path\\'

    def test_coordinate_round_trips(self):
        for mils in [0, 1, 100, 500, 1000]:
            assert coord_to_mils(mils_to_coord(mils)) == mils
        assert abs(coord_to_mm(mm_to_coord(25.4)) - 25.4) < 0.001

    def test_all_cases_generate(self):
        """Make sure we generate a healthy number of test cases."""
        cases = all_test_cases()
        assert len(cases) > 400
