{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ PCBGeneric.pas - PCB object primitives for the Altium integration bridge                  }
{ Parallel to Generic.pas but for PCBServer / IPCB_* objects.               }
{..............................................................................}

Function ObjectTypeFromStringPCB(TypeStr : String) : Integer;
Begin
    Result := -1;
    If TypeStr = 'eTrackObject'         Then Result := eTrackObject
    Else If TypeStr = 'ePadObject'      Then Result := ePadObject
    Else If TypeStr = 'eViaObject'      Then Result := eViaObject
    Else If TypeStr = 'eComponentObject' Then Result := eComponentObject
    Else If TypeStr = 'eArcObject'      Then Result := eArcObject
    Else If TypeStr = 'eFillObject'     Then Result := eFillObject
    Else If TypeStr = 'eTextObject'     Then Result := eTextObject
    Else If TypeStr = 'ePolyObject'     Then Result := ePolyObject
    Else If TypeStr = 'eRegionObject'   Then Result := eRegionObject
    Else If TypeStr = 'eRuleObject'     Then Result := eRuleObject
    Else If TypeStr = 'eDimensionObject' Then Result := eDimensionObject;
End;

{..............................................................................}
{ PCB Property Getter — late-bound, returns '' on unsupported properties     }
{..............................................................................}

Function GetPCBProperty(Obj : IPCB_Primitive; PropName : String) : String;
Begin
    Result := '';
    Try
        If PropName = 'ObjectId'    Then Result := IntToStr(Obj.ObjectId)
        Else If PropName = 'X'      Then Result := IntToStr(CoordToMils(Obj.x))
        Else If PropName = 'Y'      Then Result := IntToStr(CoordToMils(Obj.y))
        Else If PropName = 'X1'     Then Result := IntToStr(CoordToMils(Obj.x1))
        Else If PropName = 'Y1'     Then Result := IntToStr(CoordToMils(Obj.y1))
        Else If PropName = 'X2'     Then Result := IntToStr(CoordToMils(Obj.x2))
        Else If PropName = 'Y2'     Then Result := IntToStr(CoordToMils(Obj.y2))
        Else If PropName = 'Layer'  Then Result := GetLayerString(Obj.Layer)
        Else If PropName = 'Net'    Then
        Begin
            If Obj.Net <> Nil Then Result := Obj.Net.Name Else Result := '';
        End
        Else If PropName = 'Width'     Then Result := IntToStr(CoordToMils(Obj.Width))
        Else If PropName = 'Name'      Then Result := Obj.Name
        Else If PropName = 'Rotation'  Then Result := FloatToStr(Obj.Rotation)
        Else If PropName = 'HoleSize'  Then Result := IntToStr(CoordToMils(Obj.HoleSize))
        Else If PropName = 'TopXSize'  Then Result := IntToStr(CoordToMils(Obj.TopXSize))
        Else If PropName = 'TopYSize'  Then Result := IntToStr(CoordToMils(Obj.TopYSize))
        Else If PropName = 'TopShape'  Then Result := IntToStr(Obj.TopShape)
        Else If PropName = 'Size'      Then Result := IntToStr(CoordToMils(Obj.Size))
        Else If PropName = 'Pattern'   Then Result := Obj.Pattern
        Else If PropName = 'SourceDesignator' Then Result := Obj.SourceDesignator
        Else If PropName = 'XCenter'   Then Result := IntToStr(CoordToMils(Obj.XCenter))
        Else If PropName = 'YCenter'   Then Result := IntToStr(CoordToMils(Obj.YCenter))
        Else If PropName = 'Radius'    Then Result := IntToStr(CoordToMils(Obj.Radius))
        Else If PropName = 'StartAngle' Then Result := FloatToStr(Obj.StartAngle)
        Else If PropName = 'EndAngle'  Then Result := FloatToStr(Obj.EndAngle)
        Else If PropName = 'Text'      Then Result := Obj.Text
        Else If PropName = 'Descriptor' Then Result := Obj.Descriptor
        Else If PropName = 'Selected'  Then Result := BoolToJsonStr(Obj.Selected);
    Except
        Result := '';
    End;
End;

{..............................................................................}
{ PCB Property Setter                                                        }
{..............................................................................}

Procedure SetPCBProperty(Obj : IPCB_Primitive; PropName : String; Value : String);
Begin
    Try
        If PropName = 'X'       Then Obj.x := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Y'  Then Obj.y := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'X1' Then Obj.x1 := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Y1' Then Obj.y1 := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'X2' Then Obj.x2 := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Y2' Then Obj.y2 := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Layer'    Then Obj.Layer := GetLayerFromString(Value)
        Else If PropName = 'Width'    Then Obj.Width := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Rotation' Then Obj.Rotation := StrToFloatDef(Value, 0)
        Else If PropName = 'Name'     Then Obj.Name := Value
        Else If PropName = 'Text'     Then Obj.Text := Value
        Else If PropName = 'HoleSize' Then Obj.HoleSize := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'TopXSize' Then Obj.TopXSize := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'TopYSize' Then Obj.TopYSize := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Selected' Then Obj.Selected := StrToBool(Value);
    Except
    End;
