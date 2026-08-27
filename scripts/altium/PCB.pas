{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
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

{ Find a net by name, or create it if missing (used to wire up pad nets when  }
{ populating a board without an ECO). Net creation is the standard            }
{ PCBObjectFactory(eNetObject) + AddPCBObject pattern.                        }
Function EnsureNet(Board : IPCB_Board; NetName : String) : IPCB_Net;
Var
    Net : IPCB_Net;
Begin
    Result := Nil;
    If NetName = '' Then Exit;
    Result := FindNetByName(Board, NetName);
    If Result <> Nil Then Exit;
    Net := PCBServer.PCBObjectFactory(eNetObject, eNoDimension, eCreate_Default);
    If Net = Nil Then Exit;
    Net.Name := NetName;
    Board.AddPCBObject(Net);
    Result := Net;
End;

{ Look up a pad's net in a pipe-delimited "padname=netname|..." string.        }
Function GetPadNet(PadNetsStr, PadName : String) : String;
Var
    Token, Remaining, K, V : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
    Remaining := PadNetsStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Token := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Token := Remaining;
            Remaining := '';
        End;
        EqPos := Pos('=', Token);
        If EqPos > 0 Then
        Begin
            K := Copy(Token, 1, EqPos - 1);
            V := Copy(Token, EqPos + 1, Length(Token));
            If K = PadName Then
            Begin
                Result := V;
                Exit;
            End;
        End;
    End;
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
    Board := GetPCBBoardAnywhere;
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
{ PCB_DeleteNets - Remove nets from the board.                                 }
{ Params: nets (comma-separated names; empty = all empty nets), force (bool). }
{ A net with connected primitives is skipped unless force=true (forcing       }
{ orphans those pads/tracks). Empty nets (no connections) are the common      }
{ cleanup target left behind after deleting components.                       }
{..............................................................................}

Function PCB_DeleteNets(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Prim : IPCB_Primitive;
    Net : IPCB_Net;
    Connected, Targets, ToDelete : TStringList;
    NetsStr, ForceStr, NName, Skipped : String;
    Force, WantThis : Boolean;
    I, DeletedCount, SkippedCount : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetsStr := ExtractJsonValue(Params, 'nets');
    ForceStr := LowerCase(ExtractJsonValue(Params, 'force'));
    Force := (ForceStr = 'true') Or (ForceStr = '1');

    Connected := TStringList.Create;
    Targets := TStringList.Create;
    ToDelete := TStringList.Create;
    DeletedCount := 0;
    SkippedCount := 0;
    Skipped := '';

    { Optional explicit name list (comma-separated). Empty NetsStr means    }
    { "all empty nets". Inline split (no Split helper in this engine).       }
    NName := NetsStr;
    While NName <> '' Do
    Begin
        I := Pos(',', NName);
        If I > 0 Then
        Begin
            Targets.Add(Copy(NName, 1, I - 1));
            NName := Copy(NName, I + 1, Length(NName));
        End
        Else
        Begin
            Targets.Add(NName);
            NName := '';
        End;
    End;

    { Collect the set of net names that have at least one connected         }
    { primitive, so "empty" nets can be distinguished from in-use ones.     }
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eViaObject, ePadObject,
        eArcObject, eFillObject, ePolyObject, eRegionObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Prim := Iter.FirstPCBObject;
    While Prim <> Nil Do
    Begin
        Try
            If Prim.Net <> Nil Then
            Begin
                NName := Prim.Net.Name;
                If Connected.IndexOf(NName) < 0 Then Connected.Add(NName);
            End;
        Except End;
        Prim := Iter.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iter);

    { Decide which nets to delete. }
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eNetObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Net := Iter.FirstPCBObject;
    While Net <> Nil Do
    Begin
        NName := Net.Name;
        WantThis := (Targets.Count = 0) Or (Targets.IndexOf(NName) >= 0);
        If WantThis Then
        Begin
            If (Connected.IndexOf(NName) >= 0) And (Not Force) Then
            Begin
                If Skipped <> '' Then Skipped := Skipped + ',';
                Skipped := Skipped + '"' + EscapeJsonString(NName) + '"';
                SkippedCount := SkippedCount + 1;
            End
            Else
                ToDelete.Add(NName);
        End;
        Net := Iter.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iter);

    { Remove by re-finding each name (don't hold net refs across removal). }
    PCBServer.PreProcess;
    Try
        For I := 0 To ToDelete.Count - 1 Do
        Begin
            Net := FindNetByName(Board, ToDelete[I]);
            If Net <> Nil Then
            Begin
                Board.RemovePCBObject(Net);
                DeletedCount := DeletedCount + 1;
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Connected.Free;
    Targets.Free;
    ToDelete.Free;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"deleted":' + IntToStr(DeletedCount)
        + ',"skipped_connected":' + IntToStr(SkippedCount)
        + ',"skipped_nets":[' + Skipped + ']}');
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
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;

    { MemberCount / MemberName[] are not exposed on IPCB_ObjectClass in         }
    { DelphiScript, they are compile-time undeclared. Return class metadata   }
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
    Board := GetPCBBoardAnywhere;
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
    Board := GetPCBBoardAnywhere;
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
{ PCB_SetRulesEnabled - Bulk toggle DRC-enabled flag on design rules by name.   }
{                                                                                }
{ Used for focused review passes: disable a noisy class of rules to surface     }
{ the violations that matter, or re-enable a set before a release sweep.        }
{ Matches rule names case-insensitively against the input list; supports a      }
{ trailing '*' wildcard on a name so the caller can target a rule family       }
{ without enumerating every name.                                                }
{                                                                                }
{ Two writable Enabled flags:                                                    }
{   Rule.Enabled    -- whether the rule participates in the rule list at all   }
{   Rule.DRCEnabled -- whether DRC actually checks this rule on its next run   }
{ For "focused review" the DRCEnabled flip is the right one to toggle.          }
{                                                                                }
{ Params: names (pipe-separated), enabled ("true"/"false"), match (optional;   }
{         "name" default, "kind" matches against rule_kind ordinal).            }
{ Response shape:                                                                }
{   matched -- int: rules that matched any input pattern                        }
{   updated -- int: how many actually changed value                              }
{   items[] -- array of per-rule name + kind + prev_enabled + new_enabled       }
{..............................................................................}

Function PCB_SetRulesEnabled(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Rule : IPCB_Rule;
    NamesStr, EnabledStr, MatchMode : String;
    NewEnabled, PrevEnabled, ShouldMatch : Boolean;
    Matched, Updated : Integer;
    ItemsJson, EntryJson, RuleNameUpper, Pattern, PatternUpper : String;
    First : Boolean;
    PipePos : Integer;
    Remaining : String;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    NamesStr := ExtractJsonValue(Params, 'names');
    EnabledStr := LowerCase(ExtractJsonValue(Params, 'enabled'));
    MatchMode := LowerCase(ExtractJsonValue(Params, 'match'));
    If MatchMode = '' Then MatchMode := 'name';
    NewEnabled := (EnabledStr = 'true') Or (EnabledStr = '1');

    If NamesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'names is required (pipe-separated rule names or kind ordinals)');
        Exit;
    End;

    Matched := 0;
    Updated := 0;
    ItemsJson := '';
    First := True;

    PCBServer.PreProcess;
    Try
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eRuleObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Rule := Iter.FirstPCBObject;
            While Rule <> Nil Do
            Begin
                Try
                    RuleNameUpper := UpperCase(Rule.Name);
                    ShouldMatch := False;
                    Remaining := NamesStr;
                    While (Length(Remaining) > 0) And (Not ShouldMatch) Do
                    Begin
                        PipePos := Pos('|', Remaining);
                        If PipePos = 0 Then
                        Begin
                            Pattern := Remaining;
                            Remaining := '';
                        End
                        Else
                        Begin
                            Pattern := Copy(Remaining, 1, PipePos - 1);
                            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
                        End;
                        Pattern := Trim(Pattern);
                        If Pattern = '' Then Continue;
                        If MatchMode = 'kind' Then
                        Begin
                            { Numeric ordinal match against Rule.RuleKind. }
                            If IntToStr(Rule.RuleKind) = Pattern Then
                                ShouldMatch := True;
                        End
                        Else
                        Begin
                            PatternUpper := UpperCase(Pattern);
                            If (Length(PatternUpper) > 0)
                               And (Copy(PatternUpper, Length(PatternUpper), 1) = '*') Then
                            Begin
                                { Trailing-* wildcard: prefix match. }
                                PatternUpper := Copy(PatternUpper, 1,
                                                     Length(PatternUpper) - 1);
                                If (Length(RuleNameUpper) >= Length(PatternUpper))
                                   And (Copy(RuleNameUpper, 1, Length(PatternUpper))
                                        = PatternUpper) Then
                                    ShouldMatch := True;
                            End
                            Else If RuleNameUpper = PatternUpper Then
                                ShouldMatch := True;
                        End;
                    End;

                    If ShouldMatch Then
                    Begin
                        Inc(Matched);
                        PrevEnabled := Rule.DRCEnabled;
                        If PrevEnabled <> NewEnabled Then
                        Begin
                            Rule.DRCEnabled := NewEnabled;
                            Inc(Updated);
                        End;
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('name', Rule.Name) + ',' +
                            JsonInt('kind', Rule.RuleKind) + ',' +
                            JsonBool('prev_enabled', PrevEnabled) + ',' +
                            JsonBool('new_enabled', NewEnabled);
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                Except End;
                Rule := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('matched', Matched) + ',' +
            JsonInt('updated', Updated) + ',' +
            JsonBool('enabled', NewEnabled) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;

{..............................................................................}
{ PCB_GetRuleProperties - Read properties of a design rule.                     }
{                                                                               }
{ Constraint values (Gap, MinWidth, MinHoleSize, impedance, etc.) are NOT       }
{ properties of the base IPCB_Rule interface, they live on the per-kind        }
{ subtypes (IPCB_ClearanceConstraint, IPCB_MaxMinWidthConstraint, etc.). The    }
{ kind-specific Pascal constants are not declared in every Altium build, so     }
{ accessing them directly compiles in some versions and crashes others with     }
{ "Undeclared identifier" errors that Try/Except cannot catch.                  }
{                                                                               }
{ Rule.Descriptor is a stable, documented string property on every IPCB_Rule    }
{ subtype that already contains all constraint values in human-readable form,   }
{ e.g. "Width Constraint (Min=0.18mm) (Max=0.19mm) (Preferred=0.185mm)".        }
{ Callers that need parsed values can split the descriptor, far safer than    }
{ dispatching on RuleKind to typed subtype access in script.                    }
{..............................................................................}

Function PCB_GetRuleProperties(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Rule : IPCB_Rule;
    RuleName : String;
Begin
    Board := GetPCBBoardAnywhere;
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

    Result := BuildSuccessResponse(RequestId,
        '{"name":"' + EscapeJsonString(Rule.Name) + '",'
        + '"rule_kind":' + IntToStr(Rule.RuleKind) + ','
        + '"enabled":' + BoolToJsonStr(Rule.Enabled) + ','
        + '"priority":' + IntToStr(Rule.Priority) + ','
        + '"descriptor":"' + EscapeJsonString(Rule.Descriptor) + '"}');
End;

{..............................................................................}
{ PCB_SetRuleProperties - Update metadata + constraint values of a rule.        }
{                                                                               }
{ Metadata params (writable on the base IPCB_Rule reference):                   }
{   enabled / scope1 / scope2 / comment                                          }
{                                                                               }
{ Priority is INTENTIONALLY not writable through this tool. Per the PCB API     }
{ reference (pcb-api-design-objects-interfaces-reference.html:15331), IPCB_Rule }
{ exposes `Function Priority : TRulePrecedence` as a read-only METHOD, not as   }
{ a writable property. Assigning `Rule.Priority := N` against that function-   }
{ kind property reference crashes the script engine at runtime with an         }
{ unreadable error popup. There is no SetState_Priority / SetPriority method   }
{ in the public SDK either. To change a rule's priority, use Altium's UI       }
{ (PCB > Rules and Constraints Editor, drag-reorder the rule in its category). }
{                                                                               }
{ Constraint params (dispatched by Rule.RuleKind, written through typed        }
{ iterator-returned locals per the ModifyWidthRules.pas reference pattern):    }
{   - Clearance (kind 0) + ComponentClearance (kind 24)                        }
{     + HoleToHoleClearance (kind 52):  gap_mils                                }
{   - MaxMinWidth (kind 2):  min_width_mils / max_width_mils / favored_width_mils}
{   - MaxMinHoleSize (kind 42):  min_hole_size_mils / max_hole_size_mils       }
{                                                                               }
{ Empirically determined kind 52 from the runtime rule list; the published     }
{ TRuleKind enum stops at 51 (DifferentialPairsRouting). Kinds 52+ are newer   }
{ Altium rule kinds that share the IPCB_ClearanceConstraint interface.         }
{                                                                               }
{ The cast `TypedLocal := UntypedLocal` (e.g. RuleWidth := Rule) does NOT       }
{ narrow the interface at runtime in DelphiScript, the typed variable keeps   }
{ behaving like the source IPCB_Rule and constraint-only property writes      }
{ crash the engine. The proven write path declares the typed variable AS the }
{ iterator-result type and assigns directly from BoardIterator.FirstPCBObject,}
{ where DelphiScript does narrow. See delphiscript_interface_narrowing.md     }
{ in user memory for details.                                                  }
{..............................................................................}

Function PCB_SetRuleProperties(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Rule : IPCB_Rule;
    Iter : IPCB_BoardIterator;
    RuleClearIter : IPCB_ClearanceConstraint;
    RuleWidthIter : IPCB_MaxMinWidthConstraint;
    RuleHoleIter : IPCB_MaxMinHoleSizeConstraint;
    RuleName, V, GapStr, MinWStr, MaxWStr, FavWStr, MinHStr, MaxHStr : String;
    UpdatedCount, Kind, ValMils : Integer;
    L : TLayer;
    Found : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
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
    Kind := 0;
    Try Kind := Rule.RuleKind; Except End;

    { -- Metadata path: writes against the base IPCB_Rule. priority is NOT    }
    { included, it is a read-only function and writing to it crashes the      }
    { engine. See the docstring above for details.                            }
    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Rule.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);

        V := ExtractJsonValue(Params, 'enabled');
        If V <> '' Then
        Begin
            Try Rule.Enabled := (V = 'true') Or (V = 'True') Or (V = '1'); Inc(UpdatedCount); Except End;
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

    { -- Constraint values: per-kind iterator with typed iterator-returned    }
    { locals. Each block opens its own BoardIterator, walks rule objects,    }
    { matches by name + kind, applies the write, breaks out. See the         }
    { ModifyWidthRules.pas reference pattern.                                  }
    GapStr := ExtractJsonValue(Params, 'gap_mils');
    MinWStr := ExtractJsonValue(Params, 'min_width_mils');
    MaxWStr := ExtractJsonValue(Params, 'max_width_mils');
    FavWStr := ExtractJsonValue(Params, 'favored_width_mils');
    MinHStr := ExtractJsonValue(Params, 'min_hole_size_mils');
    MaxHStr := ExtractJsonValue(Params, 'max_hole_size_mils');

    If (GapStr <> '') And
       ((Kind = eRule_Clearance) Or (Kind = 24) Or (Kind = 52)) Then
    Begin
        Iter := Board.BoardIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eRuleObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Found := False;
        Try
            RuleClearIter := Iter.FirstPCBObject;
            While (RuleClearIter <> Nil) And (Not Found) Do
            Begin
                If RuleClearIter.Name = RuleName Then
                Begin
                    Try
                        RuleClearIter.Gap := MilsToCoord(StrToIntDef(GapStr, 0));
                        Inc(UpdatedCount);
                    Except End;
                    Found := True;
                End;
                If Not Found Then RuleClearIter := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    End;

    If (Kind = eRule_MaxMinWidth) And
       ((MinWStr <> '') Or (MaxWStr <> '') Or (FavWStr <> '')) Then
    Begin
        Iter := Board.BoardIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eRuleObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Found := False;
        Try
            RuleWidthIter := Iter.FirstPCBObject;
            While (RuleWidthIter <> Nil) And (Not Found) Do
            Begin
                If (RuleWidthIter.RuleKind = eRule_MaxMinWidth)
                    And (RuleWidthIter.Name = RuleName) Then
                Begin
                    If MinWStr <> '' Then
                    Begin
                        ValMils := StrToIntDef(MinWStr, 0);
                        Try
                            For L := MinLayer To MaxLayer Do
                                RuleWidthIter.MinWidth(L) := MilsToCoord(ValMils);
                            Inc(UpdatedCount);
                        Except End;
                    End;
                    If MaxWStr <> '' Then
                    Begin
                        ValMils := StrToIntDef(MaxWStr, 0);
                        Try
                            For L := MinLayer To MaxLayer Do
                                RuleWidthIter.MaxWidth(L) := MilsToCoord(ValMils);
                            Inc(UpdatedCount);
                        Except End;
                    End;
                    If FavWStr <> '' Then
                    Begin
                        ValMils := StrToIntDef(FavWStr, 0);
                        Try
                            For L := MinLayer To MaxLayer Do
                                RuleWidthIter.FavoredWidth(L) := MilsToCoord(ValMils);
                            Inc(UpdatedCount);
                        Except End;
                    End;
                    Found := True;
                End;
                If Not Found Then RuleWidthIter := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    End;

    If (Kind = eRule_MaxMinHoleSize) And
       ((MinHStr <> '') Or (MaxHStr <> '')) Then
    Begin
        Iter := Board.BoardIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eRuleObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Found := False;
        Try
            RuleHoleIter := Iter.FirstPCBObject;
            While (RuleHoleIter <> Nil) And (Not Found) Do
            Begin
                If (RuleHoleIter.RuleKind = eRule_MaxMinHoleSize)
                    And (RuleHoleIter.Name = RuleName) Then
                Begin
                    If MinHStr <> '' Then
                    Begin
                        Try RuleHoleIter.MinLimit := MilsToCoord(StrToIntDef(MinHStr, 0)); Inc(UpdatedCount); Except End;
                    End;
                    If MaxHStr <> '' Then
                    Begin
                        Try RuleHoleIter.MaxLimit := MilsToCoord(StrToIntDef(MaxHStr, 0)); Inc(UpdatedCount); Except End;
                    End;
                    Found := True;
                End;
                If Not Found Then RuleHoleIter := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"name":"' + EscapeJsonString(Rule.Name) + '",'
        + '"rule_kind":' + IntToStr(Kind) + ','
        + '"properties_updated":' + IntToStr(UpdatedCount) + '}');
End;

{..............................................................................}
{ PCB_RunDRC - Run design rule check, return violation count                  }
{..............................................................................}

{ BuildViolationJson now emits LOCATION info so the agent can jump to       }
{ where the violation actually is, not just guess from the description.     }
{ Includes: violation bbox centre (x, y, layer) and each offending          }
{ primitive's own centre coords. With this the agent can drive the          }
{ dashboard's Drawing tab to the exact spot or call cross_probe + the       }
{ Components / Nets tabs.                                                    }
Function BuildViolationJson(Violation : IPCB_Violation) : String;
Var
    RuleName, P1Desc, P2Desc, P1Net, P2Net, P1Type, P2Type : String;
    P1X, P1Y, P2X, P2Y, VX, VY : Integer;
    P1Layer, P2Layer, VLayer : String;
    BBox : TCoordRect;
    Prim : IPCB_Primitive;
Begin
    Result := '{';
    Try Result := Result + '"name":"' + EscapeJsonString(Violation.Name) + '"'; Except Result := Result + '"name":""'; End;
    Try Result := Result + ',"description":"' + EscapeJsonString(Violation.Description) + '"'; Except End;
    RuleName := '';
    Try If Violation.Rule <> Nil Then RuleName := Violation.Rule.Name; Except End;
    Result := Result + ',"rule":"' + EscapeJsonString(RuleName) + '"';

    { Violation's own bounding rectangle -- usable as a "go here" hint.    }
    VX := 0; VY := 0; VLayer := '';
    Try
        BBox := Violation.BoundingRectangle;
        VX := CoordToMils((BBox.Left + BBox.Right) Div 2);
        VY := CoordToMils((BBox.Bottom + BBox.Top) Div 2);
    Except End;
    Try VLayer := GetLayerString(Violation.Layer); Except End;
    Result := Result + ',"x_mils":' + IntToStr(VX);
    Result := Result + ',"y_mils":' + IntToStr(VY);
    Result := Result + ',"layer":"' + EscapeJsonString(VLayer) + '"';

    P1Desc := ''; P1Net := ''; P1Type := ''; P1X := 0; P1Y := 0; P1Layer := '';
    Try
        Prim := Violation.Primitive1;
        If Prim <> Nil Then
        Begin
            Try P1Desc := Prim.Detail; Except End;
            Try If Prim.Net <> Nil Then P1Net := Prim.Net.Name; Except End;
            Try P1Type := ObjectIDToObjectName(Prim.ObjectId); Except End;
            Try
                BBox := Prim.BoundingRectangle;
                P1X := CoordToMils((BBox.Left + BBox.Right) Div 2);
                P1Y := CoordToMils((BBox.Bottom + BBox.Top) Div 2);
            Except End;
            Try P1Layer := GetLayerString(Prim.Layer); Except End;
        End;
    Except End;
    Result := Result + ',"primitive1":' + JsonObj(
        JsonStr('detail', P1Desc) + ',' +
        JsonStr('type', P1Type) + ',' +
        JsonStr('net', P1Net) + ',' +
        JsonStr('layer', P1Layer) + ',' +
        JsonInt('x_mils', P1X) + ',' +
        JsonInt('y_mils', P1Y)
    );

    P2Desc := ''; P2Net := ''; P2Type := ''; P2X := 0; P2Y := 0; P2Layer := '';
    Try
        Prim := Violation.Primitive2;
        If Prim <> Nil Then
        Begin
            Try P2Desc := Prim.Detail; Except End;
            Try If Prim.Net <> Nil Then P2Net := Prim.Net.Name; Except End;
            Try P2Type := ObjectIDToObjectName(Prim.ObjectId); Except End;
            Try
                BBox := Prim.BoundingRectangle;
                P2X := CoordToMils((BBox.Left + BBox.Right) Div 2);
                P2Y := CoordToMils((BBox.Bottom + BBox.Top) Div 2);
            Except End;
            Try P2Layer := GetLayerString(Prim.Layer); Except End;
        End;
    Except End;
    Result := Result + ',"primitive2":' + JsonObj(
        JsonStr('detail', P2Desc) + ',' +
        JsonStr('type', P2Type) + ',' +
        JsonStr('net', P2Net) + ',' +
        JsonStr('layer', P2Layer) + ',' +
        JsonInt('x_mils', P2X) + ',' +
        JsonInt('y_mils', P2Y)
    );
    Result := Result + '}';
End;

Function PCB_RunDRC(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    ViolationCount : Integer;
    Iterator : IPCB_BoardIterator;
    Violation : IPCB_Violation;
    JsonItems, AllowStr : String;
    First, AllowModal : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    { PCB:DesignRuleCheck is the documented process (TR0124 Server Process  }
    { Reference v1.5; "PCB:RunDRC" does not exist) -- but it raises the      }
    { MODAL "Design Rule Checker" setup dialog and BLOCKS this              }
    { single-threaded polling loop until a human clicks it. Observed: one   }
    { call left the loop dead for 30+ min, the client timed out at 1800 s,  }
    { and a scripting-engine access violation sat hidden behind the dialog. }
    { No documented parameter suppresses that dialog, so the trigger is     }
    { OPT-IN: the caller must pass allow_modal=true and accept the block.   }
    { Everything else reads the violations already stored on the board --   }
    { see PCB_GetClearanceViolations, which never triggers a run.           }
    AllowStr := LowerCase(ExtractJsonValue(Params, 'allow_modal'));
    AllowModal := (AllowStr = 'true') Or (AllowStr = '1');
    If Not AllowModal Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MODAL_BLOCKED',
            'PCB:DesignRuleCheck opens the modal Design Rule Checker dialog and '
            + 'blocks the bridge until a human closes it. Pass allow_modal=true '
            + 'to accept that block, or call pcb.get_clearance_violations to read '
            + 'the violations left on the board by the last DRC run.');
        Exit;
    End;

    ResetParameters;
    RunProcess('PCB:DesignRuleCheck');

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
        + '"drc_triggered":true,'
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
    BBox : TCoordRect;
    JsonItems, Designator, Footprint, LayerStr, CommentStr, SrcDesignator : String;
    First : Boolean;
    Count, HeightMils, BBoxX1, BBoxY1, BBoxX2, BBoxY2, BBoxW, BBoxH : Integer;
Begin
    Board := GetPCBBoardAnywhere;
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

        { Bounding rectangle for collision/placement planning. Returns the    }
        { current axis-aligned bounding box in mils, accounting for the      }
        { component's current rotation and side. Width / Height are derived. }
        BBoxX1 := 0; BBoxY1 := 0; BBoxX2 := 0; BBoxY2 := 0;
        BBoxW := 0; BBoxH := 0;
        Try
            BBox := Comp.BoundingRectangle;
            BBoxX1 := CoordToMils(BBox.X1);
            BBoxY1 := CoordToMils(BBox.Y1);
            BBoxX2 := CoordToMils(BBox.X2);
            BBoxY2 := CoordToMils(BBox.Y2);
            BBoxW := BBoxX2 - BBoxX1;
            BBoxH := BBoxY2 - BBoxY1;
        Except End;

        JsonItems := JsonItems + '{"designator":"' + EscapeJsonString(Designator) + '",'
            + '"comment":"' + EscapeJsonString(CommentStr) + '",'
            + '"x":' + IntToStr(CoordToMils(Comp.x)) + ','
            + '"y":' + IntToStr(CoordToMils(Comp.y)) + ','
            + '"rotation":' + FloatToJsonStr(Comp.Rotation) + ','
            + '"layer":"' + EscapeJsonString(LayerStr) + '",'
            + '"footprint":"' + EscapeJsonString(Footprint) + '",'
            + '"source_designator":"' + EscapeJsonString(SrcDesignator) + '",'
            + '"height_mils":' + IntToStr(HeightMils) + ','
            + '"bbox":{"x1":' + IntToStr(BBoxX1) + ',"y1":' + IntToStr(BBoxY1)
            + ',"x2":' + IntToStr(BBoxX2) + ',"y2":' + IntToStr(BBoxY2)
            + ',"width":' + IntToStr(BBoxW) + ',"height":' + IntToStr(BBoxH) + '}}';
        Inc(Count);
        Comp := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"components":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_CheckPlacementCollision - Dry-run check whether moving a component to a   }
{ proposed (x, y[, rotation]) would overlap any other placed component.         }
{                                                                              }
{ Params: designator (required), x (mils), y (mils), rotation (deg, optional;  }
{   defaults to the target's current rotation), margin_mils (optional clearance }
{   to require, default 0).                                                     }
{                                                                              }
{ Method: read target's current AABB to extract its footprint width / height,  }
{ rotate dimensions by 90/-90 deg if the rotation delta is a quarter turn      }
{ (swap w<->h), centre the predicted AABB on the proposed (x, y) using the     }
{ same reference-point-to-bbox-centre offset the component currently has, then }
{ AABB-overlap test against every other component on the board. Returns the    }
{ list of colliding designators and a count.                                    }
{                                                                              }
{ Caveats: this is an axis-aligned approximation. Components with non-square    }
{ footprints rotated by non-quarter-turn angles will have an inflated bounding }
{ box (treats the rotated polygon's AABB). Same-side check is applied (TopLayer}
{ to TopLayer, BotLayer to BotLayer); cross-side components never collide.     }
{..............................................................................}

Function PCB_CheckPlacementCollision(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Comp, Other : IPCB_Component;
    DesStr, XStr, YStr, RotStr, MarginStr : String;
    NewX, NewY, MarginCoord, Margin : Integer;
    NewRot, CurRot, RotDelta : Double;
    HasRot : Boolean;
    BBoxCur, BBoxOther : TCoordRect;
    Width, Height, NewW, NewH : Integer;
    OffsetX, OffsetY : Integer;
    NewBBoxX1, NewBBoxY1, NewBBoxX2, NewBBoxY2 : Integer;
    SwapWH : Boolean;
    JsonItems, OtherDes, TargetLayer : String;
    First : Boolean;
    CollisionCount : Integer;
    Overlap : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    DesStr := ExtractJsonValue(Params, 'designator');
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    RotStr := ExtractJsonValue(Params, 'rotation');
    MarginStr := ExtractJsonValue(Params, 'margin_mils');

    If (DesStr = '') Or (XStr = '') Or (YStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'designator, x, and y are required');
        Exit;
    End;

    Comp := Board.GetPcbComponentByRefDes(DesStr);
    If Comp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + DesStr);
        Exit;
    End;

    NewX := MilsToCoord(StrToIntDef(XStr, 0));
    NewY := MilsToCoord(StrToIntDef(YStr, 0));
    Margin := StrToIntDef(MarginStr, 0);
    MarginCoord := MilsToCoord(Margin);

    Try CurRot := Comp.Rotation; Except CurRot := 0; End;
    HasRot := RotStr <> '';
    If HasRot Then NewRot := StrToFloatDef(RotStr, CurRot)
    Else NewRot := CurRot;
    RotDelta := NewRot - CurRot;

    { Quarter-turn detection (mod 180). Anything within +-1 degree of 90 or  }
    { 270 swaps the AABB dimensions. Smaller rotations leave the AABB        }
    { intact at this approximation.                                          }
    SwapWH := False;
    If (Abs(RotDelta - 90) < 1) Or (Abs(RotDelta + 90) < 1)
        Or (Abs(RotDelta - 270) < 1) Or (Abs(RotDelta + 270) < 1) Then
        SwapWH := True;

    BBoxCur := Comp.BoundingRectangle;
    Width := BBoxCur.X2 - BBoxCur.X1;
    Height := BBoxCur.Y2 - BBoxCur.Y1;
    OffsetX := ((BBoxCur.X1 + BBoxCur.X2) Div 2) - Comp.x;
    OffsetY := ((BBoxCur.Y1 + BBoxCur.Y2) Div 2) - Comp.y;

    If SwapWH Then
    Begin
        NewW := Height;
        NewH := Width;
    End
    Else
    Begin
        NewW := Width;
        NewH := Height;
    End;

    { Predicted AABB centred on the proposed (NewX, NewY) plus the current   }
    { reference-to-centre offset (rotated trivially: swap and possibly flip  }
    { signs at quarter turns).                                                }
    NewBBoxX1 := NewX + OffsetX - (NewW Div 2) - MarginCoord;
    NewBBoxY1 := NewY + OffsetY - (NewH Div 2) - MarginCoord;
    NewBBoxX2 := NewX + OffsetX + (NewW Div 2) + MarginCoord;
    NewBBoxY2 := NewY + OffsetY + (NewH Div 2) + MarginCoord;

    Try TargetLayer := GetLayerString(Comp.Layer); Except TargetLayer := ''; End;

    JsonItems := '';
    First := True;
    CollisionCount := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Try
        Other := Iterator.FirstPCBObject;
        While Other <> Nil Do
        Begin
            OtherDes := '';
            Try OtherDes := Other.Name.Text; Except End;

            { Skip target itself. }
            If OtherDes = DesStr Then
            Begin
                Other := Iterator.NextPCBObject;
                Continue;
            End;

            { Cross-side components do not collide in plane. }
            Try
                If GetLayerString(Other.Layer) <> TargetLayer Then
                Begin
                    Other := Iterator.NextPCBObject;
                    Continue;
                End;
            Except End;

            BBoxOther := Other.BoundingRectangle;

            Overlap := (NewBBoxX1 <= BBoxOther.X2) And (NewBBoxX2 >= BBoxOther.X1)
                And (NewBBoxY1 <= BBoxOther.Y2) And (NewBBoxY2 >= BBoxOther.Y1);

            If Overlap Then
            Begin
                If Not First Then JsonItems := JsonItems + ',';
                First := False;
                JsonItems := JsonItems +
                    '{"designator":"' + EscapeJsonString(OtherDes) +
                    '","bbox":{"x1":' + IntToStr(CoordToMils(BBoxOther.X1)) +
                    ',"y1":' + IntToStr(CoordToMils(BBoxOther.Y1)) +
                    ',"x2":' + IntToStr(CoordToMils(BBoxOther.X2)) +
                    ',"y2":' + IntToStr(CoordToMils(BBoxOther.Y2)) + '}}';
                Inc(CollisionCount);
            End;

            Other := Iterator.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iterator);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(DesStr) + '"' +
        ',"proposed":{"x":' + IntToStr(CoordToMils(NewX)) +
        ',"y":' + IntToStr(CoordToMils(NewY)) +
        ',"rotation":' + FloatToJsonStr(NewRot) +
        ',"bbox":{"x1":' + IntToStr(CoordToMils(NewBBoxX1)) +
        ',"y1":' + IntToStr(CoordToMils(NewBBoxY1)) +
        ',"x2":' + IntToStr(CoordToMils(NewBBoxX2)) +
        ',"y2":' + IntToStr(CoordToMils(NewBBoxY2)) +
        '},"margin_mils":' + IntToStr(Margin) + '}' +
        ',"colliding_count":' + IntToStr(CollisionCount) +
        ',"clear":' + BoolToJsonStr(CollisionCount = 0) +
        ',"colliding":[' + JsonItems + ']}');
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
    Board := GetPCBBoardAnywhere;
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

    { Comp.X / Comp.Y are inherited writable properties from IPCB_Group     }
    { (Component's parent, per ref line 3887: "the X,Y fields inherited from }
    { IPCB_Group interface"). Direct assignment is the documented API. The   }
    { previous OleStr->Double crash was the locale-dependent StrToFloat in   }
    { the rotation path; fixed in Utils.pas StrToFloatDef.                    }
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
        + '"rotation":' + FloatToJsonStr(Comp.Rotation) + '}');
End;


{..............................................................................}
{ PCB_CopyComponentPlacement - Clone source-component placement onto dest    }
{ components by explicit mapping.                                             }
{                                                                              }
{ Copy layer + x + y + rotation from each source component to a              }
{ corresponding dest. Unlike a name-sort-based approach, this version       }
{ takes an explicit source -> dest mapping so an agent caller can be        }
{ precise.                                                                   }
{ Optional flags pass the designator + comment text placement (rotation /  }
{ size / layer / XY offset from component centre / NameOn).                 }
{                                                                              }
{ Param: "mapping" is pipe-separated; each entry is "src=dst" (e.g.          }
{        "U1=U2|R1=R5|C1=C8"). Optional flags include_designator (default     }
{        true) + include_comment (default true).                              }
{                                                                              }
{ Response shape: applied (int), failed (int), items[] each carrying        }
{ src, dst, ok, error.                                                        }
{..............................................................................}

Function PCB_CopyComponentPlacement(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Src, Dst : IPCB_Component;
    Mapping, Pair, Remaining, SrcDes, DstDes, EqStr : String;
    IncludeDes, IncludeComment : Boolean;
    PipePos, EqPos : Integer;
    Applied, Failed : Integer;
    ItemsJson, EntryJson, ErrStr : String;
    First, PairOk : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Mapping := ExtractJsonValue(Params, 'mapping');
    If Mapping = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'mapping is required (pipe-separated src=dst pairs)');
        Exit;
    End;
    EqStr := ExtractJsonValue(Params, 'include_designator');
    IncludeDes := Not ((EqStr = 'false') Or (EqStr = '0'));
    EqStr := ExtractJsonValue(Params, 'include_comment');
    IncludeComment := Not ((EqStr = 'false') Or (EqStr = '0'));

    Applied := 0;
    Failed := 0;
    ItemsJson := '';
    First := True;
    Remaining := Mapping;

    PCBServer.PreProcess;
    Try
        While Length(Remaining) > 0 Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then
            Begin
                Pair := Remaining;
                Remaining := '';
            End
            Else
            Begin
                Pair := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End;
            Pair := Trim(Pair);
            If Pair = '' Then Continue;

            EqPos := Pos('=', Pair);
            If EqPos <= 0 Then Continue;
            SrcDes := Trim(Copy(Pair, 1, EqPos - 1));
            DstDes := Trim(Copy(Pair, EqPos + 1, Length(Pair)));

            PairOk := False;
            ErrStr := '';
            Src := Nil;
            Dst := Nil;
            Try Src := Board.GetPcbComponentByRefDes(SrcDes); Except End;
            Try Dst := Board.GetPcbComponentByRefDes(DstDes); Except End;
            If Src = Nil Then ErrStr := 'src not found: ' + SrcDes
            Else If Dst = Nil Then ErrStr := 'dst not found: ' + DstDes
            Else
            Begin
                Try
                    PCBServer.SendMessageToRobots(Dst.I_ObjectAddress,
                        c_Broadcast, PCBM_BeginModify, c_NoEventData);
                    Try Dst.Layer := Src.Layer; Except End;
                    Try Dst.x := Src.x; Except End;
                    Try Dst.y := Src.y; Except End;
                    Try Dst.Rotation := Src.Rotation; Except End;
                    If IncludeDes Then
                    Begin
                        Try Dst.NameOn := Src.NameOn; Except End;
                        Try Dst.Name.XLocation := Src.Name.XLocation
                            - Src.x + Dst.x; Except End;
                        Try Dst.Name.YLocation := Src.Name.YLocation
                            - Src.y + Dst.y; Except End;
                        Try Dst.Name.Rotation := Src.Name.Rotation; Except End;
                        Try Dst.Name.Size := Src.Name.Size; Except End;
                        Try Dst.Name.Width := Src.Name.Width; Except End;
                        Try Dst.Name.Layer := Src.Name.Layer; Except End;
                    End;
                    If IncludeComment Then
                    Begin
                        Try Dst.CommentOn := Src.CommentOn; Except End;
                        Try Dst.Comment.XLocation := Src.Comment.XLocation
                            - Src.x + Dst.x; Except End;
                        Try Dst.Comment.YLocation := Src.Comment.YLocation
                            - Src.y + Dst.y; Except End;
                        Try Dst.Comment.Rotation := Src.Comment.Rotation; Except End;
                        Try Dst.Comment.Size := Src.Comment.Size; Except End;
                        Try Dst.Comment.Width := Src.Comment.Width; Except End;
                        Try Dst.Comment.Layer := Src.Comment.Layer; Except End;
                    End;
                    PCBServer.SendMessageToRobots(Dst.I_ObjectAddress,
                        c_Broadcast, PCBM_EndModify, c_NoEventData);
                    PairOk := True;
                    Inc(Applied);
                Except
                    ErrStr := 'apply exception';
                End;
            End;

            If Not PairOk Then Inc(Failed);
            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            EntryJson :=
                JsonStr('src', SrcDes) + ',' +
                JsonStr('dst', DstDes) + ',' +
                JsonBool('ok', PairOk) + ',' +
                JsonStr('error', ErrStr);
            ItemsJson := ItemsJson + JsonObj(EntryJson);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Try Board.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('applied', Applied) + ',' +
            JsonInt('failed', Failed) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{..............................................................................}
{ PCB_ReplicateLayout - Replicate a source channel's ROUTING onto a matching   }
{ destination channel (true multi-channel layout reuse).                       }
{                                                                              }
{ Unlike copy_component_placement (which only relocates components), this      }
{ copies the source group's routing primitives -- tracks, arcs, vias,         }
{ polygons, regions, fills -- onto the destination group and remaps each       }
{ copy's net from the source net to the corresponding destination net.        }
{                                                                              }
{ Positioning: a single RIGID transform is derived from the FIRST mapping pair }
{ (the anchor). Each copied primitive is rotated about the source anchor by    }
{ (dstRot - srcRot) then translated by (dstAnchor - srcAnchor), so the routing }
{ lands on the destination components in their existing location. The          }
{ destination components are NOT moved unless move_components=true.            }
{                                                                              }
{ Source routing identification (naming-agnostic): routing on nets INTERNAL to }
{ the source group -- every component pad on the net belongs to a mapped       }
{ source component. Nets that escape the group (shared GND / power) are        }
{ intentionally left alone; you do not replicate a global pour per channel.    }
{ An explicit "nets" override copies exactly those nets' routing instead.      }
{                                                                              }
{ Net remapping uses the explicit mapping (source pad net -> destination pad   }
{ net, matched by pad name) -- deterministic, not the geometric flood-fill the }
{ reference relied on.                                                          }
{                                                                              }
{ Params:                                                                       }
{   mapping          -- pipe-separated src=dst pairs (e.g. "U1=U2|R1=R5").     }
{                       First pair is the transform anchor.                    }
{   nets             -- (optional) pipe-separated source net names to copy,    }
{                       overriding the internal-net auto-detection.            }
{   move_components  -- (optional) "true" to also relocate the destination     }
{                       components onto the rigid transform (guarantees the    }
{                       routing aligns). Default false.                        }
{                                                                              }
{ Response: copied (int, primitives replicated), net_assigned (int),           }
{   internal_nets (int), shared_nets_skipped (int), congruence_warnings (int,  }
{   dst pairs that do not match the anchor transform -- routing may not align),}
{   notes (string).                                                            }
{..............................................................................}

Function PCB_ReplicateLayout(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Mapping, NetsOverride, Remaining, Pair, SrcDes, DstDes : String;
    MoveCompStr : String;
    MoveComps, UseOverride : Boolean;
    PipePos, EqPos, I : Integer;
    SrcList, DstList : TStringList;
    SrcGroup : TStringList;            { source refdes set                     }
    GroupNets, OutsideNets : TStringList;
    InternalNets : TStringList;       { source nets fully inside the group    }
    NetMap : TStringList;             { Values: srcNet -> dstNet              }
    DstPadNet : TStringList;          { Values: padName -> netName (per dst)  }
    Src0, Dst0, CmpSrc, CmpDst, Comp : IPCB_Component;
    SrcAnchorX, SrcAnchorY, DstAnchorX, DstAnchorY, DX, DY : TCoord;
    DRot, ExpX, ExpY : Double;
    PadIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Iter : IPCB_BoardIterator;
    Prim, NewPrim : IPCB_Primitive;
    NetObj : IPCB_Net;
    SrcNetName, DstNetName, NetName, RefName : String;
    IsGroup : Boolean;
    CopiedCount, NetAssignedCount, SharedSkipped, CongruenceWarn : Integer;
    Tol : TCoord;
    Notes : String;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Mapping := ExtractJsonValue(Params, 'mapping');
    If Mapping = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'mapping is required (pipe-separated src=dst pairs; first pair is '
            + 'the transform anchor)');
        Exit;
    End;
    NetsOverride := ExtractJsonValue(Params, 'nets');
    UseOverride := (NetsOverride <> '');
    MoveCompStr := LowerCase(ExtractJsonValue(Params, 'move_components'));
    MoveComps := (MoveCompStr = 'true') Or (MoveCompStr = '1');

    SrcList := TStringList.Create;
    DstList := TStringList.Create;
    SrcGroup := TStringList.Create;
    GroupNets := TStringList.Create;       GroupNets.Duplicates := dupIgnore;
    OutsideNets := TStringList.Create;     OutsideNets.Duplicates := dupIgnore;
    InternalNets := TStringList.Create;    InternalNets.Duplicates := dupIgnore;
    NetMap := TStringList.Create;
    CopiedCount := 0;
    NetAssignedCount := 0;
    SharedSkipped := 0;
    CongruenceWarn := 0;
    Notes := '';
    Tol := MilsToCoord(10);

    Try
        { 1. Parse the mapping into parallel component lists. }
        Remaining := Mapping;
        While Length(Remaining) > 0 Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then
            Begin
                Pair := Remaining;
                Remaining := '';
            End
            Else
            Begin
                Pair := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End;
            Pair := Trim(Pair);
            If Pair = '' Then Continue;
            EqPos := Pos('=', Pair);
            If EqPos <= 0 Then Continue;
            SrcDes := Trim(Copy(Pair, 1, EqPos - 1));
            DstDes := Trim(Copy(Pair, EqPos + 1, Length(Pair)));
            If (SrcDes = '') Or (DstDes = '') Then Continue;
            SrcList.Add(SrcDes);
            DstList.Add(DstDes);
            SrcGroup.Add(SrcDes);
        End;

        If SrcList.Count = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
                'mapping had no valid src=dst pairs');
            Exit;
        End;

        { 2. Resolve the anchor pair and derive the rigid transform. }
        Src0 := Board.GetPcbComponentByRefDes(SrcList.Get(0));
        Dst0 := Board.GetPcbComponentByRefDes(DstList.Get(0));
        If (Src0 = Nil) Or (Dst0 = Nil) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
                'anchor pair not found: ' + SrcList.Get(0) + '=' + DstList.Get(0));
            Exit;
        End;
        If Src0.Layer <> Dst0.Layer Then
        Begin
            Result := BuildErrorResponse(RequestId, 'CROSS_SIDE',
                'anchor source and destination are on different board sides; '
                + 'cross-side (mirrored) replication is not supported');
            Exit;
        End;
        SrcAnchorX := Src0.x;   SrcAnchorY := Src0.y;
        DstAnchorX := Dst0.x;   DstAnchorY := Dst0.y;
        DRot := Dst0.Rotation - Src0.Rotation;
        DX := DstAnchorX - SrcAnchorX;
        DY := DstAnchorY - SrcAnchorY;

        { 3. Build the source-net -> dest-net map from matched pad names, and
             optionally relocate destination components onto the transform. }
        For I := 0 To SrcList.Count - 1 Do
        Begin
            CmpSrc := Board.GetPcbComponentByRefDes(SrcList.Get(I));
            CmpDst := Board.GetPcbComponentByRefDes(DstList.Get(I));
            If (CmpSrc = Nil) Or (CmpDst = Nil) Then Continue;

            { Congruence: does this dst sit where the anchor transform predicts? }
            If I > 0 Then
            Begin
                ExpX := DstAnchorX + (CmpSrc.x - SrcAnchorX);
                ExpY := DstAnchorY + (CmpSrc.y - SrcAnchorY);
                { Rotation about the anchor is ignored in this cheap check when
                  DRot=0 (the common case); a rotated channel still reports via
                  the position delta below. }
                If (Abs(CmpDst.x - ExpX) > Tol) Or (Abs(CmpDst.y - ExpY) > Tol) Then
                    Inc(CongruenceWarn);
            End;

            If MoveComps Then
            Begin
                Try
                    PCBServer.SendMessageToRobots(CmpDst.I_ObjectAddress,
                        c_Broadcast, PCBM_BeginModify, c_NoEventData);
                    CmpDst.x := DstAnchorX + (CmpSrc.x - SrcAnchorX);
                    CmpDst.y := DstAnchorY + (CmpSrc.y - SrcAnchorY);
                    CmpDst.Rotation := CmpSrc.Rotation + DRot;
                    PCBServer.SendMessageToRobots(CmpDst.I_ObjectAddress,
                        c_Broadcast, PCBM_EndModify, c_NoEventData);
                Except End;
            End;

            { dst pad name -> net. A fresh list per pair: TStringList.Clear is
              unreliable across the DelphiScript boundary (rebuild instead). }
            DstPadNet := TStringList.Create;
            Try
                PadIter := CmpDst.GroupIterator_Create;
                PadIter.AddFilter_ObjectSet(MkSet(ePadObject));
                Pad := PadIter.FirstPCBObject;
                While Pad <> Nil Do
                Begin
                    If Pad.InComponent And (Pad.Net <> Nil) Then
                        DstPadNet.Values[Pad.Name] := Pad.Net.Name;
                    Pad := PadIter.NextPCBObject;
                End;
                CmpDst.GroupIterator_Destroy(PadIter);

                { src pad net -> dst pad net (matched by pad name) }
                PadIter := CmpSrc.GroupIterator_Create;
                PadIter.AddFilter_ObjectSet(MkSet(ePadObject));
                Pad := PadIter.FirstPCBObject;
                While Pad <> Nil Do
                Begin
                    If Pad.InComponent And (Pad.Net <> Nil) Then
                    Begin
                        SrcNetName := Pad.Net.Name;
                        DstNetName := DstPadNet.Values[Pad.Name];
                        If (DstNetName <> '') And (NetMap.IndexOfName(SrcNetName) < 0) Then
                            NetMap.Values[SrcNetName] := DstNetName;
                    End;
                    Pad := PadIter.NextPCBObject;
                End;
                CmpSrc.GroupIterator_Destroy(PadIter);
            Finally
                DstPadNet.Free;
            End;
        End;

        { 4. Decide which source nets to copy. }
        If UseOverride Then
        Begin
            Remaining := NetsOverride;
            While Length(Remaining) > 0 Do
            Begin
                PipePos := Pos('|', Remaining);
                If PipePos = 0 Then
                Begin
                    NetName := Remaining;  Remaining := '';
                End
                Else
                Begin
                    NetName := Copy(Remaining, 1, PipePos - 1);
                    Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
                End;
                NetName := Trim(NetName);
                If NetName <> '' Then InternalNets.Add(NetName);
            End;
        End
        Else
        Begin
            { Classify every component net as touching the group, the outside,
              or both. Internal = touches group, never the outside. }
            Iter := Board.BoardIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
            Iter.AddFilter_Method(eProcessAll);
            Comp := Iter.FirstPCBObject;
            While Comp <> Nil Do
            Begin
                RefName := Comp.Name.Text;
                IsGroup := (SrcGroup.IndexOf(RefName) >= 0);
                PadIter := Comp.GroupIterator_Create;
                PadIter.AddFilter_ObjectSet(MkSet(ePadObject));
                Pad := PadIter.FirstPCBObject;
                While Pad <> Nil Do
                Begin
                    If Pad.InComponent And (Pad.Net <> Nil) Then
                    Begin
                        If IsGroup Then GroupNets.Add(Pad.Net.Name)
                        Else OutsideNets.Add(Pad.Net.Name);
                    End;
                    Pad := PadIter.NextPCBObject;
                End;
                Comp.GroupIterator_Destroy(PadIter);
                Comp := Iter.NextPCBObject;
            End;
            Board.BoardIterator_Destroy(Iter);

            For I := 0 To GroupNets.Count - 1 Do
            Begin
                NetName := GroupNets.Get(I);
                If OutsideNets.IndexOf(NetName) < 0 Then
                    InternalNets.Add(NetName)
                Else
                    Inc(SharedSkipped);
            End;
        End;

        { 5. Replicate + transform + re-net the source routing. }
        PCBServer.PreProcess;
        Try
            Iter := Board.BoardIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject,
                ePolyObject, eRegionObject, eFillObject));
            Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iter.AddFilter_Method(eProcessAll);

            Prim := Iter.FirstPCBObject;
            While Prim <> Nil Do
            Begin
                If (Prim.Net <> Nil)
                   And (InternalNets.IndexOf(Prim.Net.Name) >= 0) Then
                Begin
                    SrcNetName := Prim.Net.Name;
                    DstNetName := NetMap.Values[SrcNetName];

                    NewPrim := Nil;
                    Try
                        If (Prim.ObjectId = ePolyObject)
                           Or (Prim.ObjectId = eRegionObject) Then
                            NewPrim := Prim.ReplicateWithChildren
                        Else
                            NewPrim := Prim.Replicate;
                    Except
                        NewPrim := Nil;
                    End;

                    If NewPrim <> Nil Then
                    Begin
                        Try
                            Board.BeginModify;
                            Board.AddPCBObject(NewPrim);
                            Board.EndModify;

                            NewPrim.BeginModify;
                            If Abs(DRot) > 0.0001 Then
                                NewPrim.RotateAroundXY(SrcAnchorX, SrcAnchorY, DRot);
                            NewPrim.MoveByXY(DX, DY);
                            NewPrim.EndModify;
                            Inc(CopiedCount);

                            { Re-net the copy to the destination net. }
                            If DstNetName <> '' Then
                            Begin
                                NetObj := FindNetByName(Board, DstNetName);
                                If NetObj <> Nil Then
                                Begin
                                    NewPrim.BeginModify;
                                    NewPrim.Net := NetObj;
                                    NewPrim.EndModify;
                                    NetObj.AddPCBObject(NewPrim);
                                    Inc(NetAssignedCount);
                                End;
                            End;

                            PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
                                c_Broadcast, PCBM_BoardRegisteration,
                                NewPrim.I_ObjectAddress);
                        Except End;
                    End;
                End;
                Prim := Iter.NextPCBObject;
            End;
            Board.BoardIterator_Destroy(Iter);
        Finally
            PCBServer.PostProcess;
        End;

        { 6. Rebuild connectivity so ratsnest / highlighting reflect the copies. }
        Try Board.ConnectivelyValidateNets; Except End;
        Try Board.ViewManager_FullUpdate; Except End;

        If CongruenceWarn > 0 Then
            Notes := Notes + IntToStr(CongruenceWarn) + ' destination component(s) '
                + 'do not match the anchor transform; copied routing may not '
                + 'align there (pass move_components=true to relocate them). ';
        If (Not UseOverride) And (InternalNets.Count = 0) Then
            Notes := Notes + 'No internal nets found -- every source net is '
                + 'shared with the rest of the board, so nothing was copied. '
                + 'Pass an explicit "nets" list to force specific nets. ';

        SaveDocByPath(Board.FileName);

        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonInt('copied', CopiedCount) + ',' +
                JsonInt('net_assigned', NetAssignedCount) + ',' +
                JsonInt('internal_nets', InternalNets.Count) + ',' +
                JsonInt('shared_nets_skipped', SharedSkipped) + ',' +
                JsonInt('congruence_warnings', CongruenceWarn) + ',' +
                JsonStr('notes', Trim(Notes))
            ));
    Finally
        SrcList.Free;
        DstList.Free;
        SrcGroup.Free;
        GroupNets.Free;
        OutsideNets.Free;
        InternalNets.Free;
        NetMap.Free;
    End;
End;


{..............................................................................}
{ PCB_FilterVariantComponents - Select the components of a chosen fitted-class }
{ for a named variant, so they stand out on the board (the agent-callable      }
{ equivalent of the community VariantFilter script).                           }
{                                                                              }
{ Classifies every flattened component under the variant via                   }
{ DM_FindComponentVariationByUniqueId (Nil = fitted original; kind 1 = not     }
{ fitted; kind 2 = alternate), collects the ones matching the requested set,   }
{ then selects exactly those on the active board (deselecting the rest). Uses  }
{ the verified GetPcbComponentByRefDes + Selected API rather than the          }
{ PCB:RunQuery process, so it is deterministic.                                 }
{                                                                              }
{ Params:                                                                       }
{   variant_name -- required; the variant to classify against.                }
{   select       -- one of not_fitted (default), fitted_original, alternate,  }
{                   all_fitted (fitted_original + alternate).                  }
{                                                                              }
{ Response: variant, select, matched (count), designators (array).            }
{..............................................................................}

Function PCB_FilterVariantComponents(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Flat : IDocument;
    Variant, V0 : IProjectVariant;
    CompVar : IComponentVariation;
    Comp : IComponent;
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    PcbComp : IPCB_Component;
    VariantName, SelMode, Desig, Kind, DesigJson : String;
    I, VarIdx, NVar, Matched, W : Integer;
    Matches : TStringList;
    Include, First : Boolean;
Begin
    VariantName := ExtractJsonValue(Params, 'variant_name');
    SelMode := LowerCase(ExtractJsonValue(Params, 'select'));
    If SelMode = '' Then SelMode := 'not_fitted';
    If VariantName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'variant_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project'); Exit; End;
    SmartCompile(Project);
    Flat := Project.DM_DocumentFlattened;
    If Flat = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_COMPILED',
            'Could not get the flattened document; compile the project first');
        Exit;
    End;

    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD', 'No active PCB board');
        Exit;
    End;

    { Find the variant by name. }
    Variant := Nil;
    NVar := Project.DM_ProjectVariantCount;
    For VarIdx := 0 To NVar - 1 Do
    Begin
        V0 := Project.DM_ProjectVariants(VarIdx);
        If (V0 <> Nil) And (V0.DM_Name = VariantName) Then
        Begin
            Variant := V0;
            Break;
        End;
    End;
    If Variant = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Variant not found: ' + VariantName);
        Exit;
    End;

    { Classify and collect the matching designators. }
    Matches := TStringList.Create;
    Try
        For I := 0 To Flat.DM_ComponentCount - 1 Do
        Begin
            Comp := Flat.DM_Components(I);
            If Comp = Nil Then Continue;
            Desig := '';
            Try Desig := Comp.DM_PhysicalDesignator; Except End;
            { Match by physical designator (the DM_UniqueId lookup mis-resolves }
            { on some boards -- see Proj_GetVariantMatrix). }
            Kind := 'fitted_original';
            Try
                For W := 0 To Variant.DM_VariationCount - 1 Do
                Begin
                    CompVar := Variant.DM_Variations(W);
                    If CompVar = Nil Then Continue;
                    If CompVar.DM_PhysicalDesignator = Desig Then
                    Begin
                        If CompVar.DM_VariationKind = 1 Then Kind := 'not_fitted'
                        Else If CompVar.DM_VariationKind = 2 Then Kind := 'alternate'
                        Else Kind := 'fitted_original';
                        Break;
                    End;
                End;
            Except End;

            If SelMode = 'all_fitted' Then
                Include := (Kind = 'fitted_original') Or (Kind = 'alternate')
            Else If SelMode = 'fitted_original' Then
                Include := (Kind = 'fitted_original')
            Else If SelMode = 'alternate' Then
                Include := (Kind = 'alternate')
            Else
                Include := (Kind = 'not_fitted');

            If Include Then
            Begin
                Desig := '';
                Try Desig := Comp.DM_PhysicalDesignator; Except End;
                If Desig <> '' Then Matches.Add(Desig);
            End;
        End;

        { Deselect every board component, then select the matched ones. }
        PCBServer.PreProcess;
        Try
            Iter := Board.BoardIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
            Iter.AddFilter_Method(eProcessAll);
            PcbComp := Iter.FirstPCBObject;
            While PcbComp <> Nil Do
            Begin
                Try PcbComp.Selected := (Matches.IndexOf(PcbComp.Name.Text) >= 0); Except End;
                PcbComp := Iter.NextPCBObject;
            End;
            Board.BoardIterator_Destroy(Iter);
        Finally
            PCBServer.PostProcess;
        End;
        Try Board.ViewManager_FullUpdate; Except End;

        DesigJson := '[';
        Matched := 0;
        First := True;
        For I := 0 To Matches.Count - 1 Do
        Begin
            If Not First Then DesigJson := DesigJson + ',';
            First := False;
            DesigJson := DesigJson + '"' + EscapeJsonString(Matches.Get(I)) + '"';
            Inc(Matched);
        End;
        DesigJson := DesigJson + ']';

        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonStr('variant', VariantName) + ',' +
                JsonStr('select', SelMode) + ',' +
                JsonInt('matched', Matched) + ',' +
                JsonRaw('designators', DesigJson)
            ));
    Finally
        Matches.Free;
    End;
End;


{..............................................................................}
{ CollectSelectedPCBPrims - Fill L with every board primitive of a kind in     }
{ ObjectSet whose .Selected flag is set. Read selection THIS way, not via       }
{ Board.SelectecObject[], because a programmatic Prim.Selected := True (e.g.    }
{ from PCB_FilterVariantComponents) sets the flag but does NOT populate the     }
{ editor's SelectecObject list -- so a scan on the flag sees both UI and        }
{ programmatic selections, while SelectecObject misses the latter. A Procedure  }
{ (not a Function) so the fixed-array return-slot hazard cannot apply.          }
{..............................................................................}
Procedure CollectSelectedPCBPrims(Board : IPCB_Board; ObjectSet : TSet;
    L : TInterfaceList);
Var
    Iter : IPCB_BoardIterator;
    Prim : IPCB_Primitive;
Begin
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(ObjectSet);
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Prim := Iter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Try If Prim.Selected Then L.Add(Prim); Except End;
            Prim := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;
End;


{..............................................................................}
{ PCB_RenumberPads - Renumber the pads of the current PcbLib footprint in a    }
{ deterministic spatial order (the non-interactive form of the community       }
{ RenumberPads tool, which renumbers by click order).                          }
{                                                                              }
{ Collects the footprint's pads, sorts them by the chosen order, then assigns  }
{ sequential designators (start, start+increment, ...). Rows are banded by a   }
{ small Y tolerance so a grid of pads numbers row-by-row rather than           }
{ interleaving slightly-misaligned pads.                                       }
{                                                                              }
{ Params:                                                                       }
{   order     -- lr_tb (default: rows top-to-bottom, left-to-right in a row),  }
{                tb_lr (columns left-to-right, top-to-bottom in a column).     }
{   start     -- first designator number (default 1).                         }
{   increment -- step between pads (default 1).                               }
{   prefix    -- optional string prefixed to each number (e.g. "A").          }
{                                                                              }
{ Response: renumbered (count), order, mapping (array of old -> new).         }
{..............................................................................}

Function PCB_RenumberPads(Params, RequestId : String) : String;
Var
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    OrderStr, Prefix, MapJson, OldName, NewName : String;
    StartIdx, Increment, N, I, J, P, BestPos, Num, K : Integer;
    Ai, Aj, Bj : Integer;
    Xs, Ys, Order, NewNames : TStringList;   { parallel string lists, no fixed arrays }
    Tol, Xa, Ya, Xb, Yb : TCoord;
    Better : Boolean;
    First : Boolean;
    Tmp : String;
Begin
    OrderStr := LowerCase(ExtractJsonValue(Params, 'order'));
    If OrderStr = '' Then OrderStr := 'lr_tb';
    StartIdx := StrToIntDef(ExtractJsonValue(Params, 'start'), 1);
    Increment := StrToIntDef(ExtractJsonValue(Params, 'increment'), 1);
    If Increment = 0 Then Increment := 1;
    Prefix := ExtractJsonValue(Params, 'prefix');

    PcbLib := Nil;
    Try PcbLib := PCBServer.GetCurrentPCBLibrary; Except End;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB library is active. Open the .PcbLib and select a footprint.');
        Exit;
    End;
    Footprint := PcbLib.CurrentComponent;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'No footprint is selected');
        Exit;
    End;

    Xs := TStringList.Create;
    Ys := TStringList.Create;
    Order := TStringList.Create;
    NewNames := TStringList.Create;
    Try
        { Pass 1: collect pad coordinates (stringified) in iteration order. }
        GrpIter := Footprint.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                Xs.Add(IntToStr(Pad.X));
                Ys.Add(IntToStr(Pad.Y));
                Pad := GrpIter.NextPCBObject;
            End;
        Finally
            Footprint.GroupIterator_Destroy(GrpIter);
        End;
        N := Xs.Count;
        If N = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PADS', 'Footprint has no pads');
            Exit;
        End;

        { Build an index permutation and selection-sort it into the order.    }
        { Row/column banding: pads within Tol on the banding axis are one row. }
        For I := 0 To N - 1 Do Order.Add(IntToStr(I));
        Tol := MilsToCoord(5);
        For P := 0 To N - 2 Do
        Begin
            BestPos := P;
            For J := P + 1 To N - 1 Do
            Begin
                Aj := StrToInt(Order.Get(BestPos));
                Bj := StrToInt(Order.Get(J));
                Xa := StrToInt(Xs.Get(Aj));  Ya := StrToInt(Ys.Get(Aj));
                Xb := StrToInt(Xs.Get(Bj));  Yb := StrToInt(Ys.Get(Bj));
                If OrderStr = 'tb_lr' Then
                Begin
                    { columns: primary X asc, secondary Y desc (top first) }
                    If Abs(Xb - Xa) > Tol Then Better := (Xb < Xa)
                    Else Better := (Yb > Ya);
                End
                Else
                Begin
                    { lr_tb rows: primary Y desc (top first), secondary X asc }
                    If Abs(Yb - Ya) > Tol Then Better := (Yb > Ya)
                    Else Better := (Xb < Xa);
                End;
                If Better Then BestPos := J;
            End;
            If BestPos <> P Then
            Begin
                Tmp := Order.Get(P);
                Order.Strings[P] := Order.Get(BestPos);
                Order.Strings[BestPos] := Tmp;
            End;
        End;

        { Map original-iteration-index -> new designator, keyed by index    }
        { string via Values (avoids pre-populating with empty strings).      }
        Num := StartIdx;
        For P := 0 To N - 1 Do
        Begin
            AI := StrToInt(Order.Get(P));
            NewNames.Values[IntToStr(AI)] := Prefix + IntToStr(Num);
            Num := Num + Increment;
        End;

        { Pass 2: iterate pads again (stable order) and assign the new names. }
        MapJson := '[';
        First := True;
        K := 0;
        PCBServer.PreProcess;
        Try
            GrpIter := Footprint.GroupIterator_Create;
            Try
                GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                Pad := GrpIter.FirstPCBObject;
                While (Pad <> Nil) And (K < N) Do
                Begin
                    OldName := Pad.Name;
                    NewName := NewNames.Values[IntToStr(K)];
                    Try
                        PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast,
                            PCBM_BeginModify, c_NoEventData);
                        Pad.Name := NewName;
                        PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast,
                            PCBM_EndModify, c_NoEventData);
                    Except End;
                    If Not First Then MapJson := MapJson + ',';
                    First := False;
                    MapJson := MapJson + '{"old":"' + EscapeJsonString(OldName) +
                        '","new":"' + EscapeJsonString(NewName) + '"}';
                    Inc(K);
                    Pad := GrpIter.NextPCBObject;
                End;
            Finally
                Footprint.GroupIterator_Destroy(GrpIter);
            End;
        Finally
            PCBServer.PostProcess;
        End;
        MapJson := MapJson + ']';

        Try SaveDocByPath(PcbLib.Board.FileName); Except End;

        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonInt('renumbered', N) + ',' +
                JsonStr('order', OrderStr) + ',' +
                JsonRaw('mapping', MapJson)
            ));
    Finally
        Xs.Free;
        Ys.Free;
        Order.Free;
        NewNames.Free;
    End;
