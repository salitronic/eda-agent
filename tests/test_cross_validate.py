# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
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

    if fn_name == 'GetBatchField':
        return get_batch_field(args[0], args[1])

    elif fn_name == 'GetBatchTail':
        return get_batch_tail(args[0], args[1])

    elif fn_name == 'PickSession':
        return pick_session(args[0], args[1], int(args[2]), int(args[3]))

    elif fn_name == 'SessionFocusAction':
        return session_focus_action(args[0], args[1])

    elif fn_name == 'BatchFieldSweep':
        return batch_field_sweep(args[0], args[1])

    elif fn_name == 'ExtractJsonValue':
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

def _discover_fpc_unit_paths(fpc_exe: str) -> list:
    """Return candidate -Fu paths for FPC unit directories (fcl-base etc.).

    Scoop/zip FPC installs don't always have a fpc.cfg wired up, so we
    discover the unit tree explicitly. Search:
      1. Directories relative to the fpc binary (typical MSI install).
      2. Scoop apps directory when the binary is a scoop shim.
      3. %USERPROFILE% and %ProgramFiles% walks for freepascal/units/.

    Returns every subdirectory containing a .ppu, one -Fu per directory.
    """
    roots_to_scan: set = set()
    fpc_dir = Path(fpc_exe).resolve().parent

    # Case 1: binary sits under .../bin/<arch>/fpc.exe, units at ../../units.
    for rel in ("../../units", "../units", "units"):
        candidate = (fpc_dir / rel).resolve()
        if candidate.is_dir():
            roots_to_scan.add(candidate)

    # Case 2: scoop shim, actual install is under scoop/apps/freepascal.
    scoop_dir = Path.home() / "scoop" / "apps" / "freepascal" / "current"
    if scoop_dir.is_dir():
        scoop_units = scoop_dir / "units"
        if scoop_units.is_dir():
            roots_to_scan.add(scoop_units)

    # Flatten: every dir with at least one .ppu becomes an -Fu candidate.
    candidates: list = []
    for root in roots_to_scan:
        try:
            for d in root.rglob("*.ppu"):
                parent = d.parent
                if str(parent) not in candidates:
                    candidates.append(str(parent))
        except OSError:
            continue
    return candidates


@pytest.fixture(scope="module")
def fpc_executable():
    """Compile the Pascal cross-validation program. Skip if FPC unavailable."""
    fpc_path = shutil.which("fpc")
    if fpc_path is None:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")

    if not PASCAL_SRC.exists():
        pytest.skip(f"Pascal source not found: {PASCAL_SRC}")

    # Compile. If FPC_UNIT_PATH is set, use it; otherwise auto-discover
    # the fcl-base unit directory so scoop/zip installs work without config.
    cmd = ["fpc"]
    if FPC_UNIT_PATH:
        cmd.append(f"-Fu{FPC_UNIT_PATH}")
    else:
        for path in _discover_fpc_unit_paths(fpc_path):
            cmd.append(f"-Fu{path}")
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


def next_batch_op(remaining: str) -> tuple[str, str]:
    """Main.pas NextBatchOp: returns (op, rest), skipping empty ops."""
    while len(remaining) > 0:
        sep = remaining.find("~~")
        if sep < 0:
            return remaining, ""
        op = remaining[:sep]
        remaining = remaining[sep + 2:]
        if op != "":
            return op, remaining
    return "", ""


def get_batch_field(op: str, key: str) -> str:
    """Main.pas GetBatchField: fields split on ';', key on the FIRST '='."""
    remaining = op
    while len(remaining) > 0:
        sep = remaining.find(";")
        if sep < 0:
            field, remaining = remaining, ""
        else:
            field, remaining = remaining[:sep], remaining[sep + 1:]
        # Pascal's Pos is 1-BASED and returns 0 when absent, so its
        # `EqPos > 0` means "an '=' exists". Python's find is 0-based
        # and returns -1 when absent, so the faithful mirror is >= 0.
        # Writing `> 0` here dropped a field whose '=' is the first
        # character -- "=orphan" matches an EMPTY key in Pascal and
        # returned "orphan" while this returned "". Caught by FPC on the
        # first run, which is the entire point of comparing against the
        # compiled original instead of a careful reading of it.
        eq = field.find("=")
        if eq >= 0:
            fkey, fval = field[:eq], field[eq + 1:]
            if fkey.upper() == key.upper():
                return fval
    return ""


