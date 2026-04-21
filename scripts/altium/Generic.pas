{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Generic.pas - Generic primitives for the Altium integration bridge                        }
{ 5 primitives: run_process, query_objects, modify_objects,                  }
{               create_object, delete_objects                                }
{ These provide a thin, generic layer so Python controls all logic.          }
{..............................................................................}

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
{ Typed component property helper — extract designator / comment text by    }
{ casting to ISch_Component first. Required because `Obj.Designator` on a    }
{ base ISch_GraphicalObject fails to compile — DelphiScript cannot late-bind }
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
        // Ignore failures silently
    End;
End;

Function GetSchProperty(Obj : ISch_GraphicalObject; PropName : String) : String;
Begin
    Result := '';
    Try
        // Identity
        If PropName = 'ObjectId'    Then Result := IntToStr(Obj.ObjectId)

        // Coordinates (returned in mils)
        Else If PropName = 'Location.X'  Then Result := IntToStr(CoordToMils(Obj.Location.X))
        Else If PropName = 'Location.Y'  Then Result := IntToStr(CoordToMils(Obj.Location.Y))
        Else If PropName = 'Corner.X'    Then Result := IntToStr(CoordToMils(Obj.Corner.X))
        Else If PropName = 'Corner.Y'    Then Result := IntToStr(CoordToMils(Obj.Corner.Y))

        // String properties (late-bound across all types — primitives only)
        Else If PropName = 'Text'        Then Result := Obj.Text
        Else If PropName = 'Name'        Then Result := Obj.Name
        Else If PropName = 'LibReference'       Then Result := Obj.LibReference
        Else If PropName = 'SourceLibraryName'  Then Result := Obj.SourceLibraryName
        Else If PropName = 'ComponentDescription' Then Result := Obj.ComponentDescription
        Else If PropName = 'UniqueId'    Then Result := Obj.UniqueId

        // Sub-object string properties (compound interfaces — typed cast required)
        Else If PropName = 'Designator'      Then Result := GetSchComponentSubText(Obj, 'Designator')
        Else If PropName = 'Designator.Text' Then Result := GetSchComponentSubText(Obj, 'Designator')
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
        Else If PropName = 'XSize'       Then Result := IntToStr(CoordToMils(Obj.XSize))
        Else If PropName = 'YSize'       Then Result := IntToStr(CoordToMils(Obj.YSize))

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

Procedure SetSchProperty(Obj : ISch_GraphicalObject; PropName : String; Value : String);
Var
    Loc : TLocation;
    Crn : TLocation;
