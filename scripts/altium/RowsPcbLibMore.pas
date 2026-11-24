{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ RowsPcbLibMore.pas - more rows kinds of a PcbLib (docs/ROWS_CONTRACT.md):    }
{ arc and text, on the shape of RowsPcbLib.pas (track).                        }
{                                                                              }
{ Scope: footprint = a name or wildcard pattern (default every footprint);    }
{ layer = one layer, resolved once. Coordinates are from each footprints     }
{ reference point (Footprint.X / Y), as every library reader gives them.      }
{                                                                              }
{ DelphiScript has no procedure values, so the two kinds share ONE read and   }
{ ONE write, told the kind by name: only the iterators object set, the field }
{ table, the getter, the value check and the setter differ, and each of those }
{ is a small dispatcher over the kinds own routine.                          }
{                                                                              }
{ TEXTS. text = the text as stored (UnderlyingString): a special string such  }
{ as .Designator or .Comment stays that, not what it shows; shown = what      }
{ Altium draws (Text). special = the stored text starts with a dot. A write  }
{ of text over a special string with a plain one would cut that link: it is  }
{ refused in the guard pass unless the new text is itself special.           }
{ Mirroring flips the text about its anchor (x / y), as MirrorFlag does: the }
{ anchor is the field the row names, and it does not move.                   }
{..............................................................................}

{ The arc table. x / y = the centre; angles in degrees, counter-clockwise.   }
{ sweep and length are read-only conveniences (length along the centreline).}
Function RowsPcbLibArcSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|footprint:s:r|description:s:r|layer:s:w|layer_name:s:r'
        + '|x:l:w|y:l:w|radius:l:w|start_angle:a:w|end_angle:a:w|width:l:w'
        + '|sweep:a:r|length:l:r|keepout:b:r|selected:b:r';
End;

{ The text table. height = Altiums Size, stroke_width = its Width (stroke }
{ texts); text_kind 0 stroke / 1 TrueType / 2 barcode; font_id 1 Default /  }
{ 2 Sans Serif / 3 Serif (stroke fonts).                                    }
Function RowsPcbLibTextSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|footprint:s:r|description:s:r|layer:s:w|layer_name:s:r'
        + '|text:s:w|shown:s:r|special:b:r|x:l:w|y:l:w|height:l:w'
        + '|stroke_width:l:w|rotation:a:w|mirrored:b:w|text_kind:i:r'
        + '|font_id:i:r|font_name:s:r|bold:b:r|italic:b:r|multiline:b:r'
        + '|inverted:b:r|is_designator:b:r|is_comment:b:r'
        + '|keepout:b:r|selected:b:r';
End;

{ The table of a kind of this unit;  for a kind it does not build. }
Function RowsPcbLibMoreSpec(Kind : String) : String;
Begin
    Result := '';
    If Kind = 'arc' Then Result := RowsPcbLibArcSpec(0)
    Else If Kind = 'text' Then Result := RowsPcbLibTextSpec(0);
End;

{ An arcs length along its centreline, in the radius own units. }
Function RowsArcLength(Radius : Double; StartAngle : Double; EndAngle : Double) : Double;
Begin
    Result := Radius * RowsArcSweep(StartAngle, EndAngle) * 3.14159265358979 / 180.0;
End;

{ Whether a stored text is a special string (.Designator, .Comment ...). }
Function RowsTextIsSpecial(Text : String) : Boolean;
Begin
    Result := Copy(Trim(Text), 1, 1) = '.';
End;

{ Why a text write may not replace Have (the stored text) with Want:  when }
{ it may. A special string is only replaced by another special string.      }
Function RowsTextWriteProblem(Have : String; Want : String) : String;
Begin
    Result := '';
    If RowsTextIsSpecial(Have) And (Not RowsTextIsSpecial(Want)) Then
        Result := 'text is the special string ' + Have + '; writing "' + Want
            + '" would unlink it (guard with ?special=false to skip such texts)';
End;

{ One field of a library arc as text in the calls unit;  when it }
{ cannot be read.                                                  }
Function RowsPcbLibArcGet(Arc : IPCB_Arc; Field : String; FpName : String;
    FpDesc : String; XOrg : Integer; YOrg : Integer; Units : String;
    Board : IPCB_Board) : String;
