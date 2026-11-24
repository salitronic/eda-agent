{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ RowsSchGraph.pas - schematic drawing kinds (docs/ROWS_CONTRACT.md):         }
{ line, polyline and arc, in a SchLib (schlib/...) and on a sheet (sch/...).  }
{ The shape follows RowsSchLib.pas (schlib / pin).                            }
{                                                                              }
{ schlib: the drawing of the symbols asked for (symbol = a name or pattern,   }
{ default the symbol on screen), every part and display mode; each row       }
{ carries its symbol, part and mode. The symbols are found by walking the    }
{ library, never by a by-name lookup (see the head of RowsSchLib.pas).       }
{ sch: the drawing on ONE sheet (document_path, default the sheet in front), }
{ first level only: what belongs to a placed part or to the sheet template   }
{ is never listed nor written.                                               }
{                                                                              }
{ THE ID: a schematic object address cannot be turned into text (see       }
{ RowsSchLibPinId), so an id is the position of the object in its walk     }
{ (symbol position, a colon, then the position among that symbol objects of  }
{ the kind; on a sheet the position alone) and, after an @, the coordinates  }
{ of its first point (line start, first vertex, arc centre). A write finds   }
{ the object only when both still agree: an object moved, added or removed  }
{ since the read is refused, never written in the place of another.         }
{                                                                              }
{ Fields: width as Altium keeps it (0 smallest, 1 small, 2 medium, 3 large), }
{ style 0 solid, 1 dashed, 2 dotted, 3 dash dot; colours as Altium packs     }
{ them (BGR integer); polyline vertices as a point list (type v, x,y;x,y,    }
{ every number a length); end shapes by name (None, Arrow, Solid Arrow,      }
{ Tail, Solid Tail, Circle, Square); arc angles in degrees as type a.       }
{ Every member is read and written through the typed interface of its own   }
{ object kind only: a member asked of another kind halts the script.        }
{..............................................................................}

{ The object id of a kind; 0 for a kind this unit does not draw. }
Function RowsSGObjectId(Kind : String) : Integer;
Begin
    If Kind = 'line' Then Result := eLine
    Else If Kind = 'polyline' Then Result := ePolyline
    Else If Kind = 'arc' Then Result := eArc
    Else Result := 0;
End;

{ The field table of a kind on a doc. }
Function RowsSGSpec(Doc : String; Kind : String) : String;
Var
    Ctx : String;
Begin
    If Doc = 'schlib' Then Ctx := 'id:s:r|symbol:s:r|part:i:r|mode:i:r'
    Else Ctx := 'id:s:r';
    If Kind = 'line' Then
        Result := Ctx + '|x1:l:w|y1:l:w|x2:l:w|y2:l:w|width:i:w|style:i:w|color:i:w'
    Else If Kind = 'polyline' Then
        Result := Ctx + '|vertices:v:w|points:i:r|width:i:w|style:i:w|color:i:w'
            + '|start_shape:s:w|end_shape:s:w|shape_size:i:w'
    Else
        Result := Ctx + '|x:l:w|y:l:w|radius:l:w|start_angle:a:w|end_angle:a:w'
            + '|width:i:w|color:i:w';
End;

Function RowsSchLibLineSpec(Dummy : Integer) : String;
Begin
    Result := RowsSGSpec('schlib', 'line');
End;

Function RowsSchLibPolylineSpec(Dummy : Integer) : String;
Begin
    Result := RowsSGSpec('schlib', 'polyline');
End;

Function RowsSchLibArcSpec(Dummy : Integer) : String;
Begin
    Result := RowsSGSpec('schlib', 'arc');
End;

Function RowsSchLineSpec(Dummy : Integer) : String;
Begin
    Result := RowsSGSpec('sch', 'line');
End;

Function RowsSchPolylineSpec(Dummy : Integer) : String;
Begin
    Result := RowsSGSpec('sch', 'polyline');
End;

Function RowsSchArcSpec(Dummy : Integer) : String;
Begin
    Result := RowsSGSpec('sch', 'arc');
End;

{ The vertices of a polyline as the wire carries them: x,y;x,y, integers. }
Function RowsSGVertices(Poly : ISch_Polyline) : String;
Var
    N, I : Integer;
    P : TLocation;