End;

{..............................................................................}
{ PCB Filter / JSON / Apply — parallel to schematic versions                 }
{..............................................................................}

Function MatchesFilterPCB(Obj : IPCB_Primitive; FilterStr : String) : Boolean;
Var
    Remaining, Condition, PropName, Expected, Actual : String;
    PipePos, EqPos : Integer;
Begin
    Result := True;
    If FilterStr = '' Then Exit;
    Remaining := FilterStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Condition := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else Begin Condition := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Condition);
        If EqPos = 0 Then Continue;
        PropName := Copy(Condition, 1, EqPos - 1);
        Expected := Copy(Condition, EqPos + 1, Length(Condition));
        Actual := GetPCBProperty(Obj, PropName);
        If Actual <> Expected Then Begin Result := False; Exit; End;
    End;
End;

Function BuildObjectJsonPCB(Obj : IPCB_Primitive; PropsStr : String) : String;
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
        Begin PropName := Copy(Remaining, 1, CommaPos - 1); Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining)); End
        Else Begin PropName := Remaining; Remaining := ''; End;
        PropValue := GetPCBProperty(Obj, PropName);
        If Not First Then Result := Result + ',';
        First := False;
        Result := Result + '"' + EscapeJsonString(PropName) + '":"' + EscapeJsonString(PropValue) + '"';
    End;
    Result := Result + '}';
End;

Procedure ApplySetPropertiesPCB(Obj : IPCB_Primitive; SetStr : String);
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
Begin
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin Assignment := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End
        Else Begin Assignment := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Assignment);
        If EqPos = 0 Then Continue;
        PropName := Copy(Assignment, 1, EqPos - 1);
        PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));
        SetPCBProperty(Obj, PropName, PropValue);
    End;
End;

{..............................................................................}
{ PCB Board iteration — query/modify/delete on active PCB                    }
{..............................................................................}

Function ProcessPCBBoardObjects(Board : IPCB_Board; ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; Var TotalMatched : Integer; Limit : Integer) : String;
Var
    Iterator : IPCB_BoardIterator;
    Obj, FoundObj : IPCB_Primitive;
    ObjJson : String;
    First : Boolean;
    MaxIter : Integer;
Begin
    Result := '';
    First := (TotalMatched = 0);

    If Mode = 'delete' Then
    Begin
        PCBServer.PreProcess;
        MaxIter := 100000;
        While MaxIter > 0 Do
        Begin
            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            FoundObj := Nil;
            Obj := Iterator.FirstPCBObject;
            While Obj <> Nil Do
            Begin
                If MatchesFilterPCB(Obj, FilterStr) Then Begin FoundObj := Obj; Break; End;
                Obj := Iterator.NextPCBObject;
            End;
            Board.BoardIterator_Destroy(Iterator);
            If FoundObj = Nil Then Break;
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                PCBM_BoardRegisteration, FoundObj.I_ObjectAddress);
            Board.RemovePCBObject(FoundObj);
            Inc(TotalMatched);
            Dec(MaxIter);
        End;
        PCBServer.PostProcess;
        Exit;
    End;

    If Mode = 'modify' Then PCBServer.PreProcess;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Obj := Iterator.FirstPCBObject;
    While Obj <> Nil Do
    Begin
        If (Limit > 0) And (TotalMatched >= Limit) Then Break;
        If MatchesFilterPCB(Obj, FilterStr) Then
        Begin
            If Mode = 'query' Then
            Begin
                ObjJson := BuildObjectJsonPCB(Obj, PropsStr);
                If Not First Then Result := Result + ',';
                First := False;
                Result := Result + ObjJson;
            End
            Else If Mode = 'modify' Then
                ApplySetPropertiesPCB(Obj, SetStr);
            Inc(TotalMatched);
        End;
        Obj := Iterator.NextPCBObject;
    End;

    Board.BoardIterator_Destroy(Iterator);
    If Mode = 'modify' Then PCBServer.PostProcess;
End;

Function ProcessActivePCBDoc(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    Board : IPCB_Board;
    TotalMatched : Integer;
    JsonItems : String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;
    TotalMatched := 0;
    JsonItems := ProcessPCBBoardObjects(Board, ObjTypeInt,
        FilterStr, PropsStr, SetStr, Mode, TotalMatched, Limit);

    If (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create') Then
    Begin
        Board.GraphicalView_ZoomRedraw;
        SaveDocByPath(Board.FileName);
    End;

    If Mode = 'query' Then
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched) + '}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched) + '}');
End;