Var
    V : String;
Begin
    V := '';
    Try
        Case Field Of
            'id':          V := IntToStr(Arc.I_ObjectAddress);
            'footprint':   V := FpName;
            'description': V := FpDesc;
            'layer':       V := LayerDisplayName(Arc.Layer);
            'layer_name':  V := RowsLayerUserName(Board, Arc.Layer);
            'x':           V := RowsLen(Arc.XCenter - XOrg, Units);
            'y':           V := RowsLen(Arc.YCenter - YOrg, Units);
            'radius':      V := RowsLen(Arc.Radius, Units);
            'start_angle': V := RowsAngle(Arc.StartAngle);
            'end_angle':   V := RowsAngle(Arc.EndAngle);
            'width':       V := RowsLen(Arc.LineWidth, Units);
            'sweep':       V := RowsAngle(RowsArcSweep(Arc.StartAngle, Arc.EndAngle));
            'length':      V := RowsLen(RowsArcLength(Arc.Radius, Arc.StartAngle,
                               Arc.EndAngle), Units);
            'keepout':     V := BoolToJsonStr(Arc.IsKeepout);
            'selected':    V := BoolToJsonStr(Arc.Selected);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ One field of a library text as text in the calls unit;  when it }
{ cannot be read.                                                   }
Function RowsPcbLibTextGet(Txt : IPCB_Text; Field : String; FpName : String;
    FpDesc : String; XOrg : Integer; YOrg : Integer; Units : String;
    Board : IPCB_Board) : String;
Var
    V : String;
Begin
    V := '';
    Try
        Case Field Of
            'id':            V := IntToStr(Txt.I_ObjectAddress);
            'footprint':     V := FpName;
            'description':   V := FpDesc;
            'layer':         V := LayerDisplayName(Txt.Layer);
            'layer_name':    V := RowsLayerUserName(Board, Txt.Layer);
            'text':          V := Txt.UnderlyingString;
            'shown':         V := Txt.Text;
            'special':       V := BoolToJsonStr(RowsTextIsSpecial(Txt.UnderlyingString));
            'x':             V := RowsLen(Txt.XLocation - XOrg, Units);
            'y':             V := RowsLen(Txt.YLocation - YOrg, Units);
            'height':        V := RowsLen(Txt.Size, Units);
            'stroke_width':  V := RowsLen(Txt.Width, Units);
            'rotation':      V := RowsAngle(Txt.Rotation);
            'mirrored':      V := BoolToJsonStr(Txt.MirrorFlag);
            'text_kind':     V := IntToStr(Txt.TextKind);
            'font_id':       V := IntToStr(Txt.FontID);
            'font_name':     V := Txt.FontName;
            'bold':          V := BoolToJsonStr(Txt.Bold);
            'italic':        V := BoolToJsonStr(Txt.Italic);
            'multiline':     V := BoolToJsonStr(Txt.Multiline);
            'inverted':      V := BoolToJsonStr(Txt.Inverted);
            'is_designator': V := BoolToJsonStr(Txt.IsDesignator);
            'is_comment':    V := BoolToJsonStr(Txt.IsComment);
            'keepout':       V := BoolToJsonStr(Txt.IsKeepout);
            'selected':      V := BoolToJsonStr(Txt.Selected);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ The getter of a kind of this unit, on the walks untyped primitive. }
Function RowsPcbLibMoreGet(Prim : IPCB_Primitive; Kind : String; Field : String;
    FpName : String; FpDesc : String; XOrg : Integer; YOrg : Integer;
    Units : String; Board : IPCB_Board) : String;
Var
    Arc : IPCB_Arc;
    Txt : IPCB_Text;
Begin
    If Kind = 'arc' Then
    Begin
        Arc := Prim;
        Result := RowsPcbLibArcGet(Arc, Field, FpName, FpDesc, XOrg, YOrg, Units, Board);
    End
    Else
    Begin
        Txt := Prim;
        Result := RowsPcbLibTextGet(Txt, Field, FpName, FpDesc, XOrg, YOrg, Units, Board);
    End;
End;