Begin
    Result := '';
    N := Poly.VerticesCount;
    For I := 1 To N Do
    Begin
        P := Poly.Vertex[I];
        If I > 1 Then Result := Result + ';';
        Result := Result + IntToStr(P.X) + ',' + IntToStr(P.Y);
    End;
End;

{ The first point of an object: line start, first vertex, arc centre. }
Function RowsSGAnchor(Obj : ISch_GraphicalObject; Kind : String) : String;
Var
    Poly : ISch_Polyline;
    P : TLocation;
Begin
    Result := '';
    Try
        If Kind = 'polyline' Then
        Begin
            Poly := Obj;
            If Poly.VerticesCount < 1 Then Exit;
            P := Poly.Vertex[1];
        End
        Else
            P := Obj.Location;
        Result := IntToStr(P.X) + ',' + IntToStr(P.Y);
    Except
        Result := '';
    End;
End;

{ An object id: walk position(s), @, the first point (see the unit head). }
Function RowsSGId(At : String; Obj : ISch_GraphicalObject; Kind : String) : String;
Begin
    Result := At + '@' + RowsSGAnchor(Obj, Kind);
End;

{ One field of a drawing object as wire text; empty when it cannot be read. }
{ Id and SymName are the row context the walk worked out.                 }
Function RowsSGGet(Obj : ISch_GraphicalObject; Kind : String; Field : String;
    Id : String; SymName : String) : String;
Var
    Ln : ISch_Line;
    Poly : ISch_Polyline;
    Arc : ISch_Arc;
    P : TLocation;
Begin
    Result := '';
    Try
        If Field = 'id' Then Result := Id
        Else If Field = 'symbol' Then Result := SymName
        Else If Field = 'part' Then Result := IntToStr(Obj.OwnerPartId)
        Else If Field = 'mode' Then Result := IntToStr(Obj.OwnerPartDisplayMode)
        Else If Kind = 'line' Then
        Begin
            Ln := Obj;
            If Field = 'width' Then Result := IntToStr(Ln.LineWidth)
            Else If Field = 'color' Then Result := IntToStr(Ln.Color)
            Else If Field = 'style' Then Result := IntToStr(Ln.LineStyle)
            Else If (Field = 'x1') Or (Field = 'y1') Then
            Begin
                P := Ln.Location;
                If Field = 'x1' Then Result := IntToStr(P.X) Else Result := IntToStr(P.Y);
            End
            Else If (Field = 'x2') Or (Field = 'y2') Then
            Begin
                P := Ln.Corner;
                If Field = 'x2' Then Result := IntToStr(P.X) Else Result := IntToStr(P.Y);
            End;
        End
        Else If Kind = 'polyline' Then
        Begin
            Poly := Obj;
            If Field = 'width' Then Result := IntToStr(Poly.LineWidth)
            Else If Field = 'color' Then Result := IntToStr(Poly.Color)
            Else If Field = 'style' Then Result := IntToStr(Poly.LineStyle)
            Else If Field = 'vertices' Then Result := RowsSGVertices(Poly)
            Else If Field = 'points' Then Result := IntToStr(Poly.VerticesCount)
            Else If Field = 'start_shape' Then Result := LineShapeName(Poly.StartLineShape)
            Else If Field = 'end_shape' Then Result := LineShapeName(Poly.EndLineShape)
            Else If Field = 'shape_size' Then Result := IntToStr(Poly.LineShapeSize);
        End
        Else If Kind = 'arc' Then
        Begin
            Arc := Obj;
            If Field = 'width' Then Result := IntToStr(Arc.LineWidth)
            Else If Field = 'color' Then Result := IntToStr(Arc.Color)
            Else If (Field = 'x') Or (Field = 'y') Then
            Begin
                P := Arc.Location;
                If Field = 'x' Then Result := IntToStr(P.X) Else Result := IntToStr(P.Y);
            End
            Else If Field = 'radius' Then Result := RowsLen(Arc.Radius, 'coord')
            Else If Field = 'start_angle' Then Result := RowsAngle(Arc.StartAngle)
            Else If Field = 'end_angle' Then Result := RowsAngle(Arc.EndAngle);
        End;
    Except
        Result := '';
    End;