End;


{..............................................................................}
{ PCB_CopyTracksRadial - Replicate the selected tracks/arcs/vias rotated about }
{ a center point, N-1 times, to build a radial / circular array. Reuses the    }
{ verified Replicate + RotateAroundXY transform (see PCB_ReplicateLayout).      }
{                                                                              }
{ The original selection is the source for every copy; copies are added        }
{ unselected so the source set stays stable across rotations.                  }
{                                                                              }
{ Params:                                                                       }
{   center_x, center_y -- rotation centre in mils (required).                 }
{   count              -- total instances including the original (>= 2).      }
{   angle_step         -- degrees between instances (default 360/count).      }
{                                                                              }
{ Response: copied (primitives created), count, angle_step.                   }
{..............................................................................}

Function PCB_CopyTracksRadial(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Cx, Cy : TCoord;
    Count, K, I, SelCount, Copied : Integer;
    StepStr : String;
    StepDeg, Ang : Double;
    Prim, NewPrim : IPCB_Primitive;
    Sel : TInterfaceList;
Begin
    Cx := MilsToCoord(StrToIntDef(ExtractJsonValue(Params, 'center_x'), 0));
    Cy := MilsToCoord(StrToIntDef(ExtractJsonValue(Params, 'center_y'), 0));
    Count := StrToIntDef(ExtractJsonValue(Params, 'count'), 0);
    StepStr := ExtractJsonValue(Params, 'angle_step');

    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD', 'No active PCB board');
        Exit;
    End;
    If Count < 2 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS', 'count must be >= 2');
        Exit;
    End;

    { Snapshot the selected source primitives (by .Selected flag, not the
      editor SelectecObject list) so the copies we add do not feed back in. }
    Sel := CreateObject(TInterfaceList);
    CollectSelectedPCBPrims(Board, MkSet(eTrackObject, eArcObject, eViaObject), Sel);
    SelCount := Sel.Count;
    If SelCount = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SELECTION',
            'Select the tracks/arcs/vias to array first');
        Exit;
    End;

    If StepStr <> '' Then StepDeg := StrToFloatDef(StepStr, 0)
    Else StepDeg := 360.0 / Count;

    Copied := 0;
    PCBServer.PreProcess;
    Try
        For K := 1 To Count - 1 Do
        Begin
            Ang := K * StepDeg;
            For I := 0 To SelCount - 1 Do
            Begin
                Prim := Sel.Items[I];
                If (Prim = Nil) Then Continue;
                NewPrim := Nil;
                Try NewPrim := Prim.Replicate; Except NewPrim := Nil; End;
                If NewPrim <> Nil Then
                Begin
                    Try
                        Board.AddPCBObject(NewPrim);
                        NewPrim.BeginModify;
                        NewPrim.RotateAroundXY(Cx, Cy, Ang);
                        NewPrim.EndModify;
                        PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
                            c_Broadcast, PCBM_BoardRegisteration, NewPrim.I_ObjectAddress);
                        Inc(Copied);
                    Except End;
                End;
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;
    Try Board.ViewManager_FullUpdate; Except End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('copied', Copied) + ',' +
            JsonInt('count', Count) + ',' +
            JsonStr('angle_step', FloatToStr(StepDeg))
        ));
End;


{..............................................................................}
{ PCB_Scale - Scale the selected free primitives by a ratio about an anchor    }
{ point (the non-interactive form of the community PCBScale tool).             }
{                                                                              }
{ Each coordinate maps P' = anchor + ratio*(P - anchor); sizes scale by ratio. }
{ Scope v1 handles free tracks / arcs / vias / pads / fills / text. Primitives }
{ inside a component, dimension, or polygon are skipped (the reference marks    }
{ those incomplete / risky), as are polygons and regions (contour rebuild).    }
{                                                                              }
{ Params:                                                                       }
{   ratio  -- scale factor (required, > 0; 0.95 shrinks, 1.05 grows).         }
{   anchor -- selection_center (default), board_center, or origin.            }
{                                                                              }
{ Response: scaled (count), skipped (count), ratio, anchor_x, anchor_y (mils). }
{..............................................................................}

Function PCB_Scale(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    RatioStr, AnchorMode : String;
    R : Double;
    X, Y, L, T, Rt, B : TCoord;
    BR : TCoordRect;
    I, SelCount, Scaled, Skipped : Integer;
    Prim : IPCB_Primitive;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Via : IPCB_Via;
    Pad : IPCB_Pad;
    Fil : IPCB_Fill;
    Txt : IPCB_Text;
    Sel : TInterfaceList;
Begin
    RatioStr := ExtractJsonValue(Params, 'ratio');
    AnchorMode := LowerCase(ExtractJsonValue(Params, 'anchor'));
    If AnchorMode = '' Then AnchorMode := 'selection_center';
    R := StrToFloatDef(RatioStr, 0);
    If R <= 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS', 'ratio must be > 0');
        Exit;
    End;

    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD', 'No active PCB board');
        Exit;
    End;
    Sel := CreateObject(TInterfaceList);
    CollectSelectedPCBPrims(Board, MkSet(eTrackObject, eArcObject, eViaObject,
        ePadObject, eFillObject, eTextObject), Sel);
    SelCount := Sel.Count;
    If SelCount = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SELECTION',
            'Select the objects to scale first');
        Exit;
    End;

    { Determine the anchor point. }
    If AnchorMode = 'origin' Then
    Begin
        X := 0;  Y := 0;
    End
    Else If AnchorMode = 'board_center' Then
    Begin
        BR := Board.BoardOutline.BoundingRectangle;
        X := (BR.Left + BR.Right) Div 2;
        Y := (BR.Bottom + BR.Top) Div 2;
    End
    Else
    Begin
        { selection bounding-box centre }
        Prim := Sel.Items[0];
        BR := Prim.BoundingRectangle;
        L := BR.Left;  Rt := BR.Right;  T := BR.Top;  B := BR.Bottom;
        For I := 1 To SelCount - 1 Do
        Begin
            Prim := Sel.Items[I];
            BR := Prim.BoundingRectangle;
            If BR.Left   < L  Then L  := BR.Left;
            If BR.Right  > Rt Then Rt := BR.Right;
            If BR.Top    > T  Then T  := BR.Top;
            If BR.Bottom < B  Then B  := BR.Bottom;
        End;
        X := (L + Rt) Div 2;
        Y := (B + T) Div 2;
    End;

    Scaled := 0;
    Skipped := 0;
    PCBServer.PreProcess;
    Try
        For I := 0 To SelCount - 1 Do
        Begin
            Prim := Sel.Items[I];
            If Prim = Nil Then Continue;
            If Prim.InComponent Or Prim.InDimension Or Prim.InPolygon Then
            Begin
                Inc(Skipped);
                Continue;
            End;

            Try
                Prim.BeginModify;
                If Prim.ObjectId = eTrackObject Then
                Begin
                    Track := Prim;
                    Track.X1 := X + Round(R * (Track.X1 - X));
                    Track.Y1 := Y + Round(R * (Track.Y1 - Y));
                    Track.X2 := X + Round(R * (Track.X2 - X));
                    Track.Y2 := Y + Round(R * (Track.Y2 - Y));
                    Track.Width := Round(Track.Width * R);
                    Inc(Scaled);
                End
                Else If Prim.ObjectId = eArcObject Then
                Begin
                    Arc := Prim;
                    Arc.XCenter := X + Round(R * (Arc.XCenter - X));
                    Arc.YCenter := Y + Round(R * (Arc.YCenter - Y));
                    Arc.Radius := Round(Arc.Radius * R);
                    Arc.LineWidth := Round(Arc.LineWidth * R);
                    Inc(Scaled);
                End
                Else If Prim.ObjectId = eViaObject Then
                Begin
                    Via := Prim;
                    Via.X := X + Round(R * (Via.X - X));
                    Via.Y := Y + Round(R * (Via.Y - Y));
                    Via.HoleSize := Round(Via.HoleSize * R);
                    Via.Size := Round(Via.Size * R);
                    Inc(Scaled);
                End
                Else If Prim.ObjectId = ePadObject Then
                Begin
                    Pad := Prim;
                    Pad.X := X + Round(R * (Pad.X - X));
                    Pad.Y := Y + Round(R * (Pad.Y - Y));
                    Pad.HoleSize := Round(Pad.HoleSize * R);
                    Pad.TopXSize := Round(Pad.TopXSize * R);
                    Pad.TopYSize := Round(Pad.TopYSize * R);
                    Inc(Scaled);
                End
                Else If Prim.ObjectId = eFillObject Then
                Begin
                    Fil := Prim;
                    Fil.X1Location := X + Round(R * (Fil.X1Location - X));
                    Fil.Y1Location := Y + Round(R * (Fil.Y1Location - Y));
                    Fil.X2Location := X + Round(R * (Fil.X2Location - X));
                    Fil.Y2Location := Y + Round(R * (Fil.Y2Location - Y));
                    Inc(Scaled);
                End
                Else If Prim.ObjectId = eTextObject Then
                Begin
                    Txt := Prim;
                    Txt.XLocation := X + Round(R * (Txt.XLocation - X));
                    Txt.YLocation := Y + Round(R * (Txt.YLocation - Y));
                    Txt.Size := Round(Txt.Size * R);
                    Txt.Width := Round(Txt.Width * R);
                    Inc(Scaled);
                End
                Else
                    Inc(Skipped);
                Prim.EndModify;
                Try Prim.GraphicallyInvalidate; Except End;
            Except
                Inc(Skipped);
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;
    Try Board.ViewManager_FullUpdate; Except End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('scaled', Scaled) + ',' +
            JsonInt('skipped', Skipped) + ',' +
            JsonStr('ratio', FloatToStr(R)) + ',' +
            JsonInt('anchor_x', CoordToMils(X)) + ',' +
            JsonInt('anchor_y', CoordToMils(Y))
        ));
