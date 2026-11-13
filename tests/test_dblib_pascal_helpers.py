# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The DbLib helpers in Library.pas, compiled and run under Free Pascal.

These are the parts of the DbLib support that decide what reaches the
database and what reaches the caller: the .DbLib file parser (which table,
which key column), the connection-string tokenizer behind Mode=Read and
password redaction, and DbLibQuoteIdent, the only way a name gets into a
SELECT. Altium cannot run here; FPC can run the very same routines, copied
out of Library.pas by name, so a change to any of them is tested as
shipped.

One shim: DelphiScript reaches a TStringList passed as a parameter through
``L.Get(I)``, the form the shipped HistAdd uses, while FPC keeps ``Get``
protected. The extracted source has ``X.Get(I)`` rewritten to ``X[I]``,
which reads the same item, before it is compiled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: (connection string, fragments that must NOT survive redaction,
#:  fragments that MUST survive it). Shared with the Python redactor's
#: test in test_dblib_tools.py, so both layers face the same strings.
REDACTION_CASES = [
    ("Provider=SQLOLEDB;Data Source=srv;User ID=sa;Password=s3cr3t;Initial Catalog=Parts",
     ["s3cr3t"], ["Data Source=srv", "Initial Catalog=Parts", "User ID=sa"]),
    ('Provider=MSDASQL;Extended Properties="DSN=parts;UID=u;PWD=s3cr3t"',
     ["s3cr3t"], ["DSN=parts", "Provider=MSDASQL"]),
    ("Driver={SQL Server};Server=srv;Pwd={s3c;r3t}",
     ["s3c", "r3t"], ["Server=srv", "Driver={SQL Server}"]),
    ("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=C:\\db\\parts.accdb;"
     "Jet OLEDB:Database Password='s3cr3t'",
     ["s3cr3t"], ["parts.accdb"]),
    ('Password="a;b""c";Provider=x',
     ['b""c', "a;b"], ["Provider=x"]),
    ("PASSWORD = s3cr3t ; Provider=x",
     ["s3cr3t"], ["Provider=x"]),
    ("Provider=Microsoft.Jet.OLEDB.4.0;Data Source=C:\\a.mdb;Persist Security Info=False",
     [], ["Data Source=C:\\a.mdb", "Persist Security Info=False"]),
]

_DBLIB_FILE = [
    "[OutlookSettings]",
    "DBType=0",
    "[DatabaseLinks]",
    "ConnectionString=Provider=Microsoft.ACE.OLEDB.12.0;Data Source=parts.accdb;"
    "Mode=Share Deny Write;Jet OLEDB:Database Password=s3cr3t",
    "LeftQuote=[",
    "RightQuote=]",
    "LibrarySearchPath=C:\\Libs",
    "[Table1]",
    "SchemaName=",
    "TableName=Resistors",
    "Enabled=True",
    "UserWhere=0",
    "UserWhereText=",
    "Key=Part Number",
    "[Table2]",
    "TableName=Caps_Query",
    "Enabled=False",
    "UserWhere=1",
    "UserWhereText=[Corp PN] = '{Corp PN}'",
    "[Table3]",
    "Enabled=True",
    "SchemaName=dbo",
    "TableName=ICs",
    "[Table4]",
    "TableName=Both",
    "Key=Part Number",
    "UserWhere=1",
    "UserWhereText=[Corp PN] = '{Corp PN}'",
    "[FieldMappings]",
    "Options=FieldName=Resistors.Sym|TableNameOnly=Resistors|FieldNameOnly=Sym|"
    "FieldType=1|ParameterName=[Library Ref]|VisibleOnAdd=False",
    "Options=FieldName=Resistors.Fp1|TableNameOnly=Resistors|FieldNameOnly=Fp1|"
    "FieldType=1|ParameterName=[Footprint Ref]",
    "Options=FieldName=Resistors.Fp2|TableNameOnly=Resistors|FieldNameOnly=|"
    "FieldType=1|ParameterName=[Footprint Ref 2]",
    "Options=FieldName=Resistors.Pkg|TableNameOnly=Resistors|FieldNameOnly=Pkg|"
    "FieldType=1|ParameterName=",
]

#: Library.pas routines under test, in definition order (callees first).
_ROUTINES = [
    "SplitNextTab",
    "DbLibLineValue", "DbLibLineHasKey", "DbLibSectionName", "DbLibIsTableSection",
    "DbLibGlobalValue", "DbLibTableNames", "DbLibTableAttr", "DbLibOptionValue",
    "DbLibMappedField", "DbLibKeyFromWhere", "DbLibIndexOfName", "DbLibFindTab",
    "DbLibHasDbLibExt", "DbLibPathProblem", "DbLibConnNextPair", "DbLibUnquote",
    "DbLibDoubled", "DbLibIsSecretKey", "DbLibRedactConnStr", "DbLibConnHasSecret",
    "DbLibConnValue", "DbLibSetConnValue", "DbLibIsJetOrAce", "DbLibIsAbsolutePath",
    "DbLibConnectionForRead", "DbLibConnKind", "DbLibIsPlainIdentChar",
    "DbLibTableQuote", "DbLibQuoteIdent", "DbLibQualifiedTable", "DbLibResolveField",
    "DbLibKeyField",
    "DbLibFootprintFields",
]


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name} in Library.pas"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _pas(s: str) -> str:
    """A Pascal expression for the string s (quotes doubled, tabs as #9)."""
    parts = s.split("\t")
    return "+#9+".join("'" + p.replace("'", "''") + "'" for p in parts) or "''"