End;

{ A point list (x,y;x,y, canonical integers) into two lists; False when it }
{ is not one, or holds fewer than two points.                              }
Function RowsSGPoints(Text : String; Xs : TStringList; Ys : TStringList) : Boolean;
Var
    Rest, Pt, A, B : String;
    I : Integer;
Begin
    Result := False;
    Rest := Trim(Text);
    While Rest <> '' Do
    Begin
        I := Pos(';', Rest);
        If I > 0 Then
        Begin
            Pt := Trim(Copy(Rest, 1, I - 1));
            Rest := Trim(Copy(Rest, I + 1, Length(Rest)));
        End
        Else
        Begin
            Pt := Trim(Rest);
            Rest := '';
        End;
        I := Pos(',', Pt);
        If I < 1 Then Exit;
        A := Trim(Copy(Pt, 1, I - 1));
        B := Trim(Copy(Pt, I + 1, Length(Pt)));
        If (Not RowsIntStr(A)) Or (Not RowsIntStr(B)) Then Exit;
        Xs.Add(A);
        Ys.Add(B);
    End;
    Result := Xs.Count >= 2;
End;

{ Write one field of a drawing object; False when not written or raised. }
{ The caller brackets the object in SchBeginModify / SchEndModify.        }
Function RowsSGSet(Obj : ISch_GraphicalObject; Kind : String; Field : String;
    Value : String) : Boolean;
Var
    Ln : ISch_Line;
    Poly : ISch_Polyline;
    Arc : ISch_Arc;
    P : TLocation;
    Xs, Ys : TStringList;
    I, N : Integer;
    V : String;
Begin
    Result := False;
    V := Trim(Value);
    Try
        If Kind = 'line' Then
        Begin
            Ln := Obj;
            If Field = 'width' Then
            Begin
                Ln.LineWidth := RowsIntOr(V, 0);
                Result := True;
            End
            Else If Field = 'color' Then
            Begin
                Ln.Color := RowsIntOr(V, 0);
                Result := True;
            End
            Else If Field = 'style' Then
            Begin
                Ln.LineStyle := RowsIntOr(V, 0);
                Result := True;
            End
            Else If (Field = 'x1') Or (Field = 'y1') Then
            Begin
                P := Ln.Location;
                If Field = 'x1' Then P.X := RowsCoord(V, 'coord') Else P.Y := RowsCoord(V, 'coord');
                Ln.Location := P;
                Result := True;
            End
            Else If (Field = 'x2') Or (Field = 'y2') Then
            Begin
                P := Ln.Corner;
                If Field = 'x2' Then P.X := RowsCoord(V, 'coord') Else P.Y := RowsCoord(V, 'coord');
                Ln.Corner := P;
                Result := True;
            End;
        End
        Else If Kind = 'polyline' Then
        Begin
            Poly := Obj;
            If Field = 'width' Then
            Begin
                Poly.LineWidth := RowsIntOr(V, 0);
                Result := True;
            End
            Else If Field = 'color' Then
            Begin
                Poly.Color := RowsIntOr(V, 0);
                Result := True;
            End
            Else If Field = 'style' Then
            Begin
                Poly.LineStyle := RowsIntOr(V, 0);
                Result := True;
            End
            Else If Field = 'vertices' Then
            Begin
                Xs := TStringList.Create;
                Ys := TStringList.Create;
                Try
                    If Not RowsSGPoints(V, Xs, Ys) Then Exit;
                    { The route SetSchVertices (Generic.pas) proved for a    }
                    { polyline: the count first, then every vertex.         }
                    Poly.VerticesCount := Xs.Count;
                    For I := 1 To Xs.Count Do
                        Poly.Vertex[I] := Point(StrToInt(Xs.Strings[I - 1]),
                            StrToInt(Ys.Strings[I - 1]));
                    Result := Poly.VerticesCount = Xs.Count;
                Finally
                    Xs.Free;
                    Ys.Free;
                End;
            End
            Else If (Field = 'start_shape') Or (Field = 'end_shape') Then
            Begin
                N := LineShapeOrdinal(V);
                If N < 0 Then Exit;
                If Field = 'start_shape' Then Poly.StartLineShape := N
                Else Poly.EndLineShape := N;
                Result := True;
            End
            Else If Field = 'shape_size' Then
            Begin
                Poly.LineShapeSize := RowsIntOr(V, 0);
                Result := True;
            End;
        End
        Else If Kind = 'arc' Then
        Begin
            Arc := Obj;
            If Field = 'width' Then
            Begin
                Arc.LineWidth := RowsIntOr(V, 0);
                Result := True;
            End
            Else If Field = 'color' Then
            Begin
                Arc.Color := RowsIntOr(V, 0);
                Result := True;
            End
            Else If (Field = 'x') Or (Field = 'y') Then
            Begin
                P := Arc.Location;
                If Field = 'x' Then P.X := RowsCoord(V, 'coord') Else P.Y := RowsCoord(V, 'coord');
                Arc.Location := P;
                Result := True;
            End
            Else If Field = 'radius' Then
            Begin
                Arc.Radius := RowsCoord(V, 'coord');
                Result := True;
            End
            Else If Field = 'start_angle' Then
            Begin
                Arc.StartAngle := RowsAngleDeg(V);
                Result := True;
            End
            Else If Field = 'end_angle' Then
            Begin
                Arc.EndAngle := RowsAngleDeg(V);
                Result := True;
            End;
        End;
    Except
        Result := False;
    End;
