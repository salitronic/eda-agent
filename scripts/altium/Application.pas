{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Application.pas - Application-level functions for the Altium integration bridge             }
{..............................................................................}

Function App_Ping(RequestId : String) : String;
Var
    Ver, ScriptProjects : String;
    Workspace : IWorkspace;
    Proj : IProject;
    I : Integer;
Begin
    // Return the compiled-in SCRIPT_VERSION so Python can detect a stale
    // Altium script cache. cast_errors surfaces the silent-cast counter
    // (see RecordCastError), non-zero at session end indicates an
    // interface mismatch worth investigating.
    //
    // altium_version rides along because a bug report without it costs a
    // round trip, and twice now the answer changed the diagnosis: two
    // reports of a wedged polling loop were both an Altium far below the
    // versions this is developed against, naming interfaces that build
    // does not declare. It is reported, never acted on: an old build
    // runs most of this toolset perfectly well, and refusing to start
    // would take away the part that works to prevent the part that does
    // not. Empty when the API will not answer, which is itself a fact
    // worth having.
    Ver := '';
    Try
        Ver := Client.GetProductVersion;
    Except
        Ver := '';
    End;
    // Where the script project is open from. Python compares
    // SCRIPT_VERSION against a Main.pas it can see, and in a working-tree
    // setup Altium can have the script project open from somewhere else,
    // so the open Altium_API.PrjScr paths go back for it to compare.
    // Read only; a failure costs this field and nothing else.
    ScriptProjects := '';
    Try
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            For I := 0 To Workspace.DM_ProjectCount - 1 Do
            Begin
                Proj := Workspace.DM_Projects(I);
                If Proj = Nil Then Continue;
                If Pos('ALTIUM_API.PRJSCR', UpperCase(Proj.DM_ProjectFullPath)) > 0 Then
                Begin
                    If ScriptProjects <> '' Then ScriptProjects := ScriptProjects + ',';
                    ScriptProjects := ScriptProjects + '"'
                        + EscapeJsonString(Proj.DM_ProjectFullPath) + '"';
                End;
            End;
        End;
    Except
        ScriptProjects := '';
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"pong":true,"script_version":"' + SCRIPT_VERSION +
        '","altium_version":"' + EscapeJsonString(Ver) +
        '","protocol_version":' + IntToStr(PROTOCOL_VERSION) +
        ',"cast_errors":' + IntToStr(CastErrorCount) +
        { The folder this loop takes requests from. Two script projects, or }
        { two servers on one machine, are told apart by it: a request only  }
        { reaches the loop whose folder it was written to (issue #94).      }
        ',"workspace_dir":"' + EscapeJsonString(WorkspaceDir) + '"' +
        ',"script_projects":[' + ScriptProjects + ']}');
End;

Function App_GetVersion(RequestId : String) : String;
Var
    Data, Ver : String;
Begin
    Ver := '';
    Try
        Ver := Client.GetProductVersion;
    Except
        Ver := '';
    End;
    If Ver <> '' Then
        Data := '{"version":"' + EscapeJsonString(Ver) + '","product_name":"Altium Designer"}'
    Else
        Data := '{"product_name":"Altium Designer","note":"Version API not available in DelphiScript"}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{ The full path of an open document, given as Altium's project model    }
{ gives it: DM_FullPath is a bare file name for a document outside every }
{ project, and the client's own document then names the full path.       }
Function EdOpenFullPath(Path : String) : String;
Var
    ServerDoc : IServerDocument;
Begin
    Result := Path;
    If (Path = '') Or EdLooksFull(Path) Then Exit;
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(Path); Except ServerDoc := Nil; End;
    If ServerDoc <> Nil Then
    Begin
        Try
            If EdLooksFull(ServerDoc.FileName) Then Result := ServerDoc.FileName;
        Except
        End;
    End;
End;

{ What a reply says beside "modified" (Altium's own flag): "edited" (the  }
{ bridge's record, EdRecord) and "unsaved" (either). Altium's flag misses }
{ API edits, and on a PcbLib it reads False while the editor shows the    }
{ library edited; the record misses only edits made by hand, which       }
{ Altium's flag sees on every other kind. The caller runs EdRefresh first.}
Function EdDocFields(Path : String; Modified : Boolean) : String;
Var
    Body : String;
    Edited : Boolean;
Begin
    Body := EdBodyOf(EdRecord, EdKey(EdOpenFullPath(Path)));
    Edited := (Body <> '') And (EdPart(Body, Chr(2), 6) = 'E');
    Result := ',' + JsonBool('edited', Edited)
        + ',' + JsonBool('unsaved', Edited Or Modified);
    If Edited Then
        Result := Result + ',' + JsonStr('last_edit', EdPart(Body, Chr(2), 2))
            + ',' + JsonStr('last_command', EdPart(Body, Chr(2), 4));
End;

Function App_GetOpenDocuments(RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I, J : Integer;
    Data, DocInfo, FileName, FullPath, Kind, LoadedStr, ModifiedStr : String;
    FirstItem, IsLoaded : Boolean;
Begin
    EdRefresh(0);
    Workspace := GetWorkspace;
    Data := '[';
    FirstItem := True;
    If Workspace <> Nil Then
    Begin
        For I := 0 To Workspace.DM_ProjectCount - 1 Do
        Begin
            Project := Workspace.DM_Projects(I);
            If Project <> Nil Then
            Begin
                For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    Doc := Project.DM_LogicalDocuments(J);
                    If Doc <> Nil Then
                    Begin
                        If Not FirstItem Then Data := Data + ',';
                        FirstItem := False;
                        FileName := Doc.DM_FileName;
                        Kind := Doc.DM_DocumentKind;

                        // "loaded" means the document is actually resident in
                        // the editor server (SchServer/PCBServer/Client), not
                        // just listed as a project member. Project-scope
                        // queries and modifications only touch loaded sheets;
                        // callers should call load_project_sheets first if
                        // they need to hit every sheet in the project.
                        FullPath := '';
                        Try FullPath := Doc.DM_FullPath; Except FullPath := FileName; End;
                        // Client.GetDocumentByPath resolves any resident doc
                        // (SCH/PCB/OutJob/etc.) to an IServerDocument. nil
                        // means the file is a project member on disk but
                        // hasn't been loaded into the editor.
                        IsLoaded := False;
                        Try
                            If Client.GetDocumentByPath(FullPath) <> Nil Then
                                IsLoaded := True;
                        Except IsLoaded := False; End;
                        If IsLoaded Then LoadedStr := 'true' Else LoadedStr := 'false';

                        // Unsaved state. app_context filters this list on
                        // "modified" to warn about pending edits, so a missing
                        // key made that warning unreachable and every session
                        // read as clean.
                        If DocIsModified(FullPath) Then ModifiedStr := 'true'
                        Else ModifiedStr := 'false';

                        DocInfo := '{"file_name":"' + EscapeJsonString(ExtractFileName(FileName)) + '"';
                        DocInfo := DocInfo + ',"file_path":"' + EscapeJsonString(FullPath) + '"';
                        DocInfo := DocInfo + ',"document_kind":"' + EscapeJsonString(Kind) + '"';
                        DocInfo := DocInfo + ',"loaded":' + LoadedStr;
                        DocInfo := DocInfo + ',"modified":' + ModifiedStr
                            + EdDocFields(FullPath, ModifiedStr = 'true') + '}';
                        Data := Data + DocInfo;
                    End;
                End;
            End;
        End;
    End;
    Data := Data + ']';
    Result := BuildSuccessResponse(RequestId, Data);
End;

Function App_GetActiveDocument(RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    View : IServerDocumentView;
    ServerDoc : IServerDocument;
    Data, FileName : String;
Begin
    EdRefresh(0);
    Data := '';
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Workspace.DM_FocusedDocument;
        If Doc <> Nil Then
        Begin
            FileName := DocFullPath(Doc);
            Data := '{"file_name":"' + EscapeJsonString(ExtractFileName(FileName)) + '"';
            Data := Data + ',"file_path":"' + EscapeJsonString(FileName) + '"';
            Data := Data + ',"document_kind":"' + EscapeJsonString(Doc.DM_DocumentKind) + '"';
            Data := Data + ',"modified":' + BoolToJsonStr(DocIsModified(FileName))
                + EdDocFields(FileName, DocIsModified(FileName)) + '}';
        End;
    End;

    // DM_FocusedDocument is UI-focus-dependent and returns Nil when no
    // editor window holds focus (e.g. right after a programmatic
    // create/place). The editor's current view names the document on
    // screen, a free library included; the SCH and PCB servers' current
    // documents come last, as they can name a sheet shown earlier.
    If Data = '' Then
    Begin
        ServerDoc := Nil;
        Try
            View := Client.CurrentView;
            If View <> Nil Then ServerDoc := View.OwnerDocument;
        Except ServerDoc := Nil; End;
        If ServerDoc <> Nil Then
        Begin
            FileName := '';
            Try FileName := ServerDoc.FileName; Except End;
            If FileName <> '' Then
            Begin
                Data := '{"file_name":"' + EscapeJsonString(ExtractFileName(FileName)) + '"';
                Data := Data + ',"file_path":"' + EscapeJsonString(FileName) + '"';
                Data := Data + ',"document_kind":"' + EscapeJsonString(ServerDoc.Kind) + '"';
                Data := Data + ',"modified":' + BoolToJsonStr(DocIsModified(FileName))
                + EdDocFields(FileName, DocIsModified(FileName)) + '}';
            End;
        End;
    End;
    If Data = '' Then
    Begin
        SchDoc := Nil;
        Try SchDoc := SchServer.GetCurrentSchDocument; Except SchDoc := Nil; End;
        If SchDoc <> Nil Then
        Begin
            FileName := SchDoc.DocumentName;
            Data := '{"file_name":"' + EscapeJsonString(ExtractFileName(FileName)) + '"';
            Data := Data + ',"file_path":"' + EscapeJsonString(FileName) + '"';
            Data := Data + ',"document_kind":"SCH"';
            Data := Data + ',"modified":' + BoolToJsonStr(DocIsModified(FileName))
                + EdDocFields(FileName, DocIsModified(FileName)) + '}';
        End;
    End;
    If Data = '' Then
    Begin
        Board := Nil;
        Try Board := GetPCBBoardAnywhere(0); Except Board := Nil; End;
        If Board <> Nil Then
        Begin
            FileName := Board.FileName;
            Data := '{"file_name":"' + EscapeJsonString(ExtractFileName(FileName)) + '"';
            Data := Data + ',"file_path":"' + EscapeJsonString(FileName) + '"';
            Data := Data + ',"document_kind":"PCB"';
            Data := Data + ',"modified":' + BoolToJsonStr(DocIsModified(FileName))
                + EdDocFields(FileName, DocIsModified(FileName)) + '}';
        End;
    End;

    If Data = '' Then Data := '{}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{ A path in one spelling: back slashes, lower case. }
Function PathKey(Path : String) : String;
Begin
    Result := LowerCase(StringReplace(Path, '/', '\', MkSet(rfReplaceAll)));
End;

{ True when an open project has the document among its own. The project   }
{ keeps its sheets apart from the editor tab, so an unsaved change can     }
{ outlive the tab being closed (seen on AD 26.11).                         }
Function DocBelongsToOpenProject(FilePath : String) : Boolean;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Member : IDocument;
    P, D : Integer;
    Wanted : String;
Begin
    Result := False;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    Wanted := PathKey(FilePath);
    P := 0;
    While (Not Result) And (P < Workspace.DM_ProjectCount) Do
    Begin
        Project := Nil;
        Try Project := Workspace.DM_Projects(P); Except Project := Nil; End;
        D := 0;
        While (Project <> Nil) And (Not Result) And (D < Project.DM_LogicalDocumentCount) Do
        Begin
            Member := Nil;
            Try Member := Project.DM_LogicalDocuments(D); Except Member := Nil; End;
            If Member <> Nil Then Result := PathKey(Member.DM_FullPath) = Wanted;
            Inc(D);
        End;
        Inc(P);
    End;
End;

{ Close ONE document, found by its full path, whatever has the focus.        }
{ WorkspaceManager:CloseObject acts on the FOCUSED object, not the one named: }
{ a close by path once closed a different project. This closes the document }
{ object itself, as the reference scripts do (Client.CloseDocument), refuses }
{ one that reads modified (a floor only: that read can miss editor edits),  }
{ and looks it up again afterwards to say whether it is really gone.        }
{ With discard, the modified flag is cleared first, unread, which is what   }
{ keeps the save prompt away. For a schematic that belongs to an open      }
{ project, discarded stays false: the project can keep the change until it }
{ is itself closed without saving.                                          }
Function App_CloseDocument(Params : String; RequestId : String) : String;
Var
    FilePath : String;
    ServerDoc, Again : IServerDocument;
    WasModified, Discard, KeptByProject, Closed : Boolean;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');
    Discard := LowerCase(ExtractJsonValue(Params, 'discard')) = 'true';
    If FilePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'file_path is required');
        Exit;
    End;
    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_LOADED', 'Document not loaded: ' + FilePath);
        Exit;
    End;
    { Asked for a copy opened under a forward-slash path, Altium hands back }
    { the original document, and closing it closed the wrong one (AD 25.8). }
    { Only the document named is closed.                                    }
    If PathKey(ServerDoc.FileName) <> PathKey(FilePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'OTHER_DOCUMENT',
            FilePath + ' led to ' + ServerDoc.FileName + ' instead; nothing was '
            + 'closed. A copy opened under a forward-slash path does this: close '
            + 'it from its tab.');
        Exit;
    End;
    WasModified := False;
    Try WasModified := ServerDoc.Modified; Except WasModified := False; End;
    { Changes a tool made through the API do not set Modified; the bridge }
    { keeps its own list of them.                                         }
    If DocWasEdited(FilePath) Then WasModified := True;
    If WasModified And Not Discard Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MODIFIED',
            'The document has unsaved changes, so it was not closed: ' + FilePath);
        Exit;
    End;
    If Discard Then
    Begin
        { Still marked, the close can raise Altium's save prompt. }
        Try ServerDoc.SetModified(False); Except NoteWriteFailed('clear the modified flag before close'); End;
    End;
    Client.CloseDocument(ServerDoc);
    Again := Nil;
    Try Again := Client.GetDocumentByPath(FilePath); Except Again := Nil; End;
    Closed := Again = Nil;
    { Closed by the bridge, edits and all: no longer unsaved, and not lost }
    { either, since the caller chose it (discard).                         }
    If Closed Then ForgetDocEdited(FilePath);
    If Closed Then EdForget(FilePath, 'C');
    KeptByProject := False;
    If Discard And WasModified And (UpperCase(ExtractFileExt(FilePath)) = '.SCHDOC') Then
        KeptByProject := DocBelongsToOpenProject(FilePath);
    Result := BuildSuccessResponse(RequestId, JsonObj(JsonBool('closed', Closed)
        + ',' + JsonBool('discarded', Discard And WasModified And Not KeptByProject)
        + ',' + JsonBool('project_may_hold_edit', KeptByProject)
        + ',' + JsonStr('file_path', FilePath)));
End;

Function App_RunProcess(Params : String; RequestId : String) : String;
Var
    ProcessName, ProcessParams : String;
    Remaining, Pair, Key, Val : String;
    PipePos, EqPos : Integer;
Begin
    ProcessName := ExtractJsonValue(Params, 'process_name');
    ProcessParams := ExtractJsonValue(Params, 'parameters');

    If ProcessName <> '' Then
    Begin
        ResetParameters;
        If ProcessParams <> '' Then
        Begin
            // Parse pipe-separated key=value parameters
            Remaining := ProcessParams;
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
                EqPos := Pos('=', Pair);
                If EqPos > 1 Then
                Begin
                    Key := Copy(Pair, 1, EqPos - 1);
                    If Key <> '' Then
                    Begin
                        Val := Copy(Pair, EqPos + 1, Length(Pair));
                        AddStringParameter(Key, Val);
                    End;
                End;
            End;
        End;
        RunProcess(ProcessName);
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAMETER', 'Process name is required');
End;

{..............................................................................}
{ Get key Altium preferences (units, grid, snap)                              }
{..............................................................................}

Function App_GetPreferences(RequestId : String) : String;
Var
    Board : IPCB_Board;
    SchDoc : ISch_Document;
    Data : String;
Begin
    Data := '{';

    { Try to get PCB preferences from the active board }
    Try
        Board := GetPCBBoardAnywhere(0);
        If Board <> Nil Then
        Begin
            Data := Data + '"pcb":{';
            Try Data := Data + '"snap_x_mils":' + FloatToJsonStr(CoordToMilsF(Board.SnapGridSizeX)); Except Data := Data + '"snap_x_mils":0'; End;
            Try Data := Data + ',"snap_y_mils":' + FloatToJsonStr(CoordToMilsF(Board.SnapGridSizeY)); Except Data := Data + ',"snap_y_mils":0'; End;
            Try Data := Data + ',"display_unit":"' + IntToStr(Board.DisplayUnit) + '"'; Except Data := Data + ',"display_unit":"unknown"'; End;
            Data := Data + '}';
        End
        Else
            Data := Data + '"pcb":null';
    Except
        Data := Data + '"pcb":null';
    End;

    { Try to get schematic preferences from active schematic }
    Try
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc <> Nil Then
        Begin
            Data := Data + ',"schematic":{';
            Try Data := Data + '"visible_grid_size":' + IntToStr(SchDoc.VisibleGridSize); Except Data := Data + '"visible_grid_size":0'; End;
            Try Data := Data + ',"snap_grid_size":' + IntToStr(SchDoc.SnapGridSize); Except Data := Data + ',"snap_grid_size":0'; End;
            Data := Data + '}';
        End
        Else
            Data := Data + ',"schematic":null';
    Except
        Data := Data + ',"schematic":null';
    End;

    Data := Data + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Execute a menu command by path (e.g., "File>Save All")                      }
{ Params: menu_path (pipe-separated path like "File|Save All")                }
{..............................................................................}

Function App_ExecuteMenu(Params : String; RequestId : String) : String;
Var
    MenuPath, ProcessName : String;
Begin
    MenuPath := ExtractJsonValue(Params, 'menu_path');

    If MenuPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'menu_path is required');
        Exit;
    End;

    { Productive menu paths that have a dedicated handler -- delegate so   }
    { the caller gets context-validation + violation counts back instead   }
    { of a bare "success:true" that hides a silent no-op when there's no   }
    { active PCB / SCH document. RunProcess never raises on missing        }
    { context so the prior implementation reported success even when       }
    { nothing actually ran.                                                 }
    { REDIRECTED, NOT DELEGATED. These used to call PCB_RunDRC and         }
    { Gen_RunERC directly. Neither can be called from here: Application.pas }
    { is compiled third and those live in PCB.pas and Generic.pas, seventh  }
    { and eighth. DelphiScript has no forward declarations, so the calls    }
    { resolved to nothing and firing either menu path through this handler  }
    { took the scripting engine down with an access violation rather than   }
    { reporting anything. Naming the tool gives the caller the same result  }
    { by a route that works.                                                }
    If MenuPath = 'Tools|Design Rule Check' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'USE_DEDICATED_TOOL',
            'Call pcb_run_drc instead. It validates that a PCB is actually '
            + 'open and returns the violation list, where this would run '
            + 'the menu item and report success even when nothing ran.');
        Exit;
    End;
    If MenuPath = 'Tools|Electrical Rules Check' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'USE_DEDICATED_TOOL',
            'Call run_erc instead. It validates the document context and '
            + 'returns the violation list, where this would report success '
            + 'for a menu item that did nothing.');
        Exit;
    End;

    { Map remaining common menu paths to their process equivalents.       }
    { These are display-side commands where "did it fire" is the only     }
    { reasonable success signal anyway.                                    }
    If MenuPath = 'File|Save All' Then
        ProcessName := 'WorkspaceManager:SaveAll'
    Else If MenuPath = 'Project|Compile' Then
        ProcessName := 'WorkspaceManager:Compile'
    Else If MenuPath = 'Edit|Select All' Then
        ProcessName := 'Sch:SelectAll'
    Else If MenuPath = 'Edit|Deselect All' Then
        ProcessName := 'Sch:DeSelect'
    Else If MenuPath = 'View|Zoom Fit' Then
        ProcessName := 'Sch:ZoomFit'
    Else If MenuPath = 'Tools|Preferences' Then
        ProcessName := 'Client:RunConfigurationDialog'
    Else If MenuPath = 'Tools|Extensions and Updates' Then
        ProcessName := 'Client:ManagePluginsAndUpdates'
    Else
    Begin
        { Unmapped path. The old fallback passed the whole pipe-separated
          path to Client:RunMenu as a MenuID, which is not what a MenuID
          is. It reported success, and MEASURED 2026-08-17,
          Tools|Update From Libraries returned in 0.11s having opened
          nothing.

          So this branch is not merely unverifiable, it is known not to
          work, and a success here is a claim contradicted by the
          measurement in the line above it. It refuses instead. That
          only became the better answer once app_click_menu existed:
          it drives the real menu bar, so it reaches the arbitrary
          paths this branch was invented to guess at, and says what
          the menu actually contained when an item is missing. }
        Result := BuildErrorResponse(RequestId, 'UNMAPPED_MENU_PATH',
            'This path is not one the bridge maps to a process, and the '
            + 'old fallback that guessed a MenuID from it was measured '
            + 'opening nothing while reporting success. Call '
            + 'app_click_menu with the same path: it drives the real '
            + 'menu bar, so it works for any item and tells you what '
            + 'the menu held when the item is not there.');
        Exit;
    End;

    ResetParameters;
    { Sch:DeSelectAll left the selection standing (AD 26.11.1.5);  }
    { Sch:DeSelect with Action=All clears it.                      }
    If ProcessName = 'Sch:DeSelect' Then AddStringParameter('Action', 'All');
    RunProcess(ProcessName);

    { DISPATCHED, not "succeeded". RunProcess is fire and forget: it       }
    { returns nothing, raises nothing, and silently ignores a process id   }
    { it does not know. The note above the WorkspaceManager:Compare call   }
    { in Project.pas records the same trap being hit before, when          }
    { 'PCB:UpdatePCBFromProject' turned out not to be a real id and the    }
    { handler no-opped while reporting success.                            }
    {                                                                       }
    { MEASURED 2026-08-17 against a live, idle, responsive Altium:          }
    {   Tools|Preferences  -> success in 0.11s, NO dialog. That path is     }
    {                         mapped to Client:RunConfigurationDialog and   }
    {                         opens a MODAL, so a handler that really       }
    {                         launched it would have BLOCKED until the      }
    {                         dialog closed, exactly as project.update_pcb  }
    {                         does. Returning immediately proves no modal   }
    {                         was raised.                                   }
    {   View|Zoom Fit      -> success in 0.09s, no observable effect.       }
    {                                                                       }
    { So this cannot honestly claim the command ran. It reports what it     }
    { attempted and says the outcome is unverified, and names the tool      }
    { that CAN confirm one, app_click_menu, which drives the real menu bar  }
    { and fails loudly when an item is not there.                           }
    Result := BuildSuccessResponse(RequestId,
        '{"success":true'
        + ',"dispatched":true'
        + ',"outcome_verified":false'
        + ',"menu_path":"' + EscapeJsonString(MenuPath) + '"'
        + ',"process":"' + EscapeJsonString(ProcessName) + '"'
        + ',"note":"The process was dispatched. RunProcess cannot report '
        + 'failure and silently ignores unknown ids, so this is NOT '
        + 'evidence the command ran. Several mapped ids were measured '
        + 'doing nothing. To invoke a menu item and know it happened, '
        + 'use app_click_menu, which drives the menu bar itself."}');
End;

{..............................................................................}
{ Get text content from the Windows clipboard                                  }
{..............................................................................}

Function App_GetClipboardText(RequestId : String) : String;
Var
    ClipText : String;
Begin
    Try
        ClipText := Clipboard.AsText;
        Result := BuildSuccessResponse(RequestId, '{"text":"' + EscapeJsonString(ClipText) + '"}');
    Except
        Result := BuildSuccessResponse(RequestId, '{"text":"","note":"Clipboard empty or contains non-text data"}');
    End;
End;

{ The open project at ProjectPath, under either slash spelling, or Nil. }
Function OpenProjectAt(Workspace : IWorkspace; ProjectPath : String) : IProject;
Var
    I : Integer;
    Proj : IProject;
    Want, Have : String;
Begin
    Result := Nil;
    Want := UpperCase(CanonicalPathValue('path', ProjectPath));
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Proj := Nil;
        Try Proj := Workspace.DM_Projects(I); Except End;
        If Proj = Nil Then Continue;
        Have := '';
        Try Have := Proj.DM_ProjectFullPath; Except End;
        If UpperCase(CanonicalPathValue('path', Have)) = Want Then
        Begin
            Result := Proj;
            Exit;
        End;
    End;
End;

{ The projects that list FilePath among their documents, as a JSON array    }
{ of project paths. What membership IS, read back, rather than what the     }
{ create call was asked to do.                                              }
Function ProjectsHoldingJson(FilePath : String) : String;
Var
    Workspace : IWorkspace;
    Proj : IProject;
    Doc : IDocument;
    I, J : Integer;
    Want, Have, ProjPath : String;
Begin
    Result := '';
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    Want := UpperCase(CanonicalPathValue('path', FilePath));
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Proj := Nil;
        Try Proj := Workspace.DM_Projects(I); Except End;
        If Proj = Nil Then Continue;
        For J := 0 To Proj.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Nil;
            Try Doc := Proj.DM_LogicalDocuments(J); Except End;
            If Doc = Nil Then Continue;
            Have := '';
            Try Have := Doc.DM_FullPath; Except End;
            If UpperCase(CanonicalPathValue('path', Have)) = Want Then
            Begin
                ProjPath := '';
                Try ProjPath := Proj.DM_ProjectFullPath; Except End;
                If Result <> '' Then Result := Result + ',';
                Result := Result + '"' + EscapeJsonString(ProjPath) + '"';
                Break;
            End;
        End;
    End;
End;

{..............................................................................}
{ DOCUMENTS BY FULL PATH (#428): the helpers shared by Ensure in the         }
{ dispatcher and the active document / project calls below.                 }
{..............................................................................}

{ What is wrong with the spelling of an absolute path, '' when nothing.      }
Function EnsurePathProblem(P : String; IsProject : Boolean) : String;
Var
    I : Integer;
    Ext, Padded : String;
Begin
    Result := '';
    If Length(P) < 4 Then
    Begin
        Result := 'not an absolute path';
        Exit;
    End;
    If Pos('/', P) > 0 Then
    Begin
        Result := 'forward slashes: write it with back slashes';
        Exit;
    End;
    If Not (((Copy(P, 2, 2) = ':\') And (Pos(UpperCase(Copy(P, 1, 1)),
            'ABCDEFGHIJKLMNOPQRSTUVWXYZ') > 0)) Or (Copy(P, 1, 2) = '\\')) Then
    Begin
        Result := 'not an absolute path (a drive letter or \\server\share first)';
        Exit;
    End;
    Padded := P + '\';
    If (Pos('\..\', Padded) > 0) Or (Pos('\.\', Padded) > 0) Then
    Begin
        Result := 'a . or .. part: write the path out in full';
        Exit;
    End;
    For I := 1 To Length(P) - 1 Do
        If (Copy(P, I, 1) = '~') And (Pos(Copy(P, I + 1, 1), '0123456789') > 0) Then
        Begin
            Result := 'a short 8.3 spelling: write the long path';
            Exit;
        End;
    Ext := LowerCase(ExtractFileExt(P));
    If IsProject Then
    Begin
        If Not ((Copy(Ext, 1, 4) = '.prj') Or (Ext = '.libpkg')) Then
            Result := 'not a project file (.PrjPcb, .PrjScr, .LibPkg ...)';
        Exit;
    End;
    If Ext = '' Then Result := 'no file extension';
End;

{ What is wrong with sending COMMAND to DOC, '' when nothing. Only the        }
{ categories that act on one kind of document are checked.                  }
Function EnsureKindProblem(Command : String; Doc : String) : String;
Var
    Category, Ext : String;
Begin
    Result := '';
    Category := LowerCase(Trim(Command));
    If Pos('.', Category) > 0 Then Category := Copy(Category, 1, Pos('.', Category) - 1);
    Ext := LowerCase(ExtractFileExt(Doc));
    If (Category = 'pcb') And Not ((Ext = '.pcbdoc') Or (Ext = '.pcblib')) Then
        Result := 'a pcb. command needs a .PcbDoc (or a .PcbLib)';
    If (Category = 'library') And Not ((Ext = '.pcblib') Or (Ext = '.schlib')) Then
        Result := 'a library. command needs a .PcbLib or a .SchLib';
End;

{ The kind Client.OpenDocument takes for DOC, '' for one opened by process.  }
Function EnsureDocKind(Doc : String) : String;
Var
    Ext : String;
Begin
    Result := '';
    Ext := LowerCase(ExtractFileExt(Doc));
    If Ext = '.pcbdoc' Then Result := 'PCB';
    If Ext = '.schdoc' Then Result := 'SCH';
    If Ext = '.pcblib' Then Result := 'PCBLIB';
    If Ext = '.schlib' Then Result := 'SCHLIB';
End;

Function EnsureSame(A : String; B : String) : Boolean;
Begin
    Result := (A <> '') And (UpperCase(CanonicalPathValue('path', A))
        = UpperCase(CanonicalPathValue('path', B)));
End;

{ The file of the editor's own document in front: not DM_FullPath, which is  }
{ a bare name for a document outside every project.                        }
Function EnsureFrontPath(Dummy : Integer) : String;
Var
    View : IServerDocumentView;
Begin
    Result := '';
    Try
        View := Client.CurrentView;
        If View <> Nil Then Result := View.OwnerDocument.FileName;
    Except Result := ''; End;
End;

{ Is DOC one of PROJECT's documents (shared files outside its folder too)? }
Function EnsureMember(Project : IProject; Doc : String) : Boolean;
Var
    I : Integer;
    D : IDocument;
    P : String;
Begin
    Result := False;
    If Project = Nil Then Exit;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        D := Nil;
        Try D := Project.DM_LogicalDocuments(I); Except D := Nil; End;
        If D = Nil Then Continue;
        P := '';
        Try P := D.DM_FullPath; Except P := ''; End;
        If EnsureSame(P, Doc) Then
        Begin
            Result := True;
            Exit;
        End;
    End;
End;

{ Open DOC inside the project already open. Measured on AD 25.8.1 (#428): a  }
{ member of an OPEN project stays in it whichever way it is opened; opened  }
{ while its project is closed it loads as an orphan no project lists.       }
Function EnsureOpen(Doc : String) : IServerDocument;
Var
    Kind : String;
Begin
    Result := Nil;
    Kind := EnsureDocKind(Doc);
    If Kind <> '' Then
    Begin
        Try Client.OpenDocument(Kind, Doc); Except End;
    End;
    If Kind = '' Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', Doc);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    Try Result := Client.GetDocumentByPath(Doc); Except Result := Nil; End;
End;

{ Is the project at PRJ open now? }
Function EnsureProjectIsOpen(Prj : String) : Boolean;
Var
    Workspace : IWorkspace;
Begin
    Result := False;
    Workspace := Nil;
    Try Workspace := GetWorkspace; Except Workspace := Nil; End;
    If Workspace <> Nil Then Result := OpenProjectAt(Workspace, Prj) <> Nil;
End;

{ The open project at PRJ, opened when it is not. }
Function EnsureProjectOpen(Prj : String) : IProject;
Var
    Workspace : IWorkspace;
Begin
    Result := Nil;
    Workspace := Nil;
    Try Workspace := GetWorkspace; Except Workspace := Nil; End;
    If Workspace = Nil Then Exit;
    Result := OpenProjectAt(Workspace, Prj);
    If Result <> Nil Then Exit;
    If Not FileExists(Prj) Then Exit;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', Prj);
    RunProcess('WorkspaceManager:OpenObject');
    Try Workspace := GetWorkspace; Except Workspace := Nil; End;
    If Workspace <> Nil Then Result := OpenProjectAt(Workspace, Prj);
End;

{ Every document of PROJECT, with where it is and what state it is in. }
Function ProjectFilesJson(Project : IProject) : String;
Var
    I : Integer;
    D : IDocument;
    P, Kind, Folder, Front, Items : String;
    ServerDoc : IServerDocument;
    Modified : Boolean;
Begin
    Items := '';
    Folder := '';
    Try Folder := UpperCase(ExtractFilePath(Project.DM_ProjectFullPath)); Except End;
    Front := EnsureFrontPath(0);
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        D := Nil;
        Try D := Project.DM_LogicalDocuments(I); Except D := Nil; End;
        If D = Nil Then Continue;
        P := '';
        Try P := D.DM_FullPath; Except P := ''; End;
        If P = '' Then Continue;
        Kind := '';
        Try Kind := D.DM_DocumentKind; Except Kind := ''; End;
        ServerDoc := Nil;
        Try ServerDoc := Client.GetDocumentByPath(P); Except ServerDoc := Nil; End;
        Modified := False;
        If ServerDoc <> Nil Then Modified := DocIsModified(P);
        If Items <> '' Then Items := Items + ',';
        Items := Items + '{' + JsonStr('file_path', P) + ',' + JsonStr('kind', Kind) + ','
            + JsonBool('in_project_folder', (Folder <> '')
                And (Copy(UpperCase(P), 1, Length(Folder)) = Folder)) + ','
            + JsonBool('exists', FileExists(P)) + ','
            + JsonBool('loaded', ServerDoc <> Nil) + ','
            + JsonBool('front', EnsureSame(Front, P)) + ','
            + '"modified":' + BoolToJsonStr(Modified)
            + EdDocFields(P, Modified) + '}';
    End;
    Result := '[' + Items + ']';
End;

{ application.get_project_files (#428): every document of an OPEN project by }
{ full path (shared files outside its folder too), loaded or not, in front   }
{ or not, unsaved or not. A caller learns from it whether a file exists and  }
{ where, before it names one as a target.                                   }
Function App_GetProjectFiles(Params : String; RequestId : String) : String;
Var
    Prj, Why : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    Prj := Trim(ExtractJsonValue(Params, 'project_path'));
    Why := EnsurePathProblem(Prj, True);
    If Why <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_TARGET', 'project_path ' + Prj + ': '
            + Why + '. Name the project by its full absolute path.');
        Exit;
    End;
    Workspace := Nil;
    Try Workspace := GetWorkspace; Except Workspace := Nil; End;
    Project := Nil;
    If Workspace <> Nil Then Project := OpenProjectAt(Workspace, Prj);
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_OPEN', 'project ' + Prj
            + ' is not open (application.set_active_project opens it).');
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, '{' + JsonStr('project_path',
        Project.DM_ProjectFullPath) + ',"files":' + ProjectFilesJson(Project) + '}');
End;

{ application.set_active_project (#428): the project a session works in,     }
{ named by full path, opened when it is not, and its documents listed. A    }
{ member of a closed project opens as an orphan, so the project comes first. }
Function App_SetActiveProject(Params : String; RequestId : String) : String;
Var
    Prj, Why : String;
    Project : IProject;
    Opened : Boolean;
Begin
    Prj := Trim(ExtractJsonValue(Params, 'project_path'));
    Why := EnsurePathProblem(Prj, True);
    If Why <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_TARGET', 'project_path ' + Prj + ': '
            + Why + '. Name the project by its full absolute path.');
        Exit;
    End;
    If Not FileExists(Prj) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'TARGET_NOT_FOUND', 'project ' + Prj
            + ' does not exist.');
        Exit;
    End;
    Opened := Not EnsureProjectIsOpen(Prj);
    Project := EnsureProjectOpen(Prj);
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'TARGET_FAILED', 'project ' + Prj
            + ' did not open.');
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, '{' + JsonStr('project_path',
        Project.DM_ProjectFullPath) + ',' + JsonBool('opened', Opened)
        + ',"files":' + ProjectFilesJson(Project) + '}');
End;

{ application.set_active_document (#428): the document by its FULL path      }
{ only. With project_path the project is opened if needed, the document must }
{ be one of its documents, and it is opened inside it if it is not loaded.  }
{ It is then brought to the front, and the front is read back: anything     }
{ else in front is WRONG_DOCUMENT. The old bare-name match ("ends with", in  }
{ the focused project only) is gone: it could pick MyPCB.PcbDoc for         }
{ PCB.PcbDoc, or the same-named file of another project.                    }
Function App_SetActiveDocument(Params : String; RequestId : String) : String;
Var
    Doc, Prj, Why, Front : String;
    Project : IProject;
    ServerDoc : IServerDocument;
    Opened, ProjectOpened : Boolean;
Begin
    Doc := Trim(ExtractJsonValue(Params, 'file_path'));
    Prj := Trim(ExtractJsonValue(Params, 'project_path'));
    { A bare file name still names a document when exactly one open       }
    { document carries it; two files of that name are refused below.       }
    If (Doc <> '') And (Pos('\', Doc) = 0) And (Pos('/', Doc) = 0) Then
    Begin
        Front := ResolveLoadedDocPath(Doc);
        If Front = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_TARGET', 'file_path ' + Doc
                + ': no open document, or more than one, has that file name. Name it by'
                + ' its full absolute path (application.get_project_files lists a'
                + ' project''s documents).');
            Exit;
        End;
        Doc := Front;
    End;
    Why := EnsurePathProblem(Doc, False);
    If Why <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_TARGET', 'file_path ' + Doc + ': ' + Why
            + '. Name the document by its full absolute path (application.get_project_files'
            + ' lists a project''s documents).');
        Exit;
    End;
    Opened := False;
    ProjectOpened := False;
    Project := Nil;
    If Prj <> '' Then
    Begin
        Why := EnsurePathProblem(Prj, True);
        If Why <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_TARGET', 'project_path ' + Prj + ': '
                + Why + '. Name the project by its full absolute path.');
            Exit;
        End;
        ProjectOpened := Not EnsureProjectIsOpen(Prj);
        Project := EnsureProjectOpen(Prj);
        If Project = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'TARGET_FAILED', 'project ' + Prj
                + ' is not open and did not open.');
            Exit;
        End;
        If Not EnsureMember(Project, Doc) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_IN_PROJECT', Doc
                + ' is not a document of ' + Prj + ' (open projects listing it: '
                + ProjectsHoldingJson(Doc) + ').');
            Exit;
        End;
    End;
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(Doc); Except ServerDoc := Nil; End;
    If ServerDoc = Nil Then
    Begin
        If Project = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_LOADED', 'Document not loaded: '
                + Doc + '. Name its project (project_path) to have it opened inside it.');
            Exit;
        End;
        If Not FileExists(Doc) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'TARGET_NOT_FOUND', Doc
                + ' is listed by ' + Prj + ' but is not on disk.');
            Exit;
        End;
        ServerDoc := EnsureOpen(Doc);
        If ServerDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'TARGET_FAILED', Doc + ' did not open.');
            Exit;
        End;
        Opened := True;
    End;
    Try Client.ShowDocument(ServerDoc); Except End;
    Front := EnsureFrontPath(0);
    If Not EnsureSame(Front, Doc) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOCUMENT', Doc
            + ' could not be brought to the front (' + Front + ' is).');
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, '{"success":true,'
        + JsonStr('file_path', ServerDoc.FileName) + ',' + JsonStr('project_path', Prj) + ','
        + JsonBool('opened', Opened) + ',' + JsonBool('project_opened', ProjectOpened) + '}');
End;


{..............................................................................}
{ Create a new blank document of a given kind (PCB, SCH, PCBLIB, SCHLIB,       }
{ OUTPUTJOB, ...). Saves it to disk and optionally adds it to a project.       }
{ Uses IClient.OpenNewDocument, the documented API for this.                   }
{                                                                               }
{ Params: kind (required, e.g. 'PCB' or 'SCH'),                                }
{         file_path (required, absolute path where the doc should live),       }
{         name (optional display name, defaults to the filename),             }
{         add_to_project (optional bool, defaults to true),                   }
{         project_path (optional: the project to add it to, rather than       }
{         whichever project has focus)                                         }
{..............................................................................}

Function App_CreateDocument(Params : String; RequestId : String) : String;
Var
    FilePath, DocKind, DocName, AddStr, ProjectPath : String;
    ServerDoc : IServerDocument;
    Workspace : IWorkspace;
    Project : IProject;
    AddToProject, Saved, Added, Focused, ProjSaved : Boolean;
    AgeBefore : Integer;
Begin
    DocKind := ExtractJsonValue(Params, 'kind');
    FilePath := ExtractJsonValue(Params, 'file_path');
    DocName := ExtractJsonValue(Params, 'name');
    AddStr := ExtractJsonValue(Params, 'add_to_project');
    AddToProject := (AddStr = '') Or (AddStr = 'true');
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    { THE PROJECT IS NAMED OR IT IS THE FOCUSED ONE, decided before anything }
    { is created. A temporary sheet went into whichever project had focus,   }
    { a production project, while the caller meant another (issue #94).     }
    Project := Nil;
    If AddToProject Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            If ProjectPath <> '' Then
                Project := OpenProjectAt(Workspace, ProjectPath)
            Else
                Project := Workspace.DM_FocusedProject;
        End;
        If (Project = Nil) And (ProjectPath <> '') Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PROJECT',
                'No open project at ' + ProjectPath + '. Nothing was created.');
            Exit;
        End;
    End;

    If DocKind = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'kind is required (e.g. PCB, SCH, PCBLIB, SCHLIB)');
        Exit;
    End;
    If FilePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'file_path is required');
        Exit;
    End;
    If DocName = '' Then DocName := ExtractFileName(FilePath);

    { NO FOLDER, NO DOCUMENT. Saving into a folder that is not there put up }
    { Altium's "Save file error" dialog (AD 26.11), and the polling loop    }
    { waits on it until someone clicks OK.                                  }
    If Not DirectoryExists(ExtractFilePath(FilePath)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_SAVED',
            'Nothing was created: there is no folder ' + ExtractFilePath(FilePath));
        Exit;
    End;

    { A DOCUMENT ALREADY OPEN AT THIS PATH IS REFUSED. Creating a blank over }
    { it raised "object accessed after being destroyed" in ADVPCB and halted }
    { the script (a PcbLib, 2026-10-20). The caller wants the open one.      }
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(FilePath); Except ServerDoc := Nil; End;
    If ServerDoc <> Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'ALREADY_OPEN',
            'A document is already open at ' + FilePath +
            '. Focus it with app_set_active_document instead of creating it again,' +
            ' or close it first.');
        Exit;
    End;

    { Client.OpenNewDocument creates a blank in-memory IServerDocument of the
      given kind. Pass False for ReuseExisting so we don't accidentally grab
      a stale load of the same path. }
    ServerDoc := Client.OpenNewDocument(DocKind, FilePath, DocName, False);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
            'Client.OpenNewDocument returned Nil for kind=' + DocKind);
        Exit;
    End;

    { Persist to disk. For a brand-new in-memory doc Altium sometimes
      doesn't know the target path from OpenNewDocument's AFileName arg,
      so DoFileSave('') becomes a no-op. SetFileName forces the path;
      ensure it's set before the save. If DoFileSave fails for any
      reason, fall back to WorkspaceManager:SaveObject with an explicit
      FileName, that path is effectively Save-As, which is what we
      want for a previously-unsaved document. }
    { An old file at the path is overwritten, so only a new file time   }
    { shows that the save happened.                                     }
    AgeBefore := -1;
    If FileExists(FilePath) Then
        Try AgeBefore := FileAge(FilePath); Except AgeBefore := -1; End;
    Saved := False;
    Try ServerDoc.SetFileName(FilePath); Except { the file check below decides } End;
    Try
        ServerDoc.SetModified(True);
        ServerDoc.DoFileSave('');
        Saved := FileExists(FilePath) And (FileAge(FilePath) <> AgeBefore);
    Except Saved := False; End;
    If Not Saved Then
    Begin
        Try
            ServerDoc.Focus;
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', FilePath);
            RunProcess('WorkspaceManager:SaveObject');
            Saved := FileExists(FilePath) And (FileAge(FilePath) <> AgeBefore);
        Except Saved := False; End;
    End;

    { NOT SAVED IS NOT CREATED. Answering success with saved:false let the }
    { next call carry on as if the file existed. This comes before the     }
    { project add, so an unsaved document never joins a project.           }
    If Not Saved Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_SAVED',
            'The new document could not be saved to ' + FilePath);
        Exit;
    End;

    { Add to the focused project via IProject.DM_AddSourceDocument.            }
    { The earlier RunProcess('WorkspaceManager:AddDocumentToProject') path     }
    { silently no-ops in some workspace states, DM_AddSourceDocument is the  }
    { documented project-side API and the approach that works reliably        }
    { across workspace states.                                                }
    Added := False;
    ProjSaved := False;
    If AddToProject And (Project <> Nil) Then
    Begin
        Try
            Project.DM_AddSourceDocument(FilePath);
            Added := True;
        Except Added := False; End;
        { Written now, or the add is gone after a reopen. }
        If Added Then ProjSaved := SaveProjectFile(Project);
    End;

    { FOCUS THE NEW DOCUMENT. It stayed behind whatever was in front, and  }
    { the library and board tools act on the focused document, so the next }
    { authoring call wrote into the old one and reported success. Checked  }
    { by path afterwards, since a request to focus is not a focus.         }
    Try Client.ShowDocument(ServerDoc); Except End;
    Focused := UpperCase(CurrentFocusedDocPath(0)) = UpperCase(FilePath);

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"kind":"' + EscapeJsonString(DocKind) + '"' +
        ',"file_path":"' + EscapeJsonString(FilePath) + '"' +
        ',"saved":' + BoolToJsonStr(Saved) +
        ',"focused":' + BoolToJsonStr(Focused) +
        ',"added_to_project":' + BoolToJsonStr(Added) +
        ',"project_saved":' + BoolToJsonStr(ProjSaved) +
        ',"member_of":[' + ProjectsHoldingJson(FilePath) + ']}');
