# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for filter matching and property parsing logic.

Each function here mirrors the corresponding DelphiScript function EXACTLY.
The filter/property parsing is pure string logic with no Altium API dependency.

Corresponding DelphiScript files:
  - Generic.pas:157 MatchesFilter
  - Generic.pas:201 BuildObjectJson
  - Generic.pas:239 ApplySetProperties
  - Application.pas:113 pipe-separated key=value parsing
  - Dispatcher.pas:6 ProcessCommand (command splitting)
"""


# ---------------------------------------------------------------------------
# Python reimplementations of pure-logic filter/parsing functions
# ---------------------------------------------------------------------------


def parse_pipe_separated_conditions(filter_str: str) -> list[tuple[str, str]]:
    """Parse a pipe-separated filter string into (PropName, Value) tuples.

    Mirror: Generic.pas:157 MatchesFilter — the parsing portion.
    Format: "PropName=Value|PropName2=Value2"

    This extracts the parsing logic from MatchesFilter so we can test
    it independently of the GetSchProperty call.
    """
    conditions = []
    if not filter_str:
        return conditions

    remaining = filter_str
    while remaining:
        pipe_pos = remaining.find('|')
        if pipe_pos >= 0:
            condition = remaining[:pipe_pos]
            remaining = remaining[pipe_pos + 1:]
        else:
            condition = remaining
            remaining = ''

        eq_pos = condition.find('=')
        if eq_pos <= 0:
            # No = sign: skip (matches DelphiScript: If EqPos = 0 Then Continue)
            continue
        prop_name = condition[:eq_pos]
        expected = condition[eq_pos + 1:]
        conditions.append((prop_name, expected))

    return conditions


def matches_filter(obj_properties: dict[str, str], filter_str: str) -> bool:
    """Mirror: Generic.pas:157 MatchesFilter

    Instead of calling GetSchProperty, we use a dict of property values.
    The logic is identical: parse pipe-separated conditions, check each
    against the object's property value. ALL conditions must match (AND logic).
    Empty filter matches everything.
    """
    if not filter_str:
        return True

    conditions = parse_pipe_separated_conditions(filter_str)
    for prop_name, expected in conditions:
        actual = obj_properties.get(prop_name, '')
        if actual != expected:
            return False
    return True


def parse_comma_separated_props(props_str: str) -> list[str]:
    """Parse a comma-separated property list into individual property names.

    Mirror: Generic.pas:201 BuildObjectJson — the parsing portion.
    Format: "Prop1,Prop2,Prop3"
    """
    props = []
    if not props_str:
        return props

    remaining = props_str
    while remaining:
        comma_pos = remaining.find(',')
        if comma_pos >= 0:
            props.append(remaining[:comma_pos])
            remaining = remaining[comma_pos + 1:]
        else:
            props.append(remaining)
            remaining = ''
    return props


def build_object_json(obj_properties: dict[str, str], props_str: str) -> str:
    """Mirror: Generic.pas:201 BuildObjectJson

    Builds a JSON object string from the requested properties.
    Uses a mock property dict instead of calling GetSchProperty.
    """
    from tests.test_json_parsing import escape_json_string

    result = '{'
    first = True
    remaining = props_str

    while remaining:
        comma_pos = remaining.find(',')
        if comma_pos >= 0:
            prop_name = remaining[:comma_pos]
            remaining = remaining[comma_pos + 1:]
        else:
            prop_name = remaining
            remaining = ''

        prop_value = obj_properties.get(prop_name, '')

        if not first:
            result += ','
        first = False
        result += '"' + escape_json_string(prop_name) + '":"' + escape_json_string(prop_value) + '"'

    result += '}'
    return result


def parse_pipe_separated_assignments(set_str: str) -> list[tuple[str, str]]:
    """Parse pipe-separated assignments into (PropName, Value) tuples.

    Mirror: Generic.pas:239 ApplySetProperties — the parsing portion.
    Format: "PropName=Value|PropName2=Value2"
    """
    assignments = []
    if not set_str:
        return assignments

    remaining = set_str
    while remaining:
        pipe_pos = remaining.find('|')
        if pipe_pos >= 0:
            assignment = remaining[:pipe_pos]
            remaining = remaining[pipe_pos + 1:]
        else:
            assignment = remaining
            remaining = ''

        eq_pos = assignment.find('=')
        if eq_pos <= 0:
            continue
        prop_name = assignment[:eq_pos]
        prop_value = assignment[eq_pos + 1:]
        assignments.append((prop_name, prop_value))

    return assignments


def split_command(command: str) -> tuple[str, str]:
    """Mirror: Dispatcher.pas:6 ProcessCommand — command splitting only.

    Splits "category.action" into (category, action).
    If no dot, action is empty string.
    """
    dot_pos = command.find('.')
    if dot_pos >= 0:
        category = command[:dot_pos]
        action = command[dot_pos + 1:]
    else:
        category = command
        action = ''
    return category, action


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParsePipeSeparatedConditions:
    """Tests for filter condition parsing."""

    def test_empty_string(self):
        assert parse_pipe_separated_conditions('') == []

    def test_single_condition(self):
        result = parse_pipe_separated_conditions('Text=VCC')
        assert result == [('Text', 'VCC')]

    def test_two_conditions(self):
        result = parse_pipe_separated_conditions('Text=VCC|ObjectId=25')
        assert result == [('Text', 'VCC'), ('ObjectId', '25')]

    def test_three_conditions(self):
        result = parse_pipe_separated_conditions('A=1|B=2|C=3')
        assert result == [('A', '1'), ('B', '2'), ('C', '3')]

    def test_value_with_equals(self):
        """Value can contain = signs — only the first = is the separator."""
        result = parse_pipe_separated_conditions('Formula=X=Y+Z')
        assert result == [('Formula', 'X=Y+Z')]

    def test_empty_value(self):
        result = parse_pipe_separated_conditions('Text=')
        assert result == [('Text', '')]

    def test_no_equals_sign_skipped(self):
        """Conditions without = are skipped."""
        result = parse_pipe_separated_conditions('Text=VCC|badcondition|Name=R1')
        assert result == [('Text', 'VCC'), ('Name', 'R1')]

    def test_pipe_at_end(self):
        """Trailing pipe creates empty condition which is skipped."""
        result = parse_pipe_separated_conditions('Text=VCC|')
        assert result == [('Text', 'VCC')]

    def test_value_with_special_chars(self):
        """Values can contain any characters except pipe."""
        result = parse_pipe_separated_conditions('Name=R1 (100k)')
        assert result == [('Name', 'R1 (100k)')]


class TestMatchesFilter:
    """Tests for MatchesFilter logic (Generic.pas:157)."""

    def test_empty_filter_matches_everything(self):
        assert matches_filter({'Text': 'anything'}, '') is True

    def test_empty_filter_matches_empty_props(self):
        assert matches_filter({}, '') is True

    def test_single_condition_match(self):
        props = {'Text': 'VCC', 'Name': 'N1'}
        assert matches_filter(props, 'Text=VCC') is True

    def test_single_condition_no_match(self):
        props = {'Text': 'GND', 'Name': 'N1'}
        assert matches_filter(props, 'Text=VCC') is False

    def test_multiple_conditions_all_match(self):
        props = {'Text': 'VCC', 'ObjectId': '25', 'Name': 'N1'}
        assert matches_filter(props, 'Text=VCC|ObjectId=25') is True

    def test_multiple_conditions_partial_match(self):
        """AND logic: all must match. First matches, second doesn't."""
        props = {'Text': 'VCC', 'ObjectId': '99'}
        assert matches_filter(props, 'Text=VCC|ObjectId=25') is False

    def test_condition_for_missing_property(self):
        """Property not in dict returns '' — matches only if expected is ''."""
        props = {'Text': 'VCC'}
        assert matches_filter(props, 'MissingProp=') is True
        assert matches_filter(props, 'MissingProp=something') is False

    def test_case_sensitive_matching(self):
        """Filter matching is case-sensitive (Delphi string comparison)."""
        props = {'Text': 'VCC'}
        assert matches_filter(props, 'Text=VCC') is True
        assert matches_filter(props, 'Text=vcc') is False
        assert matches_filter(props, 'Text=Vcc') is False

    def test_exact_string_match(self):
        """Partial matches should NOT match."""
        props = {'Text': 'VCCIO'}
        assert matches_filter(props, 'Text=VCC') is False

    def test_numeric_string_match(self):
        """Numbers are compared as strings."""
        props = {'Location.X': '100', 'Location.Y': '200'}
        assert matches_filter(props, 'Location.X=100') is True
        assert matches_filter(props, 'Location.X=100.0') is False  # String compare