End;




{..............................................................................}
{ PCB_LockNetRouting - bulk-lock or unlock track + arc + via primitives on a   }
{ list of nets, optionally also locking the components those nets terminate    }
{ at. Locked primitives are .Moveable = False, which the autorouter and       }
{ interactive editor respect when "Protect Locked Objects" is enabled (DXP    }
{ Preferences -> PCB Editor -> General).                                       }
{                                                                                }
{ Standard workflow: lock the power / ground / clock nets before running the  }
{ autorouter so a partial reroute pass doesn't undo your hand-routed rails.   }
{                                                                                }
{ Params:                                                                       }
{   nets             -- pipe-separated net names (e.g. "VCC|GND|CLK_24")      }
{   lock             -- "true" (lock) or "false" (unlock)                     }
{   lock_components  -- "true" / "false" (default false): also lock any       }
{                       component with at least one pad on the matched net   }
{                                                                                }
{ Response: matched_primitives, updated_primitives, matched_components,        }
{           updated_components.                                                 }
{..............................................................................}

Function PCB_LockNetRouting(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter, CompIter : IPCB_BoardIterator;
    PinIter : IPCB_GroupIterator;
    Prim, Obj : IPCB_Primitive;
    Comp : IPCB_Component;
    Pad : IPCB_Pad;
    NetsStr, LockStr, LCStr : String;
    LockOn, LockComponents, NetMatched : Boolean;
    NetsBracketed, NetName, NameMark : String;
    Moveable : Boolean;
    MatchedPrim, UpdatedPrim, MatchedComp, UpdatedComp : Integer;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    NetsStr := ExtractJsonValue(Params, 'nets');
    LockStr := LowerCase(ExtractJsonValue(Params, 'lock'));
    LCStr := LowerCase(ExtractJsonValue(Params, 'lock_components'));
    LockOn := (LockStr = 'true') Or (LockStr = '1');
    LockComponents := (LCStr = 'true') Or (LCStr = '1');

    If NetsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'nets is required (pipe-separated net names)');
        Exit;
    End;
    NetsBracketed := '|' + NetsStr + '|';
    { Locked primitives have .Moveable = False. }
    Moveable := Not LockOn;

    MatchedPrim := 0;
    UpdatedPrim := 0;
    MatchedComp := 0;
    UpdatedComp := 0;

    PCBServer.PreProcess;
    Try
        { Pass 1: walk track / arc / via, lock those whose net matches. }
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Prim := Iter.FirstPCBObject;
            While Prim <> Nil Do
            Begin
                Try
                    If Prim.InNet Then
                    Begin
                        NetName := '';
                        Try NetName := Prim.Net.Name; Except End;
                        NameMark := '|' + NetName + '|';
                        If (NetName <> '')
                           And (Pos(NameMark, NetsBracketed) > 0) Then
                        Begin
                            Inc(MatchedPrim);
                            If Prim.Moveable <> Moveable Then
                            Begin
                                Prim.BeginModify;
                                Prim.Moveable := Moveable;
                                Prim.EndModify;
                                Inc(UpdatedPrim);
                            End;
                        End;
                    End;
                Except End;
                Prim := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;

        { Pass 2 (optional): walk components, lock if any pad lands on the }
        { matched net set.                                                  }
        If LockComponents Then
        Begin
            CompIter := Board.BoardIterator_Create;
            Try
                CompIter.AddFilter_ObjectSet(MkSet(eComponentObject));
                CompIter.AddFilter_LayerSet(AllLayers);
                CompIter.AddFilter_Method(eProcessAll);
                Obj := CompIter.FirstPCBObject;
                While Obj <> Nil Do
                Begin
                    Try
                        Comp := Obj;
                        NetMatched := False;
                        PinIter := Comp.GroupIterator_Create;
                        Try
                            PinIter.AddFilter_ObjectSet(MkSet(ePadObject));
                            Pad := PinIter.FirstPCBObject;
                            While Pad <> Nil Do
                            Begin
                                Try
                                    If Pad.InNet Then
                                    Begin
                                        NetName := '';
                                        Try NetName := Pad.Net.Name; Except End;
                                        NameMark := '|' + NetName + '|';
                                        If (NetName <> '')
                                           And (Pos(NameMark, NetsBracketed) > 0) Then
                                            NetMatched := True;
                                    End;
                                Except End;
                                Pad := PinIter.NextPCBObject;
                            End;
                        Finally
                            Comp.GroupIterator_Destroy(PinIter);
                        End;
                        If NetMatched Then
                        Begin
                            Inc(MatchedComp);
                            If Comp.Moveable <> Moveable Then
                            Begin
                                Comp.BeginModify;
                                Comp.Moveable := Moveable;
                                Comp.EndModify;
                                Inc(UpdatedComp);
                            End;
                        End;
                    Except End;
                    Obj := CompIter.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(CompIter);
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Try Board.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonBool('locked', LockOn) + ',' +
            JsonInt('matched_primitives', MatchedPrim) + ',' +
            JsonInt('updated_primitives', UpdatedPrim) + ',' +
            JsonInt('matched_components', MatchedComp) + ',' +
            JsonInt('updated_components', UpdatedComp)
        ));
End;


{..............................................................................}
{ PCB_PlaceStitchingVias - Place a grid of stitching vias on a named net      }
{ within a rectangle. RF / EMC tool: GND-stitch vias around high-speed        }
{ traces tie reference planes together so the return current has a low       }
{ inductance path between layers.                                              }
{                                                                                }
{ Core algorithm: walk a grid inside the rectangle; for each gridpoint,      }
{ check via spatial-iterator if any pad / via / track already occupies a     }
{ circle of clearance_mils around it. If clear, place a via on the target    }
{ net.                                                                       }
{                                                                                }
{ Params:                                                                       }
{   net               -- target net name (required, must exist on the board)  }
{   x1_mils, y1_mils, x2_mils, y2_mils -- inclusive rectangle (required)      }
{   spacing_mils      -- grid spacing (default 50)                            }
{   via_size_mils     -- via pad size (default 30)                            }
{   via_hole_mils     -- via drill size (default 14)                           }
{   clearance_mils    -- min gap to existing primitives (default 10)          }
{   dry_run           -- "true" returns the count without placing             }
{                                                                                }
{ Response: placed, skipped, dry_run, net.                                     }
{..............................................................................}

Function PCB_PlaceStitchingVias(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Net : IPCB_Net;
    Via : IPCB_Via;
    SIter : IPCB_SpatialIterator;
    Hit : IPCB_Primitive;
    NetName : String;
    X1, Y1, X2, Y2 : TCoord;
    Spacing, ViaSize, ViaHole, Clearance : TCoord;
    X1Mils, Y1Mils, X2Mils, Y2Mils : Integer;
    SpacingMils, ViaSizeMils, ViaHoleMils, ClearanceMils : Integer;
    PX, PY : TCoord;
    Placed, Skipped : Integer;
    DryRun, HasHit : Boolean;
    DryStr : String;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    NetName := ExtractJsonValue(Params, 'net');
    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'net is required');
        Exit;
    End;
    Net := FindNetByName(Board, NetName);
    If Net = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NET_NOT_FOUND',
            'Net not found on board: ' + NetName);
        Exit;
    End;

    X1Mils := StrToIntDef(ExtractJsonValue(Params, 'x1_mils'), 0);
    Y1Mils := StrToIntDef(ExtractJsonValue(Params, 'y1_mils'), 0);
    X2Mils := StrToIntDef(ExtractJsonValue(Params, 'x2_mils'), 0);
    Y2Mils := StrToIntDef(ExtractJsonValue(Params, 'y2_mils'), 0);
    SpacingMils := StrToIntDef(ExtractJsonValue(Params, 'spacing_mils'), 50);
    ViaSizeMils := StrToIntDef(ExtractJsonValue(Params, 'via_size_mils'), 30);
    ViaHoleMils := StrToIntDef(ExtractJsonValue(Params, 'via_hole_mils'), 14);
    ClearanceMils := StrToIntDef(ExtractJsonValue(Params, 'clearance_mils'), 10);
    DryStr := LowerCase(ExtractJsonValue(Params, 'dry_run'));
    DryRun := (DryStr = 'true') Or (DryStr = '1');

    If (X1Mils = 0) And (X2Mils = 0) And (Y1Mils = 0) And (Y2Mils = 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'rectangle params (x1_mils / y1_mils / x2_mils / y2_mils) required');
        Exit;
    End;
    If SpacingMils <= 0 Then SpacingMils := 50;
    If ViaSizeMils <= 0 Then ViaSizeMils := 30;
    If ViaHoleMils <= 0 Then ViaHoleMils := 14;

    X1 := MilsToCoord(X1Mils);  Y1 := MilsToCoord(Y1Mils);
    X2 := MilsToCoord(X2Mils);  Y2 := MilsToCoord(Y2Mils);
    Spacing := MilsToCoord(SpacingMils);
    ViaSize := MilsToCoord(ViaSizeMils);
    ViaHole := MilsToCoord(ViaHoleMils);
    Clearance := MilsToCoord(ClearanceMils);

    Placed := 0;
    Skipped := 0;

    If Not DryRun Then PCBServer.PreProcess;
    Try
        PY := Y1;
        While PY <= Y2 Do
        Begin
            PX := X1;
            While PX <= X2 Do
            Begin
                { Collision check: walk same-net + other-net primitives in    }
                { a clearance-padded box around the candidate. If any         }
                { non-target-net pad/via/track is in range, skip.             }
                HasHit := False;
                SIter := Board.SpatialIterator_Create;
                Try
                    SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
                        ePadObject, eViaObject));
                    SIter.AddFilter_LayerSet(AllLayers);
                    SIter.AddFilter_Area(PX - ViaSize Div 2 - Clearance,
                                         PY - ViaSize Div 2 - Clearance,
                                         PX + ViaSize Div 2 + Clearance,
                                         PY + ViaSize Div 2 + Clearance);
                    Hit := SIter.FirstPCBObject;
                    While (Hit <> Nil) And (Not HasHit) Do
                    Begin
                        Try
                            { Same-net hits are fine -- this is the net we'll  }
                            { be tying to anyway. Other-net hits or no-net    }
                            { hits are blockers.                              }
                            If (Not Hit.InNet) Or (Hit.Net.Name <> NetName) Then
                                HasHit := True;
                        Except End;
                        Hit := SIter.NextPCBObject;
                    End;
                Finally
                    Board.SpatialIterator_Destroy(SIter);
                End;

                If HasHit Then
                Begin
                    Inc(Skipped);
                End
                Else If DryRun Then
                Begin
                    Inc(Placed);
                End
                Else
                Begin
                    Via := Nil;
                    Try
                        Via := PCBServer.PCBObjectFactory(eViaObject,
                            eNoDimension, eCreate_Default);
                    Except End;
                    If Via <> Nil Then
                    Begin
                        Via.x := PX;
                        Via.y := PY;
                        Via.Size := ViaSize;
                        Via.HoleSize := ViaHole;
                        Try Via.LowLayer := eTopLayer; Except End;
                        Try Via.HighLayer := eBottomLayer; Except End;
                        Try Via.Net := Net; Except End;
                        Board.AddPCBObject(Via);
                        Inc(Placed);
                    End;
                End;

                PX := PX + Spacing;
            End;
            PY := PY + Spacing;
        End;
    Finally
        If Not DryRun Then PCBServer.PostProcess;
    End;

    If Not DryRun Then
        Try Board.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('net', NetName) + ',' +
            JsonBool('dry_run', DryRun) + ',' +
            JsonInt('placed', Placed) + ',' +
            JsonInt('skipped', Skipped) + ',' +
            JsonInt('spacing_mils', SpacingMils) + ',' +
            JsonInt('clearance_mils', ClearanceMils)
        ));
End;


{..............................................................................}
{ PCB_SetTextVisibility - bulk-toggle Component.NameOn and Component.CommentOn }
{                                                                                }
{ For the common "hide designators before a release" / "show comments for     }
{ a review" workflow.                                                          }
{                                                                                }
{ Params:                                                                       }
{   designators (optional) -- if "true" / "false" sets NameOn for matched     }
{                              components; omit to leave NameOn unchanged.    }
{   comments    (optional) -- same shape, for CommentOn.                      }
{   filter      (optional) -- pipe-separated list of designator names         }
{                              (e.g. "U1|U2|R5") to restrict the change;     }
{                              omit to apply to every component.              }
{                                                                                }
{ Response: matched, updated_names, updated_comments.                          }
{..............................................................................}

Function PCB_SetTextVisibility(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
    Obj : IPCB_Primitive;
    DesStr, ComStr, FilterStr : String;
    SetNames, SetComments, NamesOn, CommentsOn : Boolean;
    HasFilter : Boolean;
    Matched, UpdatedNames, UpdatedComments : Integer;
    CompName : String;
    NameMark : String;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    DesStr := LowerCase(ExtractJsonValue(Params, 'designators'));
    ComStr := LowerCase(ExtractJsonValue(Params, 'comments'));
    FilterStr := ExtractJsonValue(Params, 'filter');

    SetNames := (DesStr = 'true') Or (DesStr = 'false');
    SetComments := (ComStr = 'true') Or (ComStr = 'false');
    NamesOn := (DesStr = 'true');
    CommentsOn := (ComStr = 'true');
    HasFilter := FilterStr <> '';

    If (Not SetNames) And (Not SetComments) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'At least one of designators / comments must be "true" or "false"');
        Exit;
    End;

    Matched := 0;
    UpdatedNames := 0;
    UpdatedComments := 0;

    PCBServer.PreProcess;
    Try
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Obj := Iter.FirstPCBObject;
            While Obj <> Nil Do
            Begin
                Try
                    Comp := Obj;
                    CompName := '';
                    Try CompName := Comp.Name.Text; Except End;
                    If HasFilter Then
                    Begin
                        NameMark := '|' + CompName + '|';
                        If Pos(NameMark, '|' + FilterStr + '|') = 0 Then
                        Begin
                            Obj := Iter.NextPCBObject;
                            Continue;
                        End;
                    End;
                    Inc(Matched);
                    If SetNames And (Comp.NameOn <> NamesOn) Then
                    Begin
                        Comp.NameOn := NamesOn;
                        Inc(UpdatedNames);
                    End;
                    If SetComments And (Comp.CommentOn <> CommentsOn) Then
                    Begin
                        Comp.CommentOn := CommentsOn;
                        Inc(UpdatedComments);
                    End;
                Except End;
                Obj := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Try Board.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('matched', Matched) + ',' +
            JsonInt('updated_names', UpdatedNames) + ',' +
            JsonInt('updated_comments', UpdatedComments)
        ));
End;


{..............................................................................}
{ PCB_BatchMoveComponents - Move/rotate many components in ONE IPC call.      }
{ Param 'moves' is a pipe-separated list; each entry is 4 comma-separated     }
{ fields: designator,x,y,rotation. Empty field = leave that property          }
{ unchanged.                                                                   }
{                                                                              }
{ Implementation note: the batch wraps EACH per-component edit in its own     }
{ PCBServer.PreProcess / PostProcess pair, mirroring the singular             }
{ PCB_MoveComponent semantics exactly. An earlier version wrapped the whole  }
{ batch in one PreProcess block plus a final PCBM_BoardRegisteration         }
{ broadcast; that variant crashed the script engine with "Could not convert  }
{ variant of type (OleStr) into type (Double)" (likely an internal Altium    }
{ event chain fires within the bulk-PostProcess sweep and chokes on a        }
{ locale-marshalled value). The wall-time win of "one IPC round-trip"        }
{ survives, the false win of "one PreProcess" did not.                       }
{                                                                              }
{ Save runs once at the end of the whole batch.                               }
{..............................................................................}

Function PCB_BatchMoveComponents(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    MovesStr, MoveStr, Remaining : String;
    PipePos, CommaPos, Applied, Failed, FieldIdx : Integer;
    { 4 named locals instead of `Array[0..3] Of String` - fixed-size       }
    { string arrays as function locals corrupt the function return slot   }
    { in DelphiScript, see [[delphiscript_fixed_string_array_bug]].       }
    Desig, XStr, YStr, RotStr, Token : String;
    NewX, NewY : Integer;
    NewRot : Double;
    HasX, HasY, HasRot : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
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

        Desig := '';
        XStr := '';
        YStr := '';
        RotStr := '';
        FieldIdx := 0;
        While (MoveStr <> '') And (FieldIdx <= 3) Do
        Begin
            CommaPos := Pos(',', MoveStr);
            If CommaPos = 0 Then
            Begin
                Token := MoveStr;
                MoveStr := '';
            End
            Else
            Begin
                Token := Copy(MoveStr, 1, CommaPos - 1);
                MoveStr := Copy(MoveStr, CommaPos + 1, Length(MoveStr));
            End;
            Case FieldIdx Of
                0: Desig := Token;
                1: XStr := Token;
                2: YStr := Token;
                3: RotStr := Token;
            End;
            FieldIdx := FieldIdx + 1;
        End;

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

        { Per-component PreProcess+PostProcess (same as singular). Each move   }
        { is structurally a singular move, just inside one IPC turn.           }
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

        Applied := Applied + 1;
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
    JsonItems, EnvelopeData, ResponseStr : String;
    First : Boolean;
    { Parallel heap-allocated lists. ANY fixed-size local array (String,    }
    { Integer, Double, interface, all of them) corrupts the function's     }
    { return slot in DelphiScript, see                                      }
    { [[delphiscript_fixed_string_array_bug]] for the (now-broader) rule.  }
    { Lengths are stored as stringified floats and parsed back on update.  }
    NetNames, NetLengthStrs : TStringList;
    I, FoundIdx : Integer;
    SegLen, DX, DY, ArcAngle, RadiusMils, Accum : Double;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    FilterNet := ExtractJsonValue(Params, 'net');

    NetNames := TStringList.Create;
    NetLengthStrs := TStringList.Create;
    Try
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

            FoundIdx := NetNames.IndexOf(NetName);
            If FoundIdx >= 0 Then
            Begin
                Accum := StrToFloatDef(NetLengthStrs[FoundIdx], 0) + SegLen;
                NetLengthStrs[FoundIdx] := FloatToStr(Accum);
            End
            Else
            Begin
                NetNames.Add(NetName);
                NetLengthStrs.Add(FloatToStr(SegLen));
            End;

            Obj := Iterator.NextPCBObject;
        End;
        Board.BoardIterator_Destroy(Iterator);

        JsonItems := '';
        First := True;
        For I := 0 To NetNames.Count - 1 Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems + '{"net":"' + EscapeJsonString(NetNames[I]) + '",'
                + '"length_mils":' + FloatToJsonStr(StrToFloatDef(NetLengthStrs[I], 0)) + '}';
        End;

        EnvelopeData := '{"trace_lengths":[' + JsonItems + '],"net_count":'
            + IntToStr(NetNames.Count) + '}';
        ResponseStr := BuildSuccessResponse(RequestId, EnvelopeData);
        Result := ResponseStr;
    Finally
        NetLengthStrs.Free;
        NetNames.Free;
    End;
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
    Board := GetPCBBoardAnywhere;
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
            + '"copper_thickness_mils":' + FloatToJsonStr(CopperThickMils) + ','
            + '"dielectric_type":"' + EscapeJsonString(DielectricType) + '",'
            + '"dielectric_height_mils":' + FloatToJsonStr(DielectricHeightMils) + ','
            + '"dielectric_constant":' + FloatToJsonStr(DielectricConst) + '}';
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
    Board := GetPCBBoardAnywhere;
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

    TargetLayer := ResolveLayerId(Board, LayerName);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerName + '. ' + BoardLayerNamesHint(Board));
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
        '{"success":true,"layer":"' + EscapeJsonString(GetLayerString(TargetLayer)) + '"}');
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
    Board := GetPCBBoardAnywhere;
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

    TargetLayer := ResolveLayerId(Board, LayerName);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerName + '. ' + BoardLayerNamesHint(Board));
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
        '{"success":true,"layer":"' + EscapeJsonString(GetLayerString(TargetLayer)) + '"}');
End;

{..............................................................................}
{ ResolveStackLayerObject - The IPCB_LayerObject_V7 a caller meant, or Nil.    }
{                                                                              }
{ Thin wrapper over ResolveLayerIdInStack (Utils.pas), which is the ONE place  }
{ a caller-supplied layer name is turned into a TLayer. Keeping the name walk  }
{ here as well would let the two copies drift, and the drift is exactly what   }
{ costs a board: GetLayerFromString answers eTopLayer for every name it does   }
{ not know, so any handler still resolving on its own writes to the top layer  }
{ and reports success.                                                          }
{..............................................................................}

Function ResolveStackLayerObject(LayerStack : IPCB_LayerStack_V7; LayerName : String) : IPCB_LayerObject_V7;
Var
    Resolved : TLayer;
Begin
    Result := Nil;
    If LayerStack = Nil Then Exit;
    Resolved := ResolveLayerIdInStack(LayerStack, LayerName);
    If Resolved = eNoLayer Then Exit;
    Try Result := LayerStack.LayerObject_V7[Resolved]; Except Result := Nil; End;
End;

{ The dielectric type of a layer in the same vocabulary modify_layer accepts,  }
{ so a read-back can be compared against what the caller asked for. An empty   }
{ result means the property could not be read at all.                          }

Function DielectricTypeToken(LayerObj : IPCB_LayerObject_V7) : String;
Begin
    Result := '';
    Try
        If LayerObj.Dielectric.DielectricType = eNoDielectric Then Result := 'none'
        Else If LayerObj.Dielectric.DielectricType = eCore Then Result := 'core'
        Else If LayerObj.Dielectric.DielectricType = ePrePreg Then Result := 'prepreg'
        Else If LayerObj.Dielectric.DielectricType = eSurfaceMaterial Then Result := 'surface'
        Else Result := 'other';
    Except
        Result := '';
    End;
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
    ResolvedName, NameBack, TypeBack, MaterialBack : String;
    AppliedJson, RejectedJson : String;
    ThickBack, HeightBack, ConstBack : Double;
    AllOk : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
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

    LayerStack := Board.LayerStack_V7;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;

    LayerObj := ResolveStackLayerObject(LayerStack, LayerName);
    If LayerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_IN_STACK',
            'Layer ' + LayerName + ' is not present in the current stack. Use a '
            + 'name exactly as pcb_get_layer_stackup reports it, or a canonical '
            + 'token such as InternalPlane1.');
        Exit;
    End;

    { The name the stack knows this layer by, captured before a rename lands, }
    { so the response identifies the layer that was actually written.         }
    ResolvedName := LayerName;
    Try ResolvedName := LayerObj.Name; Except End;

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

    { Read back rather than trusting the write. Every assignment above is late }
    { bound and carries its own Except, so a refused write raises nothing and  }
    { answering "success" from the fact that nothing escaped reports success   }
    { for doing nothing - which is how a whole stackup came to be recorded as  }
    { set while the board still read zeros. Only a value that comes back       }
    { MATCHING counts as applied; every other field is named in "rejected".    }
    AppliedJson := '';
    RejectedJson := '';
    AllOk := True;

    If NewName <> '' Then
    Begin
        NameBack := '';
        Try NameBack := LayerObj.Name; Except NameBack := ''; End;
        If AppliedJson <> '' Then AppliedJson := AppliedJson + ',';
        AppliedJson := AppliedJson + '"name":"' + EscapeJsonString(NameBack) + '"';
        If NameBack <> NewName Then
        Begin
            If RejectedJson <> '' Then RejectedJson := RejectedJson + ',';
            RejectedJson := RejectedJson + '"name"';
            AllOk := False;
        End;
    End;

    If ThickStr <> '' Then
    Begin
        ThickBack := -1;
        Try ThickBack := LayerObj.CopperThickness / 10000; Except ThickBack := -1; End;
        If AppliedJson <> '' Then AppliedJson := AppliedJson + ',';
        AppliedJson := AppliedJson + '"copper_thickness_mils":' + FloatToJsonStr(ThickBack);
        If Abs(ThickBack - StrToIntDef(ThickStr, 0)) > 0.01 Then
        Begin
            If RejectedJson <> '' Then RejectedJson := RejectedJson + ',';
            RejectedJson := RejectedJson + '"copper_thickness_mils"';
            AllOk := False;
        End;
    End;

    If TypeStr <> '' Then
    Begin
        TypeBack := DielectricTypeToken(LayerObj);
        If AppliedJson <> '' Then AppliedJson := AppliedJson + ',';
        AppliedJson := AppliedJson + '"dielectric_type":"' + EscapeJsonString(TypeBack) + '"';
        If TypeBack <> TypeStr Then
        Begin
            If RejectedJson <> '' Then RejectedJson := RejectedJson + ',';
            RejectedJson := RejectedJson + '"dielectric_type"';
            AllOk := False;
        End;
    End;

    If HeightStr <> '' Then
    Begin
        HeightBack := -1;
        Try HeightBack := LayerObj.Dielectric.DielectricHeight / 10000; Except HeightBack := -1; End;
        If AppliedJson <> '' Then AppliedJson := AppliedJson + ',';
        AppliedJson := AppliedJson + '"dielectric_height_mils":' + FloatToJsonStr(HeightBack);
        If Abs(HeightBack - StrToIntDef(HeightStr, 0)) > 0.01 Then
        Begin
            If RejectedJson <> '' Then RejectedJson := RejectedJson + ',';
            RejectedJson := RejectedJson + '"dielectric_height_mils"';
            AllOk := False;
        End;
    End;

    If ConstStr <> '' Then
    Begin
        ConstBack := -1;
        Try ConstBack := LayerObj.Dielectric.DielectricConstant; Except ConstBack := -1; End;
        If AppliedJson <> '' Then AppliedJson := AppliedJson + ',';
        AppliedJson := AppliedJson + '"dielectric_constant":' + FloatToJsonStr(ConstBack);
        If Abs(ConstBack - StrToFloatDef(ConstStr, 1.0)) > 0.001 Then
        Begin
            If RejectedJson <> '' Then RejectedJson := RejectedJson + ',';
            RejectedJson := RejectedJson + '"dielectric_constant"';
            AllOk := False;
        End;
    End;

    If Material <> '' Then
    Begin
        MaterialBack := '';
        Try MaterialBack := LayerObj.Dielectric.DielectricMaterial; Except MaterialBack := ''; End;
        If AppliedJson <> '' Then AppliedJson := AppliedJson + ',';
        AppliedJson := AppliedJson + '"dielectric_material":"' + EscapeJsonString(MaterialBack) + '"';
        If MaterialBack <> Material Then
        Begin
            If RejectedJson <> '' Then RejectedJson := RejectedJson + ',';
            RejectedJson := RejectedJson + '"dielectric_material"';
            AllOk := False;
        End;
    End;

    { Only persist a change that actually took. Saving a board whose write was }
    { refused writes the unchanged stackup back over itself, and the fresh     }
    { file timestamp then reads as a completed edit.                           }
    If AllOk Then SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"success":' + BoolToJsonStr(AllOk) + ','
        + '"layer":"' + EscapeJsonString(ResolvedName) + '",'
        + '"applied":{' + AppliedJson + '},'
        + '"rejected":[' + RejectedJson + ']}');
End;

{..............................................................................}
{ PCB_SetPlaneNet - Give an internal plane layer its net.                     }
{                                                                              }
{ An Altium internal plane is a NEGATIVE layer: the copper is everywhere       }
{ except where the plane is cleared, and the net association lives on the      }
{ LAYER itself rather than on any poured object. A polygon poured on a plane   }
{ layer is a different thing entirely and does not connect the plane, which is }
{ why plane nets were being attempted with pcb_place_polygon_rect - there was  }
{ no other way to reach them, pcb_modify_layer having no net parameter.        }
{                                                                              }
{ THE WRITE PATH IS NOT CONFIRMED. The DelphiScript reference carried in this  }
{ repo (docs/altium-delphiscript/) documents no plane-net accessor at all, so  }
{ the layer object's Net property is attempted under Try/Except and then READ  }
{ BACK. If this script binding does not carry that property, the write raises  }
{ nothing and the read-back disagrees, and the call answers success=false with }
{ applied=false rather than claiming a write that did not land - the same      }
{ honesty contract pcb_modify_layer and pcb_set_layer_color already keep. The  }
{ board is saved only when the read-back agrees.                               }
{                                                                              }
{ Params: layer (required, plane name or InternalPlaneN), net (required)       }
{..............................................................................}

Function PCB_SetPlaneNet(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    NetObj : IPCB_Net;
    LayerStr, NetStr, ResolvedName, NetBack, NoteStr : String;
    TargetLayer : TLayer;
    Applied : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'layer required, for example "InternalPlane1" or the plane''s name '
            + 'as pcb_get_layer_stackup reports it');
        Exit;
    End;

    NetStr := ExtractJsonValue(Params, 'net');
    If NetStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'net required');
        Exit;
    End;

    TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    If (TargetLayer < eInternalPlane1) Or (TargetLayer > eInternalPlane16) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_A_PLANE',
            'Layer ' + GetLayerString(TargetLayer) + ' is not an internal plane. '
            + 'Only InternalPlane1..InternalPlane16 carry a net on the layer. '
            + 'For a signal layer, pour copper with pcb_place_polygon_rect '
            + 'instead.');
        Exit;
    End;

    LayerStack := Nil;
    Try LayerStack := Board.LayerStack_V7; Except LayerStack := Nil; End;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;

    LayerObj := Nil;
    Try LayerObj := LayerStack.LayerObject_V7[TargetLayer]; Except LayerObj := Nil; End;
    If LayerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_IN_STACK',
            'Layer ' + GetLayerString(TargetLayer) + ' is not present in the '
            + 'current stack. Add it with pcb_add_layer first.');
        Exit;
    End;

    NetObj := FindNetByName(Board, NetStr);
    If NetObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NET_NOT_FOUND',
            'No net named "' + NetStr + '" exists on this board. Read '
            + 'pcb_get_nets for the names it does carry; a plane can only be '
            + 'tied to a net the board already knows.');
        Exit;
    End;

    { The name the stack knows this plane by, so the response names the layer }
    { that was written rather than the string the caller happened to pass.    }
    ResolvedName := GetLayerString(TargetLayer);
    Try ResolvedName := LayerObj.Name; Except End;

    PCBServer.PreProcess;
    Try
        Try LayerObj.Net := NetObj; Except End;
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    NetBack := '';
    Try
        If LayerObj.Net <> Nil Then NetBack := LayerObj.Net.Name;
    Except
        NetBack := '';
    End;

    Applied := (NetBack <> '') And (UpperCase(NetBack) = UpperCase(NetStr));

    If Applied Then
    Begin
        SaveDocByPath(Board.FileName);
        NoteStr := '';
    End
    Else
        NoteStr := 'The plane still reads back as "' + NetBack + '". The net '
            + 'was NOT assigned. This build may not expose a plane net through '
            + 'the script binding; assign it in the Layer Stack Manager '
            + '(Design > Layer Stack Manager, the plane row''s Net Name '
            + 'column). The board was not saved.';

    Result := BuildSuccessResponse(RequestId,
        '{"success":' + BoolToJsonStr(Applied) + ','
        + '"layer":"' + EscapeJsonString(GetLayerString(TargetLayer)) + '",'
        + '"layer_name":"' + EscapeJsonString(ResolvedName) + '",'
        + '"net":"' + EscapeJsonString(NetStr) + '",'
        + '"net_readback":"' + EscapeJsonString(NetBack) + '",'
        + '"applied":' + BoolToJsonStr(Applied) + ','
        + '"note":"' + EscapeJsonString(NoteStr) + '"}');
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
    Board := GetPCBBoardAnywhere;
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
                + '"angle1":' + FloatToJsonStr(Outline.Segments[I].Angle1) + ','
                + '"angle2":' + FloatToJsonStr(Outline.Segments[I].Angle2);
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
    Board := GetPCBBoardAnywhere;
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
{ Layer colour conversion.                                                    }
{                                                                              }
{ Altium carries a colour as a Windows TColor, which is $00BBGGRR: the byte   }
{ order is the REVERSE of the #RRGGBB people write down. Converting in one    }
{ place stops every caller getting it backwards, which does not fail loudly.  }
{ It produces a plausible colour that is simply the wrong one, and red and    }
{ blue swapping is easy to miss on a busy board.                              }
{..............................................................................}

Function HexDigitValue(Ch : String) : Integer;
Var
    O : Integer;
    U : String;
Begin
    Result := -1;
    If Ch = '' Then Exit;
    { Materialise before indexing. DelphiScript cannot subscript the       }
    { RESULT of a function call, so UpperCase(Ch)[1] is a compile error    }
    { rather than a runtime one.                                            }
    U := UpperCase(Ch);
    O := Ord(U[1]);
    If (O >= Ord('0')) And (O <= Ord('9')) Then Result := O - Ord('0')
    Else If (O >= Ord('A')) And (O <= Ord('F')) Then Result := 10 + O - Ord('A');
End;

Function ColorToHexRgb(C : Integer) : String;
Var
    R, G, B : Integer;
    Digits : String;
Begin
    Digits := '0123456789ABCDEF';
    B := (C Shr 16) And 255;
    G := (C Shr 8) And 255;
    R := C And 255;
    Result := '#'
        + Copy(Digits, ((R Shr 4) And 15) + 1, 1) + Copy(Digits, (R And 15) + 1, 1)
        + Copy(Digits, ((G Shr 4) And 15) + 1, 1) + Copy(Digits, (G And 15) + 1, 1)
        + Copy(Digits, ((B Shr 4) And 15) + 1, 1) + Copy(Digits, (B And 15) + 1, 1);
End;

{ #RRGGBB to a TColor, or -1 when the text is not a colour. Refusing is the  }
{ point: silently treating a typo as black would repaint a layer.            }

Function HexRgbToColor(S : String) : Integer;
Var
    T : String;
    D0, D1, D2, D3, D4, D5, R, G, B : Integer;
Begin
    Result := -1;
    T := Trim(S);
    If Copy(T, 1, 1) = '#' Then T := Copy(T, 2, Length(T));
    If Length(T) <> 6 Then Exit;

    { Six named locals rather than an array: a fixed size array declared   }
    { inside a Function corrupts the return value in this dialect.         }
    D0 := HexDigitValue(Copy(T, 1, 1));
    D1 := HexDigitValue(Copy(T, 2, 1));
    D2 := HexDigitValue(Copy(T, 3, 1));
    D3 := HexDigitValue(Copy(T, 4, 1));
    D4 := HexDigitValue(Copy(T, 5, 1));
    D5 := HexDigitValue(Copy(T, 6, 1));
    If (D0 < 0) Or (D1 < 0) Or (D2 < 0) Then Exit;
    If (D3 < 0) Or (D4 < 0) Or (D5 < 0) Then Exit;

    R := (D0 Shl 4) Or D1;
    G := (D2 Shl 4) Or D3;
    B := (D4 Shl 4) Or D5;
    Result := (B Shl 16) Or (G Shl 8) Or R;
End;

{..............................................................................}
{ PCB_GetLayerDisplay - Visibility and colour for every layer.               }
{                                                                              }
{ The range eTopLayer..eMultiLayer covers signal, plane, mechanical, mask,    }
{ paste, silk, keepout and multilayer, and GetLayerString filters out the     }
{ ordinals that are not real layers.                                          }
{..............................................................................}

Function PCB_GetLayerDisplay(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    PCBSysOpts : IPCB_SystemOptions;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    Lyr : TLayer;
    JsonItems, LyrNm, UserName : String;
    First, Visible : Boolean;
    Color, Count, Shown : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    PCBSysOpts := Nil;
    Try PCBSysOpts := PCBServer.SystemOptions; Except End;
    If PCBSysOpts = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SYSTEM_OPTIONS',
            'Could not reach PCBServer.SystemOptions, which is where layer '
            + 'colours live. Nothing was read, so this is not a report that '
            + 'the board has no colours.');
        Exit;
    End;

    LayerStack := Nil;
    Try LayerStack := Board.LayerStack_V7; Except End;

    JsonItems := '';
    First := True;
    Count := 0;
    Shown := 0;

    For Lyr := eTopLayer To eMultiLayer Do
    Begin
        LyrNm := GetLayerString(Lyr);
        If LyrNm <> 'Unknown' Then
        Begin
            Color := 0;
            Visible := False;
            Try Color := PCBSysOpts.LayerColors[Lyr]; Except End;
            Try Visible := Board.LayerIsDisplayed[Lyr]; Except End;

            { The name the user gave the layer, which for a mechanical  }
            { layer is the only way to tell one from another.           }
            UserName := '';
            If LayerStack <> Nil Then
            Begin
                LayerObj := Nil;
                Try LayerObj := LayerStack.LayerObject_V7[Lyr]; Except LayerObj := Nil; End;
                If LayerObj <> Nil Then
                    Try UserName := LayerObj.Name; Except UserName := ''; End;
            End;

            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems
                + '{"layer":"' + EscapeJsonString(LyrNm) + '",'
                + '"name":"' + EscapeJsonString(UserName) + '",'
                + '"visible":' + BoolToJsonStr(Visible) + ','
                + '"color":' + IntToStr(Color) + ','
                + '"color_hex":"' + ColorToHexRgb(Color) + '"}';
            Inc(Count);
            If Visible Then Inc(Shown);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"layers":[' + JsonItems + '],"count":' + IntToStr(Count)
        + ',"visible_count":' + IntToStr(Shown) + '}');
End;

{..............................................................................}
{ PCB_SetLayerColor - Recolour one layer.                                     }
{ Params: layer, color (#RRGGBB)                                              }
{..............................................................................}

Function PCB_SetLayerColor(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    PCBSysOpts : IPCB_SystemOptions;
    LayerStr, ColorStr : String;
    LayerID : TLayer;
    Wanted, Readback : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'layer required');
        Exit;
    End;

    ColorStr := ExtractJsonValue(Params, 'color');
    If ColorStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'color required, as #RRGGBB');
        Exit;
    End;

    Wanted := HexRgbToColor(ColorStr);
    If Wanted < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_COLOR',
            'color must be #RRGGBB, got: ' + ColorStr);
        Exit;
    End;

    LayerID := ResolveLayerId(Board, LayerStr);
    If LayerID = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    PCBSysOpts := Nil;
    Try PCBSysOpts := PCBServer.SystemOptions; Except End;
    If PCBSysOpts = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SYSTEM_OPTIONS',
            'Could not reach PCBServer.SystemOptions, where layer colours live');
        Exit;
    End;

    Try PCBSysOpts.LayerColors[LayerID] := Wanted; Except End;

    { Read back rather than trusting the write, for the same reason the      }
    { mechanical layer kind does: a refused late bound assignment raises     }
    { nothing, so success here would mean only that no exception escaped.    }
    Readback := -1;
    Try Readback := PCBSysOpts.LayerColors[LayerID]; Except Readback := -1; End;
    If Readback <> Wanted Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COLOR_NOT_APPLIED',
            'The write was accepted but the layer still reads as '
            + ColorToHexRgb(Readback) + '. The colour was NOT changed.');
        Exit;
    End;

    Try Board.ViewManager_FullUpdate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"layer":"' + EscapeJsonString(GetLayerString(LayerID)) + '",'
        + '"color":' + IntToStr(Wanted) + ','
        + '"color_hex":"' + ColorToHexRgb(Wanted) + '"}');
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
    Board := GetPCBBoardAnywhere;
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

    LayerID := ResolveLayerId(Board, LayerStr);
    If LayerID = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;
    Visible := (LowerCase(VisibleStr) = 'true') Or (VisibleStr = '1');

    Board.LayerIsDisplayed[LayerID] := Visible;

    // Refresh the view
    // Board.ViewManager_FullUpdate;  // removed, expensive on large boards; Altium auto-refreshes on user interaction

    Result := BuildSuccessResponse(RequestId,
        '{"layer":"' + EscapeJsonString(GetLayerString(LayerID)) + '",'
        + '"visible":' + BoolToJsonStr(Visible) + '}');
