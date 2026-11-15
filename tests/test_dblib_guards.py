# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Source guards on the DbLib handlers in Library.pas.

The handlers open a user's parts database. Three promises keep that safe,
and none of them can be watched from here because the Pascal only runs
inside Altium, so each is pinned against the source:

* READ ONLY. Nothing but SELECT statements, and no ADO member that
  writes (ExecSQL, Post, Edit, a command object).
* NO CALLER TEXT IN SQL. A statement is built from literal fragments, a
  table name that went through DbLibQualifiedTable and a key column that
  went through DbLibQuoteIdent. The search query is matched in Pascal and
  the record key is bound as a parameter, so neither appears in the text.
* THE PASSWORD STAYS IN. The connection string goes nowhere except into
  the connection and the named helpers that read a provider, a data
  source or a redacted copy out of it.

Each guard was checked by putting back the defect it exists to catch: a
LIKE built from the query, a raw table name, the raw connection string in
the reply, an ExecSQL call, a DELETE statement.
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agent.safety import command_is_read_only
from tests.pascal_source import functions, strip_comments

LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"

_HANDLERS = {"Lib_GetDbLibInfo", "Lib_QueryDbLib", "Lib_GetDbLibRecord",
             "Lib_PlaceDbLibComponent", "DbLibSearchOne"}


def _source() -> str:
    return strip_comments(LIBRARY.read_text(encoding="utf-8", errors="replace"))


def _dblib_routines() -> dict[str, str]:
    found = {name: body for name, body in functions(_source()).items()
             if name.startswith("DbLib") or name in _HANDLERS}
    missing = _HANDLERS - set(found)
    assert not missing, (
        f"DbLib handlers {sorted(missing)} not found in Library.pas; every "
        "guard below would pass over nothing")
    return found


def _after_begin(body: str) -> str:
    """The statements of a routine: everything after its first Begin."""
    m = re.search(r"(?mi)^Begin\b", body)
    assert m, "routine without a Begin"
    return body[m.end():]


def _literals(text: str) -> list[str]:
    return [m.group(1).replace("''", "'")
            for m in re.finditer(r"'((?:[^']|'')*)'", text)]


def _sql_calls(text: str) -> list[str]:
    return re.findall(r"\.SQL\.Add\((.*)\);", text)


def test_the_handlers_only_ever_select():
    writes = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC|EXECUTE|"
                        r"MERGE|TRUNCATE|GRANT|INTO)\b", re.I)
    members = re.compile(r"(ExecSQL|ExecProc|\.Execute\b|\.Post\b|\.Edit\b|"
                         r"\.Append\b|\.Insert\(|\.Delete\(|CommandText|TADOCommand|"
                         r"TADODataSet|TADOTable|SaveToFile|WriteString)", re.I)
    statements = 0
    for name, body in _dblib_routines().items():
        code = _after_begin(body)
        code_no_strings = re.sub(r"'(?:[^']|'')*'", "''", code)
        hit = members.search(code_no_strings)
        assert not hit, f"{name} uses {hit.group(0)}, which writes"
        for lit in _literals(code):
            hit = writes.search(lit)
            assert not hit, f"{name} has a string holding {hit.group(0)!r}: {lit!r}"
        for call in _sql_calls(code):
            first = _literals(call)
            assert first and first[0].startswith("SELECT * FROM "), (
                f"{name} sends a statement that does not start with SELECT: {call}")
            statements += 1
    assert statements >= 5, f"only {statements} SELECTs found; the scan has gone blind"


def test_no_statement_carries_text_from_the_caller():
    allowed_literals = {"SELECT * FROM ", " WHERE 1=0", " WHERE ", " = :dblibkey"}
    allowed_names = {"QTable", "QKey"}
    total = len(_sql_calls(_source()))
    seen = 0
    for name, body in _dblib_routines().items():
        code = _after_begin(body)
        for call in _sql_calls(code):
            seen += 1
            for part in re.split(r"\+(?=(?:[^']*'[^']*')*[^']*$)", call):
                part = part.strip()
                if part.startswith("'"):
                    assert part[1:-1] in allowed_literals, (
                        f"{name}: unexpected SQL fragment {part} in {call}")
                else:
                    assert part in allowed_names, (
                        f"{name}: {part!r} goes into a statement; only a table name "
                        f"from DbLibQualifiedTable and a key column from "
                        f"DbLibQuoteIdent may: {call}")
        for var, maker in (("QTable", "DbLibQualifiedTable("), ("QKey", "DbLibQuoteIdent(")):
            for m in re.finditer(rf"\b{var}\s*:=\s*(.*)", code):
                assert m.group(1).startswith(maker), (
                    f"{name} assigns {var} from {m.group(1)!r}; it must come "
                    f"from {maker[:-1]}, which refuses a name that could close "
                    f"its quote")
    assert seen == total and seen, (
        f"{total} SQL.Add calls in Library.pas but {seen} inside the DbLib "
        "routines; a statement outside them is not covered by these guards")
    record = _dblib_routines()["Lib_GetDbLibRecord"]
    assert re.search(r"Parameters\.ParamByName\('dblibkey'\)\.Value\s*:=\s*KeyValue;",
                     record), "the record key must be bound as a parameter"