def pick_session(tags: str, current: str, elapsed_ms: int, slot_ms: int) -> str:
    """Main.pas PickSession: a lone session goes; the current one keeps its
    slot until slot_ms; otherwise the next tag after it, wrapping."""
    pending = [t for t in tags.split("|") if t]
    if not pending:
        return ""
    if len(pending) == 1:
        return pending[0]
    if current in pending and elapsed_ms < slot_ms:
        return current
    after = sorted(t for t in pending if t > current)
    return after[0] if after else min(pending)


def session_focus_action(pin: str, current: str) -> str:
    """Main.pas SessionFocusAction: put the session's document back unless
    it has none or it is already in front (paths compared ignoring case)."""
    if not pin or pin.upper() == current.upper():
        return "keep"
    return "restore"


def get_batch_tail(op: str, key: str) -> str:
    """Main.pas GetBatchTail: everything after the first ';<key>=', or after
    '<key>=' at the very start."""
    marker = ";" + key.lower() + "="
    p = op.lower().find(marker)
    if p >= 0:
        return op[p + len(marker):]
    if op.lower().startswith(key.lower() + "="):
        return op[len(key) + 1:]
    return ""


def batch_field_sweep(payload: str, key: str) -> str:
    """Every operation's value for one key, joined with '|'."""
    out = []
    remaining = payload
    while True:
        op, remaining = next_batch_op(remaining)
        if op == "":
            break
        out.append(get_batch_field(op, key))
        if remaining == "":
            break
    return "|".join(out)