End;

{ Whether a value read off an object is the one a guard (Exact false) or a }
{ write (Exact true) asked for. End shapes compare as shapes, whichever    }
{ name they were given by; an angle of 360 degrees is the same as 0.       }
Function RowsSGSame(FieldType : String; Field : String; Have : String; Want : String;
    Exact : Boolean) : Boolean;
Var
    W : String;
Begin
    W := Trim(Want);
    If (Field = 'start_shape') Or (Field = 'end_shape') Then
        Result := (Have <> '') And (LineShapeOrdinal(Have) = LineShapeOrdinal(W))
            And (LineShapeOrdinal(W) >= 0)
    Else If FieldType = 'a' Then
    Begin
        If W = '3600000' Then W := '0';
        If Have = '3600000' Then Have := '0';
        Result := (Have <> '') And (Have = W);
    End
    Else If FieldType = 'v' Then
        Result := (Have <> '') And (Have = StringReplace(W, ' ', '', -1))
    Else If Exact And (FieldType = 's') Then
        Result := Have = W
    Else
        Result := RowsCondHolds(FieldType, Have, '=', W);
End;

{ The schematic sheet a sch call works on: the open sheet at Path, else the }
{ one in front. Nil when there is none or it is a library.                }
Function RowsSGSheet(Path : String) : ISch_Document;
Var
    Doc : ISch_Document;
    IsLib : Boolean;
Begin
    Result := Nil;
    Doc := Nil;
    If Path <> '' Then
    Begin
        Try Doc := SchServer.GetSchDocumentByPath(Path); Except Doc := Nil; End;
    End
    Else
    Begin
        Try Doc := SchServer.GetCurrentSchDocument; Except Doc := Nil; End;
    End;
    If Doc = Nil Then Exit;
    IsLib := False;
    Try IsLib := (Doc.ObjectId = eSchLib); Except IsLib := False; End;
    If Not IsLib Then Result := Doc;
End;

{ The walks of one call: on a SchLib one container per symbol (Addrs = walk }
{ positions, Names = LibReferences), on a sheet one container, the sheet.  }
{ Iterator of a container, filtered to the kind, first level on a sheet.   }
Function RowsSGIter(Container : ISch_BasicContainer; ObjId : Integer;
    OnSheet : Boolean) : ISch_Iterator;
Begin
    Result := Container.SchIterator_Create;
    If Result = Nil Then Exit;
    If OnSheet Then Result.SetState_IterationDepth(eIterateFirstLevel);
    Result.AddFilter_ObjectSet(MkSet(ObjId));
End;

{..............................................................................}
{ rows.read_rows doc=schlib|sch kind=line|polyline|arc                         }
{..............................................................................}

Function RowsSGRead(Doc : String; Kind : String; Params : String;
    RequestId : String) : String;
