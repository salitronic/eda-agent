{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Library.pas - Library management functions for the Altium integration bridge                }
{..............................................................................}

{ Return a component's first linked implementation (the footprint model in   }
{ the common case). Altium does not expose a "current implementation"         }
{ getter, GetState_CurrentImplementation is not a real method, implementations}
{ are reached only through the component's child-object iterator. Nil when    }
{ the component has no implementations.                                        }
Function GetFirstSchImplementation(Comp : ISch_Component) : ISch_Implementation;
Var
    ImplIter : ISch_Iterator;
Begin
    Result := Nil;
    If Comp = Nil Then Exit;
    Try
        ImplIter := Comp.SchIterator_Create;
        If ImplIter <> Nil Then
        Begin
            ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
            Result := ImplIter.FirstSchObject;
            Comp.SchIterator_Destroy(ImplIter);
        End;
    Except
    End;
End;

{ Build a JSON array of a component's models (implementations): the footprint  }
{ / SPICE / 3D links, each with model_name, model_type, is_current, and the    }
{ datafile_links that are the model's SOURCE (entity_name, file_kind,          }
{ location). Empty array when the component carries no models. Every property  }
{ read is guarded so a malformed link never drops the whole model list.        }
Function BuildImplementationsJson(Comp : ISch_Component) : String;
Var
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    Link : ISch_ModelDatafileLink;
    ModelName, ModelType, LinksJson, Entity, FileKind, Loc, ModelsJson : String;
    IsCur, First, LinkFirst, UseLib : Boolean;
    J, LinkCount : Integer;
Begin
    Result := '[]';
    If Comp = Nil Then Exit;
    ImplIter := Comp.SchIterator_Create;
    If ImplIter = Nil Then Exit;
    ModelsJson := '[';
    First := True;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            ModelName := '';
            ModelType := '';
            IsCur := False;
            Try ModelName := Impl.ModelName; Except End;
            Try ModelType := Impl.ModelType; Except End;
            Try IsCur := Impl.IsCurrent; Except End;
            UseLib := True;
            Try UseLib := Impl.UseComponentLibrary; Except End;

            LinksJson := '[';
            LinkFirst := True;
            LinkCount := 0;
            Try LinkCount := Impl.DatafileLinkCount; Except LinkCount := 0; End;
            For J := 0 To LinkCount - 1 Do
            Begin
                Link := Nil;
                Try Link := Impl.DatafileLink[J]; Except End;
                If Link = Nil Then Continue;
                Entity := '';
                FileKind := '';
                Loc := '';
                Try Entity := Link.EntityName; Except End;
                Try FileKind := Link.FileKind; Except End;
                Try Loc := Link.Location; Except End;
                If Not LinkFirst Then LinksJson := LinksJson + ',';
                LinkFirst := False;
                LinksJson := LinksJson +
                    '{"entity_name":"' + EscapeJsonString(Entity) + '"' +
                    ',"file_kind":"' + EscapeJsonString(FileKind) + '"' +
                    ',"location":"' + EscapeJsonString(Loc) + '"}';
            End;
            LinksJson := LinksJson + ']';

            If Not First Then ModelsJson := ModelsJson + ',';
            First := False;
            ModelsJson := ModelsJson +
                '{"model_name":"' + EscapeJsonString(ModelName) + '"' +
                ',"model_type":"' + EscapeJsonString(ModelType) + '"' +
                ',"is_current":' + BoolToJsonStr(IsCur) +
                ',"use_component_library":' + BoolToJsonStr(UseLib) +
                ',"datafile_links":' + LinksJson + '}';

            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(ImplIter);
    End;
    ModelsJson := ModelsJson + ']';
    Result := ModelsJson;
End;

{ Set the part ownership fields on a primitive so the lib editor knows     }
{ which part of the component it belongs to. Per Altium's official         }
{ createcomp_in_lib.pas reference, primitives without OwnerPartId /        }
{ OwnerPartDisplayMode are added to the component's collection but the    }
{ editor can't display them, symbols appear empty.                        }
Procedure SetOwnerPart(Obj : ISch_GraphicalObject; Component : ISch_Component);
Begin
    If Obj = Nil Then Exit;
    If Component <> Nil Then
    Begin
        Try Obj.OwnerPartId := Component.CurrentPartID; Except End;
        Try Obj.OwnerPartDisplayMode := Component.DisplayMode; Except End;
    End
    Else
    Begin
        Try Obj.OwnerPartId := 1; Except End;
        Try Obj.OwnerPartDisplayMode := 0; Except End;
    End;
End;

{ Resolve the target component for a Lib_Add* primitive helper.             }
{                                                                              }
{ SchLib.CurrentSchComponent in DelphiScript reflects the editor's selected }
{ component, which doesn't update when we add a new component via           }
{ AddSchComponent (the setter is a no-op). Trusting it would attach        }
{ primitives to whatever the editor was showing first (usually the default  }
{ Component_1 placeholder), leaving every newly-created symbol empty.       }
{                                                                              }
{ Use the global LastCreatedLibComponent we set in Lib_CreateSymbol         }
{ instead, falling back to CurrentSchComponent only if nothing has been     }
{ created in this session.                                                  }
Function GetTargetLibComponent(SchLib : ISch_Lib) : ISch_Component;
Begin
    Result := LastCreatedLibComponent;
    If Result = Nil Then
    Begin
        If SchLib <> Nil Then
            Result := SchLib.CurrentSchComponent;
    End;
End;

{ Mark the focused SchLib doc dirty without an immediate full-file save.    }
{ DoFileSave on a multi-MB SchLib costs hundreds of milliseconds to seconds }
{ per call, so doing it from every singular mutation (lib_add_pin,          }
{ lib_set_component_description, lib_link_footprint, ...) made one-symbol-  }
{ at-a-time editing unusable. Mirror the project-side deferred-save pattern }
{ (perf_deferred_save): mutations only flag dirty, and `save_all` /         }
{ SaveAllDirty flushes the .SchLib to disk at a logical checkpoint. The     }
{ workspace's free-document save sweep already covers standalone libs, so   }
{ no save_all changes are needed.                                            }
Procedure MarkLibDirty(SchLib : ISch_Lib);
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    FullPath : String;
    ServerDoc : IServerDocument;
Begin
    If SchLib = Nil Then Exit;
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Workspace.DM_FocusedDocument;
        If Doc <> Nil Then
        Begin
            FullPath := '';
            Try FullPath := Doc.DM_FullPath; Except End;
            If FullPath <> '' Then
            Begin
                ServerDoc := Client.GetDocumentByPath(FullPath);
                If ServerDoc <> Nil Then
                    Try ServerDoc.SetModified(True); Except End;
            End;
        End;
    End;
    { Force a SchLib editor redraw -- without this, primitives that were just }
    { committed (lines, rectangles, pins, polygons, arcs added by Lib_Add*)   }
    { are saved to memory + disk but the open lib editor window doesn't show  }
    { them until the user manually closes and reopens the symbol. The         }
    { SchLib editor renders the CURRENT COMPONENT, not the lib document, so   }
    { SchLib.GraphicallyInvalidate alone is insufficient. Invalidate the      }
    { component too, and process pending paint messages so the new state     }
    { surfaces immediately.                                                  }
    Try SchLib.GraphicallyInvalidate; Except End;
    Try
        If SchLib.CurrentSchComponent <> Nil Then
            SchLib.CurrentSchComponent.GraphicallyInvalidate;
    Except End;
    Try Application.ProcessMessages; Except End;
End;

{ True when the resolved SchLib really IS the document at WantPath. Guards the  }
{ silent-wrong-library failure: WorkspaceManager:OpenObject can fail to focus   }
{ the target (bad path, file missing, doc not loadable), leaving the PREVIOUS   }
{ library current. That one is still a valid eSchLib, so without this check a   }
{ handler reads the WRONG library and labels the answer with the requested      }
{ path. Compares the full path, falling back to the file name so path-format    }
{ differences do not cause false negatives.                                     }
Function SchLibIsAtPath(SchLib : ISch_Lib; WantPath : String) : Boolean;
Var
    Actual : String;
Begin
    Result := False;
    If (SchLib = Nil) Or (WantPath = '') Then Exit;
    Actual := '';
    Try Actual := SchLib.DocumentName; Except End;
    If Actual = '' Then Exit;
    If UpperCase(Actual) = UpperCase(WantPath) Then
        Result := True
    Else If UpperCase(ExtractFileName(Actual)) = UpperCase(ExtractFileName(WantPath)) Then
        Result := True;
End;

{ Focus a SchLib by path (empty = the focused document) and return it, or Nil  }
{ when no schematic library resolves. Rewrites LibPath in place to the path     }
{ actually used, so callers can report it. Mirrors the inline open used by      }
{ Lib_CopyComponent / Lib_GetComponentDetails, factored out for the model-edit  }
{ handlers.                                                                      }
Function FocusSchLib(Var LibPath : String) : ISch_Lib;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    FocusedPath : String;
Begin
    Result := Nil;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then Exit;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    Result := SchServer.GetCurrentSchDocument;
    If (Result <> Nil) And (Result.ObjectId <> eSchLib) Then Result := Nil;
    { The open above can silently fail and leave the PREVIOUS library focused. }
    If (Result <> Nil) And (Not SchLibIsAtPath(Result, LibPath)) Then Result := Nil;
End;

Function Lib_CreateSymbol(Params : String; RequestId : String) : String;
Var
    Name, DesignatorPrefix, Description : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    PartCount : Integer;
Begin
    Name := ExtractJsonValue(Params, 'name');
    DesignatorPrefix := ExtractJsonValue(Params, 'designator_prefix');
    Description := ExtractJsonValue(Params, 'description');
    PartCount := StrToIntDef(ExtractJsonValue(Params, 'part_count'), 1);
    If PartCount < 1 Then PartCount := 1;

    If DesignatorPrefix = '' Then DesignatorPrefix := 'U';

    // Get the current schematic library
    If SchServer = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    // Create new component. Per Altium's createcomp_in_lib.pas reference,
    // CurrentPartID and DisplayMode must be set BEFORE adding primitives;
    // primitives carry OwnerPartId/OwnerPartDisplayMode that link them to
    // a specific part of the component. Without this scaffold, primitives
    // are added but the lib editor can't display them (symbol shows empty).
    Component := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    If Component <> Nil Then
    Begin
        Component.CurrentPartID := 1;
        Component.DisplayMode := 0;
        { Multi-part symbols (quad op-amp, dual gate, etc) need PartCount  }
        { set BEFORE pin / primitive add so each primitive's OwnerPartId    }
        { can address a real sub-part.                                      }
        Try Component.PartCount := PartCount; Except End;
        Component.LibReference := Name;
        Component.Designator.Text := DesignatorPrefix + '?';
        Component.ComponentDescription := Description;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SchLib.AddSchComponent(Component);
        { AddSchComponent overrides LibReference with an auto-generated      }
        { 'Component_<N>' on the second and later additions to the same     }
        { SchLib in one session. The pre-add assignment on line 119 sticks  }
        { only for the first symbol. Re-assign here so the caller's chosen  }
        { name is what survives to disk (and what ResolveLibRef will see).  }
        Component.LibReference := Name;
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        // Broadcast as a new component (source=nil, dest=c_BroadCast). This
        // is the pattern in Altium's createcomp_in_lib.pas, different from
        // the per-primitive SchRegisterObject(Container, Obj) which sends
        // from the container.
        Try
            SchServer.RobotManager.SendMessage(
                Nil, Nil, SCHM_PrimitiveRegistration,
                Component.I_ObjectAddress);
        Except End;

        SchLib.CurrentSchComponent := Component;
        LastCreatedLibComponent := Component;

        // Refresh the library editor view so the new component is visible.
        Try SchLib.GraphicallyInvalidate; Except End;

        MarkLibDirty(SchLib);
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', True) + ',' +
                JsonStr('name', Name) + ',' +
                JsonInt('part_count', PartCount)
            ));
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create symbol');
End;

{ Lib_SetCurrentComponent: make a named component the "current" one in    }
{ the SchLib editor so subsequent SchIterator-based commands (modify_objects }
{ on ePin / eRectangle / eParameter via active_doc scope) target it. The    }
{ asymmetry this fixes: GetState_SchComponentByLibRef is a read-only fetch  }
{ that does NOT update the editor's selection -- without this command, the  }
{ SchLib editor stays pointed at whatever was last manually clicked (or     }
{ the first component on load), so modify_objects silently hits the wrong  }
{ component when the caller thinks they switched.                          }
{ Switch the active SchLib's current component to the named symbol and      }
{ return it (Nil on any failure: no SchServer, no active SchLib, or no      }
{ component with that lib-ref). Shared by Lib_SetCurrentComponent and the   }
{ lib_component scope handling in the generic primitives, so a caller can   }
{ target a library symbol without a separate set_current_component round-   }
{ trip.                                                                      }
{ CurrentLibPartId - which part the SchLib editor is DISPLAYING.              }
{                                                                             }
{ Deliberately read from the document, not from Component.CurrentPartID.      }
{ Those two disagree: the component property accepts any value, while the     }
{ document reports what is on screen, and the iterator follows the document.  }
{ Returns -1 when the document does not answer, so a caller can tell "part 1" }
{ from "no idea".                                                             }
Function CurrentLibPartId(SchLib : ISch_Lib) : Integer;
Begin
    Result := -1;
    Try Result := SchLib.GetState_CurrentSchComponentPartId; Except End;
End;

{ StepLibComponentPartTo - move the editor's displayed part to Target.        }
{                                                                             }
{ Assigning CurrentPartID does not move it; the editor's own command does.    }
{ Stepping is the only route found in working code (two independent scripts   }
{ in reference/ drive SCH:NextComponentPart and check the document's part id  }
{ after each step), so it is used here rather than a property that reports    }
{ success and changes nothing.                                                }
{                                                                             }
{ Bounded by PartCount: the command WRAPS past the last part, so a target     }
{ that can never be reached would spin forever rather than fail.              }
Procedure StepLibComponentPartTo(SchLib : ISch_Lib; Component : ISch_Component; Target : Integer);
Var
    Count, Steps, Seen : Integer;
Begin
    Count := 1;
    Try Count := Component.PartCount; Except End;
    If Count < 1 Then Count := 1;

    Seen := CurrentLibPartId(SchLib);
    { No reported part id means there is nothing to verify against, and       }
    { stepping blind would move the editor off whatever the user was on.      }
    If Seen < 0 Then Exit;

    Steps := 0;
    While (Seen <> Target) And (Steps < Count) Do
    Begin
        ResetParameters;
        RunProcess('SCH:NextComponentPart');
        Steps := Steps + 1;
        Seen := CurrentLibPartId(SchLib);
        If Seen < 0 Then Break;
    End;
End;

{ SelectLibComponentPart - focus a library symbol and make PART PartId the    }
{ active one. A SchLib iterator only ever yields the CURRENT part's           }
{ primitives, so on a multi-part symbol every query, modify and delete sees   }
{ part 1 alone unless the caller can move the part pointer. PartId <= 0 keeps }
{ the historical part-1 behaviour.                                            }
Function SelectLibComponentPart(Name : String; PartId : Integer) : ISch_Component;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Target, Count : Integer;
Begin
    Result := Nil;
    If (Name = '') Or (SchServer = Nil) Then Exit;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then Exit;

    Component := SchLib.GetState_SchComponentByLibRef(Name);
    If Component = Nil Then Exit;

    SchLib.CurrentSchComponent := Component;
    LastCreatedLibComponent := Component;

    { Reset PartID + DisplayMode so subsequent Lib_AddSymbol* calls write     }
    { their primitives onto a VISIBLE normal-mode part. Without this, after a }
    { fresh SchLib reopen Component.CurrentPartID can be 0 (no part) and      }
    { AddSchObject silently succeeds but the primitive lands on an invisible  }
    { bucket -- explains the "line added with success but no eLine in         }
    { query_objects" behaviour observed on a live symbol. The reset stays,    }
    { and the only                                                            }
    { change is WHICH part it selects when the caller asks for one.           }
    Target := 1;
    If PartId > 1 Then
    Begin
        Count := 1;
        Try Count := Component.PartCount; Except End;
        { PartCount can read high by one on some symbols; clamp rather than  }
        { refuse, and never below 1.                                          }
        If (Count > 0) And (PartId <= Count) Then
            Target := PartId
        Else
            Target := PartId;   { let Altium reject an out-of-range id }
    End;
    Try Component.CurrentPartID := Target; Except End;
    Try Component.DisplayMode := 0; Except End;

    { Assigning CurrentPartID is NOT enough, and this is the whole bug        }
    { reported in GH #11 against a 4-part TPS23881B: the property takes the   }
    { value, the editor's part spinner does not move, and the SchLib iterator }
    { follows the DISPLAYED part. So every query returned part 1 while the    }
    { scope said part 3, and with the spinner moved by hand the suffix was    }
    { ignored outright. Nothing errored either way.                           }
    {                                                                          }
    { The displayed part is moved by the editor's own command, not by a       }
    { property. Step it and read the document's part id back after each step. }
    { Bounded by PartCount because the command WRAPS at the last part, so an  }
    { unreachable target would otherwise spin forever.                        }
    If Target > 0 Then
        StepLibComponentPartTo(SchLib, Component, Target);

    Try SchLib.GraphicallyInvalidate; Except End;
    Result := Component;
End;

Function SelectLibComponent(Name : String) : ISch_Component;
Begin
    Result := SelectLibComponentPart(Name, 1);
End;

Function Lib_SetCurrentComponent(Params : String; RequestId : String) : String;
Var
    Name : String;
    Component : ISch_Component;
Begin
    Name := ExtractJsonValue(Params, 'name');
    If Name = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_NAME', 'name is required');
        Exit;
    End;

    Component := SelectLibComponent(Name);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component not found in active library: ' + Name);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"name":"' + EscapeJsonString(Name) + '"}');
End;

Function Lib_AddPin(Params : String; RequestId : String) : String;
Var
    Designator, Name, ElecType : String;
    X, Y, Length, Rotation : Integer;
    Hidden : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Pin : ISch_Pin;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    Name := ExtractJsonValue(Params, 'name');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Length := StrToIntDef(ExtractJsonValue(Params, 'length'), 200);
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);
    ElecType := ExtractJsonValue(Params, 'electrical_type');
    Hidden := ExtractJsonValue(Params, 'hidden') = 'true';

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Pin := SchServer.SchObjectFactory(ePin, eCreate_Default);
    If Pin <> Nil Then
    Begin
        Pin.Designator := Designator;
        Pin.Name := Name;
        Pin.Location.X := MilsToCoord(X);
        Pin.Location.Y := MilsToCoord(Y);
        Pin.PinLength := MilsToCoord(Length);
        Pin.Orientation := Rotation Div 90;
        Pin.IsHidden := Hidden;

        { The shared parser, not an inline chain: the batch path        }
        { (Lib_AddPins) already used StrToPinElectrical, and this       }
        { path's inline copy was case- and underscore-SENSITIVE with    }
        { no aliases, so 'Input' made a passive pin here and an input   }
        { pin there. One vocabulary, stated once, in Utils.pas.         }
        Pin.Electrical := StrToPinElectrical(ElecType);

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Pin, Component);
        Component.AddSchObject(Pin);
        SchRegisterObject(Component, Pin);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"' + EscapeJsonString(Designator) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create pin');
End;

Function Lib_AddSymbolRectangle(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Integer;
    FillColorStr, BorderColorStr : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Rect : ISch_Rectangle;
    Loc : TLocation;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    FillColorStr := ExtractJsonValue(Params, 'fill_color');
    BorderColorStr := ExtractJsonValue(Params, 'border_color');

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Rect := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
    If Rect <> Nil Then
    Begin
        { Read-modify-write the TLocation record; direct `.X := value` on the }
        { Location property is a write to a record COPY and is silently       }
        { discarded (the rect retains its default 0,0 / 500,500 from the      }
        { factory). Same fix is applied in Lib_AddSymbolLine and Generic.pas. }
        Loc := Rect.Location;
        Loc.X := MilsToCoord(X1);
        Loc.Y := MilsToCoord(Y1);
        Rect.Location := Loc;
        Loc := Rect.Corner;
        Loc.X := MilsToCoord(X2);
        Loc.Y := MilsToCoord(Y2);
        Rect.Corner := Loc;

        { Colours are OPTIONAL and only touched when supplied, so a caller }
        { that sends neither gets exactly the outline it got before.       }
        {                                                                   }
        { IsSolid is the reason fill_color did nothing: it was pinned False }
        { here, so an AreaColor would never have been drawn. A supplied     }
        { fill therefore turns the rectangle solid as well, which is what   }
        { lib_create_ic_symbol has been asking for all along by sending     }
        { Altium's pale-yellow body colour and getting a hollow box.        }
        { -1 is the tool's documented "no fill" sentinel and is what the   }
        { parameter DEFAULTS to, so it arrives on nearly every call.       }
        { Treating any non-empty value as a fill would have turned every   }
        { symbol rectangle solid in colour -1.                              }
        Rect.IsSolid := False;
        If BorderColorStr <> '' Then
            Try Rect.Color := StrToIntDef(BorderColorStr, 0); Except End;
        If (FillColorStr <> '') And (StrToIntDef(FillColorStr, -1) >= 0) Then
            Try
                Rect.AreaColor := StrToIntDef(FillColorStr, 0);
                Rect.IsSolid := True;
            Except End;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Rect, Component);
        Component.AddSchObject(Rect);
        SchRegisterObject(Component, Rect);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create rectangle');
End;

Function Lib_AddSymbolLine(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, Width : Integer;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Line : ISch_Line;
    Loc : TLocation;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 1);
    If Width < 0 Then Width := 0;
    If Width > 3 Then Width := 3;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Line := SchServer.SchObjectFactory(eLine, eCreate_Default);
    If Line <> Nil Then
    Begin
        { Read-modify-write -- direct `Line.Location.X := value` writes to a }
        { record copy and is silently discarded, leaving the line at its     }
        { default 0,0 / 0,0 (zero-length, invisible, not added to the        }
        { component). Confirmed broken when 12 lib_add_symbol_line           }
        { calls all reported success but no eLine objects were on the symbol. }
        Loc := Line.Location;
        Loc.X := MilsToCoord(X1);
        Loc.Y := MilsToCoord(Y1);
        Line.Location := Loc;
        Loc := Line.Corner;
        Loc.X := MilsToCoord(X2);
        Loc.Y := MilsToCoord(Y2);
        Line.Corner := Loc;
        Line.LineWidth := Width;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Line, Component);
        Component.AddSchObject(Line);
        SchRegisterObject(Component, Line);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        { Force the lib editor to redraw -- without this, primitives are    }
        { committed but not visible until the user closes and reopens the   }
        { symbol. Same fix applied to other lib_add_symbol_* helpers.       }
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create line');
End;

Function Lib_CreateFootprint(Params : String; RequestId : String) : String;
Var
    Name, Description : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
Begin
    Name := ExtractJsonValue(Params, 'name');
    Description := ExtractJsonValue(Params, 'description');

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PCBServer.CreatePCBLibComp;
    If Footprint <> Nil Then
    Begin
        Footprint.Name := Name;
        Footprint.Description := Description;

        PcbLib.RegisterComponent(Footprint);
        PcbLib.CurrentComponent := Footprint;

        Result := BuildSuccessResponse(RequestId, '{"success":true,"name":"' + EscapeJsonString(Name) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create footprint');
End;

Function Lib_AddFootprintPad(Params : String; RequestId : String) : String;
Var
    Designator, Shape, LayerStr : String;
    X, Y, XSize, YSize, HoleSize, CornerRadius : Integer;
    Rotation : Double;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Pad : IPCB_Pad;
    PadLayer : TLayer;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    XSize := StrToIntDef(ExtractJsonValue(Params, 'x_size'), 60);
    YSize := StrToIntDef(ExtractJsonValue(Params, 'y_size'), 60);
    HoleSize := StrToIntDef(ExtractJsonValue(Params, 'hole_size'), 0);
    Shape := ExtractJsonValue(Params, 'shape');
    LayerStr := ExtractJsonValue(Params, 'layer');
    Rotation := StrToFloatDef(ExtractJsonValue(Params, 'rotation'), 0);
    CornerRadius := StrToIntDef(ExtractJsonValue(Params, 'corner_radius'), 25);

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLib.CurrentComponent;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'No footprint is selected');
        Exit;
    End;

    { Layer FIRST (the rounded-rect setters below are layer-aware): a drilled  }
    { pad is through-hole (MultiLayer); a hole-less pad is SMD on a single      }
    { layer (the named layer, default Top). Resolved before PreProcess so an    }
    { unresolvable name ends the call rather than reaching the old eTopLayer    }
    { fallback and putting a silkscreen pad on top copper.                      }
    If HoleSize > 0 Then PadLayer := eMultiLayer
    Else If LayerStr = '' Then PadLayer := eTopLayer
    Else PadLayer := ResolveLayerId(PcbLib.Board, LayerStr);
    If PadLayer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;

    PCBServer.PreProcess;

    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    If Pad <> Nil Then
    Begin
        Pad.Name := Designator;
        Pad.X := MilsToCoord(X);
        Pad.Y := MilsToCoord(Y);
        Pad.TopXSize := MilsToCoord(XSize);
        Pad.TopYSize := MilsToCoord(YSize);
        Pad.HoleSize := MilsToCoord(HoleSize);
        Pad.Rotation := Rotation;

        Pad.Layer := PadLayer;

        { Shape. roundrect = the modern IPC default: set the layer-stack shape }
        { then the corner-radius percentage (Altium stores RR radius as a %).  }
        If Shape = 'rectangular' Then Pad.TopShape := eRectangular
        Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
        Else If (Shape = 'roundrect') Or (Shape = 'rounded_rectangular') Then
        Begin
            Pad.SetState_StackShapeOnLayer(Pad.Layer, eRoundedRectangular);
            Pad.SetState_StackCRPctOnLayer(Pad.Layer, CornerRadius);
        End
        Else Pad.TopShape := eRounded;

        Footprint.AddPCBObject(Pad);

        Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"' + EscapeJsonString(Designator) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create pad');

    PCBServer.PostProcess;
    SaveDocByPath(PcbLib.Board.FileName);
End;

{ Batch pad authoring: same shape as Lib_AddPins but for PCB pads. Receives a }
{ `pads` array encoded with the ~~ / ; / = separators NextBatchOp expects,    }
{ creates every pad inside ONE PreProcess / PostProcess pair and ONE save, so }
{ a 64-pad QFP costs one IPC round-trip instead of 64. Per-pad fields:        }
{ designator, x, y, x_size, y_size, hole_size, shape, rotation (mirrors the   }
{ singular Lib_AddFootprintPad field set / defaults exactly).                 }
Function Lib_AddFootprintPads(Params : String; RequestId : String) : String;
Var
    PadsStr, Op, Remaining, Shape, LayerStr, BadLayers : String;
    OpCount, Added, Failed : Integer;
    X, Y, XSize, YSize, HoleSize, CornerRadius : Integer;
    Rotation : Double;
    PadLayer : TLayer;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Pad : IPCB_Pad;
Begin
    PadsStr := ExtractJsonValue(Params, 'pads');
    If PadsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'pads is required');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLib.CurrentComponent;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'No footprint is selected');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    BadLayers := '';
    Remaining := PadsStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            XSize := StrToIntDef(GetBatchField(Op, 'x_size'), 60);
            YSize := StrToIntDef(GetBatchField(Op, 'y_size'), 60);
            HoleSize := StrToIntDef(GetBatchField(Op, 'hole_size'), 0);
            Rotation := StrToFloatDef(GetBatchField(Op, 'rotation'), 0);
            Shape := GetBatchField(Op, 'shape');
            LayerStr := GetBatchField(Op, 'layer');
            CornerRadius := StrToIntDef(GetBatchField(Op, 'corner_radius'), 25);

            { Resolve the layer BEFORE creating anything. GetLayerFromString    }
            { answered eTopLayer for every name it did not know, so one         }
            { "Top Overlay" in a batch silently put that pad on top copper.     }
            If HoleSize > 0 Then PadLayer := eMultiLayer
            Else If LayerStr = '' Then PadLayer := eTopLayer
            Else PadLayer := ResolveLayerId(Board, LayerStr);
            If PadLayer = eNoLayer Then
            Begin
                Inc(Failed);
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;

            Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
            If Pad = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Pad.Name := GetBatchField(Op, 'designator');
            Pad.X := MilsToCoord(X);
            Pad.Y := MilsToCoord(Y);
            Pad.TopXSize := MilsToCoord(XSize);
            Pad.TopYSize := MilsToCoord(YSize);
            Pad.HoleSize := MilsToCoord(HoleSize);
            Pad.Rotation := Rotation;

            { Layer FIRST (roundrect setters are layer-aware): drilled ->       }
            { through-hole (MultiLayer); hole-less -> SMD on a single layer.    }
            Pad.Layer := PadLayer;

            If Shape = 'rectangular' Then Pad.TopShape := eRectangular
            Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
            Else If (Shape = 'roundrect') Or (Shape = 'rounded_rectangular') Then
            Begin
                Pad.SetState_StackShapeOnLayer(Pad.Layer, eRoundedRectangular);
                Pad.SetState_StackCRPctOnLayer(Pad.Layer, CornerRadius);
            End
            Else Pad.TopShape := eRounded;

            Footprint.AddPCBObject(Pad);
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"total":' + IntToStr(OpCount) + '}');
End;

Function Lib_AddFootprintTrack(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, Width : Integer;
    LayerStr : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Track : IPCB_Track;
    Layer : TLayer;
Begin
    X1 := StrToIntDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToIntDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToIntDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToIntDef(ExtractJsonValue(Params, 'y2'), 0);
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 10);
    LayerStr := ExtractJsonValue(Params, 'layer');

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLib.CurrentComponent;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'No footprint is selected');
        Exit;
    End;

    { Empty -> silkscreen (the safe default); any named layer is honoured so   }
    { courtyard/assembly tracks can go on Mechanical layers, not just overlay. }
    If LayerStr = '' Then Layer := eTopOverlay
    Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;

    PCBServer.PreProcess;

    Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    If Track <> Nil Then
    Begin
        Track.X1 := MilsToCoord(X1);
        Track.Y1 := MilsToCoord(Y1);
        Track.X2 := MilsToCoord(X2);
        Track.Y2 := MilsToCoord(Y2);
        Track.Width := MilsToCoord(Width);
        Track.Layer := Layer;

        Footprint.AddPCBObject(Track);

        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create track');

    PCBServer.PostProcess;
    SaveDocByPath(PcbLib.Board.FileName);
End;

{ Batch track authoring: same shape as Lib_AddFootprintPads. A `tracks` array }
{ (~~ / ; / = separators) -- silkscreen outline + assembly outline in one IPC }
{ round-trip instead of one-per-segment. Per-track fields: x1,y1,x2,y2,width, }
{ layer (TopOverlay default / BottomOverlay), mirroring the singular handler. }
Function Lib_AddFootprintTracks(Params : String; RequestId : String) : String;
Var
    TracksStr, Op, Remaining, LayerStr, BadLayers : String;
    OpCount, Added, Failed : Integer;
    X1, Y1, X2, Y2, Width : Integer;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Track : IPCB_Track;
    Layer : TLayer;
Begin
    TracksStr := ExtractJsonValue(Params, 'tracks');
    If TracksStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'tracks is required');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLib.CurrentComponent;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'No footprint is selected');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    BadLayers := '';
    Remaining := TracksStr;

    PCBServer.PreProcess;
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
            Width := StrToIntDef(GetBatchField(Op, 'width'), 10);
            LayerStr := GetBatchField(Op, 'layer');
            { Empty -> silkscreen (the safe default); any named layer is        }
            { honoured so courtyard/assembly tracks can go on Mechanical layers.}
            If LayerStr = '' Then Layer := eTopOverlay
            Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
            If Layer = eNoLayer Then
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

            Track.X1 := MilsToCoord(X1);
            Track.Y1 := MilsToCoord(Y1);
            Track.X2 := MilsToCoord(X2);
            Track.Y2 := MilsToCoord(Y2);
            Track.Width := MilsToCoord(Width);
            Track.Layer := Layer;

            Footprint.AddPCBObject(Track);
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"total":' + IntToStr(OpCount) + '}');
End;

Function Lib_AddFootprintArc(Params : String; RequestId : String) : String;
Var
    XCenter, YCenter, Radius, StartAngle, EndAngle, Width : Integer;
    LayerStr : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Arc : IPCB_Arc;
    Layer : TLayer;
Begin
    XCenter := StrToIntDef(ExtractJsonValue(Params, 'x_center'), 0);
    YCenter := StrToIntDef(ExtractJsonValue(Params, 'y_center'), 0);
    Radius := StrToIntDef(ExtractJsonValue(Params, 'radius'), 100);
    StartAngle := StrToIntDef(ExtractJsonValue(Params, 'start_angle'), 0);
    EndAngle := StrToIntDef(ExtractJsonValue(Params, 'end_angle'), 360);
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 10);
    LayerStr := ExtractJsonValue(Params, 'layer');

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLib.CurrentComponent;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'No footprint is selected');
        Exit;
    End;

    { Empty -> silkscreen (the safe default); any named layer is honoured so   }
    { pin-1 / assembly arcs can go on Mechanical layers, not just overlay.      }
    If LayerStr = '' Then Layer := eTopOverlay
    Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;

    PCBServer.PreProcess;

    Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
    If Arc <> Nil Then
    Begin
        Arc.XCenter := MilsToCoord(XCenter);
        Arc.YCenter := MilsToCoord(YCenter);
        Arc.Radius := MilsToCoord(Radius);
        Arc.StartAngle := StartAngle;
        Arc.EndAngle := EndAngle;
        Arc.LineWidth := MilsToCoord(Width);
        Arc.Layer := Layer;

        Footprint.AddPCBObject(Arc);

        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create arc');

    PCBServer.PostProcess;
    SaveDocByPath(PcbLib.Board.FileName);
End;

{ Lib_AddFootprintText - Stamp a text primitive onto a PcbLib footprint.       }
{                                                                              }
{ Trap baked into this handler: in a PcbLib, Footprint.AddPCBObject on its    }
{ own does not register the new primitive properly with the placement editor }
{ -- the text shows up only after a save+reload. The working pattern is to  }
{ add to BOTH the footprint AND the underlying Board, then broadcast        }
{ PCBM_BoardRegisteration to both. We replicate that exactly.               }
{                                                                              }
{ Params:                                                                      }
{   text        (required) - the string to place                              }
{   x, y                   - coordinates in mils, relative to board origin    }
{   size                   - text height in mils (default 50)                 }
{   width                  - stroke width in mils (default 8)                 }
{   rotation               - degrees, default 0                                }
{   layer                  - GetLayerFromString name, default 'TopOverlay'   }
{   use_ttfont=true|false  - default false (stroke font)                      }
{   library_path           - optional .PcbLib to focus first                  }
{   component_name         - optional footprint name; switches active fp     }
Function Lib_AddFootprintText(Params : String; RequestId : String) : String;
Var
    TextStr, LayerStr, CompName, LibPath, FocusedPath, FlagStr, RespJson : String;
    MirrorStr : String;
    Mirror : Boolean;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Text : IPCB_Text;
    Board : IPCB_Board;
    Iter : IPCB_LibraryIterator;
    Layer : TLayer;
    X, Y, Size, Width, Rotation : Integer;
    UseTTFont : Boolean;
Begin
    TextStr := ExtractJsonValue(Params, 'text');
    If TextStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text is required');
        Exit;
    End;
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Size := StrToIntDef(ExtractJsonValue(Params, 'size'), 50);
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 8);
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);
    LayerStr := ExtractJsonValue(Params, 'layer');
    If LayerStr = '' Then LayerStr := 'TopOverlay';
    FlagStr := ExtractJsonValue(Params, 'use_ttfont');
    UseTTFont := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');
    { Bottom-side text must be mirrored or it reads backwards on the      }
    { board. audit_find_mirrored_pcb_text reports exactly this pairing:    }
    { eBottomOverlay without MirrorFlag, and eTopOverlay with it.          }
    MirrorStr := ExtractJsonValue(Params, 'mirror');
    Mirror := (MirrorStr = 'true') Or (MirrorStr = 'True') Or (MirrorStr = '1');
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');

    If LibPath <> '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
            Exit;
        End;
        FocusedPath := '';
        Doc := Workspace.DM_FocusedDocument;
        If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
        If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
        Begin
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', LibPath);
            RunProcess('WorkspaceManager:OpenObject');
        End;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    { Switch to a named footprint if asked, otherwise use the active one.  }
    If CompName <> '' Then
    Begin
        Footprint := Nil;
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                If Footprint.Name = CompName Then Break;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
        If Footprint = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
                'Footprint not found in library: ' + CompName);
            Exit;
        End;
        Try PcbLib.SetState_CurrentComponent(Footprint); Except End;
    End
    Else
        Footprint := PcbLib.CurrentComponent;

    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'No footprint is selected (pass component_name to choose one)');
        Exit;
    End;

    Board := PcbLib.Board;
    Layer := ResolveLayerId(Board, LayerStr);
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
            'Unknown layer name: ' + LayerStr + '. ' + BoardLayerNamesHint(Board));
        Exit;
    End;

    PCBServer.PreProcess;
    Try
        Text := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
        If Text = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                'PCBObjectFactory returned Nil for eTextObject');
            Exit;
        End;
        { Relative to the footprint's own origin. Board.XOrigin is a board-wide
          reference and would drop the text far from the footprint. }
        Text.XLocation := Footprint.X + MilsToCoord(X);
        Text.YLocation := Footprint.Y + MilsToCoord(Y);
        Text.Layer := Layer;
        Text.UseTTFonts := UseTTFont;
        Text.UnderlyingString := TextStr;
        Text.Size := MilsToCoord(Size);
        Text.Width := MilsToCoord(Width);
        Try Text.MirrorFlag := Mirror; Except End;
        Try Text.Rotation := Rotation; Except End;

        { The working pattern: add to footprint AND to its                  }
        { underlying Board, then broadcast registration to both. Footprint  }
        { alone is not enough -- the placement editor will not see the new }
        { primitive until a save+reload.                                    }
        Footprint.AddPCBObject(Text);
        Board.AddPCBObject(Text);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Text.I_ObjectAddress);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Text.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;

    RespJson :=
        '{"success":true' +
        ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"' +
        ',"text":"' + EscapeJsonString(TextStr) + '"' +
        ',"layer":"' + EscapeJsonString(LayerStr) + '"' +
        ',"x":' + IntToStr(X) +
        ',"y":' + IntToStr(Y) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_GetFootprints - Enumerate every footprint in the active (or named)     }
{ PcbLib. Mirror of lib_get_components (SchLib) for PCB libraries. Uses the }
{ documented IPCB_LibraryIterator pattern.                                   }
{                                                                              }
{ Params:                                                                      }
{   library_path - optional .PcbLib to focus first; defaults to focused doc. }
Function Lib_GetFootprints(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName, FpDescr, FpsJson, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    Count : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    FpsJson := '[';
    Count := 0;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            FpDescr := '';
            Try FpName := Footprint.Name; Except End;
            Try FpDescr := Footprint.Description; Except End;
            If Count > 0 Then FpsJson := FpsJson + ',';
            FpsJson := FpsJson +
                '{"name":"' + EscapeJsonString(FpName) + '"' +
                ',"description":"' + EscapeJsonString(FpDescr) + '"}';
            Inc(Count);
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    FpsJson := FpsJson + ']';

    RespJson :=
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"count":' + IntToStr(Count) +
        ',"footprints":' + FpsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_GetFootprintPads - Read the pad geometry of one footprint in the active }
{ (or named) PcbLib, for export (e.g. KiCad .kicad_mod). Coordinates are in   }
{ mils relative to the library origin (the footprint reference point), which  }
{ is the same frame Lib_AddFootprintPad writes in.                            }
{                                                                              }
{ Params:                                                                      }
{   footprint_name - optional; defaults to the library's current component.   }
{   library_path   - optional .PcbLib to focus first; defaults to focused doc.}
{                                                                              }
{ Response: name, pad_count, and a pads array; each pad carries name, x, y,   }
{   size_x, size_y, shape, layer, hole, rotation -- dimensions in mils, angles }
{   in degrees.                                                                }
Function Lib_GetFootprintPads(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName : String;
    ShapeStr, LayerStr, PadsJson, RespJson : String;
    PrimsJson, KindStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Prim : IPCB_Primitive;
    XOrg, YOrg : TCoord;
    Count, PCount, BodyCount : Integer;
Begin
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    { Pick the target footprint: by name if given, else the current one. }
    Target := Nil;
    If FpWanted = '' Then
        Target := PcbLib.CurrentComponent
    Else
    Begin
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;
                If UpperCase(FpName) = UpperCase(FpWanted) Then
                Begin
                    Target := Footprint;
                    Break;
                End;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found: ' + FpWanted);
        Exit;
    End;

    FpName := '';
    Try FpName := Target.Name; Except End;
    { The footprint's own origin is the reference point, NOT Board.XOrigin:
      a PcbLib shares one board across every footprint, so a board-wide origin
      exported pads tens of thousands of mils away from where they belong. }
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;

    PadsJson := '[';
    Count := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            ShapeStr := 'round';
            Try
                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                Else If Pad.TopShape = eRoundRectangle Then ShapeStr := 'roundrectangle'
                Else ShapeStr := 'round';
            Except End;

            LayerStr := 'top';
            Try
                If (Pad.Layer = eMultiLayer) Or (Pad.HoleSize > 0) Then LayerStr := 'multi'
                Else If Pad.Layer = eBottomLayer Then LayerStr := 'bottom'
                Else LayerStr := 'top';
            Except End;

            If Count > 0 Then PadsJson := PadsJson + ',';
            PadsJson := PadsJson +
                '{"name":"' + EscapeJsonString(Pad.Name) + '"' +
                ',"x":' + IntToStr(CoordToMils(Pad.X - XOrg)) +
                ',"y":' + IntToStr(CoordToMils(Pad.Y - YOrg)) +
                ',"size_x":' + IntToStr(CoordToMils(Pad.TopXSize)) +
                ',"size_y":' + IntToStr(CoordToMils(Pad.TopYSize)) +
                ',"shape":"' + ShapeStr + '"' +
                ',"layer":"' + LayerStr + '"' +
                ',"hole":' + IntToStr(CoordToMils(Pad.HoleSize)) +
                ',"rotation":' + FloatToJsonStr(Pad.Rotation) + '}';
            Inc(Count);
            Pad := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    PadsJson := PadsJson + ']';

    { Non-pad graphics: tracks / arcs / regions / fills by layer (for the
      silkscreen / assembly / courtyard policy checks), plus a count of 3D
      component bodies. Layer names come from GetLayerString, so the policy
      auditor sees 'TopOverlay', 'Mechanical13', etc. Only ObjectId / Layer
      are touched here -- both live on IPCB_Primitive -- so no interface
      narrowing is needed. }
    PrimsJson := '[';
    PCount := 0;
    BodyCount := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
            eRegionObject, eFillObject, eComponentBodyObject));
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            If Prim.ObjectId = eComponentBodyObject Then
                Inc(BodyCount)
            Else
            Begin
                KindStr := 'track';
                If Prim.ObjectId = eArcObject Then KindStr := 'arc'
                Else If Prim.ObjectId = eRegionObject Then KindStr := 'region'
                Else If Prim.ObjectId = eFillObject Then KindStr := 'fill';
                LayerStr := '';
                Try LayerStr := GetLayerString(Prim.Layer); Except End;
                If PCount > 0 Then PrimsJson := PrimsJson + ',';
                PrimsJson := PrimsJson +
                    '{"kind":"' + KindStr + '"' +
                    ',"layer":"' + EscapeJsonString(LayerStr) + '"}';
                Inc(PCount);
            End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    PrimsJson := PrimsJson + ']';

    RespJson :=
        '{"name":"' + EscapeJsonString(FpName) + '"' +
        ',"pad_count":' + IntToStr(Count) +
        ',"pads":' + PadsJson +
        ',"primitives":' + PrimsJson +
        ',"bodies":' + IntToStr(BodyCount) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ ResolveLayerName - Human name for any TLayer, including the mechanical      }
{ layers above 16 that GetLayerString cannot map (it returns 'Unknown' for    }
{ them, and a modern Altium build encodes those as large ordinals such as     }
{ 0x04000012). Falls back to the board's own layer stack, which names every   }
{ layer the document actually has. Returns '' if nothing can name it, so the  }
{ caller can fall back to the raw ordinal.                                    }
Function ResolveLayerName(Board : IPCB_Board; Lyr : TLayer) : String;
Var
    LayerObj : IPCB_LayerObject_V7;
    Stack : IPCB_LayerStack_V7;
Begin
    Result := '';
    Try Result := GetLayerString(Lyr); Except End;
    If (Result <> '') And (Result <> 'Unknown') Then Exit;
    Result := '';
    If Board = Nil Then Exit;
    Try
        Stack := Board.LayerStack_V7;
        If Stack <> Nil Then
        Begin
            LayerObj := Stack.LayerObject_V7[Lyr];
            If LayerObj <> Nil Then Result := LayerObj.Name;
        End;
    Except
        Result := '';
    End;
End;

{ Lib_GetLibraryGeometry - Dump the policy-relevant geometry of EVERY         }
{ footprint in one library pass, for the footprint-policy auditor.            }
{                                                                              }
{ Lib_GetFootprintPads answers one footprint per call and re-scans the whole  }
{ library to find it by name, so auditing an N-footprint library costs N IPC  }
{ round trips and O(N^2) iterator steps -- minutes on a 1000-part library.    }
{ This walks the LibraryIterator once and emits a compact record per          }
{ footprint. Only the fields the auditor reads are emitted: pads carry        }
{ name/shape/layer/hole (no coordinates), and primitives are DEDUPLICATED to  }
{ the distinct (kind, layer) pairs -- a footprint with 200 silk tracks emits  }
{ one entry, since the auditor only ever looks at the set of layers per role. }
{                                                                              }
{ Each footprint's geometry read is wrapped so one malformed footprint drops  }
{ out of the sweep instead of halting the polling loop.                       }
{                                                                              }
{ Params:                                                                      }
{   library_path - optional .PcbLib to focus first; defaults to focused doc.  }
{   offset       - index of the first footprint to emit (default 0).          }
{   limit        - max footprints to emit (default 250) -- bounds response    }
{                  size; page until offset+count >= total.                    }
{                                                                              }
{ Response: library_path, total, offset, count, and a footprints array whose  }
{   entries carry name, pads, primitives, texts, pad_center, bodies.          }
{   pad_center is the AVERAGE PAD CENTRE in mils, the reference a designator  }
{   should be centred on -- the library origin is arbitrary and unrelated.    }
Function Lib_GetLibraryGeometry(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName : String;
    ShapeStr, LayerStr, KindStr, PrimKey, SeenKeys : String;
    PadsJson, PrimsJson, TextsJson, FpsJson, RespJson : String;
    TxtStr, CenterJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Prim : IPCB_Primitive;
    Txt : IPCB_Text;
    XOrg, YOrg : TCoord;
    Offset, Limit, Total, Emitted : Integer;
    PadN, PrimN, TextN, BodyCount, DesigCount : Integer;
    SumX, SumY, PadX, PadY, LayerId : Integer;
    CenterX, CenterY, TxtX, TxtY : TCoord;
    TRect : TCoordRect;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    Offset := StrToIntDef(ExtractJsonValue(Params, 'offset'), 0);
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 250);
    If Offset < 0 Then Offset := 0;
    If Limit <= 0 Then Limit := 250;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    FpsJson := '[';
    Total := 0;
    Emitted := 0;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            If (Total >= Offset) And (Emitted < Limit) Then
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;

                { Every coordinate below is relative to THIS FOOTPRINT'S OWN
                  origin (its reference point), not to Board.XOrigin. A PcbLib
                  shares one board across all footprints, and each footprint
                  sits wherever it was drawn, so a board-wide origin yields a
                  meaningless frame -- footprints read as tens of thousands of
                  mils apart when they are really all drawn about their own
                  reference point. }
                XOrg := 0;  YOrg := 0;
                Try XOrg := Footprint.X; Except End;
                Try YOrg := Footprint.Y; Except End;

                { Pads, and the running sum of their centres. The average pad
                  centre is the reference a designator should be centred on. }
                PadsJson := '[';
                PadN := 0;
                SumX := 0;
                SumY := 0;
                Try
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                        Pad := GrpIter.FirstPCBObject;
                        While Pad <> Nil Do
                        Begin
                            ShapeStr := 'round';
                            Try
                                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                                Else If Pad.TopShape = eRoundRectangle Then ShapeStr := 'roundrectangle'
                                Else ShapeStr := 'round';
                            Except End;
                            LayerStr := 'top';
                            Try
                                If (Pad.Layer = eMultiLayer) Or (Pad.HoleSize > 0) Then LayerStr := 'multi'
                                Else If Pad.Layer = eBottomLayer Then LayerStr := 'bottom'
                                Else LayerStr := 'top';
                            Except End;
                            { Accumulate in native TCoord, not mils: summing
                              per-pad mils truncates each term and the average
                              lands up to a mil off the true centre. }
                            PadX := 0;
                            PadY := 0;
                            Try
                                PadX := Pad.X - XOrg;
                                PadY := Pad.Y - YOrg;
                            Except End;
                            SumX := SumX + PadX;
                            SumY := SumY + PadY;
                            If PadN > 0 Then PadsJson := PadsJson + ',';
                            PadsJson := PadsJson +
                                '{"name":"' + EscapeJsonString(Pad.Name) + '"' +
                                ',"shape":"' + ShapeStr + '"' +
                                ',"layer":"' + LayerStr + '"' +
                                ',"hole":' + IntToStr(CoordToMils(Pad.HoleSize)) + '}';
                            Inc(PadN);
                            Pad := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                Except End;
                PadsJson := PadsJson + ']';

                { pad_center is mils (human-readable, what the policy compares);
                  pad_center_coord is the exact TCoord the writer must use, so
                  a re-centre does not introduce a mil of rounding error. }
                CenterJson := 'null';
                If PadN > 0 Then
                Begin
                    CenterX := SumX Div PadN;
                    CenterY := SumY Div PadN;
                    CenterJson := '{"x":' + IntToStr(CoordToMils(CenterX)) +
                                  ',"y":' + IntToStr(CoordToMils(CenterY)) +
                                  ',"coord_x":' + IntToStr(CenterX) +
                                  ',"coord_y":' + IntToStr(CenterY) + '}';
                End;

                { Distinct (kind, layer) pairs only -- SeenKeys is a delimited
                  membership string, so no TStringList is needed here. }
                PrimsJson := '[';
                PrimN := 0;
                BodyCount := 0;
                SeenKeys := '';
                Try
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
                            eRegionObject, eFillObject, eComponentBodyObject));
                        Prim := GrpIter.FirstPCBObject;
                        While Prim <> Nil Do
                        Begin
                            If Prim.ObjectId = eComponentBodyObject Then
                                Inc(BodyCount)
                            Else
                            Begin
                                KindStr := 'track';
                                If Prim.ObjectId = eArcObject Then KindStr := 'arc'
                                Else If Prim.ObjectId = eRegionObject Then KindStr := 'region'
                                Else If Prim.ObjectId = eFillObject Then KindStr := 'fill';
                                LayerStr := '';
                                Try LayerStr := ResolveLayerName(PcbLib.Board, Prim.Layer); Except End;
                                LayerId := -1;
                                Try LayerId := Prim.Layer; Except End;
                                PrimKey := '|' + KindStr + '@' + IntToStr(LayerId) + '|';
                                If Pos(PrimKey, SeenKeys) = 0 Then
                                Begin
                                    SeenKeys := SeenKeys + PrimKey;
                                    If PrimN > 0 Then PrimsJson := PrimsJson + ',';
                                    PrimsJson := PrimsJson +
                                        '{"kind":"' + KindStr + '"' +
                                        ',"layer_id":' + IntToStr(LayerId) +
                                        ',"layer":"' + EscapeJsonString(LayerStr) + '"}';
                                    Inc(PrimN);
                                End;
                            End;
                            Prim := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                Except End;
                PrimsJson := PrimsJson + ']';

                { Text primitives: the designator string, the comment string,
                  and any free legend. A single-type filter means the iterator
                  hands back a narrowed IPCB_Text, so .Text / .Size / location
                  are reachable. Every read is guarded: these properties are
                  the version-sensitive part of this dump. }
                TextsJson := '[';
                TextN := 0;
                DesigCount := 0;
                Try
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                        Txt := GrpIter.FirstPCBObject;
                        While Txt <> Nil Do
                        Begin
                            { UnderlyingString is the RAW authored string. .Text
                              is the RENDERED one: with special-string conversion
                              on, '.Designator' renders as the component's actual
                              designator (empty in a library), so matching on
                              .Text silently misses real designators. }
                            TxtStr := '';
                            Try TxtStr := Txt.UnderlyingString; Except End;
                            If TxtStr = '' Then
                                Try TxtStr := Txt.Text; Except End;
                            KindStr := 'free';
                            If UpperCase(TxtStr) = '.DESIGNATOR' Then
                            Begin
                                KindStr := 'designator';
                                Inc(DesigCount);
                            End
                            Else If UpperCase(TxtStr) = '.COMMENT' Then KindStr := 'comment';
                            LayerStr := '';
                            Try LayerStr := ResolveLayerName(PcbLib.Board, Txt.Layer); Except End;
                            { Emit the raw TLayer ordinal too. If even the layer
                              stack cannot name the layer, the ordinal is still a
                              stable identity to group and compare by. }
                            LayerId := -1;
                            Try LayerId := Txt.Layer; Except End;
                            If TextN > 0 Then TextsJson := TextsJson + ',';
                            TextsJson := TextsJson +
                                '{"text":"' + EscapeJsonString(TxtStr) + '"' +
                                ',"kind":"' + KindStr + '"' +
                                ',"layer_id":' + IntToStr(LayerId) +
                                ',"layer":"' + EscapeJsonString(LayerStr) + '"';
                            Try
                                TextsJson := TextsJson +
                                    ',"height":' + IntToStr(CoordToMils(Txt.Size));
                            Except End;
                            { Report the text's BOUNDING-BOX CENTRE, not its
                              XLocation. XLocation is the anchor (a corner), so
                              centring on it leaves the string hanging half its
                              width off the part. Fall back to the anchor only
                              if the bounding rectangle is unavailable. }
                            TxtX := 0;
                            TxtY := 0;
                            Try
                                TxtX := Txt.XLocation;
                                TxtY := Txt.YLocation;
                            Except End;
                            Try
                                TRect := Txt.BoundingRectangle;
                                TxtX := (TRect.X1 + TRect.X2) Div 2;
                                TxtY := (TRect.Y1 + TRect.Y2) Div 2;
                            Except End;
                            { x/y and coord_x/coord_y are the BBOX CENTRE (what
                              "centred" means). anchor_x/anchor_y are XLocation,
                              the corner the writer actually assigns. Emitting
                              both lets the caller compute the exact anchor that
                              puts the bbox centre on target, with no bounding
                              box read at write time. }
                            Try
                                TextsJson := TextsJson +
                                    ',"x":' + IntToStr(CoordToMils(TxtX - XOrg)) +
                                    ',"y":' + IntToStr(CoordToMils(TxtY - YOrg)) +
                                    ',"coord_x":' + IntToStr(TxtX - XOrg) +
                                    ',"coord_y":' + IntToStr(TxtY - YOrg) +
                                    ',"anchor_x":' + IntToStr(Txt.XLocation - XOrg) +
                                    ',"anchor_y":' + IntToStr(Txt.YLocation - YOrg);
                            Except End;
                            TextsJson := TextsJson + '}';
                            Inc(TextN);
                            Txt := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                Except End;
                TextsJson := TextsJson + ']';

                If Emitted > 0 Then FpsJson := FpsJson + ',';
                FpsJson := FpsJson +
                    '{"name":"' + EscapeJsonString(FpName) + '"' +
                    ',"pads":' + PadsJson +
                    ',"primitives":' + PrimsJson +
                    ',"texts":' + TextsJson +
                    ',"pad_center":' + CenterJson +
                    ',"designator_count":' + IntToStr(DesigCount) +
                    ',"bodies":' + IntToStr(BodyCount) + '}';
                Inc(Emitted);
            End;
            Inc(Total);
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    FpsJson := FpsJson + ']';

    RespJson :=
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"total":' + IntToStr(Total) +
        ',"offset":' + IntToStr(Offset) +
        ',"count":' + IntToStr(Emitted) +
        ',"footprints":' + FpsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_SetDesignator - Move / resize / create one footprint's .Designator.     }
{                                                                              }
{ The target layer is addressed by its raw TLayer ORDINAL (layer_id), not by  }
{ name: a house layer called 'Assembly Designator' or 'Mechanical 18' is not  }
{ in GetLayerFromString's table, and the ordinal is exactly what              }
{ Lib_GetLibraryGeometry already reported for that layer. Python decides the  }
{ target from the library's own majority; this handler only applies it.       }
{                                                                              }
{ Params:                                                                      }
{   footprint_name - required.                                                }
{   library_path   - optional .PcbLib to focus first.                         }
{   layer_id       - optional TLayer ordinal to move the designator to.       }
{   x, y           - optional native TCoord offsets from the footprint origin.}
{   height         - optional text height in mils.                            }
{   create         - 'true' to add a .Designator when the footprint has none; }
{                    refused outright if one already exists.                  }
{                                                                              }
{ Every field is optional and applied only when supplied, so one handler      }
{ serves a layer move, a re-centre, a resize, or a create.                    }
Function Lib_SetDesignator(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, TxtStr, RespJson, ChangedJson : String;
    XStr, YStr, HeightStr, LayerStr, CreateStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    SrvDoc : IServerDocument;
    Lyr : TLayer;
    XOrg, YOrg, TgtX, TgtY, CurCX, CurCY : TCoord;
    TRect : TCoordRect;
    NewX, NewY, NewH, LayerId, WidthMils : Integer;
    DoCreate, Created : Boolean;
Begin
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'footprint_name is required');
        Exit;
    End;
    LibPath := ExtractJsonValue(Params, 'library_path');
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    HeightStr := ExtractJsonValue(Params, 'height');
    LayerStr := ExtractJsonValue(Params, 'layer_id');
    CreateStr := ExtractJsonValue(Params, 'create');
    DoCreate := (CreateStr = 'true') Or (CreateStr = 'True') Or (CreateStr = '1');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found: ' + FpWanted);
        Exit;
    End;

    Board := PcbLib.Board;
    { Coordinates are relative to the footprint's own origin, matching what
      Lib_GetLibraryGeometry reports. Board.XOrigin is NOT that reference. }
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;

    { Locate the existing .Designator, if any. Match on UnderlyingString: .Text
      returns the RENDERED special string, so matching it misses real
      designators and a create would then add a DUPLICATE. }
    Desig := Nil;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            TxtStr := '';
            Try TxtStr := Txt.UnderlyingString; Except End;
            If TxtStr = '' Then Try TxtStr := Txt.Text; Except End;
            If UpperCase(TxtStr) = '.DESIGNATOR' Then
            Begin
                Desig := Txt;
                Break;
            End;
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;

    If DoCreate And (Desig <> Nil) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'ALREADY_HAS_DESIGNATOR',
            'Refusing to create: footprint already has a .Designator: ' + FpWanted);
        Exit;
    End;

    If (Desig = Nil) And (Not DoCreate) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_DESIGNATOR',
            'Footprint has no .Designator and create was not requested: ' + FpWanted);
        Exit;
    End;

    LayerId := StrToIntDef(LayerStr, -1);
    NewX := StrToIntDef(XStr, 0);
    NewY := StrToIntDef(YStr, 0);
    NewH := StrToIntDef(HeightStr, 0);
    Created := False;
    ChangedJson := '';

    PCBServer.PreProcess;
    Try
        If Desig = Nil Then
        Begin
            { Creating: layer, position and height must all be supplied,
              otherwise the new string would land somewhere arbitrary. }
            If (LayerId < 0) Or (XStr = '') Or (YStr = '') Or (NewH <= 0) Then
            Begin
                PCBServer.PostProcess;
                Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
                    'create requires layer_id, x, y and height');
                Exit;
            End;
            { Board.AddPCBObject attaches to the library's CURRENT component,
              so the target must be made current or the text lands elsewhere. }
            Try PcbLib.CurrentComponent := Target; Except End;
            If PcbLib.CurrentComponent <> Target Then
            Begin
                PCBServer.PostProcess;
                Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                    'Could not make the target footprint current: ' + FpWanted);
                Exit;
            End;
            Desig := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
            If Desig = Nil Then
            Begin
                PCBServer.PostProcess;
                Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                    'PCBObjectFactory returned Nil for eTextObject');
                Exit;
            End;
            Desig.UnderlyingString := '.Designator';
            WidthMils := NewH Div 5;
            If WidthMils < 1 Then WidthMils := 1;
            Desig.Width := MilsToCoord(WidthMils);
            Target.AddPCBObject(Desig);
            Board.AddPCBObject(Desig);
            PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
            Created := True;
            ChangedJson := ChangedJson + '"created",';
        End;

        { Bracket the mutation, or the edit lives in memory and is dropped when
          the component is re-serialised on save. }
        Try
            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                c_Broadcast, PCBM_BeginModify, c_NoEventData);
        Except End;

        If LayerId >= 0 Then
        Begin
            Lyr := LayerId;
            Try
                Desig.Layer := Lyr;
                ChangedJson := ChangedJson + '"layer",';
            Except End;
        End;
        { Size FIRST: resizing changes the bounding box the centring uses. }
        If NewH > 0 Then
        Begin
            Try
                Desig.Size := MilsToCoord(NewH);
                ChangedJson := ChangedJson + '"height",';
            Except End;
        End;
        If (XStr <> '') And (YStr <> '') Then
        Begin
            { Re-read the origin HERE: the create path makes the component
              current, which relocates it, so an earlier reading is stale. }
            Try XOrg := Target.X; Except End;
            Try YOrg := Target.Y; Except End;
            { x/y are the ANCHOR (XLocation) the caller wants, in TCoord
              relative to the footprint origin. The caller derives it from the
              anchor and bbox centre this script reports. }
            Try
                Desig.XLocation := XOrg + NewX;
                Desig.YLocation := YOrg + NewY;
                ChangedJson := ChangedJson + '"position",';
            Except End;
            { BoundingRectangle is CACHED and is not invalidated by assigning
              XLocation; without this the next read reports the OLD box. }
            Try Desig.GraphicallyInvalidate; Except End;
        End;

        Try
            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                c_Broadcast, PCBM_EndModify, c_NoEventData);
        Except End;
    Finally
        PCBServer.PostProcess;
    End;

    { Trim the trailing comma. }
    If ChangedJson <> '' Then
        ChangedJson := Copy(ChangedJson, 1, Length(ChangedJson) - 1);

    Try Board.ViewManager_FullUpdate; Except End;

    { Flag the server doc dirty so application.save_all flushes it. The write
      is deferred: nothing reaches disk until the caller explicitly saves. }
    Try
        SrvDoc := Client.GetDocumentByPath(LibPath);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    RespJson :=
        '{"success":true' +
        ',"footprint":"' + EscapeJsonString(FpWanted) + '"' +
        ',"created":' + BoolToJsonStr(Created) +
        ',"changed":[' + ChangedJson + ']}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ SplitNextTab - Pop the leading tab-delimited field off S, shortening S.     }
