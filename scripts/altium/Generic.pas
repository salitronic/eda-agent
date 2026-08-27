{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Generic.pas - Generic primitives for the Altium integration bridge                        }
{ 5 primitives: run_process, query_objects, modify_objects,                  }
{               create_object, delete_objects                                }
{ These provide a thin, generic layer so Python controls all logic.          }
{..............................................................................}

{..............................................................................}
{ Property-write diagnostics                                                  }
{                                                                              }
{ SetSchProperty appends here every time a property name is not recognised  }
{ or a write throws, so batch_modify can stop silently swallowing            }
{ "set=Description=..." style mis-spellings. The bridge is single-request,   }
{ so a module-level buffer is safe. Other handlers that call SetSchProperty }
{ via ApplySetProperties simply ignore it.                                   }
{..............................................................................}

Var
    _PropertyDiagStr : String;

{ Buffer is a String, not a TStringList. DelphiScript drops class-method  }
{ visibility on TStringList declared at module scope (Undeclared          }
{ identifier: Count on `_Buf.Count`), and on TStringList returned by a    }
{ Function, even though the equivalent declared as a Function local works.}
{ A pipe-delimited String avoids the entire trap.                          }
{                                                                            }
{ Each record is "kind:propname"; records are joined with '|'.            }

Procedure ResetPropertyDiag;
Begin
    _PropertyDiagStr := '';
End;

Procedure NotePropertyDiag(Kind : String; PropName : String);
{ Dedup so a 50-row modify with one bad prop name records it once, not 50x. }
Var
    Entry : String;
Begin
    Entry := Kind + ':' + PropName;
    { Bracket the buffer with '|' on both sides so a Pos check finds an      }
    { exact record (and not e.g. "unknown:Foo" matching inside "...Foobar"). }
    If Pos('|' + Entry + '|', '|' + _PropertyDiagStr + '|') > 0 Then Exit;
    If _PropertyDiagStr = '' Then
        _PropertyDiagStr := Entry
    Else
        _PropertyDiagStr := _PropertyDiagStr + '|' + Entry;
End;

Function RenderPropertyDiagJson : String;
Var
    UJson, FJson, Remaining, Entry, Kind, Nm : String;
    UCount, FCount, P : Integer;
Begin
    UJson := '['; UCount := 0;
    FJson := '['; FCount := 0;
    Remaining := _PropertyDiagStr;
    While Length(Remaining) > 0 Do
    Begin
        P := Pos('|', Remaining);
        If P = 0 Then
        Begin
            Entry := Remaining;
            Remaining := '';
        End
        Else
        Begin
            Entry := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining));
        End;
        P := Pos(':', Entry);
        If P = 0 Then Continue;
        Kind := Copy(Entry, 1, P - 1);
        Nm := Copy(Entry, P + 1, Length(Entry));
        If Kind = 'unknown' Then
        Begin
            If UCount > 0 Then UJson := UJson + ',';
            UJson := UJson + '"' + EscapeJsonString(Nm) + '"';
            Inc(UCount);
        End
        Else If Kind = 'failed' Then
        Begin
            If FCount > 0 Then FJson := FJson + ',';
            FJson := FJson + '"' + EscapeJsonString(Nm) + '"';
            Inc(FCount);
        End;
    End;
    UJson := UJson + ']';
    FJson := FJson + ']';
    Result := '{"unknown_count":' + IntToStr(UCount)
            + ',"unknown":' + UJson
            + ',"failed_count":' + IntToStr(FCount)
            + ',"failed":' + FJson + '}';
End;

{..............................................................................}
{ Object Type Mapping                                                         }
{..............................................................................}

Function ObjectTypeFromString(TypeStr : String) : Integer;
Begin
    Result := -1;
    If TypeStr = 'eNetLabel'      Then Result := eNetLabel
    Else If TypeStr = 'ePort'          Then Result := ePort
    Else If TypeStr = 'ePowerObject'   Then Result := ePowerObject
    Else If TypeStr = 'eSchComponent'  Then Result := eSchComponent
    Else If TypeStr = 'eWire'          Then Result := eWire
    Else If TypeStr = 'eBus'           Then Result := eBus
    Else If TypeStr = 'eBusEntry'      Then Result := eBusEntry
    Else If TypeStr = 'eParameter'     Then Result := eParameter
    Else If TypeStr = 'eParameterSet'  Then Result := eParameterSet
    Else If TypeStr = 'ePin'           Then Result := ePin
    Else If TypeStr = 'eLabel'         Then Result := eLabel
    Else If TypeStr = 'eLine'          Then Result := eLine
    Else If TypeStr = 'eRectangle'     Then Result := eRectangle
    Else If TypeStr = 'eSheetSymbol'   Then Result := eSheetSymbol
    Else If TypeStr = 'eSheetEntry'    Then Result := eSheetEntry
    Else If TypeStr = 'eNoERC'         Then Result := eNoERC
    Else If TypeStr = 'eJunction'      Then Result := eJunction
    Else If TypeStr = 'eImage'         Then Result := eImage;
End;

{..............................................................................}
{ Generic Property Getter                                                     }
{ Returns string value of a named property from a schematic object.          }
{ Coordinates are returned in mils.                                          }
{..............................................................................}

{..............................................................................}
{ Typed component property helper, extract designator / comment text by    }
{ casting to ISch_Component first. Required because `Obj.Designator` on a    }
{ base ISch_GraphicalObject fails to compile, DelphiScript cannot late-bind }
{ properties that return compound interfaces (ISch_Parameter) the way it can  }
{ for primitive returns.                                                      }
{..............................................................................}

Function GetSchComponentSubText(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    C : ISch_Component;
Begin
    Result := '';
    If Obj.ObjectId <> eSchComponent Then Exit;
    Try
        C := Obj;
        If PropName = 'Designator' Then Result := C.Designator.Text
        Else If PropName = 'Comment' Then Result := C.Comment.Text;
    Except
        // Cast unexpectedly failed despite ObjectId check, surface via counter.
        RecordCastError('GetSchComponentSubText:' + PropName);
        Result := '';
    End;
End;

Procedure SetSchComponentSubText(Obj : ISch_GraphicalObject; PropName : String; Value : String);
Var
    C : ISch_Component;
Begin
    If Obj.ObjectId <> eSchComponent Then Exit;
    Try
        C := Obj;
        If PropName = 'Designator' Then C.Designator.Text := Value
        Else If PropName = 'Comment' Then C.Comment.Text := Value;
    Except
        RecordCastError('SetSchComponentSubText:' + PropName);
    End;
End;

{ Read the Designator and SheetFileName text labels carried by a sheet symbol. }
{ ISch_SheetSymbol exposes them as compound sub-objects (ISch_SheetName,        }
{ ISch_SheetFileName), so the Obj.SheetName / Obj.SheetFileName access has to  }
{ go through a typed-local cast and then read .Text. The same primitive cast   }
{ pattern as GetSchComponentSubText, just for the sheet-symbol family.          }
Function GetSheetSymbolText(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    SS : ISch_SheetSymbol;
Begin
    Result := '';
    If Obj.ObjectId <> eSheetSymbol Then Exit;
    Try
        SS := Obj;
        If PropName = 'Designator' Then
        Begin
            Try If SS.SheetName <> Nil Then Result := SS.SheetName.Text; Except End;
        End
        Else If PropName = 'Filename' Then
        Begin
            Try If SS.SheetFileName <> Nil Then Result := SS.SheetFileName.Text; Except End;
        End;
    Except
        RecordCastError('GetSheetSymbolText:' + PropName);
        Result := '';
    End;
End;

{ Indexed vertex access for the polyline family (eWire, eBus, eLine,          }
{ ePolyline, ...). Recognises `Vertex<N>.X` / `Vertex<N>.Y`, `VertexLast.X` / }
{ `VertexLast.Y` (N = VerticesCount) and a compact all-vertex dump `Vertices` }
{ formatted `x1,y1;x2,y2` with no trailing separator. Names match case-       }
{ insensitively. Altium's vertex array is 1-based, so GetState_Vertex(1) is   }
{ the first vertex and matches what Location.X / Location.Y report. Values    }
{ come back in mils through CoordToMils, exactly like Location.X, so a caller }
{ can compare a vertex against a Location value directly. Objects with no     }
{ vertex array, out-of-range or non-numeric indices and malformed names all   }
{ return '' -- the same thing GetSchProperty yields for an unknown property.  }
{ Nothing here raises.                                                        }
Function GetSchVertexProperty(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    U : String;
    Axis : String;
    IdxStr : String;
    Ch : String;
    Dump : String;
    Cnt : Integer;
    Idx : Integer;
    I : Integer;
    Vtx : TLocation;
    Ok : Boolean;
Begin
    Result := '';
    U := UpperCase(Trim(PropName));

    { Bulk dump. VerticesCount itself stays on the caller's fast path. }
    If U = 'VERTICES' Then
    Begin
        Cnt := 0;
        Try Cnt := Obj.GetState_VerticesCount; Except End;
        If Cnt < 1 Then Exit;
        Dump := '';
        For I := 1 To Cnt Do
        Begin
            Ok := False;
            Try
                Vtx := Obj.GetState_Vertex(I);
                Ok := True;
            Except
                Ok := False;
            End;
            If Not Ok Then Exit;
            If I > 1 Then Dump := Dump + ';';
            Dump := Dump + IntToStr(CoordToMils(Vtx.X)) + ',' + IntToStr(CoordToMils(Vtx.Y));
        End;
        Result := Dump;
        Exit;
    End;

    { 'VERTEX' + index + '.X' / '.Y'. Shortest legal form is 'VERTEX1.X' (9). }
    If Length(U) < 9 Then Exit;
    If Copy(U, 1, 6) <> 'VERTEX' Then Exit;
    Axis := Copy(U, Length(U) - 1, 2);
    If (Axis <> '.X') And (Axis <> '.Y') Then Exit;
    { Everything between the 'VERTEX' prefix and the trailing '.X' / '.Y'. }
    IdxStr := Copy(U, 7, Length(U) - 8);
    If IdxStr = '' Then Exit;

    Cnt := 0;
    Try Cnt := Obj.GetState_VerticesCount; Except End;
    If Cnt < 1 Then Exit;

    If IdxStr = 'LAST' Then
        Idx := Cnt
    Else
    Begin
        { Digits only. Rejects 'Vertex.1.X', 'VertexA.X', 'Vertex 1.X', '-1'. }
        { DelphiScript quirk (see ClassifyPassivePrefix): index a string with }
        { a 1-char Copy rather than S[I], which is not a Char here.           }
        For I := 1 To Length(IdxStr) Do
        Begin
            Ch := Copy(IdxStr, I, 1);
            If (Ch < '0') Or (Ch > '9') Then Exit;
        End;
        Idx := StrToIntDef(IdxStr, 0);
    End;

    If (Idx < 1) Or (Idx > Cnt) Then Exit;

    Ok := False;
    Try
        Vtx := Obj.GetState_Vertex(Idx);
        Ok := True;
    Except
        Ok := False;
    End;
    If Not Ok Then Exit;

    If Axis = '.X' Then
        Result := IntToStr(CoordToMils(Vtx.X))
    Else
        Result := IntToStr(CoordToMils(Vtx.Y));
End;

Function GetSchProperty(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    R : ISch_Rectangle;
    L : ISch_Line;
    Comp : ISch_Component;
    Crn : TLocation;
    Have : Boolean;
    POrient, PLen, PCoord : Integer;
Begin
    Result := '';
    Try
        // Identity
        If PropName = 'ObjectId'    Then Result := IntToStr(Obj.ObjectId)

        // Coordinates (returned in mils). Corner is declared only on
        // ISch_Rectangle and ISch_Line (ISch_RoundRectangle inherits from
        // ISch_Rectangle), NOT on the base ISch_GraphicalObject. The
        // DelphiScript compiler rejects any textual reference to
        // 'Obj.Corner' when Obj is typed ISch_GraphicalObject, regardless
        // of the assignment target. The only compile-safe path is to
        // dispatch on Obj.ObjectId and narrow to a typed-local interface
        // that actually has Corner (R := Obj is a legal interface
        // narrowing in DelphiScript, same pattern as GetSchComponentSubText).
        Else If PropName = 'Location.X'  Then Result := IntToStr(CoordToMils(Obj.Location.X))
        Else If PropName = 'Location.Y'  Then Result := IntToStr(CoordToMils(Obj.Location.Y))
        Else If (PropName = 'Corner.X') Or (PropName = 'Corner.Y') Then
        Begin
            Have := False;
            If (Obj.ObjectId = eRectangle) Or (Obj.ObjectId = eRoundRectangle) Then
            Begin
                R := Obj;
                Crn := R.Corner;
                Have := True;
            End
            Else If Obj.ObjectId = eLine Then
            Begin
                L := Obj;
                Crn := L.Corner;
                Have := True;
            End;
            If Have Then
            Begin
                If PropName = 'Corner.X' Then
                    Result := IntToStr(CoordToMils(Crn.X))
                Else
                    Result := IntToStr(CoordToMils(Crn.Y));
            End;
        End

        // String properties (late-bound across all types, primitives only)
        Else If PropName = 'Text'        Then Result := Obj.Text
        Else If PropName = 'Name'        Then Result := Obj.Name
        Else If PropName = 'LibReference'       Then Result := Obj.LibReference
        Else If PropName = 'SourceLibraryName'  Then Result := Obj.SourceLibraryName
        Else If PropName = 'DesignItemId'       Then Result := Obj.DesignItemId
        // Which part of a multi-part symbol owns this primitive (0 = shared
        // across all parts). Without it a caller querying a multi-part
        // library symbol cannot tell which part a returned primitive is on.
        Else If PropName = 'OwnerPartId'        Then Result := IntToStr(Obj.OwnerPartId)
        Else If PropName = 'OwnerPartDisplayMode' Then Result := IntToStr(Obj.OwnerPartDisplayMode)
        Else If PropName = 'ComponentDescription' Then Result := Obj.ComponentDescription
        Else If PropName = 'UniqueId'    Then Result := Obj.UniqueId
        Else If PropName = 'CurrentPartID' Then
        Begin
            Comp := Obj;
            Try Result := IntToStr(Comp.CurrentPartID); Except Result := ''; End;
        End
        Else If PropName = 'PartCount' Then
        Begin
            Comp := Obj;
            Try Result := IntToStr(Comp.PartCount); Except Result := ''; End;
        End

        // Sub-object string properties (compound interfaces, typed cast required).
        // Designator dispatches by ObjectId, ISch_Component carries the live designator
        // text on its sub-object Designator.Text, ISch_SheetSymbol carries it on
        // SheetName.Text. Filename / SheetFileName are sheet-symbol only and read the
        // SheetFileName.Text label that links the symbol to its child sheet.
        Else If (PropName = 'Designator') Or (PropName = 'Designator.Text') Then
        Begin
            If Obj.ObjectId = ePin Then
                // A pin's Designator IS its pin number (ISch_Pin.Designator);
                // it must not be routed through the component-text helper,
                // which returns empty for a pin and was the cause of blank
                // pin numbers in ePin queries.
                Result := Obj.Designator
            Else If Obj.ObjectId = eSheetSymbol Then
                Result := GetSheetSymbolText(Obj, 'Designator')
            Else
                Result := GetSchComponentSubText(Obj, 'Designator');
        End
        Else If (PropName = 'Filename') Or (PropName = 'FileName')
             Or (PropName = 'SheetFileName') Then
            Result := GetSheetSymbolText(Obj, 'Filename')
        Else If PropName = 'Comment'         Then Result := GetSchComponentSubText(Obj, 'Comment')
        Else If PropName = 'Comment.Text'    Then Result := GetSchComponentSubText(Obj, 'Comment')

        // Integer properties (returned as string)
        Else If PropName = 'Orientation' Then Result := IntToStr(Obj.Orientation)
        Else If PropName = 'FontId'      Then Result := IntToStr(Obj.FontId)
        Else If PropName = 'LineWidth'   Then Result := IntToStr(Obj.LineWidth)
        Else If PropName = 'Style'       Then Result := IntToStr(Obj.Style)
        Else If PropName = 'IOType'      Then Result := IntToStr(Obj.IOType)
        Else If PropName = 'Alignment'   Then Result := IntToStr(Obj.Alignment)
        Else If PropName = 'Electrical'  Then Result := IntToStr(Obj.Electrical)
        Else If PropName = 'Color'       Then Result := IntToStr(Obj.Color)
        Else If PropName = 'AreaColor'   Then Result := IntToStr(Obj.AreaColor)
        Else If PropName = 'TextColor'   Then Result := IntToStr(Obj.TextColor)
        Else If PropName = 'Justification' Then Result := IntToStr(Obj.Justification)

        // Coord properties (returned in mils)
        Else If PropName = 'Width'       Then Result := IntToStr(CoordToMils(Obj.Width))
        Else If PropName = 'PinLength'   Then Result := IntToStr(CoordToMils(Obj.PinLength))

        // ConnectionX / ConnectionY: the pin's ELECTRICAL end, i.e. the
        // point a wire or net label must sit on to attach. Pin.Location is
        // the BODY-side root, so callers were deriving this by hand and
        // getting it wrong. Orientation is the direction the electrical end
        // points away from the body: 0=right(+x) 1=up(+y) 2=left(-x)
        // 3=down(-y). Same convention as the pin dump in Proj_GetComponentInfo.
        Else If PropName = 'ConnectionX' Then
        Begin
            POrient := 0; PLen := 0; PCoord := 0;
            Try POrient := Obj.Orientation; Except End;
            Try PLen := Obj.PinLength; Except End;
            Try PCoord := Obj.Location.X; Except End;
            If POrient = 0 Then PCoord := PCoord + PLen
            Else If POrient = 2 Then PCoord := PCoord - PLen;
            Result := IntToStr(CoordToMils(PCoord));
        End
        Else If PropName = 'ConnectionY' Then
        Begin
            POrient := 0; PLen := 0; PCoord := 0;
            Try POrient := Obj.Orientation; Except End;
            Try PLen := Obj.PinLength; Except End;
            Try PCoord := Obj.Location.Y; Except End;
            If POrient = 1 Then PCoord := PCoord + PLen
            Else If POrient = 3 Then PCoord := PCoord - PLen;
            Result := IntToStr(CoordToMils(PCoord));
        End
        Else If PropName = 'XSize'       Then Result := IntToStr(CoordToMils(Obj.XSize))
        Else If PropName = 'YSize'       Then Result := IntToStr(CoordToMils(Obj.YSize))

        // BoundingRectangle on ISch_Component returns a TCoordRect.
        // Audit code reads X1/Y1/X2/Y2 to detect overlapping component
        // bodies. The same accessors are valid on other geometric sch
        // objects; we late-bind so the get-side stays type-agnostic.
        Else If PropName = 'BoundingRectangle.X1' Then Result := IntToStr(CoordToMils(Obj.BoundingRectangle.X1))
        Else If PropName = 'BoundingRectangle.Y1' Then Result := IntToStr(CoordToMils(Obj.BoundingRectangle.Y1))
        Else If PropName = 'BoundingRectangle.X2' Then Result := IntToStr(CoordToMils(Obj.BoundingRectangle.X2))
        Else If PropName = 'BoundingRectangle.Y2' Then Result := IntToStr(CoordToMils(Obj.BoundingRectangle.Y2))

        // Polyline vertex access. eWire / eBus / eLine are ISch_Polyline
        // children with GetState_Vertex(i : Integer) : TLocation. Audit
        // code reads the first two vertices to test wire-vs-component
        // segment intersection; higher indices are theoretically valid
        // but rarely needed (most wires are 2-vertex).
        Else If PropName = 'VerticesCount' Then Result := IntToStr(Obj.GetState_VerticesCount)
        Else If PropName = 'Vertex.1.X'   Then Result := IntToStr(CoordToMils(Obj.GetState_Vertex(1).X))
        Else If PropName = 'Vertex.1.Y'   Then Result := IntToStr(CoordToMils(Obj.GetState_Vertex(1).Y))
        Else If PropName = 'Vertex.2.X'   Then Result := IntToStr(CoordToMils(Obj.GetState_Vertex(2).X))
        Else If PropName = 'Vertex.2.Y'   Then Result := IntToStr(CoordToMils(Obj.GetState_Vertex(2).Y))
        // Indexed / bulk vertex access, delegated so the parsing stays out
        // of this chain: Vertex<N>.X, Vertex<N>.Y, VertexLast.X,
        // VertexLast.Y and `Vertices` (a compact `x1,y1;x2,y2` dump).
        // Matched case-insensitively and placed AFTER the exact-match
        // forms above so Vertex.1.X / Vertex.2.X and VerticesCount keep
        // their current behaviour. GetSchVertexProperty returns '' for
        // anything it cannot resolve, which is what an unknown PropName
        // yields here anyway.
        Else If (UpperCase(Copy(PropName, 1, 6)) = 'VERTEX')
             Or (UpperCase(PropName) = 'VERTICES') Then
            Result := GetSchVertexProperty(Obj, PropName)

        // Boolean properties
        Else If PropName = 'IsHidden'    Then Result := BoolToJsonStr(Obj.IsHidden)
        Else If PropName = 'IsSolid'     Then Result := BoolToJsonStr(Obj.IsSolid)
        Else If PropName = 'IsMirrored'  Then Result := BoolToJsonStr(Obj.IsMirrored);
    Except
        Result := '';
    End;
End;

{..............................................................................}
{ Generic Property Setter                                                     }
{ Sets a named property on a schematic object from a string value.           }
{ Coordinates are expected in mils. Caller handles BeginModify/EndModify.    }
{..............................................................................}

Function SetSchProperty(Obj : ISch_GraphicalObject; PropName : String; Value : String) : Integer;
{ Returns: 1 = handled, 0 = unknown property name, -1 = write threw.        }
{ Unknown and failed names are appended to the module-level _PropertyDiag   }
{ so Gen_BatchModify can surface them in the response. Existing callers     }
{ that discard the return value still get the old behaviour.                }
Var
    Loc : TLocation;
    Crn : TLocation;
    R : ISch_Rectangle;
    L : ISch_Line;
    Comp : ISch_Component;
    Matched : Boolean;
Begin
    { Measured on a live document: callers using modify_objects /             }
    { batch_modify                                                            }
    { with a pipe-combined set like `Location.X=200|Orientation=2` on an ePin }
    { saw Location.X take effect but Orientation silently dropped. Writing    }
    { Location on a pin triggers a re-layout that can snapshot the previous   }
    { Orientation. Workaround until the multi-set parser applies properties   }
    { in an Altium-safe order (Orientation first, then Location): split the   }
    { combined set into two separate ops, the second filtering on the NEW    }
    { Location.X so it still matches the moved pin.                          }
    Result := 0;
    Matched := True;
    Try
        // Coordinates (expected in mils). `Obj.Location` returns a copy of
        // the TLocation record via the GetState_Location reader; writing
        // directly to `.X` / `.Y` on that copy is silently discarded. Read
        // the whole record, patch the target field, write it back.
        If PropName = 'Location.X' Then
        Begin
            Loc := Obj.Location;
            Loc.X := MilsToCoord(StrToIntDef(Value, 0));
            { A COMPONENT owns child primitives (pins, designator, comment). }
            { Assigning Location moves only the component record and leaves  }
            { every pin at its old coordinate, silently desynchronising the  }
            { symbol from its own pins, and it 16-bit-truncates coords.      }
            { MoveToXY is the documented whole-component move and is already }
            { what the placement path (Gen_PlaceComponents) uses. Any        }
            { exception propagates to the outer handler as Result = -1       }
            { rather than being swallowed, so a failed move is never         }
            { reported as applied.                                           }
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Comp.MoveToXY(Loc.X, Loc.Y);
            End
            Else
                Obj.Location := Loc;
        End
        Else If PropName = 'Location.Y' Then
        Begin
            Loc := Obj.Location;
            Loc.Y := MilsToCoord(StrToIntDef(Value, 0));
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Comp.MoveToXY(Loc.X, Loc.Y);
            End
            Else
                Obj.Location := Loc;
        End
        // Corner lives on ISch_Rectangle and ISch_Line only (not on the base
        // ISch_GraphicalObject, the compiler rejects Obj.Corner regardless
        // of assignment target). Dispatch on ObjectId and narrow to a typed
        // local before touching Corner. See GetSchProperty for the read side.
        Else If (PropName = 'Corner.X') Or (PropName = 'Corner.Y') Then
        Begin
            If (Obj.ObjectId = eRectangle) Or (Obj.ObjectId = eRoundRectangle) Then
            Begin
                R := Obj;
                Crn := R.Corner;
                If PropName = 'Corner.X' Then
                    Crn.X := MilsToCoord(StrToIntDef(Value, 0))
                Else
                    Crn.Y := MilsToCoord(StrToIntDef(Value, 0));
                R.Corner := Crn;
            End
            Else If Obj.ObjectId = eLine Then
            Begin
                L := Obj;
                Crn := L.Corner;
                If PropName = 'Corner.X' Then
                    Crn.X := MilsToCoord(StrToIntDef(Value, 0))
                Else
                    Crn.Y := MilsToCoord(StrToIntDef(Value, 0));
                L.Corner := Crn;
            End;
        End

        // String properties (late-bound across all types, primitives only)
        Else If PropName = 'Text'        Then Obj.Text := Value
        Else If PropName = 'Name'        Then Obj.Name := Value
        Else If PropName = 'LibReference'       Then Obj.LibReference := Value
        // SourceLibraryName is the design-cache field that records which
        // library a placed component came from. It is read in GetSchProperty
        // but had no write case, so obj_modify / batch_modify silently no-oped
        // (recorded only as an "unknown property"). Clearing it to '' is the
        // canonical way to detach a part from a stale source-library binding.
        Else If PropName = 'SourceLibraryName'  Then Obj.SourceLibraryName := Value
        // DesignItemId is the library ITEM the placed part re-matches
        // against ("Design Item ID" in the UI). It is a component
        // PROPERTY, not a parameter: stamping a parameter named
        // DesignItemId just creates a stray user parameter, and with no
        // write case here obj_modify silently no-oped while reporting
        // matched. A stale DesignItemId after a library re-link is what
        // produces the <Not Found> state in the Properties panel.
        Else If PropName = 'DesignItemId'       Then Obj.DesignItemId := Value
        // `Description` is the natural name (matches get_component_info /
        // BOM column / lib_set_component_description); `ComponentDescription`
        // is what ISch_Component actually exposes -- both accepted.
        Else If (PropName = 'ComponentDescription') Or (PropName = 'Description') Then
            Obj.ComponentDescription := Value
        { UniqueId is how Altium groups multi-part sub-parts into one physical
          component for ECO. The UniqueId property setter remints when the
          value is already in the document; SetState_UniqueId is the SDK
          writer (same property) and is the path to try first. }
        Else If PropName = 'UniqueId' Then
        Begin
            Comp := Obj;
            Try Comp.SetState_UniqueId(Value); Except End;
            Try Comp.UniqueId := Value; Except End;
        End
        Else If PropName = 'CurrentPartID' Then
        Begin
            Comp := Obj;
            Try Comp.SetState_CurrentPartID(StrToIntDef(Value, 1)); Except End;
            Try Comp.CurrentPartID := StrToIntDef(Value, 1); Except End;
        End
        Else If PropName = 'PartCount' Then
        Begin
            { LoadComponentFromLibrary stamps PartCount=4 on this UL
              TLV9024PWR even though the SchLib reports part_count=5.
              Without a write path the supply unit cannot be selected.
              Do NOT call SetState_PartCount: that identifier is not on
              ISch_Component in this DelphiScript, and undeclared
              identifiers raise a modal that bypasses Try/Except. }
            Comp := Obj;
            Try Comp.PartCount := StrToIntDef(Value, 1); Except End;
        End

        // Sub-object string properties (compound interfaces, typed cast required)
        Else If (PropName = 'Designator') Or (PropName = 'Designator.Text') Then
            SetSchComponentSubText(Obj, 'Designator', Value)
        Else If (PropName = 'Comment') Or (PropName = 'Comment.Text') Then
            SetSchComponentSubText(Obj, 'Comment', Value)

        // Integer properties
        Else If PropName = 'Orientation' Then Obj.Orientation := StrToIntDef(Value, 0)
        Else If PropName = 'FontId'      Then Obj.FontId := StrToIntDef(Value, 1)
        Else If PropName = 'LineWidth'   Then Obj.LineWidth := StrToIntDef(Value, 1)
        Else If PropName = 'Style'       Then Obj.Style := StrToIntDef(Value, 0)
        Else If PropName = 'IOType'      Then Obj.IOType := StrToIntDef(Value, 0)
        Else If PropName = 'Alignment'   Then Obj.Alignment := StrToIntDef(Value, 0)
        Else If PropName = 'Electrical'  Then Obj.Electrical := ElectricalOrdinal(Value)
        Else If PropName = 'Color'       Then Obj.Color := StrToIntDef(Value, 0)
        Else If PropName = 'AreaColor'   Then Obj.AreaColor := StrToIntDef(Value, 0)
        Else If PropName = 'TextColor'   Then Obj.TextColor := StrToIntDef(Value, 0)
        Else If PropName = 'Justification' Then Obj.Justification := StrToIntDef(Value, 0)

        // Coord properties (expected in mils)
        Else If PropName = 'Width'       Then Obj.Width := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'PinLength'   Then Obj.PinLength := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'XSize'       Then Obj.XSize := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'YSize'       Then Obj.YSize := MilsToCoord(StrToIntDef(Value, 0))

        // Boolean properties
        Else If PropName = 'IsHidden'    Then Obj.IsHidden := StrToBool(Value)
        Else If PropName = 'IsSolid'     Then Obj.IsSolid := StrToBool(Value)
        Else If PropName = 'IsMirrored'  Then Obj.IsMirrored := StrToBool(Value)
        Else If PropName = 'Selection'   Then Obj.Selection := StrToBool(Value)
        Else Matched := False;

        If Matched Then Result := 1
        Else Result := 0;
    Except
        Result := -1;
    End;

    If Result = 0 Then NotePropertyDiag('unknown', PropName)
    Else If Result = -1 Then NotePropertyDiag('failed', PropName);
End;

{..............................................................................}
{ Filter matching                                                             }
{ FilterStr format: "PropName=Value|PropName2=Value2" (AND logic)            }
{ Empty filter matches everything.                                           }
{..............................................................................}

Function MatchesFilter(Obj : ISch_GraphicalObject; FilterStr : String) : Boolean;
Var
    Remaining, Condition, PropName, Expected, Actual : String;
    PipePos, EqPos : Integer;
Begin
    Result := True;
    If FilterStr = '' Then Exit;

    Remaining := FilterStr;
    While Remaining <> '' Do
    Begin
        // Extract next pipe-separated condition
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Condition := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Condition := Remaining;
            Remaining := '';
        End;

        // Parse "PropName=Value"
        EqPos := Pos('=', Condition);
        If EqPos = 0 Then Continue;
        PropName := Copy(Condition, 1, EqPos - 1);
        Expected := Copy(Condition, EqPos + 1, Length(Condition));

        // Compare
        Actual := GetSchProperty(Obj, PropName);
        If Actual <> Expected Then
        Begin
            Result := False;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ Parse comma-separated property names into JSON for one object              }
{..............................................................................}

Function BuildObjectJson(Obj : ISch_GraphicalObject; PropsStr : String) : String;
Var
    Remaining, PropName, PropValue : String;
    CommaPos : Integer;
    First : Boolean;
Begin
    Result := '{';
    First := True;
    Remaining := PropsStr;

    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            PropName := Copy(Remaining, 1, CommaPos - 1);
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else
        Begin
            PropName := Remaining;
            Remaining := '';
        End;

        PropValue := GetSchProperty(Obj, PropName);

        If Not First Then Result := Result + ',';
        First := False;
        Result := Result + '"' + EscapeJsonString(PropName) + '":"' + EscapeJsonString(PropValue) + '"';
    End;

    Result := Result + '}';
End;

{..............................................................................}
{ Apply pipe-separated "PropName=Value" assignments to an object             }
{..............................................................................}

Procedure ApplySetProperties(Obj : ISch_GraphicalObject; SetStr : String);
{ Location.X and Location.Y arriving in the SAME pipe-combined set used to be }
{ applied as two independent SetSchProperty writes. Measured on a live        }
{ eSchComponent: `Location.X=1200|Location.Y=7600` moved X and left Y at its  }
{ old value. Each write re-reads Obj.Location, and the copy handed back after }
{ the first move still carried the PREVIOUS coordinates, so writing it back   }
{ reverted the move that had just been made. Same class of bug as the         }
{ Location.X + Orientation interaction documented in SetSchProperty.          }
{                                                                             }
{ Fix: parse the whole set FIRST, coalesce both axes into ONE positional      }
{ write, then apply every remaining property. A component gets MoveToXY so    }
{ its child pins travel with the body (see SetSchProperty); everything else   }
{ owns no child primitives, so a plain Location assignment is correct for it  }
{ (a net label in particular must NOT go through MoveToXY -- it has no        }
{ MoveToXY and is not a component).                                           }
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
    Loc : TLocation;
    Comp : ISch_Component;
    HasX, HasY : Boolean;
    NewX, NewY : Integer;
Begin
    { Pass 1: collect the positional assignments without applying anything. }
    HasX := False;
    HasY := False;
    NewX := 0;
    NewY := 0;
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Assignment := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Assignment := Remaining;
            Remaining := '';
        End;

        EqPos := Pos('=', Assignment);
        If EqPos = 0 Then Continue;
        PropName := Copy(Assignment, 1, EqPos - 1);
        PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));

        If PropName = 'Location.X' Then
        Begin
            HasX := True;
            NewX := StrToIntDef(PropValue, 0);
        End
        Else If PropName = 'Location.Y' Then
        Begin
            HasY := True;
            NewY := StrToIntDef(PropValue, 0);
        End;
    End;

    { One positional write covering whichever axes were supplied. }
    If HasX Or HasY Then
    Begin
        Try
            Loc := Obj.Location;
            If HasX Then Loc.X := MilsToCoord(NewX);
            If HasY Then Loc.Y := MilsToCoord(NewY);
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Comp.MoveToXY(Loc.X, Loc.Y);
            End
            Else
                Obj.Location := Loc;
        Except
            NotePropertyDiag('failed', 'Location');
        End;
    End;

    { Pass 2: every non-positional property, in the order it was given. }
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Assignment := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Assignment := Remaining;
            Remaining := '';
        End;

        EqPos := Pos('=', Assignment);
        If EqPos = 0 Then Continue;
        PropName := Copy(Assignment, 1, EqPos - 1);
        PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));

        { Already applied above as part of the coalesced positional write. }
        If (PropName = 'Location.X') Or (PropName = 'Location.Y') Then Continue;

        SetSchProperty(Obj, PropName, PropValue);
    End;
End;

{..............................................................................}
{ Pull an optional owner-designator constraint (OwnerDesignator=X or          }
{ Designator=X) out of a pipe-separated parameter filter so a delete can      }
{ target one component (e.g. U1) instead of every part on the sheet. Returns  }
{ the designator value and rewrites FilterStr to the remaining conditions.    }
{..............................................................................}

Function PullOwnerDesignator(Var FilterStr : String) : String;
Var
    Remaining, Condition, Rest, PropName, PropUpper : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
    Rest := '';
    Remaining := FilterStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Condition := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Condition := Remaining;
            Remaining := '';
        End;

        EqPos := Pos('=', Condition);
        If EqPos > 0 Then
        Begin
            PropName := Copy(Condition, 1, EqPos - 1);
            PropUpper := UpperCase(PropName);
            If (PropUpper = 'OWNERDESIGNATOR') Or (PropUpper = 'DESIGNATOR') Then
            Begin
                Result := Copy(Condition, EqPos + 1, Length(Condition));
                Continue;
            End;
        End;

        { Keep every non-owner condition in the reduced filter. }
        If Rest = '' Then Rest := Condition
        Else Rest := Rest + '|' + Condition;
    End;
    FilterStr := Rest;
End;

{..............................................................................}
{ Delete schematic parameters by their REAL owner. A component parameter is   }
{ owned by its ISch_Component (created via Comp.AddSchObject), so             }
{ SchDoc.RemoveSchObject silently no-ops on it - the same wrong-owner trap    }
{ the sheet-entry delete works around. It must be removed via                 }
{ Comp.RemoveSchObject. Sheet-level (title-block) parameters are owned by the }
{ document and removed via SchDoc.RemoveSchObject. An optional owner          }
{ designator in the filter restricts the delete to one component. Caller      }
{ wraps this in PreProcess/PostProcess.                                       }
{..............................................................................}

Procedure DeleteParametersAnyOwner(SchDoc : ISch_Document; FilterStr : String;
    Var TotalMatched : Integer);
Var
    OwnerDesig, ReducedFilter, CompDesig : String;
    CompIter, ParamIter : ISch_Iterator;
    Comp : ISch_Component;
    Param, FoundParam : ISch_GraphicalObject;
    Guard : Integer;
Begin
    ReducedFilter := FilterStr;
    OwnerDesig := PullOwnerDesignator(ReducedFilter);

    { Component-owned parameters. Guard against a catastrophic "delete every   }
    { parameter on every part": require an owner designator or a non-empty     }
    { reduced filter before touching component parameters.                     }
    If (OwnerDesig <> '') Or (ReducedFilter <> '') Then
    Begin
        CompIter := SchDoc.SchIterator_Create;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Comp := CompIter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                CompDesig := '';
                Try CompDesig := Comp.Designator.Text; Except End;
                If (OwnerDesig = '') Or
                   (UpperCase(CompDesig) = UpperCase(OwnerDesig)) Then
                Begin
                    { Re-scan after each removal to avoid iterator            }
                    { invalidation; removing a child does not disturb the     }
                    { outer component iterator.                               }
                    Guard := 10000;
                    While Guard > 0 Do
                    Begin
                        FoundParam := Nil;
                        ParamIter := Comp.SchIterator_Create;
                        Try
                            ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                            Param := ParamIter.FirstSchObject;
                            While Param <> Nil Do
                            Begin
                                If MatchesFilter(Param, ReducedFilter) Then
                                Begin
                                    FoundParam := Param;
                                    Break;
                                End;
                                Param := ParamIter.NextSchObject;
                            End;
                        Finally
                            Comp.SchIterator_Destroy(ParamIter);
                        End;
                        If FoundParam = Nil Then Break;
                        Comp.RemoveSchObject(FoundParam);
                        Inc(TotalMatched);
                        Dec(Guard);
                    End;
                End;
                Comp := CompIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(CompIter);
        End;
    End;

    { Sheet-level (document-owned) parameters. Skipped when an owner           }
    { designator was given - that means "this component only".                }
    If OwnerDesig = '' Then
    Begin
        Guard := 10000;
        While Guard > 0 Do
        Begin
            FoundParam := Nil;
            ParamIter := SchDoc.SchIterator_Create;
            Try
                ParamIter.SetState_IterationDepth(eIterateFirstLevel);
                ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                Param := ParamIter.FirstSchObject;
                While Param <> Nil Do
                Begin
                    If MatchesFilter(Param, ReducedFilter) Then
                    Begin
                        FoundParam := Param;
                        Break;
                    End;
                    Param := ParamIter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(ParamIter);
            End;
            If FoundParam = Nil Then Break;
            SchDoc.RemoveSchObject(FoundParam);
            Inc(TotalMatched);
            Dec(Guard);
        End;
    End;
End;

{..............................................................................}
{ Helper: Process objects in a single SchDoc                                  }
{ Mode: 'query', 'modify', 'delete'                                         }
{..............................................................................}

Function ProcessSchDocObjects(SchDoc : ISch_Document; ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; DocPath : String;
    Var TotalMatched : Integer; Limit : Integer) : String;
Var
    Iterator, SymIter : ISch_Iterator;
    Obj, FoundObj : ISch_GraphicalObject;
    Sym : ISch_SheetSymbol;
    Removed : Boolean;
    ObjJson : String;
    First : Boolean;
    MaxIter : Integer;
Begin
    Result := '';
    First := (TotalMatched = 0);

    // Delete mode: one-at-a-time to avoid iterator invalidation.
    If Mode = 'delete' Then
    Begin
        SchServer.ProcessControl.PreProcess(SchDoc, '');

        { Parameters are owned by their component (or by the document for   }
        { sheet-level params), NOT reachable/removable through the plain    }
        { doc-level RemoveSchObject loop below. Dispatch by owner.          }
        If ObjTypeInt = eParameter Then
        Begin
            DeleteParametersAnyOwner(SchDoc, FilterStr, TotalMatched);
            SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
            Exit;
        End;

        MaxIter := 100000;
        While MaxIter > 0 Do
        Begin
            Iterator := SchDoc.SchIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            FoundObj := Nil;
            Obj := Iterator.FirstSchObject;
            While Obj <> Nil Do
            Begin
                If MatchesFilter(Obj, FilterStr) Then
                Begin
                    FoundObj := Obj;
                    Break;
                End;
                Obj := Iterator.NextSchObject;
            End;
            SchDoc.SchIterator_Destroy(Iterator);
            If FoundObj = Nil Then Break;
            { Sheet entries belong to their parent sheet symbol's child       }
            { container, not the SchDoc. SchDoc.RemoveSchObject silently      }
            { no-ops on them, leaving the entry placed. Walk sheet symbols    }
            { until one of them accepts the remove. Try/Except absorbs the    }
            { wrong-parent failures.                                          }
            If ObjTypeInt = eSheetEntry Then
            Begin
                Removed := False;
                SymIter := SchDoc.SchIterator_Create;
                SymIter.AddFilter_ObjectSet(MkSet(eSheetSymbol));
                Try
                    Sym := SymIter.FirstSchObject;
                    While Sym <> Nil Do
                    Begin
                        Try
                            Sym.RemoveSchObject(FoundObj);
                            Removed := True;
                            Break;
                        Except End;
                        Sym := SymIter.NextSchObject;
                    End;
                Finally
                    SchDoc.SchIterator_Destroy(SymIter);
                End;
                If Not Removed Then
                    Try SchDoc.RemoveSchObject(FoundObj); Except End;
            End
            Else
                SchDoc.RemoveSchObject(FoundObj);
            Inc(TotalMatched);
            Dec(MaxIter);
        End;
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
        Exit;
    End;

    // Modify mode: wrap in PreProcess/PostProcess for undo support.
    If Mode = 'modify' Then
        SchServer.ProcessControl.PreProcess(SchDoc, '');

    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));

    Obj := Iterator.FirstSchObject;
    While Obj <> Nil Do
    Begin
        If (Limit > 0) And (TotalMatched >= Limit) Then Break;

        If MatchesFilter(Obj, FilterStr) Then
        Begin
            If Mode = 'query' Then
            Begin
                ObjJson := BuildObjectJson(Obj, PropsStr);
                If Length(ObjJson) <= 2 Then
                    ObjJson := '{"_doc":"' + EscapeJsonString(DocPath) + '"}'
                Else
                    ObjJson := Copy(ObjJson, 1, 1) + '"_doc":"' + EscapeJsonString(DocPath) + '",' + Copy(ObjJson, 2, Length(ObjJson));
                If Not First Then Result := Result + ',';
                First := False;
                Result := Result + ObjJson;
            End
            Else If Mode = 'modify' Then
            Begin
                // Bracket the property writes in SCHM_BeginModify /
                // SCHM_EndModify so the editor sub-systems and the undo
                // stack observe the edit. Without these the property is
                // updated in memory but the UI never re-renders and
                // SaveAll may skip the doc.
                SchBeginModify(Obj);
                ApplySetProperties(Obj, SetStr);
                SchEndModify(Obj);
            End;

            Inc(TotalMatched);
        End;

        Obj := Iterator.NextSchObject;
    End;

    SchDoc.SchIterator_Destroy(Iterator);

    If Mode = 'modify' Then
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
End;

{..............................................................................}
{ Helper: Iterate project schematic documents                                 }
{..............................................................................}

Function IterateProjectDocs(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; ProjectPath : String; Limit : Integer) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    I, TotalMatched, SheetsProcessed, SheetsSaved : Integer;
    FilePath, JsonItems : String;
    IsMutating : Boolean;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    If ProjectPath <> '' Then
        Project := FindProjectByPath(Workspace, ProjectPath)
    Else
        Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found');
        Exit;
    End;

    TotalMatched := 0;
    SheetsProcessed := 0;
    SheetsSaved := 0;
    JsonItems := '';
    IsMutating := (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create');

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;

        FilePath := Doc.DM_FullPath;

        // Do NOT force-open documents. Calling RunProcess('Client:OpenDocument')
        // loads the file but strips its project association, so it appears
        // as a "free document" with the absolute path as its tab title,
        // clutters the UI and breaks project-member semantics.
        //
        // Instead, only iterate documents that SchServer already has in
        // memory. If a project sheet isn't loaded (DM_Compile didn't wake
        // it up for some reason), silently skip it. The user can open it
        // manually in Altium and re-run the query.
        SchDoc := SchServer.GetSchDocumentByPath(FilePath);
        If SchDoc = Nil Then Continue;

        JsonItems := JsonItems + ProcessSchDocObjects(SchDoc, ObjTypeInt,
            FilterStr, PropsStr, SetStr, Mode, FilePath, TotalMatched, Limit);

        If IsMutating Then
        Begin
            Try SchDoc.GraphicallyInvalidate; Except End;
            // SaveDocByPath does SetModified + DoFileSave, which writes
            // directly to disk and bypasses SaveAll's non-active-doc blind spot.
            SaveDocByPath(FilePath);
            Inc(SheetsSaved);
        End;

        Inc(SheetsProcessed);

        If (Limit > 0) And (TotalMatched >= Limit) Then Break;
    End;

    If Mode = 'query' Then
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched) +
            ',"sheets_processed":' + IntToStr(SheetsProcessed) + '}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched) +
            ',"sheets_processed":' + IntToStr(SheetsProcessed) +
            ',"sheets_saved":' + IntToStr(SheetsSaved) + '}');
End;

{..............................................................................}
{ Helper: Process active document only                                       }
{..............................................................................}

Function ProcessActiveDoc(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    TotalMatched : Integer;
    JsonItems, DocPath, SavedStr : String;
    IsMutating, Saved : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    DocPath := SchDoc.DocumentName;
    TotalMatched := 0;
    JsonItems := ProcessSchDocObjects(SchDoc, ObjTypeInt,
        FilterStr, PropsStr, SetStr, Mode, DocPath, TotalMatched, Limit);

    IsMutating := (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create');
    Saved := False;
    If IsMutating Then
    Begin
        Try SchDoc.GraphicallyInvalidate; Except End;
        SaveDocByPath(DocPath);
        Saved := True;
    End;

    If Mode = 'query' Then
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched) + '}')
    Else
    Begin
        If Saved Then SavedStr := 'true' Else SavedStr := 'false';
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched) + ',"saved":' + SavedStr + '}');
    End;
