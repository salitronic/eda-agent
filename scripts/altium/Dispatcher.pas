{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Dispatcher.pas - Command dispatcher for the Altium integration bridge                      }
{ This file MUST compile last so all Handle*Command functions are declared.   }
{..............................................................................}

{ Request counters — used by ping_altium to report liveness. A floating      }
{ status window was attempted but Altium's DelphiScript host blocks every    }
{ VCL property setter we tried (.Caption, .SimpleText, .Text, .Lines are all }
{ "Undeclared identifier" at compile time — Try/Except cannot catch those).  }
{ A proper status GUI would need a pre-built .dfm form loaded via            }
{ Application.CreateForm — left as a future-work item.                       }
Var
    StatusStartTick    : Cardinal;
    StatusRequestCount : Integer;
    StatusLastCommand  : String;

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
    SetStatusText('MCP: running ' + Command + '...  | req ' + IntToStr(StatusRequestCount));

    // Process the command
    ExceptionMsg := '';
    Try
        ResponseContent := ProcessCommand(Command, Params, RequestId);
    Except
        ExceptionMsg := 'Unhandled exception processing: ' + Command;
        ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR', ExceptionMsg);
    End;

    // Write the response
    WriteFileContent(ResponsePath, ResponseContent);
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
Begin
    If Running Then Exit;

    EnsureWorkspaceDir;
    Running := True;
    StopPath := WorkspaceDir + 'stop';
    If FileExists(StopPath) Then DeleteFile(StopPath);

    IdleCount := 0;
    CurrentSleep := POLL_INTERVAL_ACTIVE;
    LastActivityMs := GetTickCount;

    StatusStartTick := GetTickCount;
    StatusRequestCount := 0;
    StatusLastCommand := '';
    ShowStatusForm;
    SetStatusText('MCP: idle');

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
                SetStatusText('MCP: idle  | req ' + IntToStr(StatusRequestCount)
                              + '  | last: ' + StatusLastCommand);
            End
            Else
            Begin
                Inc(IdleCount);
                If IdleCount > IDLE_THRESHOLD Then
                    CurrentSleep := POLL_INTERVAL_IDLE;
            End;

            // --- Yield to Altium ---
            // In idle mode, call ProcessMessages multiple times to let Altium
            // handle UI events, internal operations, and shutdown requests.
            If CurrentSleep >= POLL_INTERVAL_IDLE Then
            Begin
                For I := 1 To YIELD_ITERATIONS Do
                Begin
                    Application.ProcessMessages;
                    Sleep(CurrentSleep Div YIELD_ITERATIONS);
                    If Not Running Then Break;
                End;
            End
            Else
            Begin
                Application.ProcessMessages;
                Sleep(CurrentSleep);
            End;
        End;
    Except
        // Altium is shutting down or fatal error — exit gracefully
    End;

    // Always clean up regardless of how we exited
    Running := False;
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
