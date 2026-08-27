{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
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

Function CoordWithinTol(A, B, Tol : Integer) : Boolean;
Begin
    Result := Abs(A - B) <= Tol;
End;

{ True if (X,Y) is within Tol of the segment (X1,Y1)-(X2,Y2). Orthogonal }
{ schematic wires (the common case) are exact. Diagonal segments use a   }
{ bounding-box test so we never need a 64-bit multiply.                  }
Function PointNearSegment(X, Y, X1, Y1, X2, Y2, Tol : Integer) : Boolean;
Var
    Lo, Hi : Integer;
Begin
    Result := False;
    If (X1 = X2) And (Y1 = Y2) Then
    Begin
        Result := CoordWithinTol(X, X1, Tol) And CoordWithinTol(Y, Y1, Tol);
        Exit;
    End;
    If X1 = X2 Then
    Begin
        If Not CoordWithinTol(X, X1, Tol) Then Exit;
        Lo := Y1;
        If Y2 < Lo Then Lo := Y2;
        Hi := Y1;
        If Y2 > Hi Then Hi := Y2;
        Result := (Y >= Lo - Tol) And (Y <= Hi + Tol);
        Exit;
    End;
    If Y1 = Y2 Then
    Begin
        If Not CoordWithinTol(Y, Y1, Tol) Then Exit;
        Lo := X1;
        If X2 < Lo Then Lo := X2;
        Hi := X1;
        If X2 > Hi Then Hi := X2;
        Result := (X >= Lo - Tol) And (X <= Hi + Tol);
        Exit;
    End;
    Lo := X1;
    If X2 < Lo Then Lo := X2;
    Hi := X1;
    If X2 > Hi Then Hi := X2;
    If (X < Lo - Tol) Or (X > Hi + Tol) Then Exit;
    Lo := Y1;
    If Y2 < Lo Then Lo := Y2;
    Hi := Y1;
    If Y2 > Hi Then Hi := Y2;
    Result := (Y >= Lo - Tol) And (Y <= Hi + Tol);
End;

Function BoolToJsonStr(Value : Boolean) : String;
Begin
    If Value Then Result := 'true'
    Else Result := 'false';
End;

Function FloatToJsonStr(Value : Double) : String;
Var
    OldSep : Char;
Begin
    { Locale-agnostic float -> string. Delphi FloatToStr respects the global }
    { DecimalSeparator, so on a system with comma-as-decimal it produces     }
    { '90,0' which is invalid JSON. Force '.' for the duration of the call. }
    OldSep := DecimalSeparator;
    DecimalSeparator := '.';
    Try
        Result := FloatToStr(Value);
    Finally
        DecimalSeparator := OldSep;
    End;
End;

{ HexNibble / ByteToHex4 / EscapeJsonString MUST be defined before the JSON  }
{ prop builders below, which call EscapeJsonString. DelphiScript resolves     }
{ identifiers top-down within a unit and has no Forward declarations, so a    }
{ callee defined later in the file is "Undeclared identifier" at the caller.  }
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
    // read kludge on the Python side, output is always pure ASCII.
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

{..............................................................................}
{ JSON prop builders. Each returns a `"name":value` fragment ready to be     }
{ joined with comma separators into an object body. Replaces hand-rolled    }
{ `'"' + EscapeJsonString(...) + '":"' + EscapeJsonString(...) + '"'`        }
{ patterns scattered across the codebase. Cuts escape-at-the-seams bugs in  }
{ long response bodies (every key + every string value goes through         }
{ EscapeJsonString exactly once).                                            }
{                                                                              }
{ Usage:                                                                       }
{   Body := JsonStr('designator', Des) + ',' +                                 }
{           JsonInt('pin_count', N)    + ',' +                                 }
{           JsonRaw('pins', PinsArray);                                        }
{   Result := JsonObj(Body);   // wraps Body in object braces                  }
{                                                                              }
{ JsonRaw is the escape hatch for nested objects/arrays whose body is        }
{ already a serialized string (no extra escaping applied).                   }
{..............................................................................}

Function JsonStr(Name, Value : String) : String;
Begin
    Result := '"' + EscapeJsonString(Name) + '":"' + EscapeJsonString(Value) + '"';
End;

Function JsonInt(Name : String; Value : Integer) : String;
Begin
    Result := '"' + EscapeJsonString(Name) + '":' + IntToStr(Value);
End;

Function JsonFloat(Name : String; Value : Double) : String;
Begin
    Result := '"' + EscapeJsonString(Name) + '":' + FloatToJsonStr(Value);
End;

Function JsonBool(Name : String; Value : Boolean) : String;
Begin
    Result := '"' + EscapeJsonString(Name) + '":' + BoolToJsonStr(Value);
End;

Function JsonRaw(Name, RawValue : String) : String;
Begin
    { For nested objects/arrays already serialized as a string. Caller is    }
    { responsible for emitting valid JSON in RawValue (no escaping).         }
    Result := '"' + EscapeJsonString(Name) + '":' + RawValue;
End;

Function JsonNull(Name : String) : String;
Begin
    Result := '"' + EscapeJsonString(Name) + '":null';
End;

Function JsonObj(Body : String) : String;
Begin
    Result := '{' + Body + '}';
End;

Function JsonArr(Body : String) : String;
Begin
    Result := '[' + Body + ']';
End;

{..............................................................................}
{ Pin electrical-type <-> string converters. Used for JSON output of sch    }
{ geometry, and for parsing JSON pin specs when creating symbols. Keeps the }
{ enum vocabulary in one place instead of scattered If/Else If chains.      }
{ Verified against the Altium schematic API documentation; note that        }
{ Altium spells bidirectional as eElectricIO (NOT eElectricBiDir).           }
{..............................................................................}

Function PinElectricalToStr(Electrical : TPinElectrical) : String;
Begin
    If Electrical = eElectricInput Then Result := 'input'
    Else If Electrical = eElectricOutput Then Result := 'output'
    Else If Electrical = eElectricIO Then Result := 'io'
    Else If Electrical = eElectricPower Then Result := 'power'
    Else If Electrical = eElectricOpenCollector Then Result := 'open_collector'
    Else If Electrical = eElectricOpenEmitter Then Result := 'open_emitter'
    Else If Electrical = eElectricHiZ Then Result := 'hiz'
    Else Result := 'passive';   { eElectricPassive fallback covers unknown ords }
End;

Function StrToPinElectrical(S : String) : TPinElectrical;
Var
    LS : String;
Begin
    LS := LowerCase(Trim(S));
    { Accept Altium's raw enum name too (e.g. 'eElectricOutput'), not only the }
    { short human form, and drop underscores so 'open_collector' and          }
    { 'opencollector' both match.                                             }
    If Copy(LS, 1, 9) = 'eelectric' Then LS := Copy(LS, 10, Length(LS));
    LS := StringReplace(LS, '_', '', -1);

    If (LS = 'input') Or (LS = 'in') Then Result := eElectricInput
    Else If (LS = 'output') Or (LS = 'out') Then Result := eElectricOutput
    Else If (LS = 'io') Or (LS = 'bidir') Or (LS = 'bidirectional') Then Result := eElectricIO
    Else If LS = 'power' Then Result := eElectricPower
    Else If (LS = 'opencollector') Or (LS = 'oc') Then Result := eElectricOpenCollector
    Else If (LS = 'openemitter') Or (LS = 'oe') Then Result := eElectricOpenEmitter
    Else If (LS = 'hiz') Or (LS = 'tri') Or (LS = 'tristate') Then Result := eElectricHiZ
    Else Result := eElectricPassive;
End;

{..............................................................................}
{ Pin orientation (rotation) <-> degrees / string converters. Altium's       }
{ TRotationBy90 ordinals are eRotate0/90/180/270 with ord values 0..3;      }
{ multiplying the ord by 90 gives the degree value used in our JSON API.    }
{ String form matches Altium's compass-direction convention for symbol pins }
{ (pin "points" in this direction).                                          }
{..............................................................................}

Function OrientationToDegrees(Orient : TRotationBy90) : Integer;
Begin
    Result := Ord(Orient) * 90;
End;

Function DegreesToOrientation(Degrees : Integer) : TRotationBy90;
Begin
    { Normalize input to [0, 360) and snap to nearest 90. }
    Degrees := ((Degrees Mod 360) + 360) Mod 360;
    If Degrees >= 270 Then Result := eRotate270
    Else If Degrees >= 180 Then Result := eRotate180
    Else If Degrees >= 90 Then Result := eRotate90
    Else Result := eRotate0;
End;

Function StrToPinOrientation(S : String) : TRotationBy90;
Var
    LS : String;
Begin
    { Accept both compass words ("right" = pin points right = 0 deg) and a    }
    { raw degree value. The compass mapping follows Altium symbol convention. }
    LS := LowerCase(Trim(S));
    If (LS = 'right') Or (LS = '0') Then Result := eRotate0
    Else If (LS = 'up') Or (LS = '90') Then Result := eRotate90
    Else If (LS = 'left') Or (LS = '180') Then Result := eRotate180
    Else If (LS = 'down') Or (LS = '270') Then Result := eRotate270
    Else Result := DegreesToOrientation(StrToIntDef(LS, 0));
End;

{..............................................................................}
{ Power-port style <-> string converters. TPowerObjectStyle has the standard }
{ Altium variants for GND symbols (signal / power / earth), supply rails    }
{ (Bar / Arrow / Wave), and a generic Circle. Used when authoring power    }
{ ports programmatically and when auditing orientation / net-name rules.   }
{..............................................................................}

Function PowerStyleToStr(Style : TPowerObjectStyle) : String;
Begin
    If Style = ePowerCircle Then Result := 'circle'
    Else If Style = ePowerArrow Then Result := 'arrow'
    Else If Style = ePowerBar Then Result := 'bar'
    Else If Style = ePowerWave Then Result := 'wave'
    Else If Style = ePowerGndPower Then Result := 'gnd_power'
    Else If Style = ePowerGndSignal Then Result := 'gnd_signal'
    Else If Style = ePowerGndEarth Then Result := 'gnd_earth'
    Else Result := '';
End;

Function StrToPowerStyle(S : String) : TPowerObjectStyle;
Var
    LS : String;
Begin
    LS := LowerCase(Trim(S));
    If (LS = 'circle') Or (LS = 'gnd_circle') Then Result := ePowerCircle
    Else If LS = 'arrow' Then Result := ePowerArrow
    Else If (LS = 'bar') Or (LS = 'powerbar') Or (LS = 'rail') Then Result := ePowerBar
    Else If LS = 'wave' Then Result := ePowerWave
    Else If (LS = 'gnd_power') Or (LS = 'powergnd') Then Result := ePowerGndPower
    Else If (LS = 'gnd_signal') Or (LS = 'signalgnd') Or (LS = 'sgnd') Then Result := ePowerGndSignal
    Else If (LS = 'gnd_earth') Or (LS = 'earth') Or (LS = 'egnd') Then Result := ePowerGndEarth
    Else If (LS = 'gnd') Or (LS = 'ground') Then Result := ePowerGndPower  { common shorthand }
    Else Result := ePowerBar;  { sensible default for a supply rail }
End;

Function StrToBool(S : String) : Boolean;
Begin
    Result := (LowerCase(S) = 'true') Or (S = '1');
End;

{ True iff S is a plain (optionally negative) integer literal. Used to gate    }
{ StrToInt so a non-numeric value never raises EConvertError - the exception   }
{ was caught anyway, but Altium's IDE break-on-exception pops a modal that     }
{ blocks the polling loop.                                                     }
Function IsIntStr(S : String) : Boolean;
Var
    I, StartPos : Integer;
Begin
    S := Trim(S);
    Result := False;
    If S = '' Then Exit;
    StartPos := 1;
    If S[1] = '-' Then StartPos := 2;
    If StartPos > Length(S) Then Exit;   { a lone '-' is not an integer }
    For I := StartPos To Length(S) Do
        If (S[I] < '0') Or (S[I] > '9') Then Exit;
    Result := True;
End;

{..............................................................................}
{ IEEE pin-symbol (TIeeeSymbol) converters, used for the decoration drawn on  }
{ a pin's inner or outer edge: the inversion bubble on an active-low pin      }
{ (outer edge, 'dot') and the wedge on a clock pin (inner edge, 'clock').     }
{                                                                             }
{ These deliberately traffic in Integer, never in TIeeeSymbol. That type name }
{ appears nowhere else in this codebase, so whether DelphiScript declares it  }
{ is unverified, and an undeclared identifier in a signature faults at        }
{ runtime where Try/Except cannot catch it. Assigning a plain Integer to an   }
{ enum-typed property is already established here: Lib_AddPins sets           }
{ Pin.Orientation (a TRotationBy90) from Rotation Div 90.                     }
{                                                                             }
{ Position in IeeeSymbolNames IS the enum ordinal, so the two converters      }
{ below cannot disagree. Order verified against the schematic API types       }
{ reference (TIeeeSymbol, 35 members, eNoSymbol = 0).                         }
{..............................................................................}

{ Delete every occurrence of one character. Written out rather than calling  }
{ StringReplace because DelphiScript spells the replace-all flag as the      }
{ integer -1 while Free Pascal wants a TReplaceFlags set, and these routines }
{ are compiled by BOTH: tests/cross_validate_pascal.pas carries them         }
{ verbatim so a real Pascal compiler can check them without Altium.          }
Function StripChar(S : String; C : Char) : String;
Var
    I : Integer;
Begin
    Result := '';
    For I := 1 To Length(S) Do
        If S[I] <> C Then Result := Result + S[I];
End;

Function IeeeSymbolNames : String;
Begin
    Result :=
        'no_symbol|dot|right_left_signal_flow|clock|active_low_input|' +
        'analog_signal_in|not_logic_connection|shift_right|postponed_output|' +
        'open_collector|hiz|high_current|pulse|schmitt|delay|group_line|' +
        'group_bin|active_low_output|pi_symbol|greater_equal|less_equal|' +
        'sigma|open_collector_pullup|open_emitter|open_emitter_pullup|' +
        'digital_signal_in|and|invertor|or|xor|shift_left|input_output|' +
        'open_circuit_output|left_right_signal_flow|bidirectional_signal_flow';
End;

Function IeeeSymbolToStr(V : Integer) : String;
Var
    Names, Tok : String;
    I, P : Integer;
Begin
    { Unknown ordinals report as 'no_symbol' rather than raising: this feeds }
    { JSON output, where a bad read must not abort the whole response.       }
    Result := 'no_symbol';
    If V <= 0 Then Exit;
    Names := IeeeSymbolNames + '|';
    I := 0;
    While Names <> '' Do
    Begin
        P := Pos('|', Names);
        If P = 0 Then Break;
        Tok := Copy(Names, 1, P - 1);
        Names := Copy(Names, P + 1, Length(Names));
        If I = V Then
        Begin
            Result := Tok;
            Exit;
        End;
        I := I + 1;
    End;
End;

Function StrToIeeeSymbol(S : String) : Integer;
Var
    LS, Compact, Names, Tok : String;
    I, P : Integer;
Begin
    Result := 0;
    LS := LowerCase(Trim(S));
    If LS = '' Then Exit;

    { A bare ordinal is accepted so a caller can reach any TIeeeSymbol member, }
    { including the ones with no friendly alias spelled out below.             }
    If IsIntStr(LS) Then
    Begin
        Result := StrToIntDef(LS, 0);
        If (Result < 0) Or (Result > 34) Then Result := 0;
        Exit;
    End;

    { Friendly aliases for the two that carry real schematic meaning. KiCad   }
    { and most part libraries describe these as "inverted" and "clock".       }
    Compact := StripChar(LS, '_');
    If (Compact = 'inverted') Or (Compact = 'inversion') Or (Compact = 'bubble')
        Or (Compact = 'activelow') Or (Compact = 'negated') Then
    Begin
        Result := 1;    { eDot }
        Exit;
    End;
    If Compact = 'clk' Then
    Begin
        Result := 3;    { eClock }
        Exit;
    End;

    { Altium's raw enum spelling ('eActiveLowInput') differs from the         }
    { canonical name only by a leading 'e', so retry once with it stripped.   }
    Names := IeeeSymbolNames + '|';
    I := 0;
    While Names <> '' Do
    Begin
        P := Pos('|', Names);
        If P = 0 Then Break;
        Tok := StripChar(Copy(Names, 1, P - 1), '_');
        Names := Copy(Names, P + 1, Length(Names));
        If Compact = Tok Then
        Begin
            Result := I;
            Exit;
        End;
        If (Length(Compact) > 1) And (Compact[1] = 'e') Then
            If Copy(Compact, 2, Length(Compact)) = Tok Then
            Begin
                Result := I;
                Exit;
            End;
        I := I + 1;
    End;
End;

Function StrToFloatDef(S : String; Default : Double) : Double;
Var
    OldSep : Char;
Begin
    { Locale-agnostic float parsing. JSON always uses '.' as the decimal      }
    { separator regardless of the user's Windows regional settings, but Delphi}
    { StrToFloat respects the global DecimalSeparator, so on a system with    }
    { comma-as-decimal (much of Europe) parsing "90.0" silently fails and     }
    { the default value comes back instead. Temporarily force '.' for the    }
    { duration of the parse, then restore whatever the system set.            }
    If (S = '') Or (S = 'null') Then
    Begin
        Result := Default;
        Exit;
    End;
    OldSep := DecimalSeparator;
    DecimalSeparator := '.';
    Try
        Try
            Result := StrToFloat(S);
        Except
            Result := Default;
        End;
    Finally
        DecimalSeparator := OldSep;
    End;
End;

Function StrToIntDef(S : String; Default : Integer) : Integer;
Begin
    { Guard the conversion: a non-integer value (blank, 'null', or a symbolic  }
    { token like 'eElectricOutput') returns Default WITHOUT calling StrToInt,  }
    { so no EConvertError is raised. The Try/Except stays as a backstop.       }
    If Not IsIntStr(S) Then
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

{ Resolve an electrical-type value to the integer ordinal assigned directly to }
{ ISch_Pin.Electrical. Accepts an integer ('2'), the human form ('output') or }
{ Altium's raw enum name ('eElectricOutput'). Never raises. Round-trips with   }
{ GetSchProperty('Electrical'), which returns IntToStr(Obj.Electrical).        }
Function ElectricalOrdinal(Value : String) : Integer;
Begin
    If IsIntStr(Value) Then Result := StrToIntDef(Value, 0)
    Else Result := Ord(StrToPinElectrical(Value));
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

{..............................................................................}
{ Mechanical layer KIND: the property that says what a mechanical layer is    }
{ FOR, rather than what it is called. Courtyard, Assembly, 3D Body and the    }
{ rest. A renamed layer still has no kind, and every feature that resolves a  }
{ layer by purpose then skips it, so the outlines are drawn and nothing uses  }
{ them.                                                                        }
{                                                                              }
{ Carried as an Integer. The enum identifiers are not declared in this script  }
{ binding, and an undeclared identifier faults at RUN time on the user's board }
{ rather than being caught when the script loads.                              }
{                                                                              }
{ The numbering is the layer stack manager's own. 31 to 36 are unassigned,     }
{ which is why the map has a hole in it rather than an off-by-one.            }
{..............................................................................}

Function MechKindToString(K : Integer) : String;
Begin
    Case K Of
        0  : Result := 'Not Set';
        1  : Result := 'Assembly Top';
        2  : Result := 'Assembly Bottom';
        3  : Result := 'Assembly Notes';
        4  : Result := 'Board';
        5  : Result := 'Coating Top';
        6  : Result := 'Coating Bottom';
        7  : Result := 'Component Center Top';
        8  : Result := 'Component Center Bottom';
        9  : Result := 'Component Outline Top';
        10 : Result := 'Component Outline Bottom';
        11 : Result := 'Courtyard Top';
        12 : Result := 'Courtyard Bottom';
        13 : Result := 'Designator Top';
        14 : Result := 'Designator Bottom';
        15 : Result := 'Dimensions';
        16 : Result := 'Dimensions Top';
        17 : Result := 'Dimensions Bottom';
        18 : Result := 'Fab Notes';
        19 : Result := 'Glue Points Top';
        20 : Result := 'Glue Points Bottom';
        21 : Result := 'Gold Plating Top';
        22 : Result := 'Gold Plating Bottom';
        23 : Result := 'Value Top';
        24 : Result := 'Value Bottom';
        25 : Result := 'V Cut';
        26 : Result := '3D Body Top';
        27 : Result := '3D Body Bottom';
        28 : Result := 'Route Tool Path';
        29 : Result := 'Sheet';
        30 : Result := 'Board Shape';
        37 : Result := 'Tenting Top';
        38 : Result := 'Tenting Bottom';
        39 : Result := 'Covering Top';
        40 : Result := 'Covering Bottom';
        41 : Result := 'Plugging Top';
        42 : Result := 'Plugging Bottom';
        43 : Result := 'Filling';
        44 : Result := 'Capping';
    Else
        Result := 'Unknown';
    End;
End;

{ A kind name or a bare number to its integer, or -1 when neither.            }
{ Numbers are accepted so a kind added by a later Altium release can still be }
{ set through this handler without waiting for the map above to catch up.     }

Function MechKindFromString(S : String) : Integer;
Var
    U, Candidate : String;
    I : Integer;
Begin
    Result := -1;
    U := UpperCase(Trim(S));
    If U = '' Then Exit;

    If IsIntStr(U) Then
    Begin
        I := StrToIntDef(U, -1);
        If (I >= 0) And (I <= 44) Then Result := I;
        Exit;
    End;

    For I := 0 To 44 Do
    Begin
        Candidate := MechKindToString(I);
        { 'Unknown' is what the map returns for the unassigned numbers, so    }
        { matching against it would quietly resolve to the first hole.        }
        If Candidate <> 'Unknown' Then
        Begin
            If UpperCase(Candidate) = U Then
            Begin
                Result := I;
                Exit;
            End;
        End;
    End;
End;

{ The kind currently on a mechanical layer, or -1 when the property is not    }
{ readable. AD17 and AD18 have no mechanical layer kinds at all, and the read }
{ faults there rather than returning zero.                                    }

Function ReadMechKind(LayerObj : IPCB_LayerObject_V7) : Integer;
Begin
    Result := -1;
    If LayerObj = Nil Then Exit;
    Try
        Result := LayerObj.Kind;
    Except
        Result := -1;
    End;
End;

{..............................................................................}
{ Mechanical layers above 16.                                                  }
{                                                                              }
{ GetLayerFromString knows Mechanical1 to Mechanical16, which is the legacy    }
{ set. A V9 stack goes to 1024, and a real library was found keeping eleven of }
{ its twelve named layers in the 17 to 28 range: Top 3D Body on Mechanical 21, }
{ Top Courtyard on 25, and so on. Every one of those was unreachable, so a     }
{ sweep applied the single layer that happened to sit below 16 and silently    }
{ skipped the rest.                                                            }
{                                                                              }
{ LayerUtils.MechanicalLayer(n) is the accessor that covers the full range.    }
{ It is guarded because this codebase has not used LayerUtils before, and an   }
{ identifier this binding does not declare faults at RUN time rather than      }
{ when the script loads.                                                       }
{                                                                              }
{ The identifiers encode as 16908288 + n, which is how Mechanical 21 reads as  }
{ 16908309 in a library file. Written in decimal deliberately: an eight digit  }
{ hex literal has silently aborted a unit in this dialect before.              }
{..............................................................................}

Function MechLayerIdBase : Integer;
Begin
    Result := 16908288;
End;

{ The mechanical layer NUMBER a caller meant, or -1.                          }
{ Accepts "Mechanical21", "Mech21", "21", and the raw layer id.               }

Function ParseMechLayerNumber(S : String) : Integer;
Var
    T : String;
    I, Value : Integer;
Begin
    Result := -1;
    T := UpperCase(Trim(S));
    If T = '' Then Exit;

    T := StringReplace(T, ' ', '', MkSet(rfReplaceAll));
    If Copy(T, 1, 10) = 'MECHANICAL' Then
        T := Copy(T, 11, Length(T))
    Else If Copy(T, 1, 4) = 'MECH' Then
        T := Copy(T, 5, Length(T));

    If Not IsIntStr(T) Then Exit;
    Value := StrToIntDef(T, -1);
    If Value < 0 Then Exit;

    { A raw layer id, as stored in the file. }
    If Value > 1024 Then
    Begin
        If (Value > MechLayerIdBase) And (Value <= MechLayerIdBase + 1024) Then
            Result := Value - MechLayerIdBase;
        Exit;
    End;

    If (Value >= 1) And (Value <= 1024) Then Result := Value;
End;

{ The TLayer for a mechanical layer number, or eNoLayer.                      }

Function MechLayerFromNumber(N : Integer) : TLayer;
Begin
    Result := eNoLayer;
    If (N < 1) Or (N > 1024) Then Exit;
    If N <= 16 Then
    Begin
        Result := GetLayerFromString('Mechanical' + IntToStr(N));
        Exit;
    End;
    Try
        Result := LayerUtils.MechanicalLayer(N);
    Except
        Result := eNoLayer;
    End;
End;


{..............................................................................}
{ LAYER NAMES THAT COME FROM A CALLER.                                         }
{                                                                              }
{ GetLayerFromString above matches canonical space-free tokens ONLY, and its    }
{ Else branch answers eTopLayer for EVERY name it does not recognise. Handlers  }
{ fed that result straight into an object's Layer, so "Internal Plane 1" - the  }
{ exact spelling pcb_get_layer_stackup PRINTS - placed the object on the TOP    }
{ layer while the response echoed the REQUESTED name, so nothing looked wrong.  }
{ Measured on a real board: two full-board pours on different nets both landed  }
{ on TopLayer, each answering "placed":true, and shorted the board.             }
{                                                                              }
{ ResolveLayerId answers eNoLayer rather than guessing. Every handler that      }
{ takes a layer name from the caller MUST resolve through it, MUST report       }
{ eNoLayer as an error, and MUST echo the RESOLVED layer rather than the        }
{ string it was handed. Silently retargeting the top layer is the bug.          }
{                                                                              }
{ Resolution order, first hit wins:                                            }
{   1. the copper stack's own layer names - exactly what get_layer_stackup      }
{      reports - compared with spaces stripped and case folded, so a renamed    }
{      plane or signal layer resolves;                                          }
{   2. the mechanical layers' names, read the same way; they are not part of    }
{      the FirstLayer/NextLayer walk;                                           }
{   3. the canonical token, but ONLY when GetLayerString round-trips it. That   }
{      round-trip is the guard: without it the eTopLayer Else branch comes      }
{      back as a confident wrong answer for any typo at all;                    }
{   4. Mechanical17..1024, which have no canonical token.                       }
{..............................................................................}

{ Spaces stripped and case folded: the form every comparison below uses, so    }
{ "Internal Plane 1", "InternalPlane1" and "internalplane1" are one name.      }

Function NormalizeLayerName(S : String) : String;
Begin
    Result := UpperCase(StripChar(Trim(S), ' '));
End;

Function ResolveLayerIdInStack(LayerStack : IPCB_LayerStack_V7; LayerName : String) : TLayer;
Var
    Obj : IPCB_LayerObject_V7;
    Stripped, Wanted, ThisName : String;
    Candidate, Lyr, Hit : TLayer;
    MechNum : Integer;
Begin
    Result := eNoLayer;
    Stripped := StripChar(Trim(LayerName), ' ');
    If Stripped = '' Then Exit;
    Wanted := UpperCase(Stripped);

    If LayerStack <> Nil Then
    Begin
        Obj := Nil;
        Try Obj := LayerStack.FirstLayer; Except Obj := Nil; End;
        While Obj <> Nil Do
        Begin
            ThisName := '';
            Try ThisName := Obj.Name; Except ThisName := ''; End;
            If NormalizeLayerName(ThisName) = Wanted Then
            Begin
                Hit := eNoLayer;
                Try Hit := Obj.LayerID; Except Hit := eNoLayer; End;
                If Hit <> eNoLayer Then
                Begin
                    Result := Hit;
                    Exit;
                End;
            End;
            Try Obj := LayerStack.NextLayer(Obj); Except Obj := Nil; End;
        End;

        For Lyr := eMechanical1 To eMechanical16 Do
        Begin
            Obj := Nil;
            Try Obj := LayerStack.LayerObject_V7[Lyr]; Except Obj := Nil; End;
            If Obj <> Nil Then
            Begin
                ThisName := '';
                Try ThisName := Obj.Name; Except ThisName := ''; End;
                If NormalizeLayerName(ThisName) = Wanted Then
                Begin
                    Result := Lyr;
                    Exit;
                End;
            End;
        End;
    End;

    Candidate := GetLayerFromString(Stripped);
    If UpperCase(GetLayerString(Candidate)) = Wanted Then
    Begin
        Result := Candidate;
        Exit;
    End;

    If Copy(Wanted, 1, 4) = 'MECH' Then
    Begin
        MechNum := ParseMechLayerNumber(Stripped);
        If MechNum > 0 Then Result := MechLayerFromNumber(MechNum);
    End;
End;

{ The same resolution for the handlers that hold an IPCB_Board rather than a   }
{ stack. A board whose stack cannot be read still resolves canonical tokens.   }

Function ResolveLayerId(Board : IPCB_Board; LayerName : String) : TLayer;
Var
    LayerStack : IPCB_LayerStack_V7;
Begin
    LayerStack := Nil;
    If Board <> Nil Then
    Begin
        Try LayerStack := Board.LayerStack_V7; Except LayerStack := Nil; End;
    End;
    Result := ResolveLayerIdInStack(LayerStack, LayerName);
End;

{ The names this board actually answers to, appended to an UNKNOWN_LAYER       }
{ message so the caller can correct the call without a second round trip.      }
{ Bounded: the stack's own names, then the mechanical layers' names.           }

Function BoardLayerNamesHint(Board : IPCB_Board) : String;
Var
    LayerStack : IPCB_LayerStack_V7;
    Obj : IPCB_LayerObject_V7;
    Names, ThisName : String;
    Lyr : TLayer;
    Count : Integer;
Begin
    Names := '';
    Count := 0;
    LayerStack := Nil;
    If Board <> Nil Then
    Begin
        Try LayerStack := Board.LayerStack_V7; Except LayerStack := Nil; End;
    End;

    If LayerStack <> Nil Then
    Begin
        Obj := Nil;
        Try Obj := LayerStack.FirstLayer; Except Obj := Nil; End;
        While (Obj <> Nil) And (Count < 64) Do
        Begin
            ThisName := '';
            Try ThisName := Obj.Name; Except ThisName := ''; End;
            If ThisName <> '' Then
            Begin
                If Names <> '' Then Names := Names + ', ';
                Names := Names + ThisName;
                Inc(Count);
            End;
            Try Obj := LayerStack.NextLayer(Obj); Except Obj := Nil; End;
        End;

        For Lyr := eMechanical1 To eMechanical16 Do
        Begin
            If Count < 64 Then
            Begin
                Obj := Nil;
                Try Obj := LayerStack.LayerObject_V7[Lyr]; Except Obj := Nil; End;
                If Obj <> Nil Then
                Begin
                    ThisName := '';
                    Try ThisName := Obj.Name; Except ThisName := ''; End;
                    If ThisName <> '' Then
                    Begin
                        If Names <> '' Then Names := Names + ', ';
                        Names := Names + ThisName;
                        Inc(Count);
                    End;
                End;
            End;
        End;
    End;

    If Names = '' Then
        Result := 'Valid names are canonical tokens such as TopLayer, '
            + 'BottomLayer, MidLayer1, InternalPlane1, TopOverlay, TopPaste, '
            + 'TopSolder, MultiLayer, KeepOutLayer, Mechanical1.'
    Else
        Result := 'Layers on this board: ' + Names
            + '. Canonical tokens are also accepted (TopLayer, MidLayer1, '
            + 'InternalPlane1, TopOverlay, TopPaste, TopSolder, MultiLayer, '
            + 'KeepOutLayer, Mechanical1..16).';
End;
{..............................................................................}
{ Paired mechanical layer kinds.                                               }
{                                                                              }
{ Most kinds come as a Top and Bottom pair, and Altium refuses to set one      }
{ unless the two layers are joined as a LAYER PAIR first. Measured on a real   }
{ library: on a single layer in one call, "Fab Notes" and "Not Set" applied    }
{ and "Component Outline Top" was refused, with nothing else holding that      }
{ kind. Single kinds need no partner; paired ones do.                          }
{                                                                              }
{ Derived from the NAME rather than a second hardcoded table, so a kind added  }
{ by a later Altium release pairs correctly without another list to update.    }
{..............................................................................}

Function MechKindIsPaired(K : Integer) : Boolean;
Var
    S : String;
Begin
    S := MechKindToString(K);
    Result := (Pos(' Top', S) > 0) Or (Pos(' Bottom', S) > 0);
End;

{ The kind on the other side of a pair, or -1 when the kind is single. }

Function MechKindPartner(K : Integer) : Integer;
Var
    S, Other : String;
    P, I : Integer;
Begin
    Result := -1;
    S := MechKindToString(K);
    If S = 'Unknown' Then Exit;

    P := Pos(' Top', S);
    If P > 0 Then
        Other := Copy(S, 1, P - 1) + ' Bottom'
    Else
    Begin
        P := Pos(' Bottom', S);
        If P = 0 Then Exit;
        Other := Copy(S, 1, P - 1) + ' Top';
    End;

    For I := 0 To 44 Do
        If MechKindToString(I) = Other Then
        Begin
            Result := I;
            Exit;
        End;
End;

{..............................................................................}
{ Layer PAIR kinds are a SECOND enum, not the layer kinds renumbered.          }
{                                                                              }
{ A paired concept is held by the pair, not by either layer: the pair carries  }
{ "Component Outline" while the two layers carry "Component Outline Top" and   }
{ "Component Outline Bottom". The ids differ as well, so a layer kind used as  }
{ a pair kind names a different concept. Writing the layer property leaves the }
{ LayerKindMapping stream empty, which is why a paired kind read back          }
{ unchanged however the layer write was attempted.                             }
{                                                                              }
{ There are no Top and Bottom entries here, and the numbering is its own.      }
{..............................................................................}

Function MechPairKindToString(K : Integer) : String;
Begin
    Result := 'Unknown';
    If K = 0  Then Result := 'Not Set';
    If K = 1  Then Result := 'Assembly';
    If K = 2  Then Result := 'Coating';
    If K = 3  Then Result := 'Component Center';
    If K = 4  Then Result := 'Component Outline';
    If K = 5  Then Result := 'Courtyard';
    If K = 6  Then Result := 'Designator';
    If K = 7  Then Result := 'Dimensions';
    If K = 8  Then Result := 'Glue Points';
    If K = 9  Then Result := 'Gold Plating';
    If K = 10 Then Result := 'Value';
    If K = 11 Then Result := '3D Body';
    { Via protection, IPC-4761. }
    If K = 15 Then Result := 'Tenting';
    If K = 16 Then Result := 'Covering';
    If K = 17 Then Result := 'Plugging';
End;

{ The pair kind that carries a paired layer kind.                              }
{                                                                              }
{ Matched on the name with the side suffix removed rather than through a       }
{ third table, so the two enums cannot drift apart here. The reference does    }
{ the same match but stops at 12, which silently drops Tenting, Covering and   }
{ Plugging; those are 15 to 17, so the search has to reach 17.                 }

Function MechPairKindFromLayerKind(K : Integer) : Integer;
Var
    S, Base : String;
    P, I : Integer;
Begin
    Result := -1;
    S := MechKindToString(K);
    If S = 'Unknown' Then Exit;

    P := Pos(' Top', S);
    If P = 0 Then P := Pos(' Bottom', S);
    If P = 0 Then Exit;
    Base := Copy(S, 1, P - 1);

    For I := 0 To 17 Do
        If MechPairKindToString(I) = Base Then
        Begin
            Result := I;
            Exit;
        End;
End;