Begin
    Try
        // Coordinates (expected in mils). `Obj.Location` returns a copy of
        // the TLocation record via the GetState_Location reader; writing
        // directly to `.X` / `.Y` on that copy is silently discarded. Read
        // the whole record, patch the target field, write it back.
        If PropName = 'Location.X' Then
        Begin
            Loc := Obj.Location;
            Loc.X := MilsToCoord(StrToIntDef(Value, 0));
            Obj.Location := Loc;
        End
        Else If PropName = 'Location.Y' Then
        Begin
            Loc := Obj.Location;
            Loc.Y := MilsToCoord(StrToIntDef(Value, 0));
            Obj.Location := Loc;
        End
        Else If PropName = 'Corner.X' Then
        Begin
            Crn := Obj.Corner;
            Crn.X := MilsToCoord(StrToIntDef(Value, 0));
            Obj.Corner := Crn;
        End
        Else If PropName = 'Corner.Y' Then
        Begin
            Crn := Obj.Corner;
            Crn.Y := MilsToCoord(StrToIntDef(Value, 0));
            Obj.Corner := Crn;
        End

        // String properties (late-bound across all types — primitives only)
        Else If PropName = 'Text'        Then Obj.Text := Value
        Else If PropName = 'Name'        Then Obj.Name := Value
        Else If PropName = 'LibReference'       Then Obj.LibReference := Value
        Else If PropName = 'ComponentDescription' Then Obj.ComponentDescription := Value

        // Sub-object string properties (compound interfaces — typed cast required)
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
        Else If PropName = 'Electrical'  Then Obj.Electrical := StrToIntDef(Value, 0)
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
        Else If PropName = 'Selection'   Then Obj.Selection := StrToBool(Value);
    Except
        // Property doesn't exist on this object type — silently skip
    End;
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
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
Begin
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

        SetSchProperty(Obj, PropName, PropValue);
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
    Iterator : ISch_Iterator;
    Obj, FoundObj : ISch_GraphicalObject;
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
            SchDoc.RemoveSchObject(FoundObj);
            Inc(TotalMatched);
            Dec(MaxIter);
        End;
        SchServer.ProcessControl.PostProcess(SchDoc, '');
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
        SchServer.ProcessControl.PostProcess(SchDoc, '');
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
        // as a "free document" with the absolute path as its tab title —
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
    DocPath := StringReplace(DocPath, '\\', '\', -1);

    // Do NOT RunProcess Client:OpenDocument — that loads the file but
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
{ Helper: Parse scope string — returns scope type and path if specified       }
{ Scope formats: "active_doc", "project", "project:C:\path", "doc:C:\path"  }
{..............................................................................}

Procedure ParseScope(Scope : String; Var ScopeType : String; Var ScopePath : String);
Begin
    ScopeType := 'active_doc';
    ScopePath := '';

    If Scope = '' Then Exit;

    If Copy(Scope, 1, 4) = 'doc:' Then
    Begin
        ScopeType := 'doc';
        ScopePath := Copy(Scope, 5, Length(Scope));
        ScopePath := StringReplace(ScopePath, '\\', '\', -1);
    End
    Else If Copy(Scope, 1, 8) = 'project:' Then
    Begin
        ScopeType := 'project';
        ScopePath := Copy(Scope, 9, Length(Scope));
        ScopePath := StringReplace(ScopePath, '\\', '\', -1);
    End
    Else
        ScopeType := Scope;
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
        SchServer.ProcessControl.PostProcess(SchDoc, '');
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
{ Select objects matching filter — sets Selection/Selected on matching objs  }
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

Function Gen_DeselectAll(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc <> Nil Then
    Begin
        SchDoc.ClearSelection;
        SchDoc.GraphicallyInvalidate;
        Result := BuildSuccessResponse(RequestId, '{"deselected":true}');
        Exit;
    End;

    Board := PCBServer.GetCurrentPCBBoard;
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
    Board := PCBServer.GetCurrentPCBBoard;

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
{ Params: operations — pipe-separated list of operations, each semicolon-    }
{   separated as: scope;object_type;filter;set                               }
{   Example: "project;eParameter;Name=Engineer;Text=John|                    }
{             project;eParameter;Name=Revision;Text=2.0"                     }
{                                                                            }
{ This processes ALL operations on the Altium side in one round-trip,        }
{ dramatically faster than multiple individual modify_objects calls.          }
{..............................................................................}

Function Gen_BatchModify(Params : String; RequestId : String) : String;
Var
    Operations, OpStr, Remaining : String;
    Scope, ObjTypeStr, FilterStr, SetStr : String;
    ScopeType, ScopePath : String;
    ObjTypeInt, PipePos, SemiPos : Integer;
    TotalMatched, OpCount, OpMatched : Integer;
    ResultJson : String;
Begin
    Operations := ExtractJsonValue(Params, 'operations');
    If Operations = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'operations parameter is required');
        Exit;
    End;

    TotalMatched := 0;
    OpCount := 0;
    ResultJson := '';
    Remaining := Operations;

    While Length(Remaining) > 0 Do
    Begin
        // Split on pipe to get next operation
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

        // Parse operation: scope;object_type;filter;set
        // Split on semicolons
        SemiPos := Pos(';', OpStr);
        If SemiPos = 0 Then Continue;
        Scope := Copy(OpStr, 1, SemiPos - 1);
        OpStr := Copy(OpStr, SemiPos + 1, Length(OpStr));

        SemiPos := Pos(';', OpStr);
        If SemiPos = 0 Then Continue;
        ObjTypeStr := Copy(OpStr, 1, SemiPos - 1);
        OpStr := Copy(OpStr, SemiPos + 1, Length(OpStr));

        SemiPos := Pos(';', OpStr);
        If SemiPos = 0 Then Continue;
        FilterStr := Copy(OpStr, 1, SemiPos - 1);
        SetStr := Copy(OpStr, SemiPos + 1, Length(OpStr));

        If (ObjTypeStr = '') Or (SetStr = '') Then Continue;

        ParseScope(Scope, ScopeType, ScopePath);
        ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
        If ObjTypeInt = -1 Then Continue;

        // Execute this operation
        OpMatched := 0;
        If ScopeType = 'project' Then
        Begin
            IterateProjectDocs(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, ScopePath, 0);
        End
        Else If ScopeType = 'doc' Then
        Begin
            ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);
        End
        Else
        Begin
            ProcessActiveDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);
        End;

        Inc(OpCount);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"operations_processed":' + IntToStr(OpCount) + '}');
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
    Project.DM_Compile;

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
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    NetName := ExtractJsonValue(Params, 'net_name');
    ClearExisting := ExtractJsonValue(Params, 'clear_existing');

    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'net_name parameter is required');
        Exit;
    End;

    Board := PCBServer.GetCurrentPCBBoard;
    SchDoc := SchServer.GetCurrentSchDocument;

    If Board <> Nil Then
    Begin
        // PCB: clear first if requested (default true)
        If (ClearExisting = '') Or (ClearExisting = 'true') Then
        Begin
            ResetParameters;
            RunProcess('PCB:DeSelect');
        End;

        ResetParameters;
        AddStringParameter('Net', NetName);
        RunProcess('PCB:NetColorHighlight');

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"net":"' + EscapeJsonString(NetName) + '","context":"pcb"}');
    End
    Else If SchDoc <> Nil Then
    Begin
        // Schematic: use Sch:NetHighlight
        If (ClearExisting = '') Or (ClearExisting = 'true') Then
        Begin
            ResetParameters;
            RunProcess('Sch:ClearHighlight');
        End;

        ResetParameters;
        AddStringParameter('Net', NetName);
        RunProcess('Sch:NetHighlight');

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"net":"' + EscapeJsonString(NetName) + '","context":"schematic"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Clear all highlights in the active document (schematic or PCB)             }
{..............................................................................}

