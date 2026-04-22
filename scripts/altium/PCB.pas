{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ PCB.pas - PCB-specific operations for the Altium integration bridge                        }
{ Provides high-level PCB commands: net classes, design rules, DRC,           }
{ component placement, trace lengths, layer stackup, board outline, etc.      }
{..............................................................................}

{..............................................................................}
{ Helper: Find a net object by name on the given board.                       }
{ Returns Nil if not found.                                                   }
{..............................................................................}

Function FindNetByName(Board : IPCB_Board; NetName : String) : IPCB_Net;
Var
    Iterator : IPCB_BoardIterator;
    Net : IPCB_Net;
Begin
    Result := Nil;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Net := Iterator.FirstPCBObject;
    While Net <> Nil Do
    Begin
        If Net.Name = NetName Then
        Begin
            Result := Net;
            Break;
        End;
        Net := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);
End;

{..............................................................................}
{ PCB_GetNets - Get all unique net names from the board                       }
{..............................................................................}

Function PCB_GetNets(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Net : IPCB_Net;
    JsonItems : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Net := Iterator.FirstPCBObject;
    While Net <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '"' + EscapeJsonString(Net.Name) + '"';
        Inc(Count);
        Net := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"nets":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_GetNetClasses - Get all net classes with their member nets              }
{..............................................................................}

Function PCB_GetNetClasses(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    ObjClass : IPCB_ObjectClass;
    JsonItems : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    { MemberCount / MemberName[] are not exposed on IPCB_ObjectClass in         }
    { DelphiScript — they are compile-time undeclared. Return class metadata   }
    { only; callers that need per-member resolution can iterate nets and       }
    { group them via the parent class on each net.                              }
    Iterator := Board.BoardIterator_Create;
    Iterator.SetState_FilterAll;
    Iterator.AddFilter_ObjectSet(MkSet(eClassObject));

    ObjClass := Iterator.FirstPCBObject;
    While ObjClass <> Nil Do
    Begin
        If ObjClass.MemberKind = eClassMemberKind_Net Then
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;

            JsonItems := JsonItems + '{"name":"' + EscapeJsonString(ObjClass.Name) + '",'
                + '"super_class":' + BoolToJsonStr(ObjClass.SuperClass) + '}';
            Inc(Count);
        End;
        ObjClass := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"net_classes":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_CreateNetClass - Create a net class from a list of net names            }
{ Params: name=<class_name>, nets=<comma-separated net names>                }
{..............................................................................}

Function PCB_CreateNetClass(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    ClassName, NetsStr, NetName, Remaining : String;
    NetClass : IPCB_ObjectClass;
    Iterator : IPCB_BoardIterator;
    ExistingClass : IPCB_ObjectClass;
    ClassExists : Boolean;
    CommaPos, AddedCount : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    ClassName := ExtractJsonValue(Params, 'name');
    NetsStr := ExtractJsonValue(Params, 'nets');

    If ClassName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "name" parameter');
        Exit;
    End;

    // Check for existing class with same name
    ClassExists := False;
    Iterator := Board.BoardIterator_Create;
    Iterator.SetState_FilterAll;
    Iterator.AddFilter_ObjectSet(MkSet(eClassObject));
    ExistingClass := Iterator.FirstPCBObject;
    While ExistingClass <> Nil Do
    Begin
        If (ExistingClass.MemberKind = eClassMemberKind_Net) And
           (ExistingClass.Name = ClassName) Then
        Begin
            ClassExists := True;
            NetClass := ExistingClass;
            Break;
        End;
        ExistingClass := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    // Create new class if it doesn't exist
    If Not ClassExists Then
    Begin
        PCBServer.PreProcess;
        NetClass := PCBServer.PCBClassFactoryByClassMember(eClassMemberKind_Net);
        NetClass.SuperClass := False;
        NetClass.Name := ClassName;
        Board.AddPCBObject(NetClass);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, NetClass.I_ObjectAddress);
        PCBServer.PostProcess;
    End;

    // Add nets to the class
    AddedCount := 0;
    Remaining := NetsStr;
    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            NetName := Copy(Remaining, 1, CommaPos - 1);
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else
        Begin
            NetName := Remaining;
            Remaining := '';
        End;
        If NetName <> '' Then
        Begin
            PCBServer.PreProcess;
            NetClass.AddMemberByName(NetName);
            PCBServer.PostProcess;
            Inc(AddedCount);
        End;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"class_name":"' + EscapeJsonString(ClassName) + '",'
        + '"class_created":' + BoolToJsonStr(Not ClassExists) + ','
        + '"nets_added":' + IntToStr(AddedCount) + '}');
End;

{..............................................................................}
{ PCB_GetDesignRules - Get all design rules                                   }
{..............................................................................}

Function PCB_GetDesignRules(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Rule : IPCB_Rule;
    JsonItems, RuleTypeStr : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Rule := Iterator.FirstPCBObject;
    While Rule <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        // Get rule type as string
        Try
            RuleTypeStr := IntToStr(Rule.RuleKind);
        Except
            RuleTypeStr := 'unknown';
        End;

        JsonItems := JsonItems + '{"name":"' + EscapeJsonString(Rule.Name) + '",'
            + '"rule_kind":' + RuleTypeStr + ','
            + '"enabled":' + BoolToJsonStr(Rule.Enabled) + ','
            + '"priority":' + IntToStr(Rule.Priority) + ','
            + '"scope_1":"' + EscapeJsonString(Rule.Scope1Expression) + '",'
            + '"scope_2":"' + EscapeJsonString(Rule.Scope2Expression) + '",'
            + '"comment":"' + EscapeJsonString(Rule.Comment) + '",'
            + '"descriptor":"' + EscapeJsonString(Rule.Descriptor) + '"}';
        Inc(Count);
        Rule := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"rules":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_FindRuleByName - Helper to locate an IPCB_Rule by its Name                }
{..............................................................................}

Function PCB_FindRuleByName(Board : IPCB_Board; RuleName : String) : IPCB_Rule;
Var
    Iterator : IPCB_BoardIterator;
    Rule : IPCB_Rule;
Begin
    Result := Nil;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Rule := Iterator.FirstPCBObject;
    While Rule <> Nil Do
    Begin
        If Rule.Name = RuleName Then
        Begin
            Result := Rule;
            Break;
        End;
        Rule := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);
End;

{..............................................................................}
{ PCB_GetRuleProperties - Read kind-specific properties of a design rule.       }
{                                                                               }
{ IPCB_Rule is declared with a union of all rule-kind properties (Gap for       }
{ clearance, MinWidth[L] for width, MinLimit / MaxLimit / MinHoleSize /         }
{ MaxHoleSize / Impedance, ...). Reading a property that doesn't apply to the   }
{ rule's actual kind raises at runtime; Try/Except skips those silently.        }
{..............................................................................}

Function PCB_GetRuleProperties(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Rule : IPCB_Rule;
    RuleName, JsonProps : String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    RuleName := ExtractJsonValue(Params, 'name');
    If RuleName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', '"name" parameter is required');
        Exit;
    End;

    Rule := PCB_FindRuleByName(Board, RuleName);
    If Rule = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Rule not found: ' + RuleName);
        Exit;
    End;

    JsonProps := '';

    { Clearance rules — Gap is the minimum clearance in internal coord units. }
    Try JsonProps := JsonProps + ',"gap_mils":' + IntToStr(CoordToMils(Rule.Gap)); Except End;

    { Width rules — per-layer min/max/favored. Emit the top-layer value as a  }
    { representative; full per-layer readback is rarely useful in practice.   }
    Try JsonProps := JsonProps + ',"min_width_mils":' + IntToStr(CoordToMils(Rule.MinWidth[eTopLayer])); Except End;
    Try JsonProps := JsonProps + ',"max_width_mils":' + IntToStr(CoordToMils(Rule.MaxWidth[eTopLayer])); Except End;
    Try JsonProps := JsonProps + ',"preferred_width_mils":' + IntToStr(CoordToMils(Rule.FavoredWidth[eTopLayer])); Except End;

    { Generic MinLimit / MaxLimit — hole size, via size, flight time limit.   }
    Try JsonProps := JsonProps + ',"min_limit_mils":' + IntToStr(CoordToMils(Rule.MinLimit)); Except End;
    Try JsonProps := JsonProps + ',"max_limit_mils":' + IntToStr(CoordToMils(Rule.MaxLimit)); Except End;

    { Via size — hole and diameter. }
    Try JsonProps := JsonProps + ',"min_hole_size_mils":' + IntToStr(CoordToMils(Rule.MinHoleSize)); Except End;
    Try JsonProps := JsonProps + ',"max_hole_size_mils":' + IntToStr(CoordToMils(Rule.MaxHoleSize)); Except End;
    Try JsonProps := JsonProps + ',"preferred_hole_size_mils":' + IntToStr(CoordToMils(Rule.PreferredHoleSize)); Except End;
    Try JsonProps := JsonProps + ',"min_width_via_mils":' + IntToStr(CoordToMils(Rule.MinWidth)); Except End;
    Try JsonProps := JsonProps + ',"max_width_via_mils":' + IntToStr(CoordToMils(Rule.MaxWidth)); Except End;
    Try JsonProps := JsonProps + ',"preferred_width_via_mils":' + IntToStr(CoordToMils(Rule.PreferredWidth)); Except End;

    { Parallel segment / daisy chain stub — Limit is already a coord. }
    Try JsonProps := JsonProps + ',"parallel_limit_mils":' + IntToStr(CoordToMils(Rule.ParallelLimit)); Except End;
    Try JsonProps := JsonProps + ',"parallel_gap_mils":' + IntToStr(CoordToMils(Rule.ParallelGap)); Except End;

    { Max/min impedance — no coord units, floating-point ohms.                  }
    Try JsonProps := JsonProps + ',"min_impedance":' + FloatToStr(Rule.MinImpedance); Except End;
    Try JsonProps := JsonProps + ',"max_impedance":' + FloatToStr(Rule.MaxImpedance); Except End;
    Try JsonProps := JsonProps + ',"preferred_impedance":' + FloatToStr(Rule.PreferredImpedance); Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"name":"' + EscapeJsonString(Rule.Name) + '",'
        + '"rule_kind":' + IntToStr(Rule.RuleKind) + ','
        + '"enabled":' + BoolToJsonStr(Rule.Enabled) + ','
        + '"priority":' + IntToStr(Rule.Priority)
        + JsonProps + '}');
End;

{..............................................................................}
{ PCB_SetRuleProperties - Update kind-specific properties of a design rule.     }
{ Params: name + any of the keys PCB_GetRuleProperties returns.                 }
{..............................................................................}

Function PCB_SetRuleProperties(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Rule : IPCB_Rule;
    RuleName, V : String;
    L : TLayer;
    UpdatedCount : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    RuleName := ExtractJsonValue(Params, 'name');
    If RuleName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', '"name" parameter is required');
        Exit;
    End;

    Rule := PCB_FindRuleByName(Board, RuleName);
    If Rule = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Rule not found: ' + RuleName);
        Exit;
    End;

    UpdatedCount := 0;
    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Rule.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);

        V := ExtractJsonValue(Params, 'enabled');
        If V <> '' Then
        Begin
            Try Rule.Enabled := (V = 'true') Or (V = 'True') Or (V = '1'); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'priority');
        If V <> '' Then
        Begin
            Try Rule.Priority := StrToIntDef(V, Rule.Priority); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'gap_mils');
        If V <> '' Then
        Begin
            Try Rule.Gap := MilsToCoord(StrToIntDef(V, 0)); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'min_width_mils');
        If V <> '' Then
        Begin
            For L := MinLayer To MaxLayer Do
                Try Rule.MinWidth[L] := MilsToCoord(StrToIntDef(V, 0)); Except End;
            Inc(UpdatedCount);
        End;

        V := ExtractJsonValue(Params, 'max_width_mils');
        If V <> '' Then
        Begin
            For L := MinLayer To MaxLayer Do
                Try Rule.MaxWidth[L] := MilsToCoord(StrToIntDef(V, 0)); Except End;
            Inc(UpdatedCount);
        End;

        V := ExtractJsonValue(Params, 'preferred_width_mils');
        If V <> '' Then
        Begin
            For L := MinLayer To MaxLayer Do
                Try Rule.FavoredWidth[L] := MilsToCoord(StrToIntDef(V, 0)); Except End;
            Inc(UpdatedCount);
        End;

        V := ExtractJsonValue(Params, 'min_limit_mils');
        If V <> '' Then
        Begin
            Try Rule.MinLimit := MilsToCoord(StrToIntDef(V, 0)); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'max_limit_mils');
        If V <> '' Then
        Begin
            Try Rule.MaxLimit := MilsToCoord(StrToIntDef(V, 0)); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'min_hole_size_mils');
        If V <> '' Then
        Begin
            Try Rule.MinHoleSize := MilsToCoord(StrToIntDef(V, 0)); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'max_hole_size_mils');
        If V <> '' Then
        Begin
            Try Rule.MaxHoleSize := MilsToCoord(StrToIntDef(V, 0)); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'preferred_hole_size_mils');
        If V <> '' Then
        Begin
            Try Rule.PreferredHoleSize := MilsToCoord(StrToIntDef(V, 0)); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'min_impedance');
        If V <> '' Then
        Begin
            Try Rule.MinImpedance := StrToFloatDef(V, 0); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'max_impedance');
        If V <> '' Then
        Begin
            Try Rule.MaxImpedance := StrToFloatDef(V, 0); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'preferred_impedance');
        If V <> '' Then
        Begin
            Try Rule.PreferredImpedance := StrToFloatDef(V, 0); Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'scope1');
        If V <> '' Then
        Begin
            Try Rule.Scope1Expression := V; Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'scope2');
        If V <> '' Then
        Begin
            Try Rule.Scope2Expression := V; Inc(UpdatedCount); Except End;
        End;

        V := ExtractJsonValue(Params, 'comment');
        If V <> '' Then
        Begin
            Try Rule.Comment := V; Inc(UpdatedCount); Except End;
        End;

        PCBServer.SendMessageToRobots(Rule.I_ObjectAddress, c_Broadcast,
            PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"name":"' + EscapeJsonString(Rule.Name) + '",'
        + '"properties_updated":' + IntToStr(UpdatedCount) + '}');
End;

{..............................................................................}
{ PCB_RunDRC - Run design rule check, return violation count                  }
{..............................................................................}

Function BuildViolationJson(Violation : IPCB_Violation) : String;
Var
    RuleName, P1Desc, P2Desc : String;
Begin
    Result := '{';
    Try Result := Result + '"name":"' + EscapeJsonString(Violation.Name) + '"'; Except Result := Result + '"name":""'; End;
    Try Result := Result + ',"description":"' + EscapeJsonString(Violation.Description) + '"'; Except End;
    RuleName := '';
    Try If Violation.Rule <> Nil Then RuleName := Violation.Rule.Name; Except End;
    Result := Result + ',"rule":"' + EscapeJsonString(RuleName) + '"';
    P1Desc := '';
    Try If Violation.Primitive1 <> Nil Then P1Desc := Violation.Primitive1.Detail; Except End;
    Result := Result + ',"primitive1":"' + EscapeJsonString(P1Desc) + '"';
    P2Desc := '';
    Try If Violation.Primitive2 <> Nil Then P2Desc := Violation.Primitive2.Detail; Except End;
    Result := Result + ',"primitive2":"' + EscapeJsonString(P2Desc) + '"';
    Result := Result + '}';
End;