End;

{..............................................................................}
{ Helper: Process a SPECIFIC document by file path (no focus change)          }
{..............................................................................}

Function ProcessDocByPath(DocPath : String; ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    TotalMatched : Integer;
    JsonItems, SavedStr : String;
    IsMutating, Saved : Boolean;
Begin

    // Do NOT RunProcess Client:OpenDocument, that loads the file but
    // strips any project association, producing a "free document" in the
    // UI with the full path as its tab title. Require the document to
    // already be open in Altium; the caller has to open it first.
    SchDoc := SchServer.GetSchDocumentByPath(DocPath);
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'Document not loaded: ' + DocPath +
            '. Open it in Altium first, then retry.');
        Exit;
    End;

    TotalMatched := 0;
    JsonItems := ProcessSchDocObjects(SchDoc, ObjTypeInt,
        FilterStr, PropsStr, SetStr, Mode, DocPath, TotalMatched, Limit);

    IsMutating := (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create');
    Saved := False;
    If IsMutating Then
    Begin
        Try SchDoc.GraphicallyInvalidate; Except End;
        SaveDocByPath(DocPath);
        Saved := True;
    End;

    If Mode = 'query' Then
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched) + '}')
    Else
    Begin
        If Saved Then SavedStr := 'true' Else SavedStr := 'false';
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched) + ',"saved":' + SavedStr + '}');
    End;
End;

{..............................................................................}
{ Helper: Parse scope value into type + optional file path.                    }
{                                                                              }
{ Wire form (structured, sent by the Python helper) is a JSON object with     }
{ a "type" field (active_doc / project / doc) and an optional "file_path"     }
{ field. Top-level scope values arriving from MCP tools always use this form. }
{                                                                              }
{ For batch-operation strings (compact key=value;...~~ encoding) the scope    }
{ is still a plain string token: active_doc / project / doc:path /            }
{ project:path. ParseScope handles both forms, JSON-object first, then the   }
{ legacy compact form for batch-op fields.                                    }
{..............................................................................}

Procedure ParseScope(Scope : String; Var ScopeType : String; Var ScopePath : String);
Var
    InnerType, InnerPath : String;
Begin
    ScopeType := 'active_doc';
    ScopePath := '';

    If Scope = '' Then Exit;

    // Structured form: {"type":"...","file_path":"..."}
    If Copy(Scope, 1, 1) = '{' Then
    Begin
        InnerType := ExtractJsonValue(Scope, 'type');
        InnerPath := ExtractJsonValue(Scope, 'file_path');
        If InnerType <> '' Then
            ScopeType := InnerType;
        If InnerPath <> '' Then
            ScopePath := InnerPath;
        Exit;
    End;

    // Legacy compact form used inside batch-op strings only.
    If Copy(Scope, 1, 4) = 'doc:' Then
    Begin
        ScopeType := 'doc';
        ScopePath := Copy(Scope, 5, Length(Scope));
    End
    Else If Copy(Scope, 1, 8) = 'project:' Then
    Begin
        ScopeType := 'project';
        ScopePath := Copy(Scope, 9, Length(Scope));
    End
    Else If Copy(Scope, 1, 14) = 'lib_component:' Then
    Begin
        { Target a named symbol inside the active SchLib. ScopePath carries }
        { the lib-ref name (not a file path), optionally suffixed '@N' to   }
        { select part N of a multi-part symbol. The suffix is left on the   }
        { string here and split by ApplyLibComponentScope, so ParseScope's  }
        { signature stays as every other caller expects it.                 }
        ScopeType := 'lib_component';
        ScopePath := Copy(Scope, 15, Length(Scope));
    End
    Else
        ScopeType := Scope;
End;

{..............................................................................}
{ If ScopeType is 'lib_component', switch the active SchLib to the named      }
{ symbol (ScopePath holds the lib-ref) and rewrite ScopeType to 'active_doc'  }
{ so the normal active-doc path then iterates that symbol's primitives. This  }
{ folds what used to be a separate set_current_component call into the same   }
{ request. Returns False if no such component exists in the active library.  }
{..............................................................................}
Function ApplyLibComponentScope(Var ScopeType : String; ScopePath : String) : Boolean;
Var
    AtPos, PartId, I : Integer;
    CompName, PartStr : String;
Begin
    Result := True;
    If ScopeType <> 'lib_component' Then Exit;

    { Optional '@N' suffix selects part N of a multi-part symbol. A SchLib  }
    { iterator only yields the CURRENT part's primitives, so without this   }
    { every query/modify/delete on a multi-part component could only ever   }
    { reach part 1 and correcting parts 2..N meant a full rebuild.          }
    { Scan from the RIGHT: a lib-ref may legitimately contain '@'.          }
    CompName := ScopePath;
    PartId := 1;
    AtPos := 0;
    For I := Length(ScopePath) DownTo 1 Do
        If ScopePath[I] = '@' Then
        Begin
            AtPos := I;
            Break;
        End;
    If AtPos > 1 Then
    Begin
        PartStr := Copy(ScopePath, AtPos + 1, Length(ScopePath));
        If (PartStr <> '') And IsIntStr(PartStr) Then
        Begin
            PartId := StrToIntDef(PartStr, 1);
            CompName := Copy(ScopePath, 1, AtPos - 1);
            If PartId < 1 Then PartId := 1;
        End;
    End;

    If SelectLibComponentPart(CompName, PartId) = Nil Then
        Result := False
    Else
        ScopeType := 'active_doc';
End;

{..............................................................................}
{ PRIMITIVE 1: query_objects                                                  }
{ Params: scope, object_type, filter, properties                             }
{..............................................................................}

Function Gen_QueryObjects(Params : String; RequestId : String) : String;
Var
    Scope, ObjTypeStr, FilterStr, PropsStr, ScopeType, ScopePath : String;
    ObjTypeInt, Limit : Integer;
Begin
    Scope := ExtractJsonValue(Params, 'scope');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    PropsStr := ExtractJsonValue(Params, 'properties');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 0);

    If PropsStr = '' Then PropsStr := 'Location.X,Location.Y';
    ParseScope(Scope, ScopeType, ScopePath);
    If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Library component not found in active library: ' + ScopePath);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        If ScopeType = 'project' Then
            Result := IterateProjectDocs(ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, ScopePath, Limit)
        Else If ScopeType = 'doc' Then
            Result := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, Limit)
        Else
            Result := ProcessActiveDoc(ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, Limit);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, Limit);
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown object type: ' + ObjTypeStr);
End;

{..............................................................................}
{ PRIMITIVE 2: modify_objects                                                 }
{ Params: scope, object_type, filter, set                                    }
{..............................................................................}

Function Gen_ModifyObjects(Params : String; RequestId : String) : String;
Var
    Scope, ObjTypeStr, FilterStr, SetStr, ScopeType, ScopePath : String;
    ObjTypeInt : Integer;
Begin
    Scope := ExtractJsonValue(Params, 'scope');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    SetStr := ExtractJsonValue(Params, 'set');

    If SetStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'set parameter is required');
        Exit;
    End;

    ParseScope(Scope, ScopeType, ScopePath);
    If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Library component not found in active library: ' + ScopePath);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        If ScopeType = 'project' Then
            Result := IterateProjectDocs(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, ScopePath, 0)
        Else If ScopeType = 'doc' Then
            Result := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0)
        Else
            Result := ProcessActiveDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown object type: ' + ObjTypeStr);
End;

{..............................................................................}
{ PRIMITIVE 3: create_object                                                  }
{ Params: object_type, properties, container                                  }
{..............................................................................}

Function Gen_CreateObject(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, PropsStr, Container : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    NewObj : ISch_GraphicalObject;
Begin
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    PropsStr := ExtractJsonValue(Params, 'properties');
    Container := ExtractJsonValue(Params, 'container');
    If Container = '' Then Container := 'document';

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt = -1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown object type: ' + ObjTypeStr);
        Exit;
    End;

    // Create the object
    NewObj := SchServer.SchObjectFactory(ObjTypeInt, eCreate_Default);
    If NewObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create object of type: ' + ObjTypeStr);
        Exit;
    End;

    // Set properties
    ApplySetProperties(NewObj, PropsStr);

    // Register in container
    If Container = 'component' Then
    Begin
        // Library component container
        SchLib := SchServer.GetCurrentSchDocument;
        If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
        Begin
            SchServer.DestroySchObject(NewObj);
            Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
            Exit;
        End;
        Component := SchLib.CurrentSchComponent;
        If Component = Nil Then
        Begin
            SchServer.DestroySchObject(NewObj);
            Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No library component is selected');
            Exit;
        End;
        SchServer.ProcessControl.PreProcess(SchLib, '');
        Component.AddSchObject(NewObj);
        SchRegisterObject(Component, NewObj);
        SchServer.ProcessControl.PostProcess(SchLib, '');
    End
    Else
    Begin
        // Document container
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            SchServer.DestroySchObject(NewObj);
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
            Exit;
        End;
        SchServer.ProcessControl.PreProcess(SchDoc, '');
        SchDoc.RegisterSchObjectInContainer(NewObj);
        SchRegisterObject(SchDoc, NewObj);
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    Result := BuildSuccessResponse(RequestId, '{"created":true,"object_type":"' + ObjTypeStr + '"}');
End;

{..............................................................................}
{ PRIMITIVE 4: delete_objects                                                 }
{ Params: scope, object_type, filter                                         }
{..............................................................................}

Function Gen_DeleteObjects(Params : String; RequestId : String) : String;
Var
    Scope, ObjTypeStr, FilterStr, ScopeType, ScopePath : String;
    ObjTypeInt : Integer;
Begin
    Scope := ExtractJsonValue(Params, 'scope');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');

    ParseScope(Scope, ScopeType, ScopePath);
    If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Library component not found in active library: ' + ScopePath);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        If ScopeType = 'project' Then
            Result := IterateProjectDocs(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, ScopePath, 0)
        Else If ScopeType = 'doc' Then
            Result := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0)
        Else
            Result := ProcessActiveDoc(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0);
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown object type: ' + ObjTypeStr);
End;

{..............................................................................}
{ PRIMITIVE 5: run_process (enhanced)                                         }
{ Params: process, params (pipe-separated key=value)                         }
{..............................................................................}

Function Gen_RunProcess(Params : String; RequestId : String) : String;
Var
    ProcessName, ProcessParams : String;
    Remaining, KVPair, Key, Value : String;
    PipePos, EqPos : Integer;
Begin
    ProcessName := ExtractJsonValue(Params, 'process');
    ProcessParams := ExtractJsonValue(Params, 'params');

    If ProcessName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'process parameter is required');
        Exit;
    End;

    ResetParameters;

    // Parse pipe-separated key=value pairs
    If ProcessParams <> '' Then
    Begin
        Remaining := ProcessParams;
        While Remaining <> '' Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos > 0 Then
            Begin
                KVPair := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End
            Else
            Begin
                KVPair := Remaining;
                Remaining := '';
            End;

            EqPos := Pos('=', KVPair);
            If EqPos > 1 Then
            Begin
                Key := Copy(KVPair, 1, EqPos - 1);
                If Key <> '' Then
                Begin
                    Value := Copy(KVPair, EqPos + 1, Length(KVPair));
                    AddStringParameter(Key, Value);
                End;
            End;
        End;
    End;

    RunProcess(ProcessName);
    Result := BuildSuccessResponse(RequestId, '{"success":true,"process":"' + EscapeJsonString(ProcessName) + '"}');
End;

{..............................................................................}
{ PRIMITIVE 6: get_font_spec                                                 }
{ Params: font_id                                                            }
{..............................................................................}

Function Gen_GetFontSpec(Params : String; RequestId : String) : String;
Var
    FontMgr : ISch_FontManager;
    FontId, Size, Rotation : Integer;
    Underline, Italic, Bold, StrikeOut : Boolean;
    FontName : String;
Begin
    FontId := StrToIntDef(ExtractJsonValue(Params, 'font_id'), 1);
    FontMgr := SchServer.FontManager;
    FontMgr.GetFontSpec(FontId, Size, Rotation, Underline, Italic, Bold, StrikeOut, FontName);
    Result := BuildSuccessResponse(RequestId,
        '{"font_id":' + IntToStr(FontId) +
        ',"size":' + IntToStr(Size) +
        ',"rotation":' + IntToStr(Rotation) +
        ',"bold":' + BoolToJsonStr(Bold) +
        ',"italic":' + BoolToJsonStr(Italic) +
        ',"underline":' + BoolToJsonStr(Underline) +
        ',"strikeout":' + BoolToJsonStr(StrikeOut) +
        ',"font_name":"' + EscapeJsonString(FontName) + '"}');
End;

{..............................................................................}
{ PRIMITIVE 7: get_font_id                                                   }
{ Params: size, font_name, bold, italic, rotation, underline, strikeout      }
{..............................................................................}

Function Gen_GetFontId(Params : String; RequestId : String) : String;
Var
    FontMgr : ISch_FontManager;
    FontId, Size, Rotation : Integer;
    Underline, Italic, Bold, StrikeOut : Boolean;
    FontName : String;
Begin
    Size := StrToIntDef(ExtractJsonValue(Params, 'size'), 10);
    FontName := ExtractJsonValue(Params, 'font_name');
    If FontName = '' Then FontName := 'Arial';
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);
    Bold := ExtractJsonValue(Params, 'bold') = 'true';
    Italic := ExtractJsonValue(Params, 'italic') = 'true';
    Underline := ExtractJsonValue(Params, 'underline') = 'true';
    StrikeOut := ExtractJsonValue(Params, 'strikeout') = 'true';

    FontMgr := SchServer.FontManager;
    FontId := FontMgr.GetFontID(Size, Rotation, Underline, Italic, Bold, StrikeOut, FontName);
    Result := BuildSuccessResponse(RequestId, '{"font_id":' + IntToStr(FontId) + '}');
End;

{..............................................................................}
{ Select objects matching filter, sets Selection/Selected on matching objs  }
{..............................................................................}

Function Gen_SelectObjects(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, FilterStr : String;
    ObjTypeInt : Integer;
Begin
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');

    // Route through modify with Selection=true
    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        Result := ProcessActiveDoc(ObjTypeInt, FilterStr, '', 'Selection=true', 'modify', RequestId, 0);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', 'Selected=true', 'modify', RequestId, 0);
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown object type: ' + ObjTypeStr);
End;

{..............................................................................}
{ Deselect all objects on the active document                                }
{..............................................................................}

{ ISch_Document has NO ClearSelection method (raises "Undeclared identifier:
  ClearSelection"). Use the Sch:DeSelectAll process -- iterating objects and
  setting .Selection := False faults on sub-objects (parameters, pin labels)
  that don't expose Selection, and that "Undeclared identifier" modal bypasses
  Try/Except. The process is what Edit|Deselect All runs (see Application.pas). }
Procedure SchDeselectAllObjects(SchDoc : ISch_Document);
Begin
    If SchDoc = Nil Then Exit;
    Try
        ResetParameters;
        RunProcess('Sch:DeSelectAll');
    Except End;
End;

Function Gen_DeselectAll(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc <> Nil Then
    Begin
        SchDeselectAllObjects(SchDoc);
        SchDoc.GraphicallyInvalidate;
        Result := BuildSuccessResponse(RequestId, '{"deselected":true}');
        Exit;
    End;

    Board := GetPCBBoardAnywhere;
    If Board <> Nil Then
    Begin
        ResetParameters;
        AddStringParameter('Scope', 'All');
        RunProcess('PCB:DeSelect');
        Result := BuildSuccessResponse(RequestId, '{"deselected":true}');
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active document');
End;

{..............................................................................}
{ Zoom viewport: fit, selection, or region                                   }
{..............................................................................}

Function Gen_Zoom(Params : String; RequestId : String) : String;
Var
    Action : String;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    Action := ExtractJsonValue(Params, 'action');
    If Action = '' Then Action := 'fit';

    SchDoc := SchServer.GetCurrentSchDocument;
    Board := GetPCBBoardAnywhere;

    If Action = 'fit' Then
    Begin
        If SchDoc <> Nil Then RunProcess('Sch:ZoomToFit')
        Else If Board <> Nil Then
        Begin
            ResetParameters;
            AddStringParameter('Action', 'ZoomToFit');
            RunProcess('PCB:Zoom');
        End;
    End
    Else If Action = 'selection' Then
    Begin
        If SchDoc <> Nil Then RunProcess('Sch:ZoomToSelected')
        Else If Board <> Nil Then
        Begin
            ResetParameters;
            AddStringParameter('Action', 'ZoomToSelection');
            RunProcess('PCB:Zoom');
        End;
    End;

    Result := BuildSuccessResponse(RequestId, '{"action":"' + Action + '"}');
End;

{..............................................................................}
{ BATCH MODIFY: Multiple modify operations in a single IPC call.             }
{                                                                            }
{ Params: operations. Preferred wire format is the '~~' batch format shared  }
{   with batch_delete / place_wires:                                         }
{     scope=active_doc;object_type=eNetLabel;filter=Text=VBUS;set=Location.Y=6400~~... }
{                                                                            }
{ LEGACY format (still accepted when the payload contains no '~~'):          }
{   pipe-separated operations, each semicolon-separated as                   }
{   scope;object_type;filter;set                                             }
{                                                                            }
{ The legacy format was BROKEN for any op whose filter or set contained a    }
{ '|', which is the documented separator INSIDE both of those fields.        }
{ Measured on a live document: an op list of 4 eSchComponent moves followed  }
{ by 8 eNetLabel moves reported operations_processed=4 and silently dropped  }
{ the other 8, and a lone eNetLabel op with a two-condition filter reported  }
{ operations_processed=0 while the identical filter matched through          }
{ obj_query and obj_batch_delete. Cause: the top-level split on '|' tore     }
{ each such op into fragments, and every fragment after the first had fewer  }
{ than three ';' so it hit `Continue` and vanished without a trace. It was   }
{ never an object-type allowlist -- eNetLabel was always accepted.           }
{                                                                            }
{ '~~' cannot appear in an Altium name, filter or property string (see the   }
{ batch helper notes in Main.pas), so the new format is unambiguous.         }
{                                                                            }
{ Every op is now evaluated independently and reports its own matched count  }
{ in "results", so an op that matches nothing is REPORTED rather than being  }
{ silently counted as fine.                                                  }
{..............................................................................}

{ Split one legacy 'scope;object_type;filter;set' operation. Returns False  }
{ when the fragment does not carry all four fields, which is how a torn      }
{ legacy payload is detected.                                                }
Function SplitLegacyModifyOp(OpStr : String; Var Scope : String;
    Var ObjTypeStr : String; Var FilterStr : String; Var SetStr : String) : Boolean;
Var
    Rest : String;
    SemiPos : Integer;
Begin
    Result := False;
    Rest := OpStr;

    SemiPos := Pos(';', Rest);
    If SemiPos = 0 Then Exit;
    Scope := Copy(Rest, 1, SemiPos - 1);
    Rest := Copy(Rest, SemiPos + 1, Length(Rest));

    SemiPos := Pos(';', Rest);
    If SemiPos = 0 Then Exit;
    ObjTypeStr := Copy(Rest, 1, SemiPos - 1);
    Rest := Copy(Rest, SemiPos + 1, Length(Rest));

    SemiPos := Pos(';', Rest);
    If SemiPos = 0 Then Exit;
    FilterStr := Copy(Rest, 1, SemiPos - 1);
    SetStr := Copy(Rest, SemiPos + 1, Length(Rest));
    Result := True;
End;

Function Gen_BatchModify(Params : String; RequestId : String) : String;
Var
    Operations, OpStr, Remaining, OpResult, Note : String;
    Scope, ObjTypeStr, FilterStr, SetStr : String;
    ScopeType, ScopePath : String;
    ObjTypeInt, PipePos : Integer;
    TotalMatched, OpCount, OpSkipped, OpMatched : Integer;
    ResultJson, ResultsJson : String;
    UseTilde : Boolean;
Begin
    Operations := ExtractJsonValue(Params, 'operations');
    If Operations = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'operations parameter is required');
        Exit;
    End;

    TotalMatched := 0;
    OpCount := 0;
    OpSkipped := 0;
    ResultJson := '';
    ResultsJson := '';
    Remaining := Operations;
    UseTilde := Pos('~~', Operations) > 0;

    { Clear the property-write diagnostics buffer so this call only       }
    { surfaces issues raised by THIS batch, not anything left over.       }
    ResetPropertyDiag;

    While Length(Remaining) > 0 Do
    Begin
        Scope := '';
        ObjTypeStr := '';
        FilterStr := '';
        SetStr := '';
        Note := '';

        If UseTilde Then
        Begin
            OpStr := NextBatchOp(Remaining);
            If OpStr = '' Then Break;
            Scope := GetBatchField(OpStr, 'scope');
            ObjTypeStr := GetBatchField(OpStr, 'object_type');
            FilterStr := GetBatchField(OpStr, 'filter');
            SetStr := GetBatchField(OpStr, 'set');
        End
        Else
        Begin
            { Legacy '|' framing. Kept so an older Python client keeps      }
            { working, but a fragment that lost fields to a '|' inside a    }
            { filter or set is now COUNTED and reported instead of being    }
            { dropped in silence.                                           }
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then
            Begin
                OpStr := Remaining;
                Remaining := '';
            End
            Else
            Begin
                OpStr := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End;
            If OpStr = '' Then Continue;
            If Not SplitLegacyModifyOp(OpStr, Scope, ObjTypeStr, FilterStr, SetStr) Then
                Note := 'malformed_operation';
        End;

        If Scope = '' Then Scope := 'active_doc';

        { Every op is evaluated on its own merits from here down. A reason  }
        { to skip is RECORDED, never silently swallowed: silent-drop is     }
        { what made the original bug invisible.                             }
        OpMatched := 0;
        ObjTypeInt := -1;
        ScopeType := '';
        ScopePath := '';

        If Note = '' Then
        Begin
            If ObjTypeStr = '' Then Note := 'missing_object_type'
            Else If SetStr = '' Then Note := 'missing_set';
        End;

        If Note = '' Then
        Begin
            ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
            If ObjTypeInt = -1 Then Note := 'unknown_object_type';
        End;

        If Note = '' Then
        Begin
            ParseScope(Scope, ScopeType, ScopePath);
            { lib_component scope: select the symbol; report if it's gone. }
            If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
                Note := 'lib_component_not_found';
        End;

        If Note = '' Then
        Begin
            OpResult := '';
            If ScopeType = 'project' Then
                OpResult := IterateProjectDocs(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, ScopePath, 0)
            Else If ScopeType = 'doc' Then
                OpResult := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0)
            Else
                OpResult := ProcessActiveDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);

            OpMatched := StrToIntDef(ExtractJsonValue(OpResult, 'matched'), 0);
            TotalMatched := TotalMatched + OpMatched;
            If OpMatched = 0 Then Note := 'no_objects_matched';
            Inc(OpCount);
        End
        Else
            Inc(OpSkipped);

        { Per-op row so a zero-match or skipped op is visible to the caller. }
        If ResultsJson <> '' Then ResultsJson := ResultsJson + ',';
        ResultsJson := ResultsJson +
            '{"object_type":"' + EscapeJsonString(ObjTypeStr) + '"' +
            ',"filter":"' + EscapeJsonString(FilterStr) + '"' +
            ',"matched":' + IntToStr(OpMatched) +
            ',"note":"' + EscapeJsonString(Note) + '"}';
    End;

    { Surface unknown / failed property writes so they stop being silent. }
    ResultJson :=
        '{"operations_processed":' + IntToStr(OpCount) +
        ',"operations_skipped":' + IntToStr(OpSkipped) +
        ',"total_matched":' + IntToStr(TotalMatched) +
        ',"results":[' + ResultsJson + ']' +
        ',"properties":' + RenderPropertyDiagJson + '}';
    Result := BuildSuccessResponse(RequestId, ResultJson);
End;

{..............................................................................}
{ Run Electrical Rules Check on the focused project                          }
{ Compiles the project then runs ERC via the DM API.                         }
{..............................................................................}

Function Gen_RunERC(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    // Compile the project first (required before ERC)
    SmartCompile(Project);

    // Run ERC via RunProcess
    ResetParameters;
    RunProcess('Sch:ERC');

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"message":"ERC completed on project"}');
End;

{..............................................................................}
{ Highlight a net by name in the active document (schematic or PCB)          }
{..............................................................................}

Function Gen_HighlightNet(Params : String; RequestId : String) : String;
Var
    NetName : String;
    ClearExisting : String;
    Context : String;
    FocusedKind : String;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    Net : IPCB_Net;
    Iterator : IPCB_BoardIterator;
    AllNet : IPCB_Net;
    SchIter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Matched : Integer;
    TargetUpper, ObjNet : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PreferSch : Boolean;
Begin
    NetName := ExtractJsonValue(Params, 'net_name');
    ClearExisting := ExtractJsonValue(Params, 'clear_existing');
    Context := ExtractJsonValue(Params, 'context');
    TargetUpper := UpperCase(NetName);

    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'net_name parameter is required');
        Exit;
    End;

    Board := GetPCBBoardAnywhere;
    SchDoc := SchServer.GetCurrentSchDocument;

    { GetPCBBoardAnywhere returns a board even when a schematic is the    }
    { focused document, so a schematic highlight would silently paint the }
    { PCB instead. Prefer the focused doc, or an explicit context param.  }
    FocusedKind := '';
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Nil;
        Try Doc := Workspace.DM_FocusedDocument; Except End;
        If Doc <> Nil Then
            Try FocusedKind := Doc.DM_DocumentKind; Except End;
    End;
    PreferSch := (UpperCase(Context) = 'SCHEMATIC') Or
        ((UpperCase(Context) <> 'PCB') And (FocusedKind = 'SCH'));
    If PreferSch And (SchDoc <> Nil) Then
        Board := Nil;

    { PCB path, use the documented IPCB_Net.IsHighlighted property set    }
    { directly on the net object. The earlier RunProcess('PCB:NetColor-    }
    { Highlight') was a guess; that process name isn't in the reference   }
    { and silently no-ops, which is why the tool appeared to do nothing.  }
    If Board <> Nil Then
    Begin
        If (ClearExisting = '') Or (ClearExisting = 'true') Then
        Begin
            Iterator := Board.BoardIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
                Iterator.AddFilter_LayerSet(AllLayers);
                Iterator.AddFilter_Method(eProcessAll);
                AllNet := Iterator.FirstPCBObject;
                While AllNet <> Nil Do
                Begin
                    Try AllNet.IsHighlighted := False; Except End;
                    AllNet := Iterator.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iterator);
            End;
        End;

        Net := FindNetByName(Board, NetName);
        If Net = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
                'Net not found on PCB: ' + NetName);
            Exit;
        End;

        PCBServer.PreProcess;
        Try Net.IsHighlighted := True; Except End;
        PCBServer.PostProcess;
        Try Board.GraphicallyInvalidate; Except End;

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"net":"' + EscapeJsonString(NetName) + '",'
            + '"context":"pcb","highlighted":1}');
        Exit;
    End;

    { Schematic path, nets aren't first-class objects in Altium's Sch    }
    { API. The base ISch_GraphicalObject has no NetName property          }
    { (compile-time "Undeclared identifier: NetName"; Try/Except can't    }
    { rescue it). Instead, dispatch on ObjectId:                          }
    {   - eNetLabel / ePowerObject / ePort , match against .Text         }
    {   - eSheetEntry                       , match against .Name         }
    {   - eWire                             , wires don't store a net    }
    {     name as a primitive property; the net is derived at compile     }
    {     time from the labels / ports attached to the wire segment.     }
    {     We skip them, selecting the net labels is enough to make the  }
    {     user eyeball-trace the wires.                                  }
    {                                                                    }
    { Do NOT wrap this in ProcessControl.PreProcess. The MCP poller     }
    { already owns the script engine; PreProcess + a debugger break     }
    { (or an undeclared-identifier modal on .Selection) deadlocks       }
    { Altium. Selection-only paint is not an undoable edit. Clear via   }
    { Sch:DeSelectAll, then set Selection=True on matches only -- never }
    { Selection=False inside the iterator (that path raises a modal     }
    { that bypasses Try/Except on sub-objects).                         }
    If SchDoc <> Nil Then
    Begin
        Matched := 0;
        If (ClearExisting = '') Or (ClearExisting = 'true') Then
            SchDeselectAllObjects(SchDoc);

        SchIter := SchDoc.SchIterator_Create;
        If SchIter <> Nil Then
        Begin
            Try
                SchIter.AddFilter_ObjectSet(MkSet(eNetLabel, ePowerObject,
                    ePort, eSheetEntry));
                Obj := SchIter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    ObjNet := '';
                    If Obj.ObjectId = eSheetEntry Then
                        Try ObjNet := Obj.Name; Except End
                    Else
                        Try ObjNet := Obj.Text; Except End;

                    If (ObjNet <> '') And (UpperCase(ObjNet) = TargetUpper) Then
                    Begin
                        Try Obj.Selection := True; Except End;
                        Matched := Matched + 1;
                    End;
                    Obj := SchIter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(SchIter);
            End;
        End;
        Try SchDoc.GraphicallyInvalidate; Except End;

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"net":"' + EscapeJsonString(NetName) + '",'
            + '"context":"schematic","highlighted":' + IntToStr(Matched) + '}');
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Clear all highlights in the active document (schematic or PCB)             }
{..............................................................................}