def generate_batch_field_cases():
    """Cases for the payload grammar every bulk tool depends on.

    Built partly from the REAL emitter so the parser is exercised against
    strings this codebase actually sends, not only hand-written ones. The
    boundary is where a converter's sanitiser has to agree with Pascal,
    and until now that agreement was asserted by reading the Pascal.
    """
    cases = [
        # Plain lookups, and the case-insensitive key match.
        ("GetBatchField", ["designator=1;x=10;y=20", "x"]),
        ("GetBatchField", ["designator=1;x=10;y=20", "X"]),
        ("GetBatchField", ["DESIGNATOR=A1", "designator"]),
        ("GetBatchField", ["designator=1;x=10", "missing"]),
        ("GetBatchField", ["", "x"]),
        # A value MAY contain '=' -- only the first one splits. An
        # emitter that escaped '=' would corrupt these.
        ("GetBatchField", ["name=A=B;x=1", "name"]),
        ("GetBatchField", ["expr=a==b;x=1", "expr"]),
        # A leading '=' means an empty key, which never matches.
        ("GetBatchField", ["=orphan;x=1", ""]),
        # Field with no '=' at all is skipped, not treated as a key.
        ("GetBatchField", ["bare;x=7", "bare"]),
        ("GetBatchField", ["bare;x=7", "x"]),
        # Trailing separator, empty value, repeated key (first wins).
        ("GetBatchField", ["x=1;", "x"]),
        ("GetBatchField", ["x=;y=2", "x"]),
        ("GetBatchField", ["x=1;x=2", "x"]),
        # Overbar names: a SINGLE '~' is data and must survive.
        ("GetBatchField", ["name=~RESET;x=1", "name"]),
        ("GetBatchField", ["name=~{RESET};x=1", "name"]),
        # Whole-payload sweeps across the '~~' operation separator.
        ("BatchFieldSweep", ["designator=1;x=0~~designator=2;x=10",
                             "designator"]),
        ("BatchFieldSweep", ["designator=1~~~~designator=2", "designator"]),
        ("BatchFieldSweep", ["designator=1;name=~A~~designator=2;name=~B",
                             "name"]),
        ("BatchFieldSweep", ["a=1", "a"]),
        # The set field is read to the end, so its text keeps a ';'.
        ("GetBatchTail", ["scope=active_doc;object_type=eLabel;"
                          "filter=Location.X=100;set=Text=first;second", "set"]),
        ("GetBatchTail", ["scope=a;SET=Text=x", "set"]),
        ("GetBatchTail", ["set=Text=a;b", "set"]),
        ("GetBatchTail", ["scope=a;filter=b", "set"]),
        ("GetBatchTail", ["scope=a;set=", "set"]),
        ("GetBatchTail", ["scope=a;set=Text=x;set=y", "set"]),
        # A session alone is never slotted, however long it has run.
        ("PickSession", ["aaaa1111", "aaaa1111", "999999", "30000"]),
        ("PickSession", ["bbbb2222", "aaaa1111", "0", "30000"]),
        # The current session keeps its slot until it runs out...
        ("PickSession", ["aaaa1111|bbbb2222", "aaaa1111", "29999", "30000"]),
        # ...then the next one in tag order, wrapping to the lowest.
        ("PickSession", ["aaaa1111|bbbb2222", "aaaa1111", "30000", "30000"]),
        ("PickSession", ["aaaa1111|bbbb2222|cccc3333", "cccc3333", "40000", "30000"]),
        ("PickSession", ["cccc3333|aaaa1111", "bbbb2222", "0", "30000"]),
        ("PickSession", ["legacy|aaaa1111", "aaaa1111", "30000", "30000"]),
        ("PickSession", ["", "aaaa1111", "0", "30000"]),
        # A session with no document acts on whatever is in front.
        ("SessionFocusAction", ["", r"C:\B\b.PcbDoc"]),
        ("SessionFocusAction", [r"C:\A\a.PcbDoc", r"C:\A\a.PcbDoc"]),
        ("SessionFocusAction", [r"C:\A\a.PcbDoc", r"c:\a\A.PCBDOC"]),
        ("SessionFocusAction", [r"C:\A\a.PcbDoc", r"C:\B\b.PcbDoc"]),
        ("SessionFocusAction", [r"C:\A\a.PcbDoc", ""]),
    ]

    # And the real thing: payloads the Altium emitter produces.
    try:
        from eda_agent.libimport.easyeda.altium import build_altium_plan
        from eda_agent.libimport.easyeda.document import EasyEdaComponent
        from eda_agent.libimport.kicad.reader import read_kicad_symbol
    except Exception:  # pragma: no cover - converter absent
        return cases

    sym = read_kicad_symbol('''(kicad_symbol_lib (version 20251024) (generator t)
  (symbol "X" (property "Reference" "U" (at 0 0 0))
    (symbol "X_1_1"
      (pin input inverted (at -5 0 0) (length 2)
        (name "~{RESET}") (number "1"))
      (pin open_collector line (at -5 -2 0) (length 2)
        (name "OC=OUT") (number "2"))
      (pin power_in line (at -5 -4 0) (length 2)
        (hide yes) (name "") (number "3")))))''')
    plan = build_altium_plan(
        EasyEdaComponent(mpn="X", symbol=sym.symbol), "a.SchLib", "b.PcbLib")
    step = next(s for s in plan["steps"] if s["tool"] == "lib_add_pins")

    from eda_agent.tools import library as _lib

    payload, _skipped = _lib._pins_payload(step["args"]["pins"])
    for key in ("designator", "name", "electrical_type", "hidden"):
        cases.append(("BatchFieldSweep", [payload, key]))

    # And the land-pattern payload, where a parse mistake is a board
    # that cannot be assembled. Built from a footprint carrying the
    # fields this converter had defects in: a rounded-rectangle pad with
    # an explicit corner ratio, a slotted through-hole, and an unplated
    # hole.
    from eda_agent.libimport.kicad.reader import read_kicad_footprint

    fp = read_kicad_footprint('''(footprint "F" (layer "F.Cu")
  (pad "1" smd roundrect (at -0.25 0) (size 0.4 0.3)
    (roundrect_rratio 0.25) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" thru_hole oval (at 0 0) (size 1 0.8) (drill oval 1.6 0.8)
    (layers "*.Cu" "*.Mask"))
  (pad "3" np_thru_hole circle (at 2 0) (size 1 1) (drill 0.9)
    (layers "*.Cu" "*.Mask")))''')
    fp_plan = build_altium_plan(
        EasyEdaComponent(mpn="F", footprint=fp.footprint),
        "a.SchLib", "b.PcbLib")
    pad_step = next(s for s in fp_plan["steps"]
                    if s["tool"] == "lib_add_footprint_pads")
    pad_payload, _dropped = _lib._pads_payload(pad_step["args"]["pads"])
    for key in ("designator", "shape", "corner_radius", "hole_size", "layer"):
        cases.append(("BatchFieldSweep", [pad_payload, key]))

    # And the package footprint's payloads: the optional pad fields the
    # batch handler reads only when present (a slot, a non-plated hole, a
    # manual paste and mask expansion), and the regions handler's points.
    try:
        from eda_agent.design.landpattern.emit import (
            altium_plan, model_from_package, resolve_altium_layers)
    except Exception:  # pragma: no cover - engine absent
        return cases
    layers = resolve_altium_layers([], assembly_layer="Mechanical13",
                                   courtyard_layer="Mechanical15")
    qfn = model_from_package(
        {"family": "qfn", "pins": 16, "pitch": 0.5, "body_x": [2.9, 3.1],
         "body_y": [2.9, 3.1], "lead_length": [0.3, 0.5],
         "lead_width": [0.18, 0.30], "ep_x": [1.61, 1.75],
         "ep_y": [1.61, 1.75]}, drafting={"mask_expansion": 0.05})
    holes = model_from_package(
        {"family": "custom", "body_x": 6, "body_y": 4, "custom_pads": [
            {"number": "1", "x": -1.5, "y": 0, "width": 2.4, "height": 1.4,
             "shape": "oval", "hole": 0.8, "hole_length": 1.6},
            {"number": "MH", "x": 1.5, "y": 0, "width": 1.0, "height": 1.0,
             "shape": "circle", "hole": 1.0, "plated": False}]})
    for model in (qfn, holes):
        plan = altium_plan(model, layers)
        payload, _ = _lib._pads_payload(plan["pads"], "mm")
        for key in ("x", "hole_length", "hole_rotation", "plated",
                    "paste_expansion", "mask_expansion"):
            cases.append(("BatchFieldSweep", [payload, key]))
        if plan["regions"]:
            regions, _ = _lib._regions_payload(plan["regions"])
            for key in ("layer", "points"):
                cases.append(("BatchFieldSweep", [regions, key]))
    return cases


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
    cases.extend(generate_batch_field_cases())
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


