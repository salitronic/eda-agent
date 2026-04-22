{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Project.pas - Project management functions for the Altium integration bridge                }
{..............................................................................}

Function FindProjectByPath(Workspace : IWorkspace; ProjectPath : String) : IProject;
Var
    I : Integer;
    Proj : IProject;
Begin
    Result := Nil;
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Proj := Workspace.DM_Projects(I);
        If Proj <> Nil Then
        Begin
            If Proj.DM_ProjectFullPath = ProjectPath Then
            Begin
                Result := Proj;
                Exit;
            End;
        End;
    End;
End;

Function Proj_Create(Params : String; RequestId : String) : String;
Var
    ProjectPath, ProjectType : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    ProjectType := ExtractJsonValue(Params, 'project_type');

    If ProjectType = '' Then ProjectType := 'PCB';

    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', ProjectPath);
    RunProcess('WorkspaceManager:OpenObject');

    Result := BuildSuccessResponse(RequestId, '{"success":true,"project_path":"' + EscapeJsonString(ProjectPath) + '"}');
End;

Function Proj_Open(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', ProjectPath);
    RunProcess('WorkspaceManager:OpenObject');

    Result := BuildSuccessResponse(RequestId, '{"success":true}');
End;

Function Proj_Save(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            RunProcess('WorkspaceManager:SaveAll');
            Result := BuildSuccessResponse(RequestId, '{"success":true}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

Function Proj_Close(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    SaveFirst : Boolean;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    SaveFirst := ExtractJsonValue(Params, 'save') <> 'false';

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            ProjectPath := Project.DM_ProjectFullPath;
            If SaveFirst Then
                RunProcess('WorkspaceManager:SaveAll');

            ResetParameters;
            AddStringParameter('ObjectKind', 'Project');
            AddStringParameter('FileName', ProjectPath);
            RunProcess('WorkspaceManager:CloseObject');
            Result := BuildSuccessResponse(RequestId, '{"success":true}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

Function Proj_GetDocuments(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
    Data, DocInfo : String;
    First : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            Data := '[';
            First := True;
            For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
            Begin
                Doc := Project.DM_LogicalDocuments(I);
                If Doc = Nil Then Continue;
                If Not First Then Data := Data + ',';
                First := False;
                DocInfo := '{"file_name":"' + EscapeJsonString(ExtractFileName(Doc.DM_FileName)) + '"';
                DocInfo := DocInfo + ',"file_path":"' + EscapeJsonString(Doc.DM_FileName) + '"';
                DocInfo := DocInfo + ',"document_kind":"' + EscapeJsonString(Doc.DM_DocumentKind) + '"}';
                Data := Data + DocInfo;
            End;
            Data := Data + ']';
            Result := BuildSuccessResponse(RequestId, Data);
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

Function Proj_AddDocument(Params : String; RequestId : String) : String;
Var
    ProjectPath, DocumentPath : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    DocumentPath := ExtractJsonValue(Params, 'document_path');
    DocumentPath := StringReplace(DocumentPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            Project.DM_AddSourceDocument(DocumentPath);
            Result := BuildSuccessResponse(RequestId, '{"success":true}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

Function Proj_RemoveDocument(Params : String; RequestId : String) : String;
Var
    ProjectPath, DocumentPath : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    DocumentPath := ExtractJsonValue(Params, 'document_path');
    DocumentPath := StringReplace(DocumentPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', DocumentPath);
            RunProcess('WorkspaceManager:CloseObject');
            Result := BuildSuccessResponse(RequestId, '{"success":true}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

Function Proj_GetParameters(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Param : IParameter;
    I : Integer;
    Data, ParamInfo : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            Data := '[';
            For I := 0 To Project.DM_ParameterCount - 1 Do
            Begin
                Param := Project.DM_Parameters(I);
                If I > 0 Then Data := Data + ',';
                ParamInfo := '{"name":"' + EscapeJsonString(Param.DM_Name) + '","value":"' + EscapeJsonString(Param.DM_Value) + '"}';
                Data := Data + ParamInfo;
            End;
            Data := Data + ']';
            Result := BuildSuccessResponse(RequestId, Data);
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

Function Proj_SetParameter(Params : String; RequestId : String) : String;
Var
    ProjectPath, ParamName, ParamValue : String;
    Workspace : IWorkspace;
    Project : IProject;
    Param : IParameter;
    I : Integer;
    Found : Boolean;
Begin
    ParamName := ExtractJsonValue(Params, 'name');
    ParamValue := ExtractJsonValue(Params, 'value');
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    If ParamName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    If ProjectPath <> '' Then
        Project := FindProjectByPath(Workspace, ProjectPath)
    Else
        Project := Workspace.DM_FocusedProject;

    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'No project found');
        Exit;
    End;

    ProjectPath := Project.DM_ProjectFullPath;

    { Try to find and update existing parameter }
    Found := False;
    For I := 0 To Project.DM_ParameterCount - 1 Do
    Begin
        Param := Project.DM_Parameters(I);
        If Param.DM_Name = ParamName Then
        Begin
            Param.DM_Value := ParamValue;
            Found := True;
            Break;
        End;
    End;

    { If not found, add via RunProcess }
    If Not Found Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Project');
        AddStringParameter('Name', ParamName);
        AddStringParameter('Value', ParamValue);
        RunProcess('WorkspaceManager:DocumentAddParameter');
    End;

    { Save the project to persist changes }
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', ProjectPath);
    RunProcess('WorkspaceManager:SaveObject');

    Result := BuildSuccessResponse(RequestId, '{"success":true,"name":"' + EscapeJsonString(ParamName) + '","value":"' + EscapeJsonString(ParamValue) + '","project_path":"' + EscapeJsonString(ProjectPath) + '"}');
End;

Function Proj_Compile(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            { Explicit user-requested compile: invalidate cache then recompile. }
            LastCompileTick := 0;
            SmartCompile(Project);
            Result := BuildSuccessResponse(RequestId, '{"success":true}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

Function Proj_GetFocused(RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Data : String;
Begin
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Project := Workspace.DM_FocusedProject;
        If Project <> Nil Then
        Begin
            Data := '{"project_name":"' + EscapeJsonString(Project.DM_ProjectFileName) + '"';
            Data := Data + ',"project_path":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"';
            Data := Data + ',"document_count":' + IntToStr(Project.DM_LogicalDocumentCount) + '}';
            Result := BuildSuccessResponse(RequestId, Data);
        End
        Else
            Result := BuildSuccessResponse(RequestId, '{}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

{..............................................................................}
{ Get net-to-pin connectivity from compiled project                           }
{ Params: project_path, component, net_name, limit                            }
{..............................................................................}

Function Proj_GetNets(Params : String; RequestId : String) : String;
Var
    ProjectPath, FilterComp, FilterNet : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, Count, Limit : Integer;
    Data, CompDesig, NetName : String;
    First : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    FilterComp := ExtractJsonValue(Params, 'component');
    FilterNet := ExtractJsonValue(Params, 'net_name');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 500);

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    If ProjectPath <> '' Then
        Project := FindProjectByPath(Workspace, ProjectPath)
    Else
        Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found');
        Exit;
    End;

    // Compile to resolve net connectivity
    SmartCompile(Project);

    Data := '[';
    First := True;
    Count := 0;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        If Count >= Limit Then Break;
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            If Count >= Limit Then Break;
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;

            CompDesig := Comp.DM_PhysicalDesignator;
            If (FilterComp <> '') And (CompDesig <> FilterComp) Then Continue;

            For K := 0 To Comp.DM_PinCount - 1 Do
            Begin
                If Count >= Limit Then Break;
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;

                NetName := Pin.DM_FlattenedNetName;
                If (FilterNet <> '') And (NetName <> FilterNet) Then Continue;

                If Not First Then Data := Data + ',';
                First := False;

                Data := Data + '{"component":"' + EscapeJsonString(CompDesig) + '"';
                Data := Data + ',"pin":"' + EscapeJsonString(Pin.DM_PinNumber) + '"';
                Data := Data + ',"pin_name":"' + EscapeJsonString(Pin.DM_PinName) + '"';
                Data := Data + ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(Count);
            End;
        End;
    End;

    Data := Data + ']';
    Result := BuildSuccessResponse(RequestId, '{"pins":' + Data + ',"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ BOM export from compiled project                                           }
{..............................................................................}

Function Proj_GetBOM(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, Count, Limit : Integer;
    Data, CompDesig, CompComment, CompFP, CompLib, PinList : String;
    First, FirstPin : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 1000);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    Data := '[';
    First := True;
    Count := 0;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        If Count >= Limit Then Break;
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            If Count >= Limit Then Break;
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;

            CompDesig := Comp.DM_PhysicalDesignator;
            CompComment := Comp.DM_Comment;
            CompFP := Comp.DM_Footprint;
            CompLib := Comp.DM_LibraryReference;

            // Build pin-net list
            PinList := '';
            FirstPin := True;
            For K := 0 To Comp.DM_PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;
                If Not FirstPin Then PinList := PinList + ',';
                FirstPin := False;
                PinList := PinList + '{"pin":"' + EscapeJsonString(Pin.DM_PinNumber) +
                    '","name":"' + EscapeJsonString(Pin.DM_PinName) +
                    '","net":"' + EscapeJsonString(Pin.DM_FlattenedNetName) + '"}';
            End;

            If Not First Then Data := Data + ',';
            First := False;
            Data := Data + '{"designator":"' + EscapeJsonString(CompDesig) + '"';
            Data := Data + ',"comment":"' + EscapeJsonString(CompComment) + '"';
            Data := Data + ',"footprint":"' + EscapeJsonString(CompFP) + '"';
            Data := Data + ',"lib_ref":"' + EscapeJsonString(CompLib) + '"';
            Data := Data + ',"pins":[' + PinList + ']}';
            Inc(Count);
        End;
    End;

    Data := Data + ']';
    Result := BuildSuccessResponse(RequestId, '{"components":' + Data + ',"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ Get full info for a single component (params + nets in one call)           }
{..............................................................................}

Function Proj_GetComponentInfo(Params : String; RequestId : String) : String;
Var
    ProjectPath, Designator : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K : Integer;
    Data, PinList, ParamList : String;
    FirstPin, FirstParam : Boolean;
    Found : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    Designator := ExtractJsonValue(Params, 'designator');

    If Designator = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required'); Exit; End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);
    Found := False;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        If Found Then Break;
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;
            If Comp.DM_PhysicalDesignator <> Designator Then Continue;

            Found := True;

            // Build pin list with nets
            PinList := '';
            FirstPin := True;
            For K := 0 To Comp.DM_PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;
                If Not FirstPin Then PinList := PinList + ',';
                FirstPin := False;
                PinList := PinList + '{"pin":"' + EscapeJsonString(Pin.DM_PinNumber) +
                    '","name":"' + EscapeJsonString(Pin.DM_PinName) +
                    '","net":"' + EscapeJsonString(Pin.DM_FlattenedNetName) + '"}';
            End;

            // Build parameter list
            ParamList := '';
            FirstParam := True;
            Try
                For K := 0 To Comp.DM_ParameterCount - 1 Do
                Begin
                    If Not FirstParam Then ParamList := ParamList + ',';
                    FirstParam := False;
                    ParamList := ParamList + '"' + EscapeJsonString(Comp.DM_Parameters(K).DM_Name) +
                        '":"' + EscapeJsonString(Comp.DM_Parameters(K).DM_Value) + '"';
                End;
            Except
            End;

            Data := '{"designator":"' + EscapeJsonString(Designator) + '"';
            Data := Data + ',"comment":"' + EscapeJsonString(Comp.DM_Comment) + '"';
            Data := Data + ',"footprint":"' + EscapeJsonString(Comp.DM_Footprint) + '"';
            Data := Data + ',"lib_ref":"' + EscapeJsonString(Comp.DM_LibraryReference) + '"';
            Data := Data + ',"sheet":"' + EscapeJsonString(Doc.DM_FileName) + '"';
            Data := Data + ',"parameters":{' + ParamList + '}';
            Data := Data + ',"pins":[' + PinList + ']}';

            Result := BuildSuccessResponse(RequestId, Data);
            Exit;
        End;
    End;

    If Not Found Then
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
End;

{..............................................................................}
{ Export active schematic or PCB to PDF                                       }
{..............................................................................}

Function Proj_ExportPDF(Params : String; RequestId : String) : String;
Var
    OutputPath : String;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');
    OutputPath := StringReplace(OutputPath, '\\', '\', -1);

    If OutputPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'output_path is required');
        Exit;
    End;

    ResetParameters;
    AddStringParameter('FileName', OutputPath);
    RunProcess('WorkspaceManager:Print');

    Result := BuildSuccessResponse(RequestId, '{"success":true,"output_path":"' + EscapeJsonString(OutputPath) + '"}');
End;

{..............................................................................}
{ Cross-probe: zoom to a component by designator                             }
{..............................................................................}

Function Proj_CrossProbe(Params : String; RequestId : String) : String;
Var
    Designator, Target : String;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    Target := ExtractJsonValue(Params, 'target');
    If Target = '' Then Target := 'schematic';

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required');
        Exit;
    End;

    If Target = 'pcb' Then
    Begin
        ResetParameters;
        AddStringParameter('Action', 'JumpToComponent');
        AddStringParameter('Reference', Designator);
        RunProcess('PCB:RunGotoJumpDialog');
    End
    Else
    Begin
        ResetParameters;
        AddStringParameter('Object', 'Designator');
        AddStringParameter('Text', Designator);
        RunProcess('Sch:Find');
    End;

    Result := BuildSuccessResponse(RequestId, '{"success":true,"designator":"' + EscapeJsonString(Designator) + '","target":"' + Target + '"}');
End;

{..............................................................................}
{ Design statistics from compiled project                                    }
{..............................................................................}

Function Proj_GetDesignStats(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    I, J : Integer;
    CompCount, PinCount, DocCount : Integer;
    Data : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    CompCount := 0; PinCount := 0; DocCount := 0;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        Inc(DocCount);
        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;
            Inc(CompCount);
            PinCount := PinCount + Comp.DM_PinCount;
        End;
    End;

    Data := '{"sheets":' + IntToStr(DocCount);
    Data := Data + ',"components":' + IntToStr(CompCount);
    Data := Data + ',"pins":' + IntToStr(PinCount) + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ PCB board info — outline, layer stack, origin                              }
{..............................................................................}

Function Proj_GetBoardInfo(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    I, PtCount : Integer;
    OutlineStr, LayerStr, Data : String;
    First : Boolean;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active'); Exit; End;

    // Board outline vertices
    OutlineStr := '[';
    First := True;
    Try
        PtCount := Board.BoardOutline.PointCount;
        For I := 0 To PtCount - 1 Do
        Begin
            If Not First Then OutlineStr := OutlineStr + ',';
            First := False;
            OutlineStr := OutlineStr + '{"x":' + IntToStr(CoordToMils(Board.BoardOutline.Segments[I].vx)) +
                ',"y":' + IntToStr(CoordToMils(Board.BoardOutline.Segments[I].vy)) + '}';
        End;
    Except
    End;
    OutlineStr := OutlineStr + ']';

    // Active layers from layer stack
    LayerStr := '[';
    First := True;
    Try
        LayerStack := Board.LayerStack_V7;
        If LayerStack <> Nil Then
        Begin
            LayerObj := LayerStack.FirstLayer;
            While LayerObj <> Nil Do
            Begin
                Try
                    If Board.LayerIsUsed[LayerObj.LayerID] Then
                    Begin
                        If Not First Then LayerStr := LayerStr + ',';
                        First := False;
                        LayerStr := LayerStr + '"' + EscapeJsonString(LayerObj.Name) + '"';
                    End;
                Except
                    // LayerID access may fail on some layer types
                End;
                LayerObj := LayerStack.NextLayer(LayerObj);
            End;
        End;
    Except
    End;
    LayerStr := LayerStr + ']';

    Data := '{"origin_x":' + IntToStr(CoordToMils(Board.XOrigin));
    Data := Data + ',"origin_y":' + IntToStr(CoordToMils(Board.YOrigin));
    Data := Data + ',"outline":' + OutlineStr;
    Data := Data + ',"layers":' + LayerStr + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Annotate schematic designators — programmatic, no dialog                    }
{                                                                              }
{ Strategy:                                                                    }
{ - For each SCH doc in the focused/specified project, iterate components.    }
{ - Extract the alpha prefix from each component's current designator         }
{   (e.g., "R?" or "R13" -> "R"). If empty (just "?" or ""), fall back to     }
{   "U" as a generic prefix.                                                  }
{ - Skip components whose Designator.IsLocked is True.                        }
{ - Group components by prefix across the whole project, sort them by the    }
{   requested order using their (DocIndex, X, Y) tuple, then assign           }
{   sequential numbers starting at 1 per prefix.                              }
{ - Sort order values match Altium's Annotate dialog:                         }
{     down_then_across  = sort by X ascending, then Y descending              }
{     up_then_across    = sort by X ascending, then Y ascending               }
{     across_then_down  = sort by Y descending, then X ascending              }
{     across_then_up    = sort by Y ascending,  then X ascending              }
{     none              = reset all to "<prefix>?"                            }
{ - Wrap each doc in SchServer.ProcessControl.PreProcess/PostProcess for      }
{   undo support, then GraphicallyInvalidate.                                 }
{..............................................................................}

{ Helper: extract alpha prefix from a designator like "R13" -> "R", "U?" -> "U" }
Function ExtractDesignatorPrefix(Des : String) : String;
Var
    I : Integer;
    C : Char;
Begin
    Result := '';
    For I := 1 To Length(Des) Do
    Begin
        C := Des[I];
        If ((C >= 'A') And (C <= 'Z')) Or ((C >= 'a') And (C <= 'z')) Then
            Result := Result + C
        Else
            Break;
    End;
    If Result = '' Then Result := 'U';
End;

{ Helper: compare two component entries by the requested annotation order.
  Returns -1 if A should come before B, +1 if after, 0 if equal.
  Each "entry" is encoded as a flat string "X|Y|DocIdx|CompIdx" where X and Y
  are integer mils (padded to a consistent width to allow lexical sort-safe
  decoding). We pass them as separate integer params to keep it simple. }
Function CompareAnnotationOrder(Order : String;
    AX, AY, ADocIdx : Integer;
    BX, BY, BDocIdx : Integer) : Integer;
Begin
    Result := 0;
    { Doc index is the primary tie-breaker — keep designators contiguous per sheet }
    If ADocIdx < BDocIdx Then Begin Result := -1; Exit; End;
    If ADocIdx > BDocIdx Then Begin Result :=  1; Exit; End;

    If Order = 'down_then_across' Then
    Begin
        { Row-major, top-to-bottom: primary Y descending, secondary X ascending }
        If AY > BY Then Result := -1
        Else If AY < BY Then Result := 1
        Else If AX < BX Then Result := -1
        Else If AX > BX Then Result := 1;
    End
    Else If Order = 'up_then_across' Then
    Begin
        { Row-major, bottom-to-top: primary Y ascending, secondary X ascending }
        If AY < BY Then Result := -1
        Else If AY > BY Then Result := 1
        Else If AX < BX Then Result := -1
        Else If AX > BX Then Result := 1;
    End
    Else If Order = 'across_then_down' Then
    Begin
        { Column-major, left-to-right then top-to-bottom:
          primary X ascending, secondary Y descending }
        If AX < BX Then Result := -1
        Else If AX > BX Then Result := 1
        Else If AY > BY Then Result := -1
        Else If AY < BY Then Result := 1;
    End
    Else If Order = 'across_then_up' Then
    Begin
        { Column-major, left-to-right then bottom-to-top:
          primary X ascending, secondary Y ascending }
        If AX < BX Then Result := -1
        Else If AX > BX Then Result := 1
        Else If AY < BY Then Result := -1
        Else If AY > BY Then Result := 1;
    End;
End;

Function Proj_Annotate(Params : String; RequestId : String) : String;
Var
    Order, ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    I, J, DocCount, Total : Integer;
    RenameCount, ResetCount, SkipCount, ProcessedDocs : Integer;
    FilePath : String;

    { Flat parallel arrays — one slot per unlocked, considered component.
      Interfaces go in a TInterfaceList; sort keys go in parallel TStringList
      (DelphiScript-friendly approach — TStringList.Objects[] with interface
      pointers is unreliable). }
    CompList   : TInterfaceList;
    Prefixes   : TStringList;
    XCoords    : TStringList;  { X in mils as integer-string }
    YCoords    : TStringList;  { Y in mils as integer-string }
    DocIndices : TStringList;

    { Set of modified docs — PreProcess/PostProcess/Invalidate are scoped to these only }
    TouchedDocs : TStringList;

    { Per-prefix counter for final assignment — stored as "Prefix=N" lines }
    PrefixCounters : TStringList;
    PrefixIdx, CounterVal : Integer;
    N : Integer;

    NewDesText, TmpPrefix, TmpStr : String;
    AX, AY, BX, BY, ADoc, BDoc : Integer;
    ShouldSwap : Boolean;
    TmpObj : ISch_Component;
Begin
    Order := ExtractJsonValue(Params, 'order');
    If Order = '' Then Order := 'down_then_across';
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    If SchServer = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCH_SERVER', 'Schematic server is not available');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    CompList       := TInterfaceList.Create;
    Prefixes       := TStringList.Create;
    XCoords        := TStringList.Create;
    YCoords        := TStringList.Create;
    DocIndices     := TStringList.Create;
    TouchedDocs    := TStringList.Create;
    PrefixCounters := TStringList.Create;

    RenameCount := 0;
    ResetCount  := 0;
    SkipCount   := 0;
    ProcessedDocs := 0;

    Try
        DocCount := Project.DM_LogicalDocumentCount;

        { ---------- Pass 1: open every SCH doc, collect components ---------- }
        For I := 0 To DocCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            If Doc.DM_DocumentKind <> 'SCH' Then Continue;

            FilePath := Doc.DM_FullPath;

            { Don't force-open — RunProcess Client:OpenDocument strips
              project association and creates a free document in the UI.
              Skip sheets that aren't currently loaded. }
            SchDoc := SchServer.GetSchDocumentByPath(FilePath);
            If SchDoc = Nil Then Continue;

            Inc(ProcessedDocs);
            TouchedDocs.Add(FilePath);

            { -------- Order = 'none': just reset designators to "<prefix>?" -------- }
            If Order = 'none' Then
            Begin
                SchServer.ProcessControl.PreProcess(SchDoc, '');
                Iterator := SchDoc.SchIterator_Create;
                Try
                    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
                    Obj := Iterator.FirstSchObject;
                    While Obj <> Nil Do
                    Begin
                        Try
                            Comp := Obj;
                            If Not Comp.Designator.IsLocked Then
                            Begin
                                SchBeginModify(Comp);
                                Comp.Designator.Text := ExtractDesignatorPrefix(Comp.Designator.Text) + '?';
                                SchEndModify(Comp);
                                Inc(ResetCount);
                            End
                            Else
                                Inc(SkipCount);
                        Except
                        End;
                        Obj := Iterator.NextSchObject;
                    End;
                Finally
                    SchDoc.SchIterator_Destroy(Iterator);
                End;
                SchServer.ProcessControl.PostProcess(SchDoc, '');
                SchDoc.GraphicallyInvalidate;
                SaveDocByPath(FilePath);
                Continue;
            End;

            { -------- Normal annotation: collect unlocked components -------- }
            Iterator := SchDoc.SchIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iterator.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Try
                        Comp := Obj;
                        If Comp.Designator.IsLocked Then
                        Begin
                            Inc(SkipCount);
                        End
                        Else
                        Begin
                            CompList.Add(Comp);
                            Prefixes.Add(ExtractDesignatorPrefix(Comp.Designator.Text));
                            XCoords.Add(IntToStr(CoordToMils(Comp.Location.X)));
                            YCoords.Add(IntToStr(CoordToMils(Comp.Location.Y)));
                            DocIndices.Add(IntToStr(I));
                        End;
                    Except
                    End;
                    Obj := Iterator.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iterator);
            End;
        End;

        { 'none' mode: skip annotation entirely }
        If Order = 'none' Then
        Begin
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"order":"none","reset":' + IntToStr(ResetCount) +
                ',"skipped_locked":' + IntToStr(SkipCount) +
                ',"documents_processed":' + IntToStr(ProcessedDocs) +
                ',"programmatic":true}');
            Exit;
        End;

        { ---------- Pass 2: bubble-sort parallel arrays + CompList by Order ---------- }
        Total := CompList.Count;
        For I := 0 To Total - 2 Do
        Begin
            For J := 0 To Total - 2 - I Do
            Begin
                AX := StrToIntDef(XCoords[J], 0);
                AY := StrToIntDef(YCoords[J], 0);
                ADoc := StrToIntDef(DocIndices[J], 0);
                BX := StrToIntDef(XCoords[J+1], 0);
                BY := StrToIntDef(YCoords[J+1], 0);
                BDoc := StrToIntDef(DocIndices[J+1], 0);

                ShouldSwap := CompareAnnotationOrder(Order, AX, AY, ADoc, BX, BY, BDoc) > 0;

                If ShouldSwap Then
                Begin
                    { Swap interface entry }
                    TmpObj := CompList.Items[J];
                    CompList.Items[J]   := CompList.Items[J+1];
                    CompList.Items[J+1] := TmpObj;

                    { Swap string entries in lockstep }
                    TmpPrefix := Prefixes[J];     Prefixes[J]   := Prefixes[J+1];   Prefixes[J+1]   := TmpPrefix;
                    TmpStr    := XCoords[J];      XCoords[J]    := XCoords[J+1];    XCoords[J+1]    := TmpStr;
                    TmpStr    := YCoords[J];      YCoords[J]    := YCoords[J+1];    YCoords[J+1]    := TmpStr;
                    TmpStr    := DocIndices[J];   DocIndices[J] := DocIndices[J+1]; DocIndices[J+1] := TmpStr;
                End;
            End;
        End;

        { ---------- Pass 3: PreProcess every touched doc, assign designators ---------- }
        For I := 0 To TouchedDocs.Count - 1 Do
        Begin
            SchDoc := SchServer.GetSchDocumentByPath(TouchedDocs[I]);
            If SchDoc <> Nil Then
                SchServer.ProcessControl.PreProcess(SchDoc, '');
        End;

        For I := 0 To Total - 1 Do
        Begin
            TmpPrefix := Prefixes[I];
            PrefixIdx := -1;
            For J := 0 To PrefixCounters.Count - 1 Do
            Begin
                TmpStr := PrefixCounters[J];
                N := Pos('=', TmpStr);
                If (N > 0) And (Copy(TmpStr, 1, N-1) = TmpPrefix) Then
                Begin
                    PrefixIdx := J;
                    Break;
                End;
            End;

            If PrefixIdx < 0 Then
            Begin
                CounterVal := 1;
                PrefixCounters.Add(TmpPrefix + '=1');
            End
            Else
            Begin
                TmpStr := PrefixCounters[PrefixIdx];
                N := Pos('=', TmpStr);
                CounterVal := StrToIntDef(Copy(TmpStr, N+1, Length(TmpStr)), 0) + 1;
                PrefixCounters[PrefixIdx] := TmpPrefix + '=' + IntToStr(CounterVal);
            End;

            NewDesText := TmpPrefix + IntToStr(CounterVal);
            Try
                Comp := CompList.Items[I];
                If Comp <> Nil Then
                Begin
                    SchBeginModify(Comp);
                    Comp.Designator.Text := NewDesText;
                    SchEndModify(Comp);
                    Inc(RenameCount);
                End;
            Except
            End;
        End;

        { PostProcess + Invalidate + Save every touched doc }
        For I := 0 To TouchedDocs.Count - 1 Do
        Begin
            SchDoc := SchServer.GetSchDocumentByPath(TouchedDocs[I]);
            If SchDoc <> Nil Then
            Begin
                SchServer.ProcessControl.PostProcess(SchDoc, '');
                SchDoc.GraphicallyInvalidate;
            End;
            SaveDocByPath(TouchedDocs[I]);
        End;

    Finally
        { TInterfaceList owns its interface references — Free to release them }
        CompList.Free;
        Prefixes.Free;
        XCoords.Free;
        YCoords.Free;
        DocIndices.Free;
        TouchedDocs.Free;
        PrefixCounters.Free;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"order":"' + EscapeJsonString(Order) + '"' +
        ',"renamed":' + IntToStr(RenameCount) +
        ',"skipped_locked":' + IntToStr(SkipCount) +
        ',"documents_processed":' + IntToStr(ProcessedDocs) +
        ',"programmatic":true}');
End;

{..............................................................................}
{ Generate manufacturing outputs from PCB                                    }
{..............................................................................}

Function Proj_GenerateOutput(Params : String; RequestId : String) : String;
Var
    OutputType, OutputPath : String;
Begin
    OutputType := ExtractJsonValue(Params, 'output_type');
    OutputPath := ExtractJsonValue(Params, 'output_path');
    OutputPath := StringReplace(OutputPath, '\\', '\', -1);

    If OutputType = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'output_type is required'); Exit; End;

    If OutputType = 'gerber' Then
    Begin
        ResetParameters;
        If OutputPath <> '' Then AddStringParameter('OutputPath', OutputPath);
        RunProcess('PCB:GenericExport');
    End
    Else If OutputType = 'drill' Then
    Begin
        ResetParameters;
        If OutputPath <> '' Then AddStringParameter('OutputPath', OutputPath);
        RunProcess('PCB:ExportDrill');
    End
    Else If OutputType = 'pick_place' Then
    Begin
        ResetParameters;
        If OutputPath <> '' Then AddStringParameter('FileName', OutputPath);
        RunProcess('PCB:ExportPickAndPlace');
    End
    Else If OutputType = 'ipc_netlist' Then
    Begin
        ResetParameters;
        RunProcess('PCB:ExportIPC356Netlist');
    End
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', 'Unknown output type: ' + OutputType + '. Use: gerber, drill, pick_place, ipc_netlist');
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId, '{"generated":true,"output_type":"' + OutputType + '"}');
End;

{..............................................................................}
{ Export PCB to STEP 3D model                                                 }
{ Params: output_path (optional — if omitted, Altium may prompt)              }
{..............................................................................}

Function Proj_ExportSTEP(Params : String; RequestId : String) : String;
Var
    OutputPath : String;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');
    OutputPath := StringReplace(OutputPath, '\\', '\', -1);

    ResetParameters;
    If OutputPath <> '' Then
        AddStringParameter('FileName', OutputPath);
    RunProcess('PCB:ExportSTEP3D');

    If OutputPath <> '' Then
        Result := BuildSuccessResponse(RequestId, '{"success":true,"output_path":"' + EscapeJsonString(OutputPath) + '"}')
    Else
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
End;

{..............................................................................}
{ Export PCB to DXF/AutoCAD format                                            }
{ Params: output_path (optional)                                              }
{..............................................................................}

Function Proj_ExportDXF(Params : String; RequestId : String) : String;
Var
    OutputPath : String;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');
    OutputPath := StringReplace(OutputPath, '\\', '\', -1);

    ResetParameters;
    If OutputPath <> '' Then
        AddStringParameter('FileName', OutputPath);
    RunProcess('PCB:ExportToAutoCAD');

    If OutputPath <> '' Then
        Result := BuildSuccessResponse(RequestId, '{"success":true,"output_path":"' + EscapeJsonString(OutputPath) + '"}')
    Else
        Result := BuildSuccessResponse(RequestId, '{"success":true}');
End;

{..............................................................................}
{ Export current view as image                                                 }
{ Params: output_path, format (png/jpg/bmp), width, height                    }
{..............................................................................}

Function Proj_ExportImage(Params : String; RequestId : String) : String;
Var
    OutputPath, Fmt : String;
    Width, Height : Integer;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');
    OutputPath := StringReplace(OutputPath, '\\', '\', -1);
    Fmt := ExtractJsonValue(Params, 'format');
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 1920);
    Height := StrToIntDef(ExtractJsonValue(Params, 'height'), 1080);

    If OutputPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'output_path is required');
        Exit;
    End;

    If Fmt = '' Then Fmt := 'png';

    ResetParameters;
    AddStringParameter('FileName', OutputPath);
    AddStringParameter('ImageFormat', Fmt);
    AddIntegerParameter('ImageWidth', Width);
    AddIntegerParameter('ImageHeight', Height);
    RunProcess('WorkspaceManager:Print');

    Result := BuildSuccessResponse(RequestId, '{"success":true,"output_path":"' + EscapeJsonString(OutputPath) + '","format":"' + Fmt + '","width":' + IntToStr(Width) + ',"height":' + IntToStr(Height) + '}');
End;

{..............................................................................}
{ List output containers from an open .OutJob document                        }
{ The OutJob file is an INI format — parse sections for containers.           }
{ Params: outjob_path (optional — uses first open OutJob if omitted)          }
{..............................................................................}

Function Proj_GetOutJobContainers(Params : String; RequestId : String) : String;
Var
    OutJobPath, S : String;
    IniFile : TIniFile;
    ContainerName, ContainerType : String;
    G, J : Integer;
    Data : String;
    First : Boolean;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
Begin
    OutJobPath := ExtractJsonValue(Params, 'outjob_path');
    OutJobPath := StringReplace(OutJobPath, '\\', '\', -1);

    { If no path given, find first OutJob in the focused project }
    If OutJobPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Project := Workspace.DM_FocusedProject;
            If Project <> Nil Then
            Begin
                For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    Doc := Project.DM_LogicalDocuments(I);
                    If Doc <> Nil Then
                        If Doc.DM_DocumentKind = 'OUTPUTJOB' Then
                        Begin
                            OutJobPath := Doc.DM_FullPath;
                            Break;
                        End;
                End;
            End;
        End;
    End;

    If OutJobPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_OUTJOB', 'No OutJob document found. Provide outjob_path or ensure one is in the project.');
        Exit;
    End;

    If Not FileExists(OutJobPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FILE_NOT_FOUND', 'OutJob file not found: ' + OutJobPath);
        Exit;
    End;

    { OutJob files are INI format — parse OutputGroup sections }
    Data := '[';
    First := True;
    IniFile := TIniFile.Create(OutJobPath);
    Try
        G := 1;
        While True Do
        Begin
            S := 'OutputGroup' + IntToStr(G);
            J := 1;
            ContainerName := IniFile.ReadString(S, 'OutputMedium1', '');
            If ContainerName = '' Then Break;  { no more groups }

            While True Do
            Begin
                ContainerName := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J), '');
                If ContainerName = '' Then Break;

                ContainerType := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J) + '_Type', '');

                If Not First Then Data := Data + ',';
                First := False;
                Data := Data + '{"name":"' + EscapeJsonString(ContainerName) + '"';
                Data := Data + ',"type":"' + EscapeJsonString(ContainerType) + '"';
                Data := Data + ',"group":' + IntToStr(G) + '}';

                Inc(J);
            End;
            Inc(G);
        End;
    Finally
        IniFile.Free;
    End;
    Data := Data + ']';

    Result := BuildSuccessResponse(RequestId, '{"outjob_path":"' + EscapeJsonString(OutJobPath) + '","containers":' + Data + '}');
End;

{..............................................................................}
{ Execute a specific OutJob container by name                                  }
{ Params: outjob_path (optional), container_name                              }
{..............................................................................}

Function Proj_RunOutJob(Params : String; RequestId : String) : String;
Var
    OutJobPath, ContainerName, S : String;
    IniFile : TIniFile;
    FoundContainerName, ContainerType, RelativePath : String;
    G, J : Integer;
    Found : Boolean;
    OutJobDoc : IServerDocument;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
Begin
    OutJobPath := ExtractJsonValue(Params, 'outjob_path');
    OutJobPath := StringReplace(OutJobPath, '\\', '\', -1);
    ContainerName := ExtractJsonValue(Params, 'container_name');

    If ContainerName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'container_name is required');
        Exit;
    End;

    { If no path given, find first OutJob in the focused project }
    If OutJobPath = '' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
        Begin
            Project := Workspace.DM_FocusedProject;
            If Project <> Nil Then
            Begin
                For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    Doc := Project.DM_LogicalDocuments(I);
                    If Doc <> Nil Then
                        If Doc.DM_DocumentKind = 'OUTPUTJOB' Then
                        Begin
                            OutJobPath := Doc.DM_FullPath;
                            Break;
                        End;
                End;
            End;
        End;
    End;

    If OutJobPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_OUTJOB', 'No OutJob document found');
        Exit;
    End;

    { Open/focus the OutJob document }
    Try
        If Not Client.IsDocumentOpen(OutJobPath) Then
        Begin
            OutJobDoc := Client.OpenDocument('OUTPUTJOB', OutJobPath);
            If OutJobDoc <> Nil Then OutJobDoc.Focus;
        End
        Else
        Begin
            OutJobDoc := Client.GetDocumentByPath(OutJobPath);
            If OutJobDoc <> Nil Then OutJobDoc.Focus;
        End;
    Except
    End;

    { Parse the INI to find the container and its type }
    Found := False;
    ContainerType := '';
    RelativePath := '';
    IniFile := TIniFile.Create(OutJobPath);
    Try
        G := 1;
        While Not Found Do
        Begin
            S := 'OutputGroup' + IntToStr(G);
            J := 1;
            FoundContainerName := IniFile.ReadString(S, 'OutputMedium1', '');
            If FoundContainerName = '' Then Break;

            While True Do
            Begin
                FoundContainerName := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J), '');
                If FoundContainerName = '' Then Break;

                If FoundContainerName = ContainerName Then
                Begin
                    ContainerType := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J) + '_Type', '');
                    RelativePath := IniFile.ReadString('PublishSettings', 'OutputBasePath' + IntToStr(J), '');
                    Found := True;
                    Break;
                End;
                Inc(J);
            End;
            Inc(G);
        End;
    Finally
        IniFile.Free;
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CONTAINER_NOT_FOUND', 'Container not found: ' + ContainerName);
        Exit;
    End;

    { Execute based on container type }
    If ContainerType = 'Publish' Then
    Begin
        ResetParameters;
        AddStringParameter('Action', 'PublishToPDF');
        AddStringParameter('OutputMedium', ContainerName);
        AddStringParameter('ObjectKind', 'OutputBatch');
        If RelativePath <> '' Then AddStringParameter('OutputBasePath', RelativePath);
        AddStringParameter('DisableDialog', 'True');
        RunProcess('WorkspaceManager:Print');
    End
    Else
    Begin
        { Default: GeneratedFiles and others use GenerateReport }
        ResetParameters;
        AddStringParameter('Action', 'Run');
        AddStringParameter('OutputMedium', ContainerName);
        AddStringParameter('ObjectKind', 'OutputBatch');
        If RelativePath <> '' Then AddStringParameter('OutputBasePath', RelativePath);
        RunProcess('WorkspaceManager:GenerateReport');
    End;

    Result := BuildSuccessResponse(RequestId, '{"success":true,"container_name":"' + EscapeJsonString(ContainerName) + '","container_type":"' + EscapeJsonString(ContainerType) + '"}');
End;

{..............................................................................}
{ List all project variants                                                    }
{ Params: project_path (optional)                                             }
{..............................................................................}

Function Proj_GetVariants(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant : IProjectVariant;
    CompVar : IComponentVariation;
    ParamVar : IParameterVariation;
    I, J, K : Integer;
    Data, VarInfo, CompInfo, ParamInfo : String;
    First, FirstComp, FirstParam : Boolean;
    KindStr : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    Data := '[';
    First := True;
    For I := 0 To Project.DM_ProjectVariantCount - 1 Do
    Begin
        Variant := Project.DM_ProjectVariants(I);
        If Variant = Nil Then Continue;

        If Not First Then Data := Data + ',';
        First := False;

        VarInfo := '{"name":"' + EscapeJsonString(Variant.DM_Name) + '"';
        VarInfo := VarInfo + ',"description":"' + EscapeJsonString(Variant.DM_Description) + '"';

        { Component variations }
        VarInfo := VarInfo + ',"variations":[';
        FirstComp := True;
        For J := 0 To Variant.DM_VariationCount - 1 Do
        Begin
            CompVar := Variant.DM_Variations(J);
            If CompVar = Nil Then Continue;

            If Not FirstComp Then VarInfo := VarInfo + ',';
            FirstComp := False;

            { Translate variation kind to string (If/Else chain — Case on enum crashes DelphiScript) }
            If CompVar.DM_VariationKind = 0 Then
                KindStr := 'Fitted'
            Else If CompVar.DM_VariationKind = 1 Then
                KindStr := 'Not Fitted'
            Else If CompVar.DM_VariationKind = 2 Then
                KindStr := 'Alternate'
            Else
                KindStr := 'Unknown(' + IntToStr(CompVar.DM_VariationKind) + ')';

            CompInfo := '{"designator":"' + EscapeJsonString(CompVar.DM_PhysicalDesignator) + '"';
            CompInfo := CompInfo + ',"kind":"' + KindStr + '"';
            CompInfo := CompInfo + ',"alternate_part":"' + EscapeJsonString(CompVar.DM_AlternatePart) + '"';

            { Parameter variations within this component }
            CompInfo := CompInfo + ',"parameters":[';
            FirstParam := True;
            Try
                For K := 0 To CompVar.DM_VariationCount - 1 Do
                Begin
                    ParamVar := CompVar.DM_Variations(K);
                    If ParamVar = Nil Then Continue;
                    If Not FirstParam Then CompInfo := CompInfo + ',';
                    FirstParam := False;
                    ParamInfo := '{"name":"' + EscapeJsonString(ParamVar.DM_ParameterName) + '"';
                    ParamInfo := ParamInfo + ',"value":"' + EscapeJsonString(ParamVar.DM_VariedValue) + '"}';
                    CompInfo := CompInfo + ParamInfo;
                End;
            Except
            End;
            CompInfo := CompInfo + ']}';

            VarInfo := VarInfo + CompInfo;
        End;
        VarInfo := VarInfo + ']}';

        Data := Data + VarInfo;
    End;
    Data := Data + ']';

    Result := BuildSuccessResponse(RequestId, '{"variants":' + Data + ',"count":' + IntToStr(Project.DM_ProjectVariantCount) + '}');
End;

{..............................................................................}
{ Get the currently active project variant                                     }
{ Params: project_path (optional)                                             }
{..............................................................................}

Function Proj_GetActiveVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant : IProjectVariant;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    Try
        Variant := Project.DM_CurrentProjectVariant;
        If Variant <> Nil Then
            Result := BuildSuccessResponse(RequestId, '{"name":"' + EscapeJsonString(Variant.DM_Name) + '","description":"' + EscapeJsonString(Variant.DM_Description) + '"}')
        Else
            Result := BuildSuccessResponse(RequestId, '{"name":"[No Variations]","description":"Base design, no variant active"}');
    Except
        Result := BuildSuccessResponse(RequestId, '{"name":"[No Variations]","description":"Base design, no variant active"}');
    End;
End;

{..............................................................................}
{ Switch active variant by name                                                }
{ Params: variant_name, project_path (optional)                               }
{..............................................................................}

Function Proj_SetActiveVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath, VariantName : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant : IProjectVariant;
    I : Integer;
    Found : Boolean;
Begin
    VariantName := ExtractJsonValue(Params, 'variant_name');
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    If VariantName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'variant_name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    { Verify variant exists }
    Found := False;
    For I := 0 To Project.DM_ProjectVariantCount - 1 Do
    Begin
        Variant := Project.DM_ProjectVariants(I);
        If (Variant <> Nil) And (Variant.DM_Name = VariantName) Then
        Begin
            Found := True;
            Break;
        End;
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'VARIANT_NOT_FOUND', 'Variant not found: ' + VariantName);
        Exit;
    End;

    { Use RunProcess to switch variant via project options }
    ResetParameters;
    AddStringParameter('Action', 'SetCurrentVariant');
    AddStringParameter('VariantName', VariantName);
    RunProcess('WorkspaceManager:VariantManagement');

    Result := BuildSuccessResponse(RequestId, '{"success":true,"variant_name":"' + EscapeJsonString(VariantName) + '"}');
End;

{..............................................................................}
{ Create a new project variant                                                 }
{ Params: name, description (optional), project_path (optional)               }
{..............................................................................}

Function Proj_CreateVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath, VarName, VarDesc : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    VarName := ExtractJsonValue(Params, 'name');
    VarDesc := ExtractJsonValue(Params, 'description');
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    If VarName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'name is required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    { Create variant via RunProcess }
    ResetParameters;
    AddStringParameter('Action', 'AddVariant');
    AddStringParameter('VariantName', VarName);
    If VarDesc <> '' Then
        AddStringParameter('VariantDescription', VarDesc);
    RunProcess('WorkspaceManager:VariantManagement');

    Result := BuildSuccessResponse(RequestId, '{"success":true,"name":"' + EscapeJsonString(VarName) + '","description":"' + EscapeJsonString(VarDesc) + '"}');
End;

{..............................................................................}
{ List all currently open projects in the workspace                            }
{..............................................................................}

Function Proj_GetOpenProjects(RequestId : String) : String;
Var
    Workspace : IWorkspace;
    I : Integer;
    Data : String;
    First : Boolean;
    Proj : IProject;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    { List all currently open projects in the workspace }
    Data := '[';
    First := True;
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Proj := Workspace.DM_Projects(I);
        If Proj = Nil Then Continue;

        If Not First Then Data := Data + ',';
        First := False;
        Data := Data + '{"project_name":"' + EscapeJsonString(Proj.DM_ProjectFileName) + '"';
        Data := Data + ',"project_path":"' + EscapeJsonString(Proj.DM_ProjectFullPath) + '"';
        Data := Data + ',"document_count":' + IntToStr(Proj.DM_LogicalDocumentCount) + '}';
    End;
    Data := Data + ']';

    Result := BuildSuccessResponse(RequestId, '{"projects":' + Data + ',"count":' + IntToStr(Workspace.DM_ProjectCount) + '}');
End;

{..............................................................................}
{ Save all open documents                                                      }
{..............................................................................}

Function Proj_SaveAll(RequestId : String) : String;
Begin
    RunProcess('WorkspaceManager:SaveAll');
    Result := BuildSuccessResponse(RequestId, '{"success":true}');
End;

{..............................................................................}
{ Get messages from the Messages panel (compile errors, ERC, etc.)            }
{ Uses DM_ViolationCount on the compiled project.                             }
{..............................................................................}

Function Proj_GetMessages(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Violation : IViolation;
    I, Count : Integer;
    Data, Msg, Src : String;
    First : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    { Compile to populate violations }
    SmartCompile(Project);

    Data := '[';
    First := True;
    Count := 0;

    Try
        For I := 0 To Project.DM_ViolationCount - 1 Do
        Begin
            Violation := Project.DM_Violations(I);
            If Violation = Nil Then Continue;

            If Not First Then Data := Data + ',';
            First := False;

            { Severity is deliberately omitted: DM_ErrorLevelString is a
              compile-time undeclared identifier in DelphiScript, and
              DM_ErrorLevel hasn't been confirmed as declared either. Use
              DM_ShortDescriptorString (documented on IDMObject base) rather
              than the undocumented DM_DescriptorString. DM_OwnerDocumentName
              is documented on IDMObject and is safe. }
            Msg := '';
            Try Msg := Violation.DM_ShortDescriptorString; Except Msg := ''; End;

            Src := '';
            Try Src := Violation.DM_OwnerDocumentName; Except Src := ''; End;

            Data := Data + '{"message":"' + EscapeJsonString(Msg) + '"';
            Data := Data + ',"source":"' + EscapeJsonString(Src) + '"';
            Data := Data + '}';
            Inc(Count);
        End;
    Except
    End;

    Data := Data + ']';
    Result := BuildSuccessResponse(RequestId, '{"messages":' + Data + ',"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ Find a component across all project sheets by designator, value, or comment }
{ Params: search_text, search_by (designator/value/comment)                   }
{..............................................................................}

Function Proj_FindComponent(Params : String; RequestId : String) : String;
Var
    ProjectPath, SearchText, SearchBy : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    I, J, Count : Integer;
    Data, MatchValue : String;
    First : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    SearchText := ExtractJsonValue(Params, 'search_text');
    SearchBy := ExtractJsonValue(Params, 'search_by');

    If SearchText = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'search_text is required'); Exit; End;
    If SearchBy = '' Then SearchBy := 'designator';

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    Data := '[';
    First := True;
    Count := 0;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;

            { Select which property to match }
            If SearchBy = 'value' Then
                MatchValue := Comp.DM_Comment
            Else If SearchBy = 'comment' Then
                MatchValue := Comp.DM_Comment
            Else
                MatchValue := Comp.DM_PhysicalDesignator;

            { Case-insensitive partial match }
            If Pos(UpperCase(SearchText), UpperCase(MatchValue)) > 0 Then
            Begin
                If Not First Then Data := Data + ',';
                First := False;

                Data := Data + '{"designator":"' + EscapeJsonString(Comp.DM_PhysicalDesignator) + '"';
                Data := Data + ',"comment":"' + EscapeJsonString(Comp.DM_Comment) + '"';
                Data := Data + ',"footprint":"' + EscapeJsonString(Comp.DM_Footprint) + '"';
                Data := Data + ',"lib_ref":"' + EscapeJsonString(Comp.DM_LibraryReference) + '"';
                Data := Data + ',"sheet":"' + EscapeJsonString(Doc.DM_FileName) + '"';
                Try
                    Data := Data + ',"location_x":' + IntToStr(Comp.DM_LocationX);
                    Data := Data + ',"location_y":' + IntToStr(Comp.DM_LocationY);
                Except
                    Data := Data + ',"location_x":0,"location_y":0';
                End;
                Data := Data + '}';
                Inc(Count);
            End;
        End;
    End;

    Data := Data + ']';
    Result := BuildSuccessResponse(RequestId, '{"results":' + Data + ',"count":' + IntToStr(Count) + ',"search_text":"' + EscapeJsonString(SearchText) + '","search_by":"' + SearchBy + '"}');
End;

{..............................................................................}
{ Get connectivity info for a specific component (all pins + nets)            }
{ Params: designator, project_path (optional)                                 }
{..............................................................................}

Function Proj_GetConnectivity(Params : String; RequestId : String) : String;
Var
    ProjectPath, Designator : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K : Integer;
    Data, PinList : String;
    FirstPin, Found : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    Designator := ExtractJsonValue(Params, 'designator');

    If Designator = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required'); Exit; End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);
    Found := False;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        If Found Then Break;
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;
            If Comp.DM_PhysicalDesignator <> Designator Then Continue;

            Found := True;

            { Build pin-net connectivity list }
            PinList := '';
            FirstPin := True;
            For K := 0 To Comp.DM_PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;
                If Not FirstPin Then PinList := PinList + ',';
                FirstPin := False;
                PinList := PinList + '{"pin_number":"' + EscapeJsonString(Pin.DM_PinNumber) + '"';
                PinList := PinList + ',"pin_name":"' + EscapeJsonString(Pin.DM_PinName) + '"';
                PinList := PinList + ',"net":"' + EscapeJsonString(Pin.DM_FlattenedNetName) + '"';
                { DM_ElectricalType does not exist as a DM_ identifier
                  (compile-time undeclared). Omitting electrical type; the
                  Sch-server side (via query_objects eSchPin) exposes
                  Pin.Electrical if needed. }
                PinList := PinList + '}';
            End;

            Data := '{"designator":"' + EscapeJsonString(Designator) + '"';
            Data := Data + ',"comment":"' + EscapeJsonString(Comp.DM_Comment) + '"';
            Data := Data + ',"sheet":"' + EscapeJsonString(Doc.DM_FileName) + '"';
            Data := Data + ',"pin_count":' + IntToStr(Comp.DM_PinCount);
            Data := Data + ',"pins":[' + PinList + ']}';
            Result := BuildSuccessResponse(RequestId, Data);
            Exit;
        End;
    End;

    If Not Found Then
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
End;

{..............................................................................}
{ Proj_GetConnectivityBatch - Pin-net connectivity for MANY components in ONE  }
{ call. Iterates every project document once and matches component            }
{ designators against a '~~'-separated set. Output is a JSON array of         }
{ per-component records in the same shape as Proj_GetConnectivity returns.    }
{ Missing designators are reported in "not_found".                             }
{..............................................................................}

Function Proj_GetConnectivityBatch(Params : String; RequestId : String) : String;
Var
    ProjectPath, DesigStr : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, N : Integer;
    Data, PinList, CompEntry, NotFoundJson, ThisDesig : String;
    FirstPin, FirstC, FirstNF, Matched : Boolean;
    Wanted : Array[0..499] Of String;
    Found : Array[0..499] Of Boolean;
    WantedCount, MatchCount : Integer;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);
    DesigStr := ExtractJsonValue(Params, 'designators');

    If DesigStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designators is required');
        Exit;
    End;

    SplitBatchOps(DesigStr, Wanted, WantedCount);
    If WantedCount = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'EMPTY_BATCH', 'No designators parsed');
        Exit;
    End;

    For I := 0 To WantedCount - 1 Do Found[I] := False;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found');
        Exit;
    End;

    SmartCompile(Project);

    Data := '';
    FirstC := True;
    MatchCount := 0;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;

            Matched := False;
            For N := 0 To WantedCount - 1 Do
            Begin
                If Comp.DM_PhysicalDesignator = Wanted[N] Then
                Begin
                    Found[N] := True;
                    ThisDesig := Wanted[N];
                    Matched := True;
                    Break;
                End;
            End;
            If Not Matched Then Continue;

            PinList := '';
            FirstPin := True;
            For K := 0 To Comp.DM_PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;
                If Not FirstPin Then PinList := PinList + ',';
                FirstPin := False;
                PinList := PinList + '{"pin_number":"' + EscapeJsonString(Pin.DM_PinNumber) + '"';
                PinList := PinList + ',"pin_name":"' + EscapeJsonString(Pin.DM_PinName) + '"';
                PinList := PinList + ',"net":"' + EscapeJsonString(Pin.DM_FlattenedNetName) + '"';
                PinList := PinList + '}';
            End;

            CompEntry := '{"designator":"' + EscapeJsonString(ThisDesig) + '"';
            CompEntry := CompEntry + ',"comment":"' + EscapeJsonString(Comp.DM_Comment) + '"';
            CompEntry := CompEntry + ',"sheet":"' + EscapeJsonString(Doc.DM_FileName) + '"';
            CompEntry := CompEntry + ',"pin_count":' + IntToStr(Comp.DM_PinCount);
            CompEntry := CompEntry + ',"pins":[' + PinList + ']}';

            If Not FirstC Then Data := Data + ',';
            FirstC := False;
            Data := Data + CompEntry;
            MatchCount := MatchCount + 1;
        End;
    End;

    NotFoundJson := '';
    FirstNF := True;
    For I := 0 To WantedCount - 1 Do
    Begin
        If Not Found[I] Then
        Begin
            If Not FirstNF Then NotFoundJson := NotFoundJson + ',';
            FirstNF := False;
            NotFoundJson := NotFoundJson + '"' + EscapeJsonString(Wanted[I]) + '"';
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"components":[' + Data + '],'
        + '"matched":' + IntToStr(MatchCount) + ','
        + '"requested":' + IntToStr(WantedCount) + ','
        + '"not_found":[' + NotFoundJson + ']}');
End;

{..............................................................................}
{ Import a document into the project from an external path                    }
{ Copies the file to the project directory, then adds it to the project.      }
{ Params: source_path                                                         }
{..............................................................................}

Function Proj_ImportDocument(Params : String; RequestId : String) : String;
Var
    SourcePath, ProjectDir, DestPath, FileName : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    SourcePath := ExtractJsonValue(Params, 'source_path');
    SourcePath := StringReplace(SourcePath, '\\', '\', -1);

    If SourcePath = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'source_path is required'); Exit; End;
    If Not FileExists(SourcePath) Then Begin Result := BuildErrorResponse(RequestId, 'FILE_NOT_FOUND', 'Source file not found: ' + SourcePath); Exit; End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    ProjectDir := ExtractFilePath(Project.DM_ProjectFullPath);
    FileName := ExtractFileName(SourcePath);
    DestPath := ProjectDir + FileName;

    { Copy the file to the project directory (skip if same location) }
    If UpperCase(SourcePath) <> UpperCase(DestPath) Then
    Begin
        Try
            CopyFile(SourcePath, DestPath, False);
        Except
            Result := BuildErrorResponse(RequestId, 'COPY_FAILED', 'Failed to copy file to project directory');
            Exit;
        End;
    End;

    { Add to project }
    Project.DM_AddSourceDocument(DestPath);

    { Save the project to persist the change }
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', Project.DM_ProjectFullPath);
    RunProcess('WorkspaceManager:SaveObject');

    Result := BuildSuccessResponse(RequestId, '{"success":true,"source_path":"' + EscapeJsonString(SourcePath) + '","dest_path":"' + EscapeJsonString(DestPath) + '"}');
End;

{..............................................................................}
{ Get the full path of the focused project file                               }
{..............................................................................}

Function Proj_GetProjectPath(RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project'); Exit; End;

    Result := BuildSuccessResponse(RequestId, '{"project_path":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '","project_dir":"' + EscapeJsonString(ExtractFilePath(Project.DM_ProjectFullPath)) + '","project_name":"' + EscapeJsonString(Project.DM_ProjectFileName) + '"}');
End;

{..............................................................................}
{ Set a document-level parameter on a specific sheet                          }
{ Uses SchServer to open the sheet and modify its document parameters.        }
{ Params: file_path, name, value                                              }
{..............................................................................}

Function Proj_SetDocumentParameter(Params : String; RequestId : String) : String;
Var
    FilePath, ParamName, ParamValue, Action : String;
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    Iterator : ISch_Iterator;
    Parameter : ISch_Parameter;
    Found : Boolean;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');
    ParamName := ExtractJsonValue(Params, 'name');
    ParamValue := ExtractJsonValue(Params, 'value');

    If FilePath = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'file_path is required'); Exit; End;
    If ParamName = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'name is required'); Exit; End;

    { Sheet parameter write. Follows the Altium Schematic API docs:
      SchIterator + eParameter for existing params, SchObjectFactory +
      RegisterSchObjectInContainer for new ones, with RobotManager
      SendMessage notifications around each.

      Does NOT auto-load missing sheets. Client.OpenDocument and
      Client.ShowDocumentDontFocus both detach the sheet from its
      project on recent Altium builds (tab title shows the absolute
      path instead of the filename). Require the caller to load every
      target sheet beforehand via load_project_sheets, which uses the
      project-aware open path that preserves membership. }

    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_LOADED',
            'Document not loaded in editor: ' + FilePath +
            '. Call load_project_sheets first, or open the sheet in Altium.');
        Exit;
    End;

    SchDoc := SchServer.GetSchDocumentByPath(FilePath);
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'Document loaded but SchServer cannot resolve it: ' + FilePath);
        Exit;
    End;

    { Wrap all sch-object mutations in PreProcess/PostProcess so the
      undo system is notified of the edit. Per the docs, the empty
      message here is fine as long as the inner SCHM_BeginModify /
      SCHM_EndModify / SCHM_PrimitiveRegistration broadcasts bracket
      the actual property write and registration. }
    Found := False;
    SchServer.ProcessControl.PreProcess(SchDoc, '');
    Try
        Iterator := SchDoc.SchIterator_Create;
        Iterator.SetState_IterationDepth(eIterateFirstLevel);
        Iterator.AddFilter_ObjectSet(MkSet(eParameter));
        Try
            Parameter := Iterator.FirstSchObject;
            While Parameter <> Nil Do
            Begin
                If Parameter.Name = ParamName Then
                Begin
                    SchBeginModify(Parameter);
                    Parameter.Text := ParamValue;
                    SchEndModify(Parameter);
                    Found := True;
                    Break;
                End;
                Parameter := Iterator.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iterator);
        End;

        If Not Found Then
        Begin
            { Add pattern: create via SchObjectFactory, set properties,
              register in the container, broadcast SCHM_PrimitiveRegistration.
              SchObjectFactory docs name RegisterSchObjectInContainer as
              the ISch_Document-level add; the broadcast notifies the
              editor sub-systems so the new primitive is visible. }
            Parameter := SchServer.SchObjectFactory(eParameter, eCreate_Default);
            Parameter.Name := ParamName;
            Parameter.Text := ParamValue;
            SchDoc.RegisterSchObjectInContainer(Parameter);
            SchRegisterObject(SchDoc, Parameter);
        End;
    Finally
        SchServer.ProcessControl.PostProcess(SchDoc, '');
    End;

    { Persist directly to disk via the IServerDocument API. SaveDocByPath
      does SetModified + DoFileSave. WorkspaceManager:SaveAll doesn't
      reach non-active sheets in our tests, so we don't rely on it. }
    SaveDocByPath(FilePath);
    Try SchDoc.GraphicallyInvalidate; Except End;

    If Found Then Action := 'updated' Else Action := 'added';
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"action":"' + Action + '"' +
        ',"saved":true' +
        ',"file_path":"' + EscapeJsonString(FilePath) +
        '","name":"' + EscapeJsonString(ParamName) +
        '","value":"' + EscapeJsonString(ParamValue) + '"}');
End;

{..............................................................................}
{ Compare schematic to PCB: compile and compare net/component counts          }
{..............................................................................}

Function Proj_CompareSchPcb(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc, PcbDoc : IDocument;
    I : Integer;
    SchCompCount, PcbCompCount : Integer;
    Data : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    { Count schematic components (DM_NetCount does not exist in scripting API) }
    SchCompCount := 0;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;
        SchCompCount := SchCompCount + Doc.DM_ComponentCount;
    End;

    { Count PCB components }
    PcbCompCount := 0;
    PcbDoc := Project.DM_PrimaryImplementationDocument;
    If PcbDoc <> Nil Then
    Begin
        Try
            PcbCompCount := PcbDoc.DM_ComponentCount;
        Except
        End;
    End;

    Data := '{"sch_components":' + IntToStr(SchCompCount);
    Data := Data + ',"pcb_components":' + IntToStr(PcbCompCount);
    Data := Data + ',"components_match":' + BoolToJsonStr(SchCompCount = PcbCompCount);
    If PcbDoc <> Nil Then
        Data := Data + ',"pcb_path":"' + EscapeJsonString(PcbDoc.DM_FullPath) + '"'
    Else
        Data := Data + ',"pcb_path":""';
    Data := Data + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Compute ECO differences for reporting. Returns diff counts by direction.     }
{ Direction: 'to_pcb'  = extras in schematic need to be added to PCB.          }
{ Direction: 'to_sch'  = extras in PCB need to be added to schematic.          }
{..............................................................................}

Function ComputeECODifferences(Project : IProject;
    Var MatchedCount, ExtraInSch, ExtraInPcb : Integer;
    Var PcbDocPath : String) : Boolean;
Var
    PcbDoc : IDocument;
    Mappings : IComponentMappings;
Begin
    Result := False;
    MatchedCount := 0;
    ExtraInSch := 0;
    ExtraInPcb := 0;
    PcbDocPath := '';

    PcbDoc := Project.DM_PrimaryImplementationDocument;
    If PcbDoc = Nil Then Exit;
    PcbDocPath := PcbDoc.DM_FullPath;

    { DM_ComponentMappings takes a file PATH (OleStr), not an IDocument.
      Passing the object triggers EVariantTypeCastError "Could not convert
      variant of type (Dispatch) into type (OleStr)". The error dialog
      is shown by Altium's global handler even though our Try catches it. }
    Try
        Mappings := Project.DM_ComponentMappings(PcbDocPath);
    Except
        Exit;
    End;

    If Mappings = Nil Then Exit;

    Try MatchedCount := Mappings.DM_MatchedComponentCount;    Except End;
    Try ExtraInSch   := Mappings.DM_UnmatchedSourceComponentCount; Except End;
    Try ExtraInPcb   := Mappings.DM_UnmatchedTargetComponentCount; Except End;
    Result := True;
End;

{..............................................................................}
{ Push schematic changes to PCB (Design > Update PCB Document).               }
{                                                                              }
{ Altium does not expose a fully documented DelphiScript API for silently      }
{ executing an ECO — the IEngineeringChangeOrder / IECOManager interfaces     }
{ are not reachable from scripting in any publicly documented way.             }
{                                                                              }
{ Strategy:                                                                    }
{   1. Compile the project and gather component mapping differences so useful  }
{      counts are available regardless of what the ECO dialog does.            }
{   2. Invoke PCB:UpdatePCBFromProject with parameter flags (DisableDialog,   }
{      Silent, Execute, NoConfirm, AutoApply) that various Altium builds      }
{      honor — older builds ignore unknown flags but do not error. Modern     }
{      builds (AD20+) honor DisableDialog=True by applying changes            }
{      automatically in many cases.                                           }
{   3. Re-compile and recompute mappings; report the before/after delta.      }
{                                                                              }
{ If the ECO dialog still opens (older Altium), the caller sees               }
{ dialog_may_have_opened:true and the user can confirm manually; the          }
{ difference data still reports what Altium found.                            }
{..............................................................................}

Function Proj_UpdatePCB(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    MatchedBefore, ExtraSchBefore, ExtraPcbBefore : Integer;
    MatchedAfter,  ExtraSchAfter,  ExtraPcbAfter  : Integer;
    PcbPath : String;
    Data : String;
    Ok : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);
    Ok := ComputeECODifferences(Project, MatchedBefore, ExtraSchBefore, ExtraPcbBefore, PcbPath);
    If Not Ok Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No primary PCB document in this project or mappings unavailable');
        Exit;
    End;

    { Invoke the ECO process with every parameter flag Altium builds have
      historically honored. Unknown flags are ignored silently. }
    ResetParameters;
    AddStringParameter('Action', 'Execute');
    AddStringParameter('DisableDialog', 'True');
    AddStringParameter('Silent', 'True');
    AddStringParameter('NoConfirm', 'True');
    AddStringParameter('AutoApply', '1');
    RunProcess('PCB:UpdatePCBFromProject');

    { Recompile and recompute to report actual changes }
    Try SmartCompile(Project); Except End;
    ComputeECODifferences(Project, MatchedAfter, ExtraSchAfter, ExtraPcbAfter, PcbPath);

    Data := '{"success":true';
    Data := Data + ',"pcb_path":"' + EscapeJsonString(PcbPath) + '"';
    Data := Data + ',"before":{"matched":' + IntToStr(MatchedBefore);
    Data := Data + ',"extra_in_schematic":' + IntToStr(ExtraSchBefore);
    Data := Data + ',"extra_in_pcb":' + IntToStr(ExtraPcbBefore) + '}';
    Data := Data + ',"after":{"matched":' + IntToStr(MatchedAfter);
    Data := Data + ',"extra_in_schematic":' + IntToStr(ExtraSchAfter);
    Data := Data + ',"extra_in_pcb":' + IntToStr(ExtraPcbAfter) + '}';
    Data := Data + ',"components_added_to_pcb":' + IntToStr(ExtraSchBefore - ExtraSchAfter);
    Data := Data + ',"components_removed_from_pcb":' + IntToStr(ExtraPcbBefore - ExtraPcbAfter);
    Data := Data + ',"in_sync":' + BoolToJsonStr((ExtraSchAfter = 0) And (ExtraPcbAfter = 0));
    { Heuristic: if counts didn't change, the ECO dialog probably opened for
      user confirmation (older Altium). In that case we flag it so the caller
      / user knows to click Execute Changes. }
    Data := Data + ',"dialog_may_have_opened":' +
        BoolToJsonStr((ExtraSchBefore = ExtraSchAfter) And (ExtraPcbBefore = ExtraPcbAfter) And
                      ((ExtraSchBefore + ExtraPcbBefore) > 0));
    Data := Data + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Push PCB changes back to schematic (back-annotation). Same strategy as       }
{ Proj_UpdatePCB but in the opposite direction.                                }
{..............................................................................}

Function Proj_UpdateSchematic(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    MatchedBefore, ExtraSchBefore, ExtraPcbBefore : Integer;
    MatchedAfter,  ExtraSchAfter,  ExtraPcbAfter  : Integer;
    PcbPath : String;
    Data : String;
    Ok : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);
    Ok := ComputeECODifferences(Project, MatchedBefore, ExtraSchBefore, ExtraPcbBefore, PcbPath);
    If Not Ok Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No primary PCB document in this project or mappings unavailable');
        Exit;
    End;

    ResetParameters;
    AddStringParameter('Action', 'Execute');
    AddStringParameter('DisableDialog', 'True');
    AddStringParameter('Silent', 'True');
    AddStringParameter('NoConfirm', 'True');
    AddStringParameter('AutoApply', '1');
    RunProcess('PCB:UpdateSchematicFromPCB');

    Try SmartCompile(Project); Except End;
    ComputeECODifferences(Project, MatchedAfter, ExtraSchAfter, ExtraPcbAfter, PcbPath);

    Data := '{"success":true';
    Data := Data + ',"pcb_path":"' + EscapeJsonString(PcbPath) + '"';
    Data := Data + ',"before":{"matched":' + IntToStr(MatchedBefore);
    Data := Data + ',"extra_in_schematic":' + IntToStr(ExtraSchBefore);
    Data := Data + ',"extra_in_pcb":' + IntToStr(ExtraPcbBefore) + '}';
    Data := Data + ',"after":{"matched":' + IntToStr(MatchedAfter);
    Data := Data + ',"extra_in_schematic":' + IntToStr(ExtraSchAfter);
    Data := Data + ',"extra_in_pcb":' + IntToStr(ExtraPcbAfter) + '}';
    Data := Data + ',"components_added_to_schematic":' + IntToStr(ExtraPcbBefore - ExtraPcbAfter);
    Data := Data + ',"components_removed_from_schematic":' + IntToStr(ExtraSchBefore - ExtraSchAfter);
    Data := Data + ',"in_sync":' + BoolToJsonStr((ExtraSchAfter = 0) And (ExtraPcbAfter = 0));
    Data := Data + ',"dialog_may_have_opened":' +
        BoolToJsonStr((ExtraSchBefore = ExtraSchAfter) And (ExtraPcbBefore = ExtraPcbAfter) And
                      ((ExtraSchBefore + ExtraPcbBefore) > 0));
    Data := Data + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Get design differences between schematic and PCB netlist                    }
{ Uses IComponentMappings to find unmatched source/target components          }
{..............................................................................}

Function Proj_GetDesignDifferences(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    PcbDoc : IDocument;
    Mappings : IComponentMappings;
    I, MatchedCount, UnmatchedSrcCount, UnmatchedTgtCount : Integer;
    Data, SrcList, TgtList : String;
    First : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    PcbDoc := Project.DM_PrimaryImplementationDocument;
    If PcbDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No primary PCB document in this project');
        Exit;
    End;

    { DM_ComponentMappings takes a file path (OleStr), not an IDocument }
    Try
        Mappings := Project.DM_ComponentMappings(PcbDoc.DM_FullPath);
    Except
        Result := BuildErrorResponse(RequestId, 'MAPPING_FAILED', 'Could not get component mappings');
        Exit;
    End;

    If Mappings = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MAPPING_FAILED', 'Component mappings returned nil');
        Exit;
    End;

    MatchedCount := 0;
    UnmatchedSrcCount := 0;
    UnmatchedTgtCount := 0;

    Try MatchedCount := Mappings.DM_MatchedComponentCount; Except End;
    Try UnmatchedSrcCount := Mappings.DM_UnmatchedSourceComponentCount; Except End;
    Try UnmatchedTgtCount := Mappings.DM_UnmatchedTargetComponentCount; Except End;

    { Unmatched source components (in schematic but not in PCB) }
    SrcList := '[';
    First := True;
    Try
        For I := 0 To UnmatchedSrcCount - 1 Do
        Begin
            If Not First Then SrcList := SrcList + ',';
            First := False;
            Try
                SrcList := SrcList + '"' + EscapeJsonString(Mappings.DM_UnmatchedSourceComponent(I).DM_PhysicalDesignator) + '"';
            Except
                SrcList := SrcList + '"?"';
            End;
        End;
    Except
    End;
    SrcList := SrcList + ']';

    { Unmatched target components (in PCB but not in schematic) }
    TgtList := '[';
    First := True;
    Try
        For I := 0 To UnmatchedTgtCount - 1 Do
        Begin
            If Not First Then TgtList := TgtList + ',';
            First := False;
            Try
                TgtList := TgtList + '"' + EscapeJsonString(Mappings.DM_UnmatchedTargetComponent(I).DM_PhysicalDesignator) + '"';
            Except
                TgtList := TgtList + '"?"';
            End;
        End;
    Except
    End;
    TgtList := TgtList + ']';

    Data := '{"matched_components":' + IntToStr(MatchedCount);
    Data := Data + ',"extra_in_schematic_count":' + IntToStr(UnmatchedSrcCount);
    Data := Data + ',"extra_in_pcb_count":' + IntToStr(UnmatchedTgtCount);
    Data := Data + ',"extra_in_schematic":' + SrcList;
    Data := Data + ',"extra_in_pcb":' + TgtList;
    Data := Data + ',"in_sync":' + BoolToJsonStr((UnmatchedSrcCount = 0) And (UnmatchedTgtCount = 0));
    Data := Data + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Lock/unlock component designators to prevent re-annotation                  }
{ Params: designator (or "all"), lock (true/false)                            }
{..............................................................................}

Function Proj_LockDesignator(Params : String; RequestId : String) : String;
Var
    Designator : String;
    LockStr : String;
    LockVal : Boolean;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Count : Integer;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    LockStr := ExtractJsonValue(Params, 'lock');
    If LockStr = '' Then LockStr := 'true';
    LockVal := (LockStr = 'true');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required (or "all")');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Count := 0;
    SchServer.ProcessControl.PreProcess(SchDoc, '');

    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));

    Obj := Iterator.FirstSchObject;
    While Obj <> Nil Do
    Begin
        Try
            Comp := Obj;
            If (Designator = 'all') Or (Comp.Designator.Text = Designator) Then
            Begin
                Comp.Designator.IsLocked := LockVal;
                Inc(Count);
            End;
        Except
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);

    SchServer.ProcessControl.PostProcess(SchDoc, '');
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator) +
        '","locked":' + BoolToJsonStr(LockVal) +
        ',"count":' + IntToStr(Count) + '}');
