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
    Result := UpperCase(CanonicalPathValue('path', FocusedPath))
        = UpperCase(CanonicalPathValue('path', ProjectPath));
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

{..............................................................................}
{ Unsaved edits of ONE project, for a close that does not save.               }
{                                                                             }
{ A close issued while a member is still modified raises Altium's save        }
{ prompt. RunProcess is synchronous, so the prompt blocks inside the handler, }
{ the handler blocks the polling loop, and the loop is the only thing that    }
{ could have answered it: every later call waits until a human clicks.        }
{ Clearing the modified flag first leaves the close nothing to ask about,     }
{ and closing a document that reads as clean drops its in-memory edits,       }
{ which is exactly what save=false asks for.                                  }
{                                                                             }
{ SCOPED TO THE PROJECT: the same members SaveProjectMembers writes, and the  }
{ project file. Never the workspace, which is how a close once reached a      }
{ client project nobody had named.                                            }
{..............................................................................}

{ MEASURED 2026-09-13 on AD 26.10.1.6: after a ProcessControl edit the tab    }
{ showed the sheet as modified while IServerDocument.Modified read False, and }
{ CloseObject raised its save prompt anyway. A clear gated on that read       }
{ cleared nothing, so the flag is cleared on every loaded member without      }
{ reading it. Whether clearing it stops the prompt is still unmeasured, and   }
{ proj_close answers the prompt from Python when it appears, which is what    }
{ keeps the bridge from wedging.                                              }
Function ClearDocModifiedByPath(Path : String) : Boolean;
Var
    ServerDoc : IServerDocument;
Begin
    Result := False;
    If Path = '' Then Exit;
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(Path); Except ServerDoc := Nil; End;
    If ServerDoc = Nil Then Exit;
    Try ServerDoc.SetModified(False); Except End;
    Result := True;
End;

Function DiscardProjectMembers(Project : IProject) : String;
Var
    J : Integer;
    Doc : IDocument;
    Path : String;
Begin
    Result := '';
    If Project = Nil Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc = Nil Then Continue;
        Path := '';
        Try Path := Doc.DM_FullPath; Except Path := ''; End;
        If ClearDocModifiedByPath(Path) Then
        Begin
            If Result <> '' Then Result := Result + '|';
            Result := Result + Path;
        End;
    End;
    Path := '';
    Try Path := Project.DM_ProjectFullPath; Except Path := ''; End;
    If ClearDocModifiedByPath(Path) Then
    Begin
        If Result <> '' Then Result := Result + '|';
        Result := Result + Path;
    End;
End;

{ What is STILL modified after a discard. DocIsModified reads a flag that    }
{ App_SaveAll records does not always propagate, so the discard is checked   }
{ rather than trusted: one document left dirty is enough to raise the prompt. }
Function DirtyProjectMembers(Project : IProject) : String;
Var
    J : Integer;
    Doc : IDocument;
    Path : String;
Begin
    Result := '';
    If Project = Nil Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc = Nil Then Continue;
        Path := '';
        Try Path := Doc.DM_FullPath; Except Path := ''; End;
        If (Path <> '') And DocIsModified(Path) Then
        Begin
            If Result <> '' Then Result := Result + '|';
            Result := Result + Path;
        End;
    End;
    Path := '';
    Try Path := Project.DM_ProjectFullPath; Except Path := ''; End;
    If (Path <> '') And DocIsModified(Path) Then
    Begin
        If Result <> '' Then Result := Result + '|';
        Result := Result + Path;
    End;
End;

Function PipePathsToJsonArray(PathList : String) : String;
Var
    Remaining, Path : String;
    P : Integer;
Begin
    Result := '[';
    Remaining := PathList;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Path := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Path := Remaining;
            Remaining := '';
        End;
        If Result <> '[' Then Result := Result + ',';
        Result := Result + '"' + EscapeJsonString(Path) + '"';
    End;
    Result := Result + ']';
End;

{..............................................................................}
{ WorkspaceManager:CloseObject CLOSES THE FOCUSED PROJECT.                    }
{                                                                             }
{ MEASURED 2026-09-13 on AD 26.10.1.6: proj_close named a scratch project     }
{ with no loaded document while Blinker555_v4's board was focused. The first  }
{ CloseObject, given the scratch project's full path, closed Blinker555_v4,   }
{ and the retry closed the scratch project. Every earlier close that worked   }
{ had a sheet of the named project focused, so the two could not be told      }
{ apart. The reference scripts never close a project by name: they focus it,  }
{ check DM_FocusedProject, and close the focused one.                         }
{                                                                             }
{ It also explains a report from a managed project: a save prompt listing 49  }
{ documents for a 10-document project, then a second call that listed 4 and   }
{ closed. The first close was aimed at a different project.                   }
{..............................................................................}

Function OpenProjectPaths(Workspace : IWorkspace) : String;
Var
    I : Integer;
    Proj : IProject;
    P : String;
Begin
    Result := '';
    If Workspace = Nil Then Exit;
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Proj := Workspace.DM_Projects(I);
        If Proj = Nil Then Continue;
        P := '';
        Try P := Proj.DM_ProjectFullPath; Except P := ''; End;
        If P = '' Then Continue;
        If Result <> '' Then Result := Result + '|';
        Result := Result + P;
    End;
End;

{ Focus follows the active DOCUMENT, so the project is focused by showing one  }
{ of its documents. Only LOADED ones: opening a member here would load it as a }
{ free document, the way App_SetActiveDocument records, and focus nothing.     }
Function FocusProjectForClose(Project : IProject) : Boolean;
Var
    J : Integer;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    Path, Target : String;
Begin
    Result := False;
    If Project = Nil Then Exit;
    Target := '';
    Try Target := Project.DM_ProjectFullPath; Except Target := ''; End;
    If Target = '' Then Exit;
    If FocusedProjectPathIs(Target) Then
    Begin
        Result := True;
        Exit;
    End;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc = Nil Then Continue;
        Path := DocFullPath(Doc);
        If Path = '' Then Continue;
        ServerDoc := Nil;
        Try ServerDoc := Client.GetDocumentByPath(Path); Except ServerDoc := Nil; End;
        If ServerDoc = Nil Then Continue;
        Try Client.ShowDocument(ServerDoc); Except End;
        If FocusedProjectPathIs(Target) Then
        Begin
            Result := True;
            Exit;
        End;
    End;
    { NO MEMBER LOADED: nothing above could be shown, and the project could }
    { not be closed. MEASURED: opening a member (load_project_sheets, or    }
    { OpenObject) focuses the project and the close then works. The Client }
    { API keeps the membership; RunProcess Client:OpenDocument does not.    }
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc = Nil Then Continue;
        Path := DocFullPath(Doc);
        If (Path = '') Or (Not FileExists(Path)) Then Continue;
        ServerDoc := Nil;
        Try ServerDoc := Client.OpenDocument(Doc.DM_DocumentKind, Path); Except ServerDoc := Nil; End;
        If ServerDoc = Nil Then Continue;
        Try Client.ShowDocument(ServerDoc); Except End;
        If FocusedProjectPathIs(Target) Then
        Begin
            Result := True;
            Exit;
        End;
    End;
End;

{ Paths in BeforeList that are gone from AfterList, other than TargetPath.   }
Function PathsGoneOtherThan(BeforeList, AfterList, TargetPath : String) : String;
Var
    Remaining, P, UpAfter : String;
    K : Integer;
Begin
    Result := '';
    UpAfter := '|' + UpperCase(AfterList) + '|';
    Remaining := BeforeList;
    While Remaining <> '' Do
    Begin
        K := Pos('|', Remaining);
        If K > 0 Then
        Begin
            P := Copy(Remaining, 1, K - 1);
            Remaining := Copy(Remaining, K + 1, Length(Remaining) - K);
        End
        Else
        Begin
            P := Remaining;
            Remaining := '';
        End;
        If (P <> '') And (UpperCase(P) <> UpperCase(TargetPath))
            And (Pos('|' + UpperCase(P) + '|', UpAfter) = 0) Then
        Begin
            If Result <> '' Then Result := Result + '|';
            Result := Result + P;
        End;
    End;
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
            { The project file too: its document list changes without the }
            { modified flag, so SaveProjectMembers alone left it unwritten. }
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"project_path":"'
                + EscapeJsonString(Project.DM_ProjectFullPath) + '"'
                + ',"project_file_saved":' + BoolToJsonStr(SaveProjectFile(Project)) + '}');
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
    ProjectPath, Discarded, StillDirty, More : String;
    ClosedInstead, BeforeClose, Why : String;
    SaveFirst, Closed : Boolean;
    Attempts : Integer;
    Workspace : IWorkspace;
    Project : IProject;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    SaveFirst := ExtractJsonValue(Params, 'save') <> 'false';
    Discarded := '';

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

            { BEFORE ANY SAVE OR DISCARD, so a close that has to be refused  }
            { leaves the project exactly as it found it.                     }
            If Not FocusProjectForClose(Project) Then
            Begin
                Result := BuildSuccessResponse(RequestId,
                    '{"success":false,"closed":false,"project_path":"'
                    + EscapeJsonString(ProjectPath) + '"'
                    + ',"discarded":[],"attempts":0,"closed_instead":[]'
                    + ',"reason":"Altium closes the FOCUSED project, and this '
                    + 'one could not be made focused because none of its '
                    + 'documents is loaded. Nothing was closed or changed. '
                    + 'Load a sheet with proj_load_sheets, or activate one of '
                    + 'its documents, and close again."}');
                Exit;
            End;

            If SaveFirst Then
                SaveProjectMembers(Project)
            Else
            Begin
                { DISCARD BEFORE CLOSING, then check the discard took. A close }
                { issued with anything still modified raises a save prompt     }
                { that blocks this loop until a human answers it, so refusing  }
                { here is the only way the caller hears anything at all.       }
                Discarded := DiscardProjectMembers(Project);
                StillDirty := DirtyProjectMembers(Project);
                If StillDirty <> '' Then
                Begin
                    Result := BuildSuccessResponse(RequestId,
                        '{"success":false,"closed":false,"project_path":"'
                        + EscapeJsonString(ProjectPath) + '"'
                        + ',"still_modified":' + PipePathsToJsonArray(StillDirty)
                        + ',"reason":"save=false could not clear the modified '
                        + 'flag on these documents, so closing would raise a '
                        + 'save prompt, and that prompt blocks the bridge until '
                        + 'someone answers it in Altium. Nothing was closed. '
                        + 'Pass save=true to write them first, or close the '
                        + 'project in Altium."}');
                    Exit;
                End;
            End;

            { Confirm by looking the project up again. A cancelled save      }
            { prompt aborts the close, and the process layer cannot say so.  }
            { A close without saving is retried ONCE: reported on a managed  }
            { project, the first close was abandoned after its prompt was    }
            { answered and a second completed. Not retried when saving,      }
            { where the likely cause is a prompt cancelled on purpose.       }
            Attempts := 0;
            Closed := False;
            ClosedInstead := '';
            While (Not Closed) And (Attempts < 2) Do
            Begin
                { CloseObject closes the FOCUSED project, whatever FileName  }
                { says, so focus the named one before EVERY attempt: focus   }
                { can move between one attempt and the next.                  }
                Project := FindProjectByPath(Workspace, ProjectPath);
                If Not FocusProjectForClose(Project) Then Break;
                BeforeClose := OpenProjectPaths(Workspace);
                Attempts := Attempts + 1;
                ResetParameters;
                AddStringParameter('ObjectKind', 'Project');
                AddStringParameter('FileName', ProjectPath);
                RunProcess('WorkspaceManager:CloseObject');
                Closed := FindProjectByPath(Workspace, ProjectPath) = Nil;
                ClosedInstead := PathsGoneOtherThan(BeforeClose,
                    OpenProjectPaths(Workspace), ProjectPath);
                { Never retry after something else closed. The retry is what }
                { turned one wrong close into two.                            }
                If ClosedInstead <> '' Then Break;
                If SaveFirst Then Break;
                If Not Closed Then
                Begin
                    Project := FindProjectByPath(Workspace, ProjectPath);
                    More := DiscardProjectMembers(Project);
                    If More <> '' Then
                    Begin
                        If Discarded <> '' Then Discarded := Discarded + '|';
                        Discarded := Discarded + More;
                    End;
                End;
            End;

            If Closed And (ClosedInstead = '') Then
                Result := BuildSuccessResponse(RequestId,
                    '{"success":true,"closed":true,"project_path":"'
                    + EscapeJsonString(ProjectPath) + '"'
                    + ',"saved":' + BoolToJsonStr(SaveFirst)
                    + ',"discarded":' + PipePathsToJsonArray(Discarded)
                    + ',"attempts":' + IntToStr(Attempts)
                    + ',"closed_instead":[]}')
            Else
            Begin
                If ClosedInstead <> '' Then
                    Why := 'closing this project also closed a project nobody '
                        + 'named, listed under closed_instead. Altium closes '
                        + 'the focused project; this stopped there and did not '
                        + 'retry. Reopen those with proj_open.'
                Else
                    Why := 'the project is still open after the close was '
                        + 'issued, which is what happens when a save prompt is '
                        + 'cancelled, a document refuses to close, or focus '
                        + 'could not be moved back to it for a second attempt';
                Result := BuildSuccessResponse(RequestId,
                    '{"success":false,"closed":' + BoolToJsonStr(Closed)
                    + ',"project_path":"' + EscapeJsonString(ProjectPath) + '"'
                    + ',"discarded":' + PipePathsToJsonArray(Discarded)
                    + ',"attempts":' + IntToStr(Attempts)
                    + ',"closed_instead":' + PipePathsToJsonArray(ClosedInstead)
                    + ',"reason":"' + Why + '"}');
            End;
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
            { Written now, or the add is gone after a reopen. }
            Result := BuildSuccessResponse(RequestId, '{"success":true,"project_saved":'
                + BoolToJsonStr(SaveProjectFile(Project)) + '}');
        End
        Else
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_FOUND', 'Project not found');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
End;

{ The DocumentPath= line of the project FILE that names DocPath's file, or   }
{ ''. Matched on the file name alone, so a same-named document in another    }
{ folder reads as still listed: the error falls on "not removed", never on   }
{ a false "removed".                                                          }
Function ProjectFileEntryFor(ProjPath, DocPath : String) : String;
Var
    SL : TStringList;
    I : Integer;
    Line, Value, Want : String;
Begin
    Result := '';
    Want := UpperCase(ExtractFileName(DocPath));
    SL := TStringList.Create;
    Try
        If LoadTextFile(SL, ProjPath) Then
        Begin
            For I := 0 To SL.Count - 1 Do
            Begin
                Line := Trim(SL.Strings[I]);
                If UpperCase(Copy(Line, 1, 13)) <> 'DOCUMENTPATH=' Then Continue;
                Value := Copy(Line, 14, Length(Line));
                If UpperCase(ExtractFileName(Value)) = Want Then
                Begin
                    Result := Line;
                    Exit;
                End;
            End;
        End;
    Finally
        SL.Free;
    End;
End;

{ Remove a document FROM A PROJECT. It does not delete the file.             }
{                                                                             }
{ THE PROJECT FILE IS WRITTEN AND READ BACK. Removal changed the project in  }
{ memory only, so the .PrjPcb kept the [DocumentN] entry and the document    }
{ was back after a reopen (issue #94). The project is saved, unless          }
{ save_project is false, and the file itself is checked for the entry.       }
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
    ProjectPath, DocumentPath, DocPath, ProjPath, Entry, Members : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
    StillMember, WasMember, ProjSaved : Boolean;
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

                { Members as Altium holds them now. The reply carries them so }
                { the server can take leftover sections out of the file: only }
                { sections past this count, and only when the rest match it.  }
                StillMember := False;
                Members := '';
                For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    DocPath := '';
                    Doc := Project.DM_LogicalDocuments(I);
                    If Doc <> Nil Then Try DocPath := Doc.DM_FullPath; Except End;
                    If UpperCase(DocPath) = UpperCase(DocumentPath) Then
                        StillMember := True;
                    If DocPath <> '' Then
                    Begin
                        If Members <> '' Then Members := Members + ',';
                        Members := Members + '"' + EscapeJsonString(DocPath) + '"';
                    End;
                End;

                If StillMember Then
                Begin
                    Result := BuildSuccessResponse(RequestId,
                        '{"success":false,"removed":false,"reason":"the '
                        + 'document is still a member of the project after '
                        + 'DM_RemoveSourceDocument was called"}');
                    Exit;
                End;

                ProjPath := '';
                Try ProjPath := Project.DM_ProjectFullPath; Except End;
                ProjSaved := False;
                Entry := '';
                { ALTIUM'S WRITER LEAVES THE OLD SECTIONS. Measured on AD 26: }
                { it rewrites [Document1]..[DocumentN] for the new count and   }
                { leaves every section past N in the file, so a removal leaves }
                { an entry (or a shifted duplicate) behind and a reopen loads  }
                { it again. Saving more does not help. The file is read back,  }
                { and a leftover is reported with the members, for the server  }
                { to take out (eda_agent.prjpcb); never called removed here.   }
                If ExtractJsonValue(Params, 'save_project') <> 'false' Then
                    ProjSaved := SaveProjectFile(Project);
                Entry := ProjectFileEntryFor(ProjPath, DocumentPath);

                If Entry <> '' Then
                    Result := BuildSuccessResponse(RequestId,
                        '{"success":false,"removed":true,"project_saved":'
                        + BoolToJsonStr(ProjSaved) + ',"in_project_file":true'
                        + ',"project_file_entry":"' + EscapeJsonString(Entry)
                        + '","document_path":"' + EscapeJsonString(DocumentPath)
                        + '","project_path":"' + EscapeJsonString(ProjPath)
                        + '","members":[' + Members + ']'
                        + ',"reason":"removed in memory, but the project file '
                        + 'on disk still lists it, so it comes back when the '
                        + 'project is reopened. Altium rewrites only as many '
                        + '[DocumentN] sections as the project now has and leaves '
                        + 'the rest; another save does not remove them."}')
                Else
                    Result := BuildSuccessResponse(RequestId,
                        '{"success":true,"removed":true,"project_saved":'
                        + BoolToJsonStr(ProjSaved) + ',"in_project_file":false'
                        + ',"document_path":"' + EscapeJsonString(DocumentPath)
                        + '","project_path":"' + EscapeJsonString(ProjPath)
                        + '","members":[' + Members + ']}');
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
{ limit_hit says the list stopped at limit with more pins left, and          }
{ skipped_nil counts documents, components and pins the model returned as   }
{ Nil, so a short list is not mistaken for the whole design. Proj_GetBOM    }
{ reports the same two.                                                      }
{..............................................................................}

Function Proj_GetNets(Params : String; RequestId : String) : String;
Var
    ProjectPath, FilterComp, FilterNet : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, Count, Limit, DocCount, Skipped : Integer;
    UsePhysical, LimitHit : Boolean;
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
    Skipped := 0;
    LimitHit := False;

    GetCompiledDocs(Project, DocCount, UsePhysical);
    For I := 0 To DocCount - 1 Do
    Begin
        If LimitHit Then Break;
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Begin Inc(Skipped); Continue; End;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            If LimitHit Then Break;
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Begin Inc(Skipped); Continue; End;

            CompDesig := Comp.DM_PhysicalDesignator;
            If (FilterComp <> '') And (CompDesig <> FilterComp) Then Continue;

            For K := 0 To Comp.DM_PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Begin Inc(Skipped); Continue; End;

                NetName := Pin.DM_FlattenedNetName;
                If (FilterNet <> '') And (NetName <> FilterNet) Then Continue;
                If Count >= Limit Then
                Begin
                    LimitHit := True;
                    Break;
                End;

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
    Result := BuildSuccessResponse(RequestId, '{"pins":' + Data
        + ',"count":' + IntToStr(Count)
        + ',"limit":' + IntToStr(Limit)
        + ',"limit_hit":' + BoolToJsonStr(LimitHit)
        + ',"skipped_nil":' + IntToStr(Skipped) + '}');
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
    I, J, K, Count, Limit, DocCount, Skipped : Integer;
    UsePhysical, LimitHit : Boolean;
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
    Skipped := 0;
    LimitHit := False;

    GetCompiledDocs(Project, DocCount, UsePhysical);
    For I := 0 To DocCount - 1 Do
    Begin
        If LimitHit Then Break;
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Begin Inc(Skipped); Continue; End;

        For J := 0 To Doc.DM_ComponentCount - 1 Do
        Begin
            If LimitHit Then Break;
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Begin Inc(Skipped); Continue; End;

            If Count >= Limit Then
            Begin
                LimitHit := True;
                Break;
            End;
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
                If Pin = Nil Then Begin Inc(Skipped); Continue; End;
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
    Result := BuildSuccessResponse(RequestId, '{"components":' + Data
        + ',"count":' + IntToStr(Count)
        + ',"limit":' + IntToStr(Limit)
        + ',"limit_hit":' + BoolToJsonStr(LimitHit)
        + ',"skipped_nil":' + IntToStr(Skipped) + '}');
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
            OutlineStr := OutlineStr + '{"x":' + FloatToJsonStr(CoordToMilsF(Board.BoardOutline.Segments[I].vx)) +
                ',"y":' + FloatToJsonStr(CoordToMilsF(Board.BoardOutline.Segments[I].vy)) + '}';
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

    Data := '{"origin_x":' + FloatToJsonStr(CoordToMilsF(Board.XOrigin));
    Data := Data + ',"origin_y":' + FloatToJsonStr(CoordToMilsF(Board.YOrigin));
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
{ - Wrap each doc in SchBeginEdit/SchEndEdit for                              }
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
  Returns -1 if A should come before B, +1 if after, 0 if equal. X and Y
  are Altium's own coordinates: whole mils put parts a fraction of a mil
  apart on one sort key. }
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
    XCoords    : TStringList;  { X, Altium's coordinate, as integer-string }
    YCoords    : TStringList;  { Y, Altium's coordinate, as integer-string }
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
              Skip sheets that aren't currently loaded; ProjectSheet counts
              them, so the reply says how many were left out. }
            SchDoc := ProjectSheet(FilePath);
            If SchDoc = Nil Then Continue;

            Inc(ProcessedDocs);
            TouchedDocs.Add(FilePath);

            { -------- Order = 'none': just reset designators to "<prefix>?" -------- }
            If Order = 'none' Then
            Begin
                SchBeginEdit(SchDoc, '');
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
                { The sheet is in TouchedDocs, so the outer Finally closes it:
                  PostProcess, redraw and mark dirty. Closing it here as well
                  ran PostProcess twice for one PreProcess. }
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
                            XCoords.Add(IntToStr(Comp.Location.X));
                            YCoords.Add(IntToStr(Comp.Location.Y));
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
                SchBeginEdit(SchDoc, '');
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
                Try SchEndEdit(SchDoc, 'Edit'); Except End;
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
{ ProjectHoldingDoc - the first open project whose documents include FilePath, }
{ or Nil for a free document.                                                 }
{..............................................................................}
Function ProjectHoldingDoc(FilePath : String) : IProject;
Var
    Workspace : IWorkspace;
    Proj : IProject;
    Doc : IDocument;
    I, J : Integer;
    Want, Have : String;
Begin
    Result := Nil;
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
                Result := Proj;
                Exit;
            End;
        End;
    End;
End;

{ SDK REACH: STEP EXPORTER SETTINGS, ONE OUTPUT WITHOUT AN OUTJOB, CHANNELS,  }
{ WHERE A PART'S MODELS RESOLVE, LIBRARY LOOKUPS, THE OUTPUT PANEL AND       }
{ GENERATED DOCUMENTS. Members are read inside Try; read_errors counts what }
{ this build would not give.                                                 }

{ The STEP exporter's settings as JSON members (no braces). }
Function StepOptionsJson(Exporter : IPCB_Board3DModelExporter) : String;
Var
    Folded, MechHoles, ElecHoles, FreeHoles, Barrels, Cover : Boolean;
    Rate, Comps, Models, Holes, Copper, SuffixType, Misses : Integer;
    Suffix : String;
Begin
    Folded := False; MechHoles := False; ElecHoles := False; FreeHoles := False;
    Barrels := False; Cover := False;
    Rate := 0; Comps := 0; Models := 0; Holes := 0; Copper := 0; SuffixType := 0;
    Suffix := ''; Misses := 0;
    Try Folded := Exporter.GetState_ExportFoldedBoard; Except Inc(Misses); End;
    Try Rate := Exporter.GetState_ExportFoldedBoardRate; Except Inc(Misses); End;
    Try Comps := Exporter.GetState_ExportComponentOptions; Except Inc(Misses); End;
    Try Models := Exporter.GetState_ExportModelsOption; Except Inc(Misses); End;
    Try Holes := Exporter.GetState_ExportHolesOption; Except Inc(Misses); End;
    Try Copper := Exporter.GetState_ExportCopperOption; Except Inc(Misses); End;
    Try MechHoles := Exporter.GetState_IncludeMechanicalPadHoles; Except Inc(Misses); End;
    Try ElecHoles := Exporter.GetState_IncludeElectricalPadHoles; Except Inc(Misses); End;
    Try FreeHoles := Exporter.GetState_IncludeFreePadHoles; Except Inc(Misses); End;
    Try Barrels := Exporter.GetState_ExportPadAndViaBarrelsOnly; Except Inc(Misses); End;
    Try Cover := Exporter.GetState_IncludeCoverLayer; Except Inc(Misses); End;
    Try SuffixType := Exporter.GetState_ComponentSuffixType; Except Inc(Misses); End;
    Try Suffix := Exporter.GetState_ComponentSuffix; Except Inc(Misses); End;
    Result := JsonBool('folded', Folded) + ','
        + JsonInt('fold_rate', Rate) + ','
        + JsonInt('component_option', Comps) + ','
        + JsonInt('models_option', Models) + ','
        + JsonInt('holes_option', Holes) + ','
        + JsonInt('copper_option', Copper) + ','
        + JsonBool('mechanical_pad_holes', MechHoles) + ','
        + JsonBool('electrical_pad_holes', ElecHoles) + ','
        + JsonBool('free_pad_holes', FreeHoles) + ','
        + JsonBool('barrels_only', Barrels) + ','
        + JsonBool('cover_layer', Cover) + ','
        + JsonInt('component_suffix_type', SuffixType) + ','
        + JsonStr('component_suffix', Suffix) + ','
        + JsonInt('read_errors', Misses);
End;

{ Write the STEP settings Params names; returns the names that did not take. }
Function ApplyStepOptions(Exporter : IPCB_Board3DModelExporter; Params : String) : String;
Var
    V : String;
Begin
    Result := '';
    V := LowerCase(ExtractJsonValue(Params, 'folded'));
    If V <> '' Then
    Begin
        Try Exporter.SetState_ExportFoldedBoard(V = 'true'); Except Result := Result + 'folded '; End;
    End;
    V := ExtractJsonValue(Params, 'fold_rate');
    If V <> '' Then
    Begin
        Try Exporter.SetState_ExportFoldedBoardRate(StrToIntDef(V, 0)); Except Result := Result + 'fold_rate '; End;
    End;
    V := ExtractJsonValue(Params, 'component_option');
    If V <> '' Then
    Begin
        Try Exporter.SetState_ExportComponentOptions(StrToIntDef(V, 0)); Except Result := Result + 'component_option '; End;
    End;
    V := ExtractJsonValue(Params, 'models_option');
    If V <> '' Then
    Begin
        Try Exporter.SetState_ExportModelsOption(StrToIntDef(V, 0)); Except Result := Result + 'models_option '; End;
    End;
    V := ExtractJsonValue(Params, 'holes_option');
    If V <> '' Then
    Begin
        Try Exporter.SetState_ExportHolesOption(StrToIntDef(V, 0)); Except Result := Result + 'holes_option '; End;
    End;
    V := ExtractJsonValue(Params, 'copper_option');
    If V <> '' Then
    Begin
        Try Exporter.SetState_ExportCopperOption(StrToIntDef(V, 0)); Except Result := Result + 'copper_option '; End;
    End;
    V := LowerCase(ExtractJsonValue(Params, 'mechanical_pad_holes'));
    If V <> '' Then
    Begin
        Try Exporter.SetState_IncludeMechanicalPadHoles(V = 'true'); Except Result := Result + 'mechanical_pad_holes '; End;
    End;
    V := LowerCase(ExtractJsonValue(Params, 'electrical_pad_holes'));
    If V <> '' Then
    Begin
        Try Exporter.SetState_IncludeElectricalPadHoles(V = 'true'); Except Result := Result + 'electrical_pad_holes '; End;
    End;
    V := LowerCase(ExtractJsonValue(Params, 'free_pad_holes'));
    If V <> '' Then
    Begin
        Try Exporter.SetState_IncludeFreePadHoles(V = 'true'); Except Result := Result + 'free_pad_holes '; End;
    End;
    V := LowerCase(ExtractJsonValue(Params, 'barrels_only'));
    If V <> '' Then
    Begin
        Try Exporter.SetState_ExportPadAndViaBarrelsOnly(V = 'true'); Except Result := Result + 'barrels_only '; End;
    End;
    V := LowerCase(ExtractJsonValue(Params, 'cover_layer'));
    If V <> '' Then
    Begin
        Try Exporter.SetState_IncludeCoverLayer(V = 'true'); Except Result := Result + 'cover_layer '; End;
    End;
    V := ExtractJsonValue(Params, 'component_suffix_type');
    If V <> '' Then
    Begin
        Try Exporter.SetState_ComponentSuffixType(StrToIntDef(V, 0)); Except Result := Result + 'component_suffix_type '; End;
    End;
    If ExtractJsonValue(Params, 'set_component_suffix') = 'true' Then
    Begin
        Try Exporter.SetState_ComponentSuffix(ExtractJsonValue(Params, 'component_suffix')); Except Result := Result + 'component_suffix '; End;
    End;
    Result := Trim(Result);
End;

{..............................................................................}
{ Proj_StepOptions - read, or set, the settings Altium's STEP exporter keeps  }
{ (the ones File > Export > STEP 3D last used), with no export.             }
{..............................................................................}
Function Proj_StepOptions(Params : String; RequestId : String) : String;
Var
    Exporter : IPCB_Board3DModelExporter;
    NotTaken : String;
Begin
    Exporter := Nil;
    Try Exporter := PCBServer.Board3DModelExporter; Except Exporter := Nil; End;
    If Exporter = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_SUPPORTED',
            'This Altium build gives scripts no 3D model exporter');
        Exit;
    End;
    NotTaken := ApplyStepOptions(Exporter, Params);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        StepOptionsJson(Exporter) + ','
        + JsonStr('not_taken', NotTaken)));
End;

{..............................................................................}
{ Proj_GetChannels - multi-channel designs as Altium compiled them: every    }
{ sheet instance with its channel index, prefix, room and instance name.    }
{..............................................................................}
Function Proj_GetChannels(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Physical : IDocument;
    ProjectPath, Items, Sheet, Name, Path, Room, Prefix : String;
    I, Count, ChanIdx, Inst, Style, Misses : Integer;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    SmartCompile(Project);
    Style := -1;
    Misses := 0;
    Try Style := Project.DM_ChannelRoomNamingStyle; Except Inc(Misses); End;
    Count := 0;
    Try Count := Project.DM_PhysicalDocumentCount; Except Count := 0; End;
    Items := '';
    For I := 0 To Count - 1 Do
    Begin
        Physical := Nil;
        Try Physical := Project.DM_PhysicalDocuments(I); Except Physical := Nil; End;
        If Physical = Nil Then Continue;
        Sheet := ''; Name := ''; Path := ''; Room := ''; Prefix := ''; ChanIdx := -1; Inst := -1;
        Try Sheet := Physical.DM_FileName; Except Inc(Misses); End;
        Try Name := Physical.DM_PhysicalInstanceName; Except Inc(Misses); End;
        Try Path := Physical.DM_PhysicalInstancePath; Except Inc(Misses); End;
        Try Room := Physical.DM_PhysicalRoomName; Except Inc(Misses); End;
        Try Prefix := Physical.DM_ChannelPrefix; Except Inc(Misses); End;
        Try ChanIdx := Physical.DM_ChannelIndex; Except Inc(Misses); End;
        Try Inst := Physical.DM_CurrentInstanceNumber; Except Inc(Misses); End;
        If Items <> '' Then Items := Items + ',';
        Items := Items + JsonObj(JsonStr('sheet', Sheet) + ','
            + JsonStr('instance', Name) + ','
            + JsonStr('path', Path) + ','
            + JsonStr('room', Room) + ','
            + JsonStr('channel_prefix', Prefix) + ','
            + JsonInt('channel_index', ChanIdx) + ','
            + JsonInt('instance_number', Inst));
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonInt('room_naming_style', Style) + ','
        + JsonInt('sheet_instances', Count) + ','
        + JsonInt('read_errors', Misses) + ','
        + JsonRaw('instances', '[' + Items + ']')));
End;

{..............................................................................}
{ Proj_ComponentModels - where each compiled part's models resolve: model    }
{ name and type, each datafile's library, entity and kind, the full path     }
{ Altium finds for it (empty when it finds none), integrated or not.        }
{ Params: project_path, designator (optional), limit (default 300).          }
{..............................................................................}
Function Proj_ComponentModels(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Impl : IComponentImplementation;
    ProjectPath, Want, Items, Models, Files, Desig, MName, MType, Loc, Ent, Kind, Full, Dummy : String;
    I, J, F, NFiles, Listed, Limit, Missing, Misses : Integer;
    Integrated, Current : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    Want := UpperCase(ExtractJsonValue(Params, 'designator'));
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 300);
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    SmartCompile(Project);
    Doc := Nil;
    Try Doc := Project.DM_DocumentFlattened; Except Doc := Nil; End;
    If Doc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FLAT_NETLIST', 'Project has no compiled design (compile it first).');
        Exit;
    End;
    Items := '';
    Listed := 0;
    Missing := 0;
    Misses := 0;
    For I := 0 To Doc.DM_ComponentCount - 1 Do
    Begin
        If Listed >= Limit Then Break;
        Comp := Nil;
        Try Comp := Doc.DM_Components(I); Except Comp := Nil; End;
        If Comp = Nil Then Continue;
        Desig := '';
        Try Desig := Comp.DM_PhysicalDesignator; Except End;
        If (Want <> '') And (UpperCase(Desig) <> Want) Then Continue;
        Models := '';
        For J := 0 To Comp.DM_ImplementationCount - 1 Do
        Begin
            Impl := Nil;
            Try Impl := Comp.DM_Implementations(J); Except Impl := Nil; End;
            If Impl = Nil Then Continue;
            MName := ''; MType := ''; Integrated := False; Current := False; NFiles := 0;
            Try MName := Impl.DM_ModelName; Except Inc(Misses); End;
            Try MType := Impl.DM_ModelType; Except Inc(Misses); End;
            Try Integrated := Impl.DM_IntegratedModel; Except Inc(Misses); End;
            Try Current := Impl.DM_IsCurrent; Except Inc(Misses); End;
            Try NFiles := Impl.DM_DatafileCount; Except Inc(Misses); End;
            Files := '';
            For F := 0 To NFiles - 1 Do
            Begin
                Loc := ''; Ent := ''; Kind := ''; Full := ''; Dummy := '';
                Try Loc := Impl.DM_DatafileLocation(F); Except Inc(Misses); End;
                Try Ent := Impl.DM_DatafileEntity(F); Except Inc(Misses); End;
                Try Kind := Impl.DM_DatafileKind(F); Except Inc(Misses); End;
                Try Full := Impl.DM_DatafileFullPath(F, Ent, Kind, Dummy); Except Inc(Misses); End;
                If (Full = '') And (Not Integrated) Then Inc(Missing);
                If Files <> '' Then Files := Files + ',';
                Files := Files + JsonObj(JsonStr('library', Loc) + ','
                    + JsonStr('entity', Ent) + ','
                    + JsonStr('kind', Kind) + ','
                    + JsonStr('found_at', Full));
            End;
            If Models <> '' Then Models := Models + ',';
            Models := Models + JsonObj(JsonStr('name', MName) + ','
                + JsonStr('type', MType) + ','
                + JsonBool('current', Current) + ','
                + JsonBool('integrated', Integrated) + ','
                + JsonRaw('datafiles', '[' + Files + ']'));
        End;
        If Items <> '' Then Items := Items + ',';
        Items := Items + JsonObj(JsonStr('designator', Desig) + ','
            + JsonRaw('models', '[' + Models + ']'));
        Inc(Listed);
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonInt('components', Listed) + ','
        + JsonInt('datafiles_not_found', Missing) + ','
        + JsonInt('read_errors', Misses) + ','
        + JsonRaw('parts', '[' + Items + ']')));
End;

{..............................................................................}
{ Proj_LibraryLookup - Altium's own library manager, asked directly.          }
{ action locate: library, component -> where Altium finds the part.        }
{ action contents: library_path -> each part with its model count, and per    }
{ model whether it has a footprint and its first datafile path.               }
{ action extract: library_path (an IntLib), destination -> its sources.      }
{..............................................................................}
Function Proj_LibraryLookup(Params : String; RequestId : String) : String;
Var
    Action, Lib, CompName, Dest, Found, Dummy, Items, Name, PathStr, Problem : String;
    I, Count, Models, Limit, Libs : Integer;
    HasPcb, Known : Boolean;
Begin
    Action := LowerCase(ExtractJsonValue(Params, 'action'));
    Lib := ExtractJsonValue(Params, 'library');
    CompName := ExtractJsonValue(Params, 'component');
    Dest := ExtractJsonValue(Params, 'destination');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 500);
    Problem := '';
    If Action = 'locate' Then
    Begin
        If CompName = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'component is required');
            Exit;
        End;
        Found := ''; Dummy := '';
        Try Found := IntegratedLibraryManager.GetComponentLocation(Lib, CompName, Dummy); Except Problem := 'the library manager raised an error'; End;
        Result := BuildSuccessResponse(RequestId, JsonObj(
            JsonStr('component', CompName) + ','
            + JsonStr('library', Lib) + ','
            + JsonBool('found', Found <> '') + ','
            + JsonStr('location', Found) + ','
            + JsonStr('problem', Problem)));
        Exit;
    End;
    If Action = 'contents' Then
    Begin
        If Lib = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library is required (a full path)');
            Exit;
        End;
        Count := -1;
        Try Count := IntegratedLibraryManager.GetComponentCount(Lib); Except Count := -1; End;
        If Count < 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_READ', 'The library manager could not read ' + Lib);
            Exit;
        End;
        { The manager reads only the libraries it holds: installed ones and   }
        { those of open projects. Another file reads as zero parts.          }
        Known := False;
        Libs := 0;
        Try Libs := IntegratedLibraryManager.AvailableLibraryCount; Except Libs := 0; End;
        For I := 0 To Libs - 1 Do
        Begin
            PathStr := '';
            Try PathStr := IntegratedLibraryManager.AvailableLibraryPath(I); Except End;
            If SameText(PathStr, Lib) Then Known := True;
        End;
        If (Count = 0) And Not Known Then
            Problem := 'Altium''s library manager reads only libraries it has installed or that an open project uses; install this one first (lib_install_library)';
        Items := '';
        For I := 0 To Count - 1 Do
        Begin
            If I >= Limit Then Break;
            Name := ''; Models := 0; HasPcb := False; PathStr := '';
            Try Name := IntegratedLibraryManager.GetComponentName(Lib, I); Except End;
            Try Models := IntegratedLibraryManager.GetModelCount(Lib, I); Except End;
            Try HasPcb := IntegratedLibraryManager.ComponentHasModelOfType(Lib, I, 'PCBLIB'); Except End;
            If Models > 0 Then
            Begin
                Try PathStr := IntegratedLibraryManager.GetDatafilePath(Lib, I, 0, 0); Except End;
            End;
            If Items <> '' Then Items := Items + ',';
            Items := Items + JsonObj(JsonStr('name', Name) + ','
                + JsonInt('models', Models) + ','
                + JsonBool('has_footprint', HasPcb) + ','
                + JsonStr('first_datafile', PathStr));
        End;
        Result := BuildSuccessResponse(RequestId, JsonObj(
            JsonStr('library', Lib) + ','
            + JsonInt('component_count', Count) + ','
            + JsonBool('known_to_manager', Known) + ','
            + JsonBool('truncated', Count > Limit) + ','
            + JsonStr('problem', Problem) + ','
            + JsonRaw('components', '[' + Items + ']')));
        Exit;
    End;
    If Action = 'extract' Then
    Begin
        If (Lib = '') Or (Dest = '') Then
        Begin
            Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'library and destination are required');
            Exit;
        End;
        Try IntegratedLibraryManager.ExtractSourcesToPath(Lib, Dest); Except Problem := 'the library manager raised an error'; End;
        Result := BuildSuccessResponse(RequestId, JsonObj(
            JsonStr('library', Lib) + ','
            + JsonStr('destination', Dest) + ','
            + JsonBool('done', Problem = '') + ','
            + JsonStr('problem', Problem)));
        Exit;
    End;
    Result := BuildErrorResponse(RequestId, 'BAD_PARAM', 'action is locate, contents or extract');
End;

{..............................................................................}
{ Proj_OutputPanel - the lines in Altium's Output panel, newest last.        }
{ Params: last (how many, default 100), clear 'true' empties it after.      }
{..............................................................................}
Function Proj_OutputPanel(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Items, Line : String;
    I, Count, Last, First : Integer;
Begin
    Last := StrToIntDef(ExtractJsonValue(Params, 'last'), 100);
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    Count := -1;
    Try Count := Workspace.DM_GetOutputLineCount; Except Count := -1; End;
    If Count < 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_SUPPORTED', 'This Altium build gives scripts no Output panel');
        Exit;
    End;
    First := Count - Last;
    If First < 0 Then First := 0;
    Items := '';
    For I := First To Count - 1 Do
    Begin
        Line := '';
        Try Line := Workspace.DM_GetOutputLine(I); Except End;
        If Items <> '' Then Items := Items + ',';
        Items := Items + '"' + EscapeJsonString(Line) + '"';
    End;
    If LowerCase(ExtractJsonValue(Params, 'clear')) = 'true' Then
    Begin
        Try Workspace.DM_ClearOutputLines; Except End;
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonInt('line_count', Count) + ','
        + JsonRaw('lines', '[' + Items + ']')));
End;

{..............................................................................}
{ Proj_AddGeneratedDocument - list a file the project produced (a report, a }
{ STEP, a PDF) under its Generated folder. Params: project_path, file_path.  }
{..............................................................................}
Function Proj_AddGeneratedDocument(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ProjectPath, FilePath, Problem, Have : String;
    I, Before, After : Integer;
    Listed : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    FilePath := ExtractJsonValue(Params, 'file_path');
    If FilePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'file_path is required');
        Exit;
    End;
    If Not FileExists(FilePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'No file at ' + FilePath);
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    Before := -1;
    Try Before := Project.DM_GeneratedDocumentCount; Except Before := -1; End;
    Problem := '';
    Try Project.DM_AddGeneratedDocument(FilePath); Except Problem := 'the project raised an error'; End;
    After := -1;
    Try After := Project.DM_GeneratedDocumentCount; Except After := -1; End;
    Listed := False;
    For I := 0 To After - 1 Do
    Begin
        Doc := Nil;
        Have := '';
        Try Doc := Project.DM_GeneratedDocuments(I); Except Doc := Nil; End;
        If Doc = Nil Then Continue;
        Try Have := Doc.DM_FullPath; Except End;
        If UpperCase(Have) = UpperCase(FilePath) Then Listed := True;
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonBool('listed', Listed) + ','
        + JsonInt('generated_before', Before) + ','
        + JsonInt('generated_after', After) + ','
        + JsonStr('problem', Problem)));
End;

{..............................................................................}
{ Proj_ExportSTEPApi - write the board's 3D model through Altium's own        }
{ exporter object (PCBServer.Board3DModelExporter.ExportBoard3DModel), with   }
{ no dialog. The exporter keeps the                                           }
{ options last used in File > Export > STEP 3D. format is the exporter's own  }
{ code: 0 wrote STEP on AD 26.11.1.5 (7 MB in 17 s, no dialog).            }
{ generated is true only when a new file is at output_path afterwards, so a  }
{ build where the call does nothing says so and proj_export_step falls back  }
{ to the dialog route.                                                        }
{ Params: output_path (required), format (default 0)                          }
{..............................................................................}
Function Proj_ExportSTEPApi(Params : String; RequestId : String) : String;
Var
    OutputPath, Problem, NotTaken : String;
    Board : IPCB_Board;
    Exporter : IPCB_Board3DModelExporter;
    Proj : IProject;
    Format, AgeBefore, AgeAfter : Integer;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');
    Format := StrToIntDef(ExtractJsonValue(Params, 'format'), 0);
    If OutputPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'output_path is required');
        Exit;
    End;
    Board := GetPCBBoardAnywhere(0);
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is open');
        Exit;
    End;
    Exporter := Nil;
    Try Exporter := PCBServer.Board3DModelExporter; Except Exporter := Nil; End;
    If Exporter = Nil Then
    Begin
        Result := BuildSuccessResponse(RequestId, JsonObj(JsonBool('generated', False)
            + ',' + JsonStr('reason', 'this Altium build gives scripts no 3D model exporter')));
        Exit;
    End;
    Proj := ProjectHoldingDoc(Board.FileName);
    AgeBefore := -1;
    Try If FileExists(OutputPath) Then AgeBefore := FileAge(OutputPath); Except End;
    { The exporter keeps its settings between runs: any given here stay   }
    { for the next export too, as they do in the dialog.                   }
    NotTaken := ApplyStepOptions(Exporter, Params);
    Problem := '';
    Try
        Exporter.ExportBoard3DModel(OutputPath, Format, Board, Proj);
    Except
        Problem := 'the exporter raised an error';
    End;
    AgeAfter := -1;
    Try If FileExists(OutputPath) Then AgeAfter := FileAge(OutputPath); Except End;
    If (AgeAfter <> -1) And (AgeAfter <> AgeBefore) Then
        Result := BuildSuccessResponse(RequestId, JsonObj(JsonBool('generated', True)
            + ',' + JsonStr('output_path', OutputPath)
            + ',' + JsonStr('options_not_taken', NotTaken)
            + ',' + JsonStr('board', Board.FileName)
            + ',' + JsonBool('in_project', Proj <> Nil)))
    Else
    Begin
        If Problem = '' Then Problem := 'the exporter returned and no new file is at output_path';
        Result := BuildSuccessResponse(RequestId, JsonObj(JsonBool('generated', False)
            + ',' + JsonStr('output_path', OutputPath)
            + ',' + JsonStr('reason', Problem)));
    End;
End;

{..............................................................................}
{ Export PCB to STEP 3D model                                                 }
{ Params: output_path (optional, if omitted, Altium may prompt)              }
{..............................................................................}

Function Proj_ExportSTEP(Params : String; RequestId : String) : String;
Var
    OutputPath : String;
    Board : IPCB_Board;
    AgeBefore, AgeAfter : Integer;
Begin
    OutputPath := ExtractJsonValue(Params, 'output_path');

    { RunProcess acts on the FOCUSED view, and the board lookup puts the  }
    { user's previous view back, so an export from a schematic tab went   }
    { nowhere. ResolvePCBBoard leaves the board focused.                  }
    Board := GetPCBBoardAnywhere(0);
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is open');
        Exit;
    End;
    Board := ResolvePCBBoard(Board.FileName);

    AgeBefore := -1;
    If OutputPath <> '' Then
        Try If FileExists(OutputPath) Then AgeBefore := FileAge(OutputPath); Except End;

    { PCB:ExportSTEP3D is not a registered process, so it did nothing and  }
    { said nothing. The STEP exporter is the 'STEP 3D' filter of           }
    { PCB:Export (advpcb.ins), the route File > Export > STEP 3D takes. It }
    { asks for the file name (already filled in from FileName), the       }
    { export options, and closes with "Done!"; proj_export_step answers    }
    { those three from outside while this waits in RunProcess.             }
    ResetParameters;
    AddStringParameter('Format', 'STEP 3D');
    If OutputPath <> '' Then
        AddStringParameter('FileName', OutputPath);
    RunProcess('PCB:Export');

    { success:true used to come back whatever happened, and a reported    }
    { run that wrote nothing was indistinguishable from one that did.     }
    If OutputPath = '' Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            '{"generated":false,"dispatched":true'
            + ',"reason":"the export was launched with no output_path, so '
            + 'there is nothing to check; pass output_path, or run an OutJob '
            + 'with an ExportSTEP output through proj_run_outjob"}');
        Exit;
    End;

    AgeAfter := -1;
    Try If FileExists(OutputPath) Then AgeAfter := FileAge(OutputPath); Except End;
    If (AgeAfter <> -1) And (AgeAfter <> AgeBefore) Then
        Result := BuildSuccessResponse(RequestId,
            '{"generated":true,"success":true,"output_path":"'
            + EscapeJsonString(OutputPath) + '"}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"generated":false,"dispatched":true,"output_path":"'
            + EscapeJsonString(OutputPath) + '"'
            + ',"reason":"the export was launched but no new file is at '
            + 'output_path. The export dialogs were cancelled or answered '
            + 'with another file name"}');
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
    Found, OutJobOpened, OutJobActive : Boolean;
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

    { The container writes nothing unless the OutJob is the ACTIVE document: }
    { focused but not shown, it ran cleanly and wrote 0 files (AD25, AD26).  }
    { Open it the way the Projects panel does, then show it, then focus it.  }
    OutJobOpened := False;
    OutJobDoc := Nil;
    Try OutJobDoc := Client.GetDocumentByPath(OutJobPath); Except OutJobDoc := Nil; End;
    If OutJobDoc = Nil Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', OutJobPath);
        RunProcess('WorkspaceManager:OpenObject');
        Try OutJobDoc := Client.GetDocumentByPath(OutJobPath); Except OutJobDoc := Nil; End;
        If OutJobDoc = Nil Then
        Begin
            Try OutJobDoc := Client.OpenDocument('OUTPUTJOB', OutJobPath); Except OutJobDoc := Nil; End;
        End;
        OutJobOpened := OutJobDoc <> Nil;
    End;
    If OutJobDoc <> Nil Then
    Begin
        Try Client.ShowDocument(OutJobDoc); Except End;
        Try OutJobDoc.Focus; Except End;
    End;
    OutJobActive := False;
    Try
        Workspace := GetWorkspace;
        If Workspace <> Nil Then
            If Workspace.DM_FocusedDocument <> Nil Then
                OutJobActive := UpperCase(Workspace.DM_FocusedDocument.DM_FullPath)
                    = UpperCase(OutJobPath);
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

    { NOT A SUCCESS CLAIM. The process was issued, and nothing here can say }
    { whether it wrote anything: a container bound to a managed release, or }
    { with its outputs off, runs cleanly and produces no file, and this used }
    { to reply success with an output_dir that had never been created. The  }
    { Python tools check that directory afterwards and decide success there. }
    Result := BuildSuccessResponse(RequestId,
        '{"process_issued":true' +
        ',"container_name":"' + EscapeJsonString(ContainerName) + '"' +
        ',"container_type":"' + EscapeJsonString(ContainerType) + '"' +
        ',"relative_path":"' + EscapeJsonString(RelativePath) + '"' +
        ',"output_dir":"' + EscapeJsonString(OutputDir) + '"' +
        ',"outjob_path":"' + EscapeJsonString(OutJobPath) + '"' +
        ',"outjob_opened":' + BoolToJsonStr(OutJobOpened) +
        ',"outjob_active":' + BoolToJsonStr(OutJobActive) + '}');
End;

{..............................................................................}
{ VariantLabel - the name Altium saves and shows for a variant: its          }
{ description. DM_Name lives only in memory and reads 'Variant' once the     }
{ project is reopened.                                                         }
{..............................................................................}
Function VariantLabel(V : IProjectVariant) : String;
Var
    Lbl : String;
Begin
    Lbl := '';
    If V <> Nil Then
    Begin
        Lbl := V.DM_Description;
        If Lbl = '' Then Lbl := V.DM_Name;
    End;
    Result := Lbl;
End;

{..............................................................................}
{ FindVariantNamed - a project variant by its name or its description,        }
{ ignoring case. Nil when there is none.                                       }
{..............................................................................}
Function FindVariantNamed(Project : IProject; Wanted : String) : IProjectVariant;
Var
    I : Integer;
    V : IProjectVariant;
Begin
    Result := Nil;
    For I := 0 To Project.DM_ProjectVariantCount - 1 Do
    Begin
        V := Project.DM_ProjectVariants(I);
        If V = Nil Then Continue;
        If (UpperCase(V.DM_Name) = UpperCase(Wanted))
            Or (UpperCase(V.DM_Description) = UpperCase(Wanted)) Then
        Begin
            Result := V;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ VariantNameTaken - True when a variant other than Skip already has Name as  }
{ its name or its description, ignoring case.                                  }
{..............................................................................}
Function VariantNameTaken(Project : IProject; Name : String; Skip : IProjectVariant) : Boolean;
Var
    I : Integer;
    V : IProjectVariant;