class TestPayloadSurvivesHostileInput:
    """The payload must carry values Pascal can read back INTACT.

    Distinct from the cross-validation above, and the distinction
    matters: that suite proves the Python mirror and the compiled Pascal
    agree on how a string parses. It cannot notice a payload that is
    wrong, because both parsers mangle a corrupted string identically
    and no divergence appears. Disabling the sanitiser entirely leaves
    every cross-validation case green.

    So these assert the property instead: a value containing the
    grammar's own separators must still come back as intended. The
    parser used here is the mirror the FPC suite has just proven
    equivalent to Main.pas, so a pass here is a statement about Pascal.
    """

    def _field(self, payload, key, index=0):
        remaining = payload
        for _ in range(index + 1):
            op, remaining = next_batch_op(remaining)
        return get_batch_field(op, key)

    def test_a_semicolon_in_a_pin_name_cannot_split_the_field(self):
        """';' ends a field, so an unescaped one truncates the value AND
        turns the remainder into a bogus key=value pair."""
        from eda_agent.tools import library as lib

        payload, _ = lib._pins_payload([
            {"designator": "1", "name": "A;rotation=270", "x": 0, "y": 0},
        ])
        assert self._field(payload, "designator") == "1"
        assert self._field(payload, "rotation") == "0", (
            "the injected 'rotation=270' was read as a real field; the pin "
            "would be placed at the attacker's angle")
        assert ";" not in self._field(payload, "name")

    def test_a_double_tilde_cannot_forge_an_extra_pin(self):
        """'~~' ends an operation, so an unescaped one splits one pin
        into two and the second is whatever the value said."""
        from eda_agent.tools import library as lib

        payload, _ = lib._pins_payload([
            {"designator": "1", "name": "A~~designator=99", "x": 0, "y": 0},
        ])
        ops = []
        remaining = payload
        while True:
            op, remaining = next_batch_op(remaining)
            if not op:
                break
            ops.append(op)
        assert len(ops) == 1, f"the payload forged {len(ops)} pins: {ops}"

    def test_a_single_tilde_is_data_and_survives(self):
        """Overbar names are spelled with '~', and mangling them renames
        every active-low pin on the part."""
        from eda_agent.tools import library as lib

        payload, _ = lib._pins_payload([
            {"designator": "1", "name": "~{RESET}", "x": 0, "y": 0},
        ])
        assert self._field(payload, "name") == "~{RESET}"

    def test_an_equals_in_a_value_is_left_alone(self):
        """Only the FIRST '=' splits, so a value may contain more. An
        emitter that escaped them would corrupt real pin names."""
        from eda_agent.tools import library as lib

        payload, _ = lib._pins_payload([
            {"designator": "1", "name": "OC=OUT", "x": 0, "y": 0},
        ])
        assert self._field(payload, "name") == "OC=OUT"

    def test_a_hostile_pad_layer_cannot_move_the_pad(self):
        """Same grammar, and here the consequence is copper in the wrong
        place rather than a misdrawn symbol."""
        from eda_agent.tools import library as lib

        payload, _ = lib._pads_payload([
            {"designator": "1", "x": 10, "y": 20,
             "layer": "TopLayer;x=999", "shape": "rect"},
        ])
        assert self._field(payload, "x") == "10", (
            "an injected coordinate overrode the real one")

    def test_every_pin_payload_builder_shares_one_implementation(self):
        """Three tools built this payload inline, all three raw.

        lib_create_ic_symbol, lib_create_passive_symbol and
        lib_add_pins each formatted their own ";"-joined string and
        interpolated designator, name and electrical_type straight in.
        Any of them carrying a ";" reshaped the payload -- and a pin
        name copied out of a datasheet table is enough to do that by
        accident, so this was not only an adversarial concern.

        Pinned by BEHAVIOUR: hostile input through the tools that build
        a symbol must survive exactly as it does through the shared
        helper. A fourth copy would fail here rather than being noticed
        by someone re-reading the file.
        """
        from eda_agent.tools import library as lib

        hostile = {"designator": "1", "name": "A;rotation=270",
                   "x": 0, "y": 0, "length": 200, "rotation": 0}
        payload, _ = lib._pins_payload([hostile])
        op, _rest = next_batch_op(payload)
        assert get_batch_field(op, "rotation") == "0"
        assert ";" not in get_batch_field(op, "name")

    def test_one_sanitiser_serves_every_tool_module(self):
        """library.py and generic.py must not disagree on the grammar.

        They did: generic.py replaced "~~" with two spaces while
        library.py collapsed runs of tildes to one, so the same pin name
        came out differently depending on which tool sent it. Neither
        was wrong about the injection, but two implementations of one
        wire format is how the next one ends up wrong.

        Asserted on BEHAVIOUR rather than on the import, so a fresh
        local copy fails here even if it happens to look right.
        """
        from eda_agent.tools import generic as gen
        from eda_agent.tools import library as lib

        for value in ("A;x=99", "B~~designator=9", "~~~", "~{RESET}",
                      "OC=OUT", "plain"):
            assert lib._payload_safe(value) == gen.payload_safe(value), (
                f"the two modules disagree on {value!r}")

    def test_a_hostile_stamp_designator_cannot_inject_a_field(self):
        """generic.py escaped every stamp value EXCEPT the designator,
        which is the one field guaranteed to come from the caller."""
        from eda_agent.tools import generic as gen

        clean = gen.payload_safe("R1;Value=999")
        assert ";" not in clean
        assert get_batch_field(f"designator={clean};Value=10", "Value") == "10"

    def test_the_stamp_tool_itself_sanitises_its_designator(self):
        """Exercises the real tool, not just the helper.

        The previous test compares the two modules' sanitisers and would
        pass even if a builder stopped calling one -- which is exactly
        what had happened here, the designator being the single field
        every other value was escaped around. This drives
        sch_set_components_parameters with a recording bridge and reads
        the payload that would have gone to Pascal.
        """
        import asyncio

        from eda_agent.tools import generic as gen_mod

        sent: list[tuple[str, dict]] = []

        class _Bridge:
            def send_command(self, command, params=None, **kw):
                sent.append((command, dict(params or {})))
                return {"success": True}

            async def send_command_async(self, command, params=None, **kw):
                return self.send_command(command, params, **kw)

        captured: dict = {}

        class _Capture:
            def tool(self, *a, **k):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn
                return deco

            def prompt(self, *a, **k):
                return self.tool()

            def resource(self, *a, **k):
                return self.tool()

        original = gen_mod.get_bridge
        gen_mod.get_bridge = lambda: _Bridge()
        try:
            gen_mod.register_generic_tools(_Capture())
            asyncio.run(captured["sch_set_components_parameters"](
                stamps=[{"designator": "R1;Value=999", "Value": "10k"}]))
        finally:
            gen_mod.get_bridge = original

        assert sent, "the tool sent nothing"
        payload = sent[-1][1]["stamps"]
        op, _rest = next_batch_op(payload)
        assert get_batch_field(op, "Value") == "10k", (
            f"an injected Value overrode the real one: {payload!r}")
        assert ";" not in get_batch_field(op, "designator")

    def test_the_template_drawer_shares_the_same_rules(self):
        """sch_draw_template stamps plan-authored values with the same
        sanitiser.

        That path is fed by LLM-written plans, which is exactly where an
        odd string arrives without anyone typing it deliberately: a
        value carrying "~~" would end its operation early and forge an
        extra stamp, and a ";" in a designator would reshape the fields.
        """
        import asyncio
        import json

        from eda_agent.tools import sch_template as mod

        lib_pins = [
            {"designator": "1", "name": "1", "x": -100, "y": 0,
             "orientation": 2, "length": 100},
            {"designator": "2", "name": "2", "x": 100, "y": 0,
             "orientation": 0, "length": 100},
        ]
        sent = []

        class _Bridge:
            rows = []

            async def send_command_async(self, command, params=None, **kw):
                params = dict(params or {})
                sent.append((command, params))
                if command == "library.get_pin_list":
                    return {"pins": lib_pins}
                if command == "generic.get_sch_doc_pins":
                    return {"pins": self.rows}
                if command == "generic.place_sch_components_from_library":
                    for p in mod.st.place_symbol_pins("R1", lib_pins, 0, 0):
                        self.rows.append({
                            "refdes": "R1", "pin_number": p.number,
                            "x_mils": p.root_x, "y_mils": p.root_y,
                            "orientation": p.orientation,
                            "pin_length_mils": 100})
                return {"success": True}

        plan = {"spec": "x", "summary": "x", "sheets": [{"name": "main"}],
                "parts": [{"refdes": "R1", "lib_ref": "RES",
                           "value": "10k~~designator=R99",
                           "manufacturer": "a;b=2"}],
                "nets": [{"name": "N", "pins": [{"refdes": "R1", "pin": "1"},
                                                {"refdes": "R1", "pin": "2"}]}]}
        template = {"parts": [{"designator": "R1", "x": 0, "y": 0}],
                    "wires": [{"net": "N", "path": ["R1.1", [-200, -200],
                                                    [200, -200], "R1.2"]}]}
        tools = {}

        class _M:
            def tool(self, *a, **k):
                def deco(fn):
                    tools[fn.__name__] = fn
                    return fn
                return deco

        original = mod.get_bridge
        mod.get_bridge = lambda: _Bridge()
        try:
            mod.register_sch_template_tools(_M())
            out = asyncio.run(tools["sch_draw_template"](
                template, "S.SchDoc", plan_json=json.dumps(plan)))
        finally:
            mod.get_bridge = original

        assert out["stage"] == "done", out
        payload = next(params["stamps"] for _cmd, params in sent
                       if "stamps" in params)
        ops = []
        remaining = payload
        while True:
            op, remaining = next_batch_op(remaining)
            if not op:
                break
            ops.append(op)
        assert len(ops) == 1, f"the payload forged {len(ops)} stamps: {ops}"
        assert get_batch_field(ops[0], "designator") == "R1"
        assert get_batch_field(ops[0], "Value") == "10k~designator=R99", (
            "an injected Value overrode the real one")
        assert ";" not in get_batch_field(ops[0], "Manufacturer")