Function Gen_ClearHighlights(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    SchDoc := SchServer.GetCurrentSchDocument;

    If Board <> Nil Then
    Begin
        ResetParameters;
        RunProcess('PCB:ClearAllHighlights');
        Result := BuildSuccessResponse(RequestId, '{"success":true,"context":"pcb"}');
    End
    Else If SchDoc <> Nil Then
    Begin
        ResetParameters;
        RunProcess('Sch:ClearHighlight');
        Result := BuildSuccessResponse(RequestId, '{"success":true,"context":"schematic"}');
    End
    Else
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

    // Create blank schematic (no FileName param — causes null key error)
    ResetParameters;
    AddStringParameter('ObjectKind', 'SchDoc');
    RunProcess('WorkspaceManager:CreateNewDocument');

    // Save the newly created blank doc to the desired path
    ResetParameters;
    AddStringParameter('ObjectKind', 'SchDoc');
    AddStringParameter('FileName', NewDocPath);
    RunProcess('WorkspaceManager:SaveObject');

    // Add to the project
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', NewDocPath);
    RunProcess('WorkspaceManager:AddObjectToProject');

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"path":"' + EscapeJsonString(NewDocPath) + '"}');
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

    FilePath := StringReplace(FilePath, '\\', '\', -1);

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

    Board := PCBServer.GetCurrentPCBBoard;
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

    Board := PCBServer.GetCurrentPCBBoard;
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
        ',"distance_mils":' + FloatToStr(Distance) +
        ',"distance_mm":' + FloatToStr(Distance * 0.0254) + '}');
End;

{..............................................................................}
{ Get ERC violations from the focused project after compilation/ERC          }
{ Returns violation count and messages from the DM API.                      }
{..............................................................................}

Function Gen_GetErcViolations(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Violation : IViolation;
    I, VCount, MaxItems : Integer;
    JsonItems : String;
    First : Boolean;
    Desc : String;
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

        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '{"index":' + IntToStr(I) +
            ',"description":"' + EscapeJsonString(Desc) + '"}';
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
    Board := PCBServer.GetCurrentPCBBoard;

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
    I, J, K, PinCount, CompCount, Total : Integer;
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
    Project.DM_Compile;

    Total := 0;
    JsonItems := '';
    First := True;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
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

    Wire.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Wire.InsertVertex := 1;
    Wire.SetState_Vertex(1, Point(MilsToCoord(X2), MilsToCoord(Y2)));
    Wire.Color := 0;
    Wire.LineWidth := eSmall;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Wire);
    SchRegisterObject(SchDoc, Wire);
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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

    Bus.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Bus.InsertVertex := 1;
    Bus.SetState_Vertex(1, Point(MilsToCoord(X2), MilsToCoord(Y2)));

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Bus);
    SchRegisterObject(SchDoc, Bus);
    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) +
        ',"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ Place a rectangle on the schematic — graphic box, not a functional shape.   }
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"text":"' + EscapeJsonString(TextStr) + '"}');
End;

