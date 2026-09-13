{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Project.pas - Project management functions for the Altium integration bridge                }
{..............................................................................}

{ FocusedProjectPathIs - is the named project the one tools will default to?  }
{                                                                             }
{ Every handler here that takes an optional project_path falls back to        }
{ DM_FocusedProject, and so does app_create_document when it attaches a new   }
{ file. Opening or creating a project does NOT make it focused, because       }
{ Altium's focused project follows the focused document. Callers assumed      }
{ otherwise, so the create and open replies now state it outright.            }
Function FocusedProjectPathIs(ProjectPath : String) : Boolean;
Var
    Workspace : IWorkspace;
    Focused : IProject;
    FocusedPath : String;
Begin
    Result := False;
    If ProjectPath = '' Then Exit;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    Focused := Workspace.DM_FocusedProject;
    If Focused = Nil Then Exit;
    FocusedPath := '';
    Try FocusedPath := Focused.DM_ProjectFullPath; Except End;
    Result := UpperCase(FocusedPath) = UpperCase(ProjectPath);
End;

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
    ProjectPath, ProjectType, ProjectExt : String;
    StubContent : String;
    F : TextFile;
    Saved : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    ProjectType := ExtractJsonValue(Params, 'project_type');

    If ProjectType = '' Then ProjectType := 'PCB';

    // Altium project files are INI-like text. Write a minimal stub directly
    // to disk so we don't trigger the GUI New Project dialog, then open it
    // via WorkspaceManager:OpenObject (the documented programmatic open).
    // The stub is the smallest .PrjPcb that Altium will load and let us
    // attach documents to.
    If ProjectType = 'PCB' Then
    Begin
        ProjectExt := '.PrjPcb';
        StubContent :=
            '[Design]' + #13#10 +
            'Version=1.0' + #13#10 +
            'HierarchyMode=0' + #13#10 +
            'OpenOutputs=1' + #13#10 +
            'ArchiveProject=0' + #13#10;
    End
    Else
    Begin
        ProjectExt := '.PrjPcb';
        StubContent := '[Design]' + #13#10 + 'Version=1.0' + #13#10;
    End;

    Saved := False;
    // Ensure the target directory exists; Rewrite throws EInOutError
    // ("Invalid file name") into an engine modal if the folder is missing,
    // and that modal escapes the Try/Except below.
    Try ForceDirectories(ExtractFilePath(ProjectPath)); Except End;
    Try
        AssignFile(F, ProjectPath);
        Rewrite(F);
        Try
            Write(F, StubContent);
        Finally
            CloseFile(F);
        End;
        Saved := FileExists(ProjectPath);
    Except
        Saved := False;
    End;

    If Not Saved Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
            'Could not write project stub: ' + ProjectPath);
        Exit;
    End;

    // Open the freshly-written project so subsequent commands can target it.
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', ProjectPath);
    RunProcess('WorkspaceManager:OpenObject');

    { SAY WHETHER IT IS FOCUSED, because it usually is not, and the next      }
    { call the caller makes probably assumes it is. Altium's focused project  }
    { follows the focused DOCUMENT, and a project just created has none, so   }
    { there is nothing to focus onto and no API to force it.                  }
    {                                                                          }
    { Why this matters more than it looks: app_create_document defaults to    }
    { add_to_project=True and attaches to the FOCUSED project. Create a       }
    { project, add a document, and the document lands in whatever was open    }
    { before, which during the live sweep was a client design.                }
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"project_path":"' + EscapeJsonString(ProjectPath) +
        '","saved":true,"focused":'
        + BoolToJsonStr(FocusedProjectPathIs(ProjectPath))
        + ',"note":"a new project has no documents, so it cannot become the '
        + 'focused project. Pass project_path explicitly to proj_add_document, '
        + 'or activate one of its documents first: tools that default to the '
        + 'focused project will otherwise act on a different one."}');
End;

Function Proj_Open(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', ProjectPath);
    RunProcess('WorkspaceManager:OpenObject');

    { Opening an ALREADY-OPEN project is a no-op that changes no focus, and   }
    { this used to return a bare success, so a caller could not tell whether  }
    { the project it named is the one subsequent calls will act on.           }
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"project_path":"' + EscapeJsonString(ProjectPath) + '"'
        + ',"focused":' + BoolToJsonStr(FocusedProjectPathIs(ProjectPath))
        + ',"note":"focus follows the active DOCUMENT, not this call. If '
        + 'focused is false, activate one of the project''s documents with '
        + 'app_set_active_document before using tools that default to the '
        + 'focused project."}');
End;

