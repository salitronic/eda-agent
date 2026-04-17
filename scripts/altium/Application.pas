{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Application.pas - Application-level functions for the Altium integration bridge             }
{..............................................................................}

Function App_Ping(RequestId : String) : String;
Begin
    // Return the compiled-in SCRIPT_VERSION so Python can detect a stale
    // Altium script cache. The Altium string here comes from whatever was
    // compiled when the script project was last opened; the Python side
    // reads the on-disk version and warns on mismatch.
    Result := BuildSuccessResponse(RequestId,
        '{"pong":true,"script_version":"' + SCRIPT_VERSION + '"}');
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
    Data, DocInfo, FileName, FullPath, Kind, LoadedStr : String;
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

                        DocInfo := '{"file_name":"' + EscapeJsonString(ExtractFileName(FileName)) + '"';
                        DocInfo := DocInfo + ',"file_path":"' + EscapeJsonString(FullPath) + '"';
                        DocInfo := DocInfo + ',"document_kind":"' + EscapeJsonString(Kind) + '"';
                        DocInfo := DocInfo + ',"loaded":' + LoadedStr + '}';
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
    Data, FileName : String;
Begin
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Workspace.DM_FocusedDocument;
        If Doc <> Nil Then
        Begin
            FileName := Doc.DM_FileName;
            Data := '{"file_name":"' + EscapeJsonString(ExtractFileName(FileName)) + '"';
            Data := Data + ',"file_path":"' + EscapeJsonString(FileName) + '"';
            Data := Data + ',"document_kind":"' + EscapeJsonString(Doc.DM_DocumentKind) + '"}';
        End
        Else
            Data := '{}';
    End
    Else
        Data := '{}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

Function App_SetActiveDocument(Params : String; RequestId : String) : String;
Var
    FilePath : String;
    ServerDoc : IServerDocument;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');
    FilePath := StringReplace(FilePath, '\\', '\', -1);

    // Only switch focus to a document that is ALREADY loaded.
    // RunProcess('WorkspaceManager:OpenObject') would load it but strip
    // any project association, producing a "free document" in the UI
    // (tab title shows the full absolute path instead of filename).
    // Refuse the call if the doc isn't loaded — the caller must open
    // it in Altium first.
    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_LOADED',
            'Document not loaded: ' + FilePath +
            '. Open it in Altium first (File > Open or via the project tree).');
        Exit;
    End;

    // Make it the active/focused document.
    Client.ShowDocument(ServerDoc);

    Result := BuildSuccessResponse(RequestId, '{"success":true,"file_path":"' + EscapeJsonString(FilePath) + '"}');
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
        Board := PCBServer.GetCurrentPCBBoard;
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

    { Map common menu paths to their process equivalents }
    If MenuPath = 'File|Save All' Then
        ProcessName := 'WorkspaceManager:SaveAll'
    Else If MenuPath = 'Tools|Design Rule Check' Then
        ProcessName := 'PCB:RunDRC'
    Else If MenuPath = 'Tools|Electrical Rules Check' Then
        ProcessName := 'Sch:ERC'
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
        { For unknown paths, try Client.SendMessage with the menu path }
        Try
            Client.SendMessage('Client:RunMenu', 'MenuID=' + MenuPath, 1024, Nil);
            Result := BuildSuccessResponse(RequestId, '{"success":true,"menu_path":"' + EscapeJsonString(MenuPath) + '","method":"SendMessage"}');
            Exit;
        Except
            Result := BuildErrorResponse(RequestId, 'MENU_FAILED', 'Could not execute menu: ' + MenuPath + '. Use a known path or specify a process name via run_process instead.');
            Exit;
        End;
    End;

    ResetParameters;
    RunProcess(ProcessName);

    Result := BuildSuccessResponse(RequestId, '{"success":true,"menu_path":"' + EscapeJsonString(MenuPath) + '","process":"' + EscapeJsonString(ProcessName) + '"}');
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
{         name (optional display name — defaults to the filename),             }
{         add_to_project (optional bool — defaults to true)                    }
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
      FileName — that path is effectively Save-As, which is what we
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

    { Add to the focused project via WorkspaceManager:AddDocumentToProject.
      The process reads DocumentPath from parameters and attaches the file
      to whatever project is currently focused. }
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
                    ResetParameters;
                    AddStringParameter('DocumentPath', FilePath);
                    RunProcess('WorkspaceManager:AddDocumentToProject');
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

Function HandleApplicationCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'ping':                Result := App_Ping(RequestId);
        'get_version':         Result := App_GetVersion(RequestId);
        'get_open_documents':  Result := App_GetOpenDocuments(RequestId);
        'get_active_document': Result := App_GetActiveDocument(RequestId);
        'set_active_document': Result := App_SetActiveDocument(Params, RequestId);
        'run_process':         Result := App_RunProcess(Params, RequestId);
        'get_preferences':     Result := App_GetPreferences(RequestId);
        'execute_menu':        Result := App_ExecuteMenu(Params, RequestId);
        'get_clipboard_text':  Result := App_GetClipboardText(RequestId);
        'create_document':     Result := App_CreateDocument(Params, RequestId);
        'stop_server':         Begin Running := False; Result := BuildSuccessResponse(RequestId, '{"stopped":true}'); End;
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown application action: ' + Action);
    End;
End;