def _fpc():
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


def _run(tmp_path: Path, body: list[str]) -> list[str]:
    """Compile the routines plus ``body`` (statements) and return stdout lines."""
    fpc = _fpc()
    code = (SCRIPTS / "Library.pas").read_text(encoding="utf-8", errors="replace")
    source = "\n\n".join(_routine(code, n) for n in _ROUTINES)
    source = re.sub(r"\b(\w+)\.Get\(([^()]*)\)", r"\1[\2]", source)
    program = "\n".join([
        "program dblib;", "{$mode delphi}{$H+}", "uses SysUtils, Classes;",
        source,
        "function B(X : Boolean) : String;",
        "begin if X then Result := 'true' else Result := 'false'; end;",
        "var L, Cols : TStringList; S1, S2 : String;",
        "begin",
        "  L := TStringList.Create; Cols := TStringList.Create;",
        *[f"  L.Add({_pas(line)});" for line in _DBLIB_FILE],
        *body,
        "end.",
    ])
    path = tmp_path / "dblib.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    exe = tmp_path / ("dblib.exe" if os.name == "nt" else "dblib")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


def _check(tmp_path, cases):
    """cases: list of (pascal expression, expected output line)."""
    out = _run(tmp_path, [f"  WriteLn({expr});" for expr, _ in cases])
    assert len(out) == len(cases), out
    for (expr, want), got in zip(cases, out):
        assert got == want, f"{expr}\n  want {want!r}\n  got  {got!r}"