Function PCB_RunDRC(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    ViolationCount : Integer;
    Iterator : IPCB_BoardIterator;
    Violation : IPCB_Violation;
    JsonItems : String;
    First : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    // Run DRC via RunProcess
    ResetParameters;
    RunProcess('PCB:RunDRC');

    // Count violations by iterating
    ViolationCount := 0;
    JsonItems := '';
    First := True;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eViolationObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Violation := Iterator.FirstPCBObject;
    While Violation <> Nil Do
    Begin
        Inc(ViolationCount);
        If ViolationCount <= 100 Then  // Limit detail output
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems + BuildViolationJson(Violation);
        End;
        Violation := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"violation_count":' + IntToStr(ViolationCount) + ','
        + '"violations":[' + JsonItems + ']}');
End;

{..............................................................................}
{ PCB_GetComponents - Get all components with position, rotation, layer       }
{..............................................................................}

Function PCB_GetComponents(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Comp : IPCB_Component;
    JsonItems, Designator, Footprint, LayerStr, CommentStr, SrcDesignator : String;
    First : Boolean;
    Count, HeightMils : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Comp := Iterator.FirstPCBObject;
    While Comp <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        Try Designator := Comp.Name.Text; Except Designator := ''; End;
        Try CommentStr := Comp.Comment.Text; Except CommentStr := ''; End;
        Try Footprint := Comp.Pattern; Except Footprint := ''; End;
        Try LayerStr := GetLayerString(Comp.Layer); Except LayerStr := 'Unknown'; End;
        Try SrcDesignator := Comp.SourceDesignator; Except SrcDesignator := ''; End;
        Try HeightMils := CoordToMils(Comp.Height); Except HeightMils := 0; End;

        // Note: IPCB_Component.Locked is undeclared in DelphiScript despite
        // being documented. Try/Except does not catch compile-time undeclared
        // identifiers — assigning to Comp.Locked crashes the whole script.
        // Skipped for now; if a future build exposes it, add it back.

        JsonItems := JsonItems + '{"designator":"' + EscapeJsonString(Designator) + '",'
            + '"comment":"' + EscapeJsonString(CommentStr) + '",'
            + '"x":' + IntToStr(CoordToMils(Comp.x)) + ','
            + '"y":' + IntToStr(CoordToMils(Comp.y)) + ','
            + '"rotation":' + FloatToStr(Comp.Rotation) + ','
            + '"layer":"' + EscapeJsonString(LayerStr) + '",'
            + '"footprint":"' + EscapeJsonString(Footprint) + '",'
            + '"source_designator":"' + EscapeJsonString(SrcDesignator) + '",'
            + '"height_mils":' + IntToStr(HeightMils) + '}';
        Inc(Count);
        Comp := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"components":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_MoveComponent - Move/rotate a component by designator                   }
{ Params: designator=<ref>, x=<mils>, y=<mils>, rotation=<deg>              }
{..............................................................................}

Function PCB_MoveComponent(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    DesStr, XStr, YStr, RotStr : String;
    NewX, NewY : Integer;
    NewRot : Double;
    HasX, HasY, HasRot : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DesStr := ExtractJsonValue(Params, 'designator');
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    RotStr := ExtractJsonValue(Params, 'rotation');

    If DesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "designator" parameter');
        Exit;
    End;

    // Find component by designator
    Comp := Board.GetPcbComponentByRefDes(DesStr);
    If Comp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + DesStr);
        Exit;
    End;

    HasX := (XStr <> '');
    HasY := (YStr <> '');
    HasRot := (RotStr <> '');

    If HasX Then NewX := StrToIntDef(XStr, 0);
    If HasY Then NewY := StrToIntDef(YStr, 0);
    If HasRot Then NewRot := StrToFloatDef(RotStr, 0);

    // Modify the component
    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);

        If HasX Then Comp.x := MilsToCoord(NewX);
        If HasY Then Comp.y := MilsToCoord(NewY);
        If HasRot Then Comp.Rotation := NewRot;

        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
            PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(DesStr) + '",'
        + '"x":' + IntToStr(CoordToMils(Comp.x)) + ','
        + '"y":' + IntToStr(CoordToMils(Comp.y)) + ','
        + '"rotation":' + FloatToStr(Comp.Rotation) + '}');
End;

{..............................................................................}
{ PCB_BatchMoveComponents - Move/rotate many components in ONE IPC call.      }
{ Param 'moves' is a pipe-separated list; each entry is 4 comma-separated     }
{ fields: designator,x,y,rotation. Empty field = leave that property          }
{ unchanged. Single PreProcess/PostProcess and a single save for the whole    }
{ batch, so N moves cost roughly 1x the overhead of one move.                 }
{..............................................................................}

Function PCB_BatchMoveComponents(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    MovesStr, MoveStr, Remaining : String;
    PipePos, CommaPos, Applied, Failed, I : Integer;
    FieldVals : Array[0..3] Of String;
    Desig, XStr, YStr, RotStr : String;
    NewX, NewY : Integer;
    NewRot : Double;
    HasX, HasY, HasRot : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    MovesStr := ExtractJsonValue(Params, 'moves');
    If MovesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'moves parameter required');
        Exit;
    End;

    Applied := 0;
    Failed := 0;
    Remaining := MovesStr;

    PCBServer.PreProcess;
    Try
        While Length(Remaining) > 0 Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then
            Begin
                MoveStr := Remaining;
                Remaining := '';
            End
            Else
            Begin
                MoveStr := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End;

            If MoveStr = '' Then Continue;

            For I := 0 To 3 Do FieldVals[I] := '';
            I := 0;
            While (MoveStr <> '') And (I <= 3) Do
            Begin
                CommaPos := Pos(',', MoveStr);
                If CommaPos = 0 Then
                Begin
                    FieldVals[I] := MoveStr;
                    MoveStr := '';
                End
                Else
                Begin
                    FieldVals[I] := Copy(MoveStr, 1, CommaPos - 1);
                    MoveStr := Copy(MoveStr, CommaPos + 1, Length(MoveStr));
                End;
                I := I + 1;
            End;

            Desig := FieldVals[0];
            XStr := FieldVals[1];
            YStr := FieldVals[2];
            RotStr := FieldVals[3];

            If Desig = '' Then
            Begin
                Failed := Failed + 1;
                Continue;
            End;

            Comp := Board.GetPcbComponentByRefDes(Desig);
            If Comp = Nil Then
            Begin
                Failed := Failed + 1;
                Continue;
            End;

            HasX := (XStr <> '');
            HasY := (YStr <> '');
            HasRot := (RotStr <> '');

            If HasX Then NewX := StrToIntDef(XStr, 0);
            If HasY Then NewY := StrToIntDef(YStr, 0);
            If HasRot Then NewRot := StrToFloatDef(RotStr, 0);

            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                PCBM_BeginModify, c_NoEventData);

            If HasX Then Comp.x := MilsToCoord(NewX);
            If HasY Then Comp.y := MilsToCoord(NewY);
            If HasRot Then Comp.Rotation := NewRot;

            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                PCBM_EndModify, c_NoEventData);

            Applied := Applied + 1;
        End;

        { One broadcast at end of batch kicks the connectivity/rules engines  }
        { to refresh once instead of N times.                                  }
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"moves_applied":' + IntToStr(Applied) + ','
        + '"failed":' + IntToStr(Failed) + '}');
End;

{..............................................................................}
{ PCB_GetTraceLengths - Sum track segment lengths per net                     }
{..............................................................................}

Function PCB_GetTraceLengths(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Obj : IPCB_Primitive;
    NetName, FilterNet : String;
    JsonItems : String;
    First : Boolean;
    Count : Integer;
    // Use parallel arrays for net names and lengths
    NetNames : Array[0..999] Of String;
    NetLengths : Array[0..999] Of Double;
    NetCount, I, FoundIdx : Integer;
    SegLen, DX, DY, ArcAngle, RadiusMils : Double;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    FilterNet := ExtractJsonValue(Params, 'net');
    NetCount := 0;

    // Include tracks AND arcs — routed nets use both. Arc length =
    // radius * sweepAngle(radians). AnglesRange is SweepAngle for
    // IPCB_Arc (StartAngle/EndAngle also available but sweep is the
    // pre-computed arc extent in degrees).
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Obj := Iterator.FirstPCBObject;
    While Obj <> Nil Do
    Begin
        NetName := '';
        Try
            If Obj.Net <> Nil Then NetName := Obj.Net.Name;
        Except End;

        // Filter by net name if specified
        If (FilterNet <> '') And (NetName <> FilterNet) Then
        Begin
            Obj := Iterator.NextPCBObject;
            Continue;
        End;

        SegLen := 0;
        If Obj.ObjectId = eTrackObject Then
        Begin
            Track := Obj;
            DX := CoordToMils(Track.x2) - CoordToMils(Track.x1);
            DY := CoordToMils(Track.y2) - CoordToMils(Track.y1);
            SegLen := Sqrt(DX * DX + DY * DY);
        End
        Else If Obj.ObjectId = eArcObject Then
        Begin
            Arc := Obj;
            Try
                RadiusMils := CoordToMils(Arc.Radius);
                ArcAngle := Arc.EndAngle - Arc.StartAngle;
                If ArcAngle < 0 Then ArcAngle := ArcAngle + 360;
                SegLen := RadiusMils * ArcAngle * 3.14159265358979 / 180.0;
            Except SegLen := 0; End;
        End;

        // Find or add net in array
        FoundIdx := -1;
        For I := 0 To NetCount - 1 Do
        Begin
            If NetNames[I] = NetName Then
            Begin
                FoundIdx := I;
                Break;
            End;
        End;

        If FoundIdx >= 0 Then
            NetLengths[FoundIdx] := NetLengths[FoundIdx] + SegLen
        Else If NetCount < 1000 Then
        Begin
            NetNames[NetCount] := NetName;
            NetLengths[NetCount] := SegLen;
            Inc(NetCount);
        End;

        Obj := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    // Build JSON output
    JsonItems := '';
    First := True;
    For I := 0 To NetCount - 1 Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '{"net":"' + EscapeJsonString(NetNames[I]) + '",'
            + '"length_mils":' + FloatToStr(NetLengths[I]) + '}';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"trace_lengths":[' + JsonItems + '],"net_count":' + IntToStr(NetCount) + '}');
End;

{..............................................................................}
{ PCB_GetLayerStackup - Get full layer stack info                             }
{..............................................................................}

Function PCB_GetLayerStackup(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    JsonItems, LayerName, DielectricType : String;
    First : Boolean;
    Count : Integer;
    CopperThickMils, DielectricHeightMils, DielectricConst : Double;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerStack := Board.LayerStack_V7;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    LayerObj := LayerStack.FirstLayer;
    While LayerObj <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        Try LayerName := LayerObj.Name; Except LayerName := 'Unknown'; End;

        // Copper thickness
        CopperThickMils := 0;
        Try CopperThickMils := LayerObj.CopperThickness / 10000; Except End;

        // Dielectric info
        DielectricType := 'none';
        DielectricHeightMils := 0;
        DielectricConst := 0;
        Try
            If LayerObj.Dielectric.DielectricType <> eNoDielectric Then
            Begin
                If LayerObj.Dielectric.DielectricType = eCore Then DielectricType := 'Core'
                Else If LayerObj.Dielectric.DielectricType = ePrePreg Then DielectricType := 'PrePreg'
                Else If LayerObj.Dielectric.DielectricType = eSurfaceMaterial Then DielectricType := 'SurfaceMaterial'
                Else DielectricType := 'Other';
                DielectricHeightMils := LayerObj.Dielectric.DielectricHeight / 10000;
                DielectricConst := LayerObj.Dielectric.DielectricConstant;
            End;
        Except
        End;

        JsonItems := JsonItems + '{"name":"' + EscapeJsonString(LayerName) + '",'
            + '"order":' + IntToStr(Count + 1) + ','
            + '"copper_thickness_mils":' + FloatToStr(CopperThickMils) + ','
            + '"dielectric_type":"' + EscapeJsonString(DielectricType) + '",'
            + '"dielectric_height_mils":' + FloatToStr(DielectricHeightMils) + ','
            + '"dielectric_constant":' + FloatToStr(DielectricConst) + '}';
        Inc(Count);
        LayerObj := LayerStack.NextLayer(LayerObj);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"layers":[' + JsonItems + '],"layer_count":' + IntToStr(Count) + ','
        + '"board_name":"' + EscapeJsonString(ExtractFileName(Board.FileName)) + '"}');
End;

{..............................................................................}
{ PCB_AddLayer - Insert a copper layer (MidLayer1..30 / InternalPlane1..16)   }
{ into the stack via IPCB_LayerStack.InsertLayer.                             }
{ Params: layer (e.g. MidLayer1)                                              }
{..............................................................................}

Function PCB_AddLayer(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerName : String;
    TargetLayer : TLayer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerName := ExtractJsonValue(Params, 'layer');
    If LayerName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'layer required (e.g. MidLayer1, InternalPlane1)');
        Exit;
    End;

    TargetLayer := GetLayerFromString(LayerName);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
            'Unknown layer name: ' + LayerName);
        Exit;
    End;

    LayerStack := Board.LayerStack_V7;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Try LayerStack.InsertLayer(TargetLayer); Except End;
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"layer":"' + EscapeJsonString(LayerName) + '"}');
End;

{..............................................................................}
{ PCB_RemoveLayer - Remove a copper layer from the stack.                     }
{ Params: layer (e.g. MidLayer1)                                              }
{..............................................................................}

Function PCB_RemoveLayer(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    LayerName : String;
    TargetLayer : TLayer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerName := ExtractJsonValue(Params, 'layer');
    If LayerName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'layer required');
        Exit;
    End;

    TargetLayer := GetLayerFromString(LayerName);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
            'Unknown layer name: ' + LayerName);
        Exit;
    End;

    LayerStack := Board.LayerStack_V7;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;

    LayerObj := LayerStack.LayerObject_V7[TargetLayer];
    If LayerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_IN_STACK',
            'Layer ' + LayerName + ' is not present in the current stack');
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Try LayerStack.RemoveFromStack(LayerObj); Except End;
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"layer":"' + EscapeJsonString(LayerName) + '"}');
End;

{..............................................................................}
{ PCB_ModifyLayer - Change copper thickness, layer name, and/or dielectric    }
{ properties on an existing layer.                                            }
{ Params: layer, name, copper_thickness_mils, dielectric_type (none/core/     }
{         prepreg/surface), dielectric_height_mils, dielectric_constant,     }
{         dielectric_material                                                  }
{..............................................................................}

Function PCB_ModifyLayer(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    LayerName, NewName, TypeStr, Material : String;
    ThickStr, HeightStr, ConstStr : String;
    TargetLayer : TLayer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerName := ExtractJsonValue(Params, 'layer');
    If LayerName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'layer required');
        Exit;
    End;

    TargetLayer := GetLayerFromString(LayerName);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
            'Unknown layer name: ' + LayerName);
        Exit;
    End;

    LayerStack := Board.LayerStack_V7;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;

    LayerObj := LayerStack.LayerObject_V7[TargetLayer];
    If LayerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_IN_STACK',
            'Layer ' + LayerName + ' is not present in the current stack');
        Exit;
    End;

    NewName := ExtractJsonValue(Params, 'name');
    ThickStr := ExtractJsonValue(Params, 'copper_thickness_mils');
    TypeStr := LowerCase(ExtractJsonValue(Params, 'dielectric_type'));
    HeightStr := ExtractJsonValue(Params, 'dielectric_height_mils');
    ConstStr := ExtractJsonValue(Params, 'dielectric_constant');
    Material := ExtractJsonValue(Params, 'dielectric_material');

    PCBServer.PreProcess;
    Try
        If NewName <> '' Then
            Try LayerObj.Name := NewName; Except End;
        If ThickStr <> '' Then
            Try LayerObj.CopperThickness := MilsToCoord(StrToIntDef(ThickStr, 0)); Except End;

        If TypeStr = 'none' Then
            Try LayerObj.Dielectric.DielectricType := eNoDielectric; Except End
        Else If TypeStr = 'core' Then
            Try LayerObj.Dielectric.DielectricType := eCore; Except End
        Else If TypeStr = 'prepreg' Then
            Try LayerObj.Dielectric.DielectricType := ePrePreg; Except End
        Else If TypeStr = 'surface' Then
            Try LayerObj.Dielectric.DielectricType := eSurfaceMaterial; Except End;

        If HeightStr <> '' Then
            Try LayerObj.Dielectric.DielectricHeight := MilsToCoord(StrToIntDef(HeightStr, 0)); Except End;
        If ConstStr <> '' Then
            Try LayerObj.Dielectric.DielectricConstant := StrToFloatDef(ConstStr, 1.0); Except End;
        If Material <> '' Then
            Try LayerObj.Dielectric.DielectricMaterial := Material; Except End;

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"layer":"' + EscapeJsonString(LayerName) + '"}');
End;

