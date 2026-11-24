{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ RowsPcbLib.pas - rows kinds of a PcbLib (docs/ROWS_CONTRACT.md).            }
{                                                                              }
{ track is the WORKED TEMPLATE every other kind follows: a field table, a      }
{ getter, a value check, a setter, then a read and a write built on            }
{ RowsCore.pas. A new kind copies this shape and changes only the object.      }
{                                                                              }
{ Scope: footprint = a name or wildcard pattern (default every footprint);    }
{ layer = one layer, resolved once. Coordinates are from each footprint's     }
{ reference point (Footprint.X / Y), as every library reader gives them.      }
{ Each row carries the footprint's name and description.                      }
{                                                                              }
{ A footprint's group iterator lists a primitive added in this session twice  }
{ until the footprint is made current again (see LibActionAddsToFootprint):   }
{ every walk here keeps the addresses it has seen and skips a repeat.         }
{..............................................................................}

{ The track table. layer = Altium's own name (TopOverlay, Mechanical13) --   }
{ the one a where can rely on; layer_name = the stack's user name.           }
Function RowsPcbLibTrackSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|footprint:s:r|description:s:r|layer:s:w|layer_name:s:r'
        + '|x1:l:w|y1:l:w|x2:l:w|y2:l:w|width:l:w|length:l:r'
        + '|keepout:b:r|selected:b:r';
End;

{ A track's length in coordinates, as a Double (the square of a board-sized }
{ difference does not fit an Integer).                                       }
Function RowsTrackLength(Track : IPCB_Track) : Double;
Var
    DX, DY : Double;
Begin
    DX := Track.X2 - Track.X1;
    DY := Track.Y2 - Track.Y1;
    Result := Sqrt(DX * DX + DY * DY);
End;

{ One field of a library track as text in the call's unit; '' when it }
{ cannot be read.                                                      }
Function RowsPcbLibTrackGet(Track : IPCB_Track; Field : String; FpName : String;
    FpDesc : String; XOrg : Integer; YOrg : Integer; Units : String;
    Board : IPCB_Board) : String;
Var
    V : String;
Begin
    V := '';
    Try
        Case Field Of
            'id':          V := IntToStr(Track.I_ObjectAddress);
            'footprint':   V := FpName;
            'description': V := FpDesc;
            'layer':       V := LayerDisplayName(Track.Layer);
            'layer_name':  V := RowsLayerUserName(Board, Track.Layer);
            'x1':          V := RowsLen(Track.X1 - XOrg, Units);
            'y1':          V := RowsLen(Track.Y1 - YOrg, Units);
            'x2':          V := RowsLen(Track.X2 - XOrg, Units);
            'y2':          V := RowsLen(Track.Y2 - YOrg, Units);
            'width':       V := RowsLen(Track.Width, Units);
            'length':      V := RowsLen(RowsTrackLength(Track), Units);
            'keepout':     V := BoolToJsonStr(Track.IsKeepout);
            'selected':    V := BoolToJsonStr(Track.Selected);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ Write one field of a library track. Layer is the resolved layer for a  }
{ 'layer' write. False when the write raised.                            }
Function RowsPcbLibTrackSet(Track : IPCB_Track; Field : String; Value : String;
    XOrg : Integer; YOrg : Integer; Units : String; Layer : TLayer) : Boolean;
Begin
    Result := True;
    Try
        Case Field Of
            'layer': Track.Layer := Layer;
            'x1':    Track.X1 := XOrg + RowsCoord(Value, Units);
            'y1':    Track.Y1 := YOrg + RowsCoord(Value, Units);
            'x2':    Track.X2 := XOrg + RowsCoord(Value, Units);
            'y2':    Track.Y2 := YOrg + RowsCoord(Value, Units);
            'width': Track.Width := RowsCoord(Value, Units);
        Else
            Result := False;
        End;
    Except
        Result := False;
    End;
End;

{..............................................................................}
{ rows.read_rows doc=pcblib kind=track                                              }
{..............................................................................}

Function RowsPcbLibTrackRead(Params : String; RequestId : String) : String;
Var
    CellCache : TStringList;
    Spec, Units, LibPath, UsedPath, Problem, FpPattern, LayerStr, FpName, FpDesc : String;
    Addr, Line, F, Have : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Track : IPCB_Track;
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
    Spec := RowsPcbLibTrackSpec(0);
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
                        GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject));
                        Track := GrpIter.FirstPCBObject;
                        While (Track <> Nil) And (Not Stop) Do
                        Begin
                            Addr := '';
                            Try Addr := IntToStr(Track.I_ObjectAddress); Except End;
                            If (Addr <> '') And (Seen.IndexOf(Addr) < 0) Then
                            Begin
                                Seen.Add(Addr);
                                Inc(Scanned);
                                Keep := True;
                                If LayerStr <> '' Then Keep := Track.Layer = LayerId;
                                I := 0;
                                While Keep And (I < Conds.Count) Do
                                Begin
                                    Have := RowsPcbLibTrackGet(Track, RowsCol(CF, I),
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
                                                    RowsPcbLibTrackGet(Track, F, FpName, FpDesc,
                                                    XOrg, YOrg, Units, PcbLib.Board), I, CellCache);
                                            End;
                                            Lines.Add('[' + Line + ']');
                                        End;
                                    End;
                                End;
                            End;
                            If Not Stop Then Track := GrpIter.NextPCBObject;
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
            RowsReadReply('pcblib', 'track', Units, Fields, Lines, Offset, More,
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
{ rows.write_rows doc=pcblib kind=track                                             }
{ Every row needs ?footprint=, which also limits the walks to those           }
{ footprints. Two walks, as Lib_SetPadGeometry: find and guard, then write.    }
{..............................................................................}

Function RowsPcbLibTrackWrite(Params : String; RequestId : String) : String;
Var
    RS, RE, FKeys, FVals, FTypes : TStringList;
    S, E, F : Integer;
    Spec, Units, LibPath, UsedPath, Problem, Code, FpName, FpDesc, Addr : String;
    Key, Name, Value, Have, Notes, Rec, LayerCache : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Track : IPCB_Track;
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
    Spec := RowsPcbLibTrackSpec(0);

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

        { Pass 1 finds every row's track and checks its guards; pass 2 writes. }
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
                        Notes := Notes + 'row ' + Ids.Strings[K] + ': no track with that id in '
                            + RowsFlatValue(FKeys, FVals, StrToInt(RS.Strings[K]),
                StrToInt(RE.Strings[K]), '?footprint') + ';';
                If Notes <> '' Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                        Notes + ' nothing was changed; read the tracks again');
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
                                GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject));
                                Track := GrpIter.FirstPCBObject;
                                While Track <> Nil Do
                                Begin
                                    Addr := '';
                                    Try Addr := IntToStr(Track.I_ObjectAddress); Except End;
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
                                                    If Copy(Key, 1, 1) <> '?' Then Continue;
                                                    Name := Copy(Key, 2, Length(Key));
                                                    Value := FVals.Strings[F];
                                                    Have := RowsPcbLibTrackGet(Track, Name,
                                                        FpName, FpDesc, XOrg, YOrg, Units,
                                                        PcbLib.Board);
                                                    { A layer guard may name the layer either }
                                                    { way: compare the resolved layers.       }
                                                    If Name = 'layer' Then
                                                    Begin
                                                        If ResolveLayerIdCached(PcbLib.Board, Value,
                                                            LayerCache) <> Track.Layer Then
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
                                                PCBServer.SendMessageToRobots(Track.I_ObjectAddress,
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
                                                If Not RowsPcbLibTrackSet(Track, Key, Value,
                                                    XOrg, YOrg, Units, LayerId) Then
                                                    Ok := False;
                                            End;
                                            Try
                                                PCBServer.SendMessageToRobots(Track.I_ObjectAddress,
                                                    c_Broadcast, PCBM_EndModify, c_NoEventData);
                                            Except End;
                                            { Read every written field back. }
                                            S := StrToInt(RS.Strings[K]);
                                            E := StrToInt(RE.Strings[K]);
                                            For F := S To E - 1 Do
                                            Begin
                                                Key := FKeys.Strings[F];
                                                If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then
                                                    Continue;
                                                Value := FVals.Strings[F];
                                                Have := RowsPcbLibTrackGet(Track, Key, FpName,
                                                    FpDesc, XOrg, YOrg, Units, PcbLib.Board);
                                                If Key = 'layer' Then
                                                Begin
                                                    LayerId := ResolveLayerIdCached(PcbLib.Board,
                                                        Value, LayerCache);
                                                    If Track.Layer <> LayerId Then Ok := False;
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
                                    Track := GrpIter.NextPCBObject;
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
                    Notes + ' nothing was changed; read the tracks again');
                Exit;
            End;
        End;
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(UsedPath);

        Result := BuildSuccessResponse(RequestId,
            RowsWriteReply('pcblib', 'track', Ids.Count, Written, Failed, Repeats,
                Results, Notes));
    Finally
        RS.Free;  RE.Free;  FKeys.Free;  FVals.Free;  FTypes.Free;
        Ids.Free;  Texts.Free;  Hits.Free;  FpNames.Free;
        Seen.Free;  Done.Free;  Results.Free;
    End;
End;

{ UnitStamp_RowsPcbLib - which build of RowsPcbLib.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_RowsPcbLib(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
