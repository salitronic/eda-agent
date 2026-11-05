# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Pascal request reader returns top-level keys only.

Compiles the real JSON functions out of Main.pas with fpc and checks them
against Python's json module: a key of the same name inside a nested
object or array, or appearing as a string value, is never returned for a
top-level key, and the per-document index stays right while more
documents are read than it keeps.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pascal_source import functions, strip_comments

MAIN = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Main.pas"
NAMES = ("IsWhitespaceOrColon", "IsDelimiter", "HexDigitValue",
         "UnescapeJsonString", "CanonicalPathValue", "JsonValueAnywhere",
         "JsonBuildIndex", "JsonIndexOf", "ExtractJsonValue")

PROGRAM = """program json_keys;
{$mode delphi}{$H+}
uses SysUtils, Classes;
var
  JsonIdxDoc0, JsonIdxDoc1, JsonIdxDoc2, JsonIdxDoc3 : String;
  JsonIdx0, JsonIdx1, JsonIdx2, JsonIdx3 : String;
  JsonIdxNext : Integer;
  JsonNestedSeen : String;
procedure AppendLog(Line : String); begin end;
function FormatLogStamp(Dummy : Integer) : String; begin Result := ''; end;
function StringReplace(S, Old, New : String; Flags : Integer) : String;
begin Result := SysUtils.StringReplace(S, Old, New, [rfReplaceAll]); end;
@@FUNCS@@
function Hex(S : String) : String;
var I : Integer;
begin
  Result := '';
  for I := 1 to Length(S) do Result := Result + IntToHex(Ord(S[I]), 2);
end;
var Lines : TStringList; I, Bar : Integer; L : String;
begin
  Lines := TStringList.Create;
  Lines.LoadFromFile(ParamStr(1));
  for I := 0 to Lines.Count - 1 do
  begin
    L := Lines[I];
    Bar := Pos(#9, L);
    WriteLn(Hex(ExtractJsonValue(Copy(L, Bar + 1, Length(L)), Copy(L, 1, Bar - 1))));
  end;
end.
"""


@pytest.fixture(scope="module")
def reader(tmp_path_factory):
    if shutil.which("fpc") is None:
        pytest.skip("Free Pascal Compiler (fpc) not on PATH")
    found = functions(strip_comments(MAIN.read_text(encoding="utf-8", errors="replace")))
    missing = [n for n in NAMES if n not in found]
    assert not missing, f"Main.pas no longer defines {missing}"
    src = PROGRAM.replace("@@FUNCS@@", "\n".join(found[n] for n in NAMES))
    work = tmp_path_factory.mktemp("json_keys")
    (work / "json_keys.pas").write_text(src, encoding="latin-1")
    built = subprocess.run(["fpc", "-Mdelphi", "json_keys.pas"], cwd=work,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:]

    def read(cases: list[tuple[str, str]]) -> list[str]:
        for key, doc in cases:
            assert "\t" not in key + doc and "\n" not in key + doc
        data = work / "cases.txt"
        data.write_text("\n".join(f"{k}\t{d}" for k, d in cases) + "\n",
                        encoding="latin-1")
        out = subprocess.run([str(work / "json_keys.exe"), str(data)],
                             capture_output=True, text=True, check=True)
        return [bytes.fromhex(line).decode("latin-1")
                for line in out.stdout.splitlines()]

    return read


def _expected(doc: str, key: str) -> str:
    obj = json.loads(doc)
    if not isinstance(obj, dict) or key not in obj:
        return ""
    value = obj[key]
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def test_a_nested_key_of_the_same_name_is_not_the_top_level_key(reader):
    cases = [
        ("layer", '{"items":[{"layer":"Bottom"}],"layer":"Top"}'),
        ("layer", '{"items":[{"layer":"Bottom"}]}'),
        ("x", '{"vertices":[{"x":1,"y":2}],"x":5}'),
        ("net", '{"filter":{"net":"GND"},"net":"VCC"}'),
        ("net", '{"filter":{"net":"GND"}}'),
        ("layer", '{"name":"layer","width":10}'),
        ("layer", '{"text":"say \\"layer\\": no","layer":"Mid1"}'),
    ]
    assert reader(cases) == ["Top", "", "5", "VCC", "", "", "Mid1"]