Var
    CellCache : TStringList;
    Spec, Units, Path, Problem, SymPattern, SymName, WalkPos, Id, Line, F, Have : String;
    CurAddr : String;
    SchLib : ISch_Lib;
    Sheet : ISch_Document;
    Comp : ISch_Component;
    Container : ISch_BasicContainer;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Fields, Types, Conds, Lines, Seen, CF, CO, CV, CT, Addrs, Names : TStringList;
    Offset, Limit, Matched, Scanned, I, C, ObjPos, ObjId : Integer;
    Keep, More, Stop, OnSheet : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    OnSheet := Doc = 'sch';
    ObjId := RowsSGObjectId(Kind);
    Spec := RowsSGSpec(Doc, Kind);
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
        SchLib := Nil;
        Sheet := Nil;
        If OnSheet Then
        Begin
            Path := Trim(ExtractJsonValue(Params, 'document_path'));
            Sheet := RowsSGSheet(Path);
            If Sheet = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'NO_SCHDOC',
                    'No schematic sheet is in front and document_path did not name an '
                    + 'open one');
                Exit;
            End;
            { one walk: the sheet (the entry itself is never read) }
            Addrs.Add('sheet');
            Names.Add('sheet');
        End
        Else
        Begin
            Path := ExtractJsonValue(Params, 'library_path');
            SchLib := FocusSchLib(Path);
            If SchLib = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
                    'No schematic library is active and library_path did not resolve');
                Exit;
            End;
            CurAddr := RowsSchLibSymbols(SchLib, SymPattern, False, Seen, Addrs, Names);
            If (SymPattern = '') And (Addrs.Count = 0) Then
            Begin
                Result := BuildErrorResponse(RequestId, 'NO_SYMBOL',
                    'No symbol is on screen in this library; pass symbol= (a name or pattern)');
                Exit;
            End;
        End;

        Matched := 0;
        Scanned := 0;
        More := False;
        Stop := False;
        C := 0;
        While (C < Addrs.Count) And (Not Stop) Do
        Begin
            If OnSheet Then
            Begin
                Container := Sheet;
                SymName := '';
            End
            Else
            Begin
                Comp := RowsSchLibSymbolAt(SchLib, Addrs.Strings[C]);
                Container := Comp;
                SymName := RowsCol(Names, C);
            End;
            If Container <> Nil Then
            Begin
                Iter := RowsSGIter(Container, ObjId, OnSheet);
                Try
                    ObjPos := 0;
                    Obj := Iter.FirstSchObject;
                    While (Obj <> Nil) And (Not Stop) Do
                    Begin
                        Inc(ObjPos);
                        If OnSheet Then WalkPos := IntToStr(ObjPos)
                        Else WalkPos := Addrs.Strings[C] + ':' + IntToStr(ObjPos);
                        Id := RowsSGId(WalkPos, Obj, Kind);
                        Inc(Scanned);
                        Keep := True;
                        I := 0;
                        While Keep And (I < Conds.Count) Do
                        Begin
                            F := RowsCol(CF, I);
                            Have := RowsSGGet(Obj, Kind, F, Id, SymName);
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
                                        If Line <> '' Then Line := Line + ',';
                                        Line := Line + RowsCellCached(Types.Strings[I],
                                            RowsSGGet(Obj, Kind, Fields.Strings[I], Id,
                                                SymName), I, CellCache);
                                    End;
                                    Lines.Add('[' + Line + ']');
                                End;
                            End;
                        End;
                        If Not Stop Then Obj := Iter.NextSchObject;
                    End;
                Finally
                    Container.SchIterator_Destroy(Iter);
                End;
            End;
            Inc(C);
        End;

        Result := BuildSuccessResponse(RequestId,
            RowsReadReply(Doc, Kind, Units, Fields, Lines, Offset, More, Scanned, ''));
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
{ rows.write_rows doc=schlib|sch kind=line|polyline|arc                        }
{ schlib rows need ?symbol=, which also limits the walks to those symbols.   }
{ Two passes: find and guard every row, then write inside ONE edit, each     }
{ object in its own SchBeginModify / SchEndModify, every written field read  }
{ back. An id names its object by position and first point (unit head).     }
{..............................................................................}

