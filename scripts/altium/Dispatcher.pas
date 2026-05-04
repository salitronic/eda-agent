{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Dispatcher.pas - Polling loop and per-request dispatcher.                     }
{ Compiles last so all Handle*Command functions are visible.                   }
{..............................................................................}

{ Dashboard counters fed to StatusForm.pas helpers each tick. }
Var
    StatusStartTick      : Cardinal;
    StatusRequestCount   : Integer;
    StatusLastCommand    : String;
    StatusTotalAltiumMs  : Cardinal;

Function ProcessCommand(Command : String; Params : String; RequestId : String) : String;
Var
    Category, Action : String;
    DotPos : Integer;
Begin
    DotPos := Pos('.', Command);
    If DotPos > 0 Then
    Begin
        Category := Copy(Command, 1, DotPos - 1);
        Action := Copy(Command, DotPos + 1, Length(Command));
    End
    Else
    Begin
        Category := Command;
        Action := '';
    End;

    Case Category Of
        'application': Result := HandleApplicationCommand(Action, Params, RequestId);
        'project':     Result := HandleProjectCommand(Action, Params, RequestId);
        'library':     Result := HandleLibraryCommand(Action, Params, RequestId);
        'generic':     Result := HandleGenericCommand(Action, Params, RequestId);
        'pcb':         Result := HandlePCBCommand(Action, Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_COMMAND',
            'Unknown command category: ' + Category +
            '. Use generic.* for object operations or pcb.* for PCB-specific commands.');
    End;
End;

{..............................................................................}
{ Process a single request if one exists. Returns True iff a request was found.}
{                                                                                }
{ The dispatcher scans for any request_*.json file in the workspace, extracts  }
{ the ID from the filename, reads and deletes the request, dispatches, and    }
{ writes response_<id>.json. The handler returns the JSON envelope as a       }
{ String; the dispatcher writes the file. Handlers that previously bypassed    }
{ the dispatcher's write via the ResponseAlreadyWritten flag have all been     }
{ migrated to the standard pattern.                                            }
{..............................................................................}

Function ProcessSingleRequest : Boolean;
Var
    RequestPath, RequestId : String;
    RequestContent, ResponseContent : String;
    Command, Params, ProtoVer, EnvelopeError : String;
    ExceptionMsg : String;
    StartMs, DurationMs : Cardinal;
    ResultTag : String;
Begin
    Result := False;
    EnsureWorkspaceDir;

    If Not ScanForRequestFile(RequestPath, RequestId) Then Exit;

    // Read the request file
    RequestContent := ReadFileContent(RequestPath);
    // Remove the request file regardless of read outcome so we never reprocess
    DeleteFile(RequestPath);

    If RequestContent = '' Then Exit;

    // ID arrives in the JSON body. Per-request response files use it for
    // the filename so concurrent callers each get an isolated response file.
    RequestId := ExtractJsonValue(RequestContent, 'id');
    Command := ExtractJsonValue(RequestContent, 'command');
    Params := ExtractJsonValue(RequestContent, 'params');
    ProtoVer := ExtractJsonValue(RequestContent, 'protocol_version');

    EnvelopeError := ValidateRequestEnvelope(RequestId, Command);
    If EnvelopeError <> '' Then
    Begin
        // Without a valid id we can't write a per-request response file —
        // fall back to writing response.json so Python can still pick it up.
        If IsValidRequestId(RequestId) Then
            WriteResponseFile(RequestId,
                BuildErrorResponse(RequestId, 'MALFORMED_REQUEST', EnvelopeError))
        Else
            WriteFileContent(WorkspaceDir + 'response.json',
                BuildErrorResponse('', 'MALFORMED_REQUEST', EnvelopeError));
        Result := True;
        Exit;
    End;

    If (ProtoVer <> '') And (ProtoVer <> IntToStr(PROTOCOL_VERSION)) Then
    Begin
        WriteResponseFile(RequestId,
            BuildErrorResponseDetailed(RequestId, 'PROTOCOL_VERSION_MISMATCH',
                'Client protocol_version=' + ProtoVer +
                ' does not match server PROTOCOL_VERSION=' + IntToStr(PROTOCOL_VERSION) +
                '. Update the eda-agent client or restart the Altium script.',
                '{"client_version":' + ProtoVer +
                ',"server_version":' + IntToStr(PROTOCOL_VERSION) + '}'));
        Result := True;
        Exit;
    End;

    StatusLastCommand := Command;
    Inc(StatusRequestCount);
    UpdateStatusHeader('MCP: running ' + Command + '...');
    StartMs := GetTickCount;
    ResultTag := 'OK';

    ExceptionMsg := '';
    Try
        ResponseContent := ProcessCommand(Command, Params, RequestId);
    Except
        ExceptionMsg := 'Unhandled exception processing: ' + Command;
        ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR', ExceptionMsg);
        ResultTag := 'EXCEPTION';
    End;

    If ResponseContent = '' Then
    Begin
        // Handler returned nothing — degenerate but recoverable. Synthesise
        // an INTERNAL_ERROR rather than leaving the caller polling forever.
        ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR',
            'Handler returned empty response for: ' + Command);
        ResultTag := 'EMPTY';
    End;

    WriteResponseFile(RequestId, ResponseContent);

    DurationMs := GetTickCount - StartMs;
    StatusTotalAltiumMs := StatusTotalAltiumMs + DurationMs;

    AppendLog(FormatLogStamp + ',' + IntToStr(DurationMs) + ',' + Command + ',' + ResultTag
              + ',' + IntToStr(Length(ResponseContent)) + ',' + Copy(ResponseContent, 1, 200));
    AppendLogLine(Command, DurationMs, ResultTag = 'EXCEPTION');

    Result := True;
