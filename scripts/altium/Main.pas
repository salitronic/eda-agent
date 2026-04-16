{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ Main.pas - Constants and helpers for the Altium integration bridge                         }
{ The script polls for request.json, processes commands, writes response.json }
{..............................................................................}

Const
    CONFIG_FILE = 'mcp_config.json';
    REQUEST_FILE = 'request.json';
    RESPONSE_FILE = 'response.json';
    POLL_INTERVAL_ACTIVE = 50;    // ms between polls right after a command
    POLL_INTERVAL_IDLE   = 500;   // ms between polls when idle (gives Altium breathing room)
    IDLE_THRESHOLD       = 20;    // iterations before switching to idle polling
    AUTO_SHUTDOWN_MS     = 60000;  // 60 sec inactivity auto-shutdown (Python sends keep-alive pings)
    YIELD_ITERATIONS     = 10;    // ProcessMessages calls per sleep cycle in idle mode

Var
    WorkspaceDir : String;
    Running : Boolean;

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
Begin
    Result := S;
    Result := StringReplace(Result, '\t', #9, -1);
    Result := StringReplace(Result, '\n', #10, -1);
    Result := StringReplace(Result, '\r', #13, -1);
    Result := StringReplace(Result, '\"', '"', -1);
    Result := StringReplace(Result, '\\', '\', -1);
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