{..............................................................................}
{ Place a sheet symbol on the schematic — reference to a child SchDoc.        }
{ Params: x1,y1,x2,y2 in mils, sheet_file_name (e.g. PSU.SchDoc),             }
{         sheet_name (display name)                                           }
{..............................................................................}

Function Gen_PlaceSheetSymbol(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, TmpI : Integer;
    SchDoc : ISch_Document;
    Sym : ISch_SheetSymbol;
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

    Sym.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    Sym.Corner := Point(MilsToCoord(X2), MilsToCoord(Y2));
    { SheetFileName is the link to the child sheet file — must match an
      existing .SchDoc in the project. SheetName is the display label
      shown inside the sheet-symbol block. }
    Sym.SheetFileName := FileNameStr;
    If NameStr = '' Then NameStr := ChangeFileExt(FileNameStr, '');
    Sym.SheetName := NameStr;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Sym);
    SchRegisterObject(SchDoc, Sym);
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    Obj : ISch_BasicObject;
    Sym : ISch_SheetSymbol;
    Entry : ISch_SheetEntry;
    SheetNameStr, EntryName, IOStr, SideStr : String;
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

    { Locate the target sheet symbol by its SheetName. }
    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSheetSymbol));
    Try
        Obj := Iterator.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Try
                Sym := Obj;
                If Sym.SheetName = SheetNameStr Then
                Begin
                    Found := True;
                    Break;
                End;
            Except End;
            Obj := Iterator.NextSchObject;
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

    If SideStr = 'right' Then Entry.Side := eSide_Right
    Else If SideStr = 'top' Then Entry.Side := eSide_Top
    Else If SideStr = 'bottom' Then Entry.Side := eSide_Bottom
    Else Entry.Side := eSide_Left;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Sym.AddSchObject(Entry);
    SchRegisterObject(Sym, Entry);
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    StyleStr : String;
    CustomW, CustomH : Integer;
Begin
    StyleStr := UpperCase(ExtractJsonValue(Params, 'style'));
    CustomW := StrToIntDef(ExtractJsonValue(Params, 'custom_width'), 0);
    CustomH := StrToIntDef(ExtractJsonValue(Params, 'custom_height'), 0);

    If StyleStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'style required');
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
            SchServer.ProcessControl.PostProcess(SchDoc, '');
            Result := BuildErrorResponse(RequestId, 'INVALID_STYLE',
                'Unknown sheet style: ' + StyleStr);
            Exit;
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, '');
    End;
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"style":"' + EscapeJsonString(StyleStr) + '"}');
End;

{..............................................................................}
{ Place a schematic component instance from a library onto the active sheet.  }
{ Uses ISch_Document.PlaceSchComponent — the verified direct-placement API.   }
{ Params: library_path (.SchLib full path), lib_reference (component name),   }
{         x, y (mils), designator (optional), rotation (0|90|180|270),        }
{         footprint (optional override)                                        }
{..............................................................................}

Function Gen_PlaceSchComponentFromLibrary(Params : String; RequestId : String) : String;
Var
    LibPath, LibRef, DesigStr, FootprintStr : String;
    X, Y, Rotation : Integer;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    CompLoc : TLocation;
    RotCount, I : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    LibRef := ExtractJsonValue(Params, 'lib_reference');
    DesigStr := ExtractJsonValue(Params, 'designator');
    FootprintStr := ExtractJsonValue(Params, 'footprint');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);

    If LibRef = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'lib_reference required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { PlaceSchComponent loads the library (or uses an already-open one) and       }
    { registers the new component with the sheet. If lookup fails Comp is Nil.   }
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        Comp := SchDoc.PlaceSchComponent(LibPath, LibRef);
    Except
        Comp := Nil;
    End;

    If Comp = Nil Then
    Begin
        SchServer.ProcessControl.PostProcess(SchDoc, '');
        Result := BuildErrorResponse(RequestId, 'PLACE_FAILED',
            'PlaceSchComponent returned nil — check library_path and lib_reference');
        Exit;
    End;

    { Position the newly placed component. }
    CompLoc := Point(MilsToCoord(X), MilsToCoord(Y));
    Try Comp.Location := CompLoc; Except End;

    { Apply 90-degree rotations. }
    RotCount := 0;
    If Rotation = 90 Then RotCount := 1
    Else If Rotation = 180 Then RotCount := 2
    Else If Rotation = 270 Then RotCount := 3;
    For I := 1 To RotCount Do
        Try Comp.RotateBy90(CompLoc); Except End;

    { Override designator if caller supplied one. }
    If DesigStr <> '' Then
        Try Comp.Designator.Text := DesigStr; Except End;

    { Override footprint model if caller supplied one. }
    If FootprintStr <> '' Then
        Try Comp.CurrentFootprintModelName := FootprintStr; Except End;

    SchRegisterObject(SchDoc, Comp);
    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

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

    { ISch_ParameterSet is the proper directive interface — a group of
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ Place a net label at coordinates on active schematic                        }
{ Params: text, x, y, orientation (0/1/2/3)                                  }
{..............................................................................}