def test_the_dblib_file_parser_finds_tables_settings_and_mappings(tmp_path):
    _check(tmp_path, [
        ("DbLibGlobalValue(L, 'ConnectionString')", _DBLIB_FILE[3].split("=", 1)[1]),
        ("DbLibGlobalValue(L, 'LeftQuote')", "["),
        ("DbLibGlobalValue(L, 'librarysearchpath')", "C:\\Libs"),
        # A key that only appears inside a table section is not global.
        ("'<' + DbLibGlobalValue(L, 'TableName') + '>'", "<>"),
        ("StringReplace(DbLibTableNames(L), #9, '|', [rfReplaceAll])",
         "Resistors|Caps_Query|ICs|Both"),
        # TableName after the other keys of its section still owns them.
        ("DbLibTableAttr(L, 'ICs', 'SchemaName')", "dbo"),
        ("DbLibTableAttr(L, 'icS', 'Enabled')", "True"),
        ("DbLibTableAttr(L, 'Resistors', 'Key')", "Part Number"),
        ("DbLibTableAttr(L, 'Caps_Query', 'UserWhereText')", "[Corp PN] = '{Corp PN}'"),
        ("'<' + DbLibTableAttr(L, 'Nope', 'Key') + '>'", "<>"),
        ("'<' + DbLibTableAttr(L, 'Resistors', 'Nope') + '>'", "<>"),
        ("DbLibMappedField(L, 'Resistors', '[Library Ref]')", "Sym"),
        ("DbLibMappedField(L, 'resistors', '[footprint ref]')", "Fp1"),
        ("DbLibMappedField(L, 'Resistors', '[Footprint Ref 2]')", "Fp2"),
        ("'<' + DbLibMappedField(L, 'Caps_Query', '[Library Ref]') + '>'", "<>"),
        ("DbLibKeyFromWhere('[Corp PN] = ''{Corp PN}''', '[', ']')", "Corp PN"),
        ("DbLibKeyFromWhere('(PN = ''{PN}'')', '', '')", "PN"),
        ("DbLibFindTab('Resistors'#9'ICs', 'ics')", "ICs"),
        ("'<' + DbLibFindTab('Resistors'#9'ICs', 'IC') + '>'", "<>"),
    ])


def test_the_key_column_says_where_it_came_from(tmp_path):
    body = []
    for table, cols, want_key, want_src in [
        ("Caps_Query", ["ID", "corp pn", "Value"], "corp pn", "where_clause"),
        ("Resistors", ["part number", "Sym"], "part number", "key_setting"),
        ("ICs", ["Value", "Part Number"], "Part Number", "part_number_column"),
        ("ICs", ["A", "B"], "A", "first_column"),
        # UserWhere=1 means Altium matches with the where clause, so its
        # column wins over a Key setting left behind in the same section.
        ("Both", ["Part Number", "Corp PN"], "Corp PN", "where_clause"),
    ]:
        body.append("  Cols.Free; Cols := TStringList.Create;")
        body += [f"  Cols.Add({_pas(c)});" for c in cols]
        body.append(f"  S1 := DbLibKeyField(L, Cols, {_pas(table)}, '[', ']', S2);")
        body.append("  WriteLn(S1 + '|' + S2);")
        body.append(f"  // expect {want_key}|{want_src}")
    out = _run(tmp_path, body)
    assert out == ["corp pn|where_clause", "part number|key_setting",
                   "Part Number|part_number_column", "A|first_column",
                   "Corp PN|where_clause"]


def test_footprint_columns_pair_refs_with_libraries(tmp_path):
    out = _run(tmp_path, [
        "  Cols.Add('Fp1'); Cols.Add('Fp2'); Cols.Add('Footprint Path');",
        "  DbLibFootprintFields(L, Cols, 'Resistors', S1, S2);",
        "  WriteLn(StringReplace(S1, #9, '|', [rfReplaceAll]));",
        "  WriteLn(StringReplace(S2, #9, '|', [rfReplaceAll]));",
        "  WriteLn(DbLibResolveField(L, Cols, 'Resistors', '[Library Path]', 'footprint path'));",
    ])
    assert out == ["Fp1|Fp2", "Footprint Path|", "Footprint Path"]


def test_only_a_name_that_cannot_close_its_quote_reaches_a_select(tmp_path):
    _check(tmp_path, [
        ("DbLibQuoteIdent('Part Number', '[', ']')", "[Part Number]"),
        ("DbLibQuoteIdent('Parts', '\"', '\"')", '"Parts"'),
        ("'<' + DbLibQuoteIdent('a]b', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('a[b', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('x];DROP TABLE y', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('x;y', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('a:b', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('O''Brien', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('a\"b', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('tab'#9, '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent(' lead', '[', ']') + '>'", "<>"),
        ("'<' + DbLibQuoteIdent('', '[', ']') + '>'", "<>"),
        # No usable quote pair: plain identifiers only.
        ("DbLibQuoteIdent('Parts_2$', '', '')", "Parts_2$"),
        ("'<' + DbLibQuoteIdent('Part Number', '', '') + '>'", "<>"),
        ("DbLibQualifiedTable('dbo', 'ICs', '[', ']')", "[dbo].[ICs]"),
        ("DbLibQualifiedTable('', 'ICs', '[', ']')", "[ICs]"),
        ("'<' + DbLibQualifiedTable('d]bo', 'ICs', '[', ']') + '>'", "<>"),
    ])


