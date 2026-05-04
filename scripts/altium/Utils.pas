{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Utils.pas - Utility functions for the Altium integration bridge                             }
{..............................................................................}

Function MilsToCoord(Mils : Integer) : TCoord;
Begin
    Result := Mils * 10000; // 1 mil = 10000 internal units
End;

Function CoordToMils(Coord : TCoord) : Integer;
Begin
    Result := Round(Coord / 10000);
End;

Function MMToCoord(MM : Double) : TCoord;
Begin
    Result := Round(MM * 10000000 / 25.4);
End;

Function CoordToMM(Coord : TCoord) : Double;
Begin
    Result := Coord * 25.4 / 10000000;
End;

Function BoolToJsonStr(Value : Boolean) : String;
Begin
    If Value Then Result := 'true'
    Else Result := 'false';
End;

Function StrToBool(S : String) : Boolean;
Begin
    Result := (LowerCase(S) = 'true') Or (S = '1');
End;

Function StrToFloatDef(S : String; Default : Double) : Double;
Begin
    If (S = '') Or (S = 'null') Then
        Result := Default
    Else
    Begin
        Try
            Result := StrToFloat(S);
        Except
            Result := Default;
        End;
    End;
End;

Function StrToIntDef(S : String; Default : Integer) : Integer;
Begin
    If (S = '') Or (S = 'null') Then
        Result := Default
    Else
    Begin
        Try
            Result := StrToInt(S);
        Except
            Result := Default;
        End;
    End;
End;