Function Gen_PlaceNetLabel(Params : String; RequestId : String) : String;
Var
    Text : String;
    X, Y, Orientation : Integer;
    SchDoc : ISch_Document;
    NetLabel : ISch_NetLabel;
Begin
    Text := ExtractJsonValue(Params, 'text');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Orientation := StrToIntDef(ExtractJsonValue(Params, 'orientation'), 0);

    If Text = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text parameter is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    NetLabel := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
    If NetLabel = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create net label');
        Exit;
    End;

    NetLabel.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    NetLabel.Text := Text;
    NetLabel.Orientation := Orientation;
    NetLabel.Color := 0;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(NetLabel);
    SchRegisterObject(SchDoc, NetLabel);
    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"text":"' + EscapeJsonString(Text) +
        '","x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    Text, StyleStr : String;
    X, Y : Integer;
    SchDoc : ISch_Document;
    PowerObj : ISch_PowerObject;
Begin
    Text := ExtractJsonValue(Params, 'text');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    StyleStr := ExtractJsonValue(Params, 'style');

    If Text = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text parameter is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    PowerObj := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
    If PowerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create power port');
        Exit;
    End;

    PowerObj.Location := Point(MilsToCoord(X), MilsToCoord(Y));
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

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(PowerObj);
    SchRegisterObject(SchDoc, PowerObj);
    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

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
    SchDoc.ClearSelection;

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
    SchServer.ProcessControl.PostProcess(SchDoc, '');

    // Copy selected to clipboard
    If MatchCount > 0 Then
        RunProcess('Sch:CopyToClipboard');

    // Clear selection after copy
    SchDoc.ClearSelection;
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

        // PCB count — active doc only
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
            // Don't force-open — that creates free documents. Skip
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
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
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
    Board := PCBServer.GetCurrentPCBBoard;
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

        // Unit system — ISch_Document.UnitSystem returns a TUnitSystem enum
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

    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    ImagePath := StringReplace(ImagePath, '\\', '\', -1);
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
    SchServer.ProcessControl.PostProcess(SchDoc, '');
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
    NewLibrary := StringReplace(NewLibrary, '\\', '\', -1);

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
                If NewLibrary <> '' Then
                    Comp.SourceLibraryName := NewLibrary;
                SchServer.ProcessControl.PostProcess(SchDoc, '');
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
{ Command Handler - must be at end                                            }
{..............................................................................}

Function HandleGenericCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'query_objects':    Result := Gen_QueryObjects(Params, RequestId);
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
        'highlight_net':    Result := Gen_HighlightNet(Params, RequestId);
        'clear_highlights': Result := Gen_ClearHighlights(RequestId);
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
        'place_net_label':  Result := Gen_PlaceNetLabel(Params, RequestId);
        'place_port':       Result := Gen_PlacePort(Params, RequestId);
        'place_power_port': Result := Gen_PlacePowerPort(Params, RequestId);
        'get_sheet_parameters': Result := Gen_GetSheetParameters(Params, RequestId);
        'copy_objects':     Result := Gen_CopyObjects(Params, RequestId);
        'get_object_count': Result := Gen_GetObjectCount(Params, RequestId);
        'place_no_erc':     Result := Gen_PlaceNoERC(Params, RequestId);
        'place_junction':   Result := Gen_PlaceJunction(Params, RequestId);
        'get_document_info': Result := Gen_GetDocumentInfo(Params, RequestId);
        'set_grid':         Result := Gen_SetGrid(Params, RequestId);
        'set_sch_units':    Result := Gen_SetSchUnits(Params, RequestId);
        'place_image':      Result := Gen_PlaceImage(Params, RequestId);
        'replace_component': Result := Gen_ReplaceComponent(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown generic action: ' + Action);
    End;
End;