End;

{..............................................................................}
{ PCB_RepourPolygons - Repour all polygon pours via RunProcess                }
{..............................................................................}

Function PCB_RepourPolygons(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    ResetParameters;
    RunProcess('PCB:RepourAllPolygons');

    // Board.ViewManager_FullUpdate;  // removed, expensive on large boards; Altium auto-refreshes on user interaction

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
    LowLayer, HighLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    { Resolve BEFORE PreProcess: an unresolvable name has to end the call, and }
    { returning from inside the Try would skip PostProcess.                    }
    If LowLayerStr = '' Then LowLayer := eTopLayer
    Else LowLayer := ResolveLayerId(Board, LowLayerStr);
    If LowLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown low_layer name: ' + LowLayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    If HighLayerStr = '' Then HighLayer := eBottomLayer
    Else HighLayer := ResolveLayerId(Board, HighLayerStr);
    If HighLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown high_layer name: ' + HighLayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

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
        Via.LowLayer := LowLayer;
        Via.HighLayer := HighLayer;

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
        + '"hole_size":' + IntToStr(ViaHole) + ','
        + '"low_layer":"' + EscapeJsonString(GetLayerString(LowLayer)) + '",'
        + '"high_layer":"' + EscapeJsonString(GetLayerString(HighLayer)) + '"}');
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    If LayerStr = '' Then TargetLayer := eTopLayer
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

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

        Track.Layer := TargetLayer;

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
    PipePos, CommaPos, Placed, Failed, FieldIdx : Integer;
    TX1, TY1, TX2, TY2, TWidth : Integer;
    LayerStr, NetStr, BadLayers : String;
    FoundNet : IPCB_Net;
    TrackLayer : TLayer;
    { 7 named locals instead of `Array[0..6] Of String` - fixed-size       }
    { string arrays as function locals corrupt the function return slot   }
    { in DelphiScript, see [[delphiscript_fixed_string_array_bug]].       }
    F0, F1, F2, F3, F4, F5, F6, Token : String;
Begin
    Board := GetPCBBoardAnywhere;
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
    BadLayers := '';
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

            F0 := '';
            F1 := '';
            F2 := '';
            F3 := '';
            F4 := '';
            F5 := '';
            F6 := '';
            FieldIdx := 0;
            While (TrackStr <> '') And (FieldIdx <= 6) Do
            Begin
                CommaPos := Pos(',', TrackStr);
                If CommaPos = 0 Then
                Begin
                    Token := TrackStr;
                    TrackStr := '';
                End
                Else
                Begin
                    Token := Copy(TrackStr, 1, CommaPos - 1);
                    TrackStr := Copy(TrackStr, CommaPos + 1, Length(TrackStr));
                End;
                Case FieldIdx Of
                    0: F0 := Token;
                    1: F1 := Token;
                    2: F2 := Token;
                    3: F3 := Token;
                    4: F4 := Token;
                    5: F5 := Token;
                    6: F6 := Token;
                End;
                Inc(FieldIdx);
            End;

            TX1 := StrToIntDef(F0, 0);
            TY1 := StrToIntDef(F1, 0);
            TX2 := StrToIntDef(F2, 0);
            TY2 := StrToIntDef(F3, 0);
            TWidth := StrToIntDef(F4, 10);
            LayerStr := F5;
            NetStr := F6;

            { An unresolvable name used to fall through to eTopLayer, so one   }
            { typo in a batch quietly stacked that track on the top layer.     }
            { Skip it and name the layer in the response instead.              }
            If LayerStr = '' Then TrackLayer := eTopLayer
            Else TrackLayer := ResolveLayerId(Board, LayerStr);
            If TrackLayer = eNoLayer Then
            Begin
                Inc(Failed);
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;

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

            Track.Layer := TrackLayer;

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
        { board, much cheaper than N individual broadcasts.                   }
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ','
        + '"failed":' + IntToStr(Failed) + ','
        + '"unknown_layers":"' + EscapeJsonString(BadLayers) + '"}');
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    If LayerStr = '' Then TargetLayer := eTopLayer
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

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

        Arc.Layer := TargetLayer;

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
        + '"start_angle":' + FloatToJsonStr(ArcSA) + ','
        + '"end_angle":' + FloatToJsonStr(ArcEA) + ','
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    If LayerStr = '' Then TargetLayer := eTopOverlay
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

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

        TextObj.Layer := TargetLayer;

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
        + '"rotation":' + FloatToJsonStr(TRot) + ','
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    If LayerStr = '' Then TargetLayer := eTopLayer
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

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

        Fill.Layer := TargetLayer;

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
    Board := GetPCBBoardAnywhere;
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

    // Board.ViewManager_FullUpdate;  // removed, expensive on large boards; Altium auto-refreshes on user interaction

    Result := BuildSuccessResponse(RequestId,
        '{"interactive_tool_launched":true,'
        + '"layer":"' + EscapeJsonString(LayerStr) + '",'
        + '"net_name":"' + EscapeJsonString(NetStr) + '",'
        + '"note":"Interactive polygon placement tool launched. Requires user to draw the polygon boundary in Altium Designer, no polygon is created by this call."}');
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
    RuleClear : IPCB_ClearanceConstraint;
    RuleWidth : IPCB_MaxMinWidthConstraint;
    RuleHole : IPCB_MaxMinHoleSizeConstraint;
    RuleDiff : IPCB_DifferentialPairsRoutingRule;
    RuleTypeStr, RuleName, ValueStr, MaxValueStr, FavoredValueStr : String;
    ScopeStr, NetScopeStr, MaxUncoupStr : String;
    RuleValue, MaxValue, FavoredValue, MaxUncoupVal, NetScopeVal : Integer;
    HasMaxValue : Boolean;
    L : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    RuleTypeStr := ExtractJsonValue(Params, 'rule_type');
    RuleName := ExtractJsonValue(Params, 'name');
    ValueStr := ExtractJsonValue(Params, 'value');
    MaxValueStr := ExtractJsonValue(Params, 'max_value');
    FavoredValueStr := ExtractJsonValue(Params, 'favored_value');
    MaxUncoupStr := ExtractJsonValue(Params, 'max_uncoupled_length');
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
    { "different_nets" is the normal setting, same-net tracks touching pads      }
    { of their own net should NOT count as a clearance violation.                  }
    If NetScopeStr = 'different_nets' Then
        NetScopeVal := eNetScope_DifferentNetsOnly
    Else If NetScopeStr = 'same_net' Then
        NetScopeVal := eNetScope_SameNetOnly
    Else
        NetScopeVal := eNetScope_AnyNet;

    RuleValue := StrToIntDef(ValueStr, 10);

    { Independent max / favored values: when omitted, fall back to the legacy }
    { 5x-min default for width/via_size so existing callers keep working. The }
    { previous handler forced max = value * 5 unconditionally, which silently }
    { clamped any Width rule's max to 25 mil when value was 5 mil and broke   }
    { wider power-trace use cases.                                              }
    HasMaxValue := MaxValueStr <> '';
    MaxValue := StrToIntDef(MaxValueStr, RuleValue * 5);
    FavoredValue := StrToIntDef(FavoredValueStr, RuleValue);
    MaxUncoupVal := StrToIntDef(MaxUncoupStr, 1000);

    { Constraint values are NOT properties of the base IPCB_Rule interface,    }
    { they live on the per-kind subtypes (IPCB_ClearanceConstraint,            }
    { IPCB_MaxMinWidthConstraint, IPCB_MaxMinHoleSizeConstraint, ...). DelphiScript  }
    { needs the variable typed as the actual subtype to expose constraint      }
    { setters; assigning to a base IPCB_Rule var and then writing Rule.Gap     }
    { fails with "Undeclared identifier" on builds where IPCB_Rule does not    }
    { surface the union of constraint properties (e.g. AD 26.5+).              }
    { Indexed properties also use function-call form: RuleWidth.MinWidth(L)    }
    { not RuleWidth.MinWidth[L], bracket form is rejected in DelphiScript.    }
    Rule := Nil;
    PCBServer.PreProcess;
    Try
        If RuleTypeStr = 'clearance' Then
        Begin
            RuleClear := PCBServer.PCBRuleFactory(eRule_Clearance);
            RuleClear.Name := RuleName;
            RuleClear.NetScope := NetScopeVal;
            RuleClear.LayerKind := eRuleLayerKind_SameLayer;
            RuleClear.Gap := MilsToCoord(RuleValue);
            If ScopeStr <> '' Then
                RuleClear.Scope1Expression := ScopeStr;
            Rule := RuleClear;
        End
        Else If RuleTypeStr = 'width' Then
        Begin
            RuleWidth := PCBServer.PCBRuleFactory(eRule_MaxMinWidth);
            RuleWidth.Name := RuleName;
            RuleWidth.NetScope := NetScopeVal;
            RuleWidth.LayerKind := eRuleLayerKind_SameLayer;
            For L := MinLayer To MaxLayer Do
            Begin
                RuleWidth.MinWidth(L) := MilsToCoord(RuleValue);
                RuleWidth.MaxWidth(L) := MilsToCoord(MaxValue);
                RuleWidth.FavoredWidth(L) := MilsToCoord(FavoredValue);
            End;
            If ScopeStr <> '' Then
                RuleWidth.Scope1Expression := ScopeStr;
            Rule := RuleWidth;
        End
        Else If RuleTypeStr = 'via_size' Then
        Begin
            RuleHole := PCBServer.PCBRuleFactory(eRule_MaxMinHoleSize);
            RuleHole.Name := RuleName;
            RuleHole.NetScope := NetScopeVal;
            RuleHole.LayerKind := eRuleLayerKind_SameLayer;
            RuleHole.MinLimit := MilsToCoord(RuleValue);
            RuleHole.MaxLimit := MilsToCoord(MaxValue);
            If ScopeStr <> '' Then
                RuleHole.Scope1Expression := ScopeStr;
            Rule := RuleHole;
        End
        Else If RuleTypeStr = 'differential_pairs' Then
        Begin
            { IPCB_DifferentialPairsRoutingRule exposes MinGap / MaxGap /     }
            { PreferedGap (note SDK spelling: one 'r') as layer-indexed      }
            { properties, and MaxUncoupledLength as a single scalar. value ->}
            { MinGap, max_value -> MaxGap, favored_value -> PreferedGap. The }
            { width constraints shown in the rule's descriptor come from a   }
            { separate Width rule scoped to the diff pair, not from this    }
            { interface; create that separately with rule_type='width' if   }
            { needed.                                                          }
            RuleDiff := PCBServer.PCBRuleFactory(eRule_DifferentialPairsRouting);
            RuleDiff.Name := RuleName;
            RuleDiff.NetScope := NetScopeVal;
            RuleDiff.LayerKind := eRuleLayerKind_SameLayer;
            For L := MinLayer To MaxLayer Do
            Begin
                RuleDiff.MinGap(L) := MilsToCoord(RuleValue);
                RuleDiff.MaxGap(L) := MilsToCoord(MaxValue);
                RuleDiff.PreferedGap(L) := MilsToCoord(FavoredValue);
            End;
            RuleDiff.MaxUncoupledLength := MilsToCoord(MaxUncoupVal);
            If ScopeStr <> '' Then
                RuleDiff.Scope1Expression := ScopeStr;
            Rule := RuleDiff;
        End
        Else
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAM',
                'Unknown rule_type: ' + RuleTypeStr + '. Use clearance, width, via_size, or differential_pairs');
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
    Board := GetPCBBoardAnywhere;
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
    Board := GetPCBBoardAnywhere;
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
            + '"rotation":' + FloatToJsonStr(Pad.Rotation) + '}';
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
    Board := GetPCBBoardAnywhere;
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
    CommaPos, I, CX, CY : Integer;
    { Heap-allocated list of resolved designators. The original code held }
    { IPCB_Component pointers in `Array[0..99] Of IPCB_Component`, which  }
    { is a fixed-size local array of a managed type - that triggers the   }
    { return-slot corruption documented in                                 }
    { [[delphiscript_fixed_string_array_bug]]. The fix walks twice:        }
    { first pass to validate + measure bounds, second pass to apply.      }
    Resolved : TStringList;
    MinX, MaxX, MinY, MaxY, CenterX, CenterY : Integer;
Begin
    Board := GetPCBBoardAnywhere;
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

    Resolved := TStringList.Create;
    Try
        Remaining := DesStr;
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
                Comp := Board.GetPcbComponentByRefDes(OneDesig);
                If Comp <> Nil Then Resolved.Add(OneDesig);
            End;
        End;

        If Resolved.Count < 2 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INSUFFICIENT',
                'Need at least 2 valid components to align');
            Exit;
        End;

        { First pass: bounding extents. }
        Comp := Board.GetPcbComponentByRefDes(Resolved[0]);
        MinX := CoordToMils(Comp.x);
        MaxX := MinX;
        MinY := CoordToMils(Comp.y);
        MaxY := MinY;
        For I := 1 To Resolved.Count - 1 Do
        Begin
            Comp := Board.GetPcbComponentByRefDes(Resolved[I]);
            If Comp = Nil Then Continue;
            CX := CoordToMils(Comp.x);
            CY := CoordToMils(Comp.y);
            If CX < MinX Then MinX := CX;
            If CX > MaxX Then MaxX := CX;
            If CY < MinY Then MinY := CY;
            If CY > MaxY Then MaxY := CY;
        End;
        CenterX := (MinX + MaxX) Div 2;
        CenterY := (MinY + MaxY) Div 2;

        { Second pass: apply alignment. }
        PCBServer.PreProcess;
        Try
            For I := 0 To Resolved.Count - 1 Do
            Begin
                Comp := Board.GetPcbComponentByRefDes(Resolved[I]);
                If Comp = Nil Then Continue;
                PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                    PCBM_BeginModify, c_NoEventData);

                If AlignStr = 'left' Then
                    Comp.x := MilsToCoord(MinX)
                Else If AlignStr = 'right' Then
                    Comp.x := MilsToCoord(MaxX)
                Else If AlignStr = 'top' Then
                    Comp.y := MilsToCoord(MaxY)
                Else If AlignStr = 'bottom' Then
                    Comp.y := MilsToCoord(MinY)
                Else If AlignStr = 'center_x' Then
                    Comp.x := MilsToCoord(CenterX)
                Else If AlignStr = 'center_y' Then
                    Comp.y := MilsToCoord(CenterY);

                PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                    PCBM_EndModify, c_NoEventData);
            End;
        Finally
            PCBServer.PostProcess;
        End;

        SaveDocByPath(Board.FileName);

        Result := BuildSuccessResponse(RequestId,
            '{"aligned":true,'
            + '"alignment":"' + EscapeJsonString(AlignStr) + '",'
            + '"component_count":' + IntToStr(Resolved.Count) + '}');
    Finally
        Resolved.Free;
    End;
End;

{..............................................................................}
{ PCB_GetClearanceViolations - Get clearance violations for a net             }
{ Params: net (optional) - if specified, only show violations for this net   }
{                                                                            }
{ READ-ONLY: this reports the eViolationObject records ALREADY on the board  }
{ (left by the last DRC run, batch or online). It does NOT trigger a DRC.    }
{ It used to call RunProcess('PCB:DesignRuleCheck'), which raises the modal  }
{ "Design Rule Checker" setup dialog and wedged the whole bridge for 30+ min }
{ on a 241-component board -- a "get" tool must never do that. A fresh run   }
{ is PCB_RunDRC with allow_modal=true, which is opt-in for that reason.      }
{ Because nothing is triggered here, violation_count = 0 means "no stored    }
{ violations", which on a board where DRC has never run is NOT a pass; the   }
{ response says so via drc_triggered / note.                                 }
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
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    FilterNet := ExtractJsonValue(Params, 'net');

    { No DRC trigger here -- see the header. Iterating eViolationObject is   }
    { a pure board read: it opens no dialog and cannot block the loop.       }
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
        + '"drc_triggered":false,'
        + '"note":"Existing violations only -- no DRC was run. 0 does not mean '
        + 'the board passes if DRC has never been run on it.",'
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
    Board := GetPCBBoardAnywhere;
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
    Board := GetPCBBoardAnywhere;
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
    Board := GetPCBBoardAnywhere;
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
    TrkObj : IPCB_Track;
    XStr, YStr, LayerStr, ObjTypeStr : String;
    TargetX, TargetY, ObjX, ObjY : Integer;
    TargetLayer : TLayer;
    ObjFilter : TObjectId;
    Found : Boolean;
    FoundObj : IPCB_Primitive;
    Dist, BestDist : Double;
    BRect : TCoordRect;
Begin
    Board := GetPCBBoardAnywhere;
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

    If LayerStr = '' Then TargetLayer := eTopLayer
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    // Map object type string to filter
    If ObjTypeStr = 'track' Then
        ObjFilter := eTrackObject
    Else If ObjTypeStr = 'via' Then
        ObjFilter := eViaObject
    Else If ObjTypeStr = 'fill' Then
        ObjFilter := eFillObject
    Else If ObjTypeStr = 'text' Then
        ObjFilter := eTextObject
    Else If ObjTypeStr = 'pad' Then
        ObjFilter := ePadObject
    Else If ObjTypeStr = 'arc' Then
        ObjFilter := eArcObject
    Else If ObjTypeStr = 'polygon' Then
        ObjFilter := ePolyObject
    Else If ObjTypeStr = 'region' Then
        ObjFilter := eRegionObject
    Else If ObjTypeStr = 'component' Then
        ObjFilter := eComponentObject
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAM',
            'Unknown object_type: ' + ObjTypeStr +
            '. Use track, via, fill, text, pad, arc, polygon, region, or component');
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
            TrkObj := Obj;
            ObjX := CoordToMils((TrkObj.X1 + TrkObj.X2) Div 2);
            ObjY := CoordToMils((TrkObj.Y1 + TrkObj.Y2) Div 2);
        End
        Else If (ObjFilter = eViaObject) Or (ObjFilter = ePadObject) Then
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
            { Polygons, regions, components, arcs and text: use the bounding
              rectangle centre -- XLocation is not exposed on every type. }
            BRect := Obj.BoundingRectangle;
            ObjX := CoordToMils((BRect.Left + BRect.Right) Div 2);
            ObjY := CoordToMils((BRect.Bottom + BRect.Top) Div 2);
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
        + '"distance_mils":' + FloatToJsonStr(BestDist) + '}');
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
    Board := GetPCBBoardAnywhere;
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
            + '"rotation":' + FloatToJsonStr(Pad.Rotation) + ','
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
    Prim : IPCB_Primitive;
    Track : IPCB_Track;
    NetNameStr, WidthStr, TrackNetName : String;
    NewWidth, ModCount, I : Integer;
    Matches : TInterfaceList;