Function Gen_ClearHighlights(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Net : IPCB_Net;
    SchIter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Cleared : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    SchDoc := SchServer.GetCurrentSchDocument;
    Cleared := 0;

    If Board <> Nil Then
    Begin
        { Walk every net on the board and clear its IsHighlighted flag.   }
        { RunProcess('PCB:ClearAllHighlights') isn't documented and       }
        { appears to no-op, use the typed API path.                      }
        Iterator := Board.BoardIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Net := Iterator.FirstPCBObject;
            PCBServer.PreProcess;
            While Net <> Nil Do
            Begin
                Try
                    If Net.IsHighlighted Then
                    Begin
                        Net.IsHighlighted := False;
                        Cleared := Cleared + 1;
                    End;
                Except End;
                Net := Iterator.NextPCBObject;
            End;
            PCBServer.PostProcess;
        Finally
            Board.BoardIterator_Destroy(Iterator);
        End;
        Try Board.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"context":"pcb","cleared":' + IntToStr(Cleared) + '}');
        Exit;
    End;

    If SchDoc <> Nil Then
    Begin
        { Deselect all connective primitives on the active sheet.          }
        SchServer.ProcessControl.PreProcess(SchDoc, '');
        Try
            SchIter := SchDoc.SchIterator_Create;
            Try
                SchIter.AddFilter_ObjectSet(MkSet(eWire, eNetLabel, ePowerObject,
                    ePort, ePin, eSheetEntry));
                Obj := SchIter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Try
                        If Obj.Selection Then
                        Begin
                            Obj.Selection := False;
                            Cleared := Cleared + 1;
                        End;
                    Except End;
                    Obj := SchIter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(SchIter);
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        End;
        Try SchDoc.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"context":"schematic","cleared":' + IntToStr(Cleared) + '}');
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Add a new schematic sheet to the focused project                           }
{..............................................................................}

Function Gen_AddSheet(Params : String; RequestId : String) : String;
Var
    SheetName : String;
    Workspace : IWorkspace;
    Project : IProject;
    NewDocPath : String;
    ServerDoc : IServerDocument;
    Saved, Added : Boolean;
Begin
    SheetName := ExtractJsonValue(Params, 'name');
    If SheetName = '' Then SheetName := 'NewSheet';

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    // Build the new sheet path in the same directory as the project
    NewDocPath := Project.DM_ProjectFullPath;
    // Strip project filename to get directory
    While (Length(NewDocPath) > 0) And (Copy(NewDocPath, Length(NewDocPath), 1) <> '\') Do
        NewDocPath := Copy(NewDocPath, 1, Length(NewDocPath) - 1);
    NewDocPath := NewDocPath + SheetName + '.SchDoc';

    { Create the blank schematic via Client.OpenNewDocument, mirroring the
      working App_CreateDocument path. The previous
      RunProcess('WorkspaceManager:CreateNewDocument') WITHOUT a FileName
      raises a modal "Value cannot be null. (Parameter 'key')" and WEDGES
      the polling loop. OpenNewDocument names the doc up front, so the
      null-key never happens. }
    ServerDoc := Client.OpenNewDocument('SCH', NewDocPath, SheetName, False);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
            'Client.OpenNewDocument returned Nil for kind=SCH');
        Exit;
    End;

    { Persist to disk: force the path then DoFileSave, falling back to a
      Save-As via WorkspaceManager:SaveObject (same fallback as
      App_CreateDocument). }
    Saved := False;
    Try ServerDoc.SetFileName(NewDocPath); Except End;
    Try
        ServerDoc.SetModified(True);
        ServerDoc.DoFileSave('');
        Saved := FileExists(NewDocPath);
    Except Saved := False; End;
    If Not Saved Then
    Begin
        Try
            ServerDoc.Focus;
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', NewDocPath);
            RunProcess('WorkspaceManager:SaveObject');
            Saved := FileExists(NewDocPath);
        Except Saved := False; End;
    End;

    { Add to the focused project via the documented project-side API
      (DM_AddSourceDocument), which works across workspace states where
      WorkspaceManager:AddObjectToProject silently no-ops. }
    Added := False;
    Try
        Project.DM_AddSourceDocument(NewDocPath);
        Added := True;
    Except Added := False; End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"path":"' + EscapeJsonString(NewDocPath) + '"' +
        ',"saved":' + BoolToJsonStr(Saved) +
        ',"added_to_project":' + BoolToJsonStr(Added) + '}');
End;

{..............................................................................}
{ Delete (remove) a schematic sheet from the focused project                 }
{ Safety check: will not remove the last remaining sheet.                    }
{..............................................................................}

Function Gen_DeleteSheet(Params : String; RequestId : String) : String;
Var
    FilePath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I, SchCount : Integer;
    Found : Boolean;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');
    If FilePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'file_path parameter is required');
        Exit;
    End;


    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    // Count schematic documents and verify the target exists
    SchCount := 0;
    Found := False;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind = 'SCH' Then
        Begin
            Inc(SchCount);
            If SameText(Doc.DM_FullPath, FilePath) Then
                Found := True;
        End;
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Sheet not found in project: ' + FilePath);
        Exit;
    End;

    If SchCount <= 1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAFETY_CHECK',
            'Cannot remove the last schematic sheet from the project');
        Exit;
    End;

    // Close the document first
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', FilePath);
    RunProcess('WorkspaceManager:CloseObject');

    // Remove from project
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', FilePath);
    RunProcess('WorkspaceManager:RemoveObjectFromProject');

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"removed":"' + EscapeJsonString(FilePath) + '"}');
End;

{..............................................................................}
{ Zoom to specific X,Y coordinates (in mils for SCH, mils for PCB)          }
{..............................................................................}

Function Gen_ZoomToXY(Params : String; RequestId : String) : String;
Var
    XStr, YStr : String;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');

    If (XStr = '') Or (YStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'x and y parameters are required');
        Exit;
    End;

    Board := GetPCBBoardAnywhere;
    SchDoc := SchServer.GetCurrentSchDocument;

    If Board <> Nil Then
    Begin
        ResetParameters;
        AddStringParameter('Object', 'JumpToLocation10');
        AddStringParameter('X', XStr);
        AddStringParameter('Y', YStr);
        RunProcess('PCB:Jump');

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"x":' + XStr + ',"y":' + YStr + ',"context":"pcb"}');
    End
    Else If SchDoc <> Nil Then
    Begin
        ResetParameters;
        AddStringParameter('X', XStr);
        AddStringParameter('Y', YStr);
        RunProcess('Sch:ZoomToLocation');

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"x":' + XStr + ',"y":' + YStr + ',"context":"schematic"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Switch between 2D and 3D view for PCB documents                           }
{..............................................................................}

Function Gen_SwitchView(Params : String; RequestId : String) : String;
Var
    Mode : String;
    Board : IPCB_Board;
Begin
    Mode := ExtractJsonValue(Params, 'mode');
    If Mode = '' Then Mode := '3d';

    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No active PCB document');
        Exit;
    End;

    If (Mode = '3d') Or (Mode = '3D') Then
    Begin
        ResetParameters;
        RunProcess('PCB:SwitchTo3D');
    End
    Else
    Begin
        ResetParameters;
        RunProcess('PCB:SwitchTo2D');
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"mode":"' + EscapeJsonString(Mode) + '"}');
End;

{..............................................................................}
{ Measure distance between two points (calculated, no Altium interaction)    }
{ Coordinates in mils. Returns Euclidean distance.                           }
{..............................................................................}

Function Gen_MeasureDistance(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Integer;
    DX, DY : Integer;
    Distance : Double;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);

    DX := X2 - X1;
    DY := Y2 - Y1;
    Distance := Sqrt(DX * DX + DY * DY);

    Result := BuildSuccessResponse(RequestId,
        '{"x1":' + IntToStr(X1) +
        ',"y1":' + IntToStr(Y1) +
        ',"x2":' + IntToStr(X2) +
        ',"y2":' + IntToStr(Y2) +
        ',"dx":' + IntToStr(DX) +
        ',"dy":' + IntToStr(DY) +
        ',"distance_mils":' + FloatToJsonStr(Distance) +
        ',"distance_mm":' + FloatToJsonStr(Distance * 0.0254) + '}');
End;

{..............................................................................}
{ Get ERC violations from the focused project after compilation/ERC          }
{ Returns violation count and messages from the DM API.                      }
{..............................................................................}

{ Reports each violation WITH the objects it is about.                        }
{                                                                             }
{ A category and a sheet name are not actionable: "floating input pin" on a   }
{ sheet with forty parts does not say which pin, and the only safe response   }
{ to that is to do nothing. A NoERC marker placed by guesswork silently       }
{ suppresses a real disconnection, which is strictly worse than the warning   }
{ it clears.                                                                  }
{                                                                             }
{ IViolation.DM_RelatedObjects carries the offending objects. Everything read }
{ from one is declared on IDMObject, the base interface every related object  }
{ implements, so no call here can hit the undeclared-identifier crash that a  }
{ narrower interface would risk. DM_PrimaryCrossProbeString is what Altium    }
{ itself uses to jump to the object, so it identifies the exact pin or net.   }
Function Gen_GetErcViolations(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Violation : IViolation;
    RelObj : IDMObject;
    I, J, VCount, MaxItems, RelCount : Integer;
    JsonItems, RelItems : String;
    First, FirstRel : Boolean;
    Desc, Detail, Kind, DocName, Probe : String;
Begin
    MaxItems := StrToIntDef(ExtractJsonValue(Params, 'limit'), 100);

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    VCount := Project.DM_ViolationCount;
    JsonItems := '';
    First := True;

    For I := 0 To VCount - 1 Do
    Begin
        If (MaxItems > 0) And (I >= MaxItems) Then Break;

        Violation := Project.DM_Violations(I);
        If Violation = Nil Then Continue;

        Try
            Desc := Violation.DM_LongDescriptorString;
        Except
            Desc := '(description unavailable)';
        End;

        Try
            Detail := Violation.DM_DetailString;
        Except
            Detail := '';
        End;

        { The objects the violation is actually about. Without these the
          caller can see that something is wrong but never what, which
          is the difference between a report and a to-do list. }
        RelItems := '';
        FirstRel := True;
        RelCount := 0;
        Try
            RelCount := Violation.DM_RelatedObjectCount;
        Except
            RelCount := 0;
        End;

        For J := 0 To RelCount - 1 Do
        Begin
            Try
                RelObj := Violation.DM_RelatedObjects(J);
            Except
                RelObj := Nil;
            End;
            If RelObj = Nil Then Continue;

            Kind := '';
            DocName := '';
            Probe := '';
            Try
                Kind := RelObj.DM_ObjectKindString;
            Except
                Kind := '';
            End;
            Try
                DocName := RelObj.DM_OwnerDocumentName;
            Except
                DocName := '';
            End;
            Try
                { What Altium uses to cross-probe to this exact object.
                  This is the field that turns "a floating pin somewhere
                  on this sheet" into a designator and pin number. }
                Probe := RelObj.DM_PrimaryCrossProbeString;
            Except
                Probe := '';
            End;

            If Not FirstRel Then RelItems := RelItems + ',';
            FirstRel := False;
            RelItems := RelItems + '{"kind":"' + EscapeJsonString(Kind) +
                '","document":"' + EscapeJsonString(DocName) +
                '","cross_probe":"' + EscapeJsonString(Probe) + '"}';
        End;

        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '{"index":' + IntToStr(I) +
            ',"description":"' + EscapeJsonString(Desc) +
            '","detail":"' + EscapeJsonString(Detail) +
            '","related_object_count":' + IntToStr(RelCount) +
            ',"related_objects":[' + RelItems + ']}';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"violation_count":' + IntToStr(VCount) +
        ',"violations":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Force refresh/redraw of the current document                               }
{..............................................................................}

Function Gen_RefreshDocument(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    Board := GetPCBBoardAnywhere;

    If SchDoc <> Nil Then
    Begin
        SchDoc.GraphicallyInvalidate;
        Result := BuildSuccessResponse(RequestId, '{"success":true,"context":"schematic"}');
    End
    Else If Board <> Nil Then
    Begin
        ResetParameters;
        AddStringParameter('Action', 'Redraw');
        RunProcess('PCB:Zoom');
        Result := BuildSuccessResponse(RequestId, '{"success":true,"context":"pcb"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Get unconnected/floating pins via DM API                                    }
{ Compiles the project first, then iterates DM components to check            }
{ pin connection status. Returns designator + pin pairs with no net.          }
{..............................................................................}

Function Gen_GetUnconnectedPins(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, PinCount, CompCount, Total, DocCount : Integer;
    UsePhysical : Boolean;
    NetName, Designator, PinNumber, PinName, JsonItems : String;
    First : Boolean;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    // Compile the project (required for DM pin connectivity data)
    SmartCompile(Project);

    Total := 0;
    JsonItems := '';
    First := True;

    GetCompiledDocs(Project, DocCount, UsePhysical);
    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;

        CompCount := Doc.DM_ComponentCount;
        For J := 0 To CompCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;
            Designator := Comp.DM_PhysicalDesignator;
            PinCount := Comp.DM_PinCount;

            For K := 0 To PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;

                NetName := Pin.DM_FlattenedNetName;
                PinNumber := Pin.DM_PinNumber;
                PinName := Pin.DM_PinName;

                // A pin with no net or with '?' net is unconnected
                If (NetName = '') Or (NetName = '?') Then
                Begin
                    If Not First Then JsonItems := JsonItems + ',';
                    First := False;
                    JsonItems := JsonItems + '{"designator":"' + EscapeJsonString(Designator) +
                        '","pin_number":"' + EscapeJsonString(PinNumber) +
                        '","pin_name":"' + EscapeJsonString(PinName) +
                        '","sheet":"' + EscapeJsonString(Doc.DM_FullPath) + '"}';
                    Inc(Total);
                End;
            End;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"count":' + IntToStr(Total) + ',"unconnected_pins":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Place a wire segment between two XY coordinates on active schematic         }
{ Params: x1, y1, x2, y2 (in mils)                                          }
{..............................................................................}

Function Gen_PlaceWire(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Integer;
    SchDoc : ISch_Document;
    Wire : ISch_Wire;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Wire := SchServer.SchObjectFactory(eWire, eCreate_Default);
    If Wire = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create wire object');
        Exit;
    End;

    { Two-vertex wire. The canonical pattern: each vertex needs its own      }
    { InsertVertex BEFORE the SetState_Vertex assignment. The previous code  }
    { only inserted vertex 1, so the wire was a single point, invisible.     }
    Wire.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Wire.InsertVertex := 1;
    Wire.SetState_Vertex(1, Point(MilsToCoord(X1), MilsToCoord(Y1)));
    Wire.InsertVertex := 2;
    Wire.SetState_Vertex(2, Point(MilsToCoord(X2), MilsToCoord(Y2)));
    { Color := 0 renders the wire BLACK so it looks like a graphic
      line, not an electrical wire. Leave Color at factory default so
      Altium's wire colour scheme applies. }
    Wire.LineWidth := eSmall;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Wire);
    SchRegisterObject(SchDoc, Wire);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) +
        ',"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ Place a bus segment between two points on the active schematic.             }
{ Buses are multi-signal wires (typically used with bus net labels like       }
{ DATA[0..7]). Placement and vertex handling mirror a normal wire.            }
{..............................................................................}

Function Gen_PlaceBus(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Integer;
    SchDoc : ISch_Document;
    Bus : ISch_Bus;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Bus := SchServer.SchObjectFactory(eBus, eCreate_Default);
    If Bus = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create bus object');
        Exit;
    End;

    { Two-vertex bus: insert both vertices explicitly. }
    Bus.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Bus.InsertVertex := 1;
    Bus.SetState_Vertex(1, Point(MilsToCoord(X1), MilsToCoord(Y1)));
    Bus.InsertVertex := 2;
    Bus.SetState_Vertex(2, Point(MilsToCoord(X2), MilsToCoord(Y2)));

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Bus);
    SchRegisterObject(SchDoc, Bus);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) +
        ',"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ Place a rectangle on the schematic, graphic box, not a functional shape.   }
{ Params: x1,y1,x2,y2 in mils, solid=true/false, line_width=0..3              }
{..............................................................................}

Function Gen_PlaceRectangle(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, TmpI, LW : Integer;
    SchDoc : ISch_Document;
    Rect : ISch_Rectangle;
    SolidStr : String;
    Solid : Boolean;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    SolidStr := ExtractJsonValue(Params, 'solid');
    LW := StrToIntDef(ExtractJsonValue(Params, 'line_width'), 1);
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;
    Solid := (LowerCase(SolidStr) = 'true') Or (SolidStr = '1');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Rect := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
    If Rect = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create rectangle');
        Exit;
    End;

    Rect.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Rect.Corner := Point(MilsToCoord(X2), MilsToCoord(Y2));
    Rect.IsSolid := Solid;
    Try
        If LW <= 0 Then Rect.LineWidth := eSmall
        Else If LW = 1 Then Rect.LineWidth := eSmall
        Else If LW = 2 Then Rect.LineWidth := eMedium
        Else Rect.LineWidth := eLarge;
    Except End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Rect);
    SchRegisterObject(SchDoc, Rect);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"solid":' + BoolToJsonStr(Solid) + '}');
End;

{..............................................................................}
{ Place a line segment on the schematic.                                      }
{ Params: x1,y1,x2,y2 in mils, line_width=0..3                                }
{..............................................................................}

Function Gen_PlaceLine(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, LW : Integer;
    SchDoc : ISch_Document;
    Line : ISch_Line;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    LW := StrToIntDef(ExtractJsonValue(Params, 'line_width'), 1);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Line := SchServer.SchObjectFactory(eLine, eCreate_Default);
    If Line = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create line');
        Exit;
    End;

    Line.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Line.Corner := Point(MilsToCoord(X2), MilsToCoord(Y2));
    Try
        If LW <= 1 Then Line.LineWidth := eSmall
        Else If LW = 2 Then Line.LineWidth := eMedium
        Else Line.LineWidth := eLarge;
    Except End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Line);
    SchRegisterObject(SchDoc, Line);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ Place a note (text box) on the schematic. Notes are ISch_Rectangle children }
{ with rich text. Useful for commentary / design notes on sheets.             }
{ Params: x1,y1,x2,y2 in mils, text                                           }
{..............................................................................}

Function Gen_PlaceNote(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, TmpI : Integer;
    SchDoc : ISch_Document;
    Note : ISch_Note;
    TextStr : String;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    TextStr := ExtractJsonValue(Params, 'text');
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Note := SchServer.SchObjectFactory(eNote, eCreate_Default);
    If Note = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create note');
        Exit;
    End;

    Note.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Note.Corner := Point(MilsToCoord(X2), MilsToCoord(Y2));
    Try Note.Text := TextStr; Except End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Note);
    SchRegisterObject(SchDoc, Note);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"text":"' + EscapeJsonString(TextStr) + '"}');
End;

{..............................................................................}
{ Place a sheet symbol on the schematic, reference to a child SchDoc.        }
{ Params: x1,y1,x2,y2 in mils, sheet_file_name (e.g. PSU.SchDoc),             }
{         sheet_name (display name)                                           }
{..............................................................................}

Function Gen_PlaceSheetSymbol(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, TmpI : Integer;
    SchDoc : ISch_Document;
    Sym : ISch_SheetSymbol;
    FNObj : ISch_SheetFileName;
    NMObj : ISch_SheetName;
    FileNameStr, NameStr : String;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    FileNameStr := ExtractJsonValue(Params, 'sheet_file_name');
    NameStr := ExtractJsonValue(Params, 'sheet_name');
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    If FileNameStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'sheet_file_name required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Sym := SchServer.SchObjectFactory(eSheetSymbol, eCreate_Default);
    If Sym = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create sheet symbol');
        Exit;
    End;

    { ISch_SheetSymbol has no Corner property (unlike ISch_Rectangle) -- using
      it raises "Undeclared identifier: Corner". Size is set via XSize/YSize
      from the bottom-left Location (Altium SDK: SetState_XSize/YSize). }
    Sym.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Sym.XSize := MilsToCoord(X2 - X1);
    Sym.YSize := MilsToCoord(Y2 - Y1);
    If NameStr = '' Then NameStr := ChangeFileExt(FileNameStr, '');

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Sym);
    SchRegisterObject(SchDoc, Sym);

    { SheetFileName (link to the child .SchDoc) and SheetName (display label)
      are complex-text SUB-OBJECTS, not direct properties -- assigning
      Sym.SheetFileName raises "Property does not exist or is readonly". Set via
      GetState_SchSheetFileName/Name + SetState_Text, after the symbol is
      registered so the sub-objects exist (Altium SDK / UpdateSheetSymbolFN). }
    FNObj := Nil; Try FNObj := Sym.GetState_SchSheetFileName; Except End;
    If FNObj <> Nil Then FNObj.SetState_Text(FileNameStr);
    NMObj := Nil; Try NMObj := Sym.GetState_SchSheetName; Except End;
    If NMObj <> Nil Then NMObj.SetState_Text(NameStr);

    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"sheet_file_name":"' + EscapeJsonString(FileNameStr) + '",'
        + '"sheet_name":"' + EscapeJsonString(NameStr) + '"}');
End;

{..............................................................................}
{ Place a sheet entry on a sheet symbol.                                      }
{ Params: sheet_name (name of target ISch_SheetSymbol), entry_name,           }
{         io_type=Input|Output|Bidirectional|Unspecified,                     }
{         side=Left|Right|Top|Bottom, distance_from_top (mils),               }
{         style=None|Left|Right|LeftRight                                     }
{..............................................................................}

Function Gen_PlaceSheetEntry(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Sym : ISch_SheetSymbol;
    Entry : ISch_SheetEntry;
    SheetNameStr, EntryName, IOStr, SideStr, ThisName : String;
    DistFromTop : Integer;
    Found : Boolean;
Begin
    SheetNameStr := ExtractJsonValue(Params, 'sheet_name');
    EntryName := ExtractJsonValue(Params, 'entry_name');
    IOStr := LowerCase(ExtractJsonValue(Params, 'io_type'));
    SideStr := LowerCase(ExtractJsonValue(Params, 'side'));
    DistFromTop := StrToIntDef(ExtractJsonValue(Params, 'distance_from_top'), 100);

    If (SheetNameStr = '') Or (EntryName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'sheet_name and entry_name are required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { Locate the target sheet symbol by its SheetName.                          }
    { ISch_SheetSymbol.SheetName is a COMPOUND sub-object (ISch_SheetName),     }
    { not a String -- see Generic.pas:83-97. Accessing it directly returns an   }
    { interface reference; comparing that to a String raises "Invalid variant   }
    { operation" at runtime. The actual text is on the .Text property of the   }
    { sub-object. ISch_SheetFileName has the same shape; both must be          }
    { dereferenced through .Text before any string operation.                  }
    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSheetSymbol));
    Try
        Sym := Iterator.FirstSchObject;
        While Sym <> Nil Do
        Begin
            ThisName := '';
            Try
                If Sym.SheetName <> Nil Then
                    ThisName := Sym.SheetName.Text;
            Except End;
            If ThisName = SheetNameStr Then
            Begin
                Found := True;
                Break;
            End;
            Sym := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Sheet symbol with SheetName "' + SheetNameStr + '" not found');
        Exit;
    End;

    Entry := SchServer.SchObjectFactory(eSheetEntry, eCreate_Default);
    If Entry = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create sheet entry');
        Exit;
    End;

    Entry.Name := EntryName;
    Entry.DistanceFromTop := MilsToCoord(DistFromTop);

    If IOStr = 'input' Then Entry.IOType := ePortInput
    Else If IOStr = 'output' Then Entry.IOType := ePortOutput
    Else If IOStr = 'bidirectional' Then Entry.IOType := ePortBidirectional
    Else Entry.IOType := ePortUnspecified;

    If SideStr = 'right' Then Entry.Side := eRightSide
    Else If SideStr = 'top' Then Entry.Side := eTopSide
    Else If SideStr = 'bottom' Then Entry.Side := eBottomSide
    Else Entry.Side := eLeftSide;

    { Use AddAndPositionSchObject, not AddSchObject. The plain AddSchObject       }
    { attaches the entry to the parent sheet symbol's child container but does    }
    { NOT compute the entry's geometric position from Side + DistanceFromTop;     }
    { the entry ends up drawn at default 0,0 world coords, off the sheet symbol.  }
    { AddAndPositionSchObject performs the position calc against the symbol's    }
    { current bounds. See SDK reference, ISch_BasicContainer interface.          }
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Sym.AddAndPositionSchObject(Entry);
    SchRegisterObject(Sym, Entry);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"sheet_name":"' + EscapeJsonString(SheetNameStr) + '",'
        + '"entry_name":"' + EscapeJsonString(EntryName) + '",'
        + '"io_type":"' + EscapeJsonString(IOStr) + '",'
        + '"side":"' + EscapeJsonString(SideStr) + '"}');
End;

{..............................................................................}
{ Place a bus entry (45° stub) between a bus line and a wire.                 }
{ ISch_BusEntry inherits ISch_Line, so it accepts Location + Corner.          }
{ Params: x1,y1,x2,y2 in mils                                                 }
{..............................................................................}

Function Gen_PlaceBusEntry(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Integer;
    SchDoc : ISch_Document;
    Entry : ISch_BusEntry;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Entry := SchServer.SchObjectFactory(eBusEntry, eCreate_Default);
    If Entry = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create bus entry');
        Exit;
    End;

    Entry.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Entry.Corner := Point(MilsToCoord(X2), MilsToCoord(Y2));

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Entry);
    SchRegisterObject(SchDoc, Entry);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1)
        + ',"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ Set the sheet size / template style of the active schematic.                }
{ Params: style (e.g. A, B, C, A0, A1, A2, A3, A4, Letter, Legal, Custom),   }
{         custom_width, custom_height (in mils, only used with Custom)        }
{..............................................................................}

Function Gen_SetSheetSize(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    StyleStr, OrientStr : String;
    CustomW, CustomH : Integer;
Begin
    StyleStr := UpperCase(ExtractJsonValue(Params, 'style'));
    CustomW := StrToIntDef(ExtractJsonValue(Params, 'custom_width'), 0);
    CustomH := StrToIntDef(ExtractJsonValue(Params, 'custom_height'), 0);
    OrientStr := LowerCase(ExtractJsonValue(Params, 'orientation'));

    If (StyleStr = '') And (OrientStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'style or orientation required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        If OrientStr = 'landscape' Then
            Try SchDoc.WorkspaceOrientation := eLandscape; Except End
        Else If OrientStr = 'portrait' Then
            Try SchDoc.WorkspaceOrientation := ePortrait; Except End;

        If StyleStr = '' Then
        Begin
            SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"orientation":"' + EscapeJsonString(OrientStr) + '"}');
            Exit;
        End;

        If StyleStr = 'A' Then SchDoc.SheetStyle := eSheetA
        Else If StyleStr = 'B' Then SchDoc.SheetStyle := eSheetB
        Else If StyleStr = 'C' Then SchDoc.SheetStyle := eSheetC
        Else If StyleStr = 'D' Then SchDoc.SheetStyle := eSheetD
        Else If StyleStr = 'E' Then SchDoc.SheetStyle := eSheetE
        Else If StyleStr = 'A4' Then SchDoc.SheetStyle := eSheetA4
        Else If StyleStr = 'A3' Then SchDoc.SheetStyle := eSheetA3
        Else If StyleStr = 'A2' Then SchDoc.SheetStyle := eSheetA2
        Else If StyleStr = 'A1' Then SchDoc.SheetStyle := eSheetA1
        Else If StyleStr = 'A0' Then SchDoc.SheetStyle := eSheetA0
        Else If StyleStr = 'LETTER' Then SchDoc.SheetStyle := eSheetLetter
        Else If StyleStr = 'LEGAL' Then SchDoc.SheetStyle := eSheetLegal
        Else If StyleStr = 'TABLOID' Then SchDoc.SheetStyle := eSheetTabloid
        Else If StyleStr = 'CUSTOM' Then
        Begin
            SchDoc.SheetStyle := eSheetCustom;
            If CustomW > 0 Then SchDoc.CustomX := MilsToCoord(CustomW);
            If CustomH > 0 Then SchDoc.CustomY := MilsToCoord(CustomH);
        End
        Else
        Begin
            SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
            Result := BuildErrorResponse(RequestId, 'INVALID_STYLE',
                'Unknown sheet style: ' + StyleStr);
            Exit;
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    End;
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"style":"' + EscapeJsonString(StyleStr) + '"}');
End;

{..............................................................................}
{ Place a schematic component instance from a library onto the active sheet.  }
{ Uses ISch_Document.PlaceSchComponent, the verified direct-placement API.   }
{ Params: library_path (.SchLib full path), lib_reference (component name),   }
{         x, y (mils), designator (optional), rotation (0|90|180|270),        }
{         footprint (optional override)                                        }
{..............................................................................}

{ Validate lib_path + lib_reference resolve via CreateLibCompInfoReader     }
{ BEFORE calling PlaceSchComponent. PlaceSchComponent pops a modal Error    }
{ dialog when its lookup fails, Altium shows the popup at the COM layer    }
{ before our Try/Except can swallow it, which freezes the polling loop.    }
{ Pre-validating off-disk avoids that path entirely.                         }
{ Returns ''  if found, otherwise a comma-separated sample of names that    }
{ ARE in the lib so the caller can see what's available.                    }
Function ResolveLibRef(LibPath, LibRef : String; Var Available : String) : Boolean;
Var
    Reader : ILibCompInfoReader;
    Info : IComponentInfo;
    Count, I, Shown : Integer;
Begin
    Result := False;
    Available := '';
    If LibPath = '' Then Exit;
    Try
        Reader := SchServer.CreateLibCompInfoReader(LibPath);
    Except
        Reader := Nil;
    End;
    If Reader = Nil Then Exit;
    Try Reader.ReadAllComponentInfo; Except End;

    Try Count := Reader.NumComponentInfos; Except Count := 0; End;
    Shown := 0;
    For I := 0 To Count - 1 Do
    Begin
        Info := Reader.ComponentInfos[I];
        If Info = Nil Then Continue;
        If Info.CompName = LibRef Then
        Begin
            Result := True;
            Exit;
        End;
        If Shown < 5 Then
        Begin
            If Available <> '' Then Available := Available + '; ';
            Available := Available + Info.CompName;
            Inc(Shown);
        End;
    End;
End;

{ Find the placed ISch_Component on SchDoc that matches the given lib_ref.   }
{ Used after PlaceSchComponent because the SDK signature returns only an     }
{ integer TSchObjectHandle via a Var parameter, not the component object.    }
{ Returns the most recently placed component matching the lib_ref so a      }
{ caller can position / rename / customise it.                                }
Function FindPlacedComponentByLibRef(SchDoc : ISch_Document; LibRef : String) : ISch_Component;
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Best : ISch_Component;
Begin
    Best := Nil;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.LibReference = LibRef Then Best := Obj;  { keep last match }
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
    Result := Best;
End;

Function Gen_PlaceSchComponentFromLibrary(Params : String; RequestId : String) : String;
Var
    LibPath, LibRef, DesigStr, FootprintStr, AvailHint, SheetPath : String;
    X, Y, Rotation, OrientationVal : Integer;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    SrvDoc : IServerDocument;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    LibRef := ExtractJsonValue(Params, 'lib_reference');
    DesigStr := ExtractJsonValue(Params, 'designator');
    FootprintStr := ExtractJsonValue(Params, 'footprint');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);

    If LibRef = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'lib_reference required');
        Exit;
    End;

    { Resolve target sheet (focus-independent). }
    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath + '. Open it first.');
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    If SchDoc.ObjectId <> eSheet Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOC_KIND',
            'Target document is not a schematic sheet (ObjectId=' +
            IntToStr(SchDoc.ObjectId) + '). Pass sheet_path to a .SchDoc.');
        Exit;
    End;

    { Pre-validate that lib_reference exists in the SchLib. Cheap on-disk   }
    { check via CreateLibCompInfoReader; avoids any internal-popup path.    }
    If LibPath <> '' Then
    Begin
        If Not ResolveLibRef(LibPath, LibRef, AvailHint) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'PLACE_FAILED',
                'lib_reference "' + LibRef + '" not found in ' + LibPath +
                '. Sample of available names: ' + AvailHint);
            Exit;
        End;
    End;

    { THE WORKING PLACEMENT API (per SamacSys Altium Library Loader and    }
    { the Altium Circad translator reference):                              }
    {   1. SchServer.LoadComponentFromLibrary(LibRef, LibPath), note the }
    {      argument order is (REF, PATH), opposite of PlaceSchComponent.   }
    {   2. SchDoc.AddSchObject(comp), attach to the sheet.                }
    {   3. comp.MoveToXY(MilsToCoord(X), MilsToCoord(Y)), proper          }
    {      whole-component positioning. Moves designator / comment / pins }
    {      together.                                                        }
    {   4. comp.SetState_Orientation(N), 0/1/2/3 for 0°/90°/180°/270°.    }
    {                                                                        }
    { This replaces the broken PlaceSchComponent + Comp.Location :=         }
    { Point(...) approach which 16-bit-truncates coords and pops modal      }
    { errors.                                                               }
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Comp := Nil;
    Try
        Comp := SchServer.LoadComponentFromLibrary(LibRef, LibPath);
    Except
        Comp := Nil;
    End;

    If Comp = Nil Then
    Begin
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        Result := BuildErrorResponse(RequestId, 'PLACE_FAILED',
            'LoadComponentFromLibrary returned nil for ' + LibRef +
            ' from ' + LibPath);
        Exit;
    End;

    Try SchDoc.AddSchObject(Comp); Except End;
    Try Comp.MoveToXY(MilsToCoord(X), MilsToCoord(Y)); Except End;

    { Translate degrees to the orientation enum. }
    OrientationVal := 0;
    If Rotation = 90 Then OrientationVal := 1
    Else If Rotation = 180 Then OrientationVal := 2
    Else If Rotation = 270 Then OrientationVal := 3;
    Try Comp.SetState_Orientation(OrientationVal); Except End;

    { Override designator if caller supplied one. }
    If DesigStr <> '' Then
        Try Comp.Designator.Text := DesigStr; Except End;

    { Footprint override at place time is intentionally skipped:           }
    { Comp.CurrentFootprintModelName is a read-only getter in DelphiScript  }
    { and assigning to it raises Undeclared identifier which Try/Except     }
    { cannot suppress (memory: delphiscript_api_quirks.md). The symbol's    }
    { own linked footprint from the library is used. To override at run    }
    { time, walk Comp.Implementations and edit ISch_Implementation.ModelName }
    { in a dedicated handler.                                                }

    SchRegisterObject(SchDoc, Comp);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { Explicitly flag the IServerDocument dirty so save_all flushes it.    }
    Try
        SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"library_path":"' + EscapeJsonString(LibPath) + '",'
        + '"lib_reference":"' + EscapeJsonString(LibRef) + '",'
        + '"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + ','
        + '"rotation":' + IntToStr(Rotation) + ','
        + '"designator":"' + EscapeJsonString(DesigStr) + '"}');