Begin
    Result := False;
    For I := 0 To Project.DM_ProjectVariantCount - 1 Do
    Begin
        V := Project.DM_ProjectVariants(I);
        If V = Nil Then Continue;
        If (Skip <> Nil) And (V.DM_UniqueId = Skip.DM_UniqueId) Then Continue;
        If (UpperCase(V.DM_Name) = UpperCase(Name))
            Or (UpperCase(V.DM_Description) = UpperCase(Name)) Then
        Begin
            Result := True;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ AltLinkJson - the library link of a variation's alternate part, as a JSON   }
{ object: what a BOM needs to name the part that is fitted instead.           }
{ An empty object when the variation has no link.                              }
{..............................................................................}
Function AltLinkJson(CompVar : IComponentVariation) : String;
Var
    Link : IComponentLibraryLink;
    Body : String;
Begin
    Link := Nil;
    Try Link := CompVar.DM_AlternateLibraryLink; Except Link := Nil; End;
    If Link = Nil Then
    Begin
        Body := '{}';
        Result := Body;
        Exit;
    End;
    Body := '';
    Try
        Body := JsonStr('design_item_id', Link.DM_DesignItemID) + ','
            + JsonStr('symbol_reference', Link.DM_SymbolReference) + ','
            + JsonStr('footprint', Link.DM_Footprint) + ','
            + JsonBool('same_footprint', Link.DM_IsSameFootprint) + ','
            + JsonStr('library', Link.DM_SourceLibraryName) + ','
            + JsonStr('library_identifier', Link.DM_LibraryIdentifier) + ','
            + JsonInt('library_identifier_kind', Link.DM_LibIdentifierKind) + ','
            + JsonBool('use_library_name', Link.DM_UseLibraryName) + ','
            + JsonBool('use_db_table_name', Link.DM_UseDBTableName) + ','
            + JsonStr('db_table_name', Link.DM_DatabaseTableName) + ','
            + JsonStr('vault_guid', Link.DM_VaultGUID) + ','
            + JsonStr('item_guid', Link.DM_ItemGUID) + ','
            + JsonStr('revision_guid', Link.DM_RevisionGUID);
    Except
    End;
    Body := '{' + Body + '}';
    Result := Body;