Begin
    Board := GetPCBBoardAnywhere;
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

    { Collect first, THEN modify. Changing Track.Width while the BoardIterator
      is still walking corrupts the iterator and hangs the loop -- never mutate
      during iteration. }
    Matches := CreateObject(TInterfaceList);
    Iterator := Board.BoardIterator_Create;
    Try
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        { Walk + collect as the BASE IPCB_Primitive, exactly like the proven
          PCB_Scale / CollectSelectedPCBPrims path. A TInterfaceList stores
          untyped IInterface; assigning a retrieved item straight to a DERIVED
          IPCB_Track skips QueryInterface and leaves a mistyped pointer whose
          vtable call faults in oleaut32 (read of FFFFFFFF). Narrow to Track
          only in a typed local, after retrieval. }
        Prim := Iterator.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Track := Prim;
            TrackNetName := '';
            Try If Track.Net <> Nil Then TrackNetName := Track.Net.Name; Except End;
            If TrackNetName = NetNameStr Then Matches.Add(Prim);
            Prim := Iterator.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iterator);
    End;

    PCBServer.PreProcess;
    Try
        For I := 0 To Matches.Count - 1 Do
        Begin
            Prim := Matches.Items[I];
            If Prim = Nil Then Continue;
            { Skip child primitives of a component / polygon / dimension --
              modifying those faults the same way (see PCB_Scale guard). }
            If Prim.InComponent Or Prim.InPolygon Or Prim.InDimension Then Continue;
            Try
                Track := Prim;
                Track.BeginModify;
                Track.Width := MilsToCoord(NewWidth);
                Track.EndModify;
                Inc(ModCount);
            Except End;
        End;
    Finally
        PCBServer.PostProcess;
    End;
    { Do NOT Free a TInterfaceList holding board-primitive interface refs --
      releasing them through the COM marshaller faults in oleaut32 (read of
      FFFFFFFF). PCB_Scale / CollectSelectedPCBPrims leave the list to the
      script host for the same reason. }

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
    JsonItems, NetName, CountStr : String;
    FinalResp : String;
    First : Boolean;
    Count, I, FoundIdx, NewCount : Integer;
    { Heap-allocated parallel lists. Function-local `Array[0..N] Of T`     }
    { where T is any type (String, Integer, ...) silently corrupts this    }
    { function's return slot in DelphiScript - both array-of-string AND   }
    { array-of-int trigger it, the originally-documented narrower theory   }
    { was wrong. See [[delphiscript_fixed_string_array_bug]].              }
    NetNames, NetCounts : TStringList;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetNames := TStringList.Create;
    NetCounts := TStringList.Create;
    Try
        Count := 0;

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
                    If Obj.Net <> Nil Then NetName := Obj.Net.Name;
                Except End;

                FoundIdx := NetNames.IndexOf(NetName);
                If FoundIdx >= 0 Then
                Begin
                    NewCount := StrToIntDef(NetCounts[FoundIdx], 0) + 1;
                    NetCounts[FoundIdx] := IntToStr(NewCount);
                End
                Else
                Begin
                    NetNames.Add(NetName);
                    NetCounts.Add('1');
                End;

                Inc(Count);
            End;
            Obj := Iterator.NextPCBObject;
        End;
        Board.BoardIterator_Destroy(Iterator);

        JsonItems := '';
        First := True;
        For I := 0 To NetNames.Count - 1 Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            CountStr := NetCounts[I];
            JsonItems := JsonItems + '{"net":"' + EscapeJsonString(NetNames[I]) + '",'
                + '"unrouted_connections":' + CountStr + '}';
        End;

        FinalResp := BuildSuccessResponse(RequestId,
            '{"unrouted_nets":[' + JsonItems + '],"net_count":' + IntToStr(NetNames.Count)
            + ',"total_unrouted":' + IntToStr(Count) + '}');
        Result := FinalResp;
    Finally
        NetCounts.Free;
        NetNames.Free;
    End;
End;

{..............................................................................}
{ PolygonAreaSqMils - Compute a polygon's outline area in SQUARE MILS via the  }
{ shoelace formula over its line vertices. Altium's IPCB_Polygon.AreaSize is   }
{ unreliable (it can exceed the bounding box) and IPCB_Polygon.GeometricPolygon }
{ is undeclared in this script binding, so this vertex sum is the trustworthy   }
{ area. CRITICAL: convert each coord to mils (/ 10000.0, a REAL division)       }
{ BEFORE multiplying -- raw internal coords (~2e7) overflow 32-bit integer      }
{ multiplication and yield garbage.                                            }
{..............................................................................}

Function PolygonAreaSqMils(Poly : IPCB_Polygon) : Double;
Var
    I, N : Integer;
    Ax, Ay, Bx, By, Sum : Double;
Begin
    Result := 0;
    N := 0;
    Try N := Poly.PointCount; Except End;
    If N < 3 Then Exit;
    Sum := 0;
    For I := 0 To N - 1 Do
    Begin
        Try
            Ax := Poly.Segments[I].vx / 10000.0;
            Ay := Poly.Segments[I].vy / 10000.0;
            If I < N - 1 Then
            Begin
                Bx := Poly.Segments[I + 1].vx / 10000.0;
                By := Poly.Segments[I + 1].vy / 10000.0;
            End
            Else
            Begin
                Bx := Poly.Segments[0].vx / 10000.0;
                By := Poly.Segments[0].vy / 10000.0;
            End;
            Sum := Sum + (Ax * By - Bx * Ay);
        Except End;
    End;
    Result := Abs(Sum) / 2.0;
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
    Count, VCount : Integer;
    AreaInternal : Int64;
    AreaSqMils, AreaMm2, BBoxMm2 : Double;
    BR : TCoordRect;
Begin
    Board := GetPCBBoardAnywhere;
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
            If Polygon.PolyHatchStyle = ePolySolid Then HatchStr := 'Solid'
            Else If Polygon.PolyHatchStyle = ePolyNoHatch Then HatchStr := 'NoHatch'
            Else If Polygon.PolyHatchStyle = ePolyHatch45 Then HatchStr := '45Degree'
            Else If Polygon.PolyHatchStyle = ePolyHatch90 Then HatchStr := '90Degree'
            Else HatchStr := 'Other';
        Except End;

        { Compute actual copper area (after pour) and bounding-rect      }
        { area for the polygon outline. Used for current-capacity audits }
        { (multiply area_mm2 by copper thickness for cubic copper) and   }
        { for spotting accidentally-tiny power islands.                  }
        { AreaSize is the polygon OUTLINE area. IPCB_Polygon.GeometricPolygon
          is undeclared in this Altium script binding, so the actual-copper
          area is not available here -- use AreaSize (outline) + bbox. }
        AreaSqMils := 0;
        Try AreaSqMils := PolygonAreaSqMils(Polygon); Except End;
        AreaMm2 := AreaSqMils * 0.00064516;
        BR := Polygon.BoundingRectangle;
        BBoxMm2 := CoordToMM(BR.Right - BR.Left)
                 * CoordToMM(BR.Top - BR.Bottom);
        VCount := 0;
        Try VCount := Polygon.PointCount; Except End;

        JsonItems := JsonItems + '{"index":' + IntToStr(Count) + ','
            + '"name":"' + EscapeJsonString(Polygon.Name) + '",'
            + '"net":"' + EscapeJsonString(NetName) + '",'
            + '"layer":"' + EscapeJsonString(LayerStr) + '",'
            + '"hatch_style":"' + EscapeJsonString(HatchStr) + '",'
            + '"pour_over":' + BoolToJsonStr(Polygon.PourOver <> ePolygonPourOver_None) + ','
            + '"area_sqmils":' + IntToStr(Trunc(AreaSqMils)) + ','
            + '"area_mm2":' + FloatToJsonStr(AreaMm2) + ','
            + '"bbox_mm2":' + FloatToJsonStr(BBoxMm2) + ','
            + '"vertex_count":' + IntToStr(VCount) + '}';
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    TargetLayer := eNoLayer;
    If LayerStr <> '' Then
    Begin
        TargetLayer := ResolveLayerId(Board, LayerStr);
        If TargetLayer = eNoLayer Then
        Begin
            Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
                'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
            Exit;
        End;
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
        If TargetLayer <> eNoLayer Then
            FoundPoly.Layer := TargetLayer;

        // Modify hatch style
        If HatchStr <> '' Then
        Begin
            If HatchStr = 'Solid' Then
                FoundPoly.PolyHatchStyle := ePolySolid
            Else If HatchStr = 'NoHatch' Then
                FoundPoly.PolyHatchStyle := ePolyNoHatch
            Else If HatchStr = '45Degree' Then
                FoundPoly.PolyHatchStyle := ePolyHatch45
            Else If HatchStr = '90Degree' Then
                FoundPoly.PolyHatchStyle := ePolyHatch90;
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
        + '"layer":"' + EscapeJsonString(GetLayerString(FoundPoly.Layer)) + '",'
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
    Room : IPCB_ConfinementConstraint;
    JsonItems, KindStr : String;
    BR : TCoordRect;
    First : Boolean;
    Count : Integer;
Begin
    Board := GetPCBBoardAnywhere;
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

            { BoundingRect / Kind / Comment etc. live on IPCB_ConfinementConstraint, }
            { not on the base IPCB_Rule. Narrow the typed reference before reading. }
            Room := Rule;
            Try
                BR := Room.BoundingRect;
            Except
                BR.Left := 0; BR.Bottom := 0; BR.Right := 0; BR.Top := 0;
            End;

            Try
                If Room.Kind = eConfineIn Then KindStr := 'ConfineIn'
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
    Board := GetPCBBoardAnywhere;
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

        { Materialize the record by reading it from the rule first; writing a
          field of a never-assigned TCoordRect local raises "Undeclared
          identifier: Left" and halts the loop. }
        CoordRect := Rule.BoundingRect;
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
    TrkObj : IPCB_Track;
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
    Board := GetPCBBoardAnywhere;
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
            TrkObj := Obj;
            DX := CoordToMils(TrkObj.X2) - CoordToMils(TrkObj.X1);
            DY := CoordToMils(TrkObj.Y2) - CoordToMils(TrkObj.Y1);
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
        + '"total_trace_length_mils":' + FloatToJsonStr(TotalTraceLen) + ','
        + '"board_width_mils":' + FloatToJsonStr(BoardWidth) + ','
        + '"board_height_mils":' + FloatToJsonStr(BoardHeight) + ','
        + '"board_area_sq_mils":' + FloatToJsonStr(BoardArea) + ','
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
    Board := GetPCBBoardAnywhere;
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
            + '"rotation":' + FloatToJsonStr(Comp.Rotation) + ','
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
    Board := GetPCBBoardAnywhere;
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
          DelphiScript idiom, you assign a
          full TPolySegment record to Segments[I] rather than writing
          individual fields through the indexed property. Build each
          corner as a local record and assign in order. }
        Board.BoardOutline.PointCount := 4;
        Seg := TPolySegment;   { instantiate before any field write -- see memory }
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    { The layer is resolved BEFORE anything is created. "Internal Plane 1"    }
    { used to reach GetLayerFromString, miss the canonical table, and come     }
    { back as eTopLayer - so a PGND pour and a VBUS_PROT pour both landed on   }
    { the top layer, each answering placed:true with layer echoing the        }
    { REQUESTED plane name. That is a board-wide short.                       }
    If LayerStr = '' Then TargetLayer := eTopLayer
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Polygon := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
        If Polygon = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create polygon object');
            Exit;
        End;

        Polygon.Layer := TargetLayer;

        Polygon.PolyHatchStyle := ePolySolid;

        { Build the 4-corner outline via the Segments API. CRITICAL: a local
          TPolySegment must be instantiated with ':= TPolySegment' before its
          fields can be written -- without that, Seg.Kind := ... raises
          "Undeclared identifier: Kind" in the script engine. IPCB_Polygon has
          no SetOutlineContour (that is a region-only method), so Segments is
          the only path. Whole-record writes (Segments[i] := Seg) are fine. }
        Polygon.PointCount := 4;
        Seg := TPolySegment;
        Seg.Kind := ePolySegmentLine;
        Seg.vx := Cx1;  Seg.vy := Cy1;  Polygon.Segments[0] := Seg;
        Seg.vx := Cx2;  Seg.vy := Cy1;  Polygon.Segments[1] := Seg;
        Seg.vx := Cx2;  Seg.vy := Cy2;  Polygon.Segments[2] := Seg;
        Seg.vx := Cx1;  Seg.vy := Cy2;  Polygon.Segments[3] := Seg;

        { Assign net if specified. }
        If NetStr <> '' Then
        Begin
            Try FoundNet := FindNetByName(Board,NetStr); Except FoundNet := Nil; End;
            If FoundNet <> Nil Then Polygon.Net := FoundNet;
        End;

        { PourOver controls whether the polygon pours over existing
          same-net primitives (tracks/pads). Default true, matches the
          common "pour GND plane" case. }
        If (PourOverStr = '') Or (LowerCase(PourOverStr) = 'true') Then
            Polygon.PourOver := ePolygonPourOver_SameNet
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
        + '"layer":"' + EscapeJsonString(GetLayerString(Polygon.Layer)) + '",'
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
    Board := GetPCBBoardAnywhere;
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
        Try FoundNet := FindNetByName(Board,NetStr); Except End;

    If LowLayerStr = '' Then LowLayer := eTopLayer
    Else LowLayer := ResolveLayerId(Board, LowLayerStr);
    If LowLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown low_layer name: ' + LowLayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;
    If HighLayerStr = '' Then HighLayer := eBottomLayer
    Else HighLayer := ResolveLayerId(Board, HighLayerStr);
    If HighLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown high_layer name: ' + HighLayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

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
        + '"low_layer":"' + EscapeJsonString(GetLayerString(LowLayer)) + '",'
        + '"high_layer":"' + EscapeJsonString(GetLayerString(HighLayer)) + '",'
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
    Board := GetPCBBoardAnywhere;
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
    Try PosNetObj := FindNetByName(Board,PosNet); Except End;
    Try NegNetObj := FindNetByName(Board,NegNet); Except End;
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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

    If LayerStr = '' Then TargetLayer := eTopLayer
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
        If Region = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create region object');
            Exit;
        End;

        Region.Layer := TargetLayer;

        { IPCB_Region uses MainContour + SetOutlineContour (NOT the polygon
          Segments API). Note that the contour X[I]/Y[I] arrays are
          1-based (not 0-based). }
        Contour := Region.MainContour.Replicate;
        Contour.Count := 4;
        Contour.X[1] := Cx1;  Contour.Y[1] := Cy1;
        Contour.X[2] := Cx2;  Contour.Y[2] := Cy1;
        Contour.X[3] := Cx2;  Contour.Y[3] := Cy2;
        Contour.X[4] := Cx1;  Contour.Y[4] := Cy2;
        Region.SetOutlineContour(Contour);

        If NetStr <> '' Then
        Begin
            Try FoundNet := FindNetByName(Board,NetStr); Except FoundNet := Nil; End;
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
        + '"layer":"' + EscapeJsonString(GetLayerString(TargetLayer)) + '",'
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
    Board := GetPCBBoardAnywhere;
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
        { No CompList.Free -- releasing a TInterfaceList of board-component
          interface refs faults in oleaut32; leave it to the script host. }
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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
    TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;
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

        Dim.Layer := TargetLayer;
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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
    TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;
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
        Pad.Layer := TargetLayer;
        If NameStr <> '' Then Pad.Name := NameStr;

        If Shape = 'rect' Then Pad.TopShape := eRectangular
        Else If Shape = 'oct' Then Pad.TopShape := eOctagonal
        Else Pad.TopShape := eRounded;

        If NetStr <> '' Then
        Begin
            Try FoundNet := FindNetByName(Board,NetStr); Except FoundNet := Nil; End;
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
        + '"layer":"' + EscapeJsonString(GetLayerString(TargetLayer)) + '",'
        + '"name":"' + EscapeJsonString(NameStr) + '",'
        + '"net":"' + EscapeJsonString(NetStr) + '"}');
End;

{..............................................................................}
{ PCB_PlaceComponent - Place a footprint from a PcbLib directly onto the      }
{ board, WITHOUT an ECO. This is the scriptable substitute for Design >       }
{ Update PCB Document (which is not scriptable): drop footprints whose        }
{ designators match the schematic so the board can be populated and          }
{ auto-placed. Pattern from Allen Gong (forum.live.altium.com/#posts/241580): }
{ PCBObjectFactory(eComponentObject) + IPCB_Component.LoadFromLibrary.        }
{ Note: this places geometry only; it does NOT create the sch<->pcb linkage   }
{ or assign pad nets (those come from a real ECO).                            }
{ Params: footprint (req), library_path (req, .PcbLib), lib_reference,        }
{         x, y (mils), rotation, layer, designator, comment                   }
{..............................................................................}

Function PCB_PlaceComponent(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Net : IPCB_Net;
    X, Y, NetsAssigned : Integer;
    Linked : Boolean;
    Rotation : Double;
    Footprint, LibPath, LibRef, Designator, Comment, LayerStr, LoadStr : String;
    UniqueIdStr, PadNetsStr, PadName, NetName, BoardPathStr : String;
    CompLayer : TLayer;
Begin
    { Target a specific board by path when several PcbDocs are open, so a    }
    { placement can't silently land on the wrong (focused) board. Empty      }
    { board_path falls back to the current/focused board.                    }
    BoardPathStr := ExtractJsonValue(Params, 'board_path');
    Board := ResolvePCBBoard(BoardPathStr);
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Footprint  := ExtractJsonValue(Params, 'footprint');
    LibPath    := ExtractJsonValue(Params, 'library_path');
    LibRef     := ExtractJsonValue(Params, 'lib_reference');
    Designator := ExtractJsonValue(Params, 'designator');
    Comment    := ExtractJsonValue(Params, 'comment');
    LayerStr   := ExtractJsonValue(Params, 'layer');
    UniqueIdStr := ExtractJsonValue(Params, 'unique_id');
    PadNetsStr  := ExtractJsonValue(Params, 'pad_nets');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Rotation := StrToFloatDef(ExtractJsonValue(Params, 'rotation'), 0);
    NetsAssigned := 0;
    Linked := False;

    If Footprint = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "footprint" parameter');
        Exit;
    End;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'Missing "library_path" parameter (.PcbLib)');
        Exit;
    End;
    If LibRef = '' Then LibRef := Footprint;
    If LayerStr = '' Then LayerStr := 'TopLayer';
    CompLayer := ResolveLayerId(Board, LayerStr);
    If CompLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Comp := PCBServer.PCBObjectFactory(eComponentObject, eNoDimension, eCreate_Default);
        If Comp = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create component object');
            Exit;
        End;

        Comp.Board := Board;
        LoadStr := 'SourceLibReference=' + LibRef + '|FootPrint=' + Footprint
                 + '|SourceComponentLibrary=' + LibPath;
        Comp.LoadFromLibrary(LoadStr);
        Comp.Layer := CompLayer;
        Comp.x := MilsToCoord(X);
        Comp.y := MilsToCoord(Y);
        Comp.Rotation := Rotation;
        If Designator <> '' Then Comp.Name.Text := Designator;
        If Comment <> '' Then Comp.Comment.Text := Comment;

        { sch<->pcb link: stamping the source UniqueId + designator makes a    }
        { later ECO treat this part as MATCHED to its schematic counterpart    }
        { instead of "extra in PCB". Same writable Source* family as the       }
        { proven Comp.SourceFootprintLibrary assignment.                        }
        If UniqueIdStr <> '' Then
        Begin
            Comp.SourceUniqueId := UniqueIdStr;
            If Designator <> '' Then Comp.SourceDesignator := Designator;
            Linked := True;
        End;

        Board.AddPCBObject(Comp);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Comp.I_ObjectAddress);

        { pad nets: create each named net if missing, assign it to the pad,    }
        { giving the board real connectivity (ratsnest + DRC) without an ECO.  }
        If PadNetsStr <> '' Then
        Begin
            GrpIter := Comp.GroupIterator_Create;
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                PadName := '';
                Try PadName := Pad.Name; Except End;
                NetName := GetPadNet(PadNetsStr, PadName);
                If NetName <> '' Then
                Begin
                    Net := EnsureNet(Board, NetName);
                    If Net <> Nil Then
                    Begin
                        Pad.Net := Net;
                        NetsAssigned := NetsAssigned + 1;
                    End;
                End;
                Pad := GrpIter.NextPCBObject;
            End;
            Comp.GroupIterator_Destroy(GrpIter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"footprint":"' + EscapeJsonString(Footprint) + '",'
        + '"designator":"' + EscapeJsonString(Designator) + '",'
        + '"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + ','
        + '"rotation":' + FloatToJsonStr(Rotation) + ','
        + '"layer":"' + EscapeJsonString(LayerStr) + '",'
        + '"linked":' + BoolToJsonStr(Linked) + ','
        + '"nets_assigned":' + IntToStr(NetsAssigned) + '}');
End;

{ Read one field from a batch placement record encoded as                     }
{ key==value;;key==value (so pad_nets' single '=' and '|' don't collide).     }
Function GetPlacementField(Str, Key : String) : String;
Var
    Token, Remaining, K : String;
    SepPos, EqPos : Integer;
Begin
    Result := '';
    Remaining := Str;
    While Remaining <> '' Do
    Begin
        SepPos := Pos(';;', Remaining);
        If SepPos > 0 Then
        Begin
            Token := Copy(Remaining, 1, SepPos - 1);
            Remaining := Copy(Remaining, SepPos + 2, Length(Remaining));
        End
        Else
        Begin
            Token := Remaining;
            Remaining := '';
        End;
        EqPos := Pos('==', Token);
        If EqPos > 0 Then
        Begin
            K := Copy(Token, 1, EqPos - 1);
            If K = Key Then
            Begin
                Result := Copy(Token, EqPos + 2, Length(Token));
                Exit;
            End;
        End;
    End;
End;

{..............................................................................}
{ PCB_PlaceComponents - place MANY footprints in ONE call (board resolved     }
{ once, one PreProcess/Save). Same synced-placement logic as the singular     }
{ handler. Records separated by '~~'; fields key==value;;key==value.          }
{..............................................................................}

Function PCB_PlaceComponents(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Net : IPCB_Net;
    PlacementsStr, BoardPathStr, OnePlace, Remaining, LoadStr : String;
    Footprint, LibPath, LibRef, Designator, Comment, LayerStr : String;
    UniqueIdStr, PadNetsStr, PadName, NetName, BadLayers : String;
    X, Y, PlacedCount, FailedCount, SepPos : Integer;
    Rotation : Double;
    CompLayer : TLayer;
Begin
    BoardPathStr  := ExtractJsonValue(Params, 'board_path');
    PlacementsStr := ExtractJsonValue(Params, 'placements');
    Board := ResolvePCBBoard(BoardPathStr);
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    PlacedCount := 0;
    FailedCount := 0;
    BadLayers := '';

    PCBServer.PreProcess;
    Try
        Remaining := PlacementsStr;
        While Remaining <> '' Do
        Begin
            SepPos := Pos('~~', Remaining);
            If SepPos > 0 Then
            Begin
                OnePlace := Copy(Remaining, 1, SepPos - 1);
                Remaining := Copy(Remaining, SepPos + 2, Length(Remaining));
            End
            Else
            Begin
                OnePlace := Remaining;
                Remaining := '';
            End;
            If OnePlace = '' Then Continue;

            Footprint   := GetPlacementField(OnePlace, 'footprint');
            LibPath     := GetPlacementField(OnePlace, 'library_path');
            LibRef      := GetPlacementField(OnePlace, 'lib_reference');
            Designator  := GetPlacementField(OnePlace, 'designator');
            Comment     := GetPlacementField(OnePlace, 'comment');
            LayerStr    := GetPlacementField(OnePlace, 'layer');
            UniqueIdStr := GetPlacementField(OnePlace, 'unique_id');
            PadNetsStr  := GetPlacementField(OnePlace, 'pad_nets');
            X := StrToIntDef(GetPlacementField(OnePlace, 'x'), 0);
            Y := StrToIntDef(GetPlacementField(OnePlace, 'y'), 0);
            Rotation := StrToFloatDef(GetPlacementField(OnePlace, 'rotation'), 0);

            If (Footprint = '') Or (LibPath = '') Then
            Begin
                FailedCount := FailedCount + 1;
                Continue;
            End;
            If LibRef = '' Then LibRef := Footprint;
            If LayerStr = '' Then LayerStr := 'TopLayer';
            CompLayer := ResolveLayerId(Board, LayerStr);
            If CompLayer = eNoLayer Then
            Begin
                FailedCount := FailedCount + 1;
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;

            Comp := PCBServer.PCBObjectFactory(eComponentObject, eNoDimension, eCreate_Default);
            If Comp = Nil Then
            Begin
                FailedCount := FailedCount + 1;
                Continue;
            End;

            Comp.Board := Board;
            LoadStr := 'SourceLibReference=' + LibRef + '|FootPrint=' + Footprint
                     + '|SourceComponentLibrary=' + LibPath;
            Comp.LoadFromLibrary(LoadStr);
            Comp.Layer := CompLayer;
            Comp.x := MilsToCoord(X);
            Comp.y := MilsToCoord(Y);
            Comp.Rotation := Rotation;
            If Designator <> '' Then Comp.Name.Text := Designator;
            If Comment <> '' Then Comp.Comment.Text := Comment;
            If UniqueIdStr <> '' Then
            Begin
                Comp.SourceUniqueId := UniqueIdStr;
                If Designator <> '' Then Comp.SourceDesignator := Designator;
            End;

            Board.AddPCBObject(Comp);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                PCBM_BoardRegisteration, Comp.I_ObjectAddress);

            If PadNetsStr <> '' Then
            Begin
                GrpIter := Comp.GroupIterator_Create;
                GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                Pad := GrpIter.FirstPCBObject;
                While Pad <> Nil Do
                Begin
                    PadName := '';
                    Try PadName := Pad.Name; Except End;
                    NetName := GetPadNet(PadNetsStr, PadName);
                    If NetName <> '' Then
                    Begin
                        Net := EnsureNet(Board, NetName);
                        If Net <> Nil Then Pad.Net := Net;
                    End;
                    Pad := GrpIter.NextPCBObject;
                End;
                Comp.GroupIterator_Destroy(GrpIter);
            End;

            PlacedCount := PlacedCount + 1;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(PlacedCount)
        + ',"failed":' + IntToStr(FailedCount)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"total":' + IntToStr(PlacedCount + FailedCount) + '}');
End;

{..............................................................................}
{ PCB_FocusBoard - make a specific board the focused/current one, so the      }
{ GetPCBBoardAnywhere-based tools (get_components, delete, plan_placement,    }
{ render, ...) all operate on it. Essential when several PcbDocs are open.    }
{ Params: board_path (the .PcbDoc to focus).                                  }
{..............................................................................}

Function PCB_FocusBoard(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    BoardPathStr : String;
Begin
    BoardPathStr := ExtractJsonValue(Params, 'board_path');
    Board := ResolvePCBBoard(BoardPathStr);
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB',
            'Could not resolve a PCB board for the given path');
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"focused":true,"board":"' + EscapeJsonString(Board.FileName) + '"}');
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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
    TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Dim := PCBServer.PCBObjectFactory(eDimensionObject, eAngularDimension, eCreate_Default);
        If Dim = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create angular dimension');
            Exit;
        End;
        Dim.Layer := TargetLayer;
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
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
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
    TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Dim := PCBServer.PCBObjectFactory(eDimensionObject, eRadialDimension, eCreate_Default);
        If Dim = Nil Then
        Begin
            PCBServer.PostProcess;
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create radial dimension');
            Exit;
        End;
        Dim.Layer := TargetLayer;
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
    Board := GetPCBBoardAnywhere;
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

    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Rows := StrToIntDef(ExtractJsonValue(Params, 'rows'), 1);
    Cols := StrToIntDef(ExtractJsonValue(Params, 'cols'), 1);
    RowSpace := StrToIntDef(ExtractJsonValue(Params, 'row_spacing_mils'), 0);
    ColSpace := StrToIntDef(ExtractJsonValue(Params, 'col_spacing_mils'), 0);
    LayerStr := ExtractJsonValue(Params, 'layer');
    MirrorStr := LowerCase(ExtractJsonValue(Params, 'mirror'));

    If LayerStr = '' Then TargetLayer := eTopLayer
    Else TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

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
        + '"layer":"' + EscapeJsonString(GetLayerString(TargetLayer)) + '",'
        + '"rows":' + IntToStr(Rows) + ',"cols":' + IntToStr(Cols)
        + ',"x":' + IntToStr(X) + ',"y":' + IntToStr(Y) + '}');
End;

{ PCB_AddTestpointsForNetClass                                                 }
{                                                                              }
{ For each net in a target netclass that does NOT already have a testpoint,   }
{ place a new pad above the board outline with the net assigned and the       }
{ standard IsTestpoint_Top / IsTestpoint_Bottom / IsAssyTestpoint_Top /       }
{ IsAssyTestpoint_Bottom flags set per request. The pad lands in a row above }
{ the board outline ready for the user (or `pcb_move_components`) to drag    }
{ into position.                                                              }
{                                                                              }
{ Net is detected as "already covered" if any pad or via on that net carries }
{ ANY of the four testpoint flags (so DFM tools, fab and assembly testpoint  }
{ vendors all see the existing coverage).                                    }
{                                                                              }
{ Params:                                                                      }
{   net_class            (required)  -- netclass name to scan                  }
{   type                 "smd" or "through_hole" (default "smd")              }
{   pad_size_mils        outer pad size, default 40                            }
{   hole_size_mils       drill diameter, default 20 (only used for through)   }
{   fab_top              "true"/"false", default false                         }
{   fab_bottom           default false                                         }
{   assy_top             default true (most common)                            }
{   assy_bottom          default false                                         }
{   force                default false -- ignore existing, always place        }
{                                                                              }
Function PCB_AddTestpointsForNetClass(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter, NetIter : IPCB_BoardIterator;
    GrIter : IPCB_GroupIterator;
    NetClassObj : IPCB_ObjectClass;
    Net : IPCB_Net;
    Pad : IPCB_Pad;
    Prim : IPCB_Primitive;
    NetClassName, Kind : String;
    PadSizeMils, HoleSizeMils : Integer;
    FabTop, FabBot, AssyTop, AssyBot, Force : Boolean;
    BoardRect : TCoordRect;
    PosX, PosY, PadSize, HoleSize, StepX : Integer;
    ClassFound, CoveredAlready : Boolean;
    Placed, Skipped : Integer;
    ItemsPlaced, ItemsSkipped, NetName : String;
    FirstPlaced, FirstSkipped : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;
    NetClassName := ExtractJsonValue(Params, 'net_class');
    If NetClassName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'net_class is required');
        Exit;
    End;
    Kind := ExtractJsonValue(Params, 'type');
    If Kind = '' Then Kind := 'smd';
    PadSizeMils  := StrToIntDef(ExtractJsonValue(Params, 'pad_size_mils'), 40);
    HoleSizeMils := StrToIntDef(ExtractJsonValue(Params, 'hole_size_mils'), 20);
    FabTop  := LowerCase(ExtractJsonValue(Params, 'fab_top'))  = 'true';
    FabBot  := LowerCase(ExtractJsonValue(Params, 'fab_bottom')) = 'true';
    AssyTop := LowerCase(ExtractJsonValue(Params, 'assy_top'))  = 'true';
    AssyBot := LowerCase(ExtractJsonValue(Params, 'assy_bottom')) = 'true';
    Force   := LowerCase(ExtractJsonValue(Params, 'force')) = 'true';
    If (Not FabTop) And (Not FabBot) And (Not AssyTop) And (Not AssyBot) Then
        AssyTop := True;

    { Locate the netclass.                                                    }
    NetClassObj := Nil;
    ClassFound := False;
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eClassObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        NetClassObj := Iter.FirstPCBObject;
        While NetClassObj <> Nil Do
        Begin
            Try
                If (NetClassObj.MemberKind = eClassMemberKind_Net)
                   And (NetClassObj.Name = NetClassName) Then
                Begin
                    ClassFound := True;
                    Break;
                End;
            Except End;
            NetClassObj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    If Not ClassFound Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NETCLASS_NOT_FOUND',
            'No net class named "' + NetClassName + '"');
        Exit;
    End;

    { Walk all nets, filter to this netclass, and emit a testpoint per       }
    { uncovered net. Layout: row above the board outline, padded.           }
    BoardRect := Board.BoardOutline.BoundingRectangle;
    PadSize := MilsToCoord(PadSizeMils);
    HoleSize := MilsToCoord(HoleSizeMils);
    StepX := PadSize + MilsToCoord(20);
    PosX := BoardRect.Left + (PadSize Div 2);
    PosY := BoardRect.Top + MilsToCoord(60) + (PadSize Div 2);

    Placed := 0;
    Skipped := 0;
    ItemsPlaced := '';
    ItemsSkipped := '';
    FirstPlaced := True;
    FirstSkipped := True;

    PCBServer.PreProcess;
    Try
        NetIter := Board.BoardIterator_Create;
        Try
            NetIter.AddFilter_ObjectSet(MkSet(eNetObject));
            NetIter.AddFilter_LayerSet(AllLayers);
            NetIter.AddFilter_Method(eProcessAll);
            Net := NetIter.FirstPCBObject;
            While Net <> Nil Do
            Begin
                Try
                    If NetClassObj.IsMember(Net) Then
                    Begin
                        NetName := '';
                        Try NetName := Net.Name; Except End;

                        CoveredAlready := False;
                        If Not Force Then
                        Begin
                            GrIter := Net.GroupIterator_Create;
                            Try
                                GrIter.AddFilter_ObjectSet(MkSet(ePadObject, eViaObject));
                                GrIter.AddFilter_AllLayers;
                                Prim := GrIter.FirstPCBObject;
                                While Prim <> Nil Do
                                Begin
                                    Try
                                        If Prim.IsTestpoint_Top
                                           Or Prim.IsTestpoint_Bottom
                                           Or Prim.IsAssyTestpoint_Top
                                           Or Prim.IsAssyTestpoint_Bottom Then
                                            CoveredAlready := True;
                                    Except End;
                                    If CoveredAlready Then Break;
                                    Prim := GrIter.NextPCBObject;
                                End;
                            Finally
                                Net.GroupIterator_Destroy(GrIter);
                            End;
                        End;

                        If CoveredAlready Then
                        Begin
                            Inc(Skipped);
                            If Not FirstSkipped Then ItemsSkipped := ItemsSkipped + ',';
                            FirstSkipped := False;
                            ItemsSkipped := ItemsSkipped + '"' +
                                EscapeJsonString(NetName) + '"';
                        End
                        Else
                        Begin
                            Pad := PCBServer.PCBObjectFactory(ePadObject,
                                eNoDimension, eCreate_Default);
                            If Pad <> Nil Then
                            Begin
                                Pad.Mode := ePadMode_Simple;
                                Pad.X := PosX;
                                Pad.Y := PosY;
                                Pad.TopXSize := PadSize;
                                Pad.TopYSize := PadSize;
                                Pad.TopShape := eRounded;
                                If LowerCase(Kind) = 'through_hole' Then
                                Begin
                                    Pad.Layer := eMultiLayer;
                                    Pad.HoleSize := HoleSize;
                                End
                                Else
                                Begin
                                    Pad.Layer := eTopLayer;
                                    Pad.HoleSize := 0;
                                End;
                                Pad.Name := 'TP_' + NetName;
                                Pad.Net := Net;
                                Try Pad.IsTestpoint_Top := FabTop; Except End;
                                Try Pad.IsTestpoint_Bottom := FabBot; Except End;
                                Try Pad.IsAssyTestpoint_Top := AssyTop; Except End;
                                Try Pad.IsAssyTestpoint_Bottom := AssyBot; Except End;
                                Board.AddPCBObject(Pad);

                                Inc(Placed);
                                If Not FirstPlaced Then ItemsPlaced := ItemsPlaced + ',';
                                FirstPlaced := False;
                                ItemsPlaced := ItemsPlaced + '"' +
                                    EscapeJsonString(NetName) + '"';
                                PosX := PosX + StepX;
                            End;
                        End;
                    End;
                Except End;
                Net := NetIter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(NetIter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    If Placed > 0 Then SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('net_class', NetClassName) + ',' +
            JsonStr('type', Kind) + ',' +
            JsonInt('placed', Placed) + ',' +
            JsonInt('skipped_already_covered', Skipped) + ',' +
            JsonRaw('placed_nets', '[' + ItemsPlaced + ']') + ',' +
            JsonRaw('skipped_nets', '[' + ItemsSkipped + ']')
        ));
End;


{ PCB_MakePasteGrid                                                            }
{                                                                              }
{ Split a single pad's solder-paste opening into a grid of smaller fills.     }
{ The classic use case is the central thermal pad on a QFN / DFN / QFP --     }
{ a full-area paste opening makes the IC "swim" sideways during reflow as    }
{ the molten solder pool reduces friction. Splitting the opening into        }
{ smaller squares totalling ~50-75% coverage gives the IC something to       }
{ bond to while letting flux gases escape.                                   }
{                                                                              }
{ Algorithm:                                                                  }
{   - Locate the target pad by designator + pad name (e.g. "U5" pad "9").    }
{   - Suppress the existing full-area paste by setting PasteMaskExpansion    }
{     negative (eCacheManual override on the pad cache).                     }
{   - Compute grid layout: how many grid_size x grid_size squares fit with   }
{     at least min_gap between them.                                          }
{   - If coverage < min_coverage_pct, bump grid_size and retry.              }
{   - Place each square as a Fill on the appropriate paste layer (top or    }
{     bottom, derived from the pad's own layer).                              }
{                                                                              }
{ All input dimensions are mils; coverage is a 0-100 percent.                  }
Function PCB_MakePasteGrid(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
    PadIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Cache : TPadCache;
    Designator, PadName, CompDes, PadId : String;
    MinGridSizeMils, MinGapMils : Integer;
    MinCoverPct : Double;
    GridSize, MinGap : Integer;
    GridXCnt, GridYCnt : Integer;
    GridXPad, GridYPad : Integer;
    PadW, PadH, PadCenterX, PadCenterY : Integer;
    PadAreaMils2, PasteAreaMils2 : Int64;
    PctCover : Double;
    Found : Boolean;
    PasteLayer : TLayer;
    Fill : IPCB_Fill;
    I, J, Placed : Integer;
    PadRot : Double;
    FillX1, FillY1, FillX2, FillY2 : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;
    Designator := ExtractJsonValue(Params, 'designator');
    PadName    := ExtractJsonValue(Params, 'pad_name');
    MinGridSizeMils := StrToIntDef(ExtractJsonValue(Params, 'min_grid_size_mils'), 15);
    MinGapMils      := StrToIntDef(ExtractJsonValue(Params, 'min_gap_mils'), 5);
    MinCoverPct     := StrToFloatDef(ExtractJsonValue(Params, 'min_coverage_pct'), 60.0);
    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'designator is required');
        Exit;
    End;
    If PadName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'pad_name is required (use e.g. "0" for QFN exposed pad)');
        Exit;
    End;

    Pad := Nil;
    Found := False;
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Comp := Iter.FirstPCBObject;
        While (Comp <> Nil) And (Not Found) Do
        Begin
            CompDes := '';
            Try CompDes := Comp.Name.Text; Except End;
            If CompDes = Designator Then
            Begin
                PadIter := Comp.GroupIterator_Create;
                Try
                    PadIter.AddFilter_ObjectSet(MkSet(ePadObject));
                    Pad := PadIter.FirstPCBObject;
                    While (Pad <> Nil) And (Not Found) Do
                    Begin
                        PadId := '';
                        Try PadId := Pad.Name; Except End;
                        If PadId = PadName Then Found := True
                        Else Pad := PadIter.NextPCBObject;
                    End;
                Finally
                    Comp.GroupIterator_Destroy(PadIter);
                End;
            End;
            If Not Found Then Comp := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    If (Not Found) Or (Pad = Nil) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'PAD_NOT_FOUND',
            'No pad "' + PadName + '" on component "' + Designator + '"');
        Exit;
    End;

    { Pick top/bottom paste based on pad layer.                              }
    If Pad.Layer = eBottomLayer Then PasteLayer := eBottomPaste
    Else PasteLayer := eTopPaste;

    { Pad rotation interpretation: when the component is rotated 90/270 the }
    { pad's X dimension actually maps to physical height -- swap.            }
    PadRot := 0;
    Try PadRot := Pad.Rotation; Except End;
    If (Abs(PadRot - 90) < 1) Or (Abs(PadRot - 270) < 1) Then
    Begin
        PadW := Pad.TopYSize;
        PadH := Pad.TopXSize;
    End
    Else
    Begin
        PadW := Pad.TopXSize;
        PadH := Pad.TopYSize;
    End;

    PadCenterX := Pad.X;
    PadCenterY := Pad.Y;
    PadAreaMils2 := CoordToMils(PadW) * CoordToMils(PadH);
    GridSize := MinGridSizeMils;
    MinGap := MinGapMils;

    PctCover := 0;
    GridXPad := 0;
    GridYPad := 0;
    GridXCnt := 0;
    GridYCnt := 0;

    { Outer loop -- bump grid size until coverage % satisfied.               }
    While PctCover < MinCoverPct Do
    Begin
        GridXCnt := Trunc(CoordToMils(PadW) / GridSize);
        GridYCnt := Trunc(CoordToMils(PadH) / GridSize);

        { Inner loop -- shrink grid count until gap is ≥ MinGap.             }
        GridXPad := 0; GridYPad := 0;
        While (GridXPad < MinGap) Or (GridYPad < MinGap) Do
        Begin
            If GridXCnt <= 0 Then Break;
            If GridYCnt <= 0 Then Break;
            GridXPad := (CoordToMils(PadW) - (GridXCnt * GridSize)) Div (GridXCnt + 1);
            GridYPad := (CoordToMils(PadH) - (GridYCnt * GridSize)) Div (GridYCnt + 1);
            If GridXPad < MinGap Then Dec(GridXCnt);
            If GridYPad < MinGap Then Dec(GridYCnt);
        End;

        If (GridXCnt <= 0) Or (GridYCnt <= 0) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INFEASIBLE',
                'Pad too small to fit grid at min_grid_size=' + IntToStr(MinGridSizeMils) +
                ' / min_gap=' + IntToStr(MinGapMils));
            Exit;
        End;

        PasteAreaMils2 := GridXCnt * GridYCnt * GridSize * GridSize;
        PctCover := (PasteAreaMils2 * 100.0) / PadAreaMils2;
        If PctCover < MinCoverPct Then GridSize := GridSize + 5;
        { Safety -- stop if a single grid square fills the whole pad.        }
        If GridSize >= CoordToMils(PadW) Then Break;
        If GridSize >= CoordToMils(PadH) Then Break;
    End;

    PCBServer.PreProcess;
    Try
        { Suppress the existing full-area paste opening on the pad.          }
        Cache := Pad.GetState_Cache;
        Try
            Cache.PasteMaskExpansionValid := eCacheManual;
            If PadW > PadH Then Cache.PasteMaskExpansion := -PadW
            Else Cache.PasteMaskExpansion := -PadH;
            Pad.SetState_Cache := Cache;
        Except End;

        Placed := 0;
        For I := 0 To GridXCnt - 1 Do
        Begin
            For J := 0 To GridYCnt - 1 Do
            Begin
                FillX1 := PadCenterX - (PadW Div 2)
                          + MilsToCoord(GridXPad * (I + 1) + I * GridSize);
                FillX2 := FillX1 + MilsToCoord(GridSize);
                FillY1 := PadCenterY - (PadH Div 2)
                          + MilsToCoord(GridYPad * (J + 1) + J * GridSize);
                FillY2 := FillY1 + MilsToCoord(GridSize);

                Fill := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
                Fill.X1Location := FillX1;
                Fill.Y1Location := FillY1;
                Fill.X2Location := FillX2;
                Fill.Y2Location := FillY2;
                Fill.Layer := PasteLayer;
                Fill.Rotation := 0;
                Board.AddPCBObject(Fill);
                Inc(Placed);
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('designator', Designator) + ',' +
            JsonStr('pad_name', PadName) + ',' +
            JsonInt('grid_x', GridXCnt) + ',' +
            JsonInt('grid_y', GridYCnt) + ',' +
            JsonInt('grid_size_mils', GridSize) + ',' +
            JsonInt('gap_x_mils', GridXPad) + ',' +
            JsonInt('gap_y_mils', GridYPad) + ',' +
            JsonInt('fills_placed', Placed) + ',' +
            JsonFloat('coverage_pct', PctCover)
        ));
End;

{..............................................................................}
{ PCB_ApplyDnpPasteExclusion - suppress stencil paste on Not-Fitted parts.    }
{ Params: designators (pipe-separated), restore (true/false)                  }
{                                                                             }
{ A Not-Fitted component is on the BOM as a placeholder and must NOT receive  }
{ paste: the SMT line would otherwise deposit paste on empty pads, and the    }
{ bridging shows up as rework. This is the remediation half of                }
{ audit.variant_not_fitted, which is the identify half. The designator list   }
{ is passed IN rather than re-detected here, so the mutation is reviewable    }
{ and a caller can override the selection; detection stays in one place.      }
{                                                                             }
{ Mechanism: per-pad PasteMaskExpansion set manual and negative, which is     }
{ what PCB_MakePasteGrid already does to clear a pad before laying its grid.  }
{ An expansion of minus the larger pad dimension collapses the aperture       }
{ whatever the shape.                                                         }
{                                                                             }
{ restore=true puts PasteMaskExpansionValid back to eCacheInvalid, which      }
{ discards the manual override and makes Altium recompute from the design     }
{ rules. TCacheState is (eCacheInvalid, eCacheValid, eCacheManual); there is  }
{ no "use the rule" member, and eCacheValid would assert that a rule-derived  }
{ value already sits in the field, which after an override it does not.       }
{                                                                             }
{ Only surface pads are touched. A multi-layer (through-hole) pad gets no     }
{ stencil aperture anyway, so overriding it would be a no-op recorded as a    }
{ change; those are counted and reported separately instead.                  }
{..............................................................................}

Function PCB_ApplyDnpPasteExclusion(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    GrpIter : IPCB_GroupIterator;
    Comp : IPCB_Component;
    Pad : IPCB_Pad;
    Cache : TPadCache;
    DesigList, RestoreStr, CompDesig, Matched, ItemsJson, EntryJson : String;
    Restore, First : Boolean;
    PadsChanged, PadsSkippedTht, CompsMatched, CompsRequested : Integer;
    PadW, PadH, Expansion, CompPads : Integer;
Begin
    DesigList := ExtractJsonValue(Params, 'designators');
    If DesigList = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'designators is required (pipe-separated); run '
            + 'audit.variant_not_fitted first to get the Not-Fitted list');
        Exit;
    End;
    RestoreStr := LowerCase(ExtractJsonValue(Params, 'restore'));
    Restore := (RestoreStr = 'true') Or (RestoreStr = '1');

    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB',
            'No PCB document is active');
        Exit;
    End;

    { Count what was asked for, so the caller can see whether every named }
    { component was actually found on this board.                          }
    CompsRequested := 1;
    Matched := DesigList;
    While Pos('|', Matched) > 0 Do
    Begin
        CompsRequested := CompsRequested + 1;
        Matched := Copy(Matched, Pos('|', Matched) + 1, Length(Matched));
    End;

    PadsChanged := 0;
    PadsSkippedTht := 0;
    CompsMatched := 0;
    ItemsJson := '';
    First := True;

    PCBServer.PreProcess;
    Try
        Iterator := Board.BoardIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Comp := Iterator.FirstPCBObject;
            While Comp <> Nil Do
            Begin
                CompDesig := '';
                Try CompDesig := Comp.Name.Text; Except End;
                { Pipe-delimited membership, anchored so R1 does not match }
                { R10. }
                If (CompDesig <> '')
                    And (Pos('|' + CompDesig + '|', '|' + DesigList + '|') > 0) Then
                Begin
                    Inc(CompsMatched);
                    CompPads := 0;
                    GrpIter := Comp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                        Pad := GrpIter.FirstPCBObject;
                        While Pad <> Nil Do
                        Begin
                            { Surface pads only; a through-hole pad has no  }
                            { stencil aperture to suppress.                  }
                            If (Pad.Layer = eTopLayer) Or (Pad.Layer = eBottomLayer) Then
                            Begin
                                Try
                                    Cache := Pad.GetState_Cache;
                                    If Restore Then
                                        Cache.PasteMaskExpansionValid := eCacheInvalid
                                    Else
                                    Begin
                                        PadW := Pad.TopXSize;
                                        PadH := Pad.TopYSize;
                                        If PadW > PadH Then Expansion := -PadW
                                        Else Expansion := -PadH;
                                        Cache.PasteMaskExpansionValid := eCacheManual;
                                        Cache.PasteMaskExpansion := Expansion;
                                    End;
                                    Pad.SetState_Cache := Cache;
                                    Inc(PadsChanged);
                                    CompPads := CompPads + 1;
                                Except End;
                            End
                            Else
                                Inc(PadsSkippedTht);
                            Pad := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Comp.GroupIterator_Destroy(GrpIter);
                    End;
                    If Not First Then ItemsJson := ItemsJson + ',';
                    First := False;
                    EntryJson := JsonStr('designator', CompDesig) + ','
                        + JsonInt('pads_changed', CompPads);
                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                End;
                Comp := Iterator.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iterator);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Try Board.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonBool('restored', Restore) + ','
        + JsonInt('components_requested', CompsRequested) + ','
        + JsonInt('components_matched', CompsMatched) + ','
        + JsonInt('pads_changed', PadsChanged) + ','
        + JsonInt('pads_skipped_through_hole', PadsSkippedTht) + ','
        + JsonRaw('items', JsonArr(ItemsJson))));
End;



{ PCB_GetDifferentialPairs                                                     }
{                                                                              }
{ Enumerate every IPCB_DifferentialPair on the active PCB and report per-pair }
{ length statistics. Length mismatch between the two halves of a diff pair is }
{ one of the most common high-speed routing bugs -- transceivers spec a max  }
{ skew (USB: < 150 mils, HDMI: < 200 mils, MIPI: < 5 mils for high-rate D-PHY,}
{ PCIe: < 5 mils within a lane). Catching it pre-fab saves a respin.         }
{                                                                              }
{ Per-pair JSON: name, positive_net, negative_net, pos_length_mils,           }
{ neg_length_mils, skew_mils (absolute difference), both_routed (boolean --   }
{ false means one half is still ratsnest-only).                              }
{                                                                              }
{ Uses the PCB API IPCB_DifferentialPair interface.                           }
Function PCB_GetDifferentialPairs(Params : String; RequestId : String) : String;

    Function NetLengthMils(Net : IPCB_Net) : Double;
    Var
        GrIter : IPCB_GroupIterator;
        Prim : IPCB_Primitive;
        Track : IPCB_Track;
        Arc : IPCB_Arc;
        Total : Double;
        Dx, Dy : Double;
        SweepDeg : Double;
    Begin
        Total := 0;
        If Net = Nil Then
        Begin
            Result := 0;
            Exit;
        End;
        GrIter := Net.GroupIterator_Create;
        Try
            GrIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject));
            GrIter.AddFilter_IPCB_LayerSet(LayerSet.SignalLayers);
            Prim := GrIter.FirstPCBObject;
            While Prim <> Nil Do
            Begin
                Try
                    If Prim.ObjectId = eTrackObject Then
                    Begin
                        Track := Prim;
                        Dx := CoordToMils(Track.X2 - Track.X1);
                        Dy := CoordToMils(Track.Y2 - Track.Y1);
                        Total := Total + Sqrt(Dx * Dx + Dy * Dy);
                    End
                    Else If Prim.ObjectId = eArcObject Then
                    Begin
                        Arc := Prim;
                        SweepDeg := Abs(Arc.EndAngle - Arc.StartAngle);
                        If SweepDeg > 360 Then SweepDeg := SweepDeg - 360;
                        Total := Total + (SweepDeg / 360.0) * 2.0 * 3.14159265358979 *
                                          CoordToMils(Arc.Radius);
                    End;
                Except End;
                Prim := GrIter.NextPCBObject;
            End;
        Finally
            Net.GroupIterator_Destroy(GrIter);
        End;
        Result := Total;
    End;

Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Pair : IPCB_DifferentialPair;
    PosLen, NegLen, Skew : Double;
    PosName, NegName, PairName : String;
    Items, Entry : String;
    First, BothRouted : Boolean;
    Count : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;

    Items := '';
    First := True;
    Count := 0;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eDifferentialPairObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Pair := Iter.FirstPCBObject;
        While Pair <> Nil Do
        Begin
            Try
                Inc(Count);
                PairName := '';
                Try PairName := Pair.Name; Except End;
                PosName := '';
                NegName := '';
                Try
                    If Pair.PositiveNet <> Nil Then PosName := Pair.PositiveNet.Name;
                Except End;
                Try
                    If Pair.NegativeNet <> Nil Then NegName := Pair.NegativeNet.Name;
                Except End;
                PosLen := NetLengthMils(Pair.PositiveNet);
                NegLen := NetLengthMils(Pair.NegativeNet);
                Skew := Abs(PosLen - NegLen);
                BothRouted := (PosLen > 0) And (NegLen > 0);

                If Not First Then Items := Items + ',';
                First := False;
                Entry :=
                    JsonStr('name', PairName) + ',' +
                    JsonStr('positive_net', PosName) + ',' +
                    JsonStr('negative_net', NegName) + ',' +
                    JsonFloat('pos_length_mils', PosLen) + ',' +
                    JsonFloat('neg_length_mils', NegLen) + ',' +
                    JsonFloat('skew_mils', Skew) + ',' +
                    JsonBool('both_routed', BothRouted);
                Items := Items + JsonObj(Entry);
            Except End;
            Pair := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('count', Count) + ',' +
            JsonRaw('pairs', '[' + Items + ']')
        ));