End;

{..............................................................................}
{ Place a parameter-set directive on the schematic at (x, y).                 }
{ A parameter-set directive attaches a named parameter to a wire or net,     }
{ commonly used for differential pairs (DifferentialPair=<pair name>), net   }
{ class membership (NetClass=<class name>), or custom net-level rules.        }
{ Params: x, y, param_name, param_value                                       }
{..............................................................................}

Function Gen_PlaceDirective(Params : String; RequestId : String) : String;
Var
    X, Y : Integer;
    ParamName, ParamValue : String;
    SchDoc : ISch_Document;
    ParamSet : ISch_ParameterSet;
    Param : ISch_Parameter;
Begin
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    ParamName := ExtractJsonValue(Params, 'param_name');
    ParamValue := ExtractJsonValue(Params, 'param_value');

    If ParamName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'param_name required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { ISch_ParameterSet is the proper directive interface, a group of
      parameters applied to the wire/net at its location. Create the
      parameter set first, then add a child ISch_Parameter carrying the
      actual (name, value) payload. ISch_Parameter alone would render
      as free-standing text and not act as a directive. }
    ParamSet := SchServer.SchObjectFactory(eParameterSet, eCreate_Default);
    If ParamSet = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create parameter-set directive');
        Exit;
    End;

    ParamSet.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    Try ParamSet.Name := ParamName; Except End;

    Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
    If Param <> Nil Then
    Begin
        Param.Name := ParamName;
        Param.Text := ParamValue;
        ParamSet.AddSchObject(Param);
        SchRegisterObject(ParamSet, Param);
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(ParamSet);
    SchRegisterObject(SchDoc, ParamSet);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + ','
        + '"param_name":"' + EscapeJsonString(ParamName) + '",'
        + '"param_value":"' + EscapeJsonString(ParamValue) + '"}');
End;

{..............................................................................}
{ Enumerate parameter-set directives on the active sheet (or project).        }
{ Each directive is a named group of key=value parameters attached at a       }
{ specific (x, y) on a wire or net. Used for net classes, differential pair   }
{ definitions, channel naming, and any other per-net design rule directive.   }
{ Params: scope = active_doc | project (default active_doc)                  }
{..............................................................................}

Function Gen_GetDirectives(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    OuterIter, InnerIter : ISch_Iterator;
    ParamSet : ISch_BasicContainer;
    Param : ISch_BasicContainer;
    JsonItems, ChildJson, PName, PValue, DirName : String;
    First, FirstChild : Boolean;
    Count, X, Y : Integer;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    OuterIter := SchDoc.SchIterator_Create;
    OuterIter.AddFilter_ObjectSet(MkSet(eParameterSet));
    Try
        ParamSet := OuterIter.FirstSchObject;
        While ParamSet <> Nil Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;

            DirName := '';
            X := 0; Y := 0;
            Try DirName := ParamSet.Name; Except End;
            Try X := CoordToMils(ParamSet.Location.X); Except End;
            Try Y := CoordToMils(ParamSet.Location.Y); Except End;

            ChildJson := '';
            FirstChild := True;
            { Iterate the parameters (eParameter) owned by this parameter set. }
            Try
                InnerIter := ParamSet.SchIterator_Create;
                InnerIter.AddFilter_ObjectSet(MkSet(eParameter));
                Param := InnerIter.FirstSchObject;
                While Param <> Nil Do
                Begin
                    PName := '';
                    PValue := '';
                    Try PName := Param.Name; Except End;
                    Try PValue := Param.Text; Except End;
                    If Not FirstChild Then ChildJson := ChildJson + ',';
                    FirstChild := False;
                    ChildJson := ChildJson + '{"name":"' + EscapeJsonString(PName) + '","value":"' + EscapeJsonString(PValue) + '"}';
                    Param := InnerIter.NextSchObject;
                End;
                ParamSet.SchIterator_Destroy(InnerIter);
            Except End;

            JsonItems := JsonItems + '{"name":"' + EscapeJsonString(DirName) + '",'
                + '"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + ','
                + '"parameters":[' + ChildJson + ']}';
            Inc(Count);
            ParamSet := OuterIter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(OuterIter);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"directives":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ Place a compile mask (blanket) over a rectangular area on the schematic.    }
{ Compile masks exclude enclosed objects from compilation and ERC.            }
{ Params: x1,y1,x2,y2 in mils                                                 }
{..............................................................................}

Function Gen_PlaceCompileMask(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, TmpI : Integer;
    SchDoc : ISch_Document;
    Mask : ISch_CompileMask;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Mask := SchServer.SchObjectFactory(eCompileMask, eCreate_Default);
    If Mask = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create compile mask');
        Exit;
    End;

    Mask.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Mask.Corner := Point(MilsToCoord(X2), MilsToCoord(Y2));

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Mask);
    SchRegisterObject(SchDoc, Mask);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ InferNetLabelStyle - the sheet's own net-label convention, by majority.      }
{ Every net label a tool adds must match the labels already on the target      }
{ sheet: FontId (which carries font face AND size in the font table) and       }
{ Color. Iterates the existing eNetLabel objects and returns the most common   }
{ (FontId, Color) pair. Returns False when the sheet has no net labels yet,   }
{ callers then keep their historical defaults so a fresh sheet is unchanged.  }
{ Majority, not first-seen: one off-style label from an old edit must not      }
{ define the convention.                                                       }
{..............................................................................}

Function InferNetLabelStyle(SchDoc : ISch_Document;
    Var OutFontId : Integer; Var OutColor : Integer) : Boolean;
Var
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Keys, Counts : TStringList;
    Key : String;
    Idx, I, N, BestN, FId, Col, ColonPos : Integer;
Begin
    Result := False;
    OutFontId := 0;
    OutColor := 0;
    If SchDoc = Nil Then Exit;

    Keys := TStringList.Create;
    Counts := TStringList.Create;
    Try
        Iterator := SchDoc.SchIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(eNetLabel));
            Obj := Iterator.FirstSchObject;
            While Obj <> Nil Do
            Begin
                FId := 0;
                Col := 0;
                Try FId := Obj.FontId; Except End;
                Try Col := Obj.Color; Except End;
                If FId > 0 Then
                Begin
                    Key := IntToStr(FId) + ':' + IntToStr(Col);
                    Idx := Keys.IndexOf(Key);
                    If Idx < 0 Then
                    Begin
                        Keys.Add(Key);
                        Counts.Add('1');
                    End
                    Else
                        Counts[Idx] := IntToStr(StrToIntDef(Counts[Idx], 0) + 1);
                End;
                Obj := Iterator.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iterator);
        End;

        BestN := 0;
        For I := 0 To Keys.Count - 1 Do
        Begin
            N := StrToIntDef(Counts[I], 0);
            If N > BestN Then
            Begin
                BestN := N;
                Key := Keys[I];
                ColonPos := Pos(':', Key);
                OutFontId := StrToIntDef(Copy(Key, 1, ColonPos - 1), 0);
                OutColor := StrToIntDef(
                    Copy(Key, ColonPos + 1, Length(Key)), 0);
            End;
        End;
        Result := BestN > 0;
    Finally
        Keys.Free;
        Counts.Free;
    End;
End;

{..............................................................................}
{ Place a net label at coordinates on active schematic                        }
{ Params: text, x, y, orientation (0/1/2/3)                                  }
{..............................................................................}

Function Gen_PlaceNetLabel(Params : String; RequestId : String) : String;
Var
    Text, SheetPath : String;
    X, Y, Orientation : Integer;
    SchDoc : ISch_Document;
    NetLabel : ISch_NetLabel;
    Loc : TLocation;
    SrvDoc : IServerDocument;
    InfFont, InfColor : Integer;
    StyleFound : Boolean;
Begin
    Text := ExtractJsonValue(Params, 'text');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Orientation := StrToIntDef(ExtractJsonValue(Params, 'orientation'), 0);

    If Text = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text parameter is required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath);
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    NetLabel := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
    If NetLabel = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create net label');
        Exit;
    End;

    Loc := NetLabel.Location;
    Loc.X := MilsToCoord(X);
    Loc.Y := MilsToCoord(Y);
    NetLabel.Location := Loc;
    NetLabel.Text := Text;
    NetLabel.Orientation := Orientation;
    { Follow the sheet's own net-label convention (font, size via the
      font table, colour). Historical default only on a sheet that has
      no net labels yet. }
    StyleFound := InferNetLabelStyle(SchDoc, InfFont, InfColor);
    If StyleFound Then
    Begin
        Try NetLabel.FontId := InfFont; Except End;
        NetLabel.Color := InfColor;
    End
    Else
        NetLabel.Color := 0;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(NetLabel);
    SchRegisterObject(SchDoc, NetLabel);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Try
        SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"text":"' + EscapeJsonString(Text) +
        '","x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
End;

{..............................................................................}
{ Get pin world coordinates for a placed component on the active SchDoc.     }
{ Params: designator                                                          }
{ Returns array of [pin_number, pin_name, x_mils, y_mils, orientation].     }
{                                                                             }
{ Used by the design executor to look up pin positions after place_sch_     }
{ component_from_library so it can drop net labels at the right spot.        }
{ ISch_Pin.Location on a placed component instance returns world coords     }
{ already (Altium has applied component placement + orientation).           }
{..............................................................................}

Function Gen_GetSchComponentPins(Params : String; RequestId : String) : String;
Var
    Designator, SheetPath : String;
    SchDoc : ISch_Document;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    Found : Boolean;
    Data, PinList : String;
    First : Boolean;
    PinNum, PinName : String;
    PinX, PinY : Integer;
    PinOrient, PinLenMils : Integer;
    CompX, CompY : Integer;
    CompLoc : TLocation;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'designator is required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath);
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    Found := False;
    PinList := '';
    First := True;

    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        While (Comp <> Nil) And (Not Found) Do
        Begin
            If Comp.Designator.Text = Designator Then
            Begin
                Found := True;

                { CORRECTION: for an ISch_Pin attached to a placed             }
                { ISch_Component on a SchDoc, Pin.Location is ALREADY the      }
                { absolute world coordinate, not an offset from the component }
                { anchor. Adding CompX/CompY (the previous code) doubled the  }
                { offset and pushed every label / power port placed by the    }
                { design executor far past the actual pin endpoint, producing }
                { the "Floating net labels" / "Floating power objects" ERC    }
                { warnings on a freshly executed plan. Read Pin.Location      }
                { directly. CompLoc is kept for future use but no longer      }
                { added here. (Rotation / mirror still not handled in this    }
                { slice; Pin.Location already reflects the placed orientation }
                { because Altium updates it after RotateAroundXY / mirror.)   }
                CompLoc := Comp.Location;
                CompX := CoordToMils(CompLoc.X);
                CompY := CoordToMils(CompLoc.Y);

                PinIter := Comp.SchIterator_Create;
                Try
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Pin := PinIter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                        PinNum := '';
                        PinName := '';
                        PinX := 0;
                        PinY := 0;
                        PinOrient := 0;
                        PinLenMils := 0;
                        Try PinNum := Pin.Designator; Except End;
                        Try PinName := Pin.Name; Except End;
                        Try PinX := CoordToMils(Pin.Location.X); Except End;
                        Try PinY := CoordToMils(Pin.Location.Y); Except End;
                        { Pin.Orientation is the TRotationBy90 enum:           }
                        { 0=right (eRotate0),   1=up (eRotate90),               }
                        { 2=left (eRotate180),  3=down (eRotate270).            }
                        { This is the direction the pin's electrical end       }
                        { points AWAY from the component body, so a stub wire  }
                        { from Pin.Location must extend along this vector by   }
                        { Pin.PinLength to reach the electrical hot end.       }
                        Try PinOrient := Pin.Orientation; Except End;
                        Try PinLenMils := CoordToMils(Pin.PinLength); Except End;

                        If Not First Then PinList := PinList + ',';
                        First := False;
                        PinList := PinList +
                            '{"pin_number":"' + EscapeJsonString(PinNum) +
                            '","pin_name":"' + EscapeJsonString(PinName) +
                            '","x_mils":' + IntToStr(PinX) +
                            ',"y_mils":' + IntToStr(PinY) +
                            ',"orientation":' + IntToStr(PinOrient) +
                            ',"pin_length_mils":' + IntToStr(PinLenMils) + '}';

                        Pin := PinIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PinIter);
                End;
            End;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Placed component not found: ' + Designator);
        Exit;
    End;

    Data := '{"designator":"' + EscapeJsonString(Designator) +
        '","pins":[' + PinList + ']}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Place a port on active schematic                                            }
{ Params: name, x, y, style, io_type                                         }
{..............................................................................}

Function Gen_PlacePort(Params : String; RequestId : String) : String;
Var
    Name, StyleStr, IOTypeStr : String;
    X, Y : Integer;
    SchDoc : ISch_Document;
    SchPort : ISch_Port;
Begin
    Name := ExtractJsonValue(Params, 'name');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    StyleStr := ExtractJsonValue(Params, 'style');
    IOTypeStr := ExtractJsonValue(Params, 'io_type');

    If Name = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'name parameter is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    SchPort := SchServer.SchObjectFactory(ePort, eCreate_Default);
    If SchPort = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create port');
        Exit;
    End;

    SchPort.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    SchPort.Name := Name;

    // Style: none, left, right, left_right
    If StyleStr = 'left' Then SchPort.Style := ePortLeft
    Else If StyleStr = 'right' Then SchPort.Style := ePortRight
    Else If StyleStr = 'left_right' Then SchPort.Style := ePortLeftRight
    Else SchPort.Style := ePortNone;

    // IO Type: unspecified, output, input, bidirectional
    If IOTypeStr = 'output' Then SchPort.IOType := ePortOutput
    Else If IOTypeStr = 'input' Then SchPort.IOType := ePortInput
    Else If IOTypeStr = 'bidirectional' Then SchPort.IOType := ePortBidirectional
    Else SchPort.IOType := ePortUnspecified;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(SchPort);
    SchRegisterObject(SchDoc, SchPort);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"name":"' + EscapeJsonString(Name) +
        '","x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
End;

{..............................................................................}
{ Place a power port (VCC, GND, etc.) on active schematic                     }
{ Params: text, x, y, style                                                  }
{..............................................................................}

Function Gen_PlacePowerPort(Params : String; RequestId : String) : String;
Var
    Text, StyleStr, SheetPath : String;
    X, Y, OrientationVal : Integer;
    SchDoc : ISch_Document;
    PowerObj : ISch_PowerObject;
    Loc : TLocation;
    SrvDoc : IServerDocument;
Begin
    Text := ExtractJsonValue(Params, 'text');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    StyleStr := ExtractJsonValue(Params, 'style');
    OrientationVal := StrToIntDef(ExtractJsonValue(Params, 'orientation'), -1);

    If Text = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text parameter is required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath);
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    PowerObj := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
    If PowerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create power port');
        Exit;
    End;

    Loc := PowerObj.Location;
    Loc.X := MilsToCoord(X);
    Loc.Y := MilsToCoord(Y);
    PowerObj.Location := Loc;
    PowerObj.Text := Text;
    PowerObj.ShowNetName := True;

    // Style: circle, arrow, bar, wave, gnd_power, gnd_signal, gnd_earth
    If StyleStr = 'arrow' Then PowerObj.Style := ePowerArrow
    Else If StyleStr = 'bar' Then PowerObj.Style := ePowerBar
    Else If StyleStr = 'wave' Then PowerObj.Style := ePowerWave
    Else If StyleStr = 'gnd_power' Then PowerObj.Style := ePowerGndPower
    Else If StyleStr = 'gnd_signal' Then PowerObj.Style := ePowerGndSignal
    Else If StyleStr = 'gnd_earth' Then PowerObj.Style := ePowerGndEarth
    Else PowerObj.Style := ePowerCircle;

    { Default orientation: VCC-style points UP, GND-style points DOWN.   }
    { Override via the 'orientation' param (0=right, 1=up, 2=left, 3=down). }
    If OrientationVal < 0 Then
    Begin
        If (StyleStr = 'gnd_power') Or (StyleStr = 'gnd_signal') Or
           (StyleStr = 'gnd_earth') Or (StyleStr = 'bar') Or
           (StyleStr = 'wave') Then
            OrientationVal := 3
        Else
            OrientationVal := 1;
    End;
    Try PowerObj.Orientation := OrientationVal; Except End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(PowerObj);
    SchRegisterObject(SchDoc, PowerObj);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Try
        SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"text":"' + EscapeJsonString(Text) +
        '","x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
End;

{..............................................................................}
{ Get title block / sheet parameters from a schematic sheet                   }
{ Params: file_path (optional, defaults to active document)                   }
{..............................................................................}

Function Gen_GetSheetParameters(Params : String; RequestId : String) : String;
Var
    FilePath : String;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Param : ISch_Parameter;
    JsonItems : String;
    First : Boolean;
    ParamCount : Integer;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');

    If FilePath <> '' Then
        SchDoc := SchServer.GetSchDocumentByPath(FilePath)
    Else
        SchDoc := SchServer.GetCurrentSchDocument;

    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document available');
        Exit;
    End;

    JsonItems := '';
    First := True;
    ParamCount := 0;

    { SchIterator + eParameter at IterationDepth=FirstLevel returns
      sheet-level parameters that the title block reads from. This
      matches what set_document_parameter writes to. }
    Iterator := SchDoc.SchIterator_Create;
    Iterator.SetState_IterationDepth(eIterateFirstLevel);
    Iterator.AddFilter_ObjectSet(MkSet(eParameter));

    Try
        Param := Iterator.FirstSchObject;
        While Param <> Nil Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems + '{"name":"' + EscapeJsonString(Param.Name) +
                '","value":"' + EscapeJsonString(Param.Text) + '"}';
            Inc(ParamCount);
            Param := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"count":' + IntToStr(ParamCount) +
        ',"parameters":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Copy matching objects to clipboard via Sch:CopyToClipboard                  }
{ Params: object_type, filter                                                 }
{..............................................................................}

Function Gen_CopyObjects(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, FilterStr : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    MatchCount : Integer;
Begin
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt = -1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown object type: ' + ObjTypeStr);
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    // Clear current selection first
    SchDeselectAllObjects(SchDoc);

    // Select matching objects
    MatchCount := 0;
    SchServer.ProcessControl.PreProcess(SchDoc, '');

    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));

    Obj := Iterator.FirstSchObject;
    While Obj <> Nil Do
    Begin
        If MatchesFilter(Obj, FilterStr) Then
        Begin
            Obj.Selection := True;
            Inc(MatchCount);
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');

    // Copy selected to clipboard
    If MatchCount > 0 Then
        RunProcess('Sch:CopyToClipboard');

    // Clear selection after copy
    SchDeselectAllObjects(SchDoc);
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"copied":' + IntToStr(MatchCount) + '}');
End;

{..............................................................................}
{ Quick count of objects by type on active doc or project                     }
{ Params: object_type, scope (active_doc/project), filter                    }
{..............................................................................}

Function Gen_GetObjectCount(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, FilterStr, Scope, ScopeType, ScopePath : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    I, MatchCount, SheetsProcessed : Integer;
    FilePath : String;
Begin
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    Scope := ExtractJsonValue(Params, 'scope');
    ParseScope(Scope, ScopeType, ScopePath);

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt = -1 Then
    Begin
        ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
        If ObjTypeInt = -1 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown object type: ' + ObjTypeStr);
            Exit;
        End;

        // PCB count, active doc only
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', '', 'query', RequestId, 0);
        // The query result already has count, just return it
        Exit;
    End;

    MatchCount := 0;
    SheetsProcessed := 0;

    If ScopeType = 'project' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
            Exit;
        End;

        If ScopePath <> '' Then
            Project := FindProjectByPath(Workspace, ScopePath)
        Else
            Project := Workspace.DM_FocusedProject;
        If Project = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found');
            Exit;
        End;

        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            If Doc.DM_DocumentKind <> 'SCH' Then Continue;

            FilePath := Doc.DM_FullPath;
            // Don't force-open, that creates free documents. Skip
            // sheets that aren't currently loaded into SchServer.
            SchDoc := SchServer.GetSchDocumentByPath(FilePath);
            If SchDoc = Nil Then Continue;

            Iterator := SchDoc.SchIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            Obj := Iterator.FirstSchObject;
            While Obj <> Nil Do
            Begin
                If MatchesFilter(Obj, FilterStr) Then
                    Inc(MatchCount);
                Obj := Iterator.NextSchObject;
            End;
            SchDoc.SchIterator_Destroy(Iterator);
            Inc(SheetsProcessed);
        End;

        Result := BuildSuccessResponse(RequestId,
            '{"count":' + IntToStr(MatchCount) +
            ',"sheets_processed":' + IntToStr(SheetsProcessed) + '}');
    End
    Else
    Begin
        // Honor doc:<path> scope (parallel to query_objects). Without
        // this the count silently fell back to the active document,
        // returning a misleading number for an explicit doc: scope.
        If (ScopeType = 'doc') And (ScopePath <> '') Then
            SchDoc := SchServer.GetSchDocumentByPath(ScopePath)
        Else
            SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active or loaded for the given scope');
            Exit;
        End;

        Iterator := SchDoc.SchIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
        Obj := Iterator.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If MatchesFilter(Obj, FilterStr) Then
                Inc(MatchCount);
            Obj := Iterator.NextSchObject;
        End;
        SchDoc.SchIterator_Destroy(Iterator);

        Result := BuildSuccessResponse(RequestId,
            '{"count":' + IntToStr(MatchCount) + '}');
    End;
End;

{..............................................................................}
{ Place a No-ERC marker at coordinates on active schematic                    }
{ Params: x, y                                                                }
{..............................................................................}

Function Gen_PlaceNoERC(Params : String; RequestId : String) : String;
Var
    X, Y : Integer;
    SchDoc : ISch_Document;
    NoERC : ISch_GraphicalObject;
Begin
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    NoERC := SchServer.SchObjectFactory(eNoERC, eCreate_Default);
    If NoERC = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create No-ERC marker');
        Exit;
    End;

    NoERC.Location := Point(MilsToCoord(X), MilsToCoord(Y));

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(NoERC);
    SchRegisterObject(SchDoc, NoERC);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
End;

{..............................................................................}
{ Place a junction at coordinates on active schematic                         }
{ Params: x, y                                                                }
{..............................................................................}

Function Gen_PlaceJunction(Params : String; RequestId : String) : String;
Var
    X, Y : Integer;
    SchDoc : ISch_Document;
    Junction : ISch_GraphicalObject;
Begin
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Junction := SchServer.SchObjectFactory(eJunction, eCreate_Default);
    If Junction = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create junction');
        Exit;
    End;

    Junction.Location := Point(MilsToCoord(X), MilsToCoord(Y));

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Junction);
    SchRegisterObject(SchDoc, Junction);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
End;

{..............................................................................}
{ Gen_PlaceJunctions - Bulk junction placement on the active schematic.        }
{ Params: junctions = 'x=100;y=200~~x=300;y=400~~...'                          }
{..............................................................................}

Function Gen_PlaceJunctions(Params : String; RequestId : String) : String;
Var
    JuncStr, Remaining, Op : String;
    OpCount, Placed, Failed : Integer;
    X, Y : Integer;
    SchDoc : ISch_Document;
    Junction : ISch_GraphicalObject;
Begin
    JuncStr := ExtractJsonValue(Params, 'junctions');
    If JuncStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'junctions is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := JuncStr;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);

            Junction := SchServer.SchObjectFactory(eJunction, eCreate_Default);
            If Junction = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Junction.Location := Point(MilsToCoord(X), MilsToCoord(Y));
            SchDoc.RegisterSchObjectInContainer(Junction);
            SchRegisterObject(SchDoc, Junction);
            Inc(Placed);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Get comprehensive info about the active document                            }
{ Returns: file_path, kind, sheet_size, title_block, grid_size, unit_system  }
{..............................................................................}

Function Gen_GetDocumentInfo(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    Data : String;
    SheetStyle, UnitStr : String;
Begin
    Board := GetPCBBoardAnywhere;
    SchDoc := SchServer.GetCurrentSchDocument;

    If SchDoc <> Nil Then
    Begin
        // Schematic document info
        Data := '{"file_path":"' + EscapeJsonString(SchDoc.DocumentName) + '"';
        Data := Data + ',"kind":"SCH"';

        // Sheet size
        Try
            Case SchDoc.SheetStyle Of
                0 : SheetStyle := 'A4';
                1 : SheetStyle := 'A3';
                2 : SheetStyle := 'A2';
                3 : SheetStyle := 'A1';
                4 : SheetStyle := 'A0';
                5 : SheetStyle := 'A';
                6 : SheetStyle := 'B';
                7 : SheetStyle := 'C';
                8 : SheetStyle := 'D';
                9 : SheetStyle := 'E';
                10 : SheetStyle := 'Letter';
                11 : SheetStyle := 'Legal';
                12 : SheetStyle := 'Tabloid';
                13 : SheetStyle := 'OrCAD_A';
                14 : SheetStyle := 'OrCAD_B';
                15 : SheetStyle := 'OrCAD_C';
                16 : SheetStyle := 'OrCAD_D';
                17 : SheetStyle := 'OrCAD_E';
            Else
                SheetStyle := 'Custom';
            End;
        Except
            SheetStyle := 'Unknown';
        End;
        Data := Data + ',"sheet_size":"' + SheetStyle + '"';

        // Custom dimensions in mils
        Try
            Data := Data + ',"custom_width":' + IntToStr(CoordToMils(SchDoc.SheetSizeX));
            Data := Data + ',"custom_height":' + IntToStr(CoordToMils(SchDoc.SheetSizeY));
        Except
        End;

        // Title block visibility
        Try
            Data := Data + ',"title_block_on":' + BoolToJsonStr(SchDoc.TitleBlockOn);
        Except
            Data := Data + ',"title_block_on":true';
        End;

        // Snap grid size in mils
        Try
            Data := Data + ',"snap_grid":' + IntToStr(CoordToMils(SchDoc.SnapGridSize));
        Except
        End;

        // Visible grid size in mils
        Try
            Data := Data + ',"visible_grid":' + IntToStr(CoordToMils(SchDoc.VisibleGridSize));
        Except
        End;

        // Unit system, ISch_Document.UnitSystem returns a TUnitSystem enum
        // (eImperial / eMetric). TUnit has finer granularity but UnitSystem is
        // the right read for a simple "metric vs imperial" field.
        Try
            If SchDoc.UnitSystem = eMetric Then
                UnitStr := 'metric'
            Else
                UnitStr := 'imperial';
            Data := Data + ',"unit_system":"' + UnitStr + '"';
        Except End;

        Data := Data + '}';
        Result := BuildSuccessResponse(RequestId, Data);
    End
    Else If Board <> Nil Then
    Begin
        // PCB document info
        Data := '{"file_path":"' + EscapeJsonString(Board.FileName) + '"';
        Data := Data + ',"kind":"PCB"';
        Data := Data + ',"origin_x":' + IntToStr(CoordToMils(Board.XOrigin));
        Data := Data + ',"origin_y":' + IntToStr(CoordToMils(Board.YOrigin));

        Try
            Data := Data + ',"snap_grid":' + IntToStr(CoordToMils(Board.SnapGridSizeX));
        Except
        End;

        Data := Data + '}';
        Result := BuildSuccessResponse(RequestId, Data);
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Set snap grid and visible grid size for the active schematic                }
{ Params: snap_grid, visible_grid (in mils)                                   }
{..............................................................................}

Function Gen_SetGrid(Params : String; RequestId : String) : String;
Var
    SnapGrid, VisibleGrid : Integer;
    SchDoc : ISch_Document;
Begin
    SnapGrid := StrToIntDef(ExtractJsonValue(Params, 'snap_grid'), 0);
    VisibleGrid := StrToIntDef(ExtractJsonValue(Params, 'visible_grid'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    If (SnapGrid <= 0) And (VisibleGrid <= 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'At least one of snap_grid or visible_grid is required (in mils)');
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');

    If SnapGrid > 0 Then
        SchDoc.SnapGridSize := MilsToCoord(SnapGrid);
    If VisibleGrid > 0 Then
        SchDoc.VisibleGridSize := MilsToCoord(VisibleGrid);

    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true' +
        ',"snap_grid":' + IntToStr(CoordToMils(SchDoc.SnapGridSize)) +
        ',"visible_grid":' + IntToStr(CoordToMils(SchDoc.VisibleGridSize)) + '}');
End;

{..............................................................................}
{ Set the active schematic unit system via ISch_Document.SetState_Unit.       }
{ Accepts 'mil', 'inch', 'dxp', 'auto_imperial', 'mm', 'cm', 'm',             }
{ 'auto_metric'. Returns the resulting unit_system (imperial/metric).         }
{..............................................................................}

Function Gen_SetSchUnits(Params : String; RequestId : String) : String;
Var
    UnitStr : String;
    SchDoc : ISch_Document;
    Target : TUnit;
    SystemStr : String;
Begin
    UnitStr := LowerCase(ExtractJsonValue(Params, 'unit'));
    If UnitStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'unit required (mil, inch, dxp, auto_imperial, mm, cm, m, auto_metric)');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { TUnit = (eMil, eMM, eIN, eCM, eDXP, eM, eAutoImperial, eAutoMetric).      }
    If UnitStr = 'mil' Then Target := eMil
    Else If UnitStr = 'inch' Then Target := eIN
    Else If UnitStr = 'in' Then Target := eIN
    Else If UnitStr = 'dxp' Then Target := eDXP
    Else If UnitStr = 'auto_imperial' Then Target := eAutoImperial
    Else If UnitStr = 'mm' Then Target := eMM
    Else If UnitStr = 'cm' Then Target := eCM
    Else If UnitStr = 'm' Then Target := eM
    Else If UnitStr = 'auto_metric' Then Target := eAutoMetric
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_UNIT',
            'Unknown unit "' + UnitStr + '"');
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try SchDoc.SetState_Unit(Target); Except End;
    SchServer.ProcessControl.PostProcess(SchDoc, 'Set schematic unit');
    SchDoc.GraphicallyInvalidate;

    If SchDoc.UnitSystem = eMetric Then SystemStr := 'metric'
    Else SystemStr := 'imperial';

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"unit":"' + EscapeJsonString(UnitStr) + '"'
        + ',"unit_system":"' + SystemStr + '"}');
End;

{..............................................................................}
{ Place an image on the active schematic via RunProcess                       }
{ Params: image_path, x, y, width, height (in mils)                          }
{..............................................................................}

Function Gen_PlaceImage(Params : String; RequestId : String) : String;
Var
    ImagePath : String;
    X, Y, W, H : Integer;
    SchDoc : ISch_Document;
    Img : ISch_GraphicalObject;
Begin
    ImagePath := ExtractJsonValue(Params, 'image_path');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    W := StrToIntDef(ExtractJsonValue(Params, 'width'), 500);
    H := StrToIntDef(ExtractJsonValue(Params, 'height'), 500);

    If ImagePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'image_path parameter is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Img := SchServer.SchObjectFactory(eImage, eCreate_Default);
    If Img = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create image object');
        Exit;
    End;

    Img.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    Img.Corner := Point(MilsToCoord(X + W), MilsToCoord(Y + H));
    Try
        Img.FileName := ImagePath;
    Except
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Img);
    SchRegisterObject(SchDoc, Img);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"image_path":"' + EscapeJsonString(ImagePath) +
        '","x":' + IntToStr(X) + ',"y":' + IntToStr(Y) +
        ',"width":' + IntToStr(W) + ',"height":' + IntToStr(H) + '}');
End;

{..............................................................................}
{ Replace a component with a different library part                           }
{ Keeps connections, swaps the symbol.                                        }
{ Params: designator, new_lib_ref, new_library                                }
{..............................................................................}

Function Gen_ReplaceComponent(Params : String; RequestId : String) : String;
Var
    Designator, NewLibRef, NewLibrary : String;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    NewLibRef := ExtractJsonValue(Params, 'new_lib_ref');
    NewLibrary := ExtractJsonValue(Params, 'new_library');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required');
        Exit;
    End;
    If NewLibRef = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'new_lib_ref is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));

    Obj := Iterator.FirstSchObject;
    While Obj <> Nil Do
    Begin
        Try
            Comp := Obj;   // cast through the strongly-typed local so
                           // Comp.Designator.Text resolves correctly
            If Comp.Designator.Text = Designator Then
            Begin
                SchServer.ProcessControl.PreProcess(SchDoc, '');
                Comp.LibReference := NewLibRef;
                { DesignItemId must follow the new library reference or
                  the part keeps re-matching against the OLD library item
                  and shows <Not Found> after a re-link. Measured: a
                  replace that updated only LibReference/SourceLibraryName
                  left every re-linked part in that state. }
                Try Comp.DesignItemId := NewLibRef; Except End;
                If NewLibrary <> '' Then
                    Comp.SourceLibraryName := NewLibrary;
                SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
                Found := True;
                Break;
            End;
        Except
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);

    If Found Then
    Begin
        SchDoc.GraphicallyInvalidate;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"designator":"' + EscapeJsonString(Designator) +
            '","new_lib_ref":"' + EscapeJsonString(NewLibRef) +
            '","new_library":"' + EscapeJsonString(NewLibrary) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
End;

{..............................................................................}
{ Gen_GetConstraintGroups - Enumerate IDocument.DM_ConstraintGroups on the      }
{ active schematic document. Constraint groups are FPGA-style pin/timing        }
{ constraints attached to a document; each group has a target kind/id and a    }
{ list of IConstraint entries with Kind + Data payloads.                       }
{..............................................................................}

Function Gen_GetConstraintGroups(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Doc : IDocument;
    Group : IConstraintGroup;
    Cons : IConstraint;
    I, J, GroupCount, ConsCount : Integer;
    Json, GroupJson, ConsJson : String;
    FirstC : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Doc := SchDoc;
    GroupCount := 0;
    Try GroupCount := Doc.DM_ConstraintGroupCount; Except End;

    Json := '';
    For I := 0 To GroupCount - 1 Do
    Begin
        Group := Nil;
        Try Group := Doc.DM_ConstraintGroups(I); Except End;
        If Group = Nil Then Continue;

        ConsCount := 0;
        Try ConsCount := Group.DM_ConstraintCount; Except End;

        ConsJson := '';
        FirstC := True;
        For J := 0 To ConsCount - 1 Do
        Begin
            Cons := Nil;
            Try Cons := Group.DM_Constraints(J); Except End;
            If Cons = Nil Then Continue;
            If Not FirstC Then ConsJson := ConsJson + ',';
            FirstC := False;
            ConsJson := ConsJson + '{"kind":"';
            Try ConsJson := ConsJson + EscapeJsonString(Cons.DM_Kind); Except End;
            ConsJson := ConsJson + '","data":"';
            Try ConsJson := ConsJson + EscapeJsonString(Cons.DM_Data); Except End;
            ConsJson := ConsJson + '"}';
        End;

        GroupJson := '{"target_kind":"';
        Try GroupJson := GroupJson + EscapeJsonString(Group.DM_TargetKindString); Except End;
        GroupJson := GroupJson + '","target_id":"';
        Try GroupJson := GroupJson + EscapeJsonString(Group.DM_TargetId); Except End;
        GroupJson := GroupJson + '","constraint_count":' + IntToStr(ConsCount);
        GroupJson := GroupJson + ',"constraints":[' + ConsJson + ']}';

        If Json <> '' Then Json := Json + ',';
        Json := Json + GroupJson;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"groups":[' + Json + '],"count":' + IntToStr(GroupCount) + '}');