class TestParseCommaSeparatedProps:
    """Tests for property list parsing."""

    def test_empty_string(self):
        assert parse_comma_separated_props('') == []

    def test_single_prop(self):
        assert parse_comma_separated_props('Text') == ['Text']

    def test_two_props(self):
        assert parse_comma_separated_props('Location.X,Location.Y') == ['Location.X', 'Location.Y']

    def test_many_props(self):
        result = parse_comma_separated_props('Text,Name,Location.X,Location.Y,Orientation')
        assert result == ['Text', 'Name', 'Location.X', 'Location.Y', 'Orientation']


class TestBuildObjectJson:
    """Tests for BuildObjectJson (Generic.pas:201)."""

    def test_single_property(self):
        props = {'Text': 'VCC'}
        result = build_object_json(props, 'Text')
        assert result == '{"Text":"VCC"}'

    def test_multiple_properties(self):
        props = {'Location.X': '100', 'Location.Y': '200', 'Text': 'hello'}
        result = build_object_json(props, 'Location.X,Location.Y,Text')
        assert result == '{"Location.X":"100","Location.Y":"200","Text":"hello"}'

    def test_missing_property_returns_empty(self):
        props = {'Text': 'VCC'}
        result = build_object_json(props, 'Text,Missing')
        assert result == '{"Text":"VCC","Missing":""}'

    def test_empty_props_string(self):
        result = build_object_json({}, '')
        assert result == '{}'

    def test_value_needing_escaping(self):
        props = {'Name': 'R1 "100k"'}
        result = build_object_json(props, 'Name')
        assert result == '{"Name":"R1 \\"100k\\""}'

    def test_result_is_valid_json(self):
        """The built JSON should be parseable."""
        import json
        props = {'A': 'hello', 'B': '42', 'C': 'with "quotes"'}
        result = build_object_json(props, 'A,B,C')
        parsed = json.loads(result)
        assert parsed['A'] == 'hello'
        assert parsed['B'] == '42'
        assert parsed['C'] == 'with "quotes"'