def test_values_come_back_as_before(reader):
    cases = [
        ("name", '{"name":"hello","value":"world"}'),
        ("value", '{"name":"hello","value":"world"}'),
        ("missing", '{"name":"hello"}'),
        ("count", '{"count":42,"enabled":true}'),
        ("enabled", '{"count":42,"enabled":true}'),
        ("val", '{"val":null,"next":"ok"}'),
        ("x", '{"x":-100,"y":200}'),
        ("outer", '{"outer":{"inner":"value"}}'),
        ("items", '{"items":[1,2,3],"ok":true}'),
        ("ok", '{"items":[1,2,3],"ok":true}'),
        ("empty", '{"empty":""}'),
        ("msg", '{"msg":"say \\"hi\\" \\\\ there"}'),
        ("a", '{ "a" : 7 , "b" : "x" }'),
        ("b", '{ "a" : 7 , "b" : "x" }'),
        ("o", '{ "o" : { "k" : [ 1 , "}" ] } }'),
        ("s", '{"s":"a}b]c,d:e{f[g"}'),
        ("first", '{"first":1,"first":2}'),
    ]
    assert reader(cases) == [
        "hello", "world", "", "42", "true", "null", "-100",
        '{"inner":"value"}', "[1,2,3]", "true", "", 'say "hi" \\ there',
        "7", "x", '{ "k" : [ 1 , "}" ] }', "a}b]c,d:e{f[g", "1"]


def test_library_and_file_keys_get_one_path_spelling(reader):
    """source_library kept its forward slashes, missed the open library and
    copied a footprint into a hidden second copy that never saved."""
    cases = [
        ("source_library", '{"source_library":"C:/x/y.PcbLib"}'),
        ("dest_library", '{"dest_library":"//srv/x/y.PcbLib"}'),
        ("batch_file", '{"batch_file":"C:/x/b.txt"}'),
        ("destination", '{"destination":"C:/x/out"}'),
        ("library_path", '{"library_path":"C:/x/y.PcbLib"}'),
        ("library", '{"library":"Misc Devices"}'),
        ("text", '{"text":"C:/x/y"}'),
        ("menu_path", '{"menu_path":"File/Open"}'),
    ]
    assert reader(cases) == [
        "C:\\x\\y.PcbLib", "\\\\srv\\x\\y.PcbLib", "C:\\x\\b.txt", "C:\\x\\out",
        "C:\\x\\y.PcbLib", "Misc Devices", "C:/x/y", "File/Open"]


def _random_value(rng: random.Random, keys: list[str], depth: int):
    pick = rng.random()
    if depth < 3 and pick < 0.25:
        return {k: _random_value(rng, keys, depth + 1)
                for k in rng.sample(keys, rng.randint(0, 3))}
    if depth < 3 and pick < 0.4:
        return [_random_value(rng, keys, depth + 1) for _ in range(rng.randint(0, 3))]
    if pick < 0.7:
        alphabet = 'ab"\\/{}[]:, ' + "".join(keys)
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))
    return rng.choice([0, -3, 12.5, True, False, None])


def test_random_documents_agree_with_a_real_parser(reader):
    """Nested objects reuse the top-level names, so any match below the
    top level shows up as a disagreement."""
    rng = random.Random(20261104)
    keys = ["layer", "net", "x", "items", "name"]
    cases = []
    for _ in range(300):
        doc = {k: _random_value(rng, keys, 1)
               for k in rng.sample(keys, rng.randint(0, len(keys)))}
        text = json.dumps(doc, separators=(",", ":"))
        for key in keys:
            cases.append((key, text))
    got = reader(cases)
    wrong = [(k, d, g, _expected(d, k)) for (k, d), g in zip(cases, got)
             if g != _expected(d, k)]
    assert not wrong, wrong[:5]


def test_reading_more_documents_than_the_index_keeps(reader):
    """Six documents read in turn, twice over, through a four-entry index."""
    docs = [json.dumps({"n": i, "k": f"v{i}"}) for i in range(6)]
    cases = [(key, d) for _ in range(2) for d in docs for key in ("n", "k")]
    assert reader(cases) == [v for _ in range(2) for i in range(6)
                             for v in (str(i), f"v{i}")]