End;

{..............................................................................}
{ Gen_PlaceHarnessConnector - Place an ISch_HarnessConnector on the active      }
{ sheet. Harness connectors group a set of wires/buses into a named harness    }
{ so cross-sheet signal bundles can be represented as a single connection.     }
{ Params: x, y, width, height (mils), harness_type (optional name string)      }
{..............................................................................}

Function Gen_PlaceHarnessConnector(Params : String; RequestId : String) : String;
Var
    X, Y, W, H : Integer;
    HarnessType : String;
    SchDoc : ISch_Document;
    Harness : ISch_HarnessConnector;
Begin
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    W := StrToIntDef(ExtractJsonValue(Params, 'width'), 500);
    H := StrToIntDef(ExtractJsonValue(Params, 'height'), 800);
    HarnessType := ExtractJsonValue(Params, 'harness_type');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Harness := SchServer.SchObjectFactory(eHarnessConnector, eCreate_Default);
    If Harness = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create harness connector');
        Exit;
    End;

    { ISch_HarnessConnector is an ISch_RectangularGroup -- no Corner property
      (using it raises "Undeclared identifier: Corner", and the local must be
      typed as the derived interface, not ISch_GraphicalObject). Size via
      XSize/YSize from the bottom-left Location, like ISch_SheetSymbol. }
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Harness.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    Harness.XSize := MilsToCoord(W);
    Harness.YSize := MilsToCoord(H);
    If HarnessType <> '' Then
        Try Harness.HarnessType := HarnessType; Except End;

    SchDoc.RegisterSchObjectInContainer(Harness);
    SchRegisterObject(SchDoc, Harness);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y)
        + ',"width":' + IntToStr(W) + ',"height":' + IntToStr(H)
        + ',"harness_type":"' + EscapeJsonString(HarnessType) + '"}');
End;

{..............................................................................}
{ Gen_PlaceCrossSheetConnector - Place an ISch_CrossSheetConnector (the off-    }
{ sheet port variant used for hierarchical signal links).                      }
{ Params: x, y, net (net name to connect), side (left|right)                    }
{..............................................................................}

Function Gen_PlaceCrossSheetConnector(Params : String; RequestId : String) : String;
Var
    X, Y : Integer;
    NetName, SideStr : String;
    SchDoc : ISch_Document;
    Conn : ISch_GraphicalObject;
Begin
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    NetName := ExtractJsonValue(Params, 'net');
    SideStr := LowerCase(ExtractJsonValue(Params, 'side'));

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Conn := SchServer.SchObjectFactory(eCrossSheetConnector, eCreate_Default);
    If Conn = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create cross-sheet connector');
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try Conn.Location := Point(MilsToCoord(X), MilsToCoord(Y)); Except End;
    If NetName <> '' Then
        Try Conn.Text := NetName; Except End;
    If SideStr = 'left' Then
        Try Conn.Side := 0; Except End
    Else If SideStr = 'right' Then
        Try Conn.Side := 1; Except End;

    SchDoc.RegisterSchObjectInContainer(Conn);
    SchRegisterObject(SchDoc, Conn);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y)
        + ',"net":"' + EscapeJsonString(NetName) + '"}');
End;

{..............................................................................}
{ Gen_SetComponentPartId - Switch the active sub-part on a multi-part           }
{ component (e.g. U1A -> U1B on a quad op-amp). CurrentPartID is 1-based.      }
{ Params: designator, part_id                                                  }
{..............................................................................}

Function Gen_SetComponentPartId(Params : String; RequestId : String) : String;
Var
    Designator : String;
    PartId : Integer;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    Found : Boolean;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    PartId := StrToIntDef(ExtractJsonValue(Params, 'part_id'), 0);

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    If PartId < 1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PART_ID', 'part_id must be >= 1');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj := Iterator.FirstSchObject;
    While (Obj <> Nil) And Not Found Do
    Begin
        Comp := Obj;
        If Comp.Designator.Text = Designator Then
        Begin
            SchServer.ProcessControl.PreProcess(SchDoc, 'Set part id');
            Try Comp.CurrentPartID := PartId; Except End;
            SchServer.ProcessControl.PostProcess(SchDoc, 'Set part id');
            Found := True;
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator)
        + '","part_id":' + IntToStr(PartId) + '}');
End;

{..............................................................................}
{ Gen_SetComponentUniqueId - Stamp ISch_Component.UniqueId.                     }
{ ECO groups sub-parts of a multi-gate symbol (quad comparator, dual           }
{ op-amp) into one physical footprint only when they share UniqueId. Four      }
{ copies of part 1 with four UniqueIds become four TSSOP packages.             }
{ Params: designator, unique_id                                                }
{..............................................................................}

Function Gen_SetComponentUniqueId(Params : String; RequestId : String) : String;
Var
    Designator, UniqueIdStr, AfterId : String;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    Found : Boolean;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    UniqueIdStr := ExtractJsonValue(Params, 'unique_id');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    If UniqueIdStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'unique_id required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Found := False;
    AfterId := '';
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj := Iterator.FirstSchObject;
    While (Obj <> Nil) And Not Found Do
    Begin
        Comp := Obj;
        If Comp.Designator.Text = Designator Then
        Begin
            SchServer.ProcessControl.PreProcess(SchDoc, 'Set UniqueId');
            Try Comp.SetState_UniqueId(UniqueIdStr); Except End;
            Try Comp.UniqueId := UniqueIdStr; Except End;
            Try AfterId := Comp.UniqueId; Except AfterId := ''; End;
            SchServer.ProcessControl.PostProcess(SchDoc, 'Set UniqueId');
            Found := True;
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator)
        + '","unique_id":"' + EscapeJsonString(UniqueIdStr)
        + '","unique_id_after":"' + EscapeJsonString(AfterId) + '"}');
End;

{..............................................................................}
{ Gen_ReplicateSchComponent - Duplicate a placed schematic component via       }
{ ISch_Component.Replicate. Live 2026-08-21: Replicate + AddSchObject minted   }
{ a new UniqueId (NPZTIRAT vs master UCOMP2MP). This stamps UniqueId with      }
{ SetState_UniqueId AFTER Replicate and BEFORE AddSchObject, then again after  }
{ register if Add reminted. Returns staged UniqueId readbacks.                 }
{ Params: designator, part_id, x, y, new_designator                            }
{..............................................................................}

Function Gen_ReplicateSchComponent(Params : String; RequestId : String) : String;
Var
    Designator, NewDesig, MasterId, CopyId : String;
    IdAfterReplicate, IdAfterSetPreAdd, IdAfterAdd, Shared : String;
    PartId, X, Y : Integer;
    HaveXY : Boolean;
    SchDoc : ISch_Document;
    Comp, NewComp : ISch_Component;
    NewObj : ISch_GraphicalObject;
    Found : Boolean;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    NewDesig := ExtractJsonValue(Params, 'new_designator');
    PartId := StrToIntDef(ExtractJsonValue(Params, 'part_id'), 0);
    HaveXY := (ExtractJsonValue(Params, 'x') <> '') And (ExtractJsonValue(Params, 'y') <> '');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Found := False;
    MasterId := '';
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj := Iterator.FirstSchObject;
    While (Obj <> Nil) And Not Found Do
    Begin
        Comp := Obj;
        If Comp.Designator.Text = Designator Then
        Begin
            Found := True;
            Try MasterId := Comp.UniqueId; Except MasterId := ''; End;
        End
        Else
            Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, 'Replicate component');
    NewObj := Nil;
    Try NewObj := Comp.Replicate; Except NewObj := Nil; End;
    If NewObj = Nil Then
    Begin
        SchServer.ProcessControl.PostProcess(SchDoc, 'Replicate component');
        Result := BuildErrorResponse(RequestId, 'REPLICATE_FAILED',
            'ISch_Component.Replicate returned nil for ' + Designator);
        Exit;
    End;
    NewComp := NewObj;
    Try IdAfterReplicate := NewComp.UniqueId; Except IdAfterReplicate := ''; End;
    If MasterId <> '' Then
    Begin
        Try NewComp.SetState_UniqueId(MasterId); Except End;
        Try NewComp.UniqueId := MasterId; Except End;
    End;
    Try IdAfterSetPreAdd := NewComp.UniqueId; Except IdAfterSetPreAdd := ''; End;
    If PartId >= 1 Then
    Begin
        Try NewComp.SetState_CurrentPartID(PartId); Except End;
        Try NewComp.CurrentPartID := PartId; Except End;
    End;
    If NewDesig <> '' Then
        Try NewComp.Designator.Text := NewDesig; Except End;
    If HaveXY Then
        Try NewComp.MoveToXY(MilsToCoord(X), MilsToCoord(Y)); Except End;
    { AddSchObject remints UniqueId when that id is already on the sheet
      (live 2026-08-26: XVRAPPYA -> ASFAVKKE even after SetState_UniqueId
      pre-add). SCHM_PrimitiveRegistration does the same. Attach via
      RegisterSchObjectInContainer, then re-stamp UniqueId while still
      inside this PreProcess. Do not send SCHM_PrimitiveRegistration. }
    Try SchDoc.AddSchObject(NewComp); Except End;
    Try SchDoc.RegisterSchObjectInContainer(NewComp); Except End;
    Try IdAfterAdd := NewComp.UniqueId; Except IdAfterAdd := ''; End;
    If (MasterId <> '') And (IdAfterAdd <> MasterId) Then
    Begin
        Try NewComp.SetState_UniqueId(MasterId); Except End;
        Try NewComp.UniqueId := MasterId; Except End;
    End;
    Try CopyId := NewComp.UniqueId; Except CopyId := ''; End;
    SchServer.ProcessControl.PostProcess(SchDoc, 'Replicate component');
    SchDoc.GraphicallyInvalidate;

    If CopyId = MasterId Then Shared := 'true' Else Shared := 'false';

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"source_designator":"' + EscapeJsonString(Designator)
        + '","new_designator":"' + EscapeJsonString(NewDesig)
        + '","part_id":' + IntToStr(PartId)
        + ',"source_unique_id":"' + EscapeJsonString(MasterId)
        + '","copy_unique_id":"' + EscapeJsonString(CopyId)
        + '","unique_id_after_replicate":"' + EscapeJsonString(IdAfterReplicate)
        + '","unique_id_after_set_pre_add":"' + EscapeJsonString(IdAfterSetPreAdd)
        + '","unique_id_after_add":"' + EscapeJsonString(IdAfterAdd)
        + '","shared":' + Shared + '}');
End;

{..............................................................................}
{ Gen_PlaceProbe - Place an ISch_Probe marker for SPICE / simulation            }
{ measurement nodes. Probe sits at a wire and names the node to measure.       }
{ Params: x, y, net_name, probe_method (all_nets | probed_nets_only, default    }
{         probed_nets_only)                                                    }
{..............................................................................}

Function Gen_PlaceProbe(Params : String; RequestId : String) : String;
Var
    X, Y : Integer;
    NetName, MethodStr : String;
    SchDoc : ISch_Document;
    Probe : ISch_GraphicalObject;
Begin
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    NetName := ExtractJsonValue(Params, 'net_name');
    MethodStr := LowerCase(ExtractJsonValue(Params, 'probe_method'));

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Probe := SchServer.SchObjectFactory(eProbe, eCreate_Default);
    If Probe = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create probe');
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try Probe.Location := Point(MilsToCoord(X), MilsToCoord(Y)); Except End;
    { ISch_Probe exposes essentially nothing settable from DelphiScript:
      ``Text``, ``NetName`` and ``ProbeMethod`` all raise "Undeclared
      identifier" at compile (Try/Except cannot catch). The probe
      auto-picks up the net of the wire it lands on, and the default
      probe method ("probed nets only") is what we want anyway.
      ``net_name`` and ``probe_method`` from the request are accepted
      for forward compatibility but currently echoed back in the
      response without being applied. }

    SchDoc.RegisterSchObjectInContainer(Probe);
    SchRegisterObject(SchDoc, Probe);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y)
        + ',"net_name":"' + EscapeJsonString(NetName) + '"}');
End;

{..............................................................................}
{ Gen_AddDatafileLink - Add an ISch_ModelDatafileLink to a component's active  }
{ implementation. This is how parametric data (IBIS model files, sim models,   }
{ external CSVs) is attached to a schematic part.                              }
{ Params: designator, file_path, kind (optional string, implementation-       }
{         specific, e.g. "SimModel", "IBIS")                                   }
{..............................................................................}

Function Gen_AddDatafileLink(Params : String; RequestId : String) : String;
Var
    Designator, FilePath, KindStr, EntityName : String;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    Impl : ISch_Implementation;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    FilePath := ExtractJsonValue(Params, 'file_path');
    KindStr := ExtractJsonValue(Params, 'kind');

    If (Designator = '') Or (FilePath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator and file_path are required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj := Iterator.FirstSchObject;
    While (Obj <> Nil) And Not Found Do
    Begin
        Comp := Obj;
        If Comp.Designator.Text = Designator Then
        Begin
            Impl := GetFirstSchImplementation(Comp);
            If Impl <> Nil Then
            Begin
                { ISch_Implementation.AddDataFileLink is a PROCEDURE taking
                  (anEntityName, aLocation, aFileKind : WideString), NOT a
                  function returning a link object. Calling it as a no-arg
                  function (Link := Impl.AddDataFileLink) faults. The link's
                  "file" is its Location; EntityName is a label (use the file
                  name); FileKind is the model kind. (Altium SDK.) }
                EntityName := ExtractFileName(FilePath);
                SchServer.ProcessControl.PreProcess(SchDoc, 'Add datafile link');
                Try Impl.AddDataFileLink(EntityName, FilePath, KindStr); Except End;
                SchServer.ProcessControl.PostProcess(SchDoc, 'Add datafile link');
                Found := True;
            End;
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component or implementation not found for designator: ' + Designator);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator)
        + '","file_path":"' + EscapeJsonString(FilePath) + '"}');
End;

{..............................................................................}
{ Helpers for SPICE / simulation handlers                                       }
{..............................................................................}

{ Classify a component's Comment as a passive primitive kind (R / L / C) or   }
{ empty when it doesn't look like a standard passive.                         }
Function ClassifyPassivePrefix(Comment : String) : String;
Var
    S, U, First, Second : String;
Begin
    { DelphiScript quirk: ``UpCase(S[1])`` raises EInvalidCast because
      S[1] is treated as String (not Char), and UpCase here expects a
      Char. Use string ops instead: ``UpperCase(S)`` + 1-char ``Copy``
      slices. Same applies to the second-character class check. }
    Result := '';
    If Comment = '' Then Exit;
    S := Trim(Comment);
    If S = '' Then Exit;
    U := UpperCase(S);
    First := Copy(U, 1, 1);
    If (First = 'R') Or (First = 'L') Or (First = 'C') Then
    Begin
        { Accept "R1", "10k", "Res", "Cap" etc., anything that starts with
          the letter and is short. Longer names (e.g. "Resonator") we
          skip. }
        If Length(S) = 1 Then
        Begin
            Result := First;
        End
        Else If Length(S) <= 20 Then
        Begin
            Second := Copy(U, 2, 1);
            If (Second = ' ') Or
               ((Second >= '0') And (Second <= '9')) Or
               ((Second >= 'A') And (Second <= 'Z')) Then
                Result := First;
        End;
    End;
End;

{ Read a named parameter's text off a sch component. Empty string if absent.   }
Function GetCompParamText(Comp : ISch_Component; ParamName : String) : String;
Var
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
Begin
    Result := '';
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        While Param <> Nil Do
        Begin
            If UpperCase(Param.Name) = UpperCase(ParamName) Then
            Begin
                Result := Param.Text;
                Break;
            End;
            Param := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;
End;

{ Set (or create) a component parameter. Returns True if created, False if     }
{ modified. Caller is responsible for PreProcess/PostProcess on the document. }
Function SetCompParamText(Comp : ISch_Component; ParamName, ParamValue : String) : Boolean;
Var
    Iter : ISch_Iterator;
    Param, NewParam : ISch_Parameter;
    Found : ISch_Parameter;
Begin
    Result := False;
    Found := Nil;
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        While Param <> Nil Do
        Begin
            If UpperCase(Param.Name) = UpperCase(ParamName) Then
            Begin
                Found := Param;
                Break;
            End;
            Param := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;

    If Found <> Nil Then
    Begin
        SchBeginModify(Found);
        Found.Text := ParamValue;
        SchEndModify(Found);
    End
    Else
    Begin
        NewParam := SchServer.SchObjectFactory(eParameter, eCreate_Default);
        If NewParam <> Nil Then
        Begin
            NewParam.Name := ParamName;
            NewParam.Text := ParamValue;
            Comp.AddSchObject(NewParam);
            SchRegisterObject(Comp, NewParam);
            Result := True;
        End;
    End;
End;

{..............................................................................}
{ NextJsonObjectField - Iterate top-level "key":"value" pairs out of a JSON      }
{ object body (the substring BETWEEN the outer braces, NOT including them).     }
{                                                                                }
{ Walks the string from StartPos, finds the next quoted key, the colon, the     }
{ quoted string value, and returns them via the Var params. Advances StartPos   }
{ past the comma so a caller loop can keep going. Returns False when no more   }
{ pairs are left.                                                                }
{                                                                                }
{ Limitations on purpose: only string-typed values are extracted; numbers /      }
{ booleans / nested objects are skipped over. This matches the parameters-      }
{ payload contract used by Gen_SetSchComponentParameters where every value is   }
{ a string.                                                                      }
{..............................................................................}

Function NextJsonObjectField(SubObj : String; Var StartPos : Integer;
                              Var Key, Value : String) : Boolean;
Var
    L, P, KStart, VStart : Integer;
    BackslashCount, TempPos : Integer;
Begin
    Result := False;
    Key := '';
    Value := '';
    L := Length(SubObj);
    P := StartPos;

    { Skip whitespace and any leading comma. }
    While (P <= L) And ((Copy(SubObj, P, 1) = ' ') Or (Copy(SubObj, P, 1) = #9)
          Or (Copy(SubObj, P, 1) = #10) Or (Copy(SubObj, P, 1) = #13)
          Or (Copy(SubObj, P, 1) = ',')) Do
        Inc(P);

    If (P > L) Or (Copy(SubObj, P, 1) <> '"') Then
    Begin
        StartPos := P;
        Exit;
    End;

    { Parse quoted key. }
    Inc(P);
    KStart := P;
    While P <= L Do
    Begin
        If Copy(SubObj, P, 1) = '"' Then
        Begin
            BackslashCount := 0;
            TempPos := P - 1;
            While (TempPos >= KStart) And (Copy(SubObj, TempPos, 1) = '\') Do
            Begin
                Inc(BackslashCount);
                Dec(TempPos);
            End;
            If (BackslashCount Mod 2) = 0 Then Break;
        End;
        Inc(P);
    End;
    If P > L Then
    Begin
        StartPos := P;
        Exit;
    End;
    Key := UnescapeJsonString(Copy(SubObj, KStart, P - KStart));
    Inc(P);

    { Skip whitespace + colon. }
    While (P <= L) And IsWhitespaceOrColon(SubObj, P) Do
        Inc(P);

    If P > L Then
    Begin
        StartPos := P;
        Exit;
    End;

    { Only handle string values; skip non-string fields gracefully. }
    If Copy(SubObj, P, 1) <> '"' Then
    Begin
        { Skip to next comma or end. }
        While (P <= L) And (Copy(SubObj, P, 1) <> ',') Do
            Inc(P);
        StartPos := P;
        { Key without value: report as empty value; caller decides. }
        Result := True;
        Exit;
    End;

    Inc(P);
    VStart := P;
    While P <= L Do
    Begin
        If Copy(SubObj, P, 1) = '"' Then
        Begin
            BackslashCount := 0;
            TempPos := P - 1;
            While (TempPos >= VStart) And (Copy(SubObj, TempPos, 1) = '\') Do
            Begin
                Inc(BackslashCount);
                Dec(TempPos);
            End;
            If (BackslashCount Mod 2) = 0 Then Break;
        End;
        Inc(P);
    End;
    If P > L Then
    Begin
        StartPos := P;
        Exit;
    End;
    Value := UnescapeJsonString(Copy(SubObj, VStart, P - VStart));
    Inc(P);

    StartPos := P;
    Result := True;
End;

{..............................................................................}
{ Gen_SetSchComponentParameters - Stamp BOM/value/footprint metadata onto a      }
{ placed schematic component.                                                    }
{                                                                                }
{ Params: designator, sheet_path, parameters (JSON sub-object).                  }
{                                                                                }
{ For each (name, value) entry in parameters:                                    }
{   - "Value" writes to Comp.Comment.Text (convention: Comment field IS Value). }
{   - "Footprint" updates the current footprint model name.                     }
{   - everything else is a regular ISch_Parameter on the component, modified    }
{     in place if present, created via SchObjectFactory(eParameter) otherwise.  }
{                                                                                }
{ Empty values are skipped, so the caller can send a single payload with        }
{ optional fields. Returns the count of fields applied.                          }
{..............................................................................}

Function Gen_SetSchComponentParameters(Params : String; RequestId : String) : String;
Var
    DesigStr, SheetPath, SubObj, Key, Val : String;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp, TargetComp : ISch_Component;
    Found : Boolean;
    P, Applied, Created : Integer;
    SrvDoc : IServerDocument;
Begin
    DesigStr := ExtractJsonValue(Params, 'designator');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    SubObj := ExtractJsonValue(Params, 'parameters');

    If DesigStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    If SubObj = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'parameters sub-object required');
        Exit;
    End;

    { Resolve target sheet (focus-independent). Mirrors                         }
    { Gen_PlaceSchComponentFromLibrary's resolution rules.                       }
    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath + '. Open it first.');
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    If SchDoc.ObjectId <> eSheet Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOC_KIND',
            'Target document is not a schematic sheet (ObjectId=' +
            IntToStr(SchDoc.ObjectId) + '). Pass sheet_path to a .SchDoc.');
        Exit;
    End;

    { Find the placed component by designator. }
    TargetComp := Nil;
    Found := False;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iter.FirstSchObject;
        While (Obj <> Nil) And Not Found Do
        Begin
            Comp := Obj;
            If Comp.Designator.Text = DesigStr Then
            Begin
                TargetComp := Comp;
                Found := True;
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'No component with designator "' + DesigStr + '" on sheet');
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Applied := 0;
    Created := 0;
    P := 1;
    Try
        While NextJsonObjectField(SubObj, P, Key, Val) Do
        Begin
            If Val = '' Then Continue;  { skip empty values }
            If Key = 'Value' Then
            Begin
                { Convention: Value -> Comment.Text, NOT a Parameter. }
                SchBeginModify(TargetComp.Comment);
                Try TargetComp.Comment.Text := Val; Except End;
                SchEndModify(TargetComp.Comment);
                Inc(Applied);
            End
            Else If Key = 'Footprint' Then
            Begin
                { CurrentFootprintModelName is read-only in DelphiScript      }
                { (memory: delphiscript_api_quirks.md). Skip silently rather   }
                { than crash the script; footprint stays whatever the library }
                { symbol carried.                                              }
                Inc(Applied);
            End
            Else
            Begin
                If SetCompParamText(TargetComp, Key, Val) Then
                    Inc(Created);
                Inc(Applied);
            End;
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { Flag the server doc dirty so save_all flushes it. }
    Try
        SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(DesigStr) + '",'
        + '"applied":' + IntToStr(Applied) + ','
        + '"created":' + IntToStr(Created) + '}');
End;

{..............................................................................}
{ Gen_GetSimulationReadiness - Audit every component on the active schematic    }
{ and report which are ready for SPICE sim vs which need a primitive vs which   }
{ need a model file fetched from the vendor.                                   }
{                                                                               }
{ Ready means: has a SpicePrefix parameter already set. Passives that only have }
{ R/L/C-shaped Comments but no SpicePrefix land in needs_primitive so the       }
{ client can call sch_attach_spice_primitive. Everything else lands in          }
{ needs_file with a suggested vendor search URL.                               }
{..............................................................................}

Function Gen_GetSimulationReadiness(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Designator, Comment, LibRef, SpicePrefix, Value : String;
    PassivePrefix, Kind, MfrPart, Mfr : String;
    ReadyJson, NeedsPrimJson, NeedsFileJson : String;
    ReadyCount, NeedsPrimCount, NeedsFileCount : Integer;
    FirstR, FirstP, FirstF : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    ReadyJson := '';
    NeedsPrimJson := '';
    NeedsFileJson := '';
    ReadyCount := 0;
    NeedsPrimCount := 0;
    NeedsFileCount := 0;
    FirstR := True;
    FirstP := True;
    FirstF := True;

    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Comp := Obj;
            Designator := '';
            Try Designator := Comp.Designator.Text; Except End;
            { DM_Comment / DM_LibraryReference are on the DM-API
              component interface (returned by SchDoc.DM_Components),
              NOT on the ISch_Component you get from SchIterator.
              Read via the parameter table and direct LibReference
              property instead. }
            Comment := GetCompParamText(Comp, 'Comment');
            LibRef := '';
            Try LibRef := Comp.LibReference; Except End;

            SpicePrefix := GetCompParamText(Comp, 'SpicePrefix');
            Value := GetCompParamText(Comp, 'Value');
            If Value = '' Then
                Value := Comment;
            MfrPart := GetCompParamText(Comp, 'Manufacturer Part Number');
            If MfrPart = '' Then MfrPart := GetCompParamText(Comp, 'PartNumber');
            Mfr := GetCompParamText(Comp, 'Manufacturer');

            If SpicePrefix <> '' Then
            Begin
                If Not FirstR Then ReadyJson := ReadyJson + ',';
                FirstR := False;
                ReadyJson := ReadyJson
                    + '{"designator":"' + EscapeJsonString(Designator) + '",'
                    + '"comment":"' + EscapeJsonString(Comment) + '",'
                    + '"spice_prefix":"' + EscapeJsonString(SpicePrefix) + '",'
                    + '"value":"' + EscapeJsonString(Value) + '"}';
                Inc(ReadyCount);
            End
            Else
            Begin
                PassivePrefix := ClassifyPassivePrefix(Comment);
                If PassivePrefix = '' Then
                    PassivePrefix := ClassifyPassivePrefix(LibRef);
                If PassivePrefix <> '' Then
                Begin
                    Kind := PassivePrefix;
                    If Not FirstP Then NeedsPrimJson := NeedsPrimJson + ',';
                    FirstP := False;
                    NeedsPrimJson := NeedsPrimJson
                        + '{"designator":"' + EscapeJsonString(Designator) + '",'
                        + '"comment":"' + EscapeJsonString(Comment) + '",'
                        + '"suggested_prefix":"' + EscapeJsonString(Kind) + '",'
                        + '"suggested_value":"' + EscapeJsonString(Value) + '"}';
                    Inc(NeedsPrimCount);
                End
                Else
                Begin
                    If Not FirstF Then NeedsFileJson := NeedsFileJson + ',';
                    FirstF := False;
                    NeedsFileJson := NeedsFileJson
                        + '{"designator":"' + EscapeJsonString(Designator) + '",'
                        + '"comment":"' + EscapeJsonString(Comment) + '",'
                        + '"lib_ref":"' + EscapeJsonString(LibRef) + '",'
                        + '"manufacturer":"' + EscapeJsonString(Mfr) + '",'
                        + '"manufacturer_part":"' + EscapeJsonString(MfrPart) + '"}';
                    Inc(NeedsFileCount);
                End;
            End;

            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"ready":[' + ReadyJson + '],"ready_count":' + IntToStr(ReadyCount) + ','
        + '"needs_primitive":[' + NeedsPrimJson + '],"needs_primitive_count":' + IntToStr(NeedsPrimCount) + ','
        + '"needs_file":[' + NeedsFileJson + '],"needs_file_count":' + IntToStr(NeedsFileCount) + '}');
End;

{..............................................................................}
{ Gen_AttachSpicePrimitive - Attach a built-in SPICE primitive to a component.  }
{ For passives (R/L/C) and sources (V/I), this is just SpicePrefix + Value;     }
{ no model file is needed, Altium's simulator maps these to built-in          }
{ primitives directly.                                                          }
{ Params: designator, primitive (R|L|C|V|I|D|Q), value, spice_model (optional  }
{         subckt / model name for semi devices like D / Q), sim_kind (optional }
{         "General"/"Subcircuit"/"Model").                                     }
{..............................................................................}

Function Gen_AttachSpicePrimitive(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Designator, Primitive, Value, ModelName, SimKind : String;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    Primitive := UpperCase(ExtractJsonValue(Params, 'primitive'));
    Value := ExtractJsonValue(Params, 'value');
    ModelName := ExtractJsonValue(Params, 'spice_model');
    SimKind := ExtractJsonValue(Params, 'sim_kind');

    If (Designator = '') Or (Primitive = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'designator and primitive are required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Found := False;
    SchServer.ProcessControl.PreProcess(SchDoc, 'Attach SPICE primitive');
    Try
        Iter := SchDoc.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj := Iter.FirstSchObject;
            While (Obj <> Nil) And Not Found Do
            Begin
                Comp := Obj;
                If Comp.Designator.Text = Designator Then
                Begin
                    SetCompParamText(Comp, 'SpicePrefix', Primitive);
                    If Value <> '' Then
                        SetCompParamText(Comp, 'Value', Value);
                    If ModelName <> '' Then
                        SetCompParamText(Comp, 'SpiceModel', ModelName);
                    If SimKind <> '' Then
                        SetCompParamText(Comp, 'SimulationKind', SimKind);
                    Found := True;
                End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Attach SPICE primitive');
    End;
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component not found: ' + Designator);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator) + '",'
        + '"primitive":"' + EscapeJsonString(Primitive) + '",'
        + '"value":"' + EscapeJsonString(Value) + '"}');
End;

{..............................................................................}
{ Gen_AttachSpiceModel - Attach an external SPICE model file (.mdl / .ckt) to  }
{ a component. Sets SpicePrefix=X (subcircuit), SpiceModel=<model_name>,       }
{ SimulationKind=Subcircuit, and adds a datafile link pointing at the file.   }
{ Params: designator, file_path, model_name (subckt name inside the file),    }
{         primitive (default "X")                                             }
{..............................................................................}

Function Gen_AttachSpiceModel(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Impl : ISch_Implementation;
    Designator, FilePath, ModelName, Primitive : String;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    FilePath := ExtractJsonValue(Params, 'file_path');
    ModelName := ExtractJsonValue(Params, 'model_name');
    Primitive := UpperCase(ExtractJsonValue(Params, 'primitive'));
    If Primitive = '' Then Primitive := 'X';

    If (Designator = '') Or (FilePath = '') Or (ModelName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'designator, file_path, and model_name are all required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Found := False;
    SchServer.ProcessControl.PreProcess(SchDoc, 'Attach SPICE model');
    Try
        Iter := SchDoc.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj := Iter.FirstSchObject;
            While (Obj <> Nil) And Not Found Do
            Begin
                Comp := Obj;
                If Comp.Designator.Text = Designator Then
                Begin
                    SetCompParamText(Comp, 'SpicePrefix', Primitive);
                    SetCompParamText(Comp, 'SpiceModel', ModelName);
                    SetCompParamText(Comp, 'SimulationKind', 'Subcircuit');
                    SetCompParamText(Comp, 'SimulationFile', FilePath);

                    { Also add a datafile link on the first implementation so   }
                    { the file is tracked as a design asset. AddDataFileLink is }
                    { a 3-arg PROCEDURE (EntityName, Location, FileKind), not a }
                    { function returning a link object.                          }
                    Impl := GetFirstSchImplementation(Comp);
                    If Impl <> Nil Then
                        Try Impl.AddDataFileLink(ModelName, FilePath, 'SimModel'); Except End;

                    Found := True;
                End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Attach SPICE model');
    End;
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component not found: ' + Designator);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator) + '",'
        + '"file_path":"' + EscapeJsonString(FilePath) + '",'
        + '"model_name":"' + EscapeJsonString(ModelName) + '"}');
End;

{..............................................................................}
{ Gen_RunSimulation - Trigger an Altium mixed-signal simulation. The analysis  }
{ type and parameters must already be configured on the project's simulation  }
{ profile, this handler just kicks the run.                                  }
{ Params: analysis (optional: operating_point | transient | ac | dc | noise | }
{         tran | etc). Currently used only for the success-response echo;    }
{         Altium picks up the active simulation profile regardless.          }
{..............................................................................}

Function Gen_RunSimulation(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    AnalysisStr : String;
Begin
    AnalysisStr := ExtractJsonValue(Params, 'analysis');

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Project := Workspace.DM_FocusedProject;
        If Project <> Nil Then SmartCompile(Project);
    End;

    ResetParameters;
    RunProcess('Sim:RunMixedSim');

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"analysis":"' + EscapeJsonString(AnalysisStr) + '",'
        + '"note":"Simulation dispatched via Sim:RunMixedSim. Altium uses the active sim profile; configure it in the Simulation Dashboard first."}');
End;

{..............................................................................}
{ Gen_BatchCreate - Generic bulk create. Each op specifies its own scope,       }
{ object_type, and properties. One shared PreProcess/PostProcess per            }
{ document touched, so N creates cost ~1x the overhead of one create.          }
{ Params: operations = 'scope=active_doc;object_type=eNetLabel;properties=Text=VCC|Location.X=100|Location.Y=200~~scope=...;object_type=...;properties=...' }
{..............................................................................}

Function Gen_BatchCreate(Params : String; RequestId : String) : String;
Var
    Operations, Remaining : String;
    OpCount, Created, Failed : Integer;
    Op, Scope, ObjTypeStr, PropsStr : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    NewObj : ISch_GraphicalObject;
    ActiveDoc : ISch_Document;
    ContainerStr : String;
    FailuresJson, ItemReason : String;
    FirstFailure : Boolean;
Begin
    Operations := ExtractJsonValue(Params, 'operations');
    If Operations = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'operations is required');
        Exit;
    End;

    Created := 0;
    Failed := 0;
    OpCount := 0;
    ActiveDoc := SchServer.GetCurrentSchDocument;
    Remaining := Operations;
    FailuresJson := '';
    FirstFailure := True;

    If ActiveDoc <> Nil Then
        SchServer.ProcessControl.PreProcess(ActiveDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            ItemReason := '';
            Scope := GetBatchField(Op, 'scope');
            If Scope = '' Then Scope := 'active_doc';
            ObjTypeStr := GetBatchField(Op, 'object_type');
            PropsStr := GetBatchField(Op, 'properties');
            ContainerStr := GetBatchField(Op, 'container');
            If ContainerStr = '' Then ContainerStr := 'document';

            ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
            If ObjTypeInt = -1 Then
            Begin
                Inc(Failed);
                ItemReason := 'INVALID_TYPE';
            End
            Else
            Begin
                NewObj := SchServer.SchObjectFactory(ObjTypeInt, eCreate_Default);
                If NewObj = Nil Then
                Begin
                    Inc(Failed);
                    ItemReason := 'CREATE_FAILED';
                End
                Else
                Begin
                    ApplySetProperties(NewObj, PropsStr);

                    If ContainerStr = 'component' Then
                    Begin
                        SchLib := SchServer.GetCurrentSchDocument;
                        If (SchLib <> Nil) And (SchLib.ObjectId = eSchLib) Then
                        Begin
                            Component := SchLib.CurrentSchComponent;
                            If Component <> Nil Then
                            Begin
                                Component.AddSchObject(NewObj);
                                SchRegisterObject(Component, NewObj);
                                Inc(Created);
                            End
                            Else
                            Begin
                                SchServer.DestroySchObject(NewObj);
                                Inc(Failed);
                                ItemReason := 'NO_COMPONENT';
                            End;
                        End
                        Else
                        Begin
                            SchServer.DestroySchObject(NewObj);
                            Inc(Failed);
                            ItemReason := 'NO_SCHLIB';
                        End;
                    End
                    Else
                    Begin
                        SchDoc := ActiveDoc;
                        If SchDoc = Nil Then
                        Begin
                            SchServer.DestroySchObject(NewObj);
                            Inc(Failed);
                            ItemReason := 'NO_SCHEMATIC';
                        End
                        Else
                        Begin
                            SchDoc.RegisterSchObjectInContainer(NewObj);
                            SchRegisterObject(SchDoc, NewObj);
                            Inc(Created);
                        End;
                    End;
                End;
            End;

            If ItemReason <> '' Then
            Begin
                If Not FirstFailure Then FailuresJson := FailuresJson + ',';
                FirstFailure := False;
                FailuresJson := FailuresJson +
                    '{"index":' + IntToStr(OpCount - 1) +
                    ',"object_type":"' + EscapeJsonString(ObjTypeStr) +
                    '","reason":"' + ItemReason + '"}';
            End;
        End;
    Finally
        If ActiveDoc <> Nil Then
        Begin
            SchServer.ProcessControl.PostProcess(ActiveDoc, '');
            ActiveDoc.GraphicallyInvalidate;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"created":' + IntToStr(Created) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) +
        ',"failures":[' + FailuresJson + ']}');
End;

{..............................................................................}
{ Gen_BatchDelete - Generic bulk delete. Each op is one scope/type/filter       }
{ delete expressed in the same format as delete_objects.                       }
{ Params: operations = 'scope=active_doc;object_type=eWire;filter=Text=old~~scope=...' }
{..............................................................................}

Function Gen_BatchDelete(Params : String; RequestId : String) : String;
Var
    Operations, Remaining : String;
    OpCount, OpsRun : Integer;
    Op, Scope, ObjTypeStr, FilterStr, ScopeType, ScopePath : String;
    ObjTypeInt : Integer;
Begin
    Operations := ExtractJsonValue(Params, 'operations');
    If Operations = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'operations is required');
        Exit;
    End;

    OpsRun := 0;
    OpCount := 0;
    Remaining := Operations;

    While True Do
    Begin
        Op := NextBatchOp(Remaining);
        If Op = '' Then Break;
        OpCount := OpCount + 1;
        Scope := GetBatchField(Op, 'scope');
        If Scope = '' Then Scope := 'active_doc';
        ObjTypeStr := GetBatchField(Op, 'object_type');
        FilterStr := GetBatchField(Op, 'filter');

        ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
        If ObjTypeInt = -1 Then Continue;

        ParseScope(Scope, ScopeType, ScopePath);
        If ScopeType = 'project' Then
            IterateProjectDocs(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, ScopePath, 0)
        Else If ScopeType = 'doc' Then
            ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0)
        Else
            ProcessActiveDoc(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0);
        Inc(OpsRun);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"operations_processed":' + IntToStr(OpsRun) + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_PlaceWires - Bulk wire placement on the active schematic.                 }
{ Params: wires = 'x1=100;y1=200;x2=300;y2=200~~x1=300;y1=200;x2=300;y2=400~~...' }
{..............................................................................}

Function Gen_PlaceWires(Params : String; RequestId : String) : String;
Var
    WireStr, Remaining : String;
    OpCount, Placed, Failed : Integer;
    X1, Y1, X2, Y2 : Integer;
    SchDoc : ISch_Document;
    Wire : ISch_Wire;
    Op : String;
Begin
    WireStr := ExtractJsonValue(Params, 'wires');
    If WireStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'wires is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := WireStr;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X1 := StrToIntDef(GetBatchField(Op, 'x1'), 0);
            Y1 := StrToIntDef(GetBatchField(Op, 'y1'), 0);
            X2 := StrToIntDef(GetBatchField(Op, 'x2'), 0);
            Y2 := StrToIntDef(GetBatchField(Op, 'y2'), 0);

            Wire := SchServer.SchObjectFactory(eWire, eCreate_Default);
            If Wire = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            { Two-vertex wire: insert vertex 1 then vertex 2 explicitly. }
            Wire.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
            Wire.InsertVertex := 1;
            Wire.SetState_Vertex(1, Point(MilsToCoord(X1), MilsToCoord(Y1)));
            Wire.InsertVertex := 2;
            Wire.SetState_Vertex(2, Point(MilsToCoord(X2), MilsToCoord(Y2)));
            { Color := 0 makes wires render BLACK and look like graphic
              lines; leave Wire.Color at the factory default so the
              schematic editor's wire colour scheme applies (blue by
              default). LineWidth=eSmall is the canonical wire weight. }
            Wire.LineWidth := eSmall;

            SchDoc.RegisterSchObjectInContainer(Wire);
            SchRegisterObject(SchDoc, Wire);
            Inc(Placed);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_PlaceSchComponentsFromLibrary - Bulk BOM placement.                       }
{ Each op: library_path, lib_reference, x, y, designator, rotation, footprint. }
{ library_path and lib_reference are required; others have sane defaults.      }
{..............................................................................}

Function Gen_PlaceSchComponentsFromLibrary(Params : String; RequestId : String) : String;
Var
    PlaceStr, Op, Remaining, FailedRefdes, ResponseBody : String;
    OpCount, Placed, Failed, Rotation, OrientationVal : Integer;
    LibPath, LibRef, Desig, Footprint, AvailHint : String;
    X, Y : Integer;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
Begin
    PlaceStr := ExtractJsonValue(Params, 'placements');
    If PlaceStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'placements is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := PlaceStr;
    FailedRefdes := '';

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            LibPath := GetBatchField(Op, 'library_path');
            LibRef := GetBatchField(Op, 'lib_reference');
            Desig := GetBatchField(Op, 'designator');
            Footprint := GetBatchField(Op, 'footprint');
            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            Rotation := StrToIntDef(GetBatchField(Op, 'rotation'), 0);

            If LibRef = '' Then
            Begin
                Inc(Failed);
                If Desig <> '' Then
                Begin
                    If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                    FailedRefdes := FailedRefdes + Desig + ':MISSING_LIB_REF';
                End;
                Continue;
            End;

            { Pre-validate to short-circuit any internal-popup path. }
            If LibPath <> '' Then
                If Not ResolveLibRef(LibPath, LibRef, AvailHint) Then
                Begin
                    Inc(Failed);
                    If Desig <> '' Then
                    Begin
                        If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                        FailedRefdes := FailedRefdes + Desig + ':RESOLVE_FAILED';
                    End;
                    Continue;
                End;

            { Working SchDoc placement API: LoadComponentFromLibrary +     }
            { AddSchObject + MoveToXY + SetState_Orientation. Note arg     }
            { order (REF, PATH) is opposite of PlaceSchComponent.           }
            Comp := Nil;
            Try
                Comp := SchServer.LoadComponentFromLibrary(LibRef, LibPath);
            Except
                Comp := Nil;
            End;
            If Comp = Nil Then
            Begin
                Inc(Failed);
                If Desig <> '' Then
                Begin
                    If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                    FailedRefdes := FailedRefdes + Desig + ':LOAD_FAILED';
                End;
                Continue;
            End;

            Try SchDoc.AddSchObject(Comp); Except End;
            Try Comp.MoveToXY(MilsToCoord(X), MilsToCoord(Y)); Except End;

            OrientationVal := 0;
            If Rotation = 90 Then OrientationVal := 1
            Else If Rotation = 180 Then OrientationVal := 2
            Else If Rotation = 270 Then OrientationVal := 3;
            Try Comp.SetState_Orientation(OrientationVal); Except End;

            If Desig <> '' Then
                Try Comp.Designator.Text := Desig; Except End;
            { Footprint override skipped: CurrentFootprintModelName is        }
            { read-only in DelphiScript (memory: delphiscript_api_quirks.md). }
            { Library symbol's own footprint is used.                          }

            SchRegisterObject(SchDoc, Comp);
            Inc(Placed);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    ResponseBody := '{"placed":' + IntToStr(Placed)
        + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount);
    If FailedRefdes <> '' Then
        ResponseBody := ResponseBody + ',"failed_refdes":"'
            + EscapeJsonString(FailedRefdes) + '"';
    ResponseBody := ResponseBody + '}';
    Result := BuildSuccessResponse(RequestId, ResponseBody);
End;

{..............................................................................}
{ Gen_PlaceNetLabels - Bulk net-label placement on the active schematic.        }
{ Params: labels = 'text=VCC;x=100;y=200;orientation=0~~text=GND;x=...'         }
{ One PreProcess/PostProcess wraps the whole batch; cuts ~1s/label of overhead. }
{..............................................................................}

Function Gen_PlaceNetLabels(Params : String; RequestId : String) : String;
Var
    LabelsStr, Op, Remaining : String;
    OpCount, Placed, Failed, Orientation, Justification : Integer;
    Text : String;
    X, Y : Integer;
    SchDoc : ISch_Document;
    NetLabel : ISch_NetLabel;
    Loc : TLocation;
    InfFont, InfColor : Integer;
    StyleFound : Boolean;
Begin
    LabelsStr := ExtractJsonValue(Params, 'labels');
    If LabelsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'labels is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := LabelsStr;

    { Sheet convention once per batch, applied to every label below. }
    StyleFound := InferNetLabelStyle(SchDoc, InfFont, InfColor);

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            Text := GetBatchField(Op, 'text');
            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            Orientation := StrToIntDef(GetBatchField(Op, 'orientation'), 0);
            { Justification 2 = bottom-right: text ENDS at the anchor so a }
            { label on a LEFT-facing pin reads to the left of the pin      }
            { while the anchor (electrical hotspot) stays on the wire.     }
            Justification := StrToIntDef(GetBatchField(Op, 'justification'), 0);

            If Text = '' Then
            Begin
                Inc(Failed);
                Continue;
            End;

            NetLabel := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
            If NetLabel = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Loc := NetLabel.Location;
            Loc.X := MilsToCoord(X);
            Loc.Y := MilsToCoord(Y);
            NetLabel.Location := Loc;
            NetLabel.Text := Text;
            NetLabel.Orientation := Orientation;
            Try NetLabel.Justification := Justification; Except End;
            If StyleFound Then
            Begin
                Try NetLabel.FontId := InfFont; Except End;
                NetLabel.Color := InfColor;
            End
            Else
                NetLabel.Color := 0;

            SchDoc.RegisterSchObjectInContainer(NetLabel);
            SchRegisterObject(SchDoc, NetLabel);
            Inc(Placed);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_PlacePowerPorts - Bulk power-port placement on the active schematic.     }
{ Params: ports = 'text=VCC;x=100;y=200;style=bar;orientation=1~~text=GND;...'  }
{..............................................................................}

Function Gen_PlacePowerPorts(Params : String; RequestId : String) : String;
Var
    PortsStr, Op, Remaining, Text, StyleStr : String;
    OpCount, Placed, Failed, OrientationVal : Integer;
    X, Y : Integer;
    SchDoc : ISch_Document;
    PowerObj : ISch_PowerObject;
    Loc : TLocation;
Begin
    PortsStr := ExtractJsonValue(Params, 'ports');
    If PortsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'ports is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := PortsStr;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            Text := GetBatchField(Op, 'text');
            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            StyleStr := GetBatchField(Op, 'style');
            OrientationVal := StrToIntDef(GetBatchField(Op, 'orientation'), -1);

            If Text = '' Then
            Begin
                Inc(Failed);
                Continue;
            End;

            PowerObj := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
            If PowerObj = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Loc := PowerObj.Location;
            Loc.X := MilsToCoord(X);
            Loc.Y := MilsToCoord(Y);
            PowerObj.Location := Loc;
            PowerObj.Text := Text;
            PowerObj.ShowNetName := True;

            If StyleStr = 'arrow' Then PowerObj.Style := ePowerArrow
            Else If StyleStr = 'bar' Then PowerObj.Style := ePowerBar
            Else If StyleStr = 'wave' Then PowerObj.Style := ePowerWave
            Else If StyleStr = 'gnd_power' Then PowerObj.Style := ePowerGndPower
            Else If StyleStr = 'gnd_signal' Then PowerObj.Style := ePowerGndSignal
            Else If StyleStr = 'gnd_earth' Then PowerObj.Style := ePowerGndEarth
            Else PowerObj.Style := ePowerCircle;

            If OrientationVal < 0 Then
            Begin
                If (StyleStr = 'gnd_power') Or (StyleStr = 'gnd_signal') Or
                   (StyleStr = 'gnd_earth') Or (StyleStr = 'bar') Or
                   (StyleStr = 'wave') Then
                    OrientationVal := 3
                Else
                    OrientationVal := 1;
            End;
            Try PowerObj.Orientation := OrientationVal; Except End;

            SchDoc.RegisterSchObjectInContainer(PowerObj);
            SchRegisterObject(SchDoc, PowerObj);
            Inc(Placed);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_GetSchDocPins - Whole-sheet pin dump in one IPC call.                    }
{ Params: sheet_path (optional, defaults to active doc).                        }
{ Returns object with "pins" array; each entry has refdes, pin_number,       }
{ pin_name, x_mils, y_mils, orientation, pin_length_mils.                    }
{..............................................................................}

Function Gen_GetSchDocPins(Params : String; RequestId : String) : String;
Var
    SheetPath, PinList, RefDes, PinNum, PinName : String;
    SchDoc : ISch_Document;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    PinX, PinY, PinOrient, PinLenMils : Integer;
    First : Boolean;
    DataBlob : String;
Begin
    SheetPath := ExtractJsonValue(Params, 'sheet_path');

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    PinList := '';
    First := True;

    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        While Comp <> Nil Do
        Begin
            RefDes := '';
            Try RefDes := Comp.Designator.Text; Except End;
            If RefDes <> '' Then
            Begin
                PinIter := Comp.SchIterator_Create;
                Try
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Pin := PinIter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                        PinNum := '';
                        PinName := '';
                        PinX := 0;
                        PinY := 0;
                        PinOrient := 0;
                        PinLenMils := 0;
                        Try PinNum := Pin.Designator; Except End;
                        Try PinName := Pin.Name; Except End;
                        Try PinX := CoordToMils(Pin.Location.X); Except End;
                        Try PinY := CoordToMils(Pin.Location.Y); Except End;
                        Try PinOrient := Pin.Orientation; Except End;
                        Try PinLenMils := CoordToMils(Pin.PinLength); Except End;

                        If Not First Then PinList := PinList + ',';
                        First := False;
                        PinList := PinList +
                            '{"refdes":"' + EscapeJsonString(RefDes) +
                            '","pin_number":"' + EscapeJsonString(PinNum) +
                            '","pin_name":"' + EscapeJsonString(PinName) +
                            '","x_mils":' + IntToStr(PinX) +
                            ',"y_mils":' + IntToStr(PinY) +
                            ',"orientation":' + IntToStr(PinOrient) +
                            ',"pin_length_mils":' + IntToStr(PinLenMils) + '}';

                        Pin := PinIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PinIter);
                End;
            End;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    DataBlob := '{"pins":[' + PinList + ']}';
    Result := BuildSuccessResponse(RequestId, DataBlob);