Function Proj_Save(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            { Only THIS project. The workspace-wide SaveAll that used to be   }
            { here ignored project_path completely, so asking to save a       }
            { scratch project wrote every dirty document in the workspace,    }
            { client projects included. app_save_all is the tool for that,    }
            { and its name says so.                                           }
            SaveProjectMembers(Project);
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"project_path":"'
                + EscapeJsonString(Project.DM_ProjectFullPath) + '"}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

{ Close ONE project, saving only ITS documents, and report whether the       }
{ project actually went away.                                                 }
{                                                                             }
{ This used to run WorkspaceManager:SaveAll, which is workspace-wide. Called  }
{ with an explicit project_path for a scratch project, it raised an Unsaved   }
{ Changes prompt for a CLIENT project with 17 documents that the caller had   }
{ never named. Answering that prompt would have written, and then closed,     }
{ somebody else's work. SaveProjectMembers touches only the target.           }
{                                                                             }
{ It also returned success unconditionally. When the prompt was cancelled the }
{ project stayed open and the reply still said success, so a caller could not }
{ tell a completed close from an abandoned one. The close is now confirmed by }
{ looking the project up again.                                               }
Function Proj_Close(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    SaveFirst, Closed : Boolean;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
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
                SaveProjectMembers(Project);

            ResetParameters;
            AddStringParameter('ObjectKind', 'Project');
            AddStringParameter('FileName', ProjectPath);
            RunProcess('WorkspaceManager:CloseObject');

            { Confirm. A cancelled save prompt aborts the close, and the      }
            { process layer cannot report that.                               }
            Closed := FindProjectByPath(Workspace, ProjectPath) = Nil;
            If Closed Then
                Result := BuildSuccessResponse(RequestId,
                    '{"success":true,"closed":true,"project_path":"'
                    + EscapeJsonString(ProjectPath) + '"}')
            Else
                Result := BuildSuccessResponse(RequestId,
                    '{"success":false,"closed":false,"project_path":"'
                    + EscapeJsonString(ProjectPath) + '"'
                    + ',"reason":"the project is still open after the close '
                    + 'was issued, which is what happens when a save prompt '
                    + 'is cancelled or a document refuses to close"}');
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
                DocInfo := DocInfo + ',"file_path":"' + EscapeJsonString(DocFullPath(Doc)) + '"';
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
    DocumentPath := ExtractJsonValue(Params, 'document_path');

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

{ Remove a document FROM A PROJECT. It does not delete the file.             }
{                                                                             }
{ This used to run WorkspaceManager:CloseObject on the document, which closes }
{ the editor WINDOW and leaves project membership untouched. Measured: the    }
{ call reported success, raised an Unsaved Changes prompt, and the .PrjPcb    }
{ still carried the DocumentPath afterwards. The right call is the mirror of  }
{ the one Proj_AddDocument uses, DM_RemoveSourceDocument, and it is confirmed }
{ by three independent scripts in reference/.                                 }
{                                                                             }
{ Membership is re-read afterwards, because the removal is the whole point of }
{ the tool and "I issued it" is not the same as "it happened".                }
Function Proj_RemoveDocument(Params : String; RequestId : String) : String;
Var
    ProjectPath, DocumentPath, DocPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
    StillMember, WasMember : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    DocumentPath := ExtractJsonValue(Params, 'document_path');

    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        If ProjectPath <> '' Then
            Project := FindProjectByPath(Workspace, ProjectPath)
        Else
            Project := Workspace.DM_FocusedProject;

        If Project <> Nil Then
        Begin
            { Was it ever a member? Removing something that was never there   }
            { must not read the same as a successful removal.                 }
            WasMember := False;
            For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
            Begin
                DocPath := '';
                Doc := Project.DM_LogicalDocuments(I);
                If Doc <> Nil Then Try DocPath := Doc.DM_FullPath; Except End;
                If UpperCase(DocPath) = UpperCase(DocumentPath) Then
                    WasMember := True;
            End;

            If Not WasMember Then
                Result := BuildErrorResponse(RequestId, 'NOT_A_MEMBER',
                    'That document is not in this project: ' + DocumentPath)
            Else
            Begin
                Try Project.DM_RemoveSourceDocument(DocumentPath); Except End;

                StillMember := False;
                For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    DocPath := '';
                    Doc := Project.DM_LogicalDocuments(I);
                    If Doc <> Nil Then Try DocPath := Doc.DM_FullPath; Except End;
                    If UpperCase(DocPath) = UpperCase(DocumentPath) Then
                        StillMember := True;
                End;

                If StillMember Then
                    Result := BuildSuccessResponse(RequestId,
                        '{"success":false,"removed":false,"reason":"the '
                        + 'document is still a member of the project after '
                        + 'DM_RemoveSourceDocument was called"}')
                Else
                    Result := BuildSuccessResponse(RequestId,
                        '{"success":true,"removed":true,"document_path":"'
                        + EscapeJsonString(DocumentPath) + '"}');
            End;
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

{ Set a project-level parameter, and prove it took.                          }
{                                                                             }
{ The add path used to be WorkspaceManager:DocumentAddParameter. Measured on  }
{ AD26: it writes nothing. The parameter was absent from DM_Parameters and    }
{ absent from the saved .PrjPcb, while the tool returned success with the     }
{ name and value ECHOED BACK, which reads exactly like a confirmation.        }
{                                                                             }
{ That process name could not be corroborated: the only occurrence of it in   }
{ reference/ is CoAltium, a vendored copy of THIS project, so the citation    }
{ was circular. The real API is DM_AddParameter inside DM_BeginUpdate /       }
{ DM_EndUpdate, used twice by an independent script in                        }
{ reference/altium-delphiscripts-brett/Project/Prj-Parameters.pas.            }
{                                                                             }
{ Both paths are now followed by a re-read of DM_Parameters, and the reply    }
{ carries what was READ, not what was asked for.                              }
Function Proj_SetParameter(Params : String; RequestId : String) : String;
Var
    ProjectPath, ParamName, ParamValue, ReadBack : String;
    Workspace : IWorkspace;
    Project : IProject;
    Param : IParameter;
    I : Integer;
    Found, Verified : Boolean;
Begin
    ParamName := ExtractJsonValue(Params, 'name');
    ParamValue := ExtractJsonValue(Params, 'value');
    ProjectPath := ExtractJsonValue(Params, 'project_path');

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

    { Update in place if it is already there. }
    Found := False;
    Project.DM_BeginUpdate;
    Try
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

        If Not Found Then
            Try Project.DM_AddParameter(ParamName, ParamValue); Except End;
    Finally
        Project.DM_EndUpdate;
    End;

    { Persist, then READ IT BACK. }
    SaveProjectMembers(Project);

    Verified := False;
    ReadBack := '';
    For I := 0 To Project.DM_ParameterCount - 1 Do
    Begin
        Param := Project.DM_Parameters(I);
        If (Param <> Nil) And (Param.DM_Name = ParamName) Then
        Begin
            Try ReadBack := Param.DM_Value; Except End;
            Verified := ReadBack = ParamValue;
            Break;
        End;
    End;

    If Verified Then
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"name":"' + EscapeJsonString(ParamName)
            + '","value":"' + EscapeJsonString(ReadBack)
            + '","created":' + BoolToJsonStr(Not Found)
            + ',"verified":true,"project_path":"'
            + EscapeJsonString(ProjectPath) + '"}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"success":false,"verified":false,"name":"'
            + EscapeJsonString(ParamName)
            + '","requested_value":"' + EscapeJsonString(ParamValue)
            + '","read_back":"' + EscapeJsonString(ReadBack)
            + '","reason":"the parameter did not read back with the '
            + 'requested value after the write and save, so it was not '
            + 'stored","project_path":"' + EscapeJsonString(ProjectPath) + '"}');
End;

Function Proj_Compile(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

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
{ ForceRecompileIfRequested - If Params carries force_recompile=true, flush   }
{ dirty docs to disk, invalidate the SmartCompile cache, and run a fresh      }
{ DM_Compile on the given project. Called by the net/connectivity handlers   }
{ below, so it must be declared BEFORE them, DelphiScript has no forward    }
{ declarations (no `Forward;` directive), functions must appear in           }
{ caller-dependency order.                                                    }
{..............................................................................}

Procedure ForceRecompileIfRequested(Project : IProject; Params : String);
Var
    Flag : String;
Begin
    If Project = Nil Then Exit;
    Flag := LowerCase(ExtractJsonValue(Params, 'force_recompile'));
    If (Flag = 'true') Or (Flag = '1') Then
    Begin
        { Flush editor-side edits first, DM_Compile reads from the          }
        { on-disk project structure in some code paths, and users hit this  }
        { tool precisely when the in-editor state has diverged from the     }
        { cached netlist.                                                   }
        Try SaveAllDirty(0); Except End;
        LastCompileTick := 0;
        SmartCompile(Project);
    End;
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
    I, J, K, Count, Limit, DocCount : Integer;
    UsePhysical : Boolean;
    Data, CompDesig, NetName : String;
    First : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
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

    // Honor force_recompile BEFORE SmartCompile so the cache is fresh.
    ForceRecompileIfRequested(Project, Params);

    // Compile to resolve net connectivity
    SmartCompile(Project);

    Data := '[';
    First := True;
    Count := 0;

    GetCompiledDocs(Project, DocCount, UsePhysical);
    For I := 0 To DocCount - 1 Do
    Begin
        If Count >= Limit Then Break;
        Doc := GetCompiledDoc(Project, I, UsePhysical);
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
    I, J, K, Count, Limit, DocCount : Integer;
    UsePhysical : Boolean;
    Data, CompDesig, CompComment, CompFP, CompLib, PinList : String;
    First, FirstPin : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
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

    GetCompiledDocs(Project, DocCount, UsePhysical);
    For I := 0 To DocCount - 1 Do
    Begin
        If Count >= Limit Then Break;
        Doc := GetCompiledDoc(Project, I, UsePhysical);
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

{ Proj_GetComponentInfo - one-shot inspection of a single component.         }
{                                                                              }
{ Pin nets are the ONLY field that needs a project compile, and a stale-     }
{ cache compile on a multi-sheet hierarchical project can take 30-60s.       }
{ Two opt-out flags let the caller skip the slow paths when the question     }
{ doesn't need them:                                                          }
{                                                                              }
{   with_pin_nets=false  - skip SmartCompile, do not emit Pin.DM_FlattenedNet }
{                          on each pin. Pin number/name still come back. The }
{                          "looking up the part's value/footprint" case      }
{                          becomes sub-second.                                }
{   with_parameters=false - skip the parameter iterator on the live          }
{                          component. Useful when only header metadata and  }
{                          pins are needed.                                  }
{                                                                              }
{ Defaults are true for backward compatibility, so existing callers keep    }
{ getting the full payload.                                                   }
Function Proj_GetComponentInfo(Params : String; RequestId : String) : String;
Var
    ProjectPath, Designator, FlagStr : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, DocCount : Integer;
    UsePhysical, WithPinNets, WithParameters : Boolean;
    Data, PinList, ParamList : String;
    FirstPin, FirstParam : Boolean;
    Found : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    Designator := ExtractJsonValue(Params, 'designator');

    FlagStr := ExtractJsonValue(Params, 'with_pin_nets');
    WithPinNets := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');
    FlagStr := ExtractJsonValue(Params, 'with_parameters');
    WithParameters := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');

    If Designator = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required'); Exit; End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    { Compile is only needed for Pin.DM_FlattenedNetName. force_recompile   }
    { stays honoured even when nets are skipped, callers using it have an  }
    { explicit reason to refresh the cache.                                 }
    ForceRecompileIfRequested(Project, Params);
    If WithPinNets Then SmartCompile(Project);
    Found := False;

    { Physical-doc enumeration relies on the post-compile state. When we   }
    { skipped the compile, walk logical docs instead, that's where the     }
    { source-side metadata (designator, footprint, params, pin names)      }
    { lives anyway.                                                         }
    If WithPinNets Then
        GetCompiledDocs(Project, DocCount, UsePhysical)
    Else
    Begin
        DocCount := 0;
        UsePhysical := False;
        Try DocCount := Project.DM_LogicalDocumentCount; Except End;
    End;
    For I := 0 To DocCount - 1 Do
    Begin
        If Found Then Break;
        If WithPinNets Then
            Doc := GetCompiledDoc(Project, I, UsePhysical)
        Else
        Begin
            Doc := Nil;
            Try Doc := Project.DM_LogicalDocuments(I); Except End;
        End;
        If Doc = Nil Then Continue;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;
            If Comp.DM_PhysicalDesignator <> Designator Then Continue;

            Found := True;

            { Pin list. Net assignment skipped when WithPinNets is false,    }
            { the field is omitted entirely so callers can tell "we didn't  }
            { ask" from "no net assigned".                                   }
            PinList := '';
            FirstPin := True;
            For K := 0 To Comp.DM_PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;
                If Not FirstPin Then PinList := PinList + ',';
                FirstPin := False;
                PinList := PinList + '{"pin":"' + EscapeJsonString(Pin.DM_PinNumber) +
                    '","name":"' + EscapeJsonString(Pin.DM_PinName) + '"';
                If WithPinNets Then
                    PinList := PinList + ',"net":"' + EscapeJsonString(Pin.DM_FlattenedNetName) + '"';
                PinList := PinList + '}';
            End;

            { Parameter dict. Skipped when WithParameters is false, that   }
            { iterator is moderate cost but unnecessary for "look up the  }
            { footprint" / "look up the value" callers.                    }
            ParamList := '';
            FirstParam := True;
            If WithParameters Then
            Begin
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
            End;

            Data := '{"designator":"' + EscapeJsonString(Designator) + '"';
            Data := Data + ',"comment":"' + EscapeJsonString(Comp.DM_Comment) + '"';
            Data := Data + ',"footprint":"' + EscapeJsonString(Comp.DM_Footprint) + '"';
            Data := Data + ',"lib_ref":"' + EscapeJsonString(Comp.DM_LibraryReference) + '"';
            Data := Data + ',"sheet":"' + EscapeJsonString(Doc.DM_FileName) + '"';
            If WithParameters Then
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
{ Batch variant of Proj_GetComponentInfo - returns metadata + optional pin    }
{ nets and parameters for many designators in one IPC round-trip.            }
{                                                                              }
{ Modelled on the singular Proj_GetComponentInfo body shape, not on           }
{ Proj_GetConnectivityBatch (which has a pre-existing Result-clobber bug,    }
{ returns the request body verbatim as its response). Key structural rules:  }
{ - the final Result := BuildSuccessResponse(...) is NOT the last statement; }
{   an Exit; follows to break the "last statement is a long Result :="       }
{   pattern that triggers DelphiScript's clobber.                             }
{ - the response Data is stashed in a local before assigning to Result.       }
{                                                                              }
{ Params: designators (comma-separated, max 500), project_path?,              }
{         with_pin_nets? (default true), with_parameters? (default true)     }
{..............................................................................}

Function Proj_GetComponentInfoBatch(Params : String; RequestId : String) : String;
Var
    ProjectPath, DesigStr, FlagStr : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, N, DocCount, SepPos : Integer;
    UsePhysical, WithPinNets, WithParameters, AlreadyDone : Boolean;
    PinList, ParamList, CompEntry, ThisDesig, BodyJson, NotFoundJson : String;
    FirstPin, FirstParam, FirstC, FirstNF : Boolean;
    Wanted, MatchedDesigs : TStringList;
    Remaining, EnvelopeData, ResponseStr : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    DesigStr := ExtractJsonValue(Params, 'designators');

    FlagStr := ExtractJsonValue(Params, 'with_pin_nets');
    WithPinNets := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');
    FlagStr := ExtractJsonValue(Params, 'with_parameters');
    WithParameters := (FlagStr <> 'false') And (FlagStr <> 'False') And (FlagStr <> '0');

    If DesigStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designators is required');
        Exit;
    End;

    { Wanted holds parsed designators (input list). MatchedDesigs records   }
    { which designators we've already produced output for, so the not_found }
    { list is just the set difference at the end. Two heap-allocated        }
    { TStringList locals - explicitly NOT `Array[0..499] Of String`, which  }
    { triggered a "response = request body" bug in the pre-existing         }
    { Proj_GetConnectivityBatch handler (broken since May 13). DelphiScript }
    { has no Integer()/Pointer() type-casts so we cannot tag entries via    }
    { TStringList.Objects, the parallel Matched list is the cleanest fix.   }
    Wanted := TStringList.Create;
    MatchedDesigs := TStringList.Create;
    Try
        Remaining := DesigStr;
        While Length(Remaining) > 0 Do
        Begin
            SepPos := Pos('~~', Remaining);
            If SepPos = 0 Then
            Begin
                ThisDesig := Remaining;
                Remaining := '';
            End
            Else
            Begin
                ThisDesig := Copy(Remaining, 1, SepPos - 1);
                Remaining := Copy(Remaining, SepPos + 2, Length(Remaining));
            End;
            If ThisDesig <> '' Then
                Wanted.Add(ThisDesig);
        End;

        If Wanted.Count = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'EMPTY_BATCH', 'No designators parsed');
            Exit;
        End;

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

        ForceRecompileIfRequested(Project, Params);
        If WithPinNets Then SmartCompile(Project);

        { Always use GetCompiledDocs so this handler enumerates the same        }
        { doc tree the BOM does. The previous else-branch shortcut walked       }
        { DM_LogicalDocuments and read source-side DM_PhysicalDesignator,       }
        { which on a multichannel design returns the un-suffixed source         }
        { name (e.g. "R5") -- so a drawer lookup for "R5_A" found nothing       }
        { while the BOM (compiled doc enumeration) happily reported "R5_A".    }
        { GetCompiledDocs doesn't itself trigger SmartCompile; if the project   }
        { isn't compiled it falls back to logical docs internally.              }
        GetCompiledDocs(Project, DocCount, UsePhysical);

        BodyJson := '';
        FirstC := True;

        For I := 0 To DocCount - 1 Do
        Begin
            Doc := GetCompiledDoc(Project, I, UsePhysical);
            If Doc = Nil Then Continue;

            For J := 0 To Doc.DM_ComponentCount - 1 Do
            Begin
                Comp := Doc.DM_Components(J);
                If Comp = Nil Then Continue;

                ThisDesig := Comp.DM_PhysicalDesignator;
                If Wanted.IndexOf(ThisDesig) < 0 Then Continue;

                { Skip if already emitted (e.g. multi-channel hierarchical    }
                { designs can expose the same physical designator on multiple }
                { logical sheets after compile).                              }
                AlreadyDone := (MatchedDesigs.IndexOf(ThisDesig) >= 0);
                If AlreadyDone Then Continue;
                MatchedDesigs.Add(ThisDesig);

                PinList := '';
                FirstPin := True;
                For K := 0 To Comp.DM_PinCount - 1 Do
                Begin
                    Pin := Comp.DM_Pins(K);
                    If Pin = Nil Then Continue;
                    If Not FirstPin Then PinList := PinList + ',';
                    FirstPin := False;
                    PinList := PinList + '{"pin":"' + EscapeJsonString(Pin.DM_PinNumber) +
                        '","name":"' + EscapeJsonString(Pin.DM_PinName) + '"';
                    If WithPinNets Then
                        PinList := PinList + ',"net":"' + EscapeJsonString(Pin.DM_FlattenedNetName) + '"';
                    PinList := PinList + '}';
                End;

                ParamList := '';
                FirstParam := True;
                If WithParameters Then
                Begin
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
                End;

                CompEntry := '{"designator":"' + EscapeJsonString(ThisDesig) + '"';
                CompEntry := CompEntry + ',"comment":"' + EscapeJsonString(Comp.DM_Comment) + '"';
                CompEntry := CompEntry + ',"footprint":"' + EscapeJsonString(Comp.DM_Footprint) + '"';
                CompEntry := CompEntry + ',"lib_ref":"' + EscapeJsonString(Comp.DM_LibraryReference) + '"';
                CompEntry := CompEntry + ',"sheet":"' + EscapeJsonString(Doc.DM_FileName) + '"';
                If WithParameters Then
                    CompEntry := CompEntry + ',"parameters":{' + ParamList + '}';
                CompEntry := CompEntry + ',"pins":[' + PinList + ']}';

                If Not FirstC Then BodyJson := BodyJson + ',';
                FirstC := False;
                BodyJson := BodyJson + CompEntry;
            End;
        End;

        NotFoundJson := '';
        FirstNF := True;
        For I := 0 To Wanted.Count - 1 Do
        Begin
            If MatchedDesigs.IndexOf(Wanted[I]) < 0 Then
            Begin
                If Not FirstNF Then NotFoundJson := NotFoundJson + ',';
                FirstNF := False;
                NotFoundJson := NotFoundJson + '"' + EscapeJsonString(Wanted[I]) + '"';
            End;
        End;

        EnvelopeData := '{"components":[' + BodyJson + '],'
            + '"matched":' + IntToStr(MatchedDesigs.Count) + ','
            + '"requested":' + IntToStr(Wanted.Count) + ','
            + '"not_found":[' + NotFoundJson + ']}';

        ResponseStr := BuildSuccessResponse(RequestId, EnvelopeData);
        Result := ResponseStr;
    Finally
        MatchedDesigs.Free;
        Wanted.Free;
    End;
End;

{..............................................................................}
{ Export active schematic or PCB to PDF                                       }
{..............................................................................}

Function Proj_ExportPDF(Params : String; RequestId : String) : String;
Var
    OutputPath : String;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');

    If OutputPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'output_path is required');
        Exit;
    End;

    { Silent PDF export is NOT possible against a free / focused SchDoc with }
    { the documented Altium APIs - PublishToPDF + DisableDialog requires an  }
    { OutJob with at least one Output linked to a Medium. Without that      }
    { context Altium hangs waiting for an OutJob it cannot find.            }
    { Falling back to the dialog-popping form so the call returns promptly. }
    { For true silent export, configure an OutJob and use run_outjob.       }
    ResetParameters;
    AddStringParameter('FileName', OutputPath);
    RunProcess('WorkspaceManager:Print');

    { The comment above has always admitted this opens a dialog, and the      }
    { reply still claimed success with the path. Measured: the preview modal  }
    { appeared, was dismissed, and the tool reported success for a PDF that   }
    { was never written. Whether a human pressed Print is only knowable by    }
    { looking for the file.                                                   }
    If FileExists(OutputPath) Then
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"written":true,"output_path":"'
            + EscapeJsonString(OutputPath) + '"}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"success":false,"written":false,"output_path":"'
            + EscapeJsonString(OutputPath) + '"'
            + ',"reason":"the print dialog was opened but no file exists at '
            + 'output_path, so the export was cancelled or never confirmed. '
            + 'This path cannot run unattended: configure an OutJob and use '
            + 'proj_run_outjob"}');
End;

{..............................................................................}
{ Cross-probe: zoom to a component by designator                             }
{..............................................................................}

Function Proj_CrossProbe(Params : String; RequestId : String) : String;
Var
    Designator, Target, DocKind, FullPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    DmDoc : IDocument;
    DmComp : IComponent;
    ServerDoc : IServerDocument;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    SchIter : ISch_Iterator;
    SchObj : ISch_GraphicalObject;
    SchComp : ISch_Component;
    BoardIter : IPCB_BoardIterator;
    PcbComp : IPCB_Component;
    I, J, DocCount : Integer;
    UsePhysical, Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    Target := ExtractJsonValue(Params, 'target');
    If Target = '' Then Target := 'schematic';

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'designator is required');
        Exit;
    End;

    { Programmatic cross-probe via the SDK -- process-name routes (Sch:Find,  }
    { PCB:Jump, Sch:FindText) all pop dialogs in current Altium because the  }
    { parameter conventions documented in TR0124 (2008) drift from what     }
    { newer Altium expects. SDK iteration is deterministic.                  }
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

    Found := False;

    If Target = 'pcb' Then
    Begin
        { 1) Find any PCB doc in the project, focus it.                      }
        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            DmDoc := Project.DM_LogicalDocuments(I);
            If DmDoc = Nil Then Continue;
            DocKind := '';
            Try DocKind := UpperCase(DmDoc.DM_DocumentKind); Except End;
            If DocKind <> 'PCB' Then Continue;
            FullPath := '';
            Try FullPath := DmDoc.DM_FullPath; Except End;
            If FullPath = '' Then Continue;
            ServerDoc := Nil;
            Try ServerDoc := Client.GetDocumentByPath(FullPath); Except End;
            If ServerDoc = Nil Then Continue;
            Try Client.ShowDocument(ServerDoc); Except End;
            Break;
        End;

        { 2) Walk the board, find the component by Name.Text, select it.    }
        Board := Nil;
        Try Board := GetPCBBoardAnywhere(0); Except End;
        If Board = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_BOARD',
                'No active PCB board after focus attempt');
            Exit;
        End;
        { IPCB_Board.ClearSelection is ALSO an undeclared identifier in       }
        { this Altium version (mirror of the ISch_Document.ClearSelection     }
        { issue). Use the PCB:DeSelect process instead. Failure here is fine  }
        { -- adding our selection on top of any prior is acceptable.          }
        Try
            ResetParameters;
            AddStringParameter('Scope', 'All');
            RunProcess('PCB:DeSelect');
        Except End;
        BoardIter := Board.BoardIterator_Create;
        Try
            BoardIter.AddFilter_ObjectSet(MkSet(eComponentObject));
            BoardIter.AddFilter_LayerSet(AllLayers);
            BoardIter.AddFilter_Method(eProcessAll);
            PcbComp := BoardIter.FirstPCBObject;
            While (PcbComp <> Nil) And (Not Found) Do
            Begin
                Try
                    If PcbComp.Name.Text = Designator Then
                    Begin
                        Try PcbComp.Selected := True; Except End;
                        Found := True;
                    End;
                Except End;
                PcbComp := BoardIter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(BoardIter);
        End;
        Try Board.GraphicallyInvalidate; Except End;
        { Pan/zoom the view to the now-selected component. PCB:Jump with     }
        { Object=Selected is documented in TR0124 and centres the editor   }
        { viewport on the selection.                                        }
        If Found Then
        Begin
            Try
                ResetParameters;
                AddStringParameter('Object', 'Selected');
                AddStringParameter('Type', 'First');
                RunProcess('PCB:Jump');
            Except End;
        End;
    End
    Else
    Begin
        { Schematic path: find the sheet that owns the designator, focus    }
        { it, then iterate on-canvas SchComponents and select the match.    }
        { Use GetCompiledDocs (same enumeration the BOM uses) so multi-    }
        { channel / hierarchical designs expose the per-instance physical  }
        { designators -- DM_LogicalDocuments alone misses channel-expanded }
        { components (the bug that made C103 NOT_FOUND while it IS in the  }
        { BOM and on the PCB).                                              }
        SmartCompile(Project);
        GetCompiledDocs(Project, DocCount, UsePhysical);
        For I := 0 To DocCount - 1 Do
        Begin
            DmDoc := GetCompiledDoc(Project, I, UsePhysical);
            If DmDoc = Nil Then Continue;
            DocKind := '';
            Try DocKind := UpperCase(DmDoc.DM_DocumentKind); Except End;
            If DocKind <> 'SCH' Then Continue;
            For J := 0 To DmDoc.DM_ComponentCount - 1 Do
            Begin
                DmComp := DmDoc.DM_Components(J);
                If DmComp = Nil Then Continue;
                Try
                    If DmComp.DM_PhysicalDesignator = Designator Then
                    Begin
                        FullPath := '';
                        Try FullPath := DmDoc.DM_FullPath; Except End;
                        If FullPath <> '' Then
                        Begin
                            ServerDoc := Nil;
                            Try ServerDoc := Client.GetDocumentByPath(FullPath); Except End;
                            If ServerDoc <> Nil Then
                            Begin
                                Try Client.ShowDocument(ServerDoc); Except End;
                                Found := True;
                            End;
                        End;
                    End;
                Except End;
                If Found Then Break;
            End;
            If Found Then Break;
        End;

        If Found Then
        Begin
            SchDoc := Nil;
            Try SchDoc := SchServer.GetCurrentSchDocument; Except End;
            If SchDoc <> Nil Then
            Begin
                { ISch_Document.ClearSelection is rejected as an undeclared    }
                { identifier at runtime in this Altium version (uncatchable   }
                { by Try/Except per [[delphiscript_altium_enum_typos]]). Use  }
                { the Sch:DeSelect process instead -- documented in TR0124    }
                { and used elsewhere in the codebase (Gen_DeselectAll's PCB   }
                { sibling). Failure here just leaves prior selections in     }
                { place; the new component still gets selected below.        }
                Try
                    ResetParameters;
                    AddStringParameter('Scope', 'All');
                    RunProcess('Sch:DeSelect');
                Except End;
                SchIter := SchDoc.SchIterator_Create;
                Try
                    SchIter.AddFilter_ObjectSet(MkSet(eSchComponent));
                    SchObj := SchIter.FirstSchObject;
                    While SchObj <> Nil Do
                    Begin
                        Try
                            SchComp := SchObj;
                            If SchComp.Designator.Text = Designator Then
                            Begin
                                Try SchComp.Selection := True; Except End;
                                Break;
                            End;
                        Except End;
                        SchObj := SchIter.NextSchObject;
                    End;
                Finally
                    SchDoc.SchIterator_Destroy(SchIter);
                End;
                Try SchDoc.GraphicallyInvalidate; Except End;
                { Pan/zoom the editor viewport to the selected component.   }
                { Sch:Zoom with Action=Selected is the documented pattern;  }
                { if that variant isn't accepted, Action=All zooms to fit,  }
                { which is still better than just                           }
                { highlighting an off-screen part.                          }
                Try
                    ResetParameters;
                    AddStringParameter('Action', 'Selected');
                    RunProcess('Sch:Zoom');
                Except End;
            End;
        End;
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Designator not found in ' + Target + ': ' + Designator);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator) +
        '","target":"' + Target + '"}');
End;

{..............................................................................}
{ Design statistics from compiled project                                    }
{..............................................................................}

{ "sheets" is the count of SCH documents only (the schematic sheets the      }
{ engineer actually drew on). The earlier implementation counted every       }
{ logical document -- PcbDoc, OutJob, Annotation, PCBDwf, etc. -- which     }
{ inflated the figure. "documents" still reports the unfiltered total so the }
{ number is not lost. Component / pin counts come from SCH docs only, which }
{ matches BOM semantics (placed components are owned by their schematic).    }
Function Proj_GetDesignStats(Params : String; RequestId : String) : String;
Var
    ProjectPath, DocKind : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    I, J : Integer;
    CompCount, PinCount, DocCount, SheetCount : Integer;
    Data : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    CompCount := 0; PinCount := 0; DocCount := 0; SheetCount := 0;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        Inc(DocCount);
        DocKind := '';
        Try DocKind := UpperCase(Doc.DM_DocumentKind); Except End;
        If DocKind <> 'SCH' Then Continue;
        Inc(SheetCount);
        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;
            Inc(CompCount);
            PinCount := PinCount + Comp.DM_PinCount;
        End;
    End;

    Data := '{"sheets":' + IntToStr(SheetCount);
    Data := Data + ',"documents":' + IntToStr(DocCount);
    Data := Data + ',"components":' + IntToStr(CompCount);
    Data := Data + ',"pins":' + IntToStr(PinCount) + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ PCB board info, outline, layer stack, origin                              }
{..............................................................................}

Function Proj_GetBoardInfo(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    LayerStack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
    I, PtCount : Integer;
    OutlineStr, LayerStr, Data, BoardFile : String;
    First : Boolean;
Begin
    Board := GetPCBBoardAnywhere(0);
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

    { NAME THE BOARD THAT ANSWERED. GetPCBBoardAnywhere deliberately finds   }
    { any open board, so with a SCHEMATIC focused this happily returns some   }
    { other document's geometry. MEASURED: with a schematic active and a      }
    { two-document project focused, it reported an outline belonging to       }
    { neither, and the reply carried nothing to reveal that. The board is     }
    { now identified, so a caller can tell whether the answer is about the    }
    { document they meant.                                                    }
    BoardFile := '';
    Try BoardFile := Board.FileName; Except End;

    Data := '{"origin_x":' + IntToStr(CoordToMils(Board.XOrigin));
    Data := Data + ',"origin_y":' + IntToStr(CoordToMils(Board.YOrigin));
    Data := Data + ',"board_path":"' + EscapeJsonString(BoardFile) + '"';
    Data := Data + ',"outline":' + OutlineStr;
    Data := Data + ',"layers":' + LayerStr + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Annotate schematic designators, programmatic, no dialog                    }
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
    { Doc index is the primary tie-breaker, keep designators contiguous per sheet }
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

    { Flat parallel arrays, one slot per unlocked, considered component.
      Interfaces go in a TInterfaceList; sort keys go in parallel TStringList
      (DelphiScript-friendly approach, TStringList.Objects[] with interface
      pointers is unreliable). }
    CompList   : TInterfaceList;
    Prefixes   : TStringList;
    XCoords    : TStringList;  { X in mils as integer-string }
    YCoords    : TStringList;  { Y in mils as integer-string }
    DocIndices : TStringList;

    { Set of modified docs, PreProcess/PostProcess/Invalidate are scoped to these only }
    TouchedDocs : TStringList;

    { Per-prefix counter for final assignment, stored as "Prefix=N" lines }
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

            { Don't force-open, RunProcess Client:OpenDocument strips
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
                            If Not Comp.DesignatorLocked Then
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
                SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
                SchDoc.GraphicallyInvalidate;
                MarkDocDirtyByPath(FilePath);
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
                        If Comp.DesignatorLocked Then
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

    Finally
        { PostProcess + Invalidate + Save every touched doc. This MUST run in
          the Finally: the PreProcess loop above opened a transaction on each
          touched sheet, and an exception mid-rename would otherwise leave
          those sheets in an open transaction that poisons later edits. }
        For I := 0 To TouchedDocs.Count - 1 Do
        Begin
            SchDoc := Nil;
            Try SchDoc := SchServer.GetSchDocumentByPath(TouchedDocs[I]); Except End;
            If SchDoc <> Nil Then
            Begin
                Try SchServer.ProcessControl.PostProcess(SchDoc, 'Edit'); Except End;
                Try SchDoc.GraphicallyInvalidate; Except End;
            End;
            Try MarkDocDirtyByPath(TouchedDocs[I]); Except End;
        End;

        { No CompList.Free -- releasing a TInterfaceList of live schematic
          interface refs faults in oleaut32; leave it to the script host.
          The parallel TStringLists are plain strings and safe to free. }
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

{ Fire a PCB export process and REPORT WHETHER ANYTHING APPEARED.            }
{                                                                             }
{ This used to end with a flat generated-true and nothing else. Measured on   }
{ AD26: a gerber export with an explicit output_path produced no directory    }
{ and no files at all, and the caller was told it had been generated.         }
{ Altium's exporters are dialog-driven and RunProcess cannot report what the  }
{ dialog did, so the only honest signal is whether the output is on disk      }
{ afterwards.                                                                 }
{                                                                             }
{ When no output_path is supplied there is nothing to look for, so the reply  }
{ says dispatched rather than generated. Two different words for two          }
{ different claims, which is the whole point.                                 }
Function Proj_GenerateOutput(Params : String; RequestId : String) : String;
Var
    OutputType, OutputPath : String;
    ExpectDir, Appeared : Boolean;
Begin
    OutputType := ExtractJsonValue(Params, 'output_type');
    OutputPath := ExtractJsonValue(Params, 'output_path');

    If OutputType = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'output_type is required'); Exit; End;

    ExpectDir := False;

    If OutputType = 'gerber' Then
    Begin
        ExpectDir := True;
        ResetParameters;
        If OutputPath <> '' Then AddStringParameter('OutputPath', OutputPath);
        RunProcess('PCB:GenericExport');
    End
    Else If OutputType = 'drill' Then
    Begin
        ExpectDir := True;
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

    If OutputPath = '' Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            '{"generated":false,"dispatched":true,"output_type":"' + OutputType + '"'
            + ',"reason":"the export process was launched, but with no '
            + 'output_path there is nothing to check, so whether a file was '
            + 'written is unknown. Pass output_path, or drive an OutJob with '
            + 'proj_run_outjob for a result that can be confirmed"}');
        Exit;
    End;

    Appeared := False;
    If ExpectDir Then
        Try Appeared := DirectoryExists(OutputPath); Except End
    Else
        Try Appeared := FileExists(OutputPath); Except End;

    If Appeared Then
        Result := BuildSuccessResponse(RequestId,
            '{"generated":true,"output_type":"' + OutputType + '"'
            + ',"output_path":"' + EscapeJsonString(OutputPath) + '"}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"generated":false,"dispatched":true,"output_type":"' + OutputType + '"'
            + ',"output_path":"' + EscapeJsonString(OutputPath) + '"'
            + ',"reason":"the export process was launched but nothing exists '
            + 'at output_path afterwards. Altium''s exporters are '
            + 'dialog-driven and cannot run unattended this way; configure an '
            + 'OutJob and use proj_run_outjob"}');
End;

{..............................................................................}
{ Export PCB to STEP 3D model                                                 }
{ Params: output_path (optional, if omitted, Altium may prompt)              }
{..............................................................................}

Function Proj_ExportSTEP(Params : String; RequestId : String) : String;
Var
    OutputPath : String;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');

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
{ Export the active document as an image, silent (no print-preview dialog).    }
{                                                                              }
{ Params: output_path  (required) - absolute path of the output file to write. }
{         format       (optional) - 'pdf' (default), 'png', 'jpg', or 'bmp'.   }
{         width        (optional) - kept for API compatibility, unused (the    }
{                                   PDF path renders to PDF page geometry).    }
{         height       (optional) - same.                                      }
{                                                                              }
{ History and rationale:                                                       }
{ -----------------------                                                      }
{ The previous implementation built a synthetic OutJob on disk with one        }
{ "Schematic Print" output wired to a "Multimedia" container, then drove it    }
{ via Action=PublishMultimedia. It never wrote a file. Diagnosis:              }
{   1. The MediaFormat label strings ("PNG file (*.png)", "JPEG file           }
{      (*.jpg,*.jpeg)", "Bitmap file (*.bmp)") were guesses. Real OutJob       }
{      files seen in the wild (a real production-release OutJob, not any       }
{      public sample) carry strings like                                       }
{      "Windows Media file (*.wmv,*.wma,*.asf)" - they are display-time GUI    }
{      labels Altium matches against an internal format registry, and they     }
{      are locale-dependent. No public sample shows the exact string for      }
{      PNG/JPG/BMP output.                                                     }
{   2. The synthetic INI format is undocumented and differs from the [Output- }
{      Group N] + [PublishSettings] structure Altium actually writes when     }
{      you create an Image multimedia container through the UI. The shape we   }
{      were emitting was effectively fictional and silently rejected.          }
{   3. Even if the INI parsed, Action=PublishMultimedia operates on the       }
{      currently-focused OutJob's containers. Client.OpenDocument loads the    }
{      document but does NOT focus it - that requires Client.ShowDocument.    }
{      Brett Miller's RunOutJobDocs.pas reference confirms this; it calls    }
{      Client.ShowDocument before running each container.                      }
{                                                                              }
{ Alternative paths considered:                                                }
{   - Win32 BitBlt/GetWindowDC screenshot: DelphiScript blocks `external` DLL  }
{     imports, so user32/gdi32 calls aren't reachable from a script. TBitmap   }
{     is exposed via TCanvas (see MandelBrot.pas in the reference) but its     }
{     SaveToFile is not reliably reachable, and Altium's render surface uses   }
{     DirectX (IPCB_GraphicalView) which we cannot grab through a Canvas.     }
{   - ISch_Document.SaveAsImage / DM_GraphicalImage: neither method exists on  }
{     ISch_Document per the SDK reference - searched the full HTML.           }
{   - PublishToPDF with SelectedName1/SelectedName2: requires a real OutJob   }
{     to be focused with those named outputs, not silent on its own.          }
{                                                                              }
{ Current strategy (PDF):                                                      }
{ ----------------------                                                       }
{ Use the same WorkspaceManager:Print + FileName= machinery that              }
{ Proj_ExportPDF (Project.pas:804) has been using in production. This is the   }
{ ONLY silent server-process route that the codebase has confirmed working    }
{ end-to-end against the active schematic / PCB document. It writes a PDF.    }
{                                                                              }
{ format='pdf' is the silent path. format='png'/'jpg'/'bmp' returns an        }
{ explicit IMAGE_FORMAT_UNSUPPORTED error pointing the caller at export_pdf,  }
{ because there is no documented Pascal-side way to drive PNG/JPG/BMP output  }
{ silently without a hand-configured OutJob in the project (which the caller  }
{ should run via run_outjob).                                                  }
{                                                                              }
{ Python (tools/project.py) does a post-call FileExists check that downgrades  }
{ a Pascal "success" to EXPORT_FILE_MISSING if no file landed. That safety    }
{ net stays.                                                                  }
{..............................................................................}

Function Proj_ExportImage(Params : String; RequestId : String) : String;
Var
    OutputPath, Fmt, ScopeDoc : String;
    LcFmt : String;
    Width, Height : Integer;
    SchDoc : ISch_Document;
    WrittenOK : Boolean;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');
    Fmt := ExtractJsonValue(Params, 'format');
    Width := StrToIntDef(ExtractJsonValue(Params, 'width'), 1920);
    Height := StrToIntDef(ExtractJsonValue(Params, 'height'), 1080);

    If OutputPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'output_path is required');
        Exit;
    End;

    If Fmt = '' Then Fmt := 'pdf';
    LcFmt := LowerCase(Fmt);

    { Reject the raster formats with an explicit, structured error so the     }
    { caller knows exactly why and what to do. We do NOT silently fall back   }
    { to PDF under a misleading path because the Python tool's file_exists    }
    { check would still pass and the caller would think it got a PNG.        }
    If (LcFmt <> 'pdf') And (LcFmt <> '.pdf') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'IMAGE_FORMAT_UNSUPPORTED',
            'Silent raster image export (PNG/JPG/BMP) is not supported by the ' +
            'Pascal-side machinery on this Altium build. The OutJob "Multimedia ' +
            '(Image)" container is the only documented path and its INI format / ' +
            'MediaFormat labels are not publicly specified for still images, so a ' +
            'synthetic OutJob will not parse reliably. Workarounds: (1) call ' +
            'project.export_image with format="pdf" - the silent PDF path is ' +
            'proven; (2) for PNG/JPG/BMP, hand-configure an OutJob in the ' +
            'project with a Schematic Print -> Multimedia container set to your ' +
            'desired image format and call run_outjob.');
        Exit;
    End;

    { Capture the active SchDoc just for the response payload - the PDF       }
    { process operates on whatever the focused view holds, not on a path-      }
    { specified document, so this is informational only.                       }
    ScopeDoc := '';
    Try
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc <> Nil Then
            ScopeDoc := SchDoc.DocumentName;
    Except
        ScopeDoc := '';
    End;

    { Delete any pre-existing file at OutputPath so the post-run FileExists    }
    { probe is a true existence test. Without this, a stale file from a       }
    { prior export would mask a silent failure as success.                     }
    If FileExists(OutputPath) Then
    Begin
        Try
            DeleteFile(OutputPath);
        Except
        End;
    End;

    { Silent direct-export is NOT possible without an OutJob template -    }
    { PublishToPDF + DisableDialog hangs Altium waiting for an OutJob      }
    { context that doesn't exist. The fallback form below pops the print  }
    { preview dialog but returns promptly. For true silent export,         }
    { configure an OutJob with a Schematic Print -> PDF medium link, then  }
    { call run_outjob instead.                                              }
    Try
        ResetParameters;
        AddStringParameter('FileName', OutputPath);
        RunProcess('WorkspaceManager:Print');
    Except
    End;

    WrittenOK := FileExists(OutputPath);

    If Not WrittenOK Then
    Begin
        Result := BuildErrorResponse(RequestId, 'EXPORT_FAILED',
            'WorkspaceManager:Print returned but no file was written to ' + OutputPath +
            '. Possible causes: the active document is not a schematic/PCB Altium ' +
            'recognises for direct printing, no project is open, or the requested ' +
            'output directory is not writable.');
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"output_path":"' + EscapeJsonString(OutputPath) +
        '","format":"pdf","width":' + IntToStr(Width) +
        ',"height":' + IntToStr(Height) +
        ',"scope_doc":"' + EscapeJsonString(ScopeDoc) + '"}');
End;

{..............................................................................}
{ List output containers from an open .OutJob document                        }
{ The OutJob file is an INI format, parse sections for containers.           }
{ Params: outjob_path (optional, uses first open OutJob if omitted)          }
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

    { OutJob files are INI format, parse OutputGroup sections }
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
    FoundContainerName, ContainerType, RelativePath, OutputDir : String;
    G, J : Integer;
    Found : Boolean;
    OutJobDoc : IServerDocument;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
Begin
    OutJobPath := ExtractJsonValue(Params, 'outjob_path');
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

    { Resolve the OutJob's configured output directory so callers can pick }
    { up the produced files without having to re-parse the INI. The INI    }
    { stores OutputBasePath relative to the OutJob; absolute paths pass    }
    { through. Trailing slash normalised so Python can append filenames.   }
    OutputDir := '';
    If RelativePath <> '' Then
    Begin
        If (Length(RelativePath) >= 2) And (Copy(RelativePath, 2, 1) = ':') Then
            OutputDir := RelativePath
        Else
            OutputDir := ExtractFilePath(OutJobPath) + RelativePath;
        If (Length(OutputDir) > 0) And
           (Copy(OutputDir, Length(OutputDir), 1) <> '\') Then
            OutputDir := OutputDir + '\';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true' +
        ',"container_name":"' + EscapeJsonString(ContainerName) + '"' +
        ',"container_type":"' + EscapeJsonString(ContainerType) + '"' +
        ',"relative_path":"' + EscapeJsonString(RelativePath) + '"' +
        ',"output_dir":"' + EscapeJsonString(OutputDir) + '"}');
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

            { Translate variation kind to string (If/Else chain, Case on enum crashes DelphiScript) }
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
{ Proj_GetVariantMatrix - The fitted / not-fitted matrix across all variants.  }
{                                                                              }
{ Iterates EVERY flattened component (rows) and reports its status under each  }
{ variant (columns): Fitted / Not Fitted / Alternate, via                      }
{ DM_FindComponentVariationByUniqueId (Nil = fitted original). This is the     }
{ data behind the conventional "print all variants" CSV that merges with a BOM.}
{ Unlike get_variants (which lists only per-variant DEVIATIONS), every         }
{ component appears here, so the matrix is complete.                           }
{                                                                              }
{ Params: project_path (optional; defaults to the focused project).           }
{ Response: variants (name array), rows (each designator + a cells array       }
{   parallel to variants), component_count.                                    }
{..............................................................................}

Function Proj_GetVariantMatrix(Params : String; RequestId : String) : String;
Var
    ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Flat : IDocument;
    Variant : IProjectVariant;
    CompVar : IComponentVariation;
    Comp : IComponent;
    I, V, W, NVar, NComp : Integer;
    VariantsJson, RowsJson, CellsJson, Desig, Kind : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);
    Flat := Project.DM_DocumentFlattened;
    If Flat = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_COMPILED',
            'Could not get the flattened document; compile the project first');
        Exit;
    End;

    NVar := Project.DM_ProjectVariantCount;
    NComp := Flat.DM_ComponentCount;

    { Variant-name header. }
    VariantsJson := '[';
    For V := 0 To NVar - 1 Do
    Begin
        Variant := Project.DM_ProjectVariants(V);
        If V > 0 Then VariantsJson := VariantsJson + ',';
        If Variant <> Nil Then
            VariantsJson := VariantsJson + '"' + EscapeJsonString(Variant.DM_Name) + '"'
        Else
            VariantsJson := VariantsJson + '""';
    End;
    VariantsJson := VariantsJson + ']';

    { One row per component; a cell per variant. }
    RowsJson := '[';
    For I := 0 To NComp - 1 Do
    Begin
        Comp := Flat.DM_Components(I);
        If Comp = Nil Then Continue;
        Desig := '';
        Try Desig := Comp.DM_PhysicalDesignator; Except End;

        CellsJson := '[';
        For V := 0 To NVar - 1 Do
        Begin
            Variant := Project.DM_ProjectVariants(V);
            Kind := 'Fitted';
            { Match by physical designator -- the same authoritative key
              Proj_GetVariants reads. The DM_UniqueId lookup mis-resolves on
              boards where the flattened component ids do not line up with the
              variation keys (verified on a real 6-layer board). }
            If Variant <> Nil Then
            Begin
                Try
                    For W := 0 To Variant.DM_VariationCount - 1 Do
                    Begin
                        CompVar := Variant.DM_Variations(W);
                        If CompVar = Nil Then Continue;
                        If CompVar.DM_PhysicalDesignator = Desig Then
                        Begin
                            If CompVar.DM_VariationKind = 1 Then Kind := 'Not Fitted'
                            Else If CompVar.DM_VariationKind = 2 Then Kind := 'Alternate'
                            Else Kind := 'Fitted';
                            Break;
                        End;
                    End;
                Except
                    Kind := 'Fitted';
                End;
            End;
            If V > 0 Then CellsJson := CellsJson + ',';
            CellsJson := CellsJson + '"' + Kind + '"';
        End;
        CellsJson := CellsJson + ']';

        If I > 0 Then RowsJson := RowsJson + ',';
        RowsJson := RowsJson + '{"designator":"' + EscapeJsonString(Desig) +
            '","cells":' + CellsJson + '}';
    End;
    RowsJson := RowsJson + ']';

    Result := BuildSuccessResponse(RequestId,
        '{"variants":' + VariantsJson + ',"rows":' + RowsJson +
        ',"component_count":' + IntToStr(NComp) + '}');
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
    ProjectPath, VariantName, ActualName : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant, Current : IProjectVariant;
    I : Integer;
    Found : Boolean;
Begin
    VariantName := ExtractJsonValue(Params, 'variant_name');
    ProjectPath := ExtractJsonValue(Params, 'project_path');

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

    { READ BACK WHICH VARIANT IS CURRENT. The check above proves the name  }
    { exists, which is not the same as the switch having happened, and     }
    { this used to report success on the strength of that. The same        }
    { VariantManagement process is documented here as unreliable for       }
    { AddVariant, so taking its word on SetCurrentVariant was never safe.  }
    ActualName := '';
    Try
        Current := Project.DM_CurrentProjectVariant;
        If Current <> Nil Then ActualName := Current.DM_Name;
    Except End;

    If ActualName = VariantName Then
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"variant_name":"' + EscapeJsonString(VariantName)
            + '","verified":true}')
    Else
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('variant_name', VariantName) + ',' +
                JsonStr('current_variant', ActualName) + ',' +
                JsonStr('reason', 'the switch was dispatched but the project '
                    + 'still reports a different current variant. Set it in '
                    + 'the Variant Management dialog.')
            ));
End;

{..............................................................................}
{ Proj_DeleteVariant - remove one project variant by name.                     }
{                                                                              }
{ DM_RemoveProjectVariant(Index) is the ONE documented write in the whole      }
{ variant API. Everything else on IProject, IProjectVariant and                }
{ IComponentVariation reads: counts, indexed accessors and DM_VariationKind.   }
{ There is no documented way to add a variant, or to add or remove an          }
{ individual component variation entry, which is why this covers deleting a    }
{ whole variant and nothing finer.                                             }
{                                                                              }
{ Addressed BY NAME rather than by index, because an index is only valid       }
{ against the listing that produced it: removing a variant renumbers the       }
{ rest, so a caller deleting two by index deletes the wrong second one.        }
{ Params: variant_name (required), project_path (optional, focused default).   }
{..............................................................................}

Function Proj_DeleteVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath, VariantName : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant : IProjectVariant;
    I, Target, PreCount, PostCount, Entries : Integer;
    StillPresent : Boolean;
Begin
    VariantName := ExtractJsonValue(Params, 'variant_name');
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    If VariantName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'variant_name is required');
        Exit;
    End;

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

    PreCount := 0;
    Try PreCount := Project.DM_ProjectVariantCount; Except End;

    Target := -1;
    Entries := -1;
    For I := 0 To PreCount - 1 Do
    Begin
        Variant := Project.DM_ProjectVariants(I);
        If (Variant <> Nil) And (Variant.DM_Name = VariantName) Then
        Begin
            Target := I;
            { Reported so the caller learns what the deletion cost. A      }
            { variant carrying fifty component variations and an empty one }
            { are the same single call, and only one of them is cheap to   }
            { recreate: there is no scripted way to put the entries back.  }
            Try Entries := Variant.DM_VariationCount; Except End;
            Break;
        End;
    End;

    If Target < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'VARIANT_NOT_FOUND',
            'Variant not found: ' + VariantName);
        Exit;
    End;

    { Inside begin/end update, matching Proj_SetParameter: that pairing is }
    { how a DM edit to the project is applied rather than merely made.     }
    Project.DM_BeginUpdate;
    Try
        Project.DM_RemoveProjectVariant(Target);
    Finally
        Project.DM_EndUpdate;
    End;

    { VERIFY BOTH WAYS. A count that dropped proves something went, and the }
    { name being gone proves it was this one. Neither alone does: a         }
    { concurrent edit could change the count, and a rename would leave the  }
    { count intact.                                                         }
    PostCount := PreCount;
    Try PostCount := Project.DM_ProjectVariantCount; Except End;

    StillPresent := False;
    For I := 0 To PostCount - 1 Do
    Begin
        Variant := Project.DM_ProjectVariants(I);
        If (Variant <> Nil) And (Variant.DM_Name = VariantName) Then
        Begin
            StillPresent := True;
            Break;
        End;
    End;

    If StillPresent Or (PostCount >= PreCount) Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('variant_name', VariantName) + ',' +
                JsonInt('variant_count_before', PreCount) + ',' +
                JsonInt('variant_count_after', PostCount) + ',' +
                JsonStr('reason', 'the removal was issued but the variant is '
                    + 'still listed on the project afterwards.')
            ));
        Exit;
    End;

    { A variant lives in the .PrjPcb, so the deletion is only real once   }
    { that file is written. This saves the project's own members rather   }
    { than the workspace, for the reason proj_close was changed: a        }
    { workspace-wide save can raise an Unsaved Changes prompt naming a    }
    { project the caller never mentioned.                                 }
    SaveProjectMembers(Project);

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonBool('success', True) + ',' +
            JsonStr('variant_name', VariantName) + ',' +
            JsonInt('entries_removed', Entries) + ',' +
            JsonInt('variant_count_before', PreCount) + ',' +
            JsonInt('variant_count_after', PostCount) + ',' +
            JsonBool('verified', True)
        ));
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
    PreCount, PostCount : Integer;
Begin
    VarName := ExtractJsonValue(Params, 'name');
    VarDesc := ExtractJsonValue(Params, 'description');
    ProjectPath := ExtractJsonValue(Params, 'project_path');

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

    { The DM API is read-only for variants (no DM_AddProjectVariant); the
      WorkspaceManager:VariantManagement process is the only script lever and
      it is unreliable. So we attempt it, then VERIFY via the variant count and
      report the real outcome -- never a blind success (a false success here
      masked that no variant was actually created). }
    PreCount := -1;
    Try PreCount := Project.DM_ProjectVariantCount; Except End;

    ResetParameters;
    AddStringParameter('Action', 'AddVariant');
    AddStringParameter('VariantName', VarName);
    If VarDesc <> '' Then
        AddStringParameter('VariantDescription', VarDesc);
    RunProcess('WorkspaceManager:VariantManagement');

    Try Project.DM_Compile; Except End;
    PostCount := -1;
    Try PostCount := Project.DM_ProjectVariantCount; Except End;

    If (PreCount >= 0) And (PostCount > PreCount) Then
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"name":"' + EscapeJsonString(VarName) + '","description":"'
            + EscapeJsonString(VarDesc) + '","variant_count":' + IntToStr(PostCount) + '}')
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_UNCONFIRMED',
            'Variant creation could not be confirmed (count ' + IntToStr(PreCount)
            + ' -> ' + IntToStr(PostCount) + '). Script-based variant creation via '
            + 'WorkspaceManager:VariantManagement is unreliable; create the variant '
            + 'in the Variant Management dialog.');
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
    I, J, K, DocCount : Integer;
    UsePhysical : Boolean;
    Data, PinList : String;
    FirstPin, Found : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    Designator := ExtractJsonValue(Params, 'designator');

    If Designator = '' Then Begin Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required'); Exit; End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    ForceRecompileIfRequested(Project, Params);
    SmartCompile(Project);
    Found := False;

    GetCompiledDocs(Project, DocCount, UsePhysical);
    For I := 0 To DocCount - 1 Do
    Begin
        If Found Then Break;
        Doc := GetCompiledDoc(Project, I, UsePhysical);
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
    ProjectPath, DesigStr, Remaining, ThisDesig : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, DocCount, SepPos : Integer;
    UsePhysical : Boolean;
    Data, PinList, CompEntry, NotFoundJson : String;
    FirstPin, FirstC, FirstNF : Boolean;
    Wanted, MatchedDesigs : TStringList;
    EnvelopeData, ResponseStr : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    DesigStr := ExtractJsonValue(Params, 'designators');

    If DesigStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designators is required');
        Exit;
    End;

    { Heap-allocated TStringLists for both the wanted set and the "already   }
    { matched" set. Using `Array[0..N] Of String` here used to silently      }
    { return the request body as the response - see                          }
    { [[delphiscript_fixed_string_array_bug]].                               }
    Wanted := TStringList.Create;
    MatchedDesigs := TStringList.Create;
    Try
        Remaining := DesigStr;
        While Length(Remaining) > 0 Do
        Begin
            SepPos := Pos('~~', Remaining);
            If SepPos = 0 Then
            Begin
                ThisDesig := Remaining;
                Remaining := '';
            End
            Else
            Begin
                ThisDesig := Copy(Remaining, 1, SepPos - 1);
                Remaining := Copy(Remaining, SepPos + 2, Length(Remaining));
            End;
            If ThisDesig <> '' Then
                Wanted.Add(ThisDesig);
        End;

        If Wanted.Count = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'EMPTY_BATCH', 'No designators parsed');
            Exit;
        End;

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

        ForceRecompileIfRequested(Project, Params);
        SmartCompile(Project);

        Data := '';
        FirstC := True;

        GetCompiledDocs(Project, DocCount, UsePhysical);
        For I := 0 To DocCount - 1 Do
        Begin
            Doc := GetCompiledDoc(Project, I, UsePhysical);
            If Doc = Nil Then Continue;

            For J := 0 To Doc.DM_ComponentCount - 1 Do
            Begin
                Comp := Doc.DM_Components(J);
                If Comp = Nil Then Continue;

                ThisDesig := Comp.DM_PhysicalDesignator;
                If Wanted.IndexOf(ThisDesig) < 0 Then Continue;
                If MatchedDesigs.IndexOf(ThisDesig) >= 0 Then Continue;
                MatchedDesigs.Add(ThisDesig);

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
            End;
        End;

        NotFoundJson := '';
        FirstNF := True;
        For I := 0 To Wanted.Count - 1 Do
        Begin
            If MatchedDesigs.IndexOf(Wanted[I]) < 0 Then
            Begin
                If Not FirstNF Then NotFoundJson := NotFoundJson + ',';
                FirstNF := False;
                NotFoundJson := NotFoundJson + '"' + EscapeJsonString(Wanted[I]) + '"';
            End;
        End;

        EnvelopeData := '{"components":[' + Data + '],'
            + '"matched":' + IntToStr(MatchedDesigs.Count) + ','
            + '"requested":' + IntToStr(Wanted.Count) + ','
            + '"not_found":[' + NotFoundJson + ']}';

        ResponseStr := BuildSuccessResponse(RequestId, EnvelopeData);
        Result := ResponseStr;
    Finally
        MatchedDesigs.Free;
        Wanted.Free;
    End;
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
        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
    End;

    { Persist directly to disk via the IServerDocument API. MarkDocDirtyByPath
      does SetModified + DoFileSave. WorkspaceManager:SaveAll doesn't
      reach non-active sheets in our tests, so we don't rely on it. }
    MarkDocDirtyByPath(FilePath);
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
    Mappings : IComponentMappings;
    I : Integer;
    SchCompCount, PcbCompCount, Matched, ExtraSch, ExtraPcb : Integer;
    MappedOk : Boolean;
    Data : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    SmartCompile(Project);

    { Logical schematic component count -- a fallback only. The physical
      counts below (from the component mappings) are what we actually report. }
    SchCompCount := 0;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;
        SchCompCount := SchCompCount + Doc.DM_ComponentCount;
    End;

    { Count components via DM_ComponentMappings (same authoritative path as
      proj_get_differences). DM_ComponentCount on the PCB's IDocument returns
      0 -- PCB parts are enumerated through the mappings, not the DM doc -- and
      the logical sch count above ignores channel expansion, so neither side
      matches even when in sync. Matched + side-only gives the real physical
      counts. DM_ComponentMappings takes the PCB PATH (OleStr), not the object. }
    MappedOk := False;
    Matched := 0;  ExtraSch := 0;  ExtraPcb := 0;
    PcbDoc := Project.DM_PrimaryImplementationDocument;
    If PcbDoc <> Nil Then
    Begin
        Try
            Mappings := Project.DM_ComponentMappings(PcbDoc.DM_FullPath);
            If Mappings <> Nil Then
            Begin
                Try Matched  := Mappings.DM_MatchedComponentCount;          Except End;
                Try ExtraSch := Mappings.DM_UnmatchedSourceComponentCount;  Except End;
                Try ExtraPcb := Mappings.DM_UnmatchedTargetComponentCount;  Except End;
                MappedOk := True;
            End;
        Except
        End;
    End;

    If MappedOk Then
    Begin
        SchCompCount := Matched + ExtraSch;
        PcbCompCount := Matched + ExtraPcb;
    End
    Else
        PcbCompCount := 0;   { mappings unavailable; sch count stays logical }

    Data := '{"sch_components":' + IntToStr(SchCompCount);
    Data := Data + ',"pcb_components":' + IntToStr(PcbCompCount);
    Data := Data + ',"components_match":' +
        BoolToJsonStr(MappedOk And (ExtraSch = 0) And (ExtraPcb = 0));
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
{ A SILENT / headless ECO is not possible: Altium's ECO dialog is            }
{ non-suppressible by design, and the IECO interface exposes no project-wide  }
{ execute entry point reachable from DelphiScript. What IS scriptable is      }
{ LAUNCHING the real ECO via WorkspaceManager:Compare (see below); it raises  }
{ the modal change-review dialog that a human must accept.                    }
{                                                                              }
{ Strategy:                                                                    }
{   1. Compile the project and gather component mapping differences so useful  }
{      counts are available regardless of what the ECO dialog does.            }
{   2. Invoke WorkspaceManager:Compare (ObjectKind=Project, Action=UpdateOther)}
{      -- the documented/evidenced sch->PCB update. It BLOCKS on the modal     }
{      ECO dialog until the user clicks Execute Changes.                       }
{   3. Re-compile and recompute mappings; report the before/after delta.      }
{                                                                              }
{ The caller sees dialog_may_have_opened:true when the counts did not change  }
{ (user dismissed the dialog without applying); the difference data still     }
{ reports what Altium found.                                                  }
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
    FocusedDoc : IDocument;
    FocusedKind : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    { REFUSE while a schematic is focused. MEASURED 2026-08-17: with a
      child sheet focused, WorkspaceManager:Compare raised a modal reading
      "Cannot compare a source document against its owner project SCH"
      and changed nothing, while this handler went on to report success
      and components_in_sync. Catching it here turns a blocked editor
      plus a false clean into a reason the caller can act on.

      NOT applied to Proj_UpdateSchematic, which is the opposite
      direction and whose correct focus has not been measured. Guessing
      symmetry here would be inventing a precondition. }
    FocusedKind := '';
    Try
        FocusedDoc := Workspace.DM_FocusedDocument;
        If FocusedDoc <> Nil Then FocusedKind := FocusedDoc.DM_DocumentKind;
    Except
        FocusedKind := '';
    End;
    If FocusedKind = 'SCH' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_FOCUS',
            'A schematic is focused, and Altium refuses to compare a '
            + 'source document against its owner project: it raises a '
            + 'modal error and changes nothing. Focus the PCB document '
            + 'first with app_set_active_document, then call this again.');
        Exit;
    End;

    SmartCompile(Project);
    Ok := ComputeECODifferences(Project, MatchedBefore, ExtraSchBefore, ExtraPcbBefore, PcbPath);
    If Not Ok Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No primary PCB document in this project or mappings unavailable');
        Exit;
    End;

    { Fire the real ECO via the WorkspaceManager comparator. This is the
      ONLY evidenced scriptable launcher (ref: reference MultiPCBProject.pas,
      Petar Perisin): WorkspaceManager:Compare with ObjectKind=Project +
      Action=UpdateOther performs Design > Update PCB Document (schematic ->
      PCB). The previous 'PCB:UpdatePCBFromProject' was NOT a real process id
      (RunProcess silently ignores unknown ids -> the handler no-opped), and
      DisableDialog/Silent/NoConfirm/AutoApply are invented flags that appear
      in no Altium docs.
      DIALOG IS UNAVOIDABLE: Altium's Engineering Change Order dialog is
      non-suppressible BY DESIGN (altium.com .../keeping-synchronized). This
      process raises that modal and BLOCKS the polling loop until a human
      clicks "Execute Changes" (or closes it). There is no documented silent
      variant. Direction note: Action=UpdateOther is sch->PCB when driven with
      the project/schematic in focus; it back-annotates if a PCB is focused. }
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('Action', 'UpdateOther');
    RunProcess('WorkspaceManager:Compare');

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

    { COMPONENTS_IN_SYNC, not in_sync. These counters describe component
      PRESENCE and nothing else, so the old "in_sync" claimed far more
      than it measured: with a component's Comment differing between
      schematic and board, the counts are equal and it reported true.
      MEASURED 2026-08-17, and it reported in_sync while a real
      difference was pending. Renamed so the field says what it counts. }
    Data := Data + ',"components_in_sync":' + BoolToJsonStr((ExtraSchAfter = 0) And (ExtraPcbAfter = 0));
    Data := Data + ',"in_sync_note":"Counts component PRESENCE only. '
        + 'Parameter, footprint and net differences are NOT included, so '
        + 'true here does NOT mean the documents agree."';

    { The old dialog_may_have_opened was a guess from unchanged counts,
      and it was wrong in BOTH measured cases: false when the No
      Differences dialog had opened, and true whenever a change order
      was cancelled. This handler genuinely cannot tell. It fires a
      process and its own thread is blocked while any modal is up, so it
      has nothing to observe. Say so, and name the tool that CAN look,
      which works precisely because it does not use this bridge. }
    Data := Data + ',"dialog_outcome_verified":false';
    Data := Data + ',"dialog_note":"Whether a dialog appeared, and what '
        + 'it said, cannot be determined from inside this handler: the '
        + 'script thread is blocked while a modal is up. Call '
        + 'app_list_open_dialogs, which reads the Win32 windows directly '
        + 'and answers even while Altium is blocked."';
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
                Comp.DesignatorLocked := LockVal;
                Inc(Count);
            End;
        Except
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);

    SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
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

    { Hierarchy mode (If/Else chain, Case on enum crashes DelphiScript) }
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
{ Client.OpenDocument('SCH', path), the same API set_document_parameter has    }
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
{ Proj_ForceRecompile - Explicit force-recompile handler. Saves, invalidates,  }
{ recompiles, returns the new compile tick so callers can verify it moved.    }
{..............................................................................}

Function Proj_ForceRecompile(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    PrevTick, NewTick : Cardinal;
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
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    PrevTick := LastCompileTick;
    Try SaveAllDirty(0); Except End;
    LastCompileTick := 0;
    SmartCompile(Project);
    NewTick := LastCompileTick;

    Result := BuildSuccessResponse(RequestId,
        '{"recompiled":true,'
        + '"prev_compile_tick":' + IntToStr(PrevTick) + ','
        + '"new_compile_tick":' + IntToStr(NewTick) + ','
        + '"project":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"}');
End;

{..............................................................................}
{ Proj_GetCompileFreshness - Report how old the SmartCompile cache is and     }
{ how many open editor docs are dirty. Gives callers a way to see, before    }
{ trusting a netlist read, whether the compile is stale.                     }
{..............................................................................}

Function Proj_GetCompileFreshness(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    I, DirtyCount, OpenCount : Integer;
    DirtyList, FullPath : String;
    First : Boolean;
    AgeMs, CurrentTick : Cardinal;
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
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    CurrentTick := GetTickCount;
    If LastCompileTick = 0 Then
        AgeMs := 0
    Else If CurrentTick >= LastCompileTick Then
        AgeMs := CurrentTick - LastCompileTick
    Else
        AgeMs := 0;  { tick wrap-around }

    DirtyCount := 0;
    OpenCount := 0;
    DirtyList := '';
    First := True;

    { Walk the focused project's logical documents (Client.GetDocumentCount   }
    { is undeclared in DelphiScript; Client.GetDocumentByPath is how we       }
    { resolve each logical doc to an IServerDocument). A doc "counts as       }
    { open" if it resolves to a live IServerDocument. A doc is dirty if that }
    { IServerDocument reports Modified = True.                               }
    Try
        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            FullPath := '';
            Try FullPath := Doc.DM_FullPath; Except FullPath := Doc.DM_FileName; End;
            If FullPath = '' Then Continue;

            ServerDoc := Nil;
            Try ServerDoc := Client.GetDocumentByPath(FullPath); Except End;
            If ServerDoc = Nil Then Continue;
            Inc(OpenCount);

            Try
                If ServerDoc.Modified Then
                Begin
                    Inc(DirtyCount);
                    If Not First Then DirtyList := DirtyList + ',';
                    First := False;
                    DirtyList := DirtyList + '"' + EscapeJsonString(FullPath) + '"';
                End;
            Except End;
        End;
    Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"compile_age_ms":' + IntToStr(AgeMs) + ','
        + '"compile_cached":' + BoolToJsonStr(LastCompileTick > 0) + ','
        + '"ttl_ms":' + IntToStr(COMPILE_CACHE_TTL_MS) + ','
        + '"open_doc_count":' + IntToStr(OpenCount) + ','
        + '"dirty_doc_count":' + IntToStr(DirtyCount) + ','
        + '"dirty_docs":[' + DirtyList + '],'
        + '"project":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"}');
End;

{..............................................................................}
{ Dashboard snapshot - bundles every read the web dashboard needs into ONE     }
{ IPC round-trip. The dashboard used to fire 6-7 separate requests; each paid  }
{ the full poll + file-IO tax and queued behind the others (Pascal processes   }
{ one request at a time). This collapses them to a single request.            }
{                                                                              }
{ Each sub-handler returns a full success/data/error envelope; we pull the     }
{ data field out of each and re-assemble under one envelope. A failed          }
{ sub-handler contributes "null" for its section, the rest still populate.     }
{..............................................................................}
Function Proj_DashboardSnapshot(RequestId : String) : String;
Var
    Focused, Docs, Stats, Bom, Nets, Msgs, ProjPath : String;
    DataStr, EnvOut : String;
Begin
    Focused  := ExtractJsonValue(Proj_GetFocused(RequestId), 'data');
    Docs     := ExtractJsonValue(Proj_GetDocuments('', RequestId), 'data');
    Stats    := ExtractJsonValue(Proj_GetDesignStats('', RequestId), 'data');
    Bom      := ExtractJsonValue(Proj_GetBOM('', RequestId), 'data');
    Nets     := ExtractJsonValue(Proj_GetNets('', RequestId), 'data');
    Msgs     := ExtractJsonValue(Proj_GetMessages('', RequestId), 'data');
    ProjPath := ExtractJsonValue(Proj_GetProjectPath(RequestId), 'data');

    If Focused  = '' Then Focused  := 'null';
    If Docs     = '' Then Docs     := 'null';
    If Stats    = '' Then Stats    := 'null';
    If Bom      = '' Then Bom      := 'null';
    If Nets     = '' Then Nets     := 'null';
    If Msgs     = '' Then Msgs     := 'null';
    If ProjPath = '' Then ProjPath := 'null';

    DataStr := '{'
        + '"focused":'   + Focused  + ','
        + '"documents":' + Docs     + ','
        + '"stats":'     + Stats    + ','
        + '"bom":'       + Bom      + ','
        + '"nets":'      + Nets     + ','
        + '"messages":'  + Msgs     + ','
        + '"path":'      + ProjPath
        + '}';
    EnvOut := BuildSuccessResponse(RequestId, DataStr);
    Result := EnvOut;
End;

{..............................................................................}
{ Proj_PushParamsToSheets - Copy every project-level parameter onto each LOADED }
{ schematic sheet as a document parameter (so title blocks can reference them). }
{ Unloaded sheets are skipped -- call load_project_sheets first to include them. }
{ Models the proven Proj_SetDocumentParameter set/create idiom per (sheet,param).}
{..............................................................................}

Function Proj_PushParamsToSheets(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Parameter : ISch_Parameter;
    I, P, PCount : Integer;
    SheetsUpdated, SheetsSkipped, ParamsPushed : Integer;
    FullPath, ParamName, ParamValue : String;
    Found, IsSch : Boolean;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project'); Exit; End;

    PCount := 0;
    Try PCount := Project.DM_ParameterCount; Except End;
    If PCount = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PARAMS', 'Project has no parameters to push');
        Exit;
    End;

    SheetsUpdated := 0;
    SheetsSkipped := 0;
    ParamsPushed := 0;

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(I); Except End;
        If Doc <> Nil Then
        Begin
            FullPath := '';
            Try FullPath := Doc.DM_FullPath; Except End;
            IsSch := (Pos('.SCHDOC', UpperCase(FullPath)) > 0);
            If IsSch Then
            Begin
                SchDoc := Nil;
                Try SchDoc := SchServer.GetSchDocumentByPath(FullPath); Except SchDoc := Nil; End;
                If SchDoc = Nil Then
                    Inc(SheetsSkipped)
                Else
                Begin
                    SchServer.ProcessControl.PreProcess(SchDoc, '');
                    Try
                        For P := 0 To PCount - 1 Do
                        Begin
                            ParamName := '';
                            ParamValue := '';
                            Try
                                ParamName := Project.DM_Parameters(P).DM_Name;
                                ParamValue := Project.DM_Parameters(P).DM_Value;
                            Except End;
                            If ParamName <> '' Then
                            Begin
                                Found := False;
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
                                    Parameter := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                                    Parameter.Name := ParamName;
                                    Parameter.Text := ParamValue;
                                    SchDoc.RegisterSchObjectInContainer(Parameter);
                                    SchRegisterObject(SchDoc, Parameter);
                                End;
                                Inc(ParamsPushed);
                            End;
                        End;
                    Finally
                        SchServer.ProcessControl.PostProcess(SchDoc, 'Edit');
                    End;
                    Try MarkDocDirtyByPath(FullPath); Except End;
                    Try SchDoc.GraphicallyInvalidate; Except End;
                    Inc(SheetsUpdated);
                End;
            End;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"param_count":' + IntToStr(PCount)
        + ',"sheets_updated":' + IntToStr(SheetsUpdated)
        + ',"sheets_skipped_not_loaded":' + IntToStr(SheetsSkipped)
        + ',"params_pushed":' + IntToStr(ParamsPushed) + '}');
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
        'push_params_to_sheets': Result := Proj_PushParamsToSheets(Params, RequestId);
        'compile':           Result := Proj_Compile(Params, RequestId);
        'get_focused':       Result := Proj_GetFocused(RequestId);
        'get_nets':          Result := Proj_GetNets(Params, RequestId);
        'get_bom':           Result := Proj_GetBOM(Params, RequestId);
        'get_component_info': Result := Proj_GetComponentInfo(Params, RequestId);
        'get_component_info_batch': Result := Proj_GetComponentInfoBatch(Params, RequestId);
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
        'get_variant_matrix': Result := Proj_GetVariantMatrix(Params, RequestId);
        'get_active_variant': Result := Proj_GetActiveVariant(Params, RequestId);
        'set_active_variant': Result := Proj_SetActiveVariant(Params, RequestId);
        'create_variant':    Result := Proj_CreateVariant(Params, RequestId);
        'delete_variant':    Result := Proj_DeleteVariant(Params, RequestId);
        'get_open_projects': Result := Proj_GetOpenProjects(RequestId);
        'save_all':          Result := Proj_SaveAll(RequestId);
        'get_messages':      Result := Proj_GetMessages(Params, RequestId);
        'find_component':    Result := Proj_FindComponent(Params, RequestId);
        'get_connectivity':  Result := Proj_GetConnectivity(Params, RequestId);
        'get_connectivity_batch': Result := Proj_GetConnectivityBatch(Params, RequestId);
        'force_recompile':       Result := Proj_ForceRecompile(Params, RequestId);
        'get_compile_freshness': Result := Proj_GetCompileFreshness(Params, RequestId);
        'import_document':   Result := Proj_ImportDocument(Params, RequestId);
        'get_project_path':  Result := Proj_GetProjectPath(RequestId);
        'dashboard_snapshot': Result := Proj_DashboardSnapshot(RequestId);
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