def test_the_connection_string_goes_nowhere_but_the_connection():
    allowed = [
        r"ConnStr\s*:=\s*DbLibGlobalValue\(Lines,\s*'ConnectionString'\)",
        r"Trim\(ConnStr\)",
        r"ReadConn\s*:=\s*DbLibConnectionForRead\(ConnStr,",
        r"DbLibConnValue\(ConnStr,\s*'(?:Provider|Data Source)'\)",
        r"DbLibConnKind\(ConnStr\)",
        r"DbLibConnHasSecret\(ConnStr\)",
        r"DbLibRedactConnStr\(ConnStr\)",
        r"DbLibIsJetOrAce\(ConnStr\)",
        r"\.ConnectionString\s*:=\s*ReadConn;",
    ]
    checked = 0
    for name in _HANDLERS:
        code = _after_begin(_dblib_routines()[name])
        for line in code.splitlines():
            if not re.search(r"\b(ConnStr|ReadConn)\b", line):
                continue
            checked += 1
            rest = line
            for pat in allowed:
                rest = re.sub(pat, "", rest)
            assert not re.search(r"\b(ConnStr|ReadConn)\b", rest), (
                f"{name} uses the connection string outside the connection and "
                f"the redacting helpers: {line.strip()}")
        assert not re.search(r"\.ConnectionString\b(?!\s*:=)", code), (
            f"{name} reads a connection string back from ADO")
    assert checked >= 8, f"only {checked} uses found; the scan has gone blind"
    info = _dblib_routines()["Lib_GetDbLibInfo"]
    assert "DbLibRedactConnStr(ConnStr)" in info


def test_altium_answers_for_the_dblib_and_nothing_opens_the_file():
    routines = _dblib_routines()
    openers = ("LoadTextFile", "LoadFromFile", "ReadFileContent", "AssignFile",
               "TFileStream", "TIniFile", "TMemIniFile")
    for name, body in routines.items():
        code = _after_begin(body)
        for opener in openers:
            assert opener not in code, (
                f"{name} opens a file with {opener}; what a DbLib declares "
                "comes from Altium's own document")
    for name in ("Lib_GetDbLibInfo", "DbLibSearchOne", "Lib_GetDbLibRecord",
                 "DbLibPlaceTarget"):
        assert "DbLibDefinition(Lines, LibPath, Why)" in routines[name], (
            f"{name} no longer takes its definition from DbLibDefinition")
    definition = _after_begin(routines["DbLibDefinition"])
    assert "IntegratedLibraryManager.GetAvailableDBLibDocAtPath(LibPath)" in definition
    for member in ("GetConnectionString", "GetTableCount", "GetTableNameAt",
                   "TableEnabled", "GetFieldNameAt", "GetParameterNameAt",
                   "IsParameterDatabaseKey"):
        assert f"Doc.{member}" in definition, f"the definition no longer asks {member}"


def test_only_a_library_installed_for_the_read_is_uninstalled():
    definition = _after_begin(_dblib_routines()["DbLibDefinition"])
    install = definition.index("IntegratedLibraryManager.InstallLibrary(LibPath)")
    guard = definition.rindex("If ", 0, install)
    assert "Not DbLibIsInstalled(LibPath)" in definition[guard:install], (
        "a library is installed without first checking it is not installed")
    assert "Added := DbLibIsInstalled(LibPath);" in definition[install:], (
        "Added must say the library joined the installed list in this call")
    assert definition.count("UnInstallLibrary") == 1
    assert re.search(
        r"If Added Then\s+Begin\s+Try IntegratedLibraryManager\.UnInstallLibrary\(LibPath\);",
        definition), "the uninstall must be for a library this call installed"


def test_the_reads_are_reads_to_both_the_safety_layer_and_the_dispatcher():
    for command in ("library.get_dblib_info", "library.query_dblib",
                    "library.get_dblib_record"):
        assert command_is_read_only(command), (
            f"{command} would be refused in EDA_AGENT_READONLY mode")
    assert not command_is_read_only("library.place_dblib_component")
    readonly = functions(_source())["LibActionIsReadOnly"]
    for action in ("get_dblib_info", "query_dblib", "get_dblib_record"):
        assert f"'{action}'" in readonly
    assert "'place_dblib_component'" not in readonly


def test_a_parts_database_link_is_readable_and_never_silently_empty():
    # obj_query answered '' for DatabaseTableName on a database-linked part:
    # the getter had no branch for it, so the link read as absent.
    import re
    from pathlib import Path
    code = (Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas").read_text(
        encoding="utf-8", errors="replace")
    start = code.index("Function GetSchPropertyGated(")
    body = code[start:code.index("Function SetSchProperty(", start)]
    branch = body[body.index("(PropName = 'DatabaseTableName') Or (PropName = 'DatabaseLibraryName')"):]
    branch = branch[:branch.index("Else If PropName")]
    assert "Obj.ObjectId = eSchComponent" in branch
    assert "Comp.DatabaseTableName" in branch and "Comp.DatabaseLibraryName" in branch
    assert "NotePropertyDiag('unreadable', PropName)" in branch