End;

{..............................................................................}
{ App_SaveDocument - save ONE open document: file_path, or the focused one.    }
{                                                                              }
{ app_save_all writes every open document, so one that cannot be written (a   }
{ sheet in a read-only folder) stops a save the caller never asked for. This  }
{ touches only the named document, never focuses anything and never walks the }
{ workspace. Whether it reached disk is measured by the file's age, because   }
{ DoFileSave does not raise when the editor declines, and Modified is         }
{ reported but not trusted, as App_SaveAll records.                           }
{..............................................................................}

Function App_SaveDocument(Params : String; RequestId : String) : String;
Var
    FilePath, Reason : String;
    ServerDoc : IServerDocument;
    WasModified, Saved : Boolean;
    AgeBefore, AgeAfter : Integer;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');
    If FilePath = '' Then FilePath := CurrentFocusedDocPath(0);
    If FilePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT',
            'No file_path given and no document is focused');
        Exit;
    End;
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(FilePath); Except End;
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_OPEN',
            FilePath + ' is not open in Altium, so it holds no unsaved edits');
        Exit;
    End;

    WasModified := False;
    Try WasModified := ServerDoc.Modified; Except End;
    { Edits made through the API leave Modified False; the bridge's own   }
    { record of them counts too, as it does when a document is closed.    }
    If DocWasEdited(FilePath) Then WasModified := True;
    AgeBefore := -1;
    Try If FileExists(FilePath) Then AgeBefore := FileAge(FilePath); Except End;
    Try ServerDoc.SetModified(True); Except { the file age below decides } End;
    Try ServerDoc.DoFileSave(''); Except { the file age below decides } End;
    AgeAfter := -1;
    Try If FileExists(FilePath) Then AgeAfter := FileAge(FilePath); Except End;
    Saved := (AgeAfter >= 0) And (AgeAfter > AgeBefore);
    { Written, so a later stop has nothing of this edit left to save. }
    If Saved Then ForgetDocEdited(FilePath);
    If Saved Then EdForget(FilePath, 'S');

    Reason := '';
    If Not Saved Then
        Reason := 'The file did not get newer. Altium declines a save while a '
            + 'command is active in the editor, which an abandoned edit can '
            + 'leave behind with nothing on screen, and a read-only folder '
            + 'refuses it. File ages tick every two seconds, so a save right '
            + 'after another can also read as not written: try again.';

    Result := BuildSuccessResponse(RequestId,
        '{"file_path":"' + EscapeJsonString(FilePath) + '"'
        + ',"saved":' + BoolToJsonStr(Saved)
        + ',"was_modified":' + BoolToJsonStr(WasModified)
        + ',"reason":"' + EscapeJsonString(Reason) + '"}');
End;

{..............................................................................}
{ Command Handler - must be at end so all functions are declared               }
{..............................................................................}

{ Stop the polling loop. Documents a tool edited, or that the editor marks  }
{ modified, are saved first; a clean document is not touched, so detaching  }
{ never rewrites a file nobody changed. app_save_all keeps its full flush.  }
Function App_StopServer(RequestId : String) : String;
Begin
    SaveScope := 'edited';
    CleanLeftAlone := 0;
    SaveAttempts := 0;
    Try SaveAllDirty(0); Except End;
    SaveScope := '';
    Running := False;
    Result := BuildSuccessResponse(RequestId, '{"stopped":true,"edited_saved":'
        + IntToStr(SaveAttempts) + ',"clean_left_alone":'
        + IntToStr(CleanLeftAlone) + '}');
End;

Function App_SaveAll(RequestId : String) : String;
Var
    StillDirty, Seen, Written, P : Integer;
    Paths, AgesBefore, AgesAfter, Skipped, Rest, Name : String;
Begin
    Try
        // Iterate every IServerDocument the editor has open and DoFileSave
        // each modified one. This bypasses WorkspaceManager:SaveAll, which
        // silently no-ops in some workspace states, and project-walk-based
        // saves, which skip free documents.
        { WHAT REACHED DISK, measured by file timestamp, because every
          Altium-side signal here has been observed lying. DoFileSave does
          not raise when the editor declines. ServerDoc.Modified does not
          always propagate from ProcessControl. And CountDirtyDocuments
          walks the workspace exactly as SaveAllDirty does, so an empty
          enumeration made both of them report zero and zero read as
          success while 29 edits were lost. }
        Paths := WorkspaceDocPaths(0);
        Seen := CountPathEntries(Paths);
        AgesBefore := AgesForPaths(Paths);

        SaveAttempts := 0;
        LibsSkippedClean := 0;
        SkippedLibNames := '';
        SaveAllDirty(0);

        { Libraries no tool edited are not written. Name them, so a caller
          who edited one by hand in Altium can see it was not saved here. }
        Skipped := '';
        Rest := SkippedLibNames;
        While Rest <> '' Do
        Begin
            P := Pos('|', Rest);
            If P > 0 Then
            Begin
                Name := Copy(Rest, 1, P - 1);
                Rest := Copy(Rest, P + 1, Length(Rest));
            End
            Else
            Begin
                Name := Rest;
                Rest := '';
            End;
            If Skipped <> '' Then Skipped := Skipped + ',';
            Skipped := Skipped + '"' + EscapeJsonString(Name) + '"';
        End;
        Skipped := ',"libraries_left_unedited":[' + Skipped + ']';
        If LibsSkippedClean > 0 Then
            Skipped := Skipped + ',"libraries_note":"These libraries were open '
                + 'but no tool edited them, so they were not written. Edits '
                + 'made by hand in Altium are not tracked: save those in '
                + 'Altium or with app_save_document."';

        AgesAfter := AgesForPaths(Paths);
        Written := CountChangedAges(AgesBefore, AgesAfter);
        StillDirty := CountDirtyDocuments(0);

        If Seen = 0 Then
            Result := BuildSuccessResponse(RequestId,
                '{"saved":false,"documents_seen":0,"documents_written":0'
                + ',"still_dirty":' + IntToStr(StillDirty)
                + ',"reason":"no documents were enumerated, so nothing was '
                + 'even attempted. This is NOT an empty-and-clean workspace: '
                + 'the walk that saves and the count that verifies share a '
                + 'workspace lookup, so when it comes back empty both report '
                + 'zero. Use proj_save, which resolves the focused project '
                + 'directly."}')
        Else If SaveAttempts = 0 Then
            Result := BuildSuccessResponse(RequestId,
                '{"saved":true,"documents_seen":' + IntToStr(Seen)
                + ',"documents_attempted":0,"documents_written":0'
                + ',"still_dirty":' + IntToStr(StillDirty) + Skipped
                + ',"note":"nothing needed saving: no edited document was open. '
                + 'Only a document open in the editor has an IServerDocument, '
                + 'libraries no tool edited are skipped, and the rest are project '
                + 'members that cannot be holding unsaved edits. Writing '
                + 'nothing is the correct outcome here, not a failure."}')
        Else If Written = 0 Then
            Result := BuildSuccessResponse(RequestId,
                '{"saved":false,"documents_seen":' + IntToStr(Seen)
                + ',"documents_attempted":' + IntToStr(SaveAttempts)
                + ',"documents_written":0'
                + ',"still_dirty":' + IntToStr(StillDirty) + Skipped
                + ',"reason":"every open document was written to and not one '
                + 'got newer on disk. Altium declines a save while a command '
                + 'is active in the editor, and an abandoned transaction '
                + 'leaves it in that state with nothing visible on screen. '
                + 'Unwind the active command and retry, or use proj_save."}')
        Else
            Result := BuildSuccessResponse(RequestId,
                '{"saved":true,"documents_seen":' + IntToStr(Seen)
                + ',"documents_attempted":' + IntToStr(SaveAttempts)
                + ',"documents_written":' + IntToStr(Written)
                + ',"still_dirty":' + IntToStr(StillDirty) + Skipped + '}');
    Except
        Result := BuildErrorResponse(RequestId, 'SAVE_FAILED', 'SaveAllDirty raised an exception');
    End;
End;

{..............................................................................}
{ Diagnostic: enumerate the workspace via FindFirst with the given pattern.    }
{ Reports the FindFirst return code, the count of matches, and the first few  }
{ filenames so we can confirm whether DelphiScript's directory enumeration    }
{ actually works with our request_*.json convention.                          }
{..............................................................................}

Function App_DiagWorkspace(Params : String; RequestId : String) : String;
Var
    Pattern, Names, RawPattern, ExceptionMsg : String;
    Files : TStringList;
    Count, I : Integer;
    First : Boolean;
Begin
    RawPattern := ExtractJsonValue(Params, 'pattern');
    If RawPattern = '' Then RawPattern := 'request_*.json';
    Pattern := RawPattern;

    Count := 0;
    Names := '';
    First := True;
    ExceptionMsg := '';

    Try
        Files := TStringList.Create;
        Try
            // FindFiles is the documented Altium DelphiScript helper for
            // enumerating files in a directory; FindFirst from SysUtils is
            // not exposed to scripts.
            // Signature: FindFiles(folder, pattern, attr, recurse, list)
            // attr=63 ($3F) is the standard "match anything" mask.
            FindFiles(WorkspaceDir, Pattern, 63, False, Files);
            Count := Files.Count;
            For I := 0 To Files.Count - 1 Do
            Begin
                If I >= 10 Then Break;
                If Not First Then Names := Names + ',';
                First := False;
                Names := Names + '"' + EscapeJsonString(ExtractFileName(Files[I])) + '"';
            End;
        Finally
            Files.Free;
        End;
    Except
        ExceptionMsg := 'FindFiles raised an exception';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"workspace_dir":"' + EscapeJsonString(WorkspaceDir) +
        '","pattern":"' + EscapeJsonString(Pattern) +
        '","function":"FindFiles' +
        '","match_count":' + IntToStr(Count) +
        ',"first_matches":[' + Names +
        '],"exception":"' + EscapeJsonString(ExceptionMsg) + '"}');
End;

{ App_ExitActiveCommand - close a transaction a CRASHED handler left open.    }
{                                                                             }
{ Altium refuses every save while a command is active, with "A command is     }
{ currently active and save cannot be completed at this time. Do you want to  }
{ save copy of current document?". That state lives in the PCB SERVER, not in }
{ the script, so RESTARTING THE POLLING LOOP DOES NOT CLEAR IT and neither    }
{ does Escape in the editor.                                                  }
{                                                                             }
{ MEASURED on 2026-08-25: saves were being refused on a BRAND NEW library      }
{ whose only operations were create, activate and create-symbol. The state was }
{ left earlier the same day by lib_link_3d_model faulting mid-handler on the   }
{ undeclared Body.Rotation, after PCBServer.PreProcess and before its          }
{ PostProcess. It then survived several script restarts.                       }
{                                                                             }
{ Every PreProcess in this codebase is paired, including on the error paths,   }
{ so this is not a leak here: it is recovery from a handler that DIED between  }
{ the two. AltiumScriptCentral ships the same one-line remedy as               }
{ ExitActiveCommand.vbs, and the note there names the cause as "a script which }
{ crashed before it could call PCBServer.PostProcess".                         }
{                                                                             }
{ Calling PostProcess with nothing outstanding is harmless, which is why the   }
{ reference script does exactly this and nothing else.                         }
Function App_ExitActiveCommand(RequestId : String) : String;
Var
    PcbOk, SchOk : Boolean;
    DirtyBefore, DirtyAfter, I : Integer;
Begin
    DirtyBefore := CountDirtyDocuments(0);

    { REPEATED, because PreProcess NESTS and one leak is not the only shape.  }
    { A single PostProcess was measured NOT to clear a real stuck state, and  }
    { the depth cannot be queried through the API, so the only way down is to }
    { unwind further than any plausible leak. Calling it with nothing         }
    { outstanding is harmless, which is the whole basis of the one-line       }
    { remedy in ExitActiveCommand.vbs.                                        }
    PcbOk := False;
    For I := 1 To 8 Do
        Try
            PCBServer.PostProcess;
            PcbOk := True;
        Except End;
    { Raw calls on purpose, past the PcbEndEdit count, which now matches. }
    PcbEditDepth := 0;
    SchEditOpen := '|';
    SchHeld := False;

    { The schematic server keeps its own transaction, and a SchLib handler    }
    { can die the same way, so close that too. Its PostProcess takes the      }
    { document, and Nil means "whatever is current".                          }
    SchOk := False;
    Try
        SchServer.ProcessControl.PostProcess(SchServer.GetCurrentSchDocument, '');
        SchOk := True;
    Except End;

    DirtyAfter := CountDirtyDocuments(0);

    Result := BuildSuccessResponse(RequestId,
        '{"pcb_post_process":' + BoolToJsonStr(PcbOk)
        + ',"sch_post_process":' + BoolToJsonStr(SchOk)
        + ',"dirty_before":' + IntToStr(DirtyBefore)
        + ',"dirty_after":' + IntToStr(DirtyAfter)
        + ',"note":"this closes a transaction left open by a handler that '
        + 'died between PreProcess and PostProcess. It does not itself save '
        + 'anything: call app_save_all afterwards and check still_dirty."}');
End;

{ App_Autosave - read, or set, Altium's auto-save (document recovery): on or  }
{ off and the interval in minutes. Both are read back. Altium keeps the      }
{ interval in milliseconds (measured on AD 26.11: 30 minutes reads 1800000). }
{ Params: enabled ('true' / 'false'; empty = keep), interval (minutes, 1 or  }
{ more; empty = keep).                                                       }
Function App_Autosave(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    EnStr, IntStr : String;
    Enabled, Changed : Boolean;
    Interval : Integer;
Begin
    EnStr := LowerCase(ExtractJsonValue(Params, 'enabled'));
    IntStr := ExtractJsonValue(Params, 'interval');
    If (IntStr <> '') And (Not IsIntStr(IntStr)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAM', 'interval must be a whole number of minutes');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    Enabled := Workspace.DM_GetRecoveryIsEnabled;
    Interval := Workspace.DM_GetRecoveryInterval;
    Changed := False;
    If EnStr <> '' Then
    Begin
        Enabled := EnStr = 'true';
        Changed := True;
    End;
    If IntStr <> '' Then
    Begin
        Interval := StrToInt(IntStr) * 60000;
        If Interval < 60000 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAM', 'interval is 1 minute or more');
            Exit;
        End;
        Changed := True;
    End;
    If Changed Then Workspace.DM_SetRecoveryParameters(Enabled, Interval);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonBool('changed', Changed) + ','
        + JsonBool('enabled', Workspace.DM_GetRecoveryIsEnabled) + ','
        + JsonInt('interval_minutes', Workspace.DM_GetRecoveryInterval Div 60000)));
End;

{ App_FormatValue - a component value in the RKM code of IEC 60062 (4K7,  }
{ 2u2, 10uF, 3V3; the Vf functions in Utils.pas). Pure string work: no     }
{ document is read or touched. action tostring: kind + value (a plain     }
{ decimal) -> text. fromstring: text (+ kind, decimal) -> value, kind,     }
{ rest, notes. cleanup: both, the text made proper. decimal is the SOURCE }
{ text's decimal separator, '.' or ','; empty = this machine's.          }
Function App_FormatValue(Params : String; RequestId : String) : String;
Var
    Action, Text, Value, Kind, Sep, Rec, Msg, Notes, Line, Arr, Body : String;
    P : Integer;
Begin
    Action := LowerCase(Trim(ExtractJsonValue(Params, 'action')));
    Text := ExtractJsonValue(Params, 'text');
    Value := ExtractJsonValue(Params, 'value');
    Kind := ExtractJsonValue(Params, 'kind');
    Sep := Trim(ExtractJsonValue(Params, 'decimal'));
    If (Sep <> '') And (Sep <> '.') And (Sep <> ',') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAM', 'decimal is ''.'' or '','': ' + Sep);
        Exit;
    End;
    If Action = 'tostring' Then
        Rec := VfToString(Kind, Value)
    Else If Action = 'fromstring' Then
        Rec := VfParse(Text, Kind, Sep)
    Else If Action = 'cleanup' Then
        Rec := VfCleanup(Text, Kind, Sep)
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAM',
            'action is tostring, fromstring or cleanup: ' + Action);
        Exit;
    End;
    If VfField(Rec, 0) <> 'OK' Then
    Begin
        Msg := VfField(Rec, 1);
        If Copy(Msg, 1, 12) = 'unknown kind' Then
            Result := BuildErrorResponse(RequestId, 'UNKNOWN_KIND', Msg)
        Else
            Result := BuildErrorResponse(RequestId, 'BAD_VALUE', Msg);
        Exit;
    End;
    If Action = 'tostring' Then
    Begin
        Result := BuildSuccessResponse(RequestId, JsonObj(JsonStr('text', VfField(Rec, 1))));
        Exit;
    End;
    { fromstring: kind value rest notes; cleanup: text kind value rest notes }
    P := 1;
    Body := '';
    If Action = 'cleanup' Then
    Begin
        Body := JsonStr('text', VfField(Rec, 1)) + ',';
        P := 2;
    End;
    If VfField(Rec, P) = '' Then Body := Body + JsonNull('kind') + ','
    Else Body := Body + JsonStr('kind', VfField(Rec, P)) + ',';
    Body := Body + JsonStr('value', VfField(Rec, P + 1)) + ',' + JsonStr('rest', VfField(Rec, P + 2));
    Notes := VfField(Rec, P + 3);
    Arr := '';
    While Notes <> '' Do
    Begin
        P := Pos(Chr(10), Notes);
        If P > 0 Then
        Begin
            Line := Copy(Notes, 1, P - 1);
            Notes := Copy(Notes, P + 1, Length(Notes));
        End
        Else
        Begin
            Line := Notes;
            Notes := '';
        End;
        If Arr <> '' Then Arr := Arr + ',';
        Arr := Arr + '"' + EscapeJsonString(Line) + '"';
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(Body + ',"notes":' + JsonArr(Arr)));
End;

{ Which session is editing which document (DocOwners in Main.pas), with how }
{ long ago it last wrote and whether that owner still holds it.             }
Function App_DocumentOwners(RequestId : String) : String;
Var
    Rest, Rec, Path, Items, Response : String;
    P, Q, Age : Integer;
Begin
    Items := '';
    Rest := DocOwners;
    P := Pos(Chr(1), Rest);
    While P > 0 Do
    Begin
        Rest := Copy(Rest, P + 1, Length(Rest));
        Q := Pos(Chr(2), Rest);
        If Q = 0 Then Break;
        Path := Copy(Rest, 1, Q - 1);
        Rec := OwnerRecord(Path);
        If Rec <> '' Then
        Begin
            Age := OwnerAgeMs(Rec);
            If Items <> '' Then Items := Items + ',';
            Items := Items + JsonObj(JsonStr('document', Path) + ','
                + JsonStr('session', OwnerField(Rec, 0)) + ','
                + JsonStr('last_command', OwnerField(Rec, 2)) + ','
                + JsonInt('seconds_since_write', Age Div 1000) + ','
                + JsonBool('held', Age < OWNER_IDLE_MS) + ','
                + JsonBool('yours', OwnerField(Rec, 0) = RequestTag(RequestId)));
        End;
        P := Pos(Chr(1), Rest);
    End;
    Response := BuildSuccessResponse(RequestId, JsonObj(
        JsonRaw('documents', '[' + Items + ']') + ','
        + JsonStr('session', RequestTag(RequestId)) + ','
        + JsonInt('idle_release_seconds', OWNER_IDLE_MS Div 1000)));
    Result := Response;
End;

{ Hand a document to the calling session, whoever held it. }
Function App_ClaimDocument(Params, RequestId : String) : String;
Var
    Path, Key, Rec, Before, Response : String;
Begin
    Path := ExtractJsonValue(Params, 'file_path');
    If Path = '' Then
    Begin
        Response := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'file_path is required');
        Result := Response;
        Exit;
    End;
    Key := OwnerKey(Path);
    Rec := OwnerRecord(Key);
    Before := '';
    If Rec <> '' Then Before := OwnerField(Rec, 0);
    OwnerSet(Key, RequestTag(RequestId), 'claimed');
    Response := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('document', Path) + ','
        + JsonStr('session', RequestTag(RequestId)) + ','
        + JsonStr('previous_owner', Before)));
    Result := Response;
End;

{ application.save_document_copy: Altium saves the document at file_path    }
{ under copy_path, as File > Save Copy As. An open document is saved as it  }
{ is in Altium, unsaved edits included, and keeps its own path and modified }
{ state; one that is not open is opened unfocused, saved and closed again. }
{ A copy_path open in Altium is refused. written is read from the copy's    }
{ file time, not assumed.                                                   }
Function App_SaveDocumentCopy(Params : String; RequestId : String) : String;
Var
    Path, CopyPath, Kind : String;
    Doc, Held : IServerDocument;
    WasOpen, WasModified, Written : Boolean;
    AgeBefore : Integer;
Begin
    Path := ExtractJsonValue(Params, 'file_path');
    CopyPath := ExtractJsonValue(Params, 'copy_path');
    Kind := ExtractJsonValue(Params, 'kind');
    If (Path = '') Or (CopyPath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'file_path and copy_path are required');
        Exit;
    End;
    If UpperCase(Path) = UpperCase(CopyPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAME_PATH', 'copy_path is the document itself');
        Exit;
    End;
    Held := Nil;
    Try Held := Client.GetDocumentByPath(CopyPath); Except Held := Nil; End;
    If Held <> Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'TARGET_OPEN',
            CopyPath + ' is open in Altium. Close it first: writing under an '
            + 'open document is how its edits get lost.');
        Exit;
    End;
    If SaveHeldBack(CopyPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAVING_OFF',
            'Saving is switched off on the bridge dashboard');
        Exit;
    End;
    Doc := Nil;
    Try Doc := Client.GetDocumentByPath(Path); Except Doc := Nil; End;
    WasOpen := Doc <> Nil;
    If Not WasOpen Then
    Begin
        If Not FileExists(Path) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'FILE_NOT_FOUND', 'No file at ' + Path);
            Exit;
        End;
        Try Doc := Client.OpenDocument(Kind, Path); Except Doc := Nil; End;
        If Doc = Nil Then
        Begin
            { The kind read from the file, as File > Open does. }
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', Path);
            RunProcess('WorkspaceManager:OpenObject');
            Try Doc := Client.GetDocumentByPath(Path); Except Doc := Nil; End;
        End;
    End;
    If Doc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_OPENED',
            'Altium could not open ' + Path + ' to save a copy of it');
        Exit;
    End;
    AgeBefore := -1;
    If FileExists(CopyPath) Then
    Begin
        Try AgeBefore := FileAge(CopyPath); Except AgeBefore := -1; End;
    End;
    WasModified := False;
    Try WasModified := Doc.Modified; Except End;
    { The rename lasts for the one save, and is put back in Finally. }
    Try
        Doc.SetFileName(CopyPath);
        Doc.SetModified(True);
        Doc.DoFileSave('');
    Finally
        Try Doc.SetFileName(Path); Except NoteWriteFailed('point the document back at its own file'); End;
        Try Doc.SetModified(WasModified); Except NoteWriteFailed('put the document''s modified flag back'); End;
        If Not WasOpen Then
        Begin
            Try Client.CloseDocument(Doc); Except End;
        End;
    End;
    Written := False;
    If FileExists(CopyPath) Then
    Begin
        Try Written := FileAge(CopyPath) <> AgeBefore; Except Written := False; End;
    End;
    Result := BuildSuccessResponse(RequestId, '{"file_path":"' + EscapeJsonString(Path) + '"'
        + ',"copy_path":"' + EscapeJsonString(CopyPath) + '"'
        + ',"was_open":' + BoolToJsonStr(WasOpen)
        + ',"written":' + BoolToJsonStr(Written) + '}');
