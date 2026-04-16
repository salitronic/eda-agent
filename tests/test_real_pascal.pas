{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{
  test_real_pascal.pas - Comprehensive FPC test harness for ACTUAL DelphiScript code.

  Tests the REAL functions COPIED from Main.pas, Utils.pas, Generic.pas, and
  Dispatcher.pas with mock Altium objects. Each copied section is annotated with
  its source location so diffs can be audited.

  Compile: fpc -Mdelphi tests/test_real_pascal.pas
  Run:     tests/test_real_pascal.exe

  Target: 200+ test cases covering every code path.
}
program test_real_pascal;

{$mode delphi}
{$H+}

uses
  SysUtils, Math, Classes;

{ ========================================================================= }
{ Test framework                                                             }
{ ========================================================================= }

var
  TestCount, PassCount, FailCount : Integer;
  CurrentSection : String;

procedure AssertEquals(TestName : String; Expected, Actual : String);
begin
  Inc(TestCount);
  if Expected = Actual then
    Inc(PassCount)
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: [', CurrentSection, '] ', TestName);
    WriteLn('  Expected: "', Expected, '"');
    WriteLn('  Actual:   "', Actual, '"');
  end;
end;

procedure AssertEqualsInt(TestName : String; Expected, Actual : Integer);
begin
  Inc(TestCount);
  if Expected = Actual then
    Inc(PassCount)
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: [', CurrentSection, '] ', TestName);
    WriteLn('  Expected: ', Expected);
    WriteLn('  Actual:   ', Actual);
  end;
end;

procedure AssertEqualsFloat(TestName : String; Expected, Actual : Double; Epsilon : Double);
begin
  Inc(TestCount);
  if Abs(Expected - Actual) <= Epsilon then
    Inc(PassCount)
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: [', CurrentSection, '] ', TestName);
    WriteLn('  Expected: ', Expected:0:6);
    WriteLn('  Actual:   ', Actual:0:6);
  end;
end;

procedure AssertTrue(TestName : String; Value : Boolean);
begin
  Inc(TestCount);
  if Value then
    Inc(PassCount)
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: [', CurrentSection, '] ', TestName);
  end;
end;

procedure AssertFalse(TestName : String; Value : Boolean);
begin
  Inc(TestCount);
  if not Value then
    Inc(PassCount)
  else
  begin
    Inc(FailCount);
    WriteLn('FAIL: [', CurrentSection, '] ', TestName, ' (expected False, got True)');
  end;
end;

procedure Section(Name : String);
begin
  CurrentSection := Name;
end;


{ ========================================================================= }
{ Altium type stubs / mock constants                                         }
{ These replace Altium-specific types that don't exist in FPC.              }
{ ========================================================================= }

type
  TCoord = Integer;   // Altium uses Integer-based coords
  TLayer = Integer;   // Layer IDs are integers

const
  // Layer constants - COPIED from Altium SDK headers
  // Schematic object type IDs (mock values for testing)
  eNetLabel      = 25;
  ePort          = 17;
  ePowerObject   = 28;
  eSchComponent  = 1;
  eWire          = 27;
  eBus           = 14;
  eBusEntry      = 15;
  eParameter     = 2;
  ePin           = 3;
  eLabel         = 23;
  eLine          = 13;
  eRectangle     = 10;
  eSheetSymbol   = 16;
  eSheetEntry    = 22;
  eNoERC         = 24;
  eJunction      = 29;
  eImage         = 30;

  // PCB object type IDs (mock values)
  eTrackObject      = 4;
  ePadObject        = 2;
  eViaObject        = 3;
  eComponentObject  = 1;
  eArcObject        = 5;
  eFillObject       = 6;
  eTextObject       = 10;
  ePolyObject       = 11;
  eRegionObject     = 12;
  eRuleObject       = 8;
  eDimensionObject  = 13;

  // Layer constants
  eTopLayer        = 1;
  eMidLayer1       = 2;
  eMidLayer2       = 3;
  eMidLayer3       = 4;
  eMidLayer4       = 5;
  eMidLayer5       = 6;
  eMidLayer6       = 7;
  eMidLayer7       = 8;
  eMidLayer8       = 9;
  eMidLayer9       = 10;
  eMidLayer10      = 11;
  eMidLayer11      = 12;
  eMidLayer12      = 13;
  eMidLayer13      = 14;
  eMidLayer14      = 15;
  eMidLayer15      = 16;
  eMidLayer16      = 17;
  eMidLayer17      = 18;
  eMidLayer18      = 19;
  eMidLayer19      = 20;
  eMidLayer20      = 21;
  eMidLayer21      = 22;
  eMidLayer22      = 23;
  eMidLayer23      = 24;
  eMidLayer24      = 25;
  eMidLayer25      = 26;
  eMidLayer26      = 27;
  eMidLayer27      = 28;
  eMidLayer28      = 29;
  eMidLayer29      = 30;
  eMidLayer30      = 31;
  eBottomLayer     = 32;
  eTopOverlay      = 33;
  eBottomOverlay   = 34;
  eTopPaste        = 35;
  eBottomPaste     = 36;
  eTopSolder       = 37;
  eBottomSolder    = 38;
  eInternalPlane1  = 39;
  eInternalPlane2  = 40;
  eInternalPlane3  = 41;
  eInternalPlane4  = 42;
  eInternalPlane5  = 43;
  eInternalPlane6  = 44;
  eInternalPlane7  = 45;
  eInternalPlane8  = 46;
  eInternalPlane9  = 47;
  eInternalPlane10 = 48;
  eInternalPlane11 = 49;
  eInternalPlane12 = 50;
  eInternalPlane13 = 51;
  eInternalPlane14 = 52;
  eInternalPlane15 = 53;
  eInternalPlane16 = 54;
  eDrillGuide      = 55;
  eDrillDrawing    = 56;
  eMultiLayer      = 57;
  eMechanical1     = 58;
  eMechanical2     = 59;
  eMechanical3     = 60;
  eMechanical4     = 61;
  eMechanical5     = 62;
  eMechanical6     = 63;
  eMechanical7     = 64;
  eMechanical8     = 65;
  eMechanical9     = 66;
  eMechanical10    = 67;
  eMechanical11    = 68;
  eMechanical12    = 69;
  eMechanical13    = 70;
  eMechanical14    = 71;
  eMechanical15    = 72;
  eMechanical16    = 73;
  eKeepOutLayer    = 74;


{ ========================================================================= }
{ Mock Altium schematic object for testing GetSchProperty / MatchesFilter    }
{ ========================================================================= }

type
  TPoint = record
    X, Y : TCoord;
  end;

  TSubObject = record
    Text : String;
  end;

  TMockSchObject = record
    ObjectId     : Integer;
    Location     : TPoint;
    Corner       : TPoint;
    Text         : String;
    Name         : String;
    LibReference : String;
    SourceLibraryName    : String;
    ComponentDescription : String;
    DesignatorStr : String;
    UniqueId     : String;
    Orientation  : Integer;
    FontId       : Integer;
    LineWidth    : Integer;
    Style        : Integer;
    IOType       : Integer;
    Alignment    : Integer;
    Electrical   : Integer;
    Color        : Integer;
    AreaColor    : Integer;
    TextColor    : Integer;
    Justification : Integer;
    Width        : TCoord;
    PinLength    : TCoord;
    XSize        : TCoord;
    YSize        : TCoord;
    IsHidden     : Boolean;
    IsSolid      : Boolean;
    IsMirrored   : Boolean;
    DesignatorSub : TSubObject;
    CommentSub    : TSubObject;
    SheetNameSub  : TSubObject;
  end;


{ ========================================================================= }
{ COPIED FROM Main.pas lines 21-43 -- ReadFileContent                        }
{ FPC adaptation: None needed; this uses standard Pascal file I/O.          }
{ ========================================================================= }

function ReadFileContent(FilePath : String) : String;
var
  F : TextFile;
  Line, Content : String;
begin
  Content := '';
  try
    if FileExists(FilePath) then
    begin
      AssignFile(F, FilePath);
      Reset(F);
      while not EOF(F) do
      begin
        ReadLn(F, Line);
        Content := Content + Line;
      end;
      CloseFile(F);
    end;
  except
    Content := '';
  end;
  Result := Content;
end;

{ ========================================================================= }
{ COPIED FROM Main.pas lines 45-72 -- WriteFileContent                       }
{ FPC adaptation: Sleep needs SysUtils (already in uses).                   }
{ ========================================================================= }

procedure WriteFileContent(FilePath : String; Content : String);
var
  F : TextFile;
begin
  try
    AssignFile(F, FilePath);
    Rewrite(F);
    try
      Write(F, Content);
    finally
      CloseFile(F);
    end;
  except
    // Retry once after short delay
    Sleep(50);
    try
      AssignFile(F, FilePath);
      Rewrite(F);
      try
        Write(F, Content);
      finally
        CloseFile(F);
      end;
    except
      // Silently fail
    end;
  end;
end;

{ ========================================================================= }
{ COPIED FROM Main.pas lines 74-80 -- IsWhitespaceOrColon                    }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function IsWhitespaceOrColon(S : String; Idx : Integer) : Boolean;
var
  C : String;
begin
  C := Copy(S, Idx, 1);
  Result := (C = ' ') or (C = ':') or (C = #9) or (C = #10) or (C = #13);
end;

{ ========================================================================= }
{ COPIED FROM Main.pas lines 82-88 -- IsDelimiter                           }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function IsDelimiter(S : String; Idx : Integer) : Boolean;
var
  C : String;
begin
  C := Copy(S, Idx, 1);
  Result := (C = '') or (C = ',') or (C = '}') or (C = ']') or (C = ' ') or (C = #9) or (C = #10) or (C = #13);
end;

{ ========================================================================= }
{ COPIED FROM Main.pas lines 90-157 -- ExtractJsonValue                      }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function ExtractJsonValue(Json : String; Key : String) : String;
var
  StartPos, EndPos : Integer;
  SearchKey : String;
  BraceCount : Integer;
  BackslashCount, TempPos : Integer;
begin
  Result := '';
  SearchKey := '"' + Key + '"';
  StartPos := Pos(SearchKey, Json);
  if StartPos > 0 then
  begin
    StartPos := StartPos + Length(SearchKey);
    // Skip whitespace and colon
    while (StartPos <= Length(Json)) and IsWhitespaceOrColon(Json, StartPos) do
      Inc(StartPos);

    if StartPos <= Length(Json) then
    begin
      if Copy(Json, StartPos, 1) = '"' then
      begin
        // String value
        Inc(StartPos);
        EndPos := StartPos;
        while (EndPos <= Length(Json)) do
        begin
          if Copy(Json, EndPos, 1) = '"' then
          begin
            // Count consecutive backslashes before this quote
            BackslashCount := 0;
            TempPos := EndPos - 1;
            while (TempPos >= StartPos) and (Copy(Json, TempPos, 1) = '\') do
            begin
              Inc(BackslashCount);
              Dec(TempPos);
            end;
            // Even number of backslashes means quote is real
            if (BackslashCount mod 2) = 0 then Break;
          end;
          Inc(EndPos);
        end;
        Result := Copy(Json, StartPos, EndPos - StartPos);
      end
      else if Copy(Json, StartPos, 1) = '{' then
      begin
        // Object value - find matching brace
        EndPos := StartPos;
        BraceCount := 1;
        Inc(EndPos);
        while (EndPos <= Length(Json)) and (BraceCount > 0) do
        begin
          if Copy(Json, EndPos, 1) = '{' then Inc(BraceCount)
          else if Copy(Json, EndPos, 1) = '}' then Dec(BraceCount);
          Inc(EndPos);
        end;
        Result := Copy(Json, StartPos, EndPos - StartPos);
      end
      else
      begin
        // Number or other value
        EndPos := StartPos;
        while (EndPos <= Length(Json)) and (not IsDelimiter(Json, EndPos)) do
          Inc(EndPos);
        Result := Copy(Json, StartPos, EndPos - StartPos);
      end;
    end;
  end;
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 238-267 -- ExtractJsonArray                    }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

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

    if (StartPos <= Length(Json)) and (Copy(Json, StartPos, 1) = '[') then
    begin
      EndPos := StartPos;
      BracketCount := 1;
      Inc(EndPos);
      while (EndPos <= Length(Json)) and (BracketCount > 0) do
      begin
        if Copy(Json, EndPos, 1) = '[' then Inc(BracketCount)
        else if Copy(Json, EndPos, 1) = ']' then Dec(BracketCount);
        Inc(EndPos);
      end;
      Result := Copy(Json, StartPos, EndPos - StartPos);
    end;
  end;
end;

{ ========================================================================= }
{ COPIED FROM Main.pas lines 159-164 -- BuildSuccessResponse                 }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function BuildSuccessResponse(RequestId : String; Data : String) : String;
begin
  if Data = '' then
    Data := 'null';
  Result := '{"id":"' + RequestId + '","success":true,"data":' + Data + ',"error":null}';
end;

{ ========================================================================= }
{ COPIED FROM Main.pas lines 166-175 -- BuildErrorResponse                   }
{ FPC adaptation: StringReplace -1 -> [rfReplaceAll]                        }
{ ========================================================================= }

function BuildErrorResponse(RequestId : String; ErrorCode : String; ErrorMsg : String) : String;
begin
  // Inline JSON-escape (EscapeJsonString not yet declared at this point in build order)
  ErrorMsg := StringReplace(ErrorMsg, '\', '\\', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, '"', '\"', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, #13, '\r', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, #10, '\n', [rfReplaceAll]);
  ErrorMsg := StringReplace(ErrorMsg, #9, '\t', [rfReplaceAll]);
  Result := '{"id":"' + RequestId + '","success":false,"data":null,"error":{"code":"' + ErrorCode + '","message":"' + ErrorMsg + '"}}';
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 5-8 -- MilsToCoord                            }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function MilsToCoord(Mils : Integer) : TCoord;
begin
  Result := Mils * 10000; // 1 mil = 10000 internal units
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 10-13 -- CoordToMils                          }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function CoordToMils(Coord : TCoord) : Integer;
begin
  Result := Round(Coord / 10000);
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 15-18 -- MMToCoord                            }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function MMToCoord(MM : Double) : TCoord;
begin
  Result := Round(MM * 10000000 / 25.4);
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 20-23 -- CoordToMM                            }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function CoordToMM(Coord : TCoord) : Double;
begin
  Result := Coord * 25.4 / 10000000;
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 25-29 -- BoolToJsonStr                         }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function BoolToJsonStr(Value : Boolean) : String;
begin
  if Value then Result := 'true'
  else Result := 'false';
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 31-34 -- StrToBool                            }
{ FPC adaptation: renamed to StrToBoolDS to avoid conflict with SysUtils    }
{ ========================================================================= }