{..............................................................................}
{ PCB_GetBoardOutline - Get board outline vertices                            }
{..............................................................................}

Function PCB_GetBoardOutline(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Outline : IPCB_BoardOutline;
    Seg : TPolySegment;
    BR : TCoordRect;
    JsonItems, SegKind : String;
    First : Boolean;
    I : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Outline := Board.BoardOutline;
    If Outline = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_OUTLINE', 'Board has no outline defined');
        Exit;
    End;

    Try
        Outline.Invalidate;
        Outline.Rebuild;
        Outline.Validate;
    Except
    End;

    // Bounding rectangle
    BR := Outline.BoundingRectangle;

    // Iterate vertices
    JsonItems := '';
    First := True;
    For I := 0 To Outline.PointCount - 1 Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        If Outline.Segments[I].Kind = ePolySegmentLine Then
            SegKind := 'line'
        Else
            SegKind := 'arc';

        JsonItems := JsonItems + '{"index":' + IntToStr(I) + ','
            + '"kind":"' + SegKind + '",'
            + '"x":' + IntToStr(CoordToMils(Outline.Segments[I].vx)) + ','
            + '"y":' + IntToStr(CoordToMils(Outline.Segments[I].vy));

        If Outline.Segments[I].Kind <> ePolySegmentLine Then
        Begin
            JsonItems := JsonItems + ','
                + '"cx":' + IntToStr(CoordToMils(Outline.Segments[I].cx)) + ','
                + '"cy":' + IntToStr(CoordToMils(Outline.Segments[I].cy)) + ','
                + '"angle1":' + FloatToStr(Outline.Segments[I].Angle1) + ','
                + '"angle2":' + FloatToStr(Outline.Segments[I].Angle2);
        End;

        JsonItems := JsonItems + '}';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"point_count":' + IntToStr(Outline.PointCount) + ','
        + '"vertices":[' + JsonItems + '],'
        + '"bounding_rect":{"left":' + IntToStr(CoordToMils(BR.Left))
        + ',"bottom":' + IntToStr(CoordToMils(BR.Bottom))
        + ',"right":' + IntToStr(CoordToMils(BR.Right))
        + ',"top":' + IntToStr(CoordToMils(BR.Top)) + '}}');
End;

{..............................................................................}
{ PCB_GetSelectedObjects - Get properties of currently selected PCB objects   }
{..............................................................................}

Function PCB_GetSelectedObjects(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Obj : IPCB_Primitive;
    PropsStr, JsonItems, ObjTypeStr, NetName, LayerName : String;
    First : Boolean;
    I, Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    PropsStr := ExtractJsonValue(Params, 'properties');
    If PropsStr = '' Then PropsStr := 'ObjectId,X,Y,Layer,Net';

    JsonItems := '';
    First := True;
    Count := Board.SelectecObjectCount;

    For I := 0 To Count - 1 Do
    Begin
        Obj := Board.SelectecObject[I];
        If Obj = Nil Then Continue;

        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        // Build JSON using PCBGeneric helpers
        JsonItems := JsonItems + BuildObjectJsonPCB(Obj, PropsStr);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"objects":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_SetLayerVisibility - Show/hide specific layers                          }
{ Params: layer=<layer_name>, visible=<true|false>                           }
{..............................................................................}

