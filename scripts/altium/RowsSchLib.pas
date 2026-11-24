{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ RowsSchLib.pas - rows kinds of a SchLib (docs/ROWS_CONTRACT.md).            }
{                                                                              }
{ pin: every pin of the symbols asked for, every part and display mode, in   }
{ one call; written back by id, each row guarded, all in one edit or none.   }
{ The shape follows RowsPcbLib.pas (pcblib / track).                          }
{                                                                              }
{ Scope: symbol = a name or wildcard pattern; default the symbol on screen.  }
{ THE SYMBOLS ARE FOUND BY WALKING THE LIBRARY, never by a by-name lookup:   }
{ LookupLibComponent falls back to RefreshSchLibFromDisk, which SAVES, closes }
{ and reopens the library. A first walk notes the address of every symbol   }
{ that matches; each is then fetched again by a walk that stops at that      }
{ address (as ScanLibForComponent does by name), so no pin iterator ever     }
{ runs inside the library iterator (the two-phase pattern of Library.pas).   }
{                                                                              }
{ Each pin row carries its symbol, part (OwnerPartId, 0 = shared by every    }
{ part) and display mode (OwnerPartDisplayMode, 0 = normal). Coordinates are }
{ the pin Location (its root on the body), from the symbol origin.           }
{                                                                              }
{ Every field but the coordinates, the length and the context goes through   }
{ GetSchProperty / SetSchProperty (Generic.pas), which hold the proven pin   }
{ member names, their typed casts and their guards.                          }
{..............................................................................}

