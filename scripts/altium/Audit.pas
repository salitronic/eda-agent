{ SPDX-License-Identifier: Apache-2.0                                          }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>              }
{..............................................................................}
{ Audit.pas - Design-lint check handlers. Each Audit_* function is a          }
{ read-only validator that walks the focused project and returns a JSON       }
{ violation list. Mutations belong in Generic.pas / PCB.pas; this file is    }
{ pure analysis. Bundled as the `audit.*` command category in Dispatcher.    }
{..............................................................................}


{ Map a designator prefix to one of the supported parametric-validator      }
{ class buckets. Returns '' for prefixes we don't know how to validate so    }
{ the caller skips them (e.g. mechanical parts MH*, mounting holes, etc).   }
Function ComponentClassByDes(Designator : String) : String;
Var
    Prefix : String;
Begin
    Result := '';
    If Designator = '' Then Exit;
    Prefix := UpperCase(Copy(Designator, 1, 1));
    If Prefix = 'C' Then Result := 'capacitor'
    Else If Prefix = 'R' Then Result := 'resistor'
    Else If Prefix = 'L' Then Result := 'inductor'
    Else If Prefix = 'U' Then Result := 'ic';
End;


{ Cheap "looks like a number-with-unit" check used by the visibility-rule  }
{ validators. The original VBS uses RegExp which DelphiScript doesn't       }
{ expose; the rule is loose anyway -- starts with digit + ends with the     }
{ unit letter is enough to distinguish "10uF" / "5V" / "100kR" from a free }
{ text comment, with negligible false-positive risk for the four classes   }
{ we check.                                                                  }
Function ParamLooksLikeUnit(Text, UnitSuffix : String) : Boolean;
Var
    L : Integer;
    EndChar : Char;
Begin
    Result := False;
    L := Length(Text);
    If L < 2 Then Exit;
    If (Text[1] < '0') Or (Text[1] > '9') Then Exit;
    EndChar := Text[L];
    If UnitSuffix = '' Then
        Result := True
    Else
        Result := EndChar = UnitSuffix[1];
End;


{ True iff any non-hidden eParameter on Comp has a Name beginning with     }
{ NamePrefix (case-sensitive; the IC-parameter check matches               }
{ "Manufacturer Part Number" / "Manufacturer Part Number 1" / etc).        }
Function FindVisibleParamByName(Comp : ISch_Component;
                                 NamePrefix : String) : Boolean;
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Param : ISch_Parameter;
    PName : String;
    PLen : Integer;
Begin
    Result := False;
    PLen := Length(NamePrefix);
    If PLen = 0 Then Exit;
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Try
                Param := Obj;
                PName := '';
                Try PName := Param.Name; Except End;
                If (Length(PName) >= PLen)
                   And (Copy(PName, 1, PLen) = NamePrefix)
                   And (Not Param.IsHidden) Then
                Begin
                    Result := True;
                    Break;
                End;
            Except End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;
End;


{ True iff any non-hidden eParameter has a TEXT value that looks like      }
{ "<number><UnitSuffix>". Used for capacitance/voltage/inductance/current. }
{ ANY visible parameter slot can hold the value (designer's choice) -- the }
{ check doesn't care about the param NAME, only that one slot is visible   }
{ and looks right.                                                           }
Function FindVisibleParamByValueShape(Comp : ISch_Component;
                                       UnitSuffix : String) : Boolean;
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Param : ISch_Parameter;
    PText : String;
Begin
    Result := False;
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Try
                Param := Obj;
                PText := '';
                Try PText := Param.Text; Except End;
                If (ParamLooksLikeUnit(PText, UnitSuffix))
                   And (Not Param.IsHidden) Then
                Begin
                    Result := True;
                    Break;
                End;
            Except End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;
End;


{ True if the on-canvas Comment field is shown -- catches the very common  }
{ resistor case where the value is written in Comment (the symbol-level   }
{ designator+comment pair) rather than in a Parameter row.                 }
Function CommentLooksLikeUnit(Comp : ISch_Component; UnitSuffix : String) : Boolean;
Var
    CText : String;
    Visible : Boolean;
Begin
    Result := False;
    CText := '';
    Visible := False;
    Try CText := Comp.Comment.Text; Except End;
    Try Visible := Not Comp.Comment.IsHidden; Except End;
    If Visible And ParamLooksLikeUnit(CText, UnitSuffix) Then
        Result := True;
End;


{ Audit_ValidateComponentParams                                                }
{                                                                              }
{ Per-class parameter visibility check. For every placed component whose     }
{ designator prefix we recognise (C R L U), verify the class's required      }
{ visible values are present:                                                 }
{   capacitor -> visible capacitance (X F) AND visible voltage (X V)         }
{   resistor  -> visible resistance (X R/k/M/m, or via Comment)              }
{   inductor  -> visible inductance (X H) AND visible saturation current (X A)}
{   ic        -> visible parameter named "Manufacturer Part Number*"         }
{                                                                              }
{ Walks GetCompiledDocs (same enumeration as BOM) so multi-channel /          }
{ hierarchical designs are covered. Response shape:                            }
{   checked     -- int: how many components matched a known class prefix      }
{   violations  -- int: how many of those have at least one missing item      }
{   items[]     -- array of:                                                  }
{                   designator -- e.g. "C1"                                   }
{                   class      -- capacitor | resistor | inductor | ic        }
{                   missing[]  -- e.g. ["capacitance","voltage"]              }
Function Audit_ValidateComponentParams(Params : String;
                                        RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    SchIter : ISch_Iterator;
    SchObj : ISch_GraphicalObject;
    Comp : ISch_Component;
    I, DocCount, Checked, Violations : Integer;
    UsePhysical : Boolean;
    Designator, CompClass, Missing, ViolationsJson, EntryJson : String;
    First, MissingHasItems : Boolean;
    HasValue, HasVoltage, HasCurrent, HasMpn : Boolean;
Begin
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

    SmartCompile(Project);
    GetCompiledDocs(Project, DocCount, UsePhysical);

    Checked := 0;
    Violations := 0;
    ViolationsJson := '';
    First := True;

    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;

        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(Doc.DM_FullPath); Except End;
        If SchDoc = Nil Then Continue;

        SchIter := SchDoc.SchIterator_Create;
        Try
            SchIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            SchObj := SchIter.FirstSchObject;
            While SchObj <> Nil Do
            Begin
                Try
                    Comp := SchObj;
                    Designator := '';
                    Try Designator := Comp.Designator.Text; Except End;
                    CompClass := ComponentClassByDes(Designator);
                    If CompClass <> '' Then
                    Begin
                        Inc(Checked);
                        Missing := '';
                        MissingHasItems := False;

                        If CompClass = 'capacitor' Then
                        Begin
                            HasValue := FindVisibleParamByValueShape(Comp, 'F')
                                        Or CommentLooksLikeUnit(Comp, 'F');
                            HasVoltage := FindVisibleParamByValueShape(Comp, 'V');
                            If Not HasValue Then
                            Begin
                                Missing := '"capacitance"';
                                MissingHasItems := True;
                            End;
                            If Not HasVoltage Then
                            Begin
                                If MissingHasItems Then Missing := Missing + ',';
                                Missing := Missing + '"voltage"';
                                MissingHasItems := True;
                            End;
                        End
                        Else If CompClass = 'resistor' Then
                        Begin
                            HasValue := FindVisibleParamByValueShape(Comp, 'R')
                                        Or FindVisibleParamByValueShape(Comp, 'k')
                                        Or FindVisibleParamByValueShape(Comp, 'M')
                                        Or FindVisibleParamByValueShape(Comp, 'm')
                                        Or CommentLooksLikeUnit(Comp, 'R')
                                        Or CommentLooksLikeUnit(Comp, 'k')
                                        Or CommentLooksLikeUnit(Comp, 'M')
                                        Or CommentLooksLikeUnit(Comp, 'm');
                            If Not HasValue Then
                            Begin
                                Missing := '"resistance"';
                                MissingHasItems := True;
                            End;
                        End
                        Else If CompClass = 'inductor' Then
                        Begin
                            HasValue := FindVisibleParamByValueShape(Comp, 'H')
                                        Or CommentLooksLikeUnit(Comp, 'H');
                            HasCurrent := FindVisibleParamByValueShape(Comp, 'A');
                            If Not HasValue Then
                            Begin
                                Missing := '"inductance"';
                                MissingHasItems := True;
                            End;
                            If Not HasCurrent Then
                            Begin
                                If MissingHasItems Then Missing := Missing + ',';
                                Missing := Missing + '"saturation_current"';
                                MissingHasItems := True;
                            End;
                        End
                        Else If CompClass = 'ic' Then
                        Begin
                            HasMpn := FindVisibleParamByName(Comp, 'Manufacturer Part Number');
                            If Not HasMpn Then
                            Begin
                                Missing := '"mpn"';
                                MissingHasItems := True;
                            End;
                        End;

                        If MissingHasItems Then
                        Begin
                            Inc(Violations);
                            If Not First Then ViolationsJson := ViolationsJson + ',';
                            First := False;
                            EntryJson := JsonStr('designator', Designator) + ',' +
                                         JsonStr('class', CompClass) + ',' +
                                         JsonRaw('missing', '[' + Missing + ']');
                            ViolationsJson := ViolationsJson + JsonObj(EntryJson);
                        End;
                    End;
                Except End;
                SchObj := SchIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(SchIter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ViolationsJson + ']')
        ));
End;


{ Audit_PowerPortOrientation                                                 }
{                                                                              }
{ SCH-side check: power-symbol orientation must follow the standard           }
{ convention -- ground symbols (ePowerGndPower/GndSignal/GndEarth) face       }
{ down (eRotate270), power bars (ePowerBar) face up (eRotate90). Catches     }
{ accidental rotations during edits; reviewers' eyes are tuned to the         }
{ standard orientation so violations slow review and hide real problems.     }
{ Checks power-symbol orientation against the standard convention.           }
{                                                                              }
{ Response shape:                                                              }
{   checked    -- int: ground + power-bar symbols inspected                   }
{   violations -- int: count with wrong orientation                            }
{   items[]    -- array of                                                     }
{                  text     -- the symbol's caption (e.g. "GND", "+3V3")      }
{                  style    -- "ground" or "power_bar"                        }
{                  expected -- "90deg" or "270deg"                            }
{                  actual   -- current orientation in degrees                  }
{                  sheet    -- DM_FullPath of the SCH document                 }
Function Audit_PowerPortOrientation(Params : String;
                                     RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    SchIter : ISch_Iterator;
    SchObj : ISch_GraphicalObject;
    Power : ISch_PowerObject;
    I, DocCount, Checked, Violations : Integer;
    UsePhysical : Boolean;
    Style : Integer;
    Orient : Integer;
    Expected : Integer;
    StyleStr, ExpectedStr, ActualStr, SheetPath : String;
    ItemsJson, EntryJson : String;
    First, IsGround, IsBar : Boolean;
Begin
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

    SmartCompile(Project);
    GetCompiledDocs(Project, DocCount, UsePhysical);

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;
        SheetPath := '';
        Try SheetPath := Doc.DM_FullPath; Except End;
        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then Continue;

        SchIter := SchDoc.SchIterator_Create;
        Try
            SchIter.AddFilter_ObjectSet(MkSet(ePowerObject));
            SchObj := SchIter.FirstSchObject;
            While SchObj <> Nil Do
            Begin
                Try
                    Power := SchObj;
                    Style := Power.Style;
                    Orient := Ord(Power.Orientation);

                    IsGround := (Style = ePowerGndPower)
                                Or (Style = ePowerGndSignal)
                                Or (Style = ePowerGndEarth);
                    IsBar := (Style = ePowerBar);

                    If IsGround Or IsBar Then
                    Begin
                        Inc(Checked);
                        If IsGround Then
                        Begin
                            Expected := Ord(eRotate270);
                            StyleStr := 'ground';
                        End
                        Else
                        Begin
                            Expected := Ord(eRotate90);
                            StyleStr := 'power_bar';
                        End;

                        If Orient <> Expected Then
                        Begin
                            Inc(Violations);
                            ExpectedStr := IntToStr(Expected * 90) + 'deg';
                            ActualStr := IntToStr(Orient * 90) + 'deg';
                            If Not First Then ItemsJson := ItemsJson + ',';
                            First := False;
                            EntryJson :=
                                JsonStr('text', Power.Text) + ',' +
                                JsonStr('style', StyleStr) + ',' +
                                JsonStr('expected', ExpectedStr) + ',' +
                                JsonStr('actual', ActualStr) + ',' +
                                JsonStr('sheet', SheetPath);
                            ItemsJson := ItemsJson + JsonObj(EntryJson);
                        End;
                    End;
                Except End;
                SchObj := SchIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(SchIter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_TentedViaRatio                                                        }
{                                                                              }
{ PCB-side check: count tented vs untented via SURFACES (top + bottom         }
{ counted separately) and report the ratio. Untented vias risk acid traps    }
{ and corrosion in humid / salt-spray environments. The check reads          }
{ Via.GetState_IsTenting_Top / Bottom per surface, ignoring buried/blind     }
{ via surfaces that don't reach the outer layer.                              }
{ Reports the tented-via ratio on the active board.                         }
{                                                                              }
{ Returns: total_surfaces, tented, untented, ratio (0..1), violation_pct     }
{          (0..100, untented/total)                                           }
Function Audit_TentedViaRatio(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Via : IPCB_Via;
    Obj : IPCB_Primitive;
    TopLayerId, BotLayerId : Integer;
    Tented, Untented, Total : Integer;
    Ratio, ViolationPct : Double;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Tented := 0;
    Untented := 0;
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
                TopLayerId := -1;
                BotLayerId := -1;
                Try TopLayerId := Via.StartLayer.LayerId; Except End;
                Try BotLayerId := Via.StopLayer.LayerId; Except End;
                If TopLayerId = eTopLayer Then
                Begin
                    If Via.GetState_IsTenting_Top Then Inc(Tented)
                    Else Inc(Untented);
                End;
                If BotLayerId = eBottomLayer Then
                Begin
                    If Via.GetState_IsTenting_Bottom Then Inc(Tented)
                    Else Inc(Untented);
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Total := Tented + Untented;
    If Total = 0 Then
    Begin
        Ratio := 1.0;
        ViolationPct := 0.0;
    End
    Else
    Begin
        Ratio := Tented / Total;
        ViolationPct := (Untented * 100.0) / Total;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('total_surfaces', Total) + ',' +
            JsonInt('tented', Tented) + ',' +
            JsonInt('untented', Untented) + ',' +
            JsonFloat('ratio', Ratio) + ',' +
            JsonFloat('violation_pct', ViolationPct)
        ));
End;


{ Audit_FindFloatingPorts                                                     }
{                                                                              }
{ SCH-side check: a port object is "floating" on a sheet if its net does     }
{ not also appear on any component pin or any sheet-symbol entry on the      }
{ same sheet. Floating ports are wires-to-nowhere -- either an editing       }
{ leftover or a never-completed hookup. ERC sometimes catches these but      }
{ not always (a port with a valid net-label tag passes ERC); the explicit   }
{ structural check here is more reliable.                                    }
{                                                                              }
{ The algorithm collects ALL connected nets on the sheet (component pin     }
{ nets                                                                       }
{ + sheet-entry nets) into a single string-set, then iterates ports and      }
{ flags any whose net is not present.                                        }
{                                                                              }
{ Response shape:                                                              }
{   checked    -- int: total port objects inspected across project           }
{   violations -- int: floating-port count                                    }
{   items[]    -- per-violation                                              }
{                  net      -- the port's flattened net name                  }
{                  sheet    -- DM_FullPath of the SCH sheet                  }
{                  location -- "(x,y)" in mils                                }
Function Audit_FindFloatingPorts(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    DmComp : IComponent;
    Part : IPart;
    Pin : IPin;
    SheetSym : ISheetSymbol;
    Entry : INetItem;
    Port : IPort;
    I, J, K, M, DocCount, Checked, Violations : Integer;
    UsePhysical : Boolean;
    NetsSet, PortNet, SheetPath, LocStr : String;
    ItemsJson, EntryJson : String;
    First : Boolean;
Begin
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

    SmartCompile(Project);
    GetCompiledDocs(Project, DocCount, UsePhysical);

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;
        SheetPath := '';
        Try SheetPath := Doc.DM_FullPath; Except End;

        { Per-sheet: build a pipe-bracketed set of every connected net    }
        { name (component pins + sheet-symbol entries). Bracketing means }
        { Pos('|<name>|', set) is an exact substring match -- no false   }
        { positives from prefix-overlap (e.g. "VCC" vs "VCC_3V3").       }
        NetsSet := '|';

        { Component pin nets. Single-part components expose pins via     }
        { DM_Pins directly; multi-part components require a SubParts walk }
        { because DM_Pins on the parent returns only the placeholder set. }
        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            DmComp := Nil;
            Try DmComp := Doc.DM_Components(J); Except End;
            If DmComp = Nil Then Continue;
            Try
                If DmComp.DM_SubPartCount <= 1 Then
                Begin
                    For K := 0 To DmComp.DM_PinCount - 1 Do
                    Begin
                        Pin := DmComp.DM_Pins(K);
                        If Pin = Nil Then Continue;
                        NetsSet := NetsSet + Pin.DM_FlattenedNetName + '|';
                    End;
                End
                Else
                Begin
                    For M := 0 To DmComp.DM_SubPartCount - 1 Do
                    Begin
                        Part := DmComp.DM_SubParts(M);
                        If Part = Nil Then Continue;
                        For K := 0 To Part.DM_PinCount - 1 Do
                        Begin
                            Pin := Part.DM_Pins(K);
                            If Pin = Nil Then Continue;
                            NetsSet := NetsSet + Pin.DM_FlattenedNetName + '|';
                        End;
                    End;
                End;
            Except End;
        End;

        { Sheet-symbol entry nets. A port on this sheet may carry a net  }
        { that's exposed via a sheet entry into a child sheet -- still a }
        { real connection.                                                }
        Try
            For J := 0 To Doc.DM_SheetSymbolCount - 1 Do
            Begin
                SheetSym := Doc.DM_SheetSymbols(J);
                If SheetSym = Nil Then Continue;
                Try
                    For K := 0 To SheetSym.DM_SheetEntryCount - 1 Do
                    Begin
                        Entry := SheetSym.DM_SheetEntries(K);
                        If Entry = Nil Then Continue;
                        NetsSet := NetsSet + Entry.DM_FlattenedNetName + '|';
                    End;
                Except End;
            End;
        Except End;

        { Walk ports: anything not in NetsSet is floating on this sheet. }
        For J := 0 To Doc.DM_PortCount - 1 Do
        Begin
            Port := Nil;
            Try Port := Doc.DM_Ports(J); Except End;
            If Port = Nil Then Continue;
            Inc(Checked);
            PortNet := '';
            Try PortNet := Port.DM_FlattenedNetName; Except End;
            If PortNet = '' Then Continue;
            If Pos('|' + PortNet + '|', NetsSet) > 0 Then Continue;

            Inc(Violations);
            LocStr := '';
            Try LocStr := Port.DM_LocationString; Except End;
            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            EntryJson :=
                JsonStr('net', PortNet) + ',' +
                JsonStr('sheet', SheetPath) + ',' +
                JsonStr('location', LocStr);
            ItemsJson := ItemsJson + JsonObj(EntryJson);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


Function PointsCloseEnough(X1, Y1, X2, Y2, Tolerance : TCoord) : Boolean;
Begin
    Result := (Abs(X1 - X2) <= Tolerance) And (Abs(Y1 - Y2) <= Tolerance);
End;


{ Audit_FindBadConnections                                                     }
{                                                                              }
{ PCB-side check: walk every track / arc on signal layers and flag any       }
{ endpoint that doesn't actually touch another same-net primitive within     }
{ Tolerance. Center-to-center hit test against tracks (endpoints), arcs     }
{ (endpoints), pads (centre), and vias (centre, layer-intersected).         }
{                                                                              }
{ This catches near-miss connections that visual review and DRC miss --     }
{ a track whose endpoint sits ~1mil off a pad still routes electrically in  }
{ Altium's analyser but will not photoplot as connected, leading to a       }
{ bridging hazard on the fab side.                                            }
{                                                                              }
{ Param: "tolerance_mils" -- coord tolerance in mils, default 1.             }
{                                                                              }
{ Response shape:                                                              }
{   checked    -- int: tracks + arcs inspected                                }
{   violations -- int: primitives with at least one dangling endpoint        }
{   tolerance_mils -- echo of the tolerance used                              }
{   items[]    -- per-violation                                              }
{                  kind     -- "track" or "arc"                              }
{                  layer    -- layer name                                     }
{                  net      -- net name (always non-empty; net-less          }
{                              primitives are skipped silently)              }
{                  at       -- "(x,y)" mils, the dangling endpoint           }
Function Audit_FindBadConnections(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    BIter : IPCB_BoardIterator;
    SIter : IPCB_SpatialIterator;
    Prim1, Prim2 : IPCB_Primitive;
    Trk : IPCB_Track;
    ArcObj : IPCB_Arc;
    ViaObj : IPCB_Via;
    Tolerance : TCoord;
    ToleranceMils : Double;
    EndIdx, X, Y : Integer;
    LayerStr, KindStr, NetName, ItemsJson, EntryJson : String;
    Checked, Violations : Integer;
    Found, First : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    ToleranceMils := StrToFloatDef(ExtractJsonValue(Params, 'tolerance_mils'), 1.0);
    If ToleranceMils < 0.0 Then ToleranceMils := 1.0;
    Tolerance := MilsToCoord(Round(ToleranceMils));

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    BIter := Board.BoardIterator_Create;
    Try
        BIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject));
        BIter.AddFilter_IPCB_LayerSet(LayerSet.SignalLayers);
        BIter.AddFilter_Method(eProcessAll);
        Prim1 := BIter.FirstPCBObject;
        While Prim1 <> Nil Do
        Begin
            Try
                { Skip teardrops (intentionally not endpoint-aligned), pads  }
                { inside components (already shouldered onto the pad shape), }
                { primitives without a net (decorative silkscreen on a sig   }
                { layer), and full-circle arcs (no endpoint to anchor on).   }
                If Prim1.TearDrop Or Prim1.InComponent Or (Not Prim1.InNet) Then
                Begin
                    Prim1 := BIter.NextPCBObject;
                    Continue;
                End;
                If Prim1.ObjectId = eArcObject Then
                Begin
                    ArcObj := Prim1;
                    If (ArcObj.StartAngle = 0) And (ArcObj.EndAngle = 360) Then
                    Begin
                        Prim1 := BIter.NextPCBObject;
                        Continue;
                    End;
                End;

                Inc(Checked);
                If Prim1.ObjectId = eTrackObject Then KindStr := 'track'
                Else KindStr := 'arc';
                NetName := '';
                Try NetName := Prim1.Net.Name; Except End;
                LayerStr := '';
                Try LayerStr := GetLayerString(Prim1.Layer); Except End;

                { Check both endpoints, EndIdx 1..2.                         }
                For EndIdx := 1 To 2 Do
                Begin
                    If Prim1.ObjectId = eTrackObject Then
                    Begin
                        Trk := Prim1;
                        If EndIdx = 1 Then Begin X := Trk.x1; Y := Trk.y1; End
                        Else Begin X := Trk.x2; Y := Trk.y2; End;
                    End
                    Else  { eArcObject }
                    Begin
                        ArcObj := Prim1;
                        If EndIdx = 1 Then Begin X := ArcObj.StartX; Y := ArcObj.StartY; End
                        Else Begin X := ArcObj.EndX; Y := ArcObj.EndY; End;
                    End;

                    Found := False;
                    SIter := Board.SpatialIterator_Create;
                    Try
                        SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
                                                         ePadObject, eViaObject));
                        SIter.AddFilter_LayerSet(MkSet(Prim1.Layer, eMultiLayer));
                        SIter.AddFilter_Area(X - Tolerance, Y - Tolerance,
                                              X + Tolerance, Y + Tolerance);
                        Prim2 := SIter.FirstPCBObject;
                        While (Prim2 <> Nil) And (Not Found) Do
                        Begin
                            Try
                                If Prim2.InNet
                                   And (Prim1.I_ObjectAddress <> Prim2.I_ObjectAddress)
                                   And (Prim2.Net.Name = Prim1.Net.Name)
                                   And (Not Prim2.TearDrop) Then
                                Begin
                                    If (Prim2.ObjectId = eTrackObject)
                                       And (Prim2.Layer = Prim1.Layer) Then
                                    Begin
                                        Trk := Prim2;
                                        If PointsCloseEnough(Trk.x1, Trk.y1, X, Y, Tolerance)
                                           Or PointsCloseEnough(Trk.x2, Trk.y2, X, Y, Tolerance) Then
                                            Found := True;
                                    End
                                    Else If (Prim2.ObjectId = eArcObject)
                                            And (Prim2.Layer = Prim1.Layer) Then
                                    Begin
                                        ArcObj := Prim2;
                                        If PointsCloseEnough(ArcObj.StartX, ArcObj.StartY, X, Y, Tolerance)
                                           Or PointsCloseEnough(ArcObj.EndX, ArcObj.EndY, X, Y, Tolerance) Then
                                            Found := True;
                                    End
                                    Else If Prim2.ObjectId = ePadObject Then
                                    Begin
                                        If ((Prim2.Layer = eMultiLayer) Or (Prim2.Layer = Prim1.Layer))
                                           And PointsCloseEnough(Prim2.x, Prim2.y, X, Y, Tolerance) Then
                                            Found := True;
                                    End
                                    Else If Prim2.ObjectId = eViaObject Then
                                    Begin
                                        ViaObj := Prim2;
                                        If ViaObj.IntersectLayer(Prim1.Layer)
                                           And PointsCloseEnough(ViaObj.x, ViaObj.y, X, Y, Tolerance) Then
                                            Found := True;
                                    End;
                                End;
                            Except End;
                            Prim2 := SIter.NextPCBObject;
                        End;
                    Finally
                        Board.SpatialIterator_Destroy(SIter);
                    End;

                    If Not Found Then
                    Begin
                        Inc(Violations);
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('kind', KindStr) + ',' +
                            JsonStr('layer', LayerStr) + ',' +
                            JsonStr('net', NetName) + ',' +
                            JsonStr('at', '(' + IntToStr(CoordToMils(X)) + ',' +
                                          IntToStr(CoordToMils(Y)) + ')');
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                End;
            Except End;
            Prim1 := BIter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(BIter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonFloat('tolerance_mils', ToleranceMils) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ True if NetName looks like a ground / power rail by naming convention.    }
{ Heuristic only -- a stackup-aware classifier needs the user to tag       }
{ reference layers in the IPCB_LayerStack. The naming check catches GND /   }
{ VCC /                                                                      }
{ VDD / V3V3 / +3V3 / V_BAT / etc.                                          }
Function IsPowerOrGroundNetName(NetName : String) : Boolean;
Var
    Upper : String;
Begin
    Result := False;
    If NetName = '' Then Exit;
    Upper := UpperCase(NetName);
    { Plain ground / power vocab. }
    If (Upper = 'GND') Or (Upper = 'GROUND') Or (Upper = 'VSS')
       Or (Upper = 'AGND') Or (Upper = 'DGND') Or (Upper = 'PGND')
       Or (Upper = 'EGND') Or (Upper = 'SGND') Then
    Begin Result := True; Exit; End;
    { GND_xxx / xxx_GND. }
    If Pos('GND', Upper) > 0 Then Begin Result := True; Exit; End;
    { Common +/- rail prefixes: V<digit>, +<digit>, -<digit>, VCC, VDD,    }
    { VEE, VBAT. The +/- forms can carry whatever suffix.                  }
    If (Length(Upper) >= 2)
       And ((Upper[1] = 'V') Or (Upper[1] = '+') Or (Upper[1] = '-'))
       And (((Upper[2] >= '0') And (Upper[2] <= '9'))
            Or (Upper[2] = 'C') Or (Upper[2] = 'D')
            Or (Upper[2] = 'E') Or (Upper[2] = 'B')
            Or (Upper[2] = 'S')) Then
    Begin Result := True; Exit; End;
End;


{ Audit_FindSignalViasWithoutReturn                                            }
{                                                                              }
{ PCB-side check: for every signal via (NOT on a ground / power net), look  }
{ for at least one nearby ground / power via within Radius. High-speed     }
{ signals that change layers need a corresponding return via so the return }
{ current isn't forced into a long detour through the reference plane --   }
{ a near via on a reference net carries the displacement current cleanly.  }
{                                                                              }
{ This is a SIMPLIFIED proximity heuristic, not a stackup-aware analyser.   }
{ The full check (with layer-reference tagging and stripline / microstrip   }
{ rules) is a much larger GUI-driven analysis. For first-pass review the   }
{ proximity heuristic catches the obvious "signal via without a ground via }
{ anywhere in sight" cases.                                                  }
{                                                                              }
{ Param: "radius_mils" -- distance threshold, default 50.                     }
{                                                                              }
{ Response shape:                                                              }
{   checked        -- int: signal vias inspected                              }
{   violations     -- int: signal vias with no power/ground via within radius }
{   radius_mils    -- echo of radius used                                     }
{   items[]        -- per-violation                                           }
{                      net    -- signal net name                              }
{                      at     -- "(x,y)" mils of the signal via              }
Function Audit_FindSignalViasWithoutReturn(Params,
                                            RequestId : String) : String;
Var
    Board : IPCB_Board;
    BIter, SIter : IPCB_BoardIterator;
    SpatIter : IPCB_SpatialIterator;
    Via1, Via2 : IPCB_Via;
    Obj1, Obj2 : IPCB_Primitive;
    Radius : TCoord;
    RadiusMils : Double;
    Checked, Violations : Integer;
    SignalNet, RefNet, ItemsJson, EntryJson : String;
    First, ReturnFound : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    RadiusMils := StrToFloatDef(ExtractJsonValue(Params, 'radius_mils'), 50.0);
    If RadiusMils <= 0.0 Then RadiusMils := 50.0;
    Radius := MilsToCoord(Round(RadiusMils));

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    BIter := Board.BoardIterator_Create;
    Try
        BIter.AddFilter_ObjectSet(MkSet(eViaObject));
        BIter.AddFilter_LayerSet(AllLayers);
        BIter.AddFilter_Method(eProcessAll);
        Obj1 := BIter.FirstPCBObject;
        While Obj1 <> Nil Do
        Begin
            Try
                Via1 := Obj1;
                If Via1.InNet Then
                Begin
                    SignalNet := '';
                    Try SignalNet := Via1.Net.Name; Except End;
                    If (SignalNet <> '') And (Not IsPowerOrGroundNetName(SignalNet)) Then
                    Begin
                        Inc(Checked);
                        ReturnFound := False;
                        SpatIter := Board.SpatialIterator_Create;
                        Try
                            SpatIter.AddFilter_ObjectSet(MkSet(eViaObject));
                            SpatIter.AddFilter_LayerSet(AllLayers);
                            SpatIter.AddFilter_Area(Via1.x - Radius, Via1.y - Radius,
                                                     Via1.x + Radius, Via1.y + Radius);
                            Obj2 := SpatIter.FirstPCBObject;
                            While (Obj2 <> Nil) And (Not ReturnFound) Do
                            Begin
                                Try
                                    Via2 := Obj2;
                                    If (Via2.I_ObjectAddress <> Via1.I_ObjectAddress)
                                       And Via2.InNet Then
                                    Begin
                                        RefNet := '';
                                        Try RefNet := Via2.Net.Name; Except End;
                                        If IsPowerOrGroundNetName(RefNet) Then
                                            ReturnFound := True;
                                    End;
                                Except End;
                                Obj2 := SpatIter.NextPCBObject;
                            End;
                        Finally
                            Board.SpatialIterator_Destroy(SpatIter);
                        End;

                        If Not ReturnFound Then
                        Begin
                            Inc(Violations);
                            If Not First Then ItemsJson := ItemsJson + ',';
                            First := False;
                            EntryJson :=
                                JsonStr('net', SignalNet) + ',' +
                                JsonStr('at', '(' + IntToStr(CoordToMils(Via1.x)) + ',' +
                                              IntToStr(CoordToMils(Via1.y)) + ')');
                            ItemsJson := ItemsJson + JsonObj(EntryJson);
                        End;
                    End;
                End;
            Except End;
            Obj1 := BIter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(BIter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonFloat('radius_mils', RadiusMils) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindInvalidRegions                                                    }
{                                                                              }
{ PCB-side check: find polygon regions whose GeometricPolygon area is zero  }
{ or unset. These are leftover from cancelled polygon-pour operations or    }
{ corrupted file imports; they don't render visibly but can throw DRC      }
{ violations under "Modified Polygon" rules -- but we SURFACE only,        }
{ no auto-delete (mutations belong to a separate user-confirmed flow).     }
{                                                                              }
{ Response shape: checked, violations, items[] -- each entry has layer +    }
{ at coords; the caller can drill in via cross-probe.                       }
Function Audit_FindInvalidRegions(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Region : IPCB_Region;
    Checked, Violations : Integer;
    LayerStr, ItemsJson, EntryJson : String;
    BBox : TCoordRect;
    First, IsInvalid : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.SetState_FilterAll;
        Iter.AddFilter_ObjectSet(MkSet(eRegionObject));
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                { Skip primitives owned by a component / dimension -- those     }
                { aren't free-standing regions.                                 }
                If (Not Obj.InComponent) And (Not Obj.InDimension) Then
                Begin
                    Inc(Checked);
                    Region := Obj;
                    IsInvalid := False;
                    Try
                        { GeometricPolygon is undeclared in this script binding;
                          flag regions whose bounding box is degenerate (zero
                          width or height = a zero / unset-area region). }
                        BBox := Region.BoundingRectangle;
                        If ((BBox.X2 - BBox.X1) <= 0) Or ((BBox.Y2 - BBox.Y1) <= 0) Then
                            IsInvalid := True;
                    Except
                        IsInvalid := True;
                    End;
                    If IsInvalid Then
                    Begin
                        Inc(Violations);
                        LayerStr := '';
                        Try LayerStr := GetLayerString(Obj.Layer); Except End;
                        BBox.X1 := 0; BBox.Y1 := 0;
                        Try BBox := Obj.BoundingRectangle; Except End;
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('layer', LayerStr) + ',' +
                            JsonStr('at', '(' + IntToStr(CoordToMils(BBox.X1)) + ',' +
                                          IntToStr(CoordToMils(BBox.Y1)) + ')');
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_VariantNotFitted                                                       }
{                                                                              }
{ Identify components flagged "Not Fitted" in the project's CURRENT variant.  }
{ Manufacturing context: a Not-Fitted component is on the BOM as a placeholder}
{ but should NOT receive paste on the stencil -- otherwise the SMT line       }
{ deposits paste on empty pads and bridges form on subsequent rework. Some    }
{ houses also need a separate "DNP" silkscreen marker; the agent can act on  }
{ this list either way.                                                       }
{                                                                              }
{ This is the                                                                 }
{ identify half; the remediation half adds a PasteMaskExpansion rule and     }
{ belongs in a separate user-confirmed mutating call).                       }
{                                                                              }
{ Response shape:                                                              }
{   variant      -- the variant name being inspected (empty = "no variant")  }
{   checked      -- total components in the flattened project                 }
{   violations   -- components marked NotFitted in this variant               }
{   items[]      -- per NotFitted: designator, comment, unique_id            }
Function Audit_VariantNotFitted(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Variant : IProjectVariant;
    FlatDoc : IDocument;
    Comp : IComponent;
    CompVar : IComponentVariation;
    I, Checked, Violations : Integer;
    VariantName, ItemsJson, EntryJson : String;
    First : Boolean;
Begin
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

    Variant := Nil;
    Try Variant := Project.DM_CurrentProjectVariant; Except End;
    VariantName := '';
    If Variant <> Nil Then
        Try VariantName := Variant.DM_Description; Except End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    If Variant <> Nil Then
    Begin
        FlatDoc := Nil;
        Try FlatDoc := Project.DM_DocumentFlattened; Except End;
        If FlatDoc <> Nil Then
        Begin
            For I := 0 To FlatDoc.DM_ComponentCount - 1 Do
            Begin
                Comp := Nil;
                Try Comp := FlatDoc.DM_Components(I); Except End;
                If Comp = Nil Then Continue;
                Inc(Checked);
                CompVar := Nil;
                Try CompVar := Variant.DM_FindComponentVariationByUniqueId(
                    Comp.DM_UniqueId); Except End;
                If CompVar = Nil Then Continue;
                If CompVar.DM_VariationKind = eVariation_NotFitted Then
                Begin
                    Inc(Violations);
                    If Not First Then ItemsJson := ItemsJson + ',';
                    First := False;
                    EntryJson :=
                        JsonStr('designator', Comp.DM_PhysicalDesignator) + ',' +
                        JsonStr('comment', Comp.DM_Comment) + ',' +
                        JsonStr('unique_id', Comp.DM_UniqueId);
                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                End;
            End;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('variant', VariantName) + ',' +
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindUnmatchedPorts                                                    }
{                                                                              }
{ Simplified port-direction-mismatch check across the whole project. The     }
{ full check would use elaborate per-net direction                           }
{ matrices, mixed-port-name detection, and IMessagesManager hyperlink       }
{ output. This simpler audit returns the two highest-bite cases:             }
{                                                                              }
{   1) "multi_output" -- a net with MORE THAN ONE Output port. Two source   }
{      drivers fighting for the same net; almost always a wiring error.    }
{   2) "no_driver" -- a net with at least one Input port but ZERO Output    }
{      (or Bidirectional) ports across the whole project. Orphan signal.   }
{                                                                              }
{ Walks compiled docs so multichannel designs are covered. Groups ports by  }
{ DM_FlattenedNetName so nets renamed via a wire stay grouped under their  }
{ post-rename label.                                                         }
{                                                                              }
{ Response shape: checked (total ports inspected), violations, items[] each  }
{ carrying net, issue (string), and port_count.                              }
Function Audit_FindUnmatchedPorts(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Port : IPort;
    I, J, DocCount, Checked, Violations : Integer;
    UsePhysical : Boolean;
    PortNet : String;
    { Parallel pipe-delimited TStringList-substitute strings -- the         }
    { delphiscript_tstringlist_function_return memory note warns that      }
    { TStringList misbehaves as a Function-scoped object; safer to thread  }
    { state through plain strings.                                          }
    NetsSeen, NetsOutputs, NetsInputs : String;
    Net, ItemsJson, EntryJson : String;
    OutputCount, InputCount, Pos1, Pos2 : Integer;
    PortType : TPinElectrical;
    First : Boolean;
    Token, NetMark : String;
Begin
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

    SmartCompile(Project);
    GetCompiledDocs(Project, DocCount, UsePhysical);

    Checked := 0;
    NetsSeen := '|';
    NetsOutputs := '|';
    NetsInputs := '|';

    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;

        For J := 0 To Doc.DM_PortCount - 1 Do
        Begin
            Port := Nil;
            Try Port := Doc.DM_Ports(J); Except End;
            If Port = Nil Then Continue;
            Inc(Checked);
            PortNet := '';
            Try PortNet := Port.DM_FlattenedNetName; Except End;
            If PortNet = '' Then Continue;

            NetMark := '|' + PortNet + '|';
            If Pos(NetMark, NetsSeen) = 0 Then
                NetsSeen := NetsSeen + PortNet + '|';

            Try PortType := Port.DM_Electrical; Except Continue; End;
            If (PortType = eElectricOutput) Then
                NetsOutputs := NetsOutputs + PortNet + '|'
            Else If (PortType = eElectricInput) Then
                NetsInputs := NetsInputs + PortNet + '|'
            Else If (PortType = eElectricIO) Then
            Begin
                { Bidirectional counts as both -- prevents false orphan-input   }
                { reports when a bidir port is the only driver.                  }
                NetsOutputs := NetsOutputs + PortNet + '|';
                NetsInputs := NetsInputs + PortNet + '|';
            End;
        End;
    End;

    { Walk unique nets in NetsSeen, evaluate per-net counts.                 }
    Violations := 0;
    ItemsJson := '';
    First := True;
    Pos1 := 2;  { skip leading '|' }
    While Pos1 <= Length(NetsSeen) Do
    Begin
        Pos2 := Pos1;
        While (Pos2 <= Length(NetsSeen)) And (NetsSeen[Pos2] <> '|') Do
            Inc(Pos2);
        If Pos2 <= Pos1 Then Break;
        Net := Copy(NetsSeen, Pos1, Pos2 - Pos1);
        Pos1 := Pos2 + 1;
        If Net = '' Then Continue;

        NetMark := '|' + Net + '|';
        { Count occurrences in the outputs string. }
        OutputCount := 0;
        Token := NetsOutputs;
        While Pos(NetMark, Token) > 0 Do
        Begin
            Inc(OutputCount);
            Token := Copy(Token, Pos(NetMark, Token) + Length(NetMark) - 1,
                          Length(Token));
        End;
        InputCount := 0;
        Token := NetsInputs;
        While Pos(NetMark, Token) > 0 Do
        Begin
            Inc(InputCount);
            Token := Copy(Token, Pos(NetMark, Token) + Length(NetMark) - 1,
                          Length(Token));
        End;

        If OutputCount > 1 Then
        Begin
            Inc(Violations);
            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            EntryJson :=
                JsonStr('net', Net) + ',' +
                JsonStr('issue', 'multi_output') + ',' +
                JsonInt('output_count', OutputCount) + ',' +
                JsonInt('input_count', InputCount);
            ItemsJson := ItemsJson + JsonObj(EntryJson);
        End
        Else If (InputCount > 0) And (OutputCount = 0) Then
        Begin
            Inc(Violations);
            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            EntryJson :=
                JsonStr('net', Net) + ',' +
                JsonStr('issue', 'no_driver') + ',' +
                JsonInt('output_count', OutputCount) + ',' +
                JsonInt('input_count', InputCount);
            ItemsJson := ItemsJson + JsonObj(EntryJson);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindViaAntennas                                                       }
{                                                                              }
{ Find vias connected on ONLY ONE layer (signal layer or plane). The unused  }
{ side of a single-layer-connected via is an electrical stub -- at RF / high}
{ data rates it forms a resonator that reflects back into the trace,        }
{ degrading rise time and EMC compliance. Common after iterative editing    }
{ where a layer-change via is left dangling when the destination trace is   }
{ rerouted onto a different layer.                                          }
{                                                                              }
{ Counts a via "connected" on a layer when:                                  }
{   - the layer is signal and a track/arc/pad/fill/region touches the via   }
{     (PrimPrimDistance = 0) on that layer; OR                              }
{   - the layer is plane (IPCB_Plane) and Via.IsConnectedToPlane[layer]     }
{     reports True.                                                          }
{                                                                              }
{ Returns flagged via antennas as a JSON list.                              }
{                                                                              }
{ Response shape: checked, violations, items -- each entry carries net,    }
{ at coords, connected_layers count.                                         }
Function Audit_FindViaAntennas(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    BIter : IPCB_BoardIterator;
    SIter : IPCB_SpatialIterator;
    Stack : IPCB_LayerStack;
    Layer : IPCB_LayerObject;
    Via : IPCB_Via;
    Obj, Prim : IPCB_Primitive;
    BBox : TCoordRect;
    Connected, Checked, Violations : Integer;
    LayerId : Integer;
    NetName, ItemsJson, EntryJson : String;
    First, HitOnLayer : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Stack := Nil;
    Try Stack := Board.LayerStack_V7; Except End;
    If Stack = Nil Then
        Try Stack := Board.LayerStack; Except End;
    If Stack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LAYERSTACK',
            'Could not obtain board LayerStack.');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    BIter := Board.BoardIterator_Create;
    Try
        BIter.AddFilter_ObjectSet(MkSet(eViaObject));
        BIter.AddFilter_LayerSet(AllLayers);
        BIter.AddFilter_Method(eProcessAll);
        Obj := BIter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                Via := Obj;
                Inc(Checked);
                Connected := 0;
                BBox := Via.BoundingRectangle;
                Layer := Stack.FirstLayer;
                While Layer <> Nil Do
                Begin
                    Try
                        LayerId := Layer.LayerID;
                        If Via.IntersectLayer(LayerId) Then
                        Begin
                            If ILayer.IsSignalLayer(LayerId) Then
                            Begin
                                HitOnLayer := False;
                                SIter := Board.SpatialIterator_Create;
                                Try
                                    SIter.AddFilter_ObjectSet(MkSet(eTrackObject,
                                        eArcObject, ePadObject, eFillObject,
                                        eRegionObject));
                                    SIter.AddFilter_Area(BBox.Left, BBox.Bottom,
                                                          BBox.Right, BBox.Top);
                                    SIter.AddFilter_LayerSet(MkSet(LayerId));
                                    Prim := SIter.FirstPCBObject;
                                    While (Prim <> Nil) And (Not HitOnLayer) Do
                                    Begin
                                        Try
                                            If Board.PrimPrimDistance(Prim, Via) = 0 Then
                                                HitOnLayer := True;
                                        Except End;
                                        Prim := SIter.NextPCBObject;
                                    End;
                                Finally
                                    Board.SpatialIterator_Destroy(SIter);
                                End;
                                If HitOnLayer Then Inc(Connected);
                            End
                            Else
                            Begin
                                { Plane layer -- ask the via directly. }
                                Try
                                    If Via.IsConnectedToPlane[LayerId] Then
                                        Inc(Connected);
                                Except End;
                            End;
                        End;
                    Except End;
                    Layer := Stack.NextLayer(Layer);
                End;

                If Connected = 1 Then
                Begin
                    Inc(Violations);
                    NetName := '';
                    If Via.InNet Then
                        Try NetName := Via.Net.Name; Except End;
                    If Not First Then ItemsJson := ItemsJson + ',';
                    First := False;
                    EntryJson :=
                        JsonStr('net', NetName) + ',' +
                        JsonStr('at', '(' + IntToStr(CoordToMils(Via.x)) + ',' +
                                      IntToStr(CoordToMils(Via.y)) + ')') + ',' +
                        JsonInt('connected_layers', Connected);
                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                End;
            Except End;
            Obj := BIter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(BIter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindDesignatorCollisions                                              }
{                                                                              }
{ Walk every placed schematic component across the compiled doc tree and    }
{ flag any designator that appears more than once. Catches annotation      }
{ stalls where a paste-from-another-sheet left two "U7" floating, and the }
{ post-annotate sweep didn't catch them (e.g. when the user pasted into a }
{ non-annotating sheet or after a recent rename).                          }
{                                                                              }
{ Implementation: walk all SCH docs in DM_LogicalDocuments (DM_Components    }
{ on source docs returns the AS-DRAWN designator, which is what the         }
{ collision check should use -- compiled docs would auto-disambiguate via   }
{ channel suffixes). For each designator, count occurrences; flag any with }
{ count > 1.                                                                  }
{                                                                              }
{ Response: checked, violations, items where each entry has designator,    }
{           count, and the list of sheet paths comma-joined.                }
Function Audit_FindDesignatorCollisions(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    SchIter : ISch_Iterator;
    SchComp : ISch_Component;
    I, J, Checked, Violations : Integer;
    Designator, SheetPath, DocKind : String;
    { Pipe-delimited "|des|@sheet" pairs so each occurrence carries its    }
    { sheet for later violation grouping.                                   }
    OccPairs, EntryJson, ItemsJson : String;
    { Pipe-bracketed list of designators we've already emitted a violation }
    { for, to avoid duplicate entries when a designator appears 3+ times.   }
    ReportedSet : String;
    Pos1, Pos2, AtPos, Count : Integer;
    Token, Des, Sheets : String;
    NetMark : String;
    First : Boolean;
Begin
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

    Checked := 0;
    OccPairs := '';

    { Iterate the LOADED schematic sheets via SchServer (the same proven path
      Audit_FindPlaceholderValues uses). DM_Components on a logical doc returns
      nothing here, so the old DM-model walk silently checked zero. Sheets must
      be resident -- call proj_load_sheets first. }
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(I); Except End;
        If Doc = Nil Then Continue;
        DocKind := '';
        Try DocKind := Doc.DM_DocumentKind; Except End;
        If DocKind <> 'SCH' Then Continue;
        SheetPath := '';
        Try SheetPath := Doc.DM_FileName; Except End;

        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(Doc.DM_FullPath); Except End;
        If SchDoc = Nil Then Continue;
        SchIter := SchDoc.SchIterator_Create;
        If SchIter = Nil Then Continue;
        Try
            SchIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            SchComp := SchIter.FirstSchObject;
            While SchComp <> Nil Do
            Begin
                Try
                    Inc(Checked);
                    Designator := '';
                    Try Designator := SchComp.Designator.Text; Except End;
                    If Designator <> '' Then
                        OccPairs := OccPairs + '|' + Designator + '@' + SheetPath;
                Except End;
                SchComp := SchIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(SchIter);
        End;
    End;
    OccPairs := OccPairs + '|';

    { Walk unique designators in OccPairs. For each, count occurrences.    }
    Violations := 0;
    ItemsJson := '';
    ReportedSet := '|';
    First := True;
    Pos1 := 2;
    While Pos1 <= Length(OccPairs) Do
    Begin
        Pos2 := Pos1;
        While (Pos2 <= Length(OccPairs)) And (OccPairs[Pos2] <> '|') Do
            Inc(Pos2);
        If Pos2 <= Pos1 Then Break;
        Token := Copy(OccPairs, Pos1, Pos2 - Pos1);
        Pos1 := Pos2 + 1;
        AtPos := Pos('@', Token);
        If AtPos <= 1 Then Continue;
        Des := Copy(Token, 1, AtPos - 1);
        If Des = '' Then Continue;
        NetMark := '|' + Des + '|';
        If Pos(NetMark, ReportedSet) > 0 Then Continue;

        { Count occurrences of "|<des>@" in OccPairs. }
        Count := 0;
        Sheets := '';
        AtPos := Pos('|' + Des + '@', OccPairs);
        While AtPos > 0 Do
        Begin
            Inc(Count);
            { Extract this occurrence's sheet (between @ and next |).       }
            Pos2 := AtPos + Length('|' + Des + '@');
            Pos1 := Pos2;
            While (Pos1 <= Length(OccPairs)) And (OccPairs[Pos1] <> '|') Do
                Inc(Pos1);
            If Sheets <> '' Then Sheets := Sheets + ',';
            Sheets := Sheets + Copy(OccPairs, Pos2, Pos1 - Pos2);
            AtPos := Pos('|' + Des + '@',
                         Copy(OccPairs, Pos1, Length(OccPairs) - Pos1 + 1));
            If AtPos > 0 Then AtPos := AtPos + Pos1 - 1;
        End;

        ReportedSet := ReportedSet + Des + '|';
        If Count > 1 Then
        Begin
            Inc(Violations);
            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            EntryJson :=
                JsonStr('designator', Des) + ',' +
                JsonInt('count', Count) + ',' +
                JsonStr('sheets', Sheets);
            ItemsJson := ItemsJson + JsonObj(EntryJson);
        End;
        Pos1 := Pos2 + 1;  { advance past last consumed token's '|' }
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindRemovedPadShapes                                                  }
{                                                                              }
{ Pads and vias can have their copper annular ring "removed" on individual  }
{ inner layers via Altium's "Remove Unused Pad Shapes" optimization. The    }
{ flag is per-layer and silent: a via with shape removed on Layer 3 still   }
{ drills through Layer 3 but has no copper-to-trace contact there, so any   }
{ trace that tried to route INTO it on Layer 3 will fail to connect at     }
{ fab. Catches the "unused pad shapes" sweep applied too aggressively.     }
{                                                                              }
{ For each Pad on signal layers:                                              }
{   - Pad.IsPadRemoved(layer) flags removed                                  }
{ For each Via:                                                                }
{   - Via.SizeOnLayer(layer) <= Via.HoleSize means there's no annular       }
{     ring left -- effectively a drilled hole, not a connection point.       }
{                                                                              }
{ Pattern: brett's PadShapeRemoved.pas. Read-only; the original additionally }
{ highlights nets, which we skip (the agent gets coordinates back via JSON  }
{ and can cross-probe).                                                       }
{                                                                              }
{ Response: checked, violations, items where each entry has kind             }
{ ("pad" / "via"), designator (component refdes or empty), net, layer, at.  }
Function Audit_FindRemovedPadShapes(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Stack : IPCB_LayerStack;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    ViaObj : IPCB_Via;
    PadObj : IPCB_Pad;
    Layer : IPCB_LayerObject;
    LayerId : Integer;
    Checked, Violations : Integer;
    KindStr, DesStr, NetStr, LayerStr, ItemsJson, EntryJson : String;
    First, Removed : Boolean;
    PadX, PadY : TCoord;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Stack := Nil;
    Try Stack := Board.LayerStack_V7; Except End;
    If Stack = Nil Then
        Try Stack := Board.LayerStack; Except End;
    If Stack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LAYERSTACK',
            'Could not obtain board LayerStack.');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject, eViaObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                Inc(Checked);
                If Obj.ObjectId = eViaObject Then KindStr := 'via'
                Else KindStr := 'pad';
                DesStr := '';
                If Obj.InComponent Then
                    Try DesStr := Obj.Component.Name.Text; Except End;
                NetStr := '';
                If Obj.InNet Then
                    Try NetStr := Obj.Net.Name; Except End;
                Try PadX := Obj.x; Except PadX := 0; End;
                Try PadY := Obj.y; Except PadY := 0; End;

                Layer := Stack.FirstLayer;
                While Layer <> Nil Do
                Begin
                    Try
                        LayerId := Layer.LayerID;
                        Removed := False;
                        If Obj.ObjectId = eViaObject Then
                        Begin
                            ViaObj := Obj;
                            If ViaObj.IntersectLayer(LayerId)
                               And (ViaObj.SizeOnLayer(LayerId) <= ViaObj.HoleSize) Then
                                Removed := True;
                        End
                        Else
                        Begin
                            Try
                                PadObj := Obj;
                                If PadObj.IsPadRemoved(LayerId) Then
                                    Removed := True;
                            Except End;
                        End;
                        If Removed Then
                        Begin
                            Inc(Violations);
                            LayerStr := '';
                            Try LayerStr := GetLayerString(LayerId); Except End;
                            If Not First Then ItemsJson := ItemsJson + ',';
                            First := False;
                            EntryJson :=
                                JsonStr('kind', KindStr) + ',' +
                                JsonStr('designator', DesStr) + ',' +
                                JsonStr('net', NetStr) + ',' +
                                JsonStr('layer', LayerStr) + ',' +
                                JsonStr('at', '(' + IntToStr(CoordToMils(PadX)) + ',' +
                                              IntToStr(CoordToMils(PadY)) + ')');
                            ItemsJson := ItemsJson + JsonObj(EntryJson);
                        End;
                    Except End;
                    Layer := Stack.NextLayer(Layer);
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindOffGridComponents                                                 }
{                                                                              }
{ Schematic best practice: components sit on the 100-mil snap grid (or       }
{ whatever the project's SnapGridSize is) so pins land predictably on the   }
{ wire grid. Off-grid placement is a classic "I edited around it and never  }
{ resnapped" trap -- the symbol LOOKS like it's wired but the pin doesn't   }
{ actually touch the wire, so ERC silently passes and the net never forms. }
{                                                                              }
{ Walks every placed component across the project's sheet sources (not     }
{ compiled docs -- placement is per-source) and flags any whose location   }
{ X or Y is not an integer multiple of grid_mils.                          }
{                                                                              }
{ Param: "grid_mils" (default 100). Set to e.g. 50 for non-standard sheets. }
{                                                                              }
{ Response: checked, violations, grid_mils echo, items where each entry    }
{ carries designator, sheet (filename), at coords (mils), and dx/dy        }
{ off-grid magnitudes.                                                     }
Function Audit_FindOffGridComponents(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    SchIter : ISch_Iterator;
    SchObj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Loc : TLocation;
    I, Checked, Violations, GridMils, XMils, YMils, Dx, Dy : Integer;
    GridStr, Designator, SheetName, DocKind, ItemsJson, EntryJson : String;
    First : Boolean;
Begin
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

    GridStr := ExtractJsonValue(Params, 'grid_mils');
    GridMils := StrToIntDef(GridStr, 100);
    If GridMils <= 0 Then GridMils := 100;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(I); Except End;
        If Doc = Nil Then Continue;
        DocKind := '';
        Try DocKind := Doc.DM_DocumentKind; Except End;
        If DocKind <> 'SCH' Then Continue;
        SheetName := '';
        Try SheetName := Doc.DM_FileName; Except End;

        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(Doc.DM_FullPath); Except End;
        If SchDoc = Nil Then Continue;

        SchIter := SchDoc.SchIterator_Create;
        If SchIter = Nil Then Continue;
        Try
            SchIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            SchObj := SchIter.FirstSchObject;
            While SchObj <> Nil Do
            Begin
                Try
                    Comp := SchObj;
                    Inc(Checked);
                    Loc := Comp.Location;
                    XMils := CoordToMils(Loc.X);
                    YMils := CoordToMils(Loc.Y);
                    Dx := XMils Mod GridMils;
                    Dy := YMils Mod GridMils;
                    { Mod can be negative on Pascal for negative coords; }
                    { take absolute. Components at negative coords (off }
                    { sheet origin) get treated symmetrically.            }
                    If Dx < 0 Then Dx := -Dx;
                    If Dy < 0 Then Dy := -Dy;
                    If (Dx <> 0) Or (Dy <> 0) Then
                    Begin
                        Inc(Violations);
                        Designator := '';
                        Try Designator := Comp.Designator.Text; Except End;
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('designator', Designator) + ',' +
                            JsonStr('sheet', SheetName) + ',' +
                            JsonStr('at', '(' + IntToStr(XMils) + ',' +
                                          IntToStr(YMils) + ')') + ',' +
                            JsonInt('dx', Dx) + ',' +
                            JsonInt('dy', Dy);
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                Except End;
                SchObj := SchIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(SchIter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonInt('grid_mils', GridMils) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindComponentsOutsideBoardOutline                                     }
{                                                                              }
{ Find any IPCB_Component whose origin sits OUTSIDE the board outline.       }
{ These are nearly always editing accidents -- the user grabbed a component }
{ and moved it off the PCB by mistake. Often hides from visual review        }
{ because it's outside the visible board area, and DRC doesn't flag it      }
{ (DRC checks rules between primitives, not "is this on the board").       }
{                                                                              }
{ Uses IPCB_BoardOutline.PrimitiveInsidePoly to do the polygon-inside test  }
{ against the actual outline geometry (handles non-rectangular boards).    }
{                                                                              }
{ Pattern: brett's SelectCMPInOutSideBOL.pas.                                 }
{                                                                              }
{ Response: checked, violations, items where each entry has designator,    }
{ at coords (mils), layer.                                                  }
Function Audit_FindComponentsOutsideBoardOutline(Params,
                                                  RequestId : String) : String;
Var
    Board : IPCB_Board;
    Outline : IPCB_BoardOutline;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Comp : IPCB_Component;
    Checked, Violations : Integer;
    Designator, LayerStr, ItemsJson, EntryJson : String;
    First, Inside : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Outline := Nil;
    Try Outline := Board.BoardOutline; Except End;
    If Outline = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_OUTLINE',
            'Board has no defined outline; cannot check inside / outside.');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

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
                Inc(Checked);
                Inside := False;
                Try Inside := Outline.PrimitiveInsidePoly(Comp); Except End;
                If Not Inside Then
                Begin
                    Inc(Violations);
                    Designator := '';
                    Try Designator := Comp.Name.Text; Except End;
                    LayerStr := '';
                    Try LayerStr := GetLayerString(Comp.Layer); Except End;
                    If Not First Then ItemsJson := ItemsJson + ',';
                    First := False;
                    EntryJson :=
                        JsonStr('designator', Designator) + ',' +
                        JsonStr('layer', LayerStr) + ',' +
                        JsonStr('at', '(' + IntToStr(CoordToMils(Comp.x)) + ',' +
                                      IntToStr(CoordToMils(Comp.y)) + ')');
                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindPadsNearBoardEdge                                                  }
{                                                                              }
{ Find pads / vias whose centre is closer than ``clearance_mils`` to the    }
{ board outline. Depaneling damage hazard: copper too close to the routed }
{ edge gets scuffed, scored, or torn during v-cut / mouse-bite break.      }
{ IPC-2221 / typical house rules want at least 20-50mil clearance for     }
{ critical components, more for connectors.                                  }
{                                                                              }
{ Uses Board.PrimPrimDistance(BoardOutline, pad) -- handles non-rect       }
{ outlines correctly.                                                       }
{                                                                              }
{ Param: clearance_mils (default 25).                                         }
{                                                                              }
{ Response: checked, violations, clearance_mils, items where each entry has}
{ kind (pad / via), designator (refdes), distance_mils, at coords.          }
Function Audit_FindPadsNearBoardEdge(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Outline : IPCB_BoardOutline;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Checked, Violations : Integer;
    ClearanceMils : Integer;
    Clearance : TCoord;
    Dist : TCoord;
    DistMils : Integer;
    KindStr, DesStr, ItemsJson, EntryJson : String;
    First : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;
    Outline := Nil;
    Try Outline := Board.BoardOutline; Except End;
    If Outline = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_OUTLINE',
            'Board has no defined outline; cannot measure edge clearance.');
        Exit;
    End;

    ClearanceMils := StrToIntDef(ExtractJsonValue(Params, 'clearance_mils'), 25);
    If ClearanceMils <= 0 Then ClearanceMils := 25;
    Clearance := MilsToCoord(ClearanceMils);

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject, eViaObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                Inc(Checked);
                Dist := -1;
                { PrimPrimDistance returns 0 for overlap, distance otherwise. }
                { For a pad INSIDE the outline polygon the distance to the    }
                { outline segments is the gap to the nearest edge, which is  }
                { exactly what we want.                                       }
                Try Dist := Board.PrimPrimDistance(Outline, Obj); Except End;
                If (Dist >= 0) And (Dist < Clearance) Then
                Begin
                    Inc(Violations);
                    DistMils := CoordToMils(Dist);
                    If Obj.ObjectId = eViaObject Then KindStr := 'via'
                    Else KindStr := 'pad';
                    DesStr := '';
                    If Obj.InComponent Then
                        Try DesStr := Obj.Component.Name.Text; Except End;
                    If Not First Then ItemsJson := ItemsJson + ',';
                    First := False;
                    EntryJson :=
                        JsonStr('kind', KindStr) + ',' +
                        JsonStr('designator', DesStr) + ',' +
                        JsonInt('distance_mils', DistMils) + ',' +
                        JsonStr('at', '(' + IntToStr(CoordToMils(Obj.x)) + ',' +
                                      IntToStr(CoordToMils(Obj.y)) + ')');
                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonInt('clearance_mils', ClearanceMils) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindMissingDatasheets                                                 }
{                                                                              }
{ Walk every IC (designator prefix "U") on compiled SCH docs. For each, look }
{ at its parameters and return True if ANY of the following carries a URL:  }
{   - HelpURL, Datasheet, DatasheetURL                                       }
{   - ComponentLink1URL..ComponentLink4URL                                   }
{ Flags any IC where none of those slots is populated -- the agent needs    }
{ at least one fetchable datasheet to do a real review.                      }
{                                                                              }
{ Pattern: dashboard's pickDatasheetLinks JS heuristic, ported as authoritative}
{ Pascal-side audit so the agent's lint sweep doesn't depend on rendering   }
{ the Components tab.                                                         }
{                                                                              }
{ Response: checked, violations, items where each entry has designator,    }
{ comment, lib_ref.                                                            }
Function Audit_FindMissingDatasheets(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    SchIter, ParamIter : ISch_Iterator;
    SchObj, ParamObj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Param : ISch_Parameter;
    I, DocCount, Checked, Violations : Integer;
    UsePhysical : Boolean;
    Designator, Comment, LibRef, ItemsJson, EntryJson, ParamName : String;
    First, HasDatasheet : Boolean;
Begin
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

    SmartCompile(Project);
    GetCompiledDocs(Project, DocCount, UsePhysical);

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;

        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(Doc.DM_FullPath); Except End;
        If SchDoc = Nil Then Continue;

        SchIter := SchDoc.SchIterator_Create;
        Try
            SchIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            SchObj := SchIter.FirstSchObject;
            While SchObj <> Nil Do
            Begin
                Try
                    Comp := SchObj;
                    Designator := '';
                    Try Designator := Comp.Designator.Text; Except End;
                    { Only audit ICs -- passives don't generally need a    }
                    { fetchable datasheet to review.                       }
                    If (Designator <> '') And (Length(Designator) > 0)
                       And (UpperCase(Copy(Designator, 1, 1)) = 'U') Then
                    Begin
                        Inc(Checked);
                        HasDatasheet := False;
                        ParamIter := Comp.SchIterator_Create;
                        Try
                            ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                            ParamObj := ParamIter.FirstSchObject;
                            While (ParamObj <> Nil) And (Not HasDatasheet) Do
                            Begin
                                Try
                                    Param := ParamObj;
                                    ParamName := '';
                                    Try ParamName := Param.Name; Except End;
                                    If (UpperCase(ParamName) = 'HELPURL')
                                       Or (UpperCase(ParamName) = 'DATASHEET')
                                       Or (UpperCase(ParamName) = 'DATASHEETURL')
                                       Or (Copy(UpperCase(ParamName), 1, 13) = 'COMPONENTLINK')
                                    Then
                                    Begin
                                        If (Param.Text <> '')
                                           And (Pos('://', Param.Text) > 0) Then
                                            HasDatasheet := True;
                                    End;
                                Except End;
                                ParamObj := ParamIter.NextSchObject;
                            End;
                        Finally
                            Comp.SchIterator_Destroy(ParamIter);
                        End;

                        If Not HasDatasheet Then
                        Begin
                            Inc(Violations);
                            Comment := '';
                            Try Comment := Comp.Comment.Text; Except End;
                            LibRef := '';
                            Try LibRef := Comp.LibReference; Except End;
                            If Not First Then ItemsJson := ItemsJson + ',';
                            First := False;
                            EntryJson :=
                                JsonStr('designator', Designator) + ',' +
                                JsonStr('comment', Comment) + ',' +
                                JsonStr('lib_ref', LibRef);
                            ItemsJson := ItemsJson + JsonObj(EntryJson);
                        End;
                    End;
                Except End;
                SchObj := SchIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(SchIter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindMpnInconsistencies                                                }
{                                                                              }
{ Group ICs by (lib_ref, comment) and flag any group where 2+ distinct       }
{ Manufacturer Part Numbers are assigned across its instances. Two           }
{ presumably-identical parts pointing at different MPNs is almost always   }
{ either a typo / accidental override during a clone, or means the design  }
{ genuinely wants two sources but lost the alternates table.               }
{                                                                              }
{ Walks compiled SCH docs. For each IC, reads the first non-empty           }
{ parameter whose name starts with "Manufacturer Part Number" (matches      }
{ the common IC-naming convention -- handles "Manufacturer                  }
{ Part Number 1" etc).                                                       }
{                                                                              }
{ Response: checked, violations, items[]; each item carries lib_ref,        }
{ comment, mpns (comma-joined), and designators (comma-joined).             }
Function Audit_FindMpnInconsistencies(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    SchIter, ParamIter : ISch_Iterator;
    SchObj, ParamObj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Param : ISch_Parameter;
    I, DocCount, Checked, Violations : Integer;
    UsePhysical : Boolean;
    Designator, Comment, LibRef, MPN, ParamName : String;
    { Pipe-delimited accumulator: "|libref|comment|mpn|designator|"        }
    Records, Token, Group, GroupKey : String;
    Mpn1, Mpn2, Pos1, Pos2, FieldStart : Integer;
    EntryJson, ItemsJson, GroupMpns, GroupDesigs : String;
    ReportedGroups : String;
    First : Boolean;
    SubMpn, SubDes : String;
    P1, P2 : Integer;
Begin
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

    SmartCompile(Project);
    GetCompiledDocs(Project, DocCount, UsePhysical);

    Checked := 0;
    Records := '';

    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;
        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(Doc.DM_FullPath); Except End;
        If SchDoc = Nil Then Continue;

        SchIter := SchDoc.SchIterator_Create;
        Try
            SchIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            SchObj := SchIter.FirstSchObject;
            While SchObj <> Nil Do
            Begin
                Try
                    Comp := SchObj;
                    Designator := '';
                    Try Designator := Comp.Designator.Text; Except End;
                    If (Designator <> '')
                       And (UpperCase(Copy(Designator, 1, 1)) = 'U') Then
                    Begin
                        Inc(Checked);
                        Comment := '';
                        LibRef := '';
                        MPN := '';
                        Try Comment := Comp.Comment.Text; Except End;
                        Try LibRef := Comp.LibReference; Except End;

                        ParamIter := Comp.SchIterator_Create;
                        Try
                            ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                            ParamObj := ParamIter.FirstSchObject;
                            While (ParamObj <> Nil) And (MPN = '') Do
                            Begin
                                Try
                                    Param := ParamObj;
                                    ParamName := '';
                                    Try ParamName := Param.Name; Except End;
                                    If Copy(UpperCase(ParamName), 1, 24)
                                       = 'MANUFACTURER PART NUMBER' Then
                                    Begin
                                        If Param.Text <> '' Then
                                            MPN := Param.Text;
                                    End;
                                Except End;
                                ParamObj := ParamIter.NextSchObject;
                            End;
                        Finally
                            Comp.SchIterator_Destroy(ParamIter);
                        End;

                        { Accumulate one record per IC that has both an   }
                        { MPN and a lib_ref (we can't group without both).}
                        If (MPN <> '') And ((LibRef <> '') Or (Comment <> '')) Then
                            Records := Records + '|' + LibRef + '~' + Comment
                                       + '~' + MPN + '~' + Designator;
                    End;
                Except End;
                SchObj := SchIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(SchIter);
        End;
    End;
    Records := Records + '|';

    { Walk records, build per-(libref, comment) groups, flag those with >1 }
    { distinct MPN. ReportedGroups dedupes so we emit each group only once.}
    Violations := 0;
    ItemsJson := '';
    First := True;
    ReportedGroups := '|';

    Pos1 := 2;
    While Pos1 <= Length(Records) Do
    Begin
        Pos2 := Pos1;
        While (Pos2 <= Length(Records)) And (Records[Pos2] <> '|') Do
            Inc(Pos2);
        If Pos2 <= Pos1 Then Break;
        Token := Copy(Records, Pos1, Pos2 - Pos1);
        Pos1 := Pos2 + 1;

        { Split Token into libref ~ comment ~ mpn ~ designator. }
        FieldStart := Pos('~', Token);
        If FieldStart <= 0 Then Continue;
        LibRef := Copy(Token, 1, FieldStart - 1);
        Token := Copy(Token, FieldStart + 1, Length(Token));
        FieldStart := Pos('~', Token);
        If FieldStart <= 0 Then Continue;
        Comment := Copy(Token, 1, FieldStart - 1);
        Token := Copy(Token, FieldStart + 1, Length(Token));
        FieldStart := Pos('~', Token);
        If FieldStart <= 0 Then Continue;
        MPN := Copy(Token, 1, FieldStart - 1);
        Designator := Copy(Token, FieldStart + 1, Length(Token));

        GroupKey := LibRef + '~~' + Comment;
        If Pos('|' + GroupKey + '|', ReportedGroups) > 0 Then Continue;

        { Walk all records again, accumulate MPN set + designator list  }
        { for this group.                                                }
        GroupMpns := '|';
        GroupDesigs := '';
        Mpn1 := 2;
        While Mpn1 <= Length(Records) Do
        Begin
            Mpn2 := Mpn1;
            While (Mpn2 <= Length(Records)) And (Records[Mpn2] <> '|') Do
                Inc(Mpn2);
            If Mpn2 <= Mpn1 Then Break;
            Group := Copy(Records, Mpn1, Mpn2 - Mpn1);
            Mpn1 := Mpn2 + 1;
            { Split Group fields. }
            P1 := Pos('~', Group);
            If P1 <= 0 Then Continue;
            If Copy(Group, 1, Length(GroupKey)) <> GroupKey Then Continue;
            { Skip past lib_ref + ~ + comment + ~. }
            P1 := P1 + Pos('~', Copy(Group, P1 + 1, Length(Group)));
            If P1 <= 0 Then Continue;
            SubMpn := Copy(Group, P1 + 1, Length(Group));
            P2 := Pos('~', SubMpn);
            If P2 <= 0 Then Continue;
            SubDes := Copy(SubMpn, P2 + 1, Length(SubMpn));
            SubMpn := Copy(SubMpn, 1, P2 - 1);
            If Pos('|' + SubMpn + '|', GroupMpns) = 0 Then
                GroupMpns := GroupMpns + SubMpn + '|';
            If GroupDesigs <> '' Then GroupDesigs := GroupDesigs + ',';
            GroupDesigs := GroupDesigs + SubDes;
        End;
        ReportedGroups := ReportedGroups + GroupKey + '|';

        { Count distinct MPNs (entries separated by | minus leading/trailing). }
        P1 := 0;
        For P2 := 1 To Length(GroupMpns) Do
            If GroupMpns[P2] = '|' Then Inc(P1);
        Dec(P1);  { Number of separators - 1 = number of MPN entries. }
        If P1 >= 2 Then
        Begin
            Inc(Violations);
            { Strip leading + trailing | from GroupMpns for display. }
            SubMpn := Copy(GroupMpns, 2, Length(GroupMpns) - 2);
            SubMpn := StringReplace(SubMpn, '|', ',', -1);
            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            EntryJson :=
                JsonStr('lib_ref', LibRef) + ',' +
                JsonStr('comment', Comment) + ',' +
                JsonStr('mpns', SubMpn) + ',' +
                JsonStr('designators', GroupDesigs);
            ItemsJson := ItemsJson + JsonObj(EntryJson);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindSinglePinNets                                                     }
{                                                                              }
{ Find nets with exactly ONE pin AND at least one net-label / port /          }
{ power-object. The pin-count alone isn't enough -- many legitimate           }
{ unconnected pins (DNF placeholders, test-points etc) have 0 or 1 pins. }
{ The combination "1 pin BUT also a label" means the designer ASSERTED       }
{ this net should connect to something but it doesn't.                       }
{                                                                              }
{ Walks Project.DM_DocumentFlattened (single flat netlist across all sheets) }
{ so a net spanning multiple sheets via off-page connectors counts as one    }
{ net.                                                                       }
{                                                                              }
{ Flags single-pin nets that carry a named label.                            }
{                                                                              }
{ Response: checked, violations, items where each entry has net, designator, }
{ pin (the single connection it has).                                         }
Function Audit_FindSinglePinNets(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    FlatDoc : IDocument;
    Net : INet;
    Pin : IPin;
    Part : IPart;
    I, Checked, Violations : Integer;
    NetName, Designator, PinNum, ItemsJson, EntryJson : String;
    First : Boolean;
Begin
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

    SmartCompile(Project);
    FlatDoc := Nil;
    Try FlatDoc := Project.DM_DocumentFlattened; Except End;
    If FlatDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FLAT_NETLIST',
            'Project has no flattened netlist (compile the project first).');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    For I := 0 To FlatDoc.DM_NetCount - 1 Do
    Begin
        Net := Nil;
        Try Net := FlatDoc.DM_Nets(I); Except End;
        If Net = Nil Then Continue;
        Inc(Checked);
        Try
            { Single-pin connection with at least one label / port /     }
            { power-object signals "this net was named but nothing else  }
            { is on it" -- a broken net, the real failure mode. Bare 1-  }
            { pin nets (no label) are usually legitimate (NC pins).      }
            If (Net.DM_PinCount = 1)
               And ((Net.DM_NetLabelCount > 0)
                    Or (Net.DM_PortCount > 0)
                    Or (Net.DM_PowerObjectCount > 0)) Then
            Begin
                Inc(Violations);
                NetName := '';
                Try NetName := Net.DM_NetName; Except End;
                Designator := '';
                PinNum := '';
                Pin := Nil;
                Try Pin := Net.DM_Pins(0); Except End;
                If Pin <> Nil Then
                Begin
                    Try Part := Pin.DM_Part; Except Part := Nil; End;
                    If Part <> Nil Then
                        Try Designator := Part.DM_LogicalDesignator; Except End;
                    Try PinNum := Pin.DM_PinNumber; Except End;
                End;
                If Not First Then ItemsJson := ItemsJson + ',';
                First := False;
                EntryJson :=
                    JsonStr('net', NetName) + ',' +
                    JsonStr('designator', Designator) + ',' +
                    JsonStr('pin', PinNum);
                ItemsJson := ItemsJson + JsonObj(EntryJson);
            End;
        Except End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindMixedDesignatorRotation                                          }
{                                                                              }
{ Assembly-readability check: on each silkscreen overlay (top, bottom)        }
{ designators should face only TWO of the four 90-degree rotations.          }
{ A board with both 0 and 180 (or both 90 and 270) on the same side forces   }
{ the assembly inspector to physically rotate the board while reading        }
{ designators -- slows down visual QC and reflow-defect triage.              }
{                                                                              }
{ Mixed 0+90 (or 0+270, etc) is fine; that's orthogonal layout. The bad      }
{ case is rotations that are 180 degrees apart, because then HALF the parts  }
{ read upside-down relative to the other half.                               }
{                                                                              }
{ Flags mixed designator rotations on a silkscreen overlay.                  }
{                                                                              }
{ Response: per-layer flags + offending component list.                       }
Function Audit_FindMixedDesignatorRotation(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
    DesigText : IPCB_Text;
    DesigLayer : TPCBString;
    Rot : Double;
    TopHas0, TopHas90, TopHas180, TopHas270 : Boolean;
    BotHas0, BotHas90, BotHas180, BotHas270 : Boolean;
    TopMixed0_180, TopMixed90_270 : Boolean;
    BotMixed0_180, BotMixed90_270 : Boolean;
    Checked, Violations : Integer;
    ItemsJson, EntryJson, CompName : String;
    First : Boolean;
    OnTop : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;

    TopHas0 := False; TopHas90 := False; TopHas180 := False; TopHas270 := False;
    BotHas0 := False; BotHas90 := False; BotHas180 := False; BotHas270 := False;
    Checked := 0;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);

        Comp := Iter.FirstPCBObject;
        While Comp <> Nil Do
        Begin
            Try
                DesigText := Nil;
                Try DesigText := Comp.Name; Except End;
                If DesigText <> Nil Then
                Begin
                    Inc(Checked);
                    DesigLayer := GetLayerString(DesigText.Layer);
                    Rot := DesigText.Rotation;
                    OnTop := DesigLayer = 'TopOverlay';
                    { Treat the rotation as discrete 0/90/180/270 even if  }
                    { the PCB stored a near-but-not-exact value -- this   }
                    { check is about orientation classes, not precision.  }
                    If OnTop Then
                    Begin
                        If (Rot < 1) Then TopHas0 := True
                        Else If (Rot > 89) And (Rot < 91) Then TopHas90 := True
                        Else If (Rot > 179) And (Rot < 181) Then TopHas180 := True
                        Else If (Rot > 269) And (Rot < 271) Then TopHas270 := True;
                    End
                    Else If DesigLayer = 'BottomOverlay' Then
                    Begin
                        If (Rot < 1) Then BotHas0 := True
                        Else If (Rot > 89) And (Rot < 91) Then BotHas90 := True
                        Else If (Rot > 179) And (Rot < 181) Then BotHas180 := True
                        Else If (Rot > 269) And (Rot < 271) Then BotHas270 := True;
                    End;
                End;
            Except End;
            Comp := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    TopMixed0_180  := TopHas0 And TopHas180;
    TopMixed90_270 := TopHas90 And TopHas270;
    BotMixed0_180  := BotHas0 And BotHas180;
    BotMixed90_270 := BotHas90 And BotHas270;

    Violations := 0;
    If TopMixed0_180 Then Inc(Violations);
    If TopMixed90_270 Then Inc(Violations);
    If BotMixed0_180 Then Inc(Violations);
    If BotMixed90_270 Then Inc(Violations);

    { Second pass: list which components contribute to a flagged pair so  }
    { the agent has something actionable -- which parts to rotate.        }
    ItemsJson := '';
    First := True;
    If Violations > 0 Then
    Begin
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Comp := Iter.FirstPCBObject;
            While Comp <> Nil Do
            Begin
                Try
                    DesigText := Nil;
                    Try DesigText := Comp.Name; Except End;
                    If DesigText <> Nil Then
                    Begin
                        DesigLayer := GetLayerString(DesigText.Layer);
                        Rot := DesigText.Rotation;
                        OnTop := DesigLayer = 'TopOverlay';
                        CompName := '';
                        Try CompName := DesigText.Text; Except End;
                        { Component is "in" the violation if its rotation }
                        { is one of the two on its side's flagged pair.   }
                        If OnTop
                           And ((TopMixed0_180 And ((Rot < 1) Or ((Rot > 179) And (Rot < 181))))
                                Or (TopMixed90_270 And (((Rot > 89) And (Rot < 91)) Or ((Rot > 269) And (Rot < 271))))) Then
                        Begin
                            If Not First Then ItemsJson := ItemsJson + ',';
                            First := False;
                            EntryJson :=
                                JsonStr('designator', CompName) + ',' +
                                JsonStr('layer', 'top_overlay') + ',' +
                                JsonInt('rotation_deg', Round(Rot));
                            ItemsJson := ItemsJson + JsonObj(EntryJson);
                        End
                        Else If (DesigLayer = 'BottomOverlay')
                                And ((BotMixed0_180 And ((Rot < 1) Or ((Rot > 179) And (Rot < 181))))
                                     Or (BotMixed90_270 And (((Rot > 89) And (Rot < 91)) Or ((Rot > 269) And (Rot < 271))))) Then
                        Begin
                            If Not First Then ItemsJson := ItemsJson + ',';
                            First := False;
                            EntryJson :=
                                JsonStr('designator', CompName) + ',' +
                                JsonStr('layer', 'bottom_overlay') + ',' +
                                JsonInt('rotation_deg', Round(Rot));
                            ItemsJson := ItemsJson + JsonObj(EntryJson);
                        End;
                    End;
                Except End;
                Comp := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonBool('top_mixed_0_180', TopMixed0_180) + ',' +
            JsonBool('top_mixed_90_270', TopMixed90_270) + ',' +
            JsonBool('bottom_mixed_0_180', BotMixed0_180) + ',' +
            JsonBool('bottom_mixed_90_270', BotMixed90_270) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindNonEmbeddedImages                                                 }
{                                                                              }
{ Find schematic images (eImage) that are NOT embedded. Non-embedded         }
{ images carry a path reference to the original file; they render fine on   }
{ the designer's machine but break (visible as a red X box) on any machine  }
{ that doesn't have the same file at the same path. Common offenders are    }
{ company-logo title-blocks dragged in from a network share, then never     }
{ re-saved with EmbedImage=True.                                            }
{                                                                              }
{ Flags schematic images that are not embedded.                              }
{                                                                              }
{ Walks all logical SCH documents in the project and reports each non-      }
{ embedded image with its sheet + coordinates.                              }
Function Audit_FindNonEmbeddedImages(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Img : ISch_Image;
    DocKind : String;
    Total, Bad : Integer;
    ItemsJson, EntryJson, SheetName : String;
    First : Boolean;
    LocX, LocY : Integer;
    Embedded : Boolean;
Begin
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

    Total := 0;
    Bad := 0;
    ItemsJson := '';
    First := True;

    For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
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

        Iter := Sheet.SchIterator_Create;
        If Iter = Nil Then Continue;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eImage));
            Obj := Iter.FirstSchObject;
            While Obj <> Nil Do
            Begin
                Try
                    Inc(Total);
                    Img := Obj;
                    Embedded := True;
                    Try Embedded := Img.EmbedImage; Except End;
                    If Not Embedded Then
                    Begin
                        Inc(Bad);
                        LocX := 0;
                        LocY := 0;
                        Try LocX := CoordToMils(Img.Location.X); Except End;
                        Try LocY := CoordToMils(Img.Location.Y); Except End;
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('sheet', SheetName) + ',' +
                            JsonInt('x_mils', LocX) + ',' +
                            JsonInt('y_mils', LocY);
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                Except End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Total) + ',' +
            JsonInt('violations', Bad) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindUnlockedComponentPrimitives                                       }
{                                                                              }
{ Find placed components whose internal primitives (pads, silkscreen,         }
{ courtyard) are NOT locked. Without primitive-lock, an accidental click +    }
{ drag in the PCB editor moves a single pad off-center inside the footprint  }
{ without any error message -- a silent, hard-to-spot fab bug. Fab houses    }
{ won't catch it (the gerbers look fine, just slightly off), but the part    }
{ won't solder properly because the pad is no longer aligned with the lead.  }
{                                                                              }
{ Each placed component carries a PrimitiveLock flag (set via Component       }
{ Properties dialog or right-click "Lock Component"). True means primitives   }
{ are anchored to the component's origin; False means they're freely movable.}
{                                                                              }
{ Flags placed components whose internal primitives are unlocked.            }
Function Audit_FindUnlockedComponentPrimitives(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
    Checked, Violations : Integer;
    ItemsJson, EntryJson, CompName : String;
    First, Locked : Boolean;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Comp := Iter.FirstPCBObject;
        While Comp <> Nil Do
        Begin
            Inc(Checked);
            Try
                Locked := True;
                Try Locked := Comp.PrimitiveLock; Except End;
                If Not Locked Then
                Begin
                    Inc(Violations);
                    CompName := '';
                    Try CompName := Comp.Name.Text; Except End;
                    If Not First Then ItemsJson := ItemsJson + ',';
                    First := False;
                    EntryJson := JsonStr('designator', CompName);
                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                End;
            Except End;
            Comp := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindMirroredPcbText                                                   }
{                                                                              }
{ Find free-floating PCB text (eTextObject, NOT designator/value strings)    }
{ that's mirrored on the wrong layer. On the top overlay, text must read     }
{ normally; on the bottom overlay, text must be mirrored so that when the    }
{ board is flipped, the bottom-side text reads correctly to the assembly    }
{ inspector. The reverse on either side is a fab-prep mistake.              }
{                                                                              }
{ Flags free-floating PCB text mirrored on the wrong overlay.                }
Function Audit_FindMirroredPcbText(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Text;
    Checked, Violations : Integer;
    ItemsJson, EntryJson, TextStr, LayerName, Reason : String;
    First, MirrorFlag : Boolean;
    LayerVal : TLayer;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eTextObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Inc(Checked);
            Try
                LayerVal := Obj.Layer;
                MirrorFlag := False;
                Try MirrorFlag := Obj.MirrorFlag; Except End;
                Reason := '';
                If (LayerVal = eTopOverlay) And MirrorFlag Then
                    Reason := 'top_overlay_text_is_mirrored'
                Else If (LayerVal = eBottomOverlay) And (Not MirrorFlag) Then
                    Reason := 'bottom_overlay_text_is_not_mirrored';
                If Reason <> '' Then
                Begin
                    Inc(Violations);
                    TextStr := '';
                    Try TextStr := Obj.Text; Except End;
                    LayerName := GetLayerString(LayerVal);
                    If Not First Then ItemsJson := ItemsJson + ',';
                    First := False;
                    EntryJson :=
                        JsonStr('text', TextStr) + ',' +
                        JsonStr('layer', LayerName) + ',' +
                        JsonStr('reason', Reason);
                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                End;
            Except End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindVisibleSupplierPN                                                 }
{                                                                              }
{ Find schematic components with a visible supplier-PN parameter.            }
{                                                                              }
{ BOM hygiene: only the manufacturer's MPN should appear on the SCH PDF.    }
{ Supplier part numbers (Digi-Key, Mouser, Newark, Arrow, RS, Farnell)      }
{ change over time as parts go obsolete and the supplier substitutes a      }
{ different package or a different bin, so an SCH PDF showing               }
{ "Digi-Key 296-1234-1-ND" can mislead the next person picking the BOM      }
{ five years from now. Manufacturer + MPN is stable; supplier PNs are not.  }
{                                                                              }
{ Detects any parameter whose name starts with "Supplier" (case-insensitive) }
{ AND IsHidden=False. Catches both legacy "Supplier Part Number 1" and any  }
{ free-text "Supplier" / "Supplier 1" variants designers create.             }
{                                                                              }
{ Flags visible supplier part-number parameters.                             }
Function Audit_FindVisibleSupplierPN(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    Iter, ParamIter : ISch_Iterator;
    Comp : ISch_Component;
    Param : ISch_Parameter;
    DocKind, ParamName, ParamNameLower, Designator, ParamText : String;
    Checked, Violations : Integer;
    ItemsJson, EntryJson : String;
    First, Hidden : Boolean;
Begin
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

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Document := Nil;
        Try Document := Project.DM_LogicalDocuments(DocI); Except End;
        If Document = Nil Then Continue;
        DocKind := '';
        Try DocKind := Document.DM_DocumentKind; Except End;
        If DocKind <> 'SCH' Then Continue;
        Sheet := Nil;
        Try Sheet := SchServer.GetSchDocumentByPath(Document.DM_FullPath); Except End;
        If Sheet = Nil Then Continue;

        Iter := Sheet.SchIterator_Create;
        If Iter = Nil Then Continue;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Comp := Iter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                Inc(Checked);
                Try
                    Designator := '';
                    Try Designator := Comp.Designator.Text; Except End;
                    ParamIter := Comp.SchIterator_Create;
                    If ParamIter <> Nil Then
                    Begin
                        Try
                            ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                            Param := ParamIter.FirstSchObject;
                            While Param <> Nil Do
                            Begin
                                Try
                                    ParamName := '';
                                    Try ParamName := Param.Name; Except End;
                                    ParamNameLower := AnsiLowerCase(ParamName);
                                    Hidden := True;
                                    Try Hidden := Param.IsHidden; Except End;
                                    { Match any parameter whose name starts   }
                                    { with "supplier" (case-insensitive).     }
                                    { This catches "Supplier Part Number 1", }
                                    { "Supplier", "Supplier 1", etc.          }
                                    If (Pos('supplier', ParamNameLower) = 1)
                                       And (Not Hidden) Then
                                    Begin
                                        Inc(Violations);
                                        ParamText := '';
                                        Try ParamText := Param.Text; Except End;
                                        If Not First Then ItemsJson := ItemsJson + ',';
                                        First := False;
                                        EntryJson :=
                                            JsonStr('designator', Designator) + ',' +
                                            JsonStr('parameter', ParamName) + ',' +
                                            JsonStr('value', ParamText);
                                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                                    End;
                                Except End;
                                Param := ParamIter.NextSchObject;
                            End;
                        Finally
                            Comp.SchIterator_Destroy(ParamIter);
                        End;
                    End;
                Except End;
                Comp := Iter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindOrphanNetLabels                                                   }
{                                                                              }
{ A net label labels whichever wire its (x, y) sits on. If the label is in   }
{ empty space (the wire was moved or never placed under it), Altium's        }
{ compile silently produces a phantom net -- the label looks correct on      }
{ paper but the wire under-cursor it should be on isn't there, so the         }
{ "labeled" signal connects to nothing.                                      }
{                                                                              }
{ This is a quiet, common bug class: ERC sometimes catches it (no driver,    }
{ no load) but not always (other-sheet end provides a load). Cheap check:    }
{ for each ISch_NetLabel, spatial-iterate a tiny area at the label location  }
{ for any eWire / eBus; absence → orphan.                                    }
Function Audit_FindOrphanNetLabels(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    Iter, SpatialIter : ISch_Iterator;
    Obj, Hit : ISch_GraphicalObject;
    NetLbl : ISch_NetLabel;
    DocKind, SheetName, LabelText : String;
    Loc : TLocation;
    LX, LY : Integer;
    Tol : Integer;
    Total, Bad : Integer;
    ItemsJson, EntryJson : String;
    First, OnWire : Boolean;
Begin
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

    Total := 0;
    Bad := 0;
    ItemsJson := '';
    First := True;
    { 1 mil tolerance: Altium snaps net labels to wire grid, so they're     }
    { either exactly on or visibly off. Tighter than that risks missing    }
    { sub-grid placements; looser risks false positives at junctions.     }
    Tol := MilsToCoord(1);

    For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
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

        Iter := Sheet.SchIterator_Create;
        If Iter = Nil Then Continue;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eNetLabel));
            Obj := Iter.FirstSchObject;
            While Obj <> Nil Do
            Begin
                Try
                    Inc(Total);
                    NetLbl := Obj;
                    Loc := NetLbl.GetState_Location;
                    LX := Loc.X;
                    LY := Loc.Y;
                    LabelText := '';
                    Try LabelText := NetLbl.Text; Except End;

                    OnWire := False;
                    SpatialIter := Sheet.SchIterator_Create;
                    If SpatialIter <> Nil Then
                    Begin
                        Try
                            SpatialIter.AddFilter_ObjectSet(MkSet(eWire, eBus));
                            SpatialIter.AddFilter_Area(
                                LX - Tol, LY - Tol, LX + Tol, LY + Tol);
                            Hit := SpatialIter.FirstSchObject;
                            If Hit <> Nil Then OnWire := True;
                        Finally
                            Sheet.SchIterator_Destroy(SpatialIter);
                        End;
                    End;

                    If Not OnWire Then
                    Begin
                        Inc(Bad);
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('label', LabelText) + ',' +
                            JsonStr('sheet', SheetName) + ',' +
                            JsonInt('x_mils', CoordToMils(LX)) + ',' +
                            JsonInt('y_mils', CoordToMils(LY));
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                Except End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Total) + ',' +
            JsonInt('violations', Bad) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindOrphanPowerObjects                                                }
{                                                                              }
{ Symmetric cousin to Audit_FindOrphanNetLabels: a power-port marker          }
{ (GND, VCC, etc.) carries its net name from the marker's Style; the marker  }
{ has to sit ON a wire for the wire to actually adopt that net. Markers in   }
{ empty space create phantom power connections -- the schematic looks like   }
{ the rail is hooked up but the wire underneath is unrelated to it.          }
{                                                                              }
{ ERC sometimes catches this when the wire ends up with no driver, but power}
{ rails frequently have multiple drivers (the actual one elsewhere on the   }
{ sheet, plus this phantom), so ERC sees a valid net and stays quiet.       }
Function Audit_FindOrphanPowerObjects(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    Iter, SpatialIter : ISch_Iterator;
    Obj, Hit : ISch_GraphicalObject;
    Power : ISch_PowerObject;
    DocKind, SheetName, NetName : String;
    Loc : TLocation;
    LX, LY, Tol : Integer;
    Total, Bad : Integer;
    ItemsJson, EntryJson : String;
    First, OnWire : Boolean;
Begin
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

    Total := 0;
    Bad := 0;
    ItemsJson := '';
    First := True;
    Tol := MilsToCoord(1);

    For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
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

        Iter := Sheet.SchIterator_Create;
        If Iter = Nil Then Continue;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePowerObject));
            Obj := Iter.FirstSchObject;
            While Obj <> Nil Do
            Begin
                Try
                    Inc(Total);
                    Power := Obj;
                    Loc := Power.GetState_Location;
                    LX := Loc.X;
                    LY := Loc.Y;
                    NetName := '';
                    Try NetName := Power.Text; Except End;

                    OnWire := False;
                    SpatialIter := Sheet.SchIterator_Create;
                    If SpatialIter <> Nil Then
                    Begin
                        Try
                            SpatialIter.AddFilter_ObjectSet(MkSet(eWire));
                            SpatialIter.AddFilter_Area(
                                LX - Tol, LY - Tol, LX + Tol, LY + Tol);
                            Hit := SpatialIter.FirstSchObject;
                            If Hit <> Nil Then OnWire := True;
                        Finally
                            Sheet.SchIterator_Destroy(SpatialIter);
                        End;
                    End;

                    If Not OnWire Then
                    Begin
                        Inc(Bad);
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('net', NetName) + ',' +
                            JsonStr('sheet', SheetName) + ',' +
                            JsonInt('x_mils', CoordToMils(LX)) + ',' +
                            JsonInt('y_mils', CoordToMils(LY));
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                Except End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Total) + ',' +
            JsonInt('violations', Bad) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindPlaceholderValues                                                 }
{                                                                              }
{ Scan SCH component parameters for obvious "I'll fix this later" strings    }
{ that escape into a fab release:                                            }
{   TBD, TODO, FIXME, XXX, ??, PLACEHOLDER, FILLER, ASK, UNKNOWN              }
{                                                                              }
{ The classic example: an EE drops in a 100nF cap as a placeholder while     }
{ working out a regulator topology, types "TBD" in the Comment field,        }
{ moves on, never comes back. Six months later the board ships to fab with  }
{ "TBD" on the assembly drawing. The fab calls confused, the schedule slips.}
{                                                                              }
{ Checks every parameter value (Comment, Value, Manufacturer, MPN, etc) on  }
{ every component, case-insensitive. Skips empty strings -- those are       }
{ "missing" and caught by other audits (find_missing_datasheets etc).        }
Function Audit_FindPlaceholderValues(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    Iter, ParamIter : ISch_Iterator;
    Comp : ISch_Component;
    Param : ISch_Parameter;
    DocKind, ParamName, ParamText, Designator, Upper : String;
    Total, Bad : Integer;
    ItemsJson, EntryJson : String;
    First : Boolean;
    IsPlaceholder : Boolean;
Begin
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

    Total := 0;
    Bad := 0;
    ItemsJson := '';
    First := True;

    For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Document := Nil;
        Try Document := Project.DM_LogicalDocuments(DocI); Except End;
        If Document = Nil Then Continue;
        DocKind := '';
        Try DocKind := Document.DM_DocumentKind; Except End;
        If DocKind <> 'SCH' Then Continue;
        Sheet := Nil;
        Try Sheet := SchServer.GetSchDocumentByPath(Document.DM_FullPath); Except End;
        If Sheet = Nil Then Continue;

        Iter := Sheet.SchIterator_Create;
        If Iter = Nil Then Continue;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Comp := Iter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                Try
                    Designator := '';
                    Try Designator := Comp.Designator.Text; Except End;
                    ParamIter := Comp.SchIterator_Create;
                    If ParamIter <> Nil Then
                    Begin
                        Try
                            ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                            Param := ParamIter.FirstSchObject;
                            While Param <> Nil Do
                            Begin
                                Try
                                    Inc(Total);
                                    ParamName := '';
                                    Try ParamName := Param.Name; Except End;
                                    ParamText := '';
                                    Try ParamText := Param.Text; Except End;
                                    Upper := AnsiUpperCase(Trim(ParamText));
                                    { Empty values are caught elsewhere -- we only flag      }
                                    { strings that scream "I forgot to set this".            }
                                    IsPlaceholder := False;
                                    If Upper <> '' Then
                                    Begin
                                        If (Upper = 'TBD') Or (Upper = 'TODO')
                                           Or (Upper = 'FIXME') Or (Upper = 'XXX')
                                           Or (Upper = '??') Or (Upper = '???')
                                           Or (Upper = 'PLACEHOLDER')
                                           Or (Upper = 'FILLER') Or (Upper = 'ASK')
                                           Or (Upper = 'UNKNOWN') Or (Upper = '?')
                                           Or (Upper = 'N/A') Or (Upper = 'NA')
                                           Or (Upper = 'TBA') Then
                                            IsPlaceholder := True;
                                    End;
                                    If IsPlaceholder Then
                                    Begin
                                        Inc(Bad);
                                        If Not First Then ItemsJson := ItemsJson + ',';
                                        First := False;
                                        EntryJson :=
                                            JsonStr('designator', Designator) + ',' +
                                            JsonStr('parameter', ParamName) + ',' +
                                            JsonStr('value', ParamText);
                                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                                    End;
                                Except End;
                                Param := ParamIter.NextSchObject;
                            End;
                        Finally
                            Comp.SchIterator_Destroy(ParamIter);
                        End;
                    End;
                Except End;
                Comp := Iter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Total) + ',' +
            JsonInt('violations', Bad) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindAcuteAngles                                                       }
{                                                                              }
{ Find pairs of same-net tracks that meet at an acute interior angle         }
{ (< 90°). At an acute corner, the etchant pools and over-etches the inside }
{ of the bend -- the trace narrows or breaks during fabrication. Standard   }
{ fab DFM rejects sub-90° corners on critical-thickness traces.              }
{                                                                              }
{ Algorithm (pure geometry, no IPC_AcuteAngleRule dependency):                }
{   - For each Track, get its two endpoints.                                  }
{   - Spatial-iterate a 1-mil square at each endpoint for other Tracks on   }
{     the same net.                                                          }
{   - Build direction vectors AWAY from the shared point.                    }
{   - Compute dot product: positive dot = interior angle < 90° = acute.     }
{   - Compute actual angle for the report payload via arccos.                }
{                                                                              }
{ Capped at 100 violations to keep responses manageable on dense boards.    }
Function Audit_FindAcuteAngles(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter, SpatIter : IPCB_BoardIterator;
    Track, Other : IPCB_Track;
    Obj : IPCB_Primitive;
    Tol : Integer;
    Total, Bad, Endpoint : Integer;
    ItemsJson, EntryJson, NetName, LayerName : String;
    First : Boolean;
    PX, PY : Integer;
    V1X, V1Y, V2X, V2Y : Double;
    L1, L2, Dot, CosTheta, ThetaDeg : Double;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;

    Tol := MilsToCoord(1);
    Total := 0;
    Bad := 0;
    ItemsJson := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.SignalLayers);
        Iter.AddFilter_Method(eProcessAll);
        Track := Iter.FirstPCBObject;
        While (Track <> Nil) And (Bad < 100) Do
        Begin
            Inc(Total);
            { Examine BOTH endpoints of this track for an acute join.       }
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

                    SpatIter := Board.SpatialIterator_Create;
                    Try
                        SpatIter.AddFilter_ObjectSet(MkSet(eTrackObject));
                        SpatIter.AddFilter_IPCB_LayerSet(MkSet(Track.Layer));
                        SpatIter.AddFilter_Area(
                            PX - Tol, PY - Tol, PX + Tol, PY + Tol);
                        Obj := SpatIter.FirstPCBObject;
                        While Obj <> Nil Do
                        Begin
                            Try
                                Other := Obj;
                                If (Other.I_ObjectAddress <> Track.I_ObjectAddress)
                                   And (Other.Net = Track.Net) Then
                                Begin
                                    { Find which of Other's endpoints is at (PX,PY) }
                                    { and build a vector pointing away from it.    }
                                    If (Abs(Other.X1 - PX) <= Tol)
                                       And (Abs(Other.Y1 - PY) <= Tol) Then
                                    Begin
                                        V2X := Other.X2 - PX;
                                        V2Y := Other.Y2 - PY;
                                    End
                                    Else If (Abs(Other.X2 - PX) <= Tol)
                                            And (Abs(Other.Y2 - PY) <= Tol) Then
                                    Begin
                                        V2X := Other.X1 - PX;
                                        V2Y := Other.Y1 - PY;
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
                                    Dot := V1X * V2X + V1Y * V2Y;
                                    { Positive dot product → vectors within 90°    }
                                    { of each other → interior angle < 90°.        }
                                    If Dot > 0 Then
                                    Begin
                                        CosTheta := Dot / (L1 * L2);
                                        If CosTheta > 1 Then CosTheta := 1;
                                        If CosTheta < -1 Then CosTheta := -1;
                                        ThetaDeg := ArcCos(CosTheta) * 180.0 / 3.14159265358979;
                                        Inc(Bad);
                                        NetName := '';
                                        Try If Track.Net <> Nil Then NetName := Track.Net.Name; Except End;
                                        LayerName := GetLayerString(Track.Layer);
                                        If Not First Then ItemsJson := ItemsJson + ',';
                                        First := False;
                                        EntryJson :=
                                            JsonStr('net', NetName) + ',' +
                                            JsonStr('layer', LayerName) + ',' +
                                            JsonInt('x_mils', CoordToMils(PX)) + ',' +
                                            JsonInt('y_mils', CoordToMils(PY)) + ',' +
                                            JsonFloat('angle_deg', ThetaDeg);
                                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                                        If Bad >= 100 Then Break;
                                    End;
                                End;
                            Except End;
                            Obj := SpatIter.NextPCBObject;
                        End;
                    Finally
                        Board.SpatialIterator_Destroy(SpatIter);
                    End;
                Except End;
                If Bad >= 100 Then Break;
            End;
            Track := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Total) + ',' +
            JsonInt('violations', Bad) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Audit_FindInconsistentTrackWidths                                           }
{                                                                              }
{ Walk every net on the board, collect the distinct track widths used on    }
{ that net, and flag nets where max_width / min_width > 2.0. The classic   }
{ bug pattern: a power rail (VCC / VDD / GND) routed at 20 mil, someone     }
{ adds a connection later using the editor's default 8 mil, the resulting   }
{ thin section becomes a thermal hotspot under load. Mixed-width signals   }
{ are usually fine, but a 2.5x+ ratio almost always means a forgotten      }
{ width update.                                                              }
{                                                                              }
{ Per violation: net, min_width_mils, max_width_mils, ratio,                }
{ first_thin_location (where the agent should look first).                  }
Function Audit_FindInconsistentTrackWidths(Params, RequestId : String) : String;

    Function MinMaxWidthOnNet(Net : IPCB_Net;
                               Var ThinX : Integer;
                               Var ThinY : Integer;
                               Var ThinW : Integer;
                               Var MaxW : Integer) : Boolean;
    Var
        GrIter : IPCB_GroupIterator;
        Prim : IPCB_Primitive;
        Track : IPCB_Track;
        W : Integer;
        First : Boolean;
    Begin
        ThinX := 0; ThinY := 0; ThinW := 0; MaxW := 0;
        Result := False;
        First := True;
        If Net = Nil Then Exit;
        GrIter := Net.GroupIterator_Create;
        Try
            GrIter.AddFilter_ObjectSet(MkSet(eTrackObject));
            GrIter.AddFilter_IPCB_LayerSet(LayerSet.SignalLayers);
            Prim := GrIter.FirstPCBObject;
            While Prim <> Nil Do
            Begin
                Try
                    Track := Prim;
                    W := Track.Width;
                    If W > 0 Then
                    Begin
                        Result := True;
                        If First Then
                        Begin
                            ThinW := W; MaxW := W;
                            ThinX := (Track.X1 + Track.X2) Div 2;
                            ThinY := (Track.Y1 + Track.Y2) Div 2;
                            First := False;
                        End
                        Else
                        Begin
                            If W < ThinW Then
                            Begin
                                ThinW := W;
                                ThinX := (Track.X1 + Track.X2) Div 2;
                                ThinY := (Track.Y1 + Track.Y2) Div 2;
                            End;
                            If W > MaxW Then MaxW := W;
                        End;
                    End;
                Except End;
                Prim := GrIter.NextPCBObject;
            End;
        Finally
            Net.GroupIterator_Destroy(GrIter);
        End;
    End;

Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Net : IPCB_Net;
    NetName : String;
    Items, Entry : String;
    First, HasTracks : Boolean;
    Total, Bad : Integer;
    MinW, MaxW, TX, TY : Integer;
    Ratio : Double;
Begin
    Board := GetPCBBoardAnywhere;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No PCB document focused');
        Exit;
    End;

    Total := 0;
    Bad := 0;
    Items := '';
    First := True;

    Iter := Board.BoardIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eNetObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Net := Iter.FirstPCBObject;
        While Net <> Nil Do
        Begin
            Try
                HasTracks := MinMaxWidthOnNet(Net, TX, TY, MinW, MaxW);
                If HasTracks Then
                Begin
                    Inc(Total);
                    If (MinW > 0) And (MaxW > 0) And (MaxW > 2 * MinW) Then
                    Begin
                        Inc(Bad);
                        NetName := '';
                        Try NetName := Net.Name; Except End;
                        Ratio := MaxW / MinW;
                        If Not First Then Items := Items + ',';
                        First := False;
                        Entry :=
                            JsonStr('net', NetName) + ',' +
                            JsonFloat('min_width_mils', CoordToMils(MinW)) + ',' +
                            JsonFloat('max_width_mils', CoordToMils(MaxW)) + ',' +
                            JsonFloat('ratio', Ratio) + ',' +
                            JsonInt('thin_x_mils', CoordToMils(TX)) + ',' +
                            JsonInt('thin_y_mils', CoordToMils(TY));
                        Items := Items + JsonObj(Entry);
                    End;
                End;
            Except End;
            Net := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Total) + ',' +
            JsonInt('violations', Bad) + ',' +
            JsonRaw('items', '[' + Items + ']')
        ));
End;


{ Audit_FindPadsCenterNotConnected                                            }
{                                                                              }
{ Find pads where a track touches the pad on a given signal layer (so the   }
{ net analyser thinks it is wired) but no track actually lands AT the pad's }
{ centre. Classic high-current power-pad bug: a wide pour or a stub bends  }
{ in and grazes the pad's edge, full-net DRC passes, but thermal relief is }
{ unreachable because the centre is bare copper. On thermal pads the part }
{ heats unevenly; on a power input the current crowds at the edge and the }
{ joint cracks under load.                                                  }
{                                                                              }
{ Algorithm:                                                                 }
{   For each pad with a net:                                                  }
{     Multi-layer (through-hole): for every signal layer, spatial-iterate    }
{     tracks within the pad's bounding rect on that layer. If ANY same-net  }
{     track touches the pad (PrimPrimDistance = 0) but NONE has an endpoint }
{     at (Pad.x, Pad.y), the centre is unconnected on that layer.           }
{     Single-layer (SMD): spatial-iterate tracks on the pad's own layer; if }
{     ANY same-net track touches the pad but NONE has an endpoint at the    }
{     pad centre, flag it.                                                  }
{                                                                              }
{ Read-only: the original selects and zooms; we just emit JSON so the agent }
{ can navigate. Defensive Try/Except around every API call -- this audit   }
{ runs against arbitrary PCBs and a malformed pad shouldn't kill the sweep.}
{                                                                              }
{ Response: checked, violations, items where each entry has designator,    }
{ pin (Pad.Name), net, layer, at (mils).                                   }
Function Audit_FindPadsCenterNotConnected(Params, RequestId : String) : String;
Var
    Board : IPCB_Board;
    Stack : IPCB_LayerStack;
    BIter : IPCB_BoardIterator;
    SIter : IPCB_SpatialIterator;
    Layer : IPCB_LayerObject;
    Obj : IPCB_Primitive;
    Pad : IPCB_Pad;
    Track : IPCB_Track;
    Rect : TCoordRect;
    PadLayer, LayerId, Checked, Violations : Integer;
    PadX, PadY : TCoord;
    DesStr, NetStr, LayerStr, PinStr, ItemsJson, EntryJson : String;
    PadNetName, TrackNetName : String;
    First, TrackTouches, EndpointAtCenter : Boolean;
Begin
    Board := Nil;
    Try Board := GetPCBBoardAnywhere; Except End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'No active PCB board. Open the .PcbDoc and try again.');
        Exit;
    End;

    Stack := Nil;
    Try Stack := Board.LayerStack_V7; Except End;
    If Stack = Nil Then
        Try Stack := Board.LayerStack; Except End;
    If Stack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LAYERSTACK',
            'Could not obtain board LayerStack.');
        Exit;
    End;

    Checked := 0;
    Violations := 0;
    ItemsJson := '';
    First := True;

    BIter := Board.BoardIterator_Create;
    Try
        BIter.AddFilter_ObjectSet(MkSet(ePadObject));
        BIter.AddFilter_LayerSet(AllLayers);
        BIter.AddFilter_Method(eProcessAll);
        Obj := BIter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            Try
                Pad := Obj;
                Inc(Checked);
                { Skip pads without a net -- there is nothing to verify.    }
                If Not Pad.InNet Then
                Begin
                    Obj := BIter.NextPCBObject;
                    Continue;
                End;
                PadNetName := '';
                Try PadNetName := Pad.Net.Name; Except End;
                If PadNetName = '' Then
                Begin
                    Obj := BIter.NextPCBObject;
                    Continue;
                End;

                Try PadX := Pad.x; Except PadX := 0; End;
                Try PadY := Pad.y; Except PadY := 0; End;
                PadLayer := eNoLayer;
                Try PadLayer := Pad.Layer; Except End;

                DesStr := '';
                If Pad.InComponent Then
                    Try DesStr := Pad.Component.Name.Text; Except End;
                PinStr := '';
                Try PinStr := Pad.Name; Except End;
                NetStr := PadNetName;

                If PadLayer = eMultiLayer Then
                Begin
                    { Through-hole pad: check every signal layer.             }
                    Layer := Stack.FirstLayer;
                    While Layer <> Nil Do
                    Begin
                        Try
                            LayerId := Layer.LayerID;
                            If ILayer.IsSignalLayer(LayerId) Then
                            Begin
                                Try Rect := Pad.BoundingRectangleOnLayer(LayerId);
                                Except Rect.Left := 0; Rect.Right := 0;
                                       Rect.Top := 0; Rect.Bottom := 0; End;

                                TrackTouches := False;
                                EndpointAtCenter := False;
                                SIter := Board.SpatialIterator_Create;
                                Try
                                    SIter.AddFilter_ObjectSet(MkSet(eTrackObject));
                                    SIter.AddFilter_Area(Rect.Left, Rect.Bottom,
                                                          Rect.Right, Rect.Top);
                                    SIter.AddFilter_LayerSet(MkSet(LayerId));
                                    Track := SIter.FirstPCBObject;
                                    While Track <> Nil Do
                                    Begin
                                        Try
                                            If Track.InNet Then
                                            Begin
                                                TrackNetName := '';
                                                Try TrackNetName := Track.Net.Name;
                                                Except End;
                                                If TrackNetName = PadNetName Then
                                                Begin
                                                    Try
                                                        If Board.PrimPrimDistance(Track, Pad) = 0 Then
                                                            TrackTouches := True;
                                                    Except End;
                                                    If ((Track.x1 = PadX) And (Track.y1 = PadY))
                                                       Or ((Track.x2 = PadX) And (Track.y2 = PadY)) Then
                                                        EndpointAtCenter := True;
                                                End;
                                            End;
                                        Except End;
                                        Track := SIter.NextPCBObject;
                                    End;
                                Finally
                                    Board.SpatialIterator_Destroy(SIter);
                                End;

                                If TrackTouches And (Not EndpointAtCenter) Then
                                Begin
                                    Inc(Violations);
                                    LayerStr := '';
                                    Try LayerStr := GetLayerString(LayerId); Except End;
                                    If Not First Then ItemsJson := ItemsJson + ',';
                                    First := False;
                                    EntryJson :=
                                        JsonStr('designator', DesStr) + ',' +
                                        JsonStr('pin', PinStr) + ',' +
                                        JsonStr('net', NetStr) + ',' +
                                        JsonStr('layer', LayerStr) + ',' +
                                        JsonStr('at', '(' + IntToStr(CoordToMils(PadX)) + ',' +
                                                      IntToStr(CoordToMils(PadY)) + ')');
                                    ItemsJson := ItemsJson + JsonObj(EntryJson);
                                End;
                            End;
                        Except End;
                        Layer := Stack.NextLayer(Layer);
                    End;
                End
                Else If (PadLayer = eTopLayer) Or (PadLayer = eBottomLayer) Then
                Begin
                    { SMD pad: check tracks on the pad's own layer.            }
                    Try Rect := Pad.BoundingRectangleOnLayer(PadLayer);
                    Except Rect.Left := 0; Rect.Right := 0;
                           Rect.Top := 0; Rect.Bottom := 0; End;

                    TrackTouches := False;
                    EndpointAtCenter := False;
                    SIter := Board.SpatialIterator_Create;
                    Try
                        SIter.AddFilter_ObjectSet(MkSet(eTrackObject));
                        SIter.AddFilter_Area(Rect.Left, Rect.Bottom,
                                              Rect.Right, Rect.Top);
                        SIter.AddFilter_LayerSet(MkSet(PadLayer));
                        Track := SIter.FirstPCBObject;
                        While Track <> Nil Do
                        Begin
                            Try
                                If Track.InNet Then
                                Begin
                                    TrackNetName := '';
                                    Try TrackNetName := Track.Net.Name; Except End;
                                    If TrackNetName = PadNetName Then
                                    Begin
                                        Try
                                            If Board.PrimPrimDistance(Track, Pad) = 0 Then
                                                TrackTouches := True;
                                        Except End;
                                        If ((Track.x1 = PadX) And (Track.y1 = PadY))
                                           Or ((Track.x2 = PadX) And (Track.y2 = PadY)) Then
                                            EndpointAtCenter := True;
                                    End;
                                End;
                            Except End;
                            Track := SIter.NextPCBObject;
                        End;
                    Finally
                        Board.SpatialIterator_Destroy(SIter);
                    End;

                    If TrackTouches And (Not EndpointAtCenter) Then
                    Begin
                        Inc(Violations);
                        LayerStr := '';
                        Try LayerStr := GetLayerString(PadLayer); Except End;
                        If Not First Then ItemsJson := ItemsJson + ',';
                        First := False;
                        EntryJson :=
                            JsonStr('designator', DesStr) + ',' +
                            JsonStr('pin', PinStr) + ',' +
                            JsonStr('net', NetStr) + ',' +
                            JsonStr('layer', LayerStr) + ',' +
                            JsonStr('at', '(' + IntToStr(CoordToMils(PadX)) + ',' +
                                          IntToStr(CoordToMils(PadY)) + ')');
                        ItemsJson := ItemsJson + JsonObj(EntryJson);
                    End;
                End;
            Except End;
            Obj := BIter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(BIter);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Checked) + ',' +
            JsonInt('violations', Violations) + ',' +
            JsonRaw('items', '[' + ItemsJson + ']')
        ));
End;


{ Dispatcher entry for `audit.*` commands.                                    }
{..............................................................................}
{ Audit_FindNetLabelConflicts                                                 }
{                                                                              }
{ Three related failure modes that all look fine on a printed sheet:          }
{                                                                              }
{  1. CONFLICT: two net labels with DIFFERENT text at the same (x, y).        }
{     Altium merges both names into one net and one name wins. The losing     }
{     net silently ceases to exist and everything on it is absorbed. This is  }
{     a real short between two named nets.                                    }
{                                                                              }
{  2. ON_PIN_ROOT: a label sitting on a pin Location rather than on its       }
{     electrical end. Pin.Location is the BODY-side root; the pin connects at }
{     Location + PinLength along Orientation. A label on the root is inert,   }
{     so the sheet reads as wired while the pin floats on an auto-net.        }
{                                                                              }
{  3. DUPLICATE: two labels with the SAME text at one point. Harmless         }
{     electrically, but it is clutter and it hides class 1 underneath.        }
{                                                                              }
{ Orientation convention matches Generic.pas: 0=right(+x) 1=up(+y)            }
{ 2=left(-x) 3=down(-y).                                                      }
{..............................................................................}

Function Audit_FindNetLabelConflicts(Params, RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    Iter, SpatIter, PinIter : ISch_Iterator;
    Obj, Hit : ISch_GraphicalObject;
    NetLbl, OtherLbl : ISch_NetLabel;
    Pin : ISch_Pin;
    DocKind, SheetName, LabelText, OtherText : String;
    PinDesig, PinNum, OwnerDesig : String;
    Loc, PinLoc, OtherLoc : TLocation;
    LX, LY, PX, PY, CX, CY, PinLen, PinOrient : Integer;
    Tol : Integer;
    Total, NConf, NRoot, NDup : Integer;
    ConfJson, RootJson, DupJson, EntryJson : String;
    FirstC, FirstR, FirstD : Boolean;
    FoundConf, FoundDup, FoundRoot : Boolean;
Begin
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

    Total := 0;
    NConf := 0;
    NRoot := 0;
    NDup := 0;
    ConfJson := '';
    RootJson := '';
    DupJson := '';
    FirstC := True;
    FirstR := True;
    FirstD := True;
    Tol := MilsToCoord(1);

    For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
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

        Iter := Sheet.SchIterator_Create;
        If Iter = Nil Then Continue;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eNetLabel));
            Obj := Iter.FirstSchObject;
            While Obj <> Nil Do
            Begin
                Try
                    Inc(Total);
                    NetLbl := Obj;
                    Loc := NetLbl.GetState_Location;
                    LX := Loc.X;
                    LY := Loc.Y;
                    LabelText := '';
                    Try LabelText := NetLbl.Text; Except End;

                    FoundConf := False;
                    FoundDup := False;
                    OtherText := '';

                    SpatIter := Sheet.SchIterator_Create;
                    If SpatIter <> Nil Then
                    Begin
                        Try
                            SpatIter.AddFilter_ObjectSet(MkSet(eNetLabel));
                            { AddFilter_Area matches text bounding boxes, not }
                            { Location. Adjacent 100-mil pin-pitch labels in  }
                            { a column otherwise report as conflicts.         }
                            SpatIter.AddFilter_Area(LX - Tol, LY - Tol, LX + Tol, LY + Tol);
                            Hit := SpatIter.FirstSchObject;
                            While Hit <> Nil Do
                            Begin
                                If Hit <> Obj Then
                                Begin
                                    OtherLbl := Hit;
                                    Try
                                        OtherLoc := OtherLbl.GetState_Location;
                                        If CoordWithinTol(OtherLoc.X, LX, Tol) And
                                           CoordWithinTol(OtherLoc.Y, LY, Tol) Then
                                        Begin
                                            If OtherLbl.Text <> LabelText Then
                                            Begin
                                                FoundConf := True;
                                                If OtherText = '' Then OtherText := OtherLbl.Text;
                                            End
                                            Else
                                                FoundDup := True;
                                        End;
                                    Except End;
                                End;
                                Hit := SpatIter.NextSchObject;
                            End;
                        Finally
                            Sheet.SchIterator_Destroy(SpatIter);
                        End;
                    End;

                    If FoundConf Then
                    Begin
                        Inc(NConf);
                        If Not FirstC Then ConfJson := ConfJson + ',';
                        FirstC := False;
                        EntryJson :=
                            JsonStr('label', LabelText) + ',' +
                            JsonStr('conflicts_with', OtherText) + ',' +
                            JsonStr('sheet', SheetName) + ',' +
                            JsonInt('x_mils', CoordToMils(LX)) + ',' +
                            JsonInt('y_mils', CoordToMils(LY));
                        ConfJson := ConfJson + JsonObj(EntryJson);
                    End;

                    If FoundDup Then
                    Begin
                        Inc(NDup);
                        If Not FirstD Then DupJson := DupJson + ',';
                        FirstD := False;
                        EntryJson :=
                            JsonStr('label', LabelText) + ',' +
                            JsonStr('sheet', SheetName) + ',' +
                            JsonInt('x_mils', CoordToMils(LX)) + ',' +
                            JsonInt('y_mils', CoordToMils(LY));
                        DupJson := DupJson + JsonObj(EntryJson);
                    End;

                    FoundRoot := False;
                    PinDesig := '';
                    PinNum := '';
                    OwnerDesig := '';
                    CX := 0;
                    CY := 0;
                    PinIter := Sheet.SchIterator_Create;
                    If PinIter <> Nil Then
                    Begin
                        Try
                            PinIter.AddFilter_ObjectSet(MkSet(ePin));
                            PinIter.AddFilter_Area(LX - Tol, LY - Tol, LX + Tol, LY + Tol);
                            Hit := PinIter.FirstSchObject;
                            While Hit <> Nil Do
                            Begin
                                Try
                                    Pin := Hit;
                                    PinLoc := Pin.GetState_Location;
                                    PX := PinLoc.X;
                                    PY := PinLoc.Y;
                                    If (Abs(PX - LX) <= Tol) And (Abs(PY - LY) <= Tol) Then
                                    Begin
                                        PinLen := 0;
                                        PinOrient := 0;
                                        Try PinLen := Pin.PinLength; Except End;
                                        Try PinOrient := Pin.Orientation; Except End;
                                        CX := PX;
                                        CY := PY;
                                        If PinOrient = 0 Then CX := PX + PinLen
                                        Else If PinOrient = 1 Then CY := PY + PinLen
                                        Else If PinOrient = 2 Then CX := PX - PinLen
                                        Else If PinOrient = 3 Then CY := PY - PinLen;
                                        If (CX <> PX) Or (CY <> PY) Then
                                        Begin
                                            FoundRoot := True;
                                            Try PinNum := Pin.Designator; Except End;
                                            Try OwnerDesig := Pin.OwnerSchComponent.Designator.Text; Except End;
                                        End;
                                    End;
                                Except End;
                                Hit := PinIter.NextSchObject;
                            End;
                        Finally
                            Sheet.SchIterator_Destroy(PinIter);
                        End;
                    End;

                    If FoundRoot Then
                    Begin
                        Inc(NRoot);
                        If Not FirstR Then RootJson := RootJson + ',';
                        FirstR := False;
                        PinDesig := OwnerDesig + '.' + PinNum;
                        EntryJson :=
                            JsonStr('label', LabelText) + ',' +
                            JsonStr('pin', PinDesig) + ',' +
                            JsonStr('sheet', SheetName) + ',' +
                            JsonInt('x_mils', CoordToMils(LX)) + ',' +
                            JsonInt('y_mils', CoordToMils(LY)) + ',' +
                            JsonInt('connect_x_mils', CoordToMils(CX)) + ',' +
                            JsonInt('connect_y_mils', CoordToMils(CY));
                        RootJson := RootJson + JsonObj(EntryJson);
                    End;
                Except End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('checked', Total) + ',' +
            JsonInt('conflicts', NConf) + ',' +
            JsonInt('on_pin_root', NRoot) + ',' +
            JsonInt('duplicates', NDup) + ',' +
            JsonRaw('conflicting_labels', '[' + ConfJson + ']') + ',' +
            JsonRaw('labels_on_pin_root', '[' + RootJson + ']') + ',' +
            JsonRaw('duplicate_labels', '[' + DupJson + ']')
        ));
End;


Function HandleAuditCommand(Action : String; Params : String;
                             RequestId : String) : String;
Begin
    If Action = 'validate_component_params' Then
        Result := Audit_ValidateComponentParams(Params, RequestId)
    Else If Action = 'power_port_orientation' Then
        Result := Audit_PowerPortOrientation(Params, RequestId)
    Else If Action = 'tented_via_ratio' Then
        Result := Audit_TentedViaRatio(Params, RequestId)
    Else If Action = 'find_floating_ports' Then
        Result := Audit_FindFloatingPorts(Params, RequestId)
    Else If Action = 'find_bad_connections' Then
        Result := Audit_FindBadConnections(Params, RequestId)
    Else If Action = 'find_signal_vias_without_return' Then
        Result := Audit_FindSignalViasWithoutReturn(Params, RequestId)
    Else If Action = 'find_invalid_regions' Then
        Result := Audit_FindInvalidRegions(Params, RequestId)
    Else If Action = 'variant_not_fitted' Then
        Result := Audit_VariantNotFitted(Params, RequestId)
    Else If Action = 'find_unmatched_ports' Then
        Result := Audit_FindUnmatchedPorts(Params, RequestId)
    Else If Action = 'find_via_antennas' Then
        Result := Audit_FindViaAntennas(Params, RequestId)
    Else If Action = 'find_designator_collisions' Then
        Result := Audit_FindDesignatorCollisions(Params, RequestId)
    Else If Action = 'find_removed_pad_shapes' Then
        Result := Audit_FindRemovedPadShapes(Params, RequestId)
    Else If Action = 'find_off_grid_components' Then
        Result := Audit_FindOffGridComponents(Params, RequestId)
    Else If Action = 'find_components_outside_board_outline' Then
        Result := Audit_FindComponentsOutsideBoardOutline(Params, RequestId)
    Else If Action = 'find_pads_near_board_edge' Then
        Result := Audit_FindPadsNearBoardEdge(Params, RequestId)
    Else If Action = 'find_missing_datasheets' Then
        Result := Audit_FindMissingDatasheets(Params, RequestId)
    Else If Action = 'find_mpn_inconsistencies' Then
        Result := Audit_FindMpnInconsistencies(Params, RequestId)
    Else If Action = 'find_single_pin_nets' Then
        Result := Audit_FindSinglePinNets(Params, RequestId)
    Else If Action = 'find_mixed_designator_rotation' Then
        Result := Audit_FindMixedDesignatorRotation(Params, RequestId)
    Else If Action = 'find_non_embedded_images' Then
        Result := Audit_FindNonEmbeddedImages(Params, RequestId)
    Else If Action = 'find_unlocked_component_primitives' Then
        Result := Audit_FindUnlockedComponentPrimitives(Params, RequestId)
    Else If Action = 'find_mirrored_pcb_text' Then
        Result := Audit_FindMirroredPcbText(Params, RequestId)
    Else If Action = 'find_visible_supplier_pn' Then
        Result := Audit_FindVisibleSupplierPN(Params, RequestId)
    Else If Action = 'find_orphan_net_labels' Then
        Result := Audit_FindOrphanNetLabels(Params, RequestId)
    Else If Action = 'find_net_label_conflicts' Then
        Result := Audit_FindNetLabelConflicts(Params, RequestId)
    Else If Action = 'find_orphan_power_objects' Then
        Result := Audit_FindOrphanPowerObjects(Params, RequestId)
    Else If Action = 'find_placeholder_values' Then
        Result := Audit_FindPlaceholderValues(Params, RequestId)
    Else If Action = 'find_acute_angles' Then
        Result := Audit_FindAcuteAngles(Params, RequestId)
    Else If Action = 'find_inconsistent_track_widths' Then
        Result := Audit_FindInconsistentTrackWidths(Params, RequestId)
    Else If Action = 'find_pads_center_not_connected' Then
        Result := Audit_FindPadsCenterNotConnected(Params, RequestId)
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_COMMAND',
            'Unknown audit action: ' + Action);
End;