End;

{..............................................................................}
{ Gen_SetSchComponentsParameters - Bulk parameter stamping.                    }
{ Params: stamps = 'designator=R1;Value=10k;Manufacturer=Yageo;MPN=...;        }
{                   Footprint=0603~~designator=R2;Value=1k;Manufacturer=...'    }
{ One PreProcess/PostProcess wraps every component update; iterate the doc     }
{ ONCE and apply matching ops on the fly. Avoids 14 singular IPC round-trips. }
{..............................................................................}

Function Gen_SetSchComponentsParameters(Params : String; RequestId : String) : String;
Var
    StampsStr, SheetPath, Op, Remaining : String;
    OpCount, Updated, Failed, OpIdx : Integer;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    DesigList : TStringList;
    OpsList : TStringList;
    OpStr, FieldStr, Key, Val : String;
    P, EqPos, SemiPos : Integer;
    SrvDoc : IServerDocument;
Begin
    StampsStr := ExtractJsonValue(Params, 'stamps');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');

    If StampsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'stamps required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    { Build parallel arrays: designator -> raw op string.                     }
    DesigList := TStringList.Create;
    OpsList := TStringList.Create;
    Try
        Remaining := StampsStr;
        OpCount := 0;
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            FieldStr := GetBatchField(Op, 'designator');
            If FieldStr <> '' Then
            Begin
                DesigList.Add(FieldStr);
                OpsList.Add(Op);
            End;
        End;

        Updated := 0;
        Failed := 0;

        SchServer.ProcessControl.PreProcess(SchDoc, '');
        Try
            Iter := SchDoc.SchIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Comp := Obj;
                    OpIdx := DesigList.IndexOf(Comp.Designator.Text);
                    If OpIdx >= 0 Then
                    Begin
                        OpStr := OpsList[OpIdx];
                        { Iterate semicolon-separated key=value fields.       }
                        P := 1;
                        While P <= Length(OpStr) Do
                        Begin
                            SemiPos := P;
                            While (SemiPos <= Length(OpStr)) And
                                  (OpStr[SemiPos] <> ';') Do
                                Inc(SemiPos);
                            FieldStr := Copy(OpStr, P, SemiPos - P);
                            P := SemiPos + 1;

                            EqPos := Pos('=', FieldStr);
                            If EqPos > 0 Then
                            Begin
                                Key := Copy(FieldStr, 1, EqPos - 1);
                                Val := Copy(FieldStr, EqPos + 1,
                                    Length(FieldStr) - EqPos);

                                If (Key <> '') And (Key <> 'designator') And
                                   (Val <> '') Then
                                Begin
                                    If Key = 'Value' Then
                                    Begin
                                        SchBeginModify(Comp.Comment);
                                        Try Comp.Comment.Text := Val; Except End;
                                        SchEndModify(Comp.Comment);
                                    End
                                    Else If Key = 'Footprint' Then
                                    Begin
                                        { read-only, skip silently            }
                                    End
                                    Else
                                        SetCompParamText(Comp, Key, Val);
                                End;
                            End;
                        End;
                        Inc(Updated);
                    End;
                    Obj := Iter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iter);
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
        End;

        Failed := OpCount - Updated;
    Finally
        DesigList.Free;
        OpsList.Free;
    End;

    Try
        SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"updated":' + IntToStr(Updated) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_SetSchTextPositions - move Designator (and optionally Comment) text of   }
{ placed components to explicit sheet coordinates. The offline text-placement  }
{ pass picks a collision-free side per part; this mirrors those anchors onto   }
{ the live sheet so it matches the offline render. Coordinates are mils,       }
{ absolute sheet frame (the same frame place_sch_components uses).             }
{ Params: positions = 'designator=R1;dx=..;dy=..;vx=..;vy=..~~...'              }
{         (vx/vy optional; omitted = leave Comment where the library put it),  }
{         sheet_path (optional; falls back to the focused document).           }
{..............................................................................}

Function Gen_SetSchTextPositions(Params : String; RequestId : String) : String;
Var
    PosStr, SheetPath, Op, Remaining, FieldStr : String;
    OpCount, Updated, Failed, OpIdx : Integer;
    DX, DY, VX, VY : Integer;
    HasV : Boolean;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    DesigList, OpsList : TStringList;
    Loc : TLocation;
    SrvDoc : IServerDocument;
Begin
    PosStr := ExtractJsonValue(Params, 'positions');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    If PosStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'positions required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    DesigList := TStringList.Create;
    OpsList := TStringList.Create;
    Try
        Remaining := PosStr;
        OpCount := 0;
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            FieldStr := GetBatchField(Op, 'designator');
            If FieldStr <> '' Then
            Begin
                DesigList.Add(FieldStr);
                OpsList.Add(Op);
            End;
        End;

        Updated := 0;
        SchServer.ProcessControl.PreProcess(SchDoc, '');
        Try
            Iter := SchDoc.SchIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Comp := Obj;
                    OpIdx := DesigList.IndexOf(Comp.Designator.Text);
                    If OpIdx >= 0 Then
                    Begin
                        Op := OpsList[OpIdx];
                        DX := StrToIntDef(GetBatchField(Op, 'dx'), 0);
                        DY := StrToIntDef(GetBatchField(Op, 'dy'), 0);
                        HasV := (GetBatchField(Op, 'vx') <> '')
                            And (GetBatchField(Op, 'vy') <> '');
                        VX := StrToIntDef(GetBatchField(Op, 'vx'), 0);
                        VY := StrToIntDef(GetBatchField(Op, 'vy'), 0);

                        { Record-field write needs a materialized local. }
                        SchBeginModify(Comp.Designator);
                        Try
                            Loc := Comp.Designator.Location;
                            Loc.X := MilsToCoord(DX);
                            Loc.Y := MilsToCoord(DY);
                            Comp.Designator.Location := Loc;
                            Comp.Designator.Autoposition := False;
                        Except End;
                        SchEndModify(Comp.Designator);

                        If HasV Then
                        Begin
                            SchBeginModify(Comp.Comment);
                            Try
                                Loc := Comp.Comment.Location;
                                Loc.X := MilsToCoord(VX);
                                Loc.Y := MilsToCoord(VY);
                                Comp.Comment.Location := Loc;
                                Comp.Comment.Autoposition := False;
                            Except End;
                            SchEndModify(Comp.Comment);
                        End;
                        Inc(Updated);
                    End;
                    Obj := Iter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iter);
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
        End;
        Failed := OpCount - Updated;
    Finally
        DesigList.Free;
        OpsList.Free;
    End;

    Try
        SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"updated":' + IntToStr(Updated) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_AttachSpicePrimitivesBatch - Attach SPICE primitives to many components   }
{ in one go. Each op: designator, primitive, value, spice_model (optional),    }
{ sim_kind (optional).                                                          }
{..............................................................................}

Function Gen_AttachSpicePrimitivesBatch(Params : String; RequestId : String) : String;
Var
    AttachStr, Op, Remaining : String;
    OpCount, Attached, Failed : Integer;
    Designator, Primitive, Value, ModelName, SimKind : String;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Found : Boolean;
    FailuresJson, ItemReason : String;
    FirstFailure : Boolean;
Begin
    AttachStr := ExtractJsonValue(Params, 'attachments');
    If AttachStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'attachments is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Attached := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := AttachStr;
    FailuresJson := '';
    FirstFailure := True;

    SchServer.ProcessControl.PreProcess(SchDoc, 'Attach SPICE primitives');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            Designator := GetBatchField(Op, 'designator');
            Primitive := UpperCase(GetBatchField(Op, 'primitive'));
            Value := GetBatchField(Op, 'value');
            ModelName := GetBatchField(Op, 'spice_model');
            SimKind := GetBatchField(Op, 'sim_kind');

            ItemReason := '';

            If (Designator = '') Or (Primitive = '') Then
            Begin
                Inc(Failed);
                ItemReason := 'MISSING_FIELDS';
            End
            Else
            Begin
                Found := False;
                Iter := SchDoc.SchIterator_Create;
                Try
                    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                    Obj := Iter.FirstSchObject;
                    While (Obj <> Nil) And Not Found Do
                    Begin
                        Comp := Obj;
                        If Comp.Designator.Text = Designator Then
                        Begin
                            SetCompParamText(Comp, 'SpicePrefix', Primitive);
                            If Value <> '' Then
                                SetCompParamText(Comp, 'Value', Value);
                            If ModelName <> '' Then
                                SetCompParamText(Comp, 'SpiceModel', ModelName);
                            If SimKind <> '' Then
                                SetCompParamText(Comp, 'SimulationKind', SimKind);
                            Found := True;
                            Inc(Attached);
                        End;
                        Obj := Iter.NextSchObject;
                    End;
                Finally
                    SchDoc.SchIterator_Destroy(Iter);
                End;
                If Not Found Then
                Begin
                    Inc(Failed);
                    ItemReason := 'COMPONENT_NOT_FOUND';
                End;
            End;

            If ItemReason <> '' Then
            Begin
                If Not FirstFailure Then FailuresJson := FailuresJson + ',';
                FirstFailure := False;
                FailuresJson := FailuresJson +
                    '{"index":' + IntToStr(OpCount - 1) +
                    ',"designator":"' + EscapeJsonString(Designator) +
                    '","reason":"' + ItemReason + '"}';
            End;
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Attach SPICE primitives');
        SchDoc.GraphicallyInvalidate;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"attached":' + IntToStr(Attached) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) +
        ',"failures":[' + FailuresJson + ']}');
End;

