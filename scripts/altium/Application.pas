{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Application.pas - Application-level functions for the Altium integration bridge             }
{..............................................................................}

Function App_Ping(RequestId : String) : String;
Var
    Ver : String;
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
    Result := BuildSuccessResponse(RequestId,
        '{"pong":true,"script_version":"' + SCRIPT_VERSION +
        '","altium_version":"' + EscapeJsonString(Ver) +
        '","protocol_version":' + IntToStr(PROTOCOL_VERSION) +
        ',"cast_errors":' + IntToStr(CastErrorCount) + '}');
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

Function App_GetOpenDocuments(RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I, J : Integer;
    Data, DocInfo, FileName, FullPath, Kind, LoadedStr, ModifiedStr : String;
    FirstItem, IsLoaded : Boolean;
Begin
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
                        DocInfo := DocInfo + ',"modified":' + ModifiedStr + '}';
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
    Data, FileName : String;
Begin
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
            Data := Data + ',"modified":' + BoolToJsonStr(DocIsModified(FileName)) + '}';
        End;
    End;

    // DM_FocusedDocument is UI-focus-dependent and returns Nil when no
    // editor window holds focus (e.g. right after a programmatic
    // create/place). Fall back to the SCH then PCB server module's
    // current document, which track the last-shown sheet/board
    // independent of workspace focus.
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
            Data := Data + ',"modified":' + BoolToJsonStr(DocIsModified(FileName)) + '}';
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
            Data := Data + ',"modified":' + BoolToJsonStr(DocIsModified(FileName)) + '}';
        End;
    End;

    If Data = '' Then Data := '{}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

Function App_SetActiveDocument(Params : String; RequestId : String) : String;
Var
    FilePath, LowerInput, LowerDocPath, FullPath : String;
    ServerDoc : IServerDocument;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');

    // Only switch focus to a document that is ALREADY loaded.
    // RunProcess('WorkspaceManager:OpenObject') would load it but strip
    // any project association, producing a "free document" in the UI
    // (tab title shows the full absolute path instead of filename).
    // Refuse the call if the doc isn't loaded, the caller must open
    // it in Altium first.
    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then
    Begin
        // Fallback: many callers (the dashboard sheet picker, the
        // project.get_documents list) only have the bare filename
        // (e.g. "ESP32.SchDoc"). GetDocumentByPath needs an exact full
        // path, so the first call returns Nil. Walk the focused
        // project's logical docs (Client.GetDocumentCount is undeclared
        // in DelphiScript -- the project DM enumeration is the right
        // way) and match by filename suffix to recover the full path.
        LowerInput := LowerCase(FilePath);
        Try
            Workspace := GetWorkspace;
            If Workspace <> Nil Then Project := Workspace.DM_FocusedProject;
        Except End;
        If Project <> Nil Then
        Begin
            For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
            Begin
                Try
                    Doc := Project.DM_LogicalDocuments(I);
                    If Doc = Nil Then Continue;
                    FullPath := '';
                    Try FullPath := Doc.DM_FullPath; Except FullPath := Doc.DM_FileName; End;
                    If FullPath = '' Then Continue;
                    LowerDocPath := LowerCase(FullPath);
                    // Exact match OR endswith match: "ESP32.SchDoc"
                    // should match "C:\path\to\ESP32.SchDoc".
                    If (LowerDocPath = LowerInput)
                        Or ((Length(LowerDocPath) > Length(LowerInput))
                            And (Copy(LowerDocPath,
                                      Length(LowerDocPath) - Length(LowerInput) + 1,
                                      Length(LowerInput)) = LowerInput)) Then
                    Begin
                        Try ServerDoc := Client.GetDocumentByPath(FullPath); Except End;
                        If ServerDoc <> Nil Then Break;
                    End;
                Except End;
            End;
        End;
    End;

    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_LOADED',
            'Document not loaded: ' + FilePath +
            '. Open it in Altium first (File > Open or via the project tree).');
        Exit;
    End;

    // Make it the active/focused document.
    Client.ShowDocument(ServerDoc);

    Result := BuildSuccessResponse(RequestId, '{"success":true,"file_path":"' + EscapeJsonString(ServerDoc.FileName) + '"}');
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
            Try Data := Data + '"snap_x_mils":' + IntToStr(CoordToMils(Board.SnapGridSizeX)); Except Data := Data + '"snap_x_mils":0'; End;
            Try Data := Data + ',"snap_y_mils":' + IntToStr(CoordToMils(Board.SnapGridSizeY)); Except Data := Data + ',"snap_y_mils":0'; End;
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
        ProcessName := 'Sch:DeSelectAll'
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

