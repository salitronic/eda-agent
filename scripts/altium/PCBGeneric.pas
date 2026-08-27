{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
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
{ PCB Property Getter, late-bound, returns '' on unsupported properties     }
{..............................................................................}

Function GetPCBProperty(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Track : IPCB_Track;
    Arc   : IPCB_Arc;
    Pad   : IPCB_Pad;
    Via   : IPCB_Via;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Oid   : Integer;
Begin
    Result := '';
    Try
        Oid := Obj.ObjectId;
        { Base IPCB_Primitive members, valid to read on ANY primitive. }
        If PropName = 'ObjectId'        Then Result := IntToStr(Oid)
        Else If PropName = 'X'          Then Result := IntToStr(CoordToMils(Obj.x))
        Else If PropName = 'Y'          Then Result := IntToStr(CoordToMils(Obj.y))
        Else If PropName = 'Layer'      Then Result := GetLayerString(Obj.Layer)
        Else If PropName = 'Descriptor' Then Result := Obj.Descriptor
        Else If PropName = 'Selected'   Then Result := BoolToJsonStr(Obj.Selected)
        Else If PropName = 'Net'        Then
        Begin
            If Obj.Net <> Nil Then Result := Obj.Net.Name;
        End
        { Subtype members. DelphiScript resolves members against the DECLARED }
        { type, so Obj.X1 on an IPCB_Primitive is "Undeclared identifier".    }
        { Narrow to a typed local via ObjectId (no Forward casts in script).  }
        Else If PropName = 'X1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := IntToStr(CoordToMils(Track.X1)); End;
        End
        Else If PropName = 'Y1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := IntToStr(CoordToMils(Track.Y1)); End;
        End
        Else If PropName = 'X2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := IntToStr(CoordToMils(Track.X2)); End;
        End
        Else If PropName = 'Y2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := IntToStr(CoordToMils(Track.Y2)); End;
        End
        Else If PropName = 'Width' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := IntToStr(CoordToMils(Track.Width)); End
            Else If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.Width)); End;
        End
        Else If PropName = 'XCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.XCenter)); End;
        End
        Else If PropName = 'YCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.YCenter)); End;
        End
        Else If PropName = 'Radius' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.Radius)); End;
        End
        Else If PropName = 'StartAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToStr(Arc.StartAngle); End;
        End
        Else If PropName = 'EndAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToStr(Arc.EndAngle); End;
        End
        Else If PropName = 'HoleSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(CoordToMils(Pad.HoleSize)); End
            Else If Oid = eViaObject Then Begin Via := Obj; Result := IntToStr(CoordToMils(Via.HoleSize)); End;
        End
        Else If PropName = 'TopXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(CoordToMils(Pad.TopXSize)); End;
        End
        Else If PropName = 'TopYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(CoordToMils(Pad.TopYSize)); End;
        End
        Else If PropName = 'TopShape' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(Pad.TopShape); End;
        End
        Else If PropName = 'Size' Then
        Begin
            If Oid = eViaObject Then Begin Via := Obj; Result := IntToStr(CoordToMils(Via.Size)); End;
        End
        Else If PropName = 'Rotation' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := FloatToStr(Comp.Rotation); End
            Else If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToStr(Pad.Rotation); End
            Else If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToStr(Txt.Rotation); End;
        End
        Else If PropName = 'Pattern' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Pattern; End;
        End
        Else If PropName = 'SourceDesignator' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceDesignator; End;
        End
        Else If PropName = 'Name' Then
        Begin
            { Component Name is an IPCB_Text; return its .Text, not the object }
            { (Dispatch->OleStr otherwise crashed EscapeJsonString via modal). }
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Name.Text; End;
        End
        Else If (PropName = 'Designator') Or (PropName = 'Designator.Text') Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Name.Text; End;
        End
        Else If (PropName = 'Comment') Or (PropName = 'Comment.Text') Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Comment.Text; End;
        End
        Else If PropName = 'Text' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := Txt.Text; End;
        End;
    Except
        Result := '';
    End;
End;

{..............................................................................}
{ PCB Property Setter                                                        }
{..............................................................................}

{ A caller-supplied layer name reaching a primitive through obj_modify.        }
{ GetLayerFromString answered eTopLayer for every name it did not know, so     }
{ set="Layer=Internal Plane 1" MOVED the primitive to the top copper layer.    }
{ SetPCBProperty has no error channel, so an unresolvable name is left         }
{ UNAPPLIED here; ProcessActivePCBDoc rejects the whole call up front via      }
{ UnresolvedLayerAssignment, before any object has been touched.               }

Procedure SetPrimitiveLayerByName(Obj : IPCB_Primitive; Value : String);
Var
    Lyr : TLayer;
Begin
    Lyr := ResolveLayerId(GetPCBBoardAnywhere, Value);
    If Lyr <> eNoLayer Then Obj.Layer := Lyr;
End;

Procedure SetPCBProperty(Obj : IPCB_Primitive; PropName : String; Value : String);
Var
    Track : IPCB_Track;
    Pad   : IPCB_Pad;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Oid   : Integer;
Begin
    Try
        Oid := Obj.ObjectId;
        { Base members, settable on any primitive. }
        If PropName = 'X'             Then Obj.x := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Y'        Then Obj.y := MilsToCoord(StrToIntDef(Value, 0))
        Else If PropName = 'Layer'    Then SetPrimitiveLayerByName(Obj, Value)
        Else If PropName = 'Selected' Then Obj.Selected := StrToBool(Value)
        { Subtype members: narrow to a typed local via ObjectId first. }
        Else If PropName = 'X1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.X1 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Y1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Y1 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'X2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.X2 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Y2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Y2 := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Width' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Width := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Rotation' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Comp.Rotation := StrToFloatDef(Value, 0); End
            Else If Oid = ePadObject Then Begin Pad := Obj; Pad.Rotation := StrToFloatDef(Value, 0); End;
        End
        Else If PropName = 'HoleSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.HoleSize := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'TopXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.TopXSize := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'TopYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.TopYSize := MilsToCoord(StrToIntDef(Value, 0)); End;
        End
        Else If PropName = 'Text' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Txt.Text := Value; End;
        End;
    Except
    End;
End;

{..............................................................................}
{ PCB Filter / JSON / Apply, parallel to schematic versions                 }
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
{ PCB Board iteration, query/modify/delete on active PCB                    }
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

{ The first Layer= assignment in a set string this board cannot resolve, or    }
{ '' when every one of them resolves. Checked before the iteration starts so   }
{ a bad name costs nothing rather than relocating half the matched objects.    }

Function UnresolvedLayerAssignment(Board : IPCB_Board; SetStr : String) : String;
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin Assignment := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End
        Else Begin Assignment := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Assignment);
        If EqPos > 0 Then
        Begin
            PropName := UpperCase(Trim(Copy(Assignment, 1, EqPos - 1)));
            PropValue := Trim(Copy(Assignment, EqPos + 1, Length(Assignment)));
            If (PropName = 'LAYER') And (PropValue <> '') Then
            Begin
                If ResolveLayerId(Board, PropValue) = eNoLayer Then
                Begin
                    Result := PropValue;
                    Exit;
                End;
            End;
        End;
    End;
End;

Function ProcessActivePCBDoc(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    Board : IPCB_Board;
    TotalMatched : Integer;
    JsonItems, BadLayer : String;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    If SetStr <> '' Then
    Begin
        BadLayer := UnresolvedLayerAssignment(Board, SetStr);
        If BadLayer <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
                'Unknown layer name: ' + BadLayer + '. ' + BoardLayerNamesHint(Board));
            Exit;
        End;
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