End;

{..............................................................................}
{ Get project options: output path, error tolerance, compiler settings         }
{..............................................................................}

Function Proj_GetProjectOptions(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Data : String;
    HierMode : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    Data := '{"project_name":"' + EscapeJsonString(Project.DM_ProjectFileName) + '"';
    Data := Data + ',"project_path":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"';

    { Output path }
    Try
        Data := Data + ',"output_path":"' + EscapeJsonString(Project.DM_GetOutputPath) + '"';
    Except
        Data := Data + ',"output_path":""';
    End;

    { Hierarchy mode (If/Else chain — Case on enum crashes DelphiScript) }
    Try
        If Project.DM_HierarchyMode = 0 Then
            HierMode := 'Flat'
        Else If Project.DM_HierarchyMode = 1 Then
            HierMode := 'GlobalScope'
        Else
            HierMode := IntToStr(Project.DM_HierarchyMode);
        Data := Data + ',"hierarchy_mode":"' + HierMode + '"';
    Except
        Data := Data + ',"hierarchy_mode":"unknown"';
    End;

    { Document counts }
    Data := Data + ',"logical_document_count":' + IntToStr(Project.DM_LogicalDocumentCount);
    Data := Data + ',"physical_document_count":' + IntToStr(Project.DM_PhysicalDocumentCount);

    { Variant count }
    Try
        Data := Data + ',"variant_count":' + IntToStr(Project.DM_ProjectVariantCount);
    Except
        Data := Data + ',"variant_count":0';
    End;

    { Channel settings }
    Try
        Data := Data + ',"channel_designator_format":"' + EscapeJsonString(Project.DM_ChannelDesignatorFormat) + '"';
    Except
    End;
    Try
        Data := Data + ',"channel_room_separator":"' + EscapeJsonString(Project.DM_ChannelRoomLevelSeperator) + '"';
    Except
    End;

    { Port/sheet entry net name settings }
    Try
        Data := Data + ',"allow_port_net_names":' + BoolToJsonStr(Project.DM_GetAllowPortNetNames);
    Except
    End;
    Try
        Data := Data + ',"allow_sheet_entry_net_names":' + BoolToJsonStr(Project.DM_GetAllowSheetEntryNetNames);
    Except
    End;
    Try
        Data := Data + ',"append_sheet_number_to_local_nets":' + BoolToJsonStr(Project.DM_GetAppendSheetNumberToLocalNets);
    Except
    End;

    Data := Data + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Load all project schematic sheets into the editor.                            }
{                                                                               }
{ Project-scope queries (query_objects, batch_modify, etc.) only iterate        }
{ sheets actually resident in SchServer. A sheet is listed as a project member  }
{ via DM_LogicalDocuments even when Altium hasn't loaded its editor state yet.  }
{ This handler walks every project sheet and, for any that aren't loaded, calls }
{ Client.OpenDocument('SCH', path) — the same API set_document_parameter has    }
{ used without creating free documents. RunProcess('Client:OpenDocument') would }
{ strip project membership and produce free docs; do not substitute it.         }
{..............................................................................}

Function Proj_LoadProjectSheets(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    ProjectPath, FilePath, Data : String;
    I, TotalSheets, Loaded, AlreadyLoaded, Failed : Integer;
    WasLoaded : Boolean;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectPath := StringReplace(ProjectPath, '\\', '\', -1);

    If ProjectPath <> '' Then
        Project := FindProjectByPath(Workspace, ProjectPath)
    Else
        Project := Workspace.DM_FocusedProject;

    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found');
        Exit;
    End;

    TotalSheets := 0;
    Loaded := 0;
    AlreadyLoaded := 0;
    Failed := 0;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;
        Inc(TotalSheets);

        FilePath := Doc.DM_FullPath;

        WasLoaded := False;
        Try
            If Client.IsDocumentOpen(FilePath) Then WasLoaded := True;
        Except WasLoaded := False; End;

        If WasLoaded Then
        Begin
            Inc(AlreadyLoaded);
            Continue;
        End;

        Try
            ServerDoc := Client.OpenDocument('SCH', FilePath);
            If ServerDoc <> Nil Then
                Inc(Loaded)
            Else
                Inc(Failed);
        Except
            Inc(Failed);
        End;
    End;

    Data := '{"total_sheets":' + IntToStr(TotalSheets);
    Data := Data + ',"loaded":' + IntToStr(Loaded);
    Data := Data + ',"already_loaded":' + IntToStr(AlreadyLoaded);
    Data := Data + ',"failed":' + IntToStr(Failed) + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Command Handler - must be at end so all functions are declared               }
{..............................................................................}

Function HandleProjectCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'create':            Result := Proj_Create(Params, RequestId);
        'open':              Result := Proj_Open(Params, RequestId);
        'save':              Result := Proj_Save(Params, RequestId);
        'close':             Result := Proj_Close(Params, RequestId);
        'get_documents':     Result := Proj_GetDocuments(Params, RequestId);
        'add_document':      Result := Proj_AddDocument(Params, RequestId);
        'remove_document':   Result := Proj_RemoveDocument(Params, RequestId);
        'get_parameters':    Result := Proj_GetParameters(Params, RequestId);
        'set_parameter':     Result := Proj_SetParameter(Params, RequestId);
        'compile':           Result := Proj_Compile(Params, RequestId);
        'get_focused':       Result := Proj_GetFocused(RequestId);
        'get_nets':          Result := Proj_GetNets(Params, RequestId);
        'get_bom':           Result := Proj_GetBOM(Params, RequestId);
        'get_component_info': Result := Proj_GetComponentInfo(Params, RequestId);
        'export_pdf':        Result := Proj_ExportPDF(Params, RequestId);
        'cross_probe':       Result := Proj_CrossProbe(Params, RequestId);
        'get_design_stats':  Result := Proj_GetDesignStats(Params, RequestId);
        'get_board_info':    Result := Proj_GetBoardInfo(Params, RequestId);
        'annotate':          Result := Proj_Annotate(Params, RequestId);
        'generate_output':   Result := Proj_GenerateOutput(Params, RequestId);
        'export_step':       Result := Proj_ExportSTEP(Params, RequestId);
        'export_dxf':        Result := Proj_ExportDXF(Params, RequestId);
        'export_image':      Result := Proj_ExportImage(Params, RequestId);
        'get_outjob_containers': Result := Proj_GetOutJobContainers(Params, RequestId);
        'run_outjob':        Result := Proj_RunOutJob(Params, RequestId);
        'get_variants':      Result := Proj_GetVariants(Params, RequestId);
        'get_active_variant': Result := Proj_GetActiveVariant(Params, RequestId);
        'set_active_variant': Result := Proj_SetActiveVariant(Params, RequestId);
        'create_variant':    Result := Proj_CreateVariant(Params, RequestId);
        'get_open_projects': Result := Proj_GetOpenProjects(RequestId);
        'save_all':          Result := Proj_SaveAll(RequestId);
        'get_messages':      Result := Proj_GetMessages(Params, RequestId);
        'find_component':    Result := Proj_FindComponent(Params, RequestId);
        'get_connectivity':  Result := Proj_GetConnectivity(Params, RequestId);
        'get_connectivity_batch': Result := Proj_GetConnectivityBatch(Params, RequestId);
        'import_document':   Result := Proj_ImportDocument(Params, RequestId);
        'get_project_path':  Result := Proj_GetProjectPath(RequestId);
        'set_document_parameter': Result := Proj_SetDocumentParameter(Params, RequestId);
        'compare_sch_pcb':   Result := Proj_CompareSchPcb(Params, RequestId);
        'update_pcb':        Result := Proj_UpdatePCB(Params, RequestId);
        'update_schematic':  Result := Proj_UpdateSchematic(Params, RequestId);
        'get_design_differences': Result := Proj_GetDesignDifferences(Params, RequestId);
        'lock_designator':   Result := Proj_LockDesignator(Params, RequestId);
        'get_project_options': Result := Proj_GetProjectOptions(Params, RequestId);
        'load_project_sheets': Result := Proj_LoadProjectSheets(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown project action: ' + Action);
    End;
End;