End;

{ The key of the first owner record held by TAG, '' when it holds none. }
Function OwnerKeyHeldBy(Tag : String) : String;
Var
    Rest, Key, Rec : String;
    P, Q : Integer;
Begin
    Result := '';
    Rest := DocOwners;
    P := Pos(Chr(1), Rest);
    While P > 0 Do
    Begin
        Rest := Copy(Rest, P + 1, Length(Rest));
        Q := Pos(Chr(2), Rest);
        If Q = 0 Then Exit;
        Key := Copy(Rest, 1, Q - 1);
        Rec := OwnerRecord(Key);
        If (Rec <> '') And (OwnerField(Rec, 0) = Tag) Then
        Begin
            Result := Key;
            Exit;
        End;
        P := Pos(Chr(1), Rest);
    End;
End;

{ The calling session gives up a document it holds, so another session can }
{ write to it at once instead of after OWNER_IDLE_MS. Without               }
{ file_path every document it holds. A document another session holds is  }
{ refused, naming the holder: taking it is app_claim_document, on the      }
{ user's word.                                                             }
Function App_ReleaseDocument(Params, RequestId : String) : String;
Var
    Path, Key, Rec, Tag, Owner, Items : String;
    N : Integer;
Begin
    Tag := RequestTag(RequestId);
    Path := ExtractJsonValue(Params, 'file_path');
    If Path = '' Then
    Begin
        Items := '';
        N := 0;
        Key := OwnerKeyHeldBy(Tag);
        While (Key <> '') And (N < 10000) Do
        Begin
            OwnerDrop(Key);
            If Items <> '' Then Items := Items + ',';
            Items := Items + '"' + EscapeJsonString(Key) + '"';
            N := N + 1;
            Key := OwnerKeyHeldBy(Tag);
        End;
        Result := BuildSuccessResponse(RequestId, JsonObj(
            JsonRaw('released', '[' + Items + ']') + ','
            + JsonStr('session', Tag)));
        Exit;
    End;
    Key := OwnerKey(Path);
    Rec := OwnerRecord(Key);
    If Rec = '' Then
    Begin
        Result := BuildSuccessResponse(RequestId, JsonObj(
            JsonRaw('released', '[]') + ','
            + JsonStr('session', Tag) + ','
            + JsonStr('note', Path + ' was held by no session.')));
        Exit;
    End;
    Owner := OwnerField(Rec, 0);
    If Owner <> Tag Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_YOURS',
            Path + ' is held by another Claude session (' + Owner
            + '), not this one, so it was left alone. Taking it over is '
            + 'app_claim_document, when the user says so.');
        Exit;
    End;
    OwnerDrop(Key);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonRaw('released', '["' + EscapeJsonString(Key) + '"]') + ','
        + JsonStr('session', Tag)));