{ The pin table. orientation in degrees (0 right, 90 up, 180 left, 270      }
{ down); electrical as PinElectricalToStr names it; colours as Altium packs }
{ them (BGR integer); functions = the pin functions in Altium's own name   }
{ form, "/"-separated (f1/f2/.../fn): ";" already separates a row's fields. }
Function RowsSchLibPinSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|symbol:s:r|part:i:r|mode:i:r'
        + '|designator:s:w|name:s:w|x:l:w|y:l:w|length:l:w|orientation:i:w'
        + '|electrical:s:w|description:s:w|hidden:b:w'
        + '|show_name:b:w|show_designator:b:w'
        + '|name_font_mode:i:w|name_font_id:i:w|name_color:i:w'
        + '|designator_font_mode:i:w|designator_font_id:i:w|designator_color:i:w'
        + '|color:i:w|functions:s:w';
End;

{ The GetSchProperty / SetSchProperty name of a field; empty for a field    }
{ read straight off the pin (id, symbol, part, mode).                       }
Function RowsSchLibPinProp(Field : String) : String;
Begin
    Result := '';
    If Field = 'designator' Then Result := 'Designator'
    Else If Field = 'name' Then Result := 'Name'
    Else If Field = 'x' Then Result := 'Location.X'
    Else If Field = 'y' Then Result := 'Location.Y'
    Else If Field = 'length' Then Result := 'PinLength'
    Else If Field = 'orientation' Then Result := 'Orientation'
    Else If Field = 'electrical' Then Result := 'Electrical'
    Else If Field = 'description' Then Result := 'Description'
    Else If Field = 'hidden' Then Result := 'IsHidden'
    Else If Field = 'show_name' Then Result := 'ShowName'
    Else If Field = 'show_designator' Then Result := 'ShowDesignator'
    Else If Field = 'name_font_mode' Then Result := 'Name_FontMode'
    Else If Field = 'name_font_id' Then Result := 'Name_CustomFontID'
    Else If Field = 'name_color' Then Result := 'Name_CustomColor'
    Else If Field = 'designator_font_mode' Then Result := 'Designator_FontMode'
    Else If Field = 'designator_font_id' Then Result := 'Designator_CustomFontID'
    Else If Field = 'designator_color' Then Result := 'Designator_CustomColor'
    Else If Field = 'color' Then Result := 'Color'
    Else If Field = 'functions' Then Result := 'DefinedFunctions';
End;

{ An electrical type in the names PinElectricalToStr gives, from any form   }
{ StrToPinElectrical takes; empty for a word it does not know (that one     }
{ would silently fall back to passive, so the writer refuses it instead).   }
Function RowsPinElecCanon(S : String) : String;
Var
    LS : String;
Begin
    LS := LowerCase(Trim(S));
    If Copy(LS, 1, 9) = 'eelectric' Then LS := Copy(LS, 10, Length(LS));
    LS := StringReplace(LS, '_', '', -1);
    Result := '';
    If (LS = 'input') Or (LS = 'in') Then Result := 'input'
    Else If (LS = 'output') Or (LS = 'out') Then Result := 'output'
    Else If (LS = 'io') Or (LS = 'bidir') Or (LS = 'bidirectional') Then Result := 'io'
    Else If LS = 'power' Then Result := 'power'
    Else If (LS = 'opencollector') Or (LS = 'oc') Then Result := 'open_collector'
    Else If (LS = 'openemitter') Or (LS = 'oe') Then Result := 'open_emitter'
    Else If (LS = 'hiz') Or (LS = 'tri') Or (LS = 'tristate') Then Result := 'hiz'
    Else If LS = 'passive' Then Result := 'passive';
End;

{ A function list in its "/" form: items trimmed, empty ones dropped        }
{ (" A / B//C " -> "A/B/C"). Main's reader gives ";" between functions, so  }
{ ";" is taken as a separator here too.                                     }
Function RowsSchLibFuncList(Value : String) : String;
Var
    Rest, Item : String;
    P : Integer;
Begin
    Result := '';
    Rest := StringReplace(Value, ';', '/', -1);
    While Rest <> '' Do
    Begin
        P := Pos('/', Rest);
        If P = 0 Then
        Begin
            Item := Rest;
            Rest := '';
        End
        Else
        Begin
            Item := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        Item := Trim(Item);
        If Item = '' Then Continue;
        If Result <> '' Then Result := Result + '/';
        Result := Result + Item;
    End;
End;

{ An orientation in degrees, turned into 0 .. 359 (-90 is 270). }
Function RowsSchLibOrient(Value : String) : Integer;
Var
    R : Integer;
Begin
    R := RowsIntOf(Value);
    Result := ((R Mod 360) + 360) Mod 360;
End;

{ The text SetSchProperty takes for a field: orientation as Altium's       }
{ ordinal 0..3, electrical by its own name, booleans lower case. x, y and  }
{ length are written straight onto the pin (RowsSchLibPinSet), in          }
{ coordinates, never through a decimal.                                    }
Function RowsSchLibPinSetValue(Field : String; Value : String; Units : String) : String;
Begin
    Result := Trim(Value);
    If Field = 'orientation' Then
        Result := IntToStr(RowsSchLibOrient(Value) Div 90)
    Else If Field = 'electrical' Then
        Result := RowsPinElecCanon(Value)
    Else If (Field = 'hidden') Or (Field = 'show_name') Or (Field = 'show_designator') Then
        Result := LowerCase(Trim(Value))
    Else If Field = 'functions' Then
        Result := RowsSchLibFuncList(Value);
End;

{ The order a row is written in: orientation first, the location last.    }
{ MEASURED (SetSchProperty note): a pin write of Location then Orientation }
{ in one bracket lost the orientation, Location re-lays the pin out.       }
Function RowsSchLibPinRank(Field : String) : Integer;
Begin
    If Field = 'orientation' Then Result := 0
    Else If (Field = 'x') Or (Field = 'y') Then Result := 2
    Else Result := 1;
End;

{ The writes of a row (fields S .. E - 1 of the flat table, not id, not    }
{ guards) as field indexes, in write order, row order kept within a rank.  }
{ The caller frees the list.                                               }
Function RowsSchLibPinOrder(Keys : TStringList; S : Integer; E : Integer) : TStringList;
Var
    F, Rank : Integer;
Begin
    Result := TStringList.Create;
    For Rank := 0 To 2 Do
        For F := S To E - 1 Do
            If RowsFlatWrites(Keys, F) Then
                If RowsSchLibPinRank(Keys.Strings[F]) = Rank Then
                    Result.Add(IntToStr(F));
End;

{ RowsSchLibPinOrder for one row given as text, as key TAB value lines (the }
{ tests read it this way). The caller frees the list.                      }
Function RowsSchLibPinWrites(Row : String) : TStringList;
Var
    Keys, Vals, Types, Order : TStringList;
    I, F : Integer;
Begin
    Result := TStringList.Create;
    Keys := TStringList.Create;
    Vals := TStringList.Create;
    Types := TStringList.Create;
    Try
        RowsFlatAdd(Row, Keys, Vals, Types);
        Order := RowsSchLibPinOrder(Keys, 0, Keys.Count);
        Try
            For I := 0 To Order.Count - 1 Do
            Begin
                F := StrToInt(Order.Strings[I]);
                Result.Add(Keys.Strings[F] + Chr(9) + Vals.Strings[F]);
            End;
        Finally
            Order.Free;
        End;
    Finally
        Keys.Free;  Vals.Free;  Types.Free;
    End;
End;

{ Whether a value read off a pin is the one a guard (Exact false) or a     }
{ write (Exact true) asked for. Electrical compares the type, whichever   }
{ name it was given by. A guard compares text ignoring case (the contract); }
{ a read-back compares text exactly, so a case-only rename that did not    }
{ take is caught.                                                          }
Function RowsSchLibPinSame(FieldType : String; Field : String; Have : String;
    Want : String; Exact : Boolean) : Boolean;
Begin
    If Field = 'electrical' Then
        Result := (Have <> '') And (RowsPinElecCanon(Have) = RowsPinElecCanon(Want))
    Else If Field = 'orientation' Then
        Result := RowsIntStr(Trim(Have)) And (RowsSchLibOrient(Have) = RowsSchLibOrient(Want))
    Else If Field = 'functions' Then
        Result := (Have <> '') And (RowsSchLibFuncList(Have) = RowsSchLibFuncList(Want))
    Else If Exact And (FieldType = 's') Then
        Result := Have = Trim(Want)
    Else
        Result := RowsCondHolds(FieldType, Have, '=', Want);
End;

{ One field of a library pin as text in the call unit; empty when it       }
{ cannot be read. Id is the pin's id from the walk (RowsSchLibPinId).     }
Function RowsSchLibPinGet(Pin : ISch_Pin; Field : String; Id : String;
    SymName : String; Units : String) : String;
Var
    V, Prop : String;
    Loc : TLocation;
Begin
    V := '';
    Try
        Case Field Of
            'id':          V := Id;
            'symbol':      V := SymName;
            'part':        V := IntToStr(Pin.OwnerPartId);
            'mode':        V := IntToStr(Pin.OwnerPartDisplayMode);
            'x':
                Begin
                    Loc := Pin.Location;
                    V := RowsLen(Loc.X, Units);
                End;
            'y':
                Begin
                    Loc := Pin.Location;
                    V := RowsLen(Loc.Y, Units);
                End;
            'length':      V := RowsLen(Pin.PinLength, Units);
            'orientation':
                Begin
                    Prop := GetSchProperty(Pin, 'Orientation');
                    If RowsIntStr(Trim(Prop)) Then V := IntToStr(RowsIntOr(Prop, 0) * 90);
                End;
            'functions':   V := RowsSchLibFuncList(GetSchProperty(Pin, 'DefinedFunctions'));
        Else
            Begin
                Prop := RowsSchLibPinProp(Field);
                If Prop <> '' Then V := GetSchProperty(Pin, Prop);
            End;
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ Write one field of a library pin through SetSchProperty, functions       }
{ inline. False when the name was not handled or the write raised.        }
{ FUNCTIONS: the pin's name is set to the list, Altium splits it into the  }
{ pin's functions (SetState_FunctionsFromName, the call                    }
{ Lib_SplitPinFunctions makes), and the name is put back -- the split      }
{ leaves the name spoiled. A trailing "/" goes on: MEASURED on AD 25.8.1   }
{ (bug 50) a name with no "/" leaves the list alone. The caller's          }
{ SchBeginModify / SchEndModify bracket covers it; the read-back checks it. }
Function RowsSchLibPinSet(Pin : ISch_Pin; Field : String; Value : String;
    Units : String) : Boolean;
Var
    Prop, OldName, List : String;
    Loc : TLocation;
Begin
    Result := False;
    If Field = 'functions' Then
    Begin
        List := RowsSchLibFuncList(Value);
        If List = '' Then Exit;
        OldName := Pin.Name;
        Try
            Try
                Pin.Name := List + '/';
                Pin.SetState_FunctionsFromName;
                Result := True;
            Except
                Result := False;
            End;
        Finally
            Pin.Name := OldName;
        End;
        Exit;
    End;
    { Coordinates go onto the pin as integers. A pin's Location is a copy:  }
    { read, patch, write back (as SetSchProperty does for a pin).            }
    If (Field = 'x') Or (Field = 'y') Or (Field = 'length') Then
    Begin
        Try
            If Field = 'length' Then
                Pin.PinLength := RowsCoord(Value, Units)
            Else
            Begin
                Loc := Pin.Location;
                If Field = 'x' Then Loc.X := RowsCoord(Value, Units)
                Else Loc.Y := RowsCoord(Value, Units);
                Pin.Location := Loc;
            End;
            Result := True;
        Except
            Result := False;
        End;
        Exit;
    End;
    Prop := RowsSchLibPinProp(Field);
    If Prop = '' Then Exit;
    Try
        Result := SetSchProperty(Pin, Prop, RowsSchLibPinSetValue(Field, Value, Units)) = 1;
    Except
        Result := False;
    End;
End;

{ A pin's id: its symbol's position in the library walk, a colon, and its }
{ position among that symbol's pins (every part and display mode), both    }
{ from 1. MEASURED (AD 25.8, three times on 2026-11-10): a schematic       }
{ object's I_ObjectAddress cannot be turned into text -- IntToStr of it,   }
{ even through an Integer local, stopped the script ("Could not convert    }
{ variant of type (Dispatch) into type (Boolean)"). Main only ever COMPARES }
{ those addresses. The position holds while no symbol or pin is added or   }
{ removed; a write is guarded by ?symbol (and any field), so a stale id is }
{ refused, never written to another pin unchecked.                          }
Function RowsSchLibPinId(SymPos : String; PinPos : Integer) : String;
Begin
    Result := SymPos + ':' + IntToStr(PinPos);
End;

{ The library symbol at a walk position (1 = first), fetched by a walk of  }
{ the library (no by-name lookup, see the head of this unit). Nil when the }
{ library has fewer symbols.                                               }
Function RowsSchLibSymbolAt(SchLib : ISch_Lib; Pos : String) : ISch_Component;
Var
    Iter : ISch_Iterator;
    Comp : ISch_Component;
    Want, N : Integer;
Begin
    Result := Nil;
    Want := RowsIntOr(Pos, 0);
    If (SchLib = Nil) Or (Want < 1) Then Exit;
    Iter := SchLib.SchLibIterator_Create;
    If Iter = Nil Then Exit;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        N := 0;
        Comp := Iter.FirstSchObject;
        While Comp <> Nil Do
        Begin
            Inc(N);
            If N = Want Then
            Begin
                Result := Comp;
                Break;
            End;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchLib.SchIterator_Destroy(Iter);
    End;
End;

{ The symbols a call works on, by one walk of the library: Addrs gets each }
{ one's walk position (counting every symbol) and Names its LibReference,  }
{ aligned, in library order. Pattern empty = the symbol on screen; else a  }
{ name or wildcard pattern (case ignored). With UseKeys, Keys = exact      }
{ names upper cased (the write: the ?symbol of every row) replaces the     }
{ pattern. Returns the position of the symbol on screen, empty when none.  }
Function RowsSchLibSymbols(SchLib : ISch_Lib; Pattern : String; UseKeys : Boolean;
    Keys : TStringList; Addrs : TStringList; Names : TStringList) : String;
Var
    Iter : ISch_Iterator;
    Comp, Cur : ISch_Component;
    A, N : String;
    Take, IsCur : Boolean;
    Pos : Integer;
Begin
    Result := '';
    Cur := Nil;
    Try Cur := SchLib.CurrentSchComponent; Except Cur := Nil; End;
    Iter := SchLib.SchLibIterator_Create;
    If Iter = Nil Then Exit;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Pos := 0;
        Comp := Iter.FirstSchObject;
        While Comp <> Nil Do
        Begin
            Inc(Pos);
            A := IntToStr(Pos);
            N := '';
            Try N := Comp.LibReference; Except End;
            { The symbol on screen: its address only COMPARED, exactly as     }
            { Lib_CopyComponent does (see RowsSchLibPinId).                   }
            IsCur := False;
            If Cur <> Nil Then
                IsCur := (Cur.I_ObjectAddress = Comp.I_ObjectAddress);
            If IsCur Then Result := A;
            If UseKeys Then Take := Keys.IndexOf(UpperCase(N)) >= 0
            Else If Pattern = '' Then Take := IsCur
            Else Take := FilterWildMatch(N, Pattern);
            If Take And (Addrs.IndexOf(A) < 0) Then
            Begin
                Addrs.Add(A);
                Names.Add(N + Chr(1));
            End;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchLib.SchIterator_Destroy(Iter);
    End;
End;

{..............................................................................}
{ rows.read_rows doc=schlib kind=pin                                           }
{..............................................................................}

Function RowsSchLibPinRead(Params : String; RequestId : String) : String;
Var
    CellCache : TStringList;
    Spec, Units, LibPath, Problem, SymPattern, SymName, Addr, Line, F, Have, Want : String;
    CurAddr : String;
    SchLib : ISch_Lib;
    Comp : ISch_Component;
    PinIter : ISch_Iterator;
    Pin : ISch_Pin;
    Fields, Types, Conds, Lines, Seen, CF, CO, CV, CT, Addrs, Names : TStringList;
    Offset, Limit, Matched, Scanned, I, C, PinPos : Integer;
    Keep, More, Stop : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    Spec := RowsSchLibPinSpec(0);
    SymPattern := Trim(ExtractJsonValue(Params, 'symbol'));
    Offset := RowsIntOr(ExtractJsonValue(Params, 'offset'), 0);
    If Offset < 0 Then Offset := 0;
    Limit := RowsIntOr(ExtractJsonValue(Params, 'limit'), 5000);
    If Limit < 0 Then Limit := 0;

    Fields := RowsParseFields(Spec, ExtractJsonValue(Params, 'fields'), Problem);
    Types := RowsFieldTypes(Spec, Fields);
    CellCache := RowsCellCache(Fields.Count);
    Conds := Nil;
    CF := Nil;  CO := Nil;  CV := Nil;  CT := Nil;
    Lines := TStringList.Create;
    Seen := TStringList.Create;
    Seen.Sorted := True;
    Addrs := TStringList.Create;
    Names := TStringList.Create;
    Try
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_FIELD', Problem);
            Exit;
        End;
        Conds := RowsCompileWhere(Spec, ExtractJsonValue(Params, 'where'), Problem);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_WHERE', Problem);
            Exit;
        End;
        CF := RowsColumn(Conds, 0);
        CO := RowsColumn(Conds, 1);
        CV := RowsColumn(Conds, 2);
        CT := RowsColumn(Conds, 3);
        LibPath := ExtractJsonValue(Params, 'library_path');
        SchLib := FocusSchLib(LibPath);
        If SchLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
                'No schematic library is active and library_path did not resolve');
            Exit;
        End;

        { Seen is still empty here: it only stands in for the unused key list. }
        CurAddr := RowsSchLibSymbols(SchLib, SymPattern, False, Seen, Addrs, Names);
        If (SymPattern = '') And (Addrs.Count = 0) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SYMBOL',
                'No symbol is on screen in this library; pass symbol= (a name or pattern)');
            Exit;
        End;

        Matched := 0;
        Scanned := 0;
        More := False;
        Stop := False;
        C := 0;
        While (C < Addrs.Count) And (Not Stop) Do
        Begin
            Comp := RowsSchLibSymbolAt(SchLib, Addrs.Strings[C]);
            SymName := RowsCol(Names, C);
            If Comp <> Nil Then
            Begin
                PinIter := Comp.SchIterator_Create;
                Try
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    PinPos := 0;
                    Pin := PinIter.FirstSchObject;
                    While (Pin <> Nil) And (Not Stop) Do
                    Begin
                        Inc(PinPos);
                        Addr := RowsSchLibPinId(Addrs.Strings[C], PinPos);
                        If Seen.IndexOf(Addr) < 0 Then
                        Begin
                            Seen.Add(Addr);
                            Inc(Scanned);
                            Keep := True;
                            I := 0;
                            While Keep And (I < Conds.Count) Do
                            Begin
                                F := RowsCol(CF, I);
                                Have := RowsSchLibPinGet(Pin, F, Addr, SymName, Units);
                                Want := RowsCol(CV, I);
                                { electrical in any of its names: compare the type }
                                If (F = 'electrical') And (RowsPinElecCanon(Want) <> '') Then
                                    Want := RowsPinElecCanon(Want);
                                Keep := RowsCondHolds(RowsCol(CT, I), Have,
                                    RowsCol(CO, I), Want);
                                Inc(I);
                            End;
                            If Keep Then
                            Begin
                                Inc(Matched);
                                If Matched > Offset Then
                                Begin
                                    If (Limit > 0) And (Lines.Count >= Limit) Then
                                    Begin
                                        More := True;
                                        Stop := True;
                                    End
                                    Else
                                    Begin
                                        Line := '';
                                        For I := 0 To Fields.Count - 1 Do
                                        Begin
                                            F := Fields.Strings[I];
                                            If Line <> '' Then Line := Line + ',';
                                            Line := Line + RowsCellCached(Types.Strings[I],
                                                RowsSchLibPinGet(Pin, F, Addr, SymName, Units), I, CellCache);
                                        End;
                                        Lines.Add('[' + Line + ']');
                                    End;
                                End;
                            End;
                        End;
                        If Not Stop Then Pin := PinIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PinIter);
                End;
            End;
            Inc(C);
        End;

        Result := BuildSuccessResponse(RequestId,
            RowsReadReply('schlib', 'pin', Units, Fields, Lines, Offset, More,
                Scanned, ''));
    Finally
        CellCache.Free;
        Fields.Free;
        Types.Free;
        If Conds <> Nil Then Conds.Free;
        If CF <> Nil Then CF.Free;
        If CO <> Nil Then CO.Free;
        If CV <> Nil Then CV.Free;
        If CT <> Nil Then CT.Free;
        Lines.Free;
        Seen.Free;
        Addrs.Free;
        Names.Free;
    End;
End;

{..............................................................................}
{ rows.write_rows doc=schlib kind=pin                                          }
{ Every row needs ?symbol=, which also limits the walks to those symbols.     }
{ Two passes: find and guard every row, then write inside ONE edit, each pin  }
{ in its own SCHM_BeginModify / EndModify, every written field read back.    }
{..............................................................................}

Function RowsSchLibPinWrite(Params : String; RequestId : String) : String;
Var
    RS, RE, FKeys, FVals, FTypes : TStringList;
    S, E, F : Integer;
    Spec, Units, LibPath, Problem, Code, SymName, Addr : String;
    Key, Name, Value, Have, Notes, Rec, CurAddr : String;
    SchLib : ISch_Lib;
    Comp : ISch_Component;
    PinIter : ISch_Iterator;
    Pin : ISch_Pin;
    Ids, Texts, Hits, SymKeys, Addrs, Names, Seen, Done, Results, Writes : TStringList;
    K, I, C, Written, Failed, Repeats, Pass, PinPos : Integer;
    Ok : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    Spec := RowsSchLibPinSpec(0);

    RS := TStringList.Create;
    RE := TStringList.Create;
    FKeys := TStringList.Create;
    FVals := TStringList.Create;
    FTypes := TStringList.Create;
    Ids := TStringList.Create;
    Texts := TStringList.Create;
    Hits := TStringList.Create;
    SymKeys := TStringList.Create;
    Addrs := TStringList.Create;
    Names := TStringList.Create;
    Seen := TStringList.Create;
    Done := TStringList.Create;
    Results := TStringList.Create;
    Seen.Sorted := True;
    Done.Sorted := True;
    Try
        Problem := RowsTakeRows(Spec, Params, 'symbol',
            Ids, Texts, RS, RE, FKeys, FVals, FTypes, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        For K := 0 To Ids.Count - 1 Do
        Begin
            Hits.Add('0');
            SymName := UpperCase(RowsFlatValue(FKeys, FVals, StrToInt(RS.Strings[K]),
                StrToInt(RE.Strings[K]), '?symbol'));
            If SymKeys.IndexOf(SymName) < 0 Then SymKeys.Add(SymName);
        End;

        { The values of a row are checked by rows.py before it is sent        }
        { (rows_rules.py: the only way in); only what needs the document is  }
        { checked here.                                                      }

        LibPath := ExtractJsonValue(Params, 'library_path');
        SchLib := FocusSchLib(LibPath);
        If SchLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
                'No schematic library is active and library_path did not resolve');
            Exit;
        End;
        CurAddr := RowsSchLibSymbols(SchLib, '', True, SymKeys, Addrs, Names);

        { Pass 1 finds every row pin and checks its guards; pass 2 writes. }
        Notes := '';
        Written := 0;
        Failed := 0;
        Repeats := 0;
        For Pass := 1 To 2 Do
        Begin
            If Pass = 1 Then RowsProfMark('open') Else RowsProfMark('find');
            If Pass = 2 Then
            Begin
                For K := 0 To Ids.Count - 1 Do
                    If Hits.Strings[K] = '0' Then
                        Notes := Notes + 'row ' + Ids.Strings[K] + ': no pin with that id in '
                            + RowsFlatValue(FKeys, FVals, StrToInt(RS.Strings[K]),
                StrToInt(RE.Strings[K]), '?symbol') + ';';
                If Notes <> '' Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                        Notes + ' nothing was changed; read the pins again');
                    Exit;
                End;
                SchBeginEdit(SchLib, '');
            End;
            Try
                For C := 0 To Addrs.Count - 1 Do
                Begin
                    Comp := RowsSchLibSymbolAt(SchLib, Addrs.Strings[C]);
                    If Comp = Nil Then Continue;
                    SymName := RowsCol(Names, C);
                    PinIter := Comp.SchIterator_Create;
                    Try
                        PinIter.AddFilter_ObjectSet(MkSet(ePin));
                        PinPos := 0;
                        Pin := PinIter.FirstSchObject;
                        While Pin <> Nil Do
                        Begin
                            Inc(PinPos);
                            Addr := RowsSchLibPinId(Addrs.Strings[C], PinPos);
                            K := Ids.IndexOf(Addr);
                            If K >= 0 Then
                            Begin
                                S := StrToInt(RS.Strings[K]);
                                E := StrToInt(RE.Strings[K]);
                                If Pass = 1 Then
                                Begin
                                    If Seen.IndexOf(Addr) < 0 Then
                                    Begin
                                        Seen.Add(Addr);
                                        Hits.Strings[K] := '1';
                                        S := StrToInt(RS.Strings[K]);
                                        E := StrToInt(RE.Strings[K]);
                                        For F := S To E - 1 Do
                                        Begin
                                            Key := FKeys.Strings[F];
                                            If Copy(Key, 1, 1) <> '?' Then Continue;
                                            Name := Copy(Key, 2, Length(Key));
                                            Value := FVals.Strings[F];
                                            Have := RowsSchLibPinGet(Pin, Name, Addr, SymName, Units);
                                            If Not RowsSchLibPinSame(FTypes.Strings[F],
                                                Name, Have, Value, False) Then
                                                Notes := Notes + 'row ' + Addr + ': '
                                                    + Name + ' is ' + Have + ', not '
                                                    + Value + ';';
                                        End;
                                    End;
                                End
                                Else If Done.IndexOf(Addr) >= 0 Then
                                    Inc(Repeats)
                                Else
                                Begin
                                    Done.Add(Addr);
                                    Ok := True;
                                    Rec := '';
                                    Writes := RowsSchLibPinOrder(FKeys, S, E);
                                    Try
                                        SchBeginModify(Pin);
                                        For I := 0 To Writes.Count - 1 Do
                                        Begin
                                            F := StrToInt(Writes.Strings[I]);
                                            Key := FKeys.Strings[F];
                                            Value := FVals.Strings[F];
                                            If Not RowsSchLibPinSet(Pin, Key, Value, Units) Then
                                            Begin
                                                Ok := False;
                                                Notes := Notes + 'row ' + Addr + ': ' + Key
                                                    + ' was not written;';
                                            End;
                                        End;
                                        SchEndModify(Pin);
                                        { After the bracket, as the generic modify does. }
                                        RefreshSchObjectRender(Pin);
                                        { Read every written field back. }
                                        For I := 0 To Writes.Count - 1 Do
                                        Begin
                                            F := StrToInt(Writes.Strings[I]);
                                            Key := FKeys.Strings[F];
                                            Value := FVals.Strings[F];
                                            Have := RowsSchLibPinGet(Pin, Key, Addr, SymName, Units);
                                            If Not RowsSchLibPinSame(FTypes.Strings[F],
                                                Key, Have, Value, True) Then
                                            Begin
                                                Ok := False;
                                                Notes := Notes + 'row ' + Addr + ': ' + Key
                                                    + ' reads ' + Have + ' after writing '
                                                    + Value + ';';
                                            End;
                                            Rec := Rec + ',"' + Key + '":'
                                                + RowsCell(FTypes.Strings[F], Have);
                                        End;
                                    Finally
                                        Writes.Free;
                                    End;
                                    If Not Ok Then Inc(Failed);
                                    Inc(Written);
                                    Results.Add('{"id":"' + Addr + '","ok":'
                                        + BoolToJsonStr(Ok) + Rec + '}');
                                End;
                            End;
                            Pin := PinIter.NextSchObject;
                        End;
                    Finally
                        Comp.SchIterator_Destroy(PinIter);
                    End;
                End;
            Finally
                If Pass = 2 Then SchEndEdit(SchLib, 'Edit');
            End;
            If (Pass = 1) And (Notes <> '') Then
            Begin
                Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                    Notes + ' nothing was changed; read the pins again');
                Exit;
            End;
        End;
        MarkLibDirty(SchLib);

        Result := BuildSuccessResponse(RequestId,
            RowsWriteReply('schlib', 'pin', Ids.Count, Written, Failed, Repeats,
                Results, Notes));
    Finally
        RS.Free;  RE.Free;  FKeys.Free;  FVals.Free;  FTypes.Free;
        Ids.Free;  Texts.Free;  Hits.Free;  SymKeys.Free;  Addrs.Free;  Names.Free;
        Seen.Free;  Done.Free;  Results.Free;
    End;
End;

{ UnitStamp_RowsSchLib - which build of RowsSchLib.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_RowsSchLib(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