End;


{ PCB_ClearSourceFootprintLibrary                                              }
{                                                                              }
{ Walk components on the board and clear their SourceFootprintLibrary         }
{ property. When a project was created from an Integrated Library, each      }
{ placed component remembers WHICH library it came from. If the user later   }
{ consolidates / renames / moves that library, ECO and Update-From-Lib       }
{ start failing with "library not found" because the component still         }
{ points at the old path. Clearing SourceFootprintLibrary unpins it so       }
{ Altium re-matches by library-reference name from whatever's currently in   }
{ Available Libraries.                                                        }
{                                                                              }
{ Optional designator_filter (pipe-delimited list) restricts the operation;  }
{ omit / empty to clear all components.                                       }
{                                                                              }
{ Clears the source footprint library; supports an optional name filter.     }
Function PCB_ClearSourceFootprintLibrary(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
    Filter, CompDes, OldSrc : String;
    Total, Cleared : Integer;
    UseFilter, Match : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;
    Filter := ExtractJsonValue(Params, 'designator_filter');
    UseFilter := Filter <> '';

    Total := 0;
    Cleared := 0;

    PCBServer.PreProcess;
    Try
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
            Iter.AddFilter_Method(eProcessAll);
            Comp := Iter.FirstPCBObject;
            While Comp <> Nil Do
            Begin
                Try
                    Inc(Total);
                    Match := True;
                    If UseFilter Then
                    Begin
                        CompDes := '';
                        Try CompDes := Comp.Name.Text; Except End;
                        { Pipe-delimited match: "|U1|U5|" semantics; pad      }
                        { both sides so partial-name collisions don't fire.   }
                        Match := Pos('|' + CompDes + '|',
                                      '|' + Filter + '|') > 0;
                    End;
                    If Match Then
                    Begin
                        OldSrc := '';
                        Try OldSrc := Comp.SourceFootprintLibrary; Except End;
                        If OldSrc <> '' Then
                        Begin
                            Try Comp.SourceFootprintLibrary := ''; Except End;
                            Inc(Cleared);
                        End;
                    End;
                Except End;
                Comp := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    If Cleared > 0 Then SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('total', Total) + ',' +
            JsonInt('cleared', Cleared)
        ));
End;


{ PCB_GetFabStats                                                              }
{                                                                              }
{ DFM (Design For Manufacturing) summary -- the numbers fab houses ask for    }
{ on their quote forms. The agent can fetch this once before sending          }
{ gerbers out and surface red flags (sub-4mil annular ring, sub-5mil tracks,  }
{ excessive distinct drill sizes) to the user.                                }
{                                                                              }
{ Computed metrics:                                                            }
{   - board_width_mm, board_height_mm, board_area_mm2                         }
{   - num_copper_layers                                                       }
{   - vias_total, vias_through, vias_blind, vias_buried                       }
{   - pads_plated, pads_unplated, pads_slotted                                }
{   - min_annular_ring_mils (across all vias + pads with plated holes)        }
{   - min_track_width_mils (across all tracks on copper layers)               }
{   - smallest_hole_mils, largest_hole_mils, distinct_hole_count              }
{                                                                              }
{ Board statistics condensed into a single IPC call.                         }
Function PCB_GetFabStats(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack;
    LayerObj : IPCB_LayerObject;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Via : IPCB_Via;
    Pad : IPCB_Pad;
    Track : IPCB_Track;
    NumCopper : Integer;
    ViasTotal, ViasThrough, ViasBlind, ViasBuried : Integer;
    PadsPlated, PadsUnplated, PadsSlotted : Integer;
    MinAnnularRing, MinTrackWidth : Integer;
    SmallestHole, LargestHole : Integer;
    AnnRing, Width : Integer;
    DistinctHolesList, HoleKey : String;
    DistinctHoleCount : Integer;
    HoleSize : Integer;
    BoardW, BoardH, BoardA : Double;
    R : TCoordRect;
    HasAny : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;

    { Bounding box -- use the board outline rect.                            }
    Try
        R := Board.BoardOutline.BoundingRectangle;
        BoardW := CoordToMM(R.Right - R.Left);
        BoardH := CoordToMM(R.Top - R.Bottom);
        BoardA := BoardW * BoardH;
    Except
        BoardW := 0;
        BoardH := 0;
        BoardA := 0;
    End;

    { Layer count -- walk the IPCB_LayerStack and count signal layers.       }
    NumCopper := 0;
    Try
        LayerStack := Board.LayerStack_V7;
        LayerObj := LayerStack.FirstLayer;
        While LayerObj <> Nil Do
        Begin
            Try
                If LayerObj.LayerID >= 1 Then
                    If (LayerObj.LayerID = eTopLayer)
                       Or (LayerObj.LayerID = eBottomLayer)
                       Or ((LayerObj.LayerID >= eMidLayer1)
                            And (LayerObj.LayerID <= eMidLayer30)) Then
                        Inc(NumCopper);
            Except End;
            LayerObj := LayerStack.NextLayer(LayerObj);
        End;
    Except End;

    ViasTotal := 0; ViasThrough := 0; ViasBlind := 0; ViasBuried := 0;
    PadsPlated := 0; PadsUnplated := 0; PadsSlotted := 0;
    MinAnnularRing := MAX_INT;
    MinTrackWidth := MAX_INT;
    SmallestHole := MAX_INT;
    LargestHole := 0;
    DistinctHolesList := '|';
    DistinctHoleCount := 0;
    HasAny := False;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eViaObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                Via := Obj;
                Inc(ViasTotal);
                { Classify by start/stop layer -- through if Top to Bottom; }
                { blind if one side is outer (Top or Bottom) but not both;  }
                { buried if both endpoints are inner layers.                 }
                If (Via.StartLayer = eTopLayer) And (Via.StopLayer = eBottomLayer) Then
                    Inc(ViasThrough)
                Else If (Via.StartLayer = eTopLayer) Or (Via.StartLayer = eBottomLayer)
                     Or (Via.StopLayer = eTopLayer) Or (Via.StopLayer = eBottomLayer) Then
                    Inc(ViasBlind)
                Else
                    Inc(ViasBuried);

                HoleSize := Via.HoleSize;
                If HoleSize > 0 Then
                Begin
                    HasAny := True;
                    AnnRing := (Via.Size - HoleSize) Div 2;
                    If AnnRing < MinAnnularRing Then MinAnnularRing := AnnRing;
                    If HoleSize < SmallestHole Then SmallestHole := HoleSize;
                    If HoleSize > LargestHole Then LargestHole := HoleSize;
                    HoleKey := '|' + IntToStr(HoleSize) + '|';
                    If Pos(HoleKey, DistinctHolesList) = 0 Then
                    Begin
                        DistinctHolesList := DistinctHolesList + IntToStr(HoleSize) + '|';
                        Inc(DistinctHoleCount);
                    End;
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    { Pads -- track plated vs unplated, slotted vs circular.                 }
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                Pad := Obj;
                HoleSize := Pad.HoleSize;
                If HoleSize > 0 Then
                Begin
                    HasAny := True;
                    If Pad.Plated Then
                    Begin
                        Inc(PadsPlated);
                        { Pad annular ring is (X|Y - HoleSize) / 2 -- pick }
                        { the tighter of X/Y dimensions.                    }
                        AnnRing := (Pad.TopXSize - HoleSize) Div 2;
                        If ((Pad.TopYSize - HoleSize) Div 2) < AnnRing Then
                            AnnRing := (Pad.TopYSize - HoleSize) Div 2;
                        If AnnRing < MinAnnularRing Then MinAnnularRing := AnnRing;
                    End
                    Else
                        Inc(PadsUnplated);
                    { Slotted pads have HoleType <> eRoundHole.              }
                    Try
                        If Pad.HoleType <> eRoundHole Then Inc(PadsSlotted);
                    Except End;
                    If HoleSize < SmallestHole Then SmallestHole := HoleSize;
                    If HoleSize > LargestHole Then LargestHole := HoleSize;
                    HoleKey := '|' + IntToStr(HoleSize) + '|';
                    If Pos(HoleKey, DistinctHolesList) = 0 Then
                    Begin
                        DistinctHolesList := DistinctHolesList + IntToStr(HoleSize) + '|';
                        Inc(DistinctHoleCount);
                    End;
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    { Min track width -- copper layers only.                                  }
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
        Iter.AddFilter_LayerSet(SignalLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                Track := Obj;
                Width := Track.Width;
                If (Width > 0) And (Width < MinTrackWidth) Then
                    MinTrackWidth := Width;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    If Not HasAny Then
    Begin
        MinAnnularRing := 0;
        SmallestHole := 0;
    End;
    If MinTrackWidth = MAX_INT Then MinTrackWidth := 0;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonFloat('board_width_mm', BoardW) + ',' +
            JsonFloat('board_height_mm', BoardH) + ',' +
            JsonFloat('board_area_mm2', BoardA) + ',' +
            JsonInt('num_copper_layers', NumCopper) + ',' +
            JsonInt('vias_total', ViasTotal) + ',' +
            JsonInt('vias_through', ViasThrough) + ',' +
            JsonInt('vias_blind', ViasBlind) + ',' +
            JsonInt('vias_buried', ViasBuried) + ',' +
            JsonInt('pads_plated', PadsPlated) + ',' +
            JsonInt('pads_unplated', PadsUnplated) + ',' +
            JsonInt('pads_slotted', PadsSlotted) + ',' +
            JsonInt('min_annular_ring_mils', CoordToMils(MinAnnularRing)) + ',' +
            JsonInt('min_track_width_mils', CoordToMils(MinTrackWidth)) + ',' +
            JsonInt('smallest_hole_mils', CoordToMils(SmallestHole)) + ',' +
            JsonInt('largest_hole_mils', CoordToMils(LargestHole)) + ',' +
            JsonInt('distinct_hole_count', DistinctHoleCount)
        ));
End;


{..............................................................................}
{ PCB_FilletCorners                                                            }
{                                                                              }
{ Round acute track-to-track joins by replacing the shared corner with a      }
{ tangent arc and shortening each track back to its tangent point.           }
{ Interactive fillet tools make the user click corners on the canvas.        }
{ This version is                                                             }
{ agent-shaped: it walks the board, finds every same-net corner whose        }
{ interior angle is below the threshold, and either reports them (dry_run)   }
{ or applies the fillet in one undo group.                                    }
{                                                                              }
{ Params (all JSON strings):                                                   }
{   net           -- optional, restrict to corners on this net                }
{   radius_mils   -- arc radius, default 10                                   }
{   min_angle_deg -- only fillet corners with interior angle < this; default  }
{                    90 (sharp / right-angle corners and worse)               }
{   dry_run       -- "true" (default) returns the list of WOULD-fillet        }
{                    corners without mutating; "false" applies the change    }
{                                                                              }
{ Geometry: for a corner where two tracks share endpoint P and head off in   }
{ directions u and v (unit vectors away from P), with interior angle theta   }
{ between them, the fillet arc has:                                           }
{   tangent_dist (along each track from P) = R / tan(theta / 2)               }
{   center_dist  (along the bisector from P) = R / sin(theta / 2)             }
{   tangent points: T1 = P + tangent_dist * u, T2 = P + tangent_dist * v      }
{   arc center C = P + center_dist * bisector_unit                            }
{                                                                              }
{ Each tangent point sits R away from C and the radius vector C->T is        }
{ perpendicular to the corresponding track direction.                        }
{                                                                              }
{ NOTE: This handler has NOT been validated against a live Altium session.   }
{ Defensive Try/Except wraps every Altium API touch. Recommend running in    }
{ dry_run mode first, eyeballing the items[], and only flipping dry_run off  }
{ on a board you have backed up.                                              }
{..............................................................................}

Function PCB_FilletCorners(Params : String; RequestId : String) : String;
{ DelphiScript does NOT support typed constants (Const cPi : Double = X). }
{ Use an untyped Const -- the literal carries its own Double precision   }
{ when assigned to a Double receiver, which is how cPi is used below.    }
Const
    cPi = 3.14159265358979;
Var
    Board : IPCB_Board;
    Iter, SpatIter : IPCB_BoardIterator;
    Track, Other : IPCB_Track;
    Obj : IPCB_Primitive;
    Arc : IPCB_Arc;
    NetFilter, DryStr, RadStr, AngStr : String;
    DryRun : Boolean;
    RadiusMils : Integer;
    MinAngleDeg : Double;
    Tol, Endpoint : Integer;
    Filleted, Skipped, MaxItems : Integer;
    PX, PY : Integer;
    OX, OY : Integer;
    V1X, V1Y, V2X, V2Y : Double;
    L1, L2, Dot, CosTheta, ThetaRad, ThetaDeg : Double;
    U1X, U1Y, U2X, U2Y : Double;
    BX, BY, BLen : Double;
    TangentDist, CenterDist : Double;
    T1X, T1Y, T2X, T2Y : Double;
    CX, CY : Double;
    R : Double;
    StartAngleDeg, EndAngleDeg : Double;
    A1, A2 : Double;
    ItemsJson, EntryJson, NetName, LayerName : String;
    First : Boolean;
    TrackAddr, OtherAddr : String;
    OtherEndpoint : Integer;
    SaveNeeded : Boolean;
    ApplyOk : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB',
            'No PCB document is active');
        Exit;
    End;

    NetFilter := ExtractJsonValue(Params, 'net');
    DryStr := LowerCase(ExtractJsonValue(Params, 'dry_run'));
    RadStr := ExtractJsonValue(Params, 'radius_mils');
    AngStr := ExtractJsonValue(Params, 'min_angle_deg');

    { Default dry_run = TRUE so an agent cannot rewrite the board layout by  }
    { accident. The caller has to pass dry_run="false" to actually mutate.   }
    If DryStr = '' Then DryRun := True
    Else DryRun := (DryStr = 'true') Or (DryStr = '1');

    RadiusMils := StrToIntDef(RadStr, 10);
    If RadiusMils <= 0 Then RadiusMils := 10;
    MinAngleDeg := StrToFloatDef(AngStr, 90.0);
    If MinAngleDeg <= 0 Then MinAngleDeg := 90.0;
    If MinAngleDeg >= 180 Then MinAngleDeg := 179.9;

    R := MilsToCoord(RadiusMils);
    Tol := MilsToCoord(1);
    Filleted := 0;
    Skipped := 0;
    MaxItems := 200;
    ItemsJson := '';
    First := True;
    SaveNeeded := False;

    If Not DryRun Then PCBServer.PreProcess;
    Try
        Iter := Nil;
        Try
            Iter := Board.BoardIterator_Create;
        Except End;
        If Iter = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'ITER_FAILED',
                'Could not create board iterator');
            Exit;
        End;

        Try
            Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
            Iter.AddFilter_IPCB_LayerSet(LayerSet.SignalLayers);
            Iter.AddFilter_Method(eProcessAll);

            Track := Iter.FirstPCBObject;
            While (Track <> Nil) And (Filleted + Skipped < MaxItems) Do
            Begin
                { Optional net filter. Net handling is wrapped because    }
                { Track.Net may be Nil on free tracks.                      }
                If NetFilter <> '' Then
                Begin
                    Try
                        If (Track.Net = Nil) Or (Track.Net.Name <> NetFilter) Then
                        Begin
                            Track := Iter.NextPCBObject;
                            Continue;
                        End;
                    Except
                        Track := Iter.NextPCBObject;
                        Continue;
                    End;
                End;

                TrackAddr := '';
                Try TrackAddr := Track.I_ObjectAddress; Except End;

                { Examine both endpoints. We dedupe pairs by only            }
                { processing the corner when this track's address sorts      }
                { lexicographically before the neighbour's. Both halves of  }
                { the pair share the same join geometry, so visiting only   }
                { one half is sufficient.                                    }
                For Endpoint := 1 To 2 Do
                Begin
                    Try
                        If Endpoint = 1 Then
                        Begin
                            PX := Track.X1; PY := Track.Y1;
                            V1X := Track.X2 - PX; V1Y := Track.Y2 - PY;
                        End
                        Else
                        Begin
                            PX := Track.X2; PY := Track.Y2;
                            V1X := Track.X1 - PX; V1Y := Track.Y1 - PY;
                        End;
                        L1 := Sqrt(V1X * V1X + V1Y * V1Y);
                        If L1 < 1 Then Continue;

                        SpatIter := Nil;
                        Try SpatIter := Board.SpatialIterator_Create; Except End;
                        If SpatIter = Nil Then Continue;

                        Try
                            SpatIter.AddFilter_ObjectSet(MkSet(eTrackObject));
                            SpatIter.AddFilter_IPCB_LayerSet(MkSet(Track.Layer));
                            SpatIter.AddFilter_Area(
                                PX - Tol, PY - Tol, PX + Tol, PY + Tol);

                            Obj := SpatIter.FirstPCBObject;
                            While (Obj <> Nil)
                                  And (Filleted + Skipped < MaxItems) Do
                            Begin
                                Try
                                    Other := Obj;
                                    OtherAddr := '';
                                    Try OtherAddr := Other.I_ObjectAddress; Except End;

                                    { Skip self; dedupe pairs by sorting on   }
                                    { I_ObjectAddress so each corner is only }
                                    { visited once.                            }
                                    If (OtherAddr = '') Or (OtherAddr = TrackAddr)
                                       Or (OtherAddr <= TrackAddr) Then
                                    Begin
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;

                                    { Same-net check. Tracks with no net are }
                                    { skipped because we can't safely match. }
                                    Try
                                        If (Track.Net = Nil) Or (Other.Net = Nil)
                                           Or (Track.Net.Name <> Other.Net.Name) Then
                                        Begin
                                            Obj := SpatIter.NextPCBObject;
                                            Continue;
                                        End;
                                    Except
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;

                                    { Identify which of Other's endpoints sits }
                                    { on (PX, PY) and build a vector away from }
                                    { the shared point.                          }
                                    If (Abs(Other.X1 - PX) <= Tol)
                                       And (Abs(Other.Y1 - PY) <= Tol) Then
                                    Begin
                                        OtherEndpoint := 1;
                                        V2X := Other.X2 - PX;
                                        V2Y := Other.Y2 - PY;
                                        OX := Other.X2; OY := Other.Y2;
                                    End
                                    Else If (Abs(Other.X2 - PX) <= Tol)
                                            And (Abs(Other.Y2 - PY) <= Tol) Then
                                    Begin
                                        OtherEndpoint := 2;
                                        V2X := Other.X1 - PX;
                                        V2Y := Other.Y1 - PY;
                                        OX := Other.X1; OY := Other.Y1;
                                    End
                                    Else
                                    Begin
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;

                                    L2 := Sqrt(V2X * V2X + V2Y * V2Y);
                                    If L2 < 1 Then
                                    Begin
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;

                                    { Interior angle from dot product. The   }
                                    { vectors point AWAY from the shared    }
                                    { endpoint, so the dot product directly }
                                    { gives the interior angle.              }
                                    Dot := V1X * V2X + V1Y * V2Y;
                                    CosTheta := Dot / (L1 * L2);
                                    If CosTheta > 1.0 Then CosTheta := 1.0;
                                    If CosTheta < -1.0 Then CosTheta := -1.0;
                                    ThetaRad := ArcCos(CosTheta);
                                    ThetaDeg := ThetaRad * 180.0 / cPi;

                                    { Skip nearly-collinear joins (almost 180 }
                                    { degrees) -- no corner to fillet.        }
                                    If ThetaDeg > 179.0 Then
                                    Begin
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;

                                    { Only act on corners sharper than the   }
                                    { user-specified threshold.               }
                                    If ThetaDeg >= MinAngleDeg Then
                                    Begin
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;

                                    { Unit vectors along each track, away    }
                                    { from the shared endpoint.               }
                                    U1X := V1X / L1; U1Y := V1Y / L1;
                                    U2X := V2X / L2; U2Y := V2Y / L2;

                                    { tan(theta/2) and sin(theta/2). We      }
                                    { already filtered theta == pi above so   }
                                    { sin(theta/2) > 0.                       }
                                    TangentDist := R / Tan(ThetaRad / 2.0);
                                    CenterDist := R / Sin(ThetaRad / 2.0);

                                    { Refuse fillets that would consume more  }
                                    { than the track's remaining length; we   }
                                    { just skip and surface that fact via the }
                                    { Skipped counter.                        }
                                    If (TangentDist >= L1) Or (TangentDist >= L2) Then
                                    Begin
                                        Inc(Skipped);
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;

                                    { Tangent points along each track.        }
                                    T1X := PX + TangentDist * U1X;
                                    T1Y := PY + TangentDist * U1Y;
                                    T2X := PX + TangentDist * U2X;
                                    T2Y := PY + TangentDist * U2Y;

                                    { Bisector unit vector. u1 + u2 lies on   }
                                    { the bisector pointing INTO the corner   }
                                    { (away from P toward the arc center).    }
                                    BX := U1X + U2X;
                                    BY := U1Y + U2Y;
                                    BLen := Sqrt(BX * BX + BY * BY);
                                    If BLen < 0.0001 Then
                                    Begin
                                        Inc(Skipped);
                                        Obj := SpatIter.NextPCBObject;
                                        Continue;
                                    End;
                                    BX := BX / BLen;
                                    BY := BY / BLen;

                                    CX := PX + CenterDist * BX;
                                    CY := PY + CenterDist * BY;

                                    { Arc start/end angles in Altium degrees  }
                                    { (counter-clockwise from +X). Altium     }
                                    { sweeps EndAngle counter-clockwise from  }
                                    { StartAngle, so we pick the order that   }
                                    { keeps the sweep <= 180 degrees.         }
                                    A1 := ArcTan2(T1Y - CY, T1X - CX) * 180.0 / cPi;
                                    A2 := ArcTan2(T2Y - CY, T2X - CX) * 180.0 / cPi;
                                    If A1 < 0 Then A1 := A1 + 360.0;
                                    If A2 < 0 Then A2 := A2 + 360.0;
                                    StartAngleDeg := A1;
                                    EndAngleDeg := A2;
                                    If (EndAngleDeg - StartAngleDeg) < 0 Then
                                        EndAngleDeg := EndAngleDeg + 360.0;
                                    If (EndAngleDeg - StartAngleDeg) > 180.0 Then
                                    Begin
                                        StartAngleDeg := A2;
                                        EndAngleDeg := A1;
                                        If (EndAngleDeg - StartAngleDeg) < 0 Then
                                            EndAngleDeg := EndAngleDeg + 360.0;
                                    End;

                                    NetName := '';
                                    Try If Track.Net <> Nil Then NetName := Track.Net.Name; Except End;
                                    LayerName := GetLayerString(Track.Layer);

                                    { Apply the mutation when not in dry_run.  }
                                    { Defensive: bail the whole pair out on    }
                                    { any failure rather than half-modify it.  }
                                    ApplyOk := True;
                                    If Not DryRun Then
                                    Begin
                                        Arc := Nil;
                                        Try
                                            Arc := PCBServer.PCBObjectFactory(
                                                eArcObject, eNoDimension,
                                                eCreate_Default);
                                        Except End;
                                        If Arc = Nil Then ApplyOk := False;

                                        If ApplyOk Then
                                        Begin
                                            Try
                                                Arc.XCenter := Round(CX);
                                                Arc.YCenter := Round(CY);
                                                Arc.Radius := Round(R);
                                                Arc.StartAngle := StartAngleDeg;
                                                Arc.EndAngle := EndAngleDeg;
                                                Arc.LineWidth := Track.Width;
                                                Arc.Layer := Track.Layer;
                                                If Track.Net <> Nil Then
                                                    Arc.Net := Track.Net;
                                                Board.AddPCBObject(Arc);
                                                PCBServer.SendMessageToRobots(
                                                    Board.I_ObjectAddress,
                                                    c_Broadcast,
                                                    PCBM_BoardRegisteration,
                                                    Arc.I_ObjectAddress);
                                            Except
                                                ApplyOk := False;
                                            End;
                                        End;

                                        If ApplyOk Then
                                        Begin
                                            Try
                                                Track.BeginModify;
                                                If Endpoint = 1 Then
                                                Begin
                                                    Track.X1 := Round(T1X);
                                                    Track.Y1 := Round(T1Y);
                                                End
                                                Else
                                                Begin
                                                    Track.X2 := Round(T1X);
                                                    Track.Y2 := Round(T1Y);
                                                End;
                                                Track.EndModify;
                                                Try Track.GraphicallyInvalidate; Except End;
                                            Except
                                                ApplyOk := False;
                                            End;
                                        End;

                                        If ApplyOk Then
                                        Begin
                                            Try
                                                Other.BeginModify;
                                                If OtherEndpoint = 1 Then
                                                Begin
                                                    Other.X1 := Round(T2X);
                                                    Other.Y1 := Round(T2Y);
                                                End
                                                Else
                                                Begin
                                                    Other.X2 := Round(T2X);
                                                    Other.Y2 := Round(T2Y);
                                                End;
                                                Other.EndModify;
                                                Try Other.GraphicallyInvalidate; Except End;
                                            Except
                                                ApplyOk := False;
                                            End;
                                        End;
                                    End;

                                    If ApplyOk Then
                                    Begin
                                        Inc(Filleted);
                                        If Not DryRun Then SaveNeeded := True;
                                    End
                                    Else
                                        Inc(Skipped);

                                    If Not First Then ItemsJson := ItemsJson + ',';
                                    First := False;
                                    EntryJson :=
                                        JsonStr('net', NetName) + ',' +
                                        JsonStr('layer', LayerName) + ',' +
                                        JsonInt('x_mils', CoordToMils(PX)) + ',' +
                                        JsonInt('y_mils', CoordToMils(PY)) + ',' +
                                        JsonFloat('angle_deg', ThetaDeg) + ',' +
                                        JsonInt('radius_mils', RadiusMils) + ',' +
                                        JsonBool('applied', ApplyOk And (Not DryRun));
                                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                                Except End;
                                Obj := SpatIter.NextPCBObject;
                            End;
                        Finally
                            Try Board.SpatialIterator_Destroy(SpatIter); Except End;
                        End;
                    Except End;
                    If Filleted + Skipped >= MaxItems Then Break;
                End;

                Track := Iter.NextPCBObject;
            End;
        Finally
            Try Board.BoardIterator_Destroy(Iter); Except End;
        End;
    Finally
        If Not DryRun Then PCBServer.PostProcess;
    End;

    If SaveNeeded Then
    Begin
        Try Board.GraphicallyInvalidate; Except End;
        Try SaveDocByPath(Board.FileName); Except End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonBool('dry_run', DryRun) + ',' +
            JsonInt('filleted_count', Filleted) + ',' +
            JsonInt('skipped_count', Skipped) + ',' +
            JsonInt('radius_mils', RadiusMils) + ',' +
            JsonFloat('min_angle_deg', MinAngleDeg) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{..............................................................................}
{ PCB_CalcPolygonArea - Report the outline area of each polygon on the board, }
{ optionally filtered by net and/or layer. AreaSize is the polygon's overall  }
{ boundary area; reported in square mils and square millimetres.              }
{ Params: net (optional), layer (optional)                                    }
{..............................................................................}

Function PCB_CalcPolygonArea(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Poly : IPCB_Polygon;
    NetFilter, LayerFilter, NetName, LayerName, NameStr, JsonItems : String;
    AreaCoord, SqMils, SqMm : Double;
    First, Keep : Boolean;
    Count : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetFilter := ExtractJsonValue(Params, 'net');
    LayerFilter := ExtractJsonValue(Params, 'layer');
    JsonItems := '';
    First := True;
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Poly := Iterator.FirstPCBObject;
    While Poly <> Nil Do
    Begin
        NetName := '';
        Try If Poly.Net <> Nil Then NetName := Poly.Net.Name; Except End;
        LayerName := '';
        Try LayerName := GetLayerString(Poly.Layer); Except End;
        NameStr := '';
        Try NameStr := Poly.Name; Except End;

        Keep := True;
        If (NetFilter <> '') And (NetName <> NetFilter) Then Keep := False;
        If (LayerFilter <> '') And (LayerName <> LayerFilter) Then Keep := False;

        If Keep Then
        Begin
            SqMils := 0;
            Try SqMils := PolygonAreaSqMils(Poly); Except End;
            SqMm := SqMils * 0.00064516;
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems
                + '{"name":"' + EscapeJsonString(NameStr) + '",'
                + '"net":"' + EscapeJsonString(NetName) + '",'
                + '"layer":"' + EscapeJsonString(LayerName) + '",'
                + '"area_sq_mils":' + FloatToJsonStr(SqMils) + ','
                + '"area_sq_mm":' + FloatToJsonStr(SqMm) + '}';
            Inc(Count);
        End;
        Poly := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"polygons":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ PCB_SetViaSoldermaskRelief - Set per-via soldermask expansion from the hole }
{ edge so via barrels get a soldermask opening (barrel relief). Optionally    }
{ filter by net. Params: expansion_mils (default 4), net (optional)           }
{..............................................................................}

Function PCB_SetViaSoldermaskRelief(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Via : IPCB_Via;
    NetFilter, NetName, ExpStr : String;
    ExpMils, Count : Integer;
    Keep : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetFilter := ExtractJsonValue(Params, 'net');
    ExpStr := ExtractJsonValue(Params, 'expansion_mils');
    ExpMils := StrToIntDef(ExpStr, 4);
    Count := 0;

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eViaObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    PCBServer.PreProcess;
    Try
        Via := Iterator.FirstPCBObject;
        While Via <> Nil Do
        Begin
            NetName := '';
            Try If Via.Net <> Nil Then NetName := Via.Net.Name; Except End;
            Keep := True;
            If (NetFilter <> '') And (NetName <> NetFilter) Then Keep := False;
            If Keep Then
            Begin
                Try
                    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
                        PCBM_BeginModify, c_NoEventData);
                    Via.SolderMaskExpansionFromHoleEdge := True;
                    Via.SolderMaskExpansion := MilsToCoord(ExpMils);
                    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
                        PCBM_EndModify, c_NoEventData);
                    Inc(Count);
                Except
                End;
            End;
            Via := Iterator.NextPCBObject;
        End;
    Finally
        PCBServer.PostProcess;
    End;
    Board.BoardIterator_Destroy(Iterator);

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"modified":' + IntToStr(Count) + ','
        + '"expansion_mils":' + IntToStr(ExpMils) + '}');
End;

{..............................................................................}
{ PCB_SetMechLayerKind - Assign the kind of one mechanical layer.             }
{ Params: layer, kind (a name such as 'Courtyard Top', or its number)         }
{                                                                              }
{ A kind belongs to ONE layer at a time. Assigning a kind that another layer  }
{ already holds leaves two layers claiming the same purpose, so the previous  }
{ holder is cleared first and reported, rather than leaving the board in a    }
{ state the stack manager did not intend.                                      }
{..............................................................................}

Function PCB_SetMechLayerKind(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj, OtherObj : IPCB_LayerObject_V7;
    LayerName, KindStr, ClearedJson, PartnerName : String;
    PairKindJson, PartnerJson : String;
    TargetLayer, Lyr, PartnerLayer, TopL, BotL : TLayer;
    KindId, Readback, OtherKind : Integer;
    PartnerKind, PairKind, PairIdx, PairKindBack : Integer;
    MechPairs : IPCB_MechanicalLayerPairs;
    First, Paired, PairApplied : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
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

    KindStr := ExtractJsonValue(Params, 'kind');
    If KindStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'kind required, for example "Courtyard Top" or "Not Set"');
        Exit;
    End;

    KindId := MechKindFromString(KindStr);
    If KindId < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_KIND',
            'Unknown mechanical layer kind: ' + KindStr
            + '. Read pcb_get_mech_layer_names for the names this board '
            + 'reports, or pass the number.');
        Exit;
    End;

    TargetLayer := ResolveLayerId(Board, LayerName);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerName + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    If (TargetLayer < eMechanical1) Or (TargetLayer > eMechanical16) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_MECHANICAL',
            'Layer ' + LayerName + ' is not a mechanical layer. Only '
            + 'mechanical layers carry a kind.');
        Exit;
    End;

    LayerStack := Board.LayerStack_V7;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;

    LayerObj := Nil;
    Try LayerObj := LayerStack.LayerObject_V7[TargetLayer]; Except LayerObj := Nil; End;
    If LayerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_IN_STACK',
            'Layer ' + LayerName + ' is not present in the current stack');
        Exit;
    End;

    If ReadMechKind(LayerObj) < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'KIND_UNSUPPORTED',
            'This Altium build does not expose a mechanical layer kind. '
            + 'Kinds were introduced after AD18; before that a layer''s '
            + 'purpose is carried by its name and its layer pairing.');
        Exit;
    End;

    { A PAIRED KIND IS HELD BY THE LAYER PAIR, not by either layer.          }
    {                                                                        }
    { Writing Kind reads back unchanged for any Top or Bottom kind, on every }
    { mechanical layer, and leaves the LayerKindMapping stream empty. Pair   }
    { kinds are a separate enum with no side suffix and its own numbering,   }
    { written against a pair index. Single kinds such as Fab Notes are       }
    { unaffected and still go on the layer.                                  }
    {                                                                        }
    { The partner cannot be guessed: it is whichever mechanical layer the    }
    { board uses for the other side, so the caller names it.                 }
    PartnerKind := MechKindPartner(KindId);
    PairKind := MechPairKindFromLayerKind(KindId);
    PartnerName := ExtractJsonValue(Params, 'partner_layer');
    PartnerLayer := eNoLayer;
    If PartnerName <> '' Then PartnerLayer := ResolveLayerId(Board, PartnerName);

    If (PartnerKind >= 0) And (PartnerLayer = eNoLayer) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'PARTNER_REQUIRED',
            '"' + MechKindToString(KindId) + '" is one half of a pair, and a '
            + 'paired kind is held by the layer PAIR rather than by either '
            + 'layer. Pass partner_layer naming the mechanical layer that '
            + 'carries "' + MechKindToString(PartnerKind) + '", and the two '
            + 'will be joined and the pair given the kind "'
            + MechPairKindToString(PairKind) + '".');
        Exit;
    End;

    If (PartnerKind >= 0) And (PartnerLayer = TargetLayer) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'PARTNER_REQUIRED',
            'partner_layer names the same layer as layer. The two sides of a '
            + 'pair have to be different mechanical layers.');
        Exit;
    End;

    MechPairs := Nil;
    Try MechPairs := Board.MechanicalPairs; Except MechPairs := Nil; End;

    ClearedJson := '';
    First := True;
    PairApplied := False;

    PCBServer.PreProcess;
    Try
        { 'Not Set' is the one kind several layers may share, so it never    }
        { displaces anything. Nor does a paired kind, which no layer holds.   }
        If (KindId > 0) And (PartnerKind < 0) Then
        Begin
            For Lyr := eMechanical1 To eMechanical16 Do
            Begin
                If Lyr <> TargetLayer Then
                Begin
                    OtherObj := Nil;
                    Try OtherObj := LayerStack.LayerObject_V7[Lyr]; Except OtherObj := Nil; End;
                    If OtherObj <> Nil Then
                    Begin
                        OtherKind := ReadMechKind(OtherObj);
                        If OtherKind = KindId Then
                        Begin
                            Try OtherObj.Kind := 0; Except End;
                            If Not First Then ClearedJson := ClearedJson + ',';
                            First := False;
                            ClearedJson := ClearedJson
                                + '"' + EscapeJsonString(GetLayerString(Lyr)) + '"';
                        End;
                    End;
                End;
            End;
        End;

        Try LayerObj.Kind := KindId; Except End;

        If (PartnerKind >= 0) And (PairKind >= 0) And (MechPairs <> Nil) Then
        Begin
            { AddPair takes the TOP layer first and is the only call that   }
            { reports an index: PairDefined answers a boolean and           }
            { LayerPair(i) is noted as broken in the reference, so a pair   }
            { that already exists cannot be located by reading. Ask for the }
            { pair first in case AddPair is idempotent, and rebuild it only }
            { when that gives no index.                                     }
            If Pos(' Top', MechKindToString(KindId)) > 0 Then
            Begin
                TopL := TargetLayer;
                BotL := PartnerLayer;
            End
            Else
            Begin
                TopL := PartnerLayer;
                BotL := TargetLayer;
            End;

            PairIdx := -1;
            Try PairIdx := MechPairs.AddPair(TopL, BotL); Except PairIdx := -1; End;

            If PairIdx < 0 Then
            Begin
                Paired := False;
                Try Paired := MechPairs.PairDefined(TopL, BotL); Except Paired := False; End;
                If Not Paired Then
                    Try Paired := MechPairs.PairDefined(BotL, TopL); Except Paired := False; End;
                If Paired Then
                Begin
                    Try MechPairs.RemovePair(TopL, BotL); Except End;
                    Try MechPairs.RemovePair(BotL, TopL); Except End;
                    Try PairIdx := MechPairs.AddPair(TopL, BotL); Except PairIdx := -1; End;
                End;
            End;

            If PairIdx >= 0 Then
            Begin
                Try
                    MechPairs.SetState_LayerPairKind(PairIdx) := PairKind;
                Except
                End;
                PairKindBack := -1;
                Try
                    PairKindBack := MechPairs.LayerPairKind(PairIdx);
                Except
                    PairKindBack := -1;
                End;
                { A build that will not report the pair kind back must not  }
                { read as a failure, so only a value that came back         }
                { DIFFERENT counts as refused.                              }
                PairApplied := (PairKindBack = PairKind) Or (PairKindBack < 0);
            End;
        End;

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    { Read back rather than trusting the write. The assignment is late bound  }
    { and a refused write raises nothing, so reporting success from the fact  }
    { that no exception escaped would report success for doing nothing.       }
    { For a paired kind the layer property reading back unchanged is         }
    { expected, so the pair is what decides the outcome.                     }
    Readback := ReadMechKind(LayerObj);
    If (Readback <> KindId) And (Not PairApplied) Then
    Begin
        If PartnerKind >= 0 Then
            Result := BuildErrorResponse(RequestId, 'KIND_NOT_APPLIED',
                '"' + MechKindToString(KindId) + '" was refused as pair kind "'
                + MechPairKindToString(PairKind) + '" on the pair of '
                + GetLayerString(TargetLayer) + ' and '
                + GetLayerString(PartnerLayer) + '. The kind was NOT changed.')
        Else
            Result := BuildErrorResponse(RequestId, 'KIND_NOT_APPLIED',
                'The write was accepted but the layer still reads as "'
                + MechKindToString(Readback) + '". The kind was NOT changed.');
        Exit;
    End;

    { Null rather than an empty string for a single kind, so a reader can    }
    { tell "no pair involved" from "paired under a kind with no name".       }
    PairKindJson := 'null';
    PartnerJson := 'null';
    If PairApplied Then
        PairKindJson := '"' + EscapeJsonString(MechPairKindToString(PairKind)) + '"';
    If PartnerLayer <> eNoLayer Then
        PartnerJson := '"' + EscapeJsonString(GetLayerString(PartnerLayer)) + '"';

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"layer":"' + EscapeJsonString(GetLayerString(TargetLayer)) + '",'
        + '"kind":"' + EscapeJsonString(MechKindToString(KindId)) + '",'
        + '"kind_id":' + IntToStr(KindId) + ','
        + '"paired":' + BoolToJsonStr(PairApplied) + ','
        + '"pair_kind":' + PairKindJson + ','
        + '"partner_layer":' + PartnerJson + ','
        + '"cleared_from":[' + ClearedJson + ']}');