End;

{ One entry of the bridge's unsaved record (EdRecord) as JSON. }
Function EdEntryJson(Body : String) : String;
Var
    Path, Projects, List, One : String;
    I, N : Integer;
Begin
    Path := EdPart(Body, Chr(2), 0);
    Projects := EdPart(Body, Chr(2), 1);
    List := '';
    If Projects <> '' Then
    Begin
        N := EdFieldCount(Projects, '|');
        For I := 0 To N - 1 Do
        Begin
            One := EdPart(Projects, '|', I);
            If One <> '' Then
            Begin
                If List <> '' Then List := List + ',';
                List := List + '"' + EscapeJsonString(One) + '"';
            End;
        End;
    End;
    Result := JsonObj(JsonStr('file_path', Path) + ','
        + JsonRaw('projects', '[' + List + ']') + ','
        + JsonStr('last_edit', EdPart(Body, Chr(2), 2)) + ','
        + JsonStr('last_command', EdPart(Body, Chr(2), 4)) + ','
        + JsonStr('session', EdPart(Body, Chr(2), 5)) + ','
        + JsonInt('file_age_then', EdAgeOf(EdPart(Body, Chr(2), 3))) + ','
        + JsonInt('file_age_now', EdAgeNow(Path)) + ','
        + JsonBool('open', EdIsOpen(Path)) + ','
        + JsonStr('note', EdPart(Body, Chr(2), 7)));
End;

{ Whether an entry passes the filters: a file's key, a project's key. }
Function EdEntryWanted(Body : String; FileKey : String; ProjectKey : String) : Boolean;
Var
    Projects : String;
    I, N : Integer;
Begin
    Result := True;
    If (FileKey <> '') And (EdKey(EdPart(Body, Chr(2), 0)) <> FileKey) Then Result := False;
    If Result And (ProjectKey <> '') Then
    Begin
        Result := False;
        Projects := EdPart(Body, Chr(2), 1);
        N := EdFieldCount(Projects, '|');
        For I := 0 To N - 1 Do
            If EdKey(EdPart(Projects, '|', I)) = ProjectKey Then Result := True;
    End;
End;

{..............................................................................}
{ App_GetEditedDocuments - the bridge's record of unsaved documents (see      }
{ EdRecord in Main.pas). Params: file_path, project_path, both optional       }
{ filters by full path. documents = files holding edits that are not on disk, }
{ each with the projects holding it; two projects each with a PCB.PcbDoc are  }
{ two entries. lost = files edited and gone unsaved (closed without saving,   }
{ or not open when the record was rebuilt from its journal), kept until       }
{ acknowledge_lost. Read only.                                                }
{..............................................................................}
Function App_GetEditedDocuments(Params : String; RequestId : String) : String;
Var
    FileKey, ProjectKey, Key, Body, Docs, Lost : String;
    N : Integer;
Begin
    EdRefresh(0);
    FileKey := EdKey(ExtractJsonValue(Params, 'file_path'));
    ProjectKey := EdKey(ExtractJsonValue(Params, 'project_path'));
    Docs := '';
    Lost := '';
    N := 0;
    Key := EdKeyAt(EdRecord, 0);
    While Key <> '' Do
    Begin
        Body := EdBodyOf(EdRecord, Key);
        If EdEntryWanted(Body, FileKey, ProjectKey) Then
        Begin
            If EdPart(Body, Chr(2), 6) = 'E' Then
            Begin
                If Docs <> '' Then Docs := Docs + ',';
                Docs := Docs + EdEntryJson(Body);
            End
            Else
            Begin
                If Lost <> '' Then Lost := Lost + ',';
                Lost := Lost + EdEntryJson(Body);
            End;
        End;
        Inc(N);
        Key := EdKeyAt(EdRecord, N);
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonRaw('documents', '[' + Docs + ']') + ','
        + JsonRaw('lost', '[' + Lost + ']') + ','
        + JsonStr('journal', EdJournalPath(0)) + ','
        + JsonInt('journal_write_failures', EdJournalFailed)));