End;

{..............................................................................}
{ CopyAltLink - copy the alternate part's library link from one variation to  }
{ another, field by field. Returns False when either side has no link.        }
{..............................................................................}
Function CopyAltLink(Src, Dst : IComponentVariation) : Boolean;
Var
    S, D : IComponentLibraryLink;
Begin
    Result := False;
    S := Nil;
    D := Nil;
    Try S := Src.DM_AlternateLibraryLink; Except S := Nil; End;
    Try D := Dst.DM_AlternateLibraryLink; Except D := Nil; End;
    If (S = Nil) Or (D = Nil) Then Exit;
    Try
        D.DM_SetLibIdentifierKind(S.DM_LibIdentifierKind);
        D.DM_SetLibraryIdentifier(S.DM_LibraryIdentifier);
        D.DM_SetSymbolReference(S.DM_SymbolReference);
        D.DM_SetDesignItemID(S.DM_DesignItemID);
        D.DM_SetUseLibraryName(S.DM_UseLibraryName);
        D.DM_SetSourceLibraryName(S.DM_SourceLibraryName);
        D.DM_SetUseDBTableName(S.DM_UseDBTableName);
        D.DM_SetDatabaseTableName(S.DM_DatabaseTableName);
        D.DM_SetVaultGUID(S.DM_VaultGUID);
        D.DM_SetItemGUID(S.DM_ItemGUID);
        D.DM_SetRevisionGUID(S.DM_RevisionGUID);
        D.DM_SetFootprint(S.DM_Footprint);
        D.DM_SetIsSameFootprint(S.DM_IsSameFootprint);
        Result := (D.DM_DesignItemID = S.DM_DesignItemID)
            And (D.DM_SymbolReference = S.DM_SymbolReference)
            And (D.DM_Footprint = S.DM_Footprint);
    Except
        Result := False;
    End;
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
    VParam : IParameter;
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

        VarInfo := '{"name":"' + EscapeJsonString(VariantLabel(Variant)) + '"';
        VarInfo := VarInfo + ',"description":"' + EscapeJsonString(Variant.DM_Description) + '"';

        { Component variations }
        { The variant's own parameters (Variant Management's parameter     }
        { list), which a copy carries over.                                 }
        VarInfo := VarInfo + ',"parameters":[';
        Try
            For K := 0 To Variant.DM_ParameterCount - 1 Do
            Begin
                VParam := Variant.DM_Parameters(K);
                If VParam = Nil Then Continue;
                If Copy(VarInfo, Length(VarInfo), 1) <> '[' Then VarInfo := VarInfo + ',';
                VarInfo := VarInfo + JsonObj(
                    JsonStr('name', VParam.DM_Name) + ','
                    + JsonStr('value', VParam.DM_Value));
            End;
        Except
        End;
        VarInfo := VarInfo + ']';

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
            CompInfo := CompInfo + ',' + JsonStr('unique_id', CompVar.DM_UniqueId);
            If CompVar.DM_VariationKind = 2 Then
                CompInfo := CompInfo + ',' + JsonRaw('alternate_link', AltLinkJson(CompVar));

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
            VariantsJson := VariantsJson + '"' + EscapeJsonString(VariantLabel(Variant)) + '"'
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
            Result := BuildSuccessResponse(RequestId, '{"name":"' + EscapeJsonString(VariantLabel(Variant)) + '","description":"' + EscapeJsonString(Variant.DM_Description) + '"}')
        Else
            Result := BuildSuccessResponse(RequestId, '{"name":"[No Variations]","description":"Base design, no variant active"}');
    Except
        Result := BuildSuccessResponse(RequestId, '{"name":"[No Variations]","description":"Base design, no variant active"}');
    End;