Function PCB_SetLayerVisibility(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStr, VisibleStr : String;
    LayerID : TLayer;
    Visible : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerStr := ExtractJsonValue(Params, 'layer');
    VisibleStr := ExtractJsonValue(Params, 'visible');

    If LayerStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "layer" parameter');
        Exit;
    End;

    LayerID := GetLayerFromString(LayerStr);
    Visible := (LowerCase(VisibleStr) = 'true') Or (VisibleStr = '1');

    Board.LayerIsDisplayed[LayerID] := Visible;

    // Refresh the view
    // Board.ViewManager_FullUpdate;  // removed — expensive on large boards; Altium auto-refreshes on user interaction

    Result := BuildSuccessResponse(RequestId,
        '{"layer":"' + EscapeJsonString(LayerStr) + '",'
        + '"visible":' + BoolToJsonStr(Visible) + '}');
End;

{..............................................................................}
{ PCB_RepourPolygons - Repour all polygon pours via RunProcess                }
{..............................................................................}

Function PCB_RepourPolygons(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    ResetParameters;
    RunProcess('PCB:RepourAllPolygons');

    // Board.ViewManager_FullUpdate;  // removed — expensive on large boards; Altium auto-refreshes on user interaction

    Result := BuildSuccessResponse(RequestId,
        '{"repoured":true}');
End;

{..............................................................................}
{ PCB_PlaceVia - Place a via at specific coordinates on a net                 }
{ Params: x=<mils>, y=<mils>, net=<name>, size=<mils>, hole_size=<mils>,    }
{         low_layer=<layer>, high_layer=<layer>                              }
{..............................................................................}

Function PCB_PlaceVia(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Via : IPCB_Via;
    XStr, YStr, NetStr, SizeStr, HoleSizeStr, LowLayerStr, HighLayerStr : String;
    FoundNet : IPCB_Net;
    ViaX, ViaY, ViaSize, ViaHole : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    NetStr := ExtractJsonValue(Params, 'net');
    SizeStr := ExtractJsonValue(Params, 'size');
    HoleSizeStr := ExtractJsonValue(Params, 'hole_size');
    LowLayerStr := ExtractJsonValue(Params, 'low_layer');
    HighLayerStr := ExtractJsonValue(Params, 'high_layer');

    If (XStr = '') Or (YStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "x" and/or "y" parameters');
        Exit;
    End;

    ViaX := StrToIntDef(XStr, 0);
    ViaY := StrToIntDef(YStr, 0);
    ViaSize := StrToIntDef(SizeStr, 50);    // Default 50 mils pad size
    ViaHole := StrToIntDef(HoleSizeStr, 28); // Default 28 mils hole

    PCBServer.PreProcess;
    Try
        Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
        If Via = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create via object');
            Exit;
        End;

        Via.x := MilsToCoord(ViaX);
        Via.y := MilsToCoord(ViaY);
        Via.Size := MilsToCoord(ViaSize);
        Via.HoleSize := MilsToCoord(ViaHole);

        // Set layers
        If LowLayerStr <> '' Then
            Via.LowLayer := GetLayerFromString(LowLayerStr)
        Else
            Via.LowLayer := eTopLayer;

        If HighLayerStr <> '' Then
            Via.HighLayer := GetLayerFromString(HighLayerStr)
        Else
            Via.HighLayer := eBottomLayer;

        // Assign net
        If NetStr <> '' Then
        Begin
            FoundNet := FindNetByName(Board, NetStr);
            If FoundNet <> Nil Then
                Via.Net := FoundNet;
        End;

        Board.AddPCBObject(Via);

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Via.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x":' + IntToStr(ViaX) + ','
        + '"y":' + IntToStr(ViaY) + ','
        + '"size":' + IntToStr(ViaSize) + ','
        + '"hole_size":' + IntToStr(ViaHole) + '}');
End;

{..............................................................................}
{ PCB_PlaceTrack - Place a track segment between two XY points               }
{ Params: x1, y1, x2, y2 (mils), width (mils), layer, net_name             }
{..............................................................................}

Function PCB_PlaceTrack(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Track : IPCB_Track;
    X1Str, Y1Str, X2Str, Y2Str, WidthStr, LayerStr, NetStr : String;
    FoundNet : IPCB_Net;
    TX1, TY1, TX2, TY2, TWidth : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X1Str := ExtractJsonValue(Params, 'x1');
    Y1Str := ExtractJsonValue(Params, 'y1');
    X2Str := ExtractJsonValue(Params, 'x2');
    Y2Str := ExtractJsonValue(Params, 'y2');
    WidthStr := ExtractJsonValue(Params, 'width');
    LayerStr := ExtractJsonValue(Params, 'layer');
    NetStr := ExtractJsonValue(Params, 'net_name');

    If (X1Str = '') Or (Y1Str = '') Or (X2Str = '') Or (Y2Str = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing coordinate parameters (x1, y1, x2, y2)');
        Exit;
    End;

    TX1 := StrToIntDef(X1Str, 0);
    TY1 := StrToIntDef(Y1Str, 0);
    TX2 := StrToIntDef(X2Str, 0);
    TY2 := StrToIntDef(Y2Str, 0);
    TWidth := StrToIntDef(WidthStr, 10);

    PCBServer.PreProcess;
    Try
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        If Track = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create track object');
            Exit;
        End;

        Track.x1 := MilsToCoord(TX1);
        Track.y1 := MilsToCoord(TY1);
        Track.x2 := MilsToCoord(TX2);
        Track.y2 := MilsToCoord(TY2);
        Track.Width := MilsToCoord(TWidth);

        If LayerStr <> '' Then
            Track.Layer := GetLayerFromString(LayerStr)
        Else
            Track.Layer := eTopLayer;

        If NetStr <> '' Then
        Begin
            FoundNet := FindNetByName(Board, NetStr);
            If FoundNet <> Nil Then
                Track.Net := FoundNet;
        End;

        Board.AddPCBObject(Track);

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Track.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + IntToStr(TX1) + ','
        + '"y1":' + IntToStr(TY1) + ','
        + '"x2":' + IntToStr(TX2) + ','
        + '"y2":' + IntToStr(TY2) + ','
        + '"width":' + IntToStr(TWidth) + ','
        + '"layer":"' + EscapeJsonString(GetLayerString(Track.Layer)) + '"}');
End;

{..............................................................................}
{ PCB_PlaceTracks - Place many tracks in a single IPC round-trip.              }
{ Param 'tracks' is a pipe-separated list; each track is 7 comma-separated    }
{ fields: x1,y1,x2,y2,width,layer,net_name (width default 10, layer default   }
{ TopLayer, net optional). Wrapped in one PreProcess/PostProcess and one      }
{ save so N tracks cost ~1x the overhead of placing one.                       }
{..............................................................................}

Function PCB_PlaceTracks(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Track : IPCB_Track;
    TracksStr, TrackStr, Remaining, Field : String;
    PipePos, CommaPos, Placed, Failed, I : Integer;
    TX1, TY1, TX2, TY2, TWidth : Integer;
    LayerStr, NetStr : String;
    FoundNet : IPCB_Net;
    FieldVals : Array[0..6] Of String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    TracksStr := ExtractJsonValue(Params, 'tracks');
    If TracksStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'tracks parameter required');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    Remaining := TracksStr;

    PCBServer.PreProcess;
    Try
        While Length(Remaining) > 0 Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then
            Begin
                TrackStr := Remaining;
                Remaining := '';
            End
            Else
            Begin
                TrackStr := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End;

            If TrackStr = '' Then Continue;

            { Split on commas into up to 7 fields. }
            For I := 0 To 6 Do FieldVals[I] := '';
            I := 0;
            While (TrackStr <> '') And (I <= 6) Do
            Begin
                CommaPos := Pos(',', TrackStr);
                If CommaPos = 0 Then
                Begin
                    FieldVals[I] := TrackStr;
                    TrackStr := '';
                End
                Else
                Begin
                    FieldVals[I] := Copy(TrackStr, 1, CommaPos - 1);
                    TrackStr := Copy(TrackStr, CommaPos + 1, Length(TrackStr));
                End;
                Inc(I);
            End;

            TX1 := StrToIntDef(FieldVals[0], 0);
            TY1 := StrToIntDef(FieldVals[1], 0);
            TX2 := StrToIntDef(FieldVals[2], 0);
            TY2 := StrToIntDef(FieldVals[3], 0);
            TWidth := StrToIntDef(FieldVals[4], 10);
            LayerStr := FieldVals[5];
            NetStr := FieldVals[6];

            Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
            If Track = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Track.x1 := MilsToCoord(TX1);
            Track.y1 := MilsToCoord(TY1);
            Track.x2 := MilsToCoord(TX2);
            Track.y2 := MilsToCoord(TY2);
            Track.Width := MilsToCoord(TWidth);

            If LayerStr <> '' Then
                Track.Layer := GetLayerFromString(LayerStr)
            Else
                Track.Layer := eTopLayer;

            If NetStr <> '' Then
            Begin
                FoundNet := FindNetByName(Board, NetStr);
                If FoundNet <> Nil Then Track.Net := FoundNet;
            End;

            Board.AddPCBObject(Track);
            Inc(Placed);
        End;
        { Broadcast ONCE at the end of the batch instead of once per track.   }
        { A single BoardRegisteration on the board object (null child) is     }
        { enough to kick the connectivity/rules engines to refresh the whole  }
        { board — much cheaper than N individual broadcasts.                   }
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ','
        + '"failed":' + IntToStr(Failed) + '}');
End;

{..............................................................................}
{ PCB_PlaceArc - Place an arc on the PCB                                      }
{ Params: x_center, y_center, radius, start_angle, end_angle, width, layer   }
{..............................................................................}

Function PCB_PlaceArc(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Arc : IPCB_Arc;
    XCStr, YCStr, RadStr, SAStr, EAStr, WidthStr, LayerStr : String;
    ArcXC, ArcYC, ArcRad, ArcWidth : Integer;
    ArcSA, ArcEA : Double;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    XCStr := ExtractJsonValue(Params, 'x_center');
    YCStr := ExtractJsonValue(Params, 'y_center');
    RadStr := ExtractJsonValue(Params, 'radius');
    SAStr := ExtractJsonValue(Params, 'start_angle');
    EAStr := ExtractJsonValue(Params, 'end_angle');
    WidthStr := ExtractJsonValue(Params, 'width');
    LayerStr := ExtractJsonValue(Params, 'layer');

    If (XCStr = '') Or (YCStr = '') Or (RadStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing required parameters (x_center, y_center, radius)');
        Exit;
    End;

    ArcXC := StrToIntDef(XCStr, 0);
    ArcYC := StrToIntDef(YCStr, 0);
    ArcRad := StrToIntDef(RadStr, 100);
    ArcSA := StrToFloatDef(SAStr, 0);
    ArcEA := StrToFloatDef(EAStr, 360);
    ArcWidth := StrToIntDef(WidthStr, 10);

    PCBServer.PreProcess;
    Try
        Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
        If Arc = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create arc object');
            Exit;
        End;

        Arc.XCenter := MilsToCoord(ArcXC);
        Arc.YCenter := MilsToCoord(ArcYC);
        Arc.Radius := MilsToCoord(ArcRad);
        Arc.StartAngle := ArcSA;
        Arc.EndAngle := ArcEA;
        Arc.LineWidth := MilsToCoord(ArcWidth);

        If LayerStr <> '' Then
            Arc.Layer := GetLayerFromString(LayerStr)
        Else
            Arc.Layer := eTopLayer;

        Board.AddPCBObject(Arc);

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Arc.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x_center":' + IntToStr(ArcXC) + ','
        + '"y_center":' + IntToStr(ArcYC) + ','
        + '"radius":' + IntToStr(ArcRad) + ','
        + '"start_angle":' + FloatToStr(ArcSA) + ','
        + '"end_angle":' + FloatToStr(ArcEA) + ','
        + '"width":' + IntToStr(ArcWidth) + ','
        + '"layer":"' + EscapeJsonString(GetLayerString(Arc.Layer)) + '"}');
End;

{..............................................................................}
{ PCB_PlaceText - Place text string on the PCB                                }
{ Params: text, x, y (mils), layer, height (mils), rotation (deg)           }
{..............................................................................}

Function PCB_PlaceText(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    TextObj : IPCB_Text;
    TextStr, XStr, YStr, LayerStr, HeightStr, RotStr : String;
    TX, TY, THeight : Integer;
    TRot : Double;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    TextStr := ExtractJsonValue(Params, 'text');
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    LayerStr := ExtractJsonValue(Params, 'layer');
    HeightStr := ExtractJsonValue(Params, 'height');
    RotStr := ExtractJsonValue(Params, 'rotation');

    If TextStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "text" parameter');
        Exit;
    End;

    If (XStr = '') Or (YStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "x" and/or "y" parameters');
        Exit;
    End;

    TX := StrToIntDef(XStr, 0);
    TY := StrToIntDef(YStr, 0);
    THeight := StrToIntDef(HeightStr, 60);
    TRot := StrToFloatDef(RotStr, 0);

    PCBServer.PreProcess;
    Try
        TextObj := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
        If TextObj = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create text object');
            Exit;
        End;

        TextObj.XLocation := MilsToCoord(TX);
        TextObj.YLocation := MilsToCoord(TY);
        TextObj.Text := TextStr;
        TextObj.Size := MilsToCoord(THeight);
        TextObj.Rotation := TRot;

        If LayerStr <> '' Then
            TextObj.Layer := GetLayerFromString(LayerStr)
        Else
            TextObj.Layer := eTopOverlay;

        Board.AddPCBObject(TextObj);

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, TextObj.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"text":"' + EscapeJsonString(TextStr) + '",'
        + '"x":' + IntToStr(TX) + ','
        + '"y":' + IntToStr(TY) + ','
        + '"height":' + IntToStr(THeight) + ','
        + '"rotation":' + FloatToStr(TRot) + ','
        + '"layer":"' + EscapeJsonString(GetLayerString(TextObj.Layer)) + '"}');
End;

{..............................................................................}
{ PCB_PlaceFill - Place a copper fill rectangle                               }
{ Params: x1, y1, x2, y2 (mils), layer, net_name                           }
{..............................................................................}

Function PCB_PlaceFill(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Fill : IPCB_Fill;
    X1Str, Y1Str, X2Str, Y2Str, LayerStr, NetStr : String;
    FoundNet : IPCB_Net;
    FX1, FY1, FX2, FY2 : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X1Str := ExtractJsonValue(Params, 'x1');
    Y1Str := ExtractJsonValue(Params, 'y1');
    X2Str := ExtractJsonValue(Params, 'x2');
    Y2Str := ExtractJsonValue(Params, 'y2');
    LayerStr := ExtractJsonValue(Params, 'layer');
    NetStr := ExtractJsonValue(Params, 'net_name');

    If (X1Str = '') Or (Y1Str = '') Or (X2Str = '') Or (Y2Str = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing coordinate parameters (x1, y1, x2, y2)');
        Exit;
    End;

    FX1 := StrToIntDef(X1Str, 0);
    FY1 := StrToIntDef(Y1Str, 0);
    FX2 := StrToIntDef(X2Str, 0);
    FY2 := StrToIntDef(Y2Str, 0);

    PCBServer.PreProcess;
    Try
        Fill := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
        If Fill = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create fill object');
            Exit;
        End;

        Fill.X1Location := MilsToCoord(FX1);
        Fill.Y1Location := MilsToCoord(FY1);
        Fill.X2Location := MilsToCoord(FX2);
        Fill.Y2Location := MilsToCoord(FY2);
        Fill.Rotation := 0;

        If LayerStr <> '' Then
            Fill.Layer := GetLayerFromString(LayerStr)
        Else
            Fill.Layer := eTopLayer;

        If NetStr <> '' Then
        Begin
            FoundNet := FindNetByName(Board, NetStr);
            If FoundNet <> Nil Then
                Fill.Net := FoundNet;
        End;

        Board.AddPCBObject(Fill);

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Fill.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + IntToStr(FX1) + ','
        + '"y1":' + IntToStr(FY1) + ','
        + '"x2":' + IntToStr(FX2) + ','
        + '"y2":' + IntToStr(FY2) + ','
        + '"layer":"' + EscapeJsonString(GetLayerString(Fill.Layer)) + '"}');
End;

{..............................................................................}
{ PCB_StartPolygonPlacement - Launches Altium's interactive polygon tool      }
{ Requires user to draw the polygon boundary in Altium afterward              }
{ Params: layer, net_name                                                    }
{..............................................................................}

Function PCB_StartPolygonPlacement(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStr, NetStr : String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerStr := ExtractJsonValue(Params, 'layer');
    NetStr := ExtractJsonValue(Params, 'net_name');

    ResetParameters;
    If LayerStr <> '' Then
        AddStringParameter('Layer', LayerStr);
    If NetStr <> '' Then
        AddStringParameter('Net', NetStr);
    RunProcess('PCB:PlacePolygonPlane');

    // Board.ViewManager_FullUpdate;  // removed — expensive on large boards; Altium auto-refreshes on user interaction

    Result := BuildSuccessResponse(RequestId,
        '{"interactive_tool_launched":true,'
        + '"layer":"' + EscapeJsonString(LayerStr) + '",'
        + '"net_name":"' + EscapeJsonString(NetStr) + '",'
        + '"note":"Interactive polygon placement tool launched. Requires user to draw the polygon boundary in Altium Designer — no polygon is created by this call."}');
End;

{..............................................................................}
{ PCB_CreateDesignRule - Create a new design rule                             }
{ Params: rule_type (clearance/width/via_size), name, value (mils),          }
{         scope (query expression for Scope1)                                }
{..............................................................................}

Function PCB_CreateDesignRule(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Rule : IPCB_Rule;
    RuleTypeStr, RuleName, ValueStr, ScopeStr, NetScopeStr : String;
    RuleValue, NetScopeVal : Integer;
    L : TLayer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    RuleTypeStr := ExtractJsonValue(Params, 'rule_type');
    RuleName := ExtractJsonValue(Params, 'name');
    ValueStr := ExtractJsonValue(Params, 'value');
    ScopeStr := ExtractJsonValue(Params, 'scope');
    NetScopeStr := LowerCase(ExtractJsonValue(Params, 'net_scope'));

    If RuleName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "name" parameter');
        Exit;
    End;

    If RuleTypeStr = '' Then
        RuleTypeStr := 'clearance';

    { Map textual net_scope to the enum used by the rule object.                  }
    { Blank / "any_net" keeps prior default behavior. For clearance rules          }
    { "different_nets" is the normal setting — same-net tracks touching pads      }
    { of their own net should NOT count as a clearance violation.                  }
    If NetScopeStr = 'different_nets' Then
        NetScopeVal := eNetScope_DifferentNets
    Else If NetScopeStr = 'same_net' Then
        NetScopeVal := eNetScope_SameNet
    Else
        NetScopeVal := eNetScope_AnyNet;

    RuleValue := StrToIntDef(ValueStr, 10);

    PCBServer.PreProcess;
    Try
        If RuleTypeStr = 'clearance' Then
        Begin
            Rule := PCBServer.PCBRuleFactory(eRule_Clearance);
            Rule.Name := RuleName;
            Rule.NetScope := NetScopeVal;
            Rule.LayerKind := eRuleLayerKind_SameLayer;
            Rule.Gap := MilsToCoord(RuleValue);
            If ScopeStr <> '' Then
                Rule.Scope1Expression := ScopeStr;
        End
        Else If RuleTypeStr = 'width' Then
        Begin
            Rule := PCBServer.PCBRuleFactory(eRule_MaxMinWidth);
            Rule.Name := RuleName;
            Rule.NetScope := NetScopeVal;
            Rule.LayerKind := eRuleLayerKind_SameLayer;
            For L := MinLayer To MaxLayer Do
            Begin
                Rule.MinWidth[L] := MilsToCoord(RuleValue);
                Rule.MaxWidth[L] := MilsToCoord(RuleValue * 5);
                Rule.FavoredWidth[L] := MilsToCoord(RuleValue);
            End;
            If ScopeStr <> '' Then
                Rule.Scope1Expression := ScopeStr;
        End
        Else If RuleTypeStr = 'via_size' Then
        Begin
            Rule := PCBServer.PCBRuleFactory(eRule_MaxMinHoleSize);
            Rule.Name := RuleName;
            Rule.NetScope := NetScopeVal;
            Rule.LayerKind := eRuleLayerKind_SameLayer;
            Rule.MinLimit := MilsToCoord(RuleValue);
            Rule.MaxLimit := MilsToCoord(RuleValue * 5);
            If ScopeStr <> '' Then
                Rule.Scope1Expression := ScopeStr;
        End
        Else
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAM',
                'Unknown rule_type: ' + RuleTypeStr + '. Use clearance, width, or via_size');
            Exit;
        End;

        Rule.Enabled := True;
        Board.AddPCBObject(Rule);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Rule.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"created":true,'
        + '"name":"' + EscapeJsonString(RuleName) + '",'
        + '"rule_type":"' + EscapeJsonString(RuleTypeStr) + '",'
        + '"value_mils":' + IntToStr(RuleValue) + '}');
End;

{..............................................................................}
{ PCB_DeleteDesignRule - Delete a design rule by name                         }
{ Params: name=<rule_name>                                                   }
{..............................................................................}

Function PCB_DeleteDesignRule(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Rule : IPCB_Rule;
    RuleName : String;
    Found : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    RuleName := ExtractJsonValue(Params, 'name');
    If RuleName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "name" parameter');
        Exit;
    End;

    // Find the rule by name
    Found := False;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Rule := Iterator.FirstPCBObject;
    While Rule <> Nil Do
    Begin
        If Rule.Name = RuleName Then
        Begin
            Found := True;
            Break;
        End;
        Rule := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Design rule not found: ' + RuleName);
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Rule.I_ObjectAddress);
        Board.RemovePCBObject(Rule);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"deleted":true,"name":"' + EscapeJsonString(RuleName) + '"}');
End;

{..............................................................................}
{ PCB_GetComponentPads - Get all pads of a specific component                 }
{ Params: designator=<ref>                                                   }
{..............................................................................}

Function PCB_GetComponentPads(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    DesStr, JsonItems, PadName, NetName, LayerStr, ShapeStr : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DesStr := ExtractJsonValue(Params, 'designator');
    If DesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "designator" parameter');
        Exit;
    End;

    Comp := Board.GetPcbComponentByRefDes(DesStr);
    If Comp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + DesStr);
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    GrpIter := Comp.GroupIterator_Create;
    GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

    Pad := GrpIter.FirstPCBObject;
    While Pad <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        Try PadName := Pad.Name; Except PadName := ''; End;
        Try
            If Pad.Net <> Nil Then NetName := Pad.Net.Name
            Else NetName := '';
        Except NetName := ''; End;
        Try LayerStr := GetLayerString(Pad.Layer); Except LayerStr := 'Unknown'; End;

        JsonItems := JsonItems + '{"name":"' + EscapeJsonString(PadName) + '",'
            + '"x":' + IntToStr(CoordToMils(Pad.x)) + ','
            + '"y":' + IntToStr(CoordToMils(Pad.y)) + ','
            + '"net":"' + EscapeJsonString(NetName) + '",'
            + '"layer":"' + EscapeJsonString(LayerStr) + '",'
            + '"hole_size":' + IntToStr(CoordToMils(Pad.HoleSize)) + ','
            + '"top_x_size":' + IntToStr(CoordToMils(Pad.TopXSize)) + ','
            + '"top_y_size":' + IntToStr(CoordToMils(Pad.TopYSize)) + ','
            + '"rotation":' + FloatToStr(Pad.Rotation) + '}';
        Inc(Count);
        Pad := GrpIter.NextPCBObject;
    End;
    Comp.GroupIterator_Destroy(GrpIter);

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(DesStr) + '",'
        + '"pads":[' + JsonItems + '],"pad_count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_FlipComponent - Flip a component to the other side (top<->bottom)      }
{ Params: designator=<ref>                                                   }
{..............................................................................}

Function PCB_FlipComponent(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    DesStr, OldLayer, NewLayer : String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DesStr := ExtractJsonValue(Params, 'designator');
    If DesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "designator" parameter');
        Exit;
    End;

    Comp := Board.GetPcbComponentByRefDes(DesStr);
    If Comp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + DesStr);
        Exit;
    End;

    Try OldLayer := GetLayerString(Comp.Layer); Except OldLayer := 'Unknown'; End;

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);

        // Flip the component to the opposite side of the board
        If Comp.Layer = eTopLayer Then
            Comp.Layer := eBottomLayer
        Else
            Comp.Layer := eTopLayer;

        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
            PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    Try NewLayer := GetLayerString(Comp.Layer); Except NewLayer := 'Unknown'; End;
    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(DesStr) + '",'
        + '"old_layer":"' + EscapeJsonString(OldLayer) + '",'
        + '"new_layer":"' + EscapeJsonString(NewLayer) + '"}');
End;

{..............................................................................}
{ PCB_AlignComponents - Align specified components                            }
{ Params: designators=<comma-separated>, alignment=<left/right/top/bottom/  }
{         center_x/center_y>                                                 }
{..............................................................................}

Function PCB_AlignComponents(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    DesStr, AlignStr, Remaining, OneDesig : String;
    Comp : IPCB_Component;
    CommaPos, I, CompCount : Integer;
    Comps : Array[0..99] Of IPCB_Component;
    MinX, MaxX, MinY, MaxY, CenterX, CenterY, AlignTarget : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DesStr := ExtractJsonValue(Params, 'designators');
    AlignStr := ExtractJsonValue(Params, 'alignment');

    If DesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "designators" parameter');
        Exit;
    End;

    If AlignStr = '' Then AlignStr := 'left';

    // Parse designators and find components
    CompCount := 0;
    Remaining := DesStr;
    While (Remaining <> '') And (CompCount < 100) Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            OneDesig := Copy(Remaining, 1, CommaPos - 1);
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else
        Begin
            OneDesig := Remaining;
            Remaining := '';
        End;
        If OneDesig <> '' Then
        Begin
            Comp := Board.GetPcbComponentByRefDes(OneDesig);
            If Comp <> Nil Then
            Begin
                Comps[CompCount] := Comp;
                Inc(CompCount);
            End;
        End;
    End;

    If CompCount < 2 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INSUFFICIENT', 'Need at least 2 valid components to align');
        Exit;
    End;

    // Calculate bounding extents
    MinX := CoordToMils(Comps[0].x);
    MaxX := MinX;
    MinY := CoordToMils(Comps[0].y);
    MaxY := MinY;
    For I := 1 To CompCount - 1 Do
    Begin
        If CoordToMils(Comps[I].x) < MinX Then MinX := CoordToMils(Comps[I].x);
        If CoordToMils(Comps[I].x) > MaxX Then MaxX := CoordToMils(Comps[I].x);
        If CoordToMils(Comps[I].y) < MinY Then MinY := CoordToMils(Comps[I].y);
        If CoordToMils(Comps[I].y) > MaxY Then MaxY := CoordToMils(Comps[I].y);
    End;
    CenterX := (MinX + MaxX) Div 2;
    CenterY := (MinY + MaxY) Div 2;

    // Apply alignment
    PCBServer.PreProcess;
    Try
        For I := 0 To CompCount - 1 Do
        Begin
            PCBServer.SendMessageToRobots(Comps[I].I_ObjectAddress, c_Broadcast,
                PCBM_BeginModify, c_NoEventData);

            If AlignStr = 'left' Then
                Comps[I].x := MilsToCoord(MinX)
            Else If AlignStr = 'right' Then
                Comps[I].x := MilsToCoord(MaxX)
            Else If AlignStr = 'top' Then
                Comps[I].y := MilsToCoord(MaxY)
            Else If AlignStr = 'bottom' Then
                Comps[I].y := MilsToCoord(MinY)
            Else If AlignStr = 'center_x' Then
                Comps[I].x := MilsToCoord(CenterX)
            Else If AlignStr = 'center_y' Then
                Comps[I].y := MilsToCoord(CenterY);

            PCBServer.SendMessageToRobots(Comps[I].I_ObjectAddress, c_Broadcast,
                PCBM_EndModify, c_NoEventData);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"aligned":true,'
        + '"alignment":"' + EscapeJsonString(AlignStr) + '",'
        + '"component_count":' + IntToStr(CompCount) + '}');
End;

{..............................................................................}
{ PCB_GetClearanceViolations - Get clearance violations for a net             }
{ Params: net (optional) - if specified, only show violations for this net   }
{..............................................................................}

Function PCB_GetClearanceViolations(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Violation : IPCB_Violation;
    FilterNet, ViolDesc, ViolName : String;
    JsonItems : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    FilterNet := ExtractJsonValue(Params, 'net');

    // First run DRC to refresh violations
    ResetParameters;
    RunProcess('PCB:RunDRC');

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eViolationObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Violation := Iterator.FirstPCBObject;
    While Violation <> Nil Do
    Begin
        Try ViolDesc := Violation.Description; Except ViolDesc := ''; End;
        Try ViolName := Violation.Name; Except ViolName := ''; End;

        // Filter by net if specified (check if net name appears in description)
        If (FilterNet = '') Or (Pos(FilterNet, ViolDesc) > 0) Or (Pos(FilterNet, ViolName) > 0) Then
        Begin
            If Count < 200 Then
            Begin
                If Not First Then JsonItems := JsonItems + ',';
                First := False;
                JsonItems := JsonItems + BuildViolationJson(Violation);
            End;
            Inc(Count);
        End;
        Violation := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"violation_count":' + IntToStr(Count) + ','
        + '"violations":[' + JsonItems + ']}');
End;

{..............................................................................}
{ PCB_SnapToGrid - Snap a component to the nearest grid point                }
{ Params: designator=<ref>, grid_size=<mils>                                }
{..............................................................................}

Function PCB_SnapToGrid(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    DesStr, GridStr : String;
    GridSize, OldX, OldY, NewX, NewY : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DesStr := ExtractJsonValue(Params, 'designator');
    GridStr := ExtractJsonValue(Params, 'grid_size');

    If DesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "designator" parameter');
        Exit;
    End;

    GridSize := StrToIntDef(GridStr, 50);
    If GridSize <= 0 Then GridSize := 50;

    Comp := Board.GetPcbComponentByRefDes(DesStr);
    If Comp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + DesStr);
        Exit;
    End;

    OldX := CoordToMils(Comp.x);
    OldY := CoordToMils(Comp.y);

    // Snap to nearest grid point using rounding
    NewX := Round(OldX / GridSize) * GridSize;
    NewY := Round(OldY / GridSize) * GridSize;

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);

        Comp.x := MilsToCoord(NewX);
        Comp.y := MilsToCoord(NewY);

        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
            PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(DesStr) + '",'
        + '"old_x":' + IntToStr(OldX) + ','
        + '"old_y":' + IntToStr(OldY) + ','
        + '"new_x":' + IntToStr(NewX) + ','
        + '"new_y":' + IntToStr(NewY) + ','
        + '"grid_size":' + IntToStr(GridSize) + '}');
End;

{..............................................................................}
{ PCB_GetDiffPairRules - Get all differential pair routing rules              }
{ Returns design rules (not pair objects) of kind eRule_DifferentialPairsRouting }
{..............................................................................}

Function PCB_GetDiffPairRules(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Rule : IPCB_Rule;
    JsonItems : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Rule := Iterator.FirstPCBObject;
    While Rule <> Nil Do
    Begin
        If Rule.RuleKind = eRule_DifferentialPairsRouting Then
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems + '{"name":"' + EscapeJsonString(Rule.Name) + '",'
                + '"enabled":' + BoolToJsonStr(Rule.Enabled) + ','
                + '"scope_1":"' + EscapeJsonString(Rule.Scope1Expression) + '",'
                + '"scope_2":"' + EscapeJsonString(Rule.Scope2Expression) + '",'
                + '"comment":"' + EscapeJsonString(Rule.Comment) + '",'
                + '"descriptor":"' + EscapeJsonString(Rule.Descriptor) + '"}';
            Inc(Count);
        End;
        Rule := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"diff_pair_rules":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_GetVias - Get all vias on the board with position, size, net, layers   }
{..............................................................................}

Function PCB_GetVias(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Via : IPCB_Via;
    JsonItems, NetName : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eViaObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Via := Iterator.FirstPCBObject;
    While Via <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        Try
            If Via.Net <> Nil Then NetName := Via.Net.Name
            Else NetName := '';
        Except NetName := ''; End;

        JsonItems := JsonItems + '{"x":' + IntToStr(CoordToMils(Via.x)) + ','
            + '"y":' + IntToStr(CoordToMils(Via.y)) + ','
            + '"size":' + IntToStr(CoordToMils(Via.Size)) + ','
            + '"hole_size":' + IntToStr(CoordToMils(Via.HoleSize)) + ','
            + '"net":"' + EscapeJsonString(NetName) + '",'
            + '"low_layer":"' + EscapeJsonString(GetLayerString(Via.LowLayer)) + '",'
            + '"high_layer":"' + EscapeJsonString(GetLayerString(Via.HighLayer)) + '"}';
        Inc(Count);
        Via := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"vias":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_DeleteObject - Delete a PCB object at specific coordinates on a layer  }
{ Params: x, y (mils), layer, object_type (track/via/fill/text)             }
{..............................................................................}

Function PCB_DeleteObject(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    XStr, YStr, LayerStr, ObjTypeStr : String;
    TargetX, TargetY, ObjX, ObjY : Integer;
    TargetLayer : TLayer;
    ObjFilter : TObjectId;
    Found : Boolean;
    FoundObj : IPCB_Primitive;
    Dist, BestDist : Double;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    LayerStr := ExtractJsonValue(Params, 'layer');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');

    If (XStr = '') Or (YStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "x" and/or "y" parameters');
        Exit;
    End;

    If ObjTypeStr = '' Then ObjTypeStr := 'track';

    TargetX := StrToIntDef(XStr, 0);
    TargetY := StrToIntDef(YStr, 0);

    If LayerStr <> '' Then
        TargetLayer := GetLayerFromString(LayerStr)
    Else
        TargetLayer := eTopLayer;

    // Map object type string to filter
    If ObjTypeStr = 'track' Then
        ObjFilter := eTrackObject
    Else If ObjTypeStr = 'via' Then
        ObjFilter := eViaObject
    Else If ObjTypeStr = 'fill' Then
        ObjFilter := eFillObject
    Else If ObjTypeStr = 'text' Then
        ObjFilter := eTextObject
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAM',
            'Unknown object_type: ' + ObjTypeStr + '. Use track, via, fill, or text');
        Exit;
    End;

    // Find the closest matching object at the target coordinates
    Found := False;
    FoundObj := Nil;
    BestDist := 1e30;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ObjFilter));
    Iterator.AddFilter_LayerSet(MkSet(TargetLayer));
    Iterator.AddFilter_Method(eProcessAll);

    Obj := Iterator.FirstPCBObject;
    While Obj <> Nil Do
    Begin
        // Get object position based on type
        If ObjFilter = eTrackObject Then
        Begin
            ObjX := CoordToMils((Obj.x1 + Obj.x2) Div 2);
            ObjY := CoordToMils((Obj.y1 + Obj.y2) Div 2);
        End
        Else If ObjFilter = eViaObject Then
        Begin
            ObjX := CoordToMils(Obj.x);
            ObjY := CoordToMils(Obj.y);
        End
        Else If ObjFilter = eFillObject Then
        Begin
            ObjX := CoordToMils((Obj.X1Location + Obj.X2Location) Div 2);
            ObjY := CoordToMils((Obj.Y1Location + Obj.Y2Location) Div 2);
        End
        Else
        Begin
            ObjX := CoordToMils(Obj.XLocation);
            ObjY := CoordToMils(Obj.YLocation);
        End;

        Dist := Sqrt((ObjX - TargetX) * (ObjX - TargetX) + (ObjY - TargetY) * (ObjY - TargetY));
        If Dist < BestDist Then
        Begin
            BestDist := Dist;
            FoundObj := Obj;
            Found := True;
        End;

        Obj := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    If (Not Found) Or (BestDist > 100) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'No ' + ObjTypeStr + ' found within 100 mils of (' + IntToStr(TargetX) + ',' + IntToStr(TargetY) + ')');
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, FoundObj.I_ObjectAddress);
        Board.RemovePCBObject(FoundObj);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"deleted":true,'
        + '"object_type":"' + EscapeJsonString(ObjTypeStr) + '",'
        + '"distance_mils":' + FloatToStr(BestDist) + '}');