End;

{..............................................................................}
{ App_AcknowledgeLost - drop entries of the lost list (journal R). Params:     }
{ file_path, one file by full path; none drops them all. Unsaved entries are   }
{ not touched: they go when their file is saved.                              }
{..............................................................................}
Function App_AcknowledgeLost(Params : String; RequestId : String) : String;
Var
    FileKey, Snapshot, Key, Body, Done : String;
    N : Integer;
Begin
    EdRefresh(0);
    FileKey := EdKey(ExtractJsonValue(Params, 'file_path'));
    Done := '';
    Snapshot := EdRecord;
    N := 0;
    Key := EdKeyAt(Snapshot, 0);
    While Key <> '' Do
    Begin
        Body := EdBodyOf(Snapshot, Key);
        If (EdPart(Body, Chr(2), 6) = 'L') And ((FileKey = '') Or (Key = FileKey)) Then
        Begin
            EdRecord := EdWithout(EdRecord, Key);
            EdJournal('R', Body);
            If Done <> '' Then Done := Done + ',';
            Done := Done + '"' + EscapeJsonString(EdPart(Body, Chr(2), 0)) + '"';
        End;
        Inc(N);
        Key := EdKeyAt(Snapshot, N);
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonRaw('acknowledged', '[' + Done + ']')));
End;