End;

{..............................................................................}
{ CurrentVariantIs - True when the project's current variant is V, compared  }
{ by unique id; V Nil means the base design.                                   }
{..............................................................................}
Function CurrentVariantIs(Project : IProject; V : IProjectVariant) : Boolean;
Var
    Current : IProjectVariant;
Begin
    Result := False;
    Current := Nil;
    Try Current := Project.DM_CurrentProjectVariant; Except Exit; End;
    If V = Nil Then Result := Current = Nil
    Else If Current <> Nil Then Result := Current.DM_UniqueId = V.DM_UniqueId;
End;

{..............................................................................}
{ Switch active variant by name or description                                 }
{ Params: variant_name, project_path (optional)                               }
{..............................................................................}

Function Proj_SetActiveVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath, VariantName, ActualName, Route : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant, Current : IProjectVariant;
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

    { By name or description; [No Variations] is the base design. }
    Variant := Nil;
    If UpperCase(VariantName) <> '[NO VARIATIONS]' Then
    Begin
        Variant := FindVariantNamed(Project, VariantName);
        If Variant = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'VARIANT_NOT_FOUND', 'Variant not found: ' + VariantName);
            Exit;
        End;
    End;

    { IProject.DM_SetCurrentProjectVariant first; the VariantManagement   }
    { process stays for a build where it does nothing.                    }
    Route := 'api';
    Try Project.DM_SetCurrentProjectVariant(Variant); Except End;
    If Not CurrentVariantIs(Project, Variant) Then
    Begin
        Route := 'process';
        ResetParameters;
        AddStringParameter('Action', 'SetCurrentVariant');
        AddStringParameter('VariantName', VariantLabel(Variant));
        RunProcess('WorkspaceManager:VariantManagement');
    End;

    { READ BACK WHICH VARIANT IS CURRENT, by its unique id: the name a    }
    { variant was found by is not proof that the switch happened.         }
    ActualName := '[No Variations]';
    Try
        Current := Project.DM_CurrentProjectVariant;
        If Current <> Nil Then ActualName := VariantLabel(Current);
    Except End;

    If CurrentVariantIs(Project, Variant) Then
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"variant_name":"' + EscapeJsonString(ActualName)
            + '","verified":true,"route":"' + Route + '"}')
    Else
        Result := BuildSuccessResponse(RequestId,
            JsonObj(
                JsonBool('success', False) + ',' +
                JsonStr('variant_name', VariantName) + ',' +
                JsonStr('current_variant', ActualName) + ',' +
                JsonStr('reason', 'the switch was made but the project '
                    + 'still reports a different current variant. Set it in '
                    + 'the Variant Management dialog.')
            ));
End;

{..............................................................................}
{ Proj_DeleteVariant - remove one project variant by name.                     }
{                                                                              }
{ IProject.DM_RemoveProjectVariant(Index). Single entries are removed by     }
{ Proj_SetComponentVariation; Proj_CopyVariant keeps a copy first.            }
{                                                                              }
{ Addressed BY NAME rather than by index, because an index is only valid       }
{ against the listing that produced it: removing a variant renumbers the       }
{ rest, so a caller deleting two by index deletes the wrong second one.        }
{ Params: variant_name (required), project_path (optional, focused default).   }
{..............................................................................}

Function Proj_DeleteVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath, VariantName, WantedId : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant, Wanted : IProjectVariant;
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

    { By name or description, then by unique id: after a reopen every     }
    { variant's DM_Name reads 'Variant' and only the description tells    }
    { them apart.                                                          }
    Target := -1;
    Entries := -1;
    Wanted := FindVariantNamed(Project, VariantName);
    WantedId := '';
    If Wanted <> Nil Then WantedId := Wanted.DM_UniqueId;
    For I := 0 To PreCount - 1 Do
    Begin
        Variant := Project.DM_ProjectVariants(I);
        If (Variant <> Nil) And (Wanted <> Nil) And (Variant.DM_UniqueId = WantedId) Then
        Begin
            Target := I;
            { Reported so the caller learns what the deletion cost.        }
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
        If (Variant <> Nil) And (Variant.DM_UniqueId = WantedId) Then
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
    NewVariant : IProjectVariant;
    PreCount, PostCount : Integer;
    Route : String;
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

    { Whichever route runs, the variant count is checked after it and the  }
    { real outcome reported: a blind success here once masked that no      }
    { variant was created.                                                 }
    PreCount := -1;
    Try PreCount := Project.DM_ProjectVariantCount; Except End;

    { IProject.DM_AddProjectVariant first; the process    }
    { route below stays for a build where it adds nothing.                }
    Route := 'api';
    NewVariant := Nil;
    Try NewVariant := Project.DM_AddProjectVariant; Except NewVariant := Nil; End;
    If NewVariant <> Nil Then
    Begin
        NewVariant.DM_SetName(VarName);
        If VarDesc <> '' Then NewVariant.DM_SetDescription(VarDesc)
        Else NewVariant.DM_SetDescription(VarName);
        MarkDocDirtyByPath(Project.DM_ProjectFullPath);
    End;
    PostCount := -1;
    Try PostCount := Project.DM_ProjectVariantCount; Except End;
    If (PreCount < 0) Or (PostCount <= PreCount) Then
    Begin
        Route := 'process';
        ResetParameters;
        AddStringParameter('Action', 'AddVariant');
        AddStringParameter('VariantName', VarName);
        If VarDesc <> '' Then
            AddStringParameter('VariantDescription', VarDesc);
        RunProcess('WorkspaceManager:VariantManagement');

        Try Project.DM_Compile; Except End;
        PostCount := -1;
        Try PostCount := Project.DM_ProjectVariantCount; Except End;
    End;

    If (PreCount >= 0) And (PostCount > PreCount) Then
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"name":"' + EscapeJsonString(VarName) + '","description":"'
            + EscapeJsonString(VarDesc) + '","variant_count":' + IntToStr(PostCount)
            + ',"route":"' + Route + '"}')
    Else
        Result := BuildErrorResponse(RequestId, 'CREATE_UNCONFIRMED',
            'Variant creation could not be confirmed (count ' + IntToStr(PreCount)
            + ' -> ' + IntToStr(PostCount) + '). Script-based variant creation via '
            + 'WorkspaceManager:VariantManagement is unreliable; create the variant '
            + 'in the Variant Management dialog.');
End;

{..............................................................................}
{ VariationKindWord - a component variation's kind as a word.                  }
{..............................................................................}
Function VariationKindWord(Kind : Integer) : String;
Begin
    If Kind = 0 Then Result := 'fitted'
    Else If Kind = 1 Then Result := 'not_fitted'
    Else If Kind = 2 Then Result := 'alternate'
    Else Result := 'unknown(' + IntToStr(Kind) + ')';
End;

{..............................................................................}
{ Proj_SetComponentVariation - add, change or remove one part's entry in a    }
{ project variant: IProjectVariant.DM_AddComponentVariation,                  }
{ DM_RemoveComponentVariation and the IComponentVariation DM_Set* writers     }
{ The part is found by its physical designator in the         }
{ compiled project, and its variation by the part's unique id, the way the    }
{ not-fitted audit reads them. The entry is read back afterwards.             }
{ Params: variant (name or description, required), designator (required),    }
{ kind (fitted | not_fitted | alternate | remove), alternate_part,            }
{ project_path.                                                                }
{..............................................................................}
Function Proj_SetComponentVariation(Params : String; RequestId : String) : String;
Var
    ProjectPath, VarWanted, Desig, KindStr, AltPart, UniqueId, Before, After : String;
    AltLib, AltRef, AltFp, LinkJson : String;
    Link : IComponentLibraryLink;
    Workspace : IWorkspace;
    Project : IProject;
    Variant, V : IProjectVariant;
    CompVar, Other : IComponentVariation;
    FlatDoc : IDocument;
    Comp : IComponent;
    I, Kind, Found : Integer;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    VarWanted := ExtractJsonValue(Params, 'variant');
    Desig := ExtractJsonValue(Params, 'designator');
    KindStr := LowerCase(ExtractJsonValue(Params, 'kind'));
    AltPart := ExtractJsonValue(Params, 'alternate_part');
    AltLib := ExtractJsonValue(Params, 'alternate_library');
    AltRef := ExtractJsonValue(Params, 'alternate_lib_ref');
    AltFp := ExtractJsonValue(Params, 'alternate_footprint');
    If AltRef = '' Then AltRef := AltPart;
    If (VarWanted = '') Or (Desig = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'variant and designator are required');
        Exit;
    End;
    If KindStr = 'fitted' Then Kind := 0
    Else If KindStr = 'not_fitted' Then Kind := 1
    Else If KindStr = 'alternate' Then Kind := 2
    Else If KindStr = 'remove' Then Kind := -1
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_VALUE',
            'kind is fitted, not_fitted, alternate or remove');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    SmartCompile(Project);

    Variant := Nil;
    For I := 0 To Project.DM_ProjectVariantCount - 1 Do
    Begin
        V := Project.DM_ProjectVariants(I);
        If V = Nil Then Continue;
        If (UpperCase(V.DM_Name) = UpperCase(VarWanted))
            Or (UpperCase(V.DM_Description) = UpperCase(VarWanted)) Then
        Begin
            Variant := V;
            Break;
        End;
    End;
    If Variant = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_VARIANT', 'No variant named ' + VarWanted);
        Exit;
    End;

    UniqueId := '';
    FlatDoc := Nil;
    Try FlatDoc := Project.DM_DocumentFlattened; Except End;
    If FlatDoc <> Nil Then
    Begin
        For I := 0 To FlatDoc.DM_ComponentCount - 1 Do
        Begin
            Comp := Nil;
            Try Comp := FlatDoc.DM_Components(I); Except End;
            If Comp = Nil Then Continue;
            If UpperCase(Comp.DM_PhysicalDesignator) = UpperCase(Desig) Then
            Begin
                UniqueId := Comp.DM_UniqueId;
                Break;
            End;
        End;
    End;
    If UniqueId = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT',
            'No part ' + Desig + ' in the compiled project');
        Exit;
    End;

    CompVar := Nil;
    Try CompVar := Variant.DM_FindComponentVariationByUniqueId(UniqueId); Except End;
    Before := 'none';
    If CompVar <> Nil Then Before := VariationKindWord(CompVar.DM_VariationKind);

    If Kind = -1 Then
    Begin
        If CompVar <> Nil Then
        Begin
            Found := -1;
            For I := 0 To Variant.DM_VariationCount - 1 Do
            Begin
                Other := Variant.DM_Variations(I);
                If Other = Nil Then Continue;
                If Other.DM_UniqueId = UniqueId Then
                Begin
                    Found := I;
                    Break;
                End;
            End;
            If Found >= 0 Then Variant.DM_RemoveComponentVariation(Found);
        End;
    End
    Else
    Begin
        If CompVar = Nil Then
        Begin
            CompVar := Variant.DM_AddComponentVariation;
            CompVar.DM_SetUniqueId(UniqueId);
            CompVar.DM_SetPhysicalDesignator(Desig);
        End;
        CompVar.DM_SetVariationKind(Kind);
        If AltPart <> '' Then CompVar.DM_SetAlternatePart(AltPart);
        { The alternate's library link. Without one Altium saves the entry
          as an alternate but loads it back as fitted. Kind 2 is a library
          named with its extension, as an IntLib or SchLib is. }
        If (Kind = 2) And (AltLib <> '') Then
        Begin
            Link := Nil;
            Try Link := CompVar.DM_AlternateLibraryLink; Except Link := Nil; End;
            If Link <> Nil Then
            Begin
                Link.DM_SetLibIdentifierKind(2);
                Link.DM_SetLibraryIdentifier(AltLib);
                Link.DM_SetSourceLibraryName(AltLib);
                Link.DM_SetUseLibraryName(True);
                Link.DM_SetSymbolReference(AltRef);
                Link.DM_SetDesignItemID(AltRef);
                If AltFp <> '' Then
                Begin
                    Link.DM_SetFootprint(AltFp);
                    Link.DM_SetIsSameFootprint(False);
                End
                Else Link.DM_SetIsSameFootprint(True);
            End;
        End;
    End;

    CompVar := Nil;
    Try CompVar := Variant.DM_FindComponentVariationByUniqueId(UniqueId); Except End;
    After := 'none';
    AltPart := '';
    LinkJson := '{}';
    If CompVar <> Nil Then
    Begin
        After := VariationKindWord(CompVar.DM_VariationKind);
        Try AltPart := CompVar.DM_AlternatePart; Except End;
        If CompVar.DM_VariationKind = 2 Then LinkJson := AltLinkJson(CompVar);
    End;
    MarkDocDirtyByPath(Project.DM_ProjectFullPath);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('variant', Variant.DM_Description) + ','
        + JsonStr('designator', Desig) + ','
        + JsonStr('unique_id', UniqueId) + ','
        + JsonStr('before', Before) + ','
        + JsonStr('after', After) + ','
        + JsonStr('alternate_part', AltPart) + ','
        + JsonRaw('alternate_link', LinkJson) + ','
        + JsonBool('applied', ((Kind = -1) And (After = 'none'))
            Or ((Kind >= 0) And (After = VariationKindWord(Kind))))));