class TestParsePipeSeparatedAssignments:
    """Tests for ApplySetProperties parsing (Generic.pas:239)."""

    def test_empty_string(self):
        assert parse_pipe_separated_assignments('') == []

    def test_single_assignment(self):
        result = parse_pipe_separated_assignments('Text=VCC')
        assert result == [('Text', 'VCC')]

    def test_multiple_assignments(self):
        result = parse_pipe_separated_assignments('Text=VCC|Location.X=100|Location.Y=200')
        assert result == [('Text', 'VCC'), ('Location.X', '100'), ('Location.Y', '200')]

    def test_empty_value(self):
        result = parse_pipe_separated_assignments('Text=')
        assert result == [('Text', '')]

    def test_value_with_equals(self):
        """Only first = is the separator."""
        result = parse_pipe_separated_assignments('Formula=a=b')
        assert result == [('Formula', 'a=b')]


class TestSplitCommand:
    """Tests for command splitting in ProcessCommand (Dispatcher.pas:6)."""

    def test_application_ping(self):
        assert split_command('application.ping') == ('application', 'ping')

    def test_generic_query_objects(self):
        assert split_command('generic.query_objects') == ('generic', 'query_objects')

    def test_project_get_documents(self):
        assert split_command('project.get_documents') == ('project', 'get_documents')

    def test_library_create_symbol(self):
        assert split_command('library.create_symbol') == ('library', 'create_symbol')

    def test_no_dot(self):
        assert split_command('ping') == ('ping', '')

    def test_multiple_dots(self):
        """Only splits on first dot."""
        assert split_command('a.b.c') == ('a', 'b.c')

    def test_empty_string(self):
        assert split_command('') == ('', '')

    def test_dot_only(self):
        assert split_command('.') == ('', '')


class TestCommandRouting:
    """Test that all known commands map to valid categories."""

    VALID_CATEGORIES = {'application', 'project', 'library', 'generic'}

    APPLICATION_ACTIONS = [
        'ping', 'get_version', 'get_open_documents', 'get_active_document',
        'set_active_document', 'run_process', 'stop_server',
    ]

    PROJECT_ACTIONS = [
        'create', 'open', 'save', 'close', 'get_documents', 'add_document',
        'remove_document', 'get_parameters', 'set_parameter', 'compile',
        'get_focused', 'get_nets', 'get_bom', 'get_component_info',
        'export_pdf', 'cross_probe', 'get_design_stats', 'get_board_info',
        'annotate', 'generate_output',
    ]

    LIBRARY_ACTIONS = [
        'create_symbol', 'add_pin', 'add_symbol_rectangle', 'add_symbol_line',
        'create_footprint', 'add_footprint_pad', 'add_footprint_track',
        'add_footprint_arc', 'link_footprint', 'link_3d_model',
        'get_components', 'search', 'get_component_details', 'get_installed',
        'batch_set_params', 'batch_rename', 'diff_libraries',
    ]

    GENERIC_ACTIONS = [
        'query_objects', 'modify_objects', 'create_object', 'delete_objects',
        'run_process', 'get_font_spec', 'get_font_id', 'select_objects',
        'deselect_all', 'zoom',
    ]

    def test_all_application_commands_route_correctly(self):
        for action in self.APPLICATION_ACTIONS:
            cat, act = split_command(f'application.{action}')
            assert cat == 'application'
            assert cat in self.VALID_CATEGORIES

    def test_all_project_commands_route_correctly(self):
        for action in self.PROJECT_ACTIONS:
            cat, act = split_command(f'project.{action}')
            assert cat == 'project'
            assert cat in self.VALID_CATEGORIES

    def test_all_library_commands_route_correctly(self):
        for action in self.LIBRARY_ACTIONS:
            cat, act = split_command(f'library.{action}')
            assert cat == 'library'
            assert cat in self.VALID_CATEGORIES

    def test_all_generic_commands_route_correctly(self):
        for action in self.GENERIC_ACTIONS:
            cat, act = split_command(f'generic.{action}')
            assert cat == 'generic'
            assert cat in self.VALID_CATEGORIES

    def test_unknown_category_detected(self):
        cat, act = split_command('unknown.do_thing')
        assert cat not in self.VALID_CATEGORIES