End;

{..............................................................................}
{ Clean up state left by the MCP server before exiting. Deletes any leftover   }
{ per-request IPC files and flushes the UI.                                    }
{..............................................................................}

Procedure CleanupMCPServer;
Begin
    CleanupOrphanResponses;
    Application.ProcessMessages;
End;

{..............................................................................}
{ Start MCP server — adaptive polling loop.                                  }
{                                                                            }
{ Uses ADAPTIVE POLLING to avoid blocking Altium:                             }
{   - Active (just processed a request): polls fast (PollIntervalActiveMs)   }
{   - Idle: polls slow (PollIntervalIdleMs) with extra ProcessMessages calls }
{   - Auto-shuts down after AutoShutdownMs of inactivity                      }
{                                                                            }
{ All tunables come from mcp_config.json via LoadMCPConfig at startup.       }
{ Stop methods: send application.stop_server, drop a 'stop' file in the      }
{ workspace, or wait for auto-shutdown.                                      }
{..............................................................................}

Procedure StartMCPServer;
Var
    StopPath       : String;
    IdleCount      : Integer;
    CurrentSleep   : Integer;
    LastActivityMs : Cardinal;
    NowMs          : Cardinal;
    HadRequest     : Boolean;
    I              : Integer;
    ActiveTickCount : Integer;
Begin
    If Running Then Exit;

    InitDefaultConfig;
    EnsureWorkspaceDir;
    LoadMCPConfig;
    CleanupOrphanResponses;
    Running := True;
    StopPath := WorkspaceDir + 'stop';
    If FileExists(StopPath) Then DeleteFile(StopPath);

    IdleCount := 0;
    CurrentSleep := PollIntervalActiveMs;
    LastActivityMs := GetTickCount;
    ActiveTickCount := 0;

    StatusStartTick := GetTickCount;
    StatusRequestCount := 0;
    StatusLastCommand := '';
    StatusTotalAltiumMs := 0;
    ShowStatusForm;
    UpdateStatusHeader('MCP: idle');
    UpdateStatsLine(0, 0, 0, AutoShutdownMs Div 1000);
    AppendLog(FormatLogStamp + ',0,_session_start,version=' + SCRIPT_VERSION
              + ',protocol=' + IntToStr(PROTOCOL_VERSION));

    Try
        While Running Do
        Begin
            // Shutdown detection: Altium quitting
            Try
                If Client.IsQuitting Then
                Begin
                    Running := False;
                    Break;
                End;
            Except
                Running := False;
                Break;
            End;

            // Stop file
            If FileExists(StopPath) Then
            Begin
                DeleteFile(StopPath);
                Running := False;
                Break;
            End;

            // Auto-shutdown after prolonged inactivity
            If AutoShutdownMs > 0 Then
            Begin
                NowMs := GetTickCount;
                If NowMs >= LastActivityMs Then
                Begin
                    If (NowMs - LastActivityMs) > AutoShutdownMs Then
                    Begin
                        Running := False;
                        Break;
                    End;
                End;
            End;

            HadRequest := ProcessSingleRequest;

            If HadRequest Then
            Begin
                IdleCount := 0;
                CurrentSleep := PollIntervalActiveMs;
                LastActivityMs := GetTickCount;
                UpdateStatusHeader('MCP: idle');
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    (AutoShutdownMs - (GetTickCount - LastActivityMs)) Div 1000);
                RefreshPerfPanel;
            End
            Else
            Begin
                Inc(IdleCount);
                If IdleCount > IdleThreshold Then
                    CurrentSleep := PollIntervalIdleMs;
                If (IdleCount Mod 10) = 0 Then
                    UpdateStatsLine(
                        (GetTickCount - StatusStartTick) Div 1000,
                        StatusRequestCount,
                        StatusTotalAltiumMs,
                        (AutoShutdownMs - (GetTickCount - LastActivityMs)) Div 1000);
            End;

            If CurrentSleep >= PollIntervalIdleMs Then
            Begin
                For I := 1 To YieldIterations Do
                Begin
                    Application.ProcessMessages;
                    Sleep(CurrentSleep Div YieldIterations);
                    If Not Running Then Break;
                End;
                ActiveTickCount := 0;
            End
            Else
            Begin
                Inc(ActiveTickCount);
                If ActiveTickCount >= YieldEveryNActive Then
                Begin
                    Application.ProcessMessages;
                    ActiveTickCount := 0;
                End;
                Sleep(CurrentSleep);
            End;
        End;
    Except
        // Altium shutting down or fatal error — exit gracefully
    End;

    Running := False;
    AppendLog(FormatLogStamp + ',0,_session_end,requests=' + IntToStr(StatusRequestCount));
    HideStatusForm;
    CleanupMCPServer;
End;

{..............................................................................}
{ Stop the MCP server from outside the polling loop. Writes the 'stop' file   }
{ so a running StartMCPServer exits on its next poll.                          }
{..............................................................................}

Procedure StopMCPServer;
Var
    StopPath : String;
    F : TextFile;
Begin
    EnsureWorkspaceDir;
    StopPath := WorkspaceDir + 'stop';
    Try
        AssignFile(F, StopPath);
        Rewrite(F);
        Writeln(F, '1');
        CloseFile(F);
        ShowMessage('MCP server stop signal sent. The server will exit within 500ms.');
    Except
        ShowMessage('Failed to write stop file: ' + StopPath);
    End;
End;