End;

{..............................................................................}
{ FlatComponentNamed - the compiled project's part with this physical         }
{ designator, ignoring case. Nil when there is none.                           }
{..............................................................................}
Function FlatComponentNamed(Project : IProject; Desig : String) : IComponent;
Var
    FlatDoc : IDocument;
    Comp : IComponent;
    I : Integer;
Begin
    Result := Nil;
    FlatDoc := Nil;
    Try FlatDoc := Project.DM_DocumentFlattened; Except End;
    If FlatDoc = Nil Then Exit;
    For I := 0 To FlatDoc.DM_ComponentCount - 1 Do
    Begin
        Comp := Nil;
        Try Comp := FlatDoc.DM_Components(I); Except End;
        If Comp = Nil Then Continue;
        If UpperCase(Comp.DM_PhysicalDesignator) = UpperCase(Desig) Then
        Begin
            Result := Comp;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ SplitTildes - split a '~~'-separated list into Items, dropping empties.     }
{..............................................................................}
Procedure SplitTildes(Text : String; Items : TStringList);
Var
    Remaining, Item : String;
    SepPos : Integer;
Begin
    Remaining := Text;
    While Length(Remaining) > 0 Do
    Begin
        SepPos := Pos('~~', Remaining);
        If SepPos = 0 Then
        Begin
            Item := Remaining;
            Remaining := '';
        End
        Else
        Begin
            Item := Copy(Remaining, 1, SepPos - 1);
            Remaining := Copy(Remaining, SepPos + 2, Length(Remaining));
        End;
        If Item <> '' Then Items.Add(Item);
    End;
End;

{..............................................................................}
{ ParamVariationIndex - index of the parameter variation named Name in       }
{ CompVar, ignoring case; -1 when there is none.                               }
{..............................................................................}
Function ParamVariationIndex(CompVar : IComponentVariation; Name : String) : Integer;
Var
    K : Integer;
    PV : IParameterVariation;
Begin
    Result := -1;
    For K := 0 To CompVar.DM_VariationCount - 1 Do
    Begin
        PV := CompVar.DM_Variations(K);
        If PV = Nil Then Continue;
        If UpperCase(PV.DM_ParameterName) = UpperCase(Name) Then
        Begin
            Result := K;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ ParamVariationsJson - a variation's parameter variations as a JSON array.  }
{..............................................................................}
Function ParamVariationsJson(CompVar : IComponentVariation) : String;
Var
    K : Integer;
    PV : IParameterVariation;
    Body : String;
Begin
    Body := '';
    If CompVar <> Nil Then
        For K := 0 To CompVar.DM_VariationCount - 1 Do
        Begin
            PV := CompVar.DM_Variations(K);
            If PV = Nil Then Continue;
            If Body <> '' Then Body := Body + ',';
            Body := Body + JsonObj(JsonStr('name', PV.DM_ParameterName) + ','
                + JsonStr('value', PV.DM_VariedValue));
        End;
    Body := '[' + Body + ']';
    Result := Body;
End;

{..............................................................................}
{ FindVariationFor - Comp's entry in Variant: by unique id, then by          }
{ physical designator, since the variant matrix found the unique id lookup  }
{ missing on a board whose flattened ids do not match the entries.           }
{..............................................................................}
Function FindVariationFor(Variant : IProjectVariant; Comp : IComponent) : IComponentVariation;
Var
    I : Integer;
    CV : IComponentVariation;
Begin
    Result := Nil;
    CV := Nil;
    Try CV := Variant.DM_FindComponentVariationByUniqueId(Comp.DM_UniqueId); Except CV := Nil; End;
    If CV <> Nil Then
    Begin
        Result := CV;
        Exit;
    End;
    For I := 0 To Variant.DM_VariationCount - 1 Do
    Begin
        CV := Variant.DM_Variations(I);
        If CV = Nil Then Continue;
        If UpperCase(CV.DM_PhysicalDesignator) = UpperCase(Comp.DM_PhysicalDesignator) Then
        Begin
            Result := CV;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ Proj_SetParameterVariation - set or clear parameter values one part takes  }
{ in a project variant (Variant Management's varied parameter values):        }
{ IComponentVariation.DM_AddParameterVariation, DM_FindParameterVariation,    }
{ DM_RemoveParameterVariation and IParameterVariation.DM_SetParameterName /   }
{ DM_SetVariedValue. A part with no entry in the variant gets a fitted one    }
{ to hold the values. A not-fitted part is refused: nothing of it is built.  }
{ Every value is read back.                                                   }
{ Params: variant (name or description), designator, set ('Name=Value'       }
{ items joined by '~~'), clear (names joined by '~~'), project_path.          }
{..............................................................................}
Function Proj_SetParameterVariation(Params : String; RequestId : String) : String;
Var
    ProjectPath, VarWanted, Desig, SetStr, ClearStr, Item, PName, PValue : String;
    Unknown, Missed, Response : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant : IProjectVariant;
    Comp : IComponent;
    CompVar : IComponentVariation;
    PV : IParameterVariation;
    Param : IParameter;
    SetItems, ClearItems, Known : TStringList;
    I, K, EqPos : Integer;
    Created, Applied, Same : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    VarWanted := ExtractJsonValue(Params, 'variant');
    Desig := ExtractJsonValue(Params, 'designator');
    SetStr := ExtractJsonValue(Params, 'set');
    ClearStr := ExtractJsonValue(Params, 'clear');
    If (VarWanted = '') Or (Desig = '') Or ((SetStr = '') And (ClearStr = '')) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'variant, designator and a value to set or clear are required');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    SmartCompile(Project);

    Variant := FindVariantNamed(Project, VarWanted);
    If Variant = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_VARIANT', 'No variant named ' + VarWanted);
        Exit;
    End;
    Comp := FlatComponentNamed(Project, Desig);
    If Comp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_COMPONENT',
            'No part ' + Desig + ' in the compiled project');
        Exit;
    End;

    CompVar := FindVariationFor(Variant, Comp);
    If (CompVar <> Nil) And (CompVar.DM_VariationKind = 1) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FITTED',
            Desig + ' is not fitted in ' + VarWanted + '; set it fitted or alternate first');
        Exit;
    End;

    SetItems := TStringList.Create;
    ClearItems := TStringList.Create;
    Known := TStringList.Create;
    Try
        SplitTildes(SetStr, SetItems);
        SplitTildes(ClearStr, ClearItems);

        { Names the part carries. A name it does not carry is still        }
        { written, but it is named in the reply so a typo does not pass     }
        { for a change.                                                     }
        Try
            For I := 0 To Comp.DM_ParameterCount - 1 Do
            Begin
                Param := Comp.DM_Parameters(I);
                If Param <> Nil Then Known.Add(UpperCase(Param.DM_Name));
            End;
        Except
        End;
        Known.Add('COMMENT');

        Created := False;
        If (CompVar = Nil) And (SetItems.Count > 0) Then
        Begin
            CompVar := Variant.DM_AddComponentVariation;
            CompVar.DM_SetUniqueId(Comp.DM_UniqueId);
            CompVar.DM_SetPhysicalDesignator(Comp.DM_PhysicalDesignator);
            CompVar.DM_SetVariationKind(0);
            Created := True;
        End;

        Unknown := '';
        If CompVar <> Nil Then
        Begin
            For I := 0 To SetItems.Count - 1 Do
            Begin
                Item := SetItems[I];
                EqPos := Pos('=', Item);
                If EqPos < 2 Then Continue;
                PName := Copy(Item, 1, EqPos - 1);
                PValue := Copy(Item, EqPos + 1, Length(Item));
                If Known.IndexOf(UpperCase(PName)) < 0 Then
                Begin
                    If Unknown <> '' Then Unknown := Unknown + ',';
                    Unknown := Unknown + '"' + EscapeJsonString(PName) + '"';
                End;
                K := ParamVariationIndex(CompVar, PName);
                If K >= 0 Then PV := CompVar.DM_Variations(K)
                Else
                Begin
                    PV := CompVar.DM_AddParameterVariation;
                    PV.DM_SetParameterName(PName);
                End;
                PV.DM_SetVariedValue(PValue);
            End;
            For I := 0 To ClearItems.Count - 1 Do
            Begin
                K := ParamVariationIndex(CompVar, ClearItems[I]);
                If K >= 0 Then CompVar.DM_RemoveParameterVariation(K);
            End;
        End;

        { Read back through a fresh lookup, not the object just written. }
        CompVar := FindVariationFor(Variant, Comp);
        Missed := '';
        For I := 0 To SetItems.Count - 1 Do
        Begin
            Item := SetItems[I];
            EqPos := Pos('=', Item);
            If EqPos < 2 Then Continue;
            PName := Copy(Item, 1, EqPos - 1);
            PValue := Copy(Item, EqPos + 1, Length(Item));
            Same := False;
            If CompVar <> Nil Then
            Begin
                K := ParamVariationIndex(CompVar, PName);
                If K >= 0 Then Same := CompVar.DM_Variations(K).DM_VariedValue = PValue;
            End;
            If Not Same Then
            Begin
                If Missed <> '' Then Missed := Missed + ',';
                Missed := Missed + '"' + EscapeJsonString(PName) + '"';
            End;
        End;
        For I := 0 To ClearItems.Count - 1 Do
        Begin
            K := -1;
            If CompVar <> Nil Then K := ParamVariationIndex(CompVar, ClearItems[I]);
            If K >= 0 Then
            Begin
                If Missed <> '' Then Missed := Missed + ',';
                Missed := Missed + '"' + EscapeJsonString(ClearItems[I]) + '"';
            End;
        End;
    Finally
        SetItems.Free;
        ClearItems.Free;
        Known.Free;
    End;

    Applied := Missed = '';
    MarkDocDirtyByPath(Project.DM_ProjectFullPath);
    Response := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('variant', VariantLabel(Variant)) + ','
        + JsonStr('designator', Comp.DM_PhysicalDesignator) + ','
        + JsonStr('unique_id', Comp.DM_UniqueId) + ','
        + JsonBool('entry_created', Created) + ','
        + JsonRaw('parameters', ParamVariationsJson(CompVar)) + ','
        + JsonRaw('not_on_part', '[' + Unknown + ']') + ','
        + JsonRaw('not_applied', '[' + Missed + ']') + ','
        + JsonBool('applied', Applied)));
    Result := Response;
End;

{..............................................................................}
{ Proj_RenameVariant - rename a variant. Altium saves only the description   }
{ and shows it as the name, so both DM_SetDescription and DM_SetName take    }
{ the new name, read back. A name another variant already has is refused.  }
{ Params: variant (name or description), new_name, project_path.             }
{..............................................................................}
Function Proj_RenameVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath, VarWanted, NewName, OldName, Response : String;
    Workspace : IWorkspace;
    Project : IProject;
    Variant : IProjectVariant;
    WasActive, Applied : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    VarWanted := ExtractJsonValue(Params, 'variant');
    NewName := ExtractJsonValue(Params, 'new_name');
    If (VarWanted = '') Or (NewName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'variant and new_name are required');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    Variant := FindVariantNamed(Project, VarWanted);
    If Variant = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_VARIANT', 'No variant named ' + VarWanted);
        Exit;
    End;
    If VariantNameTaken(Project, NewName, Variant) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_TAKEN',
            'Another variant is already named ' + NewName);
        Exit;
    End;

    WasActive := CurrentVariantIs(Project, Variant);
    OldName := VariantLabel(Variant);
    Variant.DM_SetDescription(NewName);
    Variant.DM_SetName(NewName);

    Applied := Variant.DM_Description = NewName;
    MarkDocDirtyByPath(Project.DM_ProjectFullPath);
    Response := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('name_before', OldName) + ','
        + JsonStr('name', VariantLabel(Variant)) + ','
        + JsonBool('was_active', WasActive) + ','
        + JsonBool('applied', Applied)));
    Result := Response;
End;