{ Hex digit (0-15) to its '0'..'F' character. Used by EscapeJsonString's    }
{ \uXXXX path for non-ASCII bytes.                                            }
Function HexNibble(N : Integer) : String;
Begin
    If N < 10 Then Result := Chr(Ord('0') + N)
    Else Result := Chr(Ord('A') + (N - 10));
End;

Function ByteToHex4(B : Integer) : String;
Begin
    Result := '00' + HexNibble((B Shr 4) And $F) + HexNibble(B And $F);
End;

Function EscapeJsonString(S : String) : String;
Var
    Tmp : String;
    I, O : Integer;
    Ch : String;
    NeedsCharLoop : Boolean;
Begin
    Result := '';
    // Defensive conversion: DelphiScript lets Variants flow into a
    // parameter declared `String`. If a caller accidentally passes a
    // compound interface (e.g. Comp.Designator returning ISch_Parameter),
    // the implicit Dispatch->OleStr conversion fails. Wrap so a bad caller
    // gets an empty string instead of crashing the polling loop.
    Try
        Tmp := S;
    Except
        Exit;
    End;

    // Fast path: scan once for any byte that needs the slow per-char loop.
    // The vast majority of escaped strings are pure ASCII (designators,
    // file paths, layer names) and stay on the fast path.
    NeedsCharLoop := False;
    For I := 1 To Length(Tmp) Do
    Begin
        O := Ord(Tmp[I]);
        If (O >= 128) Or ((O < 32) And (O <> 9) And (O <> 10) And (O <> 13)) Then
        Begin
            NeedsCharLoop := True;
            Break;
        End;
    End;

    If Not NeedsCharLoop Then
    Begin
        Tmp := StringReplace(Tmp, '\', '\\', -1);
        Tmp := StringReplace(Tmp, '"', '\"', -1);
        Tmp := StringReplace(Tmp, #13, '\r', -1);
        Tmp := StringReplace(Tmp, #10, '\n', -1);
        Tmp := StringReplace(Tmp, #9, '\t', -1);
        Result := Tmp;
        Exit;
    End;

    // Slow path: char-by-char with \u00XX for any non-ASCII byte. Non-ASCII
    // input is treated as Latin-1 / CP1252 (Pascal's native single-byte
    // encoding); the escape produces valid JSON consumable as UTF-8 by any
    // reader. This is the single mechanism that lets us drop the Latin-1
    // read kludge on the Python side — output is always pure ASCII.
    For I := 1 To Length(Tmp) Do
    Begin
        Ch := Copy(Tmp, I, 1);
        O := Ord(Ch[1]);
        If O >= 128 Then
            Result := Result + '\u' + ByteToHex4(O)
        Else If O = Ord('\') Then Result := Result + '\\'
        Else If O = Ord('"') Then Result := Result + '\"'
        Else If O = 13 Then Result := Result + '\r'
        Else If O = 10 Then Result := Result + '\n'
        Else If O = 9 Then Result := Result + '\t'
        Else If O = 8 Then Result := Result + '\b'
        Else If O = 12 Then Result := Result + '\f'
        Else If O < 32 Then Result := Result + '\u' + ByteToHex4(O)
        Else Result := Result + Ch;
    End;
End;

// UnescapeJsonString is defined in Main.pas (compiles first)
// and applied automatically inside ExtractJsonValue for string values.

Function GetLayerFromString(LayerStr : String) : TLayer;
Begin
    Case LayerStr Of
        'TopLayer':        Result := eTopLayer;
        'MidLayer1':       Result := eMidLayer1;
        'MidLayer2':       Result := eMidLayer2;
        'MidLayer3':       Result := eMidLayer3;
        'MidLayer4':       Result := eMidLayer4;
        'MidLayer5':       Result := eMidLayer5;
        'MidLayer6':       Result := eMidLayer6;
        'MidLayer7':       Result := eMidLayer7;
        'MidLayer8':       Result := eMidLayer8;
        'MidLayer9':       Result := eMidLayer9;
        'MidLayer10':      Result := eMidLayer10;
        'MidLayer11':      Result := eMidLayer11;
        'MidLayer12':      Result := eMidLayer12;
        'MidLayer13':      Result := eMidLayer13;
        'MidLayer14':      Result := eMidLayer14;
        'MidLayer15':      Result := eMidLayer15;
        'MidLayer16':      Result := eMidLayer16;
        'MidLayer17':      Result := eMidLayer17;
        'MidLayer18':      Result := eMidLayer18;
        'MidLayer19':      Result := eMidLayer19;
        'MidLayer20':      Result := eMidLayer20;
        'MidLayer21':      Result := eMidLayer21;
        'MidLayer22':      Result := eMidLayer22;
        'MidLayer23':      Result := eMidLayer23;
        'MidLayer24':      Result := eMidLayer24;
        'MidLayer25':      Result := eMidLayer25;
        'MidLayer26':      Result := eMidLayer26;
        'MidLayer27':      Result := eMidLayer27;
        'MidLayer28':      Result := eMidLayer28;
        'MidLayer29':      Result := eMidLayer29;
        'MidLayer30':      Result := eMidLayer30;
        'BottomLayer':     Result := eBottomLayer;
        'TopOverlay':      Result := eTopOverlay;
        'BottomOverlay':   Result := eBottomOverlay;
        'TopPaste':        Result := eTopPaste;
        'BottomPaste':     Result := eBottomPaste;
        'TopSolder':       Result := eTopSolder;
        'BottomSolder':    Result := eBottomSolder;
        'InternalPlane1':  Result := eInternalPlane1;
        'InternalPlane2':  Result := eInternalPlane2;
        'InternalPlane3':  Result := eInternalPlane3;
        'InternalPlane4':  Result := eInternalPlane4;
        'InternalPlane5':  Result := eInternalPlane5;
        'InternalPlane6':  Result := eInternalPlane6;
        'InternalPlane7':  Result := eInternalPlane7;
        'InternalPlane8':  Result := eInternalPlane8;
        'InternalPlane9':  Result := eInternalPlane9;
        'InternalPlane10': Result := eInternalPlane10;
        'InternalPlane11': Result := eInternalPlane11;
        'InternalPlane12': Result := eInternalPlane12;
        'InternalPlane13': Result := eInternalPlane13;
        'InternalPlane14': Result := eInternalPlane14;
        'InternalPlane15': Result := eInternalPlane15;
        'InternalPlane16': Result := eInternalPlane16;
        'DrillGuide':      Result := eDrillGuide;
        'DrillDrawing':    Result := eDrillDrawing;
        'MultiLayer':      Result := eMultiLayer;
        'Mechanical1':     Result := eMechanical1;
        'Mechanical2':     Result := eMechanical2;
        'Mechanical3':     Result := eMechanical3;
        'Mechanical4':     Result := eMechanical4;
        'Mechanical5':     Result := eMechanical5;
        'Mechanical6':     Result := eMechanical6;
        'Mechanical7':     Result := eMechanical7;
        'Mechanical8':     Result := eMechanical8;
        'Mechanical9':     Result := eMechanical9;
        'Mechanical10':    Result := eMechanical10;
        'Mechanical11':    Result := eMechanical11;
        'Mechanical12':    Result := eMechanical12;
        'Mechanical13':    Result := eMechanical13;
        'Mechanical14':    Result := eMechanical14;
        'Mechanical15':    Result := eMechanical15;
        'Mechanical16':    Result := eMechanical16;
        'KeepOutLayer':    Result := eKeepOutLayer;
    Else
        Result := eTopLayer;
    End;
End;

Function GetLayerString(Layer : TLayer) : String;
Begin
    If Layer = eTopLayer Then Result := 'TopLayer'
    Else If Layer = eMidLayer1 Then Result := 'MidLayer1'
    Else If Layer = eMidLayer2 Then Result := 'MidLayer2'
    Else If Layer = eMidLayer3 Then Result := 'MidLayer3'
    Else If Layer = eMidLayer4 Then Result := 'MidLayer4'
    Else If Layer = eMidLayer5 Then Result := 'MidLayer5'
    Else If Layer = eMidLayer6 Then Result := 'MidLayer6'
    Else If Layer = eMidLayer7 Then Result := 'MidLayer7'
    Else If Layer = eMidLayer8 Then Result := 'MidLayer8'
    Else If Layer = eMidLayer9 Then Result := 'MidLayer9'
    Else If Layer = eMidLayer10 Then Result := 'MidLayer10'
    Else If Layer = eMidLayer11 Then Result := 'MidLayer11'
    Else If Layer = eMidLayer12 Then Result := 'MidLayer12'
    Else If Layer = eMidLayer13 Then Result := 'MidLayer13'
    Else If Layer = eMidLayer14 Then Result := 'MidLayer14'
    Else If Layer = eMidLayer15 Then Result := 'MidLayer15'
    Else If Layer = eMidLayer16 Then Result := 'MidLayer16'
    Else If Layer = eMidLayer17 Then Result := 'MidLayer17'
    Else If Layer = eMidLayer18 Then Result := 'MidLayer18'
    Else If Layer = eMidLayer19 Then Result := 'MidLayer19'
    Else If Layer = eMidLayer20 Then Result := 'MidLayer20'
    Else If Layer = eMidLayer21 Then Result := 'MidLayer21'
    Else If Layer = eMidLayer22 Then Result := 'MidLayer22'
    Else If Layer = eMidLayer23 Then Result := 'MidLayer23'
    Else If Layer = eMidLayer24 Then Result := 'MidLayer24'
    Else If Layer = eMidLayer25 Then Result := 'MidLayer25'
    Else If Layer = eMidLayer26 Then Result := 'MidLayer26'
    Else If Layer = eMidLayer27 Then Result := 'MidLayer27'
    Else If Layer = eMidLayer28 Then Result := 'MidLayer28'
    Else If Layer = eMidLayer29 Then Result := 'MidLayer29'
    Else If Layer = eMidLayer30 Then Result := 'MidLayer30'
    Else If Layer = eBottomLayer Then Result := 'BottomLayer'
    Else If Layer = eTopOverlay Then Result := 'TopOverlay'
    Else If Layer = eBottomOverlay Then Result := 'BottomOverlay'
    Else If Layer = eTopPaste Then Result := 'TopPaste'
    Else If Layer = eBottomPaste Then Result := 'BottomPaste'
    Else If Layer = eTopSolder Then Result := 'TopSolder'
    Else If Layer = eBottomSolder Then Result := 'BottomSolder'
    Else If Layer = eInternalPlane1 Then Result := 'InternalPlane1'
    Else If Layer = eInternalPlane2 Then Result := 'InternalPlane2'
    Else If Layer = eInternalPlane3 Then Result := 'InternalPlane3'
    Else If Layer = eInternalPlane4 Then Result := 'InternalPlane4'
    Else If Layer = eInternalPlane5 Then Result := 'InternalPlane5'
    Else If Layer = eInternalPlane6 Then Result := 'InternalPlane6'
    Else If Layer = eInternalPlane7 Then Result := 'InternalPlane7'
    Else If Layer = eInternalPlane8 Then Result := 'InternalPlane8'
    Else If Layer = eInternalPlane9 Then Result := 'InternalPlane9'
    Else If Layer = eInternalPlane10 Then Result := 'InternalPlane10'
    Else If Layer = eInternalPlane11 Then Result := 'InternalPlane11'
    Else If Layer = eInternalPlane12 Then Result := 'InternalPlane12'
    Else If Layer = eInternalPlane13 Then Result := 'InternalPlane13'
    Else If Layer = eInternalPlane14 Then Result := 'InternalPlane14'
    Else If Layer = eInternalPlane15 Then Result := 'InternalPlane15'
    Else If Layer = eInternalPlane16 Then Result := 'InternalPlane16'
    Else If Layer = eDrillGuide Then Result := 'DrillGuide'
    Else If Layer = eDrillDrawing Then Result := 'DrillDrawing'
    Else If Layer = eMultiLayer Then Result := 'MultiLayer'
    Else If Layer = eMechanical1 Then Result := 'Mechanical1'
    Else If Layer = eMechanical2 Then Result := 'Mechanical2'
    Else If Layer = eMechanical3 Then Result := 'Mechanical3'
    Else If Layer = eMechanical4 Then Result := 'Mechanical4'
    Else If Layer = eMechanical5 Then Result := 'Mechanical5'
    Else If Layer = eMechanical6 Then Result := 'Mechanical6'
    Else If Layer = eMechanical7 Then Result := 'Mechanical7'
    Else If Layer = eMechanical8 Then Result := 'Mechanical8'
    Else If Layer = eMechanical9 Then Result := 'Mechanical9'
    Else If Layer = eMechanical10 Then Result := 'Mechanical10'
    Else If Layer = eMechanical11 Then Result := 'Mechanical11'
    Else If Layer = eMechanical12 Then Result := 'Mechanical12'
    Else If Layer = eMechanical13 Then Result := 'Mechanical13'
    Else If Layer = eMechanical14 Then Result := 'Mechanical14'
    Else If Layer = eMechanical15 Then Result := 'Mechanical15'
    Else If Layer = eMechanical16 Then Result := 'Mechanical16'
    Else If Layer = eKeepOutLayer Then Result := 'KeepOutLayer'
    Else Result := 'Unknown';
End;

Function ExtractJsonArray(Json : String; Key : String) : String;
Var
    StartPos, EndPos : Integer;
    SearchKey : String;
    BracketCount : Integer;
Begin
    Result := '';
    SearchKey := '"' + Key + '"';
    StartPos := Pos(SearchKey, Json);
    If StartPos > 0 Then
    Begin
        StartPos := StartPos + Length(SearchKey);
        While (StartPos <= Length(Json)) And IsWhitespaceOrColon(Json, StartPos) Do
            Inc(StartPos);

        If (StartPos <= Length(Json)) And (Copy(Json, StartPos, 1) = '[') Then
        Begin
            EndPos := StartPos;
            BracketCount := 1;
            Inc(EndPos);
            While (EndPos <= Length(Json)) And (BracketCount > 0) Do
            Begin
                If Copy(Json, EndPos, 1) = '[' Then Inc(BracketCount)
                Else If Copy(Json, EndPos, 1) = ']' Then Dec(BracketCount);
                Inc(EndPos);
            End;
            Result := Copy(Json, StartPos, EndPos - StartPos);
        End;
    End;
End;
