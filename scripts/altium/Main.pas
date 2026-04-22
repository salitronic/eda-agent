{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Main.pas - Constants and helpers for the Altium integration bridge                         }
{ The script polls for request.json, processes commands, writes response.json }
{..............................................................................}

Const
    // Bump SCRIPT_VERSION whenever the .pas sources change. Python reads the
    // same string from the on-disk Main.pas and compares it to what ping
    // returns — mismatch means Altium is running a stale compiled script
    // (DelphiScript caches compiled units until the script project is
    // reopened or Altium is restarted).
    SCRIPT_VERSION = '2026.04.22.1';
    { Milliseconds during which SmartCompile reuses the previous DM_Compile    }
    { result instead of recompiling. Design-review snapshots fire 3-4 project  }
    { handlers back-to-back; each DM_Compile can be 5-10 s on a real design,   }
    { so bursts without this cache add up to 30-40 s of needless recompiles.   }
    COMPILE_CACHE_TTL_MS = 2000;

    CONFIG_FILE = 'mcp_config.json';
    REQUEST_FILE = 'request.json';
    RESPONSE_FILE = 'response.json';
    POLL_INTERVAL_ACTIVE = 10;    // ms between polls right after a command
    POLL_INTERVAL_IDLE   = 100;   // ms between polls when idle (fast enough to feel snappy, light enough on CPU)
    IDLE_THRESHOLD       = 20;    // iterations before switching to idle polling
    AUTO_SHUTDOWN_MS     = 600000; // 10 min inactivity auto-shutdown (Python sends keep-alive pings)
    YIELD_ITERATIONS     = 5;     // ProcessMessages calls per idle cycle
    YIELD_EVERY_N_ACTIVE = 5;     // call ProcessMessages only every Nth active tick

    // ISch_RobotManager SendMessage IDs (from Altium Schematic API docs).
    // Send SCHM_BeginModify / SCHM_EndModify around property writes on an
    // existing ISch_BasicContainer primitive so the Undo system and editor
    // sub-systems are notified. Send SCHM_PrimitiveRegistration after
    // RegisterSchObjectInContainer so a newly-added primitive is known to
    // Altium's editor (otherwise the title block / BOM never sees it).
    // Source and Destination pointers are passed as Nil for broadcast
    // (documented c_BroadCast = Nil, c_NoEventData = Nil).
    SCHM_PrimitiveRegistration = 1;
    SCHM_BeginModify           = 2;
    SCHM_EndModify             = 3;

Var
    WorkspaceDir : String;
    Running : Boolean;
    { Set True by handlers that write response.json themselves (to bypass a    }
    { DelphiScript return-value corruption bug for long strings). Checked and  }
    { cleared by ProcessSingleRequest each cycle.                              }
    ResponseAlreadyWritten : Boolean;
    { SmartCompile cache state. Tick of the last DM_Compile and the Project    }
    { pointer it was run against, so we only skip when the SAME project was    }
    { compiled recently. Reset to 0 / Nil at startup.                          }
    LastCompileTick : Cardinal;
    LastCompiledProject : IProject;