{..............................................................................}
{ Proj_CopyVariant - a new variant holding a copy of another's entries:       }
{ every part's kind, alternate part and its library link, its parameter       }
{ values, and the variant's own parameters. Each entry is compared with its  }
{ source afterwards. The name goes in the description, which is what       }
{ Altium saves and shows.                                                      }
{ Params: source (name or description), name, project_path.                   }
{..............................................................................}
Function Proj_CopyVariant(Params : String; RequestId : String) : String;
Var
    ProjectPath, SrcWanted, NewName, Missed, Response : String;
    Workspace : IWorkspace;
    Project : IProject;
    Src, Dst : IProjectVariant;
    S, D : IComponentVariation;
    SP, DP : IParameterVariation;
    VParam : IParameter;
    I, J, K, PreCount, PostCount, Entries, Values, LinkMisses : Integer;
    Same : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    SrcWanted := ExtractJsonValue(Params, 'source');
    NewName := ExtractJsonValue(Params, 'name');
    If (SrcWanted = '') Or (NewName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'source and name are required');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    Src := FindVariantNamed(Project, SrcWanted);
    If Src = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_VARIANT', 'No variant named ' + SrcWanted);
        Exit;
    End;
    If VariantNameTaken(Project, NewName, Nil) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NAME_TAKEN',
            'A variant is already named ' + NewName);
        Exit;
    End;

    PreCount := Project.DM_ProjectVariantCount;
    Dst := Nil;
    Try Dst := Project.DM_AddProjectVariant; Except Dst := Nil; End;
    PostCount := Project.DM_ProjectVariantCount;
    If (Dst = Nil) Or (PostCount <= PreCount) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_UNCONFIRMED',
            'The new variant could not be added (count ' + IntToStr(PreCount)
            + ' -> ' + IntToStr(PostCount) + ')');
        Exit;
    End;
    Dst.DM_SetName(NewName);
    Dst.DM_SetDescription(NewName);

    Try
        For I := 0 To Src.DM_ParameterCount - 1 Do
        Begin
            VParam := Src.DM_Parameters(I);
            If VParam <> Nil Then Dst.DM_AddParameter(VParam.DM_Name, VParam.DM_Value);
        End;
    Except
    End;

    Entries := 0;
    Values := 0;
    LinkMisses := 0;
    For I := 0 To Src.DM_VariationCount - 1 Do
    Begin
        S := Src.DM_Variations(I);
        If S = Nil Then Continue;
        D := Dst.DM_AddComponentVariation;
        D.DM_SetUniqueId(S.DM_UniqueId);
        D.DM_SetPhysicalDesignator(S.DM_PhysicalDesignator);
        D.DM_SetVariationKind(S.DM_VariationKind);
        If S.DM_VariationKind = 2 Then
        Begin
            If S.DM_AlternatePart <> '' Then D.DM_SetAlternatePart(S.DM_AlternatePart);
            If Not CopyAltLink(S, D) Then LinkMisses := LinkMisses + 1;
        End;
        { Found or added: Altium keeps the alternate part's name as a      }
        { varied Comment, which DM_SetAlternatePart has just written.       }
        For K := 0 To S.DM_VariationCount - 1 Do
        Begin
            SP := S.DM_Variations(K);
            If SP = Nil Then Continue;
            J := ParamVariationIndex(D, SP.DM_ParameterName);
            If J >= 0 Then DP := D.DM_Variations(J)
            Else
            Begin
                DP := D.DM_AddParameterVariation;
                DP.DM_SetParameterName(SP.DM_ParameterName);
            End;
            DP.DM_SetVariedValue(SP.DM_VariedValue);
            Values := Values + 1;
        End;
        Entries := Entries + 1;
    End;

    { Compare every source entry with the copy found by its unique id. }
    Missed := '';
    For I := 0 To Src.DM_VariationCount - 1 Do
    Begin
        S := Src.DM_Variations(I);
        If S = Nil Then Continue;
        D := Nil;
        Try D := Dst.DM_FindComponentVariationByUniqueId(S.DM_UniqueId); Except End;
        Same := D <> Nil;
        If Same Then
            Same := (D.DM_VariationKind = S.DM_VariationKind)
                And (D.DM_AlternatePart = S.DM_AlternatePart)
                And (D.DM_VariationCount = S.DM_VariationCount);
        If Same Then
            For K := 0 To S.DM_VariationCount - 1 Do
            Begin
                SP := S.DM_Variations(K);
                If SP = Nil Then Continue;
                If ParamVariationIndex(D, SP.DM_ParameterName) < 0 Then Same := False
                Else If D.DM_Variations(ParamVariationIndex(D, SP.DM_ParameterName)).DM_VariedValue
                    <> SP.DM_VariedValue Then Same := False;
            End;
        If Not Same Then
        Begin
            If Missed <> '' Then Missed := Missed + ',';
            Missed := Missed + '"' + EscapeJsonString(S.DM_PhysicalDesignator) + '"';
        End;
    End;

    MarkDocDirtyByPath(Project.DM_ProjectFullPath);
    Response := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('source', VariantLabel(Src)) + ','
        + JsonStr('name', VariantLabel(Dst)) + ','
        + JsonInt('entries_copied', Entries) + ','
        + JsonInt('parameter_values_copied', Values) + ','
        + JsonInt('variant_parameters', Dst.DM_ParameterCount) + ','
        + JsonInt('alternate_links_not_copied', LinkMisses) + ','
        + JsonRaw('differs', '[' + Missed + ']') + ','
        + JsonBool('applied', (Missed = '') And (Dst.DM_VariationCount = Src.DM_VariationCount))));
    Result := Response;
End;

{..............................................................................}
{ Proj_NetColor - read, and optionally set, the colour the project gives a    }
{ net: IProject.DM_GetNetColorByName / DM_SetNetColorByName.   }
{ Colours are Altium's BGR integers; color = -1 reads only.                   }
{ Params: net (required), color, project_path.                                }
{..............................................................................}
Function Proj_NetColor(Params : String; RequestId : String) : String;
Var
    ProjectPath, NetName, ColorStr : String;
    Workspace : IWorkspace;
    Project : IProject;
    Before, After, Wanted : Integer;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    NetName := ExtractJsonValue(Params, 'net');
    ColorStr := ExtractJsonValue(Params, 'color');
    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'net is required');
        Exit;
    End;
    Wanted := StrToIntDef(ColorStr, -1);
    { 536870912 (bit 29) is Altium's "no colour set" (measured on AD 26.11). }
    If (ColorStr <> '') And ((Wanted < 0) Or ((Wanted > 16777215) And (Wanted <> 536870912))) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_VALUE', 'color is 0 to 16777215, or 536870912 for none');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    Before := Project.DM_GetNetColorByName(NetName);
    If Wanted >= 0 Then
    Begin
        Project.DM_SetNetColorByName(NetName, Wanted);
        MarkDocDirtyByPath(Project.DM_ProjectFullPath);
    End;
    After := Project.DM_GetNetColorByName(NetName);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('net', NetName) + ','
        + JsonInt('color_before', Before) + ','
        + JsonInt('color', After)));
End;

{ Member names of a compiled object class: nets by name, parts by designator. }
{ DM_Members answers the member's NAME (Altium's SDK declares a string), not }
{ an object: read as one, every member came back empty.                    }
Function ClassMembersJson(Cls : IObjectClass) : String;
Var
    I : Integer;
    Name : String;
Begin
    Result := '';
    For I := 0 To Cls.DM_MemberCount - 1 Do
    Begin
        Name := '';
        Try Name := Cls.DM_Members(I); Except Name := ''; End;
        If Name = '' Then Continue;
        If Result <> '' Then Result := Result + ',';
        Result := Result + '"' + EscapeJsonString(Name) + '"';
    End;
    Result := '[' + Result + ']';
End;

{..............................................................................}
{ Proj_GetDeclaredDesignObjects - the net classes, component classes,         }
{ differential pairs and rooms the compiled schematics declare (directives,   }
{ class parameters, sheet-based rooms): IDocument.DM_NetClasses,               }
{ DM_ComponentClasses, DM_DifferentialPairs and DM_Rooms.       }
{ What an ECO would create on the board, read before it does.                  }
{ Params: project_path.                                                        }
{..............................................................................}
Function Proj_GetDeclaredDesignObjects(Params : String; RequestId : String) : String;
Var
    ProjectPath, NetCls, CompCls, Pairs, Rooms, PosNet, NegNet, DocPath, Leg, Ending : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Cls : IObjectClass;
    Pair : IDifferentialPair;
    Room : IRoom;
    D, I, J, Sheets : Integer;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    SmartCompile(Project);
    NetCls := ''; CompCls := ''; Pairs := ''; Rooms := '';
    Sheets := 0;
    For D := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(D); Except End;
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;
        Inc(Sheets);
        DocPath := ExtractFileName(Doc.DM_FullPath);
        For I := 0 To Doc.DM_NetClassCount - 1 Do
        Begin
            Cls := Doc.DM_NetClasses(I);
            If Cls = Nil Then Continue;
            If NetCls <> '' Then NetCls := NetCls + ',';
            NetCls := NetCls + JsonObj(JsonStr('name', Cls.DM_Name) + ','
                + JsonStr('sheet', DocPath) + ',' + JsonRaw('members', ClassMembersJson(Cls)));
        End;
        For I := 0 To Doc.DM_ComponentClassCount - 1 Do
        Begin
            Cls := Doc.DM_ComponentClasses(I);
            If Cls = Nil Then Continue;
            If CompCls <> '' Then CompCls := CompCls + ',';
            CompCls := CompCls + JsonObj(JsonStr('name', Cls.DM_Name) + ','
                + JsonStr('sheet', DocPath) + ',' + JsonRaw('members', ClassMembersJson(Cls)));
        End;
        For I := 0 To Doc.DM_DifferentialPairCount - 1 Do
        Begin
            Pair := Doc.DM_DifferentialPairs(I);
            If Pair = Nil Then Continue;
            PosNet := ''; NegNet := '';
            { Reading DM_PositiveNet.DM_NetName raised an undeclared identifier }
            { on AD 26.11. The pair is an object class listing its two nets by }
            { name, and Altium pairs schematic nets by their _P and _N ends.   }
            For J := 0 To Pair.DM_MemberCount - 1 Do
            Begin
                Leg := '';
                Try Leg := Pair.DM_Members(J); Except Leg := ''; End;
                Ending := UpperCase(Copy(Leg, Length(Leg) - 1, 2));
                If Ending = '_P' Then PosNet := Leg;
                If Ending = '_N' Then NegNet := Leg;
            End;
            If Pairs <> '' Then Pairs := Pairs + ',';
            Pairs := Pairs + JsonObj(JsonStr('name', Pair.DM_Name) + ','
                + JsonStr('sheet', DocPath) + ','
                + JsonStr('positive', PosNet) + ',' + JsonStr('negative', NegNet));
        End;
        For I := 0 To Doc.DM_RoomCount - 1 Do
        Begin
            Room := Doc.DM_Rooms(I);
            If Room = Nil Then Continue;
            If Rooms <> '' Then Rooms := Rooms + ',';
            Rooms := Rooms + JsonObj(JsonStr('name', Room.DM_RoomName) + ','
                + JsonStr('sheet', DocPath) + ','
                + JsonStr('scope', Room.DM_Scope1Expression));
        End;
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonInt('sheets_examined', Sheets) + ','
        + JsonRaw('net_classes', '[' + NetCls + ']') + ','
        + JsonRaw('component_classes', '[' + CompCls + ']') + ','
        + JsonRaw('differential_pairs', '[' + Pairs + ']') + ','
        + JsonRaw('rooms', '[' + Rooms + ']')));
End;

{ What kind of net it is, as JSON members (no braces): whether its name is  }
{ local (False when a power port gives it a global name; measured on AD      }
{ 26.11, a net joined across sheets by ports still reads local), its        }
{ electrical type as Altium words it, whether ERC is suppressed on it, and, }
{ for a bus member, the bus prefix and its index.                           }
Function NetKindJson(Net : INet) : String;
Var
    IsLocal, NoErc, InBus : Boolean;
    Electric, Prefix : String;
    BusIdx : Integer;
Begin
    IsLocal := False; NoErc := False; InBus := False;
    Electric := ''; Prefix := ''; BusIdx := -1;
    Try IsLocal := Net.DM_IsLocal; Except End;
    Try Electric := Net.DM_ElectricalString; Except End;
    Try NoErc := Net.DM_SuppressERC; Except End;
    Try InBus := Net.DM_IsBusMember; Except End;
    If InBus Then
    Begin
        Try Prefix := Net.DM_BusPrefix; Except End;
        Try BusIdx := Net.DM_BusIndex; Except End;
    End;
    Result := JsonBool('local_scope', IsLocal) + ','
        + JsonStr('electrical', Electric) + ','
        + JsonBool('erc_suppressed', NoErc) + ','
        + JsonBool('bus_member', InBus);
    If InBus Then
        Result := Result + ',' + JsonStr('bus_prefix', Prefix) + ',' + JsonInt('bus_index', BusIdx);
End;

{ One naming object on a compiled net (label, power port, port, sheet       }
{ entry, off-sheet connector) as JSON: its text, sheet and location in      }
{ mils. A power port answers no text of its own, so the net name stands in. }
Function NetNamingItemJson(Item : INetItem; Text : String) : String;
Var
    Sheet : String;
    X, Y : Integer;
Begin
    Sheet := ''; X := 0; Y := 0;
    If Text = '' Then
    Begin
        Try Text := Item.DM_NetName; Except End;
    End;
    Try Sheet := Item.DM_OwnerDocumentName; Except End;
    Try X := Item.DM_LocationX; Except End;
    Try Y := Item.DM_LocationY; Except End;
    Result := JsonObj(JsonStr('text', Text) + ',' + JsonStr('sheet', Sheet) + ','
        + JsonFloat('x', X / 10000) + ',' + JsonFloat('y', Y / 10000));
End;

{ What names one compiled net, and where: its labels, power ports, ports,  }
{ sheet entries and off-sheet connectors, with the net's own flags.        }
{ How one compiled net is built, as a JSON member: its wires and lines,    }
{ items the compiler removed, scope and signal type, the name the compiler }
{ worked out, bus width, and how many pins of each driving kind it joins   }
{ (two outputs on one net is a fight).                                     }
Function NetStructureJson(Net : INet) : String;
Var
    SubWires, Lines, AllItems, Removed, Scope, BusWidth, Outputs, BiDirs, Powers, OpenColl, Misses : Integer;
    SignalType, Calculated, Hidden : String;
Begin
    SubWires := -1; Lines := -1; AllItems := -1; Removed := -1; Scope := -1; BusWidth := 0;
    Outputs := 0; BiDirs := 0; Powers := 0; OpenColl := 0; Misses := 0;
    SignalType := ''; Calculated := ''; Hidden := '';
    Try SubWires := Net.DM_SubWireCount; Except Inc(Misses); End;
    Try Lines := Net.DM_LineCount; Except Inc(Misses); End;
    Try AllItems := Net.DM_AllNetItemCount; Except Inc(Misses); End;
    Try Removed := Net.DM_RemovedNetItemCount; Except Inc(Misses); End;
    Try Scope := Net.DM_Scope; Except Inc(Misses); End;
    Try SignalType := Net.DM_SignalType; Except Inc(Misses); End;
    Try Calculated := Net.DM_CalculatedNetName; Except Inc(Misses); End;
    Try Hidden := Net.DM_HiddenNetName; Except Inc(Misses); End;
    Try BusWidth := Net.DM_BusWidth; Except Inc(Misses); End;
    Try Outputs := Net.DM_CountOfElectricalType(eElectricOutput); Except Inc(Misses); End;
    Try BiDirs := Net.DM_CountOfElectricalType(eElectricIO); Except Inc(Misses); End;
    Try Powers := Net.DM_CountOfElectricalType(eElectricPower); Except Inc(Misses); End;
    Try OpenColl := Net.DM_CountOfElectricalType(eElectricOpenCollector); Except Inc(Misses); End;
    Result := JsonRaw('structure', JsonObj(
        JsonInt('sub_wires', SubWires) + ','
        + JsonInt('lines', Lines) + ','
        + JsonInt('all_items', AllItems) + ','
        + JsonInt('removed_items', Removed) + ','
        + JsonInt('scope', Scope) + ','
        + JsonStr('signal_type', SignalType) + ','
        + JsonStr('calculated_name', Calculated) + ','
        + JsonStr('hidden_name', Hidden) + ','
        + JsonInt('bus_width', BusWidth) + ','
        + JsonInt('output_pins', Outputs) + ','
        + JsonInt('io_pins', BiDirs) + ','
        + JsonInt('power_pins', Powers) + ','
        + JsonInt('open_collector_pins', OpenColl) + ','
        + JsonInt('read_errors', Misses)));
End;

Function NetContentsJson(Net : INet; WithPins : Boolean; WithStructure : Boolean) : String;
Var
    Item : INetItem;
    Labels, Powers, Ports, Entries, Cross, Directives, Pins, Text : String;
    I, PinCount, Left : Integer;
    AutoGen : Boolean;
Begin
    Labels := ''; Powers := ''; Ports := ''; Entries := ''; Cross := ''; Pins := '';
    Directives := '';
    For I := 0 To Net.DM_NetLabelCount - 1 Do
    Begin
        Item := Nil; Text := '';
        Try Item := Net.DM_NetLabels(I); Except Item := Nil; End;
        If Item = Nil Then Continue;
        Try Text := Item.DM_NetLabelText; Except End;
        If Labels <> '' Then Labels := Labels + ',';
        Labels := Labels + NetNamingItemJson(Item, Text);
    End;
    For I := 0 To Net.DM_PowerObjectCount - 1 Do
    Begin
        Item := Nil; Text := '';
        Try Item := Net.DM_PowerObjects(I); Except Item := Nil; End;
        If Item = Nil Then Continue;
        Try Text := Item.DM_PowerText; Except End;
        If Powers <> '' Then Powers := Powers + ',';
        Powers := Powers + NetNamingItemJson(Item, Text);
    End;
    For I := 0 To Net.DM_PortCount - 1 Do
    Begin
        Item := Nil; Text := '';
        Try Item := Net.DM_Ports(I); Except Item := Nil; End;
        If Item = Nil Then Continue;
        Try Text := Item.DM_PortName; Except End;
        If Ports <> '' Then Ports := Ports + ',';
        Ports := Ports + NetNamingItemJson(Item, Text);
    End;
    For I := 0 To Net.DM_SheetEntryCount - 1 Do
    Begin
        Item := Nil; Text := '';
        Try Item := Net.DM_SheetEntrys(I); Except Item := Nil; End;
        If Item = Nil Then Continue;
        Try Text := Item.DM_NetName; Except End;
        If Entries <> '' Then Entries := Entries + ',';
        Entries := Entries + NetNamingItemJson(Item, Text);
    End;
    For I := 0 To Net.DM_CrossSheetConnectorCount - 1 Do
    Begin
        Item := Nil; Text := '';
        Try Item := Net.DM_CrossSheetConnectors(I); Except Item := Nil; End;
        If Item = Nil Then Continue;
        Try Text := Item.DM_CrossSheetText; Except End;
        If Cross <> '' Then Cross := Cross + ',';
        Cross := Cross + NetNamingItemJson(Item, Text);
    End;
    { Directives as the compiled net holds them: which net a directive    }
    { belongs to is the compiler's answer, not a guess from where it is  }
    { drawn among the wires.                                              }
    Left := 0;
    Try Left := Net.DM_DirectiveCount; Except Left := 0; End;
    While Left > 0 Do
    Begin
        Dec(Left);
        Item := Nil;
        Try Item := Net.DM_Directives(Left); Except Item := Nil; End;
        If Item <> Nil Then
            Directives := NetNamingItemJson(Item, '') + ',' + Directives;
    End;
    If Directives <> '' Then Directives := Copy(Directives, 1, Length(Directives) - 1);
    PinCount := 0;
    Try PinCount := Net.DM_PinCount; Except End;
    If WithPins Then
        For I := 0 To PinCount - 1 Do
        Begin
            Item := Nil; Text := '';
            Try Item := Net.DM_Pins(I); Except Item := Nil; End;
            If Item = Nil Then Continue;
            Try Text := Item.DM_FullPinName; Except End;
            If Pins <> '' Then Pins := Pins + ',';
            Pins := Pins + '"' + EscapeJsonString(Text) + '"';
        End;
    AutoGen := False;
    Try AutoGen := Net.DM_IsAutoGenerated; Except End;
    Text := '';
    Try Text := Net.DM_FullNetName; Except End;
    Result := JsonObj(JsonStr('net', Net.DM_NetName) + ','
        + JsonStr('full_name', Text) + ','
        + JsonBool('auto_generated', AutoGen) + ','
        + JsonInt('pin_count', PinCount) + ','
        + NetKindJson(Net) + ','
        + JsonRaw('net_labels', '[' + Labels + ']') + ','
        + JsonRaw('power_objects', '[' + Powers + ']') + ','
        + JsonRaw('ports', '[' + Ports + ']') + ','
        + JsonRaw('sheet_entries', '[' + Entries + ']') + ','
        + JsonRaw('off_sheet_connectors', '[' + Cross + ']') + ','
        + JsonRaw('directives', '[' + Directives + ']'));
    If WithPins Then
        Result := Copy(Result, 1, Length(Result) - 1) + ',' + JsonRaw('pins', '[' + Pins + ']') + '}';
    If WithStructure Then
        Result := Copy(Result, 1, Length(Result) - 1) + ',' + NetStructureJson(Net) + '}';
End;

{..............................................................................}
{ Proj_GetNetItems - what gives each compiled net its name and where: net    }
{ labels, power ports, ports, sheet entries and off-sheet connectors, and    }
{ whether the name was made up by the compiler.                             }
{ Params: project_path (optional), net (optional, one net, any case),        }
{ named_only ('true' = skip nets nothing names), limit (default 500).        }
{..............................................................................}
Function Proj_GetNetItems(Params : String; RequestId : String) : String;
Var
    ProjectPath, NetName, Items, Name : String;
    Workspace : IWorkspace;
    Project : IProject;
    FlatDoc : IDocument;
    Net : INet;
    I, Limit, Listed, Examined, Naming : Integer;
    NamedOnly, Truncated, Structure : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    NetName := ExtractJsonValue(Params, 'net');
    NamedOnly := LowerCase(ExtractJsonValue(Params, 'named_only')) = 'true';
    Structure := LowerCase(ExtractJsonValue(Params, 'structure')) = 'true';
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 500);
    If Limit < 1 Then Limit := 500;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    SmartCompile(Project);
    FlatDoc := Nil;
    Try FlatDoc := Project.DM_DocumentFlattened; Except End;
    If FlatDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_FLAT_NETLIST',
            'Project has no flattened netlist (compile the project first).');
        Exit;
    End;
    Items := '';
    Listed := 0;
    Examined := 0;
    Truncated := False;
    For I := 0 To FlatDoc.DM_NetCount - 1 Do
    Begin
        Net := Nil;
        Try Net := FlatDoc.DM_Nets(I); Except Net := Nil; End;
        If Net = Nil Then Continue;
        Inc(Examined);
        Name := '';
        Try Name := Net.DM_NetName; Except End;
        If (NetName <> '') And (UpperCase(Name) <> UpperCase(NetName)) Then Continue;
        If NamedOnly Then
        Begin
            Naming := 0;
            Try
                Naming := Net.DM_NetLabelCount + Net.DM_PowerObjectCount + Net.DM_PortCount
                    + Net.DM_SheetEntryCount + Net.DM_CrossSheetConnectorCount;
            Except End;
            If Naming = 0 Then Continue;
        End;
        If Listed >= Limit Then
        Begin
            Truncated := True;
            Break;
        End;
        If Items <> '' Then Items := Items + ',';
        Items := Items + NetContentsJson(Net, NetName <> '', Structure);
        Inc(Listed);
    End;
    If (NetName <> '') And (Listed = 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_NET', 'No compiled net named ' + NetName);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonInt('nets_examined', Examined) + ','
        + JsonInt('nets_listed', Listed) + ','
        + JsonBool('truncated', Truncated) + ','
        + JsonRaw('nets', '[' + Items + ']')));