def test_quote_table_names_off_sends_the_table_name_bare(tmp_path):
    out = _run(tmp_path, [
        "  WriteLn(DbLibQualifiedTable('', 'ICs', DbLibTableQuote(L, '['), DbLibTableQuote(L, ']')));",
        "  L.Insert(3, 'QuoteTableNames=0');",
        "  WriteLn(DbLibQualifiedTable('', 'ic', DbLibTableQuote(L, '}'), DbLibTableQuote(L, '{')));",
        "  WriteLn('<' + DbLibQualifiedTable('', 'Part List', DbLibTableQuote(L, '['), DbLibTableQuote(L, ']')) + '>');",
        "  L.Delete(3); L.Insert(3, 'QuoteTableNames=1');",
        "  WriteLn(DbLibQualifiedTable('', 'ICs', DbLibTableQuote(L, '['), DbLibTableQuote(L, ']')));",
    ])
    # unset and 1 quote; 0 sends a plain name bare and refuses one that
    # would need quoting
    assert out == ["[ICs]", "ic", "<>", "[ICs]"]


def test_access_is_opened_read_only_and_nothing_else_is_touched(tmp_path):
    ace = _DBLIB_FILE[3].split("=", 1)[1]
    sql = "Provider=SQLOLEDB;Data Source=srv;User ID=sa;Password=s3cr3t"
    _check(tmp_path, [
        (f"DbLibConnectionForRead({_pas(ace)}, 'C:\\Libs')",
         "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=C:\\Libs\\parts.accdb;"
         "Mode=Read;Jet OLEDB:Database Password=s3cr3t"),
        (f"DbLibConnectionForRead({_pas(sql)}, 'C:\\Libs')", sql),
        ("DbLibConnectionForRead('Provider=Microsoft.Jet.OLEDB.4.0;Data Source=D:\\a.mdb', 'C:\\x\\')",
         "Provider=Microsoft.Jet.OLEDB.4.0;Data Source=D:\\a.mdb;Mode=Read"),
        ("DbLibSetConnValue('a=1;Mode=Share Deny Write;b=2;mode=x', 'Mode', 'Read')",
         "a=1;Mode=Read;b=2"),
        ("DbLibSetConnValue('a=1', 'Mode', 'Read')", "a=1;Mode=Read"),
        ("DbLibConnValue('Provider=A;Extended Properties=\"DSN=x;PWD=y\"', 'extended properties')",
         "DSN=x;PWD=y"),
        (f"DbLibConnKind({_pas(ace)})", "access"),
        ("DbLibConnKind('Provider=Microsoft.ACE.OLEDB.12.0;Data Source=a.xlsx;"
         "Extended Properties=\"Excel 12.0\"')", "excel"),
        (f"DbLibConnKind({_pas(sql)})", "sql_server"),
        ("DbLibConnKind('Driver={SQL Server};Server=x')", "odbc"),
        ("DbLibConnKind('Provider=MSDASQL;DSN=x')", "odbc"),
        ("DbLibConnKind('Provider=Foo')", "other"),
        ("B(DbLibHasDbLibExt('C:\\x\\Lib.DbLib'))", "true"),
        ("B(DbLibHasDbLibExt('C:\\x\\Lib.SVNDbLib'))", "true"),
        ("B(DbLibHasDbLibExt('C:\\x\\Lib.SchLib'))", "false"),
        ("B(DbLibPathProblem('C:\\x\\Lib.DbLib') = '')", "true"),
        ("B(DbLibPathProblem('C:\\x\\Lib.SchLib') = '')", "false"),
        ("B(DbLibPathProblem('') = '')", "false"),
    ])