End;

{..............................................................................}
{ PCB_GetMechLayerNames - List the enabled (displayed) mechanical layers with  }
{ their custom names. Uses only proven accessors (LayerStack_V7 /              }
{ LayerObject_V7[] / LayerIsDisplayed) -- ILayer.MechanicalLayer(i) and        }
{ MechanicalLayerEnabled are undeclared in this script binding.               }
{..............................................................................}

Function PCB_GetMechLayerNames(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    Lyr : TLayer;
    JsonItems, NameStr : String;
    First, Disp : Boolean;
    Count, KindId : Integer;
Begin
    Board := GetPCBBoardAnywhere;
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

    For Lyr := eMechanical1 To eMechanical16 Do
    Begin
        LayerObj := Nil;
        Try LayerObj := LayerStack.LayerObject_V7[Lyr]; Except LayerObj := Nil; End;
        If LayerObj <> Nil Then
        Begin
            Disp := False;
            Try Disp := Board.LayerIsDisplayed[Lyr]; Except End;
            If Disp Then
            Begin
                NameStr := '';
                Try NameStr := LayerObj.Name; Except End;
                { The kind says what the layer is FOR, and a caller setting  }
                { one needs to see what is already taken: a kind belongs to  }
                { a single layer. -1 means this build has no kinds at all.   }
                KindId := ReadMechKind(LayerObj);
                If Not First Then JsonItems := JsonItems + ',';
                First := False;
                JsonItems := JsonItems
                    + '{"layer":"' + EscapeJsonString(GetLayerString(Lyr)) + '",'
                    + '"name":"' + EscapeJsonString(NameStr) + '",'
                    + '"kind":"' + EscapeJsonString(MechKindToString(KindId)) + '",'
                    + '"kind_id":' + IntToStr(KindId) + '}';
                Inc(Count);
            End;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"mechanical_layers":[' + JsonItems + '],"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ Shared helpers for the production-feature handlers below.                   }
{..............................................................................}

{ AABB overlap of two coordinate rectangles, each x1<x2, y1<y2, expanded by   }
{ Margin (internal units) on every side.                                      }
Function RectsOverlap(Ax1, Ay1, Ax2, Ay2, Bx1, By1, Bx2, By2, Margin : Integer) : Boolean;
Begin
    Result := Not ((Ax1 - Margin > Bx2) Or (Ax2 + Margin < Bx1)
                Or (Ay1 - Margin > By2) Or (Ay2 + Margin < By1));
End;

{ Map a candidate index 0..7 to a designator auto-position anchor, fanning    }
{ out from the preferred side first.                                          }
Function SilkAnchorForIndex(I : Integer) : TTextAutoposition;
Begin
    Case I Of
        0: Result := eAutoPos_CenterRight;
        1: Result := eAutoPos_TopCenter;
        2: Result := eAutoPos_BottomCenter;
        3: Result := eAutoPos_CenterLeft;
        4: Result := eAutoPos_TopRight;
        5: Result := eAutoPos_TopLeft;
        6: Result := eAutoPos_BottomRight;
        7: Result := eAutoPos_BottomLeft;
    Else
        Result := eAutoPos_CenterCenter;
    End;
End;

{ True if the designator text Slk overlaps any pad or other silk text in its  }
{ immediate vicinity. SelfAddr excludes the text object itself from the test. }
Function SilkCollides(Board : IPCB_Board; Slk : IPCB_Text; SelfAddr : Integer) : Boolean;
Var
    SBB, OBB : TCoordRect;
    Margin : Integer;
    Iter : IPCB_SpatialIterator;
    Obj : IPCB_Primitive;
Begin
    Result := False;
    SBB := Slk.BoundingRectangle;
    Margin := MilsToCoord(2);
    Iter := Board.SpatialIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject, eTextObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Area(SBB.X1 - Margin, SBB.Y1 - Margin, SBB.X2 + Margin, SBB.Y2 + Margin);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            If Obj.I_ObjectAddress <> SelfAddr Then
            Begin
                Try
                    OBB := Obj.BoundingRectangle;
                    If RectsOverlap(SBB.X1, SBB.Y1, SBB.X2, SBB.Y2,
                                    OBB.X1, OBB.Y1, OBB.X2, OBB.Y2, 0) Then
                        Result := True;
                Except End;
            End;
            If Result Then Break;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.SpatialIterator_Destroy(Iter);
    End;
End;

{ Create a track primitive (caller adds it to the board / net).               }
Function NewTrack(X1c, Y1c, X2c, Y2c, WidthC : Integer; Layer : TLayer) : IPCB_Track;
Begin
    Result := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    Result.Layer := Layer;
    Result.Width := WidthC;
    Result.x1 := X1c;
    Result.y1 := Y1c;
    Result.x2 := X2c;
    Result.y2 := Y2c;
End;

{ Create an unplated tooling hole (copper == hole, no annular ring) and add   }
{ it to the board. Coordinates and diameter are in internal units.            }
Function NewToolingHole(Board : IPCB_Board; Xc, Yc, Dia : Integer) : IPCB_Pad;
Begin
    Result := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    Result.Layer := eMultiLayer;
    Result.X := Xc;
    Result.Y := Yc;
    Result.TopXSize := Dia;
    Result.TopYSize := Dia;
    Result.SetState_HoleSize(Dia);
    Board.AddPCBObject(Result);
End;

{ Create a round copper fiducial on Lyr and add it to the board.              }
Function NewFiducial(Board : IPCB_Board; Xc, Yc, Dia : Integer; Lyr : TLayer) : IPCB_Pad;
Begin
    Result := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    Result.Layer := Lyr;
    Result.X := Xc;
    Result.Y := Yc;
    Result.TopXSize := Dia;
    Result.TopYSize := Dia;
    Board.AddPCBObject(Result);
End;

{..............................................................................}
{ PCB_ImportPlacement - position components from a packed coordinate list.    }
{ placements = pipe-separated records "designator,x_mils,y_mils,rotation,     }
{ layer". x/y/rotation/layer each optional per record (empty = leave as-is).  }
{ Mirror of pcb_export_coordinates: absolute mils, rotation degrees, layer    }
{ token TopLayer/BottomLayer (a layer change flips the component side).       }
{..............................................................................}
Function PCB_ImportPlacement(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    ListStr, RecStr, Remaining, Token : String;
    Desig, XStr, YStr, RotStr, LayerStr, BadLayers : String;
    PipePos, CommaPos, FieldIdx, Applied, Failed : Integer;
    TargetLayer : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    ListStr := ExtractJsonValue(Params, 'placements');
    If ListStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'placements parameter required');
        Exit;
    End;

    Applied := 0;
    Failed := 0;
    BadLayers := '';
    Remaining := ListStr;
    While Length(Remaining) > 0 Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos = 0 Then
        Begin
            RecStr := Remaining;
            Remaining := '';
        End
        Else
        Begin
            RecStr := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End;
        If RecStr = '' Then Continue;

        Desig := ''; XStr := ''; YStr := ''; RotStr := ''; LayerStr := '';
        FieldIdx := 0;
        While (RecStr <> '') And (FieldIdx <= 4) Do
        Begin
            CommaPos := Pos(',', RecStr);
            If CommaPos = 0 Then
            Begin
                Token := RecStr;
                RecStr := '';
            End
            Else
            Begin
                Token := Copy(RecStr, 1, CommaPos - 1);
                RecStr := Copy(RecStr, CommaPos + 1, Length(RecStr));
            End;
            Case FieldIdx Of
                0: Desig := Token;
                1: XStr := Token;
                2: YStr := Token;
                3: RotStr := Token;
                4: LayerStr := Token;
            End;
            FieldIdx := FieldIdx + 1;
        End;

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

        TargetLayer := eNoLayer;
        If LayerStr <> '' Then
        Begin
            TargetLayer := ResolveLayerId(Board, LayerStr);
            If TargetLayer = eNoLayer Then
            Begin
                Failed := Failed + 1;
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;
        End;

        PCBServer.PreProcess;
        Try
            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                PCBM_BeginModify, c_NoEventData);
            If XStr <> '' Then Comp.x := MilsToCoord(StrToIntDef(XStr, 0));
            If YStr <> '' Then Comp.y := MilsToCoord(StrToIntDef(YStr, 0));
            If RotStr <> '' Then Comp.Rotation := StrToFloatDef(RotStr, 0);
            If TargetLayer <> eNoLayer Then
            Begin
                If Comp.Layer <> TargetLayer Then Comp.Layer := TargetLayer;
            End;
            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast,
                PCBM_EndModify, c_NoEventData);
        Finally
            PCBServer.PostProcess;
        End;
        Applied := Applied + 1;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"applied":' + IntToStr(Applied) + ',"failed":' + IntToStr(Failed) + ','
        + '"unknown_layers":"' + EscapeJsonString(BadLayers) + '"}');
End;

{..............................................................................}
{ PCB_Teardrops - launch Altium's Teardrop command board-wide.                }
{ The Teardrop dialog is modal and cannot be suppressed from script (same     }
{ limitation as the ECO dialog); the add/remove choice is made in the dialog. }
{..............................................................................}
Function PCB_Teardrops(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    ResetParameters;
    AddStringParameter('Scope', 'All');
    RunProcess('PCB:Select');
    ResetParameters;
    RunProcess('PCB:Teardrop');

    Result := BuildSuccessResponse(RequestId,
        '{"launched":true,"modal":true,'
        + '"note":"The Teardrop dialog is modal and cannot be suppressed from '
        + 'script; choose Add or Remove and confirm it in Altium."}');
End;

{..............................................................................}
{ PCB_AutoplaceSilkscreen - reposition component designators to clear pads    }
{ and other silk. For each visible designator, try a ring of auto-position    }
{ anchors and keep the first that collides with nothing; otherwise leave it.  }
{ Approximate (first-fit), not a global optimum.                              }
{..............................................................................}
Function PCB_AutoplaceSilkscreen(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
    Slk : IPCB_Text;
    I, Placed, Skipped, Total : Integer;
    Ok : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Placed := 0;
    Skipped := 0;
    Total := 0;
    Try PCBServer.SystemOptions.DoOnlineDRC := False; Except End;
    PCBServer.PreProcess;
    Try
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Comp := Iter.FirstPCBObject;
            While Comp <> Nil Do
            Begin
                Total := Total + 1;
                Slk := Nil;
                Try Slk := Comp.Name; Except End;
                If (Slk <> Nil) And (Not Slk.IsHidden) Then
                Begin
                    Ok := False;
                    Slk.BeginModify;
                    For I := 0 To 7 Do
                    Begin
                        Try Comp.ChangeNameAutoposition(SilkAnchorForIndex(I)); Except End;
                        If Not SilkCollides(Board, Slk, Slk.I_ObjectAddress) Then
                        Begin
                            Ok := True;
                            Break;
                        End;
                    End;
                    Slk.EndModify;
                    If Ok Then Placed := Placed + 1 Else Skipped := Skipped + 1;
                End
                Else Skipped := Skipped + 1;
                Comp := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
        Try PCBServer.SystemOptions.DoOnlineDRC := True; Except End;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"skipped":' + IntToStr(Skipped)
        + ',"total":' + IntToStr(Total) + '}');
End;

{..............................................................................}
{ PCB_TuneLength - add approximate routed length to a net by laying a square  }
{ serpentine at a caller-given anchor. Open-loop and NOT DRC-checked: the     }
{ caller supplies where to put it and verifies clearance. Reports the net's   }
{ RoutedLength before and after so the achieved delta is visible.            }
{ Params: net, add_length_mils, x_mils, y_mils, layer, amplitude_mils,        }
{ width_mils (optional). Serpentine runs horizontally from the anchor.        }
{..............................................................................}
Function PCB_TuneLength(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Net : IPCB_Net;
    NetName, LayerStr : String;
    AddLen, X0, Y0, Amp, WidthMils, Bumps, I : Integer;
    Layer : TLayer;
    BeforeLen, AfterLen, AmpC, WidthC, X, Step : Integer;
    Trk : IPCB_Track;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetName := ExtractJsonValue(Params, 'net');
    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'net parameter required');
        Exit;
    End;
    Net := FindNetByName(Board, NetName);
    If Net = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Net not found: ' + NetName);
        Exit;
    End;

    AddLen := StrToIntDef(ExtractJsonValue(Params, 'add_length_mils'), 0);
    X0 := StrToIntDef(ExtractJsonValue(Params, 'x_mils'), 0);
    Y0 := StrToIntDef(ExtractJsonValue(Params, 'y_mils'), 0);
    Amp := StrToIntDef(ExtractJsonValue(Params, 'amplitude_mils'), 40);
    WidthMils := StrToIntDef(ExtractJsonValue(Params, 'width_mils'), 6);
    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then Layer := eTopLayer
    Else Layer := ResolveLayerId(Board, LayerStr);
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    If (AddLen <= 0) Or (Amp <= 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'add_length_mils and amplitude_mils must be positive');
        Exit;
    End;

    { Each square bump (up + down) adds ~2*amplitude of copper. }
    Bumps := AddLen Div (2 * Amp);
    If Bumps < 1 Then Bumps := 1;

    AmpC := MilsToCoord(Amp);
    WidthC := MilsToCoord(WidthMils);
    Step := MilsToCoord(Amp);            { horizontal pitch per bump leg }
    X := MilsToCoord(X0);

    BeforeLen := CoordToMils(Net.RoutedLength);

    PCBServer.PreProcess;
    Try
        For I := 0 To Bumps - 1 Do
        Begin
            { up }
            Trk := NewTrack(X, MilsToCoord(Y0), X, MilsToCoord(Y0) + AmpC, WidthC, Layer);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                PCBM_BoardRegisteration, Trk.I_ObjectAddress);
            Board.AddPCBObject(Trk);
            Net.AddPCBObject(Trk);
            { across the top }
            Trk := NewTrack(X, MilsToCoord(Y0) + AmpC, X + Step, MilsToCoord(Y0) + AmpC, WidthC, Layer);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                PCBM_BoardRegisteration, Trk.I_ObjectAddress);
            Board.AddPCBObject(Trk);
            Net.AddPCBObject(Trk);
            { down }
            Trk := NewTrack(X + Step, MilsToCoord(Y0) + AmpC, X + Step, MilsToCoord(Y0), WidthC, Layer);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                PCBM_BoardRegisteration, Trk.I_ObjectAddress);
            Board.AddPCBObject(Trk);
            Net.AddPCBObject(Trk);
            { baseline gap to the next bump }
            Trk := NewTrack(X + Step, MilsToCoord(Y0), X + 2 * Step, MilsToCoord(Y0), WidthC, Layer);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                PCBM_BoardRegisteration, Trk.I_ObjectAddress);
            Board.AddPCBObject(Trk);
            Net.AddPCBObject(Trk);
            X := X + 2 * Step;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    ResetParameters;
    RunProcess('PCB:UpdateConnectivity');
    AfterLen := CoordToMils(Net.RoutedLength);
    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"net":"' + EscapeJsonString(NetName) + '",'
        + '"bumps":' + IntToStr(Bumps) + ','
        + '"length_before_mils":' + IntToStr(BeforeLen) + ','
        + '"length_after_mils":' + IntToStr(AfterLen) + ','
        + '"added_mils":' + IntToStr(AfterLen - BeforeLen) + ','
        + '"drc_checked":false}');
End;

{..............................................................................}
{ PCB_Panelize - build a production panel on the current (blank) board:       }
{ an embedded-board array of a source .PcbDoc, a rectangular panel outline,   }
{ corner tooling holes, and fiducials. board_width_mils / board_height_mils   }
{ are the source board size; the caller supplies them.                        }
{..............................................................................}
Function PCB_Panelize(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Emb : IPCB_EmbeddedBoard;
    Trk : IPCB_Track;
    ChildPath : String;
    Rows, Cols, BoardW, BoardH, ColGap, RowGap, Border : Integer;
    PanW, PanH, RailW, FidC, ToolC, Inset : Integer;
    MechL : TLayer;
    AddFid, AddTool : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active (open the blank panel board first)');
        Exit;
    End;

    ChildPath := ExtractJsonValue(Params, 'child_path');
    If ChildPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'child_path (source .PcbDoc) is required');
        Exit;
    End;

    BoardW := StrToIntDef(ExtractJsonValue(Params, 'board_width_mils'), 0);
    BoardH := StrToIntDef(ExtractJsonValue(Params, 'board_height_mils'), 0);
    If (BoardW <= 0) Or (BoardH <= 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM', 'board_width_mils and board_height_mils are required (source board size)');
        Exit;
    End;
    Rows := StrToIntDef(ExtractJsonValue(Params, 'rows'), 2);
    Cols := StrToIntDef(ExtractJsonValue(Params, 'cols'), 2);
    ColGap := StrToIntDef(ExtractJsonValue(Params, 'col_gap_mils'), 100);
    RowGap := StrToIntDef(ExtractJsonValue(Params, 'row_gap_mils'), 100);
    Border := StrToIntDef(ExtractJsonValue(Params, 'border_mils'), 200);
    AddTool := ExtractJsonValue(Params, 'tooling_holes') <> 'false';
    AddFid := ExtractJsonValue(Params, 'fiducials') <> 'false';
    If Rows < 1 Then Rows := 1;
    If Cols < 1 Then Cols := 1;

    PanW := 2 * Border + BoardW * Cols + ColGap * (Cols - 1);
    PanH := 2 * Border + BoardH * Rows + RowGap * (Rows - 1);
    RailW := MilsToCoord(10);
    MechL := eMechanical1;
    FidC := MilsToCoord(40);
    ToolC := MilsToCoord(118);
    Inset := Border Div 2;

    PCBServer.PreProcess;
    Try
        { Embedded-board array (the panel core). Emb is the subtype so the    }
        { RowCount/ColCount/Spacing members resolve.                          }
        Emb := PCBServer.PCBObjectFactory(eEmbeddedBoardObject, eNoDimension, eCreate_Default);
        Emb.DocumentPath := ChildPath;
        Emb.RowCount := Rows;
        Emb.ColCount := Cols;
        Emb.RowSpacing := MilsToCoord(BoardH + RowGap);
        Emb.ColSpacing := MilsToCoord(BoardW + ColGap);
        Emb.XLocation := MilsToCoord(Border);
        Emb.YLocation := MilsToCoord(Border);
        Board.AddPCBObject(Emb);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Emb.I_ObjectAddress);

        { Rectangular panel rails on a mechanical layer (converted to the      }
        { board outline below). Tracks are created selected for the convert.   }
        Trk := NewTrack(0, 0, MilsToCoord(PanW), 0, RailW, MechL);
        Board.AddPCBObject(Trk); Trk.Selected := True;
        Trk := NewTrack(MilsToCoord(PanW), 0, MilsToCoord(PanW), MilsToCoord(PanH), RailW, MechL);
        Board.AddPCBObject(Trk); Trk.Selected := True;
        Trk := NewTrack(MilsToCoord(PanW), MilsToCoord(PanH), 0, MilsToCoord(PanH), RailW, MechL);
        Board.AddPCBObject(Trk); Trk.Selected := True;
        Trk := NewTrack(0, MilsToCoord(PanH), 0, 0, RailW, MechL);
        Board.AddPCBObject(Trk); Trk.Selected := True;
        Try Board.LayerIsDisplayed[MechL] := True; Except End;

        If AddTool Then
        Begin
            NewToolingHole(Board, MilsToCoord(Inset), MilsToCoord(Inset), ToolC);
            NewToolingHole(Board, MilsToCoord(PanW - Inset), MilsToCoord(Inset), ToolC);
            NewToolingHole(Board, MilsToCoord(PanW - Inset), MilsToCoord(PanH - Inset), ToolC);
            NewToolingHole(Board, MilsToCoord(Inset), MilsToCoord(PanH - Inset), ToolC);
        End;
        If AddFid Then
        Begin
            NewFiducial(Board, MilsToCoord(Inset), MilsToCoord(Inset), FidC, eTopLayer);
            NewFiducial(Board, MilsToCoord(Inset), MilsToCoord(Inset), FidC, eBottomLayer);
            NewFiducial(Board, MilsToCoord(PanW - Inset), MilsToCoord(Inset), FidC, eTopLayer);
            NewFiducial(Board, MilsToCoord(Inset), MilsToCoord(PanH - Inset), FidC, eTopLayer);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    { Convert the selected rail tracks into the actual board outline. }
    ResetParameters;
    AddStringParameter('Mode', 'BOARDOUTLINE_FROM_SEL_PRIMS');
    RunProcess('PCB:PlaceBoardOutline');

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"child_path":"' + EscapeJsonString(ChildPath) + '",'
        + '"rows":' + IntToStr(Rows) + ',"cols":' + IntToStr(Cols) + ','
        + '"panel_width_mils":' + IntToStr(PanW) + ','
        + '"panel_height_mils":' + IntToStr(PanH) + ','
        + '"tooling_holes":' + BoolToJsonStr(AddTool) + ','
        + '"fiducials":' + BoolToJsonStr(AddFid) + '}');
End;

{..............................................................................}
{ PCB_DeleteInvalidObjects - remove degenerate primitives: zero-area regions  }
{ and zero-length tracks. Find-one-remove-restart so a live iterator is never  }
{ invalidated by a removal.                                                    }
{..............................................................................}
Function PCB_DeleteInvalidObjects(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj, Victim : IPCB_Primitive;
    Trk : IPCB_Track;
    BR : TCoordRect;
    Removed, Guard : Integer;
    FoundOne : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Removed := 0;
    Guard := 0;
    PCBServer.PreProcess;
    Try
        FoundOne := True;
        While FoundOne And (Guard < 100000) Do
        Begin
            Guard := Guard + 1;
            FoundOne := False;
            Victim := Nil;
            Iter := Board.BoardIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eRegionObject));
                Iter.AddFilter_LayerSet(AllLayers);
                Iter.AddFilter_Method(eProcessAll);
                Obj := Iter.FirstPCBObject;
                While Obj <> Nil Do
                Begin
                    If Obj.ObjectId = eTrackObject Then
                    Begin
                        Trk := Obj;
                        If (Trk.x1 = Trk.x2) And (Trk.y1 = Trk.y2) Then
                        Begin
                            Victim := Obj;
                            FoundOne := True;
                        End;
                    End
                    Else If Obj.ObjectId = eRegionObject Then
                    Begin
                        Try
                            BR := Obj.BoundingRectangle;
                            If ((BR.X2 - BR.X1) <= 0) Or ((BR.Y2 - BR.Y1) <= 0) Then
                            Begin
                                Victim := Obj;
                                FoundOne := True;
                            End;
                        Except End;
                    End;
                    If FoundOne Then Break;
                    Obj := Iter.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iter);
            End;

            If FoundOne And (Victim <> Nil) Then
            Begin
                Try
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                        PCBM_BoardRegisteration, Victim.I_ObjectAddress);
                    Board.RemovePCBObject(Victim);
                    Removed := Removed + 1;
                Except End;
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"removed":' + IntToStr(Removed) + '}');
End;

{..............................................................................}
{ PCB_AuditPadCenterConnected - report pads whose center has no track / via    }
{ / arc entering it (acid-pad / center-entry QA). Read-only findings.          }
{..............................................................................}
Function PCB_AuditPadCenterConnected(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter, SIter : IPCB_BoardIterator;
    Pad : IPCB_Pad;
    Obj : IPCB_Primitive;
    PX, PY, Tol : Integer;
    Connected, First : Boolean;
    Checked, Offenders : Integer;
    ItemsJson, Des : String;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Tol := MilsToCoord(2);
    Checked := 0;
    Offenders := 0;
    ItemsJson := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Pad := Iter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            If Pad.InNet Then
            Begin
                Checked := Checked + 1;
                PX := Pad.X;
                PY := Pad.Y;
                Connected := False;
                SIter := Board.SpatialIterator_Create;
                Try
                    SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject));
                    SIter.AddFilter_LayerSet(AllLayers);
                    SIter.AddFilter_Area(PX - Tol, PY - Tol, PX + Tol, PY + Tol);
                    Obj := SIter.FirstPCBObject;
                    While Obj <> Nil Do
                    Begin
                        If Obj.Net <> Nil Then
                            If Obj.Net.Name = Pad.Net.Name Then Connected := True;
                        If Connected Then Break;
                        Obj := SIter.NextPCBObject;
                    End;
                Finally
                    Board.SpatialIterator_Destroy(SIter);
                End;

                If Not Connected Then
                Begin
                    Offenders := Offenders + 1;
                    Des := '';
                    Try If Pad.Component <> Nil Then Des := Pad.Component.Name.Text; Except End;
                    If Not First Then ItemsJson := ItemsJson + ',';
                    ItemsJson := ItemsJson + JsonObj(
                        JsonStr('designator', Des) + ',' +
                        JsonStr('pad', Pad.Name) + ',' +
                        JsonStr('net', Pad.Net.Name) + ',' +
                        JsonInt('x_mils', CoordToMils(PX)) + ',' +
                        JsonInt('y_mils', CoordToMils(PY)));
                    First := False;
                End;
            End;
            Pad := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"checked":' + IntToStr(Checked) + ',"offenders":' + IntToStr(Offenders)
        + ',"items":' + JsonArr(ItemsJson) + '}');
End;

{..............................................................................}
{ PCB_AutoSizeBoardOutline - fit the board outline around all embedded-board   }
{ arrays plus a margin. Rails are drawn on a mechanical layer and converted    }
{ to the board outline.                                                        }
{..............................................................................}
Function PCB_AutoSizeBoardOutline(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Trk : IPCB_Track;
    BR : TCoordRect;
    Margin, RailW : Integer;
    MinX, MinY, MaxX, MaxY : Integer;
    MechL : TLayer;
    Found : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Margin := MilsToCoord(StrToIntDef(ExtractJsonValue(Params, 'margin_mils'), 100));
    RailW := MilsToCoord(10);
    MechL := eMechanical1;
    MinX := MAX_INT; MinY := MAX_INT; MaxX := -MAX_INT; MaxY := -MAX_INT;
    Found := False;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eEmbeddedBoardObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                BR := Obj.BoundingRectangle;
                If BR.X1 < MinX Then MinX := BR.X1;
                If BR.Y1 < MinY Then MinY := BR.Y1;
                If BR.X2 > MaxX Then MaxX := BR.X2;
                If BR.Y2 > MaxY Then MaxY := BR.Y2;
                Found := True;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_CONTENT',
            'No embedded-board arrays found to size the outline around');
        Exit;
    End;

    MinX := MinX - Margin; MinY := MinY - Margin;
    MaxX := MaxX + Margin; MaxY := MaxY + Margin;

    PCBServer.PreProcess;
    Try
        Trk := NewTrack(MinX, MinY, MaxX, MinY, RailW, MechL); Board.AddPCBObject(Trk); Trk.Selected := True;
        Trk := NewTrack(MaxX, MinY, MaxX, MaxY, RailW, MechL); Board.AddPCBObject(Trk); Trk.Selected := True;
        Trk := NewTrack(MaxX, MaxY, MinX, MaxY, RailW, MechL); Board.AddPCBObject(Trk); Trk.Selected := True;
        Trk := NewTrack(MinX, MaxY, MinX, MinY, RailW, MechL); Board.AddPCBObject(Trk); Trk.Selected := True;
        Try Board.LayerIsDisplayed[MechL] := True; Except End;
    Finally
        PCBServer.PostProcess;
    End;

    ResetParameters;
    AddStringParameter('Mode', 'BOARDOUTLINE_FROM_SEL_PRIMS');
    RunProcess('PCB:PlaceBoardOutline');

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"width_mils":' + IntToStr(CoordToMils(MaxX - MinX))
        + ',"height_mils":' + IntToStr(CoordToMils(MaxY - MinY)) + '}');
End;

{..............................................................................}
{ PCB_NormalizeVias - snap every via's diameter + hole to its dominant routing }
{ via-style rule's preferred values.                                          }
{..............................................................................}
Function PCB_NormalizeVias(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj, Prim : IPCB_Primitive;
    Via : IPCB_Via;
    Rule : IPCB_Rule;
    Matches : TInterfaceList;
    I, Checked, Changed : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Checked := 0;
    Changed := 0;

    { Collect first, THEN modify -- mutating primitives while the BoardIterator
      is walking corrupts the iterator. Collect as the base IPCB_Primitive and
      never Free the list (releasing board-interface refs faults in oleaut32). }
    Matches := CreateObject(TInterfaceList);
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eViaObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Checked := Checked + 1;
            Matches.Add(Obj);
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    PCBServer.PreProcess;
    Try
        For I := 0 To Matches.Count - 1 Do
        Begin
            Prim := Matches.Items[I];
            If Prim = Nil Then Continue;
            { Only free vias -- a via owned by a component footprint, polygon,
              or dimension is a child primitive; modifying it faults. }
            If Prim.InComponent Or Prim.InPolygon Or Prim.InDimension Then Continue;
            Try
                Via := Prim;
                Rule := Board.FindDominantRuleForObject(Via, eRule_RoutingViaStyle);
                If Rule <> Nil Then
                Begin
                    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
                        PCBM_BeginModify, c_NoEventData);
                    Via.Size := Rule.PreferedWidth;
                    Via.HoleSize := Rule.PreferedHoleWidth;
                    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
                        PCBM_EndModify, c_NoEventData);
                    Changed := Changed + 1;
                End;
            Except End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"checked":' + IntToStr(Checked) + ',"changed":' + IntToStr(Changed) + '}');
End;

{..............................................................................}
{ PCB_CopyDesignatorsToMechLayer - place a .Designator special-string copy of  }
{ every component's reference designator on a mechanical layer (assembly       }
{ drawing prep). Default eMechanical1; override with "layer".                  }
{..............................................................................}
Function PCB_CopyDesignatorsToMechLayer(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Comp : IPCB_Component;
    NewTxt : IPCB_Text;
    LayerStr : String;
    MechL : TLayer;
    Copied : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then MechL := eMechanical1
    Else MechL := ResolveLayerId(Board, LayerStr);
    If MechL = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;
    Copied := 0;

    PCBServer.PreProcess;
    Try
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Obj := Iter.FirstPCBObject;
            While Obj <> Nil Do
            Begin
                Comp := Obj;
                Try
                    NewTxt := Comp.Name.Replicate;
                    NewTxt.Layer := MechL;
                    NewTxt.Text := '.Designator';
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                        PCBM_BoardRegisteration, NewTxt.I_ObjectAddress);
                    Board.AddPCBObject(NewTxt);
                    Copied := Copied + 1;
                Except End;
                Obj := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"copied":' + IntToStr(Copied) + ',"layer":"'
        + EscapeJsonString(GetLayerString(MechL)) + '"}');
End;

{..............................................................................}
{ PCB_TrimExtendTrack - move one endpoint of a track along its own slope so it  }
{ lands at the perpendicular projection of a target point. Pure trim/extend:    }
{ the track stays collinear, only its length changes. The endpoint nearest      }
{ (from_x, from_y) is the one that moves; the opposite end is the anchor.        }
{ All coordinates in mils.                                                       }
{..............................................................................}
Function PCB_TrimExtendTrack(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Track, Best : IPCB_Track;
    FromX, FromY, ToX, ToY, Tol : Integer;
    TolC : Integer;
    BestD, D : Double;
    e1x, e1y, e2x, e2y : Double;
    MoveEnd : Integer;
    fx, fy, mx, my, tx, ty : Double;
    dxv, dyv, len2, t, nx, ny : Double;
    NewMx, NewMy, OldMxMils, OldMyMils : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    If (ExtractJsonValue(Params, 'from_x') = '') Or (ExtractJsonValue(Params, 'from_y') = '')
       Or (ExtractJsonValue(Params, 'to_x') = '') Or (ExtractJsonValue(Params, 'to_y') = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'from_x, from_y, to_x, to_y required');
        Exit;
    End;
    FromX := StrToIntDef(ExtractJsonValue(Params, 'from_x'), 0);
    FromY := StrToIntDef(ExtractJsonValue(Params, 'from_y'), 0);
    ToX := StrToIntDef(ExtractJsonValue(Params, 'to_x'), 0);
    ToY := StrToIntDef(ExtractJsonValue(Params, 'to_y'), 0);
    Tol := StrToIntDef(ExtractJsonValue(Params, 'tolerance_mils'), 5);
    TolC := MilsToCoord(Tol);

    Best := Nil;
    MoveEnd := 0;
    BestD := 1.0E30;
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Track := Iter.FirstPCBObject;
        While Track <> Nil Do
        Begin
            e1x := Track.x1; e1y := Track.y1;
            e2x := Track.x2; e2y := Track.y2;
            D := (e1x - MilsToCoord(FromX)) * (e1x - MilsToCoord(FromX))
               + (e1y - MilsToCoord(FromY)) * (e1y - MilsToCoord(FromY));
            If D < BestD Then Begin BestD := D; Best := Track; MoveEnd := 1; End;
            D := (e2x - MilsToCoord(FromX)) * (e2x - MilsToCoord(FromX))
               + (e2y - MilsToCoord(FromY)) * (e2y - MilsToCoord(FromY));
            If D < BestD Then Begin BestD := D; Best := Track; MoveEnd := 2; End;
            Track := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    If (Best = Nil) Or (Sqrt(BestD) > TolC) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'No track endpoint within tolerance of (from_x, from_y)');
        Exit;
    End;

    If MoveEnd = 1 Then
    Begin
        mx := Best.x1; my := Best.y1; fx := Best.x2; fy := Best.y2;
    End
    Else
    Begin
        mx := Best.x2; my := Best.y2; fx := Best.x1; fy := Best.y1;
    End;
    OldMxMils := CoordToMils(Round(mx));
    OldMyMils := CoordToMils(Round(my));

    dxv := mx - fx; dyv := my - fy;
    len2 := dxv * dxv + dyv * dyv;
    If len2 = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'ZERO_LENGTH', 'Target track has zero length; slope undefined');
        Exit;
    End;
    tx := MilsToCoord(ToX); ty := MilsToCoord(ToY);
    t := ((tx - fx) * dxv + (ty - fy) * dyv) / len2;
    nx := fx + t * dxv;
    ny := fy + t * dyv;
    NewMx := Round(nx);
    NewMy := Round(ny);

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Best.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);
        If MoveEnd = 1 Then Begin Best.x1 := NewMx; Best.y1 := NewMy; End
        Else Begin Best.x2 := NewMx; Best.y2 := NewMy; End;
        PCBServer.SendMessageToRobots(Best.I_ObjectAddress, c_Broadcast,
            PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;
    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"moved_end":' + IntToStr(MoveEnd)
        + ',"old_x":' + IntToStr(OldMxMils) + ',"old_y":' + IntToStr(OldMyMils)
        + ',"new_x":' + IntToStr(CoordToMils(NewMx)) + ',"new_y":' + IntToStr(CoordToMils(NewMy))
        + ',"layer":"' + EscapeJsonString(GetLayerString(Best.Layer)) + '"}');
