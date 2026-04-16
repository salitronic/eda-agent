{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{
  Free Pascal test harness for DelphiScript pure logic functions.

  Compiles and runs WITHOUT Altium Designer. Tests the same functions
  that are in the DelphiScript source but using Free Pascal's standard
  library instead of Altium's scripting engine.

  Compile: fpc test_pascal_logic.pas
  Run:     test_pascal_logic.exe

  This tests the ACTUAL Pascal code (ported), not a Python mirror.
  Any test failure here means the logic itself is wrong.
}
program test_pascal_logic;

{$mode delphi}

uses
  SysUtils;

var
  TestCount, PassCount, FailCount : Integer;

{ ========================================================================= }
{ Test framework                                                             }
{ ========================================================================= }

procedure AssertEquals(TestName : String; Expected, Actual : String);
begin
  Inc(TestCount);
  if Expected = Actual then
  begin
    Inc(PassCount);
  end
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: ', TestName);
    WriteLn('  Expected: "', Expected, '"');
    WriteLn('  Actual:   "', Actual, '"');
  end;
end;

procedure AssertEqualsInt(TestName : String; Expected, Actual : Integer);
begin
  Inc(TestCount);
  if Expected = Actual then
  begin
    Inc(PassCount);
  end
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: ', TestName);
    WriteLn('  Expected: ', Expected);
    WriteLn('  Actual:   ', Actual);
  end;
end;

procedure AssertEqualsBool(TestName : String; Expected, Actual : Boolean);
begin
  Inc(TestCount);
  if Expected = Actual then
  begin
    Inc(PassCount);
  end
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: ', TestName);
    if Expected then WriteLn('  Expected: True')
    else WriteLn('  Expected: False');
    if Actual then WriteLn('  Actual:   True')
    else WriteLn('  Actual:   False');
  end;
end;

procedure AssertEqualsFloat(TestName : String; Expected, Actual, Tolerance : Double);
begin
  Inc(TestCount);
  if Abs(Expected - Actual) <= Tolerance then
  begin
    Inc(PassCount);
  end
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: ', TestName);
    WriteLn('  Expected: ', Expected:0:6);
    WriteLn('  Actual:   ', Actual:0:6);
  end;
end;

{ ========================================================================= }
{ Functions under test — copied from DelphiScript source                     }
{ ========================================================================= }

{ --- From Utils.pas --- }

function MilsToCoord(Mils : Integer) : Integer;
begin
  Result := Mils * 10000;
end;

function CoordToMils(Coord : Integer) : Integer;
begin
  Result := Coord div 10000;
end;

function MMToCoord(MM : Double) : Integer;
begin
  Result := Round(MM * 10000000 / 25.4);
end;

function CoordToMM(Coord : Integer) : Double;
begin
  Result := Coord * 25.4 / 10000000;
end;

function BoolToJsonStr(Value : Boolean) : String;
begin
  if Value then Result := 'true'
  else Result := 'false';
end;

function StrToBoolCustom(S : String) : Boolean;
begin
  Result := (LowerCase(S) = 'true') or (S = '1');
end;

function StrToIntDefCustom(S : String; Default : Integer) : Integer;
begin
  if (S = '') or (S = 'null') then
    Result := Default
  else
  begin
    try
      Result := StrToInt(S);
    except
      Result := Default;
    end;
  end;
end;

function StrToFloatDefCustom(S : String; Default : Double) : Double;
begin
  if (S = '') or (S = 'null') then
    Result := Default
  else
  begin
    try
      Result := StrToFloat(S);
    except
      Result := Default;
    end;
  end;
end;

function EscapeJsonString(S : String) : String;
begin
  Result := S;
  Result := StringReplace(Result, '\', '\\', [rfReplaceAll]);
  Result := StringReplace(Result, '"', '\"', [rfReplaceAll]);
  Result := StringReplace(Result, #13, '\r', [rfReplaceAll]);
  Result := StringReplace(Result, #10, '\n', [rfReplaceAll]);
  Result := StringReplace(Result, #9, '\t', [rfReplaceAll]);
end;

{ --- From Main.pas --- }

function IsWhitespaceOrColon(S : String; Idx : Integer) : Boolean;
var
  C : Char;
begin
  C := S[Idx];
  Result := (C = ' ') or (C = ':') or (C = #9) or (C = #10) or (C = #13);
end;

function IsDelimiter(S : String; Idx : Integer) : Boolean;
var
  C : Char;
begin
  if Idx > Length(S) then
  begin
    Result := True;
    Exit;
  end;
  C := S[Idx];
  Result := (C = ',') or (C = '}') or (C = ']') or (C = ' ') or (C = #9) or (C = #10) or (C = #13);
end;

function ExtractJsonValue(Json : String; Key : String) : String;
var
  StartPos, EndPos : Integer;
  SearchKey : String;
  BraceCount : Integer;
begin
  Result := '';
  SearchKey := '"' + Key + '"';
  StartPos := Pos(SearchKey, Json);
  if StartPos > 0 then
  begin
    StartPos := StartPos + Length(SearchKey);
    while (StartPos <= Length(Json)) and IsWhitespaceOrColon(Json, StartPos) do
      Inc(StartPos);

    if StartPos <= Length(Json) then
    begin
      if Json[StartPos] = '"' then
      begin
        Inc(StartPos);
        EndPos := StartPos;
        while (EndPos <= Length(Json)) do
        begin
          if (Json[EndPos] = '"') and ((EndPos = StartPos) or (Json[EndPos - 1] <> '\')) then Break;
          Inc(EndPos);
        end;
        Result := Copy(Json, StartPos, EndPos - StartPos);
      end
      else if Json[StartPos] = '{' then
      begin
        EndPos := StartPos;
        BraceCount := 1;
        Inc(EndPos);
        while (EndPos <= Length(Json)) and (BraceCount > 0) do
        begin
          if Json[EndPos] = '{' then Inc(BraceCount)
          else if Json[EndPos] = '}' then Dec(BraceCount);
          Inc(EndPos);
        end;
        Result := Copy(Json, StartPos, EndPos - StartPos);
      end
      else
      begin
        EndPos := StartPos;
        while (EndPos <= Length(Json)) and (not IsDelimiter(Json, EndPos)) do
          Inc(EndPos);
        Result := Copy(Json, StartPos, EndPos - StartPos);
      end;
    end;
  end;
end;

function ExtractJsonArray(Json : String; Key : String) : String;
var
  StartPos, EndPos : Integer;
  SearchKey : String;
  BracketCount : Integer;
begin
  Result := '';
  SearchKey := '"' + Key + '"';
  StartPos := Pos(SearchKey, Json);
  if StartPos > 0 then
  begin
    StartPos := StartPos + Length(SearchKey);
    while (StartPos <= Length(Json)) and IsWhitespaceOrColon(Json, StartPos) do
      Inc(StartPos);

    if (StartPos <= Length(Json)) and (Json[StartPos] = '[') then
    begin
      EndPos := StartPos;
      BracketCount := 1;
      Inc(EndPos);
      while (EndPos <= Length(Json)) and (BracketCount > 0) do
      begin
        if Json[EndPos] = '[' then Inc(BracketCount)
        else if Json[EndPos] = ']' then Dec(BracketCount);
        Inc(EndPos);
      end;
      Result := Copy(Json, StartPos, EndPos - StartPos);
    end;
  end;
end;

function BuildSuccessResponse(RequestId : String; Data : String) : String;
begin
  if Data = '' then
    Data := 'null';
  Result := '{"id":"' + RequestId + '","success":true,"data":' + Data + ',"error":null}';
end;

function BuildErrorResponse(RequestId : String; ErrorCode : String; ErrorMsg : String) : String;
begin
  ErrorMsg := StringReplace(ErrorMsg, '\', '\\', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, '"', '\"', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, #13, '\r', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, #10, '\n', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, #9, '\t', [rfReplaceAll]);
  Result := '{"id":"' + RequestId + '","success":false,"data":null,"error":{"code":"' + ErrorCode + '","message":"' + ErrorMsg + '"}}';
end;

{ --- Command splitting from Dispatcher.pas --- }

procedure SplitCommand(Command : String; var Category, Action : String);
var
  DotPos : Integer;
begin
  DotPos := Pos('.', Command);
  if DotPos > 0 then
  begin
    Category := Copy(Command, 1, DotPos - 1);
    Action := Copy(Command, DotPos + 1, Length(Command));
  end
  else
  begin
    Category := Command;
    Action := '';
  end;
end;

{ --- Pipe-separated parsing from Generic.pas / Application.pas --- }

type
  TKVPair = record
    Key : String;
    Value : String;
  end;
  TKVArray = array of TKVPair;

function ParsePipeSeparated(S : String) : TKVArray;
var
  Remaining, Pair, Key, Val : String;
  PipePos, EqPos, Count : Integer;
begin
  SetLength(Result, 0);
  Count := 0;
  Remaining := S;
  while Length(Remaining) > 0 do
  begin
    PipePos := Pos('|', Remaining);
    if PipePos = 0 then
    begin
      Pair := Remaining;
      Remaining := '';
    end
    else
    begin
      Pair := Copy(Remaining, 1, PipePos - 1);
      Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
    end;

    EqPos := Pos('=', Pair);
    if EqPos > 0 then
    begin
      Key := Copy(Pair, 1, EqPos - 1);
      Val := Copy(Pair, EqPos + 1, Length(Pair));
      Inc(Count);
      SetLength(Result, Count);
      Result[Count - 1].Key := Key;
      Result[Count - 1].Value := Val;
    end;
  end;
end;

{ ========================================================================= }
{ Test suites                                                                }
{ ========================================================================= }

procedure TestCoordinateConversion;
begin
  WriteLn('--- Coordinate Conversion ---');
  AssertEqualsInt('MilsToCoord(0)', 0, MilsToCoord(0));
  AssertEqualsInt('MilsToCoord(1)', 10000, MilsToCoord(1));
  AssertEqualsInt('MilsToCoord(100)', 1000000, MilsToCoord(100));
  AssertEqualsInt('MilsToCoord(-50)', -500000, MilsToCoord(-50));

  AssertEqualsInt('CoordToMils(0)', 0, CoordToMils(0));
  AssertEqualsInt('CoordToMils(10000)', 1, CoordToMils(10000));
  AssertEqualsInt('CoordToMils(15000)', 1, CoordToMils(15000));
  AssertEqualsInt('CoordToMils(9999)', 0, CoordToMils(9999));

  { Round-trip }
  AssertEqualsInt('RoundTrip(100)', 100, CoordToMils(MilsToCoord(100)));
  AssertEqualsInt('RoundTrip(500)', 500, CoordToMils(MilsToCoord(500)));

  { MM conversion }
  AssertEqualsInt('MMToCoord(25.4)', 10000000, MMToCoord(25.4));
  AssertEqualsFloat('CoordToMM(10000000)', 25.4, CoordToMM(10000000), 0.0001);

  { MM round-trip }
  AssertEqualsFloat('MM RoundTrip(1.0)', 1.0, CoordToMM(MMToCoord(1.0)), 0.001);
  AssertEqualsFloat('MM RoundTrip(2.54)', 2.54, CoordToMM(MMToCoord(2.54)), 0.001);
end;

procedure TestStringConversions;
begin
  WriteLn('--- String Conversions ---');
  AssertEquals('BoolToJsonStr(True)', 'true', BoolToJsonStr(True));
  AssertEquals('BoolToJsonStr(False)', 'false', BoolToJsonStr(False));

  AssertEqualsBool('StrToBool(true)', True, StrToBoolCustom('true'));
  AssertEqualsBool('StrToBool(True)', True, StrToBoolCustom('True'));
  AssertEqualsBool('StrToBool(TRUE)', True, StrToBoolCustom('TRUE'));
  AssertEqualsBool('StrToBool(1)', True, StrToBoolCustom('1'));
  AssertEqualsBool('StrToBool(false)', False, StrToBoolCustom('false'));
  AssertEqualsBool('StrToBool(0)', False, StrToBoolCustom('0'));
  AssertEqualsBool('StrToBool(empty)', False, StrToBoolCustom(''));

  AssertEqualsInt('StrToIntDef(42)', 42, StrToIntDefCustom('42', 0));
  AssertEqualsInt('StrToIntDef(-10)', -10, StrToIntDefCustom('-10', 0));
  AssertEqualsInt('StrToIntDef(empty)', 99, StrToIntDefCustom('', 99));
  AssertEqualsInt('StrToIntDef(null)', 99, StrToIntDefCustom('null', 99));
  AssertEqualsInt('StrToIntDef(abc)', 0, StrToIntDefCustom('abc', 0));

  AssertEqualsFloat('StrToFloatDef(3.14)', 3.14, StrToFloatDefCustom('3.14', 0), 0.001);
  AssertEqualsFloat('StrToFloatDef(empty)', 99.9, StrToFloatDefCustom('', 99.9), 0.001);
  AssertEqualsFloat('StrToFloatDef(null)', 99.9, StrToFloatDefCustom('null', 99.9), 0.001);
  AssertEqualsFloat('StrToFloatDef(abc)', 0, StrToFloatDefCustom('abc', 0), 0.001);
end;

procedure TestEscapeJsonString;
begin
  WriteLn('--- EscapeJsonString ---');
  AssertEquals('No escape needed', 'hello', EscapeJsonString('hello'));
  AssertEquals('Empty string', '', EscapeJsonString(''));
  AssertEquals('Backslash', 'a\\b', EscapeJsonString('a\b'));
  AssertEquals('Quote', 'say \"hello\"', EscapeJsonString('say "hello"'));
  AssertEquals('CR', 'line1\rline2', EscapeJsonString('line1' + #13 + 'line2'));
  AssertEquals('LF', 'line1\nline2', EscapeJsonString('line1' + #10 + 'line2'));
  AssertEquals('Tab', 'col1\tcol2', EscapeJsonString('col1' + #9 + 'col2'));
  AssertEquals('CRLF', 'line1\r\nline2', EscapeJsonString('line1' + #13#10 + 'line2'));
  AssertEquals('Windows path', 'C:\\Users\\test\\file.txt',
    EscapeJsonString('C:\Users\test\file.txt'));
end;

procedure TestExtractJsonValue;
begin
  WriteLn('--- ExtractJsonValue ---');
  AssertEquals('Simple string', 'hello', ExtractJsonValue('{"name":"hello"}', 'name'));
  AssertEquals('Empty string', '', ExtractJsonValue('{"name":""}', 'name'));
  AssertEquals('Number', '42', ExtractJsonValue('{"count":42}', 'count'));
  AssertEquals('Boolean true', 'true', ExtractJsonValue('{"active":true}', 'active'));
  AssertEquals('Boolean false', 'false', ExtractJsonValue('{"active":false}', 'active'));
  AssertEquals('Null', 'null', ExtractJsonValue('{"data":null}', 'data'));
  AssertEquals('Object', '{"x":100}', ExtractJsonValue('{"params":{"x":100}}', 'params'));
  AssertEquals('Key not found', '', ExtractJsonValue('{"name":"hello"}', 'missing'));

  { Multiple keys }
  AssertEquals('Multi key 1', '123',
    ExtractJsonValue('{"id":"123","cmd":"test","params":{}}', 'id'));
  AssertEquals('Multi key 2', 'test',
    ExtractJsonValue('{"id":"123","cmd":"test","params":{}}', 'cmd'));
  AssertEquals('Multi key 3', '{}',
    ExtractJsonValue('{"id":"123","cmd":"test","params":{}}', 'params'));

  { Nested object }
  AssertEquals('Nested', '{"c":{"d":"deep"}}',
    ExtractJsonValue('{"a":{"b":1},"c":{"c":{"d":"deep"}}}', 'c'));

  { Real request format }
  AssertEquals('Real request id', 'abc-123',
    ExtractJsonValue('{"id":"abc-123","command":"application.ping","params":{}}', 'id'));
  AssertEquals('Real request command', 'application.ping',
    ExtractJsonValue('{"id":"abc-123","command":"application.ping","params":{}}', 'command'));
end;

procedure TestExtractJsonArray;
begin
  WriteLn('--- ExtractJsonArray ---');
  AssertEquals('Simple array', '[1,2,3]', ExtractJsonArray('{"items":[1,2,3]}', 'items'));
  AssertEquals('Empty array', '[]', ExtractJsonArray('{"items":[]}', 'items'));
  AssertEquals('Nested arrays', '[[1,2],[3,4]]', ExtractJsonArray('{"m":[[1,2],[3,4]]}', 'm'));
  AssertEquals('Key not found', '', ExtractJsonArray('{"items":[1]}', 'missing'));
  AssertEquals('Not array', '', ExtractJsonArray('{"items":"str"}', 'items'));
end;

procedure TestBuildResponses;
begin
  WriteLn('--- Build Responses ---');
  AssertEquals('Success with string',
    '{"id":"r1","success":true,"data":"pong","error":null}',
    BuildSuccessResponse('r1', '"pong"'));
  AssertEquals('Success with null',
    '{"id":"r2","success":true,"data":null,"error":null}',
    BuildSuccessResponse('r2', 'null'));
  AssertEquals('Success empty becomes null',
    '{"id":"r3","success":true,"data":null,"error":null}',
    BuildSuccessResponse('r3', ''));
  AssertEquals('Success with object',
    '{"id":"r4","success":true,"data":{"v":"1"},"error":null}',
    BuildSuccessResponse('r4', '{"v":"1"}'));

  AssertEquals('Error simple',
    '{"id":"e1","success":false,"data":null,"error":{"code":"ERR","message":"oops"}}',
    BuildErrorResponse('e1', 'ERR', 'oops'));
end;

procedure TestCommandSplitting;
var
  Cat, Act : String;
begin
  WriteLn('--- Command Splitting ---');

  SplitCommand('application.ping', Cat, Act);
  AssertEquals('Split app.ping cat', 'application', Cat);
  AssertEquals('Split app.ping act', 'ping', Act);

  SplitCommand('generic.query_objects', Cat, Act);
  AssertEquals('Split generic cat', 'generic', Cat);
  AssertEquals('Split generic act', 'query_objects', Act);

  SplitCommand('nodot', Cat, Act);
  AssertEquals('Split no dot cat', 'nodot', Cat);
  AssertEquals('Split no dot act', '', Act);

  SplitCommand('a.b.c', Cat, Act);
  AssertEquals('Split multi dot cat', 'a', Cat);
  AssertEquals('Split multi dot act', 'b.c', Act);
end;

procedure TestPipeSeparatedParsing;
var
  Pairs : TKVArray;
begin
  WriteLn('--- Pipe-Separated Parsing ---');

  Pairs := ParsePipeSeparated('Key=Value');
  AssertEqualsInt('Single pair count', 1, Length(Pairs));
  AssertEquals('Single pair key', 'Key', Pairs[0].Key);
  AssertEquals('Single pair val', 'Value', Pairs[0].Value);

  Pairs := ParsePipeSeparated('A=1|B=2|C=3');
  AssertEqualsInt('Multi pair count', 3, Length(Pairs));
  AssertEquals('Multi pair 1', 'A', Pairs[0].Key);
  AssertEquals('Multi pair 2', 'B', Pairs[1].Key);
  AssertEquals('Multi pair 3', 'C', Pairs[2].Key);

  Pairs := ParsePipeSeparated('Text=|Name=R1');
  AssertEqualsInt('Empty value count', 2, Length(Pairs));
  AssertEquals('Empty value', '', Pairs[0].Value);

  Pairs := ParsePipeSeparated('');
  AssertEqualsInt('Empty string', 0, Length(Pairs));

  { Value with = sign }
  Pairs := ParsePipeSeparated('Formula=X=Y');
  AssertEqualsInt('Value with = count', 1, Length(Pairs));
  AssertEquals('Value with = key', 'Formula', Pairs[0].Key);
  AssertEquals('Value with = val', 'X=Y', Pairs[0].Value);

  { Entry without = is skipped }
  Pairs := ParsePipeSeparated('Good=Yes|bad|Also=Good');
  AssertEqualsInt('Skip no-eq count', 2, Length(Pairs));
  AssertEquals('Skip no-eq 1', 'Good', Pairs[0].Key);
  AssertEquals('Skip no-eq 2', 'Also', Pairs[1].Key);
end;

{ ========================================================================= }
{ Main                                                                       }
{ ========================================================================= }

begin
  TestCount := 0;
  PassCount := 0;
  FailCount := 0;

  WriteLn('========================================');
  WriteLn('EDA Agent DelphiScript Logic Tests');
  WriteLn('(Free Pascal test harness)');
  WriteLn('========================================');
  WriteLn;

  TestCoordinateConversion;
  TestStringConversions;
  TestEscapeJsonString;
  TestExtractJsonValue;
  TestExtractJsonArray;
  TestBuildResponses;
  TestCommandSplitting;
  TestPipeSeparatedParsing;

  WriteLn;
  WriteLn('========================================');
  WriteLn('Total: ', TestCount, '  Passed: ', PassCount, '  Failed: ', FailCount);
  WriteLn('========================================');

  if FailCount > 0 then
    Halt(1);
end.