End;

{ S without trailing path separators. }
Function NoTrailingSlash(S : String) : String;
Begin
    Result := S;
    While (Length(Result) > 3) And ((Copy(Result, Length(Result), 1) = '\')
        Or (Copy(Result, Length(Result), 1) = '/')) Do
        Result := Copy(Result, 1, Length(Result) - 1);
End;

{ The project's documents in project order, its search paths and its        }
{ generated documents, as JSON members (no braces).                         }
Function ProjectSetupJson(Project : IProject) : String;
Var
    Docs, Paths, Gens, Path : String;
    Doc : IDocument;
    SP : ISearchPath;
    I, Idx, Total, Mode : Integer;
    Order : TStringList;
    OutPath : String;
    Sub, AllowPort, AllowEntry, AppendSheet, SwapLabel, SwapPin : Boolean;
Begin
    Order := TStringList.Create;
    Total := Project.DM_LogicalDocumentCount + Project.DM_PhysicalDocumentCount;
    For I := 0 To Total - 1 Do Order.Add('?');
    For I := 0 To Project.DM_LogicalDocumentCount + Project.DM_PhysicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        If I < Project.DM_LogicalDocumentCount Then
        Begin
            Try Doc := Project.DM_LogicalDocuments(I); Except Doc := Nil; End;
        End
        Else
        Begin
            Try Doc := Project.DM_PhysicalDocuments(I - Project.DM_LogicalDocumentCount); Except Doc := Nil; End;
        End;
        If Doc = Nil Then Continue;
        Path := Doc.DM_FullPath;
        Idx := -1;
        Try Idx := Project.DM_IndexOfSourceDocument(Path); Except Idx := -1; End;
        If (Idx >= 0) And (Idx < Total) Then
            Order.Strings[Idx] := Path;
    End;
    Docs := '';
    For I := 0 To Order.Count - 1 Do
    Begin
        If Order.Strings[I] = '?' Then Continue;
        If Docs <> '' Then Docs := Docs + ',';
        Docs := Docs + JsonObj(JsonInt('index', I) + ',' + JsonStr('path', Order.Strings[I]));
    End;
    Order.Free;
    Paths := '';
    For I := 0 To Project.DM_SearchPathCount - 1 Do
    Begin
        SP := Nil;
        Try SP := Project.DM_SearchPaths(I); Except SP := Nil; End;
        If SP = Nil Then Continue;
        Path := ''; Sub := False;
        Try Path := SP.DM_AbsolutePath; Except End;
        If Path = '' Then
        Begin
            Try Path := SP.DM_Path; Except End;
        End;
        Try Sub := SP.DM_IncludeSubFolders; Except End;
        If Paths <> '' Then Paths := Paths + ',';
        Paths := Paths + JsonObj(JsonStr('path', Path) + ',' + JsonBool('include_subfolders', Sub));
    End;
    Gens := '';
    For I := 0 To Project.DM_GeneratedDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_GeneratedDocuments(I); Except Doc := Nil; End;
        If Doc = Nil Then Continue;
        If Gens <> '' Then Gens := Gens + ',';
        Gens := Gens + '"' + EscapeJsonString(Doc.DM_FullPath) + '"';
    End;
    AllowPort := False; AllowEntry := False;
    Try AllowPort := Project.DM_GetAllowPortNetNames; Except End;
    Try AllowEntry := Project.DM_GetAllowSheetEntryNetNames; Except End;
    Mode := -1;
    Try Mode := Project.DM_HierarchyMode; Except End;
    OutPath := '';
    Try OutPath := Project.DM_GetOutputPath; Except End;
    AppendSheet := False; SwapLabel := False; SwapPin := False;
    Try AppendSheet := Project.DM_GetAppendSheetNumberToLocalNets; Except End;
    Try SwapLabel := Project.DM_GetPinSwapBy_Netlabel; Except End;
    Try SwapPin := Project.DM_GetPinSwapBy_Pin; Except End;
    Result := JsonRaw('documents', '[' + Docs + ']') + ','
        + JsonRaw('search_paths', '[' + Paths + ']') + ','
        + JsonRaw('generated_documents', '[' + Gens + ']') + ','
        + JsonBool('allow_port_net_names', AllowPort) + ','
        + JsonBool('allow_sheet_entry_net_names', AllowEntry) + ','
        + JsonInt('hierarchy_mode', Mode) + ','
        + JsonStr('output_path', OutPath) + ','
        + JsonBool('append_sheet_number_to_local_nets', AppendSheet) + ','
        + JsonBool('pin_swap_by_net_label', SwapLabel) + ','
        + JsonBool('pin_swap_by_pin', SwapPin);
End;

{..............................................................................}
{ Proj_ProjectSetup - read, and optionally change, a project's document      }
{ order, library search paths, the "ports / sheet entries name nets"        }
{ options, the net identifier scope (hierarchy_mode, Altium's number) and    }
{ the output path. Every change is read back; save the project to keep it.   }
{ Params: project_path, move_document + move_to_index, add_search_path +     }
{ include_subfolders, allow_port_net_names, allow_sheet_entry_net_names      }
{ ('true' / 'false'; empty = keep), append_sheet_number_to_local_nets,       }
{ pin_swap_by_net_label, pin_swap_by_pin (the same). No change params =     }
{ read only.                                                                }
{..............................................................................}
Function Proj_ProjectSetup(Params : String; RequestId : String) : String;
Var
    ProjectPath, MoveDoc, MoveTo, AddPath, SubStr, PortStr, EntryStr, NotApplied : String;
    ModeStr, OutStr, AppendStr, SwapLabelStr, SwapPinStr : String;
    Workspace : IWorkspace;
    Project : IProject;
    SP : ISearchPath;
    I, NewIdx, Was, NowIdx, PathsBefore : Integer;
    Changed, Found : Boolean;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    MoveDoc := ExtractJsonValue(Params, 'move_document');
    MoveTo := ExtractJsonValue(Params, 'move_to_index');
    AddPath := ExtractJsonValue(Params, 'add_search_path');
    SubStr := LowerCase(ExtractJsonValue(Params, 'include_subfolders'));
    PortStr := LowerCase(ExtractJsonValue(Params, 'allow_port_net_names'));
    EntryStr := LowerCase(ExtractJsonValue(Params, 'allow_sheet_entry_net_names'));
    ModeStr := ExtractJsonValue(Params, 'hierarchy_mode');
    OutStr := ExtractJsonValue(Params, 'output_path');
    AppendStr := LowerCase(ExtractJsonValue(Params, 'append_sheet_number_to_local_nets'));
    SwapLabelStr := LowerCase(ExtractJsonValue(Params, 'pin_swap_by_net_label'));
    SwapPinStr := LowerCase(ExtractJsonValue(Params, 'pin_swap_by_pin'));
    If (ModeStr <> '') And (Not IsIntStr(ModeStr)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAM', 'hierarchy_mode must be a number');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    Changed := False;
    NotApplied := '';
    Was := -1; NowIdx := -1;
    If MoveDoc <> '' Then
    Begin
        If Not IsIntStr(MoveTo) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_PARAM', 'move_to_index must be a number');
            Exit;
        End;
        NewIdx := StrToInt(MoveTo);
        Was := Project.DM_IndexOfSourceDocument(MoveDoc);
        If Was < 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'Not in the project: ' + MoveDoc);
            Exit;
        End;
        Project.DM_MoveSourceDocument(MoveDoc, NewIdx);
        NowIdx := Project.DM_IndexOfSourceDocument(MoveDoc);
        Changed := True;
        If NowIdx <> NewIdx Then NotApplied := NotApplied + '"move_to_index",';
    End;
    If AddPath <> '' Then
    Begin
        PathsBefore := Project.DM_SearchPathCount;
        Project.DM_AddSearchPath(AddPath, SubStr = 'true');
        Changed := True;
        Found := False;
        For I := 0 To Project.DM_SearchPathCount - 1 Do
        Begin
            SP := Nil;
            Try SP := Project.DM_SearchPaths(I); Except SP := Nil; End;
            If SP = Nil Then Continue;
            Try
                If (UpperCase(SP.DM_Path) = UpperCase(AddPath))
                   Or (UpperCase(SP.DM_AbsolutePath) = UpperCase(AddPath)) Then Found := True;
            Except End;
        End;
        If Not Found Then NotApplied := NotApplied + '"add_search_path",';
    End;
    If PortStr <> '' Then
    Begin
        Project.DM_SetAllowPortNetNames(PortStr = 'true');
        Changed := True;
        If Project.DM_GetAllowPortNetNames <> (PortStr = 'true') Then
            NotApplied := NotApplied + '"allow_port_net_names",';
    End;
    If EntryStr <> '' Then
    Begin
        Project.DM_SetAllowSheetEntryNetNames(EntryStr = 'true');
        Changed := True;
        If Project.DM_GetAllowSheetEntryNetNames <> (EntryStr = 'true') Then
            NotApplied := NotApplied + '"allow_sheet_entry_net_names",';
    End;
    If ModeStr <> '' Then
    Begin
        Project.DM_SetHierarchyMode(StrToInt(ModeStr));
        Changed := True;
        If Project.DM_HierarchyMode <> StrToInt(ModeStr) Then
            NotApplied := NotApplied + '"hierarchy_mode",';
    End;
    If OutStr <> '' Then
    Begin
        Project.DM_SetOutputPath(OutStr);
        Changed := True;
        If UpperCase(NoTrailingSlash(Project.DM_GetOutputPath))
           <> UpperCase(NoTrailingSlash(OutStr)) Then
            NotApplied := NotApplied + '"output_path",';
    End;
    If AppendStr <> '' Then
    Begin
        Project.DM_SetAppendSheetNumberToLocalNets(AppendStr = 'true');
        Changed := True;
        If Project.DM_GetAppendSheetNumberToLocalNets <> (AppendStr = 'true') Then
            NotApplied := NotApplied + '"append_sheet_number_to_local_nets",';
    End;
    If SwapLabelStr <> '' Then
    Begin
        Project.DM_SetPinSwapBy_Netlabel(SwapLabelStr = 'true');
        Changed := True;
        If Project.DM_GetPinSwapBy_Netlabel <> (SwapLabelStr = 'true') Then
            NotApplied := NotApplied + '"pin_swap_by_net_label",';
    End;
    If SwapPinStr <> '' Then
    Begin
        Project.DM_SetPinSwapBy_Pin(SwapPinStr = 'true');
        Changed := True;
        If Project.DM_GetPinSwapBy_Pin <> (SwapPinStr = 'true') Then
            NotApplied := NotApplied + '"pin_swap_by_pin",';
    End;
    If NotApplied <> '' Then NotApplied := Copy(NotApplied, 1, Length(NotApplied) - 1);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('project', Project.DM_ProjectFullPath) + ','
        + JsonBool('changed', Changed) + ','
        + JsonInt('moved_from', Was) + ','
        + JsonInt('moved_to', NowIdx) + ','
        + JsonRaw('not_applied', '[' + NotApplied + ']') + ','
        + ProjectSetupJson(Project)));
End;

{..............................................................................}
{ Proj_MakeSchematicLibrary - write a schematic library of every symbol      }
{ placed in the project (ISch_Document.CreateLibraryFromProject, quiet), and }
{ optionally add it to the project. Run from the first loaded sheet.         }
{ Params: project_path (optional), out_path (required), add_to_project.      }
{..............................................................................}
Function Proj_MakeSchematicLibrary(Params : String; RequestId : String) : String;
Var
    ProjectPath, OutPath, SheetPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    AddIt : Boolean;
    D : Integer;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    OutPath := ExtractJsonValue(Params, 'out_path');
    AddIt := LowerCase(ExtractJsonValue(Params, 'add_to_project')) = 'true';
    If OutPath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'out_path is required');
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    SchDoc := Nil;
    SheetPath := '';
    For D := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(D); Except Doc := Nil; End;
        If Doc = Nil Then Continue;
        If UpperCase(Doc.DM_DocumentKind) <> 'SCH' Then Continue;
        Try SchDoc := SchServer.GetSchDocumentByPath(Doc.DM_FullPath); Except SchDoc := Nil; End;
        If SchDoc <> Nil Then
        Begin
            SheetPath := Doc.DM_FullPath;
            Break;
        End;
    End;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_LOADED',
            'No sheet of the project is loaded. Call proj_load_sheets first.');
        Exit;
    End;
    SchDoc.CreateLibraryFromProject(AddIt, OutPath, True);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('project', Project.DM_ProjectFullPath) + ','
        + JsonStr('sheet', SheetPath) + ','
        + JsonStr('out_path', OutPath) + ','
        + JsonBool('add_to_project', AddIt) + ','
        + JsonBool('in_project', Project.DM_IndexOfSourceDocument(OutPath) >= 0)));
End;