End;

{ Squared-distance proximity test in internal coords, computed in Double to     }
{ avoid 32-bit overflow on board-scale magnitudes.                              }
Function PointsNearC(Ax, Ay, Bx, By, TolC : Integer) : Boolean;
Var dx, dy, tt : Double;
Begin
    dx := Ax - Bx; dy := Ay - By; tt := TolC;
    Result := (dx * dx + dy * dy) <= (tt * tt);
End;

{ Net name of a track, '' when it carries no net. }
Function TrackNetNm(T : IPCB_Track) : String;
Begin
    Result := '';
    Try If T.Net <> Nil Then Result := T.Net.Name; Except End;
End;

{..............................................................................}
{ PCB_CleanupTracks - tidy stray track geometry. Two passes, selectable via     }
{ 'mode' (slivers | merge | both; default slivers):                             }
{   slivers - delete tracks whose length is below min_length_mils (default 1).  }
{   merge   - join two collinear, same-layer, same-width, same-net tracks that   }
{             meet end-to-end into one, ONLY when the shared point is a clean    }
{             degree-2 junction (exactly those two track ends there, with no     }
{             via / pad / arc / third track). The guard makes the merge safe to  }
{             run on routed copper without breaking connectivity.                }
{..............................................................................}
Function PCB_CleanupTracks(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter, Iter2, SIter : IPCB_BoardIterator;
    A, B, MergeA, MergeB : IPCB_Track;
    SObj : IPCB_Primitive;
    Mode : String;
    MinLenMils, TolC, JTolC : Integer;
    SliverDeleted, Merged, Guard : Integer;
    DidWork : Boolean;
    a1x, a1y, a2x, a2y, b1x, b1y, b2x, b2y : Double;
    SegLen : Double;
    Sx, Sy, FarAx, FarAy, FarBx, FarBy : Integer;
    sax, say, sbx, sby, crossv, dotv, lna, lnb : Double;
    TrackCnt, BlockerCnt : Integer;
    NewT : IPCB_Track;
    NewLayer : TLayer;
    NewWidth : Integer;
    NewNet : IPCB_Net;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    Mode := ExtractJsonValue(Params, 'mode');
    If Mode = '' Then Mode := 'slivers';
    MinLenMils := StrToIntDef(ExtractJsonValue(Params, 'min_length_mils'), 1);
    TolC := MilsToCoord(2);
    JTolC := MilsToCoord(2);
    SliverDeleted := 0;
    Merged := 0;

    { Sliver pass: find-one-delete-restart so the iterator is never stale. }
    If (Mode = 'slivers') Or (Mode = 'both') Then
    Begin
        DidWork := True;
        While DidWork Do
        Begin
            DidWork := False;
            A := Nil;
            Iter := Board.BoardIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
                Iter.AddFilter_LayerSet(AllLayers);
                Iter.AddFilter_Method(eProcessAll);
                B := Iter.FirstPCBObject;
                While (B <> Nil) And (A = Nil) Do
                Begin
                    { Never delete a child primitive of a component footprint
                      (silk ticks, courtyard lines), polygon hatch, or
                      dimension -- only free routed copper. }
                    If (Not B.InComponent) And (Not B.InPolygon) And (Not B.InDimension) Then
                    Begin
                        a1x := B.x1; a1y := B.y1; a2x := B.x2; a2y := B.y2;
                        SegLen := Sqrt((a2x - a1x) * (a2x - a1x) + (a2y - a1y) * (a2y - a1y));
                        If SegLen < MilsToCoord(MinLenMils) Then A := B;
                    End;
                    B := Iter.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iter);
            End;
            If A <> Nil Then
            Begin
                PCBServer.PreProcess;
                Try
                    Board.RemovePCBObject(A);
                Finally
                    PCBServer.PostProcess;
                End;
                SliverDeleted := SliverDeleted + 1;
                DidWork := True;
            End;
        End;
    End;

    { Merge pass: find-one-merge-restart. The search runs entirely inside the    }
    { iterators using base members + typed x1/x2; the board mutation is deferred  }
    { until both iterators are destroyed so NextPCBObject never sees a stale set. }
    If (Mode = 'merge') Or (Mode = 'both') Then
    Begin
        DidWork := True;
        Guard := 0;
        While DidWork And (Guard < 20000) Do
        Begin
            DidWork := False;
            Guard := Guard + 1;
            MergeA := Nil; MergeB := Nil;
            FarAx := 0; FarAy := 0; FarBx := 0; FarBy := 0;

            Iter := Board.BoardIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
                Iter.AddFilter_LayerSet(AllLayers);
                Iter.AddFilter_Method(eProcessAll);
                A := Iter.FirstPCBObject;
                While (A <> Nil) And (MergeA = Nil) Do
                Begin
                    Iter2 := Board.BoardIterator_Create;
                    Try
                        Iter2.AddFilter_ObjectSet(MkSet(eTrackObject));
                        Iter2.AddFilter_LayerSet(AllLayers);
                        Iter2.AddFilter_Method(eProcessAll);
                        B := Iter2.FirstPCBObject;
                        While (B <> Nil) And (MergeA = Nil) Do
                        Begin
                            If (A.I_ObjectAddress <> B.I_ObjectAddress)
                               And (A.Layer = B.Layer) And (A.Width = B.Width)
                               And (TrackNetNm(A) = TrackNetNm(B))
                               { only merge free routed copper -- never child
                                 primitives of footprints/polygons/dimensions }
                               And (Not A.InComponent) And (Not A.InPolygon) And (Not A.InDimension)
                               And (Not B.InComponent) And (Not B.InPolygon) And (Not B.InDimension) Then
                            Begin
                                a1x := A.x1; a1y := A.y1; a2x := A.x2; a2y := A.y2;
                                b1x := B.x1; b1y := B.y1; b2x := B.x2; b2y := B.y2;
                                { locate the single shared endpoint S; A2/B2 are the far ends }
                                If PointsNearC(Round(a1x), Round(a1y), Round(b1x), Round(b1y), TolC) Then
                                Begin Sx := Round(a1x); Sy := Round(a1y); FarAx := Round(a2x); FarAy := Round(a2y); FarBx := Round(b2x); FarBy := Round(b2y); End
                                Else If PointsNearC(Round(a1x), Round(a1y), Round(b2x), Round(b2y), TolC) Then
                                Begin Sx := Round(a1x); Sy := Round(a1y); FarAx := Round(a2x); FarAy := Round(a2y); FarBx := Round(b1x); FarBy := Round(b1y); End
                                Else If PointsNearC(Round(a2x), Round(a2y), Round(b1x), Round(b1y), TolC) Then
                                Begin Sx := Round(a2x); Sy := Round(a2y); FarAx := Round(a1x); FarAy := Round(a1y); FarBx := Round(b2x); FarBy := Round(b2y); End
                                Else If PointsNearC(Round(a2x), Round(a2y), Round(b2x), Round(b2y), TolC) Then
                                Begin Sx := Round(a2x); Sy := Round(a2y); FarAx := Round(a1x); FarAy := Round(a1y); FarBx := Round(b1x); FarBy := Round(b1y); End
                                Else
                                    Sx := -2147483647;  { sentinel: no shared endpoint }

                                If Sx <> -2147483647 Then
                                Begin
                                    { collinear continuation: far ends point opposite directions through S }
                                    sax := FarAx - Sx; say := FarAy - Sy;
                                    sbx := FarBx - Sx; sby := FarBy - Sy;
                                    lna := Sqrt(sax * sax + say * say);
                                    lnb := Sqrt(sbx * sbx + sby * sby);
                                    If (lna > 0) And (lnb > 0) Then
                                    Begin
                                        crossv := Abs(sax * sby - say * sbx) / (lna * lnb);
                                        dotv := sax * sbx + say * sby;
                                        If (crossv < 0.02) And (dotv < 0) Then
                                        Begin
                                            { degree-2 junction guard at S using base members only }
                                            TrackCnt := 0; BlockerCnt := 0;
                                            SIter := Board.SpatialIterator_Create;
                                            Try
                                                SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, ePadObject));
                                                SIter.AddFilter_LayerSet(AllLayers);
                                                SIter.AddFilter_Area(Sx - JTolC, Sy - JTolC, Sx + JTolC, Sy + JTolC);
                                                SObj := SIter.FirstPCBObject;
                                                While SObj <> Nil Do
                                                Begin
                                                    If SObj.ObjectId = eTrackObject Then TrackCnt := TrackCnt + 1
                                                    Else BlockerCnt := BlockerCnt + 1;
                                                    SObj := SIter.NextPCBObject;
                                                End;
                                            Finally
                                                Board.SpatialIterator_Destroy(SIter);
                                            End;

                                            If (TrackCnt = 2) And (BlockerCnt = 0) Then
                                            Begin
                                                MergeA := A; MergeB := B;
                                                NewLayer := A.Layer; NewWidth := A.Width; NewNet := A.Net;
                                            End;
                                        End;
                                    End;
                                End;
                            End;
                            B := Iter2.NextPCBObject;
                        End;
                    Finally
                        Board.BoardIterator_Destroy(Iter2);
                    End;
                    A := Iter.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iter);
            End;

            If MergeA <> Nil Then
            Begin
                PCBServer.PreProcess;
                Try
                    NewT := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                    NewT.Layer := NewLayer;
                    NewT.Width := NewWidth;
                    NewT.x1 := FarAx; NewT.y1 := FarAy;
                    NewT.x2 := FarBx; NewT.y2 := FarBy;
                    If NewNet <> Nil Then NewT.Net := NewNet;
                    Board.AddPCBObject(NewT);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                        PCBM_BoardRegisteration, NewT.I_ObjectAddress);
                    Board.RemovePCBObject(MergeA);
                    Board.RemovePCBObject(MergeB);
                Finally
                    PCBServer.PostProcess;
                End;
                Merged := Merged + 1;
                DidWork := True;
            End;
        End;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"slivers_deleted":' + IntToStr(SliverDeleted)
        + ',"merged":' + IntToStr(Merged)
        + ',"mode":"' + EscapeJsonString(Mode) + '"}');
End;

{..............................................................................}
{ PCB_PlaceThievingPads - fill bare copper area with a grid of small isolated   }
{ pads (thieving) so plating current spreads evenly. A grid point is skipped     }
{ whenever ANY existing primitive (track / arc / via / pad / fill / region /     }
{ polygon / component) sits within half-pad + clearance of it, so the pads only  }
{ land in genuinely empty regions. Pads carry no net.                            }
{..............................................................................}
Function PCB_PlaceThievingPads(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Outline : IPCB_BoardOutline;
    BR : TCoordRect;
    SIter : IPCB_BoardIterator;
    SObj : IPCB_Primitive;
    LayerStr : String;
    Lyr : TLayer;
    PadSize, Pitch, Clearance, Margin : Integer;
    PadC, PitchC, ClearC, MarginC, HalfClr : Integer;
    Gx, Gy, Placed, Scanned, MaxPads, MaxScan : Integer;
    Blocked : Boolean;
    NewPad : IPCB_Pad;
Begin
    Board := GetPCBBoardAnywhere;
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
    Try Outline.Invalidate; Outline.Rebuild; Outline.Validate; Except End;
    BR := Outline.BoundingRectangle;

    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then Lyr := eTopLayer
    Else Lyr := ResolveLayerId(Board, LayerStr);
    If Lyr = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;
    PadSize := StrToIntDef(ExtractJsonValue(Params, 'pad_size_mils'), 20);
    Pitch := StrToIntDef(ExtractJsonValue(Params, 'pitch_mils'), 50);
    Clearance := StrToIntDef(ExtractJsonValue(Params, 'clearance_mils'), 15);
    Margin := StrToIntDef(ExtractJsonValue(Params, 'margin_mils'), 100);
    If Pitch < 1 Then Pitch := 50;

    PadC := MilsToCoord(PadSize);
    PitchC := MilsToCoord(Pitch);
    ClearC := MilsToCoord(Clearance);
    MarginC := MilsToCoord(Margin);
    HalfClr := (PadC Div 2) + ClearC;

    Placed := 0; Scanned := 0;
    MaxPads := 5000; MaxScan := 200000;

    PCBServer.PreProcess;
    Try
        Gy := BR.Bottom + MarginC;
        While (Gy <= BR.Top - MarginC) And (Placed < MaxPads) And (Scanned < MaxScan) Do
        Begin
            Gx := BR.Left + MarginC;
            While (Gx <= BR.Right - MarginC) And (Placed < MaxPads) And (Scanned < MaxScan) Do
            Begin
                Scanned := Scanned + 1;
                Blocked := False;
                SIter := Board.SpatialIterator_Create;
                Try
                    SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject,
                        ePadObject, eFillObject, eRegionObject, ePolyObject, eComponentObject));
                    SIter.AddFilter_LayerSet(AllLayers);
                    SIter.AddFilter_Area(Gx - HalfClr, Gy - HalfClr, Gx + HalfClr, Gy + HalfClr);
                    SObj := SIter.FirstPCBObject;
                    If SObj <> Nil Then Blocked := True;
                Finally
                    Board.SpatialIterator_Destroy(SIter);
                End;

                If Not Blocked Then
                Begin
                    NewPad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
                    NewPad.Layer := Lyr;
                    NewPad.X := Gx;
                    NewPad.Y := Gy;
                    NewPad.TopXSize := PadC;
                    NewPad.TopYSize := PadC;
                    NewPad.TopShape := eRounded;
                    Board.AddPCBObject(NewPad);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                        PCBM_BoardRegisteration, NewPad.I_ObjectAddress);
                    Placed := Placed + 1;
                End;
                Gx := Gx + PitchC;
            End;
            Gy := Gy + PitchC;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"scanned":' + IntToStr(Scanned)
        + ',"layer":"' + EscapeJsonString(GetLayerString(Lyr)) + '"}');
End;

{..............................................................................}
{ PCB_MoveTracksToLayer - move every track of one net onto a target signal      }
{ layer, then drop a via wherever the net still needs to reach a single-layer   }
{ (SMD) pad that is NOT on the target layer. Because ALL the net's tracks move,  }
{ a same-net SMD pad off the target layer can only connect through a via, so a   }
{ via at that pad's centre is exactly what is required. Multilayer (through-     }
{ hole) pads need no via.                                                        }
{..............................................................................}
Function PCB_MoveTracksToLayer(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Trk : IPCB_Track;
    Pad : IPCB_Pad;
    Via : IPCB_Via;
    NetStr, LayerStr : String;
    TargetNet : IPCB_Net;
    TargetLayer : TLayer;
    ViaSize, ViaHole, Moved, ViasAdded : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetStr := ExtractJsonValue(Params, 'net_name');
    LayerStr := ExtractJsonValue(Params, 'target_layer');
    If (NetStr = '') Or (LayerStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'net_name and target_layer required');
        Exit;
    End;
    TargetNet := FindNetByName(Board, NetStr);
    If TargetNet = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Net not found: ' + NetStr);
        Exit;
    End;
    TargetLayer := ResolveLayerId(Board, LayerStr);
    If TargetLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown target_layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;
    ViaSize := StrToIntDef(ExtractJsonValue(Params, 'via_size_mils'), 50);
    ViaHole := StrToIntDef(ExtractJsonValue(Params, 'via_hole_mils'), 28);
    Moved := 0; ViasAdded := 0;

    PCBServer.PreProcess;
    Try
        { move pass: layer change does not alter the iterated set, so in-place }
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Trk := Iter.FirstPCBObject;
            While Trk <> Nil Do
            Begin
                If (TrackNetNm(Trk) = NetStr) And (Trk.Layer <> TargetLayer) Then
                Begin
                    PCBServer.SendMessageToRobots(Trk.I_ObjectAddress, c_Broadcast,
                        PCBM_BeginModify, c_NoEventData);
                    Trk.Layer := TargetLayer;
                    PCBServer.SendMessageToRobots(Trk.I_ObjectAddress, c_Broadcast,
                        PCBM_EndModify, c_NoEventData);
                    Moved := Moved + 1;
                End;
                Trk := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;

        { via pass: same-net SMD pad off the target layer now needs a via }
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePadObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Pad := Iter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                If Pad.InNet Then
                    If Pad.Net.Name = NetStr Then
                        If (Pad.Layer <> eMultiLayer) And (Pad.Layer <> TargetLayer) Then
                        Begin
                            Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
                            Via.x := Pad.X;
                            Via.y := Pad.Y;
                            Via.Size := MilsToCoord(ViaSize);
                            Via.HoleSize := MilsToCoord(ViaHole);
                            Via.LowLayer := eTopLayer;
                            Via.HighLayer := eBottomLayer;
                            Via.Net := TargetNet;
                            Board.AddPCBObject(Via);
                            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                                PCBM_BoardRegisteration, Via.I_ObjectAddress);
                            ViasAdded := ViasAdded + 1;
                        End;
                Pad := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(Board.FileName);
    Result := BuildSuccessResponse(RequestId,
        '{"net":"' + EscapeJsonString(NetStr) + '","target_layer":"'
        + EscapeJsonString(GetLayerString(TargetLayer)) + '","moved":'
        + IntToStr(Moved) + ',"vias_added":' + IntToStr(ViasAdded) + '}');
End;

{..............................................................................}
{ PCB_BevelPolygonCorners - chamfer the corners of a copper polygon. Each sharp  }
{ vertex is replaced by two points set back along its two edges by bevel_mils    }
{ (auto-clamped so adjacent bevels never overlap), turning every corner into a   }
{ straight 45-style cut. Only line-segment polygons are handled; a polygon with  }
{ any arc segment is left untouched. Target is the Nth polygon (index, default   }
{ 0) optionally filtered to net_name. The polygon is repoured afterwards.        }
{..............................................................................}
Function PCB_BevelPolygonCorners(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Poly, Target : IPCB_Polygon;
    OrigList : TStringList;
    NetFilter, S : String;
    Idx, MatchCount, N, I, K, P, dC, BevelMils : Integer;
    vpx, vpy, vix, viy, vnx, vny, ax, ay, bx, by : Integer;
    upx, upy, unx, uny, lenp, lenn, dd : Double;
    Seg : TPolySegment;
    HasArc : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetFilter := ExtractJsonValue(Params, 'net_name');
    Idx := StrToIntDef(ExtractJsonValue(Params, 'index'), 0);
    BevelMils := StrToIntDef(ExtractJsonValue(Params, 'bevel_mils'), 25);
    dC := MilsToCoord(BevelMils);

    Target := Nil;
    MatchCount := 0;
    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePolyObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Poly := Iter.FirstPCBObject;
        While (Poly <> Nil) And (Target = Nil) Do
        Begin
            If (NetFilter = '') Or ((Poly.Net <> Nil) And (Poly.Net.Name = NetFilter)) Then
            Begin
                If MatchCount = Idx Then Target := Poly;
                MatchCount := MatchCount + 1;
            End;
            Poly := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'No matching polygon at that index');
        Exit;
    End;

    N := 0;
    Try N := Target.PointCount; Except End;
    If N < 3 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_POLYGON', 'Polygon has fewer than 3 vertices');
        Exit;
    End;

    { snapshot original vertices, refusing arc segments }
    OrigList := TStringList.Create;
    HasArc := False;
    For I := 0 To N - 1 Do
    Begin
        If Target.Segments[I].Kind <> ePolySegmentLine Then HasArc := True;
        OrigList.Add(IntToStr(Target.Segments[I].vx) + '|' + IntToStr(Target.Segments[I].vy));
    End;
    If HasArc Then
    Begin
        OrigList.Free;
        Result := BuildErrorResponse(RequestId, 'HAS_ARC', 'Polygon has arc segments; bevel only handles straight outlines');
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Target.PointCount := 2 * N;
        { Materialize the record from an existing segment before writing its
          fields -- `Seg := TPolySegment` does not initialize a record local,
          and field writes on an unmaterialized record raise "Undeclared
          identifier" at runtime. }
        Seg := Target.Segments[0];
        Seg.Kind := ePolySegmentLine;
        For I := 0 To N - 1 Do
        Begin
            K := I - 1; If K < 0 Then K := N - 1;
            S := OrigList[K];   P := Pos('|', S);
            vpx := StrToIntDef(Copy(S, 1, P - 1), 0); vpy := StrToIntDef(Copy(S, P + 1, Length(S)), 0);
            S := OrigList[I];   P := Pos('|', S);
            vix := StrToIntDef(Copy(S, 1, P - 1), 0); viy := StrToIntDef(Copy(S, P + 1, Length(S)), 0);
            K := I + 1; If K >= N Then K := 0;
            S := OrigList[K];   P := Pos('|', S);
            vnx := StrToIntDef(Copy(S, 1, P - 1), 0); vny := StrToIntDef(Copy(S, P + 1, Length(S)), 0);

            upx := vpx - vix; upy := vpy - viy; lenp := Sqrt(upx * upx + upy * upy);
            unx := vnx - vix; uny := vny - viy; lenn := Sqrt(unx * unx + uny * uny);
            dd := dC;
            If lenp > 0 Then If dd > 0.45 * lenp Then dd := 0.45 * lenp;
            If lenn > 0 Then If dd > 0.45 * lenn Then dd := 0.45 * lenn;
            If (lenp > 0) And (lenn > 0) Then
            Begin
                ax := Round(vix + dd * upx / lenp); ay := Round(viy + dd * upy / lenp);
                bx := Round(vix + dd * unx / lenn); by := Round(viy + dd * uny / lenn);
            End
            Else
            Begin
                ax := vix; ay := viy; bx := vix; by := viy;
            End;

            Seg.vx := ax; Seg.vy := ay; Target.Segments[2 * I] := Seg;
            Seg.vx := bx; Seg.vy := by; Target.Segments[2 * I + 1] := Seg;
        End;

        Target.Invalidate;
        Target.Rebuild;
        Target.Validate;
    Finally
        PCBServer.PostProcess;
    End;
    OrigList.Free;

    ResetParameters;
    RunProcess('PCB:RepourAllPolygons');
    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"beveled":true,"index":' + IntToStr(Idx)
        + ',"orig_vertices":' + IntToStr(N)
        + ',"new_vertices":' + IntToStr(2 * N)
        + ',"bevel_mils":' + IntToStr(BevelMils) + '}');
End;

{..............................................................................}
{ PCB_CreateNetsFromList - Create net objects for names not already on the   }
{ board. First leg of the netlist-driven SCH->PCB bridge (ECO is not          }
{ scriptable): footprints go down via place_components, nets are created      }
{ here from the compiled netlist, then PCB_BindPadNets attaches each pad.     }
{ Params: nets ('|'-separated net names; duplicates collapse to one net).     }
{ Returns "created" and "existing" counts.                                     }
{..............................................................................}

Function PCB_CreateNetsFromList(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Net : IPCB_Net;
    Existing : TStringList;
    NetsStr, NetName, Remaining : String;
    PipePos, CreatedCount, ExistingCount : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    NetsStr := ExtractJsonValue(Params, 'nets');
    If NetsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'nets parameter required');
        Exit;
    End;

    { One board scan for the names already present; FindNetByName per         }
    { requested net would rescan the whole board N times.                      }
    Existing := TStringList.Create;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eNetObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Net := Iter.FirstPCBObject;
    While Net <> Nil Do
    Begin
        NetName := '';
        Try NetName := Net.Name; Except End;
        If (NetName <> '') And (Existing.IndexOf(NetName) < 0) Then
            Existing.Add(NetName);
        Net := Iter.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iter);

    CreatedCount := 0;
    ExistingCount := 0;

    PCBServer.PreProcess;
    Try
        Remaining := NetsStr;
        While Remaining <> '' Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos > 0 Then
            Begin
                NetName := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End
            Else
            Begin
                NetName := Remaining;
                Remaining := '';
            End;
            If NetName = '' Then Continue;

            If Existing.IndexOf(NetName) >= 0 Then
            Begin
                ExistingCount := ExistingCount + 1;
                Continue;
            End;

            Net := PCBServer.PCBObjectFactory(eNetObject, eNoDimension, eCreate_Default);
            If Net = Nil Then Continue;
            Net.Name := NetName;
            Board.AddPCBObject(Net);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                PCBM_BoardRegisteration, Net.I_ObjectAddress);
            { Track the new name so a duplicate later in the list counts as   }
            { existing instead of creating a second net object.               }
            Existing.Add(NetName);
            CreatedCount := CreatedCount + 1;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Existing.Free;
    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"created":' + IntToStr(CreatedCount)
        + ',"existing":' + IntToStr(ExistingCount) + '}');
End;

{ Find a placed component by designator with a board-iterator Name.Text scan. }
{ Matches the field PCB_PlaceComponents stamps (Comp.Name.Text), so parts     }
{ placed earlier in the same bridge session resolve too. Returns Nil if no    }
{ component carries the designator.                                            }
Function FindPCBComponentByDesignator(Board : IPCB_Board; Desig : String) : IPCB_Component;
Var
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
    NameStr : String;
Begin
    Result := Nil;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Comp := Iter.FirstPCBObject;
    While Comp <> Nil Do
    Begin
        NameStr := '';
        Try NameStr := Comp.Name.Text; Except End;
        If NameStr = Desig Then
        Begin
            Result := Comp;
            Break;
        End;
        Comp := Iter.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iter);
End;

{..............................................................................}
{ PCB_BindPadNets - Attach component pads to existing board nets. Second leg  }
{ of the netlist-driven SCH->PCB bridge: run PCB_CreateNetsFromList first,    }
{ then bind every (designator, pin, net) row of the compiled netlist here.    }
{ Params: bindings ('~~'-separated ops, each 'designator=U1;pin=3;net=VCC',   }
{ the NextBatchOp/GetBatchField grammar).                                      }
{ Collect-then-modify per binding: the pad is located with a group iterator,  }
{ the iterator destroyed, THEN the net written (modifying while the iterator  }
{ walks corrupts it). Component and net lookups are cached: one designator    }
{ scan per component (rows arrive grouped per part) and one FindNetByName     }
{ per distinct net name.                                                       }
{ Returns "bound"/"failed" counts plus missing_components / missing_pads /    }
{ missing_nets name lists, each capped at 50 entries.                         }
{..............................................................................}

Function PCB_BindPadNets(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Comp : IPCB_Component;
    GrpIter : IPCB_GroupIterator;
    Pad, PadFound : IPCB_Pad;
    Net : IPCB_Net;
    NetNames, MissingComps, MissingPads, MissingNets : TStringList;
    NetRefs : TInterfaceList;
    BindingsStr, Remaining, Op : String;
    Desig, PinStr, NetName, PadName, LastDesig : String;
    CompsJson, PadsJson, NetsJson : String;
    LastResolved : Boolean;
    Bound, Failed, NetIdx, I : Integer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    BindingsStr := ExtractJsonValue(Params, 'bindings');
    If BindingsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'bindings parameter required');
        Exit;
    End;

    NetNames := TStringList.Create;
    MissingComps := TStringList.Create;
    MissingPads := TStringList.Create;
    MissingNets := TStringList.Create;
    NetRefs := CreateObject(TInterfaceList);

    Comp := Nil;
    LastDesig := '';
    LastResolved := False;
    Bound := 0;
    Failed := 0;

    PCBServer.PreProcess;
    Try
        Remaining := BindingsStr;
        While Length(Remaining) > 0 Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Continue;

            Desig := GetBatchField(Op, 'designator');
            PinStr := GetBatchField(Op, 'pin');
            NetName := GetBatchField(Op, 'net');
            If (Desig = '') Or (PinStr = '') Or (NetName = '') Then
            Begin
                Failed := Failed + 1;
                Continue;
            End;

            { Component cache: re-resolve only when the designator changes.   }
            { A failed resolution is cached too, so a missing part with 40    }
            { pins costs one scan, not 40.                                    }
            If Desig <> LastDesig Then
            Begin
                Comp := FindPCBComponentByDesignator(Board, Desig);
                LastDesig := Desig;
                LastResolved := (Comp <> Nil);
            End;
            If Not LastResolved Then
            Begin
                Failed := Failed + 1;
                If MissingComps.IndexOf(Desig) < 0 Then MissingComps.Add(Desig);
                Continue;
            End;

            { Net cache: one FindNetByName board scan per distinct name,      }
            { with a negative cache so absent nets don't rescan either.       }
            Net := Nil;
            NetIdx := NetNames.IndexOf(NetName);
            If NetIdx >= 0 Then
                Net := NetRefs.Items[NetIdx]
            Else If MissingNets.IndexOf(NetName) < 0 Then
            Begin
                Net := FindNetByName(Board, NetName);
                If Net <> Nil Then
                Begin
                    NetNames.Add(NetName);
                    NetRefs.Add(Net);
                End
                Else
                    MissingNets.Add(NetName);
            End;
            If Net = Nil Then
            Begin
                Failed := Failed + 1;
                Continue;
            End;

            { Locate the pad, destroy the iterator, then write the net.      }
            PadFound := Nil;
            GrpIter := Comp.GroupIterator_Create;
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                PadName := '';
                Try PadName := Pad.Name; Except End;
                If PadName = PinStr Then
                Begin
                    PadFound := Pad;
                    Break;
                End;
                Pad := GrpIter.NextPCBObject;
            End;
            Comp.GroupIterator_Destroy(GrpIter);

            If PadFound = Nil Then
            Begin
                Failed := Failed + 1;
                If MissingPads.IndexOf(Desig + '.' + PinStr) < 0 Then
                    MissingPads.Add(Desig + '.' + PinStr);
                Continue;
            End;

            PCBServer.SendMessageToRobots(PadFound.I_ObjectAddress, c_Broadcast,
                PCBM_BeginModify, c_NoEventData);
            PadFound.Net := Net;
            PCBServer.SendMessageToRobots(PadFound.I_ObjectAddress, c_Broadcast,
                PCBM_EndModify, c_NoEventData);
            Bound := Bound + 1;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    CompsJson := '';
    For I := 0 To MissingComps.Count - 1 Do
    Begin
        If I >= 50 Then Break;
        If CompsJson <> '' Then CompsJson := CompsJson + ',';
        CompsJson := CompsJson + '"' + EscapeJsonString(MissingComps[I]) + '"';
    End;
    PadsJson := '';
    For I := 0 To MissingPads.Count - 1 Do
    Begin
        If I >= 50 Then Break;
        If PadsJson <> '' Then PadsJson := PadsJson + ',';
        PadsJson := PadsJson + '"' + EscapeJsonString(MissingPads[I]) + '"';
    End;
    NetsJson := '';
    For I := 0 To MissingNets.Count - 1 Do
    Begin
        If I >= 50 Then Break;
        If NetsJson <> '' Then NetsJson := NetsJson + ',';
        NetsJson := NetsJson + '"' + EscapeJsonString(MissingNets[I]) + '"';
    End;

    NetNames.Free;
    MissingComps.Free;
    MissingPads.Free;
    MissingNets.Free;
    { No NetRefs.Free -- releasing a TInterfaceList of board interface refs   }
    { faults in oleaut32; leave it to the script host.                        }

    SaveDocByPath(Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"bound":' + IntToStr(Bound)
        + ',"failed":' + IntToStr(Failed)
        + ',"missing_components":[' + CompsJson + ']'
        + ',"missing_pads":[' + PadsJson + ']'
        + ',"missing_nets":[' + NetsJson + ']}');
End;

{..............................................................................}
{ HandlePCBCommand - Route PCB actions to handlers                            }
{..............................................................................}

Function HandlePCBCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'get_nets':                Result := PCB_GetNets(Params, RequestId);
        'create_nets_from_list':   Result := PCB_CreateNetsFromList(Params, RequestId);
        'bind_pad_nets':           Result := PCB_BindPadNets(Params, RequestId);
        'delete_nets':             Result := PCB_DeleteNets(Params, RequestId);
        'get_net_classes':         Result := PCB_GetNetClasses(Params, RequestId);
        'create_net_class':        Result := PCB_CreateNetClass(Params, RequestId);
        'get_design_rules':        Result := PCB_GetDesignRules(Params, RequestId);
        'get_rule_properties':     Result := PCB_GetRuleProperties(Params, RequestId);
        'set_rule_properties':     Result := PCB_SetRuleProperties(Params, RequestId);
        'set_rules_enabled':       Result := PCB_SetRulesEnabled(Params, RequestId);
        'run_drc':                 Result := PCB_RunDRC(Params, RequestId);
        'get_components':          Result := PCB_GetComponents(Params, RequestId);
        'move_component':          Result := PCB_MoveComponent(Params, RequestId);
        'batch_move_components':   Result := PCB_BatchMoveComponents(Params, RequestId);
        'copy_component_placement': Result := PCB_CopyComponentPlacement(Params, RequestId);
        'replicate_layout':        Result := PCB_ReplicateLayout(Params, RequestId);
        'filter_variant_components': Result := PCB_FilterVariantComponents(Params, RequestId);
        'renumber_pads':           Result := PCB_RenumberPads(Params, RequestId);
        'copy_tracks_radial':      Result := PCB_CopyTracksRadial(Params, RequestId);
        'scale':                   Result := PCB_Scale(Params, RequestId);
        'set_text_visibility':     Result := PCB_SetTextVisibility(Params, RequestId);
        'lock_net_routing':        Result := PCB_LockNetRouting(Params, RequestId);
        'place_stitching_vias':    Result := PCB_PlaceStitchingVias(Params, RequestId);
        'get_fab_stats':           Result := PCB_GetFabStats(Params, RequestId);
        'clear_source_footprint_library': Result := PCB_ClearSourceFootprintLibrary(Params, RequestId);
        'get_differential_pairs':  Result := PCB_GetDifferentialPairs(Params, RequestId);
        'make_paste_grid':         Result := PCB_MakePasteGrid(Params, RequestId);
        'apply_dnp_paste_exclusion': Result := PCB_ApplyDnpPasteExclusion(Params, RequestId);
        'add_testpoints_for_net_class': Result := PCB_AddTestpointsForNetClass(Params, RequestId);
        'check_placement_collision': Result := PCB_CheckPlacementCollision(Params, RequestId);
        'get_trace_lengths':       Result := PCB_GetTraceLengths(Params, RequestId);
        'get_layer_stackup':       Result := PCB_GetLayerStackup(Params, RequestId);
        'add_layer':               Result := PCB_AddLayer(Params, RequestId);
        'remove_layer':            Result := PCB_RemoveLayer(Params, RequestId);
        'modify_layer':            Result := PCB_ModifyLayer(Params, RequestId);
        'set_plane_net':           Result := PCB_SetPlaneNet(Params, RequestId);
        'set_mech_layer_kind':     Result := PCB_SetMechLayerKind(Params, RequestId);
        'get_layer_display':       Result := PCB_GetLayerDisplay(Params, RequestId);
        'set_layer_color':         Result := PCB_SetLayerColor(Params, RequestId);
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
        'calc_polygon_area':       Result := PCB_CalcPolygonArea(Params, RequestId);
        'set_via_soldermask_relief': Result := PCB_SetViaSoldermaskRelief(Params, RequestId);
        'get_mech_layer_names':    Result := PCB_GetMechLayerNames(Params, RequestId);
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
        'place_component':         Result := PCB_PlaceComponent(Params, RequestId);
        'place_components':        Result := PCB_PlaceComponents(Params, RequestId);
        'focus_board':             Result := PCB_FocusBoard(Params, RequestId);
        'place_angular_dimension': Result := PCB_PlaceAngularDimension(Params, RequestId);
        'place_radial_dimension':  Result := PCB_PlaceRadialDimension(Params, RequestId);
        'place_embedded_board':    Result := PCB_PlaceEmbeddedBoard(Params, RequestId);
        'fillet_corners':          Result := PCB_FilletCorners(Params, RequestId);
        'import_placement':        Result := PCB_ImportPlacement(Params, RequestId);
        'teardrops':               Result := PCB_Teardrops(Params, RequestId);
        'autoplace_silkscreen':    Result := PCB_AutoplaceSilkscreen(Params, RequestId);
        'tune_length':             Result := PCB_TuneLength(Params, RequestId);
        'panelize':                Result := PCB_Panelize(Params, RequestId);
        'delete_invalid_objects':  Result := PCB_DeleteInvalidObjects(Params, RequestId);
        'audit_pad_center_connected': Result := PCB_AuditPadCenterConnected(Params, RequestId);
        'auto_size_board_outline': Result := PCB_AutoSizeBoardOutline(Params, RequestId);
        'normalize_vias':          Result := PCB_NormalizeVias(Params, RequestId);
        'copy_designators_to_mech': Result := PCB_CopyDesignatorsToMechLayer(Params, RequestId);
        'trim_extend_track':       Result := PCB_TrimExtendTrack(Params, RequestId);
        'cleanup_tracks':          Result := PCB_CleanupTracks(Params, RequestId);
        'place_thieving_pads':     Result := PCB_PlaceThievingPads(Params, RequestId);
        'move_tracks_to_layer':    Result := PCB_MoveTracksToLayer(Params, RequestId);
        'bevel_polygon_corners':   Result := PCB_BevelPolygonCorners(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown PCB action: ' + Action);
    End;
End;