Function HandleApplicationCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'ping':                Result := App_Ping(RequestId);
        'exit_active_command': Result := App_ExitActiveCommand(RequestId);
        'get_version':         Result := App_GetVersion(RequestId);
        'get_open_documents':  Result := App_GetOpenDocuments(RequestId);
        'get_active_document': Result := App_GetActiveDocument(RequestId);
        'set_active_document': Result := App_SetActiveDocument(Params, RequestId);
        'close_document':      Result := App_CloseDocument(Params, RequestId);
        'save_document_copy':  Result := App_SaveDocumentCopy(Params, RequestId);
        'run_process':        Result := App_RunProcess(Params, RequestId);
        'get_preferences':     Result := App_GetPreferences(RequestId);
        'execute_menu':        Result := App_ExecuteMenu(Params, RequestId);
        'get_clipboard_text':  Result := App_GetClipboardText(RequestId);
        'create_document':     Result := App_CreateDocument(Params, RequestId);
        'save_all':            Result := App_SaveAll(RequestId);
        'save_document':       Result := App_SaveDocument(Params, RequestId);
        'diag_workspace':      Result := App_DiagWorkspace(Params, RequestId);
        'stop_server':         Result := App_StopServer(RequestId);
        'autosave':            Result := App_Autosave(Params, RequestId);
        'format_value':        Result := App_FormatValue(Params, RequestId);
        'document_owners':     Result := App_DocumentOwners(RequestId);
        'claim_document':      Result := App_ClaimDocument(Params, RequestId);
        'release_document':    Result := App_ReleaseDocument(Params, RequestId);
        'get_edited_documents': Result := App_GetEditedDocuments(Params, RequestId);
        'acknowledge_lost':    Result := App_AcknowledgeLost(Params, RequestId);
        'get_project_files':   Result := App_GetProjectFiles(Params, RequestId);
        'set_active_project':  Result := App_SetActiveProject(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown application action: ' + Action);
    End;
End;

{ UnitStamp_Application - which build of Application.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_Application(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