{ Write one field of a library arc. Layer is the resolved layer for a  }
{ layer write. False when the write raised.                          }
Function RowsPcbLibArcSet(Arc : IPCB_Arc; Field : String; Value : String;
    XOrg : Integer; YOrg : Integer; Units : String; Layer : TLayer) : Boolean;
Begin
    Result := True;
    Try
        Case Field Of
            'layer':       Arc.Layer := Layer;
            'x':           Arc.XCenter := XOrg + RowsCoord(Value, Units);
            'y':           Arc.YCenter := YOrg + RowsCoord(Value, Units);
            'radius':      Arc.Radius := RowsCoord(Value, Units);
            'start_angle': Arc.StartAngle := RowsAngleDeg(Value);
            'end_angle':   Arc.EndAngle := RowsAngleDeg(Value);
            'width':       Arc.LineWidth := RowsCoord(Value, Units);
        Else
            Result := False;
        End;
    Except
        Result := False;
    End;
End;

{ Write one field of a library text, the members the designator writer of }
{ Library.pas uses inside the same robots bracket. False when it raised.   }
Function RowsPcbLibTextSet(Txt : IPCB_Text; Field : String; Value : String;
    XOrg : Integer; YOrg : Integer; Units : String; Layer : TLayer) : Boolean;
Begin
    Result := True;
    Try
        Case Field Of
            'layer':        Txt.Layer := Layer;
            'text':         Txt.Text := Value;
            'x':            Txt.XLocation := XOrg + RowsCoord(Value, Units);
            'y':            Txt.YLocation := YOrg + RowsCoord(Value, Units);
            'height':       Txt.Size := RowsCoord(Value, Units);
            'stroke_width': Txt.Width := RowsCoord(Value, Units);
            'rotation':     Txt.Rotation := RowsAngleDeg(Value);
            'mirrored':     Txt.MirrorFlag := LowerCase(Trim(Value)) = 'true';
        Else
            Result := False;
        End;
    Except
        Result := False;
    End;
End;

{ The setter of a kind of this unit, on the walks untyped primitive. }
Function RowsPcbLibMoreSet(Prim : IPCB_Primitive; Kind : String; Field : String;
    Value : String; XOrg : Integer; YOrg : Integer; Units : String;
    Layer : TLayer) : Boolean;
Var
    Arc : IPCB_Arc;
    Txt : IPCB_Text;
Begin
    If Kind = 'arc' Then
    Begin
        Arc := Prim;
        Result := RowsPcbLibArcSet(Arc, Field, Value, XOrg, YOrg, Units, Layer);
    End
    Else
    Begin
        Txt := Prim;
        Result := RowsPcbLibTextSet(Txt, Field, Value, XOrg, YOrg, Units, Layer);
    End;
End;

{ A texts cached bounding box is not renewed by a write to its location or }
{ size (see the designator writer in Library.pas): invalidate it.          }
Procedure RowsPcbLibMoreRefresh(Prim : IPCB_Primitive; Kind : String);
Var
    Txt : IPCB_Text;
Begin
    If Kind <> 'text' Then Exit;
    Try
        Txt := Prim;
        Txt.GraphicallyInvalidate;
    Except
    End;
End;

{..............................................................................}
{ rows.read_rows doc=pcblib kind=arc / text                                    }
{..............................................................................}

