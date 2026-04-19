{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Dispatcher.pas - Command dispatcher for the Altium integration bridge                      }
{ This file MUST compile last so all Handle*Command functions are declared.   }
{..............................................................................}

{ Dashboard counters — fed to StatusForm.pas helpers each tick. The form is }
{ DFM-backed (StatusForm.dfm defines each control by name) so property       }
{ setters on those controls compile and work at runtime, unlike controls     }
{ created programmatically with TForm.Create where .Caption is undeclared.   }
Var
    StatusStartTick      : Cardinal;
    StatusRequestCount   : Integer;
    StatusLastCommand    : String;
    StatusTotalAltiumMs  : Cardinal;  { Sum of per-command durations — "time Altium spent working" }

Function ProcessCommand(Command : String; Params : String; RequestId : String) : String;
Var
    Category, Action : String;
    DotPos : Integer;
Begin
    // Split command into category.action
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

    // Dispatch to appropriate handler
    Case Category Of
        'application': Result := HandleApplicationCommand(Action, Params, RequestId);
        'project':     Result := HandleProjectCommand(Action, Params, RequestId);
        'library':     Result := HandleLibraryCommand(Action, Params, RequestId);
        'generic':     Result := HandleGenericCommand(Action, Params, RequestId);
        'pcb':         Result := HandlePCBCommand(Action, Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_COMMAND', 'Unknown command category: ' + Category + '. Use generic.* for object operations or pcb.* for PCB-specific commands.');
    End;
End;

{..............................................................................}
{ Process a single request if one exists. Returns True if a request was found.}
{..............................................................................}

Function ProcessSingleRequest : Boolean;
Var
    RequestPath, ResponsePath : String;
    RequestContent, ResponseContent : String;
    RequestId, Command, Params : String;
    ExceptionMsg : String;
    StartMs, DurationMs : Cardinal;
    ResultTag : String;
Begin
    Result := False;
    EnsureWorkspaceDir;
    RequestPath := WorkspaceDir + REQUEST_FILE;
    ResponsePath := WorkspaceDir + RESPONSE_FILE;

    // Check if request file exists
    If Not FileExists(RequestPath) Then Exit;

    // Read the request file
    RequestContent := ReadFileContent(RequestPath);
    If RequestContent = '' Then
    Begin
        DeleteFile(RequestPath);
        Result := False;
        Exit;
    End;

    // Extract fields from request
    RequestId := ExtractJsonValue(RequestContent, 'id');
    Command := ExtractJsonValue(RequestContent, 'command');
    Params := ExtractJsonValue(RequestContent, 'params');

    // Delete request file so we don't process it again (even if malformed)
    DeleteFile(RequestPath);

    If (RequestId = '') Or (Command = '') Then Exit;

    StatusLastCommand := Command;
    Inc(StatusRequestCount);
    UpdateStatusHeader('MCP: running ' + Command + '...');
    StartMs := GetTickCount;
    ResultTag := 'OK';
    ResponseAlreadyWritten := False;

    // Process the command
    ExceptionMsg := '';
    Try
        ResponseContent := ProcessCommand(Command, Params, RequestId);
    Except
        ExceptionMsg := 'Unhandled exception processing: ' + Command;
        ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR', ExceptionMsg);
        ResultTag := 'EXCEPTION';
    End;

    // Write the response (skip if handler already wrote it directly as a
    // workaround for the DelphiScript long-string return-corruption bug).
    If Not ResponseAlreadyWritten Then
        WriteFileContent(ResponsePath, ResponseContent);

    DurationMs := GetTickCount - StartMs;
    StatusTotalAltiumMs := StatusTotalAltiumMs + DurationMs;

    AppendLog(FormatLogStamp + ',' + IntToStr(DurationMs) + ',' + Command + ',' + ResultTag
              + ',' + IntToStr(Length(ResponseContent)) + ',' + Copy(ResponseContent, 1, 200));
    AppendLogLine(Command, DurationMs, ResultTag = 'EXCEPTION');

    Result := True;
End;

{..............................................................................}
{ Clean up any state left by the MCP server before exiting.                  }
{ Deletes stale IPC files and flushes the UI so Altium returns to normal.   }
{..............................................................................}

Procedure CleanupMCPServer;
Var
    ReqPath, RespPath : String;
Begin
    // Remove stale IPC files so they don't confuse the next session
    ReqPath := WorkspaceDir + REQUEST_FILE;
    RespPath := WorkspaceDir + RESPONSE_FILE;
    If FileExists(ReqPath)  Then DeleteFile(ReqPath);
    If FileExists(RespPath) Then DeleteFile(RespPath);

    // Flush any pending UI messages so Altium isn't stuck mid-operation
    Application.ProcessMessages;
End;

{..............................................................................}
{ Start MCP server — adaptive polling loop.                                  }
{                                                                            }
{ The server uses ADAPTIVE POLLING to avoid blocking Altium:                 }
{   - After receiving a command: polls fast (50ms) for quick response        }
{   - When idle: polls slow (500ms) with extra ProcessMessages calls         }
{     to give Altium breathing room for UI, scripts, and built-in features   }
{   - Auto-shuts down after AUTO_SHUTDOWN_MS (60s) with no request.          }
{     Python sends keep-alive pings every 30s to keep the server alive while }
{     attached, so this only fires after Python has disconnected.            }
{                                                                            }
{ IMPORTANT: While this loop runs, Altium's scripting engine is occupied.    }
{ Some script-based features may be unavailable. Use detach_from_altium      }
{ or the stop file to release the engine when not needed.                    }
{                                                                            }
{ Stop methods (all work while loop is running):                             }
{   1. Send application.stop_server via MCP (detach_from_altium does this)   }
{   2. Create a file named 'stop' in the workspace directory                 }
{   3. Altium debugger: press Stop button in the script IDE                  }
{   4. Auto-shutdown after AUTO_SHUTDOWN_MS of inactivity (see constants)    }
{                                                                            }
{ On exit the server cleans up IPC files and flushes UI state so Altium     }
{ returns to normal with no leftover artifacts.                              }
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

    EnsureWorkspaceDir;
    Running := True;
    StopPath := WorkspaceDir + 'stop';
    If FileExists(StopPath) Then DeleteFile(StopPath);

    IdleCount := 0;
    CurrentSleep := POLL_INTERVAL_ACTIVE;
    LastActivityMs := GetTickCount;
    ActiveTickCount := 0;

    StatusStartTick := GetTickCount;
    StatusRequestCount := 0;
    StatusLastCommand := '';
    StatusTotalAltiumMs := 0;
    ShowStatusForm;
    UpdateStatusHeader('MCP: idle');
    UpdateStatsLine(0, 0, 0, AUTO_SHUTDOWN_MS Div 1000);
    AppendLog(FormatLogStamp + ',0,_session_start,version=' + SCRIPT_VERSION);

    Try
        While Running Do
        Begin
            // --- Shutdown detection ---
            // Client.IsQuitting returns True when Altium is closing.
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

            // --- Stop signal checks ---

            // Check for stop file
            If FileExists(StopPath) Then
            Begin
                DeleteFile(StopPath);
                Running := False;
                Break;
            End;

            // Auto-shutdown after prolonged inactivity
            If AUTO_SHUTDOWN_MS > 0 Then
            Begin
                NowMs := GetTickCount;
                If NowMs >= LastActivityMs Then
                Begin
                    If (NowMs - LastActivityMs) > AUTO_SHUTDOWN_MS Then
                    Begin
                        Running := False;
                        Break;
                    End;
                End;
            End;

            // --- Process one request if available ---
            HadRequest := ProcessSingleRequest;

            If HadRequest Then
            Begin
                IdleCount := 0;
                CurrentSleep := POLL_INTERVAL_ACTIVE;
                LastActivityMs := GetTickCount;
                UpdateStatusHeader('MCP: idle');
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    (AUTO_SHUTDOWN_MS - (GetTickCount - LastActivityMs)) Div 1000);
                RefreshPerfPanel;
            End
            Else
            Begin
                Inc(IdleCount);
                If IdleCount > IDLE_THRESHOLD Then
                    CurrentSleep := POLL_INTERVAL_IDLE;
                { Refresh uptime + countdown once a second while idle. }
                If (IdleCount Mod 10) = 0 Then
                    UpdateStatsLine(
                        (GetTickCount - StatusStartTick) Div 1000,
                        StatusRequestCount,
                        StatusTotalAltiumMs,
                        (AUTO_SHUTDOWN_MS - (GetTickCount - LastActivityMs)) Div 1000);
            End;

            // --- Yield to Altium ---
            // Idle mode: call ProcessMessages multiple times per cycle so
            // Altium's UI stays responsive while requests are infrequent.
            // Active mode: ProcessMessages is expensive (pumps the whole UI
            // message queue), so call it only every Nth tick.
            If CurrentSleep >= POLL_INTERVAL_IDLE Then
            Begin
                For I := 1 To YIELD_ITERATIONS Do
                Begin
                    Application.ProcessMessages;
                    Sleep(CurrentSleep Div YIELD_ITERATIONS);
                    If Not Running Then Break;
                End;
                ActiveTickCount := 0;
            End
            Else
            Begin
                Inc(ActiveTickCount);
                If ActiveTickCount >= YIELD_EVERY_N_ACTIVE Then
                Begin
                    Application.ProcessMessages;
                    ActiveTickCount := 0;
                End;
                Sleep(CurrentSleep);
            End;
        End;
    Except
        // Altium is shutting down or fatal error — exit gracefully
    End;

    // Always clean up regardless of how we exited
    Running := False;
    AppendLog(FormatLogStamp + ',0,_session_end,requests=' + IntToStr(StatusRequestCount));
    HideStatusForm;
    CleanupMCPServer;
End;

{..............................................................................}
{ Stop the MCP server from outside the polling loop.                         }
{ Writes the 'stop' signal file so a running StartMCPServer will exit on    }
{ its next poll. Safe to call even if the server isn't running.              }
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