Function SplitNextTab(Var S : String) : String;
Var P : Integer;
Begin
    P := Pos(#9, S);
    If P = 0 Then
    Begin
        Result := S;
        S := '';
    End
    Else
    Begin
        Result := Copy(S, 1, P - 1);
        S := Copy(S, P + 1, Length(S) - P);
    End;
End;

{ Lib_ConvertDesignatorsToStroke - Turn every TrueType .Designator in the      }
{ library into a stroke-font one, in one pass.                                 }
{                                                                              }
{ A TrueType PCB text will not persist a position change set through           }
{ XLocation: the assignment reads back changed, then Altium recomputes the     }
{ position from the TT layout on reload and the move reverts. Bold/Italic are  }
{ TrueType-ONLY attributes (a stroke text cannot be bold), so a bold or italic }
{ designator is the reliable TrueType tell -- more reliable than UseTTFonts,   }
{ which imported footprints leave inconsistent.                                }
{                                                                              }
{ Conversion clears UseTTFonts, Bold and Italic. Each change is bracketed with }
{ PCBM_BeginModify / PCBM_EndModify so it registers, and Bold is READ BACK so a}
{ refusal is counted, not assumed.                                             }
{                                                                              }
{ Params: library_path - optional .PcbLib to focus first.                      }
{ Response: converted count, and the names it converted (up to 50).            }
Function Lib_ConvertDesignatorsToStroke(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName, TxtStr, RespJson, NamesJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    SrvDoc : IServerDocument;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    IsTT : Boolean;
    Converted, Total : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Board := PcbLib.Board;
    Converted := 0;
    Total := 0;
    NamesJson := '';

    PCBServer.PreProcess;
    Try
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;

                Desig := Nil;
                GrpIter := Footprint.GroupIterator_Create;
                Try
                    GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                    Txt := GrpIter.FirstPCBObject;
                    While Txt <> Nil Do
                    Begin
                        TxtStr := '';
                        Try TxtStr := Txt.UnderlyingString; Except End;
                        If TxtStr = '' Then Try TxtStr := Txt.Text; Except End;
                        If UpperCase(TxtStr) = '.DESIGNATOR' Then
                        Begin
                            Desig := Txt;
                            Break;
                        End;
                        Txt := GrpIter.NextPCBObject;
                    End;
                Finally
                    Footprint.GroupIterator_Destroy(GrpIter);
                End;

                If Desig <> Nil Then
                Begin
                    { Bold/Italic are TrueType-only. UseTTFonts is a weaker
                      signal but included. Treat any of them as TrueType. }
                    IsTT := False;
                    Try If Desig.Bold Then IsTT := True; Except End;
                    Try If Desig.Italic Then IsTT := True; Except End;
                    Try If Desig.UseTTFonts Then IsTT := True; Except End;

                    If IsTT Then
                    Begin
                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        Except End;
                        Try Desig.UseTTFonts := False; Except End;
                        Try Desig.Bold := False; Except End;
                        Try Desig.Italic := False; Except End;
                        { An inverted / inverted-rectangle text has its geometry
                          driven by the rectangle, so its effective position is
                          not the plain anchor -- clearing these makes it a plain
                          stroke text whose XLocation is authoritative. }
                        Try Desig.Inverted := False; Except End;
                        Try Desig.UseInvertedRectangle := False; Except End;
                        Try Desig.GraphicallyInvalidate; Except End;
                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_EndModify, c_NoEventData);
                        Except End;

                        { Read back: the text is stroke now iff it is no longer
                          bold or italic. }
                        IsTT := False;
                        Try If Desig.Bold Then IsTT := True; Except End;
                        Try If Desig.Italic Then IsTT := True; Except End;
                        If Not IsTT Then
                        Begin
                            Inc(Converted);
                            If Converted <= 50 Then
                            Begin
                                If NamesJson <> '' Then NamesJson := NamesJson + ',';
                                NamesJson := NamesJson + '"' +
                                    EscapeJsonString(FpName) + '"';
                            End;
                        End;
                    End;
                    Inc(Total);
                End;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    Try Board.ViewManager_FullUpdate; Except End;
    Try
        SrvDoc := Client.GetDocumentByPath(LibPath);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    RespJson :=
        '{"success":true' +
        ',"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"designators":' + IntToStr(Total) +
        ',"converted":' + IntToStr(Converted) +
        ',"names":[' + NamesJson + ']}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_SetDesignators - Apply designator edits to MANY footprints in ONE       }
{ library pass.                                                                }
{                                                                              }
{ Lib_SetDesignator handles a single footprint and re-scans the whole library }
{ to find it by name, so repairing M footprints of N costs O(M*N) iterator    }
{ steps -- hundreds of thousands on a big library. This walks the             }
{ LibraryIterator once and applies whatever edit matches each footprint.      }
{                                                                              }
{ The edit list arrives as a FILE, not as JSON: ExtractJsonValue is a flat    }
{ key reader and cannot express an array. One edit per line, tab separated:   }
{                                                                              }
{   name <TAB> layer_id <TAB> x <TAB> y <TAB> height <TAB> create             }
{                                                                              }
{ An empty field means "leave this property alone". create is 1 or 0. Blank   }
{ lines are skipped. x and y are native TCoord offsets from the footprint's   }
{ OWN origin; height is mils. A create against a footprint that already has a }
{ .Designator is refused, never duplicated.                                   }
{                                                                              }
{ Params:                                                                      }
{   edits_path   - required; path of the tab-separated edit file.             }
{   library_path - optional .PcbLib to focus first.                           }
{                                                                              }
{ Response: applied, created, failed counts plus the names of footprints in   }
{ the edit file that the library does not contain.                            }
Function Lib_SetDesignators(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, EditsPath, FpName, TxtStr, RespJson, MissingJson : String;
    Line, WantName, LayerStr, XStr, YStr, HeightStr, CreateStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    SrvDoc : IServerDocument;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    Edits : TStringList;
    DoneKeys : String;
    Lyr : TLayer;
    XOrg, YOrg, TgtX, TgtY, CurCX, CurCY : TCoord;
    TRect : TCoordRect;
    ImmovableJson : String;
    I, NewX, NewY, NewH, LayerId, WidthMils : Integer;
    Applied, CreatedCount, FailedCount, MissingCount, RefusedCount : Integer;
    ImmovableCount : Integer;
    DoCreate, MovedOk : Boolean;
Begin
    EditsPath := ExtractJsonValue(Params, 'edits_path');
    If EditsPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'edits_path is required');
        Exit;
    End;
    If Not FileExists(EditsPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_EDITS',
            'Edit file not found: ' + EditsPath);
        Exit;
    End;
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Board := PcbLib.Board;

    Applied := 0;
    CreatedCount := 0;
    FailedCount := 0;
    MissingCount := 0;
    RefusedCount := 0;
    ImmovableCount := 0;
    MissingJson := '';
    ImmovableJson := '';

    DoneKeys := '';
    Edits := TStringList.Create;
    Try
        Edits.LoadFromFile(EditsPath);

        PCBServer.PreProcess;
        Try
            Iter := PcbLib.LibraryIterator_Create;
            Try
                Footprint := Iter.FirstPCBObject;
                While Footprint <> Nil Do
                Begin
                    FpName := '';
                    Try FpName := Footprint.Name; Except End;

                    { Per-footprint origin: the frame the geometry dump used. }
                    XOrg := 0;  YOrg := 0;
                    Try XOrg := Footprint.X; Except End;
                    Try YOrg := Footprint.Y; Except End;

                    { Does any edit line name this footprint? }
                    For I := 0 To Edits.Count - 1 Do
                    Begin
                        Line := Edits[I];
                        If Line = '' Then Continue;
                        WantName := SplitNextTab(Line);
                        If UpperCase(WantName) <> UpperCase(FpName) Then Continue;

                        DoneKeys := DoneKeys + '|' + UpperCase(WantName) + '|';
                        LayerStr := SplitNextTab(Line);
                        XStr := SplitNextTab(Line);
                        YStr := SplitNextTab(Line);
                        HeightStr := SplitNextTab(Line);
                        CreateStr := SplitNextTab(Line);
                        DoCreate := (CreateStr = '1');
                        LayerId := StrToIntDef(LayerStr, -1);
                        NewX := StrToIntDef(XStr, 0);
                        NewY := StrToIntDef(YStr, 0);
                        NewH := StrToIntDef(HeightStr, 0);

                        { Existing .Designator, if any. Match on UnderlyingString:
                          .Text is the RENDERED special string and misses real
                          designators, which is how a create became a DUPLICATE. }
                        Desig := Nil;
                        GrpIter := Footprint.GroupIterator_Create;
                        Try
                            GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                            Txt := GrpIter.FirstPCBObject;
                            While Txt <> Nil Do
                            Begin
                                TxtStr := '';
                                Try TxtStr := Txt.UnderlyingString; Except End;
                                If TxtStr = '' Then Try TxtStr := Txt.Text; Except End;
                                If UpperCase(TxtStr) = '.DESIGNATOR' Then
                                Begin
                                    Desig := Txt;
                                    Break;
                                End;
                                Txt := GrpIter.NextPCBObject;
                            End;
                        Finally
                            Footprint.GroupIterator_Destroy(GrpIter);
                        End;

                        { Belt and braces: a create request against a footprint
                          that already has a designator is refused outright, so
                          a detection miss can never duplicate the string. }
                        If DoCreate And (Desig <> Nil) Then
                        Begin
                            Inc(RefusedCount);
                            Break;
                        End;

                        If (Desig = Nil) And (Not DoCreate) Then
                        Begin
                            Inc(FailedCount);
                            Break;
                        End;

                        If Desig = Nil Then
                        Begin
                            If (LayerId < 0) Or (XStr = '') Or (YStr = '') Or (NewH <= 0) Then
                            Begin
                                Inc(FailedCount);
                                Break;
                            End;
                            { A PcbLib shares ONE board across every footprint,
                              and Board.AddPCBObject attaches the primitive to
                              the library's CURRENT component -- not to the
                              IPCB_LibComponent handed to Footprint.AddPCBObject.
                              Without making the target current first, every
                              create in a sweep piles onto one footprint. }
                            Try PcbLib.CurrentComponent := Footprint; Except End;
                            If PcbLib.CurrentComponent <> Footprint Then
                            Begin
                                Inc(FailedCount);
                                Break;
                            End;
                            Desig := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
                            If Desig = Nil Then
                            Begin
                                Inc(FailedCount);
                                Break;
                            End;
                            Desig.UnderlyingString := '.Designator';
                            WidthMils := NewH Div 5;
                            If WidthMils < 1 Then WidthMils := 1;
                            Desig.Width := MilsToCoord(WidthMils);
                            Footprint.AddPCBObject(Desig);
                            Board.AddPCBObject(Desig);
                            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
                            PCBServer.SendMessageToRobots(Board.I_ObjectAddress,
                                c_Broadcast, PCBM_BoardRegisteration, Desig.I_ObjectAddress);
                            Inc(CreatedCount);
                        End;

                        { Bracket the mutation so the server registers it. }
                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        Except End;

                        If LayerId >= 0 Then
                        Begin
                            Lyr := LayerId;
                            Try Desig.Layer := Lyr; Except End;
                        End;
                        { Size FIRST: resizing the text changes its bounding box,
                          so centring must be computed against the final size. }
                        If NewH > 0 Then
                            Try Desig.Size := MilsToCoord(NewH); Except End;
                        MovedOk := True;
                        If (XStr <> '') And (YStr <> '') Then
                        Begin
                            { Re-read the origin HERE: making a component current
                              (the create path does) relocates it, so an origin
                              captured earlier in the loop is stale and the text
                              lands at the board origin instead of on the part. }
                            Try XOrg := Footprint.X; Except End;
                            Try YOrg := Footprint.Y; Except End;
                            { x/y are the ANCHOR (XLocation) the caller wants, in
                              TCoord relative to the footprint origin. The caller
                              derived it from the anchor and bbox centre this
                              script reported, so no bounding box is read here --
                              a write-time bbox read proved unreliable. }
                            Try
                                Desig.XLocation := XOrg + NewX;
                                Desig.YLocation := YOrg + NewY;
                            Except End;
                            { READ BACK. Some designator texts silently refuse to
                              move (the assignment neither takes nor raises), and
                              reporting those as applied hides a failed repair
                              behind a success count. }
                            MovedOk := False;
                            Try
                                MovedOk := (Desig.XLocation = XOrg + NewX) And
                                           (Desig.YLocation = YOrg + NewY);
                            Except End;
                            If Not MovedOk Then
                            Begin
                                Inc(ImmovableCount);
                                If ImmovableCount <= 25 Then
                                Begin
                                    If ImmovableJson <> '' Then
                                        ImmovableJson := ImmovableJson + ',';
                                    ImmovableJson := ImmovableJson + '"' +
                                        EscapeJsonString(FpName) + '"';
                                End;
                            End;
                            { BoundingRectangle is CACHED and is NOT invalidated
                              by assigning XLocation. Without this the next read
                              returns the box from the text's OLD position, so a
                              caller centring on the bbox diverges every pass. }
                            Try Desig.GraphicallyInvalidate; Except End;
                        End;

                        Try
                            PCBServer.SendMessageToRobots(Desig.I_ObjectAddress,
                                c_Broadcast, PCBM_EndModify, c_NoEventData);
                        Except End;

                        If MovedOk Then Inc(Applied) Else Inc(FailedCount);
                        Break;
                    End;
                    Footprint := Iter.NextPCBObject;
                End;
            Finally
                PcbLib.LibraryIterator_Destroy(Iter);
            End;
        Finally
            PCBServer.PostProcess;
        End;

        { Edit lines naming a footprint this library does not contain. }
        For I := 0 To Edits.Count - 1 Do
        Begin
            Line := Edits[I];
            If Line = '' Then Continue;
            WantName := SplitNextTab(Line);
            If Pos('|' + UpperCase(WantName) + '|', DoneKeys) > 0 Then Continue;
            If MissingCount < 25 Then
            Begin
                If MissingJson <> '' Then MissingJson := MissingJson + ',';
                MissingJson := MissingJson + '"' + EscapeJsonString(WantName) + '"';
            End;
            Inc(MissingCount);
        End;
    Finally
        Edits.Free;
    End;

    Try Board.ViewManager_FullUpdate; Except End;
    Try
        SrvDoc := Client.GetDocumentByPath(LibPath);
        If SrvDoc <> Nil Then SrvDoc.SetModified(True);
    Except End;

    RespJson :=
        '{"success":true' +
        ',"applied":' + IntToStr(Applied) +
        ',"created":' + IntToStr(CreatedCount) +
        ',"failed":' + IntToStr(FailedCount) +
        ',"refused":' + IntToStr(RefusedCount) +
        ',"immovable_count":' + IntToStr(ImmovableCount) +
        ',"immovable":[' + ImmovableJson + ']' +
        ',"missing_count":' + IntToStr(MissingCount) +
        ',"missing":[' + MissingJson + ']}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_ReloadLibrary - Close and reopen a PcbLib so its in-memory caches are    }
{ rebuilt from disk.                                                           }
{                                                                              }
{ IPCB_Text.BoundingRectangle is populated when the document loads and is NOT  }
{ refreshed by assigning XLocation or Size (GraphicallyInvalidate does not do  }
{ it either). After a write, every text's rectangle in that session reports    }
{ the text's OLD extent, so anything that centres on the bounding box computes }
{ its correction from stale geometry and moves the text by a wrong delta.      }
{                                                                              }
{ Closing the document drops those caches; the next OpenObject re-reads from   }
{ disk. Caller MUST save first -- this does not save, and a dirty close would  }
{ either lose the edits or raise a prompt.                                     }
{                                                                              }
{ Params: library_path (required).                                             }
Function Lib_ReloadLibrary(Params : String; RequestId : String) : String;
Var
    LibPath : String;
    PcbLib : IPCB_Library;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'library_path is required');
        Exit;
    End;
    If Not FileExists(LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'Library not found on disk: ' + LibPath);
        Exit;
    End;

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    Try RunProcess('WorkspaceManager:CloseObject'); Except End;
    Try Application.ProcessMessages; Except End;

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    Try RunProcess('WorkspaceManager:OpenObject'); Except End;
    Try Application.ProcessMessages; Except End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Library did not reopen: ' + LibPath);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"reloaded":true,"library_path":"' + EscapeJsonString(LibPath) + '"}');
End;

{ Lib_ProbeDesignator - Diagnostic. Dump the RAW geometry of one footprint's   }
{ .Designator and of the footprint itself, so the caller can see what          }
{ BoundingRectangle actually measures. Read-only.                              }
{                                                                              }
{ Params: footprint_name (required), library_path (optional).                  }
Function Lib_ProbeDesignator(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, TxtStr, RespJson, TextsJson : String;
    TextN : Integer;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Txt, Desig : IPCB_Text;
    Pad : IPCB_Pad;
    TRect, FRect : TCoordRect;
    PadMinX, PadMinY, PadMaxX, PadMaxY : TCoord;
    PadN : Integer;
Begin
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    LibPath := ExtractJsonValue(Params, 'library_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PcbLib');
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(FpWanted) Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', 'Not found: ' + FpWanted);
        Exit;
    End;

    { Pad extents, as an independent yardstick for "how big is this part". }
    PadN := 0;
    PadMinX := 0;  PadMinY := 0;  PadMaxX := 0;  PadMaxY := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            If PadN = 0 Then
            Begin
                PadMinX := Pad.X;  PadMaxX := Pad.X;
                PadMinY := Pad.Y;  PadMaxY := Pad.Y;
            End
            Else
            Begin
                If Pad.X < PadMinX Then PadMinX := Pad.X;
                If Pad.X > PadMaxX Then PadMaxX := Pad.X;
                If Pad.Y < PadMinY Then PadMinY := Pad.Y;
                If Pad.Y > PadMaxY Then PadMaxY := Pad.Y;
            End;
            Inc(PadN);
            Pad := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;

    Desig := Nil;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            TxtStr := '';
            Try TxtStr := Txt.UnderlyingString; Except End;
            If UpperCase(TxtStr) = '.DESIGNATOR' Then
            Begin
                Desig := Txt;
                Break;
            End;
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    If Desig = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_DESIGNATOR', 'No .Designator');
        Exit;
    End;

    { Assign the whole record; writing individual fields of an uninitialised
      local raises in DelphiScript. }
    TRect := Desig.BoundingRectangle;
    FRect := Target.BoundingRectangle;

    { Every text in the footprint, with the properties that could plausibly make
      one refuse to persist a move: layer, rotation, mirror, lock/selection, and
      whether it is the component's own designator object rather than a free
      text. Dump them rather than theorise about them. }
    TextsJson := '[';
    TextN := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
        Txt := GrpIter.FirstPCBObject;
        While Txt <> Nil Do
        Begin
            TxtStr := '';
            Try TxtStr := Txt.UnderlyingString; Except End;
            If TextN > 0 Then TextsJson := TextsJson + ',';
            TextsJson := TextsJson +
                '{"underlying":"' + EscapeJsonString(TxtStr) + '"';
            Try TextsJson := TextsJson +
                ',"text":"' + EscapeJsonString(Txt.Text) + '"'; Except End;
            Try TextsJson := TextsJson +
                ',"layer_id":' + IntToStr(Txt.Layer); Except End;
            Try TextsJson := TextsJson +
                ',"x":' + IntToStr(Txt.XLocation) +
                ',"y":' + IntToStr(Txt.YLocation); Except End;
            Try TextsJson := TextsJson +
                ',"rotation":' + FloatToJsonStr(Txt.Rotation); Except End;
            Try TextsJson := TextsJson +
                ',"mirror":' + BoolToJsonStr(Txt.MirrorFlag); Except End;
            Try TextsJson := TextsJson +
                ',"selected":' + BoolToJsonStr(Txt.Selected); Except End;
            Try TextsJson := TextsJson +
                ',"moveable":' + BoolToJsonStr(Txt.Moveable); Except End;
            Try TextsJson := TextsJson +
                ',"is_designator":' + BoolToJsonStr(Txt.IsDesignator); Except End;
            Try TextsJson := TextsJson +
                ',"use_ttfonts":' + BoolToJsonStr(Txt.UseTTFonts); Except End;
            { The REAL font-type discriminator: TextKind is the enum
              (0=stroke, 1=TrueType, 2=BarCode), UseTTFonts is only a legacy
              flag. Read the raw ordinal plus the TT font details so a TrueType
              designator is unambiguous. }
            Try TextsJson := TextsJson +
                ',"text_kind":' + IntToStr(Txt.TextKind); Except End;
            Try TextsJson := TextsJson +
                ',"font_name":"' + EscapeJsonString(Txt.FontName) + '"'; Except End;
            Try TextsJson := TextsJson +
                ',"bold":' + BoolToJsonStr(Txt.Bold); Except End;
            Try TextsJson := TextsJson +
                ',"italic":' + BoolToJsonStr(Txt.Italic); Except End;
            Try TextsJson := TextsJson +
                ',"inverted":' + BoolToJsonStr(Txt.Inverted); Except End;
            { The full font/kind property set (from AssemblyTextPrep's
              CopyTextFormatFromTo). One of these -- not Bold -- is what makes
              the position revert; probe both a working and a failing designator
              to see which differs. }
            Try TextsJson := TextsJson +
                ',"font_id":' + IntToStr(Txt.FontID); Except End;
            Try TextsJson := TextsJson +
                ',"use_inv_rect":' + BoolToJsonStr(Txt.UseInvertedRectangle); Except End;
            Try TextsJson := TextsJson +
                ',"ttf_w":' + IntToStr(Txt.TTFTextWidth); Except End;
            Try TextsJson := TextsJson +
                ',"ttf_h":' + IntToStr(Txt.TTFTextHeight); Except End;
            Try TextsJson := TextsJson +
                ',"inv_rect_w":' + IntToStr(Txt.InvRectWidth); Except End;
            Try TextsJson := TextsJson +
                ',"inv_rect_h":' + IntToStr(Txt.InvRectHeight); Except End;
            TextsJson := TextsJson + '}';
            Inc(TextN);
            Txt := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    TextsJson := TextsJson + ']';

    RespJson :=
        '{"footprint":"' + EscapeJsonString(FpWanted) + '"' +
        ',"texts":' + TextsJson +
        ',"fp_x":' + IntToStr(Target.X) +
        ',"fp_y":' + IntToStr(Target.Y) +
        ',"fp_rect":[' + IntToStr(FRect.X1) + ',' + IntToStr(FRect.Y1) + ',' +
                         IntToStr(FRect.X2) + ',' + IntToStr(FRect.Y2) + ']' +
        ',"pad_count":' + IntToStr(PadN) +
        ',"pad_rect":[' + IntToStr(PadMinX) + ',' + IntToStr(PadMinY) + ',' +
                          IntToStr(PadMaxX) + ',' + IntToStr(PadMaxY) + ']' +
        ',"desig_anchor":[' + IntToStr(Desig.XLocation) + ',' +
                              IntToStr(Desig.YLocation) + ']' +
        ',"desig_rect":[' + IntToStr(TRect.X1) + ',' + IntToStr(TRect.Y1) + ',' +
                            IntToStr(TRect.X2) + ',' + IntToStr(TRect.Y2) + ']' +
        ',"desig_size":' + IntToStr(Desig.Size) +
        ',"desig_width":' + IntToStr(Desig.Width) +
        ',"desig_text":"' + EscapeJsonString(Desig.Text) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

Function Lib_LinkFootprint(Params : String; RequestId : String) : String;
Var
    FootprintName, ComponentName, ReplaceStr, MT : String;
    Replace : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Impl, Impl2, Found : ISch_Implementation;
    ImplIter : ISch_Iterator;
    Guard : Integer;
Begin
    FootprintName := ExtractJsonValue(Params, 'footprint_name');
    ComponentName := ExtractJsonValue(Params, 'component_name');
    ReplaceStr := ExtractJsonValue(Params, 'replace');
    Replace := (ReplaceStr = '') Or (ReplaceStr = 'true') Or (ReplaceStr = 'True') Or (ReplaceStr = '1');

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    { Resolve the target by component_name so the link lands on the intended
      symbol -- the handler previously ignored it and always used the
      last-created component. Fall back to last-created/selected when empty. }
    If ComponentName <> '' Then
        Component := SelectLibComponent(ComponentName)
    Else
        Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'Target component not found (component_name) or nothing selected');
        Exit;
    End;

    { Attach the footprint model via Component.AddSchImplementation -- the
      dedicated factory that creates, owns AND registers the implementation in
      one call. The old SchObjectFactory(eImplementation) + Component.AddSchObject
      path is WRONG for models: ISch_Implementation is not an ISch_GraphicalObject,
      so on AD26 both SetOwnerPart (writing OwnerPartId) and AddSchObject raise a
      modal "Undeclared identifier" that Try/Except cannot catch and WEDGE the
      bridge. }
    SchServer.ProcessControl.PreProcess(SchLib, '');

    { replace=true (default): drop existing footprint (PCBLIB) implementations   }
    { first so re-linking replaces instead of appending a duplicate (the append  }
    { behaviour is what bloated components with duplicate models).                }
    If Replace Then
    Begin
        Guard := 1000;
        While Guard > 0 Do
        Begin
            Found := Nil;
            ImplIter := Component.SchIterator_Create;
            Try
                ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
                Impl2 := ImplIter.FirstSchObject;
                While Impl2 <> Nil Do
                Begin
                    MT := '';
                    Try MT := Impl2.ModelType; Except End;
                    If MT = cDocKind_PcbLib Then Begin Found := Impl2; Break; End;
                    Impl2 := ImplIter.NextSchObject;
                End;
            Finally
                Component.SchIterator_Destroy(ImplIter);
            End;
            If Found = Nil Then Break;
            Try Component.RemoveSchImplementation(Found); Except End;
            Dec(Guard);
        End;
    End;

    Impl := Component.AddSchImplementation;
    If Impl <> Nil Then
    Begin
        Try Impl.ClearAllDatafileLinks; Except End;
        Impl.ModelName := FootprintName;
        Impl.ModelType := cDocKind_PcbLib;
        Try Impl.IsCurrent := True; Except End;
        { A footprint implementation MUST carry a datafile link (entity name =   }
        { the footprint) or the compiler reports "Missing Component Models".     }
        { Bind by name: entity = footprint, EMPTY location. Never pass a full    }
        { .PcbLib path as the location -- a path blocks/wedges AD26; empty is    }
        { safe and is what a self-contained package wants.                       }
        Try Impl.AddDataFileLink(FootprintName, '', 'PCBLib'); Except End;
    End;

    SchServer.ProcessControl.PostProcess(SchLib, 'Link footprint');
    MarkLibDirty(SchLib);

    If Impl <> Nil Then
        Result := BuildSuccessResponse(RequestId, '{"success":true,"footprint":"' + EscapeJsonString(FootprintName) + '","replaced":' + BoolToJsonStr(Replace) + '}')
    Else
        Result := BuildErrorResponse(RequestId, 'LINK_FAILED', 'Failed to link footprint');
End;

{ Lib_Link3DModel - attach a 3D STEP model to a PcbLib FOOTPRINT.              }
{                                                                              }
{ A 3D model in Altium is geometry that lives on the footprint as an          }
{ IPCB_ComponentBody, NOT a name-reference on the schematic symbol. The STEP  }
{ is loaded with ModelFactory_FromFilename and the body added to the          }
{ footprint (canonical AutoSTEPplacer pattern; same PCB object-factory family }
{ as Lib_AddFootprintPad). The previous schematic-side version attached a     }
{ 'PCB3DModel' implementation -- a wrong ModelType (the constant is           }
{ 'PCB3DLib') -- and passed the path to AddDataFileLink, which blocks on AD26.}
{                                                                              }
{ Params: component_name (footprint name; empty = current footprint),         }
{         model_path (.step/.stp file). offset_*/rotation_* are accepted but  }
{         not applied (Altium ignores them on import; set in the editor).     }
Function Lib_Link3DModel(Params : String; RequestId : String) : String;
Var
    ModelPath, ComponentName, FpName, AppliedJson : String;
    OffX, OffY, OffZ : Integer;
    RotZ : Double;
    DidStandoff, DidRotation, DidMove : Boolean;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Iter : IPCB_LibraryIterator;
    Body : IPCB_ComponentBody;
    Model : IPCB_Model;
Begin
    ModelPath := ExtractJsonValue(Params, 'model_path');
    ComponentName := ExtractJsonValue(Params, 'component_name');
    { Mils and degrees, matching the tool's documented units.             }
    { rotation_x / rotation_y are deliberately NOT read: the body exposes }
    { StandoffHeight and a PLANAR Rotation, and the PCB API reference     }
    { gives the model no X or Y tilt, so reading them would imply a       }
    { capability that does not exist.                                      }
    OffX := StrToIntDef(ExtractJsonValue(Params, 'offset_x'), 0);
    OffY := StrToIntDef(ExtractJsonValue(Params, 'offset_y'), 0);
    OffZ := StrToIntDef(ExtractJsonValue(Params, 'offset_z'), 0);
    RotZ := StrToFloatDef(ExtractJsonValue(Params, 'rotation_z'), 0.0);

    If (ModelPath = '') Or (Not FileExists(ModelPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MODEL_FILE',
            'model_path is empty or the file does not exist: ' + ModelPath);
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB library is active (a 3D body attaches to a PcbLib footprint)');
        Exit;
    End;

    { Select the footprint by name, otherwise use the current one. }
    If ComponentName <> '' Then
    Begin
        Footprint := Nil;
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                If Footprint.Name = ComponentName Then Break;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
        If Footprint = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
                'Footprint not found in library: ' + ComponentName);
            Exit;
        End;
        Try PcbLib.SetState_CurrentComponent(Footprint); Except End;
    End
    Else
        Footprint := PcbLib.CurrentComponent;

    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'No footprint selected (pass component_name to choose one)');
        Exit;
    End;
    FpName := '';
    Try FpName := Footprint.Name; Except End;

    PCBServer.PreProcess;
    Try
        Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
        If Body = Nil Then
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                'PCBObjectFactory returned Nil for eComponentBodyObject')
        Else
        Begin
            { Load the STEP geometry, then bind + add (AutoSTEPplacer order). }
            Model := Body.ModelFactory_FromFilename(ModelPath, False);
            If Model = Nil Then
                Result := BuildErrorResponse(RequestId, 'MODEL_LOAD_FAILED',
                    'Could not load 3D model from ' + ModelPath)
            Else
            Begin
                Body.SetState_FromModel;
                Body.Model := Model;
                Footprint.AddPCBObject(Body);

                { Placement adjustments. Each is guarded AND REPORTED:     }
                { StandoffHeight, Rotation and MoveByXY are documented on  }
                { the body (MoveByXY via IPCB_Primitive, used on other     }
                { primitives in PCB.pas) but appear nowhere else in this   }
                { codebase, so the first live run needs to show which ones }
                { actually took rather than trusting a blanket success.    }
                DidStandoff := False;
                DidRotation := False;
                DidMove := False;
                If OffZ <> 0 Then
                    Try
                        Body.StandoffHeight := MilsToCoord(OffZ);
                        DidStandoff := True;
                    Except End;
                If RotZ <> 0 Then
                    Try
                        Body.Rotation := RotZ;
                        DidRotation := True;
                    Except End;
                If (OffX <> 0) Or (OffY <> 0) Then
                    Try
                        Body.MoveByXY(MilsToCoord(OffX), MilsToCoord(OffY));
                        DidMove := True;
                    Except End;

                AppliedJson := JsonBool('standoff_height', DidStandoff) + ','
                    + JsonBool('rotation_z', DidRotation) + ','
                    + JsonBool('offset_xy', DidMove);

                Result := BuildSuccessResponse(RequestId, JsonObj(
                    JsonBool('success', True) + ','
                    + JsonStr('footprint', FpName) + ','
                    + JsonStr('model', ExtractFileName(ModelPath)) + ','
                    + JsonRaw('applied', JsonObj(AppliedJson))));
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    SaveDocByPath(PcbLib.Board.FileName);
End;

Function Lib_GetComponents(Params : String; RequestId : String) : String;
Var
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ParamIterator : ISch_Iterator;
    Param : ISch_Parameter;
    Impl : ISch_Implementation;
    Workspace : IWorkspace;
    Doc : IDocument;
    LibPath, Data, CompName, ParamList, WithParamsStr : String;
    ParamLower, ParamText, WithDesigStr, DefDesig : String;
    Mpn, Manufacturer, Datasheet, FootprintName : String;
    CompNum, I : Integer;
    First, WithParams, WithDesignator : Boolean;
Begin
    // Get library path from parameter or active document
    LibPath := ExtractJsonValue(Params, 'library_path');

    // Optional flag: dump parameters per component. Default is FALSE because
    // GetState_SchComponentByLibRef + parameter iterator runs O(N) and is the
    // bottleneck on large libraries (a 400+ component standard lib takes
    // tens of seconds with parameters on, sub-second without). Callers that
    // need parameters for a specific symbol should use lib_get_component_details.
    WithParamsStr := ExtractJsonValue(Params, 'with_parameters');
    WithParams := (WithParamsStr = 'true') Or (WithParamsStr = 'True') Or (WithParamsStr = '1');

    // Optional lean flag: emit each component's DEFAULT designator
    // (Component.Designator.Text, e.g. "U?" / "R?" / "IC?"). Like
    // with_parameters it must load the live symbol via
    // GetState_SchComponentByLibRef (the CompInfoReader fast path does NOT
    // expose the designator), but it skips parameter iteration so the
    // payload stays small -- intended for library-wide designator audits.
    WithDesigStr := ExtractJsonValue(Params, 'with_designator');
    WithDesignator := (WithDesigStr = 'true') Or (WithDesigStr = 'True') Or (WithDesigStr = '1');

    If LibPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Doc := Workspace.DM_FocusedDocument;
            If Doc <> Nil Then
            Begin
                // DM_FileName returns just the basename;
                // CreateLibCompInfoReader needs the full path or it
                // silently returns an empty reader (which is exactly the
                // bug that made lib_get_components always report 0).
                Try LibPath := Doc.DM_FullPath; Except End;
                If LibPath = '' Then LibPath := Doc.DM_FileName;
            End;
        End;
    End;

    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library path and no active document');
        Exit;
    End;

    // Use CreateLibCompInfoReader to enumerate components. ICompInfoReader is
    // a fast metadata reader, it returns CompName, AliasName, PartCount and
    // Description directly from the lib file without loading every symbol's
    // primitives, so the cheap path scales linearly with file IO.
    LibReader := SchServer.CreateLibCompInfoReader(LibPath);
    If LibReader = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'READER_FAILED', 'Failed to create library reader for: ' + LibPath);
        Exit;
    End;

    LibReader.ReadAllComponentInfo;
    CompNum := LibReader.NumComponentInfos;

    // Only navigate to live components when the caller asked for parameters,
    // otherwise we skip GetState_SchComponentByLibRef entirely.
    SchLib := Nil;
    If WithParams Or WithDesignator Then
        SchLib := SchServer.GetCurrentSchDocument;

    Data := '[';
    First := True;
    For I := 0 To CompNum - 1 Do
    Begin
        If Not First Then Data := Data + ',';
        First := False;
        CompInfo := LibReader.ComponentInfos[I];
        CompName := CompInfo.CompName;

        { index is the stable addressing key: pass it as component_index to  }
        { any lib tool that resolves a component, to reach names with bytes   }
        { that cannot be reproduced (embedded quotes / control chars).        }
        Data := Data + '{"index":' + IntToStr(I) +
            ',"name":"' + EscapeJsonString(CompName) + '"';
        Try Data := Data + ',"alias_name":"' + EscapeJsonString(CompInfo.AliasName) + '"'; Except End;
        Try Data := Data + ',"part_count":' + IntToStr(CompInfo.PartCount); Except End;
        Data := Data + ',"description":"' + EscapeJsonString(CompInfo.Description) + '"';

        // Slow path, opt-in via with_parameters=true.
        // Atomic-parts contract: when we're already paying for the live
        // component load, harvest mpn / manufacturer / datasheet from the
        // parameter set plus the current implementation's footprint model
        // name so the planner can populate Part directly from inventory.
        If WithParams Or WithDesignator Then
        Begin
            ParamList := '';
            Mpn := '';
            Manufacturer := '';
            Datasheet := '';
            FootprintName := '';
            DefDesig := '';
            If (SchLib <> Nil) And (SchLib.ObjectId = eSchLib) Then
            Begin
                Component := SchLib.GetState_SchComponentByLibRef(CompName);
                If (Component <> Nil) And WithDesignator Then
                    Try DefDesig := Component.Designator.Text; Except End;
                If (Component <> Nil) And WithParams Then
                Begin
                    ParamIterator := Component.SchIterator_Create;
                    ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
                    Param := ParamIterator.FirstSchObject;
                    While Param <> Nil Do
                    Begin
                        If ParamList <> '' Then ParamList := ParamList + ',';
                        ParamText := Param.Text;
                        ParamList := ParamList + '"' + EscapeJsonString(Param.Name) + '":"' + EscapeJsonString(ParamText) + '"';
                        // Capture atomic-parts fields by canonical Altium
                        // parameter names. LowerCase makes us tolerant of
                        // libs that capitalize "MPN" vs "Mpn", etc.
                        ParamLower := LowerCase(Param.Name);
                        If (Mpn = '') And ((ParamLower = 'manufacturer part number')
                            Or (ParamLower = 'manufacturerpartnumber')
                            Or (ParamLower = 'mpn')
                            Or (ParamLower = 'part number')
                            Or (ParamLower = 'partnumber')) Then
                            Mpn := ParamText;
                        If (Manufacturer = '') And ((ParamLower = 'manufacturer')
                            Or (ParamLower = 'mfr')
                            Or (ParamLower = 'mfg')) Then
                            Manufacturer := ParamText;
                        If (Datasheet = '') And ((ParamLower = 'datasheet')
                            Or (ParamLower = 'datasheeturl')
                            Or (ParamLower = 'datasheet url')
                            Or (ParamLower = 'componentlink1url')) Then
                            Datasheet := ParamText;
                        Param := ParamIterator.NextSchObject;
                    End;
                    Component.SchIterator_Destroy(ParamIterator);

                    // First implementation = the linked footprint model
                    // (see Lib_LinkFootprint, which writes Impl.ModelName).
                    // Nil when the symbol has zero implementations.
                    Impl := GetFirstSchImplementation(Component);
                    If Impl <> Nil Then
                        Try FootprintName := Impl.ModelName; Except End;
                End;
            End;
            If WithDesignator Then
                Data := Data + ',"designator":"' + EscapeJsonString(DefDesig) + '"';
            If WithParams Then
            Begin
                Data := Data + ',"parameters":{' + ParamList + '}';
                Data := Data + ',"mpn":"' + EscapeJsonString(Mpn) + '"';
                Data := Data + ',"manufacturer":"' + EscapeJsonString(Manufacturer) + '"';
                Data := Data + ',"datasheet":"' + EscapeJsonString(Datasheet) + '"';
                Data := Data + ',"footprint":"' + EscapeJsonString(FootprintName) + '"';
            End;
        End;
        Data := Data + '}';
    End;

    SchServer.DestroyCompInfoReader(LibReader);
    Data := Data + ']';

    Result := BuildSuccessResponse(RequestId, '{"count":' + IntToStr(CompNum) + ',"components":' + Data + '}');
End;

{ Lib_Search - case-insensitive substring search over all open SchLib docs. }
{ The previous implementation invoked Client:FindComponent, which only       }
{ pops the interactive Find Component panel and returns no data, so the     }
{ tool was unusable from an LLM. This handler enumerates SchLib members of  }
{ every workspace project plus the synthetic FreeDocumentsProject (where    }
{ standalone libraries live), opens an ILibCompInfoReader per file (fast,   }
{ no live-component load) and matches CompName / Description / AliasName   }
{ against the query.                                                         }
{                                                                              }
{ Params:                                                                     }
{   query        - substring (case-insensitive). Required.                   }
{   search_type  - 'all' (default) | 'name' | 'description' | 'parameters'. }
{                  'all' tests name + description + alias. 'parameters'     }
{                  also loads each candidate live (slow on big libs).        }
{   library_path - optional, restrict the search to a single .SchLib file.  }
{   limit        - max matches (default 100).                                }
{ Returns a JSON array of [name, alias_name, description, library_path,    }
{ part_count] per match.                                                    }
Function SearchOneLibrary(LibPath, Query, SearchType : String;
    SearchParams : Boolean; SchLib : ISch_Lib;
    Var ResultsJson : String; Var First : Boolean;
    Var Count : Integer; Limit : Integer) : Boolean;
Var
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Component : ISch_Component;
    ParamIterator : ISch_Iterator;
    Param : ISch_Parameter;
    LowerQuery, CompName, AliasName, Description : String;
    LowerName, LowerAlias, LowerDesc : String;
    NumComps, I : Integer;
    Matched, MatchedParam : Boolean;
Begin
    Result := False;
    LowerQuery := LowerCase(Query);

    LibReader := SchServer.CreateLibCompInfoReader(LibPath);
    If LibReader = Nil Then Exit;

    Try
        LibReader.ReadAllComponentInfo;
        NumComps := LibReader.NumComponentInfos;

        For I := 0 To NumComps - 1 Do
        Begin
            If Count >= Limit Then Break;

            CompInfo := LibReader.ComponentInfos[I];
            CompName := '';
            AliasName := '';
            Description := '';
            Try CompName := CompInfo.CompName; Except End;
            Try AliasName := CompInfo.AliasName; Except End;
            Try Description := CompInfo.Description; Except End;

            LowerName := LowerCase(CompName);
            LowerAlias := LowerCase(AliasName);
            LowerDesc := LowerCase(Description);

            Matched := False;
            If SearchType = 'name' Then
                Matched := Pos(LowerQuery, LowerName) > 0
            Else If SearchType = 'description' Then
                Matched := Pos(LowerQuery, LowerDesc) > 0
            Else
            Begin
                { 'all' / 'parameters' both check name + alias + description }
                { up front. parameters then drops to the slow path on miss. }
                Matched := (Pos(LowerQuery, LowerName) > 0)
                    Or (Pos(LowerQuery, LowerAlias) > 0)
                    Or (Pos(LowerQuery, LowerDesc) > 0);
            End;

            { Slow path, opt-in only via search_type='parameters'. Loads the }
            { live component and walks every parameter's name/value, that's }
            { what makes parameter-search expensive. }
            If (Not Matched) And SearchParams And (SchLib <> Nil) Then
            Begin
                Component := SchLib.GetState_SchComponentByLibRef(CompName);
                If Component <> Nil Then
                Begin
                    MatchedParam := False;
                    ParamIterator := Component.SchIterator_Create;
                    ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
                    Try
                        Param := ParamIterator.FirstSchObject;
                        While (Param <> Nil) And (Not MatchedParam) Do
                        Begin
                            If (Pos(LowerQuery, LowerCase(Param.Name)) > 0)
                                Or (Pos(LowerQuery, LowerCase(Param.Text)) > 0) Then
                                MatchedParam := True;
                            Param := ParamIterator.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(ParamIterator);
                    End;
                    Matched := MatchedParam;
                End;
            End;

            If Matched Then
            Begin
                If Not First Then ResultsJson := ResultsJson + ',';
                First := False;
                ResultsJson := ResultsJson +
                    '{"name":"' + EscapeJsonString(CompName) +
                    '","alias_name":"' + EscapeJsonString(AliasName) +
                    '","description":"' + EscapeJsonString(Description) +
                    '","library_path":"' + EscapeJsonString(LibPath) +
                    '","part_count":' + IntToStr(CompInfo.PartCount) + '}';
                Inc(Count);
            End;
        End;
    Finally
        SchServer.DestroyCompInfoReader(LibReader);
    End;

    Result := True;
End;

Function Lib_Search(Params : String; RequestId : String) : String;
Var
    Query, SearchType, LibPathFilter : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    FocusedSchLib : ISch_Lib;
    DocPath, ResultsJson : String;
    I, J, Count, Limit : Integer;
    First, IsLib, SearchParams : Boolean;
Begin
    Query := ExtractJsonValue(Params, 'query');
    SearchType := ExtractJsonValue(Params, 'search_type');
    LibPathFilter := ExtractJsonValue(Params, 'library_path');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 100);

    If SearchType = '' Then SearchType := 'all';
    SearchParams := SearchType = 'parameters';

    If Query = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'query is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    { Parameter searches need the live component, which only the focused }
    { library exposes. Cache the focused SchLib so SearchOneLibrary can  }
    { pass it through without re-resolving on every match attempt.       }
    FocusedSchLib := Nil;
    If SearchParams Then
    Begin
        Try
            If (SchServer.GetCurrentSchDocument <> Nil)
                And (SchServer.GetCurrentSchDocument.ObjectId = eSchLib) Then
                FocusedSchLib := SchServer.GetCurrentSchDocument;
        Except End;
    End;

    ResultsJson := '';
    First := True;
    Count := 0;

    { Single-library mode short-circuits the workspace walk. }
    If LibPathFilter <> '' Then
        SearchOneLibrary(LibPathFilter, Query, SearchType, SearchParams,
            FocusedSchLib, ResultsJson, First, Count, Limit)
    Else
    Begin
        For I := 0 To Workspace.DM_ProjectCount - 1 Do
        Begin
            If Count >= Limit Then Break;
            Project := Workspace.DM_Projects(I);
            If Project = Nil Then Continue;
            For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
            Begin
                If Count >= Limit Then Break;
                Doc := Project.DM_LogicalDocuments(J);
                If Doc = Nil Then Continue;
                IsLib := False;
                Try
                    DocPath := Doc.DM_FullPath;
                    IsLib := (UpperCase(Doc.DM_DocumentKind) = 'SCHLIB')
                        Or (Pos('.SCHLIB', UpperCase(DocPath)) > 0);
                Except End;
                If Not IsLib Then Continue;
                SearchOneLibrary(DocPath, Query, SearchType, SearchParams,
                    FocusedSchLib, ResultsJson, First, Count, Limit);
            End;
        End;

        { Free documents (libraries opened standalone, not in any project) }
        Try
            Project := Workspace.DM_FreeDocumentsProject;
            If Project <> Nil Then
            Begin
                For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    If Count >= Limit Then Break;
                    Doc := Project.DM_LogicalDocuments(J);
                    If Doc = Nil Then Continue;
                    IsLib := False;
                    Try
                        DocPath := Doc.DM_FullPath;
                        IsLib := (UpperCase(Doc.DM_DocumentKind) = 'SCHLIB')
                            Or (Pos('.SCHLIB', UpperCase(DocPath)) > 0);
                    Except End;
                    If Not IsLib Then Continue;
                    SearchOneLibrary(DocPath, Query, SearchType, SearchParams,
                        FocusedSchLib, ResultsJson, First, Count, Limit);
                End;
            End;
        Except End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"query":"' + EscapeJsonString(Query) +
        '","search_type":"' + EscapeJsonString(SearchType) +
        '","count":' + IntToStr(Count) +
        ',"limit":' + IntToStr(Limit) +
        ',"truncated":' + BoolToJsonStr(Count >= Limit) +
        ',"results":[' + ResultsJson + ']}');