Function RowsPcbLibMoreRead(Params : String; RequestId : String; Kind : String) : String;
Var
    CellCache : TStringList;
    Spec, Units, LibPath, UsedPath, Problem, FpPattern, LayerStr, FpName, FpDesc : String;
    Addr, Line, F, Have : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Fields, Types, Conds, Lines, Seen, CF, CO, CV, CT : TStringList;
    LayerId : TLayer;
    XOrg, YOrg, Offset, Limit, Matched, Scanned, I : Integer;
    Keep, More, Stop : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    Spec := RowsPcbLibMoreSpec(Kind);
    FpPattern := Trim(ExtractJsonValue(Params, 'footprint'));
    If FpPattern = '' Then FpPattern := '*';
    LayerStr := Trim(ExtractJsonValue(Params, 'layer'));
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
        PcbLib := RowsOpenPcbLib(LibPath, UsedPath, Problem);
        If PcbLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', Problem);
            Exit;
        End;
        LayerId := eNoLayer;
        If LayerStr <> '' Then
        Begin
            LayerId := ResolveLayerId(PcbLib.Board, LayerStr);
            If LayerId = eNoLayer Then
            Begin
                Result := BuildErrorResponse(RequestId, 'BAD_LAYER',
                    'No layer named "' + LayerStr + '" in this library');
                Exit;
            End;
        End;

        Matched := 0;
        Scanned := 0;
        More := False;
        Stop := False;
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While (Footprint <> Nil) And (Not Stop) Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;
                If FilterWildMatch(FpName, FpPattern) Then
                Begin
                    FpDesc := '';
                    Try FpDesc := Footprint.Description; Except End;
                    XOrg := 0;  YOrg := 0;
                    Try XOrg := Footprint.X; Except End;
                    Try YOrg := Footprint.Y; Except End;
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        If Kind = 'arc' Then
                            GrpIter.AddFilter_ObjectSet(MkSet(eArcObject))
                        Else
                            GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                        Prim := GrpIter.FirstPCBObject;
                        While (Prim <> Nil) And (Not Stop) Do
                        Begin
                            Addr := '';
                            Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
                            If (Addr <> '') And (Seen.IndexOf(Addr) < 0) Then
                            Begin
                                Seen.Add(Addr);
                                Inc(Scanned);
                                Keep := True;
                                If LayerStr <> '' Then Keep := Prim.Layer = LayerId;
                                I := 0;
                                While Keep And (I < Conds.Count) Do
                                Begin
                                    Have := RowsPcbLibMoreGet(Prim, Kind, RowsCol(CF, I),
                                        FpName, FpDesc, XOrg, YOrg, Units, PcbLib.Board);
                                    Keep := RowsCondHolds(RowsCol(CT, I), Have,
                                        RowsCol(CO, I), RowsCol(CV, I));
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
                                                    RowsPcbLibMoreGet(Prim, Kind, F, FpName,
                                                    FpDesc, XOrg, YOrg, Units, PcbLib.Board), I, CellCache);
                                            End;
                                            Lines.Add('[' + Line + ']');
                                        End;
                                    End;
                                End;
                            End;
                            If Not Stop Then Prim := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                End;
                If Not Stop Then Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;

        Result := BuildSuccessResponse(RequestId,
            RowsReadReply('pcblib', Kind, Units, Fields, Lines, Offset, More,
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
    End;
End;

{..............................................................................}
{ rows.write_rows doc=pcblib kind=arc / text                                   }
{ Every row needs ?footprint=, which also limits the walks to those           }
{ footprints. Two walks, as the track writer: find and guard, then write.     }
{..............................................................................}

Function RowsPcbLibMoreWrite(Params : String; RequestId : String; Kind : String) : String;
Var
    RS, RE, FKeys, FVals, FTypes : TStringList;
    S, E, F : Integer;
    Spec, Units, LibPath, UsedPath, Problem, Code, FpName, FpDesc, Addr : String;
    Key, Name, Value, Have, Notes, Rec, LayerCache, Current : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Ids, Texts, Hits, FpNames, Seen, Done, Results : TStringList;
    LayerId : TLayer;
    XOrg, YOrg, K, I, Written, Failed, Repeats, Pass : Integer;
    Ok : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    Spec := RowsPcbLibMoreSpec(Kind);

    RS := TStringList.Create;
    RE := TStringList.Create;
    FKeys := TStringList.Create;
    FVals := TStringList.Create;
    FTypes := TStringList.Create;
    Ids := TStringList.Create;
    Texts := TStringList.Create;
    Hits := TStringList.Create;
    FpNames := TStringList.Create;
    Seen := TStringList.Create;
    Done := TStringList.Create;
    Results := TStringList.Create;
    Seen.Sorted := True;
    Done.Sorted := True;
    Try
        Problem := RowsTakeRows(Spec, Params, 'footprint',
            Ids, Texts, RS, RE, FKeys, FVals, FTypes, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        For K := 0 To Ids.Count - 1 Do
        Begin
            Hits.Add('0');
            FpName := RowsFlatValue(FKeys, FVals, StrToInt(RS.Strings[K]),
                StrToInt(RE.Strings[K]), '?footprint');
            If FpNames.IndexOf(FpName) < 0 Then FpNames.Add(FpName);
        End;

        LibPath := ExtractJsonValue(Params, 'library_path');
        PcbLib := RowsOpenPcbLib(LibPath, UsedPath, Problem);
        If PcbLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', Problem);
            Exit;
        End;

        { Layers that exist: the document's own. Every other value of a  }
        { row is checked by rows.py before it is sent (rows_rules.py).    }
        LayerCache := '';
        For K := 0 To Ids.Count - 1 Do
        Begin
            S := StrToInt(RS.Strings[K]);
            E := StrToInt(RE.Strings[K]);
            For F := S To E - 1 Do
            Begin
                Key := FKeys.Strings[F];
                Value := FVals.Strings[F];
                If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;
                Problem := '';
                If (Problem = '') And (Key = 'layer') Then
                    If ResolveLayerIdCached(PcbLib.Board, Value, LayerCache) = eNoLayer Then
                        Problem := 'no layer named "' + Value + '"';
                If Problem <> '' Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'BAD_VALUE',
                        'row ' + Ids.Strings[K] + ': ' + Problem + '; nothing was changed');
                    Exit;
                End;
            End;
        End;

        { Pass 1 finds every rows object and checks its guards; pass 2 writes. }
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
                        Notes := Notes + 'row ' + Ids.Strings[K] + ': no ' + Kind
                            + ' with that id in ' + RowsFlatValue(FKeys, FVals, StrToInt(RS.Strings[K]),
                StrToInt(RE.Strings[K]), '?footprint') + ';';
                If Notes <> '' Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                        Notes + ' nothing was changed; read the ' + Kind + 's again');
                    Exit;
                End;
                PcbBeginEdit(0);
            End;
            Try
                Iter := PcbLib.LibraryIterator_Create;
                Try
                    Footprint := Iter.FirstPCBObject;
                    While Footprint <> Nil Do
                    Begin
                        FpName := '';
                        Try FpName := Footprint.Name; Except End;
                        If FpNames.IndexOf(FpName) >= 0 Then
                        Begin
                            FpDesc := '';
                            Try FpDesc := Footprint.Description; Except End;
                            XOrg := 0;  YOrg := 0;
                            Try XOrg := Footprint.X; Except End;
                            Try YOrg := Footprint.Y; Except End;
                            GrpIter := Footprint.GroupIterator_Create;
                            Try
                                If Kind = 'arc' Then
                                    GrpIter.AddFilter_ObjectSet(MkSet(eArcObject))
                                Else
                                    GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                                Prim := GrpIter.FirstPCBObject;
                                While Prim <> Nil Do
                                Begin
                                    Addr := '';
                                    Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
                                    K := -1;
                                    If Addr <> '' Then K := Ids.IndexOf(Addr);
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
                                                    Value := FVals.Strings[F];
                                                    { A text write over a special string. }
                                                    If (Kind = 'text') And (Key = 'text') Then
                                                    Begin
                                                        Current := RowsPcbLibMoreGet(Prim, Kind,
                                                            'text', FpName, FpDesc, XOrg, YOrg,
                                                            Units, PcbLib.Board);
                                                        Problem := RowsTextWriteProblem(Current, Value);
                                                        If Problem <> '' Then
                                                            Notes := Notes + 'row ' + Addr + ': '
                                                                + Problem + ';';
                                                        Continue;
                                                    End;
                                                    If Copy(Key, 1, 1) <> '?' Then Continue;
                                                    Name := Copy(Key, 2, Length(Key));
                                                    Have := RowsPcbLibMoreGet(Prim, Kind, Name,
                                                        FpName, FpDesc, XOrg, YOrg, Units,
                                                        PcbLib.Board);
                                                    { A layer guard may name the layer either }
                                                    { way: compare the resolved layers.       }
                                                    If Name = 'layer' Then
                                                    Begin
                                                        If ResolveLayerIdCached(PcbLib.Board, Value,
                                                            LayerCache) <> Prim.Layer Then
                                                            Notes := Notes + 'row ' + Addr
                                                                + ': layer is ' + Have + ', not '
                                                                + Value + ';';
                                                    End
                                                    Else If Not RowsCondHolds(FTypes.Strings[F],
                                                        Have, '=', Value) Then
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
                                            Try
                                                PCBServer.SendMessageToRobots(Prim.I_ObjectAddress,
                                                    c_Broadcast, PCBM_BeginModify, c_NoEventData);
                                            Except End;
                                            S := StrToInt(RS.Strings[K]);
                                            E := StrToInt(RE.Strings[K]);
                                            For F := S To E - 1 Do
                                            Begin
                                                Key := FKeys.Strings[F];
                                                If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then
                                                    Continue;
                                                Value := FVals.Strings[F];
                                                LayerId := eNoLayer;
                                                If Key = 'layer' Then
                                                    LayerId := ResolveLayerIdCached(PcbLib.Board,
                                                        Value, LayerCache);
                                                If Not RowsPcbLibMoreSet(Prim, Kind, Key, Value,
                                                    XOrg, YOrg, Units, LayerId) Then
                                                    Ok := False;
                                            End;
                                            Try
                                                PCBServer.SendMessageToRobots(Prim.I_ObjectAddress,
                                                    c_Broadcast, PCBM_EndModify, c_NoEventData);
                                            Except End;
                                            RowsPcbLibMoreRefresh(Prim, Kind);
                                            { Read every written field back. }
                                            S := StrToInt(RS.Strings[K]);
                                            E := StrToInt(RE.Strings[K]);
                                            For F := S To E - 1 Do
                                            Begin
                                                Key := FKeys.Strings[F];
                                                If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then
                                                    Continue;
                                                Value := FVals.Strings[F];
                                                Have := RowsPcbLibMoreGet(Prim, Kind, Key, FpName,
                                                    FpDesc, XOrg, YOrg, Units, PcbLib.Board);
                                                If Key = 'layer' Then
                                                Begin
                                                    LayerId := ResolveLayerIdCached(PcbLib.Board,
                                                        Value, LayerCache);
                                                    If Prim.Layer <> LayerId Then Ok := False;
                                                End
                                                Else If Not RowsCondHolds(FTypes.Strings[F],
                                                    Have, '=', Value) Then
                                                Begin
                                                    Ok := False;
                                                    Notes := Notes + 'row ' + Addr + ': ' + Key
                                                        + ' reads ' + Have + ' after writing '
                                                        + Value + ';';
                                                End;
                                                Rec := Rec + ',"' + Key + '":'
                                                    + RowsCell(FTypes.Strings[F], Have);
                                            End;
                                            If Not Ok Then Inc(Failed);
                                            Inc(Written);
                                            Results.Add('{"id":"' + Addr + '","ok":'
                                                + BoolToJsonStr(Ok) + Rec + '}');
                                        End;
                                    End;
                                    Prim := GrpIter.NextPCBObject;
                                End;
                            Finally
                                Footprint.GroupIterator_Destroy(GrpIter);
                            End;
                        End;
                        Footprint := Iter.NextPCBObject;
                    End;
                Finally
                    PcbLib.LibraryIterator_Destroy(Iter);
                End;
            Finally
                If Pass = 2 Then PcbEndEdit(0);
            End;
            If (Pass = 1) And (Notes <> '') Then
            Begin
                Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                    Notes + ' nothing was changed; read the ' + Kind + 's again');
                Exit;
            End;
        End;
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(UsedPath);

        Result := BuildSuccessResponse(RequestId,
            RowsWriteReply('pcblib', Kind, Ids.Count, Written, Failed, Repeats,
                Results, Notes));
    Finally
        RS.Free;  RE.Free;  FKeys.Free;  FVals.Free;  FTypes.Free;
        Ids.Free;  Texts.Free;  Hits.Free;  FpNames.Free;
        Seen.Free;  Done.Free;  Results.Free;
    End;
End;

{ The entry points the routing names, one per kind and action. }
Function RowsPcbLibArcRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbLibMoreRead(Params, RequestId, 'arc');
End;

Function RowsPcbLibArcWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbLibMoreWrite(Params, RequestId, 'arc');
End;

Function RowsPcbLibTextRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbLibMoreRead(Params, RequestId, 'text');
End;

Function RowsPcbLibTextWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbLibMoreWrite(Params, RequestId, 'text');
End;

{ UnitStamp_RowsPcbLibMore - which build of RowsPcbLibMore.pas Altium compiled; dev here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_RowsPcbLibMore(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