class TestIeeePinSymbolsAgainstRealPascal:
    """What decides whether an active-low pin gets its inversion bubble.

    Unlike the suites above, these do NOT compare Pascal against a Python
    mirror. A mirror would be circular here: nothing on the Python side
    needs these ordinals, the emitter only ever sends the NAME. Agreement
    between two things I wrote from the same misreading would prove
    nothing anyway.

    So the expected values below are ground truth lifted from the
    schematic API types reference (TIeeeSymbol, 35 members, eNoSymbol=0),
    and each assertion is against what a real Pascal compiler actually
    produced. Getting an ordinal wrong is silent and ugly: the pin still
    draws, just with the wrong decoration, so a NAND gate reads as an AND.
    """

    def _pascal(self, fpc_executable, tmp_path, cases, label):
        """Run cases through the compiled Pascal and return its raw output."""
        input_file = tmp_path / f"ieee_in_{label}.txt"
        output_file = tmp_path / f"ieee_out_{label}.txt"
        write_inputs(cases, str(input_file))
        result = subprocess.run(
            [fpc_executable, str(input_file), str(output_file)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"Pascal failed for {label}: {result.stdout} {result.stderr}")
        outputs = read_outputs(str(output_file))
        assert len(outputs) == len(cases)
        return outputs

    def test_the_two_ordinals_that_carry_schematic_meaning(
        self, fpc_executable, tmp_path
    ):
        """eDot=1 draws the inversion bubble, eClock=3 draws the wedge.

        Everything else in this feature is plumbing around these two
        numbers, and they are the two most likely to be wrong.
        """
        cases = [("StrToIeeeSymbol", ["dot"]), ("StrToIeeeSymbol", ["clock"])]
        assert self._pascal(fpc_executable, tmp_path, cases, "core") == ["1", "3"]

    def test_named_ordinals_match_the_api_reference(
        self, fpc_executable, tmp_path
    ):
        """Spot-check members spread across the enum, so a shifted list
        cannot pass by getting only the first few right."""
        expected = {
            "no_symbol": "0", "dot": "1", "clock": "3",
            "active_low_input": "4", "open_collector": "9", "hiz": "10",
            "schmitt": "13", "active_low_output": "17", "open_emitter": "23",
            "bidirectional_signal_flow": "34",
        }
        cases = [("StrToIeeeSymbol", [name]) for name in expected]
        got = self._pascal(fpc_executable, tmp_path, cases, "named")
        assert got == list(expected.values())

    def test_every_ordinal_survives_naming_and_reparsing(
        self, fpc_executable, tmp_path
    ):
        """0..34 named then parsed back must be the identity.

        This catches an off-by-one in the token walk. It CANNOT catch a
        wrong ordering, and that is not a hypothetical: reordering the
        name list so 'clock' sits at 2 instead of 3 leaves this test
        green while breaking the other four here. Both converters read
        the same list, so any permutation of it stays self-consistent and
        a round trip is blind to it, the same way a reader/writer round
        trip cannot see a bug the reader and writer share.

        Hence the ground-truth assertions above. This test earns its
        place by covering the walk itself, not the values.
        """
        got = self._pascal(
            fpc_executable, tmp_path, [("IeeeRoundTripSweep", [])], "roundtrip")
        assert got == ["|".join(str(n) for n in range(35))]

    def test_aliases_and_altium_raw_enum_spelling(
        self, fpc_executable, tmp_path
    ):
        """Callers write these several ways; all must land on eDot/eClock."""
        dot_spellings = ["inverted", "INVERTED", "  inverted  ", "inversion",
                         "bubble", "active_low", "activelow", "negated",
                         "eDot", "e_dot"]
        clk_spellings = ["clock", "clk", "CLOCK", "eClock"]
        cases = ([("StrToIeeeSymbol", [s]) for s in dot_spellings]
                 + [("StrToIeeeSymbol", [s]) for s in clk_spellings])
        got = self._pascal(fpc_executable, tmp_path, cases, "alias")
        assert got == ["1"] * len(dot_spellings) + ["3"] * len(clk_spellings)

    def test_bare_ordinals_pass_through_and_junk_falls_back(
        self, fpc_executable, tmp_path
    ):
        """A bare ordinal reaches members with no friendly name. Anything
        unrecognised, out of range, or negative becomes eNoSymbol rather
        than a wrong decoration or a crash inside the pin loop."""
        cases = [
            ("StrToIeeeSymbol", ["9"]),      # in range, passes through
            ("StrToIeeeSymbol", ["34"]),     # last member
            ("StrToIeeeSymbol", ["35"]),     # one past the end
            ("StrToIeeeSymbol", ["-3"]),     # negative
            ("StrToIeeeSymbol", ["999999"]),
            ("StrToIeeeSymbol", ["nonsense"]),
            ("StrToIeeeSymbol", [""]),
            ("StrToIeeeSymbol", ["e"]),      # lone 'e', the prefix-strip edge
            ("StrToIeeeSymbol", ["dot;x=1"]),  # a payload separator leaking in
        ]
        got = self._pascal(fpc_executable, tmp_path, cases, "bounds")
        assert got == ["9", "34", "0", "0", "0", "0", "0", "0", "0"]

    def test_naming_an_ordinal_back(self, fpc_executable, tmp_path):
        """The reverse direction, used when reporting a pin's decoration."""
        cases = [("IeeeSymbolToStr", [n]) for n in ("0", "1", "3", "34", "99", "-1")]
        got = self._pascal(fpc_executable, tmp_path, cases, "tostr")
        assert got == ["no_symbol", "dot", "clock",
                       "bidirectional_signal_flow", "no_symbol", "no_symbol"]