{..............................................................................}
{ Create a new blank document of a given kind (PCB, SCH, PCBLIB, SCHLIB,       }
{ OUTPUTJOB, ...). Saves it to disk and optionally adds it to the focused      }
{ project. Uses IClient.OpenNewDocument, the documented API for this.          }
{                                                                               }
{ Params: kind (required, e.g. 'PCB' or 'SCH'),                                }
{         file_path (required, absolute path where the doc should live),       }
{         name (optional display name, defaults to the filename),             }
{         add_to_project (optional bool, defaults to true)                    }
{..............................................................................}

Function App_CreateDocument(Params : String; RequestId : String) : String;
Var
    FilePath, DocKind, DocName, AddStr : String;
    ServerDoc : IServerDocument;
    Workspace : IWorkspace;
    Project : IProject;
    AddToProject, Saved, Added : Boolean;
Begin
    DocKind := ExtractJsonValue(Params, 'kind');
    FilePath := ExtractJsonValue(Params, 'file_path');
    DocName := ExtractJsonValue(Params, 'name');
    AddStr := ExtractJsonValue(Params, 'add_to_project');
    AddToProject := (AddStr = '') Or (AddStr = 'true');

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
    Saved := False;
    Try ServerDoc.SetFileName(FilePath); Except End;
    Try
        ServerDoc.SetModified(True);
        ServerDoc.DoFileSave('');
        Saved := FileExists(FilePath);
    Except Saved := False; End;
    If Not Saved Then
    Begin
        Try
            ServerDoc.Focus;
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', FilePath);
            RunProcess('WorkspaceManager:SaveObject');
            Saved := FileExists(FilePath);
        Except Saved := False; End;
    End;

    { Add to the focused project via IProject.DM_AddSourceDocument.            }
    { The earlier RunProcess('WorkspaceManager:AddDocumentToProject') path     }
    { silently no-ops in some workspace states, DM_AddSourceDocument is the  }
    { documented project-side API and the approach that works reliably        }
    { across workspace states.                                                }
    Added := False;
    If AddToProject Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Project := Workspace.DM_FocusedProject;
            If Project <> Nil Then
            Begin
                Try
                    Project.DM_AddSourceDocument(FilePath);
                    Added := True;
                Except Added := False; End;
            End;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"kind":"' + EscapeJsonString(DocKind) + '"' +
        ',"file_path":"' + EscapeJsonString(FilePath) + '"' +
        ',"saved":' + BoolToJsonStr(Saved) +
        ',"added_to_project":' + BoolToJsonStr(Added) + '}');
End;

{..............................................................................}
{ Command Handler - must be at end so all functions are declared               }
{..............................................................................}

Function App_SaveAll(RequestId : String) : String;
Var
    StillDirty, Seen, Written : Integer;
    Paths, AgesBefore, AgesAfter : String;
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
        SaveAllDirty(0);

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
                + ',"still_dirty":' + IntToStr(StillDirty)
                + ',"note":"nothing was open to save. Only a document open '
                + 'in the editor has an IServerDocument; the rest are project '
                + 'members that cannot be holding unsaved edits. Writing '
                + 'nothing is the correct outcome here, not a failure."}')
        Else If Written = 0 Then
            Result := BuildSuccessResponse(RequestId,
                '{"saved":false,"documents_seen":' + IntToStr(Seen)
                + ',"documents_attempted":' + IntToStr(SaveAttempts)
                + ',"documents_written":0'
                + ',"still_dirty":' + IntToStr(StillDirty)
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
                + ',"still_dirty":' + IntToStr(StillDirty) + '}');
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

Function HandleApplicationCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'ping':                Result := App_Ping(RequestId);
        'exit_active_command': Result := App_ExitActiveCommand(RequestId);
        'get_version':         Result := App_GetVersion(RequestId);
        'get_open_documents':  Result := App_GetOpenDocuments(RequestId);
        'get_active_document': Result := App_GetActiveDocument(RequestId);
        'set_active_document': Result := App_SetActiveDocument(Params, RequestId);
        'run_process':         Result := App_RunProcess(Params, RequestId);
        'get_preferences':     Result := App_GetPreferences(RequestId);
        'execute_menu':        Result := App_ExecuteMenu(Params, RequestId);
        'get_clipboard_text':  Result := App_GetClipboardText(RequestId);
        'create_document':     Result := App_CreateDocument(Params, RequestId);
        'save_all':            Result := App_SaveAll(RequestId);
        'diag_workspace':      Result := App_DiagWorkspace(Params, RequestId);
        'stop_server':         Begin SaveAllDirty(0); Running := False; Result := BuildSuccessResponse(RequestId, '{"stopped":true}'); End;
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown application action: ' + Action);
    End;
End;
