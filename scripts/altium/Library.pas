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

{ An explicit owner_part_id puts a primitive on that part of a multi-part }
{ symbol, the way Lib_AddPins already does; empty keeps SetOwnerPart.      }
{ Without it every body primitive landed on whichever part was current,  }
{ so part 2 of a dual op-amp could not get its own triangle.              }
Procedure SetOwnerPartFrom(Obj : ISch_GraphicalObject; Component : ISch_Component;
    OwnerStr : String);
Begin
    If Obj = Nil Then Exit;
    If (OwnerStr <> '') And IsIntStr(OwnerStr) And (StrToIntDef(OwnerStr, 0) > 0) Then
    Begin
        Try Obj.OwnerPartId := StrToIntDef(OwnerStr, 1); Except End;
        Try Obj.OwnerPartDisplayMode := 0; Except End;
    End
    Else
        SetOwnerPart(Obj, Component);
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

{ Add a new primitive to a library symbol where the editor will draw it.    }
{                                                                            }
{ A primitive added with Component.AddSchObject to the symbol on screen is  }
{ in the data but not in the editor's view: not drawn, not selectable, not  }
{ found by an iterator over the library, until something rebuilds the view }
{ (switching symbols, or reopening). The symbol on screen takes new objects }
{ through the LIBRARY, as Altium's own Import Pins example does; a symbol   }
{ that is not shown still takes them through the component. Returns True   }
{ when the library route was used.                                          }
{                                                                            }
{ MEASURED (AD 26.11): the library files the object on the part on screen,  }
{ overwriting OwnerPartId, so a part-2 polygon and a shared (0) power pin   }
{ both landed on part 1. The owner set by the caller is put back after.     }
Function AddLibSymbolPrim(SchLib : ISch_Lib; Component : ISch_Component;
    Obj : ISch_GraphicalObject) : Boolean;
Var
    Shown : ISch_Component;
    OwnerPart, OwnerMode : Integer;
Begin
    Result := False;
    Shown := Nil;
    Try Shown := SchLib.CurrentSchComponent; Except Shown := Nil; End;
    If (Shown <> Nil) And (Shown.I_ObjectAddress = Component.I_ObjectAddress) Then
    Begin
        OwnerPart := Obj.OwnerPartId;
        OwnerMode := Obj.OwnerPartDisplayMode;
        SchLib.RegisterSchObjectInContainer(Obj);
        If (Obj.OwnerPartId <> OwnerPart) Or (Obj.OwnerPartDisplayMode <> OwnerMode) Then
        Begin
            SchBeginModify(Obj);
            Obj.OwnerPartId := OwnerPart;
            Obj.OwnerPartDisplayMode := OwnerMode;
            SchEndModify(Obj);
        End;
        Result := True;
    End
    Else
    Begin
        Component.AddSchObject(Obj);
        SchRegisterObject(Component, Obj);
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
    FullPath : String;
    ServerDoc : IServerDocument;
Begin
    If SchLib = Nil Then Exit;

    { EVERY DEFERRED SAVE PASSES THROUGH HERE, which is why the follow-up
      is noted here rather than in the forty-odd handlers that call it.
      The edit is real in memory and absent from disk until app_save_all
      runs, and a caller who does not know that sees a tool that reported
      success and changed no file. }
    NoteNextStep('This edit is in memory only. Run app_save_all to write '
        + 'it to disk, and check still_dirty in the reply.');

    { MARK THE LIBRARY THAT WAS EDITED, NOT WHATEVER HAPPENS TO BE FOCUSED.
      This used to dirty Workspace.DM_FocusedDocument and ignore the SchLib
      it was handed. When the focused document was anything else -- a sheet,
      or another library -- the edited library was never flagged, so it was
      skipped by app_save_all, by SaveAllDirty, and by Altium's own
      File > Save, all of which correctly decline to write a clean document.

      MEASURED 2026-09-21: a component copied into a .SchLib read back in
      full through lib_get_component_details while the file on disk stayed
      byte-identical, 662016 bytes, with zero occurrences of the new name,
      across all three save routes. The edit was real and in memory; nothing
      had asked for it to be written.

      SchLib.DocumentName is the library's OWN path. Compare the helper
      directly below, which exists to catch this same wrong-library
      confusion when resolving one. }
    FullPath := '';
    Try FullPath := SchLib.DocumentName; Except End;
    If FullPath <> '' Then
    Begin
        NoteDocEdited(FullPath);
        ServerDoc := Client.GetDocumentByPath(FullPath);
        If ServerDoc <> Nil Then
            Try ServerDoc.SetModified(True); Except End
        Else
            { NO SILENT FALLBACK TO THE FOCUSED DOCUMENT. Marking a
              different document is what caused the defect above, and it
              cannot be distinguished from success afterwards. Say so
              instead. }
            NoteNextStep('The edited library is not open as a document, so '
                + 'it could not be flagged for saving and app_save_all will '
                + 'skip it. Open it first, then repeat the edit.');
    End
    Else
        NoteNextStep('The edited library did not report its own path, so it '
            + 'could not be flagged for saving. Verify the file on disk '
            + 'changed before relying on this edit.');
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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

{ ScanLibForComponent - find a symbol by LibReference by WALKING the document. }
{                                                                              }
{ GetState_SchComponentByLibRef IS NOT ENOUGH ON ITS OWN. It answers from the  }
{ library's index, and that index only knows the components the library was    }
{ LOADED with. A symbol created in this session is invisible to it until the   }
{ library has been saved and read back.                                        }
{                                                                              }
{ MEASURED on AD26 in a brand-new empty library:                               }
{   lib_create_symbol("CLEAN_SYM")     -> succeeded, and resolved by name      }
{                                         INSIDE the creating command          }
{   lib_batch_set_params("CLEAN_SYM")  -> no component with that libref        }
{   lib_link_footprint("CLEAN_SYM")    -> target component not found           }
{ while Component_1, which came with the file, resolved throughout. So authoring}
{ a symbol and then using it in the very next call could not work at all, and  }
{ the tools reported it as a missing component rather than as a library that   }
{ had not caught up.                                                           }
{                                                                              }
{ The walk is the same one ResolveLibComponent already used for its by-INDEX   }
{ path, and it is the idiom in CompRename2.pas: SchLibIterator_Create with an  }
{ eSchComponent filter, comparing LibReference byte for byte.                  }
Function ScanLibForComponent(SchLib : ISch_Lib; Name : String) : ISch_Component;
Var
    Iter : ISch_Iterator;
    LibComp : ISch_Component;
Begin
    Result := Nil;
    If (SchLib = Nil) Or (Name = '') Then Exit;
    Iter := SchLib.SchLibIterator_Create;
    If Iter = Nil Then Exit;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        LibComp := Iter.FirstSchObject;
        While LibComp <> Nil Do
        Begin
            If LibComp.LibReference = Name Then
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

{ RefreshSchLibFromDisk - save the library and READ IT BACK IN.               }
{                                                                             }
{ A component added this session is not findable by name until the document   }
{ has been reopened. Saving alone is NOT enough, and that is the part that    }
{ wastes people's time, because the file on disk is demonstrably correct      }
{ while every lookup keeps missing.                                           }
{                                                                             }
{ MEASURED, in this order, on AD26:                                           }
{   lib_create_symbol("RESOLVE_TEST")   succeeded, and the name resolved      }
{                                       INSIDE the creating command           }
{   app_save_all                        saved:true, still_dirty:0, and the    }
{                                       saved file contains RESOLVE_TEST      }
{   every by-name lookup                still missed                          }
{   CloseObject then OpenObject         the name resolves again               }
{                                                                             }
{ So the reopen is the step that matters and it is now part of the process    }
{ rather than something a caller has to know. Save first: closing a dirty     }
{ document either loses the edits or raises a prompt nothing here can answer. }
Function RefreshSchLibFromDisk(LibPath : String) : ISch_Lib;
Var
    ServerDoc : IServerDocument;
Begin
    Result := Nil;
    If LibPath = '' Then Exit;

    { A BASENAME HERE IS WORSE THAN NOTHING. DocumentName returns one for a  }
    { free document, and CloseObject / OpenObject given a bare name do       }
    { nothing at all, so the reopen appeared to run and changed nothing.     }
    { Resolve to an absolute path or give up honestly.                       }
    LibPath := ResolveLoadedDocPath(LibPath);
    If LibPath = '' Then Exit;

    { Flush this document only. }
    Try
        ServerDoc := Client.GetDocumentByPath(LibPath);
        If ServerDoc <> Nil Then
        Begin
            Try ServerDoc.SetModified(True); Except End;
            Try ServerDoc.DoFileSave(''); Except End;
        End;
    Except End;

    { The close below frees every component in this document, so the      }
    { reference held from creation stops pointing at anything. Dropping   }
    { it here is what keeps LookupLibComponent from handing a caller a    }
    { component that no longer exists.                                    }
    LastCreatedLibComponent := Nil;
    LastCreatedLibComponentName := '';

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    RunProcess('WorkspaceManager:CloseObject');

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', LibPath);
    RunProcess('WorkspaceManager:OpenObject');

    { THE LIBRARY THAT WAS REOPENED, OR NOTHING. This used to return        }
    { whatever schematic document was current afterwards, and reopening a   }
    { library does not always make it current. Live 2026-09-23: a lookup   }
    { into a new, empty library searched the library focused before it,    }
    { found the part there, and lib_move_components skipped it as already  }
    { present. SchLibIsAtPath exists for exactly this.                     }
    Try Result := SchServer.GetSchDocumentByPath(LibPath); Except Result := Nil; End;
    If Result = Nil Then
        Try Result := SchServer.GetCurrentSchDocument; Except End;
    If Not SchLibIsAtPath(Result, LibPath) Then
        Result := Nil;
End;

{ FindLibComponentInMemory - the index, the walk, and the symbol created or   }
{ renamed this session. NEVER REOPENS the library.                           }
{                                                                             }
{ Use it for "does this name already exist?" before an edit. The full        }
{ LookupLibComponent reopens on a miss, and a miss is the NORMAL answer to   }
{ that question: RefreshSchLibFromDisk saves the library, closes it and     }
{ opens it again, and every reference the caller is holding -- the library,  }
{ the component it is about to rename or copy -- then points into a closed  }
{ document. The edit lands on nothing, and each later save writes the       }
{ reopened library without it.                                              }
{                                                                             }
{ Live 2026-09-23 (AD 26.10.1.6, scratch library read back from disk after  }
{ every save): lib_rename_component and lib_copy_component both answered    }
{ verified:true while the file kept the old name and never gained the copy. }
{ lib_batch_rename, which does the same remove, rename and add but never    }
{ asks whether the new name exists, persisted.                              }
{                                                                             }
{ The cost is a name created this session and no longer the last one made: }
{ it can miss here, where the reopen would have found it.                   }
Function FindLibComponentInMemory(SchLib : ISch_Lib; Name : String) : ISch_Component;
Begin
    Result := Nil;
    If (SchLib = Nil) Or (Name = '') Then Exit;

    Try Result := SchLib.GetState_SchComponentByLibRef(Name); Except End;
    If Result <> Nil Then Exit;

    Result := ScanLibForComponent(SchLib, Name);
    If Result <> Nil Then Exit;

    { THE SYMBOL CREATED EARLIER IN THIS SESSION. Neither the index nor the  }
    { walk can see it, but the script still holds the reference it was given }
    { when it was made, and that outlives the command because the polling    }
    { loop does. This is the case that actually bites: author a symbol, then }
    { set a parameter or link a footprint on it in the very next call.       }
    { The name is compared against the one recorded at the time, NOT read }
    { back off the interface. See LastCreatedLibComponentName in Main for }
    { what a property read on a freed component does to the session.      }
    If (LastCreatedLibComponent <> Nil) And
       (LastCreatedLibComponentName = Name) Then
        Result := LastCreatedLibComponent;
End;

{ LookupLibComponent - a symbol of the open library by name, from memory.    }
{                                                                             }
{ Use this everywhere instead of calling GetState_SchComponentByLibRef: the  }
{ index, then the walk, then the symbol created last. It never saves, closes }
{ or reopens the library.                                                   }
Function LookupLibComponent(SchLib : ISch_Lib; Name : String) : ISch_Component;
Begin
    Result := Nil;
    If (SchLib = Nil) Or (Name = '') Then Exit;

    { The live library only. A miss is a miss: the caller reports it.        }
    {                                                                         }
    { This used to fall back to RefreshSchLibFromDisk, which SAVES the        }
    { library, closes it and opens it again, on every miss (issue #88). A    }
    { read wrote the user's file, and a stale name did it on every call.     }
    { MEASURED 2026-10-18 (AD 26.11): symbols created earlier in the session, }
    { not only the last one, are found here in memory, so the reopen no      }
    { longer earns its cost; a miss took 6.6 s and saved the file. An        }
    { explicit refresh stays available where a caller asks for it.           }
    Result := FindLibComponentInMemory(SchLib, Name);
End;

Function Lib_CreateSymbol(Params : String; RequestId : String) : String;
Var
    Name, DesignatorPrefix, Description : String;
    SchLib : ISch_Lib;
    Component, Verify : ISch_Component;
    PartCount, ActualParts : Integer;
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

        { PartCount is re-asserted for the same reason LibReference is, just  }
        { above: AddSchComponent does not necessarily keep what was set on    }
        { the detached object.                                                }
        If PartCount > 1 Then
            Try Component.PartCount := PartCount; Except End;

        SchLib.CurrentSchComponent := Component;
        LastCreatedLibComponent := Component;
        LastCreatedLibComponentName := Name;

        // Refresh the library editor view so the new component is visible.
        Try SchLib.GraphicallyInvalidate; Except End;

        MarkLibDirty(SchLib);

        { VERIFY. This used to return success with part_count ECHOED from the }
        { request. Measured on AD26: part_count=3 produced NO COMPONENT AT     }
        { ALL, and the reply still said success with part_count 3. The same    }
        { call with part_count=1 worked, so the failure is specific and silent.}
        { Resolving the symbol by its LibReference is the cheapest proof it    }
        { exists, and the count reported is the one READ BACK.                 }
        Verify := Nil;
        Try Verify := LookupLibComponent(SchLib, Name); Except End;

        If Verify = Nil Then
            Result := BuildSuccessResponse(RequestId,
                JsonObj(
                    JsonBool('success', False) + ',' +
                    JsonStr('name', Name) + ',' +
                    JsonInt('requested_part_count', PartCount) + ',' +
                    JsonStr('reason', 'the symbol does not resolve in the '
                        + 'library after being added. A part_count above 1 is '
                        + 'the known trigger: Altium wants a multi-part '
                        + 'component to carry primitives for each part. '
                        + 'Create it single-part, add pins with '
                        + 'owner_part_id, then raise the part count in the '
                        + 'library editor.')
                ))
        Else
        Begin
            ActualParts := PartCount;
            Try ActualParts := Verify.PartCount; Except End;
            Result := BuildSuccessResponse(RequestId,
                JsonObj(
                    JsonBool('success', True) + ',' +
                    JsonStr('name', Name) + ',' +
                    JsonInt('part_count', ActualParts) + ',' +
                    JsonBool('verified', True)
                ));
        End;
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
{ ONE STEP, NOT A SEARCH, and this is the second version of this function.   }
{ The first walked NextComponentPart until the DOCUMENT reported the target.  }
{ That walk is unnecessary, because the command's destination is determined:  }
{                                                                             }
{   Component.CurrentPartID := K   sets the property and does NOT move the    }
{                                  displayed part.                            }
{   SCH:NextComponentPart          moves the display to CurrentPartID + 1     }
{                                  and syncs the property to where it landed. }
{                                                                             }
{ So parking the property one below the target and stepping once arrives at   }
{ the target directly. Reported against a 4-part TPS23881B on AD 26.8.1.31    }
{ (GH #11), where it was verified by prediction rather than observation: with }
{ the display on part 4 and CurrentPartID set to 1, the model says the step   }
{ lands on part 2, and it did, returning exactly the 17 pins part 2 holds.    }
{                                                                             }
{ WHY THE WALK HAD TO GO, and it is not tidying. The walk was gated on a      }
{ readback it could not count on: GetState_CurrentSchComponentPartId is       }
{ DECLARED on that build but returns -1 at runtime, and the guard treated     }
{ "cannot read" as "nothing to check" and returned True WITHOUT STEPPING AT   }
{ ALL. So a query scoped to part 3 was answered about whatever part happened  }
{ to be displayed, reporting success, which is the exact defect the walk was  }
{ added to stop. A guard that passes in precisely the case it exists to catch }
{ is worse than no guard, because the caller stops looking.                   }
{                                                                             }
{ Target is always >= 2 here: the caller only steps when a part was named,    }
{ and part 1 is where selecting the component already leaves the editor.      }
{ That matters, because the step cannot REACH part 1 (CurrentPartID clamps    }
{ at 1, so stepping from it goes to 2) and SCH:PrevComponentPart does not     }
{ exist; Altium accepts the unknown process name and does nothing.            }
{                                                                             }
{ VERIFIED TWO WAYS, and it now fails closed. The document's part id is still }
{ preferred. When it cannot be read, CurrentPartID is read back INSTEAD, and  }
{ that readback is meaningful only because of the order above: we parked it   }
{ at Target - 1 ourselves, so if the step did nothing it still reads          }
{ Target - 1, and only a step that actually moved makes it read Target. That  }
{ is why the property is trusted here and nowhere else, and why the old note  }
{ against reading it does not apply: it is not being asked what is displayed, }
{ it is being asked whether the command ran.                                  }
Function StepLibComponentPartTo(SchLib : ISch_Lib; Component : ISch_Component;
    Target : Integer) : Boolean;
Var
    Count, Seen, Parked : Integer;
Begin
    Result := False;

    Count := 1;
    Try Count := Component.PartCount; Except End;
    If Count < 1 Then Count := 1;

    { A symbol with one part has nowhere to go and nothing to verify. }
    If (Count <= 1) And (Target <= 1) Then
    Begin
        Result := True;
        Exit;
    End;

    Parked := Target - 1;
    If Parked < 1 Then Parked := 1;
    Try Component.CurrentPartID := Parked; Except End;

    ResetParameters;
    RunProcess('SCH:NextComponentPart');

    Seen := CurrentLibPartId(SchLib);
    If Seen >= 0 Then
    Begin
        Result := (Seen = Target);
        Exit;
    End;

    { Document silent. Did the command move the property off where we put it? }
    Seen := -1;
    Try Seen := Component.CurrentPartID; Except End;
    Result := (Seen = Target);
End;

{ DisplayedPartByPins - which part the SchLib editor is showing, judged by    }
{ the pins the iterator actually yields.                                       }
{                                                                              }
{ This is the SAME iterator a lib_component query answers from, so it tests   }
{ exactly what the caller is about to receive rather than a proxy for it.    }
{ Needed because the document's own part id is declared but returns -1 on    }
{ AD 26.8.1.31, which is the build GH #11 reported from.                     }
{                                                                              }
{ Returns the single OwnerPartId seen on a part-specific pin, 0 when only    }
{ shared (OwnerPartId 0) pins or no pins are visible, and -1 when pins from  }
{ more than one part appear, which should not happen and is not trusted.     }
Function DisplayedPartByPins(SchLib : ISch_Lib) : Integer;
Var
    Iter : ISch_Iterator;
    Pin : ISch_Pin;
    Owner, Seen : Integer;
Begin
    Result := 0;
    Seen := 0;
    Iter := SchLib.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePin));
        Pin := Iter.FirstSchObject;
        While Pin <> Nil Do
        Begin
            Owner := 0;
            Try Owner := Pin.OwnerPartId; Except End;
            If Owner > 0 Then
            Begin
                If Seen = 0 Then
                Begin
                    Seen := Owner;
                End
                Else If Seen <> Owner Then
                Begin
                    Seen := -1;
                End;
            End;
            Pin := Iter.NextSchObject;
        End;
    Finally
        SchLib.SchIterator_Destroy(Iter);
    End;
    Result := Seen;
End;

{ PartOneEvidence - 1 when the editor provably shows part 1, -1 when it       }
{ provably shows some other part, 0 when nothing present can tell.            }
{                                                                              }
{ PINS ONLY. This used to ask the document's own part id first, and that is   }
{ not evidence here: ReachLibPartOne assigns CurrentPartID := 1 just before   }
{ asking. Live on AD 26.10.1.6 (2026-09-23), @1 was accepted while the editor }
{ still showed part 3 and the pins this iterator yields were part 3's; the    }
{ document's answer was the only thing that could have passed it.            }
Function PartOneEvidence(SchLib : ISch_Lib) : Integer;
Var
    Seen : Integer;
Begin
    Seen := DisplayedPartByPins(SchLib);
    If Seen = 1 Then
    Begin
        Result := 1;
    End
    Else If Seen = 0 Then
    Begin
        Result := 0;
    End
    Else
    Begin
        Result := -1;
    End;
End;

{ OtherLibComponentName - the name of any component in this library other     }
{ than Name, or '' when there is none.                                        }
{                                                                              }
{ CreateLibCompInfoReader, not SchIterator: an eSchComponent iterator returns }
{ nothing at all on a SchLib, because each symbol is its own internal sheet   }
{ rather than a component placed on the library's canvas.                    }
Function OtherLibComponentName(SchLib : ISch_Lib; Name : String) : String;
Var
    Reader : ILibCompInfoReader;
    Info : IComponentInfo;
    I, N : Integer;
Begin
    Result := '';
    Reader := Nil;
    Try Reader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(SchLib.DocumentName)); Except End;
    If Reader = Nil Then Exit;
    Try
        Try Reader.ReadAllComponentInfo; Except End;
        N := 0;
        Try N := Reader.NumComponentInfos; Except End;
        For I := 0 To N - 1 Do
        Begin
            Info := Reader.ComponentInfos[I];
            If Info <> Nil Then
            Begin
                If Info.CompName <> Name Then
                Begin
                    Result := Info.CompName;
                    Break;
                End;
            End;
        End;
    Finally
        Try SchServer.DestroyCompInfoReader(Reader); Except End;
    End;
End;

{ ReachLibPartOne - make part 1 the displayed part, and prove it.              }
{                                                                              }
{ Part 1 cannot be reached by stepping: SCH:NextComponentPart moves to       }
{ CurrentPartID + 1, CurrentPartID clamps at 1, so a step from it lands on 2, }
{ and SCH:PrevComponentPart does not exist. What DOES reset the display to    }
{ part 1 is selecting a DIFFERENT component and then reselecting this one;    }
{ reassigning the same component leaves the display where it was. Measured   }
{ 2026-09-19 on a purpose-built 4-part symbol, and confirmed independently    }
{ in GH #11 on a 4-part part.                                                 }
{                                                                              }
{ The bounce is only done when part 1 is not already provably showing, so a  }
{ lookup that is already right moves nothing.                                }
{                                                                              }
{ After the bounce, success means no pin from another part is visible. That  }
{ is the property that matters: the #11 defect was answering about part 4    }
{ when part 1 was asked for, and a query cannot do that while the iterator   }
{ shows nothing from part 4. A part 1 carrying only shared pins reads as     }
{ "nothing can tell", which after the measured reset is accepted.            }
{                                                                              }
{ A LIBRARY WITH ONE COMPONENT HAS NOTHING TO BOUNCE OFF. There is then no    }
{ known way back to part 1 once the display has left it, and this says so    }
{ rather than answering about whichever part is showing.                     }
Function ReachLibPartOne(SchLib : ISch_Lib; Component : ISch_Component;
    Name : String) : Boolean;
Var
    Count, Evidence : Integer;
    OtherName : String;
    Other : ISch_Component;
Begin
    Result := False;

    Count := 1;
    Try Count := Component.PartCount; Except End;
    If Count <= 1 Then
    Begin
        Result := True;
        Exit;
    End;

    If PartOneEvidence(SchLib) = 1 Then
    Begin
        Result := True;
        Exit;
    End;

    OtherName := OtherLibComponentName(SchLib, Name);
    If OtherName = '' Then
    Begin
        { "Saved" is deliberate: candidates are read from the file on disk,
          so a component created this session and not yet saved is not one. }
        NoteNextStep('Part 1 of ' + Name + ' cannot be reached: the display '
            + 'is on another part, and the saved library holds no other '
            + 'component to reselect from, which is the only known way back '
            + 'to part 1. A component created this session counts once the '
            + 'library is saved. Otherwise select part 1 by hand in the '
            + 'library editor.');
        Exit;
    End;

    Other := Nil;
    { Through the wrapper, never the raw index: the index only knows   }
    { what the library was LOADED with. The name came from the file on  }
    { disk, so this resolves at the wrapper's first step and never     }
    { reaches its close-and-reopen last resort.                         }
    Other := LookupLibComponent(SchLib, OtherName);
    If Other = Nil Then Exit;

    { The editor acts on a selection when it processes its messages, not  }
    { when the property is assigned. The measured reset was three separate }
    { calls with the UI running between them; done back to back inside one }
    { handler, the display stayed on part 3 (live, 2026-09-23).            }
    Try SchLib.CurrentSchComponent := Other; Except End;
    Try Application.ProcessMessages; Except End;
    Try SchLib.CurrentSchComponent := Component; Except End;
    Try Application.ProcessMessages; Except End;
    Try Component.CurrentPartID := 1; Except End;

    Evidence := PartOneEvidence(SchLib);
    Result := (Evidence >= 0);
    If Not Result Then
        NoteNextStep('Part 1 of ' + Name + ' was not reached: after '
            + 'reselecting the component the editor still shows pins from '
            + 'another part.');
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

    Component := LookupLibComponent(SchLib, Name);
    If Component = Nil Then Exit;

    SchLib.CurrentSchComponent := Component;
    LastCreatedLibComponent := Component;
    LastCreatedLibComponentName := Name;

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
    { property. StepLibComponentPartTo parks CurrentPartID one below the      }
    { target and issues one SCH:NextComponentPart, which lands on the target  }
    { and syncs the property to it; see the note on that function for why it  }
    { no longer walks, and for the readback that used to fail open.           }
    { NIL RATHER THAN THE WRONG PART. Returning the component when the
      editor never reached the requested part is exactly what GH #11
      reported: a query scoped to part 3 answered about part 1 and
      nothing said so. Nil makes the scope resolve to NOT_FOUND, which
      is a caller can act on. }
    {
      ONLY WHEN A PART WAS ACTUALLY ASKED FOR. This gate used to read
      `If Target > 0`, and Target is never below 1, so the step-and-verify
      ran on EVERY lookup including the plain by-name one. When the editor
      reported a part id that was readable but not 1, the walk failed and
      this returned Nil, so a component that demonstrably existed came back
      as "not found".

      MEASURED: lib_link_footprint and lib_batch_rename both refused
      SWEEP_SYM_A and SWEEP_SYM_C while lib_get_component_details and
      lib_get_pin_list resolved the same names in the same session, because
      those two go straight to GetState_SchComponentByLibRef and never step.

      The GH #11 protection is about a caller asking for part 3 and being
      answered about part 1. That only arises when a part was named, which
      is what PartId > 1 means. A single-part lookup has nothing to verify.
    }
    If PartId > 1 Then
    Begin
        If Not StepLibComponentPartTo(SchLib, Component, Target) Then
        Begin
            { THE DISPLAY HAS MOVED EVEN THOUGH THE TARGET WAS NOT REACHED.
              Stepping happens before the check, so a part that cannot be
              reached still leaves the editor somewhere other than where it
              started, and this build cannot read back where that is. Left
              unsaid it compounds badly: the caller does not know which part
              is showing, and scope @1 is the one suffix that cannot be
              trusted to return to part 1. Reported GH #11, 2026-09-22. }
            { Put the editor back on part 1 rather than leave it on a part
              nobody can name (issue #88: @2 on a one-part symbol moved the
              display and failed). ReachLibPartOne bounces through another
              component, the one route that verifiably lands on part 1. }
            If ReachLibPartOne(SchLib, Component, Name) Then
                NoteNextStep('Part ' + IntToStr(Target) + ' was not reached '
                    + '(the symbol may have fewer parts); the editor is back '
                    + 'on part 1.')
            Else
                NoteNextStep('Part ' + IntToStr(Target) + ' was not reached, and '
                    + 'the displayed part has moved. Select a different '
                    + 'component and reselect this one to return to part 1; a '
                    + 'suffixed scope cannot reliably do it.');
            Result := Nil;
            Exit;
        End;
    End;

    { EXPLICIT PART 1, and only explicit. PartId 0 is the plain lookup with no
      suffix, which every lib_ tool reaches through SelectLibComponent and
      which must stay exactly as it was: when the step-and-verify once ran on
      every lookup, lib_link_footprint and lib_batch_rename refused
      components that demonstrably existed. PartId 1 now means "@1 was
      written", and that is a request for part 1 that has to be honoured
      rather than answered about whichever part happens to be displayed.
      Reported GH #11, 2026-09-22: with the editor on part 3, @1 returned
      part 3's pins. }
    If PartId = 1 Then
    Begin
        If Not ReachLibPartOne(SchLib, Component, Name) Then
        Begin
            Result := Nil;
            Exit;
        End;
    End;

    Try SchLib.GraphicallyInvalidate; Except End;
    Result := Component;
End;

Function SelectLibComponent(Name : String) : ISch_Component;
Begin
    { 0, NOT 1. Zero is the plain lookup and takes the historical   }
    { path unchanged; 1 now means an explicit @1 and is verified.  }
    Result := SelectLibComponentPart(Name, 0);
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
    Hidden, ViaLib : Boolean;
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
        ViaLib := AddLibSymbolPrim(SchLib, Component, Pin);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"'
            + EscapeJsonString(Designator) + '","shown":' + BoolToJsonStr(ViaLib) + '}');
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
    ViaLib : Boolean;
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
        SetOwnerPartFrom(Rect, Component, ExtractJsonValue(Params, 'owner_part_id'));
        ViaLib := AddLibSymbolPrim(SchLib, Component, Rect);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"shown":' + BoolToJsonStr(ViaLib) + '}');
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
    ViaLib : Boolean;
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
        ViaLib := AddLibSymbolPrim(SchLib, Component, Line);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"shown":' + BoolToJsonStr(ViaLib) + '}');
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
        MarkDocDirtyByPath(PcbLib.Board.FileName);

        Result := BuildSuccessResponse(RequestId, '{"success":true,"name":"' + EscapeJsonString(Name) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create footprint');
End;

{ FootprintOriginX / Y - the footprint's own origin, in board coordinates.    }
{                                                                             }
{ EVERY AUTHORING CALL HERE MUST OFFSET BY THIS. A PcbLib footprint does not  }
{ sit at the board coordinate origin: PCBServer.CreatePCBLibComp leaves it at }
{ Altium's library origin, measured at 50000,50000 mils on AD26. The add      }
{ handlers used to write MilsToCoord(X) straight into Pad.X, which is an      }
{ ABSOLUTE board coordinate, so a pad asked for at -50 mils landed 50050 mils }
{ from the footprint it belonged to.                                          }
{                                                                             }
{ MEASURED both ways. A footprint authored by these tools reported its pads   }
{ at x_mm -1271.27 where -1.27 was asked for, and placing it on a board gave  }
{ a component with a 52452 x 50144 mil bounding box, a part over four feet    }
{ across. A hand-authored footprint from a real library reads 0.0 and 2.54,   }
{ which is what relative-to-origin looks like and what the tools claim to     }
{ take.                                                                       }
{                                                                             }
{ Reading the origin rather than forcing it to zero also fixes footprints     }
{ that were imported with an origin of their own.                             }
Function FootprintOriginX(Footprint : IPCB_LibComponent) : TCoord;
Begin
    Result := 0;
    If Footprint = Nil Then Exit;
    Try Result := Footprint.X; Except End;
End;

Function FootprintOriginY(Footprint : IPCB_LibComponent) : TCoord;
Begin
    Result := 0;
    If Footprint = Nil Then Exit;
    Try Result := Footprint.Y; Except End;
End;

{ PcbLibTarget - the footprint an authoring call writes into. With no name,  }
{ the editor's current footprint, as before. With a name, that footprint,     }
{ made current and checked by name: Board.AddPCBObject attaches to the        }
{ CURRENT footprint whatever the caller meant, and nothing else moves the     }
{ editor, so silkscreen meant for one footprint landed in whichever another   }
{ call had left current. Problem is '' on success.                           }
Function PcbLibTarget(PcbLib : IPCB_Library; Name : String; Var Problem : String) : IPCB_LibComponent;
Var
    Cur : IPCB_LibComponent;
    CurName : String;
Begin
    Problem := '';
    Result := Nil;
    If Name = '' Then
    Begin
        Result := PcbLib.CurrentComponent;
        If Result = Nil Then
            Problem := 'No footprint is selected; pass footprint_name';
        Exit;
    End;
    Try Result := PcbLib.GetComponentByName(Name); Except Result := Nil; End;
    If Result = Nil Then
    Begin
        Problem := 'Footprint not found in the library: ' + Name;
        Exit;
    End;
    Try PcbLib.CurrentComponent := Result; Except End;
    CurName := '';
    Try
        Cur := PcbLib.CurrentComponent;
        If Cur <> Nil Then CurName := Cur.Name;
    Except End;
    If CurName <> Name Then
    Begin
        Problem := 'Could not make ' + Name + ' the current footprint (the editor shows '
            + CurName + '), so nothing was written';
        Result := Nil;
    End;
End;

{ A rounded-rectangle pad. A through-hole pad sits on MultiLayer, which the
  per-layer setters ignore: it came out round with corner_pct 0. Its shape
  and percentage go on the top, middle and bottom layers instead. Altium
  stores the radius only as a percentage of half the shortest side. }
Procedure SetPadRoundRect(Pad : IPCB_Pad; Pct : Integer);
Begin
    If Pad.Layer = eMultiLayer Then
    Begin
        Pad.TopShape := eRoundedRectangular;
        Pad.SetState_StackShapeOnLayer(eTopLayer, eRoundedRectangular);
        Pad.SetState_StackCRPctOnLayer(eTopLayer, Pct);
        Pad.SetState_StackShapeOnLayer(eMidLayers, eRoundedRectangular);
        Pad.SetState_StackCRPctOnLayer(eMidLayers, Pct);
        Pad.SetState_StackShapeOnLayer(eBottomLayer, eRoundedRectangular);
        Pad.SetState_StackCRPctOnLayer(eBottomLayer, Pct);
    End
    Else
    Begin
        Pad.SetState_StackShapeOnLayer(Pad.Layer, eRoundedRectangular);
        Pad.SetState_StackCRPctOnLayer(Pad.Layer, Pct);
    End;
End;

Function Lib_AddFootprintPad(Params : String; RequestId : String) : String;
Var
    Designator, Shape, LayerStr : String;
    CornerRadius : Integer;
    X, Y, XSize, YSize, HoleSize : Double;
    UnitsStr : String;
    Rotation : Double;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    TargetProblem : String;
    Pad : IPCB_Pad;
    PadLayer : TLayer;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    Designator := ExtractJsonValue(Params, 'designator');
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    XSize := StrToFloatDef(ExtractJsonValue(Params, 'x_size'), MilsInUnits(60, UnitsStr));
    YSize := StrToFloatDef(ExtractJsonValue(Params, 'y_size'), MilsInUnits(60, UnitsStr));
    HoleSize := StrToFloatDef(ExtractJsonValue(Params, 'hole_size'), 0);
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

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
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
        Pad.X := FootprintOriginX(Footprint) + CoordFromUnits(X, UnitsStr);
        Pad.Y := FootprintOriginY(Footprint) + CoordFromUnits(Y, UnitsStr);
        Pad.TopXSize := CoordFromUnits(XSize, UnitsStr);
        Pad.TopYSize := CoordFromUnits(YSize, UnitsStr);
        Pad.HoleSize := CoordFromUnits(HoleSize, UnitsStr);
        Pad.Rotation := Rotation;

        Pad.Layer := PadLayer;

        { Shape. roundrect = the modern IPC default: set the layer-stack shape }
        { then the corner-radius percentage (Altium stores RR radius as a %).  }
        If Shape = 'rectangular' Then Pad.TopShape := eRectangular
        Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
        Else If (Shape = 'roundrect') Or (Shape = 'rounded_rectangular') Then
        Begin
            SetPadRoundRect(Pad, CornerRadius);
        End
        Else Pad.TopShape := eRounded;

        Footprint.AddPCBObject(Pad);
        { The primitive must be registered with the library's backing Board as
          well as with the footprint, and the registration broadcast to both.
          Footprint.AddPCBObject alone is not enough: the object exists in the
          working copy, every read reports it, and the save discards it. That
          cost an afternoon on an LCSC import, where pads and tracks reported
          success and the footprint came back empty, and it was read as Altium
          refusing to author land patterns at all. Lib_AddFootprintText had the
          full sequence and was the only authoring call that survived a save. }
        PcbLib.Board.AddPCBObject(Pad);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);

        Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"' + EscapeJsonString(Designator) + '"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create pad');

    PCBServer.PostProcess;
    MarkDocDirtyByPath(PcbLib.Board.FileName);
End;

{ Why the focused PCB library is not the one the caller named, or '' when it }
{ is (or when no path was given). PCBServer only hands out the focused       }
{ library, so a tool that takes a path must refuse rather than read or write }
{ some other open library.                                                  }
Function PcbLibPathProblem(PcbLib : IPCB_Library; LibPath : String) : String;
Var
    Focused : String;
Begin
    Result := '';
    If LibPath = '' Then Exit;
    Focused := '';
    Try Focused := PcbLib.Board.FileName; Except End;
    If UpperCase(StringReplace(Focused, '/', '\', -1))
        <> UpperCase(StringReplace(LibPath, '/', '\', -1)) Then
        Result := 'The focused PCB library is ' + Focused + ', not ' + LibPath
            + '. Focus the library you named and retry; nothing was changed.';
End;

{ Modify one via inside a footprint (issue #84). The via is addressed by its }
{ footprint-relative position: none within 1 mil is an error, and so is more }
{ than one. Every field is optional. Writes run in the order the reporter    }
{ measured: drill, stack mode, sizes, tenting, then the mask through the     }
{ pad cache, which the via interface does not reach. Each written field is   }
{ read back, and one that did not take clears all_writes_ran. Tenting only  }
{ sticks on a manual mask (AD 26.11), so it switches a rule mask to manual. }
{ Params: x, y (required), library_path (required), footprint_name,          }
{ units, hole_size, size, dia_mid, dia_bottom, tenting_top, tenting_bottom,  }
{ mask_expansion, mask_bottom_expansion, mask_separate.                      }
Function Lib_SetViaProperties(Params : String; RequestId : String) : String;
Var
    LibPath, UnitsStr, TargetProblem, Addr, HoleStr, SizeStr, MidStr, BotStr : String;
    TentTopStr, TentBotStr, MaskStr, MaskBotStr, MaskSepStr, XStr, YStr : String;
    ServerDoc : IServerDocument;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Found, Via : IPCB_Via;
    FoundPad : IPCB_Pad;
    PadCache : TPadCache;
    TargetX, TargetY, Tol, WantC : TCoord;
    Matches, Seen : Integer;
    SeenList : String;
    MadeManual, Ok : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    UnitsStr := ExtractJsonValue(Params, 'units');
    If (LibPath = '') Or (XStr = '') Or (YStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'library_path, x and y are required');
        Exit;
    End;
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    ServerDoc := Client.GetDocumentByPath(LibPath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_OPEN',
            'Open the PCB library in Altium first: the PCB server loads with it.');
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;
    If PcbLibPathProblem(PcbLib, LibPath) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY', PcbLibPathProblem(PcbLib, LibPath));
        Exit;
    End;
    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
        Exit;
    End;

    TargetX := FootprintOriginX(Footprint) + CoordFromUnits(StrToFloatDef(XStr, 0), UnitsStr);
    TargetY := FootprintOriginY(Footprint) + CoordFromUnits(StrToFloatDef(YStr, 0), UnitsStr);
    { 1 mil whatever the call's units: the docstring promises 1 mil, and
      1 mm would reach a neighbouring via. }
    Tol := MilsToCoord(1);

    { Find the via. The same object can be listed twice, so matches are   }
    { counted by address.                                                  }
    Found := Nil;
    Matches := 0;
    SeenList := #9;
    GrpIter := Footprint.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(eViaObject));
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Via := Prim;
            Addr := IntToStr(Prim.I_ObjectAddress);
            If (Pos(#9 + Addr + #9, SeenList) = 0)
                And (Abs(Via.X - TargetX) <= Tol) And (Abs(Via.Y - TargetY) <= Tol) Then
            Begin
                SeenList := SeenList + Addr + #9;
                Matches := Matches + 1;
                Found := Via;
            End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
    End;
    If Matches = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'No via at x=' + XStr + ', y=' + YStr + ' (within 1 mil)');
        Exit;
    End;
    If Matches > 1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'AMBIGUOUS',
            IntToStr(Matches) + ' vias sit at that position; nothing was changed');
        Exit;
    End;

    HoleStr := ExtractJsonValue(Params, 'hole_size');
    SizeStr := ExtractJsonValue(Params, 'size');
    MidStr := ExtractJsonValue(Params, 'dia_mid');
    BotStr := ExtractJsonValue(Params, 'dia_bottom');
    TentTopStr := LowerCase(ExtractJsonValue(Params, 'tenting_top'));
    TentBotStr := LowerCase(ExtractJsonValue(Params, 'tenting_bottom'));
    MaskStr := ExtractJsonValue(Params, 'mask_expansion');
    MaskBotStr := ExtractJsonValue(Params, 'mask_bottom_expansion');
    MaskSepStr := LowerCase(ExtractJsonValue(Params, 'mask_separate'));
    Ok := True;
    MadeManual := False;

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Found.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
        { 1. Drill. }
        If HoleStr <> '' Then
        Begin
            Try
                WantC := CoordFromUnits(StrToFloatDef(HoleStr, 0), UnitsStr);
                Found.HoleSize := WantC;
                If Abs(Found.HoleSize - WantC) > Tol Then Ok := False;
            Except Ok := False; End;
        End;
        { 2. Stack mode. A simple via keeps no middle size, so a middle or
          bottom diameter raises it first. }
        If (MidStr <> '') Or (BotStr <> '') Then
        Begin
            Try Found.Mode := ePadMode_LocalStack;
            Except Ok := False; End;
        End;
        { 3. Sizes, each read back. A simple via has one size, written as
          Size; a local-stack via takes it per layer. }
        If SizeStr <> '' Then
        Begin
            Try
                WantC := CoordFromUnits(StrToFloatDef(SizeStr, 0), UnitsStr);
                If Found.Mode = ePadMode_Simple Then
                    Found.Size := WantC
                Else
                    Found.SizeOnLayer(eTopLayer) := WantC;
                If Abs(Found.SizeOnLayer(eTopLayer) - WantC) > Tol Then Ok := False;
            Except Ok := False; End;
        End;
        If MidStr <> '' Then
        Begin
            Try
                WantC := CoordFromUnits(StrToFloatDef(MidStr, 0), UnitsStr);
                Found.SizeOnLayer(eMidLayers) := WantC;
                If Abs(Found.SizeOnLayer(eMidLayers) - WantC) > Tol Then Ok := False;
            Except Ok := False; End;
        End;
        If BotStr <> '' Then
        Begin
            Try
                WantC := CoordFromUnits(StrToFloatDef(BotStr, 0), UnitsStr);
                Found.SizeOnLayer(eBottomLayer) := WantC;
                If Abs(Found.SizeOnLayer(eBottomLayer) - WantC) > Tol Then Ok := False;
            Except Ok := False; End;
        End;
        { 4. Tenting. MEASURED (AD 26.11): the write is kept only while the
          via's mask expansion is manual. On a rule-driven via it is accepted
          and dropped, linked or unlinked. So a rule-driven mask is switched
          to manual first, at the value it already holds, and the response
          says so in mask_made_manual. The read-back still decides Ok. }
        If (TentTopStr = 'true') Or (TentTopStr = 'false')
            Or (TentBotStr = 'true') Or (TentBotStr = 'false') Then
        Begin
            Try
                FoundPad := Found;
                PadCache := FoundPad.GetState_Cache;
                If PadCache.SolderMaskExpansionValid <> eCacheManual Then
                Begin
                    PadCache.SolderMaskExpansionValid := eCacheManual;
                    FoundPad.SetState_Cache := PadCache;
                    MadeManual := True;
                End;
            Except Ok := False; End;
            If (TentTopStr = 'true') Or (TentTopStr = 'false') Then
            Begin
                Try
                    Found.SetState_IsTenting_Top(TentTopStr = 'true');
                    If Found.GetState_IsTenting_Top <> (TentTopStr = 'true') Then Ok := False;
                Except Ok := False; End;
            End;
            If (TentBotStr = 'true') Or (TentBotStr = 'false') Then
            Begin
                Try
                    Found.SetState_IsTenting_Bottom(TentBotStr = 'true');
                    If Found.GetState_IsTenting_Bottom <> (TentBotStr = 'true') Then Ok := False;
                Except Ok := False; End;
            End;
        End;
        { 5. Mask, through the pad cache. }
        If (MaskStr <> '') Or (MaskBotStr <> '') Or (MaskSepStr = 'true') Or (MaskSepStr = 'false') Then
        Begin
            Try
                FoundPad := Found;
                PadCache := FoundPad.GetState_Cache;
                If MaskStr <> '' Then
                Begin
                    PadCache.SolderMaskExpansionValid := eCacheManual;
                    PadCache.SolderMaskExpansion := CoordFromUnits(StrToFloatDef(MaskStr, 0), UnitsStr);
                End;
                If MaskBotStr <> '' Then
                    PadCache.SolderMaskBottomExpansion := CoordFromUnits(StrToFloatDef(MaskBotStr, 0), UnitsStr);
                If (MaskSepStr = 'true') Or (MaskSepStr = 'false') Then
                    PadCache.UseSeparateExpansions := (MaskSepStr = 'true');
                FoundPad.SetState_Cache := PadCache;
                { Read the cache back: a value it did not keep is a failed
                  write, not a success. }
                PadCache := FoundPad.GetState_Cache;
                If MaskStr <> '' Then
                    If Abs(PadCache.SolderMaskExpansion
                        - CoordFromUnits(StrToFloatDef(MaskStr, 0), UnitsStr)) > Tol Then Ok := False;
                If MaskBotStr <> '' Then
                    If Abs(PadCache.SolderMaskBottomExpansion
                        - CoordFromUnits(StrToFloatDef(MaskBotStr, 0), UnitsStr)) > Tol Then Ok := False;
                If (MaskSepStr = 'true') Or (MaskSepStr = 'false') Then
                    If PadCache.UseSeparateExpansions <> (MaskSepStr = 'true') Then Ok := False;
            Except Ok := False; End;
        End;
        PCBServer.SendMessageToRobots(Found.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
    End;
    MarkDocDirtyByPath(PcbLib.Board.FileName);

    { Read everything back, so the caller sees what the via now is and not
      only that the writes ran. Each read is guarded: a field this build
      cannot read comes back null. }
    TentTopStr := 'null';
    Try TentTopStr := BoolToJsonStr(Found.IsTenting_Top); Except End;
    TentBotStr := 'null';
    Try TentBotStr := BoolToJsonStr(Found.IsTenting_Bottom); Except End;
    MaskStr := 'null';
    MaskBotStr := 'null';
    MaskSepStr := 'null';
    Try
        FoundPad := Found;
        PadCache := FoundPad.GetState_Cache;
        MaskStr := FloatToJsonStr(CoordToMM(PadCache.SolderMaskExpansion));
        MaskBotStr := FloatToJsonStr(CoordToMM(PadCache.SolderMaskBottomExpansion));
        MaskSepStr := BoolToJsonStr(PadCache.UseSeparateExpansions);
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"all_writes_ran":' + BoolToJsonStr(Ok)
        + ',"hole_mm":' + FloatToJsonStr(CoordToMM(Found.HoleSize))
        + ',"size_mm":' + FloatToJsonStr(CoordToMM(Found.Size))
        + ',"tenting_top":' + TentTopStr
        + ',"tenting_bottom":' + TentBotStr
        + ',"mask_expansion_mm":' + MaskStr
        + ',"mask_bottom_expansion_mm":' + MaskBotStr
        + ',"mask_separate":' + MaskSepStr
        + ',"mask_made_manual":' + BoolToJsonStr(MadeManual) + '}');
End;

{ Objects per layer across every footprint of a PcbLib (issue #81). Read     }
{ only. Each footprint is walked with a FILTERED group iterator, and each    }
{ object is counted once by address, since a footprint can list one twice.  }
{ Params: library_path (required, the open .PcbLib).                         }
Function Lib_CountLayerObjects(Params : String; RequestId : String) : String;
Var
    LibPath, Addr, Seen, Key, Done, OutJson : String;
    ServerDoc : IServerDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    GrpIter : IPCB_GroupIterator;
    Footprint : IPCB_LibComponent;
    Prim : IPCB_Primitive;
    Lines : TStringList;
    I, J, Count, Footprints, Objects : Integer;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library_path is required');
        Exit;
    End;
    { The same guard as the origin read: the PCB server is not loaded until a
      library is open, and touching it before that is a modal. }
    ServerDoc := Client.GetDocumentByPath(LibPath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_OPEN',
            'Open the PCB library in Altium first: the PCB server loads with it.');
        Exit;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;
    If PcbLibPathProblem(PcbLib, LibPath) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY', PcbLibPathProblem(PcbLib, LibPath));
        Exit;
    End;

    Lines := TStringList.Create;
    Footprints := 0;
    Objects := 0;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            Footprints := Footprints + 1;
            Seen := #9;
            GrpIter := Footprint.GroupIterator_Create;
            Try
                GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
                    eTextObject, eRegionObject, eFillObject, eComponentBodyObject,
                    ePadObject, eViaObject, ePolyObject, eDimensionObject));
                Prim := GrpIter.FirstPCBObject;
                While Prim <> Nil Do
                Begin
                    Addr := IntToStr(Prim.I_ObjectAddress);
                    If Pos(#9 + Addr + #9, Seen) = 0 Then
                    Begin
                        Seen := Seen + Addr + #9;
                        Lines.Add(GetLayerString(Prim.Layer) + #9);
                        Objects := Objects + 1;
                    End;
                    Prim := GrpIter.NextPCBObject;
                End;
            Finally
                Footprint.GroupIterator_Destroy(GrpIter);
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;

    { Tally by layer name: each name once, counted over the lines. }
    Done := #9;
    OutJson := '';
    For I := 0 To Lines.Count - 1 Do
    Begin
        Key := Copy(Lines.Strings[I], 1, Pos(#9, Lines.Strings[I]) - 1);
        If Pos(#9 + Key + #9, Done) = 0 Then
        Begin
            Done := Done + Key + #9;
            Count := 0;
            For J := 0 To Lines.Count - 1 Do
                If Copy(Lines.Strings[J], 1, Length(Key) + 1) = Key + #9 Then
                    Count := Count + 1;
            If OutJson <> '' Then OutJson := OutJson + ',';
            OutJson := OutJson + '{"layer":"' + EscapeJsonString(Key) + '","count":' + IntToStr(Count) + '}';
        End;
    End;
    Try Lines.Free; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"layers":[' + OutJson + '],"footprints":' + IntToStr(Footprints)
        + ',"objects":' + IntToStr(Objects) + '}');
End;

{ The pad paste on/off flag (IsTopPasteEnabled) is measured on Altium       }
{ Designer 26. On AD 23.2 it is an undeclared identifier, which a Try does  }
{ not catch: the script halts in the debugger. Gate on the major version,   }
{ the way via templates are gated in PCB.pas; lower the bound once the flag }
{ is measured on 24 or 25.                                                  }
Function PadPasteFlagSupported(Dummy : Integer) : Boolean;
Var
    Ver : String;
    Major : Integer;
Begin
    Ver := '';
    Try Ver := Client.GetProductVersion; Except Ver := ''; End;
    Major := 0;
    If Pos('.', Ver) > 1 Then Major := StrToIntDef(Copy(Ver, 1, Pos('.', Ver) - 1), 0);
    Result := Major >= 26;
End;

{ A pad's top paste opening flag, as JSON: true, false, or null when the     }
{ flag cannot be read. The expansion is a separate control (issue #80).     }
Function PadPasteTopJson(Pad : IPCB_Pad) : String;
Begin
    Result := 'null';
    If Not PadPasteFlagSupported(0) Then Exit;
    Try
        If Pad.GetState_IsTopPasteEnabled Then Result := 'true'
        Else Result := 'false';
    Except
        Result := 'null';
    End;
End;

{ The PcbLib editor's origin marker (issue #83). Read off the library's      }
{ backing board, the same board the footprints are written into. Document    }
{ info does not cover a PcbLib, which has no schematic and no PCB board.     }
Function Lib_GetLibraryOrigin(Params : String; RequestId : String) : String;
Var
    PcbLib : IPCB_Library;
    LibPath : String;
    ServerDoc : IServerDocument;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library_path is required');
        Exit;
    End;
    { Touching PCBServer before any PCB document is open is an undeclared
      identifier modal (measured, PCB.pas). A library that is open has its
      document, so check that first and refuse honestly when it is not. }
    ServerDoc := Client.GetDocumentByPath(LibPath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_OPEN',
            'Open the PCB library in Altium first: the PCB server loads with it.');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;
    If PcbLibPathProblem(PcbLib, LibPath) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY', PcbLibPathProblem(PcbLib, LibPath));
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"origin_x_mil":' + IntToStr(CoordToMils(PcbLib.Board.XOrigin)) +
        ',"origin_y_mil":' + IntToStr(CoordToMils(PcbLib.Board.YOrigin)) +
        ',"library_path":"' + EscapeJsonString(PcbLib.Board.FileName) + '"}');
End;

{ Add a through-stack via to a footprint (issue #79). Same registration      }
{ sequence as Lib_AddFootprintPad, which the save depends on. Params: x, y   }
{ (footprint-relative, in the call's units), size (via diameter), hole_size, }
{ footprint_name (optional), library_path (optional: when given, the focused }
{ library must be that one), units. The via spans top to bottom.            }
Function Lib_AddFootprintVia(Params : String; RequestId : String) : String;
Var
    UnitsStr, TargetProblem, LibPath : String;
    X, Y, Size, HoleSize : Double;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    Via : IPCB_Via;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    Size := StrToFloatDef(ExtractJsonValue(Params, 'size'), MilsInUnits(40, UnitsStr));
    HoleSize := StrToFloatDef(ExtractJsonValue(Params, 'hole_size'), MilsInUnits(20, UnitsStr));
    If (Size <= 0) Or (HoleSize <= 0) Or (HoleSize >= Size) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_SIZE',
            'A via needs 0 < hole_size < size');
        Exit;
    End;

    LibPath := ExtractJsonValue(Params, 'library_path');
    If LibPath <> '' Then
    Begin
        If Client.GetDocumentByPath(LibPath) = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_OPEN',
                'Open the PCB library in Altium first: the PCB server loads with it.');
            Exit;
        End;
    End;
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;
    If PcbLibPathProblem(PcbLib, LibPath) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_LIBRARY', PcbLibPathProblem(PcbLib, LibPath));
        Exit;
    End;

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
        Exit;
    End;

    PCBServer.PreProcess;

    Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
    If Via <> Nil Then
    Begin
        Via.X := FootprintOriginX(Footprint) + CoordFromUnits(X, UnitsStr);
        Via.Y := FootprintOriginY(Footprint) + CoordFromUnits(Y, UnitsStr);
        Via.Size := CoordFromUnits(Size, UnitsStr);
        Via.HoleSize := CoordFromUnits(HoleSize, UnitsStr);
        Via.LowLayer := eTopLayer;
        Via.HighLayer := eBottomLayer;

        Footprint.AddPCBObject(Via);
        { Same registration as the pad path: the backing board must see the
          object too, or the save discards it. }
        PcbLib.Board.AddPCBObject(Via);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Via.I_ObjectAddress);
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Via.I_ObjectAddress);

        Result := BuildSuccessResponse(RequestId, '{"success":true,"kind":"via"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create via');

    PCBServer.PostProcess;
    MarkDocDirtyByPath(PcbLib.Board.FileName);
End;

{ Batch pad authoring: same shape as Lib_AddPins but for PCB pads. Receives a }
{ `pads` array encoded with the ~~ / ; / = separators NextBatchOp expects,    }
{ creates every pad inside ONE PreProcess / PostProcess pair and ONE save, so }
{ a 64-pad QFP costs one IPC round-trip instead of 64. Per-pad fields:        }
{ designator, x, y, x_size, y_size, hole_size, shape, rotation (mirrors the   }
{ singular Lib_AddFootprintPad field set / defaults exactly).                 }
{                                                                              }
{ Four OPTIONAL fields, each written only when the op carries it, so a payload }
{ without them builds exactly the pad it always did:                          }
{   hole_length      slot length; above hole_size it makes a slotted hole,    }
{                    HoleSize the slot width and HoleWidth its length, as     }
{                    Altium's own Hyperlynx exporter reads a slot             }
{   plated=false     a non-plated hole                                        }
{   paste_expansion  manual paste mask expansion, in the call's units         }
{   mask_expansion   manual solder mask expansion, in the call's units        }
{ The expansions go through the pad cache the way PCB_MakePasteGrid writes    }
{ paste; the solder mask half follows the API reference's own pad example.   }
{ Each write is guarded and counted, so a property this build refuses shows   }
{ up as property_failures instead of a pad that silently lacks it.            }
Function Lib_AddFootprintPads(Params : String; RequestId : String) : String;
Var
    PadsStr, Op, Remaining, Shape, LayerStr, BadLayers : String;
    OpCount, Added, Failed : Integer;
    CornerRadius : Integer;
    X, Y, XSize, YSize, HoleSize : Double;
    UnitsStr : String;
    Rotation : Double;
    PadLayer : TLayer;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    TargetProblem : String;
    Pad : IPCB_Pad;
    Cache : TPadCache;
    SlotLen : Double;
    PlatedStr, PasteStr, MaskStr : String;
    Slots, Unplated, Expanded, PropFailed : Integer;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
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

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
        Exit;
    End;

    Added := 0;
    Failed := 0;
    OpCount := 0;
    Slots := 0;
    Unplated := 0;
    Expanded := 0;
    PropFailed := 0;
    BadLayers := '';
    Remaining := PadsStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X := StrToFloatDef(GetBatchField(Op, 'x'), 0);
            Y := StrToFloatDef(GetBatchField(Op, 'y'), 0);
            XSize := StrToFloatDef(GetBatchField(Op, 'x_size'), MilsInUnits(60, UnitsStr));
            YSize := StrToFloatDef(GetBatchField(Op, 'y_size'), MilsInUnits(60, UnitsStr));
            HoleSize := StrToFloatDef(GetBatchField(Op, 'hole_size'), 0);
            Rotation := StrToFloatDef(GetBatchField(Op, 'rotation'), 0);
            Shape := GetBatchField(Op, 'shape');
            LayerStr := GetBatchField(Op, 'layer');
            CornerRadius := StrToIntDef(GetBatchField(Op, 'corner_radius'), 25);

            { Resolve the layer BEFORE creating anything. GetLayerFromString    }
            { answered eTopLayer for every name it did not know, so one         }
            { "Top Overlay" in a batch silently put that pad on top copper.     }
            If HoleSize > 0 Then PadLayer := eMultiLayer
            Else If LayerStr = '' Then PadLayer := eTopLayer
            Else PadLayer := ResolveLayerId(PcbLib.Board, LayerStr);
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
            Pad.X := FootprintOriginX(Footprint) + CoordFromUnits(X, UnitsStr);
            Pad.Y := FootprintOriginY(Footprint) + CoordFromUnits(Y, UnitsStr);
            Pad.TopXSize := CoordFromUnits(XSize, UnitsStr);
            Pad.TopYSize := CoordFromUnits(YSize, UnitsStr);
            Pad.HoleSize := CoordFromUnits(HoleSize, UnitsStr);
            Pad.Rotation := Rotation;

            { Layer FIRST (roundrect setters are layer-aware): drilled ->       }
            { through-hole (MultiLayer); hole-less -> SMD on a single layer.    }
            Pad.Layer := PadLayer;

            If Shape = 'rectangular' Then Pad.TopShape := eRectangular
            Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
            Else If (Shape = 'roundrect') Or (Shape = 'rounded_rectangular') Then
            Begin
                SetPadRoundRect(Pad, CornerRadius);
            End
            Else Pad.TopShape := eRounded;

            { Optional fields, set before the pad is added, which is the order
              the API reference's pad-cache example uses. }
            SlotLen := StrToFloatDef(GetBatchField(Op, 'hole_length'), 0);
            If (HoleSize > 0) And (SlotLen > HoleSize) Then
            Begin
                Try
                    Pad.HoleType := eSlotHole;
                    Pad.HoleWidth := CoordFromUnits(SlotLen, UnitsStr);
                    Inc(Slots);
                Except
                    Inc(PropFailed);
                End;
            End;

            PlatedStr := LowerCase(GetBatchField(Op, 'plated'));
            If (HoleSize > 0) And ((PlatedStr = 'false') Or (PlatedStr = '0')) Then
            Begin
                Try
                    Pad.Plated := False;
                    Inc(Unplated);
                Except
                    Inc(PropFailed);
                End;
            End;

            PasteStr := GetBatchField(Op, 'paste_expansion');
            MaskStr := GetBatchField(Op, 'mask_expansion');
            If (PasteStr <> '') Or (MaskStr <> '') Then
            Begin
                Try
                    Cache := Pad.GetState_Cache;
                    If PasteStr <> '' Then
                    Begin
                        Cache.PasteMaskExpansionValid := eCacheManual;
                        Cache.PasteMaskExpansion := CoordFromUnits(StrToFloatDef(PasteStr, 0), UnitsStr);
                    End;
                    If MaskStr <> '' Then
                    Begin
                        Cache.SolderMaskExpansionValid := eCacheManual;
                        Cache.SolderMaskExpansion := CoordFromUnits(StrToFloatDef(MaskStr, 0), UnitsStr);
                    End;
                    Pad.SetState_Cache := Cache;
                    Inc(Expanded);
                Except
                    Inc(PropFailed);
                End;
            End;

            Footprint.AddPCBObject(Pad);
            { The primitive must be registered with the library's backing Board as
              well as with the footprint, and the registration broadcast to both.
              Footprint.AddPCBObject alone is not enough: the object exists in the
              working copy, every read reports it, and the save discards it. That
              cost an afternoon on an LCSC import, where pads and tracks reported
              success and the footprint came back empty, and it was read as Altium
              refusing to author land patterns at all. Lib_AddFootprintText had the
              full sequence and was the only authoring call that survived a save. }
            PcbLib.Board.AddPCBObject(Pad);
            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
            PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"slots":' + IntToStr(Slots)
        + ',"unplated":' + IntToStr(Unplated)
        + ',"expansions":' + IntToStr(Expanded)
        + ',"property_failures":' + IntToStr(PropFailed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

Function Lib_AddFootprintTrack(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2, Width : Double;
    UnitsStr : String;
    LayerStr : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    TargetProblem : String;
    Track : IPCB_Track;
    Layer : TLayer;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    Width := StrToFloatDef(ExtractJsonValue(Params, 'width'), MilsInUnits(10, UnitsStr));
    LayerStr := ExtractJsonValue(Params, 'layer');

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
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
        Track.X1 := FootprintOriginX(Footprint) + CoordFromUnits(X1, UnitsStr);
        Track.Y1 := FootprintOriginY(Footprint) + CoordFromUnits(Y1, UnitsStr);
        Track.X2 := FootprintOriginX(Footprint) + CoordFromUnits(X2, UnitsStr);
        Track.Y2 := FootprintOriginY(Footprint) + CoordFromUnits(Y2, UnitsStr);
        Track.Width := CoordFromUnits(Width, UnitsStr);
        Track.Layer := Layer;

        Footprint.AddPCBObject(Track);
        { The primitive must be registered with the library's backing Board as
          well as with the footprint, and the registration broadcast to both.
          Footprint.AddPCBObject alone is not enough: the object exists in the
          working copy, every read reports it, and the save discards it. That
          cost an afternoon on an LCSC import, where pads and tracks reported
          success and the footprint came back empty, and it was read as Altium
          refusing to author land patterns at all. Lib_AddFootprintText had the
          full sequence and was the only authoring call that survived a save. }
        PcbLib.Board.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create track');

    PCBServer.PostProcess;
    MarkDocDirtyByPath(PcbLib.Board.FileName);
End;

{ Batch track authoring: same shape as Lib_AddFootprintPads. A `tracks` array }
{ (~~ / ; / = separators) -- silkscreen outline + assembly outline in one IPC }
{ round-trip instead of one-per-segment. Per-track fields: x1,y1,x2,y2,width, }
{ layer (TopOverlay default / BottomOverlay), mirroring the singular handler. }
Function Lib_AddFootprintTracks(Params : String; RequestId : String) : String;
Var
    TracksStr, Op, Remaining, LayerStr, BadLayers : String;
    OpCount, Added, Failed : Integer;
    X1, Y1, X2, Y2, Width : Double;
    UnitsStr : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    TargetProblem : String;
    Track : IPCB_Track;
    Layer : TLayer;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
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

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
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
            X1 := StrToFloatDef(GetBatchField(Op, 'x1'), 0);
            Y1 := StrToFloatDef(GetBatchField(Op, 'y1'), 0);
            X2 := StrToFloatDef(GetBatchField(Op, 'x2'), 0);
            Y2 := StrToFloatDef(GetBatchField(Op, 'y2'), 0);
            Width := StrToFloatDef(GetBatchField(Op, 'width'), MilsInUnits(10, UnitsStr));
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

            Track.X1 := FootprintOriginX(Footprint) + CoordFromUnits(X1, UnitsStr);
            Track.Y1 := FootprintOriginY(Footprint) + CoordFromUnits(Y1, UnitsStr);
            Track.X2 := FootprintOriginX(Footprint) + CoordFromUnits(X2, UnitsStr);
            Track.Y2 := FootprintOriginY(Footprint) + CoordFromUnits(Y2, UnitsStr);
            Track.Width := CoordFromUnits(Width, UnitsStr);
            Track.Layer := Layer;

            Footprint.AddPCBObject(Track);
            { The primitive must be registered with the library's backing Board as
              well as with the footprint, and the registration broadcast to both.
              Footprint.AddPCBObject alone is not enough: the object exists in the
              working copy, every read reports it, and the save discards it. That
              cost an afternoon on an LCSC import, where pads and tracks reported
              success and the footprint came back empty, and it was read as Altium
              refusing to author land patterns at all. Lib_AddFootprintText had the
              full sequence and was the only authoring call that survived a save. }
            PcbLib.Board.AddPCBObject(Track);
            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);
            PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{ RegionPointCount - how many x,y pairs a region's points field holds, the    }
{ pairs joined by '/'. -1 when any pair is malformed (no comma, or either    }
{ half not a plain number), so a typo refuses the region instead of reading  }
{ as zero and drawing a spike to the origin. Plain strings only: no          }
{ TStringList crosses a function boundary here.                              }
Function RegionPointCount(PointsStr : String) : Integer;
Var
    Rest, PointStr, XStr, YStr : String;
    SlashPos, CommaPos, Count : Integer;
Begin
    Result := 0;
    Count := 0;
    Rest := Trim(PointsStr);
    While Rest <> '' Do
    Begin
        SlashPos := Pos('/', Rest);
        If SlashPos = 0 Then
        Begin
            PointStr := Rest;
            Rest := '';
        End
        Else
        Begin
            PointStr := Copy(Rest, 1, SlashPos - 1);
            Rest := Copy(Rest, SlashPos + 1, Length(Rest));
        End;
        CommaPos := Pos(',', PointStr);
        If CommaPos = 0 Then
        Begin
            Result := -1;
            Exit;
        End;
        XStr := Trim(Copy(PointStr, 1, CommaPos - 1));
        YStr := Trim(Copy(PointStr, CommaPos + 1, Length(PointStr)));
        If (Not IsFloatStr(XStr)) Or (Not IsFloatStr(YStr)) Then
        Begin
            Result := -1;
            Exit;
        End;
        Count := Count + 1;
    End;
    Result := Count;
End;

{ RegionPointCoord - one coordinate of the Which-th pair (1-based) of a      }
{ points field, as its text; '' past the end. WantX picks x, else y.         }
Function RegionPointCoord(PointsStr : String; Which : Integer; WantX : Boolean) : String;
Var
    Rest, PointStr : String;
    SlashPos, CommaPos, Seen : Integer;
Begin
    Result := '';
    Seen := 0;
    Rest := Trim(PointsStr);
    While Rest <> '' Do
    Begin
        SlashPos := Pos('/', Rest);
        If SlashPos = 0 Then
        Begin
            PointStr := Rest;
            Rest := '';
        End
        Else
        Begin
            PointStr := Copy(Rest, 1, SlashPos - 1);
            Rest := Copy(Rest, SlashPos + 1, Length(Rest));
        End;
        Seen := Seen + 1;
        If Seen = Which Then
        Begin
            CommaPos := Pos(',', PointStr);
            If CommaPos = 0 Then Exit;
            If WantX Then Result := Trim(Copy(PointStr, 1, CommaPos - 1))
            Else Result := Trim(Copy(PointStr, CommaPos + 1, Length(PointStr)));
            Exit;
        End;
    End;
End;

{ RegionArcField - one field of the arc on the edge that starts at vertex    }
{ Which (1-based), from an arcs field of 'index,cx,cy,radius,angle1,angle2'  }
{ entries joined by '/'. Field 0 is cx and 4 is angle2. '' when that edge    }
{ has no arc entry, or the entry is short.                                    }
Function RegionArcField(ArcsStr : String; Which : Integer; Field : Integer) : String;
Var
    Rest, Entry, IndexStr : String;
    SlashPos, CommaPos, K : Integer;
Begin
    Result := '';
    Rest := Trim(ArcsStr);
    While Rest <> '' Do
    Begin
        SlashPos := Pos('/', Rest);
        If SlashPos = 0 Then
        Begin
            Entry := Rest;
            Rest := '';
        End
        Else
        Begin
            Entry := Copy(Rest, 1, SlashPos - 1);
            Rest := Copy(Rest, SlashPos + 1, Length(Rest));
        End;
        CommaPos := Pos(',', Entry);
        If CommaPos = 0 Then Continue;
        IndexStr := Trim(Copy(Entry, 1, CommaPos - 1));
        If Not IsIntStr(IndexStr) Then Continue;
        If StrToInt(IndexStr) <> Which Then Continue;
        Entry := Copy(Entry, CommaPos + 1, Length(Entry));
        For K := 1 To Field Do
        Begin
            CommaPos := Pos(',', Entry);
            If CommaPos = 0 Then Exit;
            Entry := Copy(Entry, CommaPos + 1, Length(Entry));
        End;
        CommaPos := Pos(',', Entry);
        If CommaPos = 0 Then Result := Trim(Entry)
        Else Result := Trim(Copy(Entry, 1, CommaPos - 1));
        Exit;
    End;
End;

{ Lib_AddFootprintRegions - solid polygon regions in a PcbLib footprint:     }
{ paste windows over an exposed pad, or any filled shape on a named layer.   }
{                                                                              }
{ Params: units (mil default, or mm), footprint_name (optional), regions:    }
{   '~~' separated ops, ';' separated fields:                                 }
{     layer=TopPaste;points=x1,y1/x2,y2/x3,y3[/...][;arcs=i,cx,cy,r,a1,a2/..] }
{ Points are relative to the footprint origin, three or more per region.     }
{ An arcs entry makes the edge from vertex i (1-based) to the next a true    }
{ arc: centre cx,cy (footprint-relative), radius r, and Altium's angles,     }
{ counterclockwise from a1 to a2. Such a region is built from shape segments }
{ (ShapeSegments, then UpdateContourFromShape), as Place Region builds one,  }
{ so it keeps the arc; one with no arcs keeps the plain contour route.       }
{ A region with no layer is refused rather than defaulted: the only default  }
{ the other authoring calls have is copper or silkscreen, and a solid shape  }
{ dropped on top copper by default is a short.                               }
{                                                                              }
{ Built exactly as PCB_PlaceRegion builds one on a board (MainContour        }
{ .Replicate, Count, 1-based X/Y, SetOutlineContour), then registered with   }
{ the footprint AND the library board like every other authoring call here,  }
{ or the save discards it.                                                    }
Function Lib_AddFootprintRegions(Params : String; RequestId : String) : String;
Var
    RegionsStr, Op, Remaining, LayerStr, BadLayers, PointsStr, ArcsStr : String;
    OpCount, Added, Failed, BadPoints, NPts, I, K, ArcEdges : Integer;
    UnitsStr : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    TargetProblem : String;
    Region : IPCB_Region;
    Contour : IPCB_Contour;
    Seg : TPolySegment;
    ArcsOk : Boolean;
    Layer : TLayer;
    OrgX, OrgY : TCoord;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    RegionsStr := ExtractJsonValue(Params, 'regions');
    If RegionsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'regions is required');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
        Exit;
    End;
    OrgX := FootprintOriginX(Footprint);
    OrgY := FootprintOriginY(Footprint);

    Added := 0;
    Failed := 0;
    BadPoints := 0;
    ArcEdges := 0;
    OpCount := 0;
    BadLayers := '';
    Remaining := RegionsStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            LayerStr := GetBatchField(Op, 'layer');
            If LayerStr = '' Then Layer := eNoLayer
            Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
            If Layer = eNoLayer Then
            Begin
                Inc(Failed);
                If LayerStr = '' Then LayerStr := '(none given)';
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;

            PointsStr := GetBatchField(Op, 'points');
            NPts := RegionPointCount(PointsStr);
            If NPts < 3 Then
            Begin
                Inc(BadPoints);
                Inc(Failed);
                Continue;
            End;

            { Every field of every arc a number, or the region is refused  }
            { rather than drawn with a centre read as zero.                 }
            ArcsStr := GetBatchField(Op, 'arcs');
            ArcsOk := True;
            If ArcsStr <> '' Then
                For I := 1 To NPts Do
                    If RegionArcField(ArcsStr, I, 0) <> '' Then
                        For K := 0 To 4 Do
                            If Not IsFloatStr(RegionArcField(ArcsStr, I, K)) Then
                                ArcsOk := False;
            If Not ArcsOk Then
            Begin
                Inc(BadPoints);
                Inc(Failed);
                Continue;
            End;

            Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
            If Region = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;
            Region.Layer := Layer;
            If ArcsStr = '' Then
            Begin
                Contour := Region.MainContour.Replicate;
                Contour.Count := NPts;
                For I := 1 To NPts Do
                Begin
                    Contour.X[I] := OrgX + CoordFromUnits(
                        StrToFloatDef(RegionPointCoord(PointsStr, I, True), 0), UnitsStr);
                    Contour.Y[I] := OrgY + CoordFromUnits(
                        StrToFloatDef(RegionPointCoord(PointsStr, I, False), 0), UnitsStr);
                End;
                Region.SetOutlineContour(Contour);
            End
            Else
            Begin
                { One segment per vertex, each carrying the edge that     }
                { leaves it; the count grows with each write, as in the     }
                { shape-segment samples this follows.                       }
                For I := 1 To NPts Do
                Begin
                    Seg := TPolySegment;
                    Seg.vx := OrgX + CoordFromUnits(
                        StrToFloatDef(RegionPointCoord(PointsStr, I, True), 0), UnitsStr);
                    Seg.vy := OrgY + CoordFromUnits(
                        StrToFloatDef(RegionPointCoord(PointsStr, I, False), 0), UnitsStr);
                    If RegionArcField(ArcsStr, I, 0) <> '' Then
                    Begin
                        Seg.Kind := ePolySegmentArc;
                        Seg.cx := OrgX + CoordFromUnits(
                            StrToFloatDef(RegionArcField(ArcsStr, I, 0), 0), UnitsStr);
                        Seg.cy := OrgY + CoordFromUnits(
                            StrToFloatDef(RegionArcField(ArcsStr, I, 1), 0), UnitsStr);
                        Seg.Radius := CoordFromUnits(
                            StrToFloatDef(RegionArcField(ArcsStr, I, 2), 0), UnitsStr);
                        Seg.Angle1 := StrToFloatDef(RegionArcField(ArcsStr, I, 3), 0);
                        Seg.Angle2 := StrToFloatDef(RegionArcField(ArcsStr, I, 4), 0);
                    End
                    Else
                        Seg.Kind := ePolySegmentLine;
                    Region.ShapeSegmentCount := I;
                    Region.ShapeSegments[I - 1] := Seg;
                End;
                Region.UpdateContourFromShape(True);
                { Counted off the region, so the reply says what it holds. }
                For I := 0 To Region.ShapeSegmentCount - 1 Do
                    If Region.ShapeSegments[I].Kind = ePolySegmentArc Then
                        Inc(ArcEdges);
            End;

            Footprint.AddPCBObject(Region);
            PcbLib.Board.AddPCBObject(Region);
            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Region.I_ObjectAddress);
            PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Region.I_ObjectAddress);
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"bad_points":' + IntToStr(BadPoints)
        + ',"arc_edges":' + IntToStr(ArcEdges)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{ Lib_AddFootprintFills - rectangular fills in a PcbLib footprint, in one     }
{ call: a grid of paste windows over an exposed pad, a keep-clear block on a  }
{ mechanical layer, anything that is a plain rectangle.                       }
{                                                                              }
{ Params: units (mil default, or mm), footprint_name (optional), fills:       }
{   '~~' separated ops, ';' separated fields:                                 }
{     layer=TopPaste;x1=..;y1=..;x2=..;y2=..[;rotation=deg]                   }
{ Corners are relative to the footprint origin; rotation is about the fill's  }
{ own centre and defaults to 0.                                               }
{                                                                              }
{ A fill is not a region with four points. It stays an IPCB_Fill, which is    }
{ what a fill drawn by hand in the editor is, so it reads back as one, edits  }
{ in the fill properties panel and is matched by eFillObject filters.         }
{                                                                              }
{ The layer is REQUIRED, for the reason Lib_AddFootprintRegions gives: a      }
{ solid shape defaulted onto copper is a short. A fill with no area is        }
{ refused and counted in bad_extent rather than added, because it would be    }
{ invisible in the editor and still counted as drawn.                         }
{                                                                              }
{ Registered with the footprint AND the library board, like every other       }
{ authoring call here, or the save discards it; settled by the dispatcher.    }
Function Lib_AddFootprintFills(Params : String; RequestId : String) : String;
Var
    FillsStr, Op, Remaining, LayerStr, BadLayers : String;
    OpCount, Added, Failed, BadExtent : Integer;
    UnitsStr : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    TargetProblem : String;
    Fill : IPCB_Fill;
    Layer : TLayer;
    OrgX, OrgY : TCoord;
    { Double, not Integer: an Integer local would put every corner back on }
    { the whole-mil grid that units=mm exists to get off.                  }
    X1, Y1, X2, Y2, Rotation : Double;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    FillsStr := ExtractJsonValue(Params, 'fills');
    If FillsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'fills is required');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
        Exit;
    End;
    OrgX := FootprintOriginX(Footprint);
    OrgY := FootprintOriginY(Footprint);

    Added := 0;
    Failed := 0;
    BadExtent := 0;
    OpCount := 0;
    BadLayers := '';
    Remaining := FillsStr;

    PCBServer.PreProcess;
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            LayerStr := GetBatchField(Op, 'layer');
            If LayerStr = '' Then Layer := eNoLayer
            Else Layer := ResolveLayerId(PcbLib.Board, LayerStr);
            If Layer = eNoLayer Then
            Begin
                Inc(Failed);
                If LayerStr = '' Then LayerStr := '(none given)';
                If BadLayers = '' Then BadLayers := LayerStr
                Else If Pos(LayerStr, BadLayers) = 0 Then
                    BadLayers := BadLayers + ', ' + LayerStr;
                Continue;
            End;

            X1 := StrToFloatDef(GetBatchField(Op, 'x1'), 0);
            Y1 := StrToFloatDef(GetBatchField(Op, 'y1'), 0);
            X2 := StrToFloatDef(GetBatchField(Op, 'x2'), 0);
            Y2 := StrToFloatDef(GetBatchField(Op, 'y2'), 0);
            Rotation := StrToFloatDef(GetBatchField(Op, 'rotation'), 0);
            If (X1 = X2) Or (Y1 = Y2) Then
            Begin
                Inc(BadExtent);
                Inc(Failed);
                Continue;
            End;

            Fill := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
            If Fill = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;
            Fill.X1Location := OrgX + CoordFromUnits(X1, UnitsStr);
            Fill.Y1Location := OrgY + CoordFromUnits(Y1, UnitsStr);
            Fill.X2Location := OrgX + CoordFromUnits(X2, UnitsStr);
            Fill.Y2Location := OrgY + CoordFromUnits(Y2, UnitsStr);
            Fill.Rotation := Rotation;
            Fill.Layer := Layer;

            Footprint.AddPCBObject(Fill);
            PcbLib.Board.AddPCBObject(Fill);
            PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Fill.I_ObjectAddress);
            PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
                c_Broadcast, PCBM_BoardRegisteration, Fill.I_ObjectAddress);
            Inc(Added);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"bad_extent":' + IntToStr(BadExtent)
        + ',"unknown_layers":"' + EscapeJsonString(BadLayers) + '"'
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{ Lib_GetMechLayers - the enabled mechanical layers of the CURRENT PcbLib,    }
{ each with its name and kind. Read-only.                                     }
{                                                                              }
{ pcb_get_mech_layer_names answers this for a board, but it finds its board   }
{ through GetPCBBoardAnywhere, which is not aimed at a library. The package   }
{ footprint tool needs the library's assembly and courtyard layers BEFORE it  }
{ writes anything, by kind first (the signal lib_audit_footprint_policies     }
{ trusts) and by name after. Same reads as PCB_GetMechLayerNames:            }
{ LayerObject_V7, MechanicalLayerEnabled, Name and ReadMechKind.              }
Function Lib_GetMechLayers(Params : String; RequestId : String) : String;
Var
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    Lyr : TLayer;
    ItemsJson, NameStr, FileStr : String;
    First, Enabled : Boolean;
    Num, Count, KindId : Integer;
    LibPath, FocusedPath : String;
    Workspace : IWorkspace;
    Doc : IDocument;
Begin
    { library_path, when given, is opened and focused the way             }
    { Lib_GetPadGeometry does it, so a caller reading one library's        }
    { footprints and its layer kinds reads them from the same file.        }
    LibPath := ExtractJsonValue(Params, 'library_path');
    If LibPath <> '' Then
    Begin
        FocusedPath := '';
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Doc := Workspace.DM_FocusedDocument;
            If Doc <> Nil Then
            Begin
                Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
            End;
        End;
        If UpperCase(FocusedPath) <> UpperCase(LibPath) Then
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
    Board := Nil;
    Try Board := PcbLib.Board; Except Board := Nil; End;
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_BOARD',
            'The library has no board to carry a layer stack');
        Exit;
    End;
    LayerStack := Nil;
    Try LayerStack := Board.LayerStack_V7; Except LayerStack := Nil; End;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP', 'Could not access layer stack');
        Exit;
    End;
    FileStr := '';
    Try FileStr := Board.FileName; Except End;

    ItemsJson := '';
    First := True;
    Count := 0;
    For Num := 1 To MechScanLimit Do
    Begin
        Lyr := MechLayerFromNumber(Num);
        If Lyr = eNoLayer Then Continue;
        LayerObj := Nil;
        Try LayerObj := LayerStack.LayerObject_V7[Lyr]; Except LayerObj := Nil; End;
        If LayerObj = Nil Then Continue;
        Enabled := False;
        Try Enabled := LayerObj.MechanicalLayerEnabled; Except Enabled := False; End;
        If Not Enabled Then Continue;
        NameStr := '';
        Try NameStr := LayerObj.Name; Except End;
        KindId := ReadMechKind(LayerObj);
        If Not First Then ItemsJson := ItemsJson + ',';
        First := False;
        ItemsJson := ItemsJson
            + '{"layer":"Mechanical' + IntToStr(Num) + '",'
            + '"number":' + IntToStr(Num) + ','
            + '"name":"' + EscapeJsonString(NameStr) + '",'
            + '"kind":"' + EscapeJsonString(MechKindToString(KindId)) + '",'
            + '"kind_id":' + IntToStr(KindId) + '}';
        Inc(Count);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"library_path":"' + EscapeJsonString(FileStr) + '"'
        + ',"count":' + IntToStr(Count)
        + ',"layers":[' + ItemsJson + ']}');
End;

Function Lib_AddFootprintArc(Params : String; RequestId : String) : String;
Var
    XCenter, YCenter, Radius, Width : Double;
    UnitsStr : String;
    { Angles are DOUBLE and are read with StrToFloatDef below. They are     }
    { declared `float` on the Python side, so the wire carries "360.0" and  }
    { StrToIntDef returned its DEFAULT on every call. EndAngle came through }
    { as 0 whatever the caller asked for, so every arc this tool drew had a }
    { zero sweep while the call reported success. IPCB_Arc takes Doubles,   }
    { so a half degree need not be rounded away either.                     }
    StartAngle, EndAngle : Double;
    LayerStr : String;
    PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent;
    TargetProblem : String;
    Arc : IPCB_Arc;
    Layer : TLayer;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    XCenter := StrToFloatDef(ExtractJsonValue(Params, 'x_center'), 0);
    YCenter := StrToFloatDef(ExtractJsonValue(Params, 'y_center'), 0);
    Radius := StrToFloatDef(ExtractJsonValue(Params, 'radius'), MilsInUnits(100, UnitsStr));
    StartAngle := StrToFloatDef(ExtractJsonValue(Params, 'start_angle'), 0.0);
    EndAngle := StrToFloatDef(ExtractJsonValue(Params, 'end_angle'), 360.0);
    Width := StrToFloatDef(ExtractJsonValue(Params, 'width'), MilsInUnits(10, UnitsStr));
    LayerStr := ExtractJsonValue(Params, 'layer');

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', 'No PCB library is active');
        Exit;
    End;

    Footprint := PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), TargetProblem);
    If Footprint = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FOOTPRINT', TargetProblem);
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
        Arc.XCenter := FootprintOriginX(Footprint) + CoordFromUnits(XCenter, UnitsStr);
        Arc.YCenter := FootprintOriginY(Footprint) + CoordFromUnits(YCenter, UnitsStr);
        Arc.Radius := CoordFromUnits(Radius, UnitsStr);
        Arc.StartAngle := StartAngle;
        Arc.EndAngle := EndAngle;
        Arc.LineWidth := CoordFromUnits(Width, UnitsStr);
        Arc.Layer := Layer;

        Footprint.AddPCBObject(Arc);
        { The primitive must be registered with the library's backing Board as
          well as with the footprint, and the registration broadcast to both.
          Footprint.AddPCBObject alone is not enough: the object exists in the
          working copy, every read reports it, and the save discards it. That
          cost an afternoon on an LCSC import, where pads and tracks reported
          success and the footprint came back empty, and it was read as Altium
          refusing to author land patterns at all. Lib_AddFootprintText had the
          full sequence and was the only authoring call that survived a save. }
        PcbLib.Board.AddPCBObject(Arc);
        PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Arc.I_ObjectAddress);
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
            c_Broadcast, PCBM_BoardRegisteration, Arc.I_ObjectAddress);

        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create arc');

    PCBServer.PostProcess;
    MarkDocDirtyByPath(PcbLib.Board.FileName);
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
    Rotation : Integer;
    X, Y, Size, Width : Double;
    UnitsStr : String;
    UseTTFont : Boolean;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    TextStr := ExtractJsonValue(Params, 'text');
    If TextStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text is required');
        Exit;
    End;
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    Size := StrToFloatDef(ExtractJsonValue(Params, 'size'), MilsInUnits(50, UnitsStr));
    Width := StrToFloatDef(ExtractJsonValue(Params, 'width'), MilsInUnits(8, UnitsStr));
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
        If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
        Text.XLocation := Footprint.X + CoordFromUnits(X, UnitsStr);
        Text.YLocation := Footprint.Y + CoordFromUnits(Y, UnitsStr);
        Text.Layer := Layer;
        Text.UseTTFonts := UseTTFont;
        Text.UnderlyingString := TextStr;
        Text.Size := CoordFromUnits(Size, UnitsStr);
        Text.Width := CoordFromUnits(Width, UnitsStr);
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
    MarkDocDirtyByPath(Board.FileName);

    RespJson :=
        '{"success":true' +
        ',"footprint":"' + EscapeJsonString(Footprint.Name) + '"' +
        ',"text":"' + EscapeJsonString(TextStr) + '"' +
        ',"layer":"' + EscapeJsonString(LayerStr) + '"' +
        ',"x":' + FloatToJsonStr(X) +
        ',"y":' + FloatToJsonStr(Y) + '}';
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    SeenPads : TStringList;
    PadAddr : String;
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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

    { DEDUPE BY OBJECT ADDRESS. A primitive added in this session is
      registered with BOTH the footprint and the library's backing board,
      which is what makes it survive the save, and until the library is
      reloaded the group iterator yields that one object TWICE.
      MEASURED: three pads read back as pad_count 6 and eight primitives as
      16, while the saved file held exactly three and eight. The duplicate
      is the SAME object, not a second one, so the address separates them. }
    SeenPads := TStringList.Create;
    PadsJson := '[';
    Count := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            PadAddr := '';
            Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
            If (PadAddr <> '') And (SeenPads.IndexOf(PadAddr) >= 0) Then
            Begin
                Pad := GrpIter.NextPCBObject;
                Continue;
            End;
            If PadAddr <> '' Then SeenPads.Add(PadAddr);
            ShapeStr := 'round';
            Try
                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                { eRoundedRectangular is the PAD SHAPE. This used to test
                  eRoundRectangle, which is a SCHEMATIC object id. Both
                  identifiers exist, so nothing errored and the test simply
                  never matched: every rounded-rectangle pad read back as
                  round with corner_pct 0, including ones this same library
                  had just written with
                  SetState_StackShapeOnLayer(..., eRoundedRectangular). }
                Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'
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
        Try SeenPads.Free; Except End;
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
    Try Result := LayerDisplayName(Lyr); Except End;
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
                                Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
                Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
                    'create requires layer_id, x, y and height');
                Exit;
            End;
            { Board.AddPCBObject attaches to the library's CURRENT component,
              so the target must be made current or the text lands elsewhere. }
            Try PcbLib.CurrentComponent := Target; Except End;
            If PcbLib.CurrentComponent <> Target Then
            Begin
                Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
                    'Could not make the target footprint current: ' + FpWanted);
                Exit;
            End;
            Desig := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
            If Desig = Nil Then
            Begin
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
    MarkDocDirtyByPath(LibPath);

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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    MarkDocDirtyByPath(LibPath);

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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    If Not LoadTextFile(Edits, EditsPath) Then
    Begin
        Edits.Free;
        Result := BuildErrorResponse(RequestId, 'READ_FAILED',
            'Could not read the edit file ' + EditsPath);
        Exit;
    End;
    Try

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
    MarkDocDirtyByPath(LibPath);

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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    OrgX, OrgY : TCoord;
    RotZ : Double;
    DidStandoff, DidRotation, DidMove, Shifted : Boolean;
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
    { Parse as FLOAT then round. These three are declared `float` on the    }
    { Python side, so pydantic turns an argument of 25 into 25.0 and the    }
    { wire carries "25.0". StrToIntDef cannot read that and returns its     }
    { default, so EVERY offset arrived as 0, the `If Off <> 0` guards below }
    { skipped the assignment, and `applied` reported false. That false read }
    { as "Altium refused the adjustment" when the value had simply never    }
    { arrived, which is the worst version of this bug: the tool looked      }
    { honest while silently discarding the caller's numbers. MilsToCoord    }
    { takes an Integer, hence the Round rather than widening the locals.    }
    OffX := Round(StrToFloatDef(ExtractJsonValue(Params, 'offset_x'), 0.0));
    OffY := Round(StrToFloatDef(ExtractJsonValue(Params, 'offset_y'), 0.0));
    OffZ := Round(StrToFloatDef(ExtractJsonValue(Params, 'offset_z'), 0.0));
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
                { Same registration the pad, track, arc and text paths
                  need. Without it the body lives in the working copy
                  only: the call reports success, the model loads, and
                  the save throws it away, which is exactly how this
                  read as a tool that does nothing at all.

                  Missed when the other five were fixed, because that
                  audit was scoped to handlers named Lib_AddFootprint*
                  and this one attaches a primitive under a different
                  name. Audit by the AddPCBObject call, not by what the
                  handler is called. }
                PcbLib.Board.AddPCBObject(Body);
                PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
                    c_Broadcast, PCBM_BoardRegisteration, Body.I_ObjectAddress);
                PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
                    c_Broadcast, PCBM_BoardRegisteration, Body.I_ObjectAddress);

                { Placement adjustments. Each is guarded AND REPORTED,     }
                { because a blanket success here would hide which of them  }
                { actually took.                                           }
                {                                                          }
                { Body.Rotation USED TO BE ASSIGNED HERE AND MUST NOT BE.  }
                { IPCB_ComponentBody exposes no Rotation on AD26 26.9.1.9. }
                { Measured: the assignment raised "Undeclared identifier:  }
                { Rotation", and an undeclared identifier is NOT catchable }
                { in DelphiScript, so the Try around it did nothing and    }
                { the modal killed the polling loop. The whole bridge went }
                { down and had to be restarted by hand. The guard read as  }
                { careful and could never have fired.                      }
                {                                                          }
                { The rotation IS reachable, but on the MODEL rather than  }
                { the body, and BEFORE Body.Model is assigned:             }
                { AutoSTEPplacer.pas calls Model.SetState(90,0,0,0). The   }
                { meaning of those four arguments is not documented        }
                { anywhere this project can verify, so guessing them would }
                { repeat the mistake this comment exists to record.        }
                { rotation_z therefore reports false, like rotation_x and  }
                { rotation_y, until the signature is measured.             }
                DidStandoff := False;
                DidRotation := False;
                DidMove := False;
                Shifted := False;
                If OffZ <> 0 Then
                    Try
                        Body.StandoffHeight := MilsToCoord(OffZ);
                        DidStandoff := True;
                    Except End;
                { THE BODY ARRIVES AT THE BOARD ORIGIN, and a footprint made }
                { current above sits at Altium's library origin (about     }
                { 50000,50000 mil, see FootprintOriginX), so the body      }
                { landed that far from its footprint and needed an offset   }
                { of +50000 to come back. Moved by the footprint's origin   }
                { as well as the caller's offset, which is relative to it. }
                OrgX := FootprintOriginX(Footprint);
                OrgY := FootprintOriginY(Footprint);
                If (OrgX <> 0) Or (OrgY <> 0) Or (OffX <> 0) Or (OffY <> 0) Then
                    Try
                        Body.MoveByXY(OrgX + MilsToCoord(OffX), OrgY + MilsToCoord(OffY));
                        Shifted := True;
                        DidMove := (OffX <> 0) Or (OffY <> 0);
                    Except End;

                AppliedJson := JsonBool('standoff_height', DidStandoff) + ','
                    + JsonBool('rotation_z', DidRotation) + ','
                    + JsonBool('offset_xy', DidMove);

                Result := BuildSuccessResponse(RequestId, JsonObj(
                    JsonBool('success', True) + ','
                    + JsonStr('footprint', FpName) + ','
                    + JsonStr('model', ExtractFileName(ModelPath)) + ','
                    + JsonBool('moved_to_footprint_origin', Shifted Or ((OrgX = 0) And (OrgY = 0))) + ','
                    + JsonRaw('footprint_origin_mils', '[' + IntToStr(CoordToMils(OrgX))
                        + ',' + IntToStr(CoordToMils(OrgY)) + ']') + ','
                    + JsonRaw('applied', JsonObj(AppliedJson))));
            End;
        End;
    Finally
        PCBServer.PostProcess;
    End;

    MarkDocDirtyByPath(PcbLib.Board.FileName);
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
    FileNames, LiveNames, Unsaved, Stale, Nm, Rest, DiffJson : String;
    LiveLib : ISch_Lib;
    LiveIter : ISch_Iterator;
    LiveComp : ISch_Component;
    CompNum, I, Tab : Integer;
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

    { A RELATIVE PATH HERE PRODUCES A WRONG ANSWER, NOT AN ERROR.            }
    { CreateLibCompInfoReader wants a full path. Given a bare basename it    }
    { resolves against whatever directory it likes and hands back a reader   }
    { for something else, or a stale view, and the enumeration then looks    }
    { perfectly ordinary.                                                     }
    {                                                                         }
    { MEASURED: called with no library_path against a focused free-document  }
    { SchLib, this reported ONE component twice in a row while the same call }
    { WITH the full path reported both that were really there. The default   }
    { invocation is the one that under-reports, which is the worst way round.}
    { DM_FullPath returns a basename for a free document, which is how the   }
    { relative path got in.                                                   }
    If Not ((Copy(LibPath, 2, 1) = ':') Or (Copy(LibPath, 1, 2) = '\\')) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'RELATIVE_LIBRARY_PATH',
            'The focused document reports only "' + LibPath + '", not a full '
            + 'path, and the component reader silently answers about the '
            + 'wrong file when given one. Pass library_path with the '
            + 'absolute .SchLib path.');
        Exit;
    End;

    // Use CreateLibCompInfoReader to enumerate components. ICompInfoReader is
    // a fast metadata reader, it returns CompName, AliasName, PartCount and
    // Description directly from the lib file without loading every symbol's
    // primitives, so the cheap path scales linearly with file IO.
    LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
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
    FileNames := #9;
    For I := 0 To CompNum - 1 Do
    Begin
        If Not First Then Data := Data + ',';
        First := False;
        CompInfo := LibReader.ComponentInfos[I];
        CompName := CompInfo.CompName;
        FileNames := FileNames + CompName + #9;

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
                Component := LookupLibComponent(SchLib, CompName);
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

    { The list above is the SAVED file. When the library is open, compare it }
    { with the symbols in memory (issue #88): a symbol renamed or added and  }
    { not saved was listed under its old name, or not at all, and a caller   }
    { naming it got a miss. Read only; nothing is saved or reopened.         }
    DiffJson := '';
    LiveLib := Nil;
    Try LiveLib := SchServer.GetSchDocumentByPath(LibPath); Except LiveLib := Nil; End;
    If (LiveLib <> Nil) And (LiveLib.ObjectId = eSchLib) Then
    Begin
        LiveNames := #9;
        Unsaved := '';
        LiveIter := LiveLib.SchLibIterator_Create;
        Try
            LiveIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            LiveComp := LiveIter.FirstSchObject;
            While LiveComp <> Nil Do
            Begin
                Nm := '';
                Try Nm := LiveComp.LibReference; Except End;
                If (Nm <> '') And (Pos(#9 + Nm + #9, LiveNames) = 0) Then
                Begin
                    LiveNames := LiveNames + Nm + #9;
                    If Pos(#9 + Nm + #9, FileNames) = 0 Then
                    Begin
                        If Unsaved <> '' Then Unsaved := Unsaved + ',';
                        Unsaved := Unsaved + '"' + EscapeJsonString(Nm) + '"';
                    End;
                End;
                LiveComp := LiveIter.NextSchObject;
            End;
        Finally
            LiveLib.SchIterator_Destroy(LiveIter);
        End;
        Stale := '';
        Rest := Copy(FileNames, 2, Length(FileNames));
        While Rest <> '' Do
        Begin
            Tab := Pos(#9, Rest);
            If Tab = 0 Then Tab := Length(Rest) + 1;
            Nm := Copy(Rest, 1, Tab - 1);
            Rest := Copy(Rest, Tab + 1, Length(Rest));
            If (Nm <> '') And (Pos(#9 + Nm + #9, LiveNames) = 0) Then
            Begin
                If Stale <> '' Then Stale := Stale + ',';
                Stale := Stale + '"' + EscapeJsonString(Nm) + '"';
            End;
        End;
        DiffJson := ',"unsaved_symbols":[' + Unsaved + ']'
            + ',"saved_names_not_in_open_library":[' + Stale + ']';
    End;

    Result := BuildSuccessResponse(RequestId, '{"count":' + IntToStr(CompNum)
        + ',"components":' + Data + DiffJson + '}');
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

    LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
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
                Component := LookupLibComponent(SchLib, CompName);
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
        LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
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
        Result := LookupLibComponent(SchLib, WantName);
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
    Result := LookupLibComponent(SchLib, WantName);
    If Result = Nil Then
    Begin
        ErrCode := 'COMPONENT_NOT_FOUND';
        ErrMsg := 'Component not found in library: ' + WantName;
    End;
End;

{ The highest part number any pin of the symbol is drawn on, 0 for none.   }
{ Pins belonging to every part (OwnerPartId 0) do not count.               }
Function SymbolPartsWithPins(Component : ISch_Component) : Integer;
Var
    PIter : ISch_Iterator;
    Pin : ISch_Pin;
    Owner : Integer;
Begin
    Result := 0;
    PIter := Component.SchIterator_Create;
    Try
        PIter.AddFilter_ObjectSet(MkSet(ePin));
        Pin := PIter.FirstSchObject;
        While Pin <> Nil Do
        Begin
            Owner := 0;
            Try Owner := Pin.OwnerPartId; Except End;
            If Owner > Result Then Result := Owner;
            Pin := PIter.NextSchObject;
        End;
    Finally
        Component.SchIterator_Destroy(PIter);
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
    PartCount, LivePartCount, PartsWithPins : Integer;
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
        Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;

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
    LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
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
    { THREE READINGS, NOT ONE. The file reader's PartCount read 2 where the  }
    { live symbol said 1 (issue #88), and a placed part once read 4 against }
    { a library's correct 5, so neither source is trusted alone. The live   }
    { count and the highest part with pins go alongside, and a mismatch is  }
    { named instead of one number being picked.                             }
    LivePartCount := 0;
    Try LivePartCount := Component.PartCount; Except End;
    PartsWithPins := SymbolPartsWithPins(Component);
    Data := Data + ',"part_count_live":' + IntToStr(LivePartCount);
    Data := Data + ',"parts_with_pins":' + IntToStr(PartsWithPins);
    If (LivePartCount <> PartCount) Or ((PartsWithPins > 0) And (PartsWithPins <> PartCount)) Then
        Data := Data + ',"part_count_note":"the file reader, the open symbol and its '
            + 'pins disagree on the number of parts. parts_with_pins is the highest '
            + 'part that has pins; a part with no pins yet is not counted there."';
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
    Line, CompName, ParamName, ParamValue, FailReasons : String;
    PipePos1, PipePos2 : Integer;
    Updated, Created, Failed, LineNum : Integer;
Begin
    FailReasons := '';
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
                    AddFailReason(FailReasons, Line, 'no | separator on the line');
                    Continue;
                End;
                CompName := Trim(Copy(Line, 1, PipePos1 - 1));
                Line := Copy(Line, PipePos1 + 1, Length(Line));
                PipePos2 := Pos('|', Line);
                If PipePos2 = 0 Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, CompName,
                        'the line has only one | separator, so there is no value');
                    Continue;
                End;
                ParamName := Trim(Copy(Line, 1, PipePos2 - 1));
                { ParamValue is the LAST field, which is exactly where a CRLF }
                { line ending left a stray carriage return. Without this trim }
                { the parameter was written with a CR glued to its value.     }
                ParamValue := Trim(Copy(Line, PipePos2 + 1, Length(Line)));

                Component := LookupLibComponent(SchLib, CompName);
                If Component = Nil Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, CompName,
                        'no component with that library reference');
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

                // Special case: Comment is the component's comment label,
                // not a parameter. "=Partnumber" makes it show that value.
                If ParamName = 'Comment' Then
                Begin
                    If Component.Comment <> Nil Then
                    Begin
                        SchBeginModify(Component.Comment);
                        Component.Comment.Text := ParamValue;
                        SchEndModify(Component.Comment);
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
                        { Hidden, as a library parameter normally is. Created
                          visible it sat on the symbol origin, over the pins. }
                        NewParam.IsHidden := True;
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
        ',"total_lines":' + IntToStr(LineNum) +
        ',"failures":[' + FailReasons + ']}');
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
    Line, OldName, NewName, FailReasons : String;
    PipePos : Integer;
    Renamed, Failed, LineNum : Integer;
Begin
    FailReasons := '';
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
                    AddFailReason(FailReasons, Line, 'no | separator on the line');
                    Continue;
                End;
                { Trim both. The batch file is written from Python and used to }
                { arrive CRLF-terminated, so ReadLn left the carriage return   }
                { glued to the LAST field: the rename then set a LibReference  }
                { ending in CR. Python no longer translates, and this trims    }
                { anyway, because a writer is easy to change back by accident. }
                OldName := Trim(Copy(Line, 1, PipePos - 1));
                NewName := Trim(Copy(Line, PipePos + 1, Length(Line)));

                If NewName = '' Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, OldName, 'the new name is empty');
                    Continue;
                End;

                Component := LookupLibComponent(SchLib, OldName);
                If Component = Nil Then
                Begin
                    Inc(Failed);
                    AddFailReason(FailReasons, OldName,
                        'no component with that library reference');
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
        ',"total_lines":' + IntToStr(LineNum) +
        ',"failures":[' + FailReasons + ']}');
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

    ReaderA := SchServer.CreateLibCompInfoReader(SafeSchLibPath(PathA));
    If ReaderA = Nil Then Begin Result := BuildErrorResponse(RequestId, 'READER_FAILED', 'Cannot read library A'); Exit; End;
    ReaderA.ReadAllComponentInfo;
    NumA := ReaderA.NumComponentInfos;

    ReaderB := SchServer.CreateLibCompInfoReader(SafeSchLibPath(PathB));
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
    XCenter, YCenter, Radius, Width : Integer;
    { Same defect as Lib_AddFootprintArc: `float` on the Python side, so    }
    { StrToIntDef never read the wire value and every symbol arc was drawn  }
    { with a zero sweep.                                                    }
    StartAngle, EndAngle : Double;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Arc : ISch_Arc;
    ViaLib : Boolean;
Begin
    XCenter := StrToIntDef(ExtractJsonValue(Params, 'x_center'), 0);
    YCenter := StrToIntDef(ExtractJsonValue(Params, 'y_center'), 0);
    Radius := StrToIntDef(ExtractJsonValue(Params, 'radius'), 100);
    StartAngle := StrToFloatDef(ExtractJsonValue(Params, 'start_angle'), 0.0);
    EndAngle := StrToFloatDef(ExtractJsonValue(Params, 'end_angle'), 360.0);
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
        ViaLib := AddLibSymbolPrim(SchLib, Component, Arc);
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

        MarkLibDirty(SchLib);
        Try SchLib.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"shown":' + BoolToJsonStr(ViaLib) + '}');
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
    VerticesStr, Token, FillStr, OwnerStr : String;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Polygon : ISch_Polygon;
    Remaining : String;
    CommaPos, X, Y, I : Integer;
    ViaLib : Boolean;
    { Parallel TStringLists of stringified coords. Fixed-size local arrays }
    { of any type corrupt the return slot, see                              }
    { [[delphiscript_fixed_string_array_bug]] - originally documented for  }
    { Array of String, now confirmed for Array of Integer/Double too.      }
    XValues, YValues : TStringList;
Begin
    VerticesStr := ExtractJsonValue(Params, 'vertices');
    FillStr := ExtractJsonValue(Params, 'fill_color');
    OwnerStr := ExtractJsonValue(Params, 'owner_part_id');

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
            { A fill colour makes it a BODY (an amplifier triangle, in     }
            { Altium's block yellow) rather than a glyph in the line        }
            { colour, which is what a diode triangle wants. -1 is no fill:  }
            { an outline only.                                              }
            If (FillStr <> '') And IsIntStr(FillStr) Then
            Begin
                If StrToIntDef(FillStr, -1) >= 0 Then
                    Try Polygon.AreaColor := StrToIntDef(FillStr, 0); Except End
                Else
                    Polygon.IsSolid := False;
            End;

            For I := 1 To XValues.Count Do
                Polygon.Vertex[I] := Point(
                    MilsToCoord(StrToIntDef(XValues[I-1], 0)),
                    MilsToCoord(StrToIntDef(YValues[I-1], 0)));

            SchServer.ProcessControl.PreProcess(SchLib, '');
            SetOwnerPartFrom(Polygon, Component, OwnerStr);
            ViaLib := AddLibSymbolPrim(SchLib, Component, Polygon);
            SchServer.ProcessControl.PostProcess(SchLib, 'Edit');

            MarkLibDirty(SchLib);
            Try SchLib.GraphicallyInvalidate; Except End;
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"vertices":' + IntToStr(XValues.Count)
                + ',"shown":' + BoolToJsonStr(ViaLib) + '}');
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

    Component := LookupLibComponent(SchLib, CompName);
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

    Component := LookupLibComponent(SchLib, WantName);
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
                ',"owner_part_id":' + IntToStr(Pin.OwnerPartId) +
                { 0 is the normal display; 1 and up are alternate modes, which  }
                { repeat a part's pins at other places and read as duplicates.  }
                ',"display_mode":' + IntToStr(Pin.OwnerPartDisplayMode) + '}';
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

{ Pins on every part of a library symbol, the count lib_get_component_details }
{ reports as pin_count.                                                        }
Function CountComponentPins(Comp : ISch_Component) : Integer;
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
Begin
    Result := 0;
    If Comp = Nil Then Exit;
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(ePin));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Inc(Result);
            Obj := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;
End;

Function Lib_CopyComponent(Params : String; RequestId : String) : String;
Var
    SourceLibPath, DestLibPath, FocusedPath, SourceName, NewName : String;
    OverwriteStr, RespJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    SourceLib, DestLib : ISch_Lib;
    SourceComp, NewComp, Existing, Verify, Shown : ISch_Component;
    Overwrite, SameLib, Overwrote : Boolean;
    SourcePins, CopyPins : Integer;
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    SourceComp := LookupLibComponent(SourceLib, SourceName);
    If SourceComp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'Source component not found in ' + SourceLibPath + ': ' + SourceName);
        Exit;
    End;

    { A SYMBOL NOBODY HAS SHOWN MAY NOT BE LOADED YET. MEASURED: the first of
      several copies sent right after the library opened came out with its
      parameters but no pins, no graphics and no Value, and still reported
      verified. Every later copy of the same source was whole. When the
      source reads no pins, show it, which loads it, and look it up again. }
    SourcePins := CountComponentPins(SourceComp);
    If SourcePins = 0 Then
    Begin
        Shown := SelectLibComponentPart(SourceName, 0);
        If Shown <> Nil Then SourceComp := Shown;
        SourcePins := CountComponentPins(SourceComp);
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
    { In memory only: a miss is the usual answer, and the reopening lookup   }
    { would close DestLib under us and the copy would land on nothing. See   }
    { FindLibComponentInMemory.                                             }
    Existing := FindLibComponentInMemory(DestLib, NewName);
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
    { RE-ASSERT THE NAME. AddSchComponent overrides LibReference with an   }
    { auto-generated Component_<N> on the second and later additions to a  }
    { SchLib in one session, so the assignment made before the add does    }
    { not survive it. Lib_CreateSymbol hit this and re-asserts for exactly }
    { the same reason; the copy path did not, so the clone landed under an }
    { auto name and every later lookup of new_name missed it.              }
    NewComp.LibReference := NewName;
    SchServer.ProcessControl.PostProcess(DestLib, 'Edit');

    { REGISTER THE NEW COMPONENT, or it does not reach disk. Lib_CreateSymbol
      broadcasts this and persists; this path did not and did not, which is
      the whole difference between the two. Without the broadcast the symbol
      lives in the data model -- lib_get_component_details reads it back in
      full, and the copy reports verified -- while the document is never told
      anything was added, so every save route writes nothing.

      MEASURED 2026-09-21: a copied component read back correctly while the
      .SchLib stayed byte-identical at 662016 bytes across app_save_all,
      WorkspaceManager:SaveObject and Altium's own File > Save, with zero
      occurrences of the new name. Creating the same symbol from scratch
      grew the file, because that path registers.

      source=Nil, dest=Nil, broadcast: the new-component pattern from
      Altium's own createcomp_in_lib.pas, not the per-primitive
      SchRegisterObject(Container, Obj) which sends from the container. }
    Try
        SchServer.RobotManager.SendMessage(
            Nil, Nil, SCHM_PrimitiveRegistration,
            NewComp.I_ObjectAddress);
    Except End;

    DestLib.CurrentSchComponent := NewComp;
    LastCreatedLibComponent := NewComp;
    LastCreatedLibComponentName := NewName;
    Try DestLib.GraphicallyInvalidate; Except End;
    MarkLibDirty(DestLib);

    { VERIFY, rather than reporting the issuing of the work. Measured on   }
    { AD26 at script 2026.08.25.6: this returned success:true while the    }
    { component count stayed flat and new_name resolved nowhere.           }
    Verify := Nil;
    Try Verify := LookupLibComponent(DestLib, NewName); Except End;
    If Verify = Nil Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('source_library', SourceLibPath) + ',' +
                JsonStr('dest_library', DestLibPath) + ',' +
                JsonStr('source', SourceName) + ',' +
                JsonStr('new_name', NewName) + ',' +
                JsonStr('reason', 'the copy was added but does not resolve '
                    + 'in the destination library afterwards, so nothing '
                    + 'was written under that name. Read the library back '
                    + 'before relying on this having worked.')
            ));
        Exit;
    End;

    { A NAME THAT RESOLVES IS NOT A WHOLE COPY. Compare what arrived with
      what was copied, so an empty symbol is reported instead of verified. }
    CopyPins := CountComponentPins(Verify);
    If CopyPins <> SourcePins Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('source_library', SourceLibPath) + ',' +
                JsonStr('dest_library', DestLibPath) + ',' +
                JsonStr('source', SourceName) + ',' +
                JsonStr('new_name', NewName) + ',' +
                JsonInt('source_pins', SourcePins) + ',' +
                JsonInt('copy_pins', CopyPins) + ',' +
                JsonStr('reason', 'the copy exists under new_name but does not '
                    + 'hold the source''s pins, so it is incomplete. Copy again '
                    + 'with overwrite=true, which replaces it.')
            ));
        Exit;
    End;

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
        ',"overwrote":' + BoolToJsonStr(Overwrote) +
        ',"pins":' + IntToStr(CopyPins) +
        ',"verified":true}';
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
    Hidden, OwnerExplicit, ViaLib : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Pin : ISch_Pin;
    Loc : TLocation;
Begin
    ViaLib := False;
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

            ViaLib := AddLibSymbolPrim(SchLib, Component, Pin);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);
    Try SchLib.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + ',"shown":' + BoolToJsonStr(ViaLib) + '}');
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
    OwnerExplicit, Bold, Italic, ViaLib : Boolean;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    Lbl : ISch_Label;
    Loc : TLocation;
    FontMgr : ISch_FontManager;
Begin
    ViaLib := False;
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

            ViaLib := AddLibSymbolPrim(SchLib, Component, Lbl);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);
    Try SchLib.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + ',"shown":' + BoolToJsonStr(ViaLib) + '}');
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
    ViaLib : Boolean;
Begin
    ViaLib := False;
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

            SetOwnerPartFrom(Line, Component, GetBatchField(Op, 'owner_part_id'));
            ViaLib := AddLibSymbolPrim(SchLib, Component, Line);
            Inc(Added);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Edit');
    End;

    MarkLibDirty(SchLib);
    Try SchLib.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"added":' + IntToStr(Added) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + ',"shown":' + BoolToJsonStr(ViaLib) + '}');
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
    PinIter, ParamIter, BodyIter : ISch_Iterator;
    Component : ISch_Component;
    Pin : ISch_Pin;
    Param : ISch_Parameter;
    DesigLabel : ISch_Label;
    BodyObj : ISch_GraphicalObject;
    Limit, Count, MismatchCount, PinCount, NumComps, I : Integer;
    RectCount, PolyCount : Integer;
    DesigFontId, DesigColor : Integer;
    DesigJson, CommentJson, PinList, StyleList, ElecStr, ResultsJson, Entry, CompName : String;
    PinLabelHidden : Boolean;
    First, FirstPin, FirstStyle, Mismatched : Boolean;
    Refreshed : Boolean;
    Fresh : ISch_Lib;
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
        Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;

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

    { The names below come from the FILE, so a symbol not yet saved is not  }
    { listed (MEASURED: two symbols made this session were missing). With   }
    { refresh the library is saved and read back in first.                  }
    Refreshed := False;
    If LowerCase(ExtractJsonValue(Params, 'refresh')) = 'true' Then
    Begin
        Fresh := RefreshSchLibFromDisk(LibPath);
        If Fresh <> Nil Then
        Begin
            SchLib := Fresh;
            Refreshed := True;
        End;
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
    LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
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

            Component := LookupLibComponent(SchLib, CompName);
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

                    { What the body is drawn with, so a caller can tell an    }
                    { amplifier drawn as a box from one drawn as a triangle.   }
                    RectCount := 0;
                    PolyCount := 0;
                    BodyIter := Component.SchIterator_Create;
                    BodyIter.AddFilter_ObjectSet(MkSet(eRectangle, ePolygon));
                    Try
                        BodyObj := BodyIter.FirstSchObject;
                        While BodyObj <> Nil Do
                        Begin
                            If BodyObj.ObjectId = eRectangle Then Inc(RectCount)
                            Else Inc(PolyCount);
                            BodyObj := BodyIter.NextSchObject;
                        End;
                    Finally
                        Component.SchIterator_Destroy(BodyIter);
                    End;
                    Entry := Entry + ',"body":{"rectangles":' + IntToStr(RectCount)
                        + ',"polygons":' + IntToStr(PolyCount) + '}'
                        + ',"description":"' + EscapeJsonString(Component.ComponentDescription) + '"';
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
        ',"refreshed":' + BoolToJsonStr(Refreshed) +
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
            Component := LookupLibComponent(SchLib, CompName);
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
            LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
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
                    Component := LookupLibComponent(SchLib, CompName);
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

{ Move a label and/or set its text. X and Y are in mils, in the symbol's own  }
{ frame, the same frame lib_add_symbol_text and lib_add_pins place in. An     }
{ empty string leaves that field alone. Returns 1 if anything changed, 0 if   }
{ it already matched, -1 if the write raised.                                 }
{                                                                              }
{ The text is what makes Comment show a parameter: "=Partnumber" displays the }
{ part's Partnumber value.                                                     }
Function ApplyLabelPlacement(Lbl : ISch_Label; XStr, YStr, TextStr : String;
    OnlyMismatched : Boolean) : Integer;
Var
    Loc : TLocation;
    WantX, WantY : TCoord;
    Changed : Boolean;
Begin
    Result := 0;
    If Lbl = Nil Then Exit;
    If (XStr = '') And (YStr = '') And (TextStr = '') Then Exit;
    Try
        Loc := Lbl.Location;
        WantX := Loc.X;
        WantY := Loc.Y;
        If XStr <> '' Then WantX := MilsToCoord(StrToIntDef(XStr, 0));
        If YStr <> '' Then WantY := MilsToCoord(StrToIntDef(YStr, 0));
        Changed := (WantX <> Loc.X) Or (WantY <> Loc.Y)
            Or ((TextStr <> '') And (Lbl.Text <> TextStr));
        If OnlyMismatched And (Not Changed) Then Exit;
        SchBeginModify(Lbl);
        { Location is a by-value record: read, mutate, write back. }
        Loc.X := WantX;
        Loc.Y := WantY;
        Lbl.Location := Loc;
        { A designator or comment left on auto-position is put back where
          Altium wants it, so the move read back in memory and never reached
          the saved file. Same as the component move in Generic.pas. }
        If (XStr <> '') Or (YStr <> '') Then
            Try Lbl.Autoposition := False; Except End;
        If TextStr <> '' Then Lbl.Text := TextStr;
        SchEndModify(Lbl);
        Result := 1;
    Except
        Result := -1;
    End;
End;

Function ApplyOpToComponent(Component : ISch_Component; Target : String;
    FontIdStr, ColorStr, IsHiddenStr, OrientationStr, JustificationStr : String;
    XStr, YStr, TextStr : String;
    OnlyMismatched : Boolean) : Integer;
{ Returns 0=already_compliant, 1=modified, 2=missing_target, 3=failed.        }
Var
    HasFontId, HasColor, HasIsHidden, HasOrientation, HasJustification : Boolean;
    NewFontId, NewColor, NewOrientation, NewJustification : Integer;
    NewIsHidden, Found : Boolean;
    Lbl : ISch_Label;
    AR, PR : Integer;
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
    AR := 0;
    If HasFontId Or HasColor Or HasIsHidden Or HasOrientation Or HasJustification Then
        AR := ApplyLabelFormat(Lbl, HasFontId, NewFontId,
            HasColor, NewColor, HasIsHidden, NewIsHidden,
            HasOrientation, NewOrientation, HasJustification, NewJustification,
            OnlyMismatched);
    PR := ApplyLabelPlacement(Lbl, XStr, YStr, TextStr, OnlyMismatched);
    If (AR = -1) Or (PR = -1) Then Result := 3
    Else If (AR = 1) Or (PR = 1) Then Result := 1
    Else Result := 0;
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
        OpOrientation, OpJustification, OpXs, OpYs, OpTexts : TStringList;
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    OpXs := TStringList.Create;
    OpYs := TStringList.Create;
    OpTexts := TStringList.Create;
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
            OpXs.Add(GetBatchField(OpStr, 'x'));
            OpYs.Add(GetBatchField(OpStr, 'y'));
            OpTexts.Add(GetBatchField(OpStr, 'text'));
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
                Component := LookupLibComponent(SchLib, CompName);
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
                        OpOrientation[J], OpJustification[J],
                        OpXs[J], OpYs[J], OpTexts[J], OnlyMismatched);
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
                LibReader := SchServer.CreateLibCompInfoReader(SafeSchLibPath(LibPath));
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
                        Component := LookupLibComponent(SchLib, CurCompName);
                        If Component = Nil Then Continue;
                        Inc(Total);

                        For J := 0 To NumOps - 1 Do
                        Begin
                            OpResult := ApplyOpToComponent(Component, OpTargets[J],
                                OpFontIds[J], OpColors[J], OpIsHidden[J],
                                OpOrientation[J], OpJustification[J],
                                OpXs[J], OpYs[J], OpTexts[J], OnlyMismatched);
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
        OpXs.Free;
        OpYs.Free;
        OpTexts.Free;
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
{ mode = 'raise' (default) updates only when the 3D model is TALLER than the }
{ current Footprint.Height. That protects a manually-set "I know this part   }
{ is 5mm despite the model being 3mm" value, and it is the right default.     }
{                                                                              }
{ mode = 'match' also LOWERS a height to the model. Raising alone cannot fix  }
{ the opposite fault, and an over-tall height is the more damaging of the two: }
{ a footprint claiming 50mm when the part is 3mm fails placement-collision    }
{ DRC against everything near it and blocks placements that are actually fine, }
{ where a too-low height merely fails to catch a real collision.              }
{                                                                              }
{ NEITHER MODE WRITES ZERO. A footprint with no 3D body yields no measurement, }
{ and writing the 0 that implies would silently disable the very DRC rule this }
{ handler exists to arm, across every part a library never modelled. Those are }
{ counted and named as without_model instead, which is a finding worth having. }
Function Lib_UpdateFootprintHeightsFrom3D(Params : String; RequestId : String) : String;
Var
    CurLib : IPCB_Library;
    LibIter : IPCB_LibraryIterator;
    Footprint, SavedCurrent : IPCB_LibComponent;
    GrIter : IPCB_GroupIterator;
    Body : IPCB_ComponentBody;
    Updated, Inspected, Lowered, NoModel : Integer;
    Items, FpName, Mode, NoModelNames : String;
    First, FirstNoModel, ShouldWrite : Boolean;
    OldH, NewH : TCoord;
Begin
    Mode := LowerCase(Trim(ExtractJsonValue(Params, 'mode')));
    If Mode = '' Then Mode := 'raise';
    If (Mode <> 'raise') And (Mode <> 'match') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_MODE',
            'mode must be "raise" (only increase a height, the default) or '
            + '"match" (also lower a height to the model). Got: ' + Mode);
        Exit;
    End;
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
    Lowered := 0;
    NoModel := 0;
    Items := '';
    NoModelNames := '';
    First := True;
    FirstNoModel := True;

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
                FpName := '';
                Try FpName := Footprint.Name; Except End;

                If NewH <= 0 Then
                Begin
                    { No 3D body, so nothing measured. Never written to 0. }
                    Inc(NoModel);
                    If Not FirstNoModel Then NoModelNames := NoModelNames + ',';
                    FirstNoModel := False;
                    NoModelNames := NoModelNames + '"'
                        + EscapeJsonString(FpName) + '"';
                End
                Else
                Begin
                    If Mode = 'match' Then ShouldWrite := (NewH <> OldH)
                    Else ShouldWrite := (NewH > OldH);
                    If ShouldWrite Then
                    Begin
                        Footprint.Height := NewH;
                        Inc(Updated);
                        If NewH < OldH Then Inc(Lowered);
                        If Not First Then Items := Items + ',';
                        First := False;
                        Items := Items + JsonObj(
                            JsonStr('name', FpName) + ',' +
                            JsonFloat('old_height_mm', CoordToMM(OldH)) + ',' +
                            JsonFloat('new_height_mm', CoordToMM(NewH))
                        );
                    End;
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
        MarkDocDirtyByPath(CurLib.Board.FileName);
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('mode', Mode) + ',' +
            JsonInt('inspected', Inspected) + ',' +
            JsonInt('updated', Updated) + ',' +
            JsonInt('lowered', Lowered) + ',' +
            JsonInt('without_model', NoModel) + ',' +
            JsonRaw('without_model_names', '[' + NoModelNames + ']') + ',' +
            JsonStr('without_model_note',
                'These have no 3D body, so no height could be measured and '
                + 'none was written. Their Height is whatever it already was, '
                + 'and a 0 there leaves placement-collision DRC disabled for '
                + 'that part.') + ',' +
            JsonRaw('items', '[' + Items + ']')
        ));
End;


{..............................................................................}
{ Lib_SetFootprintHeight                                                       }
{                                                                              }
{ Write Footprint.Height directly, up or down, on one named footprint or on    }
{ the library's current component.                                             }
{                                                                              }
{ The sweep above can only derive a height from a 3D body, which leaves two    }
{ cases it cannot serve: a part with no model, and a part whose model is       }
{ wrong. Before this there was no setter at all, so a footprint carrying an    }
{ absurd height could be read but not corrected from here.                     }
{                                                                              }
{ Zero is ACCEPTED but is not a neutral value: it disables the                 }
{ placement-collision rule for that footprint rather than relaxing it. The     }
{ reply says so when it is written, because "cleared the height" and "turned   }
{ off the check" are the same edit and only one of them sounds harmless.       }
{..............................................................................}

Function Lib_SetFootprintHeight(Params : String; RequestId : String) : String;
Var
    CurLib : IPCB_Library;
    LibIter : IPCB_LibraryIterator;
    Footprint, Target, SavedCurrent : IPCB_LibComponent;
    FpWanted, HeightStr, FpName, Note : String;
    HeightMM : Double;
    OldH, NewH : TCoord;
Begin
    FpWanted := Trim(ExtractJsonValue(Params, 'footprint_name'));
    HeightStr := Trim(ExtractJsonValue(Params, 'height_mm'));

    If HeightStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'height_mm is required');
        Exit;
    End;

    { StrToFloatDef, not StrToFloat in a Try. A raising conversion here
      halts on break-on-exception in the Script IDE even though the
      handler would have swallowed it, which is how a bad parameter
      turns into a stopped debugger. The sentinel doubles as the
      rejection for a negative value. }
    HeightMM := StrToFloatDef(HeightStr, -1);
    If HeightMM < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_HEIGHT',
            'height_mm must be a non-negative number in millimetres. Got: '
            + HeightStr);
        Exit;
    End;

    CurLib := PCBServer.GetCurrentPCBLibrary;
    If CurLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB Library document focused');
        Exit;
    End;

    SavedCurrent := CurLib.CurrentComponent;
    Target := Nil;

    If FpWanted = '' Then Target := SavedCurrent
    Else
    Begin
        LibIter := CurLib.LibraryIterator_Create;
        Try
            LibIter.SetState_FilterAll;
            Footprint := LibIter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;
                If UpperCase(FpName) = UpperCase(FpWanted) Then
                Begin
                    Target := Footprint;
                    Break;
                End;
                Footprint := LibIter.NextPCBObject;
            End;
        Finally
            CurLib.LibraryIterator_Destroy(LibIter);
        End;
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
            'No footprint named ' + FpWanted + ' in the focused library. '
            + 'Call lib_get_footprints to see what it holds.');
        Exit;
    End;

    FpName := '';
    Try FpName := Target.Name; Except End;
    OldH := Target.Height;
    NewH := MMToCoord(HeightMM);
    Target.Height := NewH;

    If SavedCurrent <> Nil Then CurLib.CurrentComponent := SavedCurrent;
    Try CurLib.Board.ViewManager_FullUpdate; Except End;
    If OldH <> NewH Then MarkDocDirtyByPath(CurLib.Board.FileName);

    Note := '';
    If NewH = 0 Then
        Note := 'A height of 0 disables the placement-collision rule for '
              + 'this footprint rather than relaxing it, so nothing will be '
              + 'flagged against it however tall the real part is.';

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('name', FpName) + ',' +
            JsonFloat('old_height_mm', CoordToMM(OldH)) + ',' +
            JsonFloat('new_height_mm', CoordToMM(NewH)) + ',' +
            JsonBool('changed', OldH <> NewH) + ',' +
            JsonBool('saved', False) + ',' +
            JsonStr('save_note',
                'The library is modified in memory and NOT saved. Review the '
                + 'change, then save it in Altium.') + ',' +
            JsonStr('note', Note)
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

    { LookupLibComponent, not the raw index. GetState_SchComponentByLibRef
      asks an index the library only builds when it LOADS, so a symbol
      created earlier in this same session is invisible to it: author a
      symbol, then move its pins to a sub-part in the very next call, and
      the second call reports COMPONENT_NOT_FOUND for a symbol that is
      plainly there. The helper tries the index, then walks the document,
      then the reference the script still holds from creation. }
    Component := LookupLibComponent(SchLib, WantName);
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

{..............................................................................}
{ Lib_GetInstalledLibraries - what the environment has installed.              }
{                                                                              }
{ install_library and uninstall_library have been here from the start and      }
{ nothing could report the result, so the only way to answer "what is          }
{ installed?" was to read the registry from outside Altium. lib_search only    }
{ walks SchLibs already open in the workspace, which is a different and much   }
{ smaller set.                                                                 }
{                                                                              }
{ TWO LISTS, NOT ONE, and they are not interchangeable. Installed* is what is  }
{ switched on for the current environment; Available* is every library known   }
{ to it. The TYPE is published only on the Available side, so the type of an   }
{ installed library is found by matching its path across, which is what the    }
{ published example scripts do.                                                }
{                                                                              }
{ The type ordinal is returned as an Integer and named separately rather than  }
{ compared against enum identifiers: an identifier this build does not declare }
{ faults at runtime as a modal the polling loop cannot catch, and the ordinals }
{ are stable where the names are not.                                          }
{ Params: with_counts (optional, "false" skips the per-library component count,}
{         which opens each library and is the expensive half).                 }
{..............................................................................}
Function LibTypeName(Ordinal : Integer) : String;
Begin
    { TLibraryType, in declaration order. }
    If Ordinal = 0 Then Result := 'integrated'
    Else If Ordinal = 1 Then Result := 'source'
    Else If Ordinal = 2 Then Result := 'datafile'
    Else If Ordinal = 3 Then Result := 'database'
    Else If Ordinal = 4 Then Result := 'none'
    Else If Ordinal = 5 Then Result := 'query'
    Else If Ordinal = 6 Then Result := 'design_items'
    Else Result := 'unknown';
End;

Function InstalledLibTypeOrdinal(LibPath : String) : Integer;
Var
    I, AvailCount : Integer;
Begin
    { -1 means the path is installed but absent from the Available list, }
    { which is a real state worth reporting rather than flattening to a  }
    { type name that would then be wrong.                                }
    Result := -1;
    AvailCount := 0;
    Try AvailCount := IntegratedLibraryManager.AvailableLibraryCount; Except End;
    For I := 0 To AvailCount - 1 Do
    Begin
        Try
            If IntegratedLibraryManager.AvailableLibraryPath(I) = LibPath Then
            Begin
                Result := IntegratedLibraryManager.AvailableLibraryType(I);
                Break;
            End;
        Except
        End;
    End;
End;

Function Lib_GetInstalledLibraries(Params : String; RequestId : String) : String;
Var
    JsonItems, LibPath, WithCounts : String;
    I, InstCount, AvailCount, TypeOrd, CompCount : Integer;
    First, WantCounts : Boolean;
Begin
    If IntegratedLibraryManager = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MANAGER', 'IntegratedLibraryManager unavailable');
        Exit;
    End;

    WithCounts := ExtractJsonValue(Params, 'with_counts');
    WantCounts := (WithCounts <> 'false') And (WithCounts <> 'False') And (WithCounts <> '0');

    InstCount := 0;
    Try InstCount := IntegratedLibraryManager.InstalledLibraryCount; Except End;
    AvailCount := 0;
    Try AvailCount := IntegratedLibraryManager.AvailableLibraryCount; Except End;

    JsonItems := '';
    First := True;
    For I := 0 To InstCount - 1 Do
    Begin
        LibPath := '';
        Try LibPath := IntegratedLibraryManager.InstalledLibraryPath(I); Except End;
        If LibPath = '' Then Continue;

        TypeOrd := InstalledLibTypeOrdinal(LibPath);

        { GetComponentCount opens the library to answer, so it is the one }
        { expensive call here and the caller can decline it. -1 says not  }
        { asked, which is not the same as an empty library.               }
        CompCount := -1;
        If WantCounts Then
        Begin
            Try CompCount := IntegratedLibraryManager.GetComponentCount(LibPath); Except End;
        End;

        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '{"library_path":"' + EscapeJsonString(LibPath) + '"'
            + ',"file_name":"' + EscapeJsonString(ExtractFileName(LibPath)) + '"'
            + ',"library_type":"' + LibTypeName(TypeOrd) + '"'
            + ',"library_type_ordinal":' + IntToStr(TypeOrd)
            + ',"component_count":' + IntToStr(CompCount) + '}';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"libraries":[' + JsonItems + ']'
        + ',"installed_count":' + IntToStr(InstCount)
        + ',"available_count":' + IntToStr(AvailCount)
        + ',"counts_included":' + BoolToJsonStr(WantCounts) + '}');
End;

{..............................................................................}
{ Managed (Workspace) components.                                              }
{                                                                              }
{ A managed part has no library file. SchServer.LoadComponent takes it by an  }
{ identifier kind, the Workspace name and its Design Item ID. Every managed   }
{ call sits in its own Try, because a method this host does not have fails at }
{ run time, and the reply then names the call instead of coming back empty.   }
{ A part number is not turned into a Design Item ID here; the caller brings   }
{ the ID.                                                                      }
{..............................................................................}

{ VaultNameOnSheet - the Workspace name Altium wrote onto the managed parts  }
{ of one sheet. A part is managed when it carries a VaultGUID. One design    }
{ was seen carrying the same Workspace under two names, its current one with }
{ kind 4 and an older one with kind 1, so a kind 4 name is taken first and   }
{ any other kind is kept in OtherName. Seen collects kind:name pairs.        }
Procedure VaultNameOnSheet(SchDoc : ISch_Document; Var Name : String;
    Var OtherName : String; Var Seen : String);
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Ident, Guid, Entry : String;
    Kind : Integer;
Begin
    If SchDoc = Nil Then Exit;
    Iter := SchDoc.SchIterator_Create;
    If Iter = Nil Then Exit;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Try
                Comp := Obj;
                Ident := '';
                Guid := '';
                Kind := -1;
                Try Ident := Comp.LibraryIdentifier; Except End;
                Ident := Trim(Ident);
                Try Guid := Comp.VaultGUID; Except End;
                Try Kind := Comp.LibIdentifierKind; Except End;
                If (Guid <> '') And (Ident <> '') Then
                Begin
                    Entry := IntToStr(Kind) + ':' + Ident;
                    If Pos('|' + Entry + '|', '|' + Seen + '|') = 0 Then
                    Begin
                        If Seen <> '' Then Seen := Seen + '|';
                        Seen := Seen + Entry;
                    End;
                    If Kind = LIB_IDENT_KIND_VAULT_NAME Then
                    Begin
                        If Name = '' Then Name := Ident;
                    End
                    Else If OtherName = '' Then
                        OtherName := Ident;
                End;
            Except End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
End;

{ OpenSheetPaths - the active sheet, then every other schematic of an open   }
{ project that Altium already has open, as paths joined by |. A sheet that  }
{ is not open is left out, so walking the list never opens a document.      }
Function OpenSheetPaths(Dummy : Integer) : String;
Var
    SchDoc : ISch_Document;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Active, FilePath : String;
    I, J : Integer;
    IsOpen : Boolean;
Begin
    Result := '';
    Active := '';
    SchDoc := Nil;
    Try SchDoc := SchServer.GetCurrentSchDocument; Except End;
    If SchDoc <> Nil Then
    Begin
        Try Active := SchDoc.DocumentName; Except End;
    End;
    If Active <> '' Then Result := Active;

    Workspace := Nil;
    Try Workspace := GetWorkspace; Except End;
    If Workspace = Nil Then Exit;
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Workspace.DM_Projects(I);
        If Project = Nil Then Continue;
        For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(J);
            If Doc = Nil Then Continue;
            If Doc.DM_DocumentKind <> 'SCH' Then Continue;
            FilePath := Doc.DM_FullPath;
            If UpperCase(FilePath) = UpperCase(Active) Then Continue;
            If Pos('|' + UpperCase(FilePath) + '|', '|' + UpperCase(Result) + '|') > 0 Then Continue;
            IsOpen := False;
            Try IsOpen := Client.IsDocumentOpen(FilePath); Except End;
            If Not IsOpen Then Continue;
            If Result <> '' Then Result := Result + '|';
            Result := Result + FilePath;
        End;
    End;
End;

{ NextPathField - take the text up to the next | off Remaining.              }
Function NextPathField(Var Remaining : String) : String;
Var
    Bar : Integer;
Begin
    Bar := Pos('|', Remaining);
    If Bar = 0 Then
    Begin
        Result := Remaining;
        Remaining := '';
    End
    Else
    Begin
        Result := Copy(Remaining, 1, Bar - 1);
        Remaining := Copy(Remaining, Bar + 1, Length(Remaining));
    End;
End;

{ VaultNameFromDesign - read the Workspace name off the open sheets, the     }
{ active one first. A new sheet has no managed parts yet, and that is the   }
{ usual target of a placement.                                               }
Function VaultNameFromDesign(Var Name : String; Var Seen : String;
    Var FromOtherKind : Boolean) : Boolean;
Var
    SchDoc : ISch_Document;
    OtherName, Paths, FilePath : String;
Begin
    Name := '';
    Seen := '';
    OtherName := '';
    FromOtherKind := False;
    Paths := OpenSheetPaths(0);
    While (Paths <> '') And (Name = '') Do
    Begin
        FilePath := NextPathField(Paths);
        If FilePath = '' Then Continue;
        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(FilePath); Except End;
        Try VaultNameOnSheet(SchDoc, Name, OtherName, Seen); Except End;
    End;
    If (Name = '') And (OtherName <> '') Then
    Begin
        Name := OtherName;
        FromOtherKind := True;
    End;
    Result := Name <> '';
End;

{ ResolveVaultName - the name the caller passed, else the one on the design. }
{ Note says where it came from. Nothing else is tried: the library manager's }
{ available list does not have to include a connected Workspace, and its     }
{ type ordinal is a TLibraryType, which has no Workspace value to match on.  }
Function ResolveVaultName(Requested : String; Var Name : String;
    Var Note : String) : Boolean;
Var
    Seen : String;
    FromOtherKind : Boolean;
Begin
    Note := '';
    Name := Trim(Requested);
    If Name <> '' Then
    Begin
        Result := True;
        Exit;
    End;
    Result := VaultNameFromDesign(Name, Seen, FromOtherKind);
    If Result And FromOtherKind Then
        Note := 'Workspace name read off a placed part addressed by another '
            + 'identifier kind, so it may be an older name for the Workspace '
            + '(seen: ' + Seen + ')'
    Else If Result Then
        Note := 'Workspace name read off a placed part (seen: ' + Seen + ')';
End;

Function VaultNotResolvedMessage(Dummy : Integer) : String;
Begin
    Result := 'No Workspace name was passed and none could be read off the '
        + 'design: no open sheet has a managed part on it. Pass vault= with '
        + 'the Workspace name as Altium shows it, or open a sheet of this '
        + 'design that already has managed parts and try again. If neither '
        + 'works, ask the user for the name rather than making one up.';
End;

{..............................................................................}
{ Lib_GetVaultName - the Workspace name the managed tools would use.          }
{..............................................................................}
Function Lib_GetVaultName(Params : String; RequestId : String) : String;
Var
    Name, Seen : String;
    FromOtherKind, Found : Boolean;
Begin
    Found := VaultNameFromDesign(Name, Seen, FromOtherKind);
    Result := BuildSuccessResponse(RequestId,
        '{"found":' + BoolToJsonStr(Found)
        + ',"vault":"' + EscapeJsonString(Name) + '"'
        + ',"other_kind":' + BoolToJsonStr(FromOtherKind)
        + ',"seen":"' + EscapeJsonString(Seen) + '"}');
End;

{..............................................................................}
{ Lib_GetVaultComponent - whether one managed item can be placed, without     }
{ placing it.                                                                  }
{                                                                              }
{ The answer comes from SchServer.LoadComponent, the call placement makes.    }
{ The library manager's FindComponentSymbol said no for every kind on items   }
{ that then loaded and placed with the GUIDs Altium's own placements carry,   }
{ so it is not asked. The loaded component belongs to no document, so it is   }
{ destroyed again and nothing reaches a sheet. The kind asked for is tried    }
{ first, then the others. The library manager's other lookups are reported    }
{ as they come back, and none is modal.                                       }
{ Params: design_item_id (required), vault, lib_identifier_kind.              }
{..............................................................................}
Function Lib_GetVaultComponent(Params : String; RequestId : String) : String;
Var
    DesignItemId, VaultName, Note, Diag, Probe : String;
    LoadedId, VaultGuid, ItemGuid, RevGuid : String;
    LibPath, DisplayPath, LifeCycle, Revisions, PlaceParams : String;
    Asked, Kind, LoadedKind, Slot : Integer;
    Raised, Callable, HasRevisions : Boolean;
    Comp : ISch_Component;
Begin
    DesignItemId := Trim(ExtractJsonValue(Params, 'design_item_id'));
    If DesignItemId = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'design_item_id is required');
        Exit;
    End;
    If Not ResolveVaultName(ExtractJsonValue(Params, 'vault'), VaultName, Note) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'VAULT_NOT_RESOLVED',
            VaultNotResolvedMessage(0));
        Exit;
    End;
    Asked := StrToIntDef(ExtractJsonValue(Params, 'lib_identifier_kind'),
        LIB_IDENT_KIND_VAULT_NAME);

    Probe := '';
    Callable := False;
    LoadedKind := -1;
    LoadedId := '';
    VaultGuid := '';
    ItemGuid := '';
    RevGuid := '';
    For Slot := 0 To LIB_IDENT_KIND_VAULT_NAME + 1 Do
    Begin
        If LoadedKind >= 0 Then Break;
        If Slot = 0 Then Kind := Asked
        Else
        Begin
            Kind := Slot - 1;
            If Kind = Asked Then Continue;
        End;
        If (Kind < 0) Or (Kind > LIB_IDENT_KIND_VAULT_NAME) Then Continue;

        Comp := Nil;
        Raised := True;
        Try
            Comp := SchServer.LoadComponent(Kind, VaultName, DesignItemId);
            Raised := False;
        Except End;

        If Probe <> '' Then Probe := Probe + ',';
        If Raised Then Probe := Probe + IntToStr(Kind) + ':raised'
        Else If Comp = Nil Then Probe := Probe + IntToStr(Kind) + ':nil'
        Else Probe := Probe + IntToStr(Kind) + ':loaded';
        If Not Raised Then Callable := True;

        If Comp <> Nil Then
        Begin
            LoadedKind := Kind;
            Try LoadedId := Comp.DesignItemId; Except End;
            Try VaultGuid := Comp.VaultGUID; Except End;
            Try ItemGuid := Comp.ItemGUID; Except End;
            Try RevGuid := Comp.RevisionGUID; Except End;
            Try SchServer.DestroySchObject(Comp); Except End;
        End;
    End;

    If LoadedKind >= 0 Then Kind := LoadedKind Else Kind := Asked;
    Diag := '';
    LibPath := '';
    DisplayPath := '';
    LifeCycle := '';
    Revisions := '';
    PlaceParams := '';
    HasRevisions := False;
    If IntegratedLibraryManager = Nil Then
        Diag := Diag + 'IntegratedLibraryManager unavailable;'
    Else
    Begin
        Try LibPath := IntegratedLibraryManager.FindComponentLibraryPath(
            Kind, VaultName, DesignItemId);
        Except Diag := Diag + 'FindComponentLibraryPath raised;'; End;
        Try DisplayPath := IntegratedLibraryManager.FindComponentDisplayPath(
            Kind, VaultName, DesignItemId);
        Except Diag := Diag + 'FindComponentDisplayPath raised;'; End;
        Try LifeCycle := IntegratedLibraryManager.GetLifeCycleState(
            Kind, VaultName, DesignItemId);
        Except Diag := Diag + 'GetLifeCycleState raised;'; End;
        Try HasRevisions := IntegratedLibraryManager.GetOrderedRevisionList(
            Kind, VaultName, DesignItemId, Revisions);
        Except Diag := Diag + 'GetOrderedRevisionList raised;'; End;
        Try PlaceParams := IntegratedLibraryManager.GetComponentPlacementParameters(
            Kind, VaultName, DesignItemId);
        Except Diag := Diag + 'GetComponentPlacementParameters raised;'; End;
    End;

    If Not Callable Then
        Note := 'SchServer.LoadComponent raised for every kind, so this host '
            + 'cannot load managed parts and whether this one exists is unknown.'
    Else If LoadedKind < 0 Then
        Note := 'Nothing loaded for this ID from Workspace "' + VaultName
            + '" under any kind. A managed part placed by Altium can carry its '
            + 'ID with a revision on the end; read DesignItemId, '
            + 'LibraryIdentifier and LibIdentifierKind off one with obj_query '
            + 'and use them as they are.';

    Result := BuildSuccessResponse(RequestId,
        '{"design_item_id":"' + EscapeJsonString(DesignItemId) + '"'
        + ',"vault":"' + EscapeJsonString(VaultName) + '"'
        + ',"placeable":' + BoolToJsonStr(LoadedKind >= 0)
        + ',"loader_callable":' + BoolToJsonStr(Callable)
        + ',"lib_identifier_kind":' + IntToStr(LoadedKind)
        + ',"load_probe":"' + EscapeJsonString(Probe) + '"'
        + ',"loaded_design_item_id":"' + EscapeJsonString(LoadedId) + '"'
        + ',"vault_guid":"' + EscapeJsonString(VaultGuid) + '"'
        + ',"item_guid":"' + EscapeJsonString(ItemGuid) + '"'
        + ',"revision_guid":"' + EscapeJsonString(RevGuid) + '"'
        + ',"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"display_path":"' + EscapeJsonString(DisplayPath) + '"'
        + ',"lifecycle_state":"' + EscapeJsonString(LifeCycle) + '"'
        + ',"has_revisions":' + BoolToJsonStr(HasRevisions)
        + ',"revisions":"' + EscapeJsonString(Revisions) + '"'
        + ',"placement_parameters":"' + EscapeJsonString(PlaceParams) + '"'
        + ',"note":"' + EscapeJsonString(Note) + '"'
        + ',"diag":"' + EscapeJsonString(Diag) + '"}');
End;

{..............................................................................}
{ Lib_FindVaultComponents - managed parts on the open sheets whose comment,  }
{ library reference or Design Item ID is exactly Name (case ignored).        }
{                                                                              }
{ Every match is returned, one per Design Item ID, with the lifecycle state  }
{ the library manager reports for it. Only parts already placed on a sheet   }
{ Altium has open are searched: a search of the whole Workspace needs the   }
{ EDMS interfaces, whose methods no reference here documents, and calling a  }
{ method this host lacks stops the script.                                   }
{ Params: name (required).                                                   }
{..............................................................................}
Function Lib_FindVaultComponents(Params : String; RequestId : String) : String;
Var
    Name, Paths, FilePath, Items, Ids, Id, Comment, LibRef, Ident, Guid, Life : String;
    Rev : String;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Kind, Count, Sheets : Integer;
Begin
    Name := Trim(ExtractJsonValue(Params, 'name'));
    If Name = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'name is required');
        Exit;
    End;

    Items := '';
    Ids := '';
    Count := 0;
    Sheets := 0;
    Paths := OpenSheetPaths(0);
    While Paths <> '' Do
    Begin
        FilePath := NextPathField(Paths);
        If FilePath = '' Then Continue;
        SchDoc := Nil;
        Try SchDoc := SchServer.GetSchDocumentByPath(FilePath); Except End;
        If SchDoc = Nil Then Continue;
        Inc(Sheets);
        Iter := SchDoc.SchIterator_Create;
        If Iter = Nil Then Continue;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj := Iter.FirstSchObject;
            While Obj <> Nil Do
            Begin
                Try
                    Comp := Obj;
                    Guid := '';
                    Id := '';
                    Comment := '';
                    LibRef := '';
                    Try Guid := Comp.VaultGUID; Except End;
                    If Guid <> '' Then
                    Begin
                        Try Id := Comp.DesignItemId; Except End;
                        Try Comment := Comp.Comment.Text; Except End;
                        Try LibRef := Comp.LibReference; Except End;
                    End;
                    If (Guid <> '') And (Id <> '')
                        And ((UpperCase(Comment) = UpperCase(Name))
                          Or (UpperCase(LibRef) = UpperCase(Name))
                          Or (UpperCase(Id) = UpperCase(Name)))
                        And (Pos('|' + Id + '|', '|' + Ids + '|') = 0) Then
                    Begin
                        If Ids <> '' Then Ids := Ids + '|';
                        Ids := Ids + Id;
                        Ident := '';
                        Kind := LIB_IDENT_KIND_VAULT_NAME;
                        Rev := '';
                        Try Ident := Comp.LibraryIdentifier; Except End;
                        Try Kind := Comp.LibIdentifierKind; Except End;
                        Try Rev := Comp.RevisionGUID; Except End;
                        Life := '';
                        If IntegratedLibraryManager <> Nil Then
                        Begin
                            Try Life := IntegratedLibraryManager.GetLifeCycleState(
                                Kind, Ident, Id);
                            Except End;
                        End;
                        If Items <> '' Then Items := Items + ',';
                        Items := Items + '{"design_item_id":"' + EscapeJsonString(Id) + '"'
                            + ',"comment":"' + EscapeJsonString(Comment) + '"'
                            + ',"lib_reference":"' + EscapeJsonString(LibRef) + '"'
                            + ',"vault":"' + EscapeJsonString(Ident) + '"'
                            + ',"lib_identifier_kind":' + IntToStr(Kind)
                            + ',"revision_guid":"' + EscapeJsonString(Rev) + '"'
                            + ',"lifecycle_state":"' + EscapeJsonString(Life) + '"'
                            + ',"sheet":"' + EscapeJsonString(ExtractFileName(FilePath)) + '"}';
                        Inc(Count);
                    End;
                Except End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"name":"' + EscapeJsonString(Name) + '"'
        + ',"count":' + IntToStr(Count)
        + ',"sheets_searched":' + IntToStr(Sheets)
        + ',"matches":[' + Items + ']}');
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    { In memory only: the reopening lookup would close SchLib under us, and }
    { the rename would land on a component in a closed document. See       }
    { FindLibComponentInMemory.                                             }
    Existing := FindLibComponentInMemory(SchLib, NewName);
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
        { RE-ASSERT AFTER THE ADD. AddSchComponent overrides LibReference }
        { with an auto-generated Component_<N> on the second and later    }
        { additions to a SchLib in one session, so setting it before the  }
        { add is not enough and the rename lands on a name nobody asked   }
        { for. Lib_CreateSymbol carries the same re-assertion.            }
        Component.LibReference := NewName;
    Finally
        SchServer.ProcessControl.PostProcess(SchLib, 'Rename component');
    End;
    SchLib.GraphicallyInvalidate;
    LastCreatedLibComponent := Component;
    LastCreatedLibComponentName := NewName;
    MarkLibDirty(SchLib);

    { VERIFY BOTH DIRECTIONS. A rename is only done if the new name       }
    { resolves AND the old one no longer does; checking just the first    }
    { would pass a copy, and checking neither is what reported success    }
    { while the old name was still sitting in the library.                }
    Existing := Nil;
    Try Existing := LookupLibComponent(SchLib, NewName); Except End;
    If Existing = Nil Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('library_path', LibPath) + ',' +
                JsonStr('old_name', OldName) + ',' +
                JsonStr('new_name', NewName) + ',' +
                JsonStr('reason', 'the component does not resolve under '
                    + 'new_name after the rename, so the library still '
                    + 'holds whatever it held before.')
            ));
        Exit;
    End;

    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"old_name":"' + EscapeJsonString(OldName) + '"' +
        ',"new_name":"' + EscapeJsonString(NewName) + '"' +
        ',"verified":true}';
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    MarkDocDirtyByPath(PcbLib.Board.FileName);

    { SAY THAT THE WRITE IS DEFERRED. The footprint is gone from the
      in-memory library and the file on disk still contains it until a
      flush. Reported by a user who read success, reloaded, and found the
      footprint still there with an unchanged timestamp. The docstring is
      not enough: the reply is what a caller reads. }
    RespJson :=
        '{"success":true,"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"deleted":"' + EscapeJsonString(FpWanted) + '"' +
        ',"written_to_disk":false,"pending_save":true' +
        ',"note":"removed in memory and the library marked dirty. The file '
        + 'still contains it until app_save_all or proj_save flushes."}';
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
    Component := LookupLibComponent(SchLib, CompName);
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    MarkDocDirtyByPath(PcbLib.Board.FileName);

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
    Component := LookupLibComponent(SchLib, CompName);
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
            Component := LookupLibComponent(SchLib, CompNames[C]);
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
    Component := LookupLibComponent(SchLib, CompName);
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
    SeenPrims : TStringList;
    PrimAddr : String;
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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

    { DEDUPE BY OBJECT ADDRESS. A primitive added in this session is
      registered with BOTH the footprint and the library's backing board,
      which is what makes it survive the save, and until the library is
      reloaded the group iterator yields that one object TWICE.
      MEASURED: three pads read back as pad_count 6 and eight primitives as
      16, while the saved file held exactly three and eight. The duplicate
      is the SAME object, not a second one, so the address separates them. }
    SeenPrims := TStringList.Create;
    PrimsJson := '[';
    PFirst := True;
    PrimCount := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            PrimAddr := '';
            Try PrimAddr := IntToStr(Prim.I_ObjectAddress); Except End;
            If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
            Begin
                Prim := GrpIter.NextPCBObject;
                Continue;
            End;
            If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
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
        Try SeenPrims.Free; Except End;
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

        SourceComp := LookupLibComponent(SourceLib, Name);
        If SourceComp = Nil Then Begin Inc(Failed); Continue; End;

        { In memory only, or a miss reopens DestLib under the loop. See }
        { FindLibComponentInMemory.                                     }
        Existing := FindLibComponentInMemory(DestLib, Name);
        If (Existing <> Nil) And (Not Overwrite) Then Begin Inc(Skipped); Continue; End;

        NewComp := SourceComp.Replicate;
        If NewComp = Nil Then Begin Inc(Failed); Continue; End;
        NewComp.LibReference := Name;

        SchServer.ProcessControl.PreProcess(DestLib, '');
        If Existing <> Nil Then Try DestLib.RemoveSchComponent(Existing); Except End;
        DestLib.AddSchComponent(NewComp);
        { REGISTER IT IN THE DESTINATION, or the move does not reach disk.
          Same defect as the copy path: the component is added to a library
          that is never told, so it reads back correctly and no save route
          writes it. Broadcast as a new component, the pattern from Altium's
          createcomp_in_lib.pas. }
        Try
            SchServer.RobotManager.SendMessage(
                Nil, Nil, SCHM_PrimitiveRegistration,
                NewComp.I_ObjectAddress);
        Except End;
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
    MarkDocDirtyByPath(DestPath);
    If DeleteFromSource Then MarkDocDirtyByPath(SourcePath);

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourcePath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestPath) + '"'
        + ',"moved":' + IntToStr(Moved)
        + ',"skipped":' + IntToStr(Skipped)
        + ',"failed":' + IntToStr(Failed) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ AlignCopiedFootprint - after Footprint.CopyTo, put every primitive of the    }
{ copy where it sat relative to its own footprint. CopyTo keeps ABSOLUTE       }
{ coordinates, and a source open in the editor sits at Altium's library        }
{ origin (about 50000,50000 mil, see FootprintOriginX) while a new footprint   }
{ does not, so a copy within one library came out with every pad 50000 mil     }
{ from its origin. Collected first and deduplicated by object address: a       }
{ primitive registered in this session is yielded twice by the group iterator, }
{ and moving it twice would be the same error the other way. The list is      }
{ never freed (TInterfaceList.Free on design objects crashes Altium).          }
{ Returns the primitives moved, or -1 when the two origins already agree.      }
Function AlignCopiedFootprint(Src, Dest : IPCB_LibComponent) : Integer;
Var
    DX, DY : TCoord;
    Iter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Prims : TInterfaceList;
    Seen : TStringList;
    Addr : String;
    I : Integer;
Begin
    Result := -1;
    DX := FootprintOriginX(Dest) - FootprintOriginX(Src);
    DY := FootprintOriginY(Dest) - FootprintOriginY(Src);
    If (DX = 0) And (DY = 0) Then Exit;
    Result := 0;
    Prims := CreateObject(TInterfaceList);
    Seen := TStringList.Create;
    Iter := Dest.GroupIterator_Create;
    Try
        Prim := Iter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Addr := '';
            Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
            If (Addr = '') Or (Seen.IndexOf(Addr) < 0) Then
            Begin
                If Addr <> '' Then Seen.Add(Addr);
                Prims.Add(Prim);
            End;
            Prim := Iter.NextPCBObject;
        End;
    Finally
        Dest.GroupIterator_Destroy(Iter);
    End;
    Seen.Free;
    For I := 0 To Prims.Count - 1 Do
    Begin
        Prim := Prims.Items[I];
        Try
            Prim.MoveByXY(DX, DY);
            Inc(Result);
        Except End;
    End;
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
        AlignCopiedFootprint(Footprint, NewFP);
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
    MarkDocDirtyByPath(DestLib.Board.FileName);
    If DeleteFromSource Then MarkDocDirtyByPath(SourceLib.Board.FileName);

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
    J, Aligned : Integer;
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
    Aligned := AlignCopiedFootprint(Footprint, NewFP);
    Try NewFP.Name := NewName; Except End;
    DestLib.RegisterComponent(NewFP);
    PCBServer.PostProcess;

    Try DestLib.Board.ViewManager_FullUpdate; Except End;
    Try DestLib.RefreshView; Except End;
    MarkDocDirtyByPath(DestLib.Board.FileName);

    RespJson := '{"success":true'
        + ',"source_library":"' + EscapeJsonString(SourceLibPath) + '"'
        + ',"dest_library":"' + EscapeJsonString(DestLibPath) + '"'
        + ',"source":"' + EscapeJsonString(SourceName) + '"'
        + ',"new_name":"' + EscapeJsonString(NewName) + '"'
        + ',"primitives_realigned":' + IntToStr(Aligned)
        + ',"same_library":' + BoolToJsonStr(SameLib) + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{ A pad's solder mask cache, a side at a time. Read through a declared       }
{ local: a member reached through GetState_Cache in one expression has no     }
{ type DelphiScript can resolve. Top is the single value while the sides are  }
{ linked.                                                                     }
Function MaskTopMM(Pad : IPCB_Pad) : Double;
Var
    Cache : TPadCache;
Begin
    Result := 0;
    Try
        Cache := Pad.GetState_Cache;
        Result := CoordToMM(Cache.SolderMaskExpansion);
    Except End;
End;

Function MaskBottomMM(Pad : IPCB_Pad) : Double;
Var
    Cache : TPadCache;
Begin
    Result := 0;
    Try
        Cache := Pad.GetState_Cache;
        Result := CoordToMM(Cache.SolderMaskBottomExpansion);
    Except End;
End;

Function MaskSeparate(Pad : IPCB_Pad) : Boolean;
Var
    Cache : TPadCache;
Begin
    Result := False;
    Try
        Cache := Pad.GetState_Cache;
        Result := Cache.UseSeparateExpansions;
    Except End;
End;

{ CacheStateName - a pad's solder mask (Mask true) or paste cache state, as  }
{ manual, valid or invalid. TCacheState has three values; reporting "rule"  }
{ for both of the others hid whether a written value would be followed.    }
Function CacheStateName(Pad : IPCB_Pad; Mask : Boolean) : String;
Var
    State : Integer;
Begin
    Result := 'unknown';
    State := -1;
    Try
        If Mask Then State := Pad.Cache.SolderMaskExpansionValid
        Else State := Pad.Cache.PasteMaskExpansionValid;
    Except End;
    If State = eCacheManual Then Result := 'manual'
    Else If State = eCacheValid Then Result := 'valid'
    Else If State = eCacheInvalid Then Result := 'invalid';
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
    SeenPads : TStringList;
    PadAddr : String;
    LibPath, FocusedPath, FpWanted, FpName, FpDescr : String;
    ShapeStr, HoleStr, LayerStr, PadsJson, RespJson, ExpSrc : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    XOrg, YOrg, CuX, CuY, HoleTurn : Integer;
    Count, CornerPct : Integer;
    ExpVal, ExpBottom : Double;
    ExpSeparate : Boolean;
    PadCache : TPadCache;
    PadBR : TCoordRect;
    HoleAligned : Boolean;
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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

    { DEDUPE BY OBJECT ADDRESS. A primitive added in this session is
      registered with BOTH the footprint and the library's backing board,
      which is what makes it survive the save, and until the library is
      reloaded the group iterator yields that one object TWICE.
      MEASURED: three pads read back as pad_count 6 and eight primitives as
      16, while the saved file held exactly three and eight. The duplicate
      is the SAME object, not a second one, so the address separates them. }
    SeenPads := TStringList.Create;
    PadsJson := '[';
    Count := 0;
    GrpIter := Target.GroupIterator_Create;
    Try
        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := GrpIter.FirstPCBObject;
        While Pad <> Nil Do
        Begin
            PadAddr := '';
            Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
            If (PadAddr <> '') And (SeenPads.IndexOf(PadAddr) >= 0) Then
            Begin
                Pad := GrpIter.NextPCBObject;
                Continue;
            End;
            If PadAddr <> '' Then SeenPads.Add(PadAddr);
            ShapeStr := 'round';
            Try
                If Pad.TopShape = eRectangular Then ShapeStr := 'rectangular'
                Else If Pad.TopShape = eOctagonal Then ShapeStr := 'octagonal'
                Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'roundrectangle'
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

            { x_mm and y_mm are the pad's ORIGIN. A custom pad's copper     }
            { need not be centred on it, so the copper centre is reported   }
            { too, from the bounding rectangle: the mask it includes is the }
            { same on every side, so its centre is the copper's.            }
            CuX := Pad.X;
            CuY := Pad.Y;
            Try
                PadBR := Pad.BoundingRectangle;
                If (PadBR.Right > PadBR.Left) And (PadBR.Top > PadBR.Bottom) Then
                Begin
                    CuX := (PadBR.Left + PadBR.Right) Div 2;
                    CuY := (PadBR.Bottom + PadBR.Top) Div 2;
                End;
            Except End;

            If Count > 0 Then PadsJson := PadsJson + ',';
            PadsJson := PadsJson +
                '{"name":"' + EscapeJsonString(Pad.Name) + '"' +
                ',"x_mm":' + FloatToJsonStr(CoordToMM(Pad.X - XOrg)) +
                ',"y_mm":' + FloatToJsonStr(CoordToMM(Pad.Y - YOrg)) +
                ',"copper_x_mm":' + FloatToJsonStr(CoordToMM(CuX - XOrg)) +
                ',"copper_y_mm":' + FloatToJsonStr(CoordToMM(CuY - YOrg)) +
                ',"w_mm":' + FloatToJsonStr(CoordToMM(Pad.TopXSize)) +
                ',"h_mm":' + FloatToJsonStr(CoordToMM(Pad.TopYSize)) +
                ',"shape":"' + ShapeStr + '"' +
                ',"corner_pct":' + IntToStr(CornerPct) +
                ',"rotation":' + FloatToJsonStr(Pad.Rotation) +
                ',"hole_rotation":' + FloatToJsonStr(Pad.HoleRotation) +
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

            { A slot's hole rotation is RELATIVE to the pad, so it lines up  }
            { with the copper when both long axes agree in the pad's frame: }
            { copper along X when TopXSize >= TopYSize, hole along X when   }
            { HoleRotation is a multiple of 180. Comparing the two angles   }
            { directly gets it backwards.                                   }
            If HoleStr = 'slot' Then
            Begin
                HoleAligned := True;
                Try
                    HoleTurn := Abs(Round(Pad.HoleRotation)) Mod 180;
                    HoleAligned := (Pad.TopXSize >= Pad.TopYSize) = (HoleTurn < 45);
                Except End;
                PadsJson := PadsJson + ',"slot_aligned":' + BoolToJsonStr(HoleAligned);
            End;

            Try
                PadsJson := PadsJson + ',"plated":' + BoolToJsonStr(Pad.Plated);
            Except
                PadsJson := PadsJson + ',"plated":true';
            End;
            PadsJson := PadsJson + ',"layer":"' + LayerStr + '"';
            { Whether the pad is selected in the editor, so a caller can act on }
            { the pads the user picked; and the stack mode (0 simple, 1 top-    }
            { middle-bottom, 2 full stack), since only a simple pad is wholly  }
            { described by its top size.                                       }
            Try PadsJson := PadsJson + ',"selected":' + BoolToJsonStr(Pad.Selected);
            Except PadsJson := PadsJson + ',"selected":null'; End;
            Try PadsJson := PadsJson + ',"mode":' + IntToStr(Pad.Mode);
            Except PadsJson := PadsJson + ',"mode":null'; End;

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
                ',"paste_top_enabled":' + PadPasteTopJson(Pad) +
                ',"paste_expansion_mm":' + FloatToJsonStr(ExpVal) +
                ',"paste_expansion_source":"' + ExpSrc + '"' +
                ',"paste_expansion_state":"'
                + CacheStateName(Pad, False) + '"';

            { Solder mask expansion. _source keeps its two values; _state  }
            { gives the cache's three (manual, valid, invalid).             }
            {                                                               }
            { The cache holds the top and bottom sides apart, and           }
            { SolderMaskExpansion is the TOP value once                     }
            { UseSeparateExpansions is set; with it clear, the one value    }
            { serves both. The bottom value is kept while the sides are     }
            { linked and takes effect the moment they are unlinked, so all  }
            { three are reported together. Measured on AD 26.10: 0.2 top    }
            { and 0.05 bottom read back apart, and a 0.07 bottom written    }
            { while linked came back on unlinking.                          }
            ExpVal := 0;
            ExpBottom := 0;
            ExpSeparate := False;
            ExpSrc := 'rule';
            Try
                PadCache := Pad.GetState_Cache;
                ExpSeparate := PadCache.UseSeparateExpansions;
                If PadCache.SolderMaskExpansionValid = eCacheManual Then
                Begin
                    ExpVal := CoordToMM(PadCache.SolderMaskExpansion);
                    ExpBottom := CoordToMM(PadCache.SolderMaskBottomExpansion);
                    ExpSrc := 'manual';
                End;
            Except End;
            PadsJson := PadsJson +
                ',"mask_expansion_mm":' + FloatToJsonStr(ExpVal) +
                ',"mask_bottom_expansion_mm":' + FloatToJsonStr(ExpBottom) +
                ',"mask_separate":' + BoolToJsonStr(ExpSeparate) +
                ',"mask_expansion_source":"' + ExpSrc + '"' +
                ',"mask_expansion_state":"' + CacheStateName(Pad, True) + '"}';

            Inc(Count);
            Pad := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
        Try SeenPads.Free; Except End;
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

{ A pad's drill type as the reply names it: round, square or slot. }
Function PadHoleKind(Pad : IPCB_Pad) : String;
Begin
    Result := 'round';
    Try
        If Pad.HoleType = eSlotHole Then Result := 'slot'
        Else If Pad.HoleType = eSquareHole Then Result := 'square';
    Except End;
End;

{..............................................................................}
{ Lib_SetPadProperties - change pads that already exist in a PcbLib footprint. }
{                                                                              }
{ Every pad named pad_name in footprint_name is changed; only the fields      }
{ given are written, so setting plating cannot reset a shape. Each pad is     }
{ read back afterwards and the reply reports what it holds, not what was      }
{ asked. Lengths are in units (mil, the default, or mm). Every write is one   }
{ the pad creators already make: sizes, shape, rotation, hole, slot, plating  }
{ and the mask and paste expansions through the pad cache with the manual    }
{ flag set, without which Altium ignores the value. hole_rotation is read in  }
{ shipped code and written nowhere else, so its read-back is what proves it.  }
{ A pad with a per-layer stack has only its top size written, and the reply  }
{ says so rather than resetting the stack.                                    }
{ selected=true changes the pads SELECTED in the editor instead of those     }
{ named pad_name: pad names need not be unique (an SMD pad "1" and a        }
{ thru-hole "1" in one footprint), and the selection is what the user means. }
{ hole_type (round, square or slot) is written before the hole sizes, so a   }
{ slot can be made round again; hole_width still makes a slot, so it is      }
{ refused beside hole_type round or square.                                   }
{ Params: footprint_name, pad_name or selected=true (required); library_path, }
{ units, x_size, y_size, shape, rotation, hole_type, hole_size, hole_width,   }
{ hole_rotation, plated, paste_expansion, mask_expansion (all optional).      }
{..............................................................................}
Function Lib_SetPadProperties(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, PadWanted, UnitsStr, Shape : String;
    Str, PadsJson, Notes, PlatedStr, HoleTypeStr : String;
    BySelection : Boolean;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Pad : IPCB_Pad;
    Cache : TPadCache;
    SeenPads : TStringList;
    PadAddr : String;
    Count, Failed, XOrg, YOrg : Integer;
    Ok : Boolean;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    PadWanted := ExtractJsonValue(Params, 'pad_name');
    UnitsStr := ExtractJsonValue(Params, 'units');
    Str := LowerCase(ExtractJsonValue(Params, 'selected'));
    BySelection := (Str = 'true') Or (Str = '1');
    If (FpWanted = '') Or ((PadWanted = '') And (Not BySelection)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'footprint_name and pad_name (or selected=true) are required');
        Exit;
    End;
    HoleTypeStr := LowerCase(ExtractJsonValue(Params, 'hole_type'));
    If (HoleTypeStr <> '') And (HoleTypeStr <> 'round')
        And (HoleTypeStr <> 'square') And (HoleTypeStr <> 'slot') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_HOLE_TYPE',
            'hole_type must be round, square or slot');
        Exit;
    End;
    If ((HoleTypeStr = 'round') Or (HoleTypeStr = 'square'))
        And (ExtractJsonValue(Params, 'hole_width') <> '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_HOLE_TYPE',
            'hole_width makes a slot; leave it out for a round or square hole');
        Exit;
    End;
    Shape := LowerCase(ExtractJsonValue(Params, 'shape'));
    If (Shape <> '') And (Shape <> 'round') And (Shape <> 'rectangular')
        And (Shape <> 'octagonal') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_SHAPE',
            'shape must be round, rectangular or octagonal');
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;

    Count := 0;
    Failed := 0;
    Notes := '';
    PadsJson := '';
    SeenPads := TStringList.Create;
    PCBServer.PreProcess;
    Try
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                PadAddr := '';
                Try PadAddr := IntToStr(Pad.I_ObjectAddress); Except End;
                If ((PadAddr = '') Or (SeenPads.IndexOf(PadAddr) < 0))
                    And ((BySelection And Pad.Selected)
                         Or ((Not BySelection)
                             And (UpperCase(Pad.Name) = UpperCase(PadWanted)))) Then
                Begin
                    If PadAddr <> '' Then SeenPads.Add(PadAddr);
                    Ok := True;
                    Try
                        PCBServer.SendMessageToRobots(Pad.I_ObjectAddress,
                            c_Broadcast, PCBM_BeginModify, c_NoEventData);
                    Except End;

                    Str := ExtractJsonValue(Params, 'x_size');
                    If Str <> '' Then
                    Begin
                        Try Pad.TopXSize := CoordFromUnits(StrToFloatDef(Str, 0), UnitsStr);
                        Except Ok := False; End;
                    End;
                    Str := ExtractJsonValue(Params, 'y_size');
                    If Str <> '' Then
                    Begin
                        Try Pad.TopYSize := CoordFromUnits(StrToFloatDef(Str, 0), UnitsStr);
                        Except Ok := False; End;
                    End;
                    If Shape <> '' Then
                    Begin
                        Try
                            If Shape = 'rectangular' Then Pad.TopShape := eRectangular
                            Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
                            Else Pad.TopShape := eRounded;
                        Except Ok := False; End;
                    End;
                    Str := ExtractJsonValue(Params, 'rotation');
                    If Str <> '' Then
                        Try Pad.Rotation := StrToFloatDef(Str, 0); Except Ok := False; End;
                    If HoleTypeStr <> '' Then
                    Begin
                        Try
                            If HoleTypeStr = 'slot' Then Pad.HoleType := eSlotHole
                            Else If HoleTypeStr = 'square' Then Pad.HoleType := eSquareHole
                            Else Pad.HoleType := eRoundHole;
                        Except Ok := False; End;
                    End;
                    Str := ExtractJsonValue(Params, 'hole_size');
                    If Str <> '' Then
                    Begin
                        Try Pad.HoleSize := CoordFromUnits(StrToFloatDef(Str, 0), UnitsStr);
                        Except Ok := False; End;
                    End;
                    Str := ExtractJsonValue(Params, 'hole_width');
                    If Str <> '' Then
                    Begin
                        Try
                            Pad.HoleType := eSlotHole;
                            Pad.HoleWidth := CoordFromUnits(StrToFloatDef(Str, 0), UnitsStr);
                        Except Ok := False; End;
                    End;
                    Str := ExtractJsonValue(Params, 'hole_rotation');
                    If Str <> '' Then
                        Try Pad.HoleRotation := StrToFloatDef(Str, 0); Except Ok := False; End;
                    PlatedStr := LowerCase(ExtractJsonValue(Params, 'plated'));
                    If PlatedStr <> '' Then
                    Begin
                        Try Pad.Plated := (PlatedStr = 'true') Or (PlatedStr = '1');
                        Except Ok := False; End;
                    End;

                    { The top paste opening's on/off flag (issue #80). Set on
                      its own, apart from the expansion below. }
                    Str := LowerCase(ExtractJsonValue(Params, 'paste_top_enabled'));
                    If ((Str = 'true') Or (Str = 'false')) And (Not PadPasteFlagSupported(0)) Then
                    Begin
                        Ok := False;
                        If Pos('paste flag', Notes) = 0 Then
                            Notes := Notes + 'paste flag: the pad paste on/off flag needs '
                                + 'Altium Designer 26 or later; it was not changed;';
                    End
                    Else If (Str = 'true') Or (Str = 'false') Then
                    Begin
                        { Read back: this build accepts the write without
                          error, so a flag that did not change is a failed
                          write, not a success. MEASURED (AD 26.11): the flag
                          is kept only while the paste expansion is manual,
                          as via tenting is with the mask. A rule-driven
                          paste is switched to manual first, at the value it
                          already holds, and the note says so. }
                        Try
                            Cache := Pad.GetState_Cache;
                            If Cache.PasteMaskExpansionValid <> eCacheManual Then
                            Begin
                                Cache.PasteMaskExpansionValid := eCacheManual;
                                Pad.SetState_Cache := Cache;
                                If Pos('paste manual', Notes) = 0 Then
                                    Notes := Notes + 'paste manual: a rule-driven paste expansion '
                                        + 'was made manual at its current value so the paste '
                                        + 'flag would hold;';
                            End;
                        Except Ok := False; End;
                        Try
                            Pad.SetState_IsTopPasteEnabled(Str = 'true');
                            If Pad.GetState_IsTopPasteEnabled <> (Str = 'true') Then Ok := False;
                        Except Ok := False; End;
                    End;

                    If (ExtractJsonValue(Params, 'paste_expansion') <> '')
                        Or (ExtractJsonValue(Params, 'mask_expansion') <> '')
                        Or (ExtractJsonValue(Params, 'mask_bottom_expansion') <> '')
                        Or (ExtractJsonValue(Params, 'mask_separate') <> '') Then
                    Begin
                        Try
                            Cache := Pad.GetState_Cache;
                            Str := ExtractJsonValue(Params, 'paste_expansion');
                            If Str <> '' Then
                            Begin
                                Cache.PasteMaskExpansionValid := eCacheManual;
                                Cache.PasteMaskExpansion := CoordFromUnits(StrToFloatDef(Str, 0), UnitsStr);
                            End;
                            { SolderMaskExpansion is the top value once the   }
                            { sides are separate, and the bottom value is    }
                            { kept while they are linked, ready to take      }
                            { effect on unlinking. So a value given for the  }
                            { pad goes to both sides unless the bottom has a }
                            { value of its own, and nothing is left behind   }
                            { on the side the caller did not mention.        }
                            Str := ExtractJsonValue(Params, 'mask_expansion');
                            If Str <> '' Then
                            Begin
                                Cache.SolderMaskExpansionValid := eCacheManual;
                                Cache.SolderMaskExpansion := CoordFromUnits(StrToFloatDef(Str, 0), UnitsStr);
                                If ExtractJsonValue(Params, 'mask_bottom_expansion') = '' Then
                                    Cache.SolderMaskBottomExpansion := Cache.SolderMaskExpansion;
                            End;
                            Str := ExtractJsonValue(Params, 'mask_bottom_expansion');
                            If Str <> '' Then
                            Begin
                                Cache.SolderMaskExpansionValid := eCacheManual;
                                Cache.SolderMaskBottomExpansion := CoordFromUnits(StrToFloatDef(Str, 0), UnitsStr);
                                Cache.UseSeparateExpansions := True;
                            End;
                            Str := ExtractJsonValue(Params, 'mask_separate');
                            If Str <> '' Then
                                Cache.UseSeparateExpansions := (Str = 'true');
                            Pad.SetState_Cache := Cache;
                        Except Ok := False; End;
                    End;

                    Try
                        PCBServer.SendMessageToRobots(Pad.I_ObjectAddress,
                            c_Broadcast, PCBM_EndModify, c_NoEventData);
                    Except End;
                    Try Pad.GraphicallyInvalidate; Except End;

                    If ((ExtractJsonValue(Params, 'x_size') <> '')
                        Or (ExtractJsonValue(Params, 'y_size') <> ''))
                        And (Pad.Mode <> ePadMode_Simple) And (Pos('stack', Notes) = 0) Then
                        Notes := Notes + 'a pad with a per-layer stack had only its top '
                            + 'size written;';
                    If Not Ok Then Inc(Failed);

                    If PadsJson <> '' Then PadsJson := PadsJson + ',';
                    PadsJson := PadsJson + '{"name":"' + EscapeJsonString(Pad.Name) + '"'
                        + ',"x_mm":' + FloatToJsonStr(CoordToMM(Pad.X - XOrg))
                        + ',"y_mm":' + FloatToJsonStr(CoordToMM(Pad.Y - YOrg))
                        + ',"w_mm":' + FloatToJsonStr(CoordToMM(Pad.TopXSize))
                        + ',"h_mm":' + FloatToJsonStr(CoordToMM(Pad.TopYSize))
                        + ',"rotation":' + FloatToJsonStr(Pad.Rotation)
                        + ',"hole_mm":' + FloatToJsonStr(CoordToMM(Pad.HoleSize))
                        + ',"hole_w_mm":' + FloatToJsonStr(CoordToMM(Pad.HoleWidth))
                        + ',"hole_type":"' + PadHoleKind(Pad) + '"'
                        + ',"hole_rotation":' + FloatToJsonStr(Pad.HoleRotation)
                        + ',"plated":' + BoolToJsonStr(Pad.Plated)
                        + ',"paste_expansion_state":"' + CacheStateName(Pad, False) + '"'
                        + ',"mask_expansion_state":"' + CacheStateName(Pad, True) + '"'
                        + ',"mask_expansion_mm":' + FloatToJsonStr(MaskTopMM(Pad))
                        + ',"mask_bottom_expansion_mm":' + FloatToJsonStr(MaskBottomMM(Pad))
                        + ',"mask_separate":' + BoolToJsonStr(MaskSeparate(Pad))
                        + ',"all_writes_ran":' + BoolToJsonStr(Ok) + '}';
                    Inc(Count);
                End;
                Pad := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;
    Finally
        PCBServer.PostProcess;
        Try SeenPads.Free; Except End;
    End;

    If Count = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PAD',
            'No pad named ' + PadWanted + ' in ' + FpWanted);
        Exit;
    End;
    Try PcbLib.Board.ViewManager_FullUpdate; Except End;
    MarkDocDirtyByPath(LibPath);

    Result := BuildSuccessResponse(RequestId,
        '{"footprint":"' + EscapeJsonString(FpWanted) + '"'
        + ',"pads_changed":' + IntToStr(Count)
        + ',"pads_with_a_failed_write":' + IntToStr(Failed)
        + ',"pads":[' + PadsJson + ']'
        + ',"note":"' + EscapeJsonString(Notes) + '"}');
End;

{ Lib_GetFootprintPrimitives - READ-ONLY list of one PcbLib footprint's       }
{ tracks, arcs, regions, fills and texts, each with its layer, in mm from    }
{ the footprint origin, plus the footprint height and its 3D body count.    }
{ The package audit reads silkscreen, courtyard, assembly and paste windows }
{ from it. Nothing is written.                                               }
{                                                                            }
{ Every property read here is already read in shipped code: Track X1, Y1,   }
{ X2, Y2, Width and Arc XCenter, YCenter, Radius, StartAngle, EndAngle,     }
{ LineWidth (GetPCBProperty, PCBGeneric.pas); Region.Area                   }
{ (RegionCopperSqMils, PCB.pas); BoundingRectangle on a region and on a     }
{ primitive (Audit.pas, PCB.pas); Text UnderlyingString, Text, Size,        }
{ XLocation, YLocation and BoundingRectangle (Lib_GetLibraryGeometry) and   }
{ Rotation (Lib_ProbeDesignator); the footprint's X, Y and Height           }
{ (Lib_GetPadGeometry, Lib_ProbeFootprint). A region is given by its        }
{ bounding box and area, not its vertices: whether a contour's points count }
{ from 0 or from 1 when read back is not settled (see RegionCopperSqMils).  }
{ Deduplicated by object address, as Lib_GetPadGeometry is.                 }
{ Params: footprint_name (required), library_path (optional, focused).     }
Function Lib_GetFootprintPrimitives(Params : String; RequestId : String) : String;
Var
    SeenPrims : TStringList;
    PrimAddr, LibPath, FocusedPath, FpWanted, FpName : String;
    LayerStr, TxtStr, Rec, RespJson : String;
    TracksJson, ArcsJson, RegionsJson, FillsJson, TextsJson, ViasJson, BodyLayersJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, Target : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Region : IPCB_Region;
    FillObj : IPCB_Fill;
    Txt : IPCB_Text;
    Via : IPCB_Via;
    Prim : IPCB_Primitive;
    TRect : TCoordRect;
    XOrg, YOrg, LayerId : Integer;
    NTracks, NArcs, NRegions, NFills, NTexts, NBodies, NVias : Integer;
    RegionArcs, SegI : Integer;
    HeightMM, AreaMM2 : Double;
    Body : IPCB_ComponentBody;
    BodyModel : IPCB_Model;
    BodyModelsJson, BodyIdStr, ModelFile : String;
    ModelKind : Integer;
    ModelRot, BodyTop, BodyStandoff : Double;
Begin
    BodyModelsJson := '';
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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
    Try FpName := Target.Name; Except End;
    XOrg := 0;  YOrg := 0;
    Try XOrg := Target.X; Except End;
    Try YOrg := Target.Y; Except End;
    HeightMM := 0;
    Try HeightMM := CoordToMM(Target.Height); Except End;

    TracksJson := '';
    ArcsJson := '';
    RegionsJson := '';
    FillsJson := '';
    TextsJson := '';
    ViasJson := '';
    NVias := 0;
    NTracks := 0;
    NArcs := 0;
    NRegions := 0;
    NFills := 0;
    NTexts := 0;
    NBodies := 0;
    BodyLayersJson := '';
    SeenPrims := TStringList.Create;
    Try
        { Tracks. One filter per pass, so the iterator hands back the typed }
        { object and its own members resolve.                               }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject));
            Track := GrpIter.FirstPCBObject;
            While Track <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(Track.I_ObjectAddress); Except End;
                If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
                Begin
                    Track := GrpIter.NextPCBObject;
                    Continue;
                End;
                If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
                LayerStr := '';
                Try LayerStr := ResolveLayerName(PcbLib.Board, Track.Layer); Except End;
                LayerId := -1;
                Try LayerId := Track.Layer; Except End;
                Rec := '';
                Try
                    Rec := '{"layer":"' + EscapeJsonString(LayerStr) + '"'
                        + ',"layer_id":' + IntToStr(LayerId)
                        + ',"x1":' + FloatToJsonStr(CoordToMM(Track.X1 - XOrg))
                        + ',"y1":' + FloatToJsonStr(CoordToMM(Track.Y1 - YOrg))
                        + ',"x2":' + FloatToJsonStr(CoordToMM(Track.X2 - XOrg))
                        + ',"y2":' + FloatToJsonStr(CoordToMM(Track.Y2 - YOrg))
                        + ',"width":' + FloatToJsonStr(CoordToMM(Track.Width)) + '}';
                Except
                    Rec := '';
                End;
                If Rec <> '' Then
                Begin
                    If NTracks > 0 Then TracksJson := TracksJson + ',';
                    TracksJson := TracksJson + Rec;
                    Inc(NTracks);
                End;
                Track := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;

        { Arcs: centre, radius, angles in degrees counter-clockwise. }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eArcObject));
            Arc := GrpIter.FirstPCBObject;
            While Arc <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(Arc.I_ObjectAddress); Except End;
                If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
                Begin
                    Arc := GrpIter.NextPCBObject;
                    Continue;
                End;
                If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
                LayerStr := '';
                Try LayerStr := ResolveLayerName(PcbLib.Board, Arc.Layer); Except End;
                LayerId := -1;
                Try LayerId := Arc.Layer; Except End;
                Rec := '';
                Try
                    Rec := '{"layer":"' + EscapeJsonString(LayerStr) + '"'
                        + ',"layer_id":' + IntToStr(LayerId)
                        + ',"xc":' + FloatToJsonStr(CoordToMM(Arc.XCenter - XOrg))
                        + ',"yc":' + FloatToJsonStr(CoordToMM(Arc.YCenter - YOrg))
                        + ',"r":' + FloatToJsonStr(CoordToMM(Arc.Radius))
                        + ',"start":' + FloatToJsonStr(Arc.StartAngle)
                        + ',"end":' + FloatToJsonStr(Arc.EndAngle)
                        + ',"width":' + FloatToJsonStr(CoordToMM(Arc.LineWidth)) + '}';
                Except
                    Rec := '';
                End;
                If Rec <> '' Then
                Begin
                    If NArcs > 0 Then ArcsJson := ArcsJson + ',';
                    ArcsJson := ArcsJson + Rec;
                    Inc(NArcs);
                End;
                Arc := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;

        { Regions: bounding box and area (Area is in internal units squared, }
        { 10000 per mil each way; one square mil is 0.00064516 mm2).        }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eRegionObject));
            Region := GrpIter.FirstPCBObject;
            While Region <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(Region.I_ObjectAddress); Except End;
                If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
                Begin
                    Region := GrpIter.NextPCBObject;
                    Continue;
                End;
                If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
                LayerStr := '';
                Try LayerStr := ResolveLayerName(PcbLib.Board, Region.Layer); Except End;
                LayerId := -1;
                Try LayerId := Region.Layer; Except End;
                AreaMM2 := 0;
                Try AreaMM2 := Region.Area / 100000000.0 * 0.00064516; Except End;
                { Edges held as true arcs; 0 for a region of straight lines. }
                RegionArcs := 0;
                Try
                    For SegI := 0 To Region.ShapeSegmentCount - 1 Do
                        If Region.ShapeSegments[SegI].Kind = ePolySegmentArc Then
                            Inc(RegionArcs);
                Except
                    RegionArcs := 0;
                End;
                Rec := '';
                Try
                    TRect := Region.BoundingRectangle;
                    Rec := '{"layer":"' + EscapeJsonString(LayerStr) + '"'
                        + ',"layer_id":' + IntToStr(LayerId)
                        + ',"x1":' + FloatToJsonStr(CoordToMM(TRect.X1 - XOrg))
                        + ',"y1":' + FloatToJsonStr(CoordToMM(TRect.Y1 - YOrg))
                        + ',"x2":' + FloatToJsonStr(CoordToMM(TRect.X2 - XOrg))
                        + ',"y2":' + FloatToJsonStr(CoordToMM(TRect.Y2 - YOrg))
                        + ',"area":' + FloatToJsonStr(AreaMM2)
                        + ',"arc_edges":' + IntToStr(RegionArcs) + '}';
                Except
                    Rec := '';
                End;
                If Rec <> '' Then
                Begin
                    If NRegions > 0 Then RegionsJson := RegionsJson + ',';
                    RegionsJson := RegionsJson + Rec;
                    Inc(NRegions);
                End;
                Region := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;

        { Fills: bounding box. }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eFillObject));
            FillObj := GrpIter.FirstPCBObject;
            While FillObj <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(FillObj.I_ObjectAddress); Except End;
                If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
                Begin
                    FillObj := GrpIter.NextPCBObject;
                    Continue;
                End;
                If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
                LayerStr := '';
                Try LayerStr := ResolveLayerName(PcbLib.Board, FillObj.Layer); Except End;
                LayerId := -1;
                Try LayerId := FillObj.Layer; Except End;
                Rec := '';
                Try
                    TRect := FillObj.BoundingRectangle;
                    Rec := '{"layer":"' + EscapeJsonString(LayerStr) + '"'
                        + ',"layer_id":' + IntToStr(LayerId)
                        + ',"x1":' + FloatToJsonStr(CoordToMM(TRect.X1 - XOrg))
                        + ',"y1":' + FloatToJsonStr(CoordToMM(TRect.Y1 - YOrg))
                        + ',"x2":' + FloatToJsonStr(CoordToMM(TRect.X2 - XOrg))
                        + ',"y2":' + FloatToJsonStr(CoordToMM(TRect.Y2 - YOrg)) + '}';
                Except
                    Rec := '';
                End;
                If Rec <> '' Then
                Begin
                    If NFills > 0 Then FillsJson := FillsJson + ',';
                    FillsJson := FillsJson + Rec;
                    Inc(NFills);
                End;
                FillObj := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;

        { Texts: the raw string (UnderlyingString, as Lib_GetLibraryGeometry }
        { reads it), the anchor, size, rotation and bounding box. The box is }
        { cached by Altium and can lag an edit made in this session.         }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
            Txt := GrpIter.FirstPCBObject;
            While Txt <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(Txt.I_ObjectAddress); Except End;
                If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
                Begin
                    Txt := GrpIter.NextPCBObject;
                    Continue;
                End;
                If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
                TxtStr := '';
                Try TxtStr := Txt.UnderlyingString; Except End;
                If TxtStr = '' Then
                    Try TxtStr := Txt.Text; Except End;
                LayerStr := '';
                Try LayerStr := ResolveLayerName(PcbLib.Board, Txt.Layer); Except End;
                LayerId := -1;
                Try LayerId := Txt.Layer; Except End;
                Rec := '';
                Try
                    Rec := '{"text":"' + EscapeJsonString(TxtStr) + '"'
                        + ',"layer":"' + EscapeJsonString(LayerStr) + '"'
                        + ',"layer_id":' + IntToStr(LayerId)
                        + ',"x":' + FloatToJsonStr(CoordToMM(Txt.XLocation - XOrg))
                        + ',"y":' + FloatToJsonStr(CoordToMM(Txt.YLocation - YOrg))
                        + ',"size":' + FloatToJsonStr(CoordToMM(Txt.Size))
                        + ',"rotation":' + FloatToJsonStr(Txt.Rotation);
                    TRect := Txt.BoundingRectangle;
                    Rec := Rec
                        + ',"x1":' + FloatToJsonStr(CoordToMM(TRect.X1 - XOrg))
                        + ',"y1":' + FloatToJsonStr(CoordToMM(TRect.Y1 - YOrg))
                        + ',"x2":' + FloatToJsonStr(CoordToMM(TRect.X2 - XOrg))
                        + ',"y2":' + FloatToJsonStr(CoordToMM(TRect.Y2 - YOrg)) + '}';
                Except
                    Rec := '';
                End;
                If Rec <> '' Then
                Begin
                    If NTexts > 0 Then TextsJson := TextsJson + ',';
                    TextsJson := TextsJson + Rec;
                    Inc(NTexts);
                End;
                Txt := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;

        { Vias: centre, diameter and hole. No other library tool reported }
        { them, so a footprint's stitching or thermal vias were invisible.}
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eViaObject));
            Via := GrpIter.FirstPCBObject;
            While Via <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(Via.I_ObjectAddress); Except End;
                If (PrimAddr <> '') And (SeenPrims.IndexOf(PrimAddr) >= 0) Then
                Begin
                    Via := GrpIter.NextPCBObject;
                    Continue;
                End;
                If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
                Rec := '';
                Try
                    Rec := '{"x":' + FloatToJsonStr(CoordToMM(Via.X - XOrg))
                        + ',"y":' + FloatToJsonStr(CoordToMM(Via.Y - YOrg))
                        + ',"diameter":' + FloatToJsonStr(CoordToMM(Via.Size))
                        + ',"hole":' + FloatToJsonStr(CoordToMM(Via.HoleSize)) + '}';
                Except
                    Rec := '';
                End;
                If Rec <> '' Then
                Begin
                    If NVias > 0 Then ViasJson := ViasJson + ',';
                    ViasJson := ViasJson + Rec;
                    Inc(NVias);
                End;
                Via := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;

        { 3D bodies: counted, with the layer each is on. }
        GrpIter := Target.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
            Prim := GrpIter.FirstPCBObject;
            While Prim <> Nil Do
            Begin
                PrimAddr := '';
                Try PrimAddr := IntToStr(Prim.I_ObjectAddress); Except End;
                If (PrimAddr = '') Or (SeenPrims.IndexOf(PrimAddr) < 0) Then
                Begin
                    If PrimAddr <> '' Then SeenPrims.Add(PrimAddr);
                    Inc(NBodies);
                    If BodyLayersJson <> '' Then BodyLayersJson := BodyLayersJson + ',';
                    BodyLayersJson := BodyLayersJson + '"'
                        + EscapeJsonString(GetLayerString(Prim.Layer)) + '"';
                    { The model behind the body, the members ReportComp3DModels.pas
                      reads: its source file name, type (T3DModelType; 1 for a
                      STEP model, measured on AD 26.11), rotation, heights. }
                    Body := Prim;
                    BodyModel := Nil;
                    Try BodyModel := Body.Model; Except BodyModel := Nil; End;
                    BodyIdStr := '';
                    Try BodyIdStr := Body.Identifier; Except End;
                    ModelFile := '';
                    ModelKind := -1;
                    ModelRot := 0;
                    If BodyModel <> Nil Then
                    Begin
                        Try ModelFile := BodyModel.FileName; Except End;
                        Try ModelKind := BodyModel.ModelType; Except End;
                        Try ModelRot := BodyModel.Rotation; Except End;
                    End;
                    BodyTop := 0;
                    BodyStandoff := 0;
                    Try BodyTop := CoordToMM(Body.OverallHeight); Except End;
                    Try BodyStandoff := CoordToMM(Body.StandoffHeight); Except End;
                    If BodyModelsJson <> '' Then BodyModelsJson := BodyModelsJson + ',';
                    BodyModelsJson := BodyModelsJson
                        + '{"layer":"' + EscapeJsonString(GetLayerString(Prim.Layer)) + '"'
                        + ',"identifier":"' + EscapeJsonString(BodyIdStr) + '"'
                        + ',"has_model":' + BoolToJsonStr(BodyModel <> Nil)
                        + ',"model_file":"' + EscapeJsonString(ModelFile) + '"'
                        + ',"model_type":' + IntToStr(ModelKind)
                        + ',"rotation":' + FloatToJsonStr(ModelRot)
                        + ',"overall_height_mm":' + FloatToJsonStr(BodyTop)
                        + ',"standoff_mm":' + FloatToJsonStr(BodyStandoff) + '}';
                End;
                Prim := GrpIter.NextPCBObject;
            End;
        Finally
            Target.GroupIterator_Destroy(GrpIter);
        End;
    Finally
        Try SeenPrims.Free; Except End;
    End;

    RespJson :=
        '{"name":"' + EscapeJsonString(FpName) + '"' +
        ',"library_path":"' + EscapeJsonString(LibPath) + '"' +
        ',"height_mm":' + FloatToJsonStr(HeightMM) +
        ',"bodies":' + IntToStr(NBodies) +
        ',"body_layers":[' + BodyLayersJson + ']' +
        ',"body_models":[' + BodyModelsJson + ']' +
        ',"tracks":[' + TracksJson + ']' +
        ',"arcs":[' + ArcsJson + ']' +
        ',"regions":[' + RegionsJson + ']' +
        ',"fills":[' + FillsJson + ']' +
        ',"texts":[' + TextsJson + ']' +
        ',"vias":[' + ViasJson + ']}';
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
                Component := LookupLibComponent(SchLib, AllNames[C]);
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

{ Apply mechanical layer operations to a board's stack.                       }
{                                                                              }
{ SHARED BY THE LIBRARY AND THE BOARD. A PcbLib carries an IPCB_Board and so   }
{ does a PcbDoc, and from here down nothing cares which it came from. Only     }
{ the resolution differs: a library is taken by path and verified, a board is  }
{ whichever one is open.                                                       }
{                                                                              }
{ Written as one function rather than two because the paired-kind handling is  }
{ the awkward part: drain the existing pairs, add with the top layer first,    }
{ write the kind to the pair index, release whatever else holds it, retry, and }
{ put the released kinds back if it still will not take. A second copy of that }
{ would drift from this one, and the drift would be silent.                    }
{                                                                              }
{ Where names the document in the messages, so a caller reading a refusal      }
{ knows which file it is about.                                                }

Function ApplyMechLayerOps(Board : IPCB_Board; OpsStr : String;
    TidyPairs : Boolean; Where : String; RequestId : String) : String;
Var
    Op : String;
    LayerName, NewName, EnabledStr, KindStr : String;
    ItemsJson, Problems : String;
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
    Justified : Boolean;
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
    If Trim(OpsStr) = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'layers required, for example '
            + '"layer=Mechanical13;name=Courtyard;enabled=true"');
        Exit;
    End;

    LayerStack := Nil;
    Try LayerStack := Board.LayerStack_V7; Except End;
    If LayerStack = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_STACKUP',
            'Could not access the layer stack of ' + Where);
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
            + 'nothing was changed in ' + Where + '. Expected '
            + '"layer=<name>;name=<text>;enabled=<bool>;kind=<text>" with '
            + '"~~" between entries.');
        Exit;
    End;

    Try Board.ViewManager_FullUpdate; Except End;
    MarkDocDirtyByPath(Where);

    Result := BuildSuccessResponse(RequestId,
        '{"document":"' + EscapeJsonString(Where) + '",'
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

{ The library half: take the library by PATH and refuse unless the document  }
{ that ended up focused is the one asked for.                                }
{                                                                             }
{ Acting on the wrong library is worse than not acting, because it looks like }
{ it worked. A sweep over twenty one libraries once returned twenty one       }
{ identical answers, every call having re-read the same focused file.         }

{ Delete primitives from ONE footprint in a PcbLib.                           }
{                                                                              }
{ THE LIBRARY HAD NO PRIMITIVE DELETE AT ALL. obj_delete and pcb_delete_object }
{ both resolve a BOARD, and when none is focused the board lookup opens the    }
{ first PcbDoc any open project holds. So a caller working in a footprint had  }
{ no correct tool, and the incorrect one removed primitives from a board they  }
{ had not named and did not report which.                                      }
{                                                                              }
{ Scoped three ways, all required to agree before anything is removed: the     }
{ LIBRARY by path, the FOOTPRINT by name, and the object type. A layer filter  }
{ narrows it further, which is the usual case: clear the silkscreen on one     }
{ footprint without touching its pads.                                         }
{                                                                              }
{ Pads are excluded unless include_pads is set. Deleting a pad changes the     }
{ part's connectivity rather than its drawing, and a caller clearing graphics  }
{ off a layer should not lose the pinout to a filter that was wider than they  }
{ realised.                                                                    }
{                                                                              }
{ Collect first, delete second. Removing objects while the group iterator is   }
{ walking them is how a traversal skips half the list, and a half-cleared      }
{ footprint looks like the filter was wrong.                                   }

{ A footprint other than Target to make current while Target is edited.      }
{                                                                            }
{ The current footprint's primitives live on the library board, and the      }
{ board is written back into the footprint whenever another one becomes      }
{ current. Board.RemovePCBObject does nothing on a library board, and a      }
{ footprint-side remove on the current footprint is undone by that write     }
{ back, so a delete from the current footprint reported success and the      }
{ primitives returned at the next switch. Removing while another footprint   }
{ is current lasts through switches, save and reopen. A library holding      }
{ only Target gets a temporary footprint, which TempMade reports so the     }
{ caller removes it again.                                                   }
Function FootprintToHoldFocus(PcbLib : IPCB_Library; Target : IPCB_LibComponent;
    Var TempMade : Boolean) : IPCB_LibComponent;
Var
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
Begin
    Result := Nil;
    TempMade := False;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            If Footprint.I_ObjectAddress <> Target.I_ObjectAddress Then
            Begin
                Result := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
    If Result = Nil Then
    Begin
        Result := PCBServer.CreatePCBLibComp;
        Result.Name := PcbLib.GetUniqueCompName('EDA_AGENT_HOLD');
        PcbLib.RegisterComponent(Result);
        TempMade := True;
    End;
End;

{ Whether Target is the footprint the library editor has current. }
Function IsCurrentFootprint(PcbLib : IPCB_Library; Target : IPCB_LibComponent) : Boolean;
Var
    Cur : IPCB_LibComponent;
Begin
    Result := False;
    Cur := Nil;
    Try Cur := PcbLib.CurrentComponent; Except End;
    If Cur <> Nil Then
        Result := Cur.I_ObjectAddress = Target.I_ObjectAddress;
End;

Function Lib_DeleteFootprintPrimitives(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, FpWanted, FpName, ObjTypeStr, LayerStr : String;
    ConfirmStr, RemovedJson : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    GrpIter : IPCB_GroupIterator;
    Footprint, Target : IPCB_LibComponent;
    Prim : IPCB_Primitive;
    { Integer, not TObjectId. ObjectTypeFromStringPCB returns an Integer,
      and declaring the target as an enum type this build may not define
      the way the script expects is its own runtime fault. }
    ObjFilter : Integer;
    WantLayer, MatchLayer : TLayer;
    IncludePads, Found, WasCurrent, TempMade : Boolean;
    Removed, I, Examined : Integer;
    Holder : IPCB_LibComponent;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    FpWanted := ExtractJsonValue(Params, 'footprint_name');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    LayerStr := ExtractJsonValue(Params, 'layer');
    ConfirmStr := ExtractJsonValue(Params, 'confirm');
    IncludePads := (ExtractJsonValue(Params, 'include_pads') = 'true');

    If FpWanted = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'footprint_name required. Deleting from whichever footprint the '
            + 'editor happens to show is the mistake this tool exists to '
            + 'avoid.');
        Exit;
    End;

    If ConfirmStr <> 'true' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CONFIRM_REQUIRED',
            'This removes primitives from footprint "' + FpWanted
            + '". Pass confirm=true once the object_type and layer are '
            + 'what you mean. Read them first with lib_probe_footprint.');
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
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
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

    { Verified AFTER the open, the same way lib_set_mech_layers does. A     }
    { delete aimed at a library that never came to the front would land in  }
    { whichever one did.                                                     }
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT_FOCUSED',
            'Asked for ' + LibPath + ' but the focused document is "'
            + FocusedPath + '". Nothing was deleted.');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Focused ' + LibPath + ' but it is not a PCB library. To '
            + 'delete primitives from a BOARD use pcb_delete_object. '
            + 'This tool edits a footprint inside a .PcbLib.');
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
            'Footprint not found in ' + LibPath + ': ' + FpWanted);
        Exit;
    End;

    ObjFilter := ObjectTypeFromStringPCB(ObjTypeStr);
    If (ObjTypeStr <> '') And (ObjFilter = -1) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE',
            'Unknown PCB object type: ' + ObjTypeStr);
        Exit;
    End;

    { Resolved against the library's own stack. GetLayerFromString       }
    { answers eTopLayer for any name it does not know, so "Top Assembly"  }
    { or Mechanical17 used to select, and delete, top copper.             }
    WantLayer := eNoLayer;
    If LayerStr <> '' Then
    Begin
        Try WantLayer := ResolveLayerId(PcbLib.Board, LayerStr);
        Except WantLayer := eNoLayer; End;
        If WantLayer = eNoLayer Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
                'Unknown layer name: ' + LayerStr + '. '
                + BoardLayerNamesHint(PcbLib.Board));
            Exit;
        End;
    End;

    If (ObjTypeStr = '') And (LayerStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'TOO_BROAD',
            'Give object_type or layer. Emptying a whole footprint is not '
            + 'something to reach by leaving both filters off.');
        Exit;
    End;

    { ONE MATCH PER PASS, and never a primitive held outside its iterator.  }
    {                                                                        }
    { The first version collected the matches into a TInterfaceList and then }
    { deleted them. That crashes the engine with an access violation:        }
    { DelphiScript narrows an interface at ITERATOR RETURN, and a typed      }
    { local assigned from an untyped list element keeps the base dispatch,   }
    { so RemovePCBObject was handed something that was not usable as a       }
    { primitive.                                                             }
    {                                                                        }
    { Removing during a walk is not the alternative: that makes the          }
    { traversal skip entries and leaves a half-cleared footprint that reads  }
    { as a filter which was too narrow. So each pass takes a fresh iterator, }
    { stops at the FIRST match, closes the iterator, and removes it. A       }
    { footprint holds tens of primitives, so the repeated walk costs         }
    { nothing worth optimising.                                              }
    Removed := 0;
    Examined := 0;
    RemovedJson := '';

    { The editor shows Target again once the delete is done. }
    WasCurrent := IsCurrentFootprint(PcbLib, Target);
    TempMade := False;
    Holder := Nil;
    If WasCurrent Then
    Begin
        Holder := FootprintToHoldFocus(PcbLib, Target, TempMade);
        PcbLib.SetState_CurrentComponent(Holder);
    End;

    { Counted here, once Target is off screen, so a primitive the board view }
    { also lists is counted once.                                              }
    GrpIter := Target.GroupIterator_Create;
    Try
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Examined := Examined + 1;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Target.GroupIterator_Destroy(GrpIter);
    End;

    PCBServer.PreProcess;
    Try
        PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
            c_Broadcast, PCBM_BeginModify, c_NoEventData);

        { Bounded, so a RemovePCBObject that silently refuses cannot spin  }
        { here forever re-finding the same primitive.                       }
        For I := 1 To 5000 Do
        Begin
            MatchLayer := eNoLayer;
            Found := False;
            GrpIter := Target.GroupIterator_Create;
            Try
                Prim := GrpIter.FirstPCBObject;
                While Prim <> Nil Do
                Begin
                    If ((ObjFilter = -1) Or (Prim.ObjectId = ObjFilter))
                       And ((WantLayer = eNoLayer) Or (Prim.Layer = WantLayer))
                       And (IncludePads Or (Prim.ObjectId <> ePadObject)) Then
                    Begin
                        MatchLayer := Prim.Layer;
                        Found := True;
                        Break;
                    End;
                    Prim := GrpIter.NextPCBObject;
                End;
                { REMOVE WHILE THE ITERATOR IS STILL OPEN.

                  This used to destroy the iterator first, on the reasoning
                  that a closed iterator cannot have its walk disturbed by
                  the removal. It cannot, but that was never the risk:
                  GroupIterator_Destroy releases what the iterator handed
                  out, so Prim was dangling by the time it was removed, and
                  the engine died on a null read inside
                  ScriptingSystem.DLL. An access violation in the DLL is
                  not something the Try below can catch, so the whole
                  polling loop went with it.

                  Removing here is safe because the walk STOPS at the first
                  match: the Break above means NextPCBObject is never called
                  again on this iterator, so the skip-entries problem that
                  the one-match-per-pass design exists to avoid cannot
                  arise. The outer loop takes a fresh iterator for the next
                  one. }
                If Found And (Prim <> Nil) Then
                Begin
                    Try
                        Target.RemovePCBObject(Prim);
                        PCBServer.DestroyPCBObject(Prim);
                        Removed := Removed + 1;
                        If RemovedJson <> '' Then
                            RemovedJson := RemovedJson + ',';
                        RemovedJson := RemovedJson + '"'
                            + EscapeJsonString(GetLayerString(MatchLayer))
                            + '"';
                    Except
                        { It matched and would not go. Stop rather than
                          loop on it forever. }
                        Found := False;
                    End;
                End;
            Finally
                Target.GroupIterator_Destroy(GrpIter);
            End;

            If Not Found Then Break;
        End;

        PCBServer.SendMessageToRobots(Target.I_ObjectAddress,
            c_Broadcast, PCBM_EndModify, c_NoEventData);
    Finally
        PCBServer.PostProcess;
        If WasCurrent Then
        Begin
            PcbLib.SetState_CurrentComponent(Target);
            If TempMade Then
            Begin
                PcbLib.RemoveComponent(Holder);
                PcbLib.DeRegisterComponent(Holder);
                PCBServer.DestroyPCBLibComp(Holder);
            End;
        End;
    End;

    If Removed > 0 Then
    Begin
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(LibPath);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"library":"' + EscapeJsonString(LibPath) + '",'
        + '"footprint":"' + EscapeJsonString(FpWanted) + '",'
        + '"removed":' + IntToStr(Removed) + ','
        + '"examined":' + IntToStr(Examined) + ','
        + '"layers":[' + RemovedJson + '],'
        + '"pads_included":' + BoolToJsonStr(IncludePads) + ','
        + '"was_current":' + BoolToJsonStr(WasCurrent) + '}');
End;

{ Whether a mechanical layer is enabled in the library's stack. Altium takes  }
{ a body or a track on any of its mechanical layers, enabled or not, and one  }
{ that is not enabled is not drawn and reads back as Unknown, so a caller     }
{ who mistyped the number sees nothing happen. Read as LayerObject_V7 and     }
{ MechanicalLayerEnabled, the way Lib_GetMechLayers reads them.               }
Function LibMechLayerEnabled(Board : IPCB_Board; Layer : TLayer) : Boolean;
Var
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
Begin
    Result := False;
    LayerStack := Nil;
    Try LayerStack := Board.LayerStack_V7; Except LayerStack := Nil; End;
    If LayerStack = Nil Then Exit;
    LayerObj := Nil;
    Try LayerObj := LayerStack.LayerObject_V7[Layer]; Except LayerObj := Nil; End;
    If LayerObj = Nil Then Exit;
    Try Result := LayerObj.MechanicalLayerEnabled; Except Result := False; End;
End;

{ The reply fields for that check: layer_enabled, and a warning when it is    }
{ off. Empty-warning replies carry only the flag.                             }
Function MechLayerEnabledJson(Board : IPCB_Board; Layer : TLayer; LayerStr : String) : String;
Begin
    If LibMechLayerEnabled(Board, Layer) Then
        Result := JsonRaw('layer_enabled', 'true')
    Else
        Result := JsonRaw('layer_enabled', 'false') + ','
            + JsonStr('warning', LayerStr + ' is not enabled in this library, '
            + 'so what was put there is not drawn. Enable it with '
            + 'lib_set_mech_layers, or check the layer number.');
End;

{ Whether a footprint is one a library-wide call was asked to work on.       }
{ Wanted is tab-separated; empty means every footprint. Names compare        }
{ without case, the way the library's own lookup does.                       }
Function FootprintIsWanted(Wanted : String; FpName : String) : Boolean;
Begin
    If Wanted = '' Then
        Result := True
    Else
        Result := Pos(#9 + UpperCase(FpName) + #9, #9 + UpperCase(Wanted) + #9) > 0;
End;

{ The names in Wanted that no footprint of the library answered to, as JSON  }
{ string items. Found is the tab-separated list of names that did.           }
Function FootprintsNotFound(Wanted : String; Found : String) : String;
Var
    Rest, One : String;
Begin
    Result := '';
    Rest := Wanted;
    While Rest <> '' Do
    Begin
        One := SplitNextTab(Rest);
        If (One <> '') And (Pos(#9 + UpperCase(One) + #9, #9 + UpperCase(Found) + #9) = 0) Then
        Begin
            If Result <> '' Then Result := Result + ',';
            Result := Result + '"' + EscapeJsonString(One) + '"';
        End;
    End;
End;

{ The footprint of that name, straight from a library iterator. A footprint  }
{ kept in a TInterfaceList comes back with the base dispatch, so a call that }
{ goes on to edit one looks it up again by name.                             }
Function FootprintByName(PcbLib : IPCB_Library; Name : String) : IPCB_LibComponent;
Var
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    FpName : String;
Begin
    Result := Nil;
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If UpperCase(FpName) = UpperCase(Name) Then
            Begin
                Result := Footprint;
                Break;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;
End;

{ Lib_MoveBodiesToLayer - put the 3D bodies of a library on one mechanical   }
{ layer.                                                                     }
{                                                                            }
{ Libraries assembled from several sources carry their bodies on whatever    }
{ layer each source used, so one footprint's model shows with Mechanical 13  }
{ and the next one's never does. Every footprint, or the named ones, has     }
{ each body moved; a body already there is counted and left alone.           }
{                                                                            }
{ A body changes layer in place. Nothing is removed or added, so the         }
{ ownership rules that govern deletes do not come into it: the footprint on  }
{ screen holds the same objects as the library board, and the write back at  }
{ the next switch carries the new layer with it.                             }
Function Lib_MoveBodiesToLayer(Params : String; RequestId : String) : String;
Var
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    GrpIter : IPCB_GroupIterator;
    Footprint : IPCB_LibComponent;
    Body : IPCB_ComponentBody;
    LayerStr, Wanted, FpName, Found, Items, Seen, Addr, FromJson : String;
    KeepStr, KeepIds, Tok : String;
    Want, Was, KeepL : TLayer;
    CommaPos : Integer;
    Footprints, Bodies, Moved, Already, NoBody, FpMoved, FpBodies : Integer;
Begin
    LayerStr := Trim(ExtractJsonValue(Params, 'layer'));
    Wanted := ExtractJsonValue(Params, 'footprint_names');
    If LayerStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'layer is required: the mechanical layer the bodies go on');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB library is focused. This moves the 3D bodies inside a '
            + '.PcbLib; on a board, use obj_modify on the bodies.');
        Exit;
    End;

    Want := eNoLayer;
    Try Want := ResolveLayerId(PcbLib.Board, LayerStr); Except Want := eNoLayer; End;
    If Want = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
            'Unknown layer name: ' + LayerStr + '. '
            + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;
    If Not LayerUtils.IsMechanicalLayer(Want) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_MECHANICAL',
            LayerStr + ' is not a mechanical layer. A 3D body lives on a '
            + 'mechanical layer, the way Altium''s own body dialog offers it.');
        Exit;
    End;

    { keep_layers (issue #82): bodies already on one of these layers stay
      where they are. The target is always kept. Every name is resolved
      before anything is walked, so an unknown one refuses the whole call. }
    KeepStr := ExtractJsonValue(Params, 'keep_layers');
    KeepIds := #9 + IntToStr(Want) + #9;
    While Trim(KeepStr) <> '' Do
    Begin
        CommaPos := Pos(',', KeepStr);
        If CommaPos > 0 Then
        Begin
            Tok := Trim(Copy(KeepStr, 1, CommaPos - 1));
            KeepStr := Copy(KeepStr, CommaPos + 1, Length(KeepStr));
        End
        Else
        Begin
            Tok := Trim(KeepStr);
            KeepStr := '';
        End;
        If Tok <> '' Then
        Begin
            KeepL := eNoLayer;
            Try KeepL := ResolveLayerId(PcbLib.Board, Tok); Except KeepL := eNoLayer; End;
            If KeepL = eNoLayer Then
            Begin
                Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
                    'Unknown keep layer: ' + Tok + '. Nothing was moved. '
                    + BoardLayerNamesHint(PcbLib.Board));
                Exit;
            End;
            KeepIds := KeepIds + IntToStr(KeepL) + #9;
        End;
    End;

    Footprints := 0;
    Bodies := 0;
    Moved := 0;
    Already := 0;
    NoBody := 0;
    Found := '';
    Items := '';

    PCBServer.PreProcess;
    Try
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While Footprint <> Nil Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;
                If FootprintIsWanted(Wanted, FpName) Then
                Begin
                    Footprints := Footprints + 1;
                    Found := Found + FpName + #9;
                    FpMoved := 0;
                    FpBodies := 0;
                    FromJson := '';
                    { The footprint on screen can list an object twice, once  }
                    { through the board, so each body is taken once.          }
                    Seen := #9;
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
                        Body := GrpIter.FirstPCBObject;
                        While Body <> Nil Do
                        Begin
                            Addr := IntToStr(Body.I_ObjectAddress);
                            If Pos(#9 + Addr + #9, Seen) = 0 Then
                            Begin
                                Seen := Seen + Addr + #9;
                                FpBodies := FpBodies + 1;
                                Was := Body.Layer;
                                If (Was = Want) Or (Pos(#9 + IntToStr(Was) + #9, KeepIds) > 0) Then
                                    Already := Already + 1
                                Else
                                Begin
                                    PCBServer.SendMessageToRobots(Body.I_ObjectAddress,
                                        c_Broadcast, PCBM_BeginModify, c_NoEventData);
                                    Body.Layer := Want;
                                    PCBServer.SendMessageToRobots(Body.I_ObjectAddress,
                                        c_Broadcast, PCBM_EndModify, c_NoEventData);
                                    FpMoved := FpMoved + 1;
                                    If FromJson <> '' Then FromJson := FromJson + ',';
                                    FromJson := FromJson + '"'
                                        + EscapeJsonString(GetLayerString(Was)) + '"';
                                End;
                            End;
                            Body := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                    Bodies := Bodies + FpBodies;
                    Moved := Moved + FpMoved;
                    If FpBodies = 0 Then NoBody := NoBody + 1;
                    If FpMoved > 0 Then
                    Begin
                        If Items <> '' Then Items := Items + ',';
                        Items := Items + JsonObj(
                            JsonStr('name', FpName) + ','
                            + JsonInt('moved', FpMoved) + ','
                            + JsonRaw('from', '[' + FromJson + ']'));
                    End;
                End;
                Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;
    Finally
        PCBServer.PostProcess;
    End;

    If Moved > 0 Then
    Begin
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(PcbLib.Board.FileName);
    End;

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('layer', LayerStr) + ','
        + MechLayerEnabledJson(PcbLib.Board, Want, LayerStr) + ','
        + JsonInt('footprints', Footprints) + ','
        + JsonInt('bodies', Bodies) + ','
        + JsonInt('moved', Moved) + ','
        + JsonInt('already_on_layer', Already) + ','
        + JsonInt('footprints_without_bodies', NoBody) + ','
        + JsonRaw('not_found', '[' + FootprintsNotFound(Wanted, Found) + ']') + ','
        + JsonRaw('items', '[' + Items + ']')));
End;

{ Grow the box L..R, B..T to take in RL..RR, RB..RT. An empty box has R < L. }
Procedure GrowBox(Var L, B, R, T : TCoord; RL, RB, RR, RT : TCoord);
Begin
    If (RR <= RL) Or (RT <= RB) Then Exit;
    If R < L Then
    Begin
        L := RL;
        R := RR;
        B := RB;
        T := RT;
        Exit;
    End;
    If RL < L Then L := RL;
    If RR > R Then R := RR;
    If RB < B Then B := RB;
    If RT > T Then T := RT;
End;

{ The extent of a footprint's own geometry of one kind, in board coordinates. }
{ Source is body (the 3D bodies' outlines), pads (their copper), or          }
{ courtyard (whatever is drawn on CourtLayer). Right < Left when the         }
{ footprint has none.                                                        }
{                                                                            }
{ A pad's bounding rectangle includes its solder mask expansion, so its size }
{ is taken from TopXSize and TopYSize, turned with the pad when it sits at   }
{ 90 or 270 degrees, and only its centre from the rectangle. A pad at any    }
{ other angle falls back to the rectangle, a little larger than the copper.  }
Procedure FootprintExtent(Footprint : IPCB_LibComponent; Source : String;
    CourtLayer : TLayer; Var L, B, R, T : TCoord);
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Pad : IPCB_Pad;
    Box : TCoordRect;
    Cx, Cy, Hx, Hy : TCoord;
    Rot : Integer;
Begin
    L := 1;
    R := 0;
    B := 1;
    T := 0;
    GrpIter := Footprint.GroupIterator_Create;
    Try
        If Source = 'pads' Then
        Begin
            { Its own walk, so the pad members are read off a pad. }
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Pad := GrpIter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                Box := Pad.BoundingRectangle;
                Rot := Round(Pad.Rotation) Mod 360;
                If Rot < 0 Then Rot := Rot + 360;
                If (Rot = 0) Or (Rot = 90) Or (Rot = 180) Or (Rot = 270) Then
                Begin
                    Cx := (Box.Left + Box.Right) Div 2;
                    Cy := (Box.Bottom + Box.Top) Div 2;
                    Hx := Pad.TopXSize Div 2;
                    Hy := Pad.TopYSize Div 2;
                    If (Rot = 90) Or (Rot = 270) Then
                    Begin
                        Hx := Pad.TopYSize Div 2;
                        Hy := Pad.TopXSize Div 2;
                    End;
                    GrowBox(L, B, R, T, Cx - Hx, Cy - Hy, Cx + Hx, Cy + Hy);
                End
                Else
                    GrowBox(L, B, R, T, Box.Left, Box.Bottom, Box.Right, Box.Top);
                Pad := GrpIter.NextPCBObject;
            End;
            Exit;
        End;
        If Source = 'body' Then
            GrpIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject))
        Else
            GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
                eRegionObject, eFillObject));
        Prim := GrpIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            If (Source = 'body') Or (Prim.Layer = CourtLayer) Then
            Begin
                Box := Prim.BoundingRectangle;
                GrowBox(L, B, R, T, Box.Left, Box.Bottom, Box.Right, Box.Top);
            End;
            Prim := GrpIter.NextPCBObject;
        End;
    Finally
        Footprint.GroupIterator_Destroy(GrpIter);
    End;
End;

{ Remove from Footprint every track, arc, text, fill and region on Layer,     }
{ and free each. Bodies and pads stay: a layer shared with the 3D bodies, or  }
{ a pad on a mechanical layer, is not drawing to clear. The footprint must    }
{ NOT be current, for the reason FootprintToHoldFocus records. One match per  }
{ pass, removed while its iterator is open, as Lib_DeleteFootprintPrimitives  }
{ does.                                                                       }
Function ClearDrawingOnLayer(Footprint : IPCB_LibComponent; Layer : TLayer) : Integer;
Var
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Found : Boolean;
    I : Integer;
Begin
    Result := 0;
    PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
        c_Broadcast, PCBM_BeginModify, c_NoEventData);
    For I := 1 To 5000 Do
    Begin
        Found := False;
        GrpIter := Footprint.GroupIterator_Create;
        Try
            GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
                eTextObject, eFillObject, eRegionObject));
            Prim := GrpIter.FirstPCBObject;
            While Prim <> Nil Do
            Begin
                If Prim.Layer = Layer Then
                Begin
                    Found := True;
                    Break;
                End;
                Prim := GrpIter.NextPCBObject;
            End;
            If Found And (Prim <> Nil) Then
            Begin
                Try
                    Footprint.RemovePCBObject(Prim);
                    PCBServer.DestroyPCBObject(Prim);
                    Result := Result + 1;
                Except
                    Found := False;
                End;
            End;
        Finally
            Footprint.GroupIterator_Destroy(GrpIter);
        End;
        If Not Found Then Break;
    End;
    PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
        c_Broadcast, PCBM_EndModify, c_NoEventData);
End;

{ Attach a new primitive to the footprint on screen, the way every footprint }
{ writer here does: footprint and board, registration broadcast to both.     }
Procedure AttachToCurrentFootprint(PcbLib : IPCB_Library;
    Footprint : IPCB_LibComponent; Prim : IPCB_Primitive);
Begin
    Footprint.AddPCBObject(Prim);
    PcbLib.Board.AddPCBObject(Prim);
    PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress,
        c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
    PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress,
        c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
End;

Procedure AddOutlineTrack(PcbLib : IPCB_Library; Footprint : IPCB_LibComponent;
    Layer : TLayer; Width, X1, Y1, X2, Y2 : TCoord);
Var
    Track : IPCB_Track;
Begin
    Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    Track.X1 := X1;
    Track.Y1 := Y1;
    Track.X2 := X2;
    Track.Y2 := Y2;
    Track.Width := Width;
    Track.Layer := Layer;
    AttachToCurrentFootprint(PcbLib, Footprint, Track);
End;

{ Lib_RedrawAssembly - draw a footprint's assembly layer again from its own  }
{ geometry.                                                                  }
{                                                                            }
{ The assembly drawing is a rectangle round the part and a .Designator in    }
{ its middle, and libraries collected over the years carry it on the wrong   }
{ layer, from a different package, or not at all. Each footprint gets its    }
{ old drawing on the layer cleared (tracks, arcs, texts, fills, regions;     }
{ never bodies or pads) and a new one drawn round its 3D bodies, its         }
{ courtyard or its pads.                                                     }
{                                                                            }
{ outline_from auto takes the bodies, then the courtyard when a              }
{ courtyard_layer is named, then the pads; a footprint with none of them is  }
{ skipped and named. The text is sized from the box, turned upright in a box }
{ taller than wide, and centred from its own measured extent when that       }
{ reads plausibly: a text just added can report an empty rectangle, and      }
{ centring on that moved the glyph by the whole footprint offset.            }
{                                                                            }
{ Clearing happens with another footprint current and drawing with the       }
{ target current, which is what each of the two needs; the footprint the     }
{ editor showed is shown again at the end.                                   }
Function Lib_RedrawAssembly(Params : String; RequestId : String) : String;
Var
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint, SavedCurrent, Holder, Other : IPCB_LibComponent;
    Text : IPCB_Text;
    LayerStr, CourtStr, Wanted, Source, Used, UnitsStr, FpName, Found : String;
    Items, Skipped, Names, SavedName : String;
    Layer, CourtLayer : TLayer;
    TR : TCoordRect;
    BL, BB, BR, BT : TCoord;
    Width, TextH, Size, Cx, Cy, W, H, Dx, Dy : TCoord;
    WantText, Replace, TempMade, TempNow : Boolean;
    Cleared, Drawn, TotalCleared : Integer;
Begin
    UnitsStr := ExtractJsonValue(Params, 'units');
    If UnitsProblem(UnitsStr) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(UnitsStr));
        Exit;
    End;
    LayerStr := Trim(ExtractJsonValue(Params, 'layer'));
    CourtStr := Trim(ExtractJsonValue(Params, 'courtyard_layer'));
    Wanted := ExtractJsonValue(Params, 'footprint_names');
    Source := LowerCase(Trim(ExtractJsonValue(Params, 'outline_from')));
    If Source = '' Then Source := 'auto';
    WantText := ExtractJsonValue(Params, 'designator') <> 'false';
    Replace := ExtractJsonValue(Params, 'replace') <> 'false';
    Width := CoordFromUnits(StrToFloatDef(ExtractJsonValue(Params, 'width'),
        MilsInUnits(4, UnitsStr)), UnitsStr);
    TextH := CoordFromUnits(StrToFloatDef(ExtractJsonValue(Params, 'text_height'), 0),
        UnitsStr);

    If LayerStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'layer is required: the mechanical layer the assembly drawing is on');
        Exit;
    End;
    If (Source <> 'auto') And (Source <> 'body') And (Source <> 'pads')
       And (Source <> 'courtyard') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_SOURCE',
            'outline_from must be auto, body, pads or courtyard. Got: ' + Source);
        Exit;
    End;
    If (Source = 'courtyard') And (CourtStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'outline_from courtyard needs courtyard_layer');
        Exit;
    End;
    If Replace And (ExtractJsonValue(Params, 'confirm') <> 'true') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CONFIRM_REQUIRED',
            'This clears the tracks, arcs, texts, fills and regions on '
            + LayerStr + ' in every footprint it redraws. Pass confirm=true '
            + 'once that layer holds only the assembly drawing, or '
            + 'replace=false to draw beside what is there.');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'No PCB library is focused. This redraws footprints inside a .PcbLib.');
        Exit;
    End;

    Layer := eNoLayer;
    Try Layer := ResolveLayerId(PcbLib.Board, LayerStr); Except Layer := eNoLayer; End;
    If Layer = eNoLayer Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
            'Unknown layer name: ' + LayerStr + '. '
            + BoardLayerNamesHint(PcbLib.Board));
        Exit;
    End;
    If Not LayerUtils.IsMechanicalLayer(Layer) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_MECHANICAL',
            LayerStr + ' is not a mechanical layer. Clearing a copper or '
            + 'overlay layer here would take the footprint''s own drawing.');
        Exit;
    End;
    CourtLayer := eNoLayer;
    If CourtStr <> '' Then
    Begin
        Try CourtLayer := ResolveLayerId(PcbLib.Board, CourtStr);
        Except CourtLayer := eNoLayer; End;
        If CourtLayer = eNoLayer Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_LAYER',
                'Unknown courtyard_layer: ' + CourtStr + '. '
                + BoardLayerNamesHint(PcbLib.Board));
            Exit;
        End;
    End;
    If Replace And (CourtLayer = Layer) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAME_LAYER',
            'courtyard_layer and layer are both ' + LayerStr + ', so clearing '
            + 'the old assembly drawing would take the courtyard with it. '
            + 'Pass replace=false to draw beside it.');
        Exit;
    End;

    { Names first, footprints looked up again one at a time: switching the  }
    { current footprint while a library iterator is open is not something   }
    { to find out about live.                                               }
    Names := '';
    Found := '';
    Iter := PcbLib.LibraryIterator_Create;
    Try
        Footprint := Iter.FirstPCBObject;
        While Footprint <> Nil Do
        Begin
            FpName := '';
            Try FpName := Footprint.Name; Except End;
            If (FpName <> '') And FootprintIsWanted(Wanted, FpName) Then
            Begin
                Names := Names + FpName + #9;
                Found := Found + FpName + #9;
            End;
            Footprint := Iter.NextPCBObject;
        End;
    Finally
        PcbLib.LibraryIterator_Destroy(Iter);
    End;

    SavedName := '';
    SavedCurrent := Nil;
    Try SavedCurrent := PcbLib.CurrentComponent; Except End;
    If SavedCurrent <> Nil Then Try SavedName := SavedCurrent.Name; Except End;
    Holder := Nil;
    TempMade := False;
    Items := '';
    Skipped := '';
    Drawn := 0;
    TotalCleared := 0;

    PCBServer.PreProcess;
    Try
        While Names <> '' Do
        Begin
            FpName := SplitNextTab(Names);
            If FpName = '' Then Continue;
            Footprint := FootprintByName(PcbLib, FpName);
            If Footprint = Nil Then Continue;

            Used := Source;
            If Source = 'auto' Then
            Begin
                Used := 'body';
                FootprintExtent(Footprint, 'body', CourtLayer, BL, BB, BR, BT);
                If (BR < BL) And (CourtLayer <> eNoLayer) Then
                Begin
                    Used := 'courtyard';
                    FootprintExtent(Footprint, 'courtyard', CourtLayer, BL, BB, BR, BT);
                End;
                If BR < BL Then
                Begin
                    Used := 'pads';
                    FootprintExtent(Footprint, 'pads', CourtLayer, BL, BB, BR, BT);
                End;
            End
            Else
                FootprintExtent(Footprint, Source, CourtLayer, BL, BB, BR, BT);

            If BR < BL Then
            Begin
                If Skipped <> '' Then Skipped := Skipped + ',';
                Skipped := Skipped + '"' + EscapeJsonString(FpName) + '"';
                Continue;
            End;

            Cleared := 0;
            If Replace Then
            Begin
                If IsCurrentFootprint(PcbLib, Footprint) Then
                Begin
                    Other := FootprintToHoldFocus(PcbLib, Footprint, TempNow);
                    If TempNow Then
                    Begin
                        Holder := Other;
                        TempMade := True;
                    End;
                    PcbLib.SetState_CurrentComponent(Other);
                End;
                Cleared := ClearDrawingOnLayer(Footprint, Layer);
                TotalCleared := TotalCleared + Cleared;
            End;

            { Measured again with the footprint on screen. Off screen its      }
            { primitives read relative to the footprint origin, and an outline }
            { drawn from that landed a whole footprint offset away.            }
            PcbLib.SetState_CurrentComponent(Footprint);
            FootprintExtent(Footprint, Used, CourtLayer, BL, BB, BR, BT);
            If BR < BL Then
            Begin
                If Skipped <> '' Then Skipped := Skipped + ',';
                Skipped := Skipped + '"' + EscapeJsonString(FpName) + '"';
                Continue;
            End;
            AddOutlineTrack(PcbLib, Footprint, Layer, Width, BL, BB, BR, BB);
            AddOutlineTrack(PcbLib, Footprint, Layer, Width, BR, BB, BR, BT);
            AddOutlineTrack(PcbLib, Footprint, Layer, Width, BR, BT, BL, BT);
            AddOutlineTrack(PcbLib, Footprint, Layer, Width, BL, BT, BL, BB);

            If WantText Then
            Begin
                W := BR - BL;
                H := BT - BB;
                Cx := (BL + BR) Div 2;
                Cy := (BB + BT) Div 2;
                If TextH > 0 Then
                    Size := TextH
                Else
                Begin
                    { A third of the short side, and short enough for a      }
                    { four-character designator to fit along the long side,  }
                    { between 0.2 and 1 mm. The library shows .Designator    }
                    { itself, which is wider than what a board will show.    }
                    If W < H Then Size := W Div 3 Else Size := H Div 3;
                    If (W >= H) And (Size > W * 10 Div 32) Then Size := W * 10 Div 32;
                    If (H > W) And (Size > H * 10 Div 32) Then Size := H * 10 Div 32;
                    If Size > MMToCoord(1) Then Size := MMToCoord(1);
                    If Size < MMToCoord(0.2) Then Size := MMToCoord(0.2);
                End;
                Text := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
                Text.Layer := Layer;
                Text.UseTTFonts := False;
                Text.UnderlyingString := '.Designator';
                Text.Size := Size;
                If Size Div 8 < MMToCoord(0.05) Then
                    Text.Width := MMToCoord(0.05)
                Else
                    Text.Width := Size Div 8;
                { Placed by estimate: stroke characters run about 0.8 of  }
                { the height wide, eleven of them in .Designator.         }
                If H > W Then
                Begin
                    Text.Rotation := 90;
                    Text.XLocation := Cx + Size Div 2;
                    Text.YLocation := Cy - (Size * 88 Div 10) Div 2;
                End
                Else
                Begin
                    Text.XLocation := Cx - (Size * 88 Div 10) Div 2;
                    Text.YLocation := Cy - Size Div 2;
                End;
                AttachToCurrentFootprint(PcbLib, Footprint, Text);
                { Refined from the measured extent when it is believable:  }
                { not empty, and no further off than the estimate could be. }
                TR := Text.BoundingRectangle;
                If (TR.Right > TR.Left) And (TR.Top > TR.Bottom) Then
                Begin
                    Dx := Cx - (TR.Left + TR.Right) Div 2;
                    Dy := Cy - (TR.Bottom + TR.Top) Div 2;
                    If (Abs(Dx) < Size * 11) And (Abs(Dy) < Size * 11) Then
                    Begin
                        PCBServer.SendMessageToRobots(Text.I_ObjectAddress,
                            c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        Text.MoveByXY(Dx, Dy);
                        PCBServer.SendMessageToRobots(Text.I_ObjectAddress,
                            c_Broadcast, PCBM_EndModify, c_NoEventData);
                    End;
                End;
            End;

            Drawn := Drawn + 1;
            If Items <> '' Then Items := Items + ',';
            Items := Items + JsonObj(
                JsonStr('name', FpName) + ','
                + JsonStr('outline_from', Used) + ','
                + JsonInt('cleared', Cleared) + ','
                + JsonFloat('width_mm', CoordToMM(BR - BL)) + ','
                + JsonFloat('height_mm', CoordToMM(BT - BB)));
        End;
    Finally
        PCBServer.PostProcess;
        If SavedName <> '' Then
        Begin
            SavedCurrent := FootprintByName(PcbLib, SavedName);
            If SavedCurrent <> Nil Then
                Try PcbLib.SetState_CurrentComponent(SavedCurrent); Except End;
        End;
        If TempMade Then
        Begin
            PcbLib.RemoveComponent(Holder);
            PcbLib.DeRegisterComponent(Holder);
            PCBServer.DestroyPCBLibComp(Holder);
        End;
    End;

    If Drawn > 0 Then
    Begin
        Try PcbLib.Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(PcbLib.Board.FileName);
    End;

    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('layer', LayerStr) + ','
        + MechLayerEnabledJson(PcbLib.Board, Layer, LayerStr) + ','
        + JsonInt('redrawn', Drawn) + ','
        + JsonInt('cleared', TotalCleared) + ','
        + JsonRaw('skipped', '[' + Skipped + ']') + ','
        + JsonStr('skipped_note', 'No 3D body, courtyard or pad to draw '
            + 'round, so nothing was cleared or drawn there.') + ','
        + JsonRaw('not_found', '[' + FootprintsNotFound(Wanted, Found) + ']') + ','
        + JsonRaw('items', '[' + Items + ']')));
End;

Function Lib_SetMechLayers(Params : String; RequestId : String) : String;
Var
    LibPath, FocusedPath, OpsStr : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Board : IPCB_Board;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    OpsStr := ExtractJsonValue(Params, 'layers');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
    If LibPath = '' Then LibPath := FocusedPath;
    If LibPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'library_path required, and no document is focused');
        Exit;
    End;

    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', LibPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;

    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(LibPath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT_FOCUSED',
            'Asked for ' + LibPath + ' but the focused document is "'
            + FocusedPath + '". Nothing was changed: editing whichever '
            + 'library happens to be in front is how a sweep silently '
            + 'rewrites the same file twenty times.');
        Exit;
    End;

    PcbLib := PCBServer.GetCurrentPCBLibrary;
    If PcbLib = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'Focused ' + LibPath + ' but it is not a PCB library. For a '
            + 'BOARD use pcb_set_mech_layers, which takes the open PcbDoc '
            + 'and reaches the same names, enables and kinds.');
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

    Result := ApplyMechLayerOps(Board, OpsStr,
        ExtractJsonValue(Params, 'tidy_pairs') = 'true', LibPath, RequestId);
End;

{..............................................................................}
{ DATABASE LIBRARIES (DbLib)                                                   }
{                                                                              }
{ A .DbLib holds no parts. It is a connection string plus a list of tables,    }
{ and every row of an enabled table is a component: the row names a symbol in  }
{ a .SchLib and footprints in .PcbLibs, and carries the parameters. lib_search }
{ reads open .SchLib files only, so a user whose parts live in a DbLib got 0   }
{ hits from it.                                                                }
{                                                                              }
{ WHAT ALTIUM PUBLISHES, AND WHAT IS USED HERE. The integrated-library API     }
{ lists the DbLib members of IntegratedLibraryManager (GetAvailableDBLibDoc-   }
{ AtPath, GetComponentLocationFromDatabase, GetDatabaseDatafileLocation and    }
{ others) by name only, with no signature and no description. Community        }
{ scripts show GetAvailableDBLibDocAtPath answering a document with            }
{ GetTableCount, GetTableNameAt and GetConnectionString, and nothing that      }
{ lists a table's columns or rows. So the table definitions are read from the  }
{ .DbLib file itself, inside this handler and not from Python: it is an INI    }
{ with a ConnectionString, one [TableN] section per table and one Options=     }
{ line per mapped column. The rows are read through ADO, with TADOConnection   }
{ and TADOQuery exactly as libADOQuery.pas in the reference drives them from   }
{ DelphiScript.                                                                }
{                                                                              }
{ READ ONLY. Every statement is a SELECT assembled from the DbLib's own        }
{ declared table names and the key column it names, quoted with the DbLib's    }
{ quote characters by DbLibQuoteIdent, which refuses any name that could       }
{ close the quote. Text a caller supplies never enters a statement: a search   }
{ is matched here, row by row, and a record key is bound as an ADO parameter.  }
{ Access and Excel sources are opened with Mode=Read, which is also what lets  }
{ a second reader open a file Altium holds Share Deny Write.                   }
{                                                                              }
{ A CONNECTION STRING CAN CARRY A PASSWORD. It is never returned and never put }
{ in an error. DbLibRedactConnStr blanks every Password and Pwd value before   }
{ the string is reported, and no error here quotes the connection or ADO's own }
{ message text.                                                                }
{..............................................................................}

{ The text after the first '=' of an INI line. }
Function DbLibLineValue(Line : String) : String;
Var
    P : Integer;
    Value : String;
Begin
    Value := '';
    P := Pos('=', Line);
    If P > 0 Then Value := Copy(Line, P + 1, Length(Line));
    Result := Value;
End;

{ True when an INI line sets Key. Case and spaces around the key are ignored. }
Function DbLibLineHasKey(Line : String; Key : String) : Boolean;
Var
    P : Integer;
Begin
    Result := False;
    P := Pos('=', Line);
    If P < 2 Then Exit;
    Result := LowerCase(Trim(Copy(Line, 1, P - 1))) = LowerCase(Key);
End;

{ The name inside a section header line, or '' for any other line. }
Function DbLibSectionName(Line : String) : String;
Var
    S, Name : String;
Begin
    Name := '';
    S := Trim(Line);
    If (Length(S) >= 3) And (Copy(S, 1, 1) = '[') And (Copy(S, Length(S), 1) = ']')
        And (Pos('=', S) = 0) Then
        Name := Copy(S, 2, Length(S) - 2);
    Result := Name;
End;

{ True for the sections that declare a table: Table1, Table2 and so on. }
Function DbLibIsTableSection(Name : String) : Boolean;
Var
    I : Integer;
    Rest, Ch : String;
Begin
    Result := False;
    If LowerCase(Copy(Name, 1, 5)) <> 'table' Then Exit;
    Rest := Copy(Name, 6, Length(Name));
    If Rest = '' Then Exit;
    For I := 1 To Length(Rest) Do
    Begin
        Ch := Copy(Rest, I, 1);
        If (Ch < '0') Or (Ch > '9') Then Exit;
    End;
    Result := True;
End;

{ The first value of Key outside the table sections, '' when absent. }
Function DbLibGlobalValue(Lines : TStringList; Key : String) : String;
Var
    I : Integer;
    InTable, Found : Boolean;
    Line, Section, Value : String;
Begin
    Value := '';
    InTable := False;
    Found := False;
    For I := 0 To Lines.Count - 1 Do
    Begin
        If Found Then Break;
        Line := Lines.Get(I);
        Section := DbLibSectionName(Line);
        If Section <> '' Then
        Begin
            InTable := DbLibIsTableSection(Section);
            Continue;
        End;
        If (Not InTable) And DbLibLineHasKey(Line, Key) Then
        Begin
            Value := DbLibLineValue(Line);
            Found := True;
        End;
    End;
    Result := Value;
End;

{ Every table the DbLib declares, in file order, separated by tabs. A table   }
{ section with no TableName is skipped.                                       }
Function DbLibTableNames(Lines : TStringList) : String;
Var
    I : Integer;
    InTable : Boolean;
    Line, Section, Names, Name : String;
Begin
    Names := '';
    InTable := False;
    For I := 0 To Lines.Count - 1 Do
    Begin
        Line := Lines.Get(I);
        Section := DbLibSectionName(Line);
        If Section <> '' Then
        Begin
            InTable := DbLibIsTableSection(Section);
            Continue;
        End;
        If InTable And DbLibLineHasKey(Line, 'TableName') Then
        Begin
            Name := Trim(DbLibLineValue(Line));
            If Name <> '' Then
            Begin
                If Names <> '' Then Names := Names + #9;
                Names := Names + Name;
            End;
        End;
    End;
    Result := Names;
End;

{ Every section name in the file, separated by tabs. Reported when no table   }
{ is found, so a file laid out differently can be diagnosed from the reply.   }
Function DbLibSectionNames(Lines : TStringList) : String;
Var
    I : Integer;
    Section, Names : String;
Begin
    Names := '';
    For I := 0 To Lines.Count - 1 Do
    Begin
        Section := DbLibSectionName(Lines.Get(I));
        If Section <> '' Then
        Begin
            If Names <> '' Then Names := Names + #9;
            Names := Names + Section;
        End;
    End;
    Result := Names;
End;

{ The value of Key in the table section whose TableName is Table, matched    }
{ without case. TableName need not come first in its section, so a section   }
{ is only judged once it ends. '' when the table or the key is absent.        }
Function DbLibTableAttr(Lines : TStringList; Table : String; Key : String) : String;
Var
    I : Integer;
    InTable, Done, HaveValue : Boolean;
    Line, Section, SecTable, SecValue, Value : String;
Begin
    Value := '';
    InTable := False;
    Done := False;
    HaveValue := False;
    SecTable := '';
    SecValue := '';
    For I := 0 To Lines.Count Do
    Begin
        If Done Then Break;
        If I < Lines.Count Then
        Begin
            Line := Lines.Get(I);
            Section := DbLibSectionName(Line);
        End
        Else
        Begin
            Line := '';
            Section := 'end of file';
        End;
        If Section <> '' Then
        Begin
            If InTable And (SecTable <> '')
                And (LowerCase(SecTable) = LowerCase(Trim(Table))) Then
            Begin
                Value := SecValue;
                Done := True;
            End;
            InTable := DbLibIsTableSection(Section);
            SecTable := '';
            SecValue := '';
            HaveValue := False;
        End
        Else
        Begin
            If InTable Then
            Begin
                If DbLibLineHasKey(Line, 'TableName') Then
                    SecTable := Trim(DbLibLineValue(Line));
                If (Not HaveValue) And DbLibLineHasKey(Line, Key) Then
                Begin
                    SecValue := DbLibLineValue(Line);
                    HaveValue := True;
                End;
            End;
        End;
    End;
    Result := Value;
End;

{ One Name=Value item out of an Options= body, whose items are separated by  }
{ '|'. '' when the item is absent.                                           }
Function DbLibOptionValue(Body : String; Name : String) : String;
Var
    Rest, Item, Value : String;
    P, Q : Integer;
    Found : Boolean;
Begin
    Value := '';
    Found := False;
    Rest := Body;
    While (Rest <> '') And (Not Found) Do
    Begin
        P := Pos('|', Rest);
        If P > 0 Then
        Begin
            Item := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End
        Else
        Begin
            Item := Rest;
            Rest := '';
        End;
        Q := Pos('=', Item);
        If (Q > 1) And (LowerCase(Trim(Copy(Item, 1, Q - 1))) = LowerCase(Name)) Then
        Begin
            Value := Copy(Item, Q + 1, Length(Item));
            Found := True;
        End;
    End;
    Result := Value;
End;

{ The column a table maps to a system parameter such as [Library Ref], read   }
{ from the DbLib's Options= lines. '' when the DbLib maps nothing to it.      }
Function DbLibMappedField(Lines : TStringList; Table : String; ParamName : String) : String;
Var
    I, P : Integer;
    Line, Body, Field, Full : String;
    Found : Boolean;
Begin
    Field := '';
    Found := False;
    For I := 0 To Lines.Count - 1 Do
    Begin
        If Found Then Break;
        Line := Lines.Get(I);
        If Not DbLibLineHasKey(Line, 'Options') Then Continue;
        Body := DbLibLineValue(Line);
        If LowerCase(Trim(DbLibOptionValue(Body, 'TableNameOnly'))) <> LowerCase(Trim(Table)) Then Continue;
        If LowerCase(Trim(DbLibOptionValue(Body, 'ParameterName'))) <> LowerCase(ParamName) Then Continue;
        Field := Trim(DbLibOptionValue(Body, 'FieldNameOnly'));
        If Field = '' Then
        Begin
            Full := Trim(DbLibOptionValue(Body, 'FieldName'));
            P := Pos('.', Full);
            While P > 0 Do
            Begin
                Full := Copy(Full, P + 1, Length(Full));
                P := Pos('.', Full);
            End;
            Field := Full;
        End;
        If Field <> '' Then Found := True;
    End;
    Result := Field;
End;

{ The column a where-clause lookup matches on, from a clause shaped like      }
{ [Part Number] = '...': the first quoted name, else the text before '='.    }
Function DbLibKeyFromWhere(Where : String; LeftQ : String; RightQ : String) : String;
Var
    W, Rest, Key : String;
    P, E : Integer;
Begin
    Key := '';
    W := Trim(Where);
    If (LeftQ <> '') And (RightQ <> '') Then
    Begin
        P := Pos(LeftQ, W);
        If P > 0 Then
        Begin
            Rest := Copy(W, P + Length(LeftQ), Length(W));
            E := Pos(RightQ, Rest);
            If E > 1 Then Key := Copy(Rest, 1, E - 1);
        End;
    End;
    If Key = '' Then
    Begin
        E := Pos('=', W);
        If E > 1 Then Key := Trim(Copy(W, 1, E - 1));
        While (Key <> '') And (Copy(Key, 1, 1) = '(') Do
            Key := Trim(Copy(Key, 2, Length(Key)));
    End;
    Result := Key;
End;

{ The position of Name in Names, matched without case, or -1. }
Function DbLibIndexOfName(Names : TStringList; Name : String) : Integer;
Var
    I, Found : Integer;
    Want : String;
Begin
    Found := -1;
    Want := LowerCase(Trim(Name));
    If Want <> '' Then
    Begin
        For I := 0 To Names.Count - 1 Do
        Begin
            If LowerCase(Trim(Names.Get(I))) = Want Then
            Begin
                Found := I;
                Break;
            End;
        End;
    End;
    Result := Found;
End;

{ The declared spelling of Name in a tab-separated list, matched without     }
{ case, or '' when the list does not hold it.                                }
Function DbLibFindTab(TabList : String; Name : String) : String;
Var
    Rest, One, Found : String;
Begin
    Found := '';
    Rest := TabList;
    While (Rest <> '') And (Found = '') Do
    Begin
        One := SplitNextTab(Rest);
        If (One <> '') And (LowerCase(One) = LowerCase(Trim(Name))) Then Found := One;
    End;
    Result := Found;
End;

{ True for a path ending in .DbLib or .SVNDbLib. }
Function DbLibHasDbLibExt(Path : String) : Boolean;
Var
    L : String;
Begin
    L := LowerCase(Trim(Path));
    Result := (Copy(L, Length(L) - 5, 6) = '.dblib')
        Or (Copy(L, Length(L) - 8, 9) = '.svndblib');
End;

{ Why a library_path cannot be used, or '' when it can. }
Function DbLibPathProblem(Path : String) : String;
Var
    Problem : String;
Begin
    Problem := '';
    If Trim(Path) = '' Then
        Problem := 'library_path is required: the full path of a .DbLib file'
    Else
    Begin
        If Not DbLibHasDbLibExt(Path) Then
            Problem := 'library_path must name a .DbLib or .SVNDbLib file; '
                + 'a .SchLib is searched with lib_search and an .IntLib '
                + 'with lib_extract_intlib';
    End;
    Result := Problem;
End;

{ One Key=Value pair out of a connection string, starting at P and moving P  }
{ past it. A value may be quoted with a double or single quote, in which a    }
{ doubled quote stands for itself, or braced as ODBC does, and either form    }
{ may hold a ';'. RawValue keeps the quotes. HasEq is False for a bare word.  }
{ Returns False when nothing is left.                                         }
Function DbLibConnNextPair(S : String; Var P : Integer; Var Key : String;
    Var RawValue : String; Var HasEq : Boolean) : Boolean;
Var
    N, Start, Mode : Integer;
    Ch, Closer, Raw : String;
Begin
    Result := False;
    Key := '';
    RawValue := '';
    HasEq := False;
    N := Length(S);
    While (P <= N) And ((Copy(S, P, 1) = ';') Or (Copy(S, P, 1) = ' ')) Do
        Inc(P);
    If P > N Then Exit;
    Start := P;
    While (P <= N) And (Copy(S, P, 1) <> '=') And (Copy(S, P, 1) <> ';') Do
        Inc(P);
    Key := Trim(Copy(S, Start, P - Start));
    Result := True;
    If (P > N) Or (Copy(S, P, 1) = ';') Then Exit;
    HasEq := True;
    Inc(P);
    Raw := '';
    Mode := 0;
    Closer := '';
    While P <= N Do
    Begin
        Ch := Copy(S, P, 1);
        If Mode = 0 Then
        Begin
            If Ch = ';' Then Break;
            If Trim(Raw) = '' Then
            Begin
                If (Ch = '"') Or (Ch = '''') Then
                Begin
                    Mode := 1;
                    Closer := Ch;
                End;
                If Ch = '{' Then
                Begin
                    Mode := 1;
                    Closer := '}';
                End;
            End;
            Raw := Raw + Ch;
            Inc(P);
        End
        Else
        Begin
            Raw := Raw + Ch;
            Inc(P);
            If Ch = Closer Then
            Begin
                If Copy(S, P, 1) = Closer Then
                Begin
                    Raw := Raw + Closer;
                    Inc(P);
                End
                Else
                    Mode := 0;
            End;
        End;
    End;
    RawValue := Raw;
End;

{ A connection-string value without its quotes or braces. }
Function DbLibUnquote(Raw : String) : String;
Var
    S, Opener, Closer, Inner, Value, Ch : String;
    I : Integer;
Begin
    S := Trim(Raw);
    Value := S;
    Opener := Copy(S, 1, 1);
    Closer := '';
    If (Opener = '"') Or (Opener = '''') Then Closer := Opener;
    If Opener = '{' Then Closer := '}';
    If (Closer <> '') And (Length(S) >= 2) And (Copy(S, Length(S), 1) = Closer) Then
    Begin
        Inner := Copy(S, 2, Length(S) - 2);
        Value := '';
        I := 1;
        While I <= Length(Inner) Do
        Begin
            Ch := Copy(Inner, I, 1);
            Value := Value + Ch;
            If (Ch = Closer) And (Copy(Inner, I + 1, 1) = Closer) Then Inc(I);
            Inc(I);
        End;
    End;
    Result := Value;
End;

{ S with every C written twice, which is how a quote is kept inside a value   }
{ quoted with that same character.                                            }
Function DbLibDoubled(S : String; C : String) : String;
Var
    I : Integer;
    Value, Ch : String;
Begin
    Value := '';
    For I := 1 To Length(S) Do
    Begin
        Ch := Copy(S, I, 1);
        Value := Value + Ch;
        If Ch = C Then Value := Value + C;
    End;
    Result := Value;
End;

{ True for a connection-string key whose value is a credential. }
Function DbLibIsSecretKey(Key : String) : Boolean;
Var
    K : String;
Begin
    K := LowerCase(Trim(Key));
    Result := (Pos('pwd', K) > 0) Or (Pos('password', K) > 0)
        Or (Pos('secret', K) > 0) Or (Pos('token', K) > 0);
End;

{ The connection string with every credential value replaced by ***. A value }
{ that itself holds Key=Value pairs, as an ODBC string inside Extended       }
{ Properties does, is redacted the same way, so a nested Pwd is caught too.  }
Function DbLibRedactConnStr(S : String) : String;
Var
    P : Integer;
    Key, Raw, Piece, Redacted, Inner, Opener, Closer : String;
    HasEq : Boolean;
Begin
    Redacted := '';
    P := 1;
    While DbLibConnNextPair(S, P, Key, Raw, HasEq) Do
    Begin
        If Not HasEq Then
            Piece := Key
        Else
        Begin
            If DbLibIsSecretKey(Key) Then
                Piece := Key + '=***'
            Else
            Begin
                Piece := Key + '=' + Raw;
                If Pos('=', Raw) > 0 Then
                Begin
                    Inner := DbLibRedactConnStr(DbLibUnquote(Raw));
                    Opener := Copy(Trim(Raw), 1, 1);
                    Closer := '';
                    If (Opener = '"') Or (Opener = '''') Then Closer := Opener;
                    If Opener = '{' Then Closer := '}';
                    If Closer <> '' Then
                        Piece := Key + '=' + Opener + DbLibDoubled(Inner, Closer) + Closer
                    Else
                        Piece := Key + '=' + Inner;
                End;
            End;
        End;
        If Redacted <> '' Then Redacted := Redacted + ';';
        Redacted := Redacted + Piece;
    End;
    Result := Redacted;
End;

{ True when the connection string carries a non-empty credential anywhere. }
Function DbLibConnHasSecret(S : String) : Boolean;
Var
    P : Integer;
    Key, Raw : String;
    HasEq, Found : Boolean;
Begin
    Found := False;
    P := 1;
    While (Not Found) And DbLibConnNextPair(S, P, Key, Raw, HasEq) Do
    Begin
        If HasEq Then
        Begin
            If DbLibIsSecretKey(Key) Then
                Found := Trim(DbLibUnquote(Raw)) <> ''
            Else
            Begin
                If Pos('=', Raw) > 0 Then Found := DbLibConnHasSecret(DbLibUnquote(Raw));
            End;
        End;
    End;
    Result := Found;
End;

{ The unquoted value of Key in a connection string, '' when absent. }
Function DbLibConnValue(S : String; Key : String) : String;
Var
    P : Integer;
    K, Raw, Value : String;
    HasEq, Found : Boolean;
Begin
    Value := '';
    Found := False;
    P := 1;
    While (Not Found) And DbLibConnNextPair(S, P, K, Raw, HasEq) Do
    Begin
        If HasEq And (LowerCase(K) = LowerCase(Key)) Then
        Begin
            Value := DbLibUnquote(Raw);
            Found := True;
        End;
    End;
    Result := Value;
End;

{ The connection string with Key set to NewRaw: the first occurrence is       }
{ replaced and any repeat dropped, or the pair appended when Key is absent.   }
{ Every other pair, credentials included, is carried through unchanged.       }
Function DbLibSetConnValue(S : String; Key : String; NewRaw : String) : String;
Var
    P : Integer;
    K, Raw, Rebuilt, Piece : String;
    HasEq, Replaced : Boolean;
Begin
    Rebuilt := '';
    Replaced := False;
    P := 1;
    While DbLibConnNextPair(S, P, K, Raw, HasEq) Do
    Begin
        If HasEq Then Piece := K + '=' + Raw Else Piece := K;
        If LowerCase(K) = LowerCase(Key) Then
        Begin
            If Replaced Then
                Piece := ''
            Else
            Begin
                Piece := K + '=' + NewRaw;
                Replaced := True;
            End;
        End;
        If Piece <> '' Then
        Begin
            If Rebuilt <> '' Then Rebuilt := Rebuilt + ';';
            Rebuilt := Rebuilt + Piece;
        End;
    End;
    If Not Replaced Then
    Begin
        If Rebuilt <> '' Then Rebuilt := Rebuilt + ';';
        Rebuilt := Rebuilt + Key + '=' + NewRaw;
    End;
    Result := Rebuilt;
End;

{ True when the provider is the Jet or ACE engine, which reads Access and     }
{ Excel files and is the one that honours Mode=Read.                          }
Function DbLibIsJetOrAce(S : String) : Boolean;
Var
    Prov : String;
Begin
    Prov := LowerCase(DbLibConnValue(S, 'Provider'));
    Result := (Pos('jet.oledb', Prov) > 0) Or (Pos('ace.oledb', Prov) > 0);
End;

{ True for a drive-letter, UNC or root path. }
Function DbLibIsAbsolutePath(Path : String) : Boolean;
Begin
    Result := (Copy(Path, 2, 1) = ':') Or (Copy(Path, 1, 1) = '\')
        Or (Copy(Path, 1, 1) = '/');
End;

{ The connection string this handler opens: for a Jet or ACE file, a Data     }
{ Source relative to the DbLib is made absolute against BaseDir and the       }
{ connection is set to Mode=Read. Other providers are left as written; they   }
{ are kept read-only by issuing nothing but SELECT.                          }
Function DbLibConnectionForRead(S : String; BaseDir : String) : String;
Var
    Conn, Src, Dir : String;
Begin
    Conn := S;
    If DbLibIsJetOrAce(Conn) Then
    Begin
        Src := Trim(DbLibConnValue(Conn, 'Data Source'));
        If (Src <> '') And (Not DbLibIsAbsolutePath(Src)) And (BaseDir <> '') Then
        Begin
            Dir := BaseDir;
            If (Copy(Dir, Length(Dir), 1) <> '\') And (Copy(Dir, Length(Dir), 1) <> '/') Then
                Dir := Dir + '\';
            Src := Dir + Src;
            If Pos(';', Src) > 0 Then Src := '"' + DbLibDoubled(Src, '"') + '"';
            Conn := DbLibSetConnValue(Conn, 'Data Source', Src);
        End;
        Conn := DbLibSetConnValue(Conn, 'Mode', 'Read');
    End;
    Result := Conn;
End;

{ The kind of database behind a connection string, for the reply. }
Function DbLibConnKind(S : String) : String;
Var
    Prov, Ext, Src, Kind : String;
Begin
    Kind := '';
    Prov := LowerCase(DbLibConnValue(S, 'Provider'));
    If DbLibIsJetOrAce(S) Then
    Begin
        Ext := LowerCase(DbLibConnValue(S, 'Extended Properties'));
        Src := LowerCase(Trim(DbLibConnValue(S, 'Data Source')));
        If (Pos('excel', Ext) > 0) Or (Pos('.xls', Src) > 0) Then
            Kind := 'excel'
        Else
            Kind := 'access';
    End;
    If (Kind = '') And ((Pos('sqloledb', Prov) > 0) Or (Pos('sqlncli', Prov) > 0)
        Or (Pos('msoledbsql', Prov) > 0)) Then
        Kind := 'sql_server';
    If (Kind = '') And ((Pos('msdasql', Prov) > 0) Or ((Prov = '')
        And ((DbLibConnValue(S, 'DSN') <> '') Or (DbLibConnValue(S, 'Driver') <> '')))) Then
        Kind := 'odbc';
    If (Kind = '') And ((Pos('oraoledb', Prov) > 0) Or (Pos('msdaora', Prov) > 0)) Then
        Kind := 'oracle';
    If Kind = '' Then Kind := 'other';
    Result := Kind;
End;

{ True for a letter, digit, underscore or dollar sign: the only characters    }
{ allowed in a name that has to go into a statement unquoted.                 }
Function DbLibIsPlainIdentChar(Ch : String) : Boolean;
Begin
    Result := ((Ch >= 'a') And (Ch <= 'z')) Or ((Ch >= 'A') And (Ch <= 'Z'))
        Or ((Ch >= '0') And (Ch <= '9')) Or (Ch = '_') Or (Ch = '$');
End;

{ A table or column name ready to go into a SELECT, or '' when it cannot go   }
{ in safely. The name is wrapped in the DbLib's quote characters. It is       }
{ refused outright if it holds a control character, either quote character,   }
{ a quote of any other kind, ';' or ':' (which ADO would read as a parameter).}
{ With no usable quote pair only letters, digits, '_' and '$' are allowed.    }
{ Callers pass only names the DbLib itself declares, or that ADO reported, so }
{ this is the second check, not the first.                                    }
Function DbLibQuoteIdent(Name : String; LeftQ : String; RightQ : String) : String;
Var
    I : Integer;
    Ch, Quoted : String;
    Plain, Ok : Boolean;
Begin
    Quoted := '';
    Ok := (Name <> '') And (Length(Name) <= 128) And (Trim(Name) = Name);
    Plain := (Length(LeftQ) <> 1) Or (Length(RightQ) <> 1);
    I := 1;
    While Ok And (I <= Length(Name)) Do
    Begin
        Ch := Copy(Name, I, 1);
        If (Ch < ' ') Or (Ch = ':') Or (Ch = ';') Or (Ch = '''') Or (Ch = '"')
            Or (Ch = '`') Then
            Ok := False;
        If (Not Plain) And ((Ch = LeftQ) Or (Ch = RightQ)) Then Ok := False;
        If Plain And (Not DbLibIsPlainIdentChar(Ch)) Then Ok := False;
        Inc(I);
    End;
    If Ok Then
    Begin
        If Plain Then
            Quoted := Name
        Else
            Quoted := LeftQ + Name + RightQ;
    End;
    Result := Quoted;
End;

{ A table name ready for a FROM clause, schema-qualified when the DbLib gives }
{ a schema. '' when either part is refused.                                   }
Function DbLibQualifiedTable(Schema : String; Table : String; LeftQ : String;
    RightQ : String) : String;
Var
    QT, QS, Qualified : String;
Begin
    Qualified := '';
    QT := DbLibQuoteIdent(Table, LeftQ, RightQ);
    If QT <> '' Then
    Begin
        If Trim(Schema) = '' Then
            Qualified := QT
        Else
        Begin
            QS := DbLibQuoteIdent(Trim(Schema), LeftQ, RightQ);
            If QS <> '' Then Qualified := QS + '.' + QT;
        End;
    End;
    Result := Qualified;
End;

{ The column a table maps to ParamName, else the column literally named      }
{ ColName when the table has one. The mapping wins because a DbLib can map    }
{ [Library Ref] from a column with any name.                                  }
Function DbLibResolveField(Lines : TStringList; Cols : TStringList; Table : String;
    ParamName : String; ColName : String) : String;
Var
    Field : String;
    I : Integer;
Begin
    Field := DbLibMappedField(Lines, Table, ParamName);
    If Field = '' Then
    Begin
        I := DbLibIndexOfName(Cols, ColName);
        If I >= 0 Then Field := Cols.Get(I);
    End;
    Result := Field;
End;

{ The column a table's rows are looked up by, and where that answer came     }
{ from: the table's Key setting, the column its where clause matches on, a    }
{ column named Part Number (Altium's default key), or the first column. A     }
{ name the columns hold is returned in the columns' own spelling.             }
Function DbLibKeyField(Lines : TStringList; Cols : TStringList; Table : String;
    LeftQ : String; RightQ : String; Var Source : String) : String;
Var
    KeyName, FromWhere, UserWhere : String;
    I : Integer;
Begin
    KeyName := '';
    Source := '';
    UserWhere := LowerCase(Trim(DbLibTableAttr(Lines, Table, 'UserWhere')));
    FromWhere := DbLibKeyFromWhere(DbLibTableAttr(Lines, Table, 'UserWhereText'), LeftQ, RightQ);
    If ((UserWhere = '1') Or (UserWhere = 'true')) And (FromWhere <> '') Then
    Begin
        KeyName := FromWhere;
        Source := 'where_clause';
    End;
    If KeyName = '' Then
    Begin
        KeyName := Trim(DbLibTableAttr(Lines, Table, 'Key'));
        If KeyName <> '' Then Source := 'key_setting';
    End;
    If (KeyName = '') And (FromWhere <> '') Then
    Begin
        KeyName := FromWhere;
        Source := 'where_clause';
    End;
    If (KeyName = '') And (DbLibIndexOfName(Cols, 'Part Number') >= 0) Then
    Begin
        KeyName := 'Part Number';
        Source := 'part_number_column';
    End;
    If (KeyName = '') And (Cols.Count > 0) Then
    Begin
        KeyName := Cols.Get(0);
        Source := 'first_column';
    End;
    I := DbLibIndexOfName(Cols, KeyName);
    If I >= 0 Then KeyName := Cols.Get(I);
    Result := KeyName;
End;

{ The footprint columns of a table, as two tab-separated lists in step:       }
{ [Footprint Ref] with [Footprint Path], then [Footprint Ref 2] with          }
{ [Footprint Path 2], up to eight. A library column may be empty.             }
Procedure DbLibFootprintFields(Lines : TStringList; Cols : TStringList; Table : String;
    Var RefFields : String; Var LibFields : String);
Var
    N : Integer;
    Suffix, RefF, LibF : String;
Begin
    RefFields := '';
    LibFields := '';
    For N := 1 To 8 Do
    Begin
        Suffix := '';
        If N > 1 Then Suffix := ' ' + IntToStr(N);
        RefF := DbLibResolveField(Lines, Cols, Table, '[Footprint Ref' + Suffix + ']',
            'Footprint Ref' + Suffix);
        LibF := DbLibResolveField(Lines, Cols, Table, '[Footprint Path' + Suffix + ']',
            'Footprint Path' + Suffix);
        If RefF <> '' Then
        Begin
            If RefFields <> '' Then
            Begin
                RefFields := RefFields + #9;
                LibFields := LibFields + #9;
            End;
            RefFields := RefFields + RefF;
            LibFields := LibFields + LibF;
        End;
    End;
End;

{ A tab-separated list as a JSON array of strings. }
Function DbLibTabsToJson(TabList : String) : String;
Var
    Rest, One, Json : String;
Begin
    Json := '';
    Rest := TabList;
    While Rest <> '' Do
    Begin
        One := SplitNextTab(Rest);
        If Json <> '' Then Json := Json + ',';
        Json := Json + '"' + EscapeJsonString(One) + '"';
    End;
    Result := '[' + Json + ']';
End;

{ Every setting of one table section, Options= lines aside, as a JSON object. }
{ Reported so the keys a real DbLib uses can be read off the reply.           }
Function DbLibTableSettingsJson(Lines : TStringList; Table : String) : String;
Var
    I, P : Integer;
    InTable : Boolean;
    Line, Section, SecTable, SecJson, Json, KeyName : String;
Begin
    Json := '';
    InTable := False;
    SecTable := '';
    SecJson := '';
    For I := 0 To Lines.Count Do
    Begin
        If I < Lines.Count Then
        Begin
            Line := Lines.Get(I);
            Section := DbLibSectionName(Line);
        End
        Else
        Begin
            Line := '';
            Section := 'end of file';
        End;
        If Section <> '' Then
        Begin
            If InTable And (Json = '') And (SecTable <> '')
                And (LowerCase(SecTable) = LowerCase(Trim(Table))) Then
                Json := SecJson;
            InTable := DbLibIsTableSection(Section);
            SecTable := '';
            SecJson := '';
        End
        Else
        Begin
            P := Pos('=', Line);
            If InTable And (P > 1) And (Not DbLibLineHasKey(Line, 'Options')) Then
            Begin
                KeyName := Trim(Copy(Line, 1, P - 1));
                If LowerCase(KeyName) = 'tablename' Then
                    SecTable := Trim(DbLibLineValue(Line));
                If SecJson <> '' Then SecJson := SecJson + ',';
                SecJson := SecJson + '"' + EscapeJsonString(KeyName) + '":"'
                    + EscapeJsonString(DbLibLineValue(Line)) + '"';
            End;
        End;
    End;
    Result := '{' + Json + '}';
End;

{ One table of a DbLib as a JSON object: its settings, the columns that carry }
{ the key, the symbol and the footprints, and (when HaveCols) its columns.    }
Function DbLibTableJson(Lines : TStringList; Cols : TStringList; Table : String;
    LeftQ : String; RightQ : String; HaveCols : Boolean; QueryErr : String) : String;
Var
    Enabled, Schema, KeyField, KeySource, SymField, SymLibField, DescField : String;
    RefFields, LibFields, FpJson, ColsJson, Json, ErrJson, OneRef, OneLib : String;
    I : Integer;
Begin
    Enabled := LowerCase(Trim(DbLibTableAttr(Lines, Table, 'Enabled')));
    Schema := Trim(DbLibTableAttr(Lines, Table, 'SchemaName'));
    KeySource := '';
    KeyField := DbLibKeyField(Lines, Cols, Table, LeftQ, RightQ, KeySource);
    SymField := DbLibResolveField(Lines, Cols, Table, '[Library Ref]', 'Library Ref');
    SymLibField := DbLibResolveField(Lines, Cols, Table, '[Library Path]', 'Library Path');
    DescField := DbLibResolveField(Lines, Cols, Table, '[Description]', 'Description');
    DbLibFootprintFields(Lines, Cols, Table, RefFields, LibFields);
    FpJson := '';
    While RefFields <> '' Do
    Begin
        OneRef := SplitNextTab(RefFields);
        OneLib := SplitNextTab(LibFields);
        If FpJson <> '' Then FpJson := FpJson + ',';
        FpJson := FpJson + '{"ref_field":"' + EscapeJsonString(OneRef)
            + '","library_field":"' + EscapeJsonString(OneLib) + '"}';
    End;
    ColsJson := 'null';
    If HaveCols Then
    Begin
        ColsJson := '';
        For I := 0 To Cols.Count - 1 Do
        Begin
            If ColsJson <> '' Then ColsJson := ColsJson + ',';
            ColsJson := ColsJson + '"' + EscapeJsonString(Cols.Get(I)) + '"';
        End;
        ColsJson := '[' + ColsJson + ']';
    End;
    ErrJson := 'null';
    If QueryErr <> '' Then ErrJson := '"' + EscapeJsonString(QueryErr) + '"';
    Json := '{"name":"' + EscapeJsonString(Table) + '"'
        + ',"enabled":' + BoolToJsonStr((Enabled = '') Or (Enabled = 'true') Or (Enabled = '1'))
        + ',"schema":"' + EscapeJsonString(Schema) + '"'
        + ',"key_field":"' + EscapeJsonString(KeyField) + '"'
        + ',"key_field_source":"' + EscapeJsonString(KeySource) + '"'
        + ',"key_field_found":' + BoolToJsonStr(HaveCols And (DbLibIndexOfName(Cols, KeyField) >= 0))
        + ',"symbol_ref_field":"' + EscapeJsonString(SymField) + '"'
        + ',"symbol_library_field":"' + EscapeJsonString(SymLibField) + '"'
        + ',"description_field":"' + EscapeJsonString(DescField) + '"'
        + ',"footprint_fields":[' + FpJson + ']'
        + ',"fields":' + ColsJson
        + ',"field_count":' + IntToStr(Cols.Count)
        + ',"error":' + ErrJson
        + ',"settings":' + DbLibTableSettingsJson(Lines, Table) + '}';
    Result := Json;
End;

{ Lib_GetDbLibInfo - what a DbLib declares and how it connects.               }
{                                                                              }
{ Params: library_path (the .DbLib), with_fields ("false" reads the file only  }
{ and opens no database connection).                                           }
{ Response: connection (provider, kind, data_source, has_password, redacted,   }
{ read_only), quote characters, search_path, tables (each with its key,        }
{ symbol and footprint columns, its settings and, when fields were read, every }
{ column), and connected, which is null when no connection was attempted.      }
Function Lib_GetDbLibInfo(Params : String; RequestId : String) : String;
Var
    LibPath, ConnStr, ReadConn, LeftQ, RightQ, TableList, Rest, Table : String;
    QTable, Problem, TablesJson, ConnJson, Response, QueryErr, ConnectedJson : String;
    Lines, Cols : TStringList;
    Conn : TADOConnection;
    Q : TADOQuery;
    I, TableCount : Integer;
    Loaded, WantFields, Connected, Opened, Tried : Boolean;
Begin
    LibPath := Trim(ExtractJsonValue(Params, 'library_path'));
    WantFields := ExtractJsonValue(Params, 'with_fields') <> 'false';
    Problem := DbLibPathProblem(LibPath);
    If Problem <> '' Then
    Begin
        Response := BuildErrorResponse(RequestId, 'BAD_LIBRARY_PATH', Problem);
        Result := Response;
        Exit;
    End;
    If Not FileExists(LibPath) Then
    Begin
        Response := BuildErrorResponse(RequestId, 'NOT_FOUND', 'No DbLib file at ' + LibPath);
        Result := Response;
        Exit;
    End;

    Response := '';
    Lines := TStringList.Create;
    Try
        Loaded := LoadTextFile(Lines, LibPath);

        If Not Loaded Then
            Response := BuildErrorResponse(RequestId, 'READ_FAILED', 'Could not read ' + LibPath)
        Else
        Begin
            ConnStr := DbLibGlobalValue(Lines, 'ConnectionString');
            LeftQ := DbLibGlobalValue(Lines, 'LeftQuote');
            RightQ := DbLibGlobalValue(Lines, 'RightQuote');
            If LeftQ = '' Then LeftQ := '[';
            If RightQ = '' Then RightQ := ']';
            TableList := DbLibTableNames(Lines);

            Tried := WantFields And (Trim(ConnStr) <> '') And (TableList <> '');
            Connected := False;
            Conn := Nil;
            If Tried Then
            Begin
                ReadConn := DbLibConnectionForRead(ConnStr, ExtractFilePath(LibPath));
                Try
                    Conn := TADOConnection.Create(Nil);
                    Conn.ConnectionString := ReadConn;
                    Conn.LoginPrompt := False;
                    Conn.Connected := True;
                    Connected := True;
                Except
                    Connected := False;
                End;
            End;

            TablesJson := '';
            TableCount := 0;
            Rest := TableList;
            While Rest <> '' Do
            Begin
                Table := SplitNextTab(Rest);
                If Table = '' Then Continue;
                QueryErr := '';
                Cols := TStringList.Create;
                If Connected Then
                Begin
                    QTable := DbLibQualifiedTable(DbLibTableAttr(Lines, Table, 'SchemaName'),
                        Table, LeftQ, RightQ);
                    If QTable = '' Then
                        QueryErr := 'TABLE_NAME_REFUSED'
                    Else
                    Begin
                        Opened := False;
                        Q := TADOQuery.Create(Nil);
                        Try
                            Q.Connection := Conn;
                            Q.SQL.Add('SELECT * FROM ' + QTable + ' WHERE 1=0');
                            Q.Open;
                            Opened := True;
                        Except
                            Opened := False;
                        End;
                        If Opened Then
                        Begin
                            For I := 0 To Q.FieldCount - 1 Do
                                Cols.Add(Q.Fields[I].DisplayName);
                            Try Q.Close; Except End;
                        End
                        Else
                            QueryErr := 'QUERY_FAILED';
                        Q.Free;
                    End;
                End;
                If TablesJson <> '' Then TablesJson := TablesJson + ',';
                TablesJson := TablesJson + DbLibTableJson(Lines, Cols, Table, LeftQ, RightQ,
                    Connected And (QueryErr = ''), QueryErr);
                Cols.Free;
                Inc(TableCount);
            End;
            If Conn <> Nil Then Conn.Free;

            ConnectedJson := 'null';
            If Tried Then ConnectedJson := BoolToJsonStr(Connected);
            ConnJson := '{"provider":"' + EscapeJsonString(DbLibConnValue(ConnStr, 'Provider')) + '"'
                + ',"kind":"' + DbLibConnKind(ConnStr) + '"'
                + ',"data_source":"' + EscapeJsonString(DbLibConnValue(ConnStr, 'Data Source')) + '"'
                + ',"has_password":' + BoolToJsonStr(DbLibConnHasSecret(ConnStr))
                + ',"redacted":"' + EscapeJsonString(DbLibRedactConnStr(ConnStr)) + '"'
                + ',"read_only":' + BoolToJsonStr(DbLibIsJetOrAce(ConnStr)) + '}';

            Response := BuildSuccessResponse(RequestId,
                '{"library_path":"' + EscapeJsonString(LibPath) + '"'
                + ',"connection":' + ConnJson
                + ',"left_quote":"' + EscapeJsonString(LeftQ) + '"'
                + ',"right_quote":"' + EscapeJsonString(RightQ) + '"'
                + ',"search_path":"' + EscapeJsonString(DbLibGlobalValue(Lines, 'LibrarySearchPath')) + '"'
                + ',"table_count":' + IntToStr(TableCount)
                + ',"tables":[' + TablesJson + ']'
                + ',"sections":' + DbLibTabsToJson(DbLibSectionNames(Lines))
                + ',"fields_included":' + BoolToJsonStr(Connected)
                + ',"connected":' + ConnectedJson + '}');
        End;
    Finally
        Lines.Free;
    End;
    Result := Response;
End;

{ DbLibSearchOne - case-insensitive substring search over one DbLib's rows.   }
{                                                                              }
{ Each searched table is read with SELECT * FROM <table>, the table name being }
{ one the DbLib declares, and each row is matched HERE against the lowered     }
{ query. Nothing the caller typed reaches the database. Stops at Limit hits   }
{ in total or MaxRows rows read in total, whichever comes first.              }
{ Appends hits to ResultsJson and one summary object to LibsJson.             }
Function DbLibSearchOne(LibPath : String; LowerQuery : String; TableFilter : String;
    FieldsFilter : String; Limit : Integer; MaxRows : Integer;
    Var HitCount : Integer; Var Scanned : Integer; Var Capped : Boolean;
    Var ResultsJson : String; Var LibsJson : String) : Boolean;
Var
    ConnStr, ReadConn, LeftQ, RightQ, TableList, Rest, Table, QTable, Enabled : String;
    ErrCode, SearchedJson, FailedJson, UnknownJson, FieldRest, OneField : String;
    KeyField, KeySource, SymField, SymLibField, DescField, RefFields, LibFields : String;
    V, Matched, MatchedVal, KeyV, SymV, SymLibV, DescV, FpJson, RefRest, LibRest : String;
    OneRef, OneLib, RefV, LibV, ErrJson : String;
    Lines, Cols, SearchIdx : TStringList;
    Conn : TADOConnection;
    Q : TADOQuery;
    I, J, Idx, KeyIdx, SymIdx, SymLibIdx, DescIdx, RefIdx, LibIdx : Integer;
    Loaded, Connected, Opened, Stopped : Boolean;
Begin
    Result := False;
    ErrCode := '';
    SearchedJson := '';
    FailedJson := '';
    UnknownJson := '';
    Lines := TStringList.Create;
    Try
        Loaded := LoadTextFile(Lines, LibPath);
        If Not Loaded Then ErrCode := 'READ_FAILED';

        If ErrCode = '' Then
        Begin
            ConnStr := DbLibGlobalValue(Lines, 'ConnectionString');
            LeftQ := DbLibGlobalValue(Lines, 'LeftQuote');
            RightQ := DbLibGlobalValue(Lines, 'RightQuote');
            If LeftQ = '' Then LeftQ := '[';
            If RightQ = '' Then RightQ := ']';
            TableList := DbLibTableNames(Lines);
            If TableFilter <> '' Then
            Begin
                Table := DbLibFindTab(TableList, TableFilter);
                If Table = '' Then ErrCode := 'TABLE_UNKNOWN';
                TableList := Table;
            End;
            If (ErrCode = '') And (TableList = '') Then ErrCode := 'NO_TABLES';
            If (ErrCode = '') And (Trim(ConnStr) = '') Then ErrCode := 'NO_CONNECTION_STRING';
        End;

        Connected := False;
        Conn := Nil;
        If ErrCode = '' Then
        Begin
            ReadConn := DbLibConnectionForRead(ConnStr, ExtractFilePath(LibPath));
            Try
                Conn := TADOConnection.Create(Nil);
                Conn.ConnectionString := ReadConn;
                Conn.LoginPrompt := False;
                Conn.Connected := True;
                Connected := True;
            Except
                Connected := False;
            End;
            If Not Connected Then ErrCode := 'CONNECT_FAILED';
        End;

        Rest := '';
        If ErrCode = '' Then Rest := TableList;
        While Rest <> '' Do
        Begin
            Table := SplitNextTab(Rest);
            If Table = '' Then Continue;
            If (HitCount >= Limit) Or (Scanned >= MaxRows) Then Break;
            { A disabled table is skipped, as Altium skips it, unless it was }
            { asked for by name.                                             }
            Enabled := LowerCase(Trim(DbLibTableAttr(Lines, Table, 'Enabled')));
            If (TableFilter = '') And (Enabled <> '') And (Enabled <> 'true')
                And (Enabled <> '1') Then
                Continue;
            QTable := DbLibQualifiedTable(DbLibTableAttr(Lines, Table, 'SchemaName'),
                Table, LeftQ, RightQ);
            If QTable = '' Then
            Begin
                If FailedJson <> '' Then FailedJson := FailedJson + ',';
                FailedJson := FailedJson + '{"table":"' + EscapeJsonString(Table)
                    + '","reason":"TABLE_NAME_REFUSED"}';
                Continue;
            End;

            Opened := False;
            Q := TADOQuery.Create(Nil);
            Try
                Q.Connection := Conn;
                Q.SQL.Add('SELECT * FROM ' + QTable);
                Q.Open;
                Opened := True;
            Except
                Opened := False;
            End;
            If Not Opened Then
            Begin
                Q.Free;
                If FailedJson <> '' Then FailedJson := FailedJson + ',';
                FailedJson := FailedJson + '{"table":"' + EscapeJsonString(Table)
                    + '","reason":"QUERY_FAILED"}';
                Continue;
            End;

            Cols := TStringList.Create;
            SearchIdx := TStringList.Create;
            Try
                For I := 0 To Q.FieldCount - 1 Do
                    Cols.Add(Q.Fields[I].DisplayName);

                { Which columns to match: the ones asked for that this table }
                { has, else every column. A name the table lacks is reported }
                { rather than silently searched as nothing.                  }
                If FieldsFilter <> '' Then
                Begin
                    FieldRest := FieldsFilter;
                    While FieldRest <> '' Do
                    Begin
                        I := Pos('|', FieldRest);
                        If I > 0 Then
                        Begin
                            OneField := Trim(Copy(FieldRest, 1, I - 1));
                            FieldRest := Copy(FieldRest, I + 1, Length(FieldRest));
                        End
                        Else
                        Begin
                            OneField := Trim(FieldRest);
                            FieldRest := '';
                        End;
                        If OneField = '' Then Continue;
                        Idx := DbLibIndexOfName(Cols, OneField);
                        If Idx >= 0 Then
                            SearchIdx.Add(IntToStr(Idx))
                        Else
                        Begin
                            If UnknownJson <> '' Then UnknownJson := UnknownJson + ',';
                            UnknownJson := UnknownJson + '{"table":"' + EscapeJsonString(Table)
                                + '","field":"' + EscapeJsonString(OneField) + '"}';
                        End;
                    End;
                End
                Else
                Begin
                    For I := 0 To Cols.Count - 1 Do
                        SearchIdx.Add(IntToStr(I));
                End;

                KeySource := '';
                KeyField := DbLibKeyField(Lines, Cols, Table, LeftQ, RightQ, KeySource);
                SymField := DbLibResolveField(Lines, Cols, Table, '[Library Ref]', 'Library Ref');
                SymLibField := DbLibResolveField(Lines, Cols, Table, '[Library Path]', 'Library Path');
                DescField := DbLibResolveField(Lines, Cols, Table, '[Description]', 'Description');
                DbLibFootprintFields(Lines, Cols, Table, RefFields, LibFields);
                KeyIdx := DbLibIndexOfName(Cols, KeyField);
                SymIdx := DbLibIndexOfName(Cols, SymField);
                SymLibIdx := DbLibIndexOfName(Cols, SymLibField);
                DescIdx := DbLibIndexOfName(Cols, DescField);

                If SearchedJson <> '' Then SearchedJson := SearchedJson + ',';
                SearchedJson := SearchedJson + '"' + EscapeJsonString(Table) + '"';

                Stopped := False;
                Try Q.First; Except End;
                While (Not Stopped) And (Not Q.Eof) And (HitCount < Limit) And (Scanned < MaxRows) Do
                Begin
                    Scanned := Scanned + 1;
                    Matched := '';
                    MatchedVal := '';
                    For J := 0 To SearchIdx.Count - 1 Do
                    Begin
                        Idx := StrToIntDef(SearchIdx[J], -1);
                        If Idx < 0 Then Continue;
                        V := '';
                        Try V := Q.Fields[Idx].AsString; Except V := ''; End;
                        If Pos(LowerQuery, LowerCase(V)) > 0 Then
                        Begin
                            Matched := Cols[Idx];
                            MatchedVal := V;
                            Break;
                        End;
                    End;

                    If Matched <> '' Then
                    Begin
                        KeyV := '';
                        SymV := '';
                        SymLibV := '';
                        DescV := '';
                        If KeyIdx >= 0 Then
                        Begin
                            Try KeyV := Q.Fields[KeyIdx].AsString; Except End;
                        End;
                        If SymIdx >= 0 Then
                        Begin
                            Try SymV := Q.Fields[SymIdx].AsString; Except End;
                        End;
                        If SymLibIdx >= 0 Then
                        Begin
                            Try SymLibV := Q.Fields[SymLibIdx].AsString; Except End;
                        End;
                        If DescIdx >= 0 Then
                        Begin
                            Try DescV := Q.Fields[DescIdx].AsString; Except End;
                        End;
                        FpJson := '';
                        RefRest := RefFields;
                        LibRest := LibFields;
                        While RefRest <> '' Do
                        Begin
                            OneRef := SplitNextTab(RefRest);
                            OneLib := SplitNextTab(LibRest);
                            RefV := '';
                            LibV := '';
                            RefIdx := DbLibIndexOfName(Cols, OneRef);
                            LibIdx := DbLibIndexOfName(Cols, OneLib);
                            If RefIdx >= 0 Then
                            Begin
                                Try RefV := Q.Fields[RefIdx].AsString; Except End;
                            End;
                            If LibIdx >= 0 Then
                            Begin
                                Try LibV := Q.Fields[LibIdx].AsString; Except End;
                            End;
                            If RefV <> '' Then
                            Begin
                                If FpJson <> '' Then FpJson := FpJson + ',';
                                FpJson := FpJson + '{"ref":"' + EscapeJsonString(RefV)
                                    + '","library":"' + EscapeJsonString(LibV) + '"}';
                            End;
                        End;

                        If ResultsJson <> '' Then ResultsJson := ResultsJson + ',';
                        ResultsJson := ResultsJson
                            + '{"library_path":"' + EscapeJsonString(LibPath) + '"'
                            + ',"table":"' + EscapeJsonString(Table) + '"'
                            + ',"key":"' + EscapeJsonString(KeyV) + '"'
                            + ',"key_field":"' + EscapeJsonString(KeyField) + '"'
                            + ',"symbol_ref":"' + EscapeJsonString(SymV) + '"'
                            + ',"symbol_library":"' + EscapeJsonString(SymLibV) + '"'
                            + ',"footprints":[' + FpJson + ']'
                            + ',"description":"' + EscapeJsonString(DescV) + '"'
                            + ',"matched_field":"' + EscapeJsonString(Matched) + '"'
                            + ',"matched_value":"' + EscapeJsonString(Copy(MatchedVal, 1, 200)) + '"}';
                        HitCount := HitCount + 1;
                    End;
                    Try Q.Next; Except Stopped := True; End;
                End;
                If (Scanned >= MaxRows) And (Not Q.Eof) Then Capped := True;
                Try Q.Close; Except End;
            Finally
                Cols.Free;
                SearchIdx.Free;
                Q.Free;
            End;
        End;
        If Conn <> Nil Then Conn.Free;
    Finally
        Lines.Free;
    End;

    ErrJson := 'null';
    If ErrCode <> '' Then ErrJson := '"' + ErrCode + '"';
    If LibsJson <> '' Then LibsJson := LibsJson + ',';
    LibsJson := LibsJson + '{"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"searched":' + BoolToJsonStr(ErrCode = '')
        + ',"error":' + ErrJson
        + ',"tables_searched":[' + SearchedJson + ']'
        + ',"tables_failed":[' + FailedJson + ']'
        + ',"unknown_fields":[' + UnknownJson + ']}';
    Result := ErrCode = '';
End;

{ Lib_QueryDbLib - search database libraries for components.                  }
{                                                                              }
{ Params: query (required, case-insensitive substring), library_path (one     }
{ .DbLib; omitted searches every installed database library), table (one     }
{ declared table), fields ("|"-separated column names; omitted matches every  }
{ column), limit (hits, default 50), max_rows (rows read in total, default    }
{ 20000).                                                                      }
{ Response: query, count, limit, truncated, rows_scanned, scan_capped,        }
{ libraries (one summary per DbLib, with its error code if it could not be    }
{ searched) and results (library_path, table, key, key_field, symbol_ref,     }
{ symbol_library, footprints, description, matched_field, matched_value).     }
Function Lib_QueryDbLib(Params : String; RequestId : String) : String;
Var
    LibPath, Query, TableFilter, FieldsFilter, Paths, Rest, OnePath, Problem : String;
    ResultsJson, LibsJson, Response, InstPath : String;
    Limit, MaxRows, HitCount, Scanned, I, InstCount, TypeOrd : Integer;
    Capped : Boolean;
Begin
    Query := ExtractJsonValue(Params, 'query');
    LibPath := Trim(ExtractJsonValue(Params, 'library_path'));
    TableFilter := Trim(ExtractJsonValue(Params, 'table'));
    FieldsFilter := ExtractJsonValue(Params, 'fields');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 50);
    MaxRows := StrToIntDef(ExtractJsonValue(Params, 'max_rows'), 20000);
    If Limit < 1 Then Limit := 1;
    If Limit > 1000 Then Limit := 1000;
    If MaxRows < 1 Then MaxRows := 1;
    If MaxRows > 1000000 Then MaxRows := 1000000;

    If Trim(Query) = '' Then
    Begin
        Response := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'query is required');
        Result := Response;
        Exit;
    End;

    Paths := '';
    If LibPath <> '' Then
    Begin
        Problem := DbLibPathProblem(LibPath);
        If Problem <> '' Then
        Begin
            Response := BuildErrorResponse(RequestId, 'BAD_LIBRARY_PATH', Problem);
            Result := Response;
            Exit;
        End;
        If Not FileExists(LibPath) Then
        Begin
            Response := BuildErrorResponse(RequestId, 'NOT_FOUND', 'No DbLib file at ' + LibPath);
            Result := Response;
            Exit;
        End;
        Paths := LibPath;
    End
    Else
    Begin
        If IntegratedLibraryManager = Nil Then
        Begin
            Response := BuildErrorResponse(RequestId, 'NO_MANAGER',
                'IntegratedLibraryManager unavailable');
            Result := Response;
            Exit;
        End;
        { Every installed database library. The type is matched by its      }
        { TLibraryType ordinal (3), not an enum name, as get_installed_      }
        { libraries does; an installed path missing from the available list  }
        { (ordinal -1) still counts when it is a .DbLib file.                }
        InstCount := 0;
        Try InstCount := IntegratedLibraryManager.InstalledLibraryCount; Except End;
        For I := 0 To InstCount - 1 Do
        Begin
            InstPath := '';
            Try InstPath := IntegratedLibraryManager.InstalledLibraryPath(I); Except End;
            If InstPath = '' Then Continue;
            TypeOrd := InstalledLibTypeOrdinal(InstPath);
            If (TypeOrd = 3) Or ((TypeOrd = -1) And DbLibHasDbLibExt(InstPath)) Then
            Begin
                If Paths <> '' Then Paths := Paths + #9;
                Paths := Paths + InstPath;
            End;
        End;
    End;

    HitCount := 0;
    Scanned := 0;
    Capped := False;
    ResultsJson := '';
    LibsJson := '';
    Rest := Paths;
    While Rest <> '' Do
    Begin
        OnePath := SplitNextTab(Rest);
        If OnePath = '' Then Continue;
        If (HitCount >= Limit) Or (Scanned >= MaxRows) Then
        Begin
            If LibsJson <> '' Then LibsJson := LibsJson + ',';
            LibsJson := LibsJson + '{"library_path":"' + EscapeJsonString(OnePath) + '"'
                + ',"searched":false,"error":"LIMIT_REACHED","tables_searched":[]'
                + ',"tables_failed":[],"unknown_fields":[]}';
            Continue;
        End;
        DbLibSearchOne(OnePath, LowerCase(Query), TableFilter, FieldsFilter, Limit,
            MaxRows, HitCount, Scanned, Capped, ResultsJson, LibsJson);
    End;

    Response := BuildSuccessResponse(RequestId,
        '{"query":"' + EscapeJsonString(Query) + '"'
        + ',"count":' + IntToStr(HitCount)
        + ',"limit":' + IntToStr(Limit)
        + ',"truncated":' + BoolToJsonStr(HitCount >= Limit)
        + ',"rows_scanned":' + IntToStr(Scanned)
        + ',"scan_capped":' + BoolToJsonStr(Capped)
        + ',"libraries":[' + LibsJson + ']'
        + ',"results":[' + ResultsJson + ']}');
    Result := Response;
End;

{ Lib_GetDbLibRecord - every column of one row of a DbLib table.              }
{                                                                              }
{ The table must be one the DbLib declares and the key column one the table   }
{ has. The key VALUE is bound as an ADO parameter. If the parameterised query }
{ cannot run (a key value the column's type will not accept, or a provider    }
{ without named parameters) the table is read and compared here instead, and  }
{ the reply says which lookup answered.                                       }
{ Params: library_path, table, key (all required), key_field (optional        }
{ override, checked against the table's columns), max_rows (scan bound).     }
Function Lib_GetDbLibRecord(Params : String; RequestId : String) : String;
Var
    LibPath, Table, KeyValue, KeyOverride, Problem, ConnStr, ReadConn, LeftQ, RightQ : String;
    TableList, Declared, QTable, QKey, KeyField, KeySource, Lookup, ErrCode, ErrMsg : String;
    FieldsJson, V, WantKey, SymField, SymLibField, DescField, RefFields, LibFields : String;
    FpJson, OneRef, OneLib, RefV, LibV, SymV, SymLibV, DescV, Response, Probe : String;
    Lines, Cols, Vals : TStringList;
    Conn : TADOConnection;
    Q : TADOQuery;
    I, KeyIdx, MatchCount, MaxRows, Scanned, Idx : Integer;
    Loaded, Connected, Opened, ParamWorked, Capped, Stopped : Boolean;
Begin
    LibPath := Trim(ExtractJsonValue(Params, 'library_path'));
    Table := Trim(ExtractJsonValue(Params, 'table'));
    KeyValue := ExtractJsonValue(Params, 'key');
    KeyOverride := Trim(ExtractJsonValue(Params, 'key_field'));
    MaxRows := StrToIntDef(ExtractJsonValue(Params, 'max_rows'), 100000);
    If MaxRows < 1 Then MaxRows := 1;

    Problem := DbLibPathProblem(LibPath);
    If Problem = '' Then
    Begin
        If Table = '' Then Problem := 'table is required';
    End;
    If Problem = '' Then
    Begin
        If Trim(KeyValue) = '' Then Problem := 'key is required';
    End;
    If Problem <> '' Then
    Begin
        Response := BuildErrorResponse(RequestId, 'MISSING_PARAM', Problem);
        Result := Response;
        Exit;
    End;
    If Not FileExists(LibPath) Then
    Begin
        Response := BuildErrorResponse(RequestId, 'NOT_FOUND', 'No DbLib file at ' + LibPath);
        Result := Response;
        Exit;
    End;

    ErrCode := '';
    ErrMsg := '';
    Lookup := '';
    MatchCount := 0;
    Scanned := 0;
    Capped := False;
    FieldsJson := '';
    KeyField := '';
    KeySource := '';
    SymV := '';
    SymLibV := '';
    DescV := '';
    FpJson := '';
    Declared := '';
    Conn := Nil;
    Lines := TStringList.Create;
    Cols := TStringList.Create;
    Vals := TStringList.Create;
    Try
        Loaded := LoadTextFile(Lines, LibPath);
        If Not Loaded Then
        Begin
            ErrCode := 'READ_FAILED';
            ErrMsg := 'Could not read ' + LibPath;
        End;

        If ErrCode = '' Then
        Begin
            ConnStr := DbLibGlobalValue(Lines, 'ConnectionString');
            LeftQ := DbLibGlobalValue(Lines, 'LeftQuote');
            RightQ := DbLibGlobalValue(Lines, 'RightQuote');
            If LeftQ = '' Then LeftQ := '[';
            If RightQ = '' Then RightQ := ']';
            TableList := DbLibTableNames(Lines);
            Declared := DbLibFindTab(TableList, Table);
            If Declared = '' Then
            Begin
                ErrCode := 'TABLE_UNKNOWN';
                ErrMsg := 'The DbLib declares no table named "' + Table + '". It declares: '
                    + StringReplace(TableList, #9, ', ', MkSet(rfReplaceAll));
            End;
        End;

        If ErrCode = '' Then
        Begin
            QTable := DbLibQualifiedTable(DbLibTableAttr(Lines, Declared, 'SchemaName'),
                Declared, LeftQ, RightQ);
            If QTable = '' Then
            Begin
                ErrCode := 'TABLE_NAME_REFUSED';
                ErrMsg := 'The table name holds a character that cannot be quoted safely';
            End;
        End;

        Connected := False;
        If ErrCode = '' Then
        Begin
            ReadConn := DbLibConnectionForRead(ConnStr, ExtractFilePath(LibPath));
            Try
                Conn := TADOConnection.Create(Nil);
                Conn.ConnectionString := ReadConn;
                Conn.LoginPrompt := False;
                Conn.Connected := True;
                Connected := True;
            Except
                Connected := False;
            End;
            If Not Connected Then
            Begin
                ErrCode := 'CONNECT_FAILED';
                ErrMsg := 'Could not open the database this DbLib names. Check it with '
                    + 'lib_dblib_info, which reports the provider and data source.';
            End;
        End;

        { The table's columns, to check the key column against. }
        If ErrCode = '' Then
        Begin
            Opened := False;
            Q := TADOQuery.Create(Nil);
            Try
                Q.Connection := Conn;
                Q.SQL.Add('SELECT * FROM ' + QTable + ' WHERE 1=0');
                Q.Open;
                Opened := True;
            Except
                Opened := False;
            End;
            If Opened Then
            Begin
                For I := 0 To Q.FieldCount - 1 Do
                    Cols.Add(Q.Fields[I].DisplayName);
                Try Q.Close; Except End;
            End;
            Q.Free;
            If Not Opened Then
            Begin
                ErrCode := 'QUERY_FAILED';
                ErrMsg := 'The table could not be read';
            End;
        End;

        If ErrCode = '' Then
        Begin
            If KeyOverride <> '' Then
            Begin
                KeyField := KeyOverride;
                KeySource := 'caller';
            End
            Else
                KeyField := DbLibKeyField(Lines, Cols, Declared, LeftQ, RightQ, KeySource);
            KeyIdx := DbLibIndexOfName(Cols, KeyField);
            If KeyIdx < 0 Then
            Begin
                ErrCode := 'KEY_FIELD_UNKNOWN';
                ErrMsg := 'The table has no column "' + KeyField + '" (key from '
                    + KeySource + '). Pass key_field with one of its columns; '
                    + 'lib_dblib_info lists them.';
            End
            Else
                KeyField := Cols.Get(KeyIdx);
        End;

        If ErrCode = '' Then
        Begin
            QKey := DbLibQuoteIdent(KeyField, LeftQ, RightQ);
            If QKey = '' Then
            Begin
                ErrCode := 'KEY_FIELD_REFUSED';
                ErrMsg := 'The key column name holds a character that cannot be quoted safely';
            End;
        End;

        If ErrCode = '' Then
        Begin
            WantKey := LowerCase(Trim(KeyValue));
            { First choice: the key bound as a parameter, so the database }
            { does the lookup and the value never becomes SQL text.       }
            ParamWorked := False;
            Q := TADOQuery.Create(Nil);
            Try
                Q.Connection := Conn;
                Q.SQL.Add('SELECT * FROM ' + QTable + ' WHERE ' + QKey + ' = :dblibkey');
                Q.Parameters.ParamByName('dblibkey').Value := KeyValue;
                Q.Open;
                ParamWorked := True;
            Except
                ParamWorked := False;
            End;
            If ParamWorked Then
            Begin
                Lookup := 'parameter';
                Stopped := False;
                Try Q.First; Except End;
                While (Not Stopped) And (Not Q.Eof) Do
                Begin
                    Probe := '';
                    Try Probe := Q.Fields[KeyIdx].AsString; Except End;
                    If LowerCase(Trim(Probe)) = WantKey Then
                    Begin
                        MatchCount := MatchCount + 1;
                        If MatchCount = 1 Then
                        Begin
                            For I := 0 To Q.FieldCount - 1 Do
                            Begin
                                V := '';
                                Try V := Q.Fields[I].AsString; Except End;
                                Vals.Add(V);
                            End;
                        End;
                    End;
                    Try Q.Next; Except Stopped := True; End;
                End;
                Try Q.Close; Except End;
            End;
            Q.Free;

            { Fallback: read the table and compare here. Still no caller }
            { text in the statement, only the declared table name.       }
            If Not ParamWorked Then
            Begin
                Lookup := 'scan';
                Opened := False;
                Q := TADOQuery.Create(Nil);
                Try
                    Q.Connection := Conn;
                    Q.SQL.Add('SELECT * FROM ' + QTable);
                    Q.Open;
                    Opened := True;
                Except
                    Opened := False;
                End;
                If Opened Then
                Begin
                    Stopped := False;
                    Try Q.First; Except End;
                    While (Not Stopped) And (Not Q.Eof) And (Scanned < MaxRows) Do
                    Begin
                        Scanned := Scanned + 1;
                        Probe := '';
                        Try Probe := Q.Fields[KeyIdx].AsString; Except End;
                        If LowerCase(Trim(Probe)) = WantKey Then
                        Begin
                            MatchCount := MatchCount + 1;
                            If MatchCount = 1 Then
                            Begin
                                For I := 0 To Q.FieldCount - 1 Do
                                Begin
                                    V := '';
                                    Try V := Q.Fields[I].AsString; Except End;
                                    Vals.Add(V);
                                End;
                            End;
                        End;
                        Try Q.Next; Except Stopped := True; End;
                    End;
                    If (Scanned >= MaxRows) And (Not Q.Eof) Then Capped := True;
                    Try Q.Close; Except End;
                End
                Else
                Begin
                    ErrCode := 'QUERY_FAILED';
                    ErrMsg := 'The table could not be read';
                End;
                Q.Free;
            End;
        End;

        If (ErrCode = '') And (MatchCount > 0) Then
        Begin
            For I := 0 To Cols.Count - 1 Do
            Begin
                V := '';
                If I < Vals.Count Then V := Vals[I];
                If FieldsJson <> '' Then FieldsJson := FieldsJson + ',';
                FieldsJson := FieldsJson + '"' + EscapeJsonString(Cols[I]) + '":"'
                    + EscapeJsonString(V) + '"';
            End;
            SymField := DbLibResolveField(Lines, Cols, Declared, '[Library Ref]', 'Library Ref');
            SymLibField := DbLibResolveField(Lines, Cols, Declared, '[Library Path]', 'Library Path');
            DescField := DbLibResolveField(Lines, Cols, Declared, '[Description]', 'Description');
            Idx := DbLibIndexOfName(Cols, SymField);
            If (Idx >= 0) And (Idx < Vals.Count) Then SymV := Vals[Idx];
            Idx := DbLibIndexOfName(Cols, SymLibField);
            If (Idx >= 0) And (Idx < Vals.Count) Then SymLibV := Vals[Idx];
            Idx := DbLibIndexOfName(Cols, DescField);
            If (Idx >= 0) And (Idx < Vals.Count) Then DescV := Vals[Idx];
            DbLibFootprintFields(Lines, Cols, Declared, RefFields, LibFields);
            While RefFields <> '' Do
            Begin
                OneRef := SplitNextTab(RefFields);
                OneLib := SplitNextTab(LibFields);
                RefV := '';
                LibV := '';
                Idx := DbLibIndexOfName(Cols, OneRef);
                If (Idx >= 0) And (Idx < Vals.Count) Then RefV := Vals[Idx];
                Idx := DbLibIndexOfName(Cols, OneLib);
                If (Idx >= 0) And (Idx < Vals.Count) Then LibV := Vals[Idx];
                If RefV <> '' Then
                Begin
                    If FpJson <> '' Then FpJson := FpJson + ',';
                    FpJson := FpJson + '{"ref":"' + EscapeJsonString(RefV)
                        + '","library":"' + EscapeJsonString(LibV) + '"}';
                End;
            End;
        End;
        If Conn <> Nil Then Conn.Free;
    Finally
        Lines.Free;
        Cols.Free;
        Vals.Free;
    End;

    If ErrCode <> '' Then
        Response := BuildErrorResponse(RequestId, ErrCode, ErrMsg)
    Else
        Response := BuildSuccessResponse(RequestId,
            '{"library_path":"' + EscapeJsonString(LibPath) + '"'
            + ',"table":"' + EscapeJsonString(Declared) + '"'
            + ',"key":"' + EscapeJsonString(KeyValue) + '"'
            + ',"key_field":"' + EscapeJsonString(KeyField) + '"'
            + ',"key_field_source":"' + EscapeJsonString(KeySource) + '"'
            + ',"lookup":"' + Lookup + '"'
            + ',"found":' + BoolToJsonStr(MatchCount > 0)
            + ',"match_count":' + IntToStr(MatchCount)
            + ',"rows_scanned":' + IntToStr(Scanned)
            + ',"scan_capped":' + BoolToJsonStr(Capped)
            + ',"symbol_ref":"' + EscapeJsonString(SymV) + '"'
            + ',"symbol_library":"' + EscapeJsonString(SymLibV) + '"'
            + ',"footprints":[' + FpJson + ']'
            + ',"description":"' + EscapeJsonString(DescV) + '"'
            + ',"fields":{' + FieldsJson + '}}');
    Result := Response;
End;

{ Lib_PlaceDbLibComponent - place one DbLib row on a schematic sheet.         }
{                                                                              }
{ NOT INTERACTIVE. The process Sch:PlaceIntegratedComponentFromDB, which the   }
{ reference examples use, attaches the part to the cursor for a person to drop }
{ and has been reported to place nothing when a script runs it. This uses      }
{ SchServer.LoadComponentFromDatabaseLibrary, declared in the schematic API    }
{ and used that way by CompPlaceFromLib.pas in the reference, which hands back }
{ the component built from the database row; it is then added, moved and      }
{ rotated as Gen_PlaceSchComponentFromLibrary does for a .SchLib symbol.       }
{                                                                              }
{ Altium resolves the DbLib by its file name among the libraries it has        }
{ available, so the DbLib must be installed or in the project: that is checked }
{ first, and so is the table, against the DbLib's own list.                    }
{ Params: library_path, table, key (required), x, y (mils), rotation (0, 90,   }
{ 180, 270), designator, sheet_path (optional; default the active sheet).      }
Function Lib_PlaceDbLibComponent(Params : String; RequestId : String) : String;
Var
    LibPath, Table, KeyValue, SheetPath, DesigStr, TableList, Declared, Problem : String;
    LibRef, DbTable, Response, AvailPath : String;
    X, Y, Rotation, OrientationVal, I, AvailCount, PartId, PartBack, PartCount : Integer;
    Lines : TStringList;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    Available : Boolean;
Begin
    LibPath := Trim(ExtractJsonValue(Params, 'library_path'));
    Table := Trim(ExtractJsonValue(Params, 'table'));
    KeyValue := ExtractJsonValue(Params, 'key');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    DesigStr := ExtractJsonValue(Params, 'designator');
    X := StrToIntDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToIntDef(ExtractJsonValue(Params, 'y'), 0);
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);
    PartId := StrToIntDef(ExtractJsonValue(Params, 'part_id'), 0);

    Problem := DbLibPathProblem(LibPath);
    If (Problem = '') And (Table = '') Then Problem := 'table is required';
    If (Problem = '') And (Trim(KeyValue) = '') Then Problem := 'key is required';
    If (Problem = '') And (Rotation <> 0) And (Rotation <> 90) And (Rotation <> 180)
        And (Rotation <> 270) Then
        Problem := 'rotation must be 0, 90, 180 or 270';
    If (Problem = '') And (PartId < 0) Then
        Problem := 'part_id must be 1 or more';
    If Problem <> '' Then
    Begin
        Response := BuildErrorResponse(RequestId, 'MISSING_PARAM', Problem);
        Result := Response;
        Exit;
    End;
    If Not FileExists(LibPath) Then
    Begin
        Response := BuildErrorResponse(RequestId, 'NOT_FOUND', 'No DbLib file at ' + LibPath);
        Result := Response;
        Exit;
    End;

    TableList := '';
    Lines := TStringList.Create;
    Try
        Try
            If LoadTextFile(Lines, LibPath) Then
                TableList := DbLibTableNames(Lines);
        Except
            TableList := '';
        End;
    Finally
        Lines.Free;
    End;
    Declared := DbLibFindTab(TableList, Table);
    If Declared = '' Then
    Begin
        Response := BuildErrorResponse(RequestId, 'TABLE_UNKNOWN',
            'The DbLib declares no table named "' + Table + '". It declares: '
            + StringReplace(TableList, #9, ', ', MkSet(rfReplaceAll)));
        Result := Response;
        Exit;
    End;

    If IntegratedLibraryManager = Nil Then
    Begin
        Response := BuildErrorResponse(RequestId, 'NO_MANAGER', 'IntegratedLibraryManager unavailable');
        Result := Response;
        Exit;
    End;
    Available := False;
    AvailCount := 0;
    Try AvailCount := IntegratedLibraryManager.AvailableLibraryCount; Except End;
    For I := 0 To AvailCount - 1 Do
    Begin
        AvailPath := '';
        Try AvailPath := IntegratedLibraryManager.AvailableLibraryPath(I); Except End;
        If UpperCase(AvailPath) = UpperCase(LibPath) Then
        Begin
            Available := True;
            Break;
        End;
    End;
    If Not Available Then
    Begin
        Response := BuildErrorResponse(RequestId, 'NOT_AVAILABLE',
            'Altium does not have ' + ExtractFileName(LibPath) + ' among its available '
            + 'libraries, and it finds a DbLib part by the library''s name. Install it '
            + 'with lib_install_library, or add it to the project, and place again.');
        Result := Response;
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Response := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath + '. Open it first.');
            Result := Response;
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Response := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Result := Response;
            Exit;
        End;
    End;
    If SchDoc.ObjectId <> eSheet Then
    Begin
        Response := BuildErrorResponse(RequestId, 'WRONG_DOC_KIND',
            'Target document is not a schematic sheet (ObjectId='
            + IntToStr(SchDoc.ObjectId) + '). Pass sheet_path to a .SchDoc.');
        Result := Response;
        Exit;
    End;

    { Load before the sheet's transaction opens, as the .SchLib placer does. }
    Comp := Nil;
    Try
        Comp := SchServer.LoadComponentFromDatabaseLibrary(ExtractFileName(LibPath),
            Declared, KeyValue);
    Except
        Comp := Nil;
    End;
    If Comp = Nil Then
    Begin
        Response := BuildErrorResponse(RequestId, 'PLACE_FAILED',
            'Altium returned no component for key "' + KeyValue + '" in table "'
            + Declared + '" of ' + ExtractFileName(LibPath) + '. Check the key with '
            + 'lib_dblib_get_record.');
        Result := Response;
        Exit;
    End;

    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try SchDoc.AddSchObject(Comp); Except End;
    Try Comp.MoveToXY(MilsToCoord(X), MilsToCoord(Y)); Except End;
    OrientationVal := 0;
    If Rotation = 90 Then OrientationVal := 1;
    If Rotation = 180 Then OrientationVal := 2;
    If Rotation = 270 Then OrientationVal := 3;
    Try Comp.SetState_Orientation(OrientationVal); Except End;
    If DesigStr <> '' Then
    Begin
        Try Comp.Designator.Text := DesigStr; Except End;
    End;
    { Which part of a multi-part component to place, the same two writers }
    { the generic CurrentPartID setter uses; read back below.             }
    PartCount := 0;
    Try PartCount := Comp.PartCount; Except End;
    If (PartId > 0) And ((PartCount = 0) Or (PartId <= PartCount)) Then
    Begin
        Try Comp.SetState_CurrentPartID(PartId); Except End;
        Try Comp.CurrentPartID := PartId; Except End;
    End;
    SchRegisterObject(SchDoc, Comp);
    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;
    MarkDocDirtyByPath(SchDoc.DocumentName);

    { Read back what Altium built. DatabaseTableName is empty on a part     }
    { that came from a .SchLib, so it says whether the database link held.  }
    LibRef := '';
    Try LibRef := Comp.LibReference; Except End;
    DbTable := '';
    Try DbTable := Comp.DatabaseTableName; Except End;
    PartBack := 0;
    Try PartBack := Comp.CurrentPartID; Except End;

    Response := BuildSuccessResponse(RequestId,
        '{"placed":true'
        + ',"library_path":"' + EscapeJsonString(LibPath) + '"'
        + ',"table":"' + EscapeJsonString(Declared) + '"'
        + ',"key":"' + EscapeJsonString(KeyValue) + '"'
        + ',"lib_reference":"' + EscapeJsonString(LibRef) + '"'
        + ',"database_table":"' + EscapeJsonString(DbTable) + '"'
        + ',"database_linked":' + BoolToJsonStr(DbTable <> '')
        + ',"x":' + IntToStr(X) + ',"y":' + IntToStr(Y)
        + ',"rotation":' + IntToStr(Rotation)
        + ',"part_id":' + IntToStr(PartBack)
        + ',"part_count":' + IntToStr(PartCount)
        + ',"part_id_applied":' + BoolToJsonStr((PartId = 0) Or (PartBack = PartId))
        + ',"designator":"' + EscapeJsonString(DesigStr) + '"}');
    Result := Response;
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

{ Which library actions only LOOK, and so must leave the active document  }
{ where they found it.                                                     }
{                                                                          }
{ Listed by name rather than inferred, because there is no property of a   }
{ handler this can read to tell reading from writing. The cost of the list }
{ going stale is a read that moves focus again, which is the bug it exists }
{ to prevent, so a new read-only handler belongs here on the day it is     }
{ written.                                                                 }
{                                                                          }
{ Writes are deliberately absent. Library authoring is a sequence of calls }
{ against a current component: lib_add_pins and the Lib_AddFootprint*      }
{ family read the focus the call before them left, so restoring it after a }
{ write would break the flow the bridge is built on. }
Function LibActionIsReadOnly(Action : String) : Boolean;
Begin
    Result := (Action = 'get_footprints')
           Or (Action = 'get_footprint_pads')
           Or (Action = 'get_library_geometry')
           Or (Action = 'get_component_details')
           Or (Action = 'get_pad_geometry')
           Or (Action = 'get_footprint_primitives')
           Or (Action = 'probe_footprint')
           Or (Action = 'probe_designator')
           Or (Action = 'audit_styles')
           Or (Action = 'search')
           Or (Action = 'get_dblib_info')
           Or (Action = 'query_dblib')
           Or (Action = 'get_dblib_record');
End;

{ The writes that attach a new primitive to the footprint on screen. Each     }
{ adds it to the footprint and to the library board, the pattern that        }
{ survives a save. Measured on AD 26.10: until the next footprint switch the }
{ footprint's iterator then lists the new primitive twice (12 entries, 11    }
{ objects), once through each container; a switch writes the board back and  }
{ the duplicate is gone.                                                     }
Function LibActionAddsToFootprint(Action : String) : Boolean;
Begin
    Result := (Action = 'add_footprint_pad')
           Or (Action = 'add_footprint_via')
           Or (Action = 'add_footprint_pads')
           Or (Action = 'add_footprint_track')
           Or (Action = 'add_footprint_tracks')
           Or (Action = 'add_footprint_regions')
           Or (Action = 'add_footprint_fills')
           Or (Action = 'add_footprint_arc')
           Or (Action = 'add_footprint_text')
           Or (Action = 'set_designator')
           Or (Action = 'set_designators')
           Or (Action = 'link_3d_model');
End;

{ Make the footprint on screen current again, which writes the library board }
{ back into it and loads it afresh: the same footprint stays on screen, and  }
{ a primitive just added is listed once. Without it, a later call walking    }
{ that footprint met each new primitive twice, and anything that moves what  }
{ it meets would move it twice.                                              }
Procedure SettleCurrentFootprint(Dummy : Integer);
Var
    PcbLib : IPCB_Library;
    Cur : IPCB_LibComponent;
Begin
    PcbLib := Nil;
    Try PcbLib := PCBServer.GetCurrentPCBLibrary; Except PcbLib := Nil; End;
    If PcbLib = Nil Then Exit;
    Cur := Nil;
    Try Cur := PcbLib.CurrentComponent; Except Cur := Nil; End;
    If Cur = Nil Then Exit;
    Try PcbLib.SetState_CurrentComponent(Cur); Except End;
End;

Function HandleLibraryCommand(Action : String; Params : String; RequestId : String) : String;
Var
    SweepLibs, SweepAction, OnePath, OneParams, OneReply : String;
    ItemsJson, DataJson, ErrJson, OkStr : String;
    SavedFocus : String;
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

    { Remember where the caller was looking, for the reads that are about to
      focus a library to answer. Captured HERE rather than inside each
      handler because they exit from several places apiece, and a restore
      that only runs on the success path leaves the focus moved on exactly
      the calls that already went wrong. }
    SavedFocus := '';
    If LibActionIsReadOnly(Action) Then SavedFocus := CurrentFocusedDocPath(0);

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
        'add_footprint_via':    Result := Lib_AddFootprintVia(Params, RequestId);
        'get_library_origin':   Result := Lib_GetLibraryOrigin(Params, RequestId);
        'count_layer_objects':  Result := Lib_CountLayerObjects(Params, RequestId);
        'set_via_properties':   Result := Lib_SetViaProperties(Params, RequestId);
        'add_footprint_pads':   Result := Lib_AddFootprintPads(Params, RequestId);
        'add_footprint_track':  Result := Lib_AddFootprintTrack(Params, RequestId);
        'add_footprint_tracks': Result := Lib_AddFootprintTracks(Params, RequestId);
        'add_footprint_regions': Result := Lib_AddFootprintRegions(Params, RequestId);
        'add_footprint_fills':  Result := Lib_AddFootprintFills(Params, RequestId);
        'get_mech_layers':      Result := Lib_GetMechLayers(Params, RequestId);
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
        'set_footprint_height': Result := Lib_SetFootprintHeight(Params, RequestId);
        'split_pin_functions':  Result := Lib_SplitPinFunctions(Params, RequestId);
        'get_installed_libraries': Result := Lib_GetInstalledLibraries(Params, RequestId);
        'install_library':      Result := Lib_InstallLibrary(Params, RequestId);
        'uninstall_library':    Result := Lib_UninstallLibrary(Params, RequestId);
        'get_vault_name':       Result := Lib_GetVaultName(Params, RequestId);
        'set_pad_properties':   Result := Lib_SetPadProperties(Params, RequestId);
        'get_vault_component':  Result := Lib_GetVaultComponent(Params, RequestId);
        'find_vault_components': Result := Lib_FindVaultComponents(Params, RequestId);
        'delete_component':     Result := Lib_DeleteComponent(Params, RequestId);
        'rename_component':     Result := Lib_RenameComponent(Params, RequestId);
        'delete_footprint':     Result := Lib_DeleteFootprint(Params, RequestId);
        'delete_footprint_primitives':
                                Result := Lib_DeleteFootprintPrimitives(Params, RequestId);
        'move_bodies_to_layer': Result := Lib_MoveBodiesToLayer(Params, RequestId);
        'redraw_assembly':      Result := Lib_RedrawAssembly(Params, RequestId);
        'remove_model':         Result := Lib_RemoveModel(Params, RequestId);
        'rename_footprint':     Result := Lib_RenameFootprint(Params, RequestId);
        'set_model_name':       Result := Lib_SetModelName(Params, RequestId);
        'set_model_source':     Result := Lib_SetModelSource(Params, RequestId);
        'probe_footprint':      Result := Lib_ProbeFootprint(Params, RequestId);
        'get_pad_geometry':     Result := Lib_GetPadGeometry(Params, RequestId);
        'get_footprint_primitives': Result := Lib_GetFootprintPrimitives(Params, RequestId);
        'normalize_implementations': Result := Lib_NormalizeImplementations(Params, RequestId);
        'clear_source_library': Result := Lib_ClearSourceLibrary(Params, RequestId);
        'get_dblib_info':       Result := Lib_GetDbLibInfo(Params, RequestId);
        'query_dblib':          Result := Lib_QueryDbLib(Params, RequestId);
        'get_dblib_record':     Result := Lib_GetDbLibRecord(Params, RequestId);
        'place_dblib_component': Result := Lib_PlaceDbLibComponent(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown library action: ' + Action);
    End;

    If LibActionAddsToFootprint(Action) Then SettleCurrentFootprint(0);

    { Put the caller's document back. A no-op unless a read actually moved
      it, and it runs whether the handler succeeded or refused. }
    RestoreFocusedDoc(SavedFocus);
End;

{ UnitStamp_Library - which build of Library.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_Library(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