Function RowsSGWrite(Doc : String; Kind : String; Params : String;
    RequestId : String) : String;
Var
    RS, RE, FKeys, FVals, FTypes : TStringList;
    S, E, F : Integer;
    Spec, Units, Path, Problem, Code, SymName, WalkPos, Id : String;
    Key, Name, Value, Have, Notes, Rec, CurAddr, What : String;
    SchLib : ISch_Lib;
    Sheet : ISch_Document;
    Comp : ISch_Component;
    Container : ISch_BasicContainer;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Ids, Texts, Hits, SymKeys, Addrs, Names, Seen, Done, Results : TStringList;
    K, C, Written, Failed, Repeats, Pass, ObjPos, ObjId : Integer;
    Ok, OnSheet : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    OnSheet := Doc = 'sch';
    ObjId := RowsSGObjectId(Kind);
    Spec := RowsSGSpec(Doc, Kind);
    What := Kind;

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
        If OnSheet Then Name := '' Else Name := 'symbol';
        Problem := RowsTakeRows(Spec, Params, Name,
            Ids, Texts, RS, RE, FKeys, FVals, FTypes, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        For K := 0 To Ids.Count - 1 Do
        Begin
            Hits.Add('0');
            If Not OnSheet Then
            Begin
                SymName := UpperCase(RowsFlatValue(FKeys, FVals, StrToInt(RS.Strings[K]),
                    StrToInt(RE.Strings[K]), '?symbol'));
                If SymKeys.IndexOf(SymName) < 0 Then SymKeys.Add(SymName);
            End;
        End;

        { The values of a row are checked by rows.py before it is sent        }
        { (rows_rules.py: the only way in); only what needs the document is  }
        { checked here.                                                      }
        SchLib := Nil;
        Sheet := Nil;
        If OnSheet Then
        Begin
            Path := Trim(ExtractJsonValue(Params, 'document_path'));
            Sheet := RowsSGSheet(Path);
            If Sheet = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'NO_SCHDOC',
                    'No schematic sheet is in front and document_path did not name an '
                    + 'open one; nothing was changed');
                Exit;
            End;
            { one walk: the sheet (the entry itself is never read) }
            Addrs.Add('sheet');
            Names.Add('sheet');
        End
        Else
        Begin
            Path := ExtractJsonValue(Params, 'library_path');
            SchLib := FocusSchLib(Path);
            If SchLib = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
                    'No schematic library is active and library_path did not resolve');
                Exit;
            End;
            CurAddr := RowsSchLibSymbols(SchLib, '', True, SymKeys, Addrs, Names);
        End;

        { Pass 1 finds every row object and checks its guards; pass 2 writes. }
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
                    Begin
                        Notes := Notes + 'row ' + Ids.Strings[K] + ': no ' + What
                            + ' with that id';
                        If Not OnSheet Then
                            Notes := Notes + ' in ' + RowsFlatValue(FKeys, FVals,
                                StrToInt(RS.Strings[K]), StrToInt(RE.Strings[K]), '?symbol');
                        Notes := Notes + ' (moved, added or removed since the read?);';
                    End;
                If Notes <> '' Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                        Notes + ' nothing was changed; read the ' + What + 's again');
                    Exit;
                End;
                If OnSheet Then SchBeginEdit(Sheet, '') Else SchBeginEdit(SchLib, '');
            End;
            Try
                For C := 0 To Addrs.Count - 1 Do
                Begin
                    If OnSheet Then
                    Begin
                        Container := Sheet;
                        SymName := '';
                    End
                    Else
                    Begin
                        Comp := RowsSchLibSymbolAt(SchLib, Addrs.Strings[C]);
                        If Comp = Nil Then Continue;
                        Container := Comp;
                        SymName := RowsCol(Names, C);
                    End;
                    Iter := RowsSGIter(Container, ObjId, OnSheet);
                    Try
                        ObjPos := 0;
                        Obj := Iter.FirstSchObject;
                        While Obj <> Nil Do
                        Begin
                            Inc(ObjPos);
                            If OnSheet Then WalkPos := IntToStr(ObjPos)
                            Else WalkPos := Addrs.Strings[C] + ':' + IntToStr(ObjPos);
                            Id := RowsSGId(WalkPos, Obj, Kind);
                            K := Ids.IndexOf(Id);
                            If (K >= 0) And (Pass = 1) Then
                            Begin
                                If Seen.IndexOf(Id) < 0 Then
                                Begin
                                    Seen.Add(Id);
                                    Hits.Strings[K] := '1';
                                    S := StrToInt(RS.Strings[K]);
                                    E := StrToInt(RE.Strings[K]);
                                    For F := S To E - 1 Do
                                    Begin
                                        Key := FKeys.Strings[F];
                                        If Copy(Key, 1, 1) <> '?' Then Continue;
                                        Name := Copy(Key, 2, Length(Key));
                                        Value := FVals.Strings[F];
                                        Have := RowsSGGet(Obj, Kind, Name, Id, SymName);
                                        If Not RowsSGSame(FTypes.Strings[F], Name, Have,
                                            Value, False) Then
                                            Notes := Notes + 'row ' + Id + ': ' + Name
                                                + ' is ' + Have + ', not ' + Value + ';';
                                    End;
                                End;
                            End
                            Else If (K >= 0) And (Done.IndexOf(Id) >= 0) Then
                                Inc(Repeats)
                            Else If K >= 0 Then
                            Begin
                                Done.Add(Id);
                                S := StrToInt(RS.Strings[K]);
                                E := StrToInt(RE.Strings[K]);
                                Ok := True;
                                Rec := '';
                                SchBeginModify(Obj);
                                For F := S To E - 1 Do
                                Begin
                                    Key := FKeys.Strings[F];
                                    If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;
                                    If Not RowsSGSet(Obj, Kind, Key, FVals.Strings[F]) Then
                                    Begin
                                        Ok := False;
                                        Notes := Notes + 'row ' + Id + ': ' + Key
                                            + ' was not written;';
                                    End;
                                End;
                                SchEndModify(Obj);
                                RefreshSchObjectRender(Obj);
                                { Read every written field back. }
                                For F := S To E - 1 Do
                                Begin
                                    Key := FKeys.Strings[F];
                                    If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;
                                    Value := FVals.Strings[F];
                                    Have := RowsSGGet(Obj, Kind, Key, Id, SymName);
                                    If Not RowsSGSame(FTypes.Strings[F], Key, Have, Value,
                                        True) Then
                                    Begin
                                        Ok := False;
                                        Notes := Notes + 'row ' + Id + ': ' + Key + ' reads '
                                            + Have + ' after writing ' + Value + ';';
                                    End;
                                    Rec := Rec + ',"' + Key + '":'
                                        + RowsCell(FTypes.Strings[F], Have);
                                End;
                                If Not Ok Then Inc(Failed);
                                Inc(Written);
                                Results.Add('{"id":"' + EscapeJsonString(Id) + '","ok":'
                                    + BoolToJsonStr(Ok) + Rec + '}');
                            End;
                            Obj := Iter.NextSchObject;
                        End;
                    Finally
                        Container.SchIterator_Destroy(Iter);
                    End;
                End;
            Finally
                If Pass = 2 Then
                Begin
                    If OnSheet Then SchEndEdit(Sheet, 'Edit') Else SchEndEdit(SchLib, 'Edit');
                End;
            End;
            If (Pass = 1) And (Notes <> '') Then
            Begin
                Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                    Notes + ' nothing was changed; read the ' + What + 's again');
                Exit;
            End;
        End;
        If OnSheet Then MarkDocDirtyByPath(SchDocPath(Sheet)) Else MarkLibDirty(SchLib);

        Result := BuildSuccessResponse(RequestId,
            RowsWriteReply(Doc, Kind, Ids.Count, Written, Failed, Repeats, Results, Notes));
    Finally
        RS.Free;  RE.Free;  FKeys.Free;  FVals.Free;  FTypes.Free;
        Ids.Free;  Texts.Free;  Hits.Free;  SymKeys.Free;  Addrs.Free;  Names.Free;
        Seen.Free;  Done.Free;  Results.Free;
    End;
End;

{ UnitStamp_RowsSchGraph - which build of RowsSchGraph.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy.  }
Function UnitStamp_RowsSchGraph(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