function StrToBoolDS(S : String) : Boolean;
begin
  Result := (LowerCase(S) = 'true') or (S = '1');
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 36-48 -- StrToFloatDef                         }
{ FPC adaptation: renamed to StrToFloatDefDS to avoid conflict with SysUtils }
{ ========================================================================= }

function StrToFloatDefDS(S : String; Default : Double) : Double;
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

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 50-62 -- StrToIntDef                          }
{ FPC adaptation: renamed to StrToIntDefDS to avoid conflict with SysUtils  }
{ ========================================================================= }

function StrToIntDefDS(S : String; Default : Integer) : Integer;
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

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 64-72 -- EscapeJsonString                      }
{ FPC adaptation: StringReplace -1 -> [rfReplaceAll]                        }
{ ========================================================================= }

function EscapeJsonString(S : String) : String;
begin
  Result := S;
  Result := StringReplace(Result, '\', '\\', [rfReplaceAll]);
  Result := StringReplace(Result, '"', '\"', [rfReplaceAll]);
  Result := StringReplace(Result, #13, '\r', [rfReplaceAll]);
  Result := StringReplace(Result, #10, '\n', [rfReplaceAll]);
  Result := StringReplace(Result, #9, '\t', [rfReplaceAll]);
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 74-154 -- GetLayerFromString                   }
{ FPC adaptation: Converted to if/else chain (FPC does not support string case). }
{ ========================================================================= }

function GetLayerFromString(LayerStr : String) : TLayer;
begin
  if LayerStr = 'TopLayer'        then Result := eTopLayer
  else if LayerStr = 'MidLayer1'       then Result := eMidLayer1
  else if LayerStr = 'MidLayer2'       then Result := eMidLayer2
  else if LayerStr = 'MidLayer3'       then Result := eMidLayer3
  else if LayerStr = 'MidLayer4'       then Result := eMidLayer4
  else if LayerStr = 'MidLayer5'       then Result := eMidLayer5
  else if LayerStr = 'MidLayer6'       then Result := eMidLayer6
  else if LayerStr = 'MidLayer7'       then Result := eMidLayer7
  else if LayerStr = 'MidLayer8'       then Result := eMidLayer8
  else if LayerStr = 'MidLayer9'       then Result := eMidLayer9
  else if LayerStr = 'MidLayer10'      then Result := eMidLayer10
  else if LayerStr = 'MidLayer11'      then Result := eMidLayer11
  else if LayerStr = 'MidLayer12'      then Result := eMidLayer12
  else if LayerStr = 'MidLayer13'      then Result := eMidLayer13
  else if LayerStr = 'MidLayer14'      then Result := eMidLayer14
  else if LayerStr = 'MidLayer15'      then Result := eMidLayer15
  else if LayerStr = 'MidLayer16'      then Result := eMidLayer16
  else if LayerStr = 'MidLayer17'      then Result := eMidLayer17
  else if LayerStr = 'MidLayer18'      then Result := eMidLayer18
  else if LayerStr = 'MidLayer19'      then Result := eMidLayer19
  else if LayerStr = 'MidLayer20'      then Result := eMidLayer20
  else if LayerStr = 'MidLayer21'      then Result := eMidLayer21
  else if LayerStr = 'MidLayer22'      then Result := eMidLayer22
  else if LayerStr = 'MidLayer23'      then Result := eMidLayer23
  else if LayerStr = 'MidLayer24'      then Result := eMidLayer24
  else if LayerStr = 'MidLayer25'      then Result := eMidLayer25
  else if LayerStr = 'MidLayer26'      then Result := eMidLayer26
  else if LayerStr = 'MidLayer27'      then Result := eMidLayer27
  else if LayerStr = 'MidLayer28'      then Result := eMidLayer28
  else if LayerStr = 'MidLayer29'      then Result := eMidLayer29
  else if LayerStr = 'MidLayer30'      then Result := eMidLayer30
  else if LayerStr = 'BottomLayer'     then Result := eBottomLayer
  else if LayerStr = 'TopOverlay'      then Result := eTopOverlay
  else if LayerStr = 'BottomOverlay'   then Result := eBottomOverlay
  else if LayerStr = 'TopPaste'        then Result := eTopPaste
  else if LayerStr = 'BottomPaste'     then Result := eBottomPaste
  else if LayerStr = 'TopSolder'       then Result := eTopSolder
  else if LayerStr = 'BottomSolder'    then Result := eBottomSolder
  else if LayerStr = 'InternalPlane1'  then Result := eInternalPlane1
  else if LayerStr = 'InternalPlane2'  then Result := eInternalPlane2
  else if LayerStr = 'InternalPlane3'  then Result := eInternalPlane3
  else if LayerStr = 'InternalPlane4'  then Result := eInternalPlane4
  else if LayerStr = 'InternalPlane5'  then Result := eInternalPlane5
  else if LayerStr = 'InternalPlane6'  then Result := eInternalPlane6
  else if LayerStr = 'InternalPlane7'  then Result := eInternalPlane7
  else if LayerStr = 'InternalPlane8'  then Result := eInternalPlane8
  else if LayerStr = 'InternalPlane9'  then Result := eInternalPlane9
  else if LayerStr = 'InternalPlane10' then Result := eInternalPlane10
  else if LayerStr = 'InternalPlane11' then Result := eInternalPlane11
  else if LayerStr = 'InternalPlane12' then Result := eInternalPlane12
  else if LayerStr = 'InternalPlane13' then Result := eInternalPlane13
  else if LayerStr = 'InternalPlane14' then Result := eInternalPlane14
  else if LayerStr = 'InternalPlane15' then Result := eInternalPlane15
  else if LayerStr = 'InternalPlane16' then Result := eInternalPlane16
  else if LayerStr = 'DrillGuide'      then Result := eDrillGuide
  else if LayerStr = 'DrillDrawing'    then Result := eDrillDrawing
  else if LayerStr = 'MultiLayer'      then Result := eMultiLayer
  else if LayerStr = 'Mechanical1'     then Result := eMechanical1
  else if LayerStr = 'Mechanical2'     then Result := eMechanical2
  else if LayerStr = 'Mechanical3'     then Result := eMechanical3
  else if LayerStr = 'Mechanical4'     then Result := eMechanical4
  else if LayerStr = 'Mechanical5'     then Result := eMechanical5
  else if LayerStr = 'Mechanical6'     then Result := eMechanical6
  else if LayerStr = 'Mechanical7'     then Result := eMechanical7
  else if LayerStr = 'Mechanical8'     then Result := eMechanical8
  else if LayerStr = 'Mechanical9'     then Result := eMechanical9
  else if LayerStr = 'Mechanical10'    then Result := eMechanical10
  else if LayerStr = 'Mechanical11'    then Result := eMechanical11
  else if LayerStr = 'Mechanical12'    then Result := eMechanical12
  else if LayerStr = 'Mechanical13'    then Result := eMechanical13
  else if LayerStr = 'Mechanical14'    then Result := eMechanical14
  else if LayerStr = 'Mechanical15'    then Result := eMechanical15
  else if LayerStr = 'Mechanical16'    then Result := eMechanical16
  else if LayerStr = 'KeepOutLayer'    then Result := eKeepOutLayer
  else Result := eTopLayer;
end;

{ ========================================================================= }
{ COPIED FROM Utils.pas lines 156-236 -- GetLayerString                      }
{ FPC adaptation: case on integer works natively.                           }
{ ========================================================================= }

function GetLayerString(Layer : TLayer) : String;
begin
  case Layer of
    eTopLayer:        Result := 'TopLayer';
    eMidLayer1:       Result := 'MidLayer1';
    eMidLayer2:       Result := 'MidLayer2';
    eMidLayer3:       Result := 'MidLayer3';
    eMidLayer4:       Result := 'MidLayer4';
    eMidLayer5:       Result := 'MidLayer5';
    eMidLayer6:       Result := 'MidLayer6';
    eMidLayer7:       Result := 'MidLayer7';
    eMidLayer8:       Result := 'MidLayer8';
    eMidLayer9:       Result := 'MidLayer9';
    eMidLayer10:      Result := 'MidLayer10';
    eMidLayer11:      Result := 'MidLayer11';
    eMidLayer12:      Result := 'MidLayer12';
    eMidLayer13:      Result := 'MidLayer13';
    eMidLayer14:      Result := 'MidLayer14';
    eMidLayer15:      Result := 'MidLayer15';
    eMidLayer16:      Result := 'MidLayer16';
    eMidLayer17:      Result := 'MidLayer17';
    eMidLayer18:      Result := 'MidLayer18';
    eMidLayer19:      Result := 'MidLayer19';
    eMidLayer20:      Result := 'MidLayer20';
    eMidLayer21:      Result := 'MidLayer21';
    eMidLayer22:      Result := 'MidLayer22';
    eMidLayer23:      Result := 'MidLayer23';
    eMidLayer24:      Result := 'MidLayer24';
    eMidLayer25:      Result := 'MidLayer25';
    eMidLayer26:      Result := 'MidLayer26';
    eMidLayer27:      Result := 'MidLayer27';
    eMidLayer28:      Result := 'MidLayer28';
    eMidLayer29:      Result := 'MidLayer29';
    eMidLayer30:      Result := 'MidLayer30';
    eBottomLayer:     Result := 'BottomLayer';
    eTopOverlay:      Result := 'TopOverlay';
    eBottomOverlay:   Result := 'BottomOverlay';
    eTopPaste:        Result := 'TopPaste';
    eBottomPaste:     Result := 'BottomPaste';
    eTopSolder:       Result := 'TopSolder';
    eBottomSolder:    Result := 'BottomSolder';
    eInternalPlane1:  Result := 'InternalPlane1';
    eInternalPlane2:  Result := 'InternalPlane2';
    eInternalPlane3:  Result := 'InternalPlane3';
    eInternalPlane4:  Result := 'InternalPlane4';
    eInternalPlane5:  Result := 'InternalPlane5';
    eInternalPlane6:  Result := 'InternalPlane6';
    eInternalPlane7:  Result := 'InternalPlane7';
    eInternalPlane8:  Result := 'InternalPlane8';
    eInternalPlane9:  Result := 'InternalPlane9';
    eInternalPlane10: Result := 'InternalPlane10';
    eInternalPlane11: Result := 'InternalPlane11';
    eInternalPlane12: Result := 'InternalPlane12';
    eInternalPlane13: Result := 'InternalPlane13';
    eInternalPlane14: Result := 'InternalPlane14';
    eInternalPlane15: Result := 'InternalPlane15';
    eInternalPlane16: Result := 'InternalPlane16';
    eDrillGuide:      Result := 'DrillGuide';
    eDrillDrawing:    Result := 'DrillDrawing';
    eMultiLayer:      Result := 'MultiLayer';
    eMechanical1:     Result := 'Mechanical1';
    eMechanical2:     Result := 'Mechanical2';
    eMechanical3:     Result := 'Mechanical3';
    eMechanical4:     Result := 'Mechanical4';
    eMechanical5:     Result := 'Mechanical5';
    eMechanical6:     Result := 'Mechanical6';
    eMechanical7:     Result := 'Mechanical7';
    eMechanical8:     Result := 'Mechanical8';
    eMechanical9:     Result := 'Mechanical9';
    eMechanical10:    Result := 'Mechanical10';
    eMechanical11:    Result := 'Mechanical11';
    eMechanical12:    Result := 'Mechanical12';
    eMechanical13:    Result := 'Mechanical13';
    eMechanical14:    Result := 'Mechanical14';
    eMechanical15:    Result := 'Mechanical15';
    eMechanical16:    Result := 'Mechanical16';
    eKeepOutLayer:    Result := 'KeepOutLayer';
  else
    Result := 'Unknown';
  end;
end;

{ ========================================================================= }
{ COPIED FROM Generic.pas lines 12-32 -- ObjectTypeFromString                }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function ObjectTypeFromString(TypeStr : String) : Integer;
begin
  Result := -1;
  if TypeStr = 'eNetLabel'      then Result := eNetLabel
  else if TypeStr = 'ePort'          then Result := ePort
  else if TypeStr = 'ePowerObject'   then Result := ePowerObject
  else if TypeStr = 'eSchComponent'  then Result := eSchComponent
  else if TypeStr = 'eWire'          then Result := eWire
  else if TypeStr = 'eBus'           then Result := eBus
  else if TypeStr = 'eBusEntry'      then Result := eBusEntry
  else if TypeStr = 'eParameter'     then Result := eParameter
  else if TypeStr = 'ePin'           then Result := ePin
  else if TypeStr = 'eLabel'         then Result := eLabel
  else if TypeStr = 'eLine'          then Result := eLine
  else if TypeStr = 'eRectangle'     then Result := eRectangle
  else if TypeStr = 'eSheetSymbol'   then Result := eSheetSymbol
  else if TypeStr = 'eSheetEntry'    then Result := eSheetEntry
  else if TypeStr = 'eNoERC'         then Result := eNoERC
  else if TypeStr = 'eJunction'      then Result := eJunction
  else if TypeStr = 'eImage'         then Result := eImage;
end;

{ ========================================================================= }
{ COPIED FROM PCBGeneric.pas lines 6-20 -- ObjectTypeFromStringPCB           }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

function ObjectTypeFromStringPCB(TypeStr : String) : Integer;
begin
  Result := -1;
  if TypeStr = 'eTrackObject'         then Result := eTrackObject
  else if TypeStr = 'ePadObject'      then Result := ePadObject
  else if TypeStr = 'eViaObject'      then Result := eViaObject
  else if TypeStr = 'eComponentObject' then Result := eComponentObject
  else if TypeStr = 'eArcObject'      then Result := eArcObject
  else if TypeStr = 'eFillObject'     then Result := eFillObject
  else if TypeStr = 'eTextObject'     then Result := eTextObject
  else if TypeStr = 'ePolyObject'     then Result := ePolyObject
  else if TypeStr = 'eRegionObject'   then Result := eRegionObject
  else if TypeStr = 'eRuleObject'     then Result := eRuleObject
  else if TypeStr = 'eDimensionObject' then Result := eDimensionObject;
end;

{ ========================================================================= }
{ Mock GetSchProperty -- uses TMockSchObject record instead of ISch_*        }
{ LOGIC COPIED FROM Generic.pas lines 40-93 -- GetSchProperty                }
{ FPC adaptation: Uses record fields instead of interface methods.          }
{ ========================================================================= }

function MockGetSchProperty(var Obj : TMockSchObject; PropName : String) : String;
begin
  Result := '';
  try
    // Identity
    if PropName = 'ObjectId'    then Result := IntToStr(Obj.ObjectId)

    // Coordinates (returned in mils)
    else if PropName = 'Location.X'  then Result := IntToStr(CoordToMils(Obj.Location.X))
    else if PropName = 'Location.Y'  then Result := IntToStr(CoordToMils(Obj.Location.Y))
    else if PropName = 'Corner.X'    then Result := IntToStr(CoordToMils(Obj.Corner.X))
    else if PropName = 'Corner.Y'    then Result := IntToStr(CoordToMils(Obj.Corner.Y))

    // String properties
    else if PropName = 'Text'        then Result := Obj.Text
    else if PropName = 'Name'        then Result := Obj.Name
    else if PropName = 'LibReference'       then Result := Obj.LibReference
    else if PropName = 'SourceLibraryName'  then Result := Obj.SourceLibraryName
    else if PropName = 'ComponentDescription' then Result := Obj.ComponentDescription
    else if PropName = 'Designator'  then Result := Obj.DesignatorStr
    else if PropName = 'UniqueId'    then Result := Obj.UniqueId

    // Sub-object string properties
    else if PropName = 'Designator.Text'   then Result := Obj.DesignatorSub.Text
    else if PropName = 'Comment.Text'      then Result := Obj.CommentSub.Text
    else if PropName = 'SheetName.Text'    then Result := Obj.SheetNameSub.Text

    // Integer properties
    else if PropName = 'Orientation' then Result := IntToStr(Obj.Orientation)
    else if PropName = 'FontId'      then Result := IntToStr(Obj.FontId)
    else if PropName = 'LineWidth'   then Result := IntToStr(Obj.LineWidth)
    else if PropName = 'Style'       then Result := IntToStr(Obj.Style)
    else if PropName = 'IOType'      then Result := IntToStr(Obj.IOType)
    else if PropName = 'Alignment'   then Result := IntToStr(Obj.Alignment)
    else if PropName = 'Electrical'  then Result := IntToStr(Obj.Electrical)
    else if PropName = 'Color'       then Result := IntToStr(Obj.Color)
    else if PropName = 'AreaColor'   then Result := IntToStr(Obj.AreaColor)
    else if PropName = 'TextColor'   then Result := IntToStr(Obj.TextColor)
    else if PropName = 'Justification' then Result := IntToStr(Obj.Justification)

    // Coord properties (returned in mils)
    else if PropName = 'Width'       then Result := IntToStr(CoordToMils(Obj.Width))
    else if PropName = 'PinLength'   then Result := IntToStr(CoordToMils(Obj.PinLength))
    else if PropName = 'XSize'       then Result := IntToStr(CoordToMils(Obj.XSize))
    else if PropName = 'YSize'       then Result := IntToStr(CoordToMils(Obj.YSize))

    // Boolean properties
    else if PropName = 'IsHidden'    then Result := BoolToJsonStr(Obj.IsHidden)
    else if PropName = 'IsSolid'     then Result := BoolToJsonStr(Obj.IsSolid)
    else if PropName = 'IsMirrored'  then Result := BoolToJsonStr(Obj.IsMirrored);
  except
    Result := '';
  end;
end;

{ ========================================================================= }
{ Mock SetSchProperty -- LOGIC COPIED FROM Generic.pas lines 101-149        }
{ FPC adaptation: Uses record fields, renamed StrToIntDef/StrToBool.        }
{ ========================================================================= }

procedure MockSetSchProperty(var Obj : TMockSchObject; PropName : String; Value : String);
begin
  try
    if PropName = 'Location.X'       then Obj.Location.X := MilsToCoord(StrToIntDefDS(Value, 0))
    else if PropName = 'Location.Y'  then Obj.Location.Y := MilsToCoord(StrToIntDefDS(Value, 0))
    else if PropName = 'Corner.X'    then Obj.Corner.X := MilsToCoord(StrToIntDefDS(Value, 0))
    else if PropName = 'Corner.Y'    then Obj.Corner.Y := MilsToCoord(StrToIntDefDS(Value, 0))

    else if PropName = 'Text'        then Obj.Text := Value
    else if PropName = 'Name'        then Obj.Name := Value
    else if PropName = 'LibReference'       then Obj.LibReference := Value
    else if PropName = 'ComponentDescription' then Obj.ComponentDescription := Value
    else if PropName = 'Designator'  then Obj.DesignatorStr := Value

    else if PropName = 'Designator.Text'   then Obj.DesignatorSub.Text := Value
    else if PropName = 'Comment.Text'      then Obj.CommentSub.Text := Value
    else if PropName = 'SheetName.Text'    then Obj.SheetNameSub.Text := Value

    else if PropName = 'Orientation' then Obj.Orientation := StrToIntDefDS(Value, 0)
    else if PropName = 'FontId'      then Obj.FontId := StrToIntDefDS(Value, 1)
    else if PropName = 'LineWidth'   then Obj.LineWidth := StrToIntDefDS(Value, 1)
    else if PropName = 'Style'       then Obj.Style := StrToIntDefDS(Value, 0)
    else if PropName = 'IOType'      then Obj.IOType := StrToIntDefDS(Value, 0)
    else if PropName = 'Alignment'   then Obj.Alignment := StrToIntDefDS(Value, 0)
    else if PropName = 'Electrical'  then Obj.Electrical := StrToIntDefDS(Value, 0)
    else if PropName = 'Color'       then Obj.Color := StrToIntDefDS(Value, 0)
    else if PropName = 'AreaColor'   then Obj.AreaColor := StrToIntDefDS(Value, 0)
    else if PropName = 'TextColor'   then Obj.TextColor := StrToIntDefDS(Value, 0)
    else if PropName = 'Justification' then Obj.Justification := StrToIntDefDS(Value, 0)

    else if PropName = 'Width'       then Obj.Width := MilsToCoord(StrToIntDefDS(Value, 0))
    else if PropName = 'PinLength'   then Obj.PinLength := MilsToCoord(StrToIntDefDS(Value, 0))
    else if PropName = 'XSize'       then Obj.XSize := MilsToCoord(StrToIntDefDS(Value, 0))
    else if PropName = 'YSize'       then Obj.YSize := MilsToCoord(StrToIntDefDS(Value, 0))

    else if PropName = 'IsHidden'    then Obj.IsHidden := StrToBoolDS(Value)
    else if PropName = 'IsSolid'     then Obj.IsSolid := StrToBoolDS(Value)
    else if PropName = 'IsMirrored'  then Obj.IsMirrored := StrToBoolDS(Value);
  except
  end;
end;

{ ========================================================================= }
{ Mock MatchesFilter -- LOGIC COPIED FROM Generic.pas lines 157-195         }
{ FPC adaptation: Calls MockGetSchProperty instead of GetSchProperty.       }
{ ========================================================================= }

function MockMatchesFilter(var Obj : TMockSchObject; FilterStr : String) : Boolean;
var
  Remaining, Condition, PropName, Expected, Actual : String;
  PipePos, EqPos : Integer;
begin
  Result := True;
  if FilterStr = '' then Exit;

  Remaining := FilterStr;
  while Remaining <> '' do
  begin
    // Extract next pipe-separated condition
    PipePos := Pos('|', Remaining);
    if PipePos > 0 then
    begin
      Condition := Copy(Remaining, 1, PipePos - 1);
      Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
    end
    else
    begin
      Condition := Remaining;
      Remaining := '';
    end;

    // Parse "PropName=Value"
    EqPos := Pos('=', Condition);
    if EqPos = 0 then Continue;
    PropName := Copy(Condition, 1, EqPos - 1);
    Expected := Copy(Condition, EqPos + 1, Length(Condition));

    // Compare
    Actual := MockGetSchProperty(Obj, PropName);
    if Actual <> Expected then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

{ ========================================================================= }
{ Mock BuildObjectJson -- LOGIC COPIED FROM Generic.pas lines 201-233       }
{ FPC adaptation: Calls MockGetSchProperty.                                 }
{ ========================================================================= }

function MockBuildObjectJson(var Obj : TMockSchObject; PropsStr : String) : String;
var
  Remaining, PropName, PropValue : String;
  CommaPos : Integer;
  First : Boolean;
begin
  Result := '{';
  First := True;
  Remaining := PropsStr;

  while Remaining <> '' do
  begin
    CommaPos := Pos(',', Remaining);
    if CommaPos > 0 then
    begin
      PropName := Copy(Remaining, 1, CommaPos - 1);
      Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
    end
    else
    begin
      PropName := Remaining;
      Remaining := '';
    end;

    PropValue := MockGetSchProperty(Obj, PropName);

    if not First then Result := Result + ',';
    First := False;
    Result := Result + '"' + EscapeJsonString(PropName) + '":"' + EscapeJsonString(PropValue) + '"';
  end;

  Result := Result + '}';
end;

{ ========================================================================= }
{ Mock ApplySetProperties -- LOGIC COPIED FROM Generic.pas lines 239-266    }
{ FPC adaptation: Calls MockSetSchProperty.                                 }
{ ========================================================================= }

procedure MockApplySetProperties(var Obj : TMockSchObject; SetStr : String);
var
  Remaining, Assignment, PropName, PropValue : String;
  PipePos, EqPos : Integer;
begin
  Remaining := SetStr;
  while Remaining <> '' do
  begin
    PipePos := Pos('|', Remaining);
    if PipePos > 0 then
    begin
      Assignment := Copy(Remaining, 1, PipePos - 1);
      Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
    end
    else
    begin
      Assignment := Remaining;
      Remaining := '';
    end;

    EqPos := Pos('=', Assignment);
    if EqPos = 0 then Continue;
    PropName := Copy(Assignment, 1, EqPos - 1);
    PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));

    MockSetSchProperty(Obj, PropName, PropValue);
  end;
end;

{ ========================================================================= }
{ COPIED FROM Dispatcher.pas lines 6-33 -- ProcessCommand (routing only)     }
{ FPC adaptation: Returns category/action for testing; no real handlers.    }
{ ========================================================================= }

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

{ ========================================================================= }
{ Pipe-separated key=value parser -- LOGIC COPIED FROM Application.pas       }
{ lines 112-126 (App_RunProcess) and Generic.pas lines 722-748              }
{ FPC adaptation: None needed.                                              }
{ ========================================================================= }

procedure ParsePipeSeparatedParams(ParamStr : String; var Keys, Values : array of String; var Count : Integer);
var
  Remaining, Pair, Key, Val : String;
  PipePos, EqPos : Integer;
begin
  Count := 0;
  Remaining := ParamStr;
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
      if Count <= High(Keys) then
      begin
        Keys[Count] := Key;
        Values[Count] := Val;
        Inc(Count);
      end;
    end;
  end;
end;

{ ========================================================================= }
{ Helper: Initialize a mock object with defaults                             }
{ ========================================================================= }

procedure InitMockObject(var Obj : TMockSchObject);
begin
  FillChar(Obj, SizeOf(Obj), 0);
  Obj.ObjectId := 0;
  Obj.Text := '';
  Obj.Name := '';
  Obj.LibReference := '';
  Obj.SourceLibraryName := '';
  Obj.ComponentDescription := '';
  Obj.DesignatorStr := '';
  Obj.UniqueId := '';
  Obj.DesignatorSub.Text := '';
  Obj.CommentSub.Text := '';
  Obj.SheetNameSub.Text := '';
end;

{ ========================================================================= }
{ TEST SUITES                                                                }
{ ========================================================================= }

procedure TestReadWriteFileContent;
var
  TmpPath, Content, ReadBack : String;
begin
  Section('ReadFileContent / WriteFileContent');
  TmpPath := GetTempDir + 'test_real_pascal_io.tmp';

  // Test 1: Basic write and read
  WriteFileContent(TmpPath, 'Hello World');
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('basic write/read', 'Hello World', ReadBack);

  // Test 2: Empty content
  WriteFileContent(TmpPath, '');
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('empty content', '', ReadBack);

  // Test 3: JSON content
  Content := '{"id":"123","command":"test","params":"data"}';
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('JSON content roundtrip', Content, ReadBack);

  // Test 4: Special characters
  Content := 'tab'#9'here and "quotes" and \backslash\';
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('special chars', Content, ReadBack);

  // Test 5: Windows path in content
  Content := '{"path":"C:\\Users\\test\\file.txt"}';
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('Windows path content', Content, ReadBack);

  // Test 6: Long content (10KB+)
  Content := '';
  while Length(Content) < 10240 do
    Content := Content + 'ABCDEFGHIJ';
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEqualsInt('long content length', Length(Content), Length(ReadBack));
  AssertEquals('long content match', Content, ReadBack);

  // Test 7: Non-existent file
  if FileExists(TmpPath) then DeleteFile(TmpPath);
  ReadBack := ReadFileContent(TmpPath + '.nonexistent');
  AssertEquals('nonexistent file', '', ReadBack);

  // Test 8: Content with many escaped quotes
  Content := '{"val":"He said \"hello\" and then \"goodbye\""}';
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('escaped quotes content', Content, ReadBack);

  // Test 9: Full request/response cycle
  Content := '{"id":"req-42","command":"application.ping","params":"{}"}';
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('full request roundtrip', Content, ReadBack);
  // Now parse it
  AssertEquals('parsed id from roundtrip', 'req-42', ExtractJsonValue(ReadBack, 'id'));
  AssertEquals('parsed command from roundtrip', 'application.ping', ExtractJsonValue(ReadBack, 'command'));

  // Cleanup
  if FileExists(TmpPath) then DeleteFile(TmpPath);
end;

procedure TestIsWhitespaceOrColon;
begin
  Section('IsWhitespaceOrColon');
  AssertTrue('space is WS', IsWhitespaceOrColon(' abc', 1));
  AssertTrue('colon is WS', IsWhitespaceOrColon(':abc', 1));
  AssertTrue('tab is WS', IsWhitespaceOrColon(#9 + 'abc', 1));
  AssertTrue('LF is WS', IsWhitespaceOrColon(#10 + 'abc', 1));
  AssertTrue('CR is WS', IsWhitespaceOrColon(#13 + 'abc', 1));
  AssertFalse('letter not WS', IsWhitespaceOrColon('abc', 1));
  AssertFalse('digit not WS', IsWhitespaceOrColon('123', 1));
  AssertFalse('quote not WS', IsWhitespaceOrColon('"abc', 1));
  AssertFalse('brace not WS', IsWhitespaceOrColon('{abc', 1));
  AssertTrue('mid-string space', IsWhitespaceOrColon('ab cd', 3));
end;

procedure TestIsDelimiter;
begin
  Section('IsDelimiter');
  AssertTrue('comma is delim', IsDelimiter(',foo', 1));
  AssertTrue('close brace delim', IsDelimiter('}foo', 1));
  AssertTrue('close bracket delim', IsDelimiter(']foo', 1));
  AssertTrue('space is delim', IsDelimiter(' foo', 1));
  AssertTrue('tab is delim', IsDelimiter(#9 + 'foo', 1));
  AssertTrue('LF is delim', IsDelimiter(#10 + 'foo', 1));
  AssertTrue('CR is delim', IsDelimiter(#13 + 'foo', 1));
  AssertFalse('letter not delim', IsDelimiter('abc', 1));
  AssertFalse('digit not delim', IsDelimiter('9abc', 1));
  AssertFalse('open brace not delim', IsDelimiter('{abc', 1));
  // Empty string at beyond-length position: Copy returns ''
  AssertTrue('past end is delim', IsDelimiter('x', 2));
end;

procedure TestExtractJsonValue;
var
  Json, LongStr : String;
  I : Integer;
begin
  Section('ExtractJsonValue - Basic');

  // Basic string extraction
  AssertEquals('simple string', 'hello', ExtractJsonValue('{"key":"hello"}', 'key'));
  AssertEquals('empty string', '', ExtractJsonValue('{"key":""}', 'key'));
  AssertEquals('key not found', '', ExtractJsonValue('{"other":"val"}', 'key'));
  AssertEquals('empty json', '', ExtractJsonValue('', 'key'));

  // Number extraction
  AssertEquals('integer', '42', ExtractJsonValue('{"num":42}', 'num'));
  AssertEquals('negative int', '-7', ExtractJsonValue('{"num":-7}', 'num'));
  AssertEquals('float', '3.14', ExtractJsonValue('{"f":3.14}', 'f'));
  AssertEquals('null value', 'null', ExtractJsonValue('{"n":null}', 'n'));
  AssertEquals('true value', 'true', ExtractJsonValue('{"b":true}', 'b'));
  AssertEquals('false value', 'false', ExtractJsonValue('{"b":false}', 'b'));

  // Object extraction
  AssertEquals('nested object', '{"inner":"val"}', ExtractJsonValue('{"obj":{"inner":"val"}}', 'obj'));
  AssertEquals('nested object with number', '{"x":1,"y":2}', ExtractJsonValue('{"pos":{"x":1,"y":2}}', 'pos'));

  // Multiple keys
  Json := '{"a":"alpha","b":"beta","c":"gamma"}';
  AssertEquals('first key', 'alpha', ExtractJsonValue(Json, 'a'));
  AssertEquals('middle key', 'beta', ExtractJsonValue(Json, 'b'));
  AssertEquals('last key', 'gamma', ExtractJsonValue(Json, 'c'));

  Section('ExtractJsonValue - Whitespace handling');
  AssertEquals('space after colon', 'val', ExtractJsonValue('{"key": "val"}', 'key'));
  AssertEquals('tab after colon', 'val', ExtractJsonValue('{"key":'#9'"val"}', 'key'));
  AssertEquals('multiple spaces', 'val', ExtractJsonValue('{"key":   "val"}', 'key'));
  AssertEquals('newline after colon', 'val', ExtractJsonValue('{"key":'#10'"val"}', 'key'));
  AssertEquals('CR+LF after colon', 'val', ExtractJsonValue('{"key":'#13#10'"val"}', 'key'));

  Section('ExtractJsonValue - Escaped quotes (backslash counting)');
  // Single escaped quote inside string
  AssertEquals('escaped quote', 'say \"hi\"', ExtractJsonValue('{"key":"say \"hi\""}', 'key'));
  // Test backslash counting with carefully constructed strings.
  // Use Chr(92) for backslash and Chr(34) for double-quote to avoid ambiguity.
  // In FPC string literals, '\' is just a literal backslash (no escape processing).
  //
  // Test: 1 backslash before closing quote -> odd -> quote is escaped (not real)
  // JSON: {"key":"path\"} -- parser sees \", skips it, runs to end
  Json := '{"key":"path' + Chr(92) + '"}';
  // The string is: {"key":"path\"}
  // Parser opens at 'p', sees \" (1 backslash, odd) -> skip, then hits end of string
  // Value = path\"} (everything after opening quote to end since no closing quote found)
  AssertEquals('1 backslash before quote (odd=escaped)', 'path' + Chr(92) + '"}', ExtractJsonValue(Json, 'key'));

  // Test: 2 backslashes before closing quote -> even -> quote IS real
  // JSON: {"key":"path\\"}
  Json := '{"key":"path' + Chr(92) + Chr(92) + '"}';
  // Parser: opens at 'p', sees \\ then ", 2 backslashes (even) -> quote is real
  // Value = path\\
  AssertEquals('2 backslashes before quote (even=real)', 'path' + Chr(92) + Chr(92), ExtractJsonValue(Json, 'key'));

  // Test: backslashes in middle of value (not before quote)
  Json := '{"key":"ab' + Chr(92) + Chr(92) + 'cd"}';
  // JSON: {"key":"ab\\cd"}
  AssertEquals('backslashes in middle of value', 'ab' + Chr(92) + Chr(92) + 'cd', ExtractJsonValue(Json, 'key'));

  Section('ExtractJsonValue - Windows paths');
  AssertEquals('win path', 'C:\\Users\\test\\file.txt',
    ExtractJsonValue('{"path":"C:\\Users\\test\\file.txt"}', 'path'));
  AssertEquals('win path nested', 'C:\\Projects\\Altium',
    ExtractJsonValue('{"dir":"C:\\Projects\\Altium","other":"x"}', 'dir'));

  Section('ExtractJsonValue - Key prefix/suffix collisions');
  Json := '{"name":"alice","username":"bob","name_full":"charlie"}';
  AssertEquals('exact key match', 'alice', ExtractJsonValue(Json, 'name'));
  AssertEquals('longer key', 'bob', ExtractJsonValue(Json, 'username'));
  AssertEquals('key with underscore', 'charlie', ExtractJsonValue(Json, 'name_full'));

  Section('ExtractJsonValue - Deeply nested objects');
  Json := '{"l1":{"l2":{"l3":{"l4":{"l5":{"l6":{"l7":{"l8":{"l9":{"l10":"deep"}}}}}}}}}}';
  AssertEquals('10-level nested', '{"l2":{"l3":{"l4":{"l5":{"l6":{"l7":{"l8":{"l9":{"l10":"deep"}}}}}}}}}',
    ExtractJsonValue(Json, 'l1'));

  Section('ExtractJsonValue - Long strings');
  LongStr := '';
  for I := 1 to 1024 do
    LongStr := LongStr + 'ABCDEFGHIJ';  // 10240 chars
  Json := '{"big":"' + LongStr + '"}';
  AssertEquals('10KB string', LongStr, ExtractJsonValue(Json, 'big'));

  Section('ExtractJsonValue - Malformed JSON');
  // Missing closing quote -- runs to end of string including trailing chars
  AssertEquals('missing close quote returns to end', 'hello}',
    ExtractJsonValue('{"key":"hello}', 'key'));
  // Extra commas -- number extraction stops at comma
  AssertEquals('value before extra comma', '42', ExtractJsonValue('{"key":42,,}', 'key'));
  // No value after colon
  AssertEquals('no value', '', ExtractJsonValue('{"key":}', 'key'));

  Section('ExtractJsonValue - Real request parsing');
  Json := '{"id":"req-001","command":"generic.query_objects","params":"{\"scope\":\"active_doc\",\"object_type\":\"eNetLabel\",\"properties\":\"Text,Location.X,Location.Y\"}"}';
  AssertEquals('request id', 'req-001', ExtractJsonValue(Json, 'id'));
  AssertEquals('request command', 'generic.query_objects', ExtractJsonValue(Json, 'command'));

  Section('ExtractJsonValue - Boolean and null neighbors');
  Json := '{"a":true,"b":false,"c":null,"d":"end"}';
  AssertEquals('true before comma', 'true', ExtractJsonValue(Json, 'a'));
  AssertEquals('false before comma', 'false', ExtractJsonValue(Json, 'b'));
  AssertEquals('null before comma', 'null', ExtractJsonValue(Json, 'c'));
  AssertEquals('string at end', 'end', ExtractJsonValue(Json, 'd'));

  Section('ExtractJsonValue - Number at end of object');
  AssertEquals('number at end', '99', ExtractJsonValue('{"x":99}', 'x'));
  AssertEquals('negative at end', '-1', ExtractJsonValue('{"x":-1}', 'x'));
end;

procedure TestExtractJsonArray;
var
  Json : String;
begin
  Section('ExtractJsonArray');

  AssertEquals('simple array', '[1,2,3]', ExtractJsonArray('{"arr":[1,2,3]}', 'arr'));
  AssertEquals('string array', '["a","b"]', ExtractJsonArray('{"arr":["a","b"]}', 'arr'));
  AssertEquals('empty array', '[]', ExtractJsonArray('{"arr":[]}', 'arr'));
  AssertEquals('nested arrays', '[[1,2],[3,4]]', ExtractJsonArray('{"arr":[[1,2],[3,4]]}', 'arr'));
  AssertEquals('key not found', '', ExtractJsonArray('{"other":[1]}', 'arr'));
  AssertEquals('not an array', '', ExtractJsonArray('{"key":"val"}', 'key'));

  // Array with whitespace
  AssertEquals('array with spaces', '[1, 2, 3]',
    ExtractJsonArray('{"arr": [1, 2, 3]}', 'arr'));

  // Array of objects
  Json := '{"items":[{"name":"a"},{"name":"b"}]}';
  AssertEquals('array of objects', '[{"name":"a"},{"name":"b"}]', ExtractJsonArray(Json, 'items'));

  // Deeply nested brackets
  Json := '{"data":[[[1]],[[2]]]}';
  AssertEquals('3-level nested', '[[[1]],[[2]]]', ExtractJsonArray(Json, 'data'));
end;

procedure TestBuildSuccessResponse;
var
  Resp : String;
begin
  Section('BuildSuccessResponse');

  // Basic
  Resp := BuildSuccessResponse('id-1', '"hello"');
  AssertEquals('success basic', '{"id":"id-1","success":true,"data":"hello","error":null}', Resp);

  // Null data
  Resp := BuildSuccessResponse('id-2', '');
  AssertEquals('success null data', '{"id":"id-2","success":true,"data":null,"error":null}', Resp);

  // Object data
  Resp := BuildSuccessResponse('id-3', '{"count":5}');
  AssertEquals('success object data', '{"id":"id-3","success":true,"data":{"count":5},"error":null}', Resp);

  // Array data
  Resp := BuildSuccessResponse('id-4', '[1,2,3]');
  AssertEquals('success array data', '{"id":"id-4","success":true,"data":[1,2,3],"error":null}', Resp);

  // Verify round-trip: parse the response back
  Resp := BuildSuccessResponse('test-42', '{"msg":"ok"}');
  AssertEquals('roundtrip id', 'test-42', ExtractJsonValue(Resp, 'id'));
  AssertEquals('roundtrip success', 'true', ExtractJsonValue(Resp, 'success'));
  AssertEquals('roundtrip data', '{"msg":"ok"}', ExtractJsonValue(Resp, 'data'));
  AssertEquals('roundtrip error', 'null', ExtractJsonValue(Resp, 'error'));
end;

procedure TestBuildErrorResponse;
var
  Resp : String;
begin
  Section('BuildErrorResponse');

  // Basic error
  Resp := BuildErrorResponse('id-1', 'ERR_TEST', 'Something went wrong');
  AssertEquals('error basic', '{"id":"id-1","success":false,"data":null,"error":{"code":"ERR_TEST","message":"Something went wrong"}}', Resp);

  // Error with special chars (backslashes and quotes)
  Resp := BuildErrorResponse('id-2', 'PATH_ERR', 'File not found: C:\test\file');
  AssertTrue('error with backslash contains escaped', Pos('C:\\test\\file', Resp) > 0);

  // Error with quotes
  Resp := BuildErrorResponse('id-3', 'QUOTE_ERR', 'Value "bad" is invalid');
  AssertTrue('error with quotes', Pos('\"bad\"', Resp) > 0);

  // Error with newlines
  Resp := BuildErrorResponse('id-4', 'NL_ERR', 'Line1'#13#10'Line2');
  AssertTrue('error with newlines', Pos('\r\n', Resp) > 0);

  // Error with tab
  Resp := BuildErrorResponse('id-5', 'TAB_ERR', 'col1'#9'col2');
  AssertTrue('error with tab', Pos('\t', Resp) > 0);

  // Verify round-trip
  Resp := BuildErrorResponse('err-99', 'INTERNAL', 'oops');
  AssertEquals('error roundtrip id', 'err-99', ExtractJsonValue(Resp, 'id'));
  AssertEquals('error roundtrip success', 'false', ExtractJsonValue(Resp, 'success'));
  AssertEquals('error roundtrip code', 'INTERNAL', ExtractJsonValue(ExtractJsonValue(Resp, 'error'), 'code'));
  AssertEquals('error roundtrip msg', 'oops', ExtractJsonValue(ExtractJsonValue(Resp, 'error'), 'message'));
end;

procedure TestEscapeJsonString;
begin
  Section('EscapeJsonString');

  AssertEquals('no escaping needed', 'hello', EscapeJsonString('hello'));
  AssertEquals('empty string', '', EscapeJsonString(''));
  AssertEquals('backslash', '\\', EscapeJsonString('\'));
  AssertEquals('double backslash', '\\\\', EscapeJsonString('\\'));
  AssertEquals('quote', '\"', EscapeJsonString('"'));
  AssertEquals('CR', '\r', EscapeJsonString(#13));
  AssertEquals('LF', '\n', EscapeJsonString(#10));
  AssertEquals('tab', '\t', EscapeJsonString(#9));
  AssertEquals('CRLF', '\r\n', EscapeJsonString(#13#10));
  AssertEquals('mixed', 'a\\b\"c\r\nd\te', EscapeJsonString('a\b"c'#13#10'd'#9'e'));

  // Windows path
  AssertEquals('windows path', 'C:\\Users\\test\\file.txt', EscapeJsonString('C:\Users\test\file.txt'));

  // Order matters: backslash must be escaped first
  AssertEquals('backslash then quote', '\\\"', EscapeJsonString('\"'));
end;

procedure TestMilsToCoord_CoordToMils;
var
  Coord, Mils : Integer;
begin
  Section('MilsToCoord / CoordToMils');

  // Basic conversions
  AssertEqualsInt('0 mils', 0, MilsToCoord(0));
  AssertEqualsInt('1 mil', 10000, MilsToCoord(1));
  AssertEqualsInt('100 mils', 1000000, MilsToCoord(100));
  AssertEqualsInt('-1 mil', -10000, MilsToCoord(-1));
  AssertEqualsInt('1000 mils', 10000000, MilsToCoord(1000));

  // Round-trip
  AssertEqualsInt('roundtrip 0', 0, CoordToMils(MilsToCoord(0)));
  AssertEqualsInt('roundtrip 1', 1, CoordToMils(MilsToCoord(1)));
  AssertEqualsInt('roundtrip 100', 100, CoordToMils(MilsToCoord(100)));
  AssertEqualsInt('roundtrip -50', -50, CoordToMils(MilsToCoord(-50)));
  AssertEqualsInt('roundtrip 10000', 10000, CoordToMils(MilsToCoord(10000)));

  // Reverse round-trip
  AssertEqualsInt('reverse roundtrip 10000', 10000, MilsToCoord(CoordToMils(10000)));
  AssertEqualsInt('reverse roundtrip 0', 0, MilsToCoord(CoordToMils(0)));

  // Large values within Integer range
  Mils := 200000; // 200 inches
  Coord := MilsToCoord(Mils);
  AssertEqualsInt('large mils', 2000000000, Coord);
  AssertEqualsInt('large roundtrip', 200000, CoordToMils(Coord));

  // CoordToMils of non-aligned values (rounding)
  AssertEqualsInt('round 4999', 0, CoordToMils(4999));
  // FPC Round uses banker's rounding: Round(0.5) = 0 (rounds to even)
  AssertEqualsInt('round 5000 bankers', 0, CoordToMils(5000));
  AssertEqualsInt('round 15000', 2, CoordToMils(15000));
  AssertEqualsInt('round -5000', 0, CoordToMils(-5000));
  AssertEqualsInt('round -5001', -1, CoordToMils(-5001));
end;

procedure TestMMToCoord_CoordToMM;
begin
  Section('MMToCoord / CoordToMM');

  // 1mm = 1000000/25.4 * 254 = ~393701 internal units
  AssertEqualsFloat('0mm', 0.0, CoordToMM(MMToCoord(0.0)), 0.001);
  AssertEqualsFloat('1mm roundtrip', 1.0, CoordToMM(MMToCoord(1.0)), 0.001);
  AssertEqualsFloat('25.4mm roundtrip', 25.4, CoordToMM(MMToCoord(25.4)), 0.001);
  AssertEqualsFloat('2.54mm roundtrip', 2.54, CoordToMM(MMToCoord(2.54)), 0.001);
  AssertEqualsFloat('0.1mm roundtrip', 0.1, CoordToMM(MMToCoord(0.1)), 0.001);

  // 25.4mm = 1 inch = 1000 mils = 10000000 internal units
  AssertEqualsInt('1 inch in coords', 10000000, MMToCoord(25.4));
  AssertEqualsFloat('coords to mm', 25.4, CoordToMM(10000000), 0.001);
end;

procedure TestBoolToJsonStr;
begin
  Section('BoolToJsonStr');
  AssertEquals('true', 'true', BoolToJsonStr(True));
  AssertEquals('false', 'false', BoolToJsonStr(False));
end;

procedure TestStrToBoolDS;
begin
  Section('StrToBoolDS');

  AssertTrue('true', StrToBoolDS('true'));
  AssertTrue('True', StrToBoolDS('True'));
  AssertTrue('TRUE', StrToBoolDS('TRUE'));
  AssertTrue('1', StrToBoolDS('1'));
  AssertFalse('false', StrToBoolDS('false'));
  AssertFalse('0', StrToBoolDS('0'));
  AssertFalse('empty', StrToBoolDS(''));
  AssertFalse('random', StrToBoolDS('yes'));
  AssertFalse('2', StrToBoolDS('2'));
end;

procedure TestStrToBoolDS_Roundtrip;
begin
  Section('BoolToJsonStr / StrToBoolDS roundtrip');
  AssertTrue('true roundtrip', StrToBoolDS(BoolToJsonStr(True)));
  AssertFalse('false roundtrip', StrToBoolDS(BoolToJsonStr(False)));
end;

procedure TestStrToIntDefDS;
begin
  Section('StrToIntDefDS');

  AssertEqualsInt('valid int', 42, StrToIntDefDS('42', 0));
  AssertEqualsInt('negative int', -7, StrToIntDefDS('-7', 0));
  AssertEqualsInt('zero', 0, StrToIntDefDS('0', 99));
  AssertEqualsInt('empty string', 99, StrToIntDefDS('', 99));
  AssertEqualsInt('null string', 55, StrToIntDefDS('null', 55));
  AssertEqualsInt('invalid string', 10, StrToIntDefDS('abc', 10));
  AssertEqualsInt('float string', 77, StrToIntDefDS('3.14', 77));
  // FPC StrToInt handles leading whitespace, so ' 5' parses to 5
  AssertEqualsInt('whitespace accepted', 5, StrToIntDefDS(' 5', 88));
  AssertEqualsInt('large number', 2000000000, StrToIntDefDS('2000000000', 0));
  AssertEqualsInt('max default', -1, StrToIntDefDS('xyz', -1));
end;

procedure TestStrToFloatDefDS;
begin
  Section('StrToFloatDefDS');

  AssertEqualsFloat('valid float', 3.14, StrToFloatDefDS('3.14', 0.0), 0.001);
  AssertEqualsFloat('integer as float', 42.0, StrToFloatDefDS('42', 0.0), 0.001);
  AssertEqualsFloat('negative float', -2.5, StrToFloatDefDS('-2.5', 0.0), 0.001);
  AssertEqualsFloat('zero', 0.0, StrToFloatDefDS('0', 99.0), 0.001);
  AssertEqualsFloat('empty string', 99.0, StrToFloatDefDS('', 99.0), 0.001);
  AssertEqualsFloat('null string', 55.0, StrToFloatDefDS('null', 55.0), 0.001);
  AssertEqualsFloat('invalid string', 10.0, StrToFloatDefDS('abc', 10.0), 0.001);
  AssertEqualsFloat('very small', 0.001, StrToFloatDefDS('0.001', 0.0), 0.0001);
end;

procedure TestGetLayerFromString;
begin
  Section('GetLayerFromString');

  AssertEqualsInt('TopLayer', eTopLayer, GetLayerFromString('TopLayer'));
  AssertEqualsInt('BottomLayer', eBottomLayer, GetLayerFromString('BottomLayer'));
  AssertEqualsInt('TopOverlay', eTopOverlay, GetLayerFromString('TopOverlay'));
  AssertEqualsInt('BottomOverlay', eBottomOverlay, GetLayerFromString('BottomOverlay'));
  AssertEqualsInt('TopPaste', eTopPaste, GetLayerFromString('TopPaste'));
  AssertEqualsInt('BottomPaste', eBottomPaste, GetLayerFromString('BottomPaste'));
  AssertEqualsInt('TopSolder', eTopSolder, GetLayerFromString('TopSolder'));
  AssertEqualsInt('BottomSolder', eBottomSolder, GetLayerFromString('BottomSolder'));
  AssertEqualsInt('MidLayer1', eMidLayer1, GetLayerFromString('MidLayer1'));
  AssertEqualsInt('MidLayer15', eMidLayer15, GetLayerFromString('MidLayer15'));
  AssertEqualsInt('MidLayer30', eMidLayer30, GetLayerFromString('MidLayer30'));
  AssertEqualsInt('InternalPlane1', eInternalPlane1, GetLayerFromString('InternalPlane1'));
  AssertEqualsInt('InternalPlane16', eInternalPlane16, GetLayerFromString('InternalPlane16'));
  AssertEqualsInt('DrillGuide', eDrillGuide, GetLayerFromString('DrillGuide'));
  AssertEqualsInt('DrillDrawing', eDrillDrawing, GetLayerFromString('DrillDrawing'));
  AssertEqualsInt('MultiLayer', eMultiLayer, GetLayerFromString('MultiLayer'));
  AssertEqualsInt('Mechanical1', eMechanical1, GetLayerFromString('Mechanical1'));
  AssertEqualsInt('Mechanical16', eMechanical16, GetLayerFromString('Mechanical16'));
  AssertEqualsInt('KeepOutLayer', eKeepOutLayer, GetLayerFromString('KeepOutLayer'));

  // Default for unknown
  AssertEqualsInt('unknown defaults to TopLayer', eTopLayer, GetLayerFromString('Bogus'));
  AssertEqualsInt('empty defaults to TopLayer', eTopLayer, GetLayerFromString(''));
  AssertEqualsInt('case mismatch defaults', eTopLayer, GetLayerFromString('toplayer'));
end;

procedure TestGetLayerString;
begin
  Section('GetLayerString');

  AssertEquals('TopLayer', 'TopLayer', GetLayerString(eTopLayer));
  AssertEquals('BottomLayer', 'BottomLayer', GetLayerString(eBottomLayer));
  AssertEquals('TopOverlay', 'TopOverlay', GetLayerString(eTopOverlay));
  AssertEquals('MidLayer1', 'MidLayer1', GetLayerString(eMidLayer1));
  AssertEquals('MidLayer30', 'MidLayer30', GetLayerString(eMidLayer30));
  AssertEquals('InternalPlane1', 'InternalPlane1', GetLayerString(eInternalPlane1));
  AssertEquals('Mechanical1', 'Mechanical1', GetLayerString(eMechanical1));
  AssertEquals('KeepOutLayer', 'KeepOutLayer', GetLayerString(eKeepOutLayer));
  AssertEquals('MultiLayer', 'MultiLayer', GetLayerString(eMultiLayer));
  AssertEquals('Unknown for bad ID', 'Unknown', GetLayerString(9999));
end;

procedure TestGetLayerRoundTrip;
var
  Names : array[0..9] of String;
  I : Integer;
begin
  Section('GetLayerFromString / GetLayerString round-trip');
  Names[0] := 'TopLayer';
  Names[1] := 'BottomLayer';
  Names[2] := 'MidLayer1';
  Names[3] := 'MidLayer30';
  Names[4] := 'TopOverlay';
  Names[5] := 'InternalPlane1';
  Names[6] := 'Mechanical1';
  Names[7] := 'KeepOutLayer';
  Names[8] := 'MultiLayer';
  Names[9] := 'DrillGuide';

  for I := 0 to 9 do
    AssertEquals('roundtrip ' + Names[I], Names[I],
      GetLayerString(GetLayerFromString(Names[I])));
end;

procedure TestObjectTypeFromString;
begin
  Section('ObjectTypeFromString (schematic)');

  AssertEqualsInt('eNetLabel', eNetLabel, ObjectTypeFromString('eNetLabel'));
  AssertEqualsInt('ePort', ePort, ObjectTypeFromString('ePort'));
  AssertEqualsInt('ePowerObject', ePowerObject, ObjectTypeFromString('ePowerObject'));
  AssertEqualsInt('eSchComponent', eSchComponent, ObjectTypeFromString('eSchComponent'));
  AssertEqualsInt('eWire', eWire, ObjectTypeFromString('eWire'));
  AssertEqualsInt('eBus', eBus, ObjectTypeFromString('eBus'));
  AssertEqualsInt('eBusEntry', eBusEntry, ObjectTypeFromString('eBusEntry'));
  AssertEqualsInt('eParameter', eParameter, ObjectTypeFromString('eParameter'));
  AssertEqualsInt('ePin', ePin, ObjectTypeFromString('ePin'));
  AssertEqualsInt('eLabel', eLabel, ObjectTypeFromString('eLabel'));
  AssertEqualsInt('eLine', eLine, ObjectTypeFromString('eLine'));
  AssertEqualsInt('eRectangle', eRectangle, ObjectTypeFromString('eRectangle'));
  AssertEqualsInt('eSheetSymbol', eSheetSymbol, ObjectTypeFromString('eSheetSymbol'));
  AssertEqualsInt('eSheetEntry', eSheetEntry, ObjectTypeFromString('eSheetEntry'));
  AssertEqualsInt('eNoERC', eNoERC, ObjectTypeFromString('eNoERC'));
  AssertEqualsInt('eJunction', eJunction, ObjectTypeFromString('eJunction'));
  AssertEqualsInt('eImage', eImage, ObjectTypeFromString('eImage'));
  AssertEqualsInt('unknown returns -1', -1, ObjectTypeFromString('Bogus'));
  AssertEqualsInt('empty returns -1', -1, ObjectTypeFromString(''));
end;

procedure TestObjectTypeFromStringPCB;
begin
  Section('ObjectTypeFromStringPCB');

  AssertEqualsInt('eTrackObject', eTrackObject, ObjectTypeFromStringPCB('eTrackObject'));
  AssertEqualsInt('ePadObject', ePadObject, ObjectTypeFromStringPCB('ePadObject'));
  AssertEqualsInt('eViaObject', eViaObject, ObjectTypeFromStringPCB('eViaObject'));
  AssertEqualsInt('eComponentObject', eComponentObject, ObjectTypeFromStringPCB('eComponentObject'));
  AssertEqualsInt('eArcObject', eArcObject, ObjectTypeFromStringPCB('eArcObject'));
  AssertEqualsInt('eFillObject', eFillObject, ObjectTypeFromStringPCB('eFillObject'));
  AssertEqualsInt('eTextObject', eTextObject, ObjectTypeFromStringPCB('eTextObject'));
  AssertEqualsInt('ePolyObject', ePolyObject, ObjectTypeFromStringPCB('ePolyObject'));
  AssertEqualsInt('eRegionObject', eRegionObject, ObjectTypeFromStringPCB('eRegionObject'));
  AssertEqualsInt('eRuleObject', eRuleObject, ObjectTypeFromStringPCB('eRuleObject'));
  AssertEqualsInt('eDimensionObject', eDimensionObject, ObjectTypeFromStringPCB('eDimensionObject'));
  AssertEqualsInt('unknown returns -1', -1, ObjectTypeFromStringPCB('Bogus'));
end;

procedure TestMockGetSchProperty;
var
  Obj : TMockSchObject;
begin
  Section('MockGetSchProperty');
  InitMockObject(Obj);
  Obj.ObjectId := 42;
  Obj.Location.X := MilsToCoord(100);
  Obj.Location.Y := MilsToCoord(200);
  Obj.Corner.X := MilsToCoord(300);
  Obj.Corner.Y := MilsToCoord(400);
  Obj.Text := 'GND';
  Obj.Name := 'NetLabel1';
  Obj.LibReference := 'RES_0603';
  Obj.SourceLibraryName := 'MyLib.SchLib';
  Obj.ComponentDescription := '10k Resistor';
  Obj.DesignatorStr := 'R1';
  Obj.UniqueId := 'ABCDE';
  Obj.Orientation := 90;
  Obj.FontId := 3;
  Obj.LineWidth := 2;
  Obj.Style := 1;
  Obj.IOType := 4;
  Obj.Alignment := 1;
  Obj.Electrical := 5;
  Obj.Color := 255;
  Obj.AreaColor := 65535;
  Obj.TextColor := 128;
  Obj.Justification := 2;
  Obj.Width := MilsToCoord(10);
  Obj.PinLength := MilsToCoord(30);
  Obj.XSize := MilsToCoord(50);
  Obj.YSize := MilsToCoord(60);
  Obj.IsHidden := True;
  Obj.IsSolid := False;
  Obj.IsMirrored := True;
  Obj.DesignatorSub.Text := 'R1_sub';
  Obj.CommentSub.Text := '10k';
  Obj.SheetNameSub.Text := 'Sheet1';

  AssertEquals('ObjectId', '42', MockGetSchProperty(Obj, 'ObjectId'));
  AssertEquals('Location.X', '100', MockGetSchProperty(Obj, 'Location.X'));
  AssertEquals('Location.Y', '200', MockGetSchProperty(Obj, 'Location.Y'));
  AssertEquals('Corner.X', '300', MockGetSchProperty(Obj, 'Corner.X'));
  AssertEquals('Corner.Y', '400', MockGetSchProperty(Obj, 'Corner.Y'));
  AssertEquals('Text', 'GND', MockGetSchProperty(Obj, 'Text'));
  AssertEquals('Name', 'NetLabel1', MockGetSchProperty(Obj, 'Name'));
  AssertEquals('LibReference', 'RES_0603', MockGetSchProperty(Obj, 'LibReference'));
  AssertEquals('SourceLibraryName', 'MyLib.SchLib', MockGetSchProperty(Obj, 'SourceLibraryName'));
  AssertEquals('ComponentDescription', '10k Resistor', MockGetSchProperty(Obj, 'ComponentDescription'));
  AssertEquals('Designator', 'R1', MockGetSchProperty(Obj, 'Designator'));
  AssertEquals('UniqueId', 'ABCDE', MockGetSchProperty(Obj, 'UniqueId'));
  AssertEquals('Designator.Text', 'R1_sub', MockGetSchProperty(Obj, 'Designator.Text'));
  AssertEquals('Comment.Text', '10k', MockGetSchProperty(Obj, 'Comment.Text'));
  AssertEquals('SheetName.Text', 'Sheet1', MockGetSchProperty(Obj, 'SheetName.Text'));
  AssertEquals('Orientation', '90', MockGetSchProperty(Obj, 'Orientation'));
  AssertEquals('FontId', '3', MockGetSchProperty(Obj, 'FontId'));
  AssertEquals('LineWidth', '2', MockGetSchProperty(Obj, 'LineWidth'));
  AssertEquals('Style', '1', MockGetSchProperty(Obj, 'Style'));
  AssertEquals('IOType', '4', MockGetSchProperty(Obj, 'IOType'));
  AssertEquals('Alignment', '1', MockGetSchProperty(Obj, 'Alignment'));
  AssertEquals('Electrical', '5', MockGetSchProperty(Obj, 'Electrical'));
  AssertEquals('Color', '255', MockGetSchProperty(Obj, 'Color'));
  AssertEquals('AreaColor', '65535', MockGetSchProperty(Obj, 'AreaColor'));
  AssertEquals('TextColor', '128', MockGetSchProperty(Obj, 'TextColor'));
  AssertEquals('Justification', '2', MockGetSchProperty(Obj, 'Justification'));
  AssertEquals('Width', '10', MockGetSchProperty(Obj, 'Width'));
  AssertEquals('PinLength', '30', MockGetSchProperty(Obj, 'PinLength'));
  AssertEquals('XSize', '50', MockGetSchProperty(Obj, 'XSize'));
  AssertEquals('YSize', '60', MockGetSchProperty(Obj, 'YSize'));
  AssertEquals('IsHidden', 'true', MockGetSchProperty(Obj, 'IsHidden'));
  AssertEquals('IsSolid', 'false', MockGetSchProperty(Obj, 'IsSolid'));
  AssertEquals('IsMirrored', 'true', MockGetSchProperty(Obj, 'IsMirrored'));

  // Unknown property returns empty
  AssertEquals('unknown prop', '', MockGetSchProperty(Obj, 'NonExistent'));
end;

procedure TestMockSetSchProperty;
var
  Obj : TMockSchObject;
begin
  Section('MockSetSchProperty');
  InitMockObject(Obj);

  MockSetSchProperty(Obj, 'Location.X', '150');
  AssertEqualsInt('set Location.X', MilsToCoord(150), Obj.Location.X);

  MockSetSchProperty(Obj, 'Location.Y', '250');
  AssertEqualsInt('set Location.Y', MilsToCoord(250), Obj.Location.Y);

  MockSetSchProperty(Obj, 'Text', 'VCC');
  AssertEquals('set Text', 'VCC', Obj.Text);

  MockSetSchProperty(Obj, 'Name', 'MyLabel');
  AssertEquals('set Name', 'MyLabel', Obj.Name);

  MockSetSchProperty(Obj, 'Orientation', '270');
  AssertEqualsInt('set Orientation', 270, Obj.Orientation);

  MockSetSchProperty(Obj, 'IsHidden', 'true');
  AssertTrue('set IsHidden true', Obj.IsHidden);

  MockSetSchProperty(Obj, 'IsHidden', 'false');
  AssertFalse('set IsHidden false', Obj.IsHidden);

  MockSetSchProperty(Obj, 'Width', '25');
  AssertEqualsInt('set Width', MilsToCoord(25), Obj.Width);

  MockSetSchProperty(Obj, 'Color', '16711680');
  AssertEqualsInt('set Color', 16711680, Obj.Color);

  // Set and read back via getter
  MockSetSchProperty(Obj, 'FontId', '5');
  AssertEquals('set/get FontId', '5', MockGetSchProperty(Obj, 'FontId'));

  MockSetSchProperty(Obj, 'Designator.Text', 'U1');
  AssertEquals('set/get Designator.Text', 'U1', MockGetSchProperty(Obj, 'Designator.Text'));
end;

procedure TestMockMatchesFilter;
var
  Obj : TMockSchObject;
begin
  Section('MockMatchesFilter');
  InitMockObject(Obj);
  Obj.Text := 'GND';
  Obj.Name := 'NetLabel1';
  Obj.Location.X := MilsToCoord(100);
  Obj.Location.Y := MilsToCoord(200);
  Obj.Orientation := 0;

  // Empty filter matches everything
  AssertTrue('empty filter', MockMatchesFilter(Obj, ''));

  // Single condition match
  AssertTrue('single match', MockMatchesFilter(Obj, 'Text=GND'));
  AssertFalse('single no match', MockMatchesFilter(Obj, 'Text=VCC'));

  // Multi-condition (AND logic)
  AssertTrue('AND match', MockMatchesFilter(Obj, 'Text=GND|Name=NetLabel1'));
  AssertFalse('AND partial mismatch', MockMatchesFilter(Obj, 'Text=GND|Name=Wrong'));

  // Numeric property filter
  AssertTrue('coord filter', MockMatchesFilter(Obj, 'Location.X=100'));
  AssertFalse('coord filter mismatch', MockMatchesFilter(Obj, 'Location.X=999'));

  // Three conditions
  AssertTrue('three conditions', MockMatchesFilter(Obj, 'Text=GND|Location.X=100|Location.Y=200'));
  AssertFalse('three conditions one wrong', MockMatchesFilter(Obj, 'Text=GND|Location.X=100|Location.Y=999'));

  // Condition without = is skipped (Continue)
  AssertTrue('no equals sign skipped', MockMatchesFilter(Obj, 'Text=GND|nosep|Name=NetLabel1'));

  // Integer filter
  AssertTrue('int filter', MockMatchesFilter(Obj, 'Orientation=0'));
  AssertFalse('int filter mismatch', MockMatchesFilter(Obj, 'Orientation=90'));
end;

procedure TestMockBuildObjectJson;
var
  Obj : TMockSchObject;
  Json : String;
begin
  Section('MockBuildObjectJson');
  InitMockObject(Obj);
  Obj.Text := 'GND';
  Obj.Name := 'NL1';
  Obj.Location.X := MilsToCoord(100);
  Obj.Location.Y := MilsToCoord(200);

  // Single property
  Json := MockBuildObjectJson(Obj, 'Text');
  AssertEquals('single prop', '{"Text":"GND"}', Json);

  // Multiple properties
  Json := MockBuildObjectJson(Obj, 'Text,Name');
  AssertEquals('two props', '{"Text":"GND","Name":"NL1"}', Json);

  // Coordinate properties
  Json := MockBuildObjectJson(Obj, 'Location.X,Location.Y');
  AssertEquals('coord props', '{"Location.X":"100","Location.Y":"200"}', Json);

  // Empty props string
  Json := MockBuildObjectJson(Obj, '');
  AssertEquals('empty props', '{}', Json);

  // Property with special chars in value
  Obj.Text := 'Line "A"';
  Json := MockBuildObjectJson(Obj, 'Text');
  AssertEquals('escaped value', '{"Text":"Line \"A\""}', Json);

  // Verify JSON is parseable back
  Obj.Text := 'TestVal';
  Obj.Name := 'TestName';
  Json := MockBuildObjectJson(Obj, 'Text,Name,Location.X');
  AssertEquals('parse back Text', 'TestVal', ExtractJsonValue(Json, 'Text'));
  AssertEquals('parse back Name', 'TestName', ExtractJsonValue(Json, 'Name'));
  AssertEquals('parse back Location.X', '100', ExtractJsonValue(Json, 'Location.X'));
end;

procedure TestMockApplySetProperties;
var
  Obj : TMockSchObject;
begin
  Section('MockApplySetProperties');
  InitMockObject(Obj);

  // Single assignment
  MockApplySetProperties(Obj, 'Text=Hello');
  AssertEquals('single set', 'Hello', Obj.Text);

  // Multiple pipe-separated assignments
  MockApplySetProperties(Obj, 'Text=GND|Name=NL1|Orientation=90');
  AssertEquals('multi set Text', 'GND', Obj.Text);
  AssertEquals('multi set Name', 'NL1', Obj.Name);
  AssertEqualsInt('multi set Orientation', 90, Obj.Orientation);

  // Coordinate assignments
  MockApplySetProperties(Obj, 'Location.X=500|Location.Y=600');
  AssertEqualsInt('set coords X', MilsToCoord(500), Obj.Location.X);
  AssertEqualsInt('set coords Y', MilsToCoord(600), Obj.Location.Y);

  // Boolean assignment
  MockApplySetProperties(Obj, 'IsHidden=true|IsSolid=false');
  AssertTrue('set bool true', Obj.IsHidden);
  AssertFalse('set bool false', Obj.IsSolid);

  // Empty set string
  Obj.Text := 'before';
  MockApplySetProperties(Obj, '');
  AssertEquals('empty set no change', 'before', Obj.Text);

  // Assignment with no = sign is skipped
  Obj.Text := 'before';
  MockApplySetProperties(Obj, 'nosep|Text=after');
  AssertEquals('skip nosep', 'after', Obj.Text);
end;

procedure TestSplitCommand;
var
  Cat, Act : String;
begin
  Section('SplitCommand (Dispatcher routing)');

  SplitCommand('application.ping', Cat, Act);
  AssertEquals('cat: application', 'application', Cat);
  AssertEquals('act: ping', 'ping', Act);

  SplitCommand('generic.query_objects', Cat, Act);
  AssertEquals('cat: generic', 'generic', Cat);
  AssertEquals('act: query_objects', 'query_objects', Act);

  SplitCommand('project.get_documents', Cat, Act);
  AssertEquals('cat: project', 'project', Cat);
  AssertEquals('act: get_documents', 'get_documents', Act);

  SplitCommand('library.get_components', Cat, Act);
  AssertEquals('cat: library', 'library', Cat);
  AssertEquals('act: get_components', 'get_components', Act);

  // No dot
  SplitCommand('nodot', Cat, Act);
  AssertEquals('no dot cat', 'nodot', Cat);
  AssertEquals('no dot act', '', Act);

  // Empty
  SplitCommand('', Cat, Act);
  AssertEquals('empty cat', '', Cat);
  AssertEquals('empty act', '', Act);

  // Multiple dots (only first split)
  SplitCommand('a.b.c', Cat, Act);
  AssertEquals('multi dot cat', 'a', Cat);
  AssertEquals('multi dot act', 'b.c', Act);
end;

procedure TestParsePipeSeparatedParams;
var
  Keys : array[0..9] of String;
  Values : array[0..9] of String;
  Count : Integer;
begin
  Section('ParsePipeSeparatedParams');

  // Single param
  ParsePipeSeparatedParams('ObjectKind=Document', Keys, Values, Count);
  AssertEqualsInt('single count', 1, Count);
  AssertEquals('single key', 'ObjectKind', Keys[0]);
  AssertEquals('single val', 'Document', Values[0]);

  // Multiple params
  ParsePipeSeparatedParams('A=1|B=2|C=3', Keys, Values, Count);
  AssertEqualsInt('multi count', 3, Count);
  AssertEquals('multi key0', 'A', Keys[0]);
  AssertEquals('multi val0', '1', Values[0]);
  AssertEquals('multi key1', 'B', Keys[1]);
  AssertEquals('multi val1', '2', Values[1]);
  AssertEquals('multi key2', 'C', Keys[2]);
  AssertEquals('multi val2', '3', Values[2]);

  // Empty string
  ParsePipeSeparatedParams('', Keys, Values, Count);
  AssertEqualsInt('empty count', 0, Count);

  // No = sign (skipped)
  ParsePipeSeparatedParams('nosep|Key=Val', Keys, Values, Count);
  AssertEqualsInt('skip nosep count', 1, Count);
  AssertEquals('skip nosep key', 'Key', Keys[0]);
  AssertEquals('skip nosep val', 'Val', Values[0]);

  // Values with = signs (only first = splits)
  ParsePipeSeparatedParams('Expr=a=b', Keys, Values, Count);
  AssertEqualsInt('val with equals count', 1, Count);
  AssertEquals('val with equals key', 'Expr', Keys[0]);
  AssertEquals('val with equals val', 'a=b', Values[0]);

  // Value with path
  ParsePipeSeparatedParams('FileName=C:\test\file.txt|ObjectKind=Document', Keys, Values, Count);
  AssertEqualsInt('path count', 2, Count);
  AssertEquals('path val', 'C:\test\file.txt', Values[0]);
end;

procedure TestFileRoundTrip;
var
  TmpDir, ReqPath, RespPath : String;
  RequestContent, ResponseContent : String;
  ReqId, Command, Params : String;
begin
  Section('Full file-based IPC round trip');
  TmpDir := GetTempDir;
  ReqPath := TmpDir + 'test_request.json';
  RespPath := TmpDir + 'test_response.json';

  // Simulate Python writing a request
  RequestContent := '{"id":"req-123","command":"application.ping","params":"{}"}';
  WriteFileContent(ReqPath, RequestContent);

  // Simulate Altium reading the request
  RequestContent := ReadFileContent(ReqPath);
  ReqId := ExtractJsonValue(RequestContent, 'id');
  Command := ExtractJsonValue(RequestContent, 'command');
  Params := ExtractJsonValue(RequestContent, 'params');

  AssertEquals('IPC req id', 'req-123', ReqId);
  AssertEquals('IPC req command', 'application.ping', Command);
  AssertEquals('IPC req params', '{}', Params);

  // Simulate Altium writing a response
  ResponseContent := BuildSuccessResponse(ReqId, '"pong"');
  WriteFileContent(RespPath, ResponseContent);

  // Simulate Python reading the response
  ResponseContent := ReadFileContent(RespPath);
  AssertEquals('IPC resp id', 'req-123', ExtractJsonValue(ResponseContent, 'id'));
  AssertEquals('IPC resp success', 'true', ExtractJsonValue(ResponseContent, 'success'));
  // ExtractJsonValue for a string value returns the unquoted content
  AssertEquals('IPC resp data', 'pong', ExtractJsonValue(ResponseContent, 'data'));

  // Cleanup
  if FileExists(ReqPath) then DeleteFile(ReqPath);
  if FileExists(RespPath) then DeleteFile(RespPath);
end;

procedure TestFileRoundTripComplex;
var
  TmpPath : String;
  Content, ReadBack : String;
begin
  Section('File round trip with complex JSON');
  TmpPath := GetTempDir + 'test_complex.json';

  // Request with nested params containing escaped quotes
  Content := '{"id":"r-1","command":"generic.query_objects","params":"{\"scope\":\"project:C:\\\\Projects\\\\Altium\",\"object_type\":\"eNetLabel\",\"filter\":\"Text=GND\",\"properties\":\"Text,Location.X,Location.Y\"}"}';
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('complex roundtrip', Content, ReadBack);

  // Parse the read-back
  AssertEquals('complex id', 'r-1', ExtractJsonValue(ReadBack, 'id'));
  AssertEquals('complex command', 'generic.query_objects', ExtractJsonValue(ReadBack, 'command'));

  // Error response with special chars
  Content := BuildErrorResponse('e-1', 'IO_ERROR', 'Cannot open C:\test\file "name"');
  WriteFileContent(TmpPath, Content);
  ReadBack := ReadFileContent(TmpPath);
  AssertEquals('error roundtrip match', Content, ReadBack);
  AssertEquals('error roundtrip id', 'e-1', ExtractJsonValue(ReadBack, 'id'));
  AssertEquals('error roundtrip success', 'false', ExtractJsonValue(ReadBack, 'success'));

  // Cleanup
  if FileExists(TmpPath) then DeleteFile(TmpPath);
end;

procedure TestAdversarialJson;
var
  Json : String;
begin
  Section('Adversarial JSON parsing');

  // Key that appears inside a value
  Json := '{"name":"my_name_is","name_id":"42"}';
  AssertEquals('key in value: name', 'my_name_is', ExtractJsonValue(Json, 'name'));
  AssertEquals('key in value: name_id', '42', ExtractJsonValue(Json, 'name_id'));

  // Duplicate keys -- first match wins
  Json := '{"key":"first","other":"x","key":"second"}';
  AssertEquals('duplicate key returns first', 'first', ExtractJsonValue(Json, 'key'));

  // Key with special characters in value
  Json := '{"msg":"Hello\nWorld\twith\r\nlines"}';
  AssertEquals('control chars in value', 'Hello\nWorld\twith\r\nlines', ExtractJsonValue(Json, 'msg'));

  // Numeric zero
  Json := '{"count":0}';
  AssertEquals('zero value', '0', ExtractJsonValue(Json, 'count'));

  // Negative float
  Json := '{"temp":-3.5}';
  AssertEquals('negative float', '-3.5', ExtractJsonValue(Json, 'temp'));

  // Object with nested objects
  Json := '{"outer":{"mid":{"inner":"val"}}}';
  AssertEquals('nested object extraction', '{"mid":{"inner":"val"}}', ExtractJsonValue(Json, 'outer'));

  // Empty object
  Json := '{"empty":{}}';
  AssertEquals('empty object', '{}', ExtractJsonValue(Json, 'empty'));

  // String that looks like a number
  Json := '{"num_str":"42"}';
  AssertEquals('string that looks like number', '42', ExtractJsonValue(Json, 'num_str'));

  // Very long key
  Json := '{"' + StringOfChar('x', 200) + '":"found"}';
  AssertEquals('long key', 'found', ExtractJsonValue(Json, StringOfChar('x', 200)));
end;

procedure TestBackslashEdgeCases;
var
  Json : String;
begin
  Section('Backslash edge cases in ExtractJsonValue');

  // 1 backslash before quote = escaped quote (odd, quote is NOT real)
  Json := '{"key":"val\"end"}';
  AssertEquals('1 backslash: escaped quote', 'val\"end', ExtractJsonValue(Json, 'key'));

  // 2 backslashes before quote = two literal backslashes, quote IS real (even)
  Json := '{"key":"val\\\\"}';
  AssertEquals('2 backslashes: real quote', 'val\\\\', ExtractJsonValue(Json, 'key'));

  // 3 backslashes before quote = escaped backslash + escaped quote (odd, quote is NOT real)
  Json := '{"key":"val\\\\\\\"end"}';
  AssertEquals('3 backslashes: escaped quote', 'val\\\\\\\"end', ExtractJsonValue(Json, 'key'));

  // Backslash at start of value
  Json := '{"key":"\\\\start"}';
  AssertEquals('backslash at start', '\\\\start', ExtractJsonValue(Json, 'key'));

  // Just backslashes
  Json := '{"key":"\\\\"}';
  AssertEquals('just two backslashes', '\\\\', ExtractJsonValue(Json, 'key'));

  // Windows path in JSON value (already escaped by producer)
  Json := '{"path":"C:\\\\Users\\\\test\\\\file.txt"}';
  AssertEquals('windows path', 'C:\\\\Users\\\\test\\\\file.txt', ExtractJsonValue(Json, 'path'));
end;

procedure TestScopeParsingLogic;
var
  Scope, ProjectPath : String;
begin
  Section('Scope parsing logic (from Gen_QueryObjects)');

  // Copied from Generic.pas lines 495-500

  // active_doc scope (default)
  Scope := '';
  if Scope = '' then Scope := 'active_doc';
  ProjectPath := '';
  if Copy(Scope, 1, 8) = 'project:' then
  begin
    ProjectPath := Copy(Scope, 9, Length(Scope));
    ProjectPath := StringReplace(ProjectPath, '\\', '\', [rfReplaceAll]);
    Scope := 'project';
  end;
  AssertEquals('default scope', 'active_doc', Scope);
  AssertEquals('default proj path', '', ProjectPath);

  // project scope without path
  Scope := 'project';
  ProjectPath := '';
  if Copy(Scope, 1, 8) = 'project:' then
  begin
    ProjectPath := Copy(Scope, 9, Length(Scope));
    ProjectPath := StringReplace(ProjectPath, '\\', '\', [rfReplaceAll]);
    Scope := 'project';
  end;
  AssertEquals('project scope', 'project', Scope);
  AssertEquals('project no path', '', ProjectPath);

  // project scope with path
  Scope := 'project:C:\\Users\\test\\proj.PrjPCB';
  ProjectPath := '';
  if Copy(Scope, 1, 8) = 'project:' then
  begin
    ProjectPath := Copy(Scope, 9, Length(Scope));
    ProjectPath := StringReplace(ProjectPath, '\\', '\', [rfReplaceAll]);
    Scope := 'project';
  end;
  AssertEquals('project with path scope', 'project', Scope);
  AssertEquals('project with path', 'C:\Users\test\proj.PrjPCB', ProjectPath);
end;

procedure TestEndToEndQueryParse;
var
  Params, Scope, ObjTypeStr, FilterStr, PropsStr : String;
  Limit : Integer;
begin
  Section('End-to-end query parameter parsing');

  // Simulate the JSON params that Gen_QueryObjects receives
  Params := '{"scope":"active_doc","object_type":"eNetLabel","filter":"Text=GND","properties":"Text,Location.X,Location.Y","limit":"50"}';

  Scope := ExtractJsonValue(Params, 'scope');
  ObjTypeStr := ExtractJsonValue(Params, 'object_type');
  FilterStr := ExtractJsonValue(Params, 'filter');
  PropsStr := ExtractJsonValue(Params, 'properties');
  Limit := StrToIntDefDS(ExtractJsonValue(Params, 'limit'), 0);

  AssertEquals('e2e scope', 'active_doc', Scope);
  AssertEquals('e2e obj type', 'eNetLabel', ObjTypeStr);
  AssertEquals('e2e filter', 'Text=GND', FilterStr);
  AssertEquals('e2e props', 'Text,Location.X,Location.Y', PropsStr);
  AssertEqualsInt('e2e limit', 50, Limit);

  // Verify object type resolves
  AssertEqualsInt('e2e type int', eNetLabel, ObjectTypeFromString(ObjTypeStr));
end;

procedure TestEndToEndModifyParse;
var
  Params, SetStr : String;
  Obj : TMockSchObject;
begin
  Section('End-to-end modify parameter parsing');

  Params := '{"scope":"active_doc","object_type":"eNetLabel","filter":"Text=GND","set":"Text=VCC|Location.X=500"}';

  SetStr := ExtractJsonValue(Params, 'set');
  AssertEquals('e2e set str', 'Text=VCC|Location.X=500', SetStr);

  // Apply to mock object
  InitMockObject(Obj);
  Obj.Text := 'GND';
  Obj.Location.X := MilsToCoord(100);

  // First verify filter matches
  AssertTrue('e2e filter matches', MockMatchesFilter(Obj, ExtractJsonValue(Params, 'filter')));

  // Apply modifications
  MockApplySetProperties(Obj, SetStr);
  AssertEquals('e2e modified Text', 'VCC', Obj.Text);
  AssertEqualsInt('e2e modified X', MilsToCoord(500), Obj.Location.X);
end;

procedure TestEndToEndResponseConstruction;
var
  Obj : TMockSchObject;
  ObjJson, Response : String;
begin
  Section('End-to-end response construction');

  InitMockObject(Obj);
  Obj.Text := 'GND';
  Obj.Location.X := MilsToCoord(100);
  Obj.Location.Y := MilsToCoord(200);

  // Build object JSON like ProcessSchDocObjects does
  ObjJson := MockBuildObjectJson(Obj, 'Text,Location.X,Location.Y');

  // Build response like query mode does
  Response := BuildSuccessResponse('req-1',
    '{"objects":[' + ObjJson + '],"count":1}');

  // Parse it all back
  AssertEquals('e2e resp id', 'req-1', ExtractJsonValue(Response, 'id'));
  AssertEquals('e2e resp success', 'true', ExtractJsonValue(Response, 'success'));

  // Parse data
  AssertEquals('e2e count', '1', ExtractJsonValue(ExtractJsonValue(Response, 'data'), 'count'));
end;

procedure TestCommandDispatchCategories;
var
  Cat, Act : String;
  Resp : String;
begin
  Section('Command dispatch category routing');

  // Valid categories
  SplitCommand('application.ping', Cat, Act);
  AssertEquals('app cat', 'application', Cat);
  AssertEquals('app act', 'ping', Act);

  SplitCommand('application.stop_server', Cat, Act);
  AssertEquals('stop cat', 'application', Cat);
  AssertEquals('stop act', 'stop_server', Act);

  SplitCommand('project.get_documents', Cat, Act);
  AssertEquals('proj cat', 'project', Cat);

  SplitCommand('library.get_components', Cat, Act);
  AssertEquals('lib cat', 'library', Cat);

  SplitCommand('generic.query_objects', Cat, Act);
  AssertEquals('gen cat', 'generic', Cat);
  AssertEquals('gen act', 'query_objects', Act);

  SplitCommand('generic.modify_objects', Cat, Act);
  AssertEquals('gen mod act', 'modify_objects', Act);

  SplitCommand('generic.create_object', Cat, Act);
  AssertEquals('gen create act', 'create_object', Act);

  SplitCommand('generic.delete_objects', Cat, Act);
  AssertEquals('gen delete act', 'delete_objects', Act);

  SplitCommand('generic.run_process', Cat, Act);
  AssertEquals('gen run act', 'run_process', Act);

  // Unknown category produces error response
  SplitCommand('unknown.thing', Cat, Act);
  AssertEquals('unknown cat', 'unknown', Cat);
  // Simulate the error branch from ProcessCommand
  Resp := BuildErrorResponse('x', 'UNKNOWN_COMMAND', 'Unknown command category: ' + Cat + '. Use generic.* for object operations.');
  AssertTrue('unknown produces error', Pos('UNKNOWN_COMMAND', Resp) > 0);
end;

procedure TestEscapeJsonStringRoundTrip;
var
  Original, Escaped, Reparsed : String;
  Json : String;
begin
  Section('EscapeJsonString -> JSON -> ExtractJsonValue round trip');

  // Simple string
  Original := 'hello world';
  Escaped := EscapeJsonString(Original);
  Json := '{"val":"' + Escaped + '"}';
  Reparsed := ExtractJsonValue(Json, 'val');
  AssertEquals('simple roundtrip', Escaped, Reparsed);

  // String with quotes
  Original := 'say "hello"';
  Escaped := EscapeJsonString(Original);
  Json := '{"val":"' + Escaped + '"}';
  Reparsed := ExtractJsonValue(Json, 'val');
  AssertEquals('quotes roundtrip', Escaped, Reparsed);

  // String with backslash
  Original := 'path\to\file';
  Escaped := EscapeJsonString(Original);
  Json := '{"val":"' + Escaped + '"}';
  Reparsed := ExtractJsonValue(Json, 'val');
  AssertEquals('backslash roundtrip', Escaped, Reparsed);

  // String with mixed
  Original := 'C:\test\"name"';
  Escaped := EscapeJsonString(Original);
  Json := '{"val":"' + Escaped + '"}';
  Reparsed := ExtractJsonValue(Json, 'val');
  AssertEquals('mixed roundtrip', Escaped, Reparsed);
end;

procedure TestGenericActionDispatch;
var
  Cat, Act : String;
begin
  Section('Generic action names');

  // All known generic actions
  SplitCommand('generic.query_objects', Cat, Act);
  AssertEquals('query', 'query_objects', Act);

  SplitCommand('generic.modify_objects', Cat, Act);
  AssertEquals('modify', 'modify_objects', Act);

  SplitCommand('generic.create_object', Cat, Act);
  AssertEquals('create', 'create_object', Act);

  SplitCommand('generic.delete_objects', Cat, Act);
  AssertEquals('delete', 'delete_objects', Act);

  SplitCommand('generic.run_process', Cat, Act);
  AssertEquals('run_process', 'run_process', Act);

  SplitCommand('generic.get_font_spec', Cat, Act);
  AssertEquals('get_font_spec', 'get_font_spec', Act);

  SplitCommand('generic.get_font_id', Cat, Act);
  AssertEquals('get_font_id', 'get_font_id', Act);

  SplitCommand('generic.select_objects', Cat, Act);
  AssertEquals('select_objects', 'select_objects', Act);

  SplitCommand('generic.deselect_all', Cat, Act);
  AssertEquals('deselect_all', 'deselect_all', Act);

  SplitCommand('generic.zoom', Cat, Act);
  AssertEquals('zoom', 'zoom', Act);
end;

procedure TestProjectActionDispatch;
var
  Cat, Act : String;
begin
  Section('Project action names');

  SplitCommand('project.create', Cat, Act);
  AssertEquals('create', 'create', Act);
  SplitCommand('project.open', Cat, Act);
  AssertEquals('open', 'open', Act);
  SplitCommand('project.save', Cat, Act);
  AssertEquals('save', 'save', Act);
  SplitCommand('project.close', Cat, Act);
  AssertEquals('close', 'close', Act);
  SplitCommand('project.get_documents', Cat, Act);
  AssertEquals('get_documents', 'get_documents', Act);
  SplitCommand('project.add_document', Cat, Act);
  AssertEquals('add_document', 'add_document', Act);
  SplitCommand('project.remove_document', Cat, Act);
  AssertEquals('remove_document', 'remove_document', Act);
  SplitCommand('project.get_parameters', Cat, Act);
  AssertEquals('get_parameters', 'get_parameters', Act);
  SplitCommand('project.set_parameter', Cat, Act);
  AssertEquals('set_parameter', 'set_parameter', Act);
  SplitCommand('project.compile', Cat, Act);
  AssertEquals('compile', 'compile', Act);
  SplitCommand('project.get_focused', Cat, Act);
  AssertEquals('get_focused', 'get_focused', Act);
  SplitCommand('project.get_nets', Cat, Act);
  AssertEquals('get_nets', 'get_nets', Act);
  SplitCommand('project.get_bom', Cat, Act);
  AssertEquals('get_bom', 'get_bom', Act);
  SplitCommand('project.get_component_info', Cat, Act);
  AssertEquals('get_component_info', 'get_component_info', Act);
  SplitCommand('project.export_pdf', Cat, Act);
  AssertEquals('export_pdf', 'export_pdf', Act);
  SplitCommand('project.cross_probe', Cat, Act);
  AssertEquals('cross_probe', 'cross_probe', Act);
  SplitCommand('project.get_design_stats', Cat, Act);
  AssertEquals('get_design_stats', 'get_design_stats', Act);
  SplitCommand('project.get_board_info', Cat, Act);
  AssertEquals('get_board_info', 'get_board_info', Act);
  SplitCommand('project.annotate', Cat, Act);
  AssertEquals('annotate', 'annotate', Act);
  SplitCommand('project.generate_output', Cat, Act);
  AssertEquals('generate_output', 'generate_output', Act);
end;

procedure TestApplicationActionDispatch;
var
  Cat, Act : String;
begin
  Section('Application action names');

  SplitCommand('application.ping', Cat, Act);
  AssertEquals('ping', 'ping', Act);
  SplitCommand('application.get_version', Cat, Act);
  AssertEquals('get_version', 'get_version', Act);
  SplitCommand('application.get_open_documents', Cat, Act);
  AssertEquals('get_open_documents', 'get_open_documents', Act);
  SplitCommand('application.get_active_document', Cat, Act);
  AssertEquals('get_active_document', 'get_active_document', Act);
  SplitCommand('application.set_active_document', Cat, Act);
  AssertEquals('set_active_document', 'set_active_document', Act);
  SplitCommand('application.run_process', Cat, Act);
  AssertEquals('run_process', 'run_process', Act);
  SplitCommand('application.stop_server', Cat, Act);
  AssertEquals('stop_server', 'stop_server', Act);
end;

procedure TestFilterWithEmptyValues;
var
  Obj : TMockSchObject;
begin
  Section('Filter matching with empty/special values');
  InitMockObject(Obj);
  Obj.Text := '';
  Obj.Name := '';

  // Empty filter matches
  AssertTrue('empty filter always matches', MockMatchesFilter(Obj, ''));

  // Filter on empty value
  AssertTrue('filter empty text matches', MockMatchesFilter(Obj, 'Text='));
  AssertFalse('filter non-empty text fails', MockMatchesFilter(Obj, 'Text=something'));

  // Filter on property with no value in condition
  Obj.Text := 'GND';
  AssertFalse('filter empty val vs non-empty prop', MockMatchesFilter(Obj, 'Text='));
  AssertTrue('filter exact match', MockMatchesFilter(Obj, 'Text=GND'));
end;

procedure TestCoordConversionEdgeCases;
begin
  Section('Coordinate conversion edge cases');

  // Zero
  AssertEqualsInt('MilsToCoord(0)', 0, MilsToCoord(0));
  AssertEqualsInt('CoordToMils(0)', 0, CoordToMils(0));
  AssertEqualsFloat('MMToCoord(0)', 0.0, CoordToMM(MMToCoord(0.0)), 0.001);

  // 1 mil
  AssertEqualsInt('1 mil exact', 10000, MilsToCoord(1));
  AssertEqualsInt('10000 coords = 1 mil', 1, CoordToMils(10000));

  // Negative
  AssertEqualsInt('negative mil', -10000, MilsToCoord(-1));
  AssertEqualsInt('negative coord', -1, CoordToMils(-10000));

  // Rounding boundary for CoordToMils
  AssertEqualsInt('below 0.5 mil rounds down', 0, CoordToMils(4999));
  // FPC banker's rounding: Round(0.5) = 0
  AssertEqualsInt('at 0.5 mil bankers round', 0, CoordToMils(5000));
  AssertEqualsInt('1.5 mils', 2, CoordToMils(15000));
end;

procedure TestMultipleObjectJsonConcatenation;
var
  Obj1, Obj2 : TMockSchObject;
  Json1, Json2, Combined, Response : String;
  First : Boolean;
begin
  Section('Multiple object JSON concatenation (like ProcessSchDocObjects)');

  InitMockObject(Obj1);
  Obj1.Text := 'GND';
  Obj1.Location.X := MilsToCoord(100);

  InitMockObject(Obj2);
  Obj2.Text := 'VCC';
  Obj2.Location.X := MilsToCoord(200);

  Json1 := MockBuildObjectJson(Obj1, 'Text,Location.X');
  Json2 := MockBuildObjectJson(Obj2, 'Text,Location.X');

  // Simulate what ProcessSchDocObjects does
  Combined := '';
  First := True;
  if not First then Combined := Combined + ',';
  First := False;
  Combined := Combined + Json1;
  if not First then Combined := Combined + ',';
  Combined := Combined + Json2;

  Response := BuildSuccessResponse('req-1', '{"objects":[' + Combined + '],"count":2}');

  // Verify we can parse it
  AssertEquals('multi obj id', 'req-1', ExtractJsonValue(Response, 'id'));
  AssertEquals('multi obj count', '2', ExtractJsonValue(ExtractJsonValue(Response, 'data'), 'count'));

  // Verify the array extraction works
  AssertTrue('objects array present', Pos('"objects":[', Response) > 0);
end;

procedure TestJsonValueWithColonInValue;
begin
  Section('JSON value with colon in value');

  // Colons in string values should not confuse the parser
  AssertEquals('colon in value', 'http://example.com',
    ExtractJsonValue('{"url":"http://example.com"}', 'url'));
  AssertEquals('colon in value 2', 'key:value',
    ExtractJsonValue('{"data":"key:value"}', 'data'));
end;

procedure TestJsonObjectExtractionNested;
var
  Json, Inner : String;
begin
  Section('JSON object extraction with nesting');

  Json := '{"a":{"b":{"c":"deep"}},"d":"flat"}';
  Inner := ExtractJsonValue(Json, 'a');
  AssertEquals('extract outer', '{"b":{"c":"deep"}}', Inner);

  // Extract from inner
  Inner := ExtractJsonValue(Inner, 'b');
  AssertEquals('extract mid', '{"c":"deep"}', Inner);

  // Extract from innermost
  AssertEquals('extract deep', 'deep', ExtractJsonValue(Inner, 'c'));

  // Flat key next to nested
  AssertEquals('flat after nested', 'flat', ExtractJsonValue(Json, 'd'));
end;

procedure TestEmptyJsonStructures;
begin
  Section('Empty JSON structures');

  AssertEquals('empty object', '{}', ExtractJsonValue('{"obj":{}}', 'obj'));
  AssertEquals('empty string', '', ExtractJsonValue('{"s":""}', 's'));
  AssertEquals('empty from empty', '', ExtractJsonValue('{}', 'anything'));
  AssertEquals('empty from empty string', '', ExtractJsonValue('', 'anything'));
end;

procedure TestBuildObjectJsonWithSpecialChars;
var
  Obj : TMockSchObject;
  Json : String;
begin
  Section('BuildObjectJson with special characters in values');
  InitMockObject(Obj);

  // Backslash in value
  Obj.Text := 'C:\path\file';
  Json := MockBuildObjectJson(Obj, 'Text');
  AssertTrue('backslash escaped', Pos('C:\\path\\file', Json) > 0);

  // Quote in value
  Obj.Text := 'say "hello"';
  Json := MockBuildObjectJson(Obj, 'Text');
  AssertTrue('quote escaped', Pos('say \"hello\"', Json) > 0);

  // Newline in value
  Obj.Text := 'line1'#10'line2';
  Json := MockBuildObjectJson(Obj, 'Text');
  AssertTrue('newline escaped', Pos('line1\nline2', Json) > 0);
end;

procedure TestMockSetSchPropertyDefaults;
var
  Obj : TMockSchObject;
begin
  Section('SetSchProperty default values');
  InitMockObject(Obj);

  // Invalid integer string should use default
  MockSetSchProperty(Obj, 'Orientation', 'abc');
  AssertEqualsInt('invalid int default 0', 0, Obj.Orientation);

  MockSetSchProperty(Obj, 'FontId', 'xyz');
  AssertEqualsInt('invalid FontId default 1', 1, Obj.FontId);

  MockSetSchProperty(Obj, 'LineWidth', '');
  AssertEqualsInt('empty LineWidth default 1', 1, Obj.LineWidth);

  // null string
  MockSetSchProperty(Obj, 'Style', 'null');
  AssertEqualsInt('null Style default 0', 0, Obj.Style);

  // Valid values work
  MockSetSchProperty(Obj, 'Orientation', '180');
  AssertEqualsInt('valid 180', 180, Obj.Orientation);
end;

procedure TestFilterWithCoordinates;
var
  Obj : TMockSchObject;
begin
  Section('Filter matching with coordinate values');
  InitMockObject(Obj);
  Obj.Location.X := MilsToCoord(100);
  Obj.Location.Y := MilsToCoord(200);
  Obj.Corner.X := MilsToCoord(300);
  Obj.Corner.Y := MilsToCoord(400);

  AssertTrue('X match', MockMatchesFilter(Obj, 'Location.X=100'));
  AssertTrue('Y match', MockMatchesFilter(Obj, 'Location.Y=200'));
  AssertTrue('X and Y match', MockMatchesFilter(Obj, 'Location.X=100|Location.Y=200'));
  AssertFalse('X wrong', MockMatchesFilter(Obj, 'Location.X=999'));
  AssertTrue('Corner match', MockMatchesFilter(Obj, 'Corner.X=300|Corner.Y=400'));
end;

procedure TestFilterWithBooleans;
var
  Obj : TMockSchObject;
begin
  Section('Filter matching with boolean values');
  InitMockObject(Obj);
  Obj.IsHidden := True;
  Obj.IsSolid := False;

  AssertTrue('hidden true', MockMatchesFilter(Obj, 'IsHidden=true'));
  AssertFalse('hidden false', MockMatchesFilter(Obj, 'IsHidden=false'));
  AssertTrue('solid false', MockMatchesFilter(Obj, 'IsSolid=false'));
  AssertFalse('solid true', MockMatchesFilter(Obj, 'IsSolid=true'));
  AssertTrue('combined bool', MockMatchesFilter(Obj, 'IsHidden=true|IsSolid=false'));
end;

{ ========================================================================= }
{ MAIN                                                                       }
{ ========================================================================= }

begin
  TestCount := 0;
  PassCount := 0;
  FailCount := 0;

  WriteLn('=== EDA Agent Real Pascal Test Harness ===');
  WriteLn('');

  // File I/O tests
  TestReadWriteFileContent;

  // Whitespace/delimiter helpers
  TestIsWhitespaceOrColon;
  TestIsDelimiter;

  // JSON parsing -- the core of the system
  TestExtractJsonValue;
  TestExtractJsonArray;
  TestAdversarialJson;
  TestBackslashEdgeCases;
  TestJsonValueWithColonInValue;
  TestJsonObjectExtractionNested;
  TestEmptyJsonStructures;

  // Response builders
  TestBuildSuccessResponse;
  TestBuildErrorResponse;

  // String escaping
  TestEscapeJsonString;
  TestEscapeJsonStringRoundTrip;

  // Coordinate conversions
  TestMilsToCoord_CoordToMils;
  TestMMToCoord_CoordToMM;
  TestCoordConversionEdgeCases;

  // Boolean/String helpers
  TestBoolToJsonStr;
  TestStrToBoolDS;
  TestStrToBoolDS_Roundtrip;
  TestStrToIntDefDS;
  TestStrToFloatDefDS;

  // Layer mapping
  TestGetLayerFromString;
  TestGetLayerString;
  TestGetLayerRoundTrip;

  // Object type mapping
  TestObjectTypeFromString;
  TestObjectTypeFromStringPCB;

  // Mock Altium object property get/set
  TestMockGetSchProperty;
  TestMockSetSchProperty;
  TestMockSetSchPropertyDefaults;

  // Filter matching
  TestMockMatchesFilter;
  TestFilterWithEmptyValues;
  TestFilterWithCoordinates;
  TestFilterWithBooleans;

  // Object JSON building
  TestMockBuildObjectJson;
  TestBuildObjectJsonWithSpecialChars;
  TestMultipleObjectJsonConcatenation;

  // Apply set properties
  TestMockApplySetProperties;

  // Command dispatch
  TestSplitCommand;
  TestCommandDispatchCategories;
  TestGenericActionDispatch;
  TestProjectActionDispatch;
  TestApplicationActionDispatch;

  // Pipe-separated parameter parsing
  TestParsePipeSeparatedParams;

  // Scope parsing
  TestScopeParsingLogic;

  // Full IPC round trips
  TestFileRoundTrip;
  TestFileRoundTripComplex;

  // End-to-end integration
  TestEndToEndQueryParse;
  TestEndToEndModifyParse;
  TestEndToEndResponseConstruction;

  WriteLn('');
  WriteLn('==========================================');
  WriteLn('Total: ', TestCount, '  Pass: ', PassCount, '  Fail: ', FailCount);
  WriteLn('==========================================');

  if FailCount > 0 then
    Halt(1);
end.