End;

{..............................................................................}
{ PCB_GetPadProperties - Get detailed pad info filtered by net or component  }
{ Params: net (optional), designator (optional)                              }
{..............................................................................}

Function PCB_GetPadProperties(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Pad : IPCB_Pad;
    FilterNet, FilterDesig : String;
    JsonItems, PadName, NetName, LayerStr, CompDesig, ShapeStr : String;
    PadCache : TPadCache;
    SolderMask, PasteMask : Integer;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    FilterNet := ExtractJsonValue(Params, 'net');
    FilterDesig := ExtractJsonValue(Params, 'designator');

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ePadObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Pad := Iterator.FirstPCBObject;
    While Pad <> Nil Do
    Begin
        // Get pad net name
        NetName := '';
        Try
            If Pad.Net <> Nil Then NetName := Pad.Net.Name;
        Except End;

        // Get parent component designator
        CompDesig := '';
        Try
            If Pad.Component <> Nil Then CompDesig := Pad.Component.Name.Text;
        Except End;

        // Apply filters
        If (FilterNet <> '') And (NetName <> FilterNet) Then
        Begin
            Pad := Iterator.NextPCBObject;
            Continue;
        End;
        If (FilterDesig <> '') And (CompDesig <> FilterDesig) Then
        Begin
            Pad := Iterator.NextPCBObject;
            Continue;
        End;

        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        Try PadName := Pad.Name; Except PadName := ''; End;
        Try LayerStr := GetLayerString(Pad.Layer); Except LayerStr := 'Unknown'; End;

        // Get pad shape as string
        Try
            If Pad.TopShape = eRounded Then ShapeStr := 'Round'
            Else If Pad.TopShape = eRectangular Then ShapeStr := 'Rectangular'
            Else If Pad.TopShape = eOctagonal Then ShapeStr := 'Octagonal'
            Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'RoundedRect'
            Else ShapeStr := 'Other';
        Except ShapeStr := 'Unknown'; End;

        // Get cache (solder/paste mask expansion)
        SolderMask := 0;
        PasteMask := 0;
        Try
            PadCache := Pad.GetState_Cache;
            If PadCache.SolderMaskExpansionValid = eCacheManual Then
                SolderMask := CoordToMils(PadCache.SolderMaskExpansion);
            If PadCache.PasteMaskExpansionValid = eCacheManual Then
                PasteMask := CoordToMils(PadCache.PasteMaskExpansion);
        Except End;

        JsonItems := JsonItems + '{"name":"' + EscapeJsonString(PadName) + '",'
            + '"component":"' + EscapeJsonString(CompDesig) + '",'
            + '"x":' + IntToStr(CoordToMils(Pad.x)) + ','
            + '"y":' + IntToStr(CoordToMils(Pad.y)) + ','
            + '"net":"' + EscapeJsonString(NetName) + '",'
            + '"layer":"' + EscapeJsonString(LayerStr) + '",'
            + '"shape":"' + EscapeJsonString(ShapeStr) + '",'
            + '"top_x_size":' + IntToStr(CoordToMils(Pad.TopXSize)) + ','
            + '"top_y_size":' + IntToStr(CoordToMils(Pad.TopYSize)) + ','
            + '"hole_size":' + IntToStr(CoordToMils(Pad.HoleSize)) + ','
            + '"rotation":' + FloatToStr(Pad.Rotation) + ','
            + '"is_smd":' + BoolToJsonStr(Pad.IsSurfaceMount) + ','
            + '"solder_mask_expansion":' + IntToStr(SolderMask) + ','
            + '"paste_mask_expansion":' + IntToStr(PasteMask) + '}';
        Inc(Count);

        If Count >= 500 Then Break;  // Limit output size
        Pad := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"pads":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_SetTrackWidth - Modify track width for all tracks on a specific net    }
{ Params: net_name, width_mils                                               }
{..............................................................................}

Function PCB_SetTrackWidth(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Track : IPCB_Track;
    NetNameStr, WidthStr, TrackNetName : String;
    NewWidth, ModCount : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetNameStr := ExtractJsonValue(Params, 'net_name');
    WidthStr := ExtractJsonValue(Params, 'width_mils');

    If NetNameStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "net_name" parameter');
        Exit;
    End;
    If WidthStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "width_mils" parameter');
        Exit;
    End;

    NewWidth := StrToIntDef(WidthStr, 10);
    ModCount := 0;

    PCBServer.PreProcess;
    Try
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        Track := Iterator.FirstPCBObject;
        While Track <> Nil Do
        Begin
            TrackNetName := '';
            Try
                If Track.Net <> Nil Then TrackNetName := Track.Net.Name;
            Except End;

            If TrackNetName = NetNameStr Then
            Begin
                PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast,
                    PCBM_BeginModify, c_NoEventData);

                Track.Width := MilsToCoord(NewWidth);

                PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast,
                    PCBM_EndModify, c_NoEventData);
                Inc(ModCount);
            End;
            Track := Iterator.NextPCBObject;
        End;
        Board.BoardIterator_Destroy(Iterator);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"modified":true,'
        + '"net_name":"' + EscapeJsonString(NetNameStr) + '",'
        + '"width_mils":' + IntToStr(NewWidth) + ','
        + '"tracks_modified":' + IntToStr(ModCount) + '}');
End;

{..............................................................................}
{ PCB_GetUnroutedNets - Get nets with unrouted connections (ratsnest lines)  }
{..............................................................................}