{..............................................................................}
{ SmartCompile                                                                  }
{                                                                               }
{ Thin wrapper over Project.DM_Compile that skips the compile when the SAME     }
{ project was compiled less than COMPILE_CACHE_TTL_MS ago. Design-review        }
{ handlers each call DM_Compile at the top; on a non-trivial design that's      }
{ 5-10 s per call. Back-to-back calls in a snapshot pay the compile cost once   }
{ instead of N times. TTL is intentionally short (2 s) so any out-of-band edit  }
{ is picked up on the next handler that runs more than 2 s after the last one. }
{..............................................................................}

Procedure SmartCompile(Project : IProject);
Begin
    If Project = Nil Then Exit;
    If (Project = LastCompiledProject) And (LastCompileTick > 0) And
       ((GetTickCount - LastCompileTick) < COMPILE_CACHE_TTL_MS) Then
        Exit;
    Project.DM_Compile;
    LastCompiledProject := Project;
    LastCompileTick := GetTickCount;
End;

{..............................................................................}
{ ISch_RobotManager.SendMessage helpers.                                        }
{                                                                               }
{ Every property write on an existing sch primitive should be bracketed by     }
{ SchBeginModify / SchEndModify. Every new primitive added via SchObjectFactory }
{ + AddSchObject / RegisterSchObjectInContainer should be followed by          }
{ SchRegisterObject. Without these broadcasts the editor sub-systems and the   }
{ undo stack are not notified, which manifests as "writes don't appear" and    }
{ "save doesn't pick up changes" in the UI.                                    }
{..............................................................................}

Procedure SchBeginModify(Obj : ISch_BasicContainer);
Begin
    If (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, Nil, SCHM_BeginModify, Nil);
End;

Procedure SchEndModify(Obj : ISch_BasicContainer);
Begin
    If (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, Nil, SCHM_EndModify, Nil);
End;

Procedure SchRegisterObject(Container, Obj : ISch_BasicContainer);
Begin
    If (Container <> Nil) And (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(
            Container.I_ObjectAddress, Nil, SCHM_PrimitiveRegistration,
            Obj.I_ObjectAddress);
End;

{..............................................................................}
{ Persist a specific document (schematic, PCB, or any server doc) to disk by   }
{ resolving its IServerDocument via Client.GetDocumentByPath and calling       }
{ SetModified(True) + DoFileSave(''). DoFileSave writes regardless of focus    }
{ and doesn't go through WorkspaceManager:SaveAll — so it works on non-active }
{ docs that SaveAll otherwise silently skips.                                  }
{..............................................................................}

{ Deferred save: mark the document dirty but don't write to disk yet.         }
{ DoFileSave on a medium PCB takes 1-5 s, so calling it after every mutation  }
{ (50+ handlers used to) made every MCP call feel slow. We now rely on Altium }
{ surfacing the dirty indicator and flush to disk happens only when the       }
{ caller explicitly invokes application.save_all (or on detach).              }
Procedure SaveDocByPath(FilePath : String);
Var
    ServerDoc : IServerDocument;
Begin
    If FilePath = '' Then Exit;
    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then Exit;
    Try ServerDoc.SetModified(True); Except End;
End;

{ Walk every open logical document in the focused workspace and call         }
{ DoFileSave on the ones whose Modified flag is set. This is the explicit    }
{ flush that replaces the per-mutation save.                                 }
Procedure SaveAllDirty;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    I, J : Integer;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Workspace.DM_Projects(I);
        If Project = Nil Then Continue;
        For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(J);
            If Doc = Nil Then Continue;
            Try
                ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
                If ServerDoc = Nil Then Continue;
                If ServerDoc.Modified Then
                    Try ServerDoc.DoFileSave(''); Except End;
            Except End;
        End;
    End;
End;

{..............................................................................}
{ Resolve workspace directory.                                                  }
{                                                                                }
{ DelphiScript has no access to environment variables, so the Python side       }
{ writes the absolute workspace path to a pointer file at a fixed location:    }
{     C:\ProgramData\eda-agent\workspace-path.txt                            }
{ This script reads that file. Python writes it at MCP-server startup and      }
{ whenever `eda-agent install-scripts` runs, so by the time this script      }
{ needs the workspace the pointer is always current.                            }
{                                                                                }
{ Fallback (pointer missing): C:\EDA Agent\workspace\                        }
{..............................................................................}

Function ResolveDefaultWorkspaceDir : String;
Var
    PointerFile : String;
    F : TextFile;
    Line : String;
Begin
    Result := '';
    PointerFile := 'C:\ProgramData\eda-agent\workspace-path.txt';
    Try
        If FileExists(PointerFile) Then
        Begin
            AssignFile(F, PointerFile);
            Reset(F);
            Try
                If Not Eof(F) Then
                    ReadLn(F, Line);
            Finally
                CloseFile(F);
            End;
            // Trim CR/LF/space
            While (Length(Line) > 0) And ((Line[Length(Line)] = #13) Or (Line[Length(Line)] = #10) Or (Line[Length(Line)] = ' ')) Do
                Line := Copy(Line, 1, Length(Line) - 1);
            While (Length(Line) > 0) And (Line[1] = ' ') Do
                Line := Copy(Line, 2, Length(Line));
            If Line <> '' Then
                Result := Line;
        End;
    Except
        Result := '';
    End;
    If Result = '' Then
        Result := 'C:\EDA Agent\workspace\';
    // Ensure trailing backslash so path joins work consistently
    If Copy(Result, Length(Result), 1) <> '\' Then
        Result := Result + '\';
End;

{..............................................................................}
{ JSON Helper Functions                                                        }
{..............................................................................}

Function ReadFileContent(FilePath : String) : String;
Var
    F : TextFile;
    Line, Content : String;
Begin
    Content := '';
    Try
        If FileExists(FilePath) Then
        Begin
            AssignFile(F, FilePath);
            Reset(F);
            While Not EOF(F) Do
            Begin
                ReadLn(F, Line);
                Content := Content + Line;
            End;
            CloseFile(F);
        End;
    Except
        Content := '';
    End;
    Result := Content;
End;

Procedure WriteFileContent(FilePath : String; Content : String);
Var
    F : TextFile;
Begin
    Try
        AssignFile(F, FilePath);
        Rewrite(F);
        Try
            Write(F, Content);
        Finally
            CloseFile(F);
        End;
    Except
        // Retry once after short delay
        Sleep(50);
        Try
            AssignFile(F, FilePath);
            Rewrite(F);
            Try
                Write(F, Content);
            Finally
                CloseFile(F);
            End;
        Except
            // Silently fail
        End;
    End;
End;

{ Append a line to workspace/activity.log for performance profiling. The     }
{ polling loop uses this to record per-command timings, and handlers can     }
{ add their own sub-stage timings for bottleneck analysis. Silently           }
{ swallows IO errors — logging must not break a command.                      }
Procedure AppendLog(Line : String);
Var
    F : TextFile;
    LogPath : String;
Begin
    Try
        LogPath := WorkspaceDir + 'activity.log';
        AssignFile(F, LogPath);
        If FileExists(LogPath) Then Append(F) Else Rewrite(F);
        Try
            WriteLn(F, Line);
        Finally
            CloseFile(F);
        End;
    Except
        // Never raise from the logger
    End;
End;

Function FormatLogStamp : String;
Begin
    Try
        Result := FormatDateTime('yyyy-mm-dd hh:nn:ss.zzz', Now);
    Except
        Result := '';
    End;
End;

Function IsWhitespaceOrColon(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = ' ') Or (C = ':') Or (C = #9) Or (C = #10) Or (C = #13);
End;

Function IsDelimiter(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = '') Or (C = ',') Or (C = '}') Or (C = ']') Or (C = ' ') Or (C = #9) Or (C = #10) Or (C = #13);
End;

Function UnescapeJsonString(S : String) : String;
Var
    I, L : Integer;
    Ch, NextCh : String;
Begin
    // Char-by-char JSON unescape. The naive StringReplace order
    // (\t -> tab, \n -> LF, ..., \\ -> \) is broken: a raw JSON sequence
    // like \\nlc (which should decode to literal \nlc) first gets its
    // inner \n interpreted as LF, producing \<LF>lc. The bug silently
    // mangled any Windows path containing \n, \t, \r, \b, \f after an
    // even number of backslashes — e.g. C:\...\nlc_480.SchDoc, \t1.log,
    // \reports\... — because Altium's fuzzy path matching obscured the
    // symptom. Must consume \\ as a single literal \ before evaluating
    // any other escape on the following character.
    Result := '';
    I := 1;
    L := Length(S);
    While I <= L Do
    Begin
        Ch := Copy(S, I, 1);
        If (Ch = '\') And (I < L) Then
        Begin
            NextCh := Copy(S, I + 1, 1);
            If NextCh = '\' Then Result := Result + '\'
            Else If NextCh = 'n' Then Result := Result + #10
            Else If NextCh = 't' Then Result := Result + #9
            Else If NextCh = 'r' Then Result := Result + #13
            Else If NextCh = '"' Then Result := Result + '"'
            Else If NextCh = '/' Then Result := Result + '/'
            Else If NextCh = 'b' Then Result := Result + #8
            Else If NextCh = 'f' Then Result := Result + #12
            Else
                // Unknown escape — keep both chars literally. Don't
                // silently eat characters on malformed input.
                Result := Result + Ch + NextCh;
            Inc(I, 2);
        End
        Else
        Begin
            Result := Result + Ch;
            Inc(I);
        End;
    End;
End;

Function ExtractJsonValue(Json : String; Key : String) : String;
Var
    StartPos, EndPos : Integer;
    SearchKey : String;
    BraceCount : Integer;
    BackslashCount, TempPos : Integer;
Begin
    Result := '';
    SearchKey := '"' + Key + '"';
    StartPos := Pos(SearchKey, Json);
    If StartPos > 0 Then
    Begin
        StartPos := StartPos + Length(SearchKey);
        // Skip whitespace and colon
        While (StartPos <= Length(Json)) And IsWhitespaceOrColon(Json, StartPos) Do
            Inc(StartPos);

        If StartPos <= Length(Json) Then
        Begin
            If Copy(Json, StartPos, 1) = '"' Then
            Begin
                // String value
                Inc(StartPos);
                EndPos := StartPos;
                While (EndPos <= Length(Json)) Do
                Begin
                    If Copy(Json, EndPos, 1) = '"' Then
                    Begin
                        // Count consecutive backslashes before this quote
                        BackslashCount := 0;
                        TempPos := EndPos - 1;
                        While (TempPos >= StartPos) And (Copy(Json, TempPos, 1) = '\') Do
                        Begin
                            Inc(BackslashCount);
                            Dec(TempPos);
                        End;
                        // Even number of backslashes means quote is real
                        If (BackslashCount Mod 2) = 0 Then Break;
                    End;
                    Inc(EndPos);
                End;
                Result := UnescapeJsonString(Copy(Json, StartPos, EndPos - StartPos));
            End
            Else If Copy(Json, StartPos, 1) = '{' Then
            Begin
                // Object value - find matching brace
                EndPos := StartPos;
                BraceCount := 1;
                Inc(EndPos);
                While (EndPos <= Length(Json)) And (BraceCount > 0) Do
                Begin
                    If Copy(Json, EndPos, 1) = '{' Then Inc(BraceCount)
                    Else If Copy(Json, EndPos, 1) = '}' Then Dec(BraceCount);
                    Inc(EndPos);
                End;
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End
            Else
            Begin
                // Number or other value
                EndPos := StartPos;
                While (EndPos <= Length(Json)) And (Not IsDelimiter(Json, EndPos)) Do
                    Inc(EndPos);
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End;
        End;
    End;
End;

Function BuildSuccessResponse(RequestId : String; Data : String) : String;
Begin
    If Data = '' Then
        Data := 'null';
    Result := '{"id":"' + RequestId + '","success":true,"data":' + Data + ',"error":null}';
End;

Function BuildErrorResponse(RequestId : String; ErrorCode : String; ErrorMsg : String) : String;
Begin
    // Inline JSON-escape (EscapeJsonString not yet declared at this point in build order)
    ErrorMsg := StringReplace(ErrorMsg, '\', '\\', -1);
    ErrorMsg := StringReplace(ErrorMsg, '"', '\"', -1);
    ErrorMsg := StringReplace(ErrorMsg, #13, '\r', -1);
    ErrorMsg := StringReplace(ErrorMsg, #10, '\n', -1);
    ErrorMsg := StringReplace(ErrorMsg, #9, '\t', -1);
    Result := '{"id":"' + RequestId + '","success":false,"data":null,"error":{"code":"' + ErrorCode + '","message":"' + ErrorMsg + '"}}';
End;

Procedure EnsureWorkspaceDir;
Begin
    If WorkspaceDir = '' Then
        WorkspaceDir := ResolveDefaultWorkspaceDir;
    If Not DirectoryExists(WorkspaceDir) Then
        ForceDirectories(WorkspaceDir);
End;

{ Dispatcher and entry points are in Dispatcher.pas (compiles last) }