def test_no_password_survives_redaction(tmp_path):
    body = []
    for conn, _gone, _kept in REDACTION_CASES:
        body.append(f"  WriteLn(DbLibRedactConnStr({_pas(conn)}));")
        body.append(f"  WriteLn(B(DbLibConnHasSecret({_pas(conn)})));")
    body.append("  WriteLn(B(DbLibConnHasSecret('Provider=x;Pwd=')));")
    out = _run(tmp_path, body)
    for i, (conn, gone, kept) in enumerate(REDACTION_CASES):
        redacted, has = out[2 * i], out[2 * i + 1]
        for frag in gone:
            assert frag not in redacted, f"{frag!r} survived in {redacted!r}"
        for frag in kept:
            assert frag in redacted, f"{frag!r} was lost from {redacted!r}"
        assert has == ("true" if gone else "false"), (conn, has)
        if gone:
            assert "***" in redacted
    assert out[-1] == "false"
    # Exact shape for the common case.
    assert out[0] == ("Provider=SQLOLEDB;Data Source=srv;User ID=sa;Password=***;"
                      "Initial Catalog=Parts")


#: A DbLib laid out the way Altium writes one, as verified live on AD26: the
#: connection in [DatabaseLinks], each table keyed by its UserWhereText, and
#: every field mapping in a [FieldMapN] section of its own. The first sample
#: written for the live test put the connection in a [DataSource] section;
#: this reader still parsed it, but Altium found no connection and placed
#: nothing, so the layout itself is pinned here.
_ALTIUM_LAYOUT = [
    "[OutputDatabaseLinkFile]",
    "Version=1.1",
    "[DatabaseLinks]",
    "ConnectionString=Provider=Microsoft.ACE.OLEDB.12.0;Data Source=parts.accdb;"
    "Persist Security Info=False",
    "LeftQuote=[",
    "RightQuote=]",
    "[Table1]",
    "SchemaName=",
    "TableName=Resistors",
    "Enabled=True",
    "UserWhere=1",
    "UserWhereText=[Part Number] = '{Part Number}'",
    "[FieldMap1]",
    "Options=FieldName=Resistors.Library Ref|TableNameOnly=Resistors|FieldNameOnly=Library Ref|"
    "FieldType=1|ParameterName=[Library Ref]|VisibleOnAdd=False|AddMode=0|RemoveMode=0|UpdateMode=0",
    "[FieldMap2]",
    "Options=FieldName=Resistors.Footprint Ref|TableNameOnly=Resistors|FieldNameOnly=Footprint Ref|"
    "FieldType=1|ParameterName=[Footprint Ref]|VisibleOnAdd=False|AddMode=0|RemoveMode=0|UpdateMode=0",
]


def test_altiums_own_layout_is_read_whole(tmp_path):
    body = ["  L.Clear;"] + [f"  L.Add({_pas(line)});" for line in _ALTIUM_LAYOUT]
    body += ["  Cols.Free; Cols := TStringList.Create;"]
    body += [f"  Cols.Add({_pas(c)});" for c in ("Part Number", "Library Ref", "Footprint Ref")]
    body += [
        "  WriteLn(DbLibGlobalValue(L, 'ConnectionString'));",
        "  WriteLn(StringReplace(DbLibTableNames(L), #9, '|', [rfReplaceAll]));",
        "  WriteLn(DbLibMappedField(L, 'Resistors', '[Library Ref]'));",
        "  WriteLn(DbLibMappedField(L, 'Resistors', '[Footprint Ref]'));",
        "  S1 := DbLibKeyField(L, Cols, 'Resistors', '[', ']', S2);",
        "  WriteLn(S1 + '|' + S2);",
    ]
    out = _run(tmp_path, body)
    assert out == [
        _ALTIUM_LAYOUT[3].split("=", 1)[1],
        "Resistors",          # [FieldMapN] sections are not tables
        "Library Ref",
        "Footprint Ref",
        "Part Number|where_clause",
    ]