End;

{ Lib_GetComponentDetails - full inspection of one library component.        }
{ Returns metadata (name, description, part_count, alias_name) PLUS pins,    }
{ parameters, and full visual-style records for the designator, the comment, }
{ and every parameter (font_id, color, is_hidden, x, y, orientation,        }
{ justification). FontId can be expanded into a (name, size, bold, italic)  }
{ record by calling get_font_spec; we pass it through as an integer here    }
{ here so the cost stays on the caller when style detail isn't needed.       }
{                                                                              }
{ Pins/parameters require loading the live ISch_Component, which only the    }
{ SchLib editor can produce, so the target library must be the focused       }
{ SchServer document. If the caller passed a library_path that doesn't       }
{ match the focused doc, we open it via WorkspaceManager:OpenObject before   }
{ resolving. Saves are deferred (see MarkLibDirty), so opening doesn't       }
{ disturb in-flight edits on other libs.                                     }
{                                                                              }
{ Schema breaks vs the previous version (introduced two commits ago):        }
{   designator_prefix (str) -> designator (object: text, font_id, color,    }
{                              is_hidden, x, y, orientation, justification) }
{   pins[].font_id, color, label_hidden added                                }
{   comment (object) added                                                   }
{   parameter_styles (array, parallel to parameters dict) added              }

{ BuildLabelStyleJson reads visual-style props off any ISch_Label-derived    }
{ object (Designator, Comment, Parameter, NetLabel, ...) using late-bound   }
{ accessors. Each access is wrapped in Try/Except since not every property  }
{ is present on every ISch_Label subtype, and DelphiScript fails at runtime }
{ rather than compile time on a missing late-bound property.                 }
Function BuildLabelStyleJson(Lbl : ISch_Label; IncludeText : Boolean) : String;
Var
    Txt : String;
    FontId, ColorVal, OrientVal, JustVal, LocX, LocY : Integer;
    HiddenVal : Boolean;
Begin
    Txt := '';
    FontId := 0;
    ColorVal := 0;
    OrientVal := 0;
    JustVal := 0;
    LocX := 0;
    LocY := 0;
    HiddenVal := False;
    Try Txt := Lbl.Text; Except End;
    Try FontId := Lbl.FontId; Except End;
    Try ColorVal := Lbl.Color; Except End;
    Try HiddenVal := Lbl.IsHidden; Except End;
    Try LocX := CoordToMils(Lbl.Location.X); Except End;
    Try LocY := CoordToMils(Lbl.Location.Y); Except End;
    Try OrientVal := Lbl.Orientation; Except End;
    Try JustVal := Lbl.Justification; Except End;

    Result := '{';
    If IncludeText Then
        Result := Result + '"text":"' + EscapeJsonString(Txt) + '",';
    Result := Result +
        '"font_id":' + IntToStr(FontId) +
        ',"color":' + IntToStr(ColorVal) +
        ',"is_hidden":' + BoolToJsonStr(HiddenVal) +
        ',"x":' + IntToStr(LocX) +
        ',"y":' + IntToStr(LocY) +
        ',"orientation":' + IntToStr(OrientVal) +
        ',"justification":' + IntToStr(JustVal) + '}';
End;

{..............................................................................}
{ ResolveLibComponent - fetch a live ISch_Component from a focused SchLib by   }
{ name OR by a name-free integer index.                                        }
{                                                                              }
{ Why the index exists: every by-name fetch funnels through                    }
{ GetState_SchComponentByLibRef, an exact-string lookup, and there is no       }
{ positional accessor in the SchLib API. A LibReference that carries bytes a   }
{ caller cannot reproduce (an embedded '"', or a control char like #16/#17/#19 }
{ left by a broken import) is therefore unreachable, the exact name never      }
{ survives the round-trip back through the caller. component_index (>= 0) is   }
{ the position in ILibCompInfoReader order, the SAME order lib_get_components   }
{ emits. We read Altium's OWN copy of the name at that position and fetch by    }
{ it, so the odd bytes stay server-side. If the direct fetch still misses (an  }
{ Altium hash quirk on the odd bytes), fall back to a SchLibIterator walk with }
{ a byte-exact LibReference compare (Pascal string equality is control-char    }
{ safe). ResolvedName echoes the name we landed on so the caller can confirm   }
{ which entry index N was. On failure returns Nil and sets ErrCode/ErrMsg.     }
Function ResolveLibComponent(SchLib : ISch_Lib; LibPath, Params : String;
    Var ResolvedName : String; Var ErrCode : String; Var ErrMsg : String) : ISch_Component;
Var
    IdxStr, WantName : String;
    Idx, CompNum : Integer;
    LibReader : ILibCompInfoReader;
    Iter : ISch_Iterator;
    LibComp : ISch_Component;
Begin
    Result := Nil;
    ResolvedName := '';
    ErrCode := '';
    ErrMsg := '';

    IdxStr := ExtractJsonValue(Params, 'component_index');
    If IdxStr <> '' Then
    Begin
        If Not IsIntStr(IdxStr) Then
        Begin
            ErrCode := 'BAD_INDEX';
            ErrMsg := 'component_index must be a non-negative integer, got: ' + IdxStr;
            Exit;
        End;
        Idx := StrToIntDef(IdxStr, -1);
        If Idx < 0 Then
        Begin
            ErrCode := 'BAD_INDEX';
            ErrMsg := 'component_index must be >= 0';
            Exit;
        End;

        { Read Altium's own copy of the name at this position. }
        WantName := '';
        LibReader := SchServer.CreateLibCompInfoReader(LibPath);
        If LibReader = Nil Then
        Begin
            ErrCode := 'READER_FAILED';
            ErrMsg := 'Failed to create library reader for: ' + LibPath;
            Exit;
        End;
        Try
            LibReader.ReadAllComponentInfo;
            CompNum := LibReader.NumComponentInfos;
            If Idx >= CompNum Then
            Begin
                ErrCode := 'INDEX_OUT_OF_RANGE';
                ErrMsg := 'component_index ' + IntToStr(Idx) + ' out of range (0..'
                    + IntToStr(CompNum - 1) + ')';
            End
            Else
                WantName := LibReader.ComponentInfos[Idx].CompName;
        Finally
            SchServer.DestroyCompInfoReader(LibReader);
        End;
        If ErrCode <> '' Then Exit;

        ResolvedName := WantName;
        { Fast path: feed Altium's exact bytes straight back. }
        Result := SchLib.GetState_SchComponentByLibRef(WantName);
        If Result <> Nil Then Exit;

        { Fallback: iterate live symbols, byte-exact LibReference compare. }
        Iter := SchLib.SchLibIterator_Create;
        If Iter <> Nil Then
        Begin
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                LibComp := Iter.FirstSchObject;
                While LibComp <> Nil Do
                Begin
                    If LibComp.LibReference = WantName Then
                    Begin
                        Result := LibComp;
                        Break;
                    End;
                    LibComp := Iter.NextSchObject;
                End;
            Finally
                SchLib.SchIterator_Destroy(Iter);
            End;
        End;
        If Result = Nil Then
        Begin
            ErrCode := 'COMPONENT_NOT_FOUND';
            ErrMsg := 'Component at index ' + IntToStr(Idx) + ' ("' + WantName
                + '") could not be loaded from ' + LibPath;
        End;
        Exit;
    End;

    { By-name path (default). }
    WantName := ExtractJsonValue(Params, 'component_name');
    If WantName = '' Then
    Begin
        ErrCode := 'MISSING_PARAMS';
        ErrMsg := 'Provide component_name, or component_index for names with '
            + 'unreproducible bytes (quotes / control chars).';
        Exit;
    End;
    ResolvedName := WantName;
    Result := SchLib.GetState_SchComponentByLibRef(WantName);
    If Result = Nil Then
    Begin
        ErrCode := 'COMPONENT_NOT_FOUND';
        ErrMsg := 'Component not found in library: ' + WantName;
    End;
End;

Function Lib_GetComponentDetails(Params : String; RequestId : String) : String;
Var
    ComponentName, LibPath, FocusedPath : String;
    ResErrCode, ResErrMsg : String;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    PinIterator, ParamIterator : ISch_Iterator;
    Pin : ISch_Pin;
    Param : ISch_Parameter;
    CompNum, I, PinCount : Integer;
    Data, PinList, ParamList, StyleList, ElecStr : String;
    DesignatorJson, CommentJson, Description, AliasName : String;
    PartCount : Integer;
    PinLabelHidden : Boolean;
    First, FirstStyle, FoundInfo : Boolean;
Begin
    ComponentName := ExtractJsonValue(Params, 'component_name');
    LibPath := ExtractJsonValue(Params, 'library_path');

    If (ComponentName = '') And (ExtractJsonValue(Params, 'component_index') = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'Provide component_name or component_index');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    { Resolve the focused doc's path so we know whether to reopen. }
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then
        Try FocusedPath := Doc.DM_FullPath; Except End;

    If LibPath = '' Then
        LibPath := FocusedPath;

    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;

    { Bring the requested library into focus when it isn't already. }
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    { Resolve the live symbol by name OR index. Index reaches components   }
    { whose LibReference carries unreproducible bytes. On success this sets }
    { ComponentName to Altium's own copy of the name, which the metadata    }
    { reader loop below then matches byte-for-byte.                          }
    Component := ResolveLibComponent(SchLib, LibPath, Params, ComponentName,
        ResErrCode, ResErrMsg);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ResErrCode, ResErrMsg);
        Exit;
    End;

    { Cheap metadata lookup via CompInfoReader: the live component carries }
    { LibReference / ComponentDescription too, but PartCount is on the    }
    { reader's IComponentInfo and not on ISch_Component, so we fetch it   }
    { here. }
    Description := '';
    AliasName := '';
    PartCount := 1;
    FoundInfo := False;
    LibReader := SchServer.CreateLibCompInfoReader(LibPath);
    If LibReader <> Nil Then
    Begin
        Try
            LibReader.ReadAllComponentInfo;
            CompNum := LibReader.NumComponentInfos;
            For I := 0 To CompNum - 1 Do
            Begin
                CompInfo := LibReader.ComponentInfos[I];
                If CompInfo.CompName = ComponentName Then
                Begin
                    Try Description := CompInfo.Description; Except End;
                    Try AliasName := CompInfo.AliasName; Except End;
                    Try PartCount := CompInfo.PartCount; Except End;
                    FoundInfo := True;
                    Break;
                End;
            End;
        Finally
            SchServer.DestroyCompInfoReader(LibReader);
        End;
    End;

    { Component already resolved above (by name or index). }
    If Description = '' Then
        Try Description := Component.ComponentDescription; Except End;

    { Designator + Comment full-style records. The sub-objects ARE        }
    { ISch_Label-derived so they expose Text + FontId + Color + IsHidden  }
    { + Location + Orientation + Justification.                            }
    DesignatorJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
    Try DesignatorJson := BuildLabelStyleJson(Component.Designator, True); Except End;
    CommentJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
    Try CommentJson := BuildLabelStyleJson(Component.Comment, True); Except End;

    { Pin list. font_id / color come from each pin's own ISch_Pin object  }
    { (it inherits from ISch_GraphicalObject which carries both); pin     }
    { name and pin number share that font/color, separate-font handling   }
    { is not exposed cleanly from DelphiScript. label_hidden is the visual}
    { hide-pin-label flag, distinct from pin.IsHidden which hides the pin }
    { from the canvas entirely.                                            }
    PinList := '';
    First := True;
    PinCount := 0;
    PinIterator := Component.SchIterator_Create;
    PinIterator.AddFilter_ObjectSet(MkSet(ePin));
    Try
        Pin := PinIterator.FirstSchObject;
        While Pin <> Nil Do
        Begin
            If Not First Then PinList := PinList + ',';
            First := False;

            If Pin.Electrical = eElectricInput Then ElecStr := 'input'
            Else If Pin.Electrical = eElectricOutput Then ElecStr := 'output'
            Else If Pin.Electrical = eElectricIO Then ElecStr := 'bidirectional'
            Else If Pin.Electrical = eElectricPassive Then ElecStr := 'passive'
            Else If Pin.Electrical = eElectricPower Then ElecStr := 'power'
            Else If Pin.Electrical = eElectricOpenCollector Then ElecStr := 'open_collector'
            Else If Pin.Electrical = eElectricOpenEmitter Then ElecStr := 'open_emitter'
            Else If Pin.Electrical = eElectricHiZ Then ElecStr := 'hiz'
            Else ElecStr := 'passive';

            { Pin label visibility: ISch_Pin.ShowName / ShowDesignator are }
            { the real flags; combine into a single label_hidden when both }
            { are off so the LLM can flag "neither pin name nor number is }
            { drawn". font_id / color are NOT exposed on ISch_Pin in the   }
            { Schematic API at all (only on the ISch_Label family), so we }
            { intentionally omit them from pins[] rather than fake zeros. }
            PinLabelHidden := False;
            Try PinLabelHidden := (Not Pin.ShowName) And (Not Pin.ShowDesignator); Except End;

            PinList := PinList + '{"designator":"' + EscapeJsonString(Pin.Designator) +
                '","name":"' + EscapeJsonString(Pin.Name) +
                '","electrical_type":"' + ElecStr +
                '","x":' + IntToStr(CoordToMils(Pin.Location.X)) +
                ',"y":' + IntToStr(CoordToMils(Pin.Location.Y)) +
                ',"orientation":' + IntToStr(Pin.Orientation) +
                ',"length":' + IntToStr(CoordToMils(Pin.PinLength)) +
                ',"hidden":' + BoolToJsonStr(Pin.IsHidden) +
                ',"label_hidden":' + BoolToJsonStr(PinLabelHidden) + '}';
            Inc(PinCount);

            Pin := PinIterator.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(PinIterator);
    End;

    { Parameter dict (cheap lookups) plus parameter_styles array (visual). }
    { We iterate parameters once and build both shapes in lockstep so the  }
    { kth entry of parameter_styles always matches the kth iteration order.}
    ParamList := '';
    StyleList := '';
    First := True;
    FirstStyle := True;
    ParamIterator := Component.SchIterator_Create;
    ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
    Try
        Param := ParamIterator.FirstSchObject;
        While Param <> Nil Do
        Begin
            If Not First Then ParamList := ParamList + ',';
            First := False;
            ParamList := ParamList + '"' + EscapeJsonString(Param.Name) +
                '":"' + EscapeJsonString(Param.Text) + '"';

            If Not FirstStyle Then StyleList := StyleList + ',';
            FirstStyle := False;
            StyleList := StyleList + '{"name":"' + EscapeJsonString(Param.Name) +
                '","value":"' + EscapeJsonString(Param.Text) + '","style":' +
                BuildLabelStyleJson(Param, False) + '}';

            Param := ParamIterator.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ParamIterator);
    End;

    Data := '{"name":"' + EscapeJsonString(ComponentName) + '"';
    Data := Data + ',"library_path":"' + EscapeJsonString(LibPath) + '"';
    Data := Data + ',"designator":' + DesignatorJson;
    Data := Data + ',"comment":' + CommentJson;
    Data := Data + ',"description":"' + EscapeJsonString(Description) + '"';
    Data := Data + ',"alias_name":"' + EscapeJsonString(AliasName) + '"';
    Data := Data + ',"part_count":' + IntToStr(PartCount);
    Data := Data + ',"pin_count":' + IntToStr(PinCount);
    Data := Data + ',"pins":[' + PinList + ']';
    Data := Data + ',"parameters":{' + ParamList + '}';
    Data := Data + ',"parameter_styles":[' + StyleList + ']';
    Data := Data + ',"models":' + BuildImplementationsJson(Component) + '}';

    Result := BuildSuccessResponse(RequestId, Data);
End;

Function Lib_BatchSetParams(Params : String; RequestId : String) : String;
Var
    LibPath, BatchPath : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ParamIterator : ISch_Iterator;
    Param : ISch_Parameter;
    NewParam : ISch_Parameter;
    FoundParam : ISch_Parameter;
    Workspace : IWorkspace;
    WDoc : IDocument;
    F : TextFile;
    Line, CompName, ParamName, ParamValue : String;
    PipePos1, PipePos2 : Integer;
    Updated, Created, Failed, LineNum : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    BatchPath := ExtractJsonValue(Params, 'batch_file');

    If BatchPath = '' Then
        BatchPath := WorkspaceDir + 'batch_params.txt';

    // Get library path from focused document if not provided
    If LibPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            WDoc := Workspace.DM_FocusedDocument;
            If WDoc <> Nil Then
                LibPath := WDoc.DM_FileName;
        End;
    End;

    // Open the library to make it the current SchServer document
    If LibPath <> '' Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    If Not FileExists(BatchPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BATCH_FILE', 'Batch file not found: ' + BatchPath);
        Exit;
    End;

    Updated := 0;
    Created := 0;
    Failed := 0;
    LineNum := 0;

    // Begin modification block for undo support
    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        AssignFile(F, BatchPath);
        Reset(F);
        Try
            While Not EOF(F) Do
            Begin
                ReadLn(F, Line);
                Inc(LineNum);

                If Line = '' Then Continue;

                // Parse: CompName|ParamName|ParamValue
                PipePos1 := Pos('|', Line);
                If PipePos1 = 0 Then
                Begin
                    Inc(Failed);
                    Continue;
                End;
                CompName := Copy(Line, 1, PipePos1 - 1);
                Line := Copy(Line, PipePos1 + 1, Length(Line));
                PipePos2 := Pos('|', Line);
                If PipePos2 = 0 Then
                Begin
                    Inc(Failed);
                    Continue;
                End;
                ParamName := Copy(Line, 1, PipePos2 - 1);
                ParamValue := Copy(Line, PipePos2 + 1, Length(Line));

                Component := SchLib.GetState_SchComponentByLibRef(CompName);
                If Component = Nil Then
                Begin
                    Inc(Failed);
                    Continue;
                End;

                // Special case: Description is a component property, not a parameter
                If ParamName = 'Description' Then
                Begin
                    Component.ComponentDescription := ParamValue;
                    Inc(Updated);
                    Continue;
                End;

                // Special case: Designator is the component's DEFAULT
                // designator (Component.Designator.Text, a property on the
                // designator label sub-object) -- NOT a parameter. Mirrors
                // the Description case. Lets library designator audits
                // normalize defaults (e.g. "IC?" / "U3" -> "U?") through the
                // existing batch tool without a dedicated handler.
                If ParamName = 'Designator' Then
                Begin
                    If Component.Designator <> Nil Then
                    Begin
                        SchBeginModify(Component.Designator);
                        Component.Designator.Text := ParamValue;
                        SchEndModify(Component.Designator);
                        Inc(Updated);
                    End
                    Else
                        Inc(Failed);
                    Continue;
                End;

                // Find existing parameter
                FoundParam := Nil;
                ParamIterator := Component.SchIterator_Create;
                ParamIterator.AddFilter_ObjectSet(MkSet(eParameter));
                Param := ParamIterator.FirstSchObject;
                While Param <> Nil Do
                Begin
                    If Param.Name = ParamName Then
                    Begin
                        FoundParam := Param;
                        Break;
                    End;
                    Param := ParamIterator.NextSchObject;
                End;
                Component.SchIterator_Destroy(ParamIterator);

                If FoundParam <> Nil Then
                Begin
                    SchBeginModify(FoundParam);
                    FoundParam.Text := ParamValue;
                    SchEndModify(FoundParam);
                    Inc(Updated);
                End
                Else
                Begin
                    NewParam := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                    If NewParam <> Nil Then
                    Begin
                        NewParam.Name := ParamName;
                        NewParam.Text := ParamValue;
                        SetOwnerPart(NewParam, Component);
                        Component.AddSchObject(NewParam);
                        SchRegisterObject(Component, NewParam);
                        Inc(Created);
                    End
                    Else
                        Inc(Failed);
                End;
            End;
        Finally
            CloseFile(F);
        End;
    Finally
        // End modification block - commit changes
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);
    Result := BuildSuccessResponse(RequestId,
        '{"updated":' + IntToStr(Updated) +
        ',"created":' + IntToStr(Created) +
        ',"failed":' + IntToStr(Failed) +
        ',"total_lines":' + IntToStr(LineNum) + '}');
End;

{..............................................................................}
{ Batch Rename Components                                                      }
{..............................................................................}

Function Lib_BatchRename(Params : String; RequestId : String) : String;
Var
    LibPath, BatchPath : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Workspace : IWorkspace;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    F : TextFile;
    Line, OldName, NewName : String;
    PipePos : Integer;
    Renamed, Failed, LineNum : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    BatchPath := ExtractJsonValue(Params, 'batch_file');
    If BatchPath = '' Then
        BatchPath := WorkspaceDir + 'batch_rename.txt';

    // Get library path from parameter or focused document
    If LibPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Doc := Workspace.DM_FocusedDocument;
            If Doc <> Nil Then
                LibPath := Doc.DM_FileName;
        End;
    End;

    // Focus the library document to make it the current SchServer document
    If LibPath <> '' Then
    Begin
        ServerDoc := Client.GetDocumentByPath(LibPath);
        If ServerDoc <> Nil Then
            Client.ShowDocument(ServerDoc)
        Else
        Begin
            // Not yet open, open it
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', LibPath);
            RunProcess('WorkspaceManager:OpenObject');
        End;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active. Provide library_path parameter.');
        Exit;
    End;

    If Not FileExists(BatchPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BATCH_FILE', 'Batch file not found: ' + BatchPath);
        Exit;
    End;

    Renamed := 0;
    Failed := 0;
    LineNum := 0;

    // Begin modification block
    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        AssignFile(F, BatchPath);
        Reset(F);
        Try
            While Not EOF(F) Do
            Begin
                ReadLn(F, Line);
                Inc(LineNum);

                If Line = '' Then Continue;

                // Parse: OldName|NewName
                PipePos := Pos('|', Line);
                If PipePos = 0 Then
                Begin
                    Inc(Failed);
                    Continue;
                End;
                OldName := Copy(Line, 1, PipePos - 1);
                NewName := Copy(Line, PipePos + 1, Length(Line));

                Component := SchLib.GetState_SchComponentByLibRef(OldName);
                If Component = Nil Then
                Begin
                    Inc(Failed);
                    Continue;
                End;

                // Must remove and re-add to update the library's internal index
                SchLib.RemoveSchComponent(Component);
                Component.LibReference := NewName;
                SchLib.AddSchComponent(Component);
                Inc(Renamed);
            End;
        Finally
            CloseFile(F);
        End;
    Finally
        // End modification block - commit changes
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    SchLib.GraphicallyInvalidate;
    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"renamed":' + IntToStr(Renamed) +
        ',"failed":' + IntToStr(Failed) +
        ',"total_lines":' + IntToStr(LineNum) + '}');
End;

{..............................................................................}
{ Diff two SchLib files, reports components only in A, only in B, or both   }
{..............................................................................}

Function Lib_DiffLibraries(Params : String; RequestId : String) : String;
Var
    PathA, PathB : String;
    ReaderA, ReaderB : ILibCompInfoReader;
    NumA, NumB, I, J : Integer;
    NameA : String;
    FoundInB : Boolean;
    OnlyA, OnlyB, Common : String;
    CountA, CountB, CountCommon : Integer;
    First : Boolean;
Begin
    PathA := ExtractJsonValue(Params, 'library_a');
    PathB := ExtractJsonValue(Params, 'library_b');

    If (PathA = '') Or (PathB = '') Then
    Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'library_a and library_b are required'); Exit; End;

    ReaderA := SchServer.CreateLibCompInfoReader(PathA);
    If ReaderA = Nil Then Begin Result := BuildErrorResponse(RequestId, 'READER_FAILED', 'Cannot read library A'); Exit; End;
    ReaderA.ReadAllComponentInfo;
    NumA := ReaderA.NumComponentInfos;

    ReaderB := SchServer.CreateLibCompInfoReader(PathB);
    If ReaderB = Nil Then
    Begin
        SchServer.DestroyCompInfoReader(ReaderA);
        Result := BuildErrorResponse(RequestId, 'READER_FAILED', 'Cannot read library B');
        Exit;
    End;
    ReaderB.ReadAllComponentInfo;
    NumB := ReaderB.NumComponentInfos;

    OnlyA := '';  CountA := 0;
    OnlyB := '';  CountB := 0;
    Common := ''; CountCommon := 0;

    // Find components in A: check if each exists in B
    For I := 0 To NumA - 1 Do
    Begin
        NameA := ReaderA.ComponentInfos[I].CompName;
        FoundInB := False;
        For J := 0 To NumB - 1 Do
        Begin
            If ReaderB.ComponentInfos[J].CompName = NameA Then Begin FoundInB := True; Break; End;
        End;
        If FoundInB Then
        Begin
            If CountCommon > 0 Then Common := Common + ',';
            Common := Common + '"' + EscapeJsonString(NameA) + '"';
            Inc(CountCommon);
        End
        Else
        Begin
            If CountA > 0 Then OnlyA := OnlyA + ',';
            OnlyA := OnlyA + '"' + EscapeJsonString(NameA) + '"';
            Inc(CountA);
        End;
    End;

    // Find components only in B
    For I := 0 To NumB - 1 Do
    Begin
        NameA := ReaderB.ComponentInfos[I].CompName;
        FoundInB := False;
        For J := 0 To NumA - 1 Do
        Begin
            If ReaderA.ComponentInfos[J].CompName = NameA Then Begin FoundInB := True; Break; End;
        End;
        If Not FoundInB Then
        Begin
            If CountB > 0 Then OnlyB := OnlyB + ',';
            OnlyB := OnlyB + '"' + EscapeJsonString(NameA) + '"';
            Inc(CountB);
        End;
    End;

    SchServer.DestroyCompInfoReader(ReaderA);
    SchServer.DestroyCompInfoReader(ReaderB);

    Result := BuildSuccessResponse(RequestId,
        '{"only_in_a":[' + OnlyA + '],"only_in_b":[' + OnlyB + '],"common":[' + Common + ']' +
        ',"count_a":' + IntToStr(NumA) + ',"count_b":' + IntToStr(NumB) +
        ',"only_a":' + IntToStr(CountA) + ',"only_b":' + IntToStr(CountB) +
        ',"shared":' + IntToStr(CountCommon) + '}');
End;

{..............................................................................}
{ Add an arc to the current library symbol                                    }
{ Params: x_center, y_center, radius, start_angle, end_angle, width          }
{..............................................................................}

Function Lib_AddSymbolArc(Params : String; RequestId : String) : String;
Var
    XCenter, YCenter, Radius, StartAngle, EndAngle, Width : Integer;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Arc : ISch_Arc;
Begin
    XCenter := StrToIntDef(ExtractJsonValue(Params, 'x_center'), 0);
    YCenter := StrToIntDef(ExtractJsonValue(Params, 'y_center'), 0);
    Radius := StrToIntDef(ExtractJsonValue(Params, 'radius'), 100);
    StartAngle := StrToIntDef(ExtractJsonValue(Params, 'start_angle'), 0);
    EndAngle := StrToIntDef(ExtractJsonValue(Params, 'end_angle'), 360);
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 1);
    If Width < 0 Then Width := 0;
    If Width > 3 Then Width := 3;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Arc := SchServer.SchObjectFactory(eArc, eCreate_Default);
    If Arc <> Nil Then
    Begin
        Arc.Location := Point(MilsToCoord(XCenter), MilsToCoord(YCenter));
        Arc.Radius := MilsToCoord(Radius);
        Arc.StartAngle := StartAngle;
        Arc.EndAngle := EndAngle;
        Arc.LineWidth := Width;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        SetOwnerPart(Arc, Component);
        Component.AddSchObject(Arc);
        SchRegisterObject(Component, Arc);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create arc');
End;

{..............................................................................}
{ Add a polygon (filled shape) to the current library symbol                  }
{ Params: vertices (comma-separated x,y pairs: "x1,y1,x2,y2,x3,y3,...")     }
{..............................................................................}

Function Lib_AddSymbolPolygon(Params : String; RequestId : String) : String;
Var
    VerticesStr, Token : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Polygon : ISch_Polygon;
    Remaining : String;
    CommaPos, X, Y, I : Integer;
    { Parallel TStringLists of stringified coords. Fixed-size local arrays }
    { of any type corrupt the return slot, see                              }
    { [[delphiscript_fixed_string_array_bug]] - originally documented for  }
    { Array of String, now confirmed for Array of Integer/Double too.      }
    XValues, YValues : TStringList;
Begin
    VerticesStr := ExtractJsonValue(Params, 'vertices');

    If VerticesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'vertices parameter is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    XValues := TStringList.Create;
    YValues := TStringList.Create;
    Try
        Remaining := VerticesStr;
        While Remaining <> '' Do
        Begin
            CommaPos := Pos(',', Remaining);
            If CommaPos = 0 Then Break;
            Token := Copy(Remaining, 1, CommaPos - 1);
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
            X := StrToIntDef(Token, 0);

            CommaPos := Pos(',', Remaining);
            If CommaPos > 0 Then
            Begin
                Token := Copy(Remaining, 1, CommaPos - 1);
                Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
            End
            Else
            Begin
                Token := Remaining;
                Remaining := '';
            End;
            Y := StrToIntDef(Token, 0);

            XValues.Add(IntToStr(X));
            YValues.Add(IntToStr(Y));
        End;

        If XValues.Count < 3 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS', 'At least 3 vertices are required');
            Exit;
        End;

        Polygon := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
        If Polygon <> Nil Then
        Begin
            Polygon.VerticesCount := XValues.Count;
            Polygon.IsSolid := True;
            Polygon.LineWidth := eSmall;

            For I := 1 To XValues.Count Do
                Polygon.Vertex[I] := Point(
                    MilsToCoord(StrToIntDef(XValues[I-1], 0)),
                    MilsToCoord(StrToIntDef(YValues[I-1], 0)));

            SchServer.ProcessControl.PreProcess(SchLib, '');
            SetOwnerPart(Polygon, Component);
            Component.AddSchObject(Polygon);
            SchRegisterObject(Component, Polygon);
            SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

            MarkLibDirty(SchLib);
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"vertices":' + IntToStr(XValues.Count) + '}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create polygon');
    Finally
        YValues.Free;
        XValues.Free;
    End;
End;

{..............................................................................}
{ Set the description field on a library component                            }
{ Params: component_name, description                                         }
{..............................................................................}

Function Lib_SetComponentDescription(Params : String; RequestId : String) : String;
Var
    CompName, Description : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
Begin
    CompName := ExtractJsonValue(Params, 'component_name');
    Description := ExtractJsonValue(Params, 'description');

    If CompName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name parameter is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := SchLib.GetState_SchComponentByLibRef(CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found: ' + CompName);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    SchBeginModify(Component);
    Component.ComponentDescription := Description;
    SchEndModify(Component);
    SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

    MarkLibDirty(SchLib);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"component":"' + EscapeJsonString(CompName) +
        '","description":"' + EscapeJsonString(Description) + '"}');
End;

{..............................................................................}
{ Get all pins of the current library component                               }
{ Returns designator, name, electrical type, x, y for each pin               }
{..............................................................................}

Function Lib_GetPinList(Params : String; RequestId : String) : String;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    PinIterator : ISch_Iterator;
    Pin : ISch_Pin;
    JsonItems, ElecStr, WantName : String;
    First : Boolean;
    PinCount : Integer;
Begin
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    { Resolve THROUGH the library, never straight off the editor's current
      component. Measured on a live library: SchIterator_Create on a component
      taken from CurrentSchComponent faults with "Undeclared identifier:
      SchIterator_Create", while the identical call works on a component
      fetched by lib-ref or returned from a SchLib iterator, which is how
      every other reader in this file gets one. DelphiScript narrows an
      interface at iterator-return; a component handed over any other way
      does not carry the methods, and the failure is a late-bound one that
      Try/Except cannot catch.

      component_name also lets a caller name the symbol rather than rely on
      whatever the editor has selected, so reading a symbol's pins stops
      depending on, and stops disturbing, the current selection. }
    WantName := ExtractJsonValue(Params, 'component_name');
    If WantName = '' Then
    Begin
        Component := GetTargetLibComponent(SchLib);
        If Component <> Nil Then
            Try
                WantName := Component.LibReference;
            Except
                WantName := '';
            End;
    End;

    If WantName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT',
            'No component is selected; pass component_name to name one');
        Exit;
    End;

    Component := SchLib.GetState_SchComponentByLibRef(WantName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Component not found in library: ' + WantName);
        Exit;
    End;

    JsonItems := '';
    First := True;
    PinCount := 0;

    PinIterator := Component.SchIterator_Create;
    PinIterator.AddFilter_ObjectSet(MkSet(ePin));

    Try
        Pin := PinIterator.FirstSchObject;
        While Pin <> Nil Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;

            // Map electrical type to string. Altium uses eElectricIO for
            // bidirectional; eElectricBiDir is undeclared.
            If Pin.Electrical = eElectricInput Then ElecStr := 'input'
            Else If Pin.Electrical = eElectricOutput Then ElecStr := 'output'
            Else If Pin.Electrical = eElectricIO Then ElecStr := 'bidirectional'
            Else If Pin.Electrical = eElectricPassive Then ElecStr := 'passive'
            Else If Pin.Electrical = eElectricPower Then ElecStr := 'power'
            Else If Pin.Electrical = eElectricOpenCollector Then ElecStr := 'open_collector'
            Else If Pin.Electrical = eElectricOpenEmitter Then ElecStr := 'open_emitter'
            Else If Pin.Electrical = eElectricHiZ Then ElecStr := 'hiz'
            Else ElecStr := 'passive';

            JsonItems := JsonItems + '{"designator":"' + EscapeJsonString(Pin.Designator) +
                '","name":"' + EscapeJsonString(Pin.Name) +
                '","electrical_type":"' + ElecStr +
                '","x":' + IntToStr(CoordToMils(Pin.Location.X)) +
                ',"y":' + IntToStr(CoordToMils(Pin.Location.Y)) +
                ',"orientation":' + IntToStr(Pin.Orientation) +
                ',"length":' + IntToStr(CoordToMils(Pin.PinLength)) +
                ',"hidden":' + BoolToJsonStr(Pin.IsHidden) +
                ',"owner_part_id":' + IntToStr(Pin.OwnerPartId) + '}';
            Inc(PinCount);

            Pin := PinIterator.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(PinIterator);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"count":' + IntToStr(PinCount) +
        ',"component":"' + EscapeJsonString(Component.LibReference) +
        '","pins":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Duplicate a component within or BETWEEN schematic libraries.                }
{                                                                              }
{ Params:                                                                      }
{   source_name (required) - lib_ref to copy                                  }
{   new_name              - lib_ref for the clone; defaults to source_name   }
{   source_library        - .SchLib to read from; defaults to focused doc    }
{   dest_library          - .SchLib to write to; defaults to source_library  }
{                           (omit / equal -> same-library duplicate, the     }
{                           original behaviour)                                }
{   overwrite=true|false  - if a component named new_name already exists in  }
{                           the destination, replace it; default false ->    }
{                           returns NAME_EXISTS                                }
{                                                                              }
{ Replicates the source while it is focused (so the clone inherits the       }
{ source's library context), then switches focus to the destination and     }
{ AddSchComponent there. Destination ends focused with the new component     }
{ selected. Save is deferred (MarkLibDirty only) per the project's perf-      }
{ deferred-save pattern.                                                       }
{..............................................................................}

Function Lib_CopyComponent(Params : String; RequestId : String) : String;
Var
    SourceLibPath, DestLibPath, FocusedPath, SourceName, NewName : String;
    OverwriteStr, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    SourceLib, DestLib : ISch_Lib;
    SourceComp, NewComp, Existing : ISch_Component;
    Overwrite, SameLib, Overwrote : Boolean;
Begin
    SourceLibPath := ExtractJsonValue(Params, 'source_library');
    DestLibPath := ExtractJsonValue(Params, 'dest_library');
    SourceName := ExtractJsonValue(Params, 'source_name');
    NewName := ExtractJsonValue(Params, 'new_name');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');

    If SourceName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'source_name is required');
        Exit;
    End;
    If NewName = '' Then NewName := SourceName;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If SourceLibPath = '' Then SourceLibPath := FocusedPath;
    If SourceLibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and source_library was not supplied');
        Exit;
    End;

    { Focus the source library so Replicate sees it in the right context. }
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(SourceLibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', SourceLibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SourceLib := SchServer.GetCurrentSchDocument;
    If (SourceLib = Nil) Or (SourceLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus source library at ' + SourceLibPath);
        Exit;
    End;
    SourceComp := SourceLib.GetState_SchComponentByLibRef(SourceName);
    If SourceComp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Source component not found in ' + SourceLibPath + ': ' + SourceName);
        Exit;
    End;

    SameLib := (DestLibPath = '') Or (UpperCase(DestLibPath) = UpperCase(SourceLibPath));
    If SameLib Then DestLibPath := SourceLibPath;

    { Replicate while source is focused. The clone is free-floating until    }
    { AddSchComponent attaches it to the destination library.                 }
    NewComp := SourceComp.Replicate;
    If NewComp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COPY_FAILED',
            'Replicate returned Nil for ' + SourceName);
        Exit;
    End;
    NewComp.LibReference := NewName;

    If SameLib Then
        DestLib := SourceLib
    Else
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', DestLibPath);
        RunProcess('WorkspaceManager:OpenObject');
        DestLib := SchServer.GetCurrentSchDocument;
        If (DestLib = Nil) Or (DestLib.ObjectId <> eSchLib) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
                'Failed to focus destination library at ' + DestLibPath);
            Exit;
        End;
    End;

    Overwrote := False;
    Existing := DestLib.GetState_SchComponentByLibRef(NewName);
    If Existing <> Nil Then
    Begin
        If Not Overwrite Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NAME_EXISTS',
                'A component named "' + NewName + '" already exists in '
                + DestLibPath + ' (pass overwrite=true to replace)');
            Exit;
        End;
        SchServer.ProcessControl.PreProcess(DestLib, '');
        DestLib.RemoveSchComponent(Existing);
        SchServer.ProcessControl.PostProcess(DestLib, 'Edit');
        Overwrote := True;
    End;

    SchServer.ProcessControl.PreProcess(DestLib, '');
    DestLib.AddSchComponent(NewComp);
    SchServer.ProcessControl.PostProcess(DestLib, 'Edit');
    DestLib.CurrentSchComponent := NewComp;
    MarkLibDirty(DestLib);

    { Stash the response in a local before assigning to Result -- the         }
    { DelphiScript last-String-arg clobber bug only bites here when the       }
    { JSON build calls a String helper, but the pattern is cheap insurance.   }
    RespJson :=
        '{"success":true' +
        ',"source_library":"' + EscapeJsonString(SourceLibPath) + '"' +
        ',"dest_library":"' + EscapeJsonString(DestLibPath) + '"' +
        ',"source":"' + EscapeJsonString(SourceName) + '"' +
        ',"new_name":"' + EscapeJsonString(NewName) + '"' +
        ',"same_library":' + BoolToJsonStr(SameLib) +
        ',"overwrote":' + BoolToJsonStr(Overwrote) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{..............................................................................}
{ Lib_AddPins - Bulk add pins to the currently-selected library component.     }
{ One PreProcess/PostProcess + one save for the whole batch, so adding 50      }
{ pins to a new IC symbol costs ~1x the overhead of adding one pin.           }
{ Params: pins = '~~'-separated list; each pin has key=value fields joined by  }
{         ';'. Fields: designator, name, x, y, length (mils), rotation        }
{         (0/90/180/270), electrical_type (input/output/bidirectional/        }
{         passive/power/open_collector/open_emitter/hiz), hidden (true/false),}
{         symbol_outer_edge / symbol_inner_edge (IEEE decoration name or      }
{         ordinal; 'dot' = inversion bubble, 'clock' = clock wedge),          }
{         show_name / show_designator (true/false; whether the pin's name     }
{         and number are drawn. Omit to leave at Altium's default).           }
{..............................................................................}

Function Lib_AddPins(Params : String; RequestId : String) : String;
Var
    PinsStr, Op, Remaining : String;
    OpCount, Added, Failed : Integer;
    Designator, Name, ElecType, HiddenStr, OwnerStr : String;
    OuterStr, InnerStr, ShowNameStr, ShowDesigStr : String;
    X, Y, Length, Rotation, OwnerPartId : Integer;
    Hidden, OwnerExplicit : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Pin : ISch_Pin;
    Loc : TLocation;
Begin
    PinsStr := ExtractJsonValue(Params, 'pins');
    If PinsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'pins is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := PinsStr;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            Designator := GetBatchField(Op, 'designator');
            Name := GetBatchField(Op, 'name');
            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            Length := StrToIntDef(GetBatchField(Op, 'length'), 200);
            Rotation := StrToIntDef(GetBatchField(Op, 'rotation'), 0);
            ElecType := GetBatchField(Op, 'electrical_type');
            HiddenStr := GetBatchField(Op, 'hidden');
            Hidden := (HiddenStr = 'true') Or (HiddenStr = '1');
            { Multi-part support: owner_part_id selects which sub-part      }
            { owns the pin. 0 = shared across ALL parts (e.g. the power     }
            { pins on a quad op-amp). Omit / empty = "current part" via   }
            { SetOwnerPart (single-part behaviour, original default).      }
            OwnerStr := GetBatchField(Op, 'owner_part_id');
            OwnerExplicit := OwnerStr <> '';
            OwnerPartId := StrToIntDef(OwnerStr, 0);
            { IEEE edge decorations: 'dot' on the outer edge is the inversion }
            { bubble of an active-low pin, 'clock' on the inner edge is the   }
            { wedge of a clock pin. Any TIeeeSymbol name or ordinal is        }
            { accepted; see StrToIeeeSymbol.                                  }
            OuterStr := GetBatchField(Op, 'symbol_outer_edge');
            InnerStr := GetBatchField(Op, 'symbol_inner_edge');
            { Whether the pin's name and number are DRAWN. Distinct from  }
            { 'hidden', which hides the whole pin: a resistor shows both  }
            { its pins and neither of their labels. Absent = leave alone. }
            ShowNameStr := GetBatchField(Op, 'show_name');
            ShowDesigStr := GetBatchField(Op, 'show_designator');

            Pin := SchServer.SchObjectFactory(ePin, eCreate_Default);
            If Pin = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Pin.Designator := Designator;
            Pin.Name := Name;
            { Location is a by-value record, read, mutate, write back.         }
            Loc := Pin.Location;
            Loc.X := MilsToCoord(X);
            Loc.Y := MilsToCoord(Y);
            Pin.Location := Loc;
            Pin.PinLength := MilsToCoord(Length);
            Pin.Orientation := Rotation Div 90;
            Pin.IsHidden := Hidden;

            Pin.Electrical := StrToPinElectrical(ElecType);

            { Only written when the caller asked for a decoration. A fresh    }
            { pin already carries eNoSymbol on both edges, so skipping the     }
            { assignment keeps this bulk path (every symbol we author runs     }
            { through it) byte-identical to its previous behaviour whenever    }
            { the new fields are absent.                                       }
            If OuterStr <> '' Then
                Pin.Symbol_OuterEdge := StrToIeeeSymbol(OuterStr);
            If InnerStr <> '' Then
                Pin.Symbol_InnerEdge := StrToIeeeSymbol(InnerStr);

            If ShowNameStr <> '' Then
                Pin.ShowName := (ShowNameStr = 'true') Or (ShowNameStr = '1');
            If ShowDesigStr <> '' Then
                Pin.ShowDesignator :=
                    (ShowDesigStr = 'true') Or (ShowDesigStr = '1');

            If OwnerExplicit Then
            Begin
                { Explicit owner_part_id from caller (multi-part symbol).  }
                Try Pin.OwnerPartId := OwnerPartId; Except End;
                Try Pin.OwnerPartDisplayMode := 0; Except End;
            End
            Else
                SetOwnerPart(Pin, Component);

            Component.AddSchObject(Pin);
            SchRegisterObject(Component, Pin);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Lib_AddSymbolText - Bulk add body text to the current library symbol.       }
{ Same batch shape as Lib_AddPins: one PreProcess/PostProcess for the lot.    }
{ Params: texts = '~~'-separated list; fields joined by ';'. Fields: text,    }
{         x, y (mils), rotation (0/90/180/270), font_size, font_name, bold,   }
{         italic (true/false), owner_part_id.                                 }
{                                                                             }
{ The primitive is an ISch_Label, which is what Altium uses for free text on  }
{ a symbol. Its property set is the one BuildLabelStyleJson already reads     }
{ (Text / FontId / Location / Orientation / Justification), so nothing new is }
{ being assumed about the interface.                                          }
{                                                                             }
{ font_size is Altium's own font size, the number SchServer.FontManager       }
{ takes, NOT mils. No conversion is attempted here because the relationship   }
{ between the two is not documented anywhere this project can check, and a    }
{ guessed constant would silently resize every imported note.                 }
{..............................................................................}

Function Lib_AddSymbolText(Params : String; RequestId : String) : String;
Var
    TextsStr, Op, Remaining : String;
    OpCount, Added, Failed : Integer;
    Content, OwnerStr, FontName, BoldStr, ItalicStr : String;
    X, Y, Rotation, FontSize, OwnerPartId : Integer;
    OwnerExplicit, Bold, Italic : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Lbl : ISch_Label;
    Loc : TLocation;
    FontMgr : ISch_FontManager;
Begin
    TextsStr := ExtractJsonValue(Params, 'texts');
    If TextsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'texts is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    FontMgr := SchServer.FontManager;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := TextsStr;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            Content := GetBatchField(Op, 'text');
            If Content = '' Then
            Begin
                { An empty string would place an invisible, unselectable }
                { primitive that only shows up as a stray object later.  }
                Inc(Failed);
                Continue;
            End;

            X := StrToIntDef(GetBatchField(Op, 'x'), 0);
            Y := StrToIntDef(GetBatchField(Op, 'y'), 0);
            Rotation := StrToIntDef(GetBatchField(Op, 'rotation'), 0);
            FontSize := StrToIntDef(GetBatchField(Op, 'font_size'), 10);
            FontName := GetBatchField(Op, 'font_name');
            If FontName = '' Then FontName := 'Arial';
            BoldStr := GetBatchField(Op, 'bold');
            ItalicStr := GetBatchField(Op, 'italic');
            Bold := (BoldStr = 'true') Or (BoldStr = '1');
            Italic := (ItalicStr = 'true') Or (ItalicStr = '1');
            OwnerStr := GetBatchField(Op, 'owner_part_id');
            OwnerExplicit := OwnerStr <> '';
            OwnerPartId := StrToIntDef(OwnerStr, 0);

            Lbl := SchServer.SchObjectFactory(eLabel, eCreate_Default);
            If Lbl = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Lbl.Text := Content;
            { Location is a by-value record: read, mutate, write back. }
            Loc := Lbl.Location;
            Loc.X := MilsToCoord(X);
            Loc.Y := MilsToCoord(Y);
            Lbl.Location := Loc;

            { Orientation is enum-typed. Assign the quarter-turn ordinal as }
            { a plain Integer, exactly as Lib_AddPins sets Pin.Orientation,  }
            { rather than naming a type this codebase cannot verify.        }
            Try
                Lbl.Orientation := (((Rotation Mod 360) + 360) Mod 360) Div 90;
            Except
            End;

            Try
                Lbl.FontId := FontMgr.GetFontID(FontSize, 0, False, Italic,
                                                Bold, False, FontName);
            Except
            End;

            If OwnerExplicit Then
            Begin
                Try Lbl.OwnerPartId := OwnerPartId; Except End;
                Try Lbl.OwnerPartDisplayMode := 0; Except End;
            End
            Else
                SetOwnerPart(Lbl, Component);

            Component.AddSchObject(Lbl);
            SchRegisterObject(Component, Lbl);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{ Batch line authoring: same shape as Lib_AddPins. Receives a `lines` array }
{ encoded with the ~~ / ; / = separators NextBatchOp expects, applies them  }
{ all inside one PreProcess / PostProcess pair, and triggers a single       }
{ MarkLibDirty (which now also handles graphical invalidate).               }
Function Lib_AddSymbolLines(Params : String; RequestId : String) : String;
Var
    LinesStr, Op, Remaining : String;
    OpCount, Added, Failed : Integer;
    X1, Y1, X2, Y2, Width : Integer;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Line : ISch_Line;
    Loc : TLocation;
Begin
    LinesStr := ExtractJsonValue(Params, 'lines');
    If LinesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'lines is required');
        Exit;
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No component is selected');
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := LinesStr;

    SchServer.ProcessControl.PreProcess(SchLib, '');
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
            Width := StrToIntDef(GetBatchField(Op, 'width'), 1);
            If Width < 0 Then Width := 0;
            If Width > 3 Then Width := 3;

            Line := SchServer.SchObjectFactory(eLine, eCreate_Default);
            If Line = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            { Read-modify-write the TLocation record -- see Lib_AddSymbolLine. }
            Loc := Line.Location;
            Loc.X := MilsToCoord(X1);
            Loc.Y := MilsToCoord(Y1);
            Line.Location := Loc;
            Loc := Line.Corner;
            Loc.X := MilsToCoord(X2);
            Loc.Y := MilsToCoord(Y2);
            Line.Corner := Loc;
            Line.LineWidth := Width;

            SetOwnerPart(Line, Component);
            Component.AddSchObject(Line);
            SchRegisterObject(Component, Line);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Lib_AuditStyles - bulk visual-style audit across every component in a       }
{ library. Walks SchLib.SchIterator with eSchComponent filter (live           }
{ components, no per-name GetState_SchComponentByLibRef lookup), and emits    }
{ the designator's full style record per component. Comment / parameter_     }
{ styles / pins are opt-in via flags so the default response stays compact.  }
{                                                                              }
{ Filter mode: when expect_designator_font_id and/or expect_designator_color }
{ are supplied, only components whose designator does NOT match the expected }
{ value go in the output. Without filters, every component is returned.       }
{                                                                              }
{ Params:                                                                     }
{   library_path                  - .SchLib path. Defaults to focused doc.   }
{   with_comment=true             - include comment style record per comp.  }
{   with_parameters=true          - include parameter_styles array per comp.}
{   with_pins=true                - include pins array per comp.             }
{   expect_designator_font_id=N   - filter: trim matches.                    }
{   expect_designator_color=N     - filter: trim matches.                    }
{   limit=5000                    - cap on emitted entries.                  }
{                                                                              }
{ Returns object with library_path, count, mismatch_count, limit, truncated, }
{ filter_applied, components:[...].                                          }
Function Lib_AuditStyles(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FlagStr : String;
    ExpFontIdStr, ExpColorStr : String;
    HasExpFontId, HasExpColor, FilterApplied : Boolean;
    WithComment, WithParameters, WithPins : Boolean;
    ExpFontId, ExpColor : Integer;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    PinIter, ParamIter : ISch_Iterator;
    Component : ISch_Component;
    Pin : ISch_Pin;
    Param : ISch_Parameter;
    DesigLabel : ISch_Label;
    Limit, Count, MismatchCount, PinCount, NumComps, I : Integer;
    DesigFontId, DesigColor : Integer;
    DesigJson, CommentJson, PinList, StyleList, ElecStr, ResultsJson, Entry, CompName : String;
    PinLabelHidden : Boolean;
    First, FirstPin, FirstStyle, Mismatched : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');

    FlagStr := ExtractJsonValue(Params, 'with_comment');
    WithComment := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');
    FlagStr := ExtractJsonValue(Params, 'with_parameters');
    WithParameters := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');
    FlagStr := ExtractJsonValue(Params, 'with_pins');
    WithPins := (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1');

    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 5000);

    ExpFontIdStr := ExtractJsonValue(Params, 'expect_designator_font_id');
    ExpColorStr := ExtractJsonValue(Params, 'expect_designator_color');
    HasExpFontId := ExpFontIdStr <> '';
    HasExpColor := ExpColorStr <> '';
    ExpFontId := StrToIntDef(ExpFontIdStr, 0);
    ExpColor := StrToIntDef(ExpColorStr, 0);
    FilterApplied := HasExpFontId Or HasExpColor;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then
        Try FocusedPath := Doc.DM_FullPath; Except End;

    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    Count := 0;
    MismatchCount := 0;
    ResultsJson := '';
    First := True;

    { Enumerate via ILibCompInfoReader. The schematic SchIterator with        }
    { eSchComponent only walks components placed on a regular SchDoc, NOT    }
    { the symbol entries inside a SchLib. The CompInfoReader gives names    }
    { in document order; for each name we load the live ISch_Component via }
    { GetState_SchComponentByLibRef to read its designator/comment/parameter}
    { style records. This is the same pattern Lib_GetComponents uses.        }
    LibReader := SchServer.CreateLibCompInfoReader(LibPath);
    If LibReader = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'READER_FAILED',
            'Failed to create library reader for ' + LibPath);
        Exit;
    End;

    Try
        LibReader.ReadAllComponentInfo;
        NumComps := LibReader.NumComponentInfos;

        For I := 0 To NumComps - 1 Do
        Begin
            If Count >= Limit Then Break;

            CompInfo := LibReader.ComponentInfos[I];
            CompName := '';
            Try CompName := CompInfo.CompName; Except End;
            If CompName = '' Then Continue;

            Component := SchLib.GetState_SchComponentByLibRef(CompName);
            If Component = Nil Then Continue;

            { Read designator font_id / color via the typed ISch_Label local. }
            { Component.Designator returns ISch_Designator which IS an        }
            { ISch_Label, so the assignment + late-bound property reads       }
            { resolve cleanly at compile time.                                  }
            DesigLabel := Nil;
            DesigFontId := 0;
            DesigColor := 0;
            Try DesigLabel := Component.Designator; Except End;
            If DesigLabel <> Nil Then
            Begin
                Try DesigFontId := DesigLabel.FontId; Except End;
                Try DesigColor := DesigLabel.Color; Except End;
            End;

            Mismatched := False;
            If HasExpFontId And (DesigFontId <> ExpFontId) Then Mismatched := True;
            If HasExpColor And (DesigColor <> ExpColor) Then Mismatched := True;

            { Skip when filter is on and the component matches the expected }
            { style. Without filters, every component is emitted.            }
            If (Not FilterApplied) Or Mismatched Then
            Begin

                DesigJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
                If DesigLabel <> Nil Then
                    Try DesigJson := BuildLabelStyleJson(DesigLabel, True); Except End;

                Entry := '{"name":"' + EscapeJsonString(CompName) +
                    '","designator":' + DesigJson +
                    ',"mismatched":' + BoolToJsonStr(Mismatched);

                If WithComment Then
                Begin
                    CommentJson := '{"text":"","font_id":0,"color":0,"is_hidden":false,"x":0,"y":0,"orientation":0,"justification":0}';
                    Try CommentJson := BuildLabelStyleJson(Component.Comment, True); Except End;
                    Entry := Entry + ',"comment":' + CommentJson;
                End;

                If WithPins Then
                Begin
                    PinList := '';
                    FirstPin := True;
                    PinCount := 0;
                    PinIter := Component.SchIterator_Create;
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Try
                        Pin := PinIter.FirstSchObject;
                        While Pin <> Nil Do
                        Begin
                            If Not FirstPin Then PinList := PinList + ',';
                            FirstPin := False;

                            If Pin.Electrical = eElectricInput Then ElecStr := 'input'
                            Else If Pin.Electrical = eElectricOutput Then ElecStr := 'output'
                            Else If Pin.Electrical = eElectricIO Then ElecStr := 'bidirectional'
                            Else If Pin.Electrical = eElectricPassive Then ElecStr := 'passive'
                            Else If Pin.Electrical = eElectricPower Then ElecStr := 'power'
                            Else If Pin.Electrical = eElectricOpenCollector Then ElecStr := 'open_collector'
                            Else If Pin.Electrical = eElectricOpenEmitter Then ElecStr := 'open_emitter'
                            Else If Pin.Electrical = eElectricHiZ Then ElecStr := 'hiz'
                            Else ElecStr := 'passive';

                            PinLabelHidden := False;
                            Try PinLabelHidden := (Not Pin.ShowName) And (Not Pin.ShowDesignator); Except End;

                            PinList := PinList + '{"designator":"' + EscapeJsonString(Pin.Designator) +
                                '","name":"' + EscapeJsonString(Pin.Name) +
                                '","electrical_type":"' + ElecStr +
                                '","x":' + IntToStr(CoordToMils(Pin.Location.X)) +
                                ',"y":' + IntToStr(CoordToMils(Pin.Location.Y)) +
                                ',"orientation":' + IntToStr(Pin.Orientation) +
                                ',"hidden":' + BoolToJsonStr(Pin.IsHidden) +
                                ',"label_hidden":' + BoolToJsonStr(PinLabelHidden) + '}';
                            Inc(PinCount);

                            Pin := PinIter.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(PinIter);
                    End;
                    Entry := Entry + ',"pin_count":' + IntToStr(PinCount) +
                        ',"pins":[' + PinList + ']';
                End;

                If WithParameters Then
                Begin
                    StyleList := '';
                    FirstStyle := True;
                    ParamIter := Component.SchIterator_Create;
                    ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                    Try
                        Param := ParamIter.FirstSchObject;
                        While Param <> Nil Do
                        Begin
                            If Not FirstStyle Then StyleList := StyleList + ',';
                            FirstStyle := False;
                            StyleList := StyleList + '{"name":"' + EscapeJsonString(Param.Name) +
                                '","value":"' + EscapeJsonString(Param.Text) +
                                '","style":' + BuildLabelStyleJson(Param, False) + '}';
                            Param := ParamIter.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(ParamIter);
                    End;
                    Entry := Entry + ',"parameter_styles":[' + StyleList + ']';
                End;

                Entry := Entry + '}';

                If Not First Then ResultsJson := ResultsJson + ',';
                First := False;
                ResultsJson := ResultsJson + Entry;

                If Mismatched Then Inc(MismatchCount);
                Inc(Count);
            End;
        End;
    Finally
        SchServer.DestroyCompInfoReader(LibReader);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"count":' + IntToStr(Count) +
        ',"mismatch_count":' + IntToStr(MismatchCount) +
        ',"limit":' + IntToStr(Limit) +
        ',"truncated":' + BoolToJsonStr(Count >= Limit) +
        ',"filter_applied":' + BoolToJsonStr(FilterApplied) +
        ',"components":[' + ResultsJson + ']}');
End;

{..............................................................................}
{ Lib_SetLabelFormat - bulk OR single-component label-style writer.            }
{                                                                              }
{ Sets any subset of (font_id, color, is_hidden, orientation, justification) }
{ on a target ISch_Label (designator, comment, or one named parameter) for    }
{ either one component (component_name supplied) or every component in the   }
{ library (component_name omitted). Symmetric counterpart to                  }
{ lib_audit_styles' filtering: when only_mismatched is true (default), the   }
{ handler skips components whose target label already matches every          }
{ specified field, so re-runs after partial application stay idempotent.     }
{                                                                              }
{ The whole edit batch is wrapped in ProcessControl.PreProcess /              }
{ PostProcess('Edit') so Altium's undo stack records it as one step. Each    }
{ label modification is bracketed by SchBeginModify / SchEndModify on the    }
{ ISch_Label so the SchServer broadcasts a refresh for that primitive.      }
{ MarkLibDirty fires once at the end; saves are deferred per the project-    }
{ side perf_deferred_save pattern.                                            }
{                                                                              }
{ Params (any combination of style fields, omitted ones are left untouched): }
{   library_path                  - .SchLib path. Defaults to focused doc.   }
{   component_name                - optional, single-component mode.         }
{   target=designator|comment|parameter:<name>  (default 'designator')      }
{   font_id, color, is_hidden, orientation, justification - new style values }
{   only_mismatched=true|false    (default true) - skip already-compliant   }
{   limit=5000                    - cap on processed components in bulk     }
{                                                                              }
{ Returns object: library_path, target, scope, total, modified,              }
{ already_compliant, missing_target, failed, limit, truncated.              }
Procedure ResolveTargetLabel(Component : ISch_Component; Target : String;
    Var Lbl : ISch_Label; Var Found : Boolean);
Var
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
    ParamName : String;
Begin
    Lbl := Nil;
    Found := False;

    If Target = 'designator' Then
    Begin
        Try Lbl := Component.Designator; Found := (Lbl <> Nil); Except End;
    End
    Else If Target = 'comment' Then
    Begin
        Try Lbl := Component.Comment; Found := (Lbl <> Nil); Except End;
    End
    Else If Pos('parameter:', Target) = 1 Then
    Begin
        ParamName := Copy(Target, 11, Length(Target) - 10);
        If ParamName = '' Then Exit;
        Iter := Component.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Try
            Param := Iter.FirstSchObject;
            While Param <> Nil Do
            Begin
                If Param.Name = ParamName Then
                Begin
                    Lbl := Param;
                    Found := True;
                    Break;
                End;
                Param := Iter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(Iter);
        End;
    End;
End;

Function ApplyLabelFormat(Lbl : ISch_Label;
    HasFontId : Boolean; NewFontId : Integer;
    HasColor : Boolean; NewColor : Integer;
    HasIsHidden : Boolean; NewIsHidden : Boolean;
    HasOrientation : Boolean; NewOrientation : Integer;
    HasJustification : Boolean; NewJustification : Integer;
    OnlyMismatched : Boolean) : Integer;
{ Returns 1 if modified, 0 if compliant (skipped), -1 if the write itself     }
{ raised (counted as failed by the caller).                                   }
Var
    Compliant : Boolean;
Begin
    Result := 0;
    If Lbl = Nil Then Exit;

    If OnlyMismatched Then
    Begin
        Compliant := True;
        If HasFontId Then
            Try If Lbl.FontId <> NewFontId Then Compliant := False; Except End;
        If Compliant And HasColor Then
            Try If Lbl.Color <> NewColor Then Compliant := False; Except End;
        If Compliant And HasIsHidden Then
            Try If Lbl.IsHidden <> NewIsHidden Then Compliant := False; Except End;
        If Compliant And HasOrientation Then
            Try If Lbl.Orientation <> NewOrientation Then Compliant := False; Except End;
        If Compliant And HasJustification Then
            Try If Lbl.Justification <> NewJustification Then Compliant := False; Except End;
        If Compliant Then Exit;
    End;

    Try
        SchBeginModify(Lbl);
        If HasFontId Then Lbl.FontId := NewFontId;
        If HasColor Then Lbl.Color := NewColor;
        If HasIsHidden Then Lbl.IsHidden := NewIsHidden;
        If HasOrientation Then Lbl.Orientation := NewOrientation;
        If HasJustification Then Lbl.Justification := NewJustification;
        SchEndModify(Lbl);
        Result := 1;
    Except
        Result := -1;
    End;
End;

Function Lib_SetLabelFormat(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, Target, CompName, FlagStr : String;
    HasFontId, HasColor, HasIsHidden, HasOrientation, HasJustification : Boolean;
    NewFontId, NewColor, NewOrientation, NewJustification : Integer;
    NewIsHidden, OnlyMismatched, Found : Boolean;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Component : ISch_Component;
    Lbl : ISch_Label;
    Limit, Total, Modified, AlreadyCompliant, MissingTarget, Failed, NumComps, I, ApplyResult : Integer;
    Scope : String;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    Target := ExtractJsonValue(Params, 'target');
    If Target = '' Then Target := 'designator';
    CompName := ExtractJsonValue(Params, 'component_name');

    HasFontId := ExtractJsonValue(Params, 'font_id') <> '';
    NewFontId := StrToIntDef(ExtractJsonValue(Params, 'font_id'), 0);
    HasColor := ExtractJsonValue(Params, 'color') <> '';
    NewColor := StrToIntDef(ExtractJsonValue(Params, 'color'), 0);
    HasIsHidden := ExtractJsonValue(Params, 'is_hidden') <> '';
    NewIsHidden := False;
    FlagStr := ExtractJsonValue(Params, 'is_hidden');
    If (FlagStr = 'true') Or (FlagStr = 'True') Or (FlagStr = '1') Then NewIsHidden := True;
    HasOrientation := ExtractJsonValue(Params, 'orientation') <> '';
    NewOrientation := StrToIntDef(ExtractJsonValue(Params, 'orientation'), 0);
    HasJustification := ExtractJsonValue(Params, 'justification') <> '';
    NewJustification := StrToIntDef(ExtractJsonValue(Params, 'justification'), 0);

    FlagStr := ExtractJsonValue(Params, 'only_mismatched');
    OnlyMismatched := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');

    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 5000);

    If (Not HasFontId) And (Not HasColor) And (Not HasIsHidden)
        And (Not HasOrientation) And (Not HasJustification) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOTHING_TO_SET',
            'At least one of font_id / color / is_hidden / orientation / justification must be supplied');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    Total := 0;
    Modified := 0;
    AlreadyCompliant := 0;
    MissingTarget := 0;
    Failed := 0;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        If CompName <> '' Then
        Begin
            { Single-component mode. }
            Scope := 'single';
            Component := SchLib.GetState_SchComponentByLibRef(CompName);
            If Component = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
                    'Component not found in library: ' + CompName);
                Exit;
            End;
            Total := 1;
            ResolveTargetLabel(Component, Target, Lbl, Found);
            If Not Found Then
                Inc(MissingTarget)
            Else
            Begin
                ApplyResult := ApplyLabelFormat(Lbl, HasFontId, NewFontId,
                    HasColor, NewColor, HasIsHidden, NewIsHidden,
                    HasOrientation, NewOrientation, HasJustification, NewJustification,
                    OnlyMismatched);
                If ApplyResult = 1 Then Inc(Modified)
                Else If ApplyResult = 0 Then Inc(AlreadyCompliant)
                Else Inc(Failed);
            End;
        End
        Else
        Begin
            { Bulk mode: walk library via CompInfoReader, same enumeration as }
            { Lib_GetComponents and Lib_AuditStyles.                            }
            Scope := 'bulk';
            LibReader := SchServer.CreateLibCompInfoReader(LibPath);
            If LibReader = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'READER_FAILED',
                    'Failed to create library reader for ' + LibPath);
                Exit;
            End;
            Try
                LibReader.ReadAllComponentInfo;
                NumComps := LibReader.NumComponentInfos;

                For I := 0 To NumComps - 1 Do
                Begin
                    If Total >= Limit Then Break;
                    CompInfo := LibReader.ComponentInfos[I];
                    CompName := '';
                    Try CompName := CompInfo.CompName; Except End;
                    If CompName = '' Then Continue;
                    Component := SchLib.GetState_SchComponentByLibRef(CompName);
                    If Component = Nil Then Continue;
                    Inc(Total);

                    ResolveTargetLabel(Component, Target, Lbl, Found);
                    If Not Found Then
                    Begin
                        Inc(MissingTarget);
                        Continue;
                    End;

                    ApplyResult := ApplyLabelFormat(Lbl, HasFontId, NewFontId,
                        HasColor, NewColor, HasIsHidden, NewIsHidden,
                        HasOrientation, NewOrientation, HasJustification, NewJustification,
                        OnlyMismatched);
                    If ApplyResult = 1 Then Inc(Modified)
                    Else If ApplyResult = 0 Then Inc(AlreadyCompliant)
                    Else Inc(Failed);
                End;
            Finally
                SchServer.DestroyCompInfoReader(LibReader);
            End;
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    If Modified > 0 Then MarkLibDirty(SchLib);

    Try SchLib.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"target":"' + EscapeJsonString(Target) + '"' +
        ',"scope":"' + EscapeJsonString(Scope) + '"' +
        ',"total":' + IntToStr(Total) +
        ',"modified":' + IntToStr(Modified) +
        ',"already_compliant":' + IntToStr(AlreadyCompliant) +
        ',"missing_target":' + IntToStr(MissingTarget) +
        ',"failed":' + IntToStr(Failed) +
        ',"limit":' + IntToStr(Limit) +
        ',"truncated":' + BoolToJsonStr(Total >= Limit) + '}');
End;

{ Lib_SetLabelFormats - apply N (target, style) ops in one library walk.      }
{                                                                              }
{ Same semantics as Lib_SetLabelFormat but processes a list of ops in a       }
{ single IPC round-trip, with one library focus and one walk. Five sequential }
{ set_label_format calls (one per parameter target) collapse into one trip,   }
{ which is the dominant cost on large libraries (each call carries the IPC,  }
{ workspace lookup, doc-focus check and CompInfoReader walk).                  }
{                                                                              }
{ Wire format for `ops` matches the project-wide NextBatchOp grammar: ops    }
{ are separated by ~~ and per-op fields by `;`, key=value. Each op may set    }
{ target, font_id, color, is_hidden, orientation, justification. The global  }
{ only_mismatched flag applies to every op.                                   }
{                                                                              }
{ Returns object: library_path, scope ("single"|"bulk"), total, limit,        }
{ truncated, ops array each with target, modified, already_compliant,         }
{ missing_target, failed.                                                     }

Function ApplyOpToComponent(Component : ISch_Component; Target : String;
    FontIdStr, ColorStr, IsHiddenStr, OrientationStr, JustificationStr : String;
    OnlyMismatched : Boolean) : Integer;
{ Returns 0=already_compliant, 1=modified, 2=missing_target, 3=failed.        }
Var
    HasFontId, HasColor, HasIsHidden, HasOrientation, HasJustification : Boolean;
    NewFontId, NewColor, NewOrientation, NewJustification : Integer;
    NewIsHidden, Found : Boolean;
    Lbl : ISch_Label;
    AR : Integer;
Begin
    HasFontId := FontIdStr <> '';
    NewFontId := StrToIntDef(FontIdStr, 0);
    HasColor := ColorStr <> '';
    NewColor := StrToIntDef(ColorStr, 0);
    HasIsHidden := IsHiddenStr <> '';
    NewIsHidden := (IsHiddenStr = 'true') Or (IsHiddenStr = 'True') Or (IsHiddenStr = '1');
    HasOrientation := OrientationStr <> '';
    NewOrientation := StrToIntDef(OrientationStr, 0);
    HasJustification := JustificationStr <> '';
    NewJustification := StrToIntDef(JustificationStr, 0);

    ResolveTargetLabel(Component, Target, Lbl, Found);
    If Not Found Then
    Begin
        Result := 2;
        Exit;
    End;
    AR := ApplyLabelFormat(Lbl, HasFontId, NewFontId,
        HasColor, NewColor, HasIsHidden, NewIsHidden,
        HasOrientation, NewOrientation, HasJustification, NewJustification,
        OnlyMismatched);
    If AR = 1 Then Result := 1
    Else If AR = 0 Then Result := 0
    Else Result := 3;
End;

Function Lib_SetLabelFormats(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, CompName, FlagStr : String;
    OpsStr, OpStr, Remaining, CurCompName, Target : String;
    OnlyMismatched : Boolean;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    LibReader : ILibCompInfoReader;
    CompInfo : IComponentInfo;
    Component : ISch_Component;
    Limit, Total, NumComps, I, J, NumOps, OpResult : Integer;
    Scope, OpsJson, RespJson : String;
    AnyModified : Boolean;
    { Parallel TStringLists -- DelphiScript Function locals with fixed-size  }
    { arrays silently corrupt Result, so we use TStringList per the project's }
    { existing pattern. Counter fields hold IntToStr(N).                       }
    OpTargets, OpFontIds, OpColors, OpIsHidden,
        OpOrientation, OpJustification : TStringList;
    OpModified, OpAlready, OpMissing, OpFailed : TStringList;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    OpsStr := ExtractJsonValue(Params, 'ops');
    If OpsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'ops is required (non-empty ~~-separated list)');
        Exit;
    End;
    FlagStr := ExtractJsonValue(Params, 'only_mismatched');
    OnlyMismatched := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 5000);

    { Workspace + library focus checks BEFORE allocating TStringLists so an  }
    { early-exit error path does not leak.                                    }
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library document is active and no library_path was supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus library at ' + LibPath);
        Exit;
    End;
    { Never answer from a different library than the one asked for. }
    If Not SchLibIsAtPath(SchLib, LibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY',
            'Focus did not land on the requested library: asked for ' + LibPath
            + ' but the active document is ' + SchLib.DocumentName
            + '. Check the path exists, and note the parameter is library_path.');
        Exit;
    End;

    OpTargets := TStringList.Create;
    OpFontIds := TStringList.Create;
    OpColors := TStringList.Create;
    OpIsHidden := TStringList.Create;
    OpOrientation := TStringList.Create;
    OpJustification := TStringList.Create;
    OpModified := TStringList.Create;
    OpAlready := TStringList.Create;
    OpMissing := TStringList.Create;
    OpFailed := TStringList.Create;
    Try
        NumOps := 0;
        Remaining := OpsStr;
        While True Do
        Begin
            OpStr := NextBatchOp(Remaining);
            If OpStr = '' Then Break;
            Target := GetBatchField(OpStr, 'target');
            If Target = '' Then Target := 'designator';
            OpTargets.Add(Target);
            OpFontIds.Add(GetBatchField(OpStr, 'font_id'));
            OpColors.Add(GetBatchField(OpStr, 'color'));
            OpIsHidden.Add(GetBatchField(OpStr, 'is_hidden'));
            OpOrientation.Add(GetBatchField(OpStr, 'orientation'));
            OpJustification.Add(GetBatchField(OpStr, 'justification'));
            OpModified.Add('0');
            OpAlready.Add('0');
            OpMissing.Add('0');
            OpFailed.Add('0');
            Inc(NumOps);
        End;

        If NumOps = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOTHING_TO_SET',
                'ops parsed to zero entries');
            Exit;
        End;

        Total := 0;
        SchServer.ProcessControl.PreProcess(SchLib, '');
        Try
            If CompName <> '' Then
            Begin
                Scope := 'single';
                Component := SchLib.GetState_SchComponentByLibRef(CompName);
                If Component = Nil Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
                        'Component not found in library: ' + CompName);
                    Exit;
                End;
                Total := 1;
                For J := 0 To NumOps - 1 Do
                Begin
                    OpResult := ApplyOpToComponent(Component, OpTargets[J],
                        OpFontIds[J], OpColors[J], OpIsHidden[J],
                        OpOrientation[J], OpJustification[J], OnlyMismatched);
                    If OpResult = 0 Then
                        OpAlready[J] := IntToStr(StrToIntDef(OpAlready[J], 0) + 1)
                    Else If OpResult = 1 Then
                        OpModified[J] := IntToStr(StrToIntDef(OpModified[J], 0) + 1)
                    Else If OpResult = 2 Then
                        OpMissing[J] := IntToStr(StrToIntDef(OpMissing[J], 0) + 1)
                    Else
                        OpFailed[J] := IntToStr(StrToIntDef(OpFailed[J], 0) + 1);
                End;
            End
            Else
            Begin
                Scope := 'bulk';
                LibReader := SchServer.CreateLibCompInfoReader(LibPath);
                If LibReader = Nil Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'READER_FAILED',
                        'Failed to create library reader for ' + LibPath);
                    Exit;
                End;
                Try
                    LibReader.ReadAllComponentInfo;
                    NumComps := LibReader.NumComponentInfos;
                    For I := 0 To NumComps - 1 Do
                    Begin
                        If Total >= Limit Then Break;
                        CompInfo := LibReader.ComponentInfos[I];
                        CurCompName := '';
                        Try CurCompName := CompInfo.CompName; Except End;
                        If CurCompName = '' Then Continue;
                        Component := SchLib.GetState_SchComponentByLibRef(CurCompName);
                        If Component = Nil Then Continue;
                        Inc(Total);

                        For J := 0 To NumOps - 1 Do
                        Begin
                            OpResult := ApplyOpToComponent(Component, OpTargets[J],
                                OpFontIds[J], OpColors[J], OpIsHidden[J],
                                OpOrientation[J], OpJustification[J], OnlyMismatched);
                            If OpResult = 0 Then
                                OpAlready[J] := IntToStr(StrToIntDef(OpAlready[J], 0) + 1)
                            Else If OpResult = 1 Then
                                OpModified[J] := IntToStr(StrToIntDef(OpModified[J], 0) + 1)
                            Else If OpResult = 2 Then
                                OpMissing[J] := IntToStr(StrToIntDef(OpMissing[J], 0) + 1)
                            Else
                                OpFailed[J] := IntToStr(StrToIntDef(OpFailed[J], 0) + 1);
                        End;
                    End;
                Finally
                    SchServer.DestroyCompInfoReader(LibReader);
                End;
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
        End;

        AnyModified := False;
        For J := 0 To NumOps - 1 Do
            If StrToIntDef(OpModified[J], 0) > 0 Then AnyModified := True;
        If AnyModified Then MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;

        OpsJson := '[';
        For J := 0 To NumOps - 1 Do
        Begin
            If J > 0 Then OpsJson := OpsJson + ',';
            OpsJson := OpsJson +
                '{"target":"' + EscapeJsonString(OpTargets[J]) + '"' +
                ',"modified":' + OpModified[J] +
                ',"already_compliant":' + OpAlready[J] +
                ',"missing_target":' + OpMissing[J] +
                ',"failed":' + OpFailed[J] + '}';
        End;
        OpsJson := OpsJson + ']';

        { Stash the full JSON in a local before assigning to Result -- the    }
        { DelphiScript last-arg clobber bug only bites on String returns      }
        { with a String arg, but the pattern is cheap insurance.              }
        RespJson :=
            '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
            ',"scope":"' + EscapeJsonString(Scope) + '"' +
            ',"total":' + IntToStr(Total) +
            ',"limit":' + IntToStr(Limit) +
            ',"truncated":' + BoolToJsonStr(Total >= Limit) +
            ',"ops":' + OpsJson + '}';
        Result := BuildSuccessResponse(RequestId, RespJson);
    Finally
        OpTargets.Free;
        OpFontIds.Free;
        OpColors.Free;
        OpIsHidden.Free;
        OpOrientation.Free;
        OpJustification.Free;
        OpModified.Free;
        OpAlready.Free;
        OpMissing.Free;
        OpFailed.Free;
    End;
End;

{ Lib_ExtractIntLib - Extract .SchLib and .PcbLib sources from an .IntLib.   }
{                                                                              }
{ An Altium .IntLib (integrated library) packages compiled symbol and        }
{ footprint libraries together; the editor's "Extract Sources" command       }
{ writes the underlying .SchLib + .PcbLib back to disk so they can be        }
{ inspected or modified. This handler opens the .IntLib, runs the extract   }
{ process, and then probes the conventional output locations (Altium drops  }
{ them into a sibling folder named after the IntLib base name) to report    }
{ which files were produced.                                                  }
{                                                                              }
{ Params:                                                                      }
{   intlib_path (required) - path to the .IntLib file                       }
{                                                                              }
{ Returns: process_run, sch_lib_path/sch_lib_found, pcb_lib_path/found.     }
Function Lib_ExtractIntLib(Params : String; RequestId : String) : String;
Var
    IntLibPath, BaseName, ParentDir, ExtractDir : String;
    SchLibPath, PcbLibPath : String;
    FoundSch, FoundPcb : Boolean;
    Workspace : IWorkspace;
    RespJson : String;
Begin
    IntLibPath := ExtractJsonValue(Params, 'intlib_path');
    If IntLibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'intlib_path is required');
        Exit;
    End;
    If Not FileExists(IntLibPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FILE_NOT_FOUND',
            'IntLib not found: ' + IntLibPath);
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    { Open the IntLib in Altium's integrated-library editor so the      }
    { extract command has a current document to act on.                  }
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', IntLibPath);
    RunProcess('WorkspaceManager:OpenObject');

    { Run Altium's source-extraction. Try/Except won't catch a bad       }
    { process name (DelphiScript can't), so we detect outcome by         }
    { probing for the resulting files instead.                            }
    ResetParameters;
    AddStringParameter('FileName', IntLibPath);
    RunProcess('IntegratedLibrary:ExtractSources');

    { Where Altium writes the extracted files: a sibling folder named    }
    { after the IntLib base name, containing <Base>.SchLib + .PcbLib.    }
    BaseName := ChangeFileExt(ExtractFileName(IntLibPath), '');
    ParentDir := ExtractFilePath(IntLibPath);
    ExtractDir := ParentDir + BaseName + '\';
    SchLibPath := ExtractDir + BaseName + '.SchLib';
    PcbLibPath := ExtractDir + BaseName + '.PcbLib';
    FoundSch := FileExists(SchLibPath);
    FoundPcb := FileExists(PcbLibPath);

    { Fallback locations: same directory as the IntLib (some Altium       }
    { versions drop sources beside the IntLib instead of in a sub-dir).  }
    If Not FoundSch Then
    Begin
        SchLibPath := ParentDir + BaseName + '.SchLib';
        FoundSch := FileExists(SchLibPath);
    End;
    If Not FoundPcb Then
    Begin
        PcbLibPath := ParentDir + BaseName + '.PcbLib';
        FoundPcb := FileExists(PcbLibPath);
    End;

    RespJson :=
        '{"intlib_path":"' + EscapeJsonString(IntLibPath) + '"' +
        ',"extract_dir":"' + EscapeJsonString(ExtractDir) + '"' +
        ',"sch_lib_path":"' + EscapeJsonString(SchLibPath) + '"' +
        ',"sch_lib_found":' + BoolToJsonStr(FoundSch) +
        ',"pcb_lib_path":"' + EscapeJsonString(PcbLibPath) + '"' +
        ',"pcb_lib_found":' + BoolToJsonStr(FoundPcb) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_UpdateFootprintHeightsFrom3D                                            }
{                                                                              }
{ Walk every footprint in the active PCB Library and propagate the maximum   }
{ 3D-body OverallHeight up to the footprint's Height field. Footprint.Height }
{ drives the placement-collision DRC rule that prevents tall components       }
{ from getting placed under shorter ones or under an enclosure overhang --   }
{ but libraries shipped without explicit heights default to 0, which         }
{ effectively disables that check.                                            }
{                                                                              }
{ Updates only when the 3D model is TALLER than the current Footprint.Height }
{ -- this matches the common convention and protects against a               }
{ manually-set "I know this part is 5mm despite the model being 3mm" value.  }
Function Lib_UpdateFootprintHeightsFrom3D(Params : String; RequestId : String) : String;
Var
    CurLib : IPCB_Library;
    LibIter : IPCB_LibraryIterator;
    Footprint, SavedCurrent : IPCB_LibComponent;
    GrIter : IPCB_GroupIterator;
    Body : IPCB_ComponentBody;
    Updated, Inspected : Integer;
    Items, FpName : String;
    First : Boolean;
    OldH, NewH : TCoord;
Begin
    CurLib := PCBServer.GetCurrentPCBLibrary;
    If CurLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB Library document focused');
        Exit;
    End;

    SavedCurrent := CurLib.CurrentComponent;
    Updated := 0;
    Inspected := 0;
    Items := '';
    First := True;

    LibIter := CurLib.LibraryIterator_Create;
    Try
        LibIter.SetState_FilterAll;
        Footprint := LibIter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            Try
                Inc(Inspected);
                NewH := 0;
                GrIter := Footprint.GroupIterator_Create;
                Try
                    GrIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
                    Body := GrIter.FirstPCBObject;
                    While Body <> Nil Do
                    Begin
                        Try
                            If Body.OverallHeight > NewH Then NewH := Body.OverallHeight;
                        Except End;
                        Body := GrIter.NextPCBObject;
                    End;
                Finally
                    Footprint.GroupIterator_Destroy(GrIter);
                End;

                OldH := Footprint.Height;
                If (NewH > 0) And (NewH > OldH) Then
                Begin
                    Footprint.Height := NewH;
                    Inc(Updated);
                    FpName := '';
                    Try FpName := Footprint.Name; Except End;
                    If Not First Then Items := Items + ',';
                    First := False;
                    Items := Items + JsonObj(
                        JsonStr('name', FpName) + ',' +
                        JsonFloat('old_height_mm', CoordToMM(OldH)) + ',' +
                        JsonFloat('new_height_mm', CoordToMM(NewH))
                    );
                End;
            Except End;
            Footprint := LibIter.NextPCBObject;
        End;
    Finally
        CurLib.LibraryIterator_Destroy(LibIter);
    End;

    { Restore the originally-focused footprint and mark the lib dirty. }
    If SavedCurrent <> Nil Then CurLib.CurrentComponent := SavedCurrent;
    If Updated > 0 Then
    Begin
        Try
            CurLib.Board.ViewManager_FullUpdate;
            { No SaveDoc here -- libraries should be reviewed by hand    }
            { before saving since this rewrites height data project-wide.}
        Except End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonInt('inspected', Inspected) + ',' +
            JsonInt('updated', Updated) + ',' +
            JsonRaw('items', '[' + Items + ']')
        ));
End;


{..............................................................................}
{ Lib_SplitPinFunctions - parse each pin's slash-delimited name into its       }
{ function list (the alt-function popup). A pin named "PA0/TX/CTS" becomes     }
{ name "PA0" with functions TX, CTS. Operates on the current library symbol.   }
{..............................................................................}
Function Lib_SplitPinFunctions(Params : String; RequestId : String) : String;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Iter : ISch_Iterator;
    Pin : ISch_Pin;
    Processed : Integer;
Begin
    SchLib := SchServer.GetCurrentSchDocument;
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;
    Component := GetTargetLibComponent(SchLib);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No current library component');
        Exit;
    End;

    Processed := 0;
    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        Iter := Component.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePin));
            Pin := Iter.FirstSchObject;
            While Pin <> Nil Do
            Begin
                Try
                    Pin.SetState_FunctionsFromName;
                    Processed := Processed + 1;
                Except End;
                Pin := Iter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, '');
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"pins_processed":' + IntToStr(Processed) + '}');
End;

{..............................................................................}
{ Lib_SetPinOwnerPart - reassign named pins of a multi-part symbol to a       }
{ sub-part, or to Part Zero (owner_part_id=0) so ONE pin is shared by the     }
{ whole package rather than redrawn at every sub-part origin. Part Zero is    }
{ Altium's documented placement for a multi-part component's supply pins.     }
{ Params: component_name (optional, defaults to the editor's current symbol), }
{         pin_designators (required, comma-separated), owner_part_id (req).   }
{..............................................................................}
Function Lib_SetPinOwnerPart(Params : String; RequestId : String) : String;
Var
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Iter : ISch_Iterator;
    Pin : ISch_Pin;
    WantName, WantPins, OwnerStr, Haystack, Changed : String;
    OwnerId, ChangedCount, PartTotal : Integer;
    First : Boolean;
Begin
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
        Exit;
    End;

    WantPins := ExtractJsonValue(Params, 'pin_designators');
    { "3, 12" would otherwise build ',3, 12,' and match nothing, returning }
    { count 0 as if the pins did not exist.                                }
    WantPins := StringReplace(WantPins, ' ', '', MkSet(rfReplaceAll));
    OwnerStr := ExtractJsonValue(Params, 'owner_part_id');
    If (WantPins = '') Or (OwnerStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'pin_designators and owner_part_id are required');
        Exit;
    End;
    OwnerId := StrToIntDef(OwnerStr, -1);
    If OwnerId < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'owner_part_id must be 0 or greater');
        Exit;
    End;

    { Resolve through the library, never off the editor's current component:  }
    { SchIterator_Create is undeclared on a component fetched that way.       }
    WantName := ExtractJsonValue(Params, 'component_name');
    If WantName = '' Then
    Begin
        Component := GetTargetLibComponent(SchLib);
        If Component <> Nil Then
            Try
                WantName := Component.LibReference;
            Except
                WantName := '';
            End;
    End;
    If WantName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT',
            'No component is selected; pass component_name to name one');
        Exit;
    End;

    Component := SchLib.GetState_SchComponentByLibRef(WantName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Component not found in library: ' + WantName);
        Exit;
    End;

    { An OwnerPartId above the symbol's part count is accepted by the       }
    { assignment but maps to no displayable part, so the pin silently       }
    { disappears from every sub-part view. Refuse rather than corrupt. Part }
    { Zero is always legal. PartCount reads high by one on some symbols,    }
    { which only makes this bound permissive, never wrong.                  }
    PartTotal := 0;
    Try PartTotal := Component.PartCount; Except End;
    If PartTotal < 1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PART_COUNT',
            'Could not read PartCount for ' + WantName + '; refusing to '
            + 'assign an unvalidated owner_part_id');
        Exit;
    End;
    If OwnerId > PartTotal Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
            'owner_part_id ' + IntToStr(OwnerId) + ' exceeds PartCount '
            + IntToStr(PartTotal) + ' for ' + WantName);
        Exit;
    End;

    Haystack := ',' + WantPins + ',';
    ChangedCount := 0;
    Changed := '';
    First := True;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        Iter := Component.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePin));
            Pin := Iter.FirstSchObject;
            While Pin <> Nil Do
            Begin
                If Pos(',' + Pin.Designator + ',', Haystack) > 0 Then
                Begin
                    Try
                        Pin.OwnerPartId := OwnerId;
                        If Not First Then Changed := Changed + ',';
                        First := False;
                        Changed := Changed + '"' + EscapeJsonString(Pin.Designator) + '"';
                        ChangedCount := ChangedCount + 1;
                    Except End;
                End;
                Pin := Iter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(Iter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, '');
    End;

    MarkLibDirty(SchLib);

    Result := BuildSuccessResponse(RequestId,
        '{"component":"' + EscapeJsonString(WantName) +
        '","owner_part_id":' + IntToStr(OwnerId) +
        ',"pins_changed":[' + Changed + ']' +
        ',"count":' + IntToStr(ChangedCount) + '}');
End;

{..............................................................................}
{ Lib_InstallLibrary / Lib_UninstallLibrary - register or unregister a library }
{ (.IntLib / .SchLib / .PcbLib) with the environment's Available Libraries.    }
{..............................................................................}
Function Lib_InstallLibrary(Params : String; RequestId : String) : String;
Var
    Path : String;
    Ok : Boolean;
Begin
    Path := ExtractJsonValue(Params, 'library_path');
    If Path = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library_path is required');
        Exit;
    End;
    If IntegratedLibraryManager = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MANAGER', 'IntegratedLibraryManager unavailable');
        Exit;
    End;
    Ok := False;
    Try
        IntegratedLibraryManager.InstallLibrary(Path);
        Ok := True;
    Except End;
    Result := BuildSuccessResponse(RequestId,
        '{"installed":' + BoolToJsonStr(Ok) + ',"library_path":"'
        + EscapeJsonString(Path) + '"}');
End;

Function Lib_UninstallLibrary(Params : String; RequestId : String) : String;
Var
    Path : String;
    Ok : Boolean;
Begin
    Path := ExtractJsonValue(Params, 'library_path');
    If Path = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library_path is required');
        Exit;
    End;
    If IntegratedLibraryManager = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MANAGER', 'IntegratedLibraryManager unavailable');
        Exit;
    End;
    Ok := False;
    Try
        IntegratedLibraryManager.UnInstallLibrary(Path);
        Ok := True;
    Except End;
    Result := BuildSuccessResponse(RequestId,
        '{"uninstalled":' + BoolToJsonStr(Ok) + ',"library_path":"'
        + EscapeJsonString(Path) + '"}');
End;

{ Lib_DeleteComponent - remove one symbol from a schematic library (.SchLib).  }
{ Mirrors the overwrite path in Lib_CopyComponent: focus the lib, resolve the  }
{ component by LibReference, RemoveSchComponent, then mark the lib dirty for    }
{ deferred save. Deletes a single named part; hard error if the name is not    }
{ found (no silent no-op, no wildcard mass-delete).                            }
{ Params: component_name (required, the LibReference), library_path (optional, }
{         defaults to the focused document).                                   }
Function Lib_DeleteComponent(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, CompName, RespJson : String;
    ResErrCode, ResErrMsg : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    Component : ISch_Component;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    If (CompName = '') And (ExtractJsonValue(Params, 'component_index') = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'Provide component_name or component_index');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus schematic library at ' + LibPath);
        Exit;
    End;
    Component := ResolveLibComponent(SchLib, LibPath, Params, CompName,
        ResErrCode, ResErrMsg);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ResErrCode, ResErrMsg);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    SchLib.RemoveSchComponent(Component);
    SchServer.ProcessControl.PostProcess(SchLib, 'Delete component');
    MarkLibDirty(SchLib);

    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"deleted":"' + EscapeJsonString(CompName) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_RenameComponent - set one component's LibReference to a clean new_name.  }
{ The point of the tool: a symbol whose current name carries bytes a caller    }
{ cannot reproduce (an embedded '"', or a control char from a broken import)   }
{ is addressable by component_index, so this is the way to give such a part a  }
{ clean name and make every name-based tool reach it again.                     }
{ Rename technique matches Lib_BatchRename: RemoveSchComponent, set            }
{ LibReference, AddSchComponent, so the library's internal by-name index is    }
{ rebuilt on the new name.                                                      }
{ Params: new_name (required), plus component_index OR component_name to pick   }
{         the target, library_path (optional, focused default).                }
Function Lib_RenameComponent(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, OldName, NewName, RespJson : String;
    ResErrCode, ResErrMsg : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    SchLib : ISch_Lib;
    Component, Existing : ISch_Component;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    NewName := ExtractJsonValue(Params, 'new_name');
    If NewName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'new_name is required');
        Exit;
    End;
    If (ExtractJsonValue(Params, 'component_name') = '')
        And (ExtractJsonValue(Params, 'component_index') = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'Provide component_name or component_index to pick the target');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    SchLib := SchServer.GetCurrentSchDocument;
    If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus schematic library at ' + LibPath);
        Exit;
    End;

    Component := ResolveLibComponent(SchLib, LibPath, Params, OldName,
        ResErrCode, ResErrMsg);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, ResErrCode, ResErrMsg);
        Exit;
    End;

    { Refuse to collide with an existing part. If new_name already resolves }
    { and it is a different object, the rename would create a duplicate.    }
    Existing := SchLib.GetState_SchComponentByLibRef(NewName);
    If (Existing <> Nil) And (Existing <> Component) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_EXISTS',
            'A different component named "' + NewName + '" already exists in ' + LibPath);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try
        SchLib.RemoveSchComponent(Component);
        Component.LibReference := NewName;
        SchLib.AddSchComponent(Component);
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Rename component');
    End;
    SchLib.GraphicallyInvalidate;
    MarkLibDirty(SchLib);

    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"old_name":"' + EscapeJsonString(OldName) + '"' +
        ',"new_name":"' + EscapeJsonString(NewName) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_DeleteFootprint - remove one footprint from a PCB library (.PcbLib).     }
{ Finds the footprint by Name via a LibraryIterator (break BEFORE deleting to  }
{ avoid iterator invalidation), then RemoveComponent + DeRegisterComponent per }
{ the reference DeleteSelectedItemsInPcbLib pattern, inside PreProcess/         }
{ PostProcess, and saves the .PcbLib. Hard error if the name is not found.     }
{ Params: footprint_name (required), library_path (optional, focused default). }
Function Lib_DeleteFootprint(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If FpName = FpWanted Then
            Begin
                Target := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
            'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    PCBServer.PreProcess;
    Try PcbLib.RemoveComponent(Target); Except End;
    Try PcbLib.DeRegisterComponent(Target); Except End;
    PCBServer.PostProcess;
    SaveDocByPath(PcbLib.Board.FileName);

    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"deleted":"' + EscapeJsonString(FpWanted) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_RemoveModel - remove implementations (models) from a SchLib component by  }
{ ModelName, via Component.RemoveSchImplementation. Re-scans after each removal  }
{ to dodge iterator invalidation. keep_one=true keeps the first match and       }
{ removes the rest (dedup); keep_one=false removes every match.                 }
{ Params: component_name, model_name (required), keep_one (bool),               }
{         library_path (optional).                                              }
Function Lib_RemoveModel(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, ModelName, KeepStr, RespJson, CurName : String;
    KeepOne : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ImplIter : ISch_Iterator;
    Impl, Found : ISch_Implementation;
    Matches, Threshold, Removed, Guard : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    ModelName := ExtractJsonValue(Params, 'model_name');
    KeepStr := ExtractJsonValue(Params, 'keep_one');
    KeepOne := (KeepStr = 'true') Or (KeepStr = 'True') Or (KeepStr = '1');
    If (CompName = '') Or (ModelName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name and model_name are required');
        Exit;
    End;
    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := SchLib.GetState_SchComponentByLibRef(CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;

    Threshold := 0;
    If KeepOne Then Threshold := 1;
    Removed := 0;
    Guard := 1000;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    While Guard > 0 Do
    Begin
        Matches := 0;
        Found := Nil;
        ImplIter := Component.SchIterator_Create;
        Try
            ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
            Impl := ImplIter.FirstSchObject;
            While Impl <> Nil Do
            Begin
                CurName := '';
                Try CurName := Impl.ModelName; Except End;
                If CurName = ModelName Then
                Begin
                    Inc(Matches);
                    If Found = Nil Then Found := Impl;
                End;
                Impl := ImplIter.NextSchObject;
            End;
        Finally
            Component.SchIterator_Destroy(ImplIter);
        End;

        If (Matches <= Threshold) Or (Found = Nil) Then Break;
        Try Component.RemoveSchImplementation(Found); Except End;
        Inc(Removed);
        Dec(Guard);
    End;
    SchServer.ProcessControl.PostProcess(SchLib, 'Remove model');
    MarkLibDirty(SchLib);

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"component":"' + EscapeJsonString(CompName) + '"'
        + ',"model_name":"' + EscapeJsonString(ModelName) + '"'
        + ',"removed":' + IntToStr(Removed)
        + ',"kept_one":' + BoolToJsonStr(KeepOne) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_RenameFootprint - rename a footprint in a PcbLib (footprint.Name := new). }
{ Errors if the source name is missing or the new name already exists.          }
{ Params: footprint_name, new_name (required), library_path (optional).         }
Function Lib_RenameFootprint(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpName, NewName, CurName, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    Clash : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpName := ExtractJsonValue(Params, 'footprint_name');
    NewName := ExtractJsonValue(Params, 'new_name');
    If (FpName = '') Or (NewName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name and new_name are required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Clash := False;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            CurName := '';
            Try CurName := Footprint.Name; Except End;
            If CurName = FpName Then Target := Footprint;
            If CurName = NewName Then Clash := True;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND', 'Footprint not found in ' + LibPath + ': ' + FpName);
        Exit;
    End;
    If Clash Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_EXISTS', 'A footprint named "' + NewName + '" already exists in ' + LibPath);
        Exit;
    End;

    PCBServer.PreProcess;
    Try Target.Name := NewName; Except End;
    PCBServer.PostProcess;
    Try PcbLib.Board.ViewManager_FullUpdate; Except End;
    Try PcbLib.RefreshView; Except End;
    SaveDocByPath(PcbLib.Board.FileName);

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"footprint":"' + EscapeJsonString(FpName) + '"'
        + ',"new_name":"' + EscapeJsonString(NewName) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Look up a rename in a ';'-separated 'old=new' map string; Default if absent.  }
Function LookupRename(MapStr, OldName, Default : String) : String;
Var
    Remaining, Pair, K, V : String;
    SemiPos, EqPos : Integer;
Begin
    Result := Default;
    Remaining := MapStr;
    While Remaining <> '' Do
    Begin
        SemiPos := Pos(';', Remaining);
        If SemiPos > 0 Then
        Begin
            Pair := Copy(Remaining, 1, SemiPos - 1);
            Remaining := Copy(Remaining, SemiPos + 1, Length(Remaining));
        End
        Else
        Begin
            Pair := Remaining;
            Remaining := '';
        End;
        EqPos := Pos('=', Pair);
        If EqPos > 0 Then
        Begin
            K := Copy(Pair, 1, EqPos - 1);
            V := Copy(Pair, EqPos + 1, Length(Pair));
            If K = OldName Then
            Begin
                Result := V;
                Exit;
            End;
        End;
    End;
End;

{ Lib_SetModelName - set a single implementation's ModelName (footprint         }
{ reference) on a SchLib component. Targets the model whose current ModelName =  }
{ model_name; if model_name is empty, the current (IsCurrent) model, else the    }
{ first. A targeted spot-edit; the bulk rebuild is Lib_NormalizeImplementations. }
{ Params: component_name, new_model_name (required), model_name (optional),      }
{         library_path (optional).                                              }
Function Lib_SetModelName(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, NewName, OldName, CurName, RespJson : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ImplIter : ISch_Iterator;
    Impl, Target, FirstImpl : ISch_Implementation;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    NewName := ExtractJsonValue(Params, 'new_model_name');
    OldName := ExtractJsonValue(Params, 'model_name');
    If (CompName = '') Or (NewName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name and new_model_name are required');
        Exit;
    End;
    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := SchLib.GetState_SchComponentByLibRef(CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;

    Target := Nil;
    FirstImpl := Nil;
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            If FirstImpl = Nil Then FirstImpl := Impl;
            CurName := '';
            Try CurName := Impl.ModelName; Except End;
            If OldName <> '' Then
            Begin
                If CurName = OldName Then Begin Target := Impl; Break; End;
            End
            Else
            Begin
                Try If Impl.IsCurrent Then Target := Impl; Except End;
                If Target <> Nil Then Break;
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;
    If (Target = Nil) And (OldName = '') Then Target := FirstImpl;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MODEL_NOT_FOUND',
            'No matching model on ' + CompName + ' in ' + LibPath);
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchLib, '');
    Try Target.ModelName := NewName; Except End;
    SchServer.ProcessControl.PostProcess(SchLib, 'Set model name');
    MarkLibDirty(SchLib);

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"component":"' + EscapeJsonString(CompName) + '"'
        + ',"new_model_name":"' + EscapeJsonString(NewName) + '"}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_NormalizeImplementations - whole-SchLib sweep that rebuilds every          }
{ component's models: read each implementation (type, name, is_current), remove  }
{ all of them, then re-add ONE fresh, name-only implementation per unique        }
{ (type, name), preferring is_current. A fresh implementation carries no stale   }
{ SourceLibraryName and no duplicate, and binds by ModelName the way            }
{ Lib_LinkFootprint does (no AddDataFileLink, which wedges AD26). rename_map     }
{ (';'-separated 'old=new') renames a model_name during the re-add unless        }
{ dedupe_only=true. Component enumeration is decoupled from modification (names  }
{ collected first) so the sweep never mutates a live iterator.                   }
{ Params: library_path (optional), rename_map (optional), dedupe_only (bool).    }
Function Lib_NormalizeImplementations(Params : String; RequestId : String) : String;
Var
    LibPath, RenameMap, DedupeStr, RespJson : String;
    DedupeOnly, IsCur, Cur, RemovedOne : Boolean;
    SchLib : ISch_Lib;
    CompIter, ImplIter, ScanIter : ISch_Iterator;
    Component : ISch_Component;
    Impl, Found : ISch_Implementation;
    Link : ISch_ModelDatafileLink;
    CompNames, AllKeys, AllCurs, SeenKeys : TStringList;
    ModelName, ModelType, Key, NewName, Nm, OldSrc, EntNm, SourceLib, UseLibStr : String;
    C, J, K, Guard, LinkCount : Integer;
    CompsTouched, DupsRemoved, SourcesCleared, LinksRepaired, SourcesSet : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    RenameMap := ExtractJsonValue(Params, 'rename_map');
    DedupeStr := ExtractJsonValue(Params, 'dedupe_only');
    DedupeOnly := (DedupeStr = 'true') Or (DedupeStr = 'True') Or (DedupeStr = '1');
    SourceLib := ExtractJsonValue(Params, 'source_library');
    UseLibStr := ExtractJsonValue(Params, 'use_component_library');

    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;

    { Collect component names first, so modification never touches a live       }
    { component iterator. }
    CompNames := TStringList.Create;
    Try
        CompIter := SchLib.SchLibIterator_Create;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Component := CompIter.FirstSchObject;
            While Component <> Nil Do
            Begin
                Nm := '';
                Try Nm := Component.LibReference; Except End;
                If Nm <> '' Then CompNames.Add(Nm);
                Component := CompIter.NextSchObject;
            End;
        Finally
            SchLib.SchIterator_Destroy(CompIter);
        End;

        CompsTouched := 0;
        DupsRemoved := 0;
        SourcesCleared := 0;
        LinksRepaired := 0;
        SourcesSet := 0;
        SchServer.ProcessControl.PreProcess(SchLib, '');

        For C := 0 To CompNames.Count - 1 Do
        Begin
            Component := SchLib.GetState_SchComponentByLibRef(CompNames[C]);
            If Component = Nil Then Continue;

            { Bug 2: the stale origin string is a COMPONENT-level property. Clear }
            { it (and the target-file tag) here. }
            OldSrc := '';
            Try OldSrc := Component.SourceLibraryName; Except End;
            Try Component.SourceLibraryName := ''; Except End;
            Try Component.TargetFileName := '*'; Except End;
            If OldSrc <> '' Then Inc(SourcesCleared);

            { Capture (type|name, was-current) up front, so after dedupe the      }
            { survivor can be re-flagged current if any of its copies was. }
            AllKeys := TStringList.Create;
            AllCurs := TStringList.Create;
            Try
                ImplIter := Component.SchIterator_Create;
                Try
                    ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
                    Impl := ImplIter.FirstSchObject;
                    While Impl <> Nil Do
                    Begin
                        ModelType := ''; ModelName := ''; IsCur := False;
                        Try ModelType := Impl.ModelType; Except End;
                        Try ModelName := Impl.ModelName; Except End;
                        Try IsCur := Impl.IsCurrent; Except End;
                        AllKeys.Add(ModelType + '|' + ModelName);
                        If IsCur Then AllCurs.Add('1') Else AllCurs.Add('0');
                        Impl := ImplIter.NextSchObject;
                    End;
                Finally
                    Component.SchIterator_Destroy(ImplIter);
                End;

                { Dedupe IN PLACE: remove later duplicates, keep the FIRST of      }
                { each (type|name). Keeping the object preserves its parameters    }
                { and its MapAsString pin-map; only surplus copies go. Re-scan     }
                { each pass so the live iterator is never mutated. }
                Guard := 2000;
                RemovedOne := True;
                While RemovedOne And (Guard > 0) Do
                Begin
                    RemovedOne := False;
                    Found := Nil;
                    SeenKeys := TStringList.Create;
                    ScanIter := Component.SchIterator_Create;
                    Try
                        ScanIter.AddFilter_ObjectSet(MkSet(eImplementation));
                        Impl := ScanIter.FirstSchObject;
                        While Impl <> Nil Do
                        Begin
                            ModelType := ''; ModelName := '';
                            Try ModelType := Impl.ModelType; Except End;
                            Try ModelName := Impl.ModelName; Except End;
                            Key := ModelType + '|' + ModelName;
                            K := -1;
                            For J := 0 To SeenKeys.Count - 1 Do
                                If SeenKeys[J] = Key Then K := J;
                            If K >= 0 Then Begin Found := Impl; Break; End;
                            SeenKeys.Add(Key);
                            Impl := ScanIter.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(ScanIter);
                        SeenKeys.Free;
                    End;
                    If Found <> Nil Then
                    Begin
                        Try Component.RemoveSchImplementation(Found); Except End;
                        Inc(DupsRemoved);
                        RemovedOne := True;
                        Dec(Guard);
                    End;
                End;

                { Finalize survivors (one per key now): restore IsCurrent, apply   }
                { rename (ModelName + its datafile entity), and repair a footprint  }
                { left with no datafile link. Parameters and MapAsString are       }
                { untouched, so they carry forward intact. }
                ImplIter := Component.SchIterator_Create;
                Try
                    ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
                    Impl := ImplIter.FirstSchObject;
                    While Impl <> Nil Do
                    Begin
                        ModelType := ''; ModelName := '';
                        Try ModelType := Impl.ModelType; Except End;
                        Try ModelName := Impl.ModelName; Except End;
                        Key := ModelType + '|' + ModelName;

                        Cur := False;
                        For J := 0 To AllKeys.Count - 1 Do
                            If (AllKeys[J] = Key) And (AllCurs[J] = '1') Then Cur := True;
                        Try Impl.IsCurrent := Cur; Except End;

                        If (Not DedupeOnly) And (RenameMap <> '') Then
                        Begin
                            NewName := LookupRename(RenameMap, ModelName, ModelName);
                            If NewName <> ModelName Then
                            Begin
                                Try Impl.ModelName := NewName; Except End;
                                LinkCount := 0;
                                Try LinkCount := Impl.DatafileLinkCount; Except End;
                                For J := 0 To LinkCount - 1 Do
                                Begin
                                    Link := Nil;
                                    Try Link := Impl.DatafileLink[J]; Except End;
                                    If Link <> Nil Then
                                    Begin
                                        EntNm := '';
                                        Try EntNm := Link.EntityName; Except End;
                                        If EntNm = ModelName Then
                                            Try Link.EntityName := NewName; Except End;
                                    End;
                                End;
                                ModelName := NewName;
                            End;
                        End;

                        If UpperCase(ModelType) = 'PCBLIB' Then
                        Begin
                            LinkCount := 0;
                            Try LinkCount := Impl.DatafileLinkCount; Except End;
                            If LinkCount = 0 Then
                            Begin
                                Try Impl.AddDataFileLink(ModelName, '', 'PCBLib'); Except End;
                                LinkCount := 0;
                                Try LinkCount := Impl.DatafileLinkCount; Except End;
                                Inc(LinksRepaired);
                            End;
                            { Populate the source-library Location so the         }
                            { footprint embeds on compile (empty resolves by name }
                            { but never embeds). Only when source_library is set;  }
                            { existing Locations are otherwise left untouched.     }
                            If SourceLib <> '' Then
                            Begin
                                SchBeginModify(Impl);
                                For J := 0 To LinkCount - 1 Do
                                    Try Impl.DatafileLink[J].Location := SourceLib; Except End;
                                If UseLibStr = 'true' Then Try Impl.UseComponentLibrary := True; Except End;
                                If UseLibStr = 'false' Then Try Impl.UseComponentLibrary := False; Except End;
                                SchEndModify(Impl);
                                Inc(SourcesSet);
                            End;
                        End;

                        Impl := ImplIter.NextSchObject;
                    End;
                Finally
                    Component.SchIterator_Destroy(ImplIter);
                End;

                Inc(CompsTouched);
            Finally
                AllKeys.Free;
                AllCurs.Free;
            End;
        End;

        SchServer.ProcessControl.PostProcess(SchLib, 'Normalize implementations');
        MarkLibDirty(SchLib);
    Finally
        CompNames.Free;
    End;

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"components_touched":' + IntToStr(CompsTouched)
        + ',"duplicates_removed":' + IntToStr(DupsRemoved)
        + ',"sources_cleared":' + IntToStr(SourcesCleared)
        + ',"links_repaired":' + IntToStr(LinksRepaired)
        + ',"sources_set":' + IntToStr(SourcesSet) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_SetModelSource - write the datafile-link Location (source-library ref) on }
{ a component's footprint models, so the footprint is EMBEDDABLE in a            }
{ self-contained compiled library. An empty Location resolves in the editor by  }
{ name but cannot embed on compile. Sets Location IN PLACE (never AddDataFileLink}
{ with a path, which wedges AD26). Targets PCBLIB models matching model_name if  }
{ given, else all. Optional use_component_library ('true'/'false') sets the      }
{ embed-vs-search flag. Verify the exact Location string + flag against a        }
{ known-good reference model before bulk use.                                    }
{ Params: component_name, source_library (required), model_name (optional),      }
{         use_component_library (optional), library_path (optional).             }
Function Lib_SetModelSource(Params : String; RequestId : String) : String;
Var
    LibPath, CompName, SourceLib, ModelName, UseLibStr, RespJson, CurName, MT : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    Updated, J, LinkCount : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    CompName := ExtractJsonValue(Params, 'component_name');
    SourceLib := ExtractJsonValue(Params, 'source_library');
    ModelName := ExtractJsonValue(Params, 'model_name');
    UseLibStr := ExtractJsonValue(Params, 'use_component_library');
    If CompName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'component_name is required');
        Exit;
    End;
    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active and library_path did not resolve');
        Exit;
    End;
    Component := SchLib.GetState_SchComponentByLibRef(CompName);
    If Component = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND', 'Component not found in ' + LibPath + ': ' + CompName);
        Exit;
    End;

    Updated := 0;
    SchServer.ProcessControl.PreProcess(SchLib, '');
    ImplIter := Component.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            CurName := '';
            Try MT := Impl.ModelType; Except End;
            Try CurName := Impl.ModelName; Except End;
            If (UpperCase(MT) = 'PCBLIB') And ((ModelName = '') Or (CurName = ModelName)) Then
            Begin
                SchBeginModify(Impl);
                LinkCount := 0;
                Try LinkCount := Impl.DatafileLinkCount; Except End;
                If LinkCount = 0 Then
                Begin
                    Try Impl.AddDataFileLink(CurName, '', 'PCBLib'); Except End;
                    LinkCount := 0;
                    Try LinkCount := Impl.DatafileLinkCount; Except End;
                End;
                For J := 0 To LinkCount - 1 Do
                    Try Impl.DatafileLink[J].Location := SourceLib; Except End;
                If UseLibStr = 'true' Then Try Impl.UseComponentLibrary := True; Except End;
                If UseLibStr = 'false' Then Try Impl.UseComponentLibrary := False; Except End;
                SchEndModify(Impl);
                Inc(Updated);
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(ImplIter);
    End;
    SchServer.ProcessControl.PostProcess(SchLib, 'Set model source');
    MarkLibDirty(SchLib);

    RespJson := '{"success":true,"component":"' + EscapeJsonString(CompName) + '"'
        + ',"source_library":"' + EscapeJsonString(SourceLib) + '"'
        + ',"models_updated":' + IntToStr(Updated) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_ProbeFootprint - READ-ONLY dump of a PcbLib footprint's name-bearing      }
{ fields, to locate where an old name persists after a rename. On IPCB_LibComp   }
{ Name and Pattern are the SAME property (Name := new writes both); its only     }
{ metadata is Name, Description, Height. So a stale name lives in a child        }
{ primitive: this dumps every primitive's ObjectId and, for text objects, its    }
{ .Text, so the offending record is visible. Nothing is written.                 }
{ Params: footprint_name (required), library_path (optional).                    }
Function Lib_ProbeFootprint(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, FpDescr, PrimsJson, RespJson, TxtVal : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Txt : IPCB_Text;
    HeightMils, PrimCount : Integer;
    PFirst : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If FpName = FpWanted Then Begin Target := Footprint; Break; End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND', 'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    FpName := '';
    FpDescr := '';
    HeightMils := 0;
    Try FpName := Target.Name; Except End;
    Try FpDescr := Target.Description; Except End;
    Try HeightMils := CoordToMils(Target.Height); Except End;

    PrimsJson := '[';
    PFirst := True;
    PrimCount := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Inc(PrimCount);
            TxtVal := '';
            If Prim.ObjectId = eTextObject Then
            Begin
                Txt := Prim;
                Try TxtVal := Txt.Text; Except End;
            End;
            If TxtVal <> '' Then
            Begin
                If Not PFirst Then PrimsJson := PrimsJson + ',';
                PFirst := False;
                PrimsJson := PrimsJson + '{"object_id":' + IntToStr(Prim.ObjectId)
                    + ',"text":"' + EscapeJsonString(TxtVal) + '"}';
            End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    PrimsJson := PrimsJson + ']';

    RespJson := '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"footprint":"' + EscapeJsonString(FpName) + '"'
        + ',"description":"' + EscapeJsonString(FpDescr) + '"'
        + ',"height_mils":' + IntToStr(HeightMils)
        + ',"primitive_count":' + IntToStr(PrimCount)
        + ',"texts":' + PrimsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_MoveComponents - bulk copy (+ optional delete) of components between two  }
{ SchLibs, the bulk analog of Lib_CopyComponent. Replicate carries the whole     }
{ symbol (pins, parameters, models). Focuses SOURCE once (Replicate needs source }
{ context) and addresses DEST by path via GetSchDocumentByPath, so there is no   }
{ per-component focus thrashing. Names arrive as a '~~'-separated explicit list   }
{ (the Python tool resolves any regex first). A name already in dest is skipped  }
{ unless overwrite. delete_from_source defaults true. Names collected up front,   }
{ so removing from source never mutates a live iterator.                         }
{ Params: source_library, dest_library, names ('~~'-sep, required), overwrite,   }
{         delete_from_source.                                                    }
Function Lib_MoveComponents(Params : String; RequestId : String) : String;
Var
    SourcePath, DestPath, NamesStr, Remaining, Name, OverwriteStr, DeleteStr, RespJson : String;
    Overwrite, DeleteFromSource : Boolean;
    SourceLib, DestLib : ISch_Lib;
    SourceComp, NewComp, Existing : ISch_Component;
    ServerDoc : IServerDocument;
    Moved, Skipped, Failed : Integer;
Begin
    SourcePath := ExtractJsonValue(Params, 'source_library');
    DestPath := ExtractJsonValue(Params, 'dest_library');
    NamesStr := ExtractJsonValue(Params, 'names');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    DeleteStr := ExtractJsonValue(Params, 'delete_from_source');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');
    DeleteFromSource := (DeleteStr = '') Or (DeleteStr = 'true') Or (DeleteStr = 'True') Or (DeleteStr = '1');

    If (SourcePath = '') Or (DestPath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'source_library and dest_library are required');
        Exit;
    End;
    If NamesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'names is required');
        Exit;
    End;
    If UpperCase(SourcePath) = UpperCase(DestPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAME_LIBRARY', 'source_library and dest_library are the same');
        Exit;
    End;

    { Open dest (load it), then focus source so Replicate has its context. }
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', DestPath);
    RunProcess('WorkspaceManager:OpenObject');

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', SourcePath);
    RunProcess('WorkspaceManager:OpenObject');

    SourceLib := SchServer.GetCurrentSchDocument;
    If (SourceLib = Nil) Or (SourceLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'Failed to focus source library at ' + SourcePath);
        Exit;
    End;
    DestLib := SchServer.GetSchDocumentByPath(DestPath);
    If (DestLib = Nil) Or (DestLib.ObjectId <> eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'Failed to open destination library at ' + DestPath);
        Exit;
    End;

    Moved := 0;
    Skipped := 0;
    Failed := 0;
    Remaining := NamesStr;
    While True Do
    Begin
        Name := NextBatchOp(Remaining);
        If Name = '' Then Break;

        SourceComp := SourceLib.GetState_SchComponentByLibRef(Name);
        If SourceComp = Nil Then Begin Inc(Failed); Continue; End;

        Existing := DestLib.GetState_SchComponentByLibRef(Name);
        If (Existing <> Nil) And (Not Overwrite) Then Begin Inc(Skipped); Continue; End;

        NewComp := SourceComp.Replicate;
        If NewComp = Nil Then Begin Inc(Failed); Continue; End;
        NewComp.LibReference := Name;

        SchServer.ProcessControl.PreProcess(DestLib, '');
        If Existing <> Nil Then Try DestLib.RemoveSchComponent(Existing); Except End;
        DestLib.AddSchComponent(NewComp);
        SchServer.ProcessControl.PostProcess(DestLib, 'Move component');

        If DeleteFromSource Then
        Begin
            SchServer.ProcessControl.PreProcess(SourceLib, '');
            Try SourceLib.RemoveSchComponent(SourceComp); Except End;
            SchServer.ProcessControl.PostProcess(SourceLib, 'Move component');
        End;

        Inc(Moved);
    End;

    { Dirty both docs BY PATH (MarkLibDirty only dirties the focused doc). }
    Try
        ServerDoc := Client.GetDocumentByPath(DestPath);
        If ServerDoc <> Nil Then ServerDoc.SetModified(True);
    Except End;
    If DeleteFromSource Then
        Try
            ServerDoc := Client.GetDocumentByPath(SourcePath);
            If ServerDoc <> Nil Then ServerDoc.SetModified(True);
        Except End;

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourcePath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestPath) + '"'
        + ',"moved":' + IntToStr(Moved)
        + ',"skipped":' + IntToStr(Skipped)
        + ',"failed":' + IntToStr(Failed) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_MoveFootprints - bulk copy (+ optional delete) of footprints between two  }
{ PcbLibs, the PcbLib analog of Lib_MoveComponents. Uses the canonical combine   }
{ pattern: DestLib.CreateNewComponent + Footprint.CopyTo(NewFP, eFullCopy) +     }
{ RegisterComponent, then RemoveComponent + DeRegisterComponent on the source.   }
{ Both libraries are resolved by path (GetPCBLibraryByPath, load if needed), so  }
{ neither needs to be focused. A name already in dest is skipped unless          }
{ overwrite. delete_from_source defaults true. Names arrive '~~'-separated.      }
{ Params: source_library, dest_library, names (required), overwrite,            }
{         delete_from_source.                                                    }
Function Lib_MoveFootprints(Params : String; RequestId : String) : String;
Var
    SourcePath, DestPath, NamesStr, Remaining, Name, OverwriteStr, DeleteStr, RespJson, FpName : String;
    Overwrite, DeleteFromSource : Boolean;
    SourceLib, DestLib : IPCB_Library;
    Footprint, NewFP, Existing, Fp : IPCB_LibComponent;
    Moved, Skipped, Failed, J : Integer;
Begin
    SourcePath := ExtractJsonValue(Params, 'source_library');
    DestPath := ExtractJsonValue(Params, 'dest_library');
    NamesStr := ExtractJsonValue(Params, 'names');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    DeleteStr := ExtractJsonValue(Params, 'delete_from_source');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');
    DeleteFromSource := (DeleteStr = '') Or (DeleteStr = 'true') Or (DeleteStr = 'True') Or (DeleteStr = '1');

    If (SourcePath = '') Or (DestPath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'source_library and dest_library are required');
        Exit;
    End;
    If NamesStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'names is required');
        Exit;
    End;
    If UpperCase(SourcePath) = UpperCase(DestPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAME_LIBRARY', 'source_library and dest_library are the same');
        Exit;
    End;

    SourceLib := Nil;
    Try SourceLib := PCBServer.GetPCBLibraryByPath(SourcePath); Except End;
    If SourceLib = Nil Then Try SourceLib := PCBServer.LoadPCBLibraryByPath(SourcePath); Except End;
    If SourceLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to open source PCB library at ' + SourcePath);
        Exit;
    End;
    DestLib := Nil;
    Try DestLib := PCBServer.GetPCBLibraryByPath(DestPath); Except End;
    If DestLib = Nil Then Try DestLib := PCBServer.LoadPCBLibraryByPath(DestPath); Except End;
    If DestLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to open destination PCB library at ' + DestPath);
        Exit;
    End;

    Moved := 0;
    Skipped := 0;
    Failed := 0;
    PCBServer.PreProcess;
    Remaining := NamesStr;
    While True Do
    Begin
        Name := NextBatchOp(Remaining);
        If Name = '' Then Break;

        { Find the source footprint by name (index scan; not a live iterator). }
        Footprint := Nil;
        For J := 0 To SourceLib.ComponentCount - 1 Do
        Begin
            Fp := SourceLib.GetComponent(J);
            FpName := '';
            If Fp <> Nil Then Try FpName := Fp.Name; Except End;
            If FpName = Name Then Begin Footprint := Fp; Break; End;
        End;
        If Footprint = Nil Then Begin Inc(Failed); Continue; End;

        Existing := DestLib.GetComponentByName(Name);
        If (Existing <> Nil) And (Not Overwrite) Then Begin Inc(Skipped); Continue; End;
        If Existing <> Nil Then
        Begin
            Try DestLib.RemoveComponent(Existing); Except End;
            Try DestLib.DeRegisterComponent(Existing); Except End;
        End;

        NewFP := DestLib.CreateNewComponent;
        If NewFP = Nil Then Begin Inc(Failed); Continue; End;
        Try Footprint.CopyTo(NewFP, eFullCopy); Except End;
        Try NewFP.Name := Name; Except End;
        DestLib.RegisterComponent(NewFP);

        If DeleteFromSource Then
        Begin
            Try SourceLib.RemoveComponent(Footprint); Except End;
            Try SourceLib.DeRegisterComponent(Footprint); Except End;
        End;

        Inc(Moved);
    End;
    PCBServer.PostProcess;

    Try DestLib.Board.ViewManager_FullUpdate; Except End;
    Try DestLib.RefreshView; Except End;
    SaveDocByPath(DestLib.Board.FileName);
    If DeleteFromSource Then SaveDocByPath(SourceLib.Board.FileName);

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourcePath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestPath) + '"'
        + ',"moved":' + IntToStr(Moved)
        + ',"skipped":' + IntToStr(Skipped)
        + ',"failed":' + IntToStr(Failed) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_CopyFootprint - copy ONE footprint (all pads/primitives) by name into a   }
{ PcbLib, optionally renaming, the footprint analog of Lib_CopyComponent. No     }
{ delete. Same-library copy requires a different new_name. Libraries resolve by  }
{ path (default source = focused, dest = source). A name already in dest errors  }
{ unless overwrite.                                                              }
{ Params: source_name (required), new_name (default source_name),               }
{         source_library (optional), dest_library (optional), overwrite.         }
Function Lib_CopyFootprint(Params : String; RequestId : String) : String;
Var
    SourceLibPath, DestLibPath, SourceName, NewName, OverwriteStr, RespJson, FpName : String;
    Overwrite, SameLib : Boolean;
    SourceLib, DestLib : IPCB_Library;
    Footprint, NewFP, Existing, Fp : IPCB_LibComponent;
    J : Integer;
Begin
    SourceLibPath := ExtractJsonValue(Params, 'source_library');
    DestLibPath := ExtractJsonValue(Params, 'dest_library');
    SourceName := ExtractJsonValue(Params, 'source_name');
    NewName := ExtractJsonValue(Params, 'new_name');
    OverwriteStr := ExtractJsonValue(Params, 'overwrite');
    Overwrite := (OverwriteStr = 'true') Or (OverwriteStr = 'True') Or (OverwriteStr = '1');
    If SourceName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'source_name is required');
        Exit;
    End;
    If NewName = '' Then NewName := SourceName;

    { Source library: by path, else the focused PcbLib. }
    SourceLib := Nil;
    If SourceLibPath <> '' Then
    Begin
        Try SourceLib := PCBServer.GetPCBLibraryByPath(SourceLibPath); Except End;
        If SourceLib = Nil Then Try SourceLib := PCBServer.LoadPCBLibraryByPath(SourceLibPath); Except End;
    End
    Else
        SourceLib := PCBServer.GetCurrentPCBLibrary;
    If SourceLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No source PCB library (source_library not supplied and none focused)');
        Exit;
    End;
    If SourceLibPath = '' Then Try SourceLibPath := SourceLib.Board.FileName; Except End;

    { Destination library: default = source. }
    SameLib := (DestLibPath = '') Or (UpperCase(DestLibPath) = UpperCase(SourceLibPath));
    If SameLib Then
    Begin
        DestLib := SourceLib;
        DestLibPath := SourceLibPath;
        If NewName = SourceName Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SAME_NAME', 'Copying within the same library requires a different new_name');
            Exit;
        End;
    End
    Else
    Begin
        DestLib := Nil;
        Try DestLib := PCBServer.GetPCBLibraryByPath(DestLibPath); Except End;
        If DestLib = Nil Then Try DestLib := PCBServer.LoadPCBLibraryByPath(DestLibPath); Except End;
        If DestLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to open destination PCB library at ' + DestLibPath);
            Exit;
        End;
    End;

    Footprint := Nil;
    For J := 0 To SourceLib.ComponentCount - 1 Do
    Begin
        Fp := SourceLib.GetComponent(J);
        FpName := '';
        If Fp <> Nil Then Try FpName := Fp.Name; Except End;
        If FpName = SourceName Then Begin Footprint := Fp; Break; End;
    End;
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND', 'Footprint not found in ' + SourceLibPath + ': ' + SourceName);
        Exit;
    End;

    Existing := DestLib.GetComponentByName(NewName);
    If (Existing <> Nil) And (Not Overwrite) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_EXISTS', 'A footprint named "' + NewName + '" already exists in ' + DestLibPath + ' (pass overwrite=true to replace)');
        Exit;
    End;

    PCBServer.PreProcess;
    If Existing <> Nil Then
    Begin
        Try DestLib.RemoveComponent(Existing); Except End;
        Try DestLib.DeRegisterComponent(Existing); Except End;
    End;
    NewFP := DestLib.CreateNewComponent;
    If NewFP = Nil Then
    Begin
        PCBServer.PostProcess;
        Result := BuildErrorResponse(RequestId, 'COPY_FAILED', 'CreateNewComponent returned Nil');
        Exit;
    End;
    Try Footprint.CopyTo(NewFP, eFullCopy); Except End;
    Try NewFP.Name := NewName; Except End;
    DestLib.RegisterComponent(NewFP);
    PCBServer.PostProcess;

    Try DestLib.Board.ViewManager_FullUpdate; Except End;
    Try DestLib.RefreshView; Except End;
    SaveDocByPath(DestLib.Board.FileName);

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourceLibPath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestLibPath) + '"'
        + ',"source":"' + EscapeJsonString(SourceName) + '"'
        + ',"new_name":"' + EscapeJsonString(NewName) + '"'
        + ',"same_library":' + BoolToJsonStr(SameLib) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_GetPadGeometry - full-precision pad dump for the datasheet land-pattern }
{ audit. Every dimension is emitted in MILLIMETRES as a float (datasheets     }
{ dimension land patterns metrically; the integer-mil dump used by the policy }
{ auditor rounds a 0.65 mm pitch by 10 um, which a tolerance-based comparison }
{ against the datasheet cannot afford). Coordinates are relative to the       }
{ footprint's own origin. Per pad: centre, size, shape (with roundrect        }
{ corner percentage), rotation, hole (size / width / type / plated), layer,   }
{ and the paste / solder mask expansions with whether each is a manual        }
{ override (Cache.*Valid = eCacheManual) or rule-driven.                      }
{ Params: footprint_name (required), library_path (optional, focused).       }
Function Lib_GetPadGeometry(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, FpDescr : String;
    ShapeStr, HoleStr, LayerStr, PadsJson, RespJson, ExpSrc : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    XOrg, YOrg : Integer;
    Count, CornerPct : Integer;
    ExpVal : Double;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'footprint_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY', 'No library is active and library_path was not supplied');
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'Failed to focus PCB library at ' + LibPath);
        Exit;
    End;

    Target := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If FpName = FpWanted Then Begin Target := Footprint; Break; End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT',
            'Footprint not found: ' + FpWanted);
        Exit;
    End;

    FpName := '';
    FpDescr := '';
    Try FpName := Target.Name; Except End;
    Try FpDescr := Target.Description; Except End;
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;

    PadsJson := '[';
    Count := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            ShapeStr := 'round';
            Try
                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                Else If Pad.TopShape = eRoundRectangle Then ShapeStr := 'roundrectangle'
                Else ShapeStr := 'round';
            Except End;

            CornerPct := 0;
            If ShapeStr = 'roundrectangle' Then
            Begin
                Try CornerPct := Pad.CRPercentage[eTopLayer]; Except End;
            End;

            HoleStr := 'round';
            Try
                If Pad.HoleType = eSquareHole Then HoleStr := 'square'
                Else If Pad.HoleType = eSlotHole Then HoleStr := 'slot'
                Else HoleStr := 'round';
            Except End;

            LayerStr := 'top';
            Try
                If (Pad.Layer = eMultiLayer) Or (Pad.HoleSize > 0) Then LayerStr := 'multi'
                Else If Pad.Layer = eBottomLayer Then LayerStr := 'bottom'
                Else LayerStr := 'top';
            Except End;

            If Count > 0 Then PadsJson := PadsJson + ',';
            PadsJson := PadsJson +
                '{"name":"' + EscapeJsonString(Pad.Name) + '"' +
                ',"x_mm":' + FloatToJsonStr(CoordToMM(Pad.X - XOrg)) +
                ',"y_mm":' + FloatToJsonStr(CoordToMM(Pad.Y - YOrg)) +
                ',"w_mm":' + FloatToJsonStr(CoordToMM(Pad.TopXSize)) +
                ',"h_mm":' + FloatToJsonStr(CoordToMM(Pad.TopYSize)) +
                ',"shape":"' + ShapeStr + '"' +
                ',"corner_pct":' + IntToStr(CornerPct) +
                ',"rotation":' + FloatToJsonStr(Pad.Rotation) +
                ',"hole_mm":' + FloatToJsonStr(CoordToMM(Pad.HoleSize));

            { Slot holes carry a second dimension. }
            ExpVal := 0;
            If HoleStr = 'slot' Then
            Begin
                Try ExpVal := CoordToMM(Pad.HoleWidth); Except End;
            End;
            PadsJson := PadsJson +
                ',"hole_w_mm":' + FloatToJsonStr(ExpVal) +
                ',"hole_type":"' + HoleStr + '"';

            Try
                PadsJson := PadsJson + ',"plated":' + BoolToJsonStr(Pad.Plated);
            Except
                PadsJson := PadsJson + ',"plated":true';
            End;
            PadsJson := PadsJson + ',"layer":"' + LayerStr + '"';

            { Paste expansion: value + manual-vs-rule source. }
            ExpVal := 0;
            ExpSrc := 'rule';
            Try
                If Pad.Cache.PasteMaskExpansionValid = eCacheManual Then
                Begin
                    ExpVal := CoordToMM(Pad.Cache.PasteMaskExpansion);
                    ExpSrc := 'manual';
                End;
            Except End;
            PadsJson := PadsJson +
                ',"paste_expansion_mm":' + FloatToJsonStr(ExpVal) +
                ',"paste_expansion_source":"' + ExpSrc + '"';

            { Solder mask expansion: same pattern. }
            ExpVal := 0;
            ExpSrc := 'rule';
            Try
                If Pad.Cache.SolderMaskExpansionValid = eCacheManual Then
                Begin
                    ExpVal := CoordToMM(Pad.Cache.SolderMaskExpansion);
                    ExpSrc := 'manual';
                End;
            Except End;
            PadsJson := PadsJson +
                ',"mask_expansion_mm":' + FloatToJsonStr(ExpVal) +
                ',"mask_expansion_source":"' + ExpSrc + '"}';

            Inc(Count);
            Pad := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;
    PadsJson := PadsJson + ']';

    RespJson :=
        '{"name":"' + EscapeJsonString(FpName) + '"' +
        ',"description":"' + EscapeJsonString(FpDescr) + '"' +
        ',"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"pad_count":' + IntToStr(Count) +
        ',"pads":' + PadsJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ Lib_ClearSourceLibrary - unpin every symbol in a SchLib from its source     }
{ provenance, the library-side sibling of the placed-component               }
{ clear_sch_source_library. When symbols were copied in from another library  }
{ (a vendor pack, a stock library) each carries SourceLibraryName /           }
{ TargetFileName pointing at the ORIGIN, and a stale DesignItemId; placing    }
{ them then re-links against a library that no longer exists. Per matching    }
{ component: clear SourceLibraryName, reset TargetFileName to '*', and sync   }
{ DesignItemId to the LibReference (each independently switchable). The       }
{ minimal fast path of what lib_normalize_implementations does as part of     }
{ its full model sweep. Deferred save via MarkLibDirty.                        }
{ Params: library_path (optional, focused default),                           }
{         component_names (optional comma list, empty = all),                 }
{         clear_target_file_name=true, sync_design_item_id=true.               }
Function Lib_ClearSourceLibrary(Params : String; RequestId : String) : String;
Var
    LibPath, NamesCsv, FlagStr, Nm, LibRef : String;
    SchLib : ISch_Lib;
    CompIter : ISch_Iterator;
    Component : ISch_Component;
    AllNames, WantNames : TStringList;
    ClearTarget, SyncId, WantAll : Boolean;
    C, Total, ClearedSrc, ClearedTgt, Synced : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    NamesCsv := ExtractJsonValue(Params, 'component_names');
    FlagStr := ExtractJsonValue(Params, 'clear_target_file_name');
    ClearTarget := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));
    FlagStr := ExtractJsonValue(Params, 'sync_design_item_id');
    SyncId := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));

    SchLib := FocusSchLib(LibPath);
    If SchLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHLIB',
            'Failed to focus schematic library at ' + LibPath);
        Exit;
    End;

    AllNames := TStringList.Create;
    WantNames := TStringList.Create;
    Try
        WantNames.CommaText := NamesCsv;
        WantAll := WantNames.Count = 0;

        { Two-phase walk (the normalize pattern): collect names via the   }
        { live iterator first, mutate by LibRef lookup after, so the      }
        { iterator never sees a component being modified under it.        }
        CompIter := SchLib.SchLibIterator_Create;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Component := CompIter.FirstSchObject;
            While Component <> Nil Do
            Begin
                Nm := '';
                Try Nm := Component.LibReference; Except End;
                If Nm <> '' Then
                Begin
                    If WantAll Or (WantNames.IndexOf(Nm) >= 0) Then
                        AllNames.Add(Nm);
                End;
                Component := CompIter.NextSchObject;
            End;
        Finally
            SchLib.SchIterator_Destroy(CompIter);
        End;

        Total := 0;
        ClearedSrc := 0;
        ClearedTgt := 0;
        Synced := 0;

        SchServer.ProcessControl.PreProcess(SchLib, '');
        Try
            For C := 0 To AllNames.Count - 1 Do
            Begin
                Component := SchLib.GetState_SchComponentByLibRef(AllNames[C]);
                If Component = Nil Then Continue;
                Inc(Total);

                Try
                    If Component.SourceLibraryName <> '' Then
                    Begin
                        Component.SourceLibraryName := '';
                        Inc(ClearedSrc);
                    End;
                Except End;

                If ClearTarget Then
                Begin
                    Try
                        If Component.TargetFileName <> '*' Then
                        Begin
                            Component.TargetFileName := '*';
                            Inc(ClearedTgt);
                        End;
                    Except End;
                End;

                If SyncId Then
                Begin
                    Try
                        LibRef := Component.LibReference;
                        If (LibRef <> '') And (Component.DesignItemId <> LibRef) Then
                        Begin
                            Component.DesignItemId := LibRef;
                            Inc(Synced);
                        End;
                    Except End;
                End;
            End;
        Finally
            SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
        End;

        SchLib.GraphicallyInvalidate;
        MarkLibDirty(SchLib);
    Finally
        AllNames.Free;
        WantNames.Free;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"total":' + IntToStr(Total) +
        ',"cleared_source_library":' + IntToStr(ClearedSrc) +
        ',"cleared_target_file_name":' + IntToStr(ClearedTgt) +
        ',"synced_design_item_id":' + IntToStr(Synced) + '}');