{..............................................................................}
{ Gen_CrossRefNet - Compare the schematic vs PCB membership of a named net.   }
{                                                                               }
{ Reports the pin list the compiled SCHEMATIC assigns to `net_name` alongside  }
{ the pad list the PCB assigns to the same net, plus the diff in each         }
{ direction. An in_sync=false result means either the design hasn't been     }
{ ECO'd (Design -> Update PCB from Schematic) OR the PCB was fabricated from }
{ an earlier schematic revision and a later edit broke the merge.             }
{                                                                               }
{ This is the go-to tool when schematic connectivity surprises the user.      }
{ Params: net_name.                                                            }
{..............................................................................}

Function Gen_CrossRefNet(Params : String; RequestId : String) : String;
Var
    NetName : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    { Must be typed as IPCB_Pad, the base IPCB_Primitive doesn't expose   }
    { .Net, .Component, or .Name, and accesses fail silently under the    }
    { surrounding Try/Except, which is why every net returned 0 pads.     }
    Pad : IPCB_Pad;
    I, J, K, N, DocCount : Integer;
    UsePhysical : Boolean;
    { Diagnostic counters, emitted in the response so we can tell "PCB   }
    { is open but iterator returned nothing" from "iterator returned pads }
    { but every one had Pad.Net = nil" from "iterator ran fine and we    }
    { just got no name match".                                            }
    DiagBoardNil, DiagIterVisited, DiagPadNetNil, DiagNameRaise : Integer;
    NetReadOk : Boolean;
    { Heap-allocated lists - fixed-size `Array[0..N] Of String` as a       }
    { function local silently returns Params as the response in            }
    { DelphiScript, see [[delphiscript_fixed_string_array_bug]].           }
    SchList, PCBList : TStringList;
    SchJson, PCBJson, SchOnlyJson, PCBOnlyJson, Key : String;
    FirstS, FirstP, FirstSO, FirstPO, InPCB, InSch, InSync : Boolean;
    SchOnlyCount, PCBOnlyCount, MatchCount : Integer;
    EnvelopeData, ResponseStr : String;
Begin
    NetName := ExtractJsonValue(Params, 'net_name');
    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'net_name is required');
        Exit;
    End;

    DiagBoardNil := 0;
    DiagIterVisited := 0;
    DiagPadNetNil := 0;
    DiagNameRaise := 0;

    SchList := TStringList.Create;
    PCBList := TStringList.Create;
    Try

    { --- Schematic side: compile project, walk every component's pins,     }
    { collect "designator.pin_number" for every pin whose flattened net    }
    { name matches.                                                         }
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Project := Workspace.DM_FocusedProject;
        If Project <> Nil Then
        Begin
            SmartCompile(Project);
            GetCompiledDocs(Project, DocCount, UsePhysical);
            For I := 0 To DocCount - 1 Do
            Begin
                Doc := GetCompiledDoc(Project, I, UsePhysical);
                If Doc = Nil Then Continue;
                For J := 0 To Doc.DM_ComponentCount - 1 Do
                Begin
                    Comp := Doc.DM_Components(J);
                    If Comp = Nil Then Continue;
                    For K := 0 To Comp.DM_PinCount - 1 Do
                    Begin
                        Pin := Comp.DM_Pins(K);
                        If Pin = Nil Then Continue;
                        Try
                            If Pin.DM_FlattenedNetName = NetName Then
                                SchList.Add(Comp.DM_PhysicalDesignator + '.' + Pin.DM_PinNumber);
                        Except End;
                    End;
                End;
            End;
        End;
    End;

    { --- PCB side: iterate every pad on the board, collect the ones whose }
    { .Net matches. Each property access is in its own Try/Except and     }
    { we use nested If (not boolean And) so a nil/raising step never      }
    { drops an otherwise-valid pad, that was the bug that made pcb_pin_ }
    { count come back 0 even though pcb_get_component_pads found them.    }
    { GetCurrentPCBBoard only returns a board when a PCB tab has focus.   }
    { crossref_net is typically called while the USER is looking at a    }
    { schematic, so focus-based lookup silently returns nil, that's the }
    { source of the original pcb_pin_count:0 bug. Fall back to iterating }
    { the project's documents, find the first .PcbDoc, resolve it via   }
    { PCBServer.GetPCBBoardByPath which doesn't care about focus.         }
    { GetPCBBoardAnywhere already walks the project's PCB docs internally,    }
    { its result is the single source of truth. Older code duplicated the     }
    { iteration with a direct PCBServer.GetPCBBoardByPath call - that symbol  }
    { is undeclared on some Altium builds and Try/Except cannot catch         }
    { undeclared identifiers (see [[delphiscript_api_quirks]]), so the inline }
    { fallback would crash the script instead of just returning Nil.          }
    Board := GetPCBBoardAnywhere;

    If Board = Nil Then
        DiagBoardNil := 1
    Else
    Begin
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePadObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Pad := Iter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                DiagIterVisited := DiagIterVisited + 1;
                NetReadOk := False;
                Key := '';
                Try
                    If Pad.Net = Nil Then
                        DiagPadNetNil := DiagPadNetNil + 1
                    Else
                    Begin
                        NetReadOk := True;
                        If Pad.Net.Name = NetName Then
                            Key := '__MATCHED__';
                    End;
                Except
                    DiagNameRaise := DiagNameRaise + 1;
                End;

                If Key = '__MATCHED__' Then
                Begin
                    Key := '';
                    Try
                        If Pad.Component <> Nil Then
                            Key := Pad.Component.Name.Text + '.';
                    Except End;
                    If Key = '' Then Key := '?.';
                    Try Key := Key + Pad.Name; Except End;
                    PCBList.Add(Key);
                End;

                Pad := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    End;

    { Build JSON arrays + diff (sch_only = in sch but not pcb, etc.). O(N*M) }
    { is fine at our scale (nets with 1000+ pins are rare). PCBList.IndexOf  }
    { gives us the set membership check without a manual loop.               }
    SchJson := '';
    FirstS := True;
    For I := 0 To SchList.Count - 1 Do
    Begin
        If Not FirstS Then SchJson := SchJson + ',';
        FirstS := False;
        SchJson := SchJson + '"' + EscapeJsonString(SchList[I]) + '"';
    End;

    PCBJson := '';
    FirstP := True;
    For I := 0 To PCBList.Count - 1 Do
    Begin
        If Not FirstP Then PCBJson := PCBJson + ',';
        FirstP := False;
        PCBJson := PCBJson + '"' + EscapeJsonString(PCBList[I]) + '"';
    End;

    SchOnlyJson := '';
    FirstSO := True;
    SchOnlyCount := 0;
    For I := 0 To SchList.Count - 1 Do
    Begin
        InPCB := PCBList.IndexOf(SchList[I]) >= 0;
        If Not InPCB Then
        Begin
            If Not FirstSO Then SchOnlyJson := SchOnlyJson + ',';
            FirstSO := False;
            SchOnlyJson := SchOnlyJson + '"' + EscapeJsonString(SchList[I]) + '"';
            SchOnlyCount := SchOnlyCount + 1;
        End;
    End;

    PCBOnlyJson := '';
    FirstPO := True;
    PCBOnlyCount := 0;
    For I := 0 To PCBList.Count - 1 Do
    Begin
        InSch := SchList.IndexOf(PCBList[I]) >= 0;
        If Not InSch Then
        Begin
            If Not FirstPO Then PCBOnlyJson := PCBOnlyJson + ',';
            FirstPO := False;
            PCBOnlyJson := PCBOnlyJson + '"' + EscapeJsonString(PCBList[I]) + '"';
            PCBOnlyCount := PCBOnlyCount + 1;
        End;
    End;

    MatchCount := SchList.Count - SchOnlyCount;
    InSync := (SchOnlyCount = 0) And (PCBOnlyCount = 0) And
              ((SchList.Count > 0) Or (PCBList.Count > 0));

    EnvelopeData := '{"net_name":"' + EscapeJsonString(NetName) + '",'
        + '"sch_pin_count":' + IntToStr(SchList.Count) + ','
        + '"pcb_pin_count":' + IntToStr(PCBList.Count) + ','
        + '"matched":' + IntToStr(MatchCount) + ','
        + '"sch_only_count":' + IntToStr(SchOnlyCount) + ','
        + '"pcb_only_count":' + IntToStr(PCBOnlyCount) + ','
        + '"in_sync":' + BoolToJsonStr(InSync) + ','
        + '"sch_pins":[' + SchJson + '],'
        + '"pcb_pins":[' + PCBJson + '],'
        + '"sch_only":[' + SchOnlyJson + '],'
        + '"pcb_only":[' + PCBOnlyJson + '],'
        + '"_diag":{'
        + '"board_nil":' + IntToStr(DiagBoardNil) + ','
        + '"iter_visited":' + IntToStr(DiagIterVisited) + ','
        + '"pad_net_nil":' + IntToStr(DiagPadNetNil) + ','
        + '"name_read_raised":' + IntToStr(DiagNameRaise)
        + '}}';

    ResponseStr := BuildSuccessResponse(RequestId, EnvelopeData);
    Result := ResponseStr;
    Finally
        PCBList.Free;
        SchList.Free;
    End;
End;

{ Gen_GetSchGeometry - Walk the active SchDoc and emit every primitive's     }
{ geometry as JSON, so a Python-side renderer can produce SVG independently  }
{ of any third-party Altium parser. v1 surface:                              }
{   - components: world position, orientation, designator, lib_ref, bbox    }
{   - pins: world position, electrical-end / body direction, length         }
{   - wires: vertex polylines                                                }
{   - junctions, net labels, ports (with IOType + width), power ports       }
{                                                                              }
{ Coordinates are reported in mils. Symbol-internal primitives (rects,      }
{ lines, arcs inside each symbol) are deferred to v2 - this v1 gives the    }
{ renderer enough to draw recognizable boxed components with labelled pin   }
{ stubs, wires, junctions, ports, and labels.                                }
Function Gen_GetSchGeometry(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter, PinIter, PrimIter, ParamIter, EntryIter : ISch_Iterator;
    Obj, Prim : ISch_GraphicalObject;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    Wire : ISch_Wire;
    NetLbl : ISch_NetLabel;
    Port : ISch_Port;
    Power : ISch_PowerObject;
    Junct : ISch_Junction;
    Rect : ISch_Rectangle;
    RoundRect : ISch_RoundRectangle;
    Line : ISch_Line;
    Arc : ISch_Arc;
    EllipArc : ISch_EllipticalArc;
    Poly : ISch_Polyline;
    Ellipse : ISch_Ellipse;
    Bezier : ISch_Bezier;
    ParamObj : ISch_Parameter;
    SheetSym : ISch_SheetSymbol;
    SheetEntry : ISch_SheetEntry;
    Bus : ISch_Bus;
    CompsJson, PinsJson, WiresJson, LabelsJson, PortsJson, PowerJson, JunctsJson : String;
    SheetSymsJson, BusesJson, EntriesJson : String;
    NumComps, NumWires, NumLabels, NumPorts, NumPower, NumJuncts, NumPins : Integer;
    NumSheetSyms, NumBuses, NumEntries : Integer;
    PrimJson, PrimPart, CompHeader, ParamsJson : String;
    NumPrim, NumParams : Integer;
    Loc : TLocation;
    BBox : TCoordRect;
    HasBBox : Boolean;
    VtxN, V : Integer;
    Vert : TLocation;
    DesigText, LibRef, ElecStr, ParamName, LogicalDes : String;
    SheetName, SheetFile, SchDocName : String;
    RespJson : String;
    PhysMap : TStringList;
    Workspace : IWorkspace;
    Project : IProject;
    DmDoc : IDocument;
    DmComp : IComponent;
    DmLogical, DmPhysical : String;
    DI, DJ, MapIdx : Integer;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC',
            'No schematic document is active');
        Exit;
    End;
    SchDocName := '';
    Try SchDocName := SchDoc.DocumentName; Except End;

    { Build a logical -> physical designator map from the DM enumeration. }
    { ISch_Component.Designator.Text returns the LOGICAL designator (the }
    { sub-sheet template name like "R1"). For multi-channel designs the  }
    { board-level annotated name (R101, R201, ...) lives on the DM-side  }
    { IComponent.DM_PhysicalDesignator. Single-channel designs map 1:1   }
    { so the lookup is a no-op for them.                                  }
    PhysMap := TStringList.Create;
    Try
        Try
            Workspace := GetWorkspace;
            If Workspace <> Nil Then Project := Workspace.DM_FocusedProject;
        Except End;
        If Project <> Nil Then
        Begin
            Try
                For DI := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    Try
                        DmDoc := Project.DM_LogicalDocuments(DI);
                        If DmDoc = Nil Then Continue;
                        { Match by file name -- the DM doc and the open sch doc both }
                        { resolve to the same .SchDoc file. Only walk components of  }
                        { the doc that matches the active SchDoc so we don't pull in }
                        { other sheets' designators (multi-channel sheets share a   }
                        { logical doc but have distinct physical designators per     }
                        { channel; we want THIS sheet's mapping).                    }
                        If (DmDoc.DM_FullPath <> SchDocName)
                           And (DmDoc.DM_FileName <> SchDocName) Then Continue;
                        For DJ := 0 To DmDoc.DM_ComponentCount - 1 Do
                        Begin
                            Try
                                DmComp := DmDoc.DM_Components(DJ);
                                If DmComp = Nil Then Continue;
                                DmLogical := '';
                                DmPhysical := '';
                                Try DmLogical := DmComp.DM_LogicalDesignator; Except End;
                                Try DmPhysical := DmComp.DM_PhysicalDesignator; Except End;
                                If (DmLogical <> '') And (DmPhysical <> '')
                                   And (DmLogical <> DmPhysical) Then
                                    PhysMap.Add(DmLogical + '|' + DmPhysical);
                            Except End;
                        End;
                    Except End;
                End;
            Except End;
        End;

    CompsJson := '[';      NumComps := 0;
    PinsJson := '[';       NumPins := 0;
    WiresJson := '[';      NumWires := 0;
    LabelsJson := '[';     NumLabels := 0;
    PortsJson := '[';      NumPorts := 0;
    PowerJson := '[';      NumPower := 0;
    JunctsJson := '[';     NumJuncts := 0;
    SheetSymsJson := '['; NumSheetSyms := 0;
    BusesJson := '[';     NumBuses := 0;

    Iter := SchDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent, eWire, eNetLabel,
        ePort, ePowerObject, eJunction, eSheetSymbol, eBus));
    Try
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Loc := Comp.Location;
                { Logical designator (the sub-sheet template name like  }
                { "R1" for a multi-channel sheet) then look up the      }
                { physical / board-level name from the map we built     }
                { earlier. Single-channel designs map 1:1 so the lookup }
                { miss falls back to the logical value -- net result    }
                { matches what the BOM endpoint returns.                 }
                LogicalDes := '';
                Try LogicalDes := Comp.Designator.Text; Except End;
                DesigText := LogicalDes;
                If LogicalDes <> '' Then
                Begin
                    For MapIdx := 0 To PhysMap.Count - 1 Do
                    Begin
                        DmLogical := PhysMap[MapIdx];
                        If Copy(DmLogical, 1, Length(LogicalDes) + 1)
                           = LogicalDes + '|' Then
                        Begin
                            DesigText := Copy(DmLogical,
                                Length(LogicalDes) + 2,
                                Length(DmLogical) - Length(LogicalDes) - 1);
                            Break;
                        End;
                    End;
                End;
                LibRef := '';
                Try LibRef := Comp.LibReference; Except End;
                HasBBox := False;
                Try
                    BBox := Comp.BoundingRectangle;
                    HasBBox := True;
                Except
                End;
                If Not HasBBox Then
                Begin
                    { Fallback: a small box at the component origin so the    }
                    { renderer still has something to draw if the API throws. }
                    BBox.X1 := Loc.X - MilsToCoord(100);
                    BBox.Y1 := Loc.Y - MilsToCoord(100);
                    BBox.X2 := Loc.X + MilsToCoord(100);
                    BBox.Y2 := Loc.Y + MilsToCoord(100);
                End;
                { Component header without primitives. The full record gets   }
                { written after the primitives are collected, since v2 nests  }
                { the symbol art inside the component object.                  }
                CompHeader :=
                    '{"des":"' + EscapeJsonString(DesigText) + '"' +
                    ',"lib_ref":"' + EscapeJsonString(LibRef) + '"' +
                    ',"x":' + IntToStr(CoordToMils(Loc.X)) +
                    ',"y":' + IntToStr(CoordToMils(Loc.Y)) +
                    ',"rot":' + IntToStr(Comp.Orientation * 90) +
                    ',"mirror":' + BoolToJsonStr(Comp.IsMirrored) +
                    ',"bbox":{"x1":' + IntToStr(CoordToMils(BBox.X1)) +
                    ',"y1":' + IntToStr(CoordToMils(BBox.Y1)) +
                    ',"x2":' + IntToStr(CoordToMils(BBox.X2)) +
                    ',"y2":' + IntToStr(CoordToMils(BBox.Y2)) + '}';

                { Symbol-internal primitives -- iterate the component's own   }
                { children for rect / line / arc / polyline / polygon /       }
                { ellipse so the renderer draws actual symbol art rather than }
                { a labelled bounding box. Colors come back as Altium's       }
                { BGR-packed integers; the Python side maps them to SVG.      }
                PrimJson := '[';
                NumPrim := 0;
                PrimIter := Comp.SchIterator_Create;
                PrimIter.AddFilter_ObjectSet(MkSet(eRectangle, eRoundRectangle,
                    eLine, eArc, eEllipticalArc, ePolyline, ePolygon, eEllipse,
                    eBezier));
                Try
                    Prim := PrimIter.FirstSchObject;
                    While Prim <> Nil Do
                    Begin
                        PrimPart := '';
                        If Prim.ObjectId = eRectangle Then
                        Begin
                            Rect := Prim;
                            PrimPart := '{"kind":"rect"'
                                + ',"x1":' + IntToStr(CoordToMils(Rect.Location.X))
                                + ',"y1":' + IntToStr(CoordToMils(Rect.Location.Y))
                                + ',"x2":' + IntToStr(CoordToMils(Rect.Corner.X))
                                + ',"y2":' + IntToStr(CoordToMils(Rect.Corner.Y))
                                + ',"color":' + IntToStr(Rect.Color)
                                + ',"line_width":' + IntToStr(Rect.LineWidth)
                                + ',"area_color":' + IntToStr(Rect.AreaColor)
                                + ',"is_solid":' + BoolToJsonStr(Rect.IsSolid)
                                + '}';
                        End
                        Else If Prim.ObjectId = eRoundRectangle Then
                        Begin
                            RoundRect := Prim;
                            PrimPart := '{"kind":"roundrect"'
                                + ',"x1":' + IntToStr(CoordToMils(RoundRect.Location.X))
                                + ',"y1":' + IntToStr(CoordToMils(RoundRect.Location.Y))
                                + ',"x2":' + IntToStr(CoordToMils(RoundRect.Corner.X))
                                + ',"y2":' + IntToStr(CoordToMils(RoundRect.Corner.Y))
                                + ',"rx":' + IntToStr(CoordToMils(RoundRect.CornerXRadius))
                                + ',"ry":' + IntToStr(CoordToMils(RoundRect.CornerYRadius))
                                + ',"color":' + IntToStr(RoundRect.Color)
                                + ',"line_width":' + IntToStr(RoundRect.LineWidth)
                                + ',"area_color":' + IntToStr(RoundRect.AreaColor)
                                + ',"is_solid":' + BoolToJsonStr(RoundRect.IsSolid)
                                + '}';
                        End
                        Else If Prim.ObjectId = eLine Then
                        Begin
                            Line := Prim;
                            PrimPart := '{"kind":"line"'
                                + ',"x1":' + IntToStr(CoordToMils(Line.Location.X))
                                + ',"y1":' + IntToStr(CoordToMils(Line.Location.Y))
                                + ',"x2":' + IntToStr(CoordToMils(Line.Corner.X))
                                + ',"y2":' + IntToStr(CoordToMils(Line.Corner.Y))
                                + ',"color":' + IntToStr(Line.Color)
                                + ',"line_width":' + IntToStr(Line.LineWidth)
                                + '}';
                        End
                        Else If Prim.ObjectId = eArc Then
                        Begin
                            Arc := Prim;
                            PrimPart := '{"kind":"arc"'
                                + ',"cx":' + IntToStr(CoordToMils(Arc.Location.X))
                                + ',"cy":' + IntToStr(CoordToMils(Arc.Location.Y))
                                + ',"r":' + IntToStr(CoordToMils(Arc.Radius))
                                + ',"start":' + FloatToJsonStr(Arc.StartAngle)
                                + ',"end":' + FloatToJsonStr(Arc.EndAngle)
                                + ',"color":' + IntToStr(Arc.Color)
                                + ',"line_width":' + IntToStr(Arc.LineWidth)
                                + '}';
                        End
                        Else If Prim.ObjectId = eEllipticalArc Then
                        Begin
                            EllipArc := Prim;
                            PrimPart := '{"kind":"arc"'
                                + ',"cx":' + IntToStr(CoordToMils(EllipArc.Location.X))
                                + ',"cy":' + IntToStr(CoordToMils(EllipArc.Location.Y))
                                + ',"r":' + IntToStr(CoordToMils(EllipArc.Radius))
                                + ',"r2":' + IntToStr(CoordToMils(EllipArc.SecondaryRadius))
                                + ',"start":' + FloatToJsonStr(EllipArc.StartAngle)
                                + ',"end":' + FloatToJsonStr(EllipArc.EndAngle)
                                + ',"color":' + IntToStr(EllipArc.Color)
                                + ',"line_width":' + IntToStr(EllipArc.LineWidth)
                                + '}';
                        End
                        Else If (Prim.ObjectId = ePolyline) Or (Prim.ObjectId = ePolygon) Then
                        Begin
                            Poly := Prim;
                            VtxN := 0;
                            Try VtxN := Poly.GetState_VerticesCount; Except End;
                            If VtxN >= 2 Then
                            Begin
                                If Prim.ObjectId = ePolyline Then
                                    PrimPart := '{"kind":"polyline","pts":['
                                Else
                                    PrimPart := '{"kind":"polygon","pts":[';
                                For V := 1 To VtxN Do
                                Begin
                                    Vert := Poly.GetState_Vertex(V);
                                    If V > 1 Then PrimPart := PrimPart + ',';
                                    PrimPart := PrimPart + '['
                                        + IntToStr(CoordToMils(Vert.X)) + ','
                                        + IntToStr(CoordToMils(Vert.Y)) + ']';
                                End;
                                PrimPart := PrimPart + ']'
                                    + ',"color":' + IntToStr(Poly.Color)
                                    + ',"line_width":' + IntToStr(Poly.LineWidth)
                                    + ',"area_color":' + IntToStr(Poly.AreaColor)
                                    + ',"is_solid":' + BoolToJsonStr(Poly.IsSolid)
                                    + '}';
                            End;
                        End
                        Else If Prim.ObjectId = eEllipse Then
                        Begin
                            Ellipse := Prim;
                            PrimPart := '{"kind":"ellipse"'
                                + ',"cx":' + IntToStr(CoordToMils(Ellipse.Location.X))
                                + ',"cy":' + IntToStr(CoordToMils(Ellipse.Location.Y))
                                + ',"rx":' + IntToStr(CoordToMils(Ellipse.Radius))
                                + ',"ry":' + IntToStr(CoordToMils(Ellipse.SecondaryRadius))
                                + ',"color":' + IntToStr(Ellipse.Color)
                                + ',"line_width":' + IntToStr(Ellipse.LineWidth)
                                + ',"area_color":' + IntToStr(Ellipse.AreaColor)
                                + ',"is_solid":' + BoolToJsonStr(Ellipse.IsSolid)
                                + '}';
                        End
                        Else If Prim.ObjectId = eBezier Then
                        Begin
                            Bezier := Prim;
                            VtxN := 0;
                            Try VtxN := Bezier.GetState_VerticesCount; Except End;
                            If VtxN >= 4 Then
                            Begin
                                PrimPart := '{"kind":"bezier","pts":[';
                                For V := 1 To VtxN Do
                                Begin
                                    Vert := Bezier.GetState_Vertex(V);
                                    If V > 1 Then PrimPart := PrimPart + ',';
                                    PrimPart := PrimPart + '['
                                        + IntToStr(CoordToMils(Vert.X)) + ','
                                        + IntToStr(CoordToMils(Vert.Y)) + ']';
                                End;
                                PrimPart := PrimPart + ']'
                                    + ',"color":' + IntToStr(Bezier.Color)
                                    + ',"line_width":' + IntToStr(Bezier.LineWidth)
                                    + '}';
                            End;
                        End;
                        If PrimPart <> '' Then
                        Begin
                            If NumPrim > 0 Then PrimJson := PrimJson + ',';
                            PrimJson := PrimJson + PrimPart;
                            Inc(NumPrim);
                        End;
                        Prim := PrimIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PrimIter);
                End;
                PrimJson := PrimJson + ']';

                { Symbol-internal parameter text -- the visible labels   }
                { living inside the symbol (other than the special       }
                { Designator / Comment, which the top-level handles).    }
                { Hidden parameters and the two specials are skipped.    }
                ParamsJson := '[';
                NumParams := 0;
                ParamIter := Comp.SchIterator_Create;
                ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                Try
                    ParamObj := ParamIter.FirstSchObject;
                    While ParamObj <> Nil Do
                    Begin
                        ParamName := '';
                        Try ParamName := ParamObj.Name; Except End;
                        If (Not ParamObj.IsHidden)
                                And (ParamName <> 'Designator')
                                And (ParamName <> 'Comment') Then
                        Begin
                            If NumParams > 0 Then ParamsJson := ParamsJson + ',';
                            ParamsJson := ParamsJson +
                                '{"name":"' + EscapeJsonString(ParamName) + '"' +
                                ',"text":"' + EscapeJsonString(ParamObj.Text) + '"' +
                                ',"x":' + IntToStr(CoordToMils(ParamObj.Location.X)) +
                                ',"y":' + IntToStr(CoordToMils(ParamObj.Location.Y)) +
                                ',"rot":' + IntToStr(ParamObj.Orientation * 90) +
                                ',"color":' + IntToStr(ParamObj.Color) +
                                ',"font_id":' + IntToStr(ParamObj.FontId) + '}';
                            Inc(NumParams);
                        End;
                        ParamObj := ParamIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(ParamIter);
                End;
                ParamsJson := ParamsJson + ']';

                If NumComps > 0 Then CompsJson := CompsJson + ',';
                CompsJson := CompsJson + CompHeader
                    + ',"primitives":' + PrimJson
                    + ',"params":' + ParamsJson + '}';
                Inc(NumComps);

                PinIter := Comp.SchIterator_Create;
                PinIter.AddFilter_ObjectSet(MkSet(ePin));
                Try
                    Pin := PinIter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                        Loc := Pin.Location;
                        { Electrical type as a string so the renderer can map }
                        { straight to a glyph (input arrow / OC bubble / ...). }
                        ElecStr := 'passive';
                        Try ElecStr := PinElectricalToStr(Pin.Electrical); Except End;
                        If NumPins > 0 Then PinsJson := PinsJson + ',';
                        PinsJson := PinsJson +
                            '{"comp":"' + EscapeJsonString(DesigText) + '"' +
                            ',"des":"' + EscapeJsonString(Pin.Designator) + '"' +
                            ',"name":"' + EscapeJsonString(Pin.Name) + '"' +
                            ',"x":' + IntToStr(CoordToMils(Loc.X)) +
                            ',"y":' + IntToStr(CoordToMils(Loc.Y)) +
                            ',"rot":' + IntToStr(Pin.Orientation * 90) +
                            ',"len":' + IntToStr(CoordToMils(Pin.PinLength)) +
                            ',"electrical":"' + EscapeJsonString(ElecStr) + '"}';
                        Inc(NumPins);
                        Pin := PinIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PinIter);
                End;
            End
            Else If Obj.ObjectId = eWire Then
            Begin
                Wire := Obj;
                VtxN := 0;
                Try VtxN := Wire.GetState_VerticesCount; Except End;
                If VtxN >= 2 Then
                Begin
                    If NumWires > 0 Then WiresJson := WiresJson + ',';
                    WiresJson := WiresJson + '{"verts":[';
                    For V := 1 To VtxN Do
                    Begin
                        Vert := Wire.GetState_Vertex(V);
                        If V > 1 Then WiresJson := WiresJson + ',';
                        WiresJson := WiresJson + '['
                            + IntToStr(CoordToMils(Vert.X)) + ','
                            + IntToStr(CoordToMils(Vert.Y)) + ']';
                    End;
                    WiresJson := WiresJson + ']}';
                    Inc(NumWires);
                End;
            End
            Else If Obj.ObjectId = eNetLabel Then
            Begin
                NetLbl := Obj;
                Loc := NetLbl.Location;
                If NumLabels > 0 Then LabelsJson := LabelsJson + ',';
                LabelsJson := LabelsJson +
                    '{"text":"' + EscapeJsonString(NetLbl.Text) + '"' +
                    ',"x":' + IntToStr(CoordToMils(Loc.X)) +
                    ',"y":' + IntToStr(CoordToMils(Loc.Y)) +
                    ',"rot":' + IntToStr(NetLbl.Orientation * 90) + '}';
                Inc(NumLabels);
            End
            Else If Obj.ObjectId = ePort Then
            Begin
                Port := Obj;
                Loc := Port.Location;
                If NumPorts > 0 Then PortsJson := PortsJson + ',';
                PortsJson := PortsJson +
                    '{"text":"' + EscapeJsonString(Port.Name) + '"' +
                    ',"x":' + IntToStr(CoordToMils(Loc.X)) +
                    ',"y":' + IntToStr(CoordToMils(Loc.Y)) +
                    ',"w":' + IntToStr(CoordToMils(Port.Width)) +
                    ',"iotype":' + IntToStr(Port.IOType) + '}';
                Inc(NumPorts);
            End
            Else If Obj.ObjectId = ePowerObject Then
            Begin
                Power := Obj;
                Loc := Power.Location;
                If NumPower > 0 Then PowerJson := PowerJson + ',';
                PowerJson := PowerJson +
                    '{"text":"' + EscapeJsonString(Power.Text) + '"' +
                    ',"x":' + IntToStr(CoordToMils(Loc.X)) +
                    ',"y":' + IntToStr(CoordToMils(Loc.Y)) +
                    ',"style":' + IntToStr(Power.Style) +
                    ',"rot":' + IntToStr(Power.Orientation * 90) + '}';
                Inc(NumPower);
            End
            Else If Obj.ObjectId = eJunction Then
            Begin
                Junct := Obj;
                Loc := Junct.Location;
                If NumJuncts > 0 Then JunctsJson := JunctsJson + ',';
                JunctsJson := JunctsJson +
                    '{"x":' + IntToStr(CoordToMils(Loc.X)) +
                    ',"y":' + IntToStr(CoordToMils(Loc.Y)) + '}';
                Inc(NumJuncts);
            End
            Else If Obj.ObjectId = eSheetSymbol Then
            Begin
                { Hierarchical sheet symbols are the labelled boxes on the   }
                { top sheet that represent sub-sheets. Their interior is     }
                { otherwise empty so without rendering them a top-of-design  }
                { sheet renders as blank space. We also walk their child     }
                { eSheetEntry terminals so the renderer can draw the IO     }
                { stubs at the right edges.                                   }
                SheetSym := Obj;
                Loc := SheetSym.Location;
                SheetName := '';
                Try If SheetSym.SheetName <> Nil Then SheetName := SheetSym.SheetName.Text; Except End;
                SheetFile := '';
                Try If SheetSym.SheetFileName <> Nil Then SheetFile := SheetSym.SheetFileName.Text; Except End;

                EntriesJson := '[';
                NumEntries := 0;
                EntryIter := SheetSym.SchIterator_Create;
                EntryIter.AddFilter_ObjectSet(MkSet(eSheetEntry));
                Try
                    SheetEntry := EntryIter.FirstSchObject;
                    While SheetEntry <> Nil Do
                    Begin
                        If NumEntries > 0 Then EntriesJson := EntriesJson + ',';
                        EntriesJson := EntriesJson +
                            '{"name":"' + EscapeJsonString(SheetEntry.Name) + '"' +
                            ',"x":' + IntToStr(CoordToMils(SheetEntry.Location.X)) +
                            ',"y":' + IntToStr(CoordToMils(SheetEntry.Location.Y)) +
                            ',"iotype":' + IntToStr(SheetEntry.IOType) +
                            ',"side":' + IntToStr(SheetEntry.Side) + '}';
                        Inc(NumEntries);
                        SheetEntry := EntryIter.NextSchObject;
                    End;
                Finally
                    SheetSym.SchIterator_Destroy(EntryIter);
                End;
                EntriesJson := EntriesJson + ']';

                If NumSheetSyms > 0 Then SheetSymsJson := SheetSymsJson + ',';
                SheetSymsJson := SheetSymsJson +
                    '{"name":"' + EscapeJsonString(SheetName) + '"' +
                    ',"filename":"' + EscapeJsonString(SheetFile) + '"' +
                    ',"x":' + IntToStr(CoordToMils(Loc.X)) +
                    ',"y":' + IntToStr(CoordToMils(Loc.Y)) +
                    ',"w":' + IntToStr(CoordToMils(SheetSym.XSize)) +
                    ',"h":' + IntToStr(CoordToMils(SheetSym.YSize)) +
                    ',"color":' + IntToStr(SheetSym.Color) +
                    ',"area_color":' + IntToStr(SheetSym.AreaColor) +
                    ',"entries":' + EntriesJson + '}';
                Inc(NumSheetSyms);
            End
            Else If Obj.ObjectId = eBus Then
            Begin
                { Buses share their vertex API with wires (ISch_Polyline      }
                { children). Emitted separately so the renderer can draw     }
                { them thicker / in a distinct colour.                        }
                Bus := Obj;
                VtxN := 0;
                Try VtxN := Bus.GetState_VerticesCount; Except End;
                If VtxN >= 2 Then
                Begin
                    If NumBuses > 0 Then BusesJson := BusesJson + ',';
                    BusesJson := BusesJson + '{"verts":[';
                    For V := 1 To VtxN Do
                    Begin
                        Vert := Bus.GetState_Vertex(V);
                        If V > 1 Then BusesJson := BusesJson + ',';
                        BusesJson := BusesJson + '['
                            + IntToStr(CoordToMils(Vert.X)) + ','
                            + IntToStr(CoordToMils(Vert.Y)) + ']';
                    End;
                    BusesJson := BusesJson + ']}';
                    Inc(NumBuses);
                End;
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    CompsJson    := CompsJson    + ']';
    PinsJson     := PinsJson     + ']';
    WiresJson    := WiresJson    + ']';
    LabelsJson   := LabelsJson   + ']';
    PortsJson    := PortsJson    + ']';
    PowerJson    := PowerJson    + ']';
    JunctsJson   := JunctsJson   + ']';
    SheetSymsJson := SheetSymsJson + ']';
    BusesJson    := BusesJson    + ']';

    RespJson :=
        '{"doc":"' + EscapeJsonString(SchDoc.DocumentName) + '"' +
        ',"counts":{"components":' + IntToStr(NumComps) +
        ',"pins":' + IntToStr(NumPins) +
        ',"wires":' + IntToStr(NumWires) +
        ',"net_labels":' + IntToStr(NumLabels) +
        ',"ports":' + IntToStr(NumPorts) +
        ',"power_ports":' + IntToStr(NumPower) +
        ',"junctions":' + IntToStr(NumJuncts) +
        ',"sheet_symbols":' + IntToStr(NumSheetSyms) +
        ',"buses":' + IntToStr(NumBuses) + '}' +
        ',"components":' + CompsJson +
        ',"pins":' + PinsJson +
        ',"wires":' + WiresJson +
        ',"net_labels":' + LabelsJson +
        ',"ports":' + PortsJson +
        ',"power_ports":' + PowerJson +
        ',"junctions":' + JunctsJson +
        ',"sheet_symbols":' + SheetSymsJson +
        ',"buses":' + BusesJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
    Finally
        Try PhysMap.Free; Except End;
    End;
End;

{ Gen_GetPcbGeometry - Walk the active PcbDoc and emit every primitive's    }
{ geometry as JSON so a Python-side renderer can produce per-layer SVG     }
{ independently of any third-party Altium parser.                          }
{                                                                              }
{ v1 surface:                                                                  }
{   - board outline (segments as a line/arc polyline)                         }
{   - tracks (X1/Y1/X2/Y2, width, layer, net)                                 }
{   - arcs (center, radius, start/end angle, width, layer, net)               }
{   - pads (location, shape, x_size, y_size, rotation, hole_size, layer, net) }
{   - vias (location, size, hole, high/low layer, net)                        }
{   - texts (location, text, size, width, rotation, layer)                    }
{                                                                              }
{ Coordinates in mils. Layer names come back as Altium's GetLayerString      }
{ form so the renderer can z-order, colour, and toggle by name.              }
{ Deferred to v2: regions / polygon fills, component bodies, drill drawing. }
Function Gen_GetPcbGeometry(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Pad : IPCB_Pad;
    Via : IPCB_Via;
    Text : IPCB_Text;
    Region : IPCB_Region;
    Contour : IPCB_Contour;
    CompObj : IPCB_Component;
    Outline : IPCB_BoardOutline;
    OutlineJson, TracksJson, ArcsJson, PadsJson, ViasJson, TextsJson : String;
    RegionsJson, CompsJson : String;
    NumTracks, NumArcs, NumPads, NumVias, NumTexts, NumOutline : Integer;
    NumRegions, NumComps : Integer;
    LayerName, ShapeStr, NetName, TextStr, PadName, HoleStr : String;
    BR : TCoordRect;
    I, K, PtCount : Integer;
    Seg : TPolySegment;
    RespJson : String;
    NameOnFlag, CommentOnFlag, IsHiddenFlag : Boolean;
    PCBSysOpts : IPCB_SystemOptions;
    Lyr : TLayer;
    LayersJson, LyrNm : String;
    LyrColor : Integer;
    LyrVisible, LyrFirst : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB',
            'No PCB document is active');
        Exit;
    End;

    { Board outline: walk Segments. Each carries a vertex (vx, vy) and -- }
    { for arc segments -- a center (cx, cy) + radius + angles. v1 keeps   }
    { both kinds; the renderer can draw arc segments as proper arcs or    }
    { fall back to chord lines.                                             }
    OutlineJson := '['; NumOutline := 0;
    Outline := Board.BoardOutline;
    If Outline <> Nil Then
    Begin
        Try Outline.Invalidate; Outline.Rebuild; Outline.Validate; Except End;
        For I := 0 To Outline.PointCount - 1 Do
        Begin
            Seg := Outline.Segments[I];
            If NumOutline > 0 Then OutlineJson := OutlineJson + ',';
            If Seg.Kind = ePolySegmentLine Then
            Begin
                OutlineJson := OutlineJson +
                    '{"kind":"line"' +
                    ',"x":' + IntToStr(CoordToMils(Seg.vx)) +
                    ',"y":' + IntToStr(CoordToMils(Seg.vy)) + '}';
            End
            Else
            Begin
                OutlineJson := OutlineJson +
                    '{"kind":"arc"' +
                    ',"x":' + IntToStr(CoordToMils(Seg.vx)) +
                    ',"y":' + IntToStr(CoordToMils(Seg.vy)) +
                    ',"cx":' + IntToStr(CoordToMils(Seg.cx)) +
                    ',"cy":' + IntToStr(CoordToMils(Seg.cy)) +
                    ',"angle1":' + FloatToJsonStr(Seg.Angle1) +
                    ',"angle2":' + FloatToJsonStr(Seg.Angle2) +
                    ',"radius":' + IntToStr(CoordToMils(Seg.Radius)) + '}';
            End;
            Inc(NumOutline);
        End;
    End;
    OutlineJson := OutlineJson + ']';

    BR := Board.BoardOutline.BoundingRectangle;

    TracksJson := '['; NumTracks := 0;
    ArcsJson := '[';   NumArcs := 0;
    PadsJson := '[';   NumPads := 0;
    ViasJson := '[';   NumVias := 0;
    TextsJson := '[';  NumTexts := 0;
    RegionsJson := '['; NumRegions := 0;
    CompsJson := '[';   NumComps := 0;

    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, ePadObject,
        eViaObject, eTextObject, eRegionObject, eComponentObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Try
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            LayerName := '';
            Try LayerName := GetLayerString(Obj.Layer); Except End;

            If Obj.ObjectId = eTrackObject Then
            Begin
                Track := Obj;
                NetName := '';
                Try If Track.Net <> Nil Then NetName := Track.Net.Name; Except End;
                If NumTracks > 0 Then TracksJson := TracksJson + ',';
                TracksJson := TracksJson +
                    '{"x1":' + IntToStr(CoordToMils(Track.X1)) +
                    ',"y1":' + IntToStr(CoordToMils(Track.Y1)) +
                    ',"x2":' + IntToStr(CoordToMils(Track.X2)) +
                    ',"y2":' + IntToStr(CoordToMils(Track.Y2)) +
                    ',"width":' + IntToStr(CoordToMils(Track.Width)) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumTracks);
            End
            Else If Obj.ObjectId = eArcObject Then
            Begin
                Arc := Obj;
                NetName := '';
                Try If Arc.Net <> Nil Then NetName := Arc.Net.Name; Except End;
                If NumArcs > 0 Then ArcsJson := ArcsJson + ',';
                ArcsJson := ArcsJson +
                    '{"cx":' + IntToStr(CoordToMils(Arc.XCenter)) +
                    ',"cy":' + IntToStr(CoordToMils(Arc.YCenter)) +
                    ',"r":' + IntToStr(CoordToMils(Arc.Radius)) +
                    ',"start":' + FloatToJsonStr(Arc.StartAngle) +
                    ',"end":' + FloatToJsonStr(Arc.EndAngle) +
                    ',"width":' + IntToStr(CoordToMils(Arc.LineWidth)) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumArcs);
            End
            Else If Obj.ObjectId = ePadObject Then
            Begin
                Pad := Obj;
                ShapeStr := 'Round';
                Try
                    If Pad.TopShape = eRounded Then ShapeStr := 'Round'
                    Else If Pad.TopShape = eRectangular Then ShapeStr := 'Rectangular'
                    Else If Pad.TopShape = eOctagonal Then ShapeStr := 'Octagonal'
                    Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'RoundedRect';
                Except End;
                { Drill shape: round / square / slot. Slot pads expose a   }
                { separate hole_width and a hole_rotation; round + square }
                { reuse hole_size for the width/diameter.                  }
                HoleStr := 'Round';
                Try
                    If Pad.HoleType = eSquareHole Then HoleStr := 'Square'
                    Else If Pad.HoleType = eSlotHole Then HoleStr := 'Slot';
                Except End;
                NetName := '';
                Try If Pad.Net <> Nil Then NetName := Pad.Net.Name; Except End;
                PadName := '';
                Try PadName := Pad.Name; Except End;
                { Owning component's designator -- empty for free pads     }
                { (fiducials, mounting holes, board-level pads). Lets the  }
                { renderer attach data-designator so a pad click on the    }
                { PCB SVG can open the parent component's drawer.          }
                TextStr := '';
                Try
                    If Pad.Component <> Nil Then TextStr := Pad.Component.Name.Text;
                Except End;
                If NumPads > 0 Then PadsJson := PadsJson + ',';
                PadsJson := PadsJson +
                    '{"x":' + IntToStr(CoordToMils(Pad.X)) +
                    ',"y":' + IntToStr(CoordToMils(Pad.Y)) +
                    ',"x_size":' + IntToStr(CoordToMils(Pad.TopXSize)) +
                    ',"y_size":' + IntToStr(CoordToMils(Pad.TopYSize)) +
                    ',"shape":"' + EscapeJsonString(ShapeStr) + '"' +
                    ',"hole_size":' + IntToStr(CoordToMils(Pad.HoleSize)) +
                    ',"hole_type":"' + EscapeJsonString(HoleStr) + '"' +
                    ',"hole_width":' + IntToStr(CoordToMils(Pad.HoleWidth)) +
                    ',"hole_rotation":' + FloatToJsonStr(Pad.HoleRotation) +
                    ',"rotation":' + FloatToJsonStr(Pad.Rotation) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"name":"' + EscapeJsonString(PadName) + '"' +
                    ',"comp":"' + EscapeJsonString(TextStr) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumPads);
            End
            Else If Obj.ObjectId = eViaObject Then
            Begin
                Via := Obj;
                NetName := '';
                Try If Via.Net <> Nil Then NetName := Via.Net.Name; Except End;
                If NumVias > 0 Then ViasJson := ViasJson + ',';
                ViasJson := ViasJson +
                    '{"x":' + IntToStr(CoordToMils(Via.X)) +
                    ',"y":' + IntToStr(CoordToMils(Via.Y)) +
                    ',"size":' + IntToStr(CoordToMils(Via.Size)) +
                    ',"hole_size":' + IntToStr(CoordToMils(Via.HoleSize)) +
                    ',"high_layer":"' + EscapeJsonString(GetLayerString(Via.HighLayer)) + '"' +
                    ',"low_layer":"' + EscapeJsonString(GetLayerString(Via.LowLayer)) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumVias);
            End
            Else If Obj.ObjectId = eTextObject Then
            Begin
                Text := Obj;
                { Skip hidden text entirely -- footprints from vendor      }
                { libraries typically carry 5-10 hidden text objects each }
                { (.Designator / .Comment / .ChannelDesignator on multiple}
                { mech / solder / paste layers). On a 100-component board }
                { that's 500-1000 IPC-irrelevant primitives. Skipping at  }
                { the Pascal side shrinks the JSON payload + client parse }
                { without losing anything the renderer would render.      }
                IsHiddenFlag := False;
                Try IsHiddenFlag := Text.IsHidden; Except End;
                If IsHiddenFlag Then
                Begin
                    Obj := Iter.NextPCBObject;
                    Continue;
                End;
                TextStr := '';
                Try TextStr := Text.Text; Except End;
                If TextStr = '' Then Try TextStr := Text.UnderlyingString; Except End;
                If TextStr = '' Then
                Begin
                    Obj := Iter.NextPCBObject;
                    Continue;
                End;
                If NumTexts > 0 Then TextsJson := TextsJson + ',';
                TextsJson := TextsJson +
                    '{"x":' + IntToStr(CoordToMils(Text.XLocation)) +
                    ',"y":' + IntToStr(CoordToMils(Text.YLocation)) +
                    ',"text":"' + EscapeJsonString(TextStr) + '"' +
                    ',"size":' + IntToStr(CoordToMils(Text.Size)) +
                    ',"width":' + IntToStr(CoordToMils(Text.Width)) +
                    ',"rotation":' + FloatToJsonStr(Text.Rotation) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"hidden":false}';
                Inc(NumTexts);
            End
            Else If Obj.ObjectId = eRegionObject Then
            Begin
                { Regions are the actual poured copper on a board -- the   }
                { biggest visual gap a tracks-only render leaves behind.   }
                { MainContour is 1-based and indexes X[i] / Y[i] arrays.   }
                Region := Obj;
                NetName := '';
                Try If Region.Net <> Nil Then NetName := Region.Net.Name; Except End;
                Contour := Nil;
                PtCount := 0;
                Try
                    Contour := Region.MainContour;
                    If Contour <> Nil Then PtCount := Contour.Count;
                Except
                End;
                If (Contour <> Nil) And (PtCount >= 3) Then
                Begin
                    If NumRegions > 0 Then RegionsJson := RegionsJson + ',';
                    RegionsJson := RegionsJson +
                        '{"layer":"' + EscapeJsonString(LayerName) + '"' +
                        ',"net":"' + EscapeJsonString(NetName) + '"' +
                        ',"pts":[';
                    For K := 1 To PtCount Do
                    Begin
                        If K > 1 Then RegionsJson := RegionsJson + ',';
                        RegionsJson := RegionsJson + '['
                            + IntToStr(CoordToMils(Contour.X[K])) + ','
                            + IntToStr(CoordToMils(Contour.Y[K])) + ']';
                    End;
                    RegionsJson := RegionsJson + ']}';
                    Inc(NumRegions);
                End;
            End
            Else If Obj.ObjectId = eComponentObject Then
            Begin
                { Component identity -- mostly so the renderer can place a }
                { designator label next to each footprint on a virtual    }
                { "Designators" pseudo-layer. The actual silkscreen art    }
                { for each footprint is already emitted via its child     }
                { tracks / arcs / texts on the *Overlay layers.            }
                CompObj := Obj;
                TextStr := '';
                Try TextStr := CompObj.Name.Text; Except End;
                If TextStr = '' Then Try TextStr := CompObj.SourceDesignator; Except End;
                { NameOn / CommentOn drive whether the designator + comment   }
                { strings are visible in Altium itself. The renderer respects }
                { these flags so designs that explicitly hide labels render  }
                { the same in the dashboard.                                 }
                NameOnFlag := True;
                Try NameOnFlag := CompObj.NameOn; Except End;
                CommentOnFlag := True;
                Try CommentOnFlag := CompObj.CommentOn; Except End;
                If NumComps > 0 Then CompsJson := CompsJson + ',';
                CompsJson := CompsJson +
                    '{"des":"' + EscapeJsonString(TextStr) + '"' +
                    ',"x":' + IntToStr(CoordToMils(CompObj.X)) +
                    ',"y":' + IntToStr(CoordToMils(CompObj.Y)) +
                    ',"rotation":' + FloatToJsonStr(CompObj.Rotation) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"name_on":' + BoolToJsonStr(NameOnFlag) +
                    ',"comment_on":' + BoolToJsonStr(CommentOnFlag) + '}';
                Inc(NumComps);
            End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    TracksJson  := TracksJson  + ']';
    ArcsJson    := ArcsJson    + ']';
    PadsJson    := PadsJson    + ']';
    ViasJson    := ViasJson    + ']';
    TextsJson   := TextsJson   + ']';
    RegionsJson := RegionsJson + ']';
    CompsJson   := CompsJson   + ']';

    { Layer colours + visibility, straight from Altium so the renderer can }
    { reproduce exactly what the user sees on the bench instead of guessing }
    { a palette. "color" is the raw TColor integer (BGR-packed: $00BBGGRR); }
    { the Python side converts to #RRGGBB. "visible" mirrors Altium's own   }
    { displayed-layer set so the render can default to showing only what    }
    { Altium shows. Keyed by GetLayerString -- the SAME name the primitives }
    { carry -- so the renderer matches by name with no translation. Range   }
    { eTopLayer..eMultiLayer covers signal, plane, mech, mask, paste, silk, }
    { keepout and multilayer (the official Altium docs iterate this range). }
    LayersJson := '{';
    LyrFirst := True;
    PCBSysOpts := Nil;
    Try PCBSysOpts := PCBServer.SystemOptions; Except End;
    If PCBSysOpts <> Nil Then
    Begin
        For Lyr := eTopLayer To eMultiLayer Do
        Begin
            LyrNm := GetLayerString(Lyr);
            If LyrNm <> 'Unknown' Then
            Begin
                LyrColor := 0;
                LyrVisible := True;
                Try LyrColor := PCBSysOpts.LayerColors[Lyr]; Except End;
                Try LyrVisible := Board.LayerIsDisplayed[Lyr]; Except End;
                If Not LyrFirst Then LayersJson := LayersJson + ',';
                LayersJson := LayersJson +
                    '"' + EscapeJsonString(LyrNm) + '":{"color":' +
                    IntToStr(LyrColor) + ',"visible":' +
                    BoolToJsonStr(LyrVisible) + '}';
                LyrFirst := False;
            End;
        End;
    End;
    LayersJson := LayersJson + '}';

    RespJson :=
        '{"counts":{"outline":' + IntToStr(NumOutline) +
        ',"tracks":' + IntToStr(NumTracks) +
        ',"arcs":' + IntToStr(NumArcs) +
        ',"pads":' + IntToStr(NumPads) +
        ',"vias":' + IntToStr(NumVias) +
        ',"texts":' + IntToStr(NumTexts) +
        ',"regions":' + IntToStr(NumRegions) +
        ',"components":' + IntToStr(NumComps) + '}' +
        ',"bbox":{"x1":' + IntToStr(CoordToMils(BR.X1)) +
        ',"y1":' + IntToStr(CoordToMils(BR.Y1)) +
        ',"x2":' + IntToStr(CoordToMils(BR.X2)) +
        ',"y2":' + IntToStr(CoordToMils(BR.Y2)) + '}' +
        ',"outline":' + OutlineJson +
        ',"tracks":' + TracksJson +
        ',"arcs":' + ArcsJson +
        ',"pads":' + PadsJson +
        ',"vias":' + ViasJson +
        ',"texts":' + TextsJson +
        ',"regions":' + RegionsJson +
        ',"components":' + CompsJson +
        ',"layers":' + LayersJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{..............................................................................}
{ Gen_IncrementDesignators - Offset the trailing number of every schematic     }
{ component designator by a delta (e.g. +100 turns R5 into R105), optionally   }
{ restricted to a designator prefix. Useful for renumbering a copied block.    }
{ Params: delta (non-zero int), prefix (optional, e.g. "R")                    }
{..............................................................................}

Function Gen_IncrementDesignators(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Comp : ISch_Component;
    Delta, NumVal, Count, i, DigitPos : Integer;
    PrefixFilter, DesigFull, AlphaPart, NumPart, NewDesig : String;
    Ch : Char;
Begin
    Delta := StrToIntDef(ExtractJsonValue(Params, 'delta'), 0);
    PrefixFilter := ExtractJsonValue(Params, 'prefix');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    If Delta = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'delta must be a non-zero integer');
        Exit;
    End;

    Count := 0;
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        Iter := SchDoc.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Try
            Comp := Iter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                DesigFull := '';
                Try DesigFull := Comp.Designator.Text; Except End;
                DigitPos := 0;
                For i := Length(DesigFull) DownTo 1 Do
                Begin
                    Ch := DesigFull[i];
                    If (Ch >= '0') And (Ch <= '9') Then DigitPos := i
                    Else Break;
                End;
                If DigitPos > 0 Then
                Begin
                    AlphaPart := Copy(DesigFull, 1, DigitPos - 1);
                    NumPart := Copy(DesigFull, DigitPos, Length(DesigFull) - DigitPos + 1);
                    If (PrefixFilter = '') Or (AlphaPart = PrefixFilter) Then
                    Begin
                        NumVal := StrToIntDef(NumPart, -1);
                        If NumVal >= 0 Then
                        Begin
                            NewDesig := AlphaPart + IntToStr(NumVal + Delta);
                            Try
                                SchBeginModify(Comp.Designator);
                                Comp.Designator.Text := NewDesig;
                                SchEndModify(Comp.Designator);
                                Inc(Count);
                            Except
                            End;
                        End;
                    End;
                End;
                Comp := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    End;
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"modified":' + IntToStr(Count) + ',"delta":' + IntToStr(Delta) + '}');
End;

{..............................................................................}
{ Gen_TogglePinVisibility - Show/hide pin name and/or designator labels on a   }
{ single component (by designator) or on every component on the sheet.        }
{ Params: designator (optional), show_name ("true"/"false", optional),        }
{         show_designator ("true"/"false", optional)                          }
{..............................................................................}

Function Gen_TogglePinVisibility(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    DesigFilter, ShowNameStr, ShowDesigStr, CompDesig : String;
    SetName, SetDesig, NameVal, DesigVal, Matched : Boolean;
    Count : Integer;
Begin
    DesigFilter := ExtractJsonValue(Params, 'designator');
    ShowNameStr := LowerCase(ExtractJsonValue(Params, 'show_name'));
    ShowDesigStr := LowerCase(ExtractJsonValue(Params, 'show_designator'));
    SetName := (ShowNameStr = 'true') Or (ShowNameStr = 'false');
    SetDesig := (ShowDesigStr = 'true') Or (ShowDesigStr = 'false');
    NameVal := (ShowNameStr = 'true');
    DesigVal := (ShowDesigStr = 'true');

    If (Not SetName) And (Not SetDesig) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'Provide show_name and/or show_designator as "true"/"false"');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Count := 0;
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        Iter := SchDoc.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Try
            Comp := Iter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                CompDesig := '';
                Try CompDesig := Comp.Designator.Text; Except End;
                Matched := (DesigFilter = '') Or (CompDesig = DesigFilter);
                If Matched Then
                Begin
                    PinIter := Comp.SchIterator_Create;
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Try
                        Pin := PinIter.FirstSchObject;
                        While Pin <> Nil Do
                        Begin
                            Try
                                SchBeginModify(Pin);
                                If SetName Then Pin.ShowName := NameVal;
                                If SetDesig Then Pin.ShowDesignator := DesigVal;
                                SchEndModify(Pin);
                                Inc(Count);
                            Except
                            End;
                            Pin := PinIter.NextSchObject;
                        End;
                    Finally
                        Comp.SchIterator_Destroy(PinIter);
                    End;
                End;
                Comp := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    End;
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"pins_modified":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ Gen_PlaceTextFrame - Place a multi-line schematic text frame (note block).  }
{ Params: x1,y1,x2,y2 (mils, the two rectangle corners), text (use \n for     }
{         line breaks), align ("left"/"center"/"right", optional)             }
{..............................................................................}

Function Gen_PlaceTextFrame(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, TmpI : Integer;
    SchDoc : ISch_Document;
    TF : ISch_TextFrame;
    TextStr, AlignStr : String;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    TextStr := ExtractJsonValue(Params, 'text');
    AlignStr := LowerCase(ExtractJsonValue(Params, 'align'));
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    TF := SchServer.SchObjectFactory(eTextFrame, eCreate_Default);
    If TF = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create text frame');
        Exit;
    End;

    TF.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    TF.Corner := Point(MilsToCoord(X2), MilsToCoord(Y2));
    Try TF.Text := TextStr; Except End;
    Try TF.WordWrap := True; Except End;
    Try TF.ClipToRect := True; Except End;
    Try TF.ShowBorder := True; Except End;
    Try TF.IsSolid := False; Except End;
    If AlignStr = 'center' Then
    Begin Try TF.Alignment := eHorizontalCentreAlign; Except End; End
    Else If AlignStr = 'right' Then
    Begin Try TF.Alignment := eRightAlign; Except End; End
    Else
    Begin Try TF.Alignment := eLeftAlign; Except End; End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(TF);
    SchRegisterObject(SchDoc, TF);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ Gen_ClearSchSourceLibrary - schematic mirror of the PCB-side                }
{ clear_source_footprint_library: unpin placed components from a stale        }
{ source library so Altium re-matches them from Available Libraries.          }
{ Per matching component: clear SourceLibraryName, and (default on) sync      }
{ DesignItemId to LibReference - the corpus-standard repair for the           }
{ <Not Found> state a re-link leaves behind when DesignItemId still names     }
{ the OLD library item. DesignItemId is a component PROPERTY, not a           }
{ parameter; the parameter-stamping path only creates a stray user            }
{ parameter of that name.                                                      }
{ Params: sheet_path (optional, focused doc default),                          }
{         designators (optional comma list; empty = every component),          }
{         clear_source_library=true, sync_design_item_id=true.                 }
Function Gen_ClearSchSourceLibrary(Params : String; RequestId : String) : String;
Var
    SheetPath, DesigCsv, FlagStr, Desig, LibRef : String;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    DesigList : TStringList;
    ClearSrc, SyncId, WantAll : Boolean;
    Total, ClearedSrc, Synced : Integer;
    SrvDoc : IServerDocument;
Begin
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    DesigCsv := ExtractJsonValue(Params, 'designators');
    FlagStr := ExtractJsonValue(Params, 'clear_source_library');
    ClearSrc := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));
    FlagStr := ExtractJsonValue(Params, 'sync_design_item_id');
    SyncId := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));

    SchDoc := Nil;
    If SheetPath <> '' Then
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    DesigList := TStringList.Create;
    Try
        DesigList.CommaText := DesigCsv;
        WantAll := DesigList.Count = 0;

        Total := 0;
        ClearedSrc := 0;
        Synced := 0;

        SchServer.ProcessControl.PreProcess(SchDoc, '');
        Try
            Iterator := SchDoc.SchIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iterator.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Comp := Obj;
                    Desig := '';
                    Try Desig := Comp.Designator.Text; Except End;
                    If WantAll Or (DesigList.IndexOf(Desig) >= 0) Then
                    Begin
                        Inc(Total);
                        If ClearSrc Then
                        Begin
                            Try
                                If Comp.SourceLibraryName <> '' Then
                                Begin
                                    SchBeginModify(Comp);
                                    Comp.SourceLibraryName := '';
                                    SchEndModify(Comp);
                                    Inc(ClearedSrc);
                                End;
                            Except End;
                        End;
                        If SyncId Then
                        Begin
                            Try
                                LibRef := Comp.LibReference;
                                If (LibRef <> '') And (Comp.DesignItemId <> LibRef) Then
                                Begin
                                    SchBeginModify(Comp);
                                    Comp.DesignItemId := LibRef;
                                    SchEndModify(Comp);
                                    Inc(Synced);
                                End;
                            Except End;
                        End;
                    End;
                    Obj := Iterator.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iterator);
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
        End;
    Finally
        DesigList.Free;
    End;

    Try
        SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"total":' + IntToStr(Total) +
        ',"cleared_source_library":' + IntToStr(ClearedSrc) +
        ',"synced_design_item_id":' + IntToStr(Synced) + '}');
End;

{..............................................................................}
{ Command Handler - must be at end                                            }
{..............................................................................}

{..............................................................................}
{ Gen_StubPins - place a short wire stub + net label at each named pin. Pins   }
{ is a pipe-separated list of "designator,pin_number,label" records (label     }
{ optional, defaults to designator_pinnumber). The stub extends from the pin's }
{ electrical end outward along the pin orientation by stub_length_mils.        }
{..............................................................................}
Function Gen_StubPins(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    PinsStr, RecStr, Remaining, Token : String;
    Desig, PinNum, Lbl : String;
    PipePos, CommaPos, FieldIdx, StubLen, Stubbed, Failed : Integer;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    PX, PY, EX, EY, Orient : Integer;
    Found : Boolean;
    Wire : ISch_Wire;
    NetLabel : ISch_NetLabel;
    InfFont, InfColor : Integer;
    StyleFound : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC', 'No schematic document is active');
        Exit;
    End;

    PinsStr := ExtractJsonValue(Params, 'pins');
    If PinsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'pins parameter required');
        Exit;
    End;
    StubLen := StrToIntDef(ExtractJsonValue(Params, 'stub_length_mils'), 100);

    Stubbed := 0;
    Failed := 0;
    { Sheet convention once, applied to every stub label below. }
    StyleFound := InferNetLabelStyle(SchDoc, InfFont, InfColor);
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        Remaining := PinsStr;
        While Length(Remaining) > 0 Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then Begin RecStr := Remaining; Remaining := ''; End
            Else Begin RecStr := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End;
            If RecStr = '' Then Continue;

            Desig := ''; PinNum := ''; Lbl := '';
            FieldIdx := 0;
            While (RecStr <> '') And (FieldIdx <= 2) Do
            Begin
                CommaPos := Pos(',', RecStr);
                If CommaPos = 0 Then Begin Token := RecStr; RecStr := ''; End
                Else Begin Token := Copy(RecStr, 1, CommaPos - 1); RecStr := Copy(RecStr, CommaPos + 1, Length(RecStr)); End;
                Case FieldIdx Of
                    0: Desig := Token;
                    1: PinNum := Token;
                    2: Lbl := Token;
                End;
                FieldIdx := FieldIdx + 1;
            End;

            If (Desig = '') Or (PinNum = '') Then Begin Failed := Failed + 1; Continue; End;

            Found := False;
            PX := 0; PY := 0; Orient := 0;
            Iter := SchDoc.SchIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                Comp := Iter.FirstSchObject;
                While (Comp <> Nil) And (Not Found) Do
                Begin
                    If Comp.Designator.Text = Desig Then
                    Begin
                        PinIter := Comp.SchIterator_Create;
                        Try
                            PinIter.AddFilter_ObjectSet(MkSet(ePin));
                            Pin := PinIter.FirstSchObject;
                            While (Pin <> Nil) And (Not Found) Do
                            Begin
                                If Pin.Designator = PinNum Then
                                Begin
                                    Try PX := CoordToMils(Pin.Location.X); Except End;
                                    Try PY := CoordToMils(Pin.Location.Y); Except End;
                                    Try Orient := Pin.Orientation; Except End;
                                    Found := True;
                                End;
                                Pin := PinIter.NextSchObject;
                            End;
                        Finally
                            Comp.SchIterator_Destroy(PinIter);
                        End;
                    End;
                    Comp := Iter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iter);
            End;

            If Not Found Then Begin Failed := Failed + 1; Continue; End;

            EX := PX; EY := PY;
            If Orient = 0 Then EX := PX + StubLen
            Else If Orient = 1 Then EY := PY + StubLen
            Else If Orient = 2 Then EX := PX - StubLen
            Else If Orient = 3 Then EY := PY - StubLen;

            Wire := SchServer.SchObjectFactory(eWire, eCreate_Default);
            If Wire <> Nil Then
            Begin
                Wire.Location := Point(MilsToCoord(PX), MilsToCoord(PY));
                Wire.InsertVertex := 1;
                Wire.SetState_Vertex(1, Point(MilsToCoord(PX), MilsToCoord(PY)));
                Wire.InsertVertex := 2;
                Wire.SetState_Vertex(2, Point(MilsToCoord(EX), MilsToCoord(EY)));
                SchDoc.RegisterSchObjectInContainer(Wire);
                SchRegisterObject(SchDoc, Wire);
            End;

            If Lbl = '' Then Lbl := Desig + '_' + PinNum;
            NetLabel := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
            If NetLabel <> Nil Then
            Begin
                NetLabel.Text := Lbl;
                NetLabel.Location := Point(MilsToCoord(EX), MilsToCoord(EY));
                If StyleFound Then
                Begin
                    Try NetLabel.FontId := InfFont; Except End;
                    NetLabel.Color := InfColor;
                End;
                SchDoc.RegisterSchObjectInContainer(NetLabel);
                SchRegisterObject(SchDoc, NetLabel);
            End;

            Stubbed := Stubbed + 1;
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"stubbed":' + IntToStr(Stubbed) + ',"failed":' + IntToStr(Failed) + '}');
End;

{..............................................................................}
{ Gen_SetNetTie - mark a placed schematic component as a net tie. A net tie     }
{ component shorts the nets landing on its pins for routing while keeping them   }
{ logically separate. mode 'bom' keeps it in the BOM; 'nobom' (default) hides    }
{ it and lets synchronization maintain it. Sets ISch_Component.ComponentKind.    }
{..............................................................................}
Function Gen_SetNetTie(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Comp, Target : ISch_Component;
    Desig, ModeStr, KindStr : String;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC', 'No schematic document is active');
        Exit;
    End;

    Desig := ExtractJsonValue(Params, 'designator');
    If Desig = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator parameter required');
        Exit;
    End;
    ModeStr := ExtractJsonValue(Params, 'mode');
    If ModeStr = '' Then ModeStr := 'nobom';

    Target := Nil;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        While (Comp <> Nil) And (Target = Nil) Do
        Begin
            If Comp.Designator.Text = Desig Then Target := Comp;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Desig);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        If ModeStr = 'bom' Then
        Begin
            Target.ComponentKind := eComponentKind_NetTie_BOM;
            KindStr := 'NetTie_BOM';
        End
        Else
        Begin
            Target.ComponentKind := eComponentKind_NetTie_NoBOM;
            KindStr := 'NetTie_NoBOM';
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    End;
    Try SchDoc.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(Desig) + '","component_kind":"'
        + KindStr + '"}');
End;

{..............................................................................}
{ Gen_ExplainPin                                                              }
{                                                                              }
{ Answers "why is this pin on that net?" -- the question proj_get_nets can    }
{ state a verdict on but never justify. Given a designator and pin number,    }
{ reports the pin's ROOT (Pin.Location, body side) and its CONNECTION point   }
{ (Location + PinLength along Orientation), then lists every schematic object }
{ sitting on each of those two points.                                        }
{                                                                              }
{ Reading the result:                                                         }
{  - objects under "at_connection" are what actually drive the pin's net.     }
{    Two net labels with different text there is a short.                     }
{  - objects under "at_root" are electrically INERT. A net label there is the }
{    classic "sheet looks wired but the pin floats" bug.                      }
{  - an empty "at_connection" with a populated "at_root" means the label      }
{    needs to move by PinLength along the pin's orientation.                  }
{                                                                              }
{ Params: designator (required), pin (required, the pin NUMBER not name).     }
{..............................................................................}

Function Gen_ExplainPin(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    CompIter, PinIter, SpatIter : ISch_Iterator;
    CompObj, PinObj, Hit : ISch_GraphicalObject;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    WantDesig, WantPin, DocKind, SheetName : String;
    FoundSheet, PinName, PinNumStr : String;
    Loc : TLocation;
    RX, RY, CX, CY, PinLen, PinOrient : Integer;
    Tol, ObjId : Integer;
    ConnJson, RootJson, Detail : String;
    FirstC, FirstR, Found, HitOk : Boolean;
    Pass : Integer;
    PX, PY, HitX, HitY, EndX, EndY : Integer;
    OtherLen, OtherOri, VtxI, VtxN : Integer;
    ItemText : String;
    V1, V2 : TLocation;
Begin
    WantDesig := ExtractJsonValue(Params, 'designator');
    WantPin := ExtractJsonValue(Params, 'pin');
    If (WantDesig = '') Or (WantPin = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
            'designator and pin are both required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project focused');
        Exit;
    End;

    Found := False;
    FoundSheet := '';
    PinName := '';
    PinNumStr := '';
    RX := 0; RY := 0; CX := 0; CY := 0;
    PinLen := 0; PinOrient := 0;
    Tol := MilsToCoord(1);
    Sheet := Nil;

    For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        If Found Then Break;
        Document := Nil;
        Try Document := Project.DM_LogicalDocuments(DocI); Except End;
        If Document = Nil Then Continue;
        DocKind := '';
        Try DocKind := Document.DM_DocumentKind; Except End;
        If DocKind <> 'SCH' Then Continue;
        Sheet := Nil;
        Try Sheet := SchServer.GetSchDocumentByPath(Document.DM_FullPath); Except End;
        If Sheet = Nil Then Continue;
        SheetName := '';
        Try SheetName := Document.DM_FileName; Except End;

        CompIter := Sheet.SchIterator_Create;
        If CompIter = Nil Then Continue;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            CompObj := CompIter.FirstSchObject;
            While (CompObj <> Nil) And (Not Found) Do
            Begin
                Try
                    Comp := CompObj;
                    If Comp.Designator.Text = WantDesig Then
                    Begin
                        PinIter := Comp.SchIterator_Create;
                        If PinIter <> Nil Then
                        Begin
                            Try
                                PinIter.AddFilter_ObjectSet(MkSet(ePin));
                                PinObj := PinIter.FirstSchObject;
                                While (PinObj <> Nil) And (Not Found) Do
                                Begin
                                    Try
                                        Pin := PinObj;
                                        PinNumStr := '';
                                        Try PinNumStr := Pin.Designator; Except End;
                                        If PinNumStr = WantPin Then
                                        Begin
                                            Found := True;
                                            FoundSheet := SheetName;
                                            Try PinName := Pin.Name; Except End;
                                            Loc := Pin.GetState_Location;
                                            RX := Loc.X;
                                            RY := Loc.Y;
                                            Try PinLen := Pin.PinLength; Except End;
                                            Try PinOrient := Pin.Orientation; Except End;
                                            CX := RX;
                                            CY := RY;
                                            If PinOrient = 0 Then CX := RX + PinLen
                                            Else If PinOrient = 1 Then CY := RY + PinLen
                                            Else If PinOrient = 2 Then CX := RX - PinLen
                                            Else If PinOrient = 3 Then CY := RY - PinLen;
                                        End;
                                    Except End;
                                    If Not Found Then PinObj := PinIter.NextSchObject;
                                End;
                            Finally
                                Comp.SchIterator_Destroy(PinIter);
                            End;
                        End;
                    End;
                Except End;
                If Not Found Then CompObj := CompIter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(CompIter);
        End;
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'PIN_NOT_FOUND',
            'No pin ' + WantPin + ' on component ' + WantDesig);
        Exit;
    End;

    ConnJson := '';
    RootJson := '';
    FirstC := True;
    FirstR := True;

    { Pass 0 = connection point (live), Pass 1 = root (inert). }
    For Pass := 0 To 1 Do
    Begin
        If Pass = 0 Then
        Begin
            PX := CX;
            PY := CY;
        End
        Else
        Begin
            PX := RX;
            PY := RY;
        End;

        SpatIter := Sheet.SchIterator_Create;
        If SpatIter = Nil Then Continue;
        Try
            SpatIter.AddFilter_ObjectSet(
                MkSet(eNetLabel, eWire, ePowerObject, eJunction, ePin, ePort));
            { AddFilter_Area matches BOUNDING BOXES. Net-label text extends }
            { hundreds of mils from Location, so a 1-mil square still hits  }
            { labels whose Location is far away. Always re-check Location   }
            { (or pin electrical end, or wire vertices) after the filter.   }
            SpatIter.AddFilter_Area(PX - Tol, PY - Tol, PX + Tol, PY + Tol);
            Hit := SpatIter.FirstSchObject;
            While Hit <> Nil Do
            Begin
                Try
                    ObjId := Hit.ObjectId;
                    ItemText := '';
                    HitOk := False;
                    HitX := 0;
                    HitY := 0;
                    If (ObjId = eNetLabel) Or (ObjId = ePowerObject) Or
                       (ObjId = ePort) Or (ObjId = eJunction) Then
                    Begin
                        Loc := Hit.GetState_Location;
                        HitX := Loc.X;
                        HitY := Loc.Y;
                        HitOk := CoordWithinTol(HitX, PX, Tol) And
                                 CoordWithinTol(HitY, PY, Tol);
                    End
                    Else If ObjId = ePin Then
                    Begin
                        Loc := Hit.GetState_Location;
                        HitX := Loc.X;
                        HitY := Loc.Y;
                        OtherLen := 0;
                        OtherOri := 0;
                        Try OtherLen := Hit.PinLength; Except End;
                        Try OtherOri := Hit.Orientation; Except End;
                        EndX := HitX;
                        EndY := HitY;
                        If OtherOri = 0 Then EndX := HitX + OtherLen
                        Else If OtherOri = 1 Then EndY := HitY + OtherLen
                        Else If OtherOri = 2 Then EndX := HitX - OtherLen
                        Else If OtherOri = 3 Then EndY := HitY - OtherLen;
                        If CoordWithinTol(EndX, PX, Tol) And
                           CoordWithinTol(EndY, PY, Tol) Then
                        Begin
                            HitOk := True;
                            HitX := EndX;
                            HitY := EndY;
                        End
                        Else If CoordWithinTol(HitX, PX, Tol) And
                                CoordWithinTol(HitY, PY, Tol) Then
                            HitOk := True;
                    End
                    Else If ObjId = eWire Then
                    Begin
                        VtxN := 0;
                        Try VtxN := Hit.GetState_VerticesCount; Except End;
                        VtxI := 1;
                        While (VtxI < VtxN) And (Not HitOk) Do
                        Begin
                            Try
                                V1 := Hit.GetState_Vertex(VtxI);
                                V2 := Hit.GetState_Vertex(VtxI + 1);
                                If PointNearSegment(PX, PY, V1.X, V1.Y,
                                    V2.X, V2.Y, Tol) Then
                                Begin
                                    HitOk := True;
                                    HitX := PX;
                                    HitY := PY;
                                End;
                            Except End;
                            VtxI := VtxI + 1;
                        End;
                    End;

                    If HitOk Then
                    Begin
                        If ObjId = eNetLabel Then
                        Begin
                            Try ItemText := Hit.Text; Except End;
                            Detail := JsonStr('kind', 'net_label') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else If ObjId = ePowerObject Then
                        Begin
                            Try ItemText := Hit.Text; Except End;
                            Detail := JsonStr('kind', 'power_object') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else If ObjId = ePort Then
                        Begin
                            Try ItemText := Hit.Name; Except End;
                            Detail := JsonStr('kind', 'port') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else If ObjId = eWire Then
                            Detail := JsonStr('kind', 'wire') + ',' +
                                      JsonStr('text', '')
                        Else If ObjId = eJunction Then
                            Detail := JsonStr('kind', 'junction') + ',' +
                                      JsonStr('text', '')
                        Else If ObjId = ePin Then
                        Begin
                            Try ItemText := Hit.Designator; Except End;
                            Detail := JsonStr('kind', 'pin') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else
                            Detail := JsonStr('kind', 'other') + ',' +
                                      JsonStr('text', '');
                        Detail := Detail + ',' +
                                  JsonInt('x_mils', CoordToMils(HitX)) + ',' +
                                  JsonInt('y_mils', CoordToMils(HitY));

                        If Pass = 0 Then
                        Begin
                            If Not FirstC Then ConnJson := ConnJson + ',';
                            FirstC := False;
                            ConnJson := ConnJson + JsonObj(Detail);
                        End
                        Else
                        Begin
                            If Not FirstR Then RootJson := RootJson + ',';
                            FirstR := False;
                            RootJson := RootJson + JsonObj(Detail);
                        End;
                    End;
                Except End;
                Hit := SpatIter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(SpatIter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('designator', WantDesig) + ',' +
            JsonStr('pin', WantPin) + ',' +
            JsonStr('pin_name', PinName) + ',' +
            JsonStr('sheet', FoundSheet) + ',' +
            JsonInt('orientation', PinOrient) + ',' +
            JsonInt('pin_length_mils', CoordToMils(PinLen)) + ',' +
            JsonInt('root_x_mils', CoordToMils(RX)) + ',' +
            JsonInt('root_y_mils', CoordToMils(RY)) + ',' +
            JsonInt('connect_x_mils', CoordToMils(CX)) + ',' +
            JsonInt('connect_y_mils', CoordToMils(CY)) + ',' +
            JsonRaw('at_connection', '[' + ConnJson + ']') + ',' +
            JsonRaw('at_root', '[' + RootJson + ']')
        ));
End;


Function HandleGenericCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'query_objects':    Result := Gen_QueryObjects(Params, RequestId);
        'explain_pin':      Result := Gen_ExplainPin(Params, RequestId);
        'modify_objects':   Result := Gen_ModifyObjects(Params, RequestId);
        'create_object':    Result := Gen_CreateObject(Params, RequestId);
        'delete_objects':   Result := Gen_DeleteObjects(Params, RequestId);
        'batch_modify':     Result := Gen_BatchModify(Params, RequestId);
        'run_process':      Result := Gen_RunProcess(Params, RequestId);
        'get_font_spec':    Result := Gen_GetFontSpec(Params, RequestId);
        'get_font_id':      Result := Gen_GetFontId(Params, RequestId);
        'select_objects':   Result := Gen_SelectObjects(Params, RequestId);
        'deselect_all':     Result := Gen_DeselectAll(RequestId);
        'zoom':             Result := Gen_Zoom(Params, RequestId);
        'run_erc':          Result := Gen_RunERC(Params, RequestId);
        'get_sch_geometry': Result := Gen_GetSchGeometry(Params, RequestId);
        'get_pcb_geometry': Result := Gen_GetPcbGeometry(Params, RequestId);
        'highlight_net':    Result := Gen_HighlightNet(Params, RequestId);
        'clear_highlights': Result := Gen_ClearHighlights(RequestId);
        'crossref_net':     Result := Gen_CrossRefNet(Params, RequestId);
        'add_sheet':        Result := Gen_AddSheet(Params, RequestId);
        'delete_sheet':     Result := Gen_DeleteSheet(Params, RequestId);
        'zoom_to_xy':       Result := Gen_ZoomToXY(Params, RequestId);
        'switch_view':      Result := Gen_SwitchView(Params, RequestId);
        'measure_distance': Result := Gen_MeasureDistance(Params, RequestId);
        'get_erc_violations': Result := Gen_GetErcViolations(Params, RequestId);
        'refresh_document': Result := Gen_RefreshDocument(RequestId);
        'get_unconnected_pins': Result := Gen_GetUnconnectedPins(Params, RequestId);
        'place_wire':       Result := Gen_PlaceWire(Params, RequestId);
        'place_bus':        Result := Gen_PlaceBus(Params, RequestId);
        'place_directive':  Result := Gen_PlaceDirective(Params, RequestId);
        'get_directives':   Result := Gen_GetDirectives(Params, RequestId);
        'place_compile_mask': Result := Gen_PlaceCompileMask(Params, RequestId);
        'place_rectangle':  Result := Gen_PlaceRectangle(Params, RequestId);
        'place_line':       Result := Gen_PlaceLine(Params, RequestId);
        'place_note':       Result := Gen_PlaceNote(Params, RequestId);
        'place_sheet_symbol': Result := Gen_PlaceSheetSymbol(Params, RequestId);
        'place_sheet_entry': Result := Gen_PlaceSheetEntry(Params, RequestId);
        'place_bus_entry':   Result := Gen_PlaceBusEntry(Params, RequestId);
        'set_sheet_size':    Result := Gen_SetSheetSize(Params, RequestId);
        'place_sch_component_from_library': Result := Gen_PlaceSchComponentFromLibrary(Params, RequestId);
        'set_sch_component_parameters': Result := Gen_SetSchComponentParameters(Params, RequestId);
        'get_sch_component_pins': Result := Gen_GetSchComponentPins(Params, RequestId);
        'place_net_label':  Result := Gen_PlaceNetLabel(Params, RequestId);
        'stub_pins':        Result := Gen_StubPins(Params, RequestId);
        'set_net_tie':      Result := Gen_SetNetTie(Params, RequestId);
        'place_port':       Result := Gen_PlacePort(Params, RequestId);
        'place_power_port': Result := Gen_PlacePowerPort(Params, RequestId);
        'get_sheet_parameters': Result := Gen_GetSheetParameters(Params, RequestId);
        'copy_objects':     Result := Gen_CopyObjects(Params, RequestId);
        'get_object_count': Result := Gen_GetObjectCount(Params, RequestId);
        'place_no_erc':     Result := Gen_PlaceNoERC(Params, RequestId);
        'place_junction':   Result := Gen_PlaceJunction(Params, RequestId);
        'place_junctions':  Result := Gen_PlaceJunctions(Params, RequestId);
        'get_document_info': Result := Gen_GetDocumentInfo(Params, RequestId);
        'set_grid':         Result := Gen_SetGrid(Params, RequestId);
        'set_sch_units':    Result := Gen_SetSchUnits(Params, RequestId);
        'place_image':      Result := Gen_PlaceImage(Params, RequestId);
        'replace_component': Result := Gen_ReplaceComponent(Params, RequestId);
        'clear_sch_source_library': Result := Gen_ClearSchSourceLibrary(Params, RequestId);
        'get_constraint_groups':      Result := Gen_GetConstraintGroups(Params, RequestId);
        'place_harness_connector':    Result := Gen_PlaceHarnessConnector(Params, RequestId);
        'place_cross_sheet_connector': Result := Gen_PlaceCrossSheetConnector(Params, RequestId);
        'place_text_frame': Result := Gen_PlaceTextFrame(Params, RequestId);
        'increment_designators': Result := Gen_IncrementDesignators(Params, RequestId);
        'toggle_pin_visibility': Result := Gen_TogglePinVisibility(Params, RequestId);
        'set_component_part_id':      Result := Gen_SetComponentPartId(Params, RequestId);
        'set_component_unique_id':    Result := Gen_SetComponentUniqueId(Params, RequestId);
        'replicate_sch_component':    Result := Gen_ReplicateSchComponent(Params, RequestId);
        'place_probe':                Result := Gen_PlaceProbe(Params, RequestId);
        'add_datafile_link':          Result := Gen_AddDatafileLink(Params, RequestId);
        'get_simulation_readiness':   Result := Gen_GetSimulationReadiness(Params, RequestId);
        'attach_spice_primitive':     Result := Gen_AttachSpicePrimitive(Params, RequestId);
        'attach_spice_model':         Result := Gen_AttachSpiceModel(Params, RequestId);
        'run_simulation':             Result := Gen_RunSimulation(Params, RequestId);
        'batch_create':               Result := Gen_BatchCreate(Params, RequestId);
        'batch_delete':               Result := Gen_BatchDelete(Params, RequestId);
        'place_wires':                Result := Gen_PlaceWires(Params, RequestId);
        'place_net_labels':           Result := Gen_PlaceNetLabels(Params, RequestId);
        'place_power_ports':          Result := Gen_PlacePowerPorts(Params, RequestId);
        'get_sch_doc_pins':           Result := Gen_GetSchDocPins(Params, RequestId);
        'set_sch_components_parameters': Result := Gen_SetSchComponentsParameters(Params, RequestId);
        'set_sch_text_positions':      Result := Gen_SetSchTextPositions(Params, RequestId);
        'place_sch_components_from_library': Result := Gen_PlaceSchComponentsFromLibrary(Params, RequestId);
        'attach_spice_primitives':    Result := Gen_AttachSpicePrimitivesBatch(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown generic action: ' + Action);
    End;
End;