{..............................................................................}
{ Proj_MakeIntegratedLibrary - Design > Make Integrated Library: an IntLib of  }
{ every part the project places, symbols and footprints together             }
{ (IntegratedLibrary:CreateLibraryFromProject). MEASURED on AD 26.11.1.5: no   }
{ dialog; Altium writes <project>.IntLib in the project folder. The process   }
{ acts on the focused project, so one of its open documents is focused first. }
{ Params: project_path (optional).                                            }
{..............................................................................}
Function Proj_MakeIntegratedLibrary(Params : String; RequestId : String) : String;
Var
    ProjectPath, DocPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    D : Integer;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');
    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;
    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;
    ServerDoc := Nil;
    DocPath := '';
    For D := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(D); Except Doc := Nil; End;
        If Doc = Nil Then Continue;
        Try ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath); Except ServerDoc := Nil; End;
        If ServerDoc <> Nil Then
        Begin
            DocPath := Doc.DM_FullPath;
            Break;
        End;
    End;
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_LOADED',
            'No document of the project is open. Call proj_load_sheets first.');
        Exit;
    End;
    Try ServerDoc.Focus; Except End;
    If UpperCase(Workspace.DM_FocusedProject.DM_ProjectFullPath)
        <> UpperCase(Project.DM_ProjectFullPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOCUSED',
            'Could not focus the project through ' + DocPath + '; nothing was made.');
        Exit;
    End;
    RunProcess('IntegratedLibrary:CreateLibraryFromProject');
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('project', Project.DM_ProjectFullPath) + ','
        + JsonStr('focused_document', DocPath)));
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

    { Save the project to persist the change. WorkspaceManager:SaveObject }
    { acts on the focused object, which need not be this project.         }
    SaveProjectFile(Project);

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
    SchBeginEdit(SchDoc, '');
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
        SchEndEdit(SchDoc, 'Edit');
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
    FocusedProject : IProject;
    FocusedKind, FocusedPath : String;
Begin
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace'); Exit; End;

    If ProjectPath <> '' Then Project := FindProjectByPath(Workspace, ProjectPath)
    Else Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Begin Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found'); Exit; End;

    { FOCUS DECIDES WHAT THE COMPARE DOES. MEASURED 2026-08-17: with a
      child sheet focused, WorkspaceManager:Compare raised a modal reading
      "Cannot compare a source document against its owner project SCH"
      and changed nothing, while this handler went on to report success.
      So the project's PCB is focused before the compare below and the
      focus is checked, rather than refusing a schematic and leaving the
      caller to pick a document.

      NOT applied to Proj_UpdateSchematic, which is the opposite
      direction and whose correct focus has not been measured. Guessing
      symmetry here would be inventing a precondition. }
    SmartCompile(Project);
    Ok := ComputeECODifferences(Project, MatchedBefore, ExtraSchBefore, ExtraPcbBefore, PcbPath);
    If Not Ok Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No primary PCB document in this project or mappings unavailable');
        Exit;
    End;

    { Fire the real ECO via the WorkspaceManager comparator. This is the
      ONLY evidenced scriptable launcher (ref: reference MultiPCBProject.pas,
      Petar Perisin). The previous 'PCB:UpdatePCBFromProject' was NOT a real
      process id (RunProcess silently ignores unknown ids -> the handler
      no-opped), and DisableDialog/Silent/NoConfirm/AutoApply are invented
      flags that appear in no Altium docs.
      DIRECTION. With the PCB focused, the reference spells the two actions
      out: Action=UpdateOther is PCB -> Update Schematic, and Action=UpdateMe
      is PCB -> Import Changes From the schematic. This handler sent
      UpdateOther while requiring the PCB in focus, so it back-annotated:
      an ECO opened with the SchDoc as the affected document, and executing
      it reverted a footprint and a comment on the schematic. The PCB is
      focused here, checked, and asked to import.
      DIALOG IS UNAVOIDABLE: Altium's Engineering Change Order dialog is
      non-suppressible BY DESIGN (altium.com .../keeping-synchronized). This
      process raises that modal and BLOCKS the polling loop until a human
      clicks "Execute Changes" (or closes it). There is no documented silent
      variant. }
    ResolvePCBBoard(PcbPath);
    FocusedKind := '';
    FocusedPath := '';
    Try
        FocusedDoc := Workspace.DM_FocusedDocument;
        If FocusedDoc <> Nil Then
        Begin
            FocusedKind := FocusedDoc.DM_DocumentKind;
            FocusedPath := FocusedDoc.DM_FullPath;
        End;
    Except
        FocusedKind := '';
    End;
    { The compare runs on the FOCUSED project, so the board in focus has  }
    { to be this one, in this project; another would be the one updated.  }
    FocusedProject := Nil;
    Try FocusedProject := Workspace.DM_FocusedProject; Except FocusedProject := Nil; End;
    If FocusedKind = 'PCB' Then
    Begin
        If PathKey(FocusedPath) <> PathKey(PcbPath) Then
            FocusedKind := 'PCB ' + FocusedPath
        Else If FocusedProject = Nil Then
            FocusedKind := 'PCB of another project'
        Else If PathKey(FocusedProject.DM_ProjectFullPath)
            <> PathKey(Project.DM_ProjectFullPath) Then
            FocusedKind := 'PCB of another project';
    End;
    If FocusedKind <> 'PCB' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_FOCUS',
            'The project''s PCB could not be focused (the focused document is '
            + FocusedKind + '), and the update direction depends on it: with '
            + 'anything else focused the same request can update the '
            + 'schematic from the board. Focus ' + PcbPath
            + ' with app_set_active_document, then call this again.');
        Exit;
    End;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('Action', 'UpdateMe');
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
    SchBeginEdit(SchDoc, '');

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

    SchEndEdit(SchDoc, 'Edit');
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

    { Hierarchy mode, the net identifier scope: TFlattenMode is Smart,     }
    { Flat, Hierarchical, Global, Strict hierarchical, in that order.       }
    { (If/Else chain, Case on enum crashes DelphiScript.)                   }
    Try
        HierMode := IntToStr(Project.DM_HierarchyMode);
        If Project.DM_HierarchyMode = 0 Then HierMode := 'Automatic';
        If Project.DM_HierarchyMode = 1 Then HierMode := 'Flat';
        If Project.DM_HierarchyMode = 2 Then HierMode := 'Hierarchical';
        If Project.DM_HierarchyMode = 3 Then HierMode := 'Global';
        If Project.DM_HierarchyMode = 4 Then HierMode := 'StrictHierarchical';
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

{..............................................................................}
{ ERC REPORT LEVELS - read and set the project's Error Reporting options:     }
{ for each violation type, No Report, Warning, Error or Fatal Error.           }
{                                                                              }
{ IProject.DM_ErrorLevels(TErrorKind) : TErrorLevel and DM_SetErrorLevels,    }
{ TErrorLevel = (eErrorLevelNoReport, eErrorLevelWarning, eErrorLevelError,   }
{ eErrorLevelFatal), 0 to 3 (workspace manager API reference). The types are  }
{ named by IWorkspace.DM_ViolationTypeDescription(kind), so the list is read }
{ from Altium rather than kept here. Asked for because a multichannel design }
{ reports "Nets with multiple names" by construction, and there was no way   }
{ to set the level without the Project Options dialog.                       }
{..............................................................................}

Function ErcLevelName(Level : Integer) : String;
Begin
    If Level = 0 Then Result := 'no_report'
    Else If Level = 1 Then Result := 'warning'
    Else If Level = 2 Then Result := 'error'
    Else If Level = 3 Then Result := 'fatal'
    Else Result := IntToStr(Level);
End;

{ -1 when the text names no level. }
Function ErcLevelFromText(Text : String) : Integer;
Var
    T : String;
Begin
    T := LowerCase(Trim(Text));
    T := StringReplace(T, ' ', '_', -1);
    If (T = 'no_report') Or (T = 'noreport') Or (T = 'none') Or (T = '0') Then Result := 0
    Else If (T = 'warning') Or (T = '1') Then Result := 1
    Else If (T = 'error') Or (T = '2') Then Result := 2
    Else If (T = 'fatal') Or (T = 'fatal_error') Or (T = '3') Then Result := 3
    Else Result := -1;
End;

Function ErcProject(Params : String; Var Problem : String) : IProject;
Var
    Workspace : IWorkspace;
    Path : String;
Begin
    Result := Nil;
    Problem := '';
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Problem := 'No workspace';
        Exit;
    End;
    Path := ExtractJsonValue(Params, 'project_path');
    If Path <> '' Then
    Begin
        Result := FindProjectByPath(Workspace, Path);
        If Result = Nil Then Problem := 'Project not open: ' + Path;
    End
    Else
    Begin
        Result := Workspace.DM_FocusedProject;
        If Result = Nil Then Problem := 'No focused project; pass project_path';
    End;
End;

{ The violation type with this exact description (case ignored), or a      }
{ number given as text. -1 when neither.                                    }
Function ErcKindFromText(Workspace : IWorkspace; Text : String) : Integer;
Var
    I : Integer;
    Desc : String;
Begin
    Result := -1;
    If (Trim(Text) <> '') And (StrToIntDef(Trim(Text), -1) >= 0) Then
    Begin
        Result := StrToIntDef(Trim(Text), -1);
        Exit;
    End;
    For I := 0 To 400 Do
    Begin
        Desc := '';
        Try Desc := Workspace.DM_ViolationTypeDescription(I); Except Desc := ''; End;
        If (Desc <> '') And (LowerCase(Desc) = LowerCase(Trim(Text))) Then
        Begin
            Result := I;
            Exit;
        End;
    End;
End;

{ Proj_GetErcLevels - every violation type with its current report level. }
{ Params: project_path (default the focused project), match (substring of  }
{ the description, optional).                                              }
Function Proj_GetErcLevels(Params : String; RequestId : String) : String;
Var
    Project : IProject;
    Workspace : IWorkspace;
    Problem, Match, Desc, Items : String;
    I, Level, Count : Integer;
Begin
    Project := ErcProject(Params, Problem);
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', Problem);
        Exit;
    End;
    Workspace := GetWorkspace;
    Match := LowerCase(ExtractJsonValue(Params, 'match'));
    Items := '';
    Count := 0;
    For I := 0 To 400 Do
    Begin
        Desc := '';
        Try Desc := Workspace.DM_ViolationTypeDescription(I); Except Desc := ''; End;
        If (Desc <> '') And ((Match = '') Or (Pos(Match, LowerCase(Desc)) > 0)) Then
        Begin
            Level := -1;
            Try Level := Project.DM_ErrorLevels(I); Except Level := -1; End;
            If Items <> '' Then Items := Items + ',';
            Items := Items + '{"kind":' + IntToStr(I)
                + ',"description":"' + EscapeJsonString(Desc) + '"'
                + ',"level":"' + ErcLevelName(Level) + '"}';
            Inc(Count);
        End;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"project":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"'
        + ',"count":' + IntToStr(Count)
        + ',"levels":[' + Items + ']}');
End;

{ Proj_SetErcLevels - set report levels. Params: project_path (default the }
{ focused project), levels = 'kind=level~~kind=level', a kind being its    }
{ number or its exact description, a level no_report, warning, error,     }
{ fatal or 0 to 3. Every one is read back. The project is marked modified; }
{ save it (proj_save) to keep the change.                                 }
Function Proj_SetErcLevels(Params : String; RequestId : String) : String;
Var
    Project : IProject;
    Workspace : IWorkspace;
    Problem, LevelsStr, Remaining, Op, KindText, LevelText, Items, Desc : String;
    Eq, Kind, Want, Got, Done, Failed : Integer;
Begin
    Project := ErcProject(Params, Problem);
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', Problem);
        Exit;
    End;
    Workspace := GetWorkspace;
    LevelsStr := ExtractJsonValue(Params, 'levels');
    If LevelsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'levels is required: kind=level~~kind=level');
        Exit;
    End;
    Items := '';
    Done := 0;
    Failed := 0;
    Remaining := LevelsStr;
    While True Do
    Begin
        Op := NextBatchOp(Remaining);
        If Op = '' Then Break;
        Eq := Pos('=', Op);
        KindText := Trim(Copy(Op, 1, Eq - 1));
        LevelText := Trim(Copy(Op, Eq + 1, Length(Op)));
        Kind := -1;
        If Eq > 0 Then Kind := ErcKindFromText(Workspace, KindText);
        Want := ErcLevelFromText(LevelText);
        Got := -1;
        Desc := '';
        If Kind >= 0 Then
        Begin
            Try Desc := Workspace.DM_ViolationTypeDescription(Kind); Except Desc := ''; End;
        End;
        If (Kind >= 0) And (Desc <> '') And (Want >= 0) Then
        Begin
            Try Project.DM_SetErrorLevels(Kind, Want); Except End;
            Try Got := Project.DM_ErrorLevels(Kind); Except Got := -1; End;
        End;
        If Items <> '' Then Items := Items + ',';
        Items := Items + '{"kind_given":"' + EscapeJsonString(KindText) + '"'
            + ',"kind":' + IntToStr(Kind)
            + ',"description":"' + EscapeJsonString(Desc) + '"'
            + ',"level":"' + ErcLevelName(Got) + '"';
        If (Kind < 0) Or (Desc = '') Then
            Items := Items + ',"problem":"no violation type by that number or exact description"'
        Else If Want < 0 Then
            Items := Items + ',"problem":"level must be no_report, warning, error, fatal or 0 to 3"'
        Else If Got <> Want Then
            Items := Items + ',"problem":"written, but it reads back ' + ErcLevelName(Got) + '"';
        Items := Items + '}';
        If (Kind >= 0) And (Desc <> '') And (Want >= 0) And (Got = Want) Then Inc(Done)
        Else Inc(Failed);
    End;
    If Done > 0 Then MarkDocDirtyByPath(Project.DM_ProjectFullPath);
    Result := BuildSuccessResponse(RequestId,
        '{"project":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"'
        + ',"set":' + IntToStr(Done)
        + ',"failed":' + IntToStr(Failed)
        + ',"results":[' + Items + ']}');
End;

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
    First, NeedsCompile : Boolean;
    AgeMs, CurrentTick : Cardinal;
    Violations : Integer;
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

    { Altium's own view: whether the project needs compiling now, and how  }
    { many violations its last compile left in the Messages panel.         }
    NeedsCompile := False;
    Try NeedsCompile := Project.DM_NeedsCompile; Except End;
    Violations := -1;
    Try Violations := Project.DM_ExistingViolationCount; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"compile_age_ms":' + IntToStr(AgeMs) + ','
        + '"needs_compile":' + BoolToJsonStr(NeedsCompile) + ','
        + '"violation_count":' + IntToStr(Violations) + ','
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
                    SchBeginEdit(SchDoc, '');
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
                        SchEndEdit(SchDoc, 'Edit');
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
        'export_step_api':   Result := Proj_ExportSTEPApi(Params, RequestId);
        'export_dxf':        Result := Proj_ExportDXF(Params, RequestId);
        'export_image':      Result := Proj_ExportImage(Params, RequestId);
        'get_outjob_containers': Result := Proj_GetOutJobContainers(Params, RequestId);
        'run_outjob':        Result := Proj_RunOutJob(Params, RequestId);
        'get_variants':      Result := Proj_GetVariants(Params, RequestId);
        'get_variant_matrix': Result := Proj_GetVariantMatrix(Params, RequestId);
        'get_active_variant': Result := Proj_GetActiveVariant(Params, RequestId);
        'set_active_variant': Result := Proj_SetActiveVariant(Params, RequestId);
        'create_variant':    Result := Proj_CreateVariant(Params, RequestId);
        'set_component_variation': Result := Proj_SetComponentVariation(Params, RequestId);
        'net_color':         Result := Proj_NetColor(Params, RequestId);
        'get_net_items':     Result := Proj_GetNetItems(Params, RequestId);
        'step_options':      Result := Proj_StepOptions(Params, RequestId);
        'get_channels':      Result := Proj_GetChannels(Params, RequestId);
        'component_models':  Result := Proj_ComponentModels(Params, RequestId);
        'library_lookup':    Result := Proj_LibraryLookup(Params, RequestId);
        'output_panel':      Result := Proj_OutputPanel(Params, RequestId);
        'add_generated_document': Result := Proj_AddGeneratedDocument(Params, RequestId);
        'project_setup':     Result := Proj_ProjectSetup(Params, RequestId);
        'make_schematic_library': Result := Proj_MakeSchematicLibrary(Params, RequestId);
        'make_integrated_library': Result := Proj_MakeIntegratedLibrary(Params, RequestId);
        'get_declared_design_objects': Result := Proj_GetDeclaredDesignObjects(Params, RequestId);
        'delete_variant':    Result := Proj_DeleteVariant(Params, RequestId);
        'set_parameter_variation': Result := Proj_SetParameterVariation(Params, RequestId);
        'rename_variant':    Result := Proj_RenameVariant(Params, RequestId);
        'copy_variant':      Result := Proj_CopyVariant(Params, RequestId);
        'get_open_projects': Result := Proj_GetOpenProjects(RequestId);
        'save_all':          Result := Proj_SaveAll(RequestId);
        'get_messages':      Result := Proj_GetMessages(Params, RequestId);
        'find_component':    Result := Proj_FindComponent(Params, RequestId);
        'get_connectivity':  Result := Proj_GetConnectivity(Params, RequestId);
        'get_connectivity_batch': Result := Proj_GetConnectivityBatch(Params, RequestId);
        'force_recompile':       Result := Proj_ForceRecompile(Params, RequestId);
        'get_erc_levels':        Result := Proj_GetErcLevels(Params, RequestId);
        'set_erc_levels':        Result := Proj_SetErcLevels(Params, RequestId);
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

{ UnitStamp_Project - which build of Project.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_Project(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