End;

{..............................................................................}
{ Command Handler - must be at end                                             }
{..............................................................................}

{..............................................................................}
{ Lib_SetMechLayers - Name, enable and kind the mechanical layers of ONE      }
{ named library.                                                              }
{                                                                              }
{ Params: library_path, layers                                                }
{   layers is '~~' separated operations, ';' separated fields:                }
{     layer=Mechanical13;name=Courtyard;enabled=true;kind=Courtyard Top       }
{                                                                              }
{ WHY THIS EXISTS SEPARATELY FROM THE pcb_* LAYER TOOLS. Those read whatever  }
{ board is current. Pointing them at a particular library depends on the      }
{ focus actually moving, and when it does not they silently operate on the    }
{ previously focused library instead: a run across twenty one libraries came  }
{ back with twenty one identical answers because every call had re-read the   }
{ same file while reporting success.                                          }
{                                                                              }
{ So this takes the library by PATH, and refuses unless the document that     }
{ ended up focused is the one that was asked for. An operation on the wrong   }
{ library is worse than no operation, because it looks like it worked.        }
{..............................................................................}

{ Which mechanical layer in THIS request is being given a kind.        }
{                                                                       }
{ Scans the caller's own ops rather than the stack, because a paired    }
{ kind is joined to the partner the caller is setting now. Reading the  }
{ stack instead would find whatever already held the kind, which is a   }
{ different layer and a different intent.                                }

Function FindLayerForKindInOps(OpsStr : String; WantKind : Integer) : Integer;
Var
    Remaining, Op, LayerName, KindStr : String;
    Num : Integer;
Begin
    Result := -1;
    Remaining := OpsStr;
    While Trim(Remaining) <> '' Do
    Begin
        Op := NextBatchOp(Remaining);
        If Trim(Op) = '' Then Continue;
        KindStr := Trim(GetBatchField(Op, 'kind'));
        If KindStr = '' Then Continue;
        If MechKindFromString(KindStr) = WantKind Then
        Begin
            LayerName := Trim(GetBatchField(Op, 'layer'));
            Num := ParseMechLayerNumber(LayerName);
            If Num > 0 Then
            Begin
                Result := Num;
                Exit;
            End;
        End;
    End;
End;

{ Whether two mechanical layers are joined, in either order.                  }
{                                                                              }
{ PairDefined is order sensitive, so asking one way round reports no pair for  }
{ one that exists the other way round.                                         }

Function PairIsDefined(MechPairs : IPCB_MechanicalLayerPairs;
    L1 : TLayer; L2 : TLayer) : Boolean;
Begin
    Result := False;
    If MechPairs = Nil Then Exit;
    Try Result := MechPairs.PairDefined(L1, L2); Except Result := False; End;
    If Result Then Exit;
    Try Result := MechPairs.PairDefined(L2, L1); Except Result := False; End;
End;

{ Drop every pair joining two layers, however many there are.                 }
{                                                                              }
{ Returns how many removals it took, which is also how many duplicates were    }
{ present. Bounded, because a build where RemovePair does nothing would        }
{ otherwise spin.                                                              }

Function DrainMechPair(MechPairs : IPCB_MechanicalLayerPairs;
    L1 : TLayer; L2 : TLayer) : Integer;
Var
    Guard : Integer;
Begin
    Result := 0;
    Guard := 0;
    While PairIsDefined(MechPairs, L1, L2) And (Guard < 64) Do
    Begin
        Try MechPairs.RemovePair(L1, L2); Except End;
        Try MechPairs.RemovePair(L2, L1); Except End;
        Guard := Guard + 1;
        Result := Guard;
    End;
End;

{ Join two mechanical layers and give the PAIR its kind.                      }
{                                                                              }
{ AddPair APPENDS WITHOUT CHECKING, so calling it for a pair that already      }
{ exists leaves a duplicate behind rather than returning the existing one.     }
{ Repeated sweeps over one library left fourteen pairs where four were         }
{ wanted, and the Layer Stack Manager shows every one. Existing pairs are      }
{ therefore drained first, which also yields the index: AddPair is the only    }
{ call that reports one, since PairDefined answers a boolean and LayerPair(i)  }
{ is noted as broken in the reference.                                         }
{                                                                              }
{ The TOP layer has to be the first argument.                                  }

Function JoinAndKindPair(MechPairs : IPCB_MechanicalLayerPairs;
    TopL : TLayer; BotL : TLayer; PairKind : Integer) : Boolean;
Var
    PairIdx, KindBack : Integer;
Begin
    Result := False;
    If MechPairs = Nil Then Exit;
    If PairKind < 0 Then Exit;

    DrainMechPair(MechPairs, TopL, BotL);
    { Still joined means the removals are not taking, and adding now would  }
    { only grow the duplicate count.                                        }
    If PairIsDefined(MechPairs, TopL, BotL) Then Exit;

    PairIdx := -1;
    Try PairIdx := MechPairs.AddPair(TopL, BotL); Except PairIdx := -1; End;
    If PairIdx < 0 Then Exit;

    Try MechPairs.SetState_LayerPairKind(PairIdx) := PairKind; Except End;
    KindBack := -1;
    Try KindBack := MechPairs.LayerPairKind(PairIdx); Except KindBack := -1; End;
    { A build that will not report the pair kind back must not read as a    }
    { failure, so only a value that came back DIFFERENT counts as refused.  }
    Result := (KindBack = PairKind) Or (KindBack < 0);
End;

{ The name the same request asked for on a given mechanical layer.            }
{                                                                              }
{ Needed because joining a layer pair renames both layers to Altium's own Top  }
{ and Bottom keywords, so the partner's name has to be put back even though    }
{ its own operation may already have run.                                      }

Function FindNameForLayerInOps(OpsStr : String; WantLayer : Integer) : String;
Var
    Remaining, Op, LayerName : String;
Begin
    Result := '';
    Remaining := OpsStr;
    While Trim(Remaining) <> '' Do
    Begin
        Op := NextBatchOp(Remaining);
        If Trim(Op) = '' Then Continue;
        LayerName := Trim(GetBatchField(Op, 'layer'));
        If ParseMechLayerNumber(LayerName) = WantLayer Then
        Begin
            Result := Trim(GetBatchField(Op, 'name'));
            Exit;
        End;
    End;
End;

Function Lib_SetMechLayers(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, OpsStr, Op : String;
    LayerName, NewName, EnabledStr, KindStr : String;
    ItemsJson, Problems : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    MasterStack : IPCB_MasterLayerStack;
    MechPairs : IPCB_MechanicalLayerPairs;
    OpsAll : String;
    PartnerKind, PartnerLayer : Integer;
    PartnerTLayer : TLayer;
    Paired : Boolean;
    PairKind, Restored : Integer;
    PairKindSet, HavePair : Boolean;
    HandledPairs, PairTag, OpReleased, Remaining : String;
    TidyPairs, Justified : Boolean;
    TidiedJson : String;
    ScanA, ScanB, KindA, KindB, Drained, PairsRemoved : Integer;
    TLayerA, TLayerB : TLayer;
    TopTLayer, BotTLayer : TLayer;
    PartnerObj : IPCB_LayerObject_V7;
    PartnerName : String;
    MechObj, OtherMech : IPCB_MechanicalLayer;
    DisplacedJson : String;
    Scan, OtherKind : Integer;
    TargetLayer : TLayer;
    MechNumber : Integer;
    KindId, KindBack, Changed, FailedCount : Integer;
    WantEnabled, GotEnabled, First, DidSomething : Boolean;
    NameBack : String;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    OpsStr := ExtractJsonValue(Params, 'layers');

    If Trim(OpsStr) = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'layers required, for example '
            + '"layer=Mechanical13;name=Courtyard;enabled=true"');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_LIBRARY',
            'No library is active and library_path was not supplied');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    { The check the pcb_* layer tools do not make. Opening a document can    }
    { report success without moving the focus, and every later read then     }
    { describes the wrong library.                                           }
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := Doc.DM_FullPath; Except End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT_FOCUSED',
            'Asked for ' + LibPath + ' but the focused document is "'
            + FocusedPath + '". Nothing was changed: editing whichever '
            + 'library happened to be in front would look like success.');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Focused ' + LibPath + ' but it is not a PCB library');
        Exit;
    End;

    Board := Nil;
    Try Board := PcbLib.Board; Except Board := Nil; End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'The library has no board to carry a layer stack');
        Exit;
    End;

    LayerStack := Nil;
    Try LayerStack := Board.LayerStack_V7; Except End;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP',
            'Could not access the layer stack of ' + LibPath);
        Exit;
    End;

    { The master stack is where a writable Kind lives. Guarded and       }
    { optional: an older build without it still gets name and enable,    }
    { and the kind write then reports that it did not take rather than   }
    { failing the whole call.                                             }
    MasterStack := Nil;
    Try MasterStack := Board.MasterLayerStack; Except MasterStack := Nil; End;

    { Where layer PAIRS live. A paired kind cannot be set without one. }
    MechPairs := Nil;
    Try MechPairs := Board.MechanicalPairs; Except MechPairs := Nil; End;

    { Kept whole: a paired kind looks up its partner in the caller's }
    { own request, and the loop below consumes OpsStr as it goes.    }
    OpsAll := OpsStr;

    ItemsJson := '';
    DisplacedJson := '';
    { The two halves of a pair name the SAME pair kind, so whichever is }
    { reached first does the work and the other reads the outcome here. }
    { Keying on the op order instead would break on a request that      }
    { lists Bottom before Top.                                          }
    HandledPairs := '';
    { Off by default: a tidy REMOVES pairs, and a caller that only wanted }
    { to rename a layer should not have the stack rearranged underneath.  }
    TidyPairs := (ExtractJsonValue(Params, 'tidy_pairs') = 'true');
    TidiedJson := '';
    PairsRemoved := 0;
    First := True;
    Changed := 0;
    FailedCount := 0;

    PCBServer.PreProcess;
    Try
        While Trim(OpsStr) <> '' Do
        Begin
            Op := NextBatchOp(OpsStr);
            If Trim(Op) = '' Then Continue;

            LayerName := Trim(GetBatchField(Op, 'layer'));
            NewName := GetBatchField(Op, 'name');
            EnabledStr := LowerCase(Trim(GetBatchField(Op, 'enabled')));
            KindStr := Trim(GetBatchField(Op, 'kind'));
            Problems := '';
            DidSomething := False;

            { Resolves the whole V9 range, not just the legacy 1 to 16.  }
            { A real library keeps most of its named layers in the 17 to }
            { 28 band, and those were being refused outright.            }
            MechNumber := ParseMechLayerNumber(LayerName);
            If MechNumber < 0 Then
                TargetLayer := eNoLayer
            Else
                TargetLayer := MechLayerFromNumber(MechNumber);

            If TargetLayer = eNoLayer Then
            Begin
                If MechNumber > 16 Then
                    Problems := 'mechanical layer ' + IntToStr(MechNumber)
                        + ' could not be resolved. This Altium build may not '
                        + 'expose LayerUtils.MechanicalLayer, which is what '
                        + 'reaches layers above 16.'
                Else
                    Problems := 'not a mechanical layer: ' + LayerName;
                LayerObj := Nil;
            End
            Else
            Begin
                LayerObj := Nil;
                Try LayerObj := LayerStack.LayerObject_V7[TargetLayer]; Except LayerObj := Nil; End;
                If LayerObj = Nil Then
                    Problems := 'layer not present in the stack';
            End;

            If LayerObj <> Nil Then
            Begin
                { ENABLED. Four reference scripts read and write             }
                { MechanicalLayerEnabled through LayerObject_V7, so it is     }
                { reached that way rather than through the                    }
                { ILayer.MechanicalLayer indexer, which is what is actually   }
                { undeclared in this binding.                                 }
                If EnabledStr <> '' Then
                Begin
                    WantEnabled := (EnabledStr = 'true') Or (EnabledStr = '1');
                    Try LayerObj.MechanicalLayerEnabled := WantEnabled; Except End;
                    GotEnabled := Not WantEnabled;
                    Try GotEnabled := LayerObj.MechanicalLayerEnabled; Except End;
                    If GotEnabled <> WantEnabled Then
                        Problems := Problems + 'enabled did not take. '
                    Else
                        DidSomething := True;
                    { A layer nobody can see is enabled but useless, so the  }
                    { display flag follows the enable rather than being a    }
                    { second call the caller has to remember.                }
                    If WantEnabled Then
                        Try Board.LayerIsDisplayed[TargetLayer] := True; Except End;
                End;

                If NewName <> '' Then
                Begin
                    Try LayerObj.Name := NewName; Except End;
                    NameBack := '';
                    Try NameBack := LayerObj.Name; Except End;
                    If NameBack <> NewName Then
                        Problems := Problems + 'name did not take. '
                    Else
                        DidSomething := True;
                End;

                If KindStr <> '' Then
                Begin
                    KindId := MechKindFromString(KindStr);
                    If KindId < 0 Then
                        Problems := Problems + 'unknown kind: ' + KindStr + '. '
                    Else
                    Begin
                        { KIND LIVES ON A DIFFERENT OBJECT.               }
                        {                                                  }
                        { Name and MechanicalLayerEnabled take on the      }
                        { LayerObject_V7 this handler already holds, and   }
                        { Kind does not: measured on a real library, the   }
                        { write was accepted and the read back was         }
                        { unchanged on EVERY mechanical layer, including   }
                        { ones well below 16, so it was never a range      }
                        { problem.                                         }
                        {                                                  }
                        { LayerObject_V7 is the LEGACY accessor. The       }
                        { reference reaches a writable Kind through        }
                        { MasterLayerStack.GetMechanicalLayer(n), which    }
                        { returns IPCB_MechanicalLayer, and writes Kind on }
                        { that. The V7 write is kept only as a fallback    }
                        { for builds with no MasterLayerStack.             }
                        MechObj := Nil;
                        If MasterStack <> Nil Then
                            Try
                                MechObj := MasterStack.GetMechanicalLayer(MechNumber);
                            Except
                                MechObj := Nil;
                            End;

                        { A PAIRED KIND NEEDS A LAYER PAIR FIRST.        }
                        {                                                  }
                        { Altium refuses "Component Outline Top" on a     }
                        { layer that is not joined to the layer carrying   }
                        { "Component Outline Bottom". Measured: on one     }
                        { layer in one call, Fab Notes and Not Set applied }
                        { and Component Outline Top was refused, with      }
                        { kinds_displaced empty, so contention was never   }
                        { the cause.                                        }
                        {                                                  }
                        { The partner comes from the SAME request. A       }
                        { caller setting a Top and its Bottom together is  }
                        { the ordinary case, and joining them here saves a }
                        { second pass that would need the pair anyway.     }
                        { THE PAIR HOLDS THE PAIRED KIND, NOT THE LAYER.  }
                        {                                                  }
                        { Creating the pair was necessary and not          }
                        { sufficient: with the pair present and nothing    }
                        { else holding the kind, the layer write still     }
                        { read back unchanged and left the                 }
                        { Library/LayerKindMapping stream empty, whose     }
                        { entry count tracks the number of assigned kinds  }
                        { exactly. A paired concept is carried by the pair }
                        { under a DIFFERENT enum, so it is written with    }
                        { SetState_LayerPairKind against a pair index.     }
                        {                                                  }
                        { Whichever half is reached first does the work,   }
                        { and the other reads the outcome out of           }
                        { HandledPairs: both name the same pair, so a      }
                        { second attempt would rebuild it and discard the  }
                        { kind just written.                                }
                        PartnerKind := MechKindPartner(KindId);
                        PairKind := MechPairKindFromLayerKind(KindId);
                        PairTag := '|' + IntToStr(PairKind) + '|';
                        HavePair := False;
                        PairKindSet := (PairKind >= 0)
                            And (Pos(PairTag, HandledPairs) > 0);

                        If (PartnerKind >= 0) And (PairKind >= 0)
                            And (MechPairs <> Nil) And (Not PairKindSet) Then
                        Begin
                            PartnerLayer := FindLayerForKindInOps(
                                OpsAll, PartnerKind);
                            If PartnerLayer > 0 Then
                            Begin
                                PartnerTLayer := MechLayerFromNumber(PartnerLayer);
                                If PartnerTLayer <> eNoLayer Then
                                Begin
                                    If Pos(' Top', MechKindToString(KindId)) > 0 Then
                                    Begin
                                        TopTLayer := TargetLayer;
                                        BotTLayer := PartnerTLayer;
                                    End
                                    Else
                                    Begin
                                        TopTLayer := PartnerTLayer;
                                        BotTLayer := TargetLayer;
                                    End;
                                    HavePair := True;

                                    PairKindSet := JoinAndKindPair(
                                        MechPairs, TopTLayer, BotTLayer, PairKind);
                                    If PairKindSet Then
                                        HandledPairs := HandledPairs + PairTag;

                                End;
                            End;
                        End;

                        { Write first, and only hunt on FAILURE.          }
                        {                                                  }
                        { A KIND BELONGS TO ONE LAYER AT A TIME, so        }
                        { assigning one another layer already holds does   }
                        { not take. That is why the single kinds land and  }
                        { the paired Top/Bottom ones are refused: a        }
                        { library already carrying Top Assembly elsewhere  }
                        { leaves nothing to assign. The reference clears   }
                        { the previous holder first.                        }
                        {                                                  }
                        { Searching for that holder means probing the      }
                        { stack a layer at a time, so it runs only when    }
                        { the write did not take. On the ordinary path it  }
                        { costs nothing, and on this one it costs a scan   }
                        { that stops at the layer it finds.                 }
                        KindBack := -1;
                        If MechObj <> Nil Then
                        Begin
                            Try MechObj.Kind := KindId; Except End;
                            Try KindBack := MechObj.Kind; Except KindBack := -1; End;
                        End;

                        { Not for a kind the pair already accepted. The     }
                        { layer property always reads back unchanged for a  }
                        { paired kind, so hunting on that alone would clear }
                        { the kind off unrelated layers on every call.      }
                        {                                                  }
                        { The layer numbers released are kept, because a   }
                        { release that is not followed by an assignment    }
                        { leaves the library holding FEWER kinds than it    }
                        { started with, and that has to be undone.          }
                        OpReleased := '';
                        If (KindBack <> KindId) And (KindId > 0)
                            And (Not PairKindSet)
                            And (MasterStack <> Nil) And (MechObj <> Nil) Then
                        Begin
                            Scan := 1;
                            While (Scan <= 1024) And (KindBack <> KindId) Do
                            Begin
                                If Scan <> MechNumber Then
                                Begin
                                    OtherMech := Nil;
                                    Try
                                        OtherMech := MasterStack.GetMechanicalLayer(Scan);
                                    Except
                                        OtherMech := Nil;
                                    End;
                                    If OtherMech <> Nil Then
                                    Begin
                                        OtherKind := -1;
                                        Try OtherKind := OtherMech.Kind; Except OtherKind := -1; End;
                                        If OtherKind = KindId Then
                                        Begin
                                            Try OtherMech.Kind := 0; Except End;
                                            OpReleased := OpReleased
                                                + IntToStr(Scan) + ',';
                                            If DisplacedJson <> '' Then
                                                DisplacedJson := DisplacedJson + ',';
                                            DisplacedJson := DisplacedJson
                                                + '{"layer":"Mechanical' + IntToStr(Scan)
                                                + '","kind":"'
                                                + EscapeJsonString(MechKindToString(KindId))
                                                + '","released_for":"Mechanical'
                                                + IntToStr(MechNumber) + '"}';
                                            Try MechObj.Kind := KindId; Except End;
                                            Try KindBack := MechObj.Kind; Except KindBack := -1; End;
                                        End;
                                    End;
                                End;
                                Scan := Scan + 1;
                            End;
                        End;

                        { RETRY THE PAIR ONCE THE OLD HOLDER IS CLEAR.      }
                        {                                                  }
                        { A pair kind is exclusive the same way a layer     }
                        { kind is, so the first attempt is refused while    }
                        { another layer still carries it. Releasing that    }
                        { layer and stopping there is the worst of both:    }
                        { measured on two libraries, the release took, the  }
                        { assignment did not, and only an identical second  }
                        { call recovered. Retrying here closes that window  }
                        { inside the one call.                              }
                        If HavePair And (Not PairKindSet) And (OpReleased <> '') Then
                        Begin
                            PairKindSet := JoinAndKindPair(
                                MechPairs, TopTLayer, BotTLayer, PairKind);
                            If PairKindSet Then
                                HandledPairs := HandledPairs + PairTag;
                        End;

                        { AND PUT THE KINDS BACK IF IT STILL WILL NOT TAKE. }
                        { Ending a call with the kind on neither the old    }
                        { layer nor the new one is destructive, and it      }
                        { reads as a partial success rather than a refusal. }
                        If (Not PairKindSet) And (KindBack <> KindId)
                            And (OpReleased <> '') And (MasterStack <> Nil) Then
                        Begin
                            Restored := 0;
                            Remaining := OpReleased;
                            While Pos(',', Remaining) > 0 Do
                            Begin
                                Scan := StrToIntDef(
                                    Copy(Remaining, 1, Pos(',', Remaining) - 1), -1);
                                Remaining := Copy(Remaining, Pos(',', Remaining) + 1,
                                    Length(Remaining));
                                If Scan > 0 Then
                                Begin
                                    OtherMech := Nil;
                                    Try
                                        OtherMech := MasterStack.GetMechanicalLayer(Scan);
                                    Except
                                        OtherMech := Nil;
                                    End;
                                    If OtherMech <> Nil Then
                                    Begin
                                        Try OtherMech.Kind := KindId; Except End;
                                        Restored := Restored + 1;
                                    End;
                                End;
                            End;
                            If Restored > 0 Then
                                Problems := Problems + 'the kind was put back on '
                                    + IntToStr(Restored) + ' layer(s) it was '
                                    + 'released from, so nothing was lost. ';
                        End;

                        { Last resort: the legacy object, for a build with }
                        { no MasterLayerStack at all.                       }
                        If (KindBack <> KindId) And (MechObj = Nil) Then
                        Begin
                            Try LayerObj.Kind := KindId; Except End;
                            KindBack := ReadMechKind(LayerObj);
                        End;

                        { PAIRING RENAMES BOTH LAYERS. Altium forces its    }
                        { own Top and Bottom keywords onto a layer the      }
                        { moment it joins a pair, which silently undoes the }
                        { name written earlier in this operation. Restored  }
                        { after the retry, since that rebuilds the pair and }
                        { would otherwise substitute the keyword again.     }
                        If HavePair Then
                        Begin
                            If NewName <> '' Then
                            Begin
                                Try LayerObj.Name := NewName; Except End;
                                NameBack := '';
                                Try NameBack := LayerObj.Name; Except End;
                                If NameBack <> NewName Then
                                    Problems := Problems
                                        + 'name did not survive pairing. ';
                            End;

                            { The partner keeps whatever name the same      }
                            { request asked for, rather than the keyword    }
                            { Altium substituted.                            }
                            PartnerName := FindNameForLayerInOps(
                                OpsAll, PartnerLayer);
                            If PartnerName <> '' Then
                            Begin
                                PartnerObj := Nil;
                                Try
                                    PartnerObj := LayerStack.LayerObject_V7[PartnerTLayer];
                                Except
                                    PartnerObj := Nil;
                                End;
                                If PartnerObj <> Nil Then
                                    Try PartnerObj.Name := PartnerName; Except End;
                            End;
                        End;

                        { A PAIRED KIND SUCCEEDS ON THE PAIR, so the layer  }
                        { property reading back unchanged is expected and  }
                        { is not the measure of whether it took.            }
                        If (KindBack = KindId) Or PairKindSet Then
                            DidSomething := True
                        Else
                        Begin
                            If MechObj = Nil Then
                                Problems := Problems + 'kind did not take, '
                                    + 'and MasterLayerStack.GetMechanicalLayer '
                                    + 'was unavailable, which is where a '
                                    + 'writable Kind lives. '
                            Else If (PartnerKind >= 0)
                                And (FindLayerForKindInOps(OpsAll, PartnerKind) < 0) Then
                                Problems := Problems + '"'
                                    + MechKindToString(KindId)
                                    + '" is one half of a pair, and a paired '
                                    + 'kind is held by the layer PAIR rather '
                                    + 'than by either layer. Assign "'
                                    + MechKindToString(PartnerKind)
                                    + '" to another mechanical layer in the '
                                    + 'SAME call so the two can be joined and '
                                    + 'the pair given the kind. '
                            Else If PartnerKind >= 0 Then
                                Problems := Problems + '"'
                                    + MechKindToString(KindId)
                                    + '" was refused as pair kind "'
                                    + MechPairKindToString(PairKind)
                                    + '" even with the pair present. '
                            Else
                                Problems := Problems + 'kind did not take; '
                                    + 'it reads back as "'
                                    + MechKindToString(KindBack) + '". ';
                        End;
                    End;
                End;

                If (EnabledStr = '') And (NewName = '') And (KindStr = '') Then
                    Problems := 'nothing asked for: give name, enabled or kind';
            End;

            If Not First Then ItemsJson := ItemsJson + ',';
            First := False;
            ItemsJson := ItemsJson
                + '{"layer":"' + EscapeJsonString(LayerName) + '",'
                + '"changed":' + BoolToJsonStr(DidSomething And (Problems = '')) + ','
                + '"problem":';
            If Problems = '' Then
                ItemsJson := ItemsJson + 'null}'
            Else
                ItemsJson := ItemsJson + '"' + EscapeJsonString(Trim(Problems)) + '"}';

            If (Problems = '') And DidSomething Then
                Changed := Changed + 1
            Else
                FailedCount := FailedCount + 1;
        End;

        { STALE PAIRS OUTLIVE THE KINDS THAT JUSTIFIED THEM.               }
        {                                                                  }
        { Nothing removes a pair when a kind moves to different layers,    }
        { and until AddPair stopped being called on pairs that already     }
        { existed, every sweep appended another copy. One library reached  }
        { fourteen pairs where four were wanted, all of them visible in    }
        { the Layer Stack Manager, and no operation exposed here could     }
        { clear them.                                                      }
        {                                                                  }
        { A pair earns its place only when the two layers carry kinds that }
        { are each other's opposite side. Anything else is left over, so   }
        { it goes. Pairs the ops above just built are kept by that same    }
        { test rather than by remembering them.                            }
        If TidyPairs And (MechPairs <> Nil) And (MasterStack <> Nil) Then
        Begin
            PairsRemoved := 0;
            For ScanA := 1 To MechScanLimit Do
            Begin
                KindA := -1;
                OtherMech := Nil;
                Try OtherMech := MasterStack.GetMechanicalLayer(ScanA); Except OtherMech := Nil; End;
                If OtherMech <> Nil Then
                    Try KindA := OtherMech.Kind; Except KindA := -1; End;

                TLayerA := MechLayerFromNumber(ScanA);
                If TLayerA <> eNoLayer Then
                    For ScanB := ScanA + 1 To MechScanLimit Do
                    Begin
                        TLayerB := MechLayerFromNumber(ScanB);
                        If TLayerB <> eNoLayer Then
                            If PairIsDefined(MechPairs, TLayerA, TLayerB) Then
                            Begin
                                KindB := -1;
                                OtherMech := Nil;
                                Try OtherMech := MasterStack.GetMechanicalLayer(ScanB); Except OtherMech := Nil; End;
                                If OtherMech <> Nil Then
                                    Try KindB := OtherMech.Kind; Except KindB := -1; End;

                                { Both sides must be paired kinds AND be  }
                                { each other's partner. A pair whose two  }
                                { layers hold unrelated kinds is not a    }
                                { pair anyone asked for.                   }
                                Justified := (KindA > 0) And (KindB > 0)
                                    And (MechKindPartner(KindA) = KindB);
                                If Not Justified Then
                                Begin
                                    Drained := DrainMechPair(MechPairs, TLayerA, TLayerB);
                                    PairsRemoved := PairsRemoved + Drained;
                                    If Drained > 0 Then
                                    Begin
                                        If TidiedJson <> '' Then
                                            TidiedJson := TidiedJson + ',';
                                        TidiedJson := TidiedJson
                                            + '{"layers":["Mechanical' + IntToStr(ScanA)
                                            + '","Mechanical' + IntToStr(ScanB) + '"],'
                                            + '"removed":' + IntToStr(Drained) + '}';
                                    End;
                                End;
                            End;
                    End;
            End;
        End;

        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;

    { NOTHING PARSED IS A FAILURE, not a successful run over no work.    }
    { A layers payload in the wrong shape produced no operations at all,  }
    { and this reported success having changed nothing. Across a sweep of }
    { twenty two libraries that read as twenty two successes.             }
    If (Changed = 0) And (FailedCount = 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_OPERATIONS',
            'No layer operations were parsed from the layers parameter, so '
            + 'nothing was changed in ' + LibPath + '. Expected '
            + '"layer=<name>;name=<text>;enabled=<bool>;kind=<text>" with '
            + '"~~" between entries.');
        Exit;
    End;

    Try Board.ViewManager_FullUpdate; Except End;
    SaveDocByPath(LibPath);

    Result := BuildSuccessResponse(RequestId,
        '{"library":"' + EscapeJsonString(LibPath) + '",'
        + '"layers":[' + ItemsJson + '],'
        + '"changed":' + IntToStr(Changed) + ','
        + '"failed":' + IntToStr(FailedCount) + ','
        { Which layers gave up a kind so this one could take it. A kind }
        { that silently moved off another layer is a change the caller  }
        { did not ask for and has to be able to see.                     }
        + '"kinds_displaced":[' + DisplacedJson + '],'
        + '"pairs_removed":' + IntToStr(PairsRemoved) + ','
        + '"pairs_tidied":[' + TidiedJson + '],'
        { The tidy sweep stops at this layer number. A stale pair above }
        { it survives, and saying so is the difference between a bound  }
        { and a silent one.                                              }
        + '"pairs_scanned_to":' + IntToStr(MechScanLimit) + '}');
End;

{ Force a library_path onto a parameter object.                              }
{                                                                             }
{ ExtractJsonValue finds the FIRST occurrence of a key, so prepending is      }
{ enough to override one the caller supplied, and the original object is      }
{ left untouched rather than rewritten. A caller passing a single             }
{ library_path alongside a library list gets the list honoured, which is the  }
{ only reading that makes sense for a sweep.                                  }

Function MergeLibraryPath(Params : String; LibPath : String) : String;
Var
    Rest : String;
Begin
    Rest := Trim(Params);
    If (Rest = '') Or (Rest = '{}') Then
    Begin
        Result := '{"library_path":"' + EscapeJsonString(LibPath) + '"}';
        Exit;
    End;
    If Copy(Rest, 1, 1) = '{' Then
        Rest := Copy(Rest, 2, Length(Rest))
    Else
        Rest := Rest + '}';
    Result := '{"library_path":"' + EscapeJsonString(LibPath) + '",' + Rest;
End;

{ One field out of a handler's own response envelope.                        }

Function ResponseField(Response : String; Key : String) : String;
Begin
    Result := ExtractJsonValue(Response, Key);
End;

Function HandleLibraryCommand(Action : String; Params : String; RequestId : String) : String;
Var
    SweepLibs, SweepAction, OnePath, OneParams, OneReply : String;
    ItemsJson, DataJson, ErrJson, OkStr : String;
    BarPos, Succeeded, FailedCount : Integer;
    FirstItem : Boolean;
Begin
    { A sweep across several libraries in ONE call.                          }
    {                                                                         }
    { Sequentially from the caller's side, every library costs a round trip   }
    { plus the orchestration between them. The expensive part is opening and  }
    { saving each library, which this cannot avoid, but the round trips it    }
    { can: the whole sweep becomes one request.                               }
    {                                                                         }
    { Handled here rather than in its own function because DelphiScript has   }
    { no forward declarations, so a separate function could not call this     }
    { dispatcher. Direct recursion can.                                       }
    If Action = 'run_across' Then
    Begin
        SweepAction := Trim(ExtractJsonValue(Params, 'action'));
        SweepLibs := ExtractJsonValue(Params, 'libraries');

        If SweepAction = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
                'action required: the library command to run on each library');
            Exit;
        End;
        If SweepAction = 'run_across' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_ACTION',
                'run_across cannot sweep itself');
            Exit;
        End;
        If Trim(SweepLibs) = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
                'libraries required: full paths separated by "|". A sweep '
                + 'over no libraries would report a clean pass having done '
                + 'nothing.');
            Exit;
        End;

        ItemsJson := '';
        FirstItem := True;
        Succeeded := 0;
        FailedCount := 0;

        While Trim(SweepLibs) <> '' Do
        Begin
            BarPos := Pos('|', SweepLibs);
            If BarPos > 0 Then
            Begin
                OnePath := Trim(Copy(SweepLibs, 1, BarPos - 1));
                SweepLibs := Copy(SweepLibs, BarPos + 1, Length(SweepLibs));
            End
            Else
            Begin
                OnePath := Trim(SweepLibs);
                SweepLibs := '';
            End;
            If OnePath = '' Then Continue;

            OneParams := MergeLibraryPath(Params, OnePath);
            { A library that will not open must not abandon the rest of the  }
            { sweep. Its failure is recorded against its own name and the    }
            { loop carries on.                                                }
            OneReply := '';
            Try
                OneReply := HandleLibraryCommand(SweepAction, OneParams, RequestId);
            Except
                OneReply := '';
            End;

            If OneReply = '' Then
            Begin
                DataJson := 'null';
                ErrJson := '"the handler raised and returned nothing"';
                OkStr := 'false';
            End
            Else
            Begin
                DataJson := ResponseField(OneReply, 'data');
                If DataJson = '' Then DataJson := 'null';
                { The envelope's own success comes before any the payload    }
                { carries, and ExtractJsonValue takes the first match, so    }
                { this reads the envelope rather than a handler's own flag.  }
                If ResponseField(OneReply, 'success') = 'true' Then
                    OkStr := 'true'
                Else
                    OkStr := 'false';
                { Only on failure. "message" is a key inside the error       }
                { object, and a SUCCESSFUL payload carrying a field of that  }
                { name would otherwise be reported here as an error.         }
                ErrJson := 'null';
                If OkStr = 'false' Then
                Begin
                    ErrJson := ResponseField(OneReply, 'message');
                    If ErrJson = '' Then
                        ErrJson := 'null'
                    Else
                        ErrJson := '"' + EscapeJsonString(ErrJson) + '"';
                End;
            End;

            If OkStr = 'true' Then
                Succeeded := Succeeded + 1
            Else
                FailedCount := FailedCount + 1;

            If Not FirstItem Then ItemsJson := ItemsJson + ',';
            FirstItem := False;
            ItemsJson := ItemsJson
                + '{"library":"' + EscapeJsonString(OnePath) + '",'
                + '"success":' + OkStr + ','
                + '"data":' + DataJson + ','
                + '"error":' + ErrJson + '}';
        End;

        { Per library, never one aggregate flag. "17 of 20 worked" collapses }
        { into either a false clean or a false failure the moment it becomes }
        { a single boolean, and the caller cannot tell which library to fix. }
        Result := BuildSuccessResponse(RequestId,
            '{"action":"' + EscapeJsonString(SweepAction) + '",'
            + '"results":[' + ItemsJson + '],'
            + '"succeeded":' + IntToStr(Succeeded) + ','
            + '"failed":' + IntToStr(FailedCount) + ','
            + '"libraries":' + IntToStr(Succeeded + FailedCount) + '}');
        Exit;
    End;

    Case Action Of
        'create_symbol':        Result := Lib_CreateSymbol(Params, RequestId);
        'add_pin':              Result := Lib_AddPin(Params, RequestId);
        'add_pins':             Result := Lib_AddPins(Params, RequestId);
        'add_symbol_text':      Result := Lib_AddSymbolText(Params, RequestId);
        'add_symbol_rectangle': Result := Lib_AddSymbolRectangle(Params, RequestId);
        'add_symbol_line':      Result := Lib_AddSymbolLine(Params, RequestId);
        'add_symbol_lines':     Result := Lib_AddSymbolLines(Params, RequestId);
        'create_footprint':     Result := Lib_CreateFootprint(Params, RequestId);
        'add_footprint_pad':    Result := Lib_AddFootprintPad(Params, RequestId);
        'add_footprint_pads':   Result := Lib_AddFootprintPads(Params, RequestId);
        'add_footprint_track':  Result := Lib_AddFootprintTrack(Params, RequestId);
        'add_footprint_tracks': Result := Lib_AddFootprintTracks(Params, RequestId);
        'add_footprint_arc':    Result := Lib_AddFootprintArc(Params, RequestId);
        'add_footprint_text':   Result := Lib_AddFootprintText(Params, RequestId);
        'get_footprints':       Result := Lib_GetFootprints(Params, RequestId);
        'get_footprint_pads':   Result := Lib_GetFootprintPads(Params, RequestId);
        'get_library_geometry': Result := Lib_GetLibraryGeometry(Params, RequestId);
        'set_designator':       Result := Lib_SetDesignator(Params, RequestId);
        'set_designators':      Result := Lib_SetDesignators(Params, RequestId);
        'probe_designator':     Result := Lib_ProbeDesignator(Params, RequestId);
        'reload_library':       Result := Lib_ReloadLibrary(Params, RequestId);
        'convert_designators_to_stroke': Result := Lib_ConvertDesignatorsToStroke(Params, RequestId);
        'extract_intlib':       Result := Lib_ExtractIntLib(Params, RequestId);
        'link_footprint':       Result := Lib_LinkFootprint(Params, RequestId);
        'link_3d_model':        Result := Lib_Link3DModel(Params, RequestId);
        'set_mech_layers':      Result := Lib_SetMechLayers(Params, RequestId);
        'get_components':       Result := Lib_GetComponents(Params, RequestId);
        'search':               Result := Lib_Search(Params, RequestId);
        'get_component_details': Result := Lib_GetComponentDetails(Params, RequestId);
        'batch_set_params':    Result := Lib_BatchSetParams(Params, RequestId);
        'batch_rename':        Result := Lib_BatchRename(Params, RequestId);
        'diff_libraries':     Result := Lib_DiffLibraries(Params, RequestId);
        'add_symbol_arc':     Result := Lib_AddSymbolArc(Params, RequestId);
        'add_symbol_polygon': Result := Lib_AddSymbolPolygon(Params, RequestId);
        'set_component_description': Result := Lib_SetComponentDescription(Params, RequestId);
        'get_pin_list':       Result := Lib_GetPinList(Params, RequestId);
        'set_pin_owner_part': Result := Lib_SetPinOwnerPart(Params, RequestId);
        'copy_component':     Result := Lib_CopyComponent(Params, RequestId);
        'move_components':    Result := Lib_MoveComponents(Params, RequestId);
        'move_footprints':    Result := Lib_MoveFootprints(Params, RequestId);
        'copy_footprint':     Result := Lib_CopyFootprint(Params, RequestId);
        'audit_styles':       Result := Lib_AuditStyles(Params, RequestId);
        'set_label_format':   Result := Lib_SetLabelFormat(Params, RequestId);
        'set_label_formats':  Result := Lib_SetLabelFormats(Params, RequestId);
        'set_current_component': Result := Lib_SetCurrentComponent(Params, RequestId);
        'update_footprint_heights_from_3d': Result := Lib_UpdateFootprintHeightsFrom3D(Params, RequestId);
        'split_pin_functions':  Result := Lib_SplitPinFunctions(Params, RequestId);
        'install_library':      Result := Lib_InstallLibrary(Params, RequestId);
        'uninstall_library':    Result := Lib_UninstallLibrary(Params, RequestId);
        'delete_component':     Result := Lib_DeleteComponent(Params, RequestId);
        'rename_component':     Result := Lib_RenameComponent(Params, RequestId);
        'delete_footprint':     Result := Lib_DeleteFootprint(Params, RequestId);
        'remove_model':         Result := Lib_RemoveModel(Params, RequestId);
        'rename_footprint':     Result := Lib_RenameFootprint(Params, RequestId);
        'set_model_name':       Result := Lib_SetModelName(Params, RequestId);
        'set_model_source':     Result := Lib_SetModelSource(Params, RequestId);
        'probe_footprint':      Result := Lib_ProbeFootprint(Params, RequestId);
        'get_pad_geometry':     Result := Lib_GetPadGeometry(Params, RequestId);
        'normalize_implementations': Result := Lib_NormalizeImplementations(Params, RequestId);
        'clear_source_library': Result := Lib_ClearSourceLibrary(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown library action: ' + Action);
    End;
End;