Function PCB_GetUnroutedNets(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    JsonItems, NetName : String;
    FinalResp : String;
    First : Boolean;
    Count, I, FoundIdx : Integer;
    NetNames : Array[0..127] Of String;
    NetCounts : Array[0..127] Of Integer;
    NetTotal : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetTotal := 0;
    Count := 0;

    { Filtering only on eConnectionObject hangs because Altium treats it as a  }
    { request for a fresh ratsnest — it rebuilds connectivity from scratch.    }
    { Using the multi-type filter (same as PCB_GetBoardStatistics) is ~1000x  }
    { faster — only walks primitives Altium already holds in memory.           }
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eViaObject, ePadObject,
        eComponentObject, eFillObject, eTextObject, ePolyObject, eConnectionObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Obj := Iterator.FirstPCBObject;
    While Obj <> Nil Do
    Begin
        If Obj.ObjectId = eConnectionObject Then
        Begin
            NetName := '';
            Try
                { Use Obj directly (late-bound); casting to IPCB_Connection via a }
                { local variable somehow corrupted the function Result string on  }
                { return in DelphiScript and the caller got only an empty object. }
                If Obj.Net <> Nil Then NetName := Obj.Net.Name;
            Except End;

            FoundIdx := -1;
            For I := 0 To NetTotal - 1 Do
            Begin
                If NetNames[I] = NetName Then
                Begin
                    FoundIdx := I;
                    Break;
                End;
            End;

            If FoundIdx >= 0 Then
                NetCounts[FoundIdx] := NetCounts[FoundIdx] + 1
            Else If NetTotal < 128 Then
            Begin
                NetNames[NetTotal] := NetName;
                NetCounts[NetTotal] := 1;
                Inc(NetTotal);
            End;

            Inc(Count);
        End;
        Obj := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    // Build JSON output
    JsonItems := '';
    First := True;
    For I := 0 To NetTotal - 1 Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '{"net":"' + EscapeJsonString(NetNames[I]) + '",'
            + '"unrouted_connections":' + IntToStr(NetCounts[I]) + '}';
    End;

    FinalResp := BuildSuccessResponse(RequestId,
        '{"unrouted_nets":[' + JsonItems + '],"net_count":' + IntToStr(NetTotal)
        + ',"total_unrouted":' + IntToStr(Count) + '}');

    { DelphiScript corrupts long string Result values on return from this     }
    { handler — the caller gets the RequestId argument instead of the JSON.  }
    { Bypass: write the response file directly here and set the sentinel     }
    { that tells Dispatcher.ProcessSingleRequest to skip its own write.       }
    WriteFileContent(WorkspaceDir + RESPONSE_FILE, FinalResp);
    ResponseAlreadyWritten := True;
    Result := FinalResp;
End;

{..............................................................................}
{ PCB_GetPolygons - Get all polygon pours with layer, net, hatching, etc.    }
{..............................................................................}

Function PCB_GetPolygons(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Polygon : IPCB_Polygon;
    JsonItems, NetName, LayerStr, HatchStr : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Polygon := Iterator.FirstPCBObject;
    While Polygon <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        NetName := '';
        Try
            If Polygon.Net <> Nil Then NetName := Polygon.Net.Name;
        Except End;
        Try LayerStr := GetLayerString(Polygon.Layer); Except LayerStr := 'Unknown'; End;

        // Get hatching style
        HatchStr := 'Unknown';
        Try
            If Polygon.HatchStyle = eHatchStyleNone Then HatchStr := 'Solid'
            Else If Polygon.HatchStyle = eHatchStyle45Degree Then HatchStr := '45Degree'
            Else If Polygon.HatchStyle = eHatchStyle90Degree Then HatchStr := '90Degree'
            Else If Polygon.HatchStyle = eHatchStyleHorizontal Then HatchStr := 'Horizontal'
            Else If Polygon.HatchStyle = eHatchStyleVertical Then HatchStr := 'Vertical'
            Else HatchStr := 'Other';
        Except End;

        JsonItems := JsonItems + '{"index":' + IntToStr(Count) + ','
            + '"name":"' + EscapeJsonString(Polygon.Name) + '",'
            + '"net":"' + EscapeJsonString(NetName) + '",'
            + '"layer":"' + EscapeJsonString(LayerStr) + '",'
            + '"hatch_style":"' + EscapeJsonString(HatchStr) + '",'
            + '"pour_over":' + BoolToJsonStr(Polygon.PourOver) + ','
            + '"remove_dead_copper":' + BoolToJsonStr(Polygon.RemoveDead) + '}';
        Inc(Count);
        Polygon := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"polygons":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_ModifyPolygon - Modify polygon pour properties                         }
{ Params: index (required), net (optional), layer (optional),               }
{         hatch_style (optional: Solid/45Degree/90Degree/Horizontal/Vertical)}
{..............................................................................}

Function PCB_ModifyPolygon(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Polygon : IPCB_Polygon;
    IndexStr, NetStr, LayerStr, HatchStr : String;
    TargetIdx, CurIdx : Integer;
    FoundPoly : IPCB_Polygon;
    FoundNet : IPCB_Net;
    Found : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    IndexStr := ExtractJsonValue(Params, 'index');
    NetStr := ExtractJsonValue(Params, 'net');
    LayerStr := ExtractJsonValue(Params, 'layer');
    HatchStr := ExtractJsonValue(Params, 'hatch_style');

    If IndexStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "index" parameter');
        Exit;
    End;

    TargetIdx := StrToIntDef(IndexStr, -1);
    If TargetIdx < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAM', 'Invalid index value');
        Exit;
    End;

    // Find the polygon at the specified index
    Found := False;
    FoundPoly := Nil;
    CurIdx := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Polygon := Iterator.FirstPCBObject;
    While Polygon <> Nil Do
    Begin
        If CurIdx = TargetIdx Then
        Begin
            FoundPoly := Polygon;
            Found := True;
            Break;
        End;
        Inc(CurIdx);
        Polygon := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Polygon index ' + IntToStr(TargetIdx) + ' not found');
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(FoundPoly.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);

        // Modify net
        If NetStr <> '' Then
        Begin
            FoundNet := FindNetByName(Board, NetStr);
            If FoundNet <> Nil Then
                FoundPoly.Net := FoundNet;
        End;

        // Modify layer
        If LayerStr <> '' Then
            FoundPoly.Layer := GetLayerFromString(LayerStr);

        // Modify hatch style
        If HatchStr <> '' Then
        Begin
            If HatchStr = 'Solid' Then
                FoundPoly.HatchStyle := eHatchStyleNone
            Else If HatchStr = '45Degree' Then
                FoundPoly.HatchStyle := eHatchStyle45Degree
            Else If HatchStr = '90Degree' Then
                FoundPoly.HatchStyle := eHatchStyle90Degree
            Else If HatchStr = 'Horizontal' Then
                FoundPoly.HatchStyle := eHatchStyleHorizontal
            Else If HatchStr = 'Vertical' Then
                FoundPoly.HatchStyle := eHatchStyleVertical;
        End;

        PCBServer.SendMessageToRobots(FoundPoly.I_ObjectAddress, c_Broadcast,
            PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"modified":true,'
        + '"index":' + IntToStr(TargetIdx) + ','
        + '"name":"' + EscapeJsonString(FoundPoly.Name) + '"}');
End;

{..............................................................................}
{ PCB_GetRoomRules - Get all room-like rules (confinement constraint rules)  }
{ Returns design rules of kind eRule_ConfinementConstraint, not physical rooms }
{..............................................................................}

Function PCB_GetRoomRules(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Rule : IPCB_Rule;
    JsonItems, KindStr : String;
    BR : TCoordRect;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Rule := Iterator.FirstPCBObject;
    While Rule <> Nil Do
    Begin
        If Rule.RuleKind = eRule_ConfinementConstraint Then
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;

            Try
                BR := Rule.BoundingRect;
            Except
                BR.Left := 0; BR.Bottom := 0; BR.Right := 0; BR.Top := 0;
            End;

            Try
                If Rule.Kind = eConfineIn Then KindStr := 'ConfineIn'
                Else KindStr := 'ConfineOut';
            Except KindStr := 'Unknown'; End;

            JsonItems := JsonItems + '{"name":"' + EscapeJsonString(Rule.Name) + '",'
                + '"enabled":' + BoolToJsonStr(Rule.Enabled) + ','
                + '"kind":"' + EscapeJsonString(KindStr) + '",'
                + '"scope_1":"' + EscapeJsonString(Rule.Scope1Expression) + '",'
                + '"comment":"' + EscapeJsonString(Rule.Comment) + '",'
                + '"x1":' + IntToStr(CoordToMils(BR.Left)) + ','
                + '"y1":' + IntToStr(CoordToMils(BR.Bottom)) + ','
                + '"x2":' + IntToStr(CoordToMils(BR.Right)) + ','
                + '"y2":' + IntToStr(CoordToMils(BR.Top)) + '}';
            Inc(Count);
        End;
        Rule := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"room_rules":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_CreateRoom - Create a room (confinement constraint) for components     }
{ Params: name, x1, y1, x2, y2 (mils), components (comma-separated desig)  }
{..............................................................................}

Function PCB_CreateRoom(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Rule : IPCB_ConfinementConstraint;
    CoordRect : TCoordRect;
    RoomName, X1Str, Y1Str, X2Str, Y2Str, CompsStr, ScopeExpr : String;
    Remaining, OneDesig : String;
    RX1, RY1, RX2, RY2, CommaPos : Integer;
    First : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    RoomName := ExtractJsonValue(Params, 'name');
    X1Str := ExtractJsonValue(Params, 'x1');
    Y1Str := ExtractJsonValue(Params, 'y1');
    X2Str := ExtractJsonValue(Params, 'x2');
    Y2Str := ExtractJsonValue(Params, 'y2');
    CompsStr := ExtractJsonValue(Params, 'components');

    If RoomName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "name" parameter');
        Exit;
    End;
    If (X1Str = '') Or (Y1Str = '') Or (X2Str = '') Or (Y2Str = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing coordinate parameters (x1, y1, x2, y2)');
        Exit;
    End;

    RX1 := StrToIntDef(X1Str, 0);
    RY1 := StrToIntDef(Y1Str, 0);
    RX2 := StrToIntDef(X2Str, 0);
    RY2 := StrToIntDef(Y2Str, 0);

    // Build scope expression from component designators
    ScopeExpr := '';
    If CompsStr <> '' Then
    Begin
        First := True;
        Remaining := CompsStr;
        While Remaining <> '' Do
        Begin
            CommaPos := Pos(',', Remaining);
            If CommaPos > 0 Then
            Begin
                OneDesig := Copy(Remaining, 1, CommaPos - 1);
                Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
            End
            Else
            Begin
                OneDesig := Remaining;
                Remaining := '';
            End;
            If OneDesig <> '' Then
            Begin
                If Not First Then ScopeExpr := ScopeExpr + ' OR ';
                First := False;
                ScopeExpr := ScopeExpr + 'InComponent(''' + OneDesig + ''')';
            End;
        End;
    End;
    If ScopeExpr = '' Then ScopeExpr := 'All';

    PCBServer.PreProcess;
    Try
        Rule := PCBServer.PCBRuleFactory(eRule_ConfinementConstraint);
        Rule.Name := RoomName;
        Rule.Comment := 'Room: ' + RoomName;
        Rule.NetScope := eNetScope_AnyNet;
        Rule.LayerKind := eRuleLayerKind_SameLayer;
        Rule.Scope1Expression := ScopeExpr;
        Rule.Kind := eConfineIn;
        Rule.Enabled := True;

        CoordRect.Left := MilsToCoord(RX1);
        CoordRect.Bottom := MilsToCoord(RY1);
        CoordRect.Right := MilsToCoord(RX2);
        CoordRect.Top := MilsToCoord(RY2);
        Rule.BoundingRect := CoordRect;

        Board.AddPCBObject(Rule);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Rule.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"created":true,'
        + '"name":"' + EscapeJsonString(RoomName) + '",'
        + '"x1":' + IntToStr(RX1) + ','
        + '"y1":' + IntToStr(RY1) + ','
        + '"x2":' + IntToStr(RX2) + ','
        + '"y2":' + IntToStr(RY2) + ','
        + '"scope":"' + EscapeJsonString(ScopeExpr) + '"}');
End;

{..............................................................................}
{ PCB_GetBoardStatistics - Comprehensive board statistics                    }
{..............................................................................}

Function PCB_GetBoardStatistics(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Outline : IPCB_BoardOutline;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    BR : TCoordRect;
    TrackCount, ViaCount, PadCount, CompCount : Integer;
    FillCount, TextCount, PolyCount, ConnCount : Integer;
    LayerCount : Integer;
    TotalTraceLen, DX, DY : Double;
    BoardWidth, BoardHeight, BoardArea : Double;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    TrackCount := 0;
    ViaCount := 0;
    PadCount := 0;
    CompCount := 0;
    FillCount := 0;
    TextCount := 0;
    PolyCount := 0;
    ConnCount := 0;
    TotalTraceLen := 0;

    // Count all object types in a single pass
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eViaObject, ePadObject,
        eComponentObject, eFillObject, eTextObject, ePolyObject, eConnectionObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Obj := Iterator.FirstPCBObject;
    While Obj <> Nil Do
    Begin
        If Obj.ObjectId = eTrackObject Then
        Begin
            Inc(TrackCount);
            DX := CoordToMils(Obj.x2) - CoordToMils(Obj.x1);
            DY := CoordToMils(Obj.y2) - CoordToMils(Obj.y1);
            TotalTraceLen := TotalTraceLen + Sqrt(DX * DX + DY * DY);
        End
        Else If Obj.ObjectId = eViaObject Then Inc(ViaCount)
        Else If Obj.ObjectId = ePadObject Then Inc(PadCount)
        Else If Obj.ObjectId = eComponentObject Then Inc(CompCount)
        Else If Obj.ObjectId = eFillObject Then Inc(FillCount)
        Else If Obj.ObjectId = eTextObject Then Inc(TextCount)
        Else If Obj.ObjectId = ePolyObject Then Inc(PolyCount)
        Else If Obj.ObjectId = eConnectionObject Then Inc(ConnCount);
        Obj := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    // Board dimensions from outline
    BoardWidth := 0;
    BoardHeight := 0;
    BoardArea := 0;
    Try
        Outline := Board.BoardOutline;
        If Outline <> Nil Then
        Begin
            Outline.Invalidate;
            Outline.Rebuild;
            Outline.Validate;
            BR := Outline.BoundingRectangle;
            BoardWidth := CoordToMils(BR.Right) - CoordToMils(BR.Left);
            BoardHeight := CoordToMils(BR.Top) - CoordToMils(BR.Bottom);
            BoardArea := BoardWidth * BoardHeight;
        End;
    Except End;

    // Layer count
    LayerCount := 0;
    Try
        LayerStack := Board.LayerStack_V7;
        If LayerStack <> Nil Then
        Begin
            LayerObj := LayerStack.FirstLayer;
            While LayerObj <> Nil Do
            Begin
                Inc(LayerCount);
                LayerObj := LayerStack.NextLayer(LayerObj);
            End;
        End;
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"track_count":' + IntToStr(TrackCount) + ','
        + '"via_count":' + IntToStr(ViaCount) + ','
        + '"pad_count":' + IntToStr(PadCount) + ','
        + '"component_count":' + IntToStr(CompCount) + ','
        + '"fill_count":' + IntToStr(FillCount) + ','
        + '"text_count":' + IntToStr(TextCount) + ','
        + '"polygon_count":' + IntToStr(PolyCount) + ','
        + '"unrouted_connections":' + IntToStr(ConnCount) + ','
        + '"total_trace_length_mils":' + FloatToStr(TotalTraceLen) + ','
        + '"board_width_mils":' + FloatToStr(BoardWidth) + ','
        + '"board_height_mils":' + FloatToStr(BoardHeight) + ','
        + '"board_area_sq_mils":' + FloatToStr(BoardArea) + ','
        + '"layer_count":' + IntToStr(LayerCount) + ','
        + '"board_name":"' + EscapeJsonString(ExtractFileName(Board.FileName)) + '"}');
End;

{..............................................................................}
{ PCB_ExportCoordinates - Export pick-and-place component coordinates        }
{..............................................................................}

Function PCB_ExportCoordinates(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Comp : IPCB_Component;
    JsonItems, Designator, Footprint, LayerStr, Comment : String;
    First : Boolean;
    Count : Integer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    Comp := Iterator.FirstPCBObject;
    While Comp <> Nil Do
    Begin
        If Not First Then JsonItems := JsonItems + ',';
        First := False;

        Try Designator := Comp.Name.Text; Except Designator := ''; End;
        Try Footprint := Comp.Pattern; Except Footprint := ''; End;
        Try LayerStr := GetLayerString(Comp.Layer); Except LayerStr := 'Unknown'; End;
        Try Comment := Comp.Comment.Text; Except Comment := ''; End;

        JsonItems := JsonItems + '{"designator":"' + EscapeJsonString(Designator) + '",'
            + '"footprint":"' + EscapeJsonString(Footprint) + '",'
            + '"comment":"' + EscapeJsonString(Comment) + '",'
            + '"x":' + IntToStr(CoordToMils(Comp.x)) + ','
            + '"y":' + IntToStr(CoordToMils(Comp.y)) + ','
            + '"rotation":' + FloatToStr(Comp.Rotation) + ','
            + '"layer":"' + EscapeJsonString(LayerStr) + '",';
        If Comp.Layer = eTopLayer Then
            JsonItems := JsonItems + '"side":"Top"}'
        Else
            JsonItems := JsonItems + '"side":"Bottom"}';
        Inc(Count);
        Comp := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"placements":[' + JsonItems + '],"count":' + IntToStr(Count) + ','
        + '"board_name":"' + EscapeJsonString(ExtractFileName(Board.FileName)) + '"}');
End;

{..............................................................................}
{ PCB_SetBoardShape - Define the board outline as a rectangle                 }
{ Params: x1,y1,x2,y2 in mils (opposite corners, any order)                   }
{..............................................................................}

Function PCB_SetBoardShape(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    X1, Y1, X2, Y2, TmpI : Integer;
    Cx1, Cy1, Cx2, Cy2 : TCoord;
    Seg : TPolySegment;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);

    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    Cx1 := MilsToCoord(X1);  Cy1 := MilsToCoord(Y1);
    Cx2 := MilsToCoord(X2);  Cy2 := MilsToCoord(Y2);

    PCBServer.PreProcess;
    Try
        { IPCB_BoardOutline inherits from IPCB_Polygon. Per the verified
          DelphiScript idiom from the Altium addons repo, you assign a
          full TPolySegment record to Segments[I] rather than writing
          individual fields through the indexed property. Build each
          corner as a local record and assign in order. }
        Board.BoardOutline.PointCount := 4;
        Seg.Kind := ePolySegmentLine;

        Seg.vx := Cx1;  Seg.vy := Cy1;  Board.BoardOutline.Segments[0] := Seg;
        Seg.vx := Cx2;  Seg.vy := Cy1;  Board.BoardOutline.Segments[1] := Seg;
        Seg.vx := Cx2;  Seg.vy := Cy2;  Board.BoardOutline.Segments[2] := Seg;
        Seg.vx := Cx1;  Seg.vy := Cy2;  Board.BoardOutline.Segments[3] := Seg;

        Board.BoardOutline.Invalidate;
        Board.BoardOutline.Rebuild;
        Board.BoardOutline.Validate;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,'
        + '"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + '}');
End;

{..............................................................................}
{ PCB_PlacePolygonRect - Drop a copper polygon pour on a rectangular area     }
{ Params: x1,y1,x2,y2 in mils, net=<name>, layer=<layer>, pour_over=<bool>   }
{..............................................................................}

Function PCB_PlacePolygonRect(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Polygon : IPCB_Polygon;
    X1, Y1, X2, Y2, TmpI : Integer;
    Cx1, Cy1, Cx2, Cy2 : TCoord;
    NetStr, LayerStr, PourOverStr : String;
    FoundNet : IPCB_Net;
    Seg : TPolySegment;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    NetStr := ExtractJsonValue(Params, 'net');
    LayerStr := ExtractJsonValue(Params, 'layer');
    PourOverStr := ExtractJsonValue(Params, 'pour_over');

    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;
    Cx1 := MilsToCoord(X1);  Cy1 := MilsToCoord(Y1);
    Cx2 := MilsToCoord(X2);  Cy2 := MilsToCoord(Y2);

    PCBServer.PreProcess;
    Try
        Polygon := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
        If Polygon = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create polygon object');
            Exit;
        End;

        If LayerStr = '' Then LayerStr := 'TopLayer';
        Polygon.Layer := GetLayerFromString(LayerStr);

        { Assign each corner as a full TPolySegment record, per the
          verified pattern from Altium scripting reference examples —
          field-level writes through Segments[I] aren't supported. }
        Polygon.PointCount := 4;
        Seg.Kind := ePolySegmentLine;
        Seg.vx := Cx1;  Seg.vy := Cy1;  Polygon.Segments[0] := Seg;
        Seg.vx := Cx2;  Seg.vy := Cy1;  Polygon.Segments[1] := Seg;
        Seg.vx := Cx2;  Seg.vy := Cy2;  Polygon.Segments[2] := Seg;
        Seg.vx := Cx1;  Seg.vy := Cy2;  Polygon.Segments[3] := Seg;

        { Assign net if specified. }
        If NetStr <> '' Then
        Begin
            Try FoundNet := Board.GetNetByName(NetStr); Except FoundNet := Nil; End;
            If FoundNet <> Nil Then Polygon.Net := FoundNet;
        End;

        { PourOver controls whether the polygon pours over existing
          same-net primitives (tracks/pads). Default true — matches the
          common "pour GND plane" case. }
        If (PourOverStr = '') Or (LowerCase(PourOverStr) = 'true') Then
            Polygon.PourOver := ePolygonPourOver_Same
        Else
            Polygon.PourOver := ePolygonPourOver_None;

        Board.AddPCBObject(Polygon);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Polygon.I_ObjectAddress);
        Polygon.Rebuild;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"layer":"' + EscapeJsonString(LayerStr) + '",'
        + '"net":"' + EscapeJsonString(NetStr) + '"}');
End;

{..............................................................................}
{ PCB_PlaceViaArray - Stitch vias in a grid across a rectangle                }
{ Params: x1,y1,x2,y2 in mils, pitch=<mils>, net=<name>, size=<mils>,        }
{         hole_size=<mils>, low_layer=<layer>, high_layer=<layer>            }
{..............................................................................}

Function PCB_PlaceViaArray(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Via : IPCB_Via;
    X1, Y1, X2, Y2, TmpI, Pitch, ViaSize, ViaHole : Integer;
    NetStr, LowLayerStr, HighLayerStr : String;
    FoundNet : IPCB_Net;
    Ix, Iy, PlacedCount : Integer;
    LowLayer, HighLayer : TLayer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    Pitch := StrToIntDef(ExtractJsonValue(Params, 'pitch'), 50);
    ViaSize := StrToIntDef(ExtractJsonValue(Params, 'size'), 30);
    ViaHole := StrToIntDef(ExtractJsonValue(Params, 'hole_size'), 12);
    NetStr := ExtractJsonValue(Params, 'net');
    LowLayerStr := ExtractJsonValue(Params, 'low_layer');
    HighLayerStr := ExtractJsonValue(Params, 'high_layer');

    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;
    If Pitch < 10 Then Pitch := 10;   { Safety: clamp to sane minimum. }

    FoundNet := Nil;
    If NetStr <> '' Then
        Try FoundNet := Board.GetNetByName(NetStr); Except End;

    If LowLayerStr = '' Then LowLayer := eTopLayer
    Else LowLayer := GetLayerFromString(LowLayerStr);
    If HighLayerStr = '' Then HighLayer := eBottomLayer
    Else HighLayer := GetLayerFromString(HighLayerStr);

    PlacedCount := 0;
    PCBServer.PreProcess;
    Try
        Iy := Y1;
        While Iy <= Y2 Do
        Begin
            Ix := X1;
            While Ix <= X2 Do
            Begin
                Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
                If Via <> Nil Then
                Begin
                    Via.x := MilsToCoord(Ix);
                    Via.y := MilsToCoord(Iy);
                    Via.Size := MilsToCoord(ViaSize);
                    Via.HoleSize := MilsToCoord(ViaHole);
                    Via.LowLayer := LowLayer;
                    Via.HighLayer := HighLayer;
                    If FoundNet <> Nil Then Via.Net := FoundNet;
                    Board.AddPCBObject(Via);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                        PCBM_BoardRegisteration, Via.I_ObjectAddress);
                    Inc(PlacedCount);
                End;
                Ix := Ix + Pitch;
            End;
            Iy := Iy + Pitch;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(PlacedCount) + ','
        + '"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"pitch":' + IntToStr(Pitch) + ','
        + '"net":"' + EscapeJsonString(NetStr) + '"}');
End;

{..............................................................................}
{ PCB_CreateDiffPair - Create a differential-pair object from two net names  }
{ Params: name=<diff pair name>, positive_net=<net>, negative_net=<net>      }
{..............................................................................}

Function PCB_CreateDiffPair(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    DiffPair : IPCB_DifferentialPair;
    DPName, PosNet, NegNet : String;
    PosNetObj, NegNetObj : IPCB_Net;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DPName := ExtractJsonValue(Params, 'name');
    PosNet := ExtractJsonValue(Params, 'positive_net');
    NegNet := ExtractJsonValue(Params, 'negative_net');

    If (PosNet = '') Or (NegNet = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'positive_net and negative_net are required');
        Exit;
    End;
    If DPName = '' Then DPName := PosNet + '_' + NegNet;

    PosNetObj := Nil;
    NegNetObj := Nil;
    Try PosNetObj := Board.GetNetByName(PosNet); Except End;
    Try NegNetObj := Board.GetNetByName(NegNet); Except End;
    If (PosNetObj = Nil) Or (NegNetObj = Nil) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NET_NOT_FOUND',
            'Could not find one or both nets on the board');
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        DiffPair := PCBServer.PCBObjectFactory(eDifferentialPairObject, eNoDimension, eCreate_Default);
        If DiffPair = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create diff pair object');
            Exit;
        End;

        DiffPair.Name := DPName;
        DiffPair.PositiveNet := PosNetObj;
        DiffPair.NegativeNet := NegNetObj;

        Board.AddPCBObject(DiffPair);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, DiffPair.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"created":true,"name":"' + EscapeJsonString(DPName) + '",'
        + '"positive_net":"' + EscapeJsonString(PosNet) + '",'
        + '"negative_net":"' + EscapeJsonString(NegNet) + '"}');
End;

{..............................................................................}
{ PCB_PlaceRegion - Drop a solid copper region (no net) on a rectangle        }
{ Params: x1,y1,x2,y2 in mils, layer=<layer>, net=<optional>                  }
{ Regions are solid primitives without a net; they don't participate in the   }
{ connectivity engine. Use place_polygon_rect for a net-associated pour.      }
{..............................................................................}

Function PCB_PlaceRegion(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Region : IPCB_Region;
    Contour : IPCB_Contour;
    X1, Y1, X2, Y2, TmpI : Integer;
    Cx1, Cy1, Cx2, Cy2 : TCoord;
    LayerStr, NetStr : String;
    FoundNet : IPCB_Net;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    LayerStr := ExtractJsonValue(Params, 'layer');
    NetStr := ExtractJsonValue(Params, 'net');

    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;
    Cx1 := MilsToCoord(X1);  Cy1 := MilsToCoord(Y1);
    Cx2 := MilsToCoord(X2);  Cy2 := MilsToCoord(Y2);

    PCBServer.PreProcess;
    Try
        Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
        If Region = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create region object');
            Exit;
        End;

        If LayerStr = '' Then LayerStr := 'TopLayer';
        Region.Layer := GetLayerFromString(LayerStr);

        { IPCB_Region uses MainContour + SetOutlineContour (NOT the polygon
          Segments API). Pattern taken from the Altium-Designer-addons
          scripting-reference CreatePCBObjects.PAS example — the contour
          X[I]/Y[I] arrays are 1-based (not 0-based). }
        Contour := Region.MainContour.Replicate;
        Contour.Count := 4;
        Contour.X[1] := Cx1;  Contour.Y[1] := Cy1;
        Contour.X[2] := Cx2;  Contour.Y[2] := Cy1;
        Contour.X[3] := Cx2;  Contour.Y[3] := Cy2;
        Contour.X[4] := Cx1;  Contour.Y[4] := Cy2;
        Region.SetOutlineContour(Contour);

        If NetStr <> '' Then
        Begin
            Try FoundNet := Board.GetNetByName(NetStr); Except FoundNet := Nil; End;
            If FoundNet <> Nil Then Region.Net := FoundNet;
        End;

        Board.AddPCBObject(Region);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Region.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"layer":"' + EscapeJsonString(LayerStr) + '",'
        + '"net":"' + EscapeJsonString(NetStr) + '"}');
End;

{..............................................................................}
{ PCB_DistributeComponents - Evenly space components along X or Y             }
{ Params: designators=<comma list>, axis=x|y, start=<mils>, end=<mils>        }
{..............................................................................}

Function PCB_DistributeComponents(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    DesStr, AxisStr, StartStr, EndStr, Remaining, DesName : String;
    AxisX : Boolean;
    StartVal, EndVal, CommaPos, Count, I : Integer;
    Step : Double;
    NewPos : Integer;
    CompList : TInterfaceList;
    Comp : IPCB_Component;
    Iterator : IPCB_BoardIterator;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DesStr := ExtractJsonValue(Params, 'designators');
    AxisStr := LowerCase(ExtractJsonValue(Params, 'axis'));
    StartStr := ExtractJsonValue(Params, 'start');
    EndStr := ExtractJsonValue(Params, 'end');

    If DesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designators required');
        Exit;
    End;
    If (StartStr = '') Or (EndStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'start and end required');
        Exit;
    End;
    If AxisStr = '' Then AxisStr := 'x';
    AxisX := (AxisStr = 'x');
    StartVal := StrToIntDef(StartStr, 0);
    EndVal := StrToIntDef(EndStr, 0);

    { Collect components that match the comma list, preserving list order. }
    CompList := CreateObject(TInterfaceList);
    Remaining := DesStr;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            DesName := Copy(Remaining, 1, CommaPos - 1);
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else
        Begin
            DesName := Remaining;
            Remaining := '';
        End;
        If DesName = '' Then Continue;

        Comp := Iterator.FirstPCBObject;
        While Comp <> Nil Do
        Begin
            Try
                If Comp.Name.Text = DesName Then
                Begin
                    CompList.Add(Comp);
                    Break;
                End;
            Except End;
            Comp := Iterator.NextPCBObject;
        End;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Count := CompList.Count;
    If Count < 2 Then
    Begin
        CompList.Free;
        Result := BuildErrorResponse(RequestId, 'TOO_FEW',
            'Need at least 2 components to distribute (matched ' + IntToStr(Count) + ')');
        Exit;
    End;

    Step := (EndVal - StartVal) / (Count - 1);

    PCBServer.PreProcess;
    Try
        For I := 0 To Count - 1 Do
        Begin
            Comp := CompList.Items[I];
            If Comp = Nil Then Continue;
            NewPos := StartVal + Round(Step * I);
            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                PCBM_BeginModify, c_NoEventData);
            If AxisX Then
                Comp.x := MilsToCoord(NewPos)
            Else
                Comp.y := MilsToCoord(NewPos);
            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                PCBM_EndModify, c_NoEventData);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    CompList.Free;

    Result := BuildSuccessResponse(RequestId,
        '{"distributed":' + IntToStr(Count) + ','
        + '"axis":"' + EscapeJsonString(AxisStr) + '",'
        + '"start":' + IntToStr(StartVal) + ',"end":' + IntToStr(EndVal) + '}');
End;

{..............................................................................}
{ PCB_PlaceDimension - Place a linear dimension (horizontal or vertical)      }
{ Params: x1,y1,x2,y2 in mils, layer=<layer>, orientation=horizontal|vertical }
{..............................................................................}

Function PCB_PlaceDimension(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Dim : IPCB_Dimension;
    X1, Y1, X2, Y2, TextX, TextY : Integer;
    Orient, LayerStr : String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    LayerStr := ExtractJsonValue(Params, 'layer');
    Orient := LowerCase(ExtractJsonValue(Params, 'orientation'));

    If LayerStr = '' Then LayerStr := 'TopOverlay';
    { Auto-detect orientation if unset: whichever axis has the larger delta. }
    If Orient = '' Then
    Begin
        If Abs(X2 - X1) >= Abs(Y2 - Y1) Then Orient := 'horizontal'
        Else Orient := 'vertical';
    End;

    { Centre the text label between the two endpoints with a small offset
      along the perpendicular axis so it doesn't sit on top of geometry. }
    If Orient = 'horizontal' Then
    Begin
        TextX := (X1 + X2) Div 2;
        TextY := Y1 + 50;
    End
    Else
    Begin
        TextX := X1 + 50;
        TextY := (Y1 + Y2) Div 2;
    End;

    PCBServer.PreProcess;
    Try
        Dim := PCBServer.PCBObjectFactory(eDimensionObject, eLinearDimension, eCreate_Default);
        If Dim = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create dimension');
            Exit;
        End;

        Dim.Layer := GetLayerFromString(LayerStr);
        Dim.DimensionKind := eLinearDimension;
        Dim.X1Location := MilsToCoord(X1);
        Dim.Y1Location := MilsToCoord(Y1);
        { Size is the dimension extent; for horizontal linear it's delta-X,
          for vertical linear it's delta-Y. Negative is clamped to absolute. }
        If Orient = 'horizontal' Then
            Dim.Size := MilsToCoord(Abs(X2 - X1))
        Else
            Dim.Size := MilsToCoord(Abs(Y2 - Y1));

        Dim.TextX := MilsToCoord(TextX);
        Dim.TextY := MilsToCoord(TextY);

        Board.AddPCBObject(Dim);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Dim.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + IntToStr(X1) + ',"y1":' + IntToStr(Y1) + ','
        + '"x2":' + IntToStr(X2) + ',"y2":' + IntToStr(Y2) + ','
        + '"orientation":"' + EscapeJsonString(Orient) + '",'
        + '"layer":"' + EscapeJsonString(LayerStr) + '"}');
End;

{..............................................................................}
{ PCB_PlacePad - Place a standalone pad (fiducial, test point, mounting hole) }
{ Params: x,y in mils, name=<designator>, net=<name>, shape=round|rect|oct,  }
{         x_size=<mils>, y_size=<mils>, hole_size=<mils>, layer=<layer>      }
{..............................................................................}

Function PCB_PlacePad(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Pad : IPCB_Pad;
    X, Y, XSize, YSize, HoleSize : Integer;
    Shape, NameStr, NetStr, LayerStr : String;
    FoundNet : IPCB_Net;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    XSize := StrToIntDef(ExtractJsonValue(Params, 'x_size'), 60);
    YSize := StrToIntDef(ExtractJsonValue(Params, 'y_size'), 60);
    HoleSize := StrToIntDef(ExtractJsonValue(Params, 'hole_size'), 0);
    Shape := LowerCase(ExtractJsonValue(Params, 'shape'));
    NameStr := ExtractJsonValue(Params, 'name');
    NetStr := ExtractJsonValue(Params, 'net');
    LayerStr := ExtractJsonValue(Params, 'layer');

    If LayerStr = '' Then LayerStr := 'TopLayer';
    If Shape = '' Then Shape := 'round';

    PCBServer.PreProcess;
    Try
        Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
        If Pad = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create pad');
            Exit;
        End;

        Pad.X := MilsToCoord(X);
        Pad.Y := MilsToCoord(Y);
        Pad.TopXSize := MilsToCoord(XSize);
        Pad.TopYSize := MilsToCoord(YSize);
        Pad.HoleSize := MilsToCoord(HoleSize);
        Pad.Layer := GetLayerFromString(LayerStr);
        If NameStr <> '' Then Pad.Name := NameStr;

        If Shape = 'rect' Then Pad.TopShape := eRectangular
        Else If Shape = 'oct' Then Pad.TopShape := eOctagonal
        Else Pad.TopShape := eRounded;

        If NetStr <> '' Then
        Begin
            Try FoundNet := Board.GetNetByName(NetStr); Except FoundNet := Nil; End;
            If FoundNet <> Nil Then Pad.Net := FoundNet;
        End;

        Board.AddPCBObject(Pad);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Pad.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + ','
        + '"x_size":' + IntToStr(XSize) + ',"y_size":' + IntToStr(YSize) + ','
        + '"hole_size":' + IntToStr(HoleSize) + ','
        + '"shape":"' + EscapeJsonString(Shape) + '",'
        + '"layer":"' + EscapeJsonString(LayerStr) + '",'
        + '"name":"' + EscapeJsonString(NameStr) + '",'
        + '"net":"' + EscapeJsonString(NetStr) + '"}');
End;

{..............................................................................}
{ PCB_PlaceAngularDimension - Place an angular dimension (arc between 2 axes) }
{ Params: center_x, center_y, x1,y1, x2,y2 in mils, radius in mils,          }
{         layer=<layer>                                                      }
{..............................................................................}

Function PCB_PlaceAngularDimension(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Dim : IPCB_Dimension;
    Cx, Cy, X1, Y1, X2, Y2, Radius : Integer;
    LayerStr : String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Cx := StrToIntDef(ExtractJsonValue(Params, 'center_x'), 0);
    Cy := StrToIntDef(ExtractJsonValue(Params, 'center_y'), 0);
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    Radius := StrToIntDef(ExtractJsonValue(Params, 'radius'), 100);
    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then LayerStr := 'TopOverlay';

    PCBServer.PreProcess;
    Try
        Dim := PCBServer.PCBObjectFactory(eDimensionObject, eAngularDimension, eCreate_Default);
        If Dim = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create angular dimension');
            Exit;
        End;
        Dim.Layer := GetLayerFromString(LayerStr);
        Dim.DimensionKind := eAngularDimension;
        Dim.X1Location := MilsToCoord(Cx);
        Dim.Y1Location := MilsToCoord(Cy);
        Dim.TextX := MilsToCoord(Cx);
        Dim.TextY := MilsToCoord(Cy + Radius + 20);
        Board.AddPCBObject(Dim);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Dim.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"kind":"angular","center_x":' + IntToStr(Cx)
        + ',"center_y":' + IntToStr(Cy) + ',"radius":' + IntToStr(Radius)
        + ',"layer":"' + EscapeJsonString(LayerStr) + '"}');
End;

{..............................................................................}
{ PCB_PlaceRadialDimension - Place a radial dimension around a center point   }
{ Params: center_x, center_y, radius in mils, layer=<layer>                   }
{..............................................................................}

Function PCB_PlaceRadialDimension(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Dim : IPCB_Dimension;
    Cx, Cy, Radius : Integer;
    LayerStr : String;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Cx := StrToIntDef(ExtractJsonValue(Params, 'center_x'), 0);
    Cy := StrToIntDef(ExtractJsonValue(Params, 'center_y'), 0);
    Radius := StrToIntDef(ExtractJsonValue(Params, 'radius'), 100);
    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then LayerStr := 'TopOverlay';

    PCBServer.PreProcess;
    Try
        Dim := PCBServer.PCBObjectFactory(eDimensionObject, eRadialDimension, eCreate_Default);
        If Dim = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create radial dimension');
            Exit;
        End;
        Dim.Layer := GetLayerFromString(LayerStr);
        Dim.DimensionKind := eRadialDimension;
        Dim.X1Location := MilsToCoord(Cx);
        Dim.Y1Location := MilsToCoord(Cy);
        Dim.Size := MilsToCoord(Radius);
        Dim.TextX := MilsToCoord(Cx + Radius);
        Dim.TextY := MilsToCoord(Cy + Radius);
        Board.AddPCBObject(Dim);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Dim.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"kind":"radial","center_x":' + IntToStr(Cx)
        + ',"center_y":' + IntToStr(Cy) + ',"radius":' + IntToStr(Radius)
        + ',"layer":"' + EscapeJsonString(LayerStr) + '"}');
End;

{..............................................................................}
{ PCB_PlaceEmbeddedBoard - Place an IPCB_EmbeddedBoard array (paneling).       }
{ The embedded-board primitive is a grid of child-PCB copies, used for panel  }
{ designs and multi-up arrays. Spacing values are in mils.                    }
{ Params: x, y (bottom-left corner, mils), child_path (full path to the child }
{         .PcbDoc), rows, cols, row_spacing_mils, col_spacing_mils,           }
{         mirror (true/false), layer (default TopLayer).                      }
{..............................................................................}

Function PCB_PlaceEmbeddedBoard(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Emb : IPCB_Primitive;
    ChildPath, LayerStr, MirrorStr : String;
    X, Y, Rows, Cols, RowSpace, ColSpace : Integer;
    TargetLayer : TLayer;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    ChildPath := ExtractJsonValue(Params, 'child_path');
    If ChildPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'child_path is required');
        Exit;
    End;
    ChildPath := StringReplace(ChildPath, '\\', '\', -1);

    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Rows := StrToIntDef(ExtractJsonValue(Params, 'rows'), 1);
    Cols := StrToIntDef(ExtractJsonValue(Params, 'cols'), 1);
    RowSpace := StrToIntDef(ExtractJsonValue(Params, 'row_spacing_mils'), 0);
    ColSpace := StrToIntDef(ExtractJsonValue(Params, 'col_spacing_mils'), 0);
    LayerStr := ExtractJsonValue(Params, 'layer');
    MirrorStr := LowerCase(ExtractJsonValue(Params, 'mirror'));

    If LayerStr <> '' Then
        TargetLayer := GetLayerFromString(LayerStr)
    Else
        TargetLayer := eTopLayer;

    Emb := PCBServer.PCBObjectFactory(eEmbeddedBoardObject, eNoDimension, eCreate_Default);
    If Emb = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create embedded board');
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Try Emb.Layer := TargetLayer; Except End;
        Try Emb.XLocation := MilsToCoord(X); Except End;
        Try Emb.YLocation := MilsToCoord(Y); Except End;
        Try Emb.DocumentPath := ChildPath; Except End;
        Try Emb.RowCount := Rows; Except End;
        Try Emb.ColCount := Cols; Except End;
        If RowSpace > 0 Then
            Try Emb.RowSpacing := MilsToCoord(RowSpace); Except End;
        If ColSpace > 0 Then
            Try Emb.ColSpacing := MilsToCoord(ColSpace); Except End;
        If (MirrorStr = 'true') Or (MirrorStr = '1') Then
            Try Emb.MirrorFlag := True; Except End;

        Board.AddPCBObject(Emb);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Emb.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"child_path":"' + EscapeJsonString(ChildPath) + '",'
        + '"rows":' + IntToStr(Rows) + ',"cols":' + IntToStr(Cols)
        + ',"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
End;

{..............................................................................}
{ HandlePCBCommand - Route PCB actions to handlers                            }
{..............................................................................}

Function HandlePCBCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'get_nets':                Result := PCB_GetNets(Params, RequestId);
        'get_net_classes':         Result := PCB_GetNetClasses(Params, RequestId);
        'create_net_class':        Result := PCB_CreateNetClass(Params, RequestId);
        'get_design_rules':        Result := PCB_GetDesignRules(Params, RequestId);
        'get_rule_properties':     Result := PCB_GetRuleProperties(Params, RequestId);
        'set_rule_properties':     Result := PCB_SetRuleProperties(Params, RequestId);
        'run_drc':                 Result := PCB_RunDRC(Params, RequestId);
        'get_components':          Result := PCB_GetComponents(Params, RequestId);
        'move_component':          Result := PCB_MoveComponent(Params, RequestId);
        'batch_move_components':   Result := PCB_BatchMoveComponents(Params, RequestId);
        'get_trace_lengths':       Result := PCB_GetTraceLengths(Params, RequestId);
        'get_layer_stackup':       Result := PCB_GetLayerStackup(Params, RequestId);
        'add_layer':               Result := PCB_AddLayer(Params, RequestId);
        'remove_layer':            Result := PCB_RemoveLayer(Params, RequestId);
        'modify_layer':            Result := PCB_ModifyLayer(Params, RequestId);
        'get_board_outline':       Result := PCB_GetBoardOutline(Params, RequestId);
        'get_selected_objects':    Result := PCB_GetSelectedObjects(Params, RequestId);
        'set_layer_visibility':    Result := PCB_SetLayerVisibility(Params, RequestId);
        'repour_polygons':         Result := PCB_RepourPolygons(Params, RequestId);
        'place_via':               Result := PCB_PlaceVia(Params, RequestId);
        'place_track':             Result := PCB_PlaceTrack(Params, RequestId);
        'place_tracks':            Result := PCB_PlaceTracks(Params, RequestId);
        'place_arc':               Result := PCB_PlaceArc(Params, RequestId);
        'place_text':              Result := PCB_PlaceText(Params, RequestId);
        'place_fill':              Result := PCB_PlaceFill(Params, RequestId);
        'start_polygon_placement': Result := PCB_StartPolygonPlacement(Params, RequestId);
        'create_design_rule':      Result := PCB_CreateDesignRule(Params, RequestId);
        'delete_design_rule':      Result := PCB_DeleteDesignRule(Params, RequestId);
        'get_component_pads':      Result := PCB_GetComponentPads(Params, RequestId);
        'flip_component':          Result := PCB_FlipComponent(Params, RequestId);
        'align_components':        Result := PCB_AlignComponents(Params, RequestId);
        'get_clearance_violations': Result := PCB_GetClearanceViolations(Params, RequestId);
        'snap_to_grid':            Result := PCB_SnapToGrid(Params, RequestId);
        'get_diff_pair_rules':     Result := PCB_GetDiffPairRules(Params, RequestId);
        'get_vias':                Result := PCB_GetVias(Params, RequestId);
        'delete_object':           Result := PCB_DeleteObject(Params, RequestId);
        'get_pad_properties':      Result := PCB_GetPadProperties(Params, RequestId);
        'set_track_width':         Result := PCB_SetTrackWidth(Params, RequestId);
        'get_unrouted_nets':       Result := PCB_GetUnroutedNets(Params, RequestId);
        'get_polygons':            Result := PCB_GetPolygons(Params, RequestId);
        'modify_polygon':          Result := PCB_ModifyPolygon(Params, RequestId);
        'get_room_rules':          Result := PCB_GetRoomRules(Params, RequestId);
        'create_room':             Result := PCB_CreateRoom(Params, RequestId);
        'get_board_statistics':    Result := PCB_GetBoardStatistics(Params, RequestId);
        'export_coordinates':      Result := PCB_ExportCoordinates(Params, RequestId);
        'set_board_shape':         Result := PCB_SetBoardShape(Params, RequestId);
        'place_polygon_rect':      Result := PCB_PlacePolygonRect(Params, RequestId);
        'place_via_array':         Result := PCB_PlaceViaArray(Params, RequestId);
        'create_diff_pair':        Result := PCB_CreateDiffPair(Params, RequestId);
        'place_region':            Result := PCB_PlaceRegion(Params, RequestId);
        'distribute_components':   Result := PCB_DistributeComponents(Params, RequestId);
        'place_dimension':         Result := PCB_PlaceDimension(Params, RequestId);
        'place_pad':               Result := PCB_PlacePad(Params, RequestId);
        'place_angular_dimension': Result := PCB_PlaceAngularDimension(Params, RequestId);
        'place_radial_dimension':  Result := PCB_PlaceRadialDimension(Params, RequestId);
        'place_embedded_board':    Result := PCB_PlaceEmbeddedBoard(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown PCB action: ' + Action);
    End;
End;
